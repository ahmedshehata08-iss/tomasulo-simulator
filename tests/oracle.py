"""A small sequential ISA interpreter used only as an independent test oracle.

This intentionally does not import the simulator's parser, instruction models,
register-renaming code, or execution helpers. It models architectural effects,
not Tomasulo timing, so an incorrect speculative implementation cannot define
its own expected answers.
"""

from dataclasses import dataclass
from ctypes import c_float
import re


@dataclass
class ArchitecturalState:
    registers: dict
    memory: dict
    instructions: int


def execute(source: str, max_steps: int = 10000) -> ArchitecturalState:
    registers = {f"R{i}": 0 for i in range(32)}
    registers.update({f"F{i}": 0.0 for i in range(32)})
    memory = {i: 0.0 for i in range(0, 256, 4)}
    code = []
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    opcodes = {"ld", "sd", "beq", "bne", "add", "addi", "sub", "add.d", "sub.d", "mult.d"}
    for raw in source.splitlines():
        line = raw.split("#", 1)[0].split("//", 1)[0].strip()
        if not line:
            continue
        first = line.split()[0].lower()
        if first in opcodes:
            code.append(re.sub(r"[,()]", " ", line).upper().split())
            continue
        for register, value in re.findall(rf"\b([RF]\d+)\s*=\s*({number})", line, re.I):
            register = register.upper()
            registers[register] = int(float(value)) if register[0] == "R" else float(value)
        for address, value in re.findall(rf"Mem\s*\[\s*(\d+)\s*\]\s*=\s*({number})", line, re.I):
            # Independently use C float conversion, not the simulator helper.
            memory[int(address)] = c_float(float(value)).value
    registers["R0"] = 0
    pc = steps = 0
    while pc < len(code):
        if pc < 0:
            raise ValueError("oracle branch target before program")
        if steps >= max_steps:
            raise RuntimeError("oracle exceeded its instruction limit")
        steps += 1
        instruction = code[pc]
        op, args = instruction[0], instruction[1:]
        next_pc = pc + 1
        if op in ("ADD", "SUB", "ADD.D", "SUB.D", "MULT.D"):
            dest, left, right = args
            a, b = registers[left], registers[right]
            value = a * b if op == "MULT.D" else a - b if op in ("SUB", "SUB.D") else a + b
            if dest != "R0":
                registers[dest] = value
        elif op == "ADDI":
            dest, src, immediate = args
            if dest != "R0":
                registers[dest] = registers[src] + int(immediate)
        elif op in ("LD", "SD"):
            floating_register, offset, base = args
            address = registers[base] + int(offset)
            if address not in memory:
                raise ValueError(f"oracle invalid memory address {address}")
            if op == "LD":
                registers[floating_register] = memory[address]
            else:
                memory[address] = c_float(registers[floating_register]).value
        elif op in ("BEQ", "BNE"):
            left, right, offset = args
            equal = registers[left] == registers[right]
            if equal == (op == "BEQ"):
                next_pc += int(offset)
        else:
            raise ValueError(f"unsupported oracle operation {op}")
        registers["R0"] = 0
        pc = next_pc
    return ArchitecturalState(registers, memory, steps)
