"""Reproduce the 1,600-configuration review: python3 -m tests.stress_review."""
import random
import json
import time
from pathlib import Path
from tests.test_simulator import source
from tests.oracle import execute
from tomasulo.engine import Simulator
from tomasulo.parser import parse_text

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'results'
RESULTS.mkdir(exist_ok=True)

start = time.monotonic()
count = replays = 0
for seed in range(400):
    rng = random.Random(884231 + seed)
    code = []
    for _ in range(40):
        op = rng.randrange(6)
        if op < 2:
            code.append(f'Addi R{rng.randrange(1,8)} R0 {4*rng.randrange(8)}')
        elif op < 3:
            code.append(f'Ld F{rng.randrange(1,8)} 0(R{rng.randrange(8)})')
        elif op < 4:
            code.append(f'Sd F{rng.randrange(1,8)} 0(R{rng.randrange(8)})')
        else:
            code.append(f'{rng.choice(["Add.d", "Sub.d", "Mult.d"])} '
                        f'F{rng.randrange(1,8)} F{rng.randrange(1,8)} F{rng.randrange(1,8)}')
    text = source('\n'.join(code),
                  initial=','.join(f'F{i}={i/2}' for i in range(1,8)),
                  int_rs=rng.randint(1,8), fp_rs=rng.randint(1,8), mul_rs=rng.randint(1,8),
                  ls_rs=rng.randint(1,10), int_ex=rng.randint(1,8), fp_ex=rng.randint(1,6),
                  mul_ex=rng.randint(1,10), mem=rng.randint(1,5), int_fu=rng.randint(1,3),
                  ls_fu=rng.randint(1,3), rob=rng.randint(2,25))
    expected = execute(text)
    for width in range(1,5):
        actual = Simulator(parse_text(text), issue_width=width).run()
        if actual.registers != expected.registers or actual.memory != expected.memory:
            (RESULTS / 'stress_failure.in').write_text(text)
            raise AssertionError((seed, width, 'architectural state mismatch'))
        count += 1
        replays += actual.stats['memory_replays']
result = {'random_programs': 400, 'issue_widths': [1,2,3,4], 'configurations': count,
          'status':'PASS', 'memory_replays_observed':replays,
          'elapsed_seconds': round(time.monotonic()-start,2), 'seed_base':884231,
          'details':'40 instructions per program; computed byte-address bases, loads/stores, FP arithmetic, varied RS/ROB/FU/latency. Compared all register and memory values against independent sequential oracle.'}
(RESULTS / 'stress_review.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
