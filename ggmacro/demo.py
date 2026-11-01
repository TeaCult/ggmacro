"""Portable three-tier simulation. No desktop input; Ollama is optional.

Run from the checkout: python -m ggmacro.demo --seconds 6
Add --model d1:latest to use Ollama instead of a deterministic decision function.
The main thread represents the AI agent's slower monitoring loop.
"""
import argparse
import json
import math
import threading
import time

from ggmacro import ActionGate, Decision, LocalDecider, MacroLoom, SupervisedMacroRunner


def run(seconds=6, model=None):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('seconds must be positive and finite')
    lock = threading.Lock()
    state = {'position': 0, 'target': 20}
    deadline = time.monotonic() + seconds
    stopped = threading.Event()

    def permitted():
        return not stopped.is_set() and time.monotonic() < deadline

    def snapshot():
        with lock:
            return dict(state)

    # Tier 3: a bounded routine. Replace this with a trusted desktop callback.
    def step(loom):
        direction = loom.get_param('direction', 0)
        with lock:
            state['position'] += direction

    loom = MacroLoom(step, interval=0.05, check=permitted)
    selector = LocalDecider(model=model, timeout=2) if model else None
    actions = {'left': 'Move left', 'right': 'Move right', 'wait': 'Stay still'}
    gate = ActionGate(
        {name: (lambda value=value: loom.update_params(direction=value))
         for name, value in [('left', -1), ('right', 1), ('wait', 0)]},
        check=permitted, max_age_ms=500, max_actions=10000,
    )

    # Tier 2: local decisions or a task-specific function, at a faster rate.
    def decide(runner):
        observation = snapshot()
        observed_at = time.monotonic()
        if selector:
            decision = selector.choose(observation, actions, 'Move toward target; wait if aligned.',
                                       observed_at=observed_at)
        else:
            delta = observation['target'] - observation['position']
            action = 'right' if delta > 0 else 'left' if delta < 0 else 'wait'
            decision = Decision(action, observed_at, 0)
        gate.run(decision)

    runner = SupervisedMacroRunner(loom, decide, supervisor_interval=0.25,
                                   focus_check=permitted)
    try:
        runner.start()
        # Tier 1: the AI host would inspect telemetry/screens here, revise goals,
        # swap routines, or stop. It remains active while lower tiers work.
        while permitted():
            print(json.dumps({'tier': 'AI monitor', **snapshot(),
                              'macro_steps': loom.iteration_count}), flush=True)
            if runner.last_error or loom.last_error:
                raise RuntimeError('Lower tier failed') from (runner.last_error or loom.last_error)
            stopped.wait(min(1.0, max(0, deadline - time.monotonic())))
    finally:
        stopped.set()
        runner.stop(timeout=3)
    if runner.last_error or loom.last_error:
        raise RuntimeError('Lower tier failed') from (runner.last_error or loom.last_error)
    return snapshot()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=6)
    parser.add_argument('--model', help='Optional Ollama model, e.g. d1:latest')
    args = parser.parse_args()
    print(json.dumps({'final': run(args.seconds, args.model)}))


if __name__ == '__main__':
    main()
