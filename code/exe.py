"""Oldest-ready execution with fixed completion cycles.
Starter: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""


def _start(sim, entry):
    ins = entry.instruction
    entry.status = "executing"
    entry.exe_start = sim.cycle
    latency = sim.latencies[ins.kind]
    entry.exe_end = sim.cycle + latency - 1
    if ins.kind == "memory":
        # A store can compute its address while its data is still pending.
        entry.address = int(entry.operands[0].value) + ins.immediate
        if not 0 <= entry.address <= sim.config.memory_size - 4:
            # Wrong-path faults are discarded with their instruction, not raised early.
            entry.fault = f"memory address {entry.address} is outside 0..{sim.config.memory_size - 4}"
        sim.address_active.append(entry)
    else:
        left, right = (operand.value for operand in entry.operands)
        if ins.op in ("Add", "Addi", "Add.d"):
            entry.result = left + right
        elif ins.op in ("Sub", "Sub.d"):
            entry.result = left - right
        elif ins.op == "Mult.d":
            entry.result = left * right
        elif ins.op == "Bne":
            entry.result = int(left != right)
        elif ins.op == "Beq":
            entry.result = int(left == right)
        sim.stations[ins.kind].remove(entry)
        sim.arithmetic_active.append(entry)


def exe(sim):
    # FP pipelines accept one operation per unit per cycle; integer units do not.
    slots = dict(sim.unit_counts)
    slots["int"] -= sum(entry.instruction.kind == "int" for entry in sim.arithmetic_active)
    slots["memory"] = sim.config.addr_units - len(sim.address_active)
    waiting = sorted(
        [e for station in sim.stations.values() for e in station]
        + [e for e in sim.lsq if e.exe_start is None], key=lambda e: e.id
    )
    for entry in waiting:
        ins = entry.instruction
        needed = entry.operands[:1] if ins.kind == "memory" else entry.operands
        ready = entry.issue < sim.cycle and all(x.ready(sim.cycle) for x in needed)
        if not ready or slots[ins.kind] <= 0:
            if sim.config.execution_order == "in_order":
                break
            continue
        _start(sim, entry)
        slots[ins.kind] -= 1
    # Resolve oldest completions first, so recovery can cancel a younger event.
    completed = sorted((entry for entry in sim.arithmetic_active + sim.address_active
                        if entry.exe_end == sim.cycle), key=lambda entry: entry.id)
    for entry in completed:
        if entry.squashed:
            continue
        if entry.instruction.kind == "memory":
            sim.address_active.remove(entry)
            entry.status = "address_ready"
            if getattr(entry, "fault", None):
                entry.ready_cycle = sim.cycle
            elif entry.instruction.op == "Sd":
                sim.validate_store_address(entry)
        else:
            sim.arithmetic_active.remove(entry)
            entry.complete_cycle = sim.cycle
            entry.status = "executed"
            if entry.instruction.branch:
                entry.ready_cycle = sim.cycle
                sim.resolve_branch(entry)
            else:
                sim.results.append(entry)
