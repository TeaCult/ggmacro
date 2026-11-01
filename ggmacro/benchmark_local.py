"""Benchmark local action selection without sending any desktop input."""
import argparse
import json
from pathlib import Path
import statistics
from ggmacro.local_decider import LocalDecider, DecisionError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='d1:latest')
    parser.add_argument('--url', default='http://127.0.0.1:11435')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--output', type=Path,
                        default=Path('artifacts/local-agent/benchmark.json'))
    args = parser.parse_args()
    model = LocalDecider(args.model, args.url, timeout=120)
    actions = {'left': 'Move the paddle left', 'right': 'Move the paddle right',
               'wait': 'Keep the paddle where it is'}
    goal = ('Align paddle_x with ball_x. Coordinates increase to the right. '
            'If the absolute difference is at most 5, wait.')
    rows = []
    for paddle, ball, expected in [(50, 90, 'right'), (50, 10, 'left'),
                                   (50, 50, 'wait'), (80, 20, 'left'),
                                   (20, 80, 'right'), (50, 53, 'wait')]:
        decision = model.choose({'paddle_x': paddle, 'ball_x': ball}, actions, goal)
        row = {'paddle_x': paddle, 'ball_x': ball, 'expected': expected,
               'action': decision.action, 'latency_ms': round(decision.latency_ms, 2)}
        rows.append(row)
        print(json.dumps(row), flush=True)
    report = {'model': args.model, 'cases': rows,
              'correct': sum(r['action'] == r['expected'] for r in rows),
              'total': len(rows),
              'warm_median_ms': round(statistics.median(
                  r['latency_ms'] for r in rows[1:]), 2),
              'note': 'Tiny synthetic smoke benchmark; not game performance or Jev parity.'}
    semantic = []
    for state, expected in [
            ('The ball is clearly to the LEFT of the paddle.', 'left'),
            ('The ball is clearly to the RIGHT of the paddle.', 'right'),
            ('The ball and paddle are already aligned.', 'wait')]:
        result = model.choose(state, actions,
                              'Move toward the ball, or wait if aligned.')
        semantic.append({'state': state, 'expected': expected,
                         'action': result.action,
                         'latency_ms': round(result.latency_ms, 2)})
    report['semantic_cases'] = semantic
    report['semantic_correct'] = sum(r['expected'] == r['action'] for r in semantic)
    if args.image:
        image_actions = {'yes': 'A browser window is visible',
                         'no': 'No browser window is visible'}
        try:
            result = model.choose({}, image_actions,
                                  'Is a browser window visible in the image?',
                                  image=args.image.read_bytes())
            report['image_smoke'] = {'action': result.action,
                                     'latency_ms': round(result.latency_ms, 2),
                                     'path': str(args.image)}
        except DecisionError as exc:
            report['image_smoke'] = {'error': str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
