"""Tests derived directly from the supplied assignment handout.

Assignment author: Ahmed Ahmed.
Expected branch paths, values, resource occupancy and recovery cycles are
calculated independently of the scheduler implementation.
"""
import collections
import subprocess
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from init import Config, parse_input, parse_program
from simulator import Simulator
from test_simulator import simulate


HANDOUT_INPUT = """# of rs Cycles in EX Cycles in Mem # of FUs
Integer adder 2 1 1
FP adder 3 3 1
FP multiplier 2 20 1
Load/store unit 3 1 4 1
ROB entries =128
Issue width=2
R1=10,R2=20,F2=30.1
Mem[4]=1,Mem[8]=2,Mem[12]=3.4
Add.d F1,F2,F3
Ld F4,8(R1)
Bne R2,R3,-3
"""


def committed(result):
    return [row for row in result["instructions"] if row["commit"] is not None]


class HandoutInputTests(unittest.TestCase):
    def test_exact_handout_input_parses_in_one_file(self):
        program, config, registers, memory = parse_input(HANDOUT_INPUT)
        self.assertEqual(len(program), 3)
        self.assertEqual((config.rs_int, config.int_latency, config.int_units), (2, 1, 1))
        self.assertEqual((config.rs_fp_add, config.fp_add_latency, config.fp_add_units), (3, 3, 1))
        self.assertEqual((config.rs_fp_mul, config.fp_mul_latency, config.fp_mul_units), (2, 20, 1))
        self.assertEqual((config.lsq_size, config.addr_latency, config.mem_latency, config.addr_units), (3, 1, 4, 1))
        self.assertEqual((config.rob_size, config.issue_width), (128, 2))
        self.assertEqual((config.writeback_width, config.mem_units), (1, 1))
        self.assertEqual(registers, {"R1": 10, "R2": 20, "F2": 30.1})
        self.assertEqual(memory, {4: 1, 8: 2, 12: 3.4})
        self.assertEqual(program[2].target, 0)

    def test_whole_input_and_labels_are_case_insensitive(self):
        first = parse_input(HANDOUT_INPUT)
        second = parse_input(HANDOUT_INPUT.lower())
        self.assertEqual(first[1:], second[1:])
        self.assertEqual([(i.op, i.dest, i.sources, i.target) for i in first[0]],
                         [(i.op, i.dest, i.sources, i.target) for i in second[0]])
        result = simulate("Addi R1 R0 2\nLoop: Addi R1 R1 -1\nBne R1 R0 LOOP")
        self.assertEqual(result["registers"]["R1"], 0)

    def test_default_width_and_architectural_state_are_zero(self):
        program, config, registers, memory = parse_input("Add R1,R2,R3")
        self.assertEqual(config.issue_width, 1)
        self.assertEqual(registers, {})
        self.assertEqual(memory, {})
        result = Simulator(program, config).run()
        self.assertEqual(len(result["registers"]), 64)
        self.assertTrue(all(value == 0 for value in result["registers"].values()))
        self.assertTrue(all(value == 0 for value in result["memory"].values()))

    def test_handout_example_loop_is_guarded_not_falsely_reported_complete(self):
        program, config, registers, memory = parse_input(HANDOUT_INPUT)
        config.max_cycles = 100
        with self.assertRaisesRegex(RuntimeError, "100|termination"):
            Simulator(program, config, registers, memory).run()

    def test_omitted_registers_stay_zero_with_partial_initialization(self):
        program, config, registers, memory = parse_input("R1=7\nF2=2.5\nAdd.d F3,F2,F0\nAdd R4,R1,R2")
        result = Simulator(program, config, registers, memory).run()
        self.assertEqual((result["registers"]["R4"], result["registers"]["F3"]), (7, 2.5))
        self.assertEqual(result["registers"]["F20"], 0)

    def test_invalid_single_file_values_fail_explicitly(self):
        inputs = ["Issue width=5", "ROB entries=0", "Integer adder 0 1 1", "R32=1", "R2=1.5",
                  "F2=nan", "Mem[-1]=2", "Mem[253]=2", "Mem[256]=2"]
        for text in inputs:
            with self.subTest(text=text), self.assertRaises((ValueError, TypeError)):
                parse_input(text)

    def test_cli_accepts_one_file_and_emits_required_output(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            input_file = Path(directory) / "case.in"
            input_file.write_text("Issue width=2\nR1=4\nMem[4]=2.5\nLd F1,0(R1)\nAdd.d F2,F1,F1\n", encoding="utf-8")
            run = subprocess.run([sys.executable, str(root / "code" / "main.py"), "--input", str(input_file)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        for word in ("ISSUE", "EX", "MEM", "WB", "COMMIT", "REGISTER", "MEMORY", "Ahmed Ahmed"):
            self.assertIn(word.upper(), run.stdout.upper())


class HardwareContractTests(unittest.TestCase):
    def test_integer_unit_cannot_overlap_multicycle_operations(self):
        result = simulate("\n".join(f"Addi R{i} R0 {i}" for i in range(1, 5)),
                          issue_width=4, int_latency=3, int_units=1)
        rows = committed(result)
        self.assertEqual([r["exe_start"] for r in rows], [2, 5, 8, 11])
        self.assertEqual([r["exe_end"] for r in rows], [4, 7, 10, 13])

    def test_parameterized_integer_units_each_remain_unpipelined(self):
        result = simulate("\n".join(f"Addi R{i} R0 {i}" for i in range(1, 7)),
                          issue_width=4, int_latency=3, int_units=2, rs_int=8)
        rows = committed(result)
        for cycle in range(1, result["cycles"] + 1):
            self.assertLessEqual(sum(r["exe_start"] <= cycle <= r["exe_end"] for r in rows), 2)
        self.assertEqual([r["exe_start"] for r in rows], [2, 2, 5, 5, 8, 8])

    def test_address_adder_does_not_occupy_integer_unit(self):
        result = simulate("Addi R1 R0 8\nLd F1 12(R0)", issue_width=2, int_latency=6, memory={12: 7})
        rows = committed(result)
        self.assertEqual((rows[0]["exe_start"], rows[1]["exe_start"]), (2, 2))
        self.assertEqual(result["registers"]["F1"], 7)

    def test_primary_profile_has_one_cdb_and_one_nonpipelined_memory_port(self):
        result = simulate("Ld F1 0(R0)\nLd F2 4(R0)\nAdd.d F3 F0 F0\nAddi R1 R0 1",
                          issue_width=4, addr_units=2, mem_latency=4)
        rows = committed(result)
        self.assertGreater(rows[1]["mem_start"], rows[0]["mem_end"])
        self.assertTrue(all(count <= 1 for count in collections.Counter(r["wb"] for r in rows if r["wb"] is not None).values()))

    def test_load_releases_lsq_at_mem_completion_before_writeback(self):
        result = simulate("Ld F1 0(R0)\nLd F2 4(R0)", issue_width=4, lsq_size=1, mem_latency=2)
        first, second = committed(result)
        self.assertEqual(second["issue"], first["mem_end"])
        self.assertLess(second["issue"], first["wb"])

    def test_store_memory_changes_only_at_commit_and_lsq_releases_then(self):
        sim = Simulator(parse_program("Sd F1 4(R0)"), Config(mem_latency=4), {"F1": 7}, {4: 2})
        while not sim.history or sim.history[0].commit is None:
            old_commit = sim.history[0].commit if sim.history else None
            sim.step()
            entry = sim.history[0]
            if entry.commit is None:
                self.assertEqual(sim.memory[4], 2)
                self.assertIn(entry, sim.lsq)
            else:
                self.assertIsNone(old_commit)
                self.assertEqual(sim.memory[4], 7)
                self.assertNotIn(entry, sim.lsq)

    def test_memory_accepts_last_four_byte_value_and_example_unaligned_address(self):
        result = simulate("Ld F1 252(R0)\nLd F2 8(R1)", registers={"R1": 10}, memory={252: 2.5, 18: 7.5})
        self.assertEqual((result["registers"]["F1"], result["registers"]["F2"]), (2.5, 7.5))

    def test_memory_load_and_store_use_single_precision_rounding(self):
        rounded = struct.unpack("<f", struct.pack("<f", 0.1))[0]
        result = simulate("Ld F1 0(R0)\nSd F2 8(R0)\nLd F3 8(R0)", issue_width=3,
                          registers={"F2": 0.1}, memory={0: 0.1})
        self.assertEqual((result["registers"]["F1"], result["registers"]["F3"]), (rounded, rounded))
        self.assertEqual(result["memory"][8], rounded)

    def test_unaligned_store_overlaps_underlying_bytes(self):
        expected = bytearray(8)
        struct.pack_into("<f", expected, 0, 1.0)
        struct.pack_into("<f", expected, 4, 2.0)
        struct.pack_into("<f", expected, 2, 3.5)
        result = simulate("Sd F1 2(R0)\nLd F2 0(R0)\nLd F3 4(R0)\nLd F4 2(R0)",
                          issue_width=4, registers={"F1": 3.5}, memory={0: 1.0, 4: 2.0})
        self.assertEqual(result["registers"]["F2"], struct.unpack_from("<f", expected, 0)[0])
        self.assertEqual(result["registers"]["F3"], struct.unpack_from("<f", expected, 4)[0])
        self.assertEqual(result["registers"]["F4"], 3.5)

    def test_memory_rejects_out_of_range_initial_and_effective_addresses(self):
        for address in (-1, 253, 256):
            with self.subTest(address=address), self.assertRaises(ValueError):
                simulate("", memory={address: 1})
            with self.subTest(effective=address), self.assertRaises(ValueError):
                simulate(f"Ld F1 {address}(R0)")


class SpeculationContractTests(unittest.TestCase):
    def test_misprediction_resumes_exactly_two_cycles_after_resolution(self):
        for width in range(1, 5):
            with self.subTest(width=width):
                result = simulate("Beq R0 R0 1\nAddi R1 R0 99\nAddi R2 R0 7", issue_width=width)
                branch, target = committed(result)
                event = result["branch_mispredictions"][0]
                self.assertEqual(event["cycle"], branch["exe_end"])
                self.assertEqual(event["resume_cycle"], branch["exe_end"] + 2)
                self.assertEqual(target["issue"], branch["exe_end"] + 2)
                self.assertEqual(result["registers"]["R1"], 0)

    def test_correct_not_taken_prediction_has_no_recovery_bubble(self):
        result = simulate("Bne R0 R0 1\nAddi R1 R0 7\nAddi R2 R0 8", issue_width=4)
        rows = committed(result)
        self.assertEqual([r["issue"] for r in rows], [1, 1, 1])
        self.assertEqual(result["branch_mispredictions"], [])
        self.assertFalse(result["branch_predictions"][0]["predicted_taken"])

    def test_one_bit_predictor_learns_taken_then_mispredicts_loop_exit(self):
        sim = Simulator(parse_program("Addi R1 R1 -1\nBne R1 R0 -2"), Config(issue_width=4), {"R1": 3}, {})
        result = sim.run()
        branches = [r for r in committed(result) if r["pc"] == 1]
        predictions = {p["id"]: p for p in result["branch_predictions"]}
        self.assertEqual([r["actual_taken"] for r in branches], [True, True, False])
        self.assertEqual([predictions[r["id"]]["predicted_taken"] for r in branches], [False, True, True])
        self.assertEqual(sum(r["mispredicted"] for r in branches), 2)
        self.assertFalse(sim.predictor[1])
        self.assertEqual(result["registers"]["R1"], 0)
        self.assertEqual(result["instruction_count"], 6)

    def test_btb_word_pc_low_three_bits_and_tags_handle_aliasing_branches(self):
        program = ["Beq R0 R0 6"] + [f"Addi R{i} R0 99" for i in range(2, 8)]
        program += ["Addi R1 R1 -1", "Bne R1 R0 -9"]
        sim = Simulator(parse_program("\n".join(program)), Config(issue_width=4), {"R1": 3}, {})
        result = sim.run()
        branches = [r for r in committed(result) if r["pc"] in (0, 8)]
        predictions = {p["id"]: p for p in result["branch_predictions"]}
        self.assertEqual([r["pc"] for r in branches], [0, 8] * 3)
        self.assertTrue(all(predictions[r["id"]]["btb_index"] == 0 for r in branches))
        self.assertEqual(len(sim.btb), 8)
        self.assertTrue(sim.predictor[0])
        self.assertFalse(sim.predictor[8])
        self.assertEqual(result["registers"]["R1"], 0)
        self.assertTrue(all(result["registers"][f"R{i}"] == 0 for i in range(2, 8)))

    def test_recovery_restores_surviving_older_rename_and_clears_wrong_path_tags(self):
        result = simulate("Mult.d F1 F2 F3\nBeq R0 R0 2\nAdd.d F1 F4 F5\nAdd.d F6 F1 F1\nAdd.d F7 F1 F1",
                          issue_width=4, fp_mul_latency=10,
                          registers={"F2": 2, "F3": 3, "F4": 20, "F5": 30})
        self.assertEqual((result["registers"]["F1"], result["registers"]["F6"], result["registers"]["F7"]), (6, 0, 12))
        self.assertEqual([r["pc"] for r in committed(result)], [0, 1, 4])
        self.assertTrue(all(r["commit"] is None for r in result["instructions"] if r["squashed"]))

    def test_wrong_path_store_never_modifies_memory(self):
        result = simulate("Addi R1 R0 1\nBne R1 R0 2\nSd F1 12(R0)\nAdd.d F2 F1 F1\nAddi R2 R0 7",
                          issue_width=4, int_latency=4, registers={"F1": 9}, memory={12: 3})
        self.assertEqual(result["memory"][12], 3)
        self.assertEqual(result["registers"]["F2"], 0)
        self.assertEqual(result["registers"]["R2"], 7)
        self.assertTrue(any(r["pc"] == 2 and r["squashed"] for r in result["instructions"]))

    def test_wrong_path_invalid_load_is_squashed_before_fault_can_commit(self):
        result = simulate("Beq R0 R0 1\nLd F1 256(R0)\nAddi R1 R0 7",
                          issue_width=4, int_latency=4)
        self.assertEqual(result["registers"]["R1"], 7)
        self.assertEqual(result["registers"]["F1"], 0)
        self.assertTrue(any(r["pc"] == 1 and r["squashed"] for r in result["instructions"]))

    def test_alias_replay_cancels_an_inflight_memory_access_at_every_width(self):
        for width in range(1, 5):
            with self.subTest(width=width):
                result = simulate("Addi R10 R0 12\nSd F1 0(R10)\nLd F2 12(R0)",
                                  issue_width=width, int_latency=4, mem_latency=10,
                                  registers={"F1": 9}, memory={12: 3})
                event = result["memory_replays"][0]
                old_load = next(r for r in result["instructions"] if r["id"] == event["load_id"])
                self.assertLess(old_load["mem_start"], event["cycle"])
                self.assertLess(event["cycle"], old_load["mem_start"] + 10 - 1)
                self.assertIsNone(old_load["mem_end"])
                self.assertTrue(old_load["squashed"])
                self.assertIsNone(old_load["wb"])
                self.assertEqual(result["registers"]["F2"], 9)
                self.assertEqual(result["instruction_count"], 3)

    def test_unknown_store_partial_byte_overlap_replays_load(self):
        expected = bytearray(struct.pack("<f", 1.0) + bytes(4))
        struct.pack_into("<f", expected, 2, 3.5)
        result = simulate("Addi R10 R0 2\nSd F1 0(R10)\nLd F2 0(R0)",
                          issue_width=3, int_latency=8, mem_latency=2,
                          registers={"F1": 3.5}, memory={0: 1.0})
        self.assertEqual(result["registers"]["F2"], struct.unpack_from("<f", expected, 0)[0])
        self.assertEqual(len(result["memory_replays"]), 1)

    def test_unknown_nonalias_store_does_not_block_load_or_force_replay(self):
        result = simulate("Addi R10 R0 8\nSd F1 0(R10)\nLd F2 12(R0)", issue_width=3,
                          int_latency=8, mem_latency=2, registers={"F1": 9}, memory={12: 3})
        rows = committed(result)
        self.assertLess(rows[2]["mem_start"], rows[1]["exe_end"])
        self.assertEqual(result["registers"]["F2"], 3)
        self.assertEqual(result["memory_replays"], [])

    def test_alias_replay_discards_already_broadcast_value_and_younger_store(self):
        result = simulate("Addi R10 R0 12\nSd F1 0(R10)\nLd F2 12(R0)\nAdd.d F3 F2 F2\nSd F3 16(R0)",
                          issue_width=4, int_latency=8, mem_latency=2, fp_add_latency=1,
                          registers={"F1": 9}, memory={12: 3})
        self.assertEqual((result["registers"]["F2"], result["registers"]["F3"]), (9, 18))
        self.assertEqual((result["memory"][12], result["memory"][16]), (9, 18))
        event = result["memory_replays"][0]
        first_load = next(r for r in result["instructions"] if r["id"] == event["load_id"])
        replayed_load = next(r for r in committed(result) if r["pc"] == 2)
        self.assertLess(first_load["wb"], event["cycle"])
        self.assertTrue(first_load["squashed"])
        self.assertEqual(replayed_load["issue"], event["cycle"] + 2)
        self.assertEqual([r["pc"] for r in committed(result)], [0, 1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main(verbosity=2)
