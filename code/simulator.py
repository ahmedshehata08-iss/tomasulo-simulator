"""Cycle-accurate educational Tomasulo simulator.
Starter design: Qing (May 2017). Assignment modifications: Ahmed Ahmed.
"""
from collections import deque
from init import Config, Instruction, Operand, numeric, parse_program, register_name
from issue import issue
from exe import exe
from mem import mem
from wb import wb
from commit import commit
from memory_model import FloatMemory

__author__ = "Ahmed Ahmed"


class Simulator:
    def __init__(self, program, config=None, registers=None, memory=None):
        self.config = config if config is not None else Config()
        if isinstance(self.config, dict):
            self.config = Config.from_dict(self.config)
        if not isinstance(self.config, Config):
            raise ValueError("config must be a Config instance or dictionary")
        self.program = parse_program(program) if isinstance(program, str) else list(program)
        if any(not isinstance(ins, Instruction) for ins in self.program):
            raise ValueError("program must contain parsed Instruction objects")
        self.registers = {f"R{i}": 0 for i in range(32)}
        self.registers.update({f"F{i}": 0.0 for i in range(32)})
        initial_regs = {} if registers is None else registers
        if not isinstance(initial_regs, dict):
            raise ValueError("registers must be a dictionary")
        for raw_name, value in initial_regs.items():
            name = register_name(raw_name)
            numeric(value, name)
            if name.startswith("R") and int(value) != value:
                raise ValueError(f"{name} requires an integer value")
            self.registers[name] = int(value) if name.startswith("R") else float(value)
        self.registers["R0"] = 0
        self.memory = FloatMemory(self.config.memory_size)
        initial_memory = {} if memory is None else memory
        if not isinstance(initial_memory, dict):
            raise ValueError("memory must be a dictionary")
        for raw_address, value in initial_memory.items():
            if isinstance(raw_address, str):
                try:
                    address = int(raw_address)
                except ValueError as exc:
                    raise ValueError("memory addresses must be nonnegative integers") from exc
            else:
                address = raw_address
            if type(address) is not int or not 0 <= address <= self.config.memory_size - 4:
                raise ValueError(f"memory addresses must be integers from 0 to {self.config.memory_size - 4}")
            self.memory[address] = numeric(value, f"memory[{address}]")
        self.rat = {}
        self.rob = deque()
        self.lsq = []
        self.stations = {"int": [], "fp_add": [], "fp_mul": []}
        self.arithmetic_active, self.address_active, self.memory_active = [], [], []
        self.results, self.history = [], []
        self.by_id = {}
        self.pc, self.next_id, self.cycle = 0, 1, 0
        self.branch_pending = None
        # PCs are word indices; the direct-mapped BTB keeps tags to reject aliases.
        self.predictor = {}
        self.btb = [None] * 8
        self.fetch_resume_cycle = 0
        self.branch_predictions, self.branch_mispredictions, self.memory_replays = [], [], []
        self.rs_capacity = {"int": self.config.rs_int, "fp_add": self.config.rs_fp_add,
                            "fp_mul": self.config.rs_fp_mul}
        self.unit_counts = {"int": self.config.int_units, "fp_add": self.config.fp_add_units,
                            "fp_mul": self.config.fp_mul_units}
        self.latencies = {"int": self.config.int_latency, "fp_add": self.config.fp_add_latency,
                          "fp_mul": self.config.fp_mul_latency, "memory": self.config.addr_latency}

    def read_operand(self, name):
        if name == "R0":
            return Operand(0)
        tag = self.rat.get(name)
        if tag is None:
            return Operand(self.registers[name])
        producer = self.by_id[tag]
        if producer.wb is not None:
            return Operand(producer.result, ready_cycle=producer.wb)
        return Operand(tag=tag)

    def predict_branch(self, entry):
        """Predict during ISSUE; a taken prediction requires a tagged BTB hit."""
        index = entry.pc & 7
        target = self.btb[index]
        hit = target is not None and target["pc"] == entry.pc
        taken = bool(self.predictor.get(entry.pc, False) and hit)
        entry.predicted_taken = taken
        entry.predicted_next_pc = target["target"] if taken else entry.pc + 1
        self.branch_predictions.append({
            "id": entry.id, "pc": entry.pc, "cycle": self.cycle,
            "btb_index": index, "btb_hit": hit, "predicted_taken": taken,
            "predicted_next_pc": entry.predicted_next_pc,
        })
        return entry.predicted_next_pc

    def squash(self, first_id, target_pc, reason):
        """Discard a dynamic suffix and reconstruct renaming from surviving ROB entries."""
        discarded = [entry for entry in self.rob if entry.id >= first_id]
        ids = {entry.id for entry in discarded}
        executing = {entry.id for entry in self.arithmetic_active + self.address_active}
        accessing = {entry.id for entry in self.memory_active}
        for entry in discarded:
            entry.status, entry.squashed = "squashed", True
            entry.squash_cycle, entry.squash_reason = self.cycle, reason
            # An interrupted operation has a start, but no completed end stage.
            if entry.id in executing:
                entry.exe_end = None
            if entry.id in accessing:
                entry.mem_end = None
            for operand in entry.operands:
                operand.tag = None
        self.rob = deque(entry for entry in self.rob if entry.id not in ids)
        self.lsq[:] = [entry for entry in self.lsq if entry.id not in ids]
        for station in self.stations.values():
            station[:] = [entry for entry in station if entry.id not in ids]
        for active in (self.arithmetic_active, self.address_active, self.memory_active, self.results):
            active[:] = [entry for entry in active if entry.id not in ids]
        self.rat.clear()
        for entry in self.rob:
            destination = entry.instruction.dest
            if destination is not None and destination != "R0":
                self.rat[destination] = entry.id
        self.branch_pending = None
        self.pc = target_pc
        # The cycle after detection performs recovery; fetch follows it.
        self.fetch_resume_cycle = self.cycle + 2
        return sorted(ids)

    def resolve_branch(self, entry):
        taken = bool(entry.result)
        actual_next = entry.instruction.target if taken else entry.pc + 1
        entry.actual_taken = taken
        self.predictor[entry.pc] = taken
        self.btb[entry.pc & 7] = {"pc": entry.pc, "target": entry.instruction.target}
        if not self.config.branch_prediction:
            self.pc, self.branch_pending = actual_next, None
            entry.mispredicted = False
            return
        entry.mispredicted = actual_next != entry.predicted_next_pc
        if entry.mispredicted:
            discarded = self.squash(entry.id + 1, actual_next,
                                     f"branch misprediction at instruction {entry.id}")
            self.branch_mispredictions.append({
                "id": entry.id, "pc": entry.pc, "cycle": self.cycle,
                "predicted_next_pc": entry.predicted_next_pc,
                "actual_next_pc": actual_next, "target": actual_next,
                "resume_cycle": self.fetch_resume_cycle, "squashed_ids": discarded,
            })

    def validate_store_address(self, store):
        """Replay a load if an older, previously unknown store now aliases it."""
        violating = []
        for load in self.rob:
            unresolved = getattr(load, "unresolved_store_ids", [])
            if store.id not in unresolved:
                continue
            unresolved.remove(store.id)
            # A younger matching forwarding source shadows this store.
            if (abs(load.address - store.address) < 4
                    and (load.forwarded_from is None or store.id > load.forwarded_from)):
                violating.append(load)
        if violating:
            load = min(violating, key=lambda entry: entry.id)
            discarded = self.squash(load.id, load.pc,
                                     f"memory-order replay after store {store.id}")
            self.memory_replays.append({
                "store_id": store.id, "load_id": load.id, "pc": load.pc,
                "cycle": self.cycle, "target": load.pc,
                "resume_cycle": self.fetch_resume_cycle, "squashed_ids": discarded,
            })

    def step(self):
        if self.cycle >= self.config.max_cycles:
            raise RuntimeError(f"Simulation exceeded {self.config.max_cycles} cycles; check loop termination")
        self.cycle += 1
        # Reverse stage order and explicit readiness dates prevent zero-cycle edges.
        commit(self)
        wb(self)
        mem(self)
        exe(self)
        issue(self)

    def run(self):
        while self.pc < len(self.program) or self.rob:
            self.step()
        committed = sum(entry.commit is not None for entry in self.history)
        squashed = sum(entry.squashed for entry in self.history)
        return {
            "author": __author__, "cycles": self.cycle,
            "instruction_count": committed, "committed_instruction_count": committed,
            "issued_instruction_count": len(self.history), "squashed_instruction_count": squashed,
            "ipc": committed / self.cycle if self.cycle else 0.0,
            "config": self.config.to_dict(),
            "instructions": [entry.trace() for entry in self.history],
            "registers": dict(self.registers), "memory": dict(sorted(self.memory.items())),
            "branch_predictions": list(self.branch_predictions),
            "branch_mispredictions": list(self.branch_mispredictions),
            "memory_replays": list(self.memory_replays),
            "branch_prediction_count": len(self.branch_predictions),
            "branch_misprediction_count": len(self.branch_mispredictions),
            "memory_replay_count": len(self.memory_replays),
        }
