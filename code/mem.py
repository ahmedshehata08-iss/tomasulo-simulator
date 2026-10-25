"""Ordered store effects, store forwarding and speculative load disambiguation.
Starter: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""


def load_source(sim, load):
    """Return (eligible, value, forwarding id), checking all older stores."""
    older = [e for e in sim.lsq if e.id < load.id and e.instruction.op == "Sd"]
    unknown = [e for e in older if e.exe_end is None or e.exe_end >= sim.cycle]
    known = [e for e in older if e.exe_end is not None and e.exe_end < sim.cycle]
    matches = [e for e in known if abs(e.address - load.address) < 4]
    if matches:
        source = matches[-1]  # The youngest overlapping store determines the source.
        # A partial overlap needs bytes from memory and stores: wait for commit.
        # An exact match overwrites all four bytes and can forward directly.
        if source.address != load.address:
            return False, None, None
        if not source.operands[1].ready(sim.cycle):
            return False, None, None
        load.unresolved_store_ids = [e.id for e in unknown]
        return True, sim.memory.normalize(source.operands[1].value), source.id
    # The handout permits access past unresolved stores; EX later checks aliases.
    load.unresolved_store_ids = [e.id for e in unknown]
    return True, sim.memory.get(load.address, 0.0), None


def mem(sim):
    # Units completing this cycle remain occupied for this cycle.
    free = sim.config.mem_units - len(sim.memory_active)
    for entry in sim.lsq:
        if free == 0:
            break
        if entry.mem_start is not None or entry.exe_end is None or entry.exe_end >= sim.cycle:
            continue
        if getattr(entry, "fault", None):
            continue
        if entry.instruction.op == "Sd":
            # A store reaches memory only at the ROB head. Effects occur at commit.
            if sim.rob[0] is not entry or not entry.operands[1].ready(sim.cycle):
                continue
            value, source = entry.operands[1].value, None
        else:
            eligible, value, source = load_source(sim, entry)
            if not eligible:
                continue
        entry.mem_start = sim.cycle
        entry.status = "memory"
        # Forwarding uses a memory port for one cycle, independent of RAM latency.
        latency = 1 if source is not None else sim.config.mem_latency
        entry.mem_end = sim.cycle + latency - 1
        entry.result = value
        entry.forwarded_from = source
        sim.memory_active.append(entry)
        free -= 1
    for entry in list(sim.memory_active):
        if entry.mem_end != sim.cycle:
            continue
        sim.memory_active.remove(entry)
        entry.complete_cycle = sim.cycle
        entry.status = "memory_complete"
        if entry.instruction.op == "Sd":
            entry.ready_cycle = sim.cycle
        else:
            sim.results.append(entry)
            sim.lsq.remove(entry)
