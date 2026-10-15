#!/usr/bin/env python3
"""Reproduce every reported issue-width experiment and compare with the ISA oracle."""
import csv
import json
from pathlib import Path

from tests.oracle import execute
from tomasulo.engine import Simulator
from tomasulo.output import format_result
from tomasulo.parser import parse_file

ROOT = Path(__file__).resolve().parent
CASES = ('corrected_starter', 'mixed_independent', 'dependency_chain', 'forwarding',
         'branch_recovery', 'memory_replay', 'resource_pressure')


def main():
    directory = ROOT / 'results'
    directory.mkdir(exist_ok=True)
    summary = []
    for name in CASES:
        path = ROOT / 'benchmarks' / f'{name}.in'
        expected = execute(path.read_text())
        baseline = None
        for width in range(1, 5):
            result = Simulator(parse_file(path), issue_width=width).run()
            assert result.registers == expected.registers, (name, width, 'register mismatch')
            assert result.memory == expected.memory, (name, width, 'memory mismatch')
            assert result.stats['committed'] == expected.instructions
            baseline = baseline or result.cycles
            prefix = directory / f'{name}_w{width}'
            prefix.with_suffix('.txt').write_text(format_result(result, show_squashed=True))
            prefix.with_suffix('.json').write_text(json.dumps(result.to_dict(), indent=2) + '\n')
            row = dict(benchmark=name, issue_width=width, cycles=result.cycles,
                       committed=result.stats['committed'],
                       ipc=round(expected.instructions/result.cycles, 4),
                       speedup=round(baseline/result.cycles, 4),
                       mispredictions=result.stats['mispredictions'],
                       memory_replays=result.stats['memory_replays'],
                       forwarded_loads=result.stats['forwarded_loads'],
                       squashed=result.stats['squashed'], correctness='PASS')
            summary.append(row)
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (directory / 'summary.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    for name in CASES:
        rows = [r for r in summary if r['benchmark'] == name]
        print(f'{name:22} ' + ' '.join(f'W{r["issue_width"]}={r["cycles"]:3}' for r in rows)
              + f'  speedup W4={rows[-1]["speedup"]:.2f}x  PASS')
    print(f'{len(summary)} configurations matched the independent sequential interpreter.')


if __name__ == '__main__':
    main()
