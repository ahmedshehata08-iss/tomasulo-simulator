"""Command-line entry point.
Starter: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""
import argparse
import csv
import json
from pathlib import Path
from init import Config, parse_input, parse_program
from simulator import Simulator
from print_status import format_result

__author__ = "Ahmed Ahmed"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tomasulo simulator by Ahmed Ahmed")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="assignment text file with hardware, initial state, and assembly")
    source.add_argument("--program", type=Path, help="legacy assembly-only file; optionally pair with --config")
    parser.add_argument("--config", type=Path, help="legacy JSON hardware object, optionally with registers/memory")
    parser.add_argument("--issue-width", type=int)
    parser.add_argument("--writeback-width", type=int)
    parser.add_argument("--commit-width", type=int)
    parser.add_argument("--execution-order", choices=("out_of_order", "in_order"))
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--json", type=Path, dest="json_output")
    parser.add_argument("--trace-csv", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.input and args.config:
            raise ValueError("--config belongs to legacy --program mode; put hardware in --input instead")
        if args.input or not (args.program or args.config):
            input_path = args.input or Path(__file__).resolve().parent.parent / "examples" / "assignment_demo.in"
            program, initial_config, registers, memory = parse_input(input_path.read_text(encoding="utf-8"))
            values = initial_config.to_dict()
        else:
            program_path = args.program or Path(__file__).with_name("code.in")
            program = parse_program(program_path.read_text(encoding="utf-8"))
            payload = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
            if not isinstance(payload, dict):
                raise ValueError("configuration must be an object")
            registers, memory = payload.get("registers"), payload.get("memory")
            if "hardware" in payload:
                if set(payload) - {"hardware", "registers", "memory"}:
                    raise ValueError("unknown top-level configuration keys")
                values = dict(payload["hardware"])
            else:
                values = {k:v for k,v in payload.items() if k not in ("registers", "memory")}
        for name in ("issue_width", "writeback_width", "commit_width", "execution_order", "max_cycles"):
            if getattr(args, name) is not None:
                values[name] = getattr(args, name)
        config = Config.from_dict(values)
        result = Simulator(program, config, registers, memory).run()
        print(format_result(result))
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(result, indent=2) + "\n")
        if args.trace_csv:
            args.trace_csv.parent.mkdir(parents=True, exist_ok=True)
            with args.trace_csv.open("w", newline="") as stream:
                columns = ["id", "pc", "text", "issue", "exe_start", "exe_end", "mem_start", "mem_end", "wb", "commit", "forwarded_from", "status", "squashed", "squash_cycle", "squash_reason", "predicted_taken", "predicted_next_pc", "actual_taken", "mispredicted"]
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(result["instructions"])
    except (ValueError, OSError, RuntimeError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
