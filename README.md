# Tomasulo Simulator

**Assignment author: Ahmed Ahmed**  
Original starter: Qing (May 2017), preserved in `original/tomasulo-python/`.

Repository: [ahmedshehata08-iss/tomasulo-simulator](https://github.com/ahmedshehata08-iss/tomasulo-simulator)  
Report: [PDF](report/Ahmed_Ahmed_Tomasulo_Report.pdf) | [Editable DOCX](report/Ahmed_Ahmed_Tomasulo_Report.docx)

This submission implements the supplied programming assignment: in-order issue at widths 1–4, out-of-order execution, register renaming, in-order commit, branch prediction and recovery, and speculative load disambiguation. The assignment profile has one CDB, one commit per cycle, one memory port, and 256-byte memory. The accompanying report follows the supplied report example and includes actual before-and-after source snapshots.

## Run and reproduce

Use Python 3.10 or newer. The simulator, experiments, and tests use only the Python standard library. Run these commands from this project's root:

```bash
python3 code/main.py
python3 code/main.py --input examples/sample_case1.in --issue-width 4
python3 code/main.py --input examples/sample_case2.in --issue-width 2 --json results/my_run.json --trace-csv results/my_run.csv
python3 -m unittest discover -s tests -v
python3 tests/stress_equivalence.py
python3 run_experiments.py
python3 audit/verify_assignment_timings.py
```

The default command reads `examples/assignment_demo.in`, resolved relative to the script. `python3 main.py` is an equivalent repository-root entry point. Explicit paths are relative to your working directory. `--max-cycles` sets a nontermination guard. `--json` writes complete timing/state/event data; `--trace-csv` writes all dynamic instruction rows, including squashed rows.

`assignment_demo.in` is a terminating variation of the handout example: it sets R3=20 so that R2 equals R3. `handout_literal.in` preserves the handout's hardware, issue width 2, initial values, and instructions. Its R2=20 and default R3=0 never change, so its branch always returns to the beginning. This command is expected to exit with a cycle-limit error:

```bash
python3 code/main.py --input examples/handout_literal.in --max-cycles 200
```

## Single-file input

Each current example combines hardware, initial registers, memory, and assembly in one case-insensitive text file:

```text
# of rs              Cycles in EX    Cycles in Mem    # of FUs
Integer adder           2               1                          1
FP adder                3               3                          1
FP multiplier           2              20                          1
Load/store unit         3               1               4          1
ROB entries = 128
Issue width = 2
R1=10, R2=20, R3=20, F2=30.1
Mem[4]=1, Mem[8]=2, Mem[12]=3.4
Add.d F1, F2, F3
Ld F4, 8(R1)
Bne R2, R3, -3
```

The load/store row's number of FUs sets the number of address units; memory still has one port. Unspecified registers and memory bytes start at zero. R0 remains zero; F0 is an ordinary writable register. Comments start with `#` or `;`. Labels, commas, and whitespace are accepted.

Supported instructions are `Add`, `Addi`, `Sub`, `Add.d`, `Sub.d`, `Mult.d` (also `Mul.d`), `Ld`, `Sd`, `Bne`, and `Beq`. Arithmetic register banks are checked. Load/store data uses F registers; addresses and branch operands use R registers.

Numeric branch displacements are **words relative to the next instruction**: `target_index = current_index + 1 + offset`. Equivalently, byte PC advances by `4 + (offset << 2)`. A label may replace the numeric offset, including an exit label immediately after the final instruction.

## Hardware and model settings

CLI issue-width, execution-order, and cycle-limit arguments override input settings. Direct configuration assignments such as `max_cycles = 200` are also accepted. Legacy assembly-only `--program` with optional JSON `--config` remains available, but the combined `--input` format is the assignment interface.

| Setting | Default | Meaning |
|---|---:|---|
| `issue_width` | 1 | Maximum contiguous in-order issues per cycle; range 1–4 |
| `writeback_width` / `commit_width` | 1 / 1 | CDB broadcasts and in-order retirements per cycle |
| `rs_int` / `rs_fp_add` / `rs_fp_mul` | 4 / 3 / 2 | Waiting arithmetic reservation stations |
| `lsq_size` / `rob_size` | 5 / 64 | Load/store queue and reorder buffer capacity |
| `int_latency` / `fp_add_latency` / `fp_mul_latency` | 1 / 4 / 15 | Arithmetic execution cycles |
| `addr_latency` / `mem_latency` | 1 / 5 | Address calculation and memory access cycles |
| `int_units` / `fp_add_units` / `fp_mul_units` | 1 / 1 / 1 | Arithmetic functional-unit counts |
| `addr_units` / `mem_units` | 1 / 1 | Address units and memory ports |
| `memory_size` | 256 | Byte-addressed memory capacity |
| `branch_prediction` | true | One-bit prediction with the eight-entry BTB |
| `execution_order` | out_of_order | Oldest-ready dispatch; optional `in_order` comparison mode |
| `max_cycles` | 100000 | Nontermination guard |

The simulator retains optional wider WB/commit and memory configurations for comparison. The submitted measurements keep WB=1, commit=1, memory ports=1, memory size=256, and prediction enabled at every issue width. Unknown configuration names and invalid values are rejected.

## Timing, prediction, and memory rules

- Each cycle visits commit, write-back, memory, execute, and issue in that order. EX cannot start in its issue cycle; WB follows completion; a broadcast cannot feed EX or commit until the next cycle. Intervals include both endpoints: `end = start + latency - 1`.
- Integer units, address units, and memory ports are unpipelined. FP adders and multipliers are pipelined, accepting one operation per unit per cycle. Waiting arithmetic RS entries release when EX starts. Loads release their LSQ entry after MEM completion; stores retain it until commit.
- Issue stops at the first full required resource or full ROB. Execution selects the oldest ready candidates for available units. Two available multiplier pipelines can start two ready multiplies in the same cycle. CDB arbitration chooses earliest completion, then oldest dynamic ID.
- Sources are captured before a destination is renamed. Unique dynamic IDs serve as producer tags. WB wakes matching consumers, while only commit changes architectural registers. Commit clears a RAT mapping only when the retiring producer still owns it.
- Branches predict during ISSUE using one-bit history initially predicting not taken and a tagged eight-entry direct-mapped BTB indexed by the low three bits of word PC. They resolve at EX completion. A misprediction squashes younger work, reconstructs the RAT from the surviving ROB, and redirects fetch. Detection in cycle n reserves n+1 for recovery; correct-path issue resumes in n+2.
- Loads may pass stores whose addresses are unresolved. They remember those older stores; a later overlapping address causes replay from the earliest affected load. Squashed work cannot commit. The same one-cycle recovery convention is used for replay.
- The youngest known older store with an identical address can forward ready data in one cycle. Known partial overlaps wait for stores to commit so the load observes correct bytes. Forwarding occupies the memory port. Other accesses take `mem_latency` cycles. Stores start MEM only at the ROB head and change memory at commit.
- Memory is an actual byte array. Each load/store accesses four little-endian IEEE binary32 bytes. Legal starting byte addresses are 0 through 252, including unaligned addresses such as 18 in the handout. Overlapping accesses share the same bytes. Output includes nonzero aligned words and explicitly initialized/stored address views.
- Stores and branches have no register-result WB stage. They can commit after their completed MEM or EX stage respectively. Invalid speculative addresses are recorded and raised only if that instruction reaches commit; wrong-path faults disappear on squash.

Timing IDs are dynamic IDs; PC is a zero-based static instruction index. IPC is committed instructions divided by total cycles. JSON distinguishes committed, issued, and squashed counts, and records branch predictions, mispredictions, and memory replays. An interrupted EX or MEM interval has a start but no completed end.

## Measured results

Only issue width changes within each row below. The CDB, commit width, and memory port count stay at one.

| Program | Committed | W1 cycles | W2 cycles | W3 cycles | W4 cycles |
|---|---:|---:|---:|---:|---:|
| Sample case 1 | 17 | 36 | 36 | 36 | 36 |
| Sample case 2, one memory port | 17 | 47 | 46 | 46 | 46 |
| Starter branch, -3 words | 15 | 45 | 45 | 45 | 45 |
| Additional full loop, -8 words | 25 | 67 | 66 | 66 | 66 |
| Independent integer operations | 24 | 27 | 27 | 27 | 27 |
| Terminating handout variation | 3 | 10 | 9 | 9 | 9 |
| Branch recovery | 13 | 28 | 25 | 25 | 25 |
| Memory-order replay | 5 | 19 | 19 | 19 | 19 |
| Unpipelined integer unit | 3 | 12 | 12 | 12 | 12 |

`run_experiments.py` reproduces all 36 terminating runs and four bounded checks of the literal nonterminating input. Its separate sequential interpreter decodes the benchmark assembly, models byte memory with `struct`, and compares all 64 registers, memory values, and committed instruction counts. Every check passes. The regression suite has 67 passing test methods, including 80 seeded equivalence runs; the separate stress script passes another 400 runs (100 programs at four issue widths).

The sample report is a formatting and program reference. Its case 2 uses two memory units; this submission keeps its two address units, two FP adders, and two multipliers but adapts it to the assignment's single memory port. The total-cycle coincidence of 47 at W1 does not imply identical intermediate timings. Instructions 2 and 4 both execute in cycles 9–23, then use the single CDB in cycles 24 and 25. Case 1's single pipelined multiplier starts those instructions in consecutive cycles 10 and 11.

The starter's old -12-byte branch displacement becomes -3 words and still targets PC5 from PC7, repeating only the pointer updates and branch. `full_loop.in` is a separate additional test using -8 words to target PC0. Their committed counts are 15 and 25 respectively. The branch recovery test has outcomes taken, taken, not taken and two mispredictions; the replay test detects one stale load and finishes with F2=7, F4=14, and Mem[16]=7 at every issue width.

The fixed backend explains the limited benefit of wider issue: 24 independent integer operations still need 27 cycles even with four integer units, because the CDB and commit remain one per cycle. Wider issue can expose work earlier, but it cannot remove those bottlenecks.

## Files and attribution

- `code/`: implemented simulator, credited to Ahmed Ahmed with Qing's starter attribution retained.
- `examples/`: current combined inputs; `examples/reference/` contains marked historical inputs.
- `tests/`: regression tests and independent stress interpreter.
- `results/`: reproducible text/JSON traces and CSV/JSON experiment summaries.
- `audit/`: independent checker and verified results for all eight main timing tables.
- `report/`: the PDF report and editable DOCX, both authored by Ahmed Ahmed.
- `original/tomasulo-python/`: unchanged supplied starter for before/after comparison.
- `changes.patch`: implementation changes relative to the supplied starter.
- `data_structure.txt`: revised state-model description.

The latest report is included in `report/`; `REPORT.pdf` is an identical convenience copy at the repository root. Source snapshots show exact original and submitted code. The repository contains the corresponding implementation, input files, tests, and measured results. Earlier implementations remain available in Git history.

## Model boundaries

FP registers and arithmetic use Python binary64 floats; memory stores and forwarded store values round to binary32. Despite the instruction spelling `Mult.d`, its register result remains a float as requested by the handout. This educational model does not emulate hardware IEEE exception flags or NaN payloads, caches, TLBs, interrupts, or fixed-width integer overflow. The conservative handling of known partial-overlap stores is a documented implementation choice. Wider backend options and no-prediction/in-order modes are extensions and are not used for the assignment results above.
