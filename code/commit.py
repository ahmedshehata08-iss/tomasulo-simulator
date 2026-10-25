"""In-order retirement with precise register and memory updates.
Starter: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""


def commit(sim):
    for _ in range(sim.config.commit_width):
        if not sim.rob:
            break
        entry = sim.rob[0]
        if entry.ready_cycle is None or entry.ready_cycle >= sim.cycle:
            break
        if getattr(entry, "fault", None):
            raise ValueError(f"Instruction {entry.id}: {entry.fault}")
        ins = entry.instruction
        if ins.op == "Sd":
            sim.memory[entry.address] = entry.result
            sim.lsq.remove(entry)
        elif ins.dest is not None and ins.dest != "R0":
            value = int(entry.result) if ins.dest.startswith("R") else float(entry.result)
            sim.registers[ins.dest] = value
            if sim.rat.get(ins.dest) == entry.id:
                del sim.rat[ins.dest]
        entry.commit = sim.cycle
        entry.status = "committed"
        sim.rob.popleft()
        sim.registers["R0"] = 0
