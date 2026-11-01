import io
import json
import time
import threading
import unittest
from unittest.mock import Mock

import ggmacro.runtime as macro
from ggmacro.local_decider import LocalDecider, DecisionError, SupervisorDecision


class MacroLoomTests(unittest.TestCase):
    def test_failed_routine_stops_and_exposes_error(self):
        attempted = threading.Event()
        error = RuntimeError('routine failed')

        def fail(loom):
            attempted.set()
            raise error

        loom = macro.MacroLoom(fail, interval=0.01)
        try:
            loom.start()
            self.assertTrue(attempted.wait(1))
        finally:
            loom.stop()
        self.assertIs(loom.last_error, error)
        self.assertEqual(loom.iteration_count, 0)

    def test_initial_focus_rejection_prevents_input(self):
        checked = threading.Event()
        routine = Mock()

        def focus():
            checked.set()
            return False

        loom = macro.MacroLoom(routine, interval=0.01)
        runner = macro.SupervisedMacroRunner(loom, Mock(), focus_check=focus)
        try:
            runner.start()
            self.assertTrue(checked.wait(1))
        finally:
            runner.stop()
        routine.assert_not_called()

    def test_supervisor_error_stops_macro_worker(self):
        attempted = threading.Event()
        error = RuntimeError('decision failed')

        def fail(runner):
            attempted.set()
            raise error

        loom = macro.MacroLoom(lambda worker: None, interval=0.01)
        runner = macro.SupervisedMacroRunner(loom, fail)
        try:
            runner.start()
            self.assertTrue(attempted.wait(1))
        finally:
            runner.stop()
        self.assertIs(runner.last_error, error)
        self.assertFalse(loom.is_running)

    def test_loom_execution_and_parameter_updates(self):
        records = []

        def sample_routine(loom):
            direction = loom.get_param('direction', 'none')
            records.append(direction)

        loom = macro.MacroLoom(routine=sample_routine, interval=0.02, name='TestLoom')
        loom.update_params(direction='right')
        loom.start()
        time.sleep(0.06)

        # Hot-swap parameters dynamically while running
        loom.update_params(direction='left')
        time.sleep(0.06)
        loom.stop(timeout=1.0)

        self.assertFalse(loom.is_running)
        self.assertGreater(len(records), 2)
        self.assertIn('right', records)
        self.assertIn('left', records)

    def test_loom_routine_hotswap(self):
        routine_a_called = []
        routine_b_called = []

        def routine_a(loom):
            routine_a_called.append(True)

        def routine_b(loom):
            routine_b_called.append(True)

        loom = macro.MacroLoom(routine=routine_a, interval=0.02)
        loom.start()
        time.sleep(0.05)

        # Hot-swap to routine B
        loom.set_routine(routine_b)
        time.sleep(0.05)
        loom.stop(timeout=1.0)

        self.assertTrue(len(routine_a_called) > 0)
        self.assertTrue(len(routine_b_called) > 0)

    def test_loom_pause_and_resume(self):
        count = []

        def tick(loom):
            count.append(1)

        loom = macro.MacroLoom(routine=tick, interval=0.02)
        loom.start()
        time.sleep(0.05)
        self.assertGreater(len(count), 0)

        loom.pause()
        paused_len = len(count)
        time.sleep(0.05)
        # Verify no or at most one execution during pause
        self.assertLessEqual(len(count), paused_len + 1)

        loom.resume()
        time.sleep(0.05)
        loom.stop(timeout=1.0)
        self.assertGreater(len(count), paused_len)

    def test_supervised_macro_runner_cycle(self):
        supervised_ticks = []

        def supervisor_callback(runner):
            supervised_ticks.append(time.monotonic())
            # Dynamically adjust loom parameter from supervisor
            runner.loom.update_params(supervisor_adjusted=True)

        mock_loom = macro.MacroLoom(routine=lambda l: time.sleep(0.01), interval=0.02)
        runner = macro.SupervisedMacroRunner(
            loom=mock_loom,
            supervisor_fn=supervisor_callback,
            supervisor_interval=0.05,
            focus_check=lambda: True
        )

        runner.start()
        time.sleep(0.15)
        runner.stop(timeout=1.0)

        self.assertGreater(len(supervised_ticks), 1)
        self.assertTrue(mock_loom.get_param('supervisor_adjusted'))

    def test_supervised_runner_focus_check(self):
        focus_state = [True]
        ticks = []

        def sup(runner):
            ticks.append(1)

        mock_loom = macro.MacroLoom(routine=lambda l: None, interval=0.02)
        runner = macro.SupervisedMacroRunner(
            loom=mock_loom,
            supervisor_fn=sup,
            supervisor_interval=0.03,
            focus_check=lambda: focus_state[0]
        )

        runner.start()
        time.sleep(0.05)
        self.assertGreater(len(ticks), 0)

        # Simulate focus loss
        focus_state[0] = False
        time.sleep(0.08)
        self.assertTrue(mock_loom._pause_event.is_set())

        runner.stop(timeout=1.0)


class SupervisoryDeciderTests(unittest.TestCase):
    def test_choose_supervisory_valid_response(self):
        client = LocalDecider(model='qwen3.5:2b')
        resp_payload = json.dumps({
            'done': True,
            'message': {
                'content': json.dumps({'action': 'evade', 'adjustment': 'bias_left'})
            }
        }).encode()
        client._http = Mock()
        client._http.open.return_value = io.BytesIO(resp_payload)

        decision = client.choose_supervisory(
            state={'ghost_near': True},
            actions={'evade': 'evade threat', 'continue': 'continue'},
            adjustments={'bias_left': 'steer left', 'bias_right': 'steer right'},
            goal='Survive'
        )

        self.assertIsInstance(decision, SupervisorDecision)
        self.assertEqual(decision.action, 'evade')
        self.assertEqual(decision.adjustment, 'bias_left')

    def test_choose_supervisory_invalid_action_rejected(self):
        client = LocalDecider(model='qwen3.5:2b')
        resp_payload = json.dumps({
            'done': True,
            'message': {
                'content': json.dumps({'action': 'unknown_action', 'adjustment': 'bias_left'})
            }
        }).encode()
        client._http = Mock()
        client._http.open.return_value = io.BytesIO(resp_payload)

        with self.assertRaises(DecisionError):
            client.choose_supervisory(
                state={},
                actions={'continue': 'continue'},
                adjustments={'bias_left': 'bias left'},
                goal='Survive'
            )


if __name__ == '__main__':
    unittest.main()
