"""Bounded CDB broadcast; rename ownership is preserved.
Starter: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""


def wb(sim):
    eligible = sorted((e for e in sim.results if e.complete_cycle < sim.cycle),
                      key=lambda e: (e.complete_cycle, e.id))
    for entry in eligible[:sim.config.writeback_width]:
        entry.wb = sim.cycle
        entry.status = "writeback"
        entry.ready_cycle = sim.cycle
        sim.results.remove(entry)
        # Broadcast to waiting consumers by producer tag, not by register name.
        for consumer in sim.rob:
            for operand in consumer.operands:
                if operand.tag == entry.id:
                    operand.value = entry.result
                    operand.tag = None
                    operand.ready_cycle = sim.cycle
        # RAT remains a mapping to the youngest writer until that writer commits.
        # Architectural register writes belong to commit, not this stage.
