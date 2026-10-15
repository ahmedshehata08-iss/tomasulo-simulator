#!/usr/bin/env python3
"""Run a configurable Tomasulo simulation using only Python's standard library."""
import argparse
import json
import sys
from pathlib import Path

from tomasulo.engine import Simulator
from tomasulo.output import format_result
from tomasulo.parser import parse_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', default='code.in', help='assembly/configuration input')
    parser.add_argument('--issue-width', type=int, choices=range(1, 5), help='override input width (1-4)')
    parser.add_argument('--max-cycles', type=int, default=100000)
    parser.add_argument('--json', dest='json_path', type=Path, help='write complete machine-readable results')
    parser.add_argument('--output', type=Path, help='also save the text results')
    parser.add_argument('--show-squashed', action='store_true', help='include wrong-path and replayed rows')
    args = parser.parse_args(argv)
    try:
        result = Simulator(parse_file(args.input), args.issue_width, args.max_cycles).run()
        report = format_result(result, args.show_squashed)
        if args.json_path:
            args.json_path.parent.mkdir(parents=True, exist_ok=True)
            args.json_path.write_text(json.dumps(result.to_dict(), indent=2) + '\n', encoding='utf-8')
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding='utf-8')
        print(report, end='')
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
