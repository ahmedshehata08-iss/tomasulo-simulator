"""Independently audit assignment-profile sample timing JSON; Author: Ahmed Ahmed.

No simulator modules are imported. Each row is parsed from assembly text, then
an independent per-cycle constraints check reconstructs greedy ISSUE/EX/MEM/WB/
COMMIT selections. Scope: the two branch-free, load-only sample programs.

Run from any directory: python verify_assignment_timings.py [results_directory]
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def parse_rows(data):
    rows, writers = [], {}
    for trace in data['instructions']:
        words = trace['text'].replace(',', ' ').split()
        op, destination = words[:2]
        if op == 'Ld':
            kind = 'memory'
            match = re.fullmatch(r'[+-]?\d+\((R\d+)\)', ''.join(words[2:]))
            check(match is not None, 'Malformed sample load address')
            sources = [match[1]]
        else:
            check(op in ('Add', 'Addi', 'Sub', 'Add.d', 'Sub.d', 'Mult.d'),
                  f'Unsupported operation in branch-free audit: {op}')
            kind = 'fp_mul' if op == 'Mult.d' else 'fp_add' if '.d' in op else 'int'
            sources = words[2:3] if op == 'Addi' else words[2:4]
        row = dict(trace)
        row['kind'] = kind
        row['dependencies'] = [writers[name] for name in sources if name != 'R0' and name in writers]
        row['completion'] = row['mem_end'] if kind == 'memory' else row['exe_end']
        check(not row.get('squashed', False) and row.get('status', 'committed') == 'committed',
              'Sample table must contain committed rows only')
        rows.append(row)
        if destination != 'R0':
            writers[destination] = row
    return rows


def verify(path):
    data = json.loads(path.read_text())
    config = data['config']
    width = config['issue_width']
    rows = parse_rows(data)
    context = path.name
    check(len(rows) == 17, f'{context}: expected 17 rows')
    check([row['id'] for row in rows] == list(range(1, 18)), f'{context}: dynamic IDs')
    check([row['pc'] for row in rows] == list(range(17)), f'{context}: instruction order')
    check(config['writeback_width'] == config['commit_width'] == config['mem_units'] == 1,
          f'{context}: assignment requires one CDB, commit width one and one memory port')
    check(config['execution_order'] == 'out_of_order', f'{context}: scheduler profile')
    capacity = {'int': config['rs_int'], 'fp_add': config['rs_fp_add'],
                'fp_mul': config['rs_fp_mul'], 'memory': config['lsq_size']}
    units = {'int': config['int_units'], 'fp_add': config['fp_add_units'],
             'fp_mul': config['fp_mul_units'], 'memory': config['addr_units']}
    latency = {'int': config['int_latency'], 'fp_add': config['fp_add_latency'],
               'fp_mul': config['fp_mul_latency'], 'memory': config['addr_latency']}
    for row in rows:
        prefix = f'{context}, instruction {row["id"]}'
        check(row['exe_start'] > row['issue'], prefix + ': ISSUE must precede EX')
        check(row['exe_end'] - row['exe_start'] + 1 == latency[row['kind']],
              prefix + ': incorrect EX duration')
        check(all(dep['wb'] < row['exe_start'] for dep in row['dependencies']),
              prefix + ': dependent EX must start after producer WB')
        if row['kind'] == 'memory':
            check(row['mem_start'] > row['exe_end'], prefix + ': EX must precede MEM')
            check(row['mem_end'] - row['mem_start'] + 1 == config['mem_latency'],
                  prefix + ': incorrect memory duration')
            check(row['forwarded_from'] is None, prefix + ': no stores exist in this sample')
        else:
            check(row['mem_start'] is None and row['mem_end'] is None,
                  prefix + ': arithmetic cannot use MEM')
        check(row['wb'] > row['completion'], prefix + ': WB must follow completion')
        check(row['commit'] > row['wb'], prefix + ': commit must follow WB')
    check(data['cycles'] == max(row['commit'] for row in rows), context + ': total cycles')
    check(data['instruction_count'] == 17, context + ': committed instruction count')
    for cycle in range(1, data['cycles'] + 1):
        prefix = f'{context}, cycle {cycle}'
        # COMMIT precedes all other stages and retires the oldest ready ROB head.
        pending = [row for row in rows if row['commit'] >= cycle]
        expected = [pending[0]['id']] if pending and pending[0]['wb'] < cycle else []
        actual = [row['id'] for row in rows if row['commit'] == cycle]
        check(actual == expected, prefix + f': COMMIT {actual} != {expected}')
        # One CDB, arbitrated by completion cycle then dynamic age.
        eligible = sorted((row for row in rows if row['completion'] < cycle <= row['wb']),
                          key=lambda row: (row['completion'], row['id']))
        expected = [row['id'] for row in eligible[:1]]
        actual = [row['id'] for row in rows if row['wb'] == cycle]
        check(actual == expected, prefix + f': WB {actual} != {expected}')
        # The memory port stays occupied through its last cycle, then is reusable.
        occupied = sum(row['kind'] == 'memory' and row['mem_start'] < cycle <= row['mem_end']
                       for row in rows)
        check(occupied <= 1, prefix + ': overlapping memory accesses')
        expected = [row['id'] for row in rows if row['kind'] == 'memory'
                    and row['exe_end'] < cycle <= row['mem_start']][:1 - occupied]
        actual = [row['id'] for row in rows if row['mem_start'] == cycle]
        check(actual == expected, prefix + f': MEM {actual} != {expected}')
        # Integer/address units are unpipelined; FP units have initiation interval one.
        slots = dict(units)
        for kind in ('int', 'memory'):
            slots[kind] -= sum(row['kind'] == kind and row['exe_start'] < cycle <= row['exe_end']
                               for row in rows)
            check(slots[kind] >= 0, prefix + f': overlapping unpipelined {kind} unit')
        expected = []
        for row in rows:
            if row['issue'] >= cycle or row['exe_start'] < cycle:
                continue
            if any(dep['wb'] >= cycle for dep in row['dependencies']):
                continue
            if slots[row['kind']] > 0:
                expected.append(row['id'])
                slots[row['kind']] -= 1
        actual = [row['id'] for row in rows if row['exe_start'] == cycle]
        check(actual == expected, prefix + f': EX {actual} != {expected}')
        # ISSUE sees resources freed by this cycle's earlier stages.
        rob = sum(row['issue'] < cycle < row['commit'] for row in rows)
        rs = Counter(row['kind'] for row in rows
                     if row['kind'] != 'memory' and row['issue'] < cycle < row['exe_start'])
        rs['memory'] = sum(row['kind'] == 'memory' and row['issue'] < cycle < row['mem_end']
                           for row in rows)
        expected = []
        for row in rows:
            if row['issue'] < cycle:
                continue
            if rob >= config['rob_size'] or rs[row['kind']] >= capacity[row['kind']]:
                break
            expected.append(row['id'])
            rob += 1
            rs[row['kind']] += 1
            if len(expected) == width:
                break
        actual = [row['id'] for row in rows if row['issue'] == cycle]
        check(actual == expected, prefix + f': ISSUE {actual} != {expected}')
    return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'issue_width': width, 'cycles': data['cycles'], 'rows_checked': len(rows), 'status': 'PASS'}


def main():
    here = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        results = Path(sys.argv[1])
    else:
        candidates = (here.parent / 'results', here.parent / 'solution' / 'results')
        results = next((path for path in candidates if path.is_dir()), candidates[0])
    paths = [results / f'experiment_sample_case{case}_assignment_w{width}.json'
             for case in (1, 2) for width in range(1, 5)]
    findings = [verify(path) for path in paths]
    summary = {'author': 'Ahmed Ahmed', 'tables_checked': len(findings),
               'rows_checked': sum(item['rows_checked'] for item in findings),
               'method': 'Independent per-cycle constraints and greedy selections; no simulator imports',
               'status': 'PASS', 'findings': findings}
    output = here / 'assignment_timing_audit.json'
    output.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
