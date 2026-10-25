"""Cycle simulator: ordered dispatch/retirement with speculative Tomasulo execution.

The cycle order is issue, EX/MEM selection, EX/MEM completion, CDB, commit.
A stage may consume another stage's result only in the following cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import Program
from .numeric import memory_word


@dataclass
class Operand:
    value: int | float = 0
    tag: int | None = None
    ready_cycle: int = 0


@dataclass
class Entry:
    tag: int
    instruction: Any
    operands: list[Operand]
    trace: dict
    kind: str
    state: str = 'waiting'
    value: int | float = 0
    ready: bool = False
    complete_cycle: int | None = None
    address: int | None = None
    fault: str | None = None
    predicted_pc: int | None = None


@dataclass
class SimulationResult:
    cycles: int
    registers: dict
    memory: dict
    history: list[dict]
    stats: dict
    config: Any

    def to_dict(self):
        from dataclasses import asdict
        return {'cycles': self.cycles, 'registers': self.registers,
                'memory': self.memory, 'history': self.history,
                'stats': self.stats, 'config': asdict(self.config)}


class Simulator:
    """One CDB and one commit slot; only the issue bandwidth is widened."""

    def __init__(self, program: Program, issue_width=None, max_cycles=100000):
        from copy import deepcopy
        self.program = program
        self.config = deepcopy(program.config)
        if issue_width is not None:
            self.config.issue_width = issue_width
        self.config.validate()
        if max_cycles < 1:
            raise ValueError('max_cycles must be positive')
        self.max_cycles = max_cycles
        self.registers = {**{f'R{i}': 0 for i in range(32)},
                          **{f'F{i}': 0.0 for i in range(32)}}
        self.registers.update(program.registers)
        self.registers['R0'] = 0
        self.memory = {i: 0.0 for i in range(0, 256, 4)}
        self.memory.update({address: memory_word(value)
                            for address, value in program.memory.items()})
        self.rat: dict[str, int] = {}
        self.rob: list[Entry] = []
        self.entries: dict[int, Entry] = {}
        self.history: list[dict] = []
        self.lsq: list[int] = []
        self.executing: list[tuple[int, int]] = []  # (inclusive end cycle, tag)
        self.memory_job: tuple[int, int, int | float | None] | None = None
        self.btb: list[dict | None] = [None] * 8
        self.pc = 0
        self.cycle = 0
        self.next_tag = 1
        self.fetch_resume = 1
        self.stats = dict(issued=0, committed=0, squashed=0,
                          branch_predictions=0, mispredictions=0,
                          memory_replays=0, forwarded_loads=0,
                          issue_stall_cycles=0)

    @staticmethod
    def kind(op):
        if op in ('ld', 'sd'):
            return 'lsu'
        if op in ('add.d', 'sub.d'):
            return 'fp_add'
        if op == 'mult.d':
            return 'fp_mul'
        return 'int_unit'

    def _source(self, register):
        if register == 'R0':
            return Operand()
        tag = self.rat.get(register)
        if tag is None:
            return Operand(self.registers[register])
        producer = self.entries[tag]
        if producer.ready:
            return Operand(producer.value, ready_cycle=producer.trace['wb'] or 0)
        return Operand(tag=tag)

    def _issue(self):
        if self.cycle < self.fetch_resume:
            return
        for _ in range(self.config.issue_width):
            if self.pc == len(self.program.instructions):
                break
            if not 0 <= self.pc < len(self.program.instructions):
                raise ValueError(f'Instruction PC {self.pc} is outside the program')
            instruction = self.program.instructions[self.pc]
            kind = self.kind(instruction.op)
            unit = getattr(self.config, kind)
            occupied = (len(self.lsq) if kind == 'lsu' else
                        sum(e.kind == kind and e.state == 'waiting' for e in self.rob))
            if len(self.rob) >= self.config.rob_entries or occupied >= unit.rs:
                self.stats['issue_stall_cycles'] += 1
                break  # Ordered issue: never skip a blocked instruction.
            operands = [self._source(r) for r in instruction.sources]
            trace = dict(id=self.next_tag, pc=self.pc,
                         instruction=instruction.text, issue=self.cycle,
                         ex_start=None, ex_end=None, mem_start=None, mem_end=None,
                         wb=None, commit=None, squashed=False, squash_cycle=None,
                         squash_reason=None, forwarded=False, replays=0,
                         predicted_pc=None, actual_pc=None)
            entry = Entry(self.next_tag, instruction, operands, trace, kind)
            self.next_tag += 1
            self.rob.append(entry)
            self.entries[entry.tag] = entry
            self.history.append(trace)
            if kind == 'lsu':
                self.lsq.append(entry.tag)
            # Sources must be captured before the destination's new mapping.
            if instruction.dest and instruction.dest != 'R0':
                self.rat[instruction.dest] = entry.tag
            next_pc = self.pc + 1
            if instruction.op in ('beq', 'bne'):
                prediction = self.btb[self.pc % 8]
                if prediction and prediction['pc'] == self.pc and prediction['taken']:
                    next_pc = prediction['target']
                entry.predicted_pc = next_pc
                trace['predicted_pc'] = next_pc
                self.stats['branch_predictions'] += 1
            self.pc = next_pc
            self.stats['issued'] += 1

    def _operands_ready(self, entry, address_only=False):
        operands = entry.operands[-1:] if address_only else entry.operands
        return all(o.tag is None and o.ready_cycle < self.cycle for o in operands)

    def _start_execution(self):
        for kind in ('int_unit', 'fp_add', 'fp_mul', 'lsu'):
            unit = getattr(self.config, kind)
            active = sum(self.entries[tag].kind == kind
                         for _, tag in self.executing if tag in self.entries)
            # FP units accept one instruction per FU per cycle. INT and the
            # address adders are non-pipelined until their inclusive EX end.
            slots = unit.count if kind in ('fp_add', 'fp_mul') else unit.count - active
            for entry in self.rob:
                if slots <= 0:
                    break
                if (entry.kind != kind or entry.state != 'waiting' or
                        entry.trace['issue'] >= self.cycle or
                        not self._operands_ready(entry, kind == 'lsu')):
                    continue
                entry.state = 'executing'
                entry.trace['ex_start'] = self.cycle
                end = self.cycle + unit.ex_cycles - 1
                entry.trace['ex_end'] = end
                self.executing.append((end, entry.tag))
                slots -= 1

    def _valid_address(self, entry):
        address = entry.address
        if not isinstance(address, int) or not 0 <= address < 256 or address % 4:
            entry.fault = f'Invalid word address {address} at PC {entry.instruction.pc * 4}'
            return False
        return True

    def _rebuild_rat(self):
        self.rat.clear()
        for entry in self.rob:
            dest = entry.instruction.dest
            if dest and dest != 'R0':
                self.rat[dest] = entry.tag

    def _squash(self, first_tag, new_pc, reason):
        removed = [e for e in self.rob if e.tag >= first_tag]
        if removed:
            for entry in removed:
                entry.trace.update(squashed=True, squash_cycle=self.cycle,
                                   squash_reason=reason)
                self.entries.pop(entry.tag)
            self.stats['squashed'] += len(removed)
            self.rob = [e for e in self.rob if e.tag < first_tag]
            self.lsq = [t for t in self.lsq if t < first_tag]
            self.executing = [(end, t) for end, t in self.executing if t < first_tag]
            if self.memory_job and self.memory_job[1] >= first_tag:
                self.memory_job = None
        self._rebuild_rat()
        self.pc = new_pc
        self.fetch_resume = self.cycle + 2

    def _check_memory_violation(self, store):
        # Loads leave the LSQ on receiving data but remain in the ROB until
        # retirement, so all speculative reads can still be checked here.
        for younger in self.rob:
            if (younger.tag > store.tag and younger.instruction.op == 'ld' and
                    younger.trace['mem_start'] is not None and
                    younger.address == store.address):
                # A known intervening same-address store supersedes this store.
                intervening = any(e.tag > store.tag and e.tag < younger.tag and
                                  e.instruction.op == 'sd' and
                                  e.address == store.address for e in self.rob)
                if intervening:
                    continue
                younger.trace['replays'] += 1
                self.stats['memory_replays'] += 1
                self._squash(younger.tag, younger.instruction.pc, 'memory replay')
                return

    def _finish_execution(self):
        finished = sorted((t for end, t in self.executing if end == self.cycle))
        for tag in finished:
            entry = self.entries.get(tag)
            if entry is None:
                continue
            ins = entry.instruction
            values = [o.value for o in entry.operands]
            if ins.op in ('ld', 'sd'):
                entry.address = values[-1] + ins.immediate
                entry.state = 'address_ready'
                if not self._valid_address(entry):
                    if ins.op == 'ld':
                        entry.value = 0.0
                        entry.complete_cycle = self.cycle
                        entry.state = 'result'
                        self.lsq.remove(tag)
                elif ins.op == 'sd':
                    self._check_memory_violation(entry)
            elif ins.op in ('beq', 'bne'):
                taken = ((values[0] == values[1]) if ins.op == 'beq'
                         else (values[0] != values[1]))
                target = ins.pc + 1 + ins.immediate
                actual = target if taken else ins.pc + 1
                entry.trace['actual_pc'] = actual
                entry.ready = True
                entry.state = 'resolved'
                # Invalid speculative branch targets become precise faults.
                if not 0 <= actual <= len(self.program.instructions):
                    entry.fault = f'Branch target {actual} is outside the program'
                    actual = len(self.program.instructions)
                # A fault on a wrong path must not poison future speculative
                # fetches with an invalid target. The live fault is still
                # raised precisely when this branch reaches the ROB head.
                self.btb[ins.pc % 8] = (dict(pc=ins.pc, taken=taken, target=target)
                                       if 0 <= target <= len(self.program.instructions)
                                       else None)
                if actual != entry.predicted_pc:
                    self.stats['mispredictions'] += 1
                    self._squash(tag + 1, actual, 'branch misprediction')
            else:
                if ins.op in ('add', 'add.d'):
                    entry.value = values[0] + values[1]
                elif ins.op == 'addi':
                    entry.value = values[0] + ins.immediate
                elif ins.op in ('sub', 'sub.d'):
                    entry.value = values[0] - values[1]
                elif ins.op == 'mult.d':
                    entry.value = values[0] * values[1]
                else:
                    raise ValueError(f'Unsupported operation {ins.op}')
                entry.complete_cycle = self.cycle
                entry.state = 'result'
        self.executing = [(end, tag) for end, tag in self.executing if end > self.cycle]

    def _load_value(self, load):
        for tag in reversed(self.lsq):
            older = self.entries[tag]
            if tag < load.tag and older.instruction.op == 'sd' and older.address == load.address:
                if not self._operands_ready(older):
                    return False, None, False
                return True, older.operands[0].value, True
        return True, None, False

    def _start_memory(self):
        if self.memory_job is not None:
            return
        if self.rob:
            head = self.rob[0]
            if (head.instruction.op == 'sd' and head.state == 'address_ready' and
                    head.trace['ex_end'] < self.cycle and self._operands_ready(head)):
                if head.fault:
                    raise ValueError(head.fault)
                head.trace['mem_start'] = self.cycle
                head.trace['mem_end'] = self.cycle + self.config.mem_cycles - 1
                head.state = 'store_memory'
                self.memory_job = (head.trace['mem_end'], head.tag, head.operands[0].value)
                return
        for tag in self.lsq:
            load = self.entries[tag]
            if (load.instruction.op != 'ld' or load.state != 'address_ready' or
                    load.trace['ex_end'] >= self.cycle):
                continue
            allowed, value, forwarded = self._load_value(load)
            if not allowed:
                continue
            latency = 1 if forwarded else self.config.mem_cycles
            load.trace.update(mem_start=self.cycle, mem_end=self.cycle + latency - 1,
                              forwarded=forwarded)
            load.state = 'load_memory'
            self.memory_job = (load.trace['mem_end'], tag, value)
            return

    def _finish_memory(self):
        if not self.memory_job or self.memory_job[0] != self.cycle:
            return
        _, tag, value = self.memory_job
        entry = self.entries[tag]
        if entry.instruction.op == 'ld':
            # Forwarding and a committed-memory read must round identically.
            entry.value = memory_word(self.memory[entry.address] if value is None else value)
            entry.complete_cycle = self.cycle
            entry.state = 'result'
            self.stats['forwarded_loads'] += int(entry.trace['forwarded'])
            self.lsq.remove(tag)
            self.memory_job = None
        # Store memory writes occur strictly in _commit, never speculatively.

    def _writeback(self):
        candidates = [e for e in self.rob if e.state == 'result' and
                      e.complete_cycle < self.cycle]
        if not candidates:
            return
        entry = min(candidates, key=lambda e: (e.complete_cycle, e.tag))
        entry.trace['wb'] = self.cycle
        entry.ready = True
        entry.state = 'written'
        for dependent in self.rob:
            for operand in dependent.operands:
                if operand.tag == entry.tag:
                    operand.tag = None
                    operand.value = entry.value
                    operand.ready_cycle = self.cycle
        # RAT holds producer tags until matching commit. Never replace a
        # younger writer's mapping when an older writer broadcasts.

    def _commit(self):
        if not self.rob:
            return
        entry = self.rob[0]
        op = entry.instruction.op
        if op == 'sd':
            if not (entry.state == 'store_memory' and entry.trace['mem_end'] == self.cycle):
                return
            if entry.fault:
                raise ValueError(entry.fault)
            self.memory[entry.address] = memory_word(self.memory_job[2])
            self.memory_job = None
            self.lsq.remove(entry.tag)
        elif op in ('beq', 'bne'):
            if not entry.ready or entry.trace['ex_end'] >= self.cycle:
                return
        elif not entry.ready or entry.trace['wb'] >= self.cycle:
            return
        if entry.fault:
            raise ValueError(entry.fault)
        dest = entry.instruction.dest
        if dest and dest != 'R0':
            self.registers[dest] = entry.value
            if self.rat.get(dest) == entry.tag:
                del self.rat[dest]
        entry.trace['commit'] = self.cycle
        self.rob.pop(0)
        del self.entries[entry.tag]
        self.stats['committed'] += 1
        self.registers['R0'] = 0

    def run(self):
        while self.pc != len(self.program.instructions) or self.rob:
            self.cycle += 1
            if self.cycle > self.max_cycles:
                raise RuntimeError(f'Simulation exceeded {self.max_cycles} cycles; '
                                   'check for an infinite loop or increase --max-cycles')
            self._issue()
            self._start_execution()
            self._start_memory()
            self._finish_execution()
            self._finish_memory()
            self._writeback()
            self._commit()
        return SimulationResult(self.cycle, dict(self.registers), dict(self.memory),
                                self.history, self.stats, self.config)
