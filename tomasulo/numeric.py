"""Numeric representation for the assignment's 32-bit data-memory words.

Floating-point registers and .d arithmetic use Python's binary64 floats. Ld
and Sd transfer single-precision values, so all memory paths round to binary32
using round-to-nearest, ties-to-even. A rounded overflow becomes signed infinity.
"""

import math
import struct


def memory_word(value: int | float) -> float:
    """Return a binary32 value, represented exactly in a Python float."""
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)
