"""256-byte floating-point memory for the assignment. Author: Ahmed Ahmed.

Byte addresses may be unaligned, as in the handout's illustrative input.
Storage is real bytes; overlapping accesses therefore observe the same bytes.
The mapping enumerates initialized/stored address views for readable output.
"""
from collections.abc import MutableMapping
import math
import struct


class FloatMemory(MutableMapping):
    def __init__(self, size=256):
        if type(size) is not int or size < 4:
            raise ValueError("memory size must be at least four bytes")
        self.data = bytearray(size)
        self.addresses = set()

    @staticmethod
    def normalize(value):
        """Round a stored/forwarded value to IEEE binary32."""
        value = float(value)
        try:
            return struct.unpack("<f", struct.pack("<f", value))[0]
        except OverflowError:
            return math.copysign(float("inf"), value)

    def _check(self, address):
        if type(address) is not int or not 0 <= address <= len(self.data) - 4:
            raise ValueError(f"memory address must be in 0..{len(self.data) - 4}")

    def __getitem__(self, address):
        self._check(address)
        return struct.unpack_from("<f", self.data, address)[0]

    def __setitem__(self, address, value):
        self._check(address)
        struct.pack_into("<f", self.data, address, self.normalize(value))
        self.addresses.add(address)

    def __delitem__(self, address):
        self._check(address)
        self.data[address:address + 4] = b"\x00" * 4
        self.addresses.discard(address)

    def __iter__(self):
        # Include every nonzero aligned word, even if an unaligned write
        # changed its bytes, as well as explicitly written address views.
        words = {a for a in range(0, len(self.data) - 3, 4) if self[a] != 0}
        return iter(sorted(words | self.addresses))

    def __len__(self):
        return sum(1 for _ in self)
