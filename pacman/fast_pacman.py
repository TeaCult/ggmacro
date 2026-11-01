"""Experimental Pacman controller using MSS perception and optional local d1.

Tracks objects and corridors, maintains heading, and asks d1 at junctions.
Capture and inference speed depend on the actual desktop, runtime and hardware.
Calibrate regions and thresholds before running on a different setup.
"""
import argparse
import json
from pathlib import Path
import threading
import time

import cv2
import mss
import numpy as np
import ggmacrolib as macro
from ggmacro.local_decider import LocalDecider, DecisionError
from .pacman_control import WINDOW, focused, focus_window, send_key

REGION = (2480, 300, 3020, 980)
MSS_MONITOR = {'left': 2480, 'top': 300, 'width': 540, 'height': 680}
OUT = Path(__file__).resolve().parents[1] / 'artifacts' / 'pacman'


def get_open_corridors(arr, px, py, step=26):
    """Detect which cardinal directions from Pac-Man (px, py in pixels) are open corridors."""
    h, w, _ = arr.shape
    candidates = {'up': (0, -step), 'down': (0, step), 'left': (-step, 0), 'right': (step, 0)}
    open_dirs = []

    for name, (dx, dy) in candidates.items():
        x = int(px + dx)
        y = int(py + dy)
        if 0 <= x < w and 0 <= y < h:
            sample = arr[max(0, y-3):min(h, y+3), max(0, x-3):min(w, x+3)]
            # Blue walls: High Blue (>120), Low Red (<90)
            is_blue = np.any((sample[:, :, 2] > 120) & (sample[:, :, 0] < 90))
            # Red borders/ghost gates: High Red (>180), Low Green/Blue (<60)
            is_red = np.any((sample[:, :, 0] > 180) & (sample[:, :, 1] < 60) & (sample[:, :, 2] < 60))
            if not is_blue and not is_red:
                open_dirs.append(name)

    return open_dirs if open_dirs else ['left', 'right', 'up', 'down']


def fast_state_extractor(arr):
    """Fast CV perception in true RGB (arr is HxWx3 with Channel 0=Red, 1=Green, 2=Blue)."""
    h, w, _ = arr.shape

    # Pac-Man: Yellow (R>180, G>180, B<80) - isolate active player in maze via connected components
    p_mask = ((arr[:, :, 0] > 180) & (arr[:, :, 1] > 180) & (arr[:, :, 2] < 80)).astype('uint8')
    p_mask[:100, :] = 0
    p_mask[560:, :] = 0  # Exclude spare lives at the bottom

    cnt, _, stats, centers = cv2.connectedComponentsWithStats(p_mask)
    player = None
    px_int, py_int = 0, 0
    valid_blobs = [i for i in range(1, cnt) if stats[i, cv2.CC_STAT_AREA] >= 20]
    if valid_blobs:
        best_i = max(valid_blobs, key=lambda j: stats[j, cv2.CC_STAT_AREA])
        px_int = int(centers[best_i][0])
        py_int = int(centers[best_i][1])
        player = [round(float(px_int / w), 3), round(float(py_int / h), 3)]

    # Multi-Ghost threats in RGB:
    # Red (Blinky): R>170, G<70, B<70
    # Pink (Pinky): R>190, G:130..190, B>160
    # Cyan (Inky): B>180, G>170, R<90
    # Orange (Clyde): R>190, G:130..180, B<80
    g_red = (arr[:, :, 0] > 170) & (arr[:, :, 1] < 70) & (arr[:, :, 2] < 70)
    g_pink = (arr[:, :, 0] > 190) & (arr[:, :, 1] > 130) & (arr[:, :, 1] < 190) & (arr[:, :, 2] > 160)
    g_cyan = (arr[:, :, 2] > 180) & (arr[:, :, 1] > 170) & (arr[:, :, 0] < 90)
    g_orange = (arr[:, :, 0] > 190) & (arr[:, :, 1] > 130) & (arr[:, :, 1] < 180) & (arr[:, :, 2] < 80)
    g_mask = (g_red | g_pink | g_cyan | g_orange).astype('uint8')
    g_mask[:100, :] = 0
    g_mask[560:, :] = 0

    cnt_g, _, stats_g, centers_g = cv2.connectedComponentsWithStats(g_mask)
    ghost = None
    threat_dir = 'none'
    ghost_dist = 999.0
    if cnt_g > 1 and player:
        ghosts = []
        for i in range(1, cnt_g):
            if stats_g[i, cv2.CC_STAT_AREA] >= 35:
                gx_cand = float(centers_g[i][0] / w)
                gy_cand = float(centers_g[i][1] / h)
                d = float(np.hypot(gx_cand - player[0], gy_cand - player[1]))
                ghosts.append((d, gx_cand, gy_cand))
        if ghosts:
            ghosts.sort(key=lambda x: x[0])
            ghost_dist, gx, gy = ghosts[0]
            ghost_dist = round(ghost_dist, 3)
            ghost = [round(gx, 3), round(gy, 3)]
            dx = gx - player[0]
            dy = gy - player[1]
            threat_dir = ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')

    # Detect open corridors around Pac-Man
    open_corridors = ['left', 'right', 'up', 'down']
    if player:
        open_corridors = get_open_corridors(arr, px_int, py_int)

    # Pellets: True pink pellets in RGB (R>190, G: 140..195, B: 150..210)
    pellet_mask = ((arr[:, :, 0] > 190) & (arr[:, :, 1] > 140) & (arr[:, :, 1] < 195)
                   & (arr[:, :, 2] > 150) & (arr[:, :, 2] < 210)).astype('uint8')
    pellet_mask[:100, :] = 0
    pellet_mask[560:, :] = 0

    # Local corridor dot radar: Count actual dots directly ahead (15 to 70px) in each corridor
    immediate_dots = {'up': 0, 'down': 0, 'left': 0, 'right': 0}
    nearest_dot_dir = 'right'
    pellet_counts = {'up': 0, 'down': 0, 'left': 0, 'right': 0}
    if player:
        span = 65
        immediate_dots = {
            'left': int(np.count_nonzero(pellet_mask[max(0, py_int-8):min(h, py_int+8), max(0, px_int-span):max(0, px_int-15)])),
            'right': int(np.count_nonzero(pellet_mask[max(0, py_int-8):min(h, py_int+8), min(w, px_int+15):min(w, px_int+span)])),
            'up': int(np.count_nonzero(pellet_mask[max(0, py_int-span):max(0, py_int-15), max(0, px_int-8):min(w, px_int+8)])),
            'down': int(np.count_nonzero(pellet_mask[min(h, py_int+15):min(h, py_int+span), max(0, px_int-8):min(w, px_int+8)]))
        }

        # Vector towards the nearest remaining dot on the entire board
        dot_y, dot_x = np.where(pellet_mask > 0)
        if len(dot_x) > 0:
            pellet_counts = {
                'up': int(np.count_nonzero(dot_y < py_int)),
                'down': int(np.count_nonzero(dot_y > py_int)),
                'left': int(np.count_nonzero(dot_x < px_int)),
                'right': int(np.count_nonzero(dot_x > px_int))
            }
            dists = np.hypot(dot_x - px_int, dot_y - py_int)
            min_i = np.argmin(dists)
            tdx = dot_x[min_i] - px_int
            tdy = dot_y[min_i] - py_int
            if abs(tdx) > abs(tdy):
                nearest_dot_dir = 'right' if tdx > 0 else 'left'
            else:
                nearest_dot_dir = 'down' if tdy > 0 else 'up'

    is_game_over = player is None and np.count_nonzero(g_mask) > 1500
    is_ready = player is None and not is_game_over

    return {
        'player': player,
        'ghost': ghost,
        'ghost_dist': ghost_dist,
        'threat_dir': threat_dir,
        'open_corridors': open_corridors,
        'immediate_dots': immediate_dots,
        'nearest_dot_dir': nearest_dot_dir,
        'pellet_counts': pellet_counts,
        'status': 'game_over' if is_game_over else ('ready' if is_ready else 'active')
    }


def run_smart_pacman(duration=0, model_name='d1:latest', continuous=False):
    """Robust, collision-aware Pac-Man controller powered by d1 on GPU."""
    OUT.mkdir(parents=True, exist_ok=True)
    if not focus_window():
        raise SystemExit(f"Cannot focus Pac-Man window {WINDOW}")

    stop_event = threading.Event()

    def on_press(key):
        if key == macro.pynput.keyboard.Key.esc:
            print("\n[Pacman] Esc pressed. Exiting cleanly.")
            stop_event.set()

    listener = macro.pynput.keyboard.Listener(on_press=on_press)
    listener.start()

    model = LocalDecider(model=model_name, timeout=2)

    mode_desc = "Continuous Endless Loop" if (continuous or duration <= 0) else f"{duration}s limit"
    print(f"=== Robust Smart Pac-Man Agent Running with {model_name} on GPU ===")
    print(f"Mode: {mode_desc} | Collision-aware corridor filtering + Junction d1 routing + Ghost evasion")
    print(f"Press ESC or create {OUT / 'STOP'} to stop\n")

    started = time.monotonic()
    step = 0
    last_unpause = 0.0
    last_pos = None
    stuck_ticks = 0
    current_heading = 'right'
    OPPOSITE = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}

    try:
        with mss.MSS() as sct:
            while not stop_event.is_set():
                if (OUT / 'STOP').exists():
                    print("[Pacman] STOP file detected. Stopping.")
                    break
                if not continuous and duration > 0 and (time.monotonic() - started) >= duration:
                    break

                if not focused():
                    time.sleep(0.05)
                    continue

                t_start = time.monotonic()
                # Capture BGRA and reorder channels into RGB (this indexing makes a copy).
                raw = sct.grab(MSS_MONITOR)
                img = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))[:, :, [2, 1, 0]]
                st = fast_state_extractor(img)

                # Handle start screen or game over cleanly
                if st['status'] in ['game_over', 'ready']:
                    if time.monotonic() - last_unpause > 2.5:
                        print(f"[{st['status'].upper()}] Starting/unpausing with space...")
                        send_key('space', duration=0.08)
                        last_unpause = time.monotonic()
                        current_heading = 'right'
                    time.sleep(0.2)
                    continue

                # Track movement progress to detect collision stops
                if st['player'] and last_pos:
                    dist = np.hypot(st['player'][0] - last_pos[0], st['player'][1] - last_pos[1])
                    stuck_ticks = stuck_ticks + 1 if dist < 0.010 else 0
                last_pos = st['player']

                open_dirs = st['open_corridors']
                # If stuck against a wall, exclude current heading to force an immediate pivot
                if stuck_ticks >= 2 and current_heading in open_dirs and len(open_dirs) > 1:
                    open_dirs = [d for d in open_dirs if d != current_heading]

                # Fast corridor shortcut: auto-follow single corridor without model delay
                if len(open_dirs) == 1:
                    action = open_dirs[0]
                    if action != current_heading or stuck_ticks >= 1:
                        send_key(action, duration=0.05)
                        current_heading = action
                    time.sleep(0.05)
                    continue

                # Filter out directions leading directly towards an imminent dangerous ghost
                safe_dirs = open_dirs
                if st['ghost_dist'] < 0.22 and len(safe_dirs) > 1:
                    evade_dirs = [d for d in safe_dirs if d != st['threat_dir']]
                    if evade_dirs:
                        safe_dirs = evade_dirs

                # Momentum rule: Avoid reversing 180 degrees in a hallway unless stuck or evading ghost
                opp = OPPOSITE.get(current_heading)
                if stuck_ticks == 0 and st['ghost_dist'] >= 0.22 and len(safe_dirs) > 1 and opp in safe_dirs:
                    non_reverse = [d for d in safe_dirs if d != opp]
                    if non_reverse:
                        safe_dirs = non_reverse

                # Build differentiated criteria for d1 to evaluate
                criteria = {}
                for d in safe_dirs:
                    immed = st['immediate_dots'].get(d, 0)
                    pellets = st['pellet_counts'].get(d, 0)
                    is_threat = (d == st['threat_dir'] and st['ghost_dist'] < 0.22)
                    if is_threat:
                        criteria[d] = f"DANGER: Ghost approaching from {d} (distance {st['ghost_dist']})"
                    elif immed > 0:
                        criteria[d] = f"PRIORITY FOOD: Corridor directly ahead contains {immed} fresh dots to eat"
                    elif d == st['nearest_dot_dir']:
                        criteria[d] = f"HEADING TOWARDS DOTS: Leads to nearest cluster with {pellets} pellets"
                    else:
                        criteria[d] = f"Open corridor with {pellets} pellets"

                state_summary = {
                    'pacman_position': st['player'],
                    'current_heading': current_heading,
                    'open_corridors': safe_dirs,
                    'ghost_threat_distance': st['ghost_dist'],
                    'ghost_threat_direction': st['threat_dir'],
                    'immediate_corridor_dots': st['immediate_dots'],
                    'nearest_dot_direction': st['nearest_dot_dir'],
                    'is_stuck_at_corner': stuck_ticks >= 2
                }

                goal = "Select the optimal open corridor to eat pellets and survive."
                latency = 0.0
                probs = None

                try:
                    dec = model.choose(
                        state=state_summary,
                        actions=criteria,
                        goal=goal,
                        observed_at=t_start
                    )
                    action = dec.action
                    latency = dec.latency_ms
                    probs = dec.probabilities
                except DecisionError:
                    action = max(safe_dirs, key=lambda d: (st['immediate_dots'].get(d, 0), st['pellet_counts'].get(d, 0)))

                # Send steering pulse if heading changed or stuck
                if action != current_heading or stuck_ticks >= 1:
                    send_key(action, duration=0.05)
                    current_heading = action

                prob_str = f"(p={probs[action]:.2f})" if probs and action in probs else ""
                loop_ms = (time.monotonic() - t_start) * 1000
                print(f"Step {step:03d}: {action.upper():<5} {prob_str:<9} | Open: {safe_dirs} | d1: {latency:.1f}ms | Loop: {loop_ms:.1f}ms | Ghost: {st['threat_dir']} ({st['ghost_dist']})")

                # Export real-time status for external monitor
                try:
                    (OUT / 'live_status.json').write_text(json.dumps({
                        'step': step,
                        'elapsed_s': round(time.monotonic() - started, 1),
                        'heading': current_heading,
                        'action': action,
                        'open_corridors': safe_dirs,
                        'd1_latency_ms': round(latency, 1),
                        'ghost_threat': st['threat_dir'],
                        'ghost_distance': st['ghost_dist'],
                        'status': st['status']
                    }))
                except Exception:
                    pass

                step += 1
                time.sleep(0.04)

    finally:
        listener.stop()
        print(f"\n=== Completed {step} junction decisions in {time.monotonic() - started:.1f}s ===")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=0, help="Play duration in seconds (0 for continuous endless)")
    parser.add_argument('--continuous', action='store_true', help="Run endless loop indefinitely")
    parser.add_argument('--model', default='d1:latest', help="Decision model name")
    args = parser.parse_args()
    run_smart_pacman(duration=args.seconds, model_name=args.model, continuous=args.continuous or (args.seconds <= 0))


if __name__ == '__main__':
    main()
