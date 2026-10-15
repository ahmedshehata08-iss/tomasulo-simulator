"""Parameterized, speculative Tomasulo processor simulator."""

from .model import Config, Instruction, Program, UnitConfig
from .parser import parse_file, parse_text

__all__ = ["Config", "Instruction", "Program", "UnitConfig", "parse_file", "parse_text"]
