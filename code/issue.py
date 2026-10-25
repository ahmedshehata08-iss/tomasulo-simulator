"""In-order multi-issue and register renaming.
Starter: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""
from init import Entry, Operand


def issue(sim):
    """Issue a contiguous program-order prefix; a full resource stops the bundle."""
    if sim.cycle < sim.fetch_resume_cycle:
        return
    for _ in range(sim.config.issue_width):
        if sim.branch_pending is not None or sim.pc >= len(sim.program):
            break
        ins = sim.program[sim.pc]
        if len(sim.rob) >= sim.config.rob_size:
            break
        if ins.kind == "memory":
            if len(sim.lsq) >= sim.config.lsq_size:
                break
        elif len(sim.stations[ins.kind]) >= sim.rs_capacity[ins.kind]:
            break
        # Read every source BEFORE installing the destination rename.
        operands = [sim.read_operand(name) for name in ins.sources]
        if ins.op == "Addi":
            operands.append(Operand(ins.immediate))
        entry = Entry(sim.next_id, sim.pc, ins, operands, sim.cycle)
        entry.status = "issued"
        sim.next_id += 1
        sim.rob.append(entry)
        sim.history.append(entry)
        sim.by_id[entry.id] = entry
        if ins.kind == "memory":
            sim.lsq.append(entry)
        else:
            sim.stations[ins.kind].append(entry)
        if ins.dest is not None and ins.dest != "R0":
            sim.rat[ins.dest] = entry.id
        sim.pc += 1
        if ins.branch:
            if sim.config.branch_prediction:
                sim.pc = sim.predict_branch(entry)
            else:
                # Optional comparison mode; assignment mode predicts the next PC.
                sim.branch_pending = entry.id
                break
