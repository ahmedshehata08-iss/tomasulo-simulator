"""Input data model for the Tomasulo simulator.

Instruction PCs and branch offsets use instruction-word indices.  Data-memory
addresses and load/store offsets use bytes.
"""

from dataclasses import dataclass, field


@dataclass
class UnitConfig:
    """Reservation stations, execution latency, and number of functional units."""

    rs: int
    ex_cycles: int
    count: int = 1


@dataclass
class Config:
    issue_width: int = 1
    rob_entries: int = 128
    int_unit: UnitConfig = field(default_factory=lambda: UnitConfig(2, 1))
    fp_add: UnitConfig = field(default_factory=lambda: UnitConfig(3, 3))
    fp_mul: UnitConfig = field(default_factory=lambda: UnitConfig(2, 20))
    lsu: UnitConfig = field(default_factory=lambda: UnitConfig(3, 1))
    mem_cycles: int = 4

    def validate(self) -> None:
        """Reject invalid configurations before simulation begins."""

        def positive_integer(name: str, value: int) -> None:
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        positive_integer("issue width", self.issue_width)
        if self.issue_width > 4:
            raise ValueError("issue width must be between 1 and 4")
        positive_integer("ROB entries", self.rob_entries)
        positive_integer("memory cycles", self.mem_cycles)
        for name, unit in (
            ("integer adder", self.int_unit),
            ("FP adder", self.fp_add),
            ("FP multiplier", self.fp_mul),
            ("load/store unit", self.lsu),
        ):
            if not isinstance(unit, UnitConfig):
                raise ValueError(f"{name} must be a UnitConfig")
            positive_integer(f"{name} reservation stations", unit.rs)
            positive_integer(f"{name} execution cycles", unit.ex_cycles)
            positive_integer(f"{name} functional units", unit.count)


@dataclass
class Instruction:
    pc: int
    op: str
    dest: str | None
    sources: tuple[str, ...]
    immediate: int = 0
    text: str = ""


@dataclass
class Program:
    config: Config
    instructions: list[Instruction]
    registers: dict[str, int | float]
    memory: dict[int, float]
