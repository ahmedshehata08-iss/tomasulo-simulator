"""Independent behavioral regression tests.

Assignment author: Ahmed Ahmed.
Expected values come from hand calculations or the sequential oracle below;
the oracle does not call the simulator parser, scheduler, or stage helpers.
"""

import collections
import random
import struct
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from init import Config, parse_program, parse_input
from simulator import Simulator


def simulate(text, *, registers=None, memory=None, **options):
    return Simulator(
        parse_program(text), Config(**options),
        registers={} if registers is None else registers,
        memory={} if memory is None else memory,
    ).run()


def sequential_oracle(lines, initial_registers, initial_memory):
    """Simple architectural interpreter for generated straight-line cases."""
    registers = {f"{bank}{i}": 0 for bank in ("R", "F") for i in range(32)}
    registers.update(initial_registers)
    memory = {address: struct.unpack("<f", struct.pack("<f", value))[0]
              for address, value in initial_memory.items()}
    for line in lines:
        op, *args = line.split()
        if op == "Addi":
            dest, left, immediate = args
            value = registers[left] + int(immediate)
        elif op in ("Add", "Add.d", "Sub", "Sub.d", "Mult.d"):
            dest, left, right = args
            a, b = registers[left], registers[right]
            value = a * b if op == "Mult.d" else a - b if op.startswith("Sub") else a + b
        elif op == "Ld":
            dest, address = args
            offset, base = address[:-1].split("(")
            value = memory.get(registers[base] + int(offset), 0)
        elif op == "Sd":
            source, address = args
            offset, base = address[:-1].split("(")
            memory[registers[base] + int(offset)] = struct.unpack("<f", struct.pack("<f", registers[source]))[0]
            continue
        else:
            raise AssertionError(f"Unsupported oracle operation {op}")
        if dest != "R0":
            registers[dest] = value
    return registers, memory


class TimingTests(unittest.TestCase):
    def test_single_addi_hand_calculated_timing(self):
        result = simulate("Addi R3 R0 7")
        row = result["instructions"][0]
        self.assertEqual((row["issue"], row["exe_start"], row["exe_end"], row["wb"], row["commit"]), (1, 2, 2, 3, 4))
        self.assertEqual(result["cycles"], 4)
        self.assertEqual(result["registers"]["R3"], 7)

    def test_same_bundle_raw_waits_until_cycle_after_writeback(self):
        result = simulate("Addi R1 R0 5\nAdd R2 R1 R1", issue_width=2)
        first, second = result["instructions"]
        self.assertEqual(first["issue"], second["issue"])
        self.assertEqual((first["exe_start"], first["wb"], second["exe_start"], second["wb"]), (2, 3, 4, 5))
        self.assertEqual(result["registers"]["R2"], 10)

    def test_issue_widths_one_through_four(self):
        text = "\n".join(f"Addi R{i} R0 {i}" for i in range(1, 9))
        for width in range(1, 5):
            with self.subTest(width=width):
                result = simulate(text, issue_width=width, rs_int=16, int_units=4)
                rows = result["instructions"]
                self.assertEqual([row["issue"] for row in rows], [1 + i // width for i in range(8)])
                self.assertTrue(all(count <= width for count in collections.Counter(r["issue"] for r in rows).values()))

    def test_multiple_writeback_and_commit(self):
        text = "\n".join(f"Addi R{i} R0 {i}" for i in range(1, 5))
        result = simulate(text, issue_width=4, writeback_width=4, commit_width=4, int_units=4)
        self.assertEqual([r["wb"] for r in result["instructions"]], [3] * 4)
        self.assertEqual([r["commit"] for r in result["instructions"]], [4] * 4)

    def test_single_cdb_serializes_ready_results(self):
        text = "\n".join(f"Addi R{i} R0 {i}" for i in range(1, 5))
        result = simulate(text, issue_width=4, writeback_width=1, commit_width=4, int_units=4)
        self.assertEqual(sorted(r["wb"] for r in result["instructions"]), [3, 4, 5, 6])

    def test_single_commit_serializes_simultaneous_results(self):
        text = "\n".join(f"Addi R{i} R0 {i}" for i in range(1, 5))
        result = simulate(text, issue_width=4, writeback_width=4, commit_width=1, int_units=4)
        self.assertEqual([r["commit"] for r in result["instructions"]], [4, 5, 6, 7])

    def test_arithmetic_latency_is_exact_with_overlapping_operations(self):
        text = "\n".join(f"Add.d F{i} F0 F0" for i in range(1, 8))
        result = simulate(text, issue_width=4, fp_add_latency=4, rs_fp_add=8)
        rows = result["instructions"]
        self.assertTrue(all(r["exe_end"] - r["exe_start"] + 1 == 4 for r in rows))
        self.assertEqual([r["exe_start"] for r in rows], list(range(2, 9)))

    def test_two_pipelined_units_admit_two_operations_each_cycle(self):
        text = "\n".join(f"Mult.d F{i} F0 F0" for i in range(1, 7))
        result = simulate(text, issue_width=4, fp_mul_units=2, fp_mul_latency=5, rs_fp_mul=8)
        self.assertEqual([r["exe_start"] for r in result["instructions"]], [2, 2, 3, 3, 4, 4])
        self.assertTrue(all(r["exe_end"] - r["exe_start"] + 1 == 5 for r in result["instructions"]))

    def test_in_order_commit_waits_for_older_long_operation(self):
        result = simulate("Mult.d F1 F2 F3\nAddi R1 R0 8", issue_width=2, writeback_width=2, commit_width=2,
                          registers={"F2": 2, "F3": 3})
        older, younger = result["instructions"]
        self.assertLess(younger["wb"], older["wb"])
        self.assertGreaterEqual(younger["commit"], older["commit"])

    def test_out_of_order_execution_bypasses_waiting_instruction(self):
        result = simulate("Mult.d F1 F2 F3\nAdd.d F4 F1 F1\nAdd.d F5 F2 F3", issue_width=3,
                          registers={"F2": 2, "F3": 3})
        rows = result["instructions"]
        self.assertLess(rows[2]["exe_start"], rows[1]["exe_start"])
        self.assertEqual(result["registers"]["F4"], 12)
        self.assertEqual(result["registers"]["F5"], 5)

    def test_strict_in_order_execution_option(self):
        result = simulate("Mult.d F1 F2 F3\nAdd.d F4 F1 F1\nAdd.d F5 F2 F3", issue_width=3,
                          execution_order="in_order", registers={"F2": 2, "F3": 3})
        starts = [r["exe_start"] for r in result["instructions"]]
        self.assertEqual(starts, sorted(starts))

    def test_rob_capacity_one_forces_reuse_after_commit(self):
        result = simulate("Addi R1 R0 1\nAddi R2 R0 2\nAddi R3 R0 3", issue_width=4, rob_size=1)
        rows = result["instructions"]
        self.assertGreaterEqual(rows[1]["issue"], rows[0]["commit"])
        self.assertGreaterEqual(rows[2]["issue"], rows[1]["commit"])
        self.assertEqual(result["registers"]["R3"], 3)

    def test_rs_full_stalls_younger_issue_in_program_order(self):
        result = simulate("Mult.d F1 F2 F3\nAdd.d F4 F1 F1\nAdd.d F5 F2 F3\nAddi R1 R0 1",
                          issue_width=4, rs_fp_add=1, registers={"F2": 2, "F3": 3})
        rows = result["instructions"]
        self.assertGreater(rows[2]["issue"], rows[1]["issue"])
        self.assertGreaterEqual(rows[3]["issue"], rows[2]["issue"])


class DependencyTests(unittest.TestCase):
    def test_same_bundle_waw_keeps_youngest_producer(self):
        result = simulate("Mult.d F1 F2 F3\nAdd.d F1 F4 F5\nAdd.d F6 F1 F0",
                          issue_width=4, registers={"F2": 2, "F3": 3, "F4": 4, "F5": 5})
        self.assertEqual(result["registers"]["F1"], 9)
        self.assertEqual(result["registers"]["F6"], 9)

    def test_late_reader_does_not_observe_stale_older_waw_result(self):
        lines = ["Mult.d F1 F2 F3", "Add.d F1 F4 F5"] + ["Addi R1 R1 1"] * 12 + ["Add.d F6 F1 F0"]
        result = simulate("\n".join(lines), issue_width=4, rs_int=1,
                          registers={"F2": 2, "F3": 3, "F4": 4, "F5": 5})
        self.assertGreater(result["instructions"][-1]["issue"], result["instructions"][0]["wb"])
        self.assertEqual(result["registers"]["F6"], 9)

    def test_war_captures_old_operand_before_younger_rename(self):
        result = simulate("Mult.d F2 F1 F3\nAdd.d F1 F4 F5", issue_width=2,
                          registers={"F1": 10, "F3": 2, "F4": 1, "F5": 2})
        self.assertEqual(result["registers"]["F2"], 20)
        self.assertEqual(result["registers"]["F1"], 3)

    def test_self_renaming_reads_previous_value(self):
        result = simulate("Addi R1 R1 1\nAddi R1 R1 1\nAdd R2 R1 R1", issue_width=3, registers={"R1": 5})
        self.assertEqual(result["registers"]["R1"], 7)
        self.assertEqual(result["registers"]["R2"], 14)

    def test_r0_is_immutable_and_never_renamed(self):
        result = simulate("Addi R0 R0 19\nAddi R1 R0 7\nAdd R2 R0 R1", issue_width=3)
        self.assertEqual(result["registers"]["R0"], 0)
        self.assertEqual(result["registers"]["R1"], 7)
        self.assertEqual(result["registers"]["R2"], 7)

    def test_float_register_zero_is_writable(self):
        result = simulate("Add.d F0 F1 F2\nAdd.d F3 F0 F0", issue_width=2, registers={"F1": 2, "F2": 3})
        self.assertEqual(result["registers"]["F0"], 5)
        self.assertEqual(result["registers"]["F3"], 10)


class MemoryTests(unittest.TestCase):
    def test_final_store_drains_and_updates_memory(self):
        result = simulate("Sd F1 0(R1)", registers={"F1": 7.5, "R1": 12}, mem_latency=5)
        row = result["instructions"][0]
        self.assertEqual(result["memory"][12], 7.5)
        self.assertEqual(row["mem_end"] - row["mem_start"] + 1, 5)
        self.assertGreaterEqual(row["commit"], row["mem_end"])
        self.assertGreaterEqual(result["cycles"], row["commit"])

    def test_load_stage_boundaries_and_exact_memory_latency(self):
        result = simulate("Ld F1 0(R1)", registers={"R1": 12}, memory={12: 4.5}, addr_latency=2, mem_latency=5)
        row = result["instructions"][0]
        self.assertEqual(row["exe_end"] - row["exe_start"] + 1, 2)
        self.assertEqual(row["mem_end"] - row["mem_start"] + 1, 5)
        self.assertGreater(row["mem_start"], row["exe_end"])
        self.assertGreater(row["wb"], row["mem_end"])
        self.assertGreater(row["commit"], row["wb"])
        self.assertEqual(result["registers"]["F1"], 4.5)

    def test_negative_offset_multidigit_base_register(self):
        result = simulate("Ld F31 -8(R12)\nSd F31 4(R31)", issue_width=2,
                          registers={"R12": 20, "R31": 28}, memory={12: 11.25})
        self.assertEqual(result["registers"]["F31"], 11.25)
        self.assertEqual(result["memory"][32], 11.25)

    def test_unknown_older_store_address_speculates_then_replays_alias(self):
        result = simulate("Addi R10 R0 12\nSd F1 0(R10)\nLd F2 0(R1)", issue_width=3,
                          int_latency=6, addr_units=2, mem_latency=2,
                          registers={"F1": 9, "R1": 12}, memory={12: 3})
        self.assertEqual(result["registers"]["F2"], 9)
        rows = result["instructions"]
        self.assertLess(rows[2]["mem_start"], rows[1]["exe_end"])
        self.assertTrue(rows[2]["squashed"])
        self.assertEqual(len(result["memory_replays"]), 1)
        self.assertEqual(len([r for r in rows if r["pc"] == 2 and r["commit"] is not None]), 1)

    def test_load_waits_for_older_store_data(self):
        result = simulate("Mult.d F1 F3 F4\nSd F1 0(R1)\nLd F2 0(R1)", issue_width=3,
                          registers={"R1": 12, "F3": 3, "F4": 4}, memory={12: 1})
        self.assertEqual(result["registers"]["F2"], 12)
        self.assertGreater(result["instructions"][2]["mem_start"], result["instructions"][0]["wb"])

    def test_forwarding_uses_youngest_older_store(self):
        result = simulate("Mult.d F20 F3 F4\nSd F1 0(R1)\nSd F2 0(R1)\nLd F5 0(R1)",
                          issue_width=4, addr_units=2, mem_units=2,
                          registers={"R1": 12, "F1": 7, "F2": 9, "F3": 2, "F4": 2}, memory={12: 1})
        self.assertEqual(result["registers"]["F5"], 9)
        self.assertEqual(result["memory"][12], 9)
        rows = result["instructions"]
        self.assertEqual(rows[3]["forwarded_from"], rows[2]["id"])

    def test_nonalias_load_bypasses_store_waiting_for_data(self):
        result = simulate("Mult.d F1 F3 F4\nSd F1 0(R1)\nLd F2 4(R1)", issue_width=3,
                          addr_units=2, mem_units=2,
                          registers={"R1": 8, "F3": 3, "F4": 4}, memory={12: 5})
        rows = result["instructions"]
        self.assertLess(rows[2]["wb"], rows[0]["wb"])
        self.assertEqual(result["registers"]["F2"], 5)

    def test_two_memory_units_overlap_independent_loads(self):
        result = simulate("Ld F1 0(R1)\nLd F2 4(R1)", issue_width=2, addr_units=2, mem_units=2,
                          registers={"R1": 8}, memory={8: 2, 12: 3})
        rows = result["instructions"]
        self.assertEqual(rows[0]["mem_start"], rows[1]["mem_start"])
        self.assertEqual((result["registers"]["F1"], result["registers"]["F2"]), (2, 3))

    def test_one_memory_unit_is_not_pipelined(self):
        result = simulate("Ld F1 0(R1)\nLd F2 4(R1)", issue_width=2, addr_units=2, mem_units=1,
                          registers={"R1": 8}, memory={8: 2, 12: 3})
        rows = result["instructions"]
        self.assertGreater(rows[1]["mem_start"], rows[0]["mem_end"])

    def test_lsq_capacity_one_does_not_drop_loads_or_stores(self):
        result = simulate("Ld F1 0(R1)\nSd F1 4(R1)\nLd F2 4(R1)", issue_width=4,
                          lsq_size=1, registers={"R1": 8}, memory={8: 6})
        self.assertEqual(result["registers"]["F2"], 6)
        self.assertEqual(result["memory"][12], 6)


class BranchAndInputTests(unittest.TestCase):
    def test_final_branch_does_not_crash(self):
        result = simulate("Bne R0 R0 0", issue_width=4)
        self.assertEqual(len(result["instructions"]), 1)
        self.assertIsNotNone(result["instructions"][0]["commit"])

    def test_taken_branch_skips_younger_same_bundle_instruction(self):
        for width in range(1, 5):
            with self.subTest(width=width):
                result = simulate("Addi R1 R0 1\nBne R1 R0 1\nAddi R2 R0 99\nAddi R3 R0 7", issue_width=width)
                self.assertEqual(result["registers"]["R2"], 0)
                self.assertEqual(result["registers"]["R3"], 7)
                self.assertEqual([r["pc"] for r in result["instructions"] if r["commit"] is not None], [0, 1, 3])

    def test_not_taken_branch_continues(self):
        result = simulate("Bne R0 R0 1\nAddi R1 R0 7", issue_width=4)
        self.assertEqual(result["registers"]["R1"], 7)

    def test_beq_taken(self):
        result = simulate("Beq R0 R0 1\nAddi R1 R0 99\nAddi R2 R0 7", issue_width=4)
        self.assertEqual((result["registers"]["R1"], result["registers"]["R2"]), (0, 7))

    def test_loop_uses_next_pc_relative_word_displacement(self):
        result = simulate("Addi R1 R1 -1\nAddi R2 R2 2\nBne R1 R0 -3", issue_width=4,
                          registers={"R1": 3})
        self.assertEqual(result["registers"]["R1"], 0)
        self.assertEqual(result["registers"]["R2"], 6)
        committed = [r for r in result["instructions"] if r["commit"] is not None]
        self.assertEqual(len(committed), 9)
        self.assertEqual([r["pc"] for r in committed], [0, 1, 2] * 3)
        self.assertEqual(len(set(r["id"] for r in result["instructions"])), len(result["instructions"]))

    def test_empty_program(self):
        result = simulate("")
        self.assertEqual(result["cycles"], 0)
        self.assertEqual(result["instructions"], [])

    def test_comments_commas_case_and_labels(self):
        result = simulate("# comments\nAddi R1, R0, 2\nloop: addi R1, R1, -1 # decrement\nbne R1, R0, loop\n")
        self.assertEqual(result["registers"]["R1"], 0)
        self.assertEqual(len([r for r in result["instructions"] if r["commit"] is not None]), 5)

    def test_invalid_programs_fail_explicitly(self):
        bad = ["Unknown R1 R2 R3", "Addi R32 R0 1", "Addi R1 R0", "Ld F1 nonsense", "Bne R0 R0 3", "Add.d F1 R2 F3"]
        for text in bad:
            with self.subTest(program=text):
                with self.assertRaises((ValueError, TypeError)):
                    simulate(text)

    def test_invalid_configuration_fails_explicitly(self):
        for options in ({"issue_width": 0}, {"issue_width": 5}, {"writeback_width": 0}, {"commit_width": 0},
                        {"rob_size": 0}, {"rs_int": 0}, {"int_latency": 0}, {"mem_units": 0}):
            with self.subTest(options=options):
                with self.assertRaises((ValueError, TypeError)):
                    simulate("Addi R1 R0 1", **options)


class DifferentialTests(unittest.TestCase):
    def test_seeded_programs_match_independent_sequential_interpreter(self):
        """80 runs: 20 different programs at each of four issue widths."""
        for seed in range(20):
            rng = random.Random(seed)
            initial_registers = {**{f"R{i}": rng.randrange(-5, 6) for i in range(1, 9)},
                                 **{f"F{i}": float(rng.randrange(-3, 4)) for i in range(1, 9)}, "R20": 64}
            initial_memory = {64 + 4 * i: float(rng.randrange(-5, 6)) for i in range(8)}
            lines = []
            for _ in range(40):
                op = rng.choice(["Addi", "Add", "Sub", "Add.d", "Sub.d", "Mult.d", "Ld", "Sd"])
                a, b, c = [rng.randrange(1, 9) for _ in range(3)]
                if op == "Addi":
                    lines.append(f"Addi R{a} R{b} {rng.randrange(-3, 4)}")
                elif op in ("Add", "Sub"):
                    lines.append(f"{op} R{a} R{b} R{c}")
                elif op in ("Ld", "Sd"):
                    lines.append(f"{op} F{a} {4 * rng.randrange(8)}(R20)")
                else:
                    lines.append(f"{op} F{a} F{b} F{c}")
            expected_registers, expected_memory = sequential_oracle(lines, initial_registers, initial_memory)
            for width in range(1, 5):
                with self.subTest(seed=seed, issue_width=width):
                    result = simulate("\n".join(lines), issue_width=width,
                                      registers=initial_registers, memory=initial_memory)
                    self.assertEqual(result["registers"], expected_registers)
                    for address in set(expected_memory) | set(result["memory"]):
                        self.assertEqual(result["memory"].get(address, 0), expected_memory.get(address, 0))
                    rows = result["instructions"]
                    self.assertEqual(len(rows), len(lines))
                    self.assertEqual([r["commit"] for r in rows], sorted(r["commit"] for r in rows))
                    for row in rows:
                        self.assertGreater(row["exe_start"], row["issue"])
                        if row["wb"] is not None:
                            completed = row["mem_end"] if row["mem_end"] is not None else row["exe_end"]
                            self.assertGreater(row["wb"], completed)
                            self.assertGreater(row["commit"], row["wb"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
