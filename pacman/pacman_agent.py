"""Dual-rate Autonomous Pac-Man Agent using ggmacrolib and Local AI Decision Model.

Architecture:
1. Real-Time Macro Loom: A concurrent worker thread executing low-latency
   responsive keystrokes and navigation macros (50-100ms cycle).
2. Supervisory Decision Agent: Evaluates the game state every 2-3 seconds,
   inspects screenshot/CV telemetry, consults the local AI decider (Ollama/Qwen/LLaMA),
   and dynamically adjusts parameters or hot-swaps active routines on the running loom.
3. Guardrails & Safety: Bounded action budget, window focus validation,
   emergency Esc key listener, and artifacts/pacman/STOP signal file.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np
import ggmacrolib as macro
from ggmacro.local_decider import ActionGate, Decision, DecisionError, LocalDecider, SupervisorDecision
from .pacman_control import WINDOW, focused, focus_window, capture, send_key

OUT = Path(__file__).resolve().parents[1] / 'artifacts' / 'pacman'
REGION = (2480, 300, 3020, 980)  # Maze region in desktop pixel space



def yellow_player(image):
    """Detect the normalized (x, y) centroid of the yellow Pac-Man player."""
    rgb = np.array(image)
    # Mask yellow pixels (Pac-Man)
    mask = ((rgb[:, :, 0] > 190) & (rgb[:, :, 1] > 180)
            & (rgb[:, :, 2] < 100)).astype('uint8')
    # Filter header and lives at bottom
    mask[:120] = 0
    mask[590:] = 0
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 8]
    if not candidates:
        return None
    i = max(candidates, key=lambda j: stats[j, cv2.CC_STAT_AREA])
    return [round(float(centers[i][0] / image.width), 3),
            round(float(centers[i][1] / image.height), 3)]


def detect_red_ghost(image):
    """Detect the normalized (x, y) centroid of the red ghost (Blinky) or closest threat."""
    rgb = np.array(image)
    # Mask red pixels (Blinky ghost)
    mask = ((rgb[:, :, 0] > 190) & (rgb[:, :, 1] < 60)
            & (rgb[:, :, 2] < 60)).astype('uint8')
    mask[:120] = 0
    mask[590:] = 0
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates = [i for i in range(1, count) if 10 <= stats[i, cv2.CC_STAT_AREA] <= 400]
    if not candidates:
        return None
    i = max(candidates, key=lambda j: stats[j, cv2.CC_STAT_AREA])
    return [round(float(centers[i][0] / image.width), 3),
            round(float(centers[i][1] / image.height), 3)]


def extract_tactical_state(image):
    """Extract comprehensive tactical telemetry: player, ghosts, pellets, and open corridors."""
    img = np.array(image)
    h, w, _ = img.shape

    def get_centroid(mask, min_area=8, max_area=600):
        cnt, _, stats, centers = cv2.connectedComponentsWithStats(mask.astype('uint8'))
        cand = [i for i in range(1, cnt) if min_area <= stats[i, cv2.CC_STAT_AREA] <= max_area]
        if not cand:
            return None
        i = max(cand, key=lambda j: stats[j, cv2.CC_STAT_AREA])
        return [round(float(centers[i][0] / w), 3), round(float(centers[i][1] / h), 3)]

    # Entity masks in RGB
    pacman_mask = (img[:, :, 0] > 180) & (img[:, :, 1] > 180) & (img[:, :, 2] < 80)
    pacman_mask[:120] = 0
    pacman_mask[590:] = 0

    red_mask = (img[:, :, 0] > 180) & (img[:, :, 1] < 60) & (img[:, :, 2] < 60)
    pink_mask = (img[:, :, 0] > 200) & (img[:, :, 1] < 180) & (img[:, :, 2] > 160)
    orange_mask = (img[:, :, 0] > 200) & (img[:, :, 1] > 120) & (img[:, :, 1] < 180) & (img[:, :, 2] < 60)
    cyan_mask = (img[:, :, 0] < 60) & (img[:, :, 1] > 180) & (img[:, :, 2] > 180)

    for m in [red_mask, pink_mask, orange_mask, cyan_mask]:
        m[:120] = 0
        m[590:] = 0

    p_pos = get_centroid(pacman_mask)
    all_ghosts = [pos for pos in [
        get_centroid(red_mask), get_centroid(pink_mask),
        get_centroid(orange_mask), get_centroid(cyan_mask)
    ] if pos is not None]

    nearest_ghost_dist = 999.0
    threat_dir = 'none'
    if p_pos and all_ghosts:
        for g in all_ghosts:
            d = np.hypot(p_pos[0] - g[0], p_pos[1] - g[1])
            if d < nearest_ghost_dist:
                nearest_ghost_dist = round(float(d), 3)
                dx = g[0] - p_pos[0]
                dy = g[1] - p_pos[1]
                if abs(dx) > abs(dy):
                    threat_dir = 'right' if dx > 0 else 'left'
                else:
                    threat_dir = 'down' if dy > 0 else 'up'

    # Pellet density per quadrant relative to Pac-Man
    pellet_mask = (img[:, :, 0] > 180) & (img[:, :, 1] > 180) & (img[:, :, 2] > 180)
    pellets = {'left': 0, 'right': 0, 'up': 0, 'down': 0}
    if p_pos:
        px, py = int(p_pos[0] * w), int(p_pos[1] * h)
        pellets['left'] = int(np.count_nonzero(pellet_mask[:, :px]))
        pellets['right'] = int(np.count_nonzero(pellet_mask[:, px:]))
        pellets['up'] = int(np.count_nonzero(pellet_mask[:py, :]))
        pellets['down'] = int(np.count_nonzero(pellet_mask[py:, :]))

    return {
        'player_pos': p_pos,
        'ghost_count': len(all_ghosts),
        'nearest_ghost_dist': nearest_ghost_dist,
        'threat_direction': threat_dir,
        'is_critical_danger': nearest_ghost_dist < 0.16,
        'pellets_by_quadrant': pellets
    }



def detect_screen_state(image):
    """Classify the high-level game state: 'active', 'game_over', or 'ready'."""
    arr = np.array(image)
    red_count = np.count_nonzero((arr[:, :, 0] > 190) & (arr[:, :, 1] < 60) & (arr[:, :, 2] < 60))
    yellow_count = np.count_nonzero((arr[:, :, 0] > 190) & (arr[:, :, 1] > 180) & (arr[:, :, 2] < 100))
    if red_count > 3000 and yellow_count < 20:
        return 'game_over'
    if yellow_count > 15:
        return 'active'
    return 'ready'


# ---------------------------------------------------------------------------
# Real-Time Macro Loom Routines (Executed concurrently at high frequency)
# ---------------------------------------------------------------------------

def explore_corridor_routine(loom):
    """Default real-time routine: keeps Pac-Man navigating along corridors."""
    direction = loom.get_param('direction', 'right')
    if direction in ['left', 'right', 'up', 'down']:
        send_key(direction, duration=0.06)


def evade_ghost_routine(loom):
    """Evasive maneuver routine: executes rapid orthogonal turns away from threats."""
    evade_dir = loom.get_param('evade_direction', 'up')
    if evade_dir in ['left', 'right', 'up', 'down']:
        send_key(evade_dir, duration=0.07)


def unstick_routine(loom):
    """Unstick routine: alternates perpendicular turns to escape corner deadlocks."""
    step = loom.get_param('unstick_step', 0)
    sequence = ['up', 'left', 'down', 'right']
    key = sequence[step % len(sequence)]
    send_key(key, duration=0.06)
    loom.update_params(unstick_step=step + 1)


# ---------------------------------------------------------------------------
# Pacman Agent & Supervisor Engine
# ---------------------------------------------------------------------------

class PacmanAgent:
    """Supervised Autonomous Pac-Man Controller.

    Couples a local AI decision model with a high-speed MacroLoom and
    a 2-3 second supervisory feedback loop.
    """

    def __init__(self, seconds=45, steps=50, supervise_interval=2.5,
                 loom_interval=0.08, model_name='d1:latest'):
        self.seconds = seconds
        self.max_steps = steps
        self.supervise_interval = supervise_interval
        self.loom_interval = loom_interval
        self.model_name = model_name

        self.stop_event = threading.Event()
        self.started_at = 0.0
        self.step_count = 0
        self.last_pos = None
        self.last_enter_time = -10.0

        # Create the high-speed macro loom
        self.loom = macro.MacroLoom(
            routine=explore_corridor_routine,
            interval=self.loom_interval,
            name='PacmanRealtimeLoom'
        )
        self.loom.update_params(direction='right', mode='explore')

        # Local AI decision model
        self.model = LocalDecider(model=model_name, timeout=3)

    def is_permitted(self):
        """Verify session budget, stop signals, and window focus invariants."""
        if self.stop_event.is_set():
            return False
        if (OUT / 'STOP').exists():
            return False
        if time.monotonic() - self.started_at >= self.seconds:
            return False
        if self.step_count >= self.max_steps:
            return False
        return focused()

    def supervisor_cycle(self, runner):
        """Supervisory inspection executed every 2-3 seconds."""
        if not self.is_permitted():
            return

        obs_time = time.monotonic()
        # 1. Screen capture of the active maze
        image = macro.screenshot(region=REGION)
        tactical = extract_tactical_state(image)
        player = tactical['player_pos']
        screen_state = detect_screen_state(image)

        # 2. Check movement displacement to detect if stuck
        stuck = False
        if player is not None and self.last_pos is not None:
            dist = np.hypot(player[0] - self.last_pos[0], player[1] - self.last_pos[1])
            if dist < 0.015:
                stuck = True
        self.last_pos = player

        # 3. Save periodic frames for visual telemetry
        if self.step_count % 3 == 0:
            image.save(OUT / f'frame-{self.step_count:03d}.png')

        # 4. Handle start / restart if Game Over or Ready is detected
        if screen_state in ['game_over', 'ready']:
            if time.monotonic() - self.last_enter_time > 3.0:
                print(f"[Supervisor] Screen is '{screen_state}'. Sending space/enter to play.")
                send_key('space', duration=0.1)
                self.last_enter_time = time.monotonic()
                self.loom.set_routine(explore_corridor_routine)
                self.loom.update_params(direction='right', mode='explore')
                self.step_count += 1
                return

        # 5. Formulate supervisory state for local AI model with rich tactical grounding
        current_direction = self.loom.get_param('direction', 'right')
        current_mode = self.loom.get_param('mode', 'explore')
        
        # Determine suggested direction based on highest pellet density
        pellets = tactical['pellets_by_quadrant']
        best_pellet_dir = max(pellets, key=pellets.get) if pellets else 'up'

        state_payload = {
            'player_position_xy': player,
            'nearest_ghost_distance': tactical['nearest_ghost_dist'],
            'ghost_threat_direction': tactical['threat_direction'],
            'ghost_critical_danger': tactical['is_critical_danger'],
            'pellet_density_by_direction': pellets,
            'highest_pellet_corridor': best_pellet_dir,
            'is_stuck_in_corner': stuck,
            'screen_status': screen_state,
            'current_heading': current_direction
        }

        # Filter permitted actions to prevent walking directly into a close ghost
        allowed_actions = {
            'turn_left': 'Steer Pac-Man left along open corridor',
            'turn_right': 'Steer Pac-Man right along open corridor',
            'turn_up': 'Steer Pac-Man up along open corridor',
            'turn_down': 'Steer Pac-Man down along open corridor',
            'maintain': 'Keep current heading along open corridor',
            'evade': 'Execute emergency evasive turn away from ghost',
            'unstick': 'Execute unstick wiggle routine to break corner deadlock'
        }

        if tactical['is_critical_danger'] and tactical['threat_direction'] in ['left', 'right', 'up', 'down']:
            threat = f"turn_{tactical['threat_direction']}"
            if threat in allowed_actions:
                del allowed_actions[threat]  # Prohibit walking directly into oncoming ghost

        # Format image thumbnail for model observation
        small = image.resize((256, 322))
        buf = io.BytesIO()
        small.save(buf, format='PNG')
        img_bytes = buf.getvalue()

        # 6. Consult Local AI Decider (qwen3.5 / Ollama)
        log_entry = {
            'step': self.step_count,
            'observed_at': round(obs_time - self.started_at, 2),
            'player': player,
            'nearest_ghost': tactical['nearest_ghost_dist'],
            'threat_dir': tactical['threat_direction'],
            'best_pellet_dir': best_pellet_dir,
            'stuck': stuck,
            'state': screen_state
        }

        goal_prompt = (
            'Goal: WIN Pac-Man. Clear remaining pellets while avoiding ghosts. '
            f'Highest pellet density is towards {best_pellet_dir}. '
            f'Ghost threat is {tactical["threat_direction"]} (distance: {tactical["nearest_ghost_dist"]}). '
            'Select the winning move: advance towards pellets or evade ghosts.'
        )

        try:
            decision = self.model.choose(
                state=state_payload,
                actions=allowed_actions,
                goal=goal_prompt,
                image=img_bytes,
                observed_at=obs_time
            )
            action = decision.action
            latency = round(decision.latency_ms, 2)
            log_entry['action'] = action
            log_entry['latency_ms'] = latency

            # 7. Dynamically adjust or modify the MacroLoom based on decision
            if action in ['turn_left', 'turn_right', 'turn_up', 'turn_down']:
                direction = action.replace('turn_', '')
                self.loom.set_routine(explore_corridor_routine)
                self.loom.update_params(direction=direction, mode='explore')
                send_key(direction, duration=0.08)
                print(f"[Supervisor] Step {self.step_count}: Direction set to {direction} towards pellets (latency: {latency}ms)")

            elif action == 'evade':
                # Evade perpendicular to threat
                evade_dir = 'up' if tactical['threat_direction'] in ['left', 'right'] else 'left'
                self.loom.set_routine(evade_ghost_routine)
                self.loom.update_params(evade_direction=evade_dir, mode='evade')
                send_key(evade_dir, duration=0.08)
                print(f"[Supervisor] Step {self.step_count}: EVASION to {evade_dir} away from ghost")

            elif action == 'unstick' or stuck:
                self.loom.set_routine(unstick_routine)
                self.loom.update_params(mode='unstick')
                print(f"[Supervisor] Step {self.step_count}: Unstick routine activated")

            elif action == 'maintain':
                pass

        except DecisionError as exc:
            # Fallback heuristic if local decider times out or errors
            log_entry['error'] = str(exc)
            fallback_dir = best_pellet_dir if not stuck else 'up'
            self.loom.set_routine(explore_corridor_routine)
            self.loom.update_params(direction=fallback_dir, mode='explore')
            send_key(fallback_dir, duration=0.08)
            print(f"[Supervisor] Step {self.step_count}: Decider fallback steering towards {fallback_dir}")


        # Write log entry
        with (OUT / 'agent.jsonl').open('a') as log_file:
            log_file.write(json.dumps(log_entry) + '\n')

        self.step_count += 1

    def run(self):
        """Run the dual-rate supervised agent session."""
        OUT.mkdir(parents=True, exist_ok=True)
        # Clear previous run stop signal if any
        if (OUT / 'STOP').exists():
            (OUT / 'STOP').unlink()

        # Keyboard listener for immediate Escape stop
        def on_press(key):
            if key == macro.pynput.keyboard.Key.esc:
                print("\n[PacmanAgent] ESC pressed. Initiating emergency stop.")
                self.stop_event.set()

        listener = macro.pynput.keyboard.Listener(on_press=on_press)
        listener.start()

        focus_window()
        time.sleep(0.3)
        if not focused():
            listener.stop()
            raise SystemExit(f"Stopped: Pac-Man window {WINDOW} must be focused to begin.")

        self.started_at = time.monotonic()
        print(f"=== Starting Supervised Pac-Man Agent ===")
        print(f"Session limit: {self.seconds}s | Max steps: {self.max_steps}")
        print(f"Supervision interval: {self.supervise_interval}s | Loom interval: {self.loom_interval}s")
        print("Press ESC at any time to halt.\n")

        # Create supervised runner
        runner = macro.SupervisedMacroRunner(
            loom=self.loom,
            supervisor_fn=self.supervisor_cycle,
            supervisor_interval=self.supervise_interval,
            focus_check=self.is_permitted
        )

        try:
            runner.start()
            # Main monitor loop waiting for completion
            while self.is_permitted():
                time.sleep(0.2)
        finally:
            runner.stop(timeout=1.0)
            listener.stop()
            capture('agent-final')
            elapsed = round(time.monotonic() - self.started_at, 2)
            summary = {
                'elapsed_seconds': elapsed,
                'supervisory_steps': self.step_count,
                'loom_macro_iterations': self.loom.iteration_count,
                'model': self.model_name,
                'stopped_by': 'escape' if self.stop_event.is_set() else 'budget/timeout'
            }
            (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            print(f"\n=== Agent Session Finished ===")
            print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=45, help="Session duration limit in seconds")
    parser.add_argument('--steps', type=int, default=50, help="Max supervisory decision steps")
    parser.add_argument('--supervise-interval', type=float, default=2.5, help="Supervisory loop cycle in seconds (e.g. 2-3s)")
    parser.add_argument('--loom-interval', type=float, default=0.08, help="Real-time macro step interval in seconds")
    parser.add_argument('--model', default='d1:latest', help="Local model name")
    args = parser.parse_args()

    agent = PacmanAgent(
        seconds=args.seconds,
        steps=args.steps,
        supervise_interval=args.supervise_interval,
        loom_interval=args.loom_interval,
        model_name=args.model
    )
    agent.run()


if __name__ == '__main__':
    main()
