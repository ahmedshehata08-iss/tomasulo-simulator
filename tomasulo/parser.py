"""Case-insensitive parser for the assignment's configuration and assembly."""

import math
import re
from pathlib import Path

from .model import Config, Instruction, Program, UnitConfig
from .numeric import memory_word


_INTEGER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")
_REGISTER = re.compile(r"([RF])(\d+)\Z", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"(?P<target>[RF]\d+|Mem\s*\[\s*[+-]?(?:0[xX][0-9a-fA-F]+|\d+)\s*\])"
    r"\s*=\s*(?P<value>[^,\s]+)",
    re.IGNORECASE,
)
_UNIT_ROWS = (
    (re.compile(r"integer\s+adder\b", re.IGNORECASE), "int_unit", 3),
    (re.compile(r"fp\s+adder\b", re.IGNORECASE), "fp_add", 3),
    (re.compile(r"fp\s+multiplier\b", re.IGNORECASE), "fp_mul", 3),
    (re.compile(r"load\s*/\s*store\s+unit\b", re.IGNORECASE), "lsu", 4),
)


def _integer(token: str, description: str) -> int:
    if not _INTEGER.fullmatch(token):
        raise ValueError(f"{description} must be an integer: {token!r}")
    unsigned = token.lstrip("+-")
    return int(token, 16 if unsigned.lower().startswith("0x") else 10)


def _number(token: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise ValueError(f"invalid numeric value: {token!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"numeric values must be finite: {token!r}")
    return value


def _register(token: str, kind: str) -> str:
    match = _REGISTER.fullmatch(token)
    if match is None or match.group(1).upper() != kind:
        raise ValueError(f"expected {kind}0..{kind}31 register, got {token!r}")
    index = int(match.group(2))
    if not 0 <= index <= 31:
        raise ValueError(f"register index outside 0..31: {token!r}")
    return f"{kind}{index}"


def _instruction(line: str, pc: int) -> Instruction:
    # Whitespace and commas are interchangeable operand separators.  Spaces
    # inside the offset(base) addressing form are permitted as well.
    pieces = line.split(maxsplit=1)
    op = pieces[0].lower()
    operands = pieces[1].strip() if len(pieces) == 2 else ""
    if operands.startswith(",") or operands.endswith(",") or re.search(r",\s*,", operands):
        raise ValueError("misplaced comma in operand list")
    if op in {"ld", "sd"}:
        match = re.fullmatch(
            r"([Ff]\d+)[\s,]+([+-]?(?:0[xX][0-9a-fA-F]+|\d+))"
            r"\s*\(\s*([Rr]\d+)\s*\)",
            operands,
        )
        if match is None:
            raise ValueError(f"expected {op} Fa, offset(Ra)")
        data = _register(match.group(1), "F")
        offset = _integer(match.group(2), "memory offset")
        base = _register(match.group(3), "R")
        if op == "ld":
            return Instruction(pc, op, data, (base,), offset, line)
        return Instruction(pc, op, None, (data, base), offset, line)

    if op not in {"add", "sub", "addi", "add.d", "sub.d", "mult.d", "beq", "bne"}:
        raise ValueError(f"unknown instruction: {pieces[0]!r}")
    args = re.split(r"[\s,]+", operands) if operands else []
    if len(args) != 3 or any(not item for item in args):
        raise ValueError(f"{op} requires exactly three operands")
    if op in {"beq", "bne"}:
        return Instruction(
            pc, op, None,
            (_register(args[0], "R"), _register(args[1], "R")),
            _integer(args[2], "branch offset"), line,
        )
    if op == "addi":
        return Instruction(
            pc, op, _register(args[0], "R"), (_register(args[1], "R"),),
            _integer(args[2], "immediate"), line,
        )
    kind = "F" if op.endswith(".d") else "R"
    return Instruction(
        pc, op, _register(args[0], kind),
        (_register(args[1], kind), _register(args[2], kind)), text=line,
    )


def parse_text(text: str) -> Program:
    """Parse an assignment input file and report errors with source line numbers.

    Configuration and initialization are optional.  Unspecified registers and
    memory words are deliberately omitted; the execution engine supplies zero.
    """

    config = Config()
    registers: dict[str, int | float] = {}
    memory: dict[int, float] = {}
    instructions: list[Instruction] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        # A table header can contain a literal '# of rs' after its first column;
        # recognize it before stripping '#' comments from normal input lines.
        raw_header = re.sub(r"[^a-z]+", " ", raw.lower()).strip()
        if "cycles in ex" in raw_header and ("of rs" in raw_header or "reservation stations" in raw_header):
            continue
        line = re.split(r"#|//|;", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        try:
            setting = re.fullmatch(
                r"(rob\s+entries|issue\s+width)\s*=\s*(\S+)", line, re.IGNORECASE
            )
            if setting:
                name = re.sub(r"\s+", " ", setting.group(1).lower())
                value = _integer(setting.group(2), name)
                setattr(config, "rob_entries" if name == "rob entries" else "issue_width", value)
                config.validate()
                continue

            unit_row = False
            for pattern, attribute, count in _UNIT_ROWS:
                match = pattern.match(line)
                if match is None:
                    continue
                fields = re.split(r"[\s,|]+", line[match.end():].strip(" \t:|="))
                if len(fields) != count or not all(fields):
                    raise ValueError(f"{match.group()} requires {count} integer configuration values")
                values = [_integer(token, "unit configuration value") for token in fields]
                if attribute == "lsu":
                    config.lsu = UnitConfig(values[0], values[1], values[3])
                    config.mem_cycles = values[2]
                else:
                    setattr(config, attribute, UnitConfig(*values))
                config.validate()
                unit_row = True
                break
            if unit_row:
                continue

            # Some tables spell their header without an initial comment marker.
            header = re.sub(r"[^a-z]+", " ", line.lower()).strip()
            if "cycles in ex" in header and ("of rs" in header or "reservation stations" in header):
                continue

            if re.match(r"(?:[RF]\d+|Mem\s*\[)", line, re.IGNORECASE):
                position = 0
                found = False
                while position < len(line):
                    match = _ASSIGNMENT.match(line, position)
                    if match is None:
                        raise ValueError(f"malformed initialization near {line[position:]!r}")
                    found = True
                    target, token = match.group("target", "value")
                    if target.lower().startswith("mem"):
                        address_text = re.search(r"\[(.*?)\]", target).group(1).strip()
                        address = _integer(address_text, "memory address")
                        if address % 4 or not 0 <= address <= 252:
                            raise ValueError("memory addresses must be aligned byte addresses from 0 to 252")
                        memory[address] = memory_word(_number(token))
                    else:
                        kind = target[0].upper()
                        register = _register(target, kind)
                        value = _integer(token, "integer register value") if kind == "R" else _number(token)
                        registers[register] = 0 if register == "R0" else value
                    position = match.end()
                    if position < len(line):
                        separator = re.match(r"(?:\s*,\s*|\s+)", line[position:])
                        if separator is None:
                            raise ValueError(f"expected a comma or whitespace near {line[position:]!r}")
                        position += separator.end()
                        if position == len(line):
                            raise ValueError("initialization line cannot end with a comma")
                if found:
                    continue

            instructions.append(_instruction(line, len(instructions)))
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"line {line_number}: {exc} [{raw.strip()}]") from exc

    config.validate()
    return Program(config, instructions, registers, memory)


def parse_file(path: str | Path) -> Program:
    """Read a UTF-8 assembly input file, accepting an optional UTF-8 BOM."""

    return parse_text(Path(path).read_text(encoding="utf-8-sig"))
