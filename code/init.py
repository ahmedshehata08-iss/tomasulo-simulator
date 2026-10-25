"""Configuration and state types for the Tomasulo simulator.

Based on the starter by Qing (May 2017).
Assignment modifications: Ahmed Ahmed.
"""
from dataclasses import asdict, dataclass, field, fields
import math
import re

__author__ = "Ahmed Ahmed"


@dataclass
class Config:
    issue_width: int = 1
    writeback_width: int = 1
    commit_width: int = 1
    rs_int: int = 4
    rs_fp_add: int = 3
    rs_fp_mul: int = 2
    lsq_size: int = 5
    rob_size: int = 64
    int_latency: int = 1
    fp_add_latency: int = 4
    fp_mul_latency: int = 15
    addr_latency: int = 1
    mem_latency: int = 5
    int_units: int = 1
    fp_add_units: int = 1
    fp_mul_units: int = 1
    addr_units: int = 1
    mem_units: int = 1
    memory_size: int = 256
    branch_prediction: bool = True
    execution_order: str = "out_of_order"
    max_cycles: int = 100000

    def __post_init__(self):
        for item in fields(self):
            if item.name in ("execution_order", "branch_prediction"):
                continue
            value = getattr(self, item.name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{item.name} must be a positive integer")
        for name in ("issue_width", "writeback_width", "commit_width"):
            if getattr(self, name) > 4:
                raise ValueError(f"{name} must be between 1 and 4")
        if self.execution_order not in ("out_of_order", "in_order"):
            raise ValueError("execution_order must be out_of_order or in_order")
        if type(self.branch_prediction) is not bool:
            raise ValueError("branch_prediction must be true or false")
        if self.memory_size < 4:
            raise ValueError("memory_size must hold at least one four-byte value")

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown configuration keys: " + ", ".join(sorted(unknown)))
        return cls(**values)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Instruction:
    op: str
    text: str
    dest: str | None = None
    sources: tuple = ()
    immediate: int = 0
    target: int | None = None

    @property
    def kind(self):
        if self.op in ("Ld", "Sd"):
            return "memory"
        if self.op in ("Add.d", "Sub.d"):
            return "fp_add"
        if self.op == "Mult.d":
            return "fp_mul"
        return "int"

    @property
    def branch(self):
        return self.op in ("Bne", "Beq")


@dataclass
class Operand:
    value: int | float | None = None
    tag: int | None = None
    ready_cycle: int = 0

    def ready(self, cycle):
        return self.tag is None and self.ready_cycle < cycle


@dataclass
class Entry:
    id: int
    pc: int
    instruction: Instruction
    operands: list
    issue: int
    exe_start: int | None = None
    exe_end: int | None = None
    mem_start: int | None = None
    mem_end: int | None = None
    wb: int | None = None
    commit: int | None = None
    result: int | float | None = None
    address: int | None = None
    complete_cycle: int | None = None
    ready_cycle: int | None = None
    forwarded_from: int | None = None
    status: str = "issued"
    squashed: bool = False
    squash_cycle: int | None = None
    squash_reason: str | None = None
    predicted_taken: bool | None = None
    predicted_next_pc: int | None = None
    actual_taken: bool | None = None
    mispredicted: bool | None = None
    unresolved_store_ids: list = field(default_factory=list)

    def trace(self):
        return {
            "id": self.id, "pc": self.pc, "text": self.instruction.text,
            "issue": self.issue, "exe_start": self.exe_start,
            "exe_end": self.exe_end, "mem_start": self.mem_start,
            "mem_end": self.mem_end, "wb": self.wb, "commit": self.commit,
            "forwarded_from": self.forwarded_from,
            "status": self.status, "squashed": self.squashed,
            "squash_cycle": self.squash_cycle, "squash_reason": self.squash_reason,
            "predicted_taken": self.predicted_taken,
            "predicted_next_pc": self.predicted_next_pc,
            "actual_taken": self.actual_taken, "mispredicted": self.mispredicted,
        }


def register_name(token, prefix=None):
    if not isinstance(token, str):
        raise ValueError("register names must be strings")
    name = token.upper()
    if not re.fullmatch(r"[RF](?:[0-9]|[12][0-9]|3[01])", name):
        raise ValueError(f"Invalid register {token!r}; use R0..R31 or F0..F31")
    if prefix and not name.startswith(prefix):
        raise ValueError(f"Expected a {prefix} register, got {token}")
    return name


def numeric(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


def parse_program(text):
    """Parse whitespace/comma assembly; branches use word offsets or labels.

    Numeric target = current instruction index + 1 + offset.
    Comments begin with # or ;. A target one past the last instruction exits.
    """
    if not isinstance(text, str):
        raise ValueError("program must be assembly text")
    lines, labels = [], {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = re.split(r"[#;]", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        while ":" in line:
            label, line = line.split(":", 1)
            label = label.strip().lower()
            if not re.fullmatch(r"[A-Za-z_]\w*", label) or label in labels:
                raise ValueError(f"Line {line_no}: invalid or duplicate label {label!r}")
            labels[label] = len(lines)
            line = line.strip()
        if line:
            lines.append((line_no, line))
    aliases = {v.lower(): v for v in (
        "Add", "Addi", "Sub", "Add.d", "Sub.d", "Mult.d", "Ld", "Sd", "Bne", "Beq"
    )}
    aliases["mul.d"] = "Mult.d"
    program = []
    for pc, (line_no, line) in enumerate(lines):
        try:
            tokens = line.replace(",", " ").split()
            op = aliases.get(tokens[0].lower())
            if op is None:
                raise ValueError(f"unsupported opcode {tokens[0]!r}")
            if op in ("Ld", "Sd"):
                # Spaces inside offset(base) are accepted.
                if len(tokens) < 3:
                    raise ValueError("load/store expects register and offset(base)")
                reg = register_name(tokens[1], "F")
                address = "".join(tokens[2:])
                match = re.fullmatch(r"([+-]?\d+)\((R\d+)\)", address, re.I)
                if not match:
                    raise ValueError("address must be integer offset(R0..R31)")
                base = register_name(match[2], "R")
                ins = Instruction(op, line, reg if op == "Ld" else None,
                                  (base,) if op == "Ld" else (base, reg), int(match[1]))
            elif op in ("Bne", "Beq"):
                if len(tokens) != 4:
                    raise ValueError("branch expects two integer registers and a target")
                sources = (register_name(tokens[1], "R"), register_name(tokens[2], "R"))
                raw_target = tokens[3].lower()
                if raw_target in labels:
                    target = labels[raw_target]
                    offset = target - pc - 1
                else:
                    try:
                        offset = int(raw_target)
                    except ValueError as exc:
                        raise ValueError(f"unknown branch label {raw_target!r}") from exc
                    target = pc + 1 + offset
                if not 0 <= target <= len(lines):
                    raise ValueError("branch target is outside the program")
                ins = Instruction(op, line, sources=sources, immediate=offset, target=target)
            else:
                if len(tokens) != 4:
                    raise ValueError("arithmetic instruction expects three operands")
                prefix = "F" if ".d" in op else "R"
                dest = register_name(tokens[1], prefix)
                source1 = register_name(tokens[2], prefix)
                if op == "Addi":
                    ins = Instruction(op, line, dest, (source1,), int(tokens[3]))
                else:
                    ins = Instruction(op, line, dest,
                                      (source1, register_name(tokens[3], prefix)))
            program.append(ins)
        except ValueError as exc:
            raise ValueError(f"Line {line_no}: {exc}") from exc
    return program


def read_instruction(codefile):
    """Compatibility convenience: read and parse an assembly file."""
    with open(codefile, encoding="utf-8") as stream:
        return parse_program(stream.read())


def parse_input(text):
    """Read the handout's single-file hardware, initial state, and assembly format.

    The load/store row gives LSQ entries, address EX latency, memory latency,
    and address-unit count. Memory itself remains single-ported. Numeric branch
    displacements are in words; data addresses are byte addresses. Return
    (program, Config, initial_registers, initial_memory).
    """
    if not isinstance(text, str):
        raise ValueError("input must be text")
    values, registers, memory, assembly = {}, {}, {}, []
    row_fields = {
        "integer adder": ("rs_int", "int_latency", "int_units"),
        "fp adder": ("rs_fp_add", "fp_add_latency", "fp_add_units"),
        "fp multiplier": ("rs_fp_mul", "fp_mul_latency", "fp_mul_units"),
        "load/store unit": ("lsq_size", "addr_latency", "mem_latency", "addr_units"),
    }
    config_names = {f.name.replace("_", " "): f.name for f in fields(Config)}
    config_names["rob entries"] = "rob_size"
    state_pattern = re.compile(r"(mem\s*\[\s*[+-]?\d+\s*\]|[rf]\d+)\s*=\s*([^,]+)", re.I)

    def number(token, label):
        try:
            result = int(token) if re.fullmatch(r"[+-]?\d+", token) else float(token)
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric") from exc
        return numeric(result, label)

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = re.split(r"[#;]", raw, maxsplit=1)[0].strip()
        if not line:
            assembly.append("")
            continue
        try:
            row = re.match(r"^(integer\s+adder|fp\s+adder|fp\s+multiplier|load\s*/\s*store\s+unit)\s*:?\s+(.+)$", line, re.I)
            if row:
                name = " ".join(row[1].lower().split())
                name = re.sub(r"\s*/\s*", "/", name)
                keys = row_fields[name]
                tokens = row[2].replace(",", " ").split()
                if len(tokens) != len(keys) or any(not re.fullmatch(r"\d+", token) for token in tokens):
                    raise ValueError(f"{name} needs {len(keys)} positive integer columns")
                values.update(zip(keys, map(int, tokens)))
            elif "=" in line and re.match(r"^(mem\s*\[|[rf]\d+\s*=)", line, re.I):
                # Full coverage prevents an invalid assignment being silently ignored.
                for assignment in line.split(","):
                    match = state_pattern.fullmatch(assignment.strip())
                    if not match:
                        raise ValueError(f"invalid initial-state assignment {assignment.strip()!r}")
                    target, token = match[1], match[2].strip()
                    value = number(token, target)
                    if target.lower().startswith("mem"):
                        address = int(re.search(r"\[\s*([+-]?\d+)\s*\]", target)[1])
                        memory[address] = value
                    else:
                        reg = register_name(target)
                        if reg.startswith("R") and int(value) != value:
                            raise ValueError(f"{reg} must have an integer value")
                        if reg == "R0" and value != 0:
                            raise ValueError("R0 is hardwired to zero")
                        registers[reg] = int(value) if reg.startswith("R") else float(value)
            elif "=" in line:
                name, value = (part.strip() for part in line.split("=", 1))
                name = " ".join(name.lower().replace("_", " ").split())
                key = config_names.get(name)
                if key is None:
                    raise ValueError(f"unknown hardware parameter {name!r}")
                if key == "branch_prediction":
                    if value.lower() not in ("true", "false"):
                        raise ValueError("branch_prediction must be true or false")
                    values[key] = value.lower() == "true"
                elif key == "execution_order":
                    values[key] = value.lower().replace(" ", "_")
                else:
                    values[key] = int(value)
            elif "cycles in ex" in line.lower() and "cycles in mem" in line.lower():
                pass  # Optional plain-text column heading.
            else:
                assembly.append(line)
                continue
            assembly.append("")  # Keep original source line numbers in errors.
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Line {line_no}: {exc}") from exc
    config = Config.from_dict(values)
    for address in memory:
        if not 0 <= address <= config.memory_size - 4:
            raise ValueError(f"Memory address {address} must be in 0..{config.memory_size - 4}")
    return parse_program("\n".join(assembly)), config, registers, memory
