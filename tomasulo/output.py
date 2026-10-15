"""Human-readable output required by the assignment."""


def interval(start, end):
    if start is None:
        return '-'
    return str(start) if start == end else f'{start}-{end}'


def format_result(result, show_squashed=False):
    lines = [f'TOMASULO SIMULATION | issue width {result.config.issue_width}',
             f'Total cycles: {result.cycles}', '',
             f'{"ID":>4} {"PC":>4} {"INSTRUCTION":<29} {"ISSUE":>5} {"EX":>9} '
             f'{"MEM":>9} {"WB":>5} {"COMMIT":>6} {"STATUS"}']
    for row in result.history:
        if row['squashed'] and not show_squashed:
            continue
        lines.append(f'{row["id"]:>4} {row["pc"]*4:>4} {row["instruction"]:<29} '
                     f'{row["issue"]:>5} {interval(row["ex_start"], row["ex_end"]):>9} '
                     f'{interval(row["mem_start"], row["mem_end"]):>9} '
                     f'{str(row["wb"] or "-"):>5} {str(row["commit"] or "-"):>6} '
                     f'{"squashed" if row["squashed"] else "committed"}'
                     f'{" (forwarded)" if row["forwarded"] else ""}')
    lines += ['', 'REGISTER VALUES']
    for prefix in ('R', 'F'):
        for first in range(0, 32, 8):
            lines.append('  '.join(f'{prefix}{i}={result.registers[f"{prefix}{i}"]:g}'
                                   for i in range(first, first+8)))
    lines += ['', 'NON-ZERO MEMORY VALUES', 'Address   Value']
    lines += [f'{address:>7}   {value:g}' for address, value in sorted(result.memory.items()) if value != 0]
    if not any(result.memory.values()):
        lines.append('(none)')
    lines += ['', 'STATISTICS']
    lines += [f'{key}: {value}' for key, value in result.stats.items()]
    return '\n'.join(lines) + '\n'
