"""Independent architectural execution versus speculative scheduling.

Author: Ahmed Ahmed. Run directly with ``python tests/stress_equivalence.py``.
The oracle shares only parsed instruction objects, not scheduler, stage, or
memory-model helpers. It compares registers and all 256 underlying bytes.
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from simulator import Simulator
from init import Config, parse_program

def sequential(program, regs, memory):
    r = {**{f'R{i}':0 for i in range(32)}, **{f'F{i}':0.0 for i in range(32)}, **regs}
    m = bytearray(256)
    for a,v in memory.items(): struct.pack_into('<f', m, a, v)
    pc=0
    while pc < len(program):
        ins=program[pc]; op=ins.op; n=pc+1
        if op in ('Ld','Sd'):
            a=r[ins.sources[0]]+ins.immediate
            if op=='Ld': value=struct.unpack_from('<f',m,a)[0]
            else: struct.pack_into('<f',m,a,r[ins.sources[1]])
        elif ins.branch:
            yes=r[ins.sources[0]]==r[ins.sources[1]]
            if yes == (op=='Beq'): n=ins.target
        else:
            a=r[ins.sources[0]];b=ins.immediate if op=='Addi' else r[ins.sources[1]]
            value=a+b if op in ('Add','Addi','Add.d') else a-b if op in ('Sub','Sub.d') else a*b
        if ins.dest and ins.dest!='R0': r[ins.dest]=value
        pc=n
    return r,m

total=0
for seed in range(100):
    rand=random.Random(seed)
    regs={**{f'R{i}':rand.randint(0,16) for i in range(1,5)},**{f'F{i}':rand.randint(-3,3)/3 for i in range(1,6)}}
    memory={a:rand.randint(-3,3)/3 for a in range(0,64,4)}
    lines=[]
    for i in range(45):
        op=rand.choice(['Addi','Add','Sub','Add.d','Sub.d','Mult.d','Ld','Sd','Beq','Bne'])
        if op in ('Ld','Sd'):
            # R6 is always an address set by Addi; delay that producer periodically.
            if rand.random()<.25: lines.append(f'Addi R6,R0,{rand.randint(0,8)}')
            lines.append(f'{op} F{rand.randint(1,5)},{rand.randint(0,40)}(R6)')
        elif op in ('Beq','Bne'): lines.append(f'{op} R{rand.randint(0,4)},R{rand.randint(0,4)},1')
        elif op=='Addi':lines.append(f'Addi R{rand.randint(0,4)},R{rand.randint(0,4)},{rand.randint(-3,3)}')
        else:
            prefix='F' if '.d' in op else 'R'
            lines.append(f'{op} {prefix}{rand.randint(1,4)},{prefix}{rand.randint(1,4)},{prefix}{rand.randint(1,4)}')
    lines.append('Addi R0,R0,0')
    p=parse_program('\n'.join(lines)); expected,mem=sequential(p,regs,memory)
    for width in range(1,5):
        c=Config(issue_width=width,int_latency=rand.randint(1,4),fp_add_latency=rand.randint(1,5),fp_mul_latency=rand.randint(2,7),mem_latency=rand.randint(1,5),rs_int=5,lsq_size=8,addr_units=rand.randint(1,2))
        s=Simulator(p,c,regs,memory); result=s.run()
        assert s.registers==expected,(seed,width,'regs',[(k,v,expected[k])for k,v in s.registers.items()if v!=expected[k]],'\n'.join(lines))
        assert s.memory.data==mem,(seed,width,'memory')
        assert all(e.commit is None for e in s.history if e.squashed)
        total+=1
print(f'{total} independent sequential-equivalence runs passed (100 random programs x four issue widths).')
