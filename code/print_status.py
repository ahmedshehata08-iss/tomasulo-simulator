"""Readable timing output. Assignment modifications: Ahmed Ahmed."""


def interval(start, end):
    return "-" if start is None else f"{start}-{end if end is not None else '?'}"


def optional(value):
    return "-" if value is None else str(value)


def format_result(result):
    lines = ["Tomasulo Simulator | Ahmed Ahmed",
             f"Issue width {result['config']['issue_width']}; WB width {result['config']['writeback_width']}; "
             f"commit width {result['config']['commit_width']}",
             f"{'ID':>3} {'Instruction':<25} {'ISSUE':>5} {'EX':>9} {'MEM':>9} {'WB':>5} {'COMMIT':>6} {'STATUS':<10}"]
    for row in result["instructions"]:
        lines.append(f"{row['id']:>3} {row['text']:<25} {row['issue']:>5} "
                     f"{interval(row['exe_start'],row['exe_end']):>9} "
                     f"{interval(row['mem_start'],row['mem_end']):>9} "
                     f"{optional(row['wb']):>5} {optional(row['commit']):>6} {row.get('status', 'committed'):<10}")
        if row.get("squash_cycle") is not None:
            lines.append(f"    Squashed in cycle {row['squash_cycle']}: {row.get('squash_reason', 'recovery')}")
    lines.append(f"Cycles: {result['cycles']} | Instructions: {result['instruction_count']} | IPC: {result['ipc']:.4f}")
    if "branch_mispredictions" in result:
        lines.append(f"Branch mispredictions: {len(result['branch_mispredictions'])}; memory replays: {len(result.get('memory_replays', []))}")
    for prefix in ("R", "F"):
        lines.append("Registers " + prefix + ": " + ", ".join(
            f"{prefix}{i}={result['registers'][f'{prefix}{i}']:g}" for i in range(32)))
    nonzero = ", ".join(f"[{k}]={v:g}" for k,v in result["memory"].items() if v != 0)
    lines.append("Nonzero memory: " + (nonzero or "(none)"))
    return "\n".join(lines)
