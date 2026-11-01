"""Observe a selected screen region through ggmacro; never click or type."""
import argparse
import json
from ggmacro.local_decider import LocalDecider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', nargs=4, type=int, required=True,
                        metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    parser.add_argument('--model', default='d1:latest')
    args = parser.parse_args()
    import ggmacrolib as macro
    decision = macro.chooseAction(
        LocalDecider(args.model, timeout=120), state={},
        actions={'browser': 'A web browser window',
                 'terminal': 'A terminal or code editor',
                 'desktop': 'Desktop background or another application'},
        goal='Classify the main application visible in the captured region.',
        region=tuple(args.region))
    print(json.dumps({'action': decision.action, 'latency_ms': decision.latency_ms,
                      'region': args.region, 'desktop_input_executed': False}))


if __name__ == '__main__':
    main()
