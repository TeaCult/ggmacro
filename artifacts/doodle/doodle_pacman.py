"""Standalone Google Doodle Pac-Man macro.

Dependencies: pip install mss numpy opencv-python pynput pywinctl
Run: python doodle_pacman.py --seconds 60 --window Firefox
Uses an existing browser window; opens a new tab. Escape/Ctrl+C/focus loss stop.
Ollama d1 must already be serving at --url. No repository imports required.
"""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler

DIRS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, -1), 'down': (0, 1)}


class D1:
    def __init__(self, url, model, timeout):
        self.url, self.model, self.timeout = url.rstrip('/'), model, timeout
        self.http = build_opener(ProxyHandler({}))

    def choose(self, state, actions):
        start = time.monotonic()
        payload = {'model': self.model, 'keep_alive': '10m', 'state': json.dumps(state),
                   'questions': {'action': {'type': 'choice', 'instructions':
                   'Collect pellets. Prefer short pellet routes and large ghost distance. Avoid danger.',
                   'criteria': actions}}}
        req = Request(self.url + '/v1/systemone', json.dumps(payload).encode(),
                      {'Content-Type': 'application/json'})
        with self.http.open(req, timeout=self.timeout) as response:
            answer = json.load(response)['answers']['action']['choice']
        if answer not in actions:
            raise ValueError('d1 returned an unpermitted action')
        return answer, (time.monotonic() - start) * 1000


def blobs(mask):
    import cv2
    n, _, stats, centers = cv2.connectedComponentsWithStats(mask.astype('uint8'))
    return [(stats[i], centers[i]) for i in range(1, n)]


class Maze:
    def __init__(self, rgb):
        import numpy as np
        r, g, b = rgb.transpose(2, 0, 1)
        blue = (b > 130) & (r < 90) & (g < 150)
        # Long horizontal blue borders identify this doodle, regardless of zoom.
        ys = np.where(blue.sum(axis=1) > rgb.shape[1] * .65)[0]
        if len(ys) < 2:
            raise ValueError('Doodle maze border not visible; keep the full maze in the browser')
        top, bottom = int(ys.min()), int(ys.max())
        xs = np.where(blue[top:bottom+1].any(axis=0))[0]
        self.rect = (int(xs.min()), top, int(xs.max()) + 1, bottom + 1)
        a = rgb[top:bottom+1, xs.min():xs.max()+1]
        r, g, b = a.transpose(2, 0, 1)
        pink = (r > 200) & (g > 120) & (g < 210) & (b > 100) & (b < 210)
        points = [c for s, c in blobs(pink) if 2 <= s[2] <= a.shape[1] / 100
                  and 2 <= s[3] <= a.shape[1] / 100 and s[4] >= 4]
        if len(points) < 30:
            raise ValueError('Not enough visible pellets for calibration')
        points = np.array(points)
        ux = np.unique(np.round(points[:, 0], 1))
        gaps = np.diff(ux)
        self.step = float(np.median(gaps[gaps > 2]))
        self.x0, self.y0 = points.min(axis=0)
        # Fit all repeated dot coordinates; a median adjacent gap alone drifts at far columns.
        indices = np.round((points[:,0]-self.x0)/self.step)
        self.step, self.x0 = np.polyfit(indices, points[:,0], 1)
        yi = np.round((points[:,1]-self.y0)/self.step)
        self.y0 = float(np.median(points[:,1]-yi*self.step))
        self.cols = round((points[:, 0].max() - self.x0) / self.step) + 1
        self.rows = round((points[:, 1].max() - self.y0) / self.step) + 1
        # Static thin colored walls. Remove compact solid sprites before building paths.
        wall = ((a.max(axis=2).astype(int) - a.min(axis=2)) > 150) & (a.min(axis=2) < 90)
        for s, _ in blobs(wall):
            x, y, w, h, area = s
            if w < self.step * 2 and h < self.step * 2 and area > self.step ** 2 * .5:
                wall[y:y+h, x:x+w] = False
        self.graph = {}
        for y in range(self.rows):
            for x in range(self.cols):
                p = (x, y)
                px, py = self.pixel(p)
                if wall[max(0,py-2):py+3, max(0,px-2):px+3].any():
                    continue
                self.graph[p] = {}
                for name, (dx, dy) in DIRS.items():
                    q = x+dx, y+dy
                    if not (0 <= q[0] < self.cols and 0 <= q[1] < self.rows):
                        continue
                    qx, qy = self.pixel(q)
                    xx = np.linspace(px, qx, 24).round().astype(int)
                    yy = np.linspace(py, qy, 24).round().astype(int)
                    if not wall[yy, xx].any():
                        self.graph[p][name] = q
        for p in self.graph:
            self.graph[p] = {d:q for d,q in self.graph[p].items() if q in self.graph}
        print(f'Calibrated {self.cols}x{self.rows}, tile={self.step:.2f}px, nodes={len(self.graph)}', flush=True)

    def pixel(self, p):
        return round(self.x0 + p[0]*self.step), round(self.y0 + p[1]*self.step)

    def cell(self, xy):
        return round((xy[0]-self.x0)/self.step), round((xy[1]-self.y0)/self.step)

    def observe(self, rgb):
        import numpy as np
        r, g, b = rgb.transpose(2, 0, 1)
        yellow = (r > 210) & (g > 210) & (b < 70)
        candidates = [(s,c) for s,c in blobs(yellow)
                      if s[4] > self.step**2*.65 and self.step < s[2] < self.step*1.9
                      and self.step < s[3] < self.step*1.9]
        # Clyde is yellow too. White eye pixels distinguish him from Pac-Man.
        white = (r > 210) & (g > 210) & (b > 210)
        candidates = [(s,c) for s,c in candidates
                      if not white[s[1]:s[1]+s[3], s[0]:s[0]+s[2]].any()]
        player = max(candidates, key=lambda sc:sc[0][4])[1] if candidates else None
        pink = (r > 200) & (g > 120) & (g < 210) & (b > 100) & (b < 210)
        pellets = set()
        for s,c in blobs(pink):
            if s[4] >= 4 and s[2] < self.step*.6 and s[3] < self.step*.6:
                p = self.cell(c)
                if p in self.graph:
                    pellets.add(p)
        threats = ((r > 190) & (g < 90) & (b < 90)) | ((r < 90) & (g > 180) & (b > 180))
        threats |= (r > 220) & (g > 100) & (g < 200) & (b > 130)
        ghosts = [self.cell(c) for s,c in blobs(threats) if s[4] > self.step**2*.45
                  and self.step*1.15 < s[2] < self.step*2
                  and self.step*1.15 < s[3] < self.step*2]
        return player, pellets, ghosts

    def choices(self, p, pellets, ghosts, visits):
        choices = {}
        for name, q in self.graph.get(p, {}).items():
            queue = deque([(q, 0)])
            seen = {p, q}
            distance = 999
            while queue:
                node, d = queue.popleft()
                if node in pellets:
                    distance = d
                    break
                for neighbor in self.graph.get(node, {}).values():
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, d+1))
            danger = min((abs(q[0]-g[0])+abs(q[1]-g[1]) for g in ghosts), default=99)
            score = distance + visits.get(q, 0)*.7 + max(0, 5-danger)*9
            choices[name] = {'pellet_distance': distance, 'ghost_distance': danger,
                             'score': round(score, 2)}
        safe = {d:v for d,v in choices.items() if v['ghost_distance'] > 2}
        return safe or choices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--window', default='Firefox', help='Existing browser title substring; must match one window')
    parser.add_argument('--mode', choices=['d1', 'rules'], default='d1')
    parser.add_argument('--url', default='http://127.0.0.1:11435')
    parser.add_argument('--model', default='d1:latest')
    parser.add_argument('--timeout', type=float, default=2)
    parser.add_argument('--stop-file', type=Path, default=Path(__file__).with_suffix('.STOP'))
    parser.add_argument('--check', action='store_true', help='Validate dependencies/options without desktop input')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('seconds and timeout must be positive finite values')
    import cv2
    import mss
    import numpy as np
    import pywinctl as pw
    from pynput.keyboard import Controller, Key, Listener
    if args.check:
        print('Dependencies/options OK; no desktop input sent.')
        return
    if args.stop_file.exists():
        print(f'Stopped: stop file exists: {args.stop_file}')
        return
    if os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        raise RuntimeError('Native Wayland window management is unsupported')
    windows = [w for w in pw.getAllWindows() if args.window.casefold() in (w.title or '').casefold()]
    if len(windows) != 1:
        raise RuntimeError(f'Expected one browser window; found {[w.title for w in windows]}. Use --window.')
    w = windows[0]
    model = D1(args.url, args.model, args.timeout)
    if args.mode == 'd1':
        # Warm up before opening the game; model availability is mandatory in d1 mode.
        warm = D1(args.url, args.model, max(30, args.timeout))
        print('d1 warmup:', warm.choose({'pellet_distance': {'left': 1, 'right': 10}},
              {'left': 'pellet nearby', 'right': 'pellets far away'}), flush=True)
    stop = threading.Event()
    deadline = time.monotonic() + args.seconds
    keyboard = Controller()
    held = None
    listener = Listener(on_press=lambda key: stop.set() if key == Key.esc else None)
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    def guard():
        if stop.is_set() or args.stop_file.exists() or time.monotonic() >= deadline:
            raise InterruptedError('Stop signal or deadline')
        if not w.isAlive or not w.isActive:
            raise InterruptedError('Browser lost focus')
    def tap(key):
        guard()
        keyboard.press(key)
        try:
            time.sleep(.04)
        finally:
            keyboard.release(key)
    def chord(key):
        guard()
        modifier = Key.cmd if __import__('sys').platform == 'darwin' else Key.ctrl
        keyboard.press(modifier)
        try:
            tap(key)
        finally:
            keyboard.release(modifier)
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        time.sleep(.25)
        guard()
        listener.start()
        chord('t')
        keyboard.type('https://www.google.com/logos/2010/pacman10-i.html')
        tap(Key.enter)
        with mss.MSS() as capture:
            maze = None
            load_deadline = min(deadline, time.monotonic()+15)
            while time.monotonic() < load_deadline:
                guard()
                desktop = capture.monitors[0]
                left, top = max(w.left,desktop['left']), max(w.top,desktop['top'])
                right = min(w.right, desktop['left']+desktop['width'])
                bottom = min(w.bottom, desktop['top']+desktop['height'])
                box = {'left':left, 'top':top, 'width':right-left, 'height':bottom-top}
                rgb = np.array(capture.grab(box))[:, :, [2,1,0]]
                try:
                    maze = Maze(rgb)
                    break
                except ValueError:
                    time.sleep(.2)
            if maze is None:
                raise RuntimeError('Could not calibrate loaded doodle')
            x1,y1,x2,y2 = maze.rect
            region = {'left':box['left']+x1, 'top':box['top']+y1, 'width':x2-x1, 'height':y2-y1}
            window_box = (w.left,w.top,w.width,w.height)
            heading, last_cell, visits = None, None, {}
            frames, calls, applied = 0, 0, 0
            started = last_seen = last_report = time.monotonic()
            last_request, last_motion = 0., started
            previous = None
            recommendation = None
            best_pellets, progress_at = 10000, started
            while True:
                guard()
                if (w.left,w.top,w.width,w.height) != window_box:
                    raise InterruptedError('Browser geometry changed; rerun to recalibrate')
                tick = time.monotonic()
                rgb = np.array(capture.grab(region))[:, :, [2,1,0]]
                player, pellets, ghosts = maze.observe(rgb)
                if len(pellets) < best_pellets:
                    best_pellets, progress_at = len(pellets), tick
                if tick-progress_at > 12:
                    print('No pellet progress for 12s: stopping for inspection.', flush=True)
                    break
                frames += 1
                if player is None:
                    if held is not None:
                        keyboard.release(held)
                        held = None
                    if tick-last_seen > 5:
                        print('Player absent for 5s: stopped at menu/result/uncertain screen.', flush=True)
                        break
                    stop.wait(.03)
                    continue
                last_seen = tick
                p = maze.cell(player)
                if previous is None or np.linalg.norm(player-previous) > maze.step*.12:
                    last_motion, previous = tick, player.copy()
                choices = maze.choices(p, pellets, ghosts, visits)
                if not choices:
                    stop.wait(.03)
                    continue
                cx,cy = maze.pixel(p)
                centered = max(abs(player[0]-cx),abs(player[1]-cy)) < maze.step*.28
                stalled = tick-last_motion > .65
                selected = min(choices, key=lambda d:choices[d]['score'])
                if future is not None and future.done():
                    try:
                        action, latency = future.result()
                        if request_cell == p and tick-request_time < .25 and action in choices:
                            recommendation = (action, request_cell, request_time)
                        print(f'd1 {action}, {latency:.1f}ms', flush=True)
                    except Exception as exc:
                        raise RuntimeError(f'd1 failed; stopped: {exc}') from exc
                    future = None
                use_d1 = (recommendation is not None and recommendation[1] == p
                          and tick-recommendation[2] < .25 and recommendation[0] in choices)
                if use_d1:
                    selected = recommendation[0]
                if args.mode == 'd1' and len(choices)>2 and future is None and tick-last_request>.4:
                    request_cell, request_time = p, tick
                    actions = {d:json.dumps(v) for d,v in choices.items()}
                    future = pool.submit(model.choose, {'heading':heading,'moves':choices}, actions)
                    last_request, calls = tick, calls+1
                if (centered and (p != last_cell or use_d1)) or heading is None or stalled:
                    if stalled and heading in choices and len(choices)>1:
                        alternatives = {d:v for d,v in choices.items() if d != heading}
                        selected = min(alternatives, key=lambda d:alternatives[d]['score'])
                        last_motion = tick
                    heading = selected
                    if use_d1 and not stalled:
                        applied += 1
                        recommendation = None
                    if p != last_cell:
                        visits[p] = visits.get(p,0)+1
                    last_cell = p
                key = getattr(Key,heading)
                if held != key:
                    guard()
                    if held is not None:
                        keyboard.release(held)
                    keyboard.press(key)
                    held = key
                if tick-last_report > 2:
                    print(json.dumps({'elapsed':round(tick-started,1),'cell':p,'pellets':len(pellets),
                          'heading':heading,'fps':round(frames/(tick-started),1),'d1_calls':calls,
                          'd1_applied':applied}), flush=True)
                    last_report = tick
                stop.wait(max(0, .02-(time.monotonic()-tick)))
    except (InterruptedError, KeyboardInterrupt) as exc:
        print(f'Stopped: {exc}', flush=True)
    finally:
        if held is not None:
            keyboard.release(held)
        stop.set()
        listener.stop()
        if listener.ident is not None:
            listener.join(1)
        pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
