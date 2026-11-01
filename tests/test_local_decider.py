import io
import ast
import json
import time
import unittest
from unittest.mock import Mock
from pathlib import Path
from ggmacro.local_decider import ActionGate, Decision, DecisionError, LocalDecider


class LocalDeciderTests(unittest.TestCase):
    def client(self, content):
        client = LocalDecider(model='qwen3.5:2b')
        response = io.BytesIO(json.dumps({
            'done': True, 'message': {'content': content}}).encode())
        client._http = Mock()
        client._http.open.return_value = response
        return client

    def test_allowed_choice_and_image_are_forwarded(self):
        client = self.client('{"action":"left"}')
        result = client.choose({}, {'left': 'left'}, 'move', image=b'png')
        self.assertEqual(result.action, 'left')
        request = client._http.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body['messages'][1]['images'], ['cG5n'])
        self.assertEqual(body['format']['properties']['action']['enum'], ['left'])

    def test_bad_answers_never_dispatch(self):
        for value in ['not json', '{"action":"shell"}',
                      '{"action":"left","code":"bad"}',
                      '{"action":[]}', '[]']:
            with self.subTest(value=value), self.assertRaises(DecisionError):
                self.client(value).choose({}, {'left': 'left'}, 'move')

    def test_stale_and_unregistered_and_focus_are_rejected(self):
        callback = Mock()
        gate = ActionGate({'left': callback}, check=lambda: False)
        for decision in [Decision('left', time.monotonic()-10, 1),
                         Decision('shell', time.monotonic(), 1),
                         Decision('left', time.monotonic(), 1)]:
            with self.assertRaises(DecisionError):
                gate.run(decision)
        callback.assert_not_called()

    def test_budget_and_callback_failure(self):
        callback = Mock(side_effect=RuntimeError('input failed'))
        gate = ActionGate({'left': callback}, check=lambda: True, max_actions=1)
        with self.assertRaises(RuntimeError):
            gate.run(Decision('left', time.monotonic(), 1))
        with self.assertRaises(DecisionError):
            gate.run(Decision('left', time.monotonic(), 1))
        self.assertEqual(callback.call_count, 1)

    def test_network_failure_does_not_choose_default_action(self):
        client = LocalDecider(model='qwen3.5:2b')
        client._http = Mock()
        client._http.open.side_effect = TimeoutError('late')
        with self.assertRaises(DecisionError):
            client.choose({}, {'left': 'left'}, 'move')

    def test_macro_capture_is_passed_to_selector_without_input(self):
        # Extract the helper without importing GUI backends in a unit test.
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'ggmacrolib.py').read_text())
        helper = next(n for n in tree.body
                      if isinstance(n, ast.FunctionDef) and n.name == 'chooseAction')
        image = Mock()
        image.save.side_effect = lambda stream, **kw: stream.write(b'png')
        capture = Mock(return_value=image)
        selector = Mock()
        env = {'time': time, 'screenshot': capture}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), '<helper>', 'exec'), env)
        actions = {'wait': 'wait'}
        env['chooseAction'](selector, {}, actions, 'inspect', region=(0, 0, 10, 10))
        capture.assert_called_once_with(region=(0, 0, 10, 10))
        self.assertEqual(selector.choose.call_args.kwargs['image'], b'png')

    def test_success_dispatches_only_selected_callback(self):
        left, right = Mock(), Mock()
        gate = ActionGate({'left': left, 'right': right}, check=lambda: True)
        gate.run(Decision('left', time.monotonic(), 1))
        left.assert_called_once_with()
        right.assert_not_called()

    def test_d1_systemone_decision(self):
        client = LocalDecider(model='d1:latest')
        resp = io.BytesIO(json.dumps({
            'model': 'd1:latest',
            'answers': {
                'action': {
                    'type': 'choice',
                    'choice': 'up',
                    'probabilities': {'up': 0.85, 'down': 0.15},
                    'confidence': 0.70
                }
            }
        }).encode())
        client._http = Mock()
        client._http.open.return_value = resp
        decision = client.choose({'pos': [0, 0]}, {'up': 'up', 'down': 'down'}, 'move')
        self.assertEqual(decision.action, 'up')
        self.assertAlmostEqual(decision.probabilities['up'], 0.85)
        self.assertAlmostEqual(decision.confidence, 0.70)
        req = client._http.open.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/v1/systemone'))



if __name__ == '__main__':
    unittest.main()


class DesktopAPITests(unittest.TestCase):
    """Exercise API contracts without loading a real desktop backend."""

    def setUp(self):
        import math
        from types import SimpleNamespace
        names = {'DesktopError', 'UnsupportedDesktopError', '_window_backend',
                 'find_windows', 'is_focused', 'focus_window', 'screenshot'}
        source = (Path(__file__).resolve().parents[1] / 'ggmacrolib.py').read_text()
        nodes = [n for n in ast.parse(source).body
                 if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
        self.backend = Mock()
        self.env = {'sys': SimpleNamespace(platform='linux'),
                    'os': SimpleNamespace(environ={'DISPLAY': ':test'}),
                    'importlib': Mock(), 'math': math, 'time': time,
                    'Image': Mock(), 'ImageGrab': Mock()}
        self.env['importlib'].import_module.return_value = self.backend
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<desktop>', 'exec'), self.env)

    def test_same_window_search_api_on_three_platforms(self):
        for platform in ('win32', 'darwin', 'linux'):
            self.env['sys'].platform = platform
            self.backend.getWindowsWithTitle.return_value = ['matched window']
            self.assertEqual(self.env['find_windows']('Firefox'), ['matched window'])
            self.backend.getWindowsWithTitle.assert_called_with(
                'Firefox', condition=self.backend.Re.CONTAINS,
                flags=self.backend.Re.IGNORECASE)

    def test_restricted_wayland_and_missing_dependency_are_explicit(self):
        self.env['os'].environ['WAYLAND_DISPLAY'] = 'wayland-0'
        with self.assertRaises(self.env['UnsupportedDesktopError']):
            self.env['find_windows']('game')
        self.env['os'].environ.pop('WAYLAND_DISPLAY')
        self.env['importlib'].import_module.side_effect = ImportError('missing')
        with self.assertRaisesRegex(self.env['DesktopError'], 'pip install pywinctl'):
            self.env['find_windows']('game')

    def test_restore_and_verified_focus_and_closed_window(self):
        window = Mock(isAlive=True, isMinimized=True, isActive=True)
        self.assertTrue(self.env['focus_window'](window, timeout=0))
        window.restore.assert_called_once_with(wait=False)
        window.activate.assert_called_once_with(wait=False)
        window.isActive = False
        self.assertFalse(self.env['focus_window'](window, timeout=0))
        window.isAlive = False
        self.assertFalse(self.env['is_focused'](window))
        self.assertFalse(self.env['focus_window'](window))

    def test_mss_uses_region_bounds_and_returns_rgb_without_closing_shared_capture(self):
        capture = Mock()
        raw = capture.grab.return_value
        raw.size = (20, 30)
        raw.bgra = b'pixels'
        result = self.env['screenshot']((-10, 5, 10, 35), backend='mss', capture=capture)
        capture.grab.assert_called_once_with({'left': -10, 'top': 5, 'width': 20, 'height': 30})
        self.env['Image'].frombytes.assert_called_once_with('RGB', (20, 30), b'pixels', 'raw', 'BGRX')
        self.assertIs(result, self.env['Image'].frombytes.return_value)
        capture.close.assert_not_called()

    def test_mss_full_desktop_and_pillow_compatibility(self):
        capture = Mock(monitors=[{'left': -100, 'top': 0, 'width': 200, 'height': 100}])
        self.env['screenshot'](backend='mss', capture=capture)
        capture.grab.assert_called_once_with(capture.monitors[0])
        self.env['screenshot']((0, 0, 10, 10), path='result.png')
        self.env['ImageGrab'].grab.assert_called_once_with(bbox=(0, 0, 10, 10))
        self.env['ImageGrab'].grab.return_value.save.assert_called_once_with('result.png')
        with self.assertRaises(ValueError):
            self.env['screenshot'](backend='unknown')
