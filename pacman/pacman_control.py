"""Session-specific Pac-Man observation and bounded input through ggmacro.

Supports manual control, dynamic window discovery, screenshot capture,
and standalone macro looms.
"""
import argparse
from pathlib import Path
import time
import ggmacrolib as macro

REGION = (1920, 0, 3840, 2160)
MAZE_REGION = (2480, 300, 3020, 980)
OUT = Path(__file__).resolve().parents[1] / 'artifacts' / 'pacman'
_KEYBOARD = macro.pynput.keyboard.Controller()



def find_pacman_window():
    """Find a game window through the portable macro API."""
    for title in ('pacman', 'pac-man', 'crazygames'):
        windows = macro.find_windows(title)
        if windows:
            return windows[0]
    return None


WINDOW = find_pacman_window()


def focused(target_window=None):
    """Require the selected game window to remain active."""
    target = target_window if target_window is not None else WINDOW
    if target is None:
        return False
    try:
        return macro.is_focused(target) and any(
            name in target.title.casefold() for name in ('pacman', 'pac-man', 'crazygames'))
    except macro.DesktopError:
        return False


def focus_window(target_window=None):
    """Request and verify focus through the portable macro API."""
    target = target_window if target_window is not None else WINDOW
    return macro.focus_window(target) and focused(target)


def capture(name='screen'):
    """Capture full screen and save both full resolution and preview thumbnails."""
    OUT.mkdir(parents=True, exist_ok=True)
    image = macro.screenshot(region=REGION, path=OUT / (name + '.png'))
    preview = image.copy()
    preview.thumbnail((960, 1080))
    preview.save(OUT / (name + '-preview.png'))
    return image


def send_key(name, duration=0.08):
    """Safely press and release an allowed navigation key."""
    allowed = ['left', 'right', 'up', 'down', 'enter', 'space']
    if name not in allowed:
        raise ValueError(f"Key '{name}' not permitted. Allowed: {allowed}")
    key_obj = getattr(macro.pynput.keyboard.Key, name, None)
    if key_obj is None:
        raise ValueError(f"Could not resolve key '{name}' in pynput")
    macro.presskey(key_obj, duration)


def fast_send_key(name):
    """Ultra-low-latency direct key tap (<0.4ms) without controller reallocation."""
    allowed = ['left', 'right', 'up', 'down', 'enter', 'space']
    if name not in allowed:
        raise ValueError(f"Key '{name}' not permitted. Allowed: {allowed}")
    key_obj = getattr(macro.pynput.keyboard.Key, name, None)
    if key_obj is None:
        raise ValueError(f"Could not resolve key '{name}' in pynput")
    _KEYBOARD.press(key_obj)
    _KEYBOARD.release(key_obj)


def click_pos(x, y):
    """Move cursor smoothly and click within the valid screen region."""
    if not (1920 <= x < 3840 and 180 <= y < 2100):
        raise ValueError(f"Coordinates ({x}, {y}) fall outside permitted game region")
    macro.move([x, y])
    macro.click()


def run_manual_loom(pattern='patrol', duration=10.0, step_interval=0.15):
    """Demonstrate a standalone manual MacroLoom running a scripted movement pattern."""
    if not focus_window():
        raise SystemExit("Error: Could not focus Pac-Man window for manual loom")

    patterns = {
        'patrol': ['left', 'up', 'right', 'down'],
        'horizontal': ['left', 'right'],
        'vertical': ['up', 'down'],
    }
    sequence = patterns.get(pattern, ['left', 'right'])

    def loom_routine(loom):
        if not focused():
            loom.pause()
            return
        idx = loom.get_param('seq_idx', 0)
        key_name = sequence[idx % len(sequence)]
        send_key(key_name, duration=0.08)
        loom.update_params(seq_idx=idx + 1)

    loom = macro.MacroLoom(routine=loom_routine, interval=step_interval, name=f"ManualLoom-{pattern}")
    print(f"Starting manual macro loom '{pattern}' for {duration} seconds...")
    loom.start()
    time.sleep(duration)
    loom.stop()
    print(f"Manual loom finished after {loom.iteration_count} macro steps.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['capture', 'click', 'key', 'focus', 'loom'])
    parser.add_argument('values', nargs='*')
    parser.add_argument('--window', default=None, help="Window title substring (not a platform-specific ID)")
    args = parser.parse_args()

    global WINDOW
    if args.window:
        matches = macro.find_windows(args.window)
        WINDOW = matches[0] if matches else None
    else:
        WINDOW = find_pacman_window()

    if args.action == 'focus':
        success = focus_window()
        print(f"Window {WINDOW} focus: {'SUCCESS' if success else 'FAILED'}")
        return

    if args.action == 'capture':
        name = args.values[0] if args.values else 'screen'
        focus_window()
        capture(name)
        print(f"Captured screen to {OUT / (name + '.png')}")
        return

    # For inputs, ensure window is focused
    if not focus_window():
        raise SystemExit(f"Stopped: Pac-Man window ({WINDOW}) is not focused")

    if args.action == 'click':
        if len(args.values) < 2:
            raise SystemExit("Usage: pacman_control.py click <x> <y>")
        x, y = map(int, args.values[:2])
        click_pos(x, y)
        time.sleep(0.3)
        capture('after-click')

    elif args.action == 'key':
        if not args.values:
            raise SystemExit("Usage: pacman_control.py key <key_name>")
        key_name = args.values[0]
        send_key(key_name)
        time.sleep(0.3)
        capture('after-key')

    elif args.action == 'loom':
        pattern = args.values[0] if args.values else 'patrol'
        run_manual_loom(pattern=pattern)
        capture('after-loom')


if __name__ == '__main__':
    main()
