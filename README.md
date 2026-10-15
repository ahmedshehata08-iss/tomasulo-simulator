# Tomasulo Multi-Issue Simulator

Author: Ahmed Ahmed

[Repository](https://github.com/ahmedshehata08-iss/tomasulo-simulator) ·
[PDF report](REPORT.pdf)

This submission implements in-order issue widths 1-4 with register renaming,
out-of-order execution, a reorder buffer, speculative branches, and memory
replay. It is a modular refactor of the supplied Python starter. The unchanged
starter modules are in `starter_original/` for comparison. The runnable code is
in `tomasulo/` with `main.py` as its entry point.

## Run

Python 3.10 or newer is required. The simulator and tests use only the standard
library. Tested with Python 3.12.14.

From this directory:

```bash
python3 main.py
python3 main.py benchmarks/corrected_starter.in --issue-width 4
python3 main.py benchmarks/branch_recovery.in --issue-width 4 --show-squashed \
  --output branch.txt --json branch.json
python3 -m unittest discover -s tests -v
python3 run_experiments.py
```

`main.py` defaults to `code.in`, a copy of the corrected supplied benchmark.
The input defaults to issue width 1. `Issue width = 2` in an input file sets
another width; `--issue-width` overrides it. `--max-cycles` defaults to 100000
and prevents an infinite loop from running indefinitely. Malformed input and
architectural faults produce an error and a nonzero exit status.

## Input format

```text
# of rs           Cycles in EX    Cycles in Mem    # of FUs
Integer adder     4               1                                1
FP adder          3               3                                1
FP multiplier     2               8                                1
Load/store unit   5               1                4               1
ROB entries = 64
Issue width = 2
R1=4, F2=2.5
Mem[4]=3
Ld F4, 0(R1)
Add.d F6, F4, F2
Sd F6, 4(R1)
```

Configuration and initialization are optional. Registers and memory default
to zero; R0 remains zero even if initialized or written. Input is case
insensitive, commas are optional, and `#`, `//`, and `;` begin comments.
Supported instructions: `Ld`, `Sd`, `Beq`, `Bne`, `Add`, `Sub`, `Addi`,
`Add.d`, `Sub.d`, and `Mult.d`. Integer and floating point registers are
separate 32-entry files. Loads/stores use F registers for data and R registers
for the base address. Integers use Python integers; FP uses Python floats.

Default hardware: 128 ROB entries; integer RS/EX/FUs = 2/1/1; FP adder =
3/3/1; FP multiplier = 2/20/1; LSQ/address-EX/address-FUs = 3/1/1;
memory access = 4 cycles. All counts and latencies must be positive integers.
The load/store FU count sets address-adder capacity; data memory always has
one non-pipelined port.

## Timing and implementation choices

- Only issue bandwidth changes with issue width. The design retains one CDB
  and one in-order commit slot. Instructions are issued sequentially within a
  bundle, so later instructions see earlier destination renames. Issue stops
  at the first blocked instruction and follows predicted branch control flow.
- A new instruction cannot execute in its issue cycle. A result broadcast in
  cycle n may be used for execution in n+1. Stage intervals are inclusive.
  A one-cycle integer instruction can ISSUE=1, EX=2, WB=3, COMMIT=4.
- Reservation stations are released at EX start; the issue stage can reuse
  their slots in the next cycle. Integer and address adders are unpipelined.
  FP units accept one operation per configured FU per cycle. The oldest ready
  instruction is selected for execution. CDB arbitration chooses the earliest
  completed result, breaking ties by dynamic instruction age.
- The tagged, direct-mapped 8-entry BTB uses word-PC modulo 8. Each entry has
  a one-bit direction predictor, initially not taken. Branches resolve at EX
  end. A branch misprediction in cycle n squashes younger work, rebuilds the
  RAT from surviving ROB entries, and resumes fetch in n+2. Prediction tables
  may be trained speculatively; invalid branch targets are never cached.
- Branch offsets are instruction words: target word-PC = PC+1+offset, which
  is equivalent to byte-PC+4+(offset<<2). The supplied `test_case.txt` uses
  `Bne R1 R0 -12`, which targets outside the program under this rule. Its
  corrected benchmark uses `-8` to repeat the full loop. Both originals are
  preserved in `benchmarks/`; no silent offset conversion is performed.
- Memory contains 64 aligned words at byte addresses 0,4,...,252. Invalid
  effective addresses raise a precise fault if the instruction reaches commit;
  faults on a wrong path are discarded. This aligned-word interpretation is
  explicit because the assignment does not specify unaligned access behavior.
- Loads can pass older stores with unresolved addresses. A load forwards from
  the youngest older matching store when its data is ready, taking one cycle.
  An older store that later resolves to an already-started younger load's
  address triggers replay of that load and its successors (unless a known
  intervening matching store supersedes it). Replay fetch resumes in n+2.
- Memory reads, store writes, and forwarding share one memory-stage service.
  A completed load leaves the LSQ immediately. Stores retain their LSQ entries
  until commit, begin memory service only at the ROB head, and update memory
  at the final cycle of that service. Store MEM shows this interval; COMMIT
  records its final cycle. Stores and branches do not use the CDB.
- Text output shows dynamic committed instructions by default. Use
  `--show-squashed` for wrong paths and replay attempts; JSON always includes
  them. EX/MEM end fields on squashed work may be the planned end of a canceled
  operation. Displayed PCs are byte addresses; JSON PCs are word indices.

## Verification and included results

`tests/oracle.py` is an independent sequential interpreter with its own
assembly parsing. It checks all 64 registers and all 64 memory words against
simulator outcomes. The 44 test methods include 72 seeded differential
configurations, plus deterministic timing/hazard/branch/memory/parser/CLI
checks. Timing expectations are checked separately from the functional oracle.

`run_experiments.py` runs seven benchmarks at widths 1,2,3,4 and asserts exact
final-state agreement with the oracle. Hardware is held fixed within each
benchmark. `results/summary.csv` and `summary.json` contain the comparison;
28 text and 28 JSON files retain the full traces. `results/test_log.txt`
records the test run. `REPORT.pdf` contains the two required assignment sections:
"Brief description what you have added to the code" and
"Show results of test cases under different issue width". The code was rerun
on October 15, 2026: 44 test methods, 72 seeded differential configurations,
and all 28 benchmark configurations passed. See `results/validation.json`,
`results/experiment_log.txt`, and `results/cli_smoke_log.txt` for the rerun evidence.

No known failing cases remain in the included tests. This is a functional
teaching simulator: FP arithmetic is not a bit-accurate IEEE ISA model, and
there are no caches, virtual memory, interrupts, or operating-system effects.
These are outside the supplied assignment. Run commands above to reproduce
results before submission.
