# Experiment inputs

Assignment modifications and additional experiments: **Ahmed Ahmed**.

`sample_case1.in` and `sample_case2.in` reproduce the two instruction lists in
the supplied sample report exactly. Their JSON files reproduce the hardware
tables. The sample's load/store unit count is represented by an equal number
of address-generation units and memory units. Arithmetic pipelines accept one
new instruction per unit per cycle; each memory unit handles one access at a
time.

The experiment runner explicitly initializes R1=12, R2=32, F20=3 and all other
registers to zero. Memory initially contains {4:3, 8:2, 12:1, 24:6, 28:5, 32:4};
uninitialized memory reads as zero. These details matter: the exact sample
uses F0, which is zero, and case 1 reads address 20, which is also zero.

`starter_branch.in` preserves the uploaded starter program. A branch target is
`next_instruction_index + displacement_in_bytes / 4`; its `-12` displacement
repeats only the pointer-update tail. It executes 15 dynamic instructions and
stores to address 32 once.

`full_loop.in` is an additional test, with `-32` replacing `-12` to repeat the
entire loop body three times. This input executes 25 dynamic instructions and
stores 7, 11, and 15 at addresses 32, 28, and 24. The original input is not
changed.

`independent_throughput.in` is an additional test of 24 independent integer
additions with four integer functional units. It exposes bandwidth changes
without a long dependency chain or memory bottleneck.

From the project root, run:

```bash
python run_experiments.py
```

Each of the five inputs runs at issue widths 1, 2, 3, and 4 under two policies:

| Policy | Issue width | Write-back width | Commit width |
| --- | --- | --- | --- |
| `scaled` | W | W | W |
| `fixed_backend` | W | 1 | 1 |

The first policy follows the sample report's multiple-issue, multiple-WB,
multiple-commit experiment. The second isolates issue-width changes. Each run
checks the full final architectural register file and memory against
hand-derived expected values, then writes its complete timing table and state
to `results/experiment_*.txt` and `results/experiment_*.json`. The summary files
include cycles, IPC, speedup relative to width 1 within the same policy, and
observed peak issue/WB/commit counts. Sample reference totals are included only
for comparison; they do not determine the simulator's output.
