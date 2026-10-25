"""Exact-value regressions for single-precision loads and stores.

Expected values below are independently specified binary32 constants. These
tests do not use the simulator's conversion routine to produce expectations.
"""

import math
import unittest

from tomasulo.engine import Simulator
from tomasulo.model import Config, Program
from tomasulo.parser import parse_text

try:
    from tests.oracle import execute
except ModuleNotFoundError:
    from oracle import execute


class MemoryPrecisionTests(unittest.TestCase):
    def run_at_width(self, source, width):
        result = Simulator(parse_text(source), issue_width=width).run()
        expected = execute(source)
        self.assertEqual(result.registers, expected.registers)
        self.assertEqual(result.memory, expected.memory)
        return result

    def test_memory_initialization_and_load_round_to_binary32(self):
        source = """Mem[0]=16777217, Mem[4]=0.1
Ld F1 0(R0)
Ld F2 4(R0)
"""
        program = parse_text(source)
        self.assertEqual(program.memory[0], 16777216.0)
        self.assertEqual(program.memory[4], 0.10000000149011612)
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.run_at_width(source, width)
                self.assertEqual(result.registers['F1'], 16777216.0)
                self.assertEqual(result.registers['F2'], 0.10000000149011612)

    def test_direct_program_memory_initialization_is_also_rounded(self):
        program = Program(Config(), [], {}, {0: 16777217.0})
        result = Simulator(program).run()
        self.assertEqual(result.memory[0], 16777216.0)

    def test_forwarded_and_later_memory_load_have_identical_rounding(self):
        # The multiply holds retirement so the first load must forward.
        # The integer dependency chain delays the second load until the store
        # has committed, exercising both data paths with the same source value.
        source = """Integer adder 16 4 1
FP multiplier 2 20 1
Load/store unit 8 1 3 1
ROB entries = 64
F1=2, F2=16777217
Mult.d F10 F1 F1
Sd F2 0(R0)
Ld F3 0(R0)
""" + "Addi R1 R1 0\n" * 10 + "Ld F4 0(R1)\n"
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.run_at_width(source, width)
                loads = [row for row in result.history
                         if row['instruction'].lower().startswith('ld')]
                store = next(row for row in result.history
                             if row['instruction'].lower().startswith('sd'))
                self.assertTrue(loads[0]['forwarded'])
                self.assertEqual(loads[0]['mem_start'], loads[0]['mem_end'])
                self.assertLess(loads[0]['mem_end'], store['commit'])
                self.assertFalse(loads[1]['forwarded'])
                self.assertGreater(loads[1]['mem_start'], store['commit'])
                self.assertEqual(result.registers['F2'], 16777217.0)
                self.assertEqual(result.registers['F3'], 16777216.0)
                self.assertEqual(result.registers['F4'], 16777216.0)
                self.assertEqual(result.memory[0], 16777216.0)

    def test_double_arithmetic_stays_unrounded_until_store(self):
        source = """F1=16777216, F2=1
Add.d F3 F1 F2
Sd F3 0(R0)
Ld F4 0(R0)
"""
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.run_at_width(source, width)
                self.assertEqual(result.registers['F3'], 16777217.0)
                self.assertEqual(result.registers['F4'], 16777216.0)
                self.assertEqual(result.memory[0], 16777216.0)

    def test_signed_zero_subnormals_and_ties_to_even(self):
        # 2**-149 is the smallest positive binary32 subnormal. 2**-150 is
        # exactly halfway to it and rounds to even zero. 3*2**-150 rounds to
        # the even significand 2, or 2**-148.
        tiny = 2.0 ** -149
        source = f"""F1=-0.0, F2={tiny!r}, F3={tiny / 2!r}, F4={3 * tiny / 2!r}
Sd F1 0(R0)
Sd F2 4(R0)
Sd F3 8(R0)
Sd F4 12(R0)
Ld F5 0(R0)
Ld F6 4(R0)
Ld F7 8(R0)
Ld F8 12(R0)
"""
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.run_at_width(source, width)
                self.assertEqual(math.copysign(1, result.memory[0]), -1)
                self.assertEqual(math.copysign(1, result.registers['F5']), -1)
                self.assertEqual(result.registers['F6'], 2.0 ** -149)
                self.assertEqual(result.registers['F7'], 0.0)
                self.assertEqual(result.registers['F8'], 2.0 ** -148)

    def test_binary32_overflow_becomes_signed_infinity(self):
        source = """F1=1e40, F2=-1e40
Mem[8]=1e40, Mem[12]=-1e40
Sd F1 0(R0)
Sd F2 4(R0)
Ld F3 0(R0)
Ld F4 4(R0)
Ld F5 8(R0)
Ld F6 12(R0)
"""
        for width in range(1, 5):
            with self.subTest(width=width):
                result = self.run_at_width(source, width)
                self.assertEqual(result.registers['F1'], 1e40)
                self.assertEqual(result.registers['F2'], -1e40)
                for register in ('F3', 'F5'):
                    self.assertEqual(result.registers[register], math.inf)
                for register in ('F4', 'F6'):
                    self.assertEqual(result.registers[register], -math.inf)


if __name__ == '__main__':
    unittest.main()
