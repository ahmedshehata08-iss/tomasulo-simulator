"""Repository entry point for the Tomasulo simulator. Author: Ahmed Ahmed."""
from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    code_directory = Path(__file__).resolve().parent / "code"
    sys.path.insert(0, str(code_directory))
    runpy.run_path(str(code_directory / "main.py"), run_name="__main__")
