"""Functional, timing, hazard, speculation, and seeded differential tests.

Run from the submission directory: python -m unittest discover -s tests -v
"""

import random
import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from collections import Counter

from tomasulo.engine import Simulator
from tomasulo.parser import parse_text

try:
    from tests.oracle import execute
except ModuleNotFoundError:
    from oracle import execute


def source(code, *, width=1, int_rs=8, int_ex=1, int_fu=1,
           fp_rs=8, fp_ex=3, fp_fu=1, mul_rs=8, mul_ex=7,
           mul_fu=1, ls_rs=12, ls_ex=1, mem=3, ls_fu=1,
           rob=64, initial=""):
    return f"""# of rs  Cycles in EX  Cycles in Mem  # of FUs
Integer adder {int_rs} {int_ex} {int_fu}
FP adder {fp_rs} {fp_ex} {fp_fu}
FP multiplier {mul_rs} {mul_ex} {mul_fu}
Load/store unit {ls_rs} {ls_ex} {mem} {ls_fu}
ROB entries = {rob}
Issue width = {width}
{initial}
{code}
"""


class SimulatorTestCase(unittest.TestCase):
    def simulate(self, text, width=None):
        return Simulator(parse_text(text), issue_width=width, max_cycles=50000).run()

    def equivalent(self, text, width=None):
        expected = execute(text)
        actual = self.simulate(text, width)
        self.assertEqual(set(actual.registers), set(expected.registers))
        self.assertEqual(set(actual.memory), set(expected.memory))
        for register, value in expected.registers.items():
            self.assertAlmostEqual(actual.registers[register], value, places=8, msg=register)
        for address, value in expected.memory.items():
            self.assertAlmostEqual(actual.memory[address], value, places=8, msg=f"Mem[{address}]")
        self.assertEqual(actual.stats["committed"], expected.instructions)
        self.assertEqual(actual.registers["R0"], 0)
        self.assertGreaterEqual(actual.cycles, expected.instructions)
        self.assert_stage_invariants(actual)
        return actual

    def assert_stage_invariants(self, result):
        history = result.history
        committed = [row for row in history if row["commit"] is not None]
        self.assertEqual(len(committed), result.stats["committed"])
        self.assertEqual([r["id"] for r in committed], sorted(r["id"] for r in committed))
        self.assertTrue(all(not row["squashed"] for row in committed))
        commit_cycles = [r["commit"] for r in committed]
        self.assertEqual(len(commit_cycles), len(set(commit_cycles)), "single commit port")
        wb_cycles = [r["wb"] for r in history if r["wb"] is not None]
        self.assertEqual(len(wb_cycles), len(set(wb_cycles)), "single common data bus")
        for row in history:
            self.assertGreaterEqual(row["issue"], 1)
            if row["ex_start"] is not None:
                self.assertGreater(row["ex_start"], row["issue"])
                self.assertGreaterEqual(row["ex_end"], row["ex_start"])
            if row["mem_start"] is not None:
                self.assertGreater(row["mem_start"], row["ex_end"])
                self.assertGreaterEqual(row["mem_end"], row["mem_start"])
            if row["wb"] is not None:
                last_stage = row["mem_end"] if row["mem_end"] is not None else row["ex_end"]
                self.assertGreater(row["wb"], last_stage)
            if row["commit"] is not None:
                before_commit = row["wb"] if row["wb"] is not None else row["ex_end"]
                self.assertGreater(row["commit"], before_commit)


class ArchitectureTests(SimulatorTestCase):
    def test_all_isa_operations_and_initialized_state(self):
        code = """
        Add R3, R1, R2
        Sub R4, R2, R1
        Addi R5, R4, -3
        Add.d F4, F1, F2
        Sub.d F5, F2, F1
        Mult.d F6, F4, F5
        Ld F8, 4(R0)
        Sd F6, 8(R0)
        Beq R1, R2, 1
        Addi R6, R0, 10
        Bne R1, R2, 1
        Addi R7, R0, 99
        Add R8, R3, R5
        """
        text = source(code, initial="R1=2, R2=5, F1=1.5, F2=4\nMem[4]=2.25")
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.equivalent(text, width)
                self.assertEqual(result.registers["F6"], 13.75)
                self.assertEqual(result.registers["R7"], 0)

    def test_hardwired_r0_with_initialization_and_writes(self):
        text = source("""Addi R0 R0 9
            Add R0 R1 R1
            Sub R0 R0 R1
            Addi R2 R0 7
            Beq R0 R0 1
            Addi R2 R0 99
            Sd F2 0(R0)
            Ld F4 0(R0)""", width=4, initial="R0=123, R1=6, F2=2.5")
        result = self.equivalent(text)
        self.assertEqual(result.registers["R2"], 7)
        self.assertEqual(result.registers["F4"], 2.5)

    def test_same_bundle_raw_war_and_waw(self):
        text = source("""Addi R1 R0 4
            Add R2 R1 R1
            Addi R1 R0 9
            Add R3 R2 R1
            Add.d F2 F4 F6
            Mult.d F8 F2 F6
            Sub.d F2 F6 F4
            Add.d F10 F8 F2""", width=4, int_fu=2,
            initial="F4=2, F6=3")
        result = self.equivalent(text)
        self.assertEqual(result.registers["R3"], 17)
        self.assertEqual(result.registers["F10"], 16)

    def test_integer_and_fp_registers_are_distinct(self):
        self.equivalent(source("Addi R2 R0 4\nAdd.d F2 F2 F2\nSd F2 0(R2)",
            initial="F2=2"))

    def test_small_rob_and_rs_with_long_dependency_chain(self):
        text = source("\n".join("Addi R1 R1 1" for _ in range(16)),
                      width=4, int_rs=1, rob=2, int_ex=3)
        result = self.equivalent(text)
        self.assertEqual(result.registers["R1"], 16)

    def test_default_issue_width_and_override(self):
        text = source("\n".join(f"Addi R{i} R0 {i}" for i in range(1, 9)), int_fu=4)
        default_text = text.replace("Issue width = 1\n", "")
        result = self.equivalent(default_text)
        self.assertLessEqual(max(Counter(r["issue"] for r in result.history).values()), 1)
        wide = self.equivalent(default_text, 4)
        self.assertEqual([r["issue"] for r in wide.history[:4]], [1, 1, 1, 1])

    def test_serialization_contains_results(self):
        result = self.equivalent(source("Addi R1 R0 7"))
        data = result.to_dict()
        self.assertEqual(data["cycles"], result.cycles)
        self.assertEqual(data["registers"]["R1"], 7)
        self.assertEqual(data["stats"]["committed"], 1)


class TimingTests(SimulatorTestCase):
    def test_simple_add_timing(self):
        result = self.equivalent(source("Addi R1 R0 7", int_ex=1))
        row = result.history[0]
        self.assertEqual([row[k] for k in ("issue", "ex_start", "ex_end", "wb", "commit")], [1, 2, 2, 3, 4])
        self.assertEqual(result.cycles, 4)

    def test_multicycle_execution_is_inclusive(self):
        result = self.equivalent(source("Mult.d F2 F4 F6", mul_ex=5, initial="F4=2,F6=3"))
        row = result.history[0]
        self.assertEqual(row["ex_end"] - row["ex_start"] + 1, 5)
        self.assertEqual(result.cycles, 8)

    def test_dependent_execution_follows_broadcast(self):
        result = self.equivalent(source("Addi R1 R0 3\nAdd R2 R1 R1", width=2, int_fu=2))
        producer, consumer = result.history
        self.assertEqual(producer["issue"], consumer["issue"])
        self.assertGreater(consumer["ex_start"], producer["wb"])

    def test_integer_unit_is_not_pipelined(self):
        result = self.equivalent(source("Addi R1 R0 1\nAddi R2 R0 2\nAddi R3 R0 3", width=3, int_ex=4))
        rows = result.history
        self.assertEqual([r["ex_start"] for r in rows], [2, 6, 10])

    def test_fp_adder_is_pipelined(self):
        result = self.equivalent(source("Add.d F2 F0 F0\nAdd.d F4 F0 F0\nSub.d F6 F0 F0", width=3, fp_ex=4))
        self.assertEqual([r["ex_start"] for r in result.history], [2, 3, 4])
        self.assertEqual([r["ex_end"] for r in result.history], [5, 6, 7])

    def test_fp_multiplier_is_pipelined(self):
        result = self.equivalent(source("Mult.d F2 F0 F0\nMult.d F4 F0 F0\nMult.d F6 F0 F0", width=3, mul_ex=6))
        self.assertEqual([r["ex_start"] for r in result.history], [2, 3, 4])

    def test_rs_freed_at_execution_reusable_next_cycle(self):
        result = self.equivalent(source("Addi R1 R0 1\nAddi R2 R0 2", width=2, int_rs=1, int_ex=4))
        first, second = result.history
        self.assertEqual(first["ex_start"], 2)
        self.assertEqual(second["issue"], first["ex_start"] + 1)

    def test_fu_count_allows_parallel_integer_execution(self):
        result = self.equivalent(source("Addi R1 R0 1\nAddi R2 R0 2\nAddi R3 R0 3", width=3, int_fu=3, int_ex=3))
        self.assertEqual([r["ex_start"] for r in result.history], [2, 2, 2])
        self.assertEqual([r["wb"] for r in result.history], [5, 6, 7])

    def test_memory_has_one_nonpipelined_port(self):
        result = self.equivalent(source("Ld F2 0(R0)\nLd F4 4(R0)\nLd F6 8(R0)",
            width=3, ls_fu=3, mem=4, initial="Mem[0]=1,Mem[4]=2,Mem[8]=3"))
        accesses = sorted((r["mem_start"], r["mem_end"]) for r in result.history)
        for start, end in accesses:
            self.assertEqual(end - start + 1, 4)
        for previous, following in zip(accesses, accesses[1:]):
            self.assertGreater(following[0], previous[1])

    def test_in_order_issue_width_bound(self):
        code = "\n".join(f"Addi R{i} R0 {i}" for i in range(1, 18))
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.equivalent(source(code, width=width, int_fu=4))
                issued = [row["issue"] for row in result.history]
                self.assertEqual(issued, sorted(issued))
                self.assertLessEqual(max(Counter(issued).values()), width)
                self.assertEqual([r["pc"] for r in result.history], list(range(17)))

    def test_full_rs_blocks_younger_instruction_of_another_kind(self):
        # The FP instruction is independent and has a free RS, but it must not
        # bypass the older integer instruction that cannot yet be issued.
        result = self.equivalent(source(
            "Addi R1 R0 1\nAddi R2 R0 2\nAdd.d F2 F0 F0",
            width=4, int_rs=1))
        first, blocked, younger = result.history
        self.assertGreater(blocked["issue"], first["issue"])
        self.assertEqual(younger["issue"], blocked["issue"])

    def test_full_rob_stalls_issue_until_next_cycle_after_commit(self):
        result = self.equivalent(source(
            "Mult.d F2 F4 F6\nAddi R1 R0 1", width=4, rob=1,
            mul_ex=5, initial="F4=2,F6=3"))
        older, younger = result.history
        self.assertEqual(younger["issue"], older["commit"] + 1)


class SpeculationTests(SimulatorTestCase):
    def test_wrong_path_invalid_branch_does_not_poison_btb(self):
        # PCs 1 and 9 alias in the 8-entry BTB. The invalid branch at PC 2
        # resolves before the older branch but must never become a fetch target
        # on a later speculative pass through this loop.
        text = source("""Addi R1 R0 1
            Beq R1 R1 1
            Beq R0 R0 100
            Addi R2 R2 -1
            Addi R3 R0 1
            Addi R4 R0 1
            Addi R5 R0 1
            Addi R6 R0 1
            Addi R7 R0 1
            Bne R2 R0 -10""", width=4, int_rs=32,
            int_ex=4, int_fu=2, initial="R2=2")
        result = self.equivalent(text)
        self.assertEqual(result.registers["R2"], 0)
        self.assertTrue(any(row["pc"] == 2 and row["squashed"] for row in result.history))

    def test_wrong_path_invalid_load_and_store_are_squashed(self):
        for op in ("Ld", "Sd"):
            with self.subTest(op=op):
                text = source(f"""Addi R1 R0 1
                    Beq R1 R1 1
                    {op} F2 256(R0)
                    Addi R3 R0 7""", width=4, int_ex=4,
                    int_fu=2, initial="F2=3")
                result = self.equivalent(text)
                self.assertEqual(result.registers["R3"], 7)

    def test_taken_branch_squashes_wrong_register_and_store(self):
        text = source("""Beq R0 R0 3
            Addi R1 R0 99
            Sd F2 0(R0)
            Add.d F6 F2 F2
            Addi R1 R0 7""", width=4, initial="F2=9,Mem[0]=2")
        result = self.equivalent(text)
        self.assertEqual(result.registers["R1"], 7)
        self.assertEqual(result.memory[0], 2)
        self.assertGreaterEqual(result.stats["mispredictions"], 1)
        self.assertTrue(any(row["squashed"] for row in result.history))

    def test_branch_recovery_cost(self):
        result = self.equivalent(source("Beq R0 R0 2\nAddi R1 R0 99\nAddi R2 R0 99\nAddi R3 R0 7", width=4))
        branch = result.history[0]
        target = next(row for row in result.history if row["pc"] == 3 and not row["squashed"])
        self.assertEqual(target["issue"], branch["ex_end"] + 2)

    def test_recovery_restores_older_uncommitted_rename(self):
        text = source("""Mult.d F2 F8 F10
            Beq R0 R0 2
            Add.d F2 F12 F14
            Addi R1 R0 99
            Add.d F4 F2 F2""", width=4, mul_ex=12,
            initial="F8=2,F10=3,F12=20,F14=30")
        result = self.equivalent(text)
        self.assertEqual(result.registers["F4"], 12)

    def test_loop_taken_then_not_taken(self):
        text = source("""Addi R1 R0 5
            Addi R2 R2 2
            Addi R1 R1 -1
            Bne R1 R0 -3
            Add R3 R2 R1""", width=4)
        result = self.equivalent(text)
        self.assertEqual(result.registers["R3"], 10)
        self.assertGreaterEqual(result.stats["branch_predictions"], 5)
        self.assertEqual(sum(row["pc"] == 3 and row["commit"] is not None
                             for row in result.history), 5)
        self.assertEqual(result.stats["mispredictions"], 2)

    def test_nested_branches_and_btb_index_aliasing(self):
        code = """Addi R1 R0 3
            Addi R2 R2 1
            Beq R1 R0 7
            Addi R3 R3 1
            Bne R0 R0 1
            Addi R4 R4 2
            Addi R5 R5 1
            Addi R6 R6 1
            Addi R7 R7 1
            Addi R1 R1 -1
            Bne R1 R0 -10
            Addi R8 R0 8"""
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.equivalent(source(code, width=width, int_fu=2))
                self.assertEqual(result.registers["R4"], 6)


class MemoryTests(SimulatorTestCase):
    def test_forward_from_youngest_matching_store(self):
        text = source("""Mult.d F30 F28 F28
            Sd F2 0(R0)
            Sd F4 4(R0)
            Sd F6 0(R0)
            Sd F8 8(R0)
            Ld F10 0(R0)""", width=4, mul_ex=20,
            initial="F2=2,F4=4,F6=6,F8=8,F28=2,Mem[0]=99")
        result = self.equivalent(text)
        load = next(row for row in result.history if row["pc"] == 5 and not row["squashed"])
        self.assertTrue(load["forwarded"])
        self.assertEqual(load["mem_end"], load["mem_start"])
        self.assertEqual(result.registers["F10"], 6)

    def test_matching_store_with_pending_data_delays_load(self):
        text = source("""Mult.d F2 F6 F8
            Sd F2 0(R0)
            Ld F4 0(R0)""", width=3, mul_ex=12,
            initial="F6=3,F8=4,Mem[0]=99")
        result = self.equivalent(text)
        multiply = result.history[0]
        load = next(row for row in result.history if row["pc"] == 2 and not row["squashed"])
        self.assertGreater(load["mem_start"], multiply["wb"])
        self.assertEqual(result.registers["F4"], 12)

    def test_unknown_store_address_load_violation_replays(self):
        text = source("""Addi R1 R0 16
            Sd F2 0(R1)
            Ld F4 16(R0)
            Add.d F6 F4 F4""", width=4, int_ex=12, mem=2,
            initial="F2=7,Mem[16]=99")
        result = self.equivalent(text)
        self.assertGreaterEqual(result.stats["memory_replays"], 1)
        self.assertEqual(result.registers["F4"], 7)
        self.assertEqual(result.registers["F6"], 14)

    def test_unknown_store_address_nonalias_has_no_replay(self):
        text = source("Addi R1 R0 20\nSd F2 0(R1)\nLd F4 16(R0)",
            width=3, int_ex=12, mem=2, initial="F2=7,Mem[16]=99")
        result = self.equivalent(text)
        self.assertEqual(result.stats["memory_replays"], 0)

    def test_addresses_zero_and_last_word(self):
        self.equivalent(source("Ld F2 252(R0)\nSd F2 0(R0)\nLd F4 0(R0)",
            width=4, initial="Mem[252]=-3.25"))

    def test_small_lsq_eventually_releases_completed_loads(self):
        text = source("\n".join(f"Ld F{i % 30} {(i % 64) * 4}(R0)" for i in range(20)),
            width=4, ls_rs=2, rob=4, mem=2)
        self.equivalent(text)

    def test_completed_load_releases_lsq_before_its_commit(self):
        # A long older multiply blocks retirement. A one-entry LSQ must still
        # admit the second load as soon as the first load has received data.
        result = self.equivalent(source(
            "Mult.d F30 F28 F28\nLd F2 0(R0)\nLd F4 4(R0)",
            width=4, mul_ex=20, ls_rs=1, mem=2,
            initial="F28=2,Mem[0]=3,Mem[4]=7"))
        _, first_load, second_load = result.history
        self.assertEqual(second_load["issue"], first_load["mem_end"] + 1)
        self.assertLess(second_load["issue"], first_load["commit"])

    def test_intervening_matching_store_prevents_unnecessary_replay(self):
        # The first store resolves late, but the load has correctly forwarded
        # from a younger same-address store that supersedes it.
        result = self.equivalent(source(
            "Addi R1 R0 16\nSd F2 0(R1)\nSd F4 16(R0)\nLd F6 16(R0)",
            width=4, int_ex=12, ls_fu=2, mem=2,
            initial="F2=7,F4=9,Mem[16]=99"))
        late_store, load = result.history[1], result.history[3]
        self.assertLess(load["mem_end"], late_store["ex_end"])
        self.assertTrue(load["forwarded"])
        self.assertEqual(result.stats["memory_replays"], 0)
        self.assertEqual(result.registers["F6"], 9)


class DifferentialTests(SimulatorTestCase):
    def test_seeded_delayed_address_memory_programs(self):
        # 12 seeds x four issue widths = 48 comparisons. Base registers are
        # repeatedly renamed, so stores can have unresolved addresses while
        # younger loads and independent FP operations execute speculatively.
        for seed in range(12):
            rng = random.Random(140200 + seed)
            initial = "R1=0,R2=4,R3=8,R4=12,F0=0.5,F1=1.5,F2=-2,F3=3\n" + ",".join(
                f"Mem[{i * 4}]={rng.randint(-6, 6)}" for i in range(16))
            code = []
            for _ in range(48):
                kind = rng.randrange(6)
                base, address = rng.randrange(1, 5), 4 * rng.randrange(16)
                if kind == 0:
                    code.append(f"Addi R{base} R0 {address}")
                elif kind in (1, 2):
                    code.append(f"Ld F{rng.randrange(4, 12)} 0(R{base})")
                elif kind in (3, 4):
                    code.append(f"Sd F{rng.randrange(12)} 0(R{base})")
                else:
                    code.append(f"Add.d F{rng.randrange(4, 12)} F{rng.randrange(12)} F{rng.randrange(12)}")
            for width in range(1, 5):
                with self.subTest(seed=seed, width=width):
                    self.equivalent(source("\n".join(code), width=width,
                        int_ex=3 + seed % 5, int_fu=1 + seed % 2,
                        fp_ex=1 + seed % 4, fp_fu=1 + seed % 2,
                        ls_ex=1 + seed % 3, ls_fu=1 + seed % 3,
                        mem=1 + seed % 5, ls_rs=2 + seed % 6,
                        rob=6 + seed % 8, initial=initial))

    def test_seeded_random_straight_line_programs(self):
        # 10 seeds x four issue widths = 40 independent architectural comparisons.
        for seed in range(10):
            rng = random.Random(73100 + seed)
            initial = "R1=3,R2=7,R3=-2,F0=0.5,F1=1.5,F2=-2,F3=3\n" + ",".join(
                f"Mem[{i * 4}]={rng.randint(-6, 6)}" for i in range(16))
            code = []
            for _ in range(36):
                kind = rng.randrange(9)
                if kind < 2:
                    code.append(f"{'Add' if kind == 0 else 'Sub'} R{rng.randrange(12)} R{rng.randrange(12)} R{rng.randrange(12)}")
                elif kind == 2:
                    code.append(f"Addi R{rng.randrange(12)} R{rng.randrange(12)} {rng.randint(-5, 5)}")
                elif kind < 6:
                    op = ("Add.d", "Sub.d", "Mult.d")[kind - 3]
                    # Multiplication reads fixed small inputs to avoid float overflow.
                    left, right = (rng.randrange(4), rng.randrange(4)) if op == "Mult.d" else (rng.randrange(12), rng.randrange(12))
                    code.append(f"{op} F{rng.randrange(4, 12)} F{left} F{right}")
                elif kind < 8:
                    code.append(f"Ld F{rng.randrange(4, 12)} {4 * rng.randrange(16)}(R0)")
                else:
                    code.append(f"Sd F{rng.randrange(12)} {4 * rng.randrange(16)}(R0)")
            for width in range(1, 5):
                with self.subTest(seed=seed, width=width):
                    self.equivalent(source("\n".join(code), width=width,
                        int_rs=2 + seed % 4, fp_rs=2 + seed % 3, mul_rs=2,
                        int_ex=1 + seed % 3, fp_ex=2 + seed % 4,
                        mul_ex=4 + seed % 5, mem=1 + seed % 4,
                        int_fu=1 + seed % 2, fp_fu=1 + seed % 2,
                        ls_rs=3 + seed % 4, rob=8 + seed % 5,
                        initial=initial))

    def test_seeded_bounded_branch_programs(self):
        for seed in range(8):
            rng = random.Random(9200 + seed)
            count, step, extra = rng.randint(2, 6), rng.randint(1, 5), rng.randint(-4, 4)
            code = f"""Addi R1 R0 {count}
            Addi R2 R2 {step}
            Beq R2 R0 1
            Addi R3 R3 {extra}
            Ld F4 0(R0)
            Add.d F6 F4 F2
            Sd F6 0(R0)
            Addi R1 R1 -1
            Bne R1 R0 -8
            Add R4 R2 R3"""
            for width in range(1, 5):
                with self.subTest(seed=seed, width=width):
                    self.equivalent(source(code, width=width,
                        int_ex=1 + seed % 2, fp_ex=2 + seed % 3,
                        mem=1 + seed % 4, rob=8 + seed % 4,
                        int_rs=2 + seed % 3, ls_rs=3 + seed % 2,
                        initial="F2=1.25,Mem[0]=0.5"))


class InputAndFailureTests(SimulatorTestCase):
    def test_empty_program(self):
        result = self.simulate("")
        self.assertEqual(result.cycles, 0)
        self.assertEqual(result.stats["committed"], 0)
        self.assertEqual(result.history, [])
        self.assertEqual(result.registers["R0"], 0)

    def test_case_insensitive_whitespace_comments_and_hex(self):
        text = """issue WIDTH = 4
        integer ADDER 4 1 2
        r1=0x10, f2=1.25e1
        mEM[0x10]=2.5
        aDdI r2, r1, -4 # integer operation
        lD f4, 0 ( r1 ) ; load
        ADD.D f6 f4 f2 // fp operation
        sD f6, 4(r1)
        """
        result = self.simulate(text)
        self.assertEqual(result.registers["R2"], 12)
        self.assertEqual(result.registers["F6"], 15)
        self.assertEqual(result.memory[20], 15)

    def test_malformed_instructions_rejected_with_line_number(self):
        invalid = ("Add R1 R2", "Add R32 R1 R2", "Add F1 R1 R2",
                   "Add.d F1 R1 F2", "Ld R1 4(R0)", "Sd F1 0(F2)",
                   "Addi R1 R0 1.5", "Bne R1 R0 hello", "Nop",
                   "Add R1,,R2,R3", "Add R1 R2 R3,")
        for instruction in invalid:
            with self.subTest(instruction=instruction):
                with self.assertRaisesRegex(ValueError, "line 2"):
                    parse_text("# comment\n" + instruction)

    def test_invalid_initializers_rejected(self):
        for initializer in ("R32=2", "F32=2", "R1=1.5", "F1=nan", "F1=inf",
                            "Mem[-4]=1", "Mem[256]=1", "Mem[2]=1",
                            "R1=1,", "R1=1 garbage", "Mem[4]=oops"):
            with self.subTest(initializer=initializer):
                with self.assertRaises(ValueError):
                    parse_text(initializer)

    def test_invalid_hardware_configuration_rejected(self):
        for configuration in ("Issue width = 0", "Issue width = 5", "Issue width = 1.5",
                              "ROB entries = 0", "Integer adder 0 1 1",
                              "FP adder 2 0 1", "FP multiplier 2 3 0",
                              "Load/store unit 2 1 0 1", "Integer adder 2 1"):
            with self.subTest(configuration=configuration):
                with self.assertRaises(ValueError):
                    parse_text(configuration)

    def test_architectural_invalid_addresses_raise_precise_fault(self):
        for instruction in ("Ld F2 2(R0)", "Ld F2 256(R0)", "Sd F2 -4(R0)"):
            with self.subTest(instruction=instruction):
                with self.assertRaisesRegex(ValueError, "Invalid word address"):
                    self.simulate(source("Addi R1 R0 7\n" + instruction, width=4))

    def test_architectural_invalid_taken_branch_raises_fault(self):
        for instruction in ("Beq R0 R0 -2", "Beq R0 R0 100"):
            with self.subTest(instruction=instruction):
                with self.assertRaisesRegex(ValueError, "Branch target"):
                    self.simulate(source(instruction))
        # An invalid target is harmless when its branch is not taken.
        self.equivalent(source("Bne R0 R0 100\nAddi R1 R0 7"))

    def test_maximum_cycle_guard_and_invalid_override(self):
        with self.assertRaisesRegex(RuntimeError, "exceeded 12 cycles"):
            Simulator(parse_text("Beq R0 R0 -1"), max_cycles=12).run()
        with self.assertRaises(ValueError):
            Simulator(parse_text(""), max_cycles=0)
        for width in (0, 5):
            with self.subTest(width=width), self.assertRaises(ValueError):
                Simulator(parse_text(""), issue_width=width)


class CommandLineTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / "main.py"), *map(str, args)],
                              cwd=self.root, capture_output=True, text=True, timeout=10)

    def test_cli_writes_text_and_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            input_path = folder / "program.in"
            input_path.write_text("Addi R1 R0 7\n", encoding="utf-8")
            text_path, json_path = folder / "out.txt", folder / "out.json"
            completed = self.cli(input_path, "--issue-width", 4, "--output", text_path,
                                 "--json", json_path)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("REGISTER VALUES", completed.stdout)
            self.assertIn("NON-ZERO MEMORY VALUES", completed.stdout)
            self.assertEqual(completed.stdout, text_path.read_text(encoding="utf-8"))
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["registers"]["R1"], 7)
            self.assertEqual(data["config"]["issue_width"], 4)

    def test_cli_missing_file_reports_error(self):
        completed = self.cli("/definitely-missing-tomasulo-file.in")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("error:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_cli_invalid_input_reports_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "invalid.in"
            input_path.write_text("Add R33 R0 R0", encoding="utf-8")
            completed = self.cli(input_path)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("line 1", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_cli_cycle_guard_reports_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "infinite.in"
            input_path.write_text("Beq R0 R0 -1", encoding="utf-8")
            completed = self.cli(input_path, "--max-cycles", 10)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("exceeded 10 cycles", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
