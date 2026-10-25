#!/usr/bin/env python3
"""Reproduce assignment-compliant issue-width experiments. Author: Ahmed Ahmed.

The main profile always has one CDB, one commit per cycle, and one memory port.
All inputs combine hardware, initial registers/memory, and assembly in one file.
An independent sequential byte-memory interpreter checks architectural results.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from dataclasses import replace
import json
import math
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'code'))
from init import parse_input
from simulator import Simulator

AUTHOR = 'Ahmed Ahmed'
CASES = {
    'sample_case1': {
        'title': 'Sample case 1 under assignment hardware rules',
        'instructions': 17,
        'explanation': 'The program and arithmetic hardware match sample case 1. One CDB and one commit per cycle are retained at every issue width. Loads read 12, 20, and 28: 1, 0, and 5. Final F4=1^2+0^2+5^2=26; R1=28.'},
    'sample_case2': {
        'title': 'Sample case 2 adapted to one memory port',
        'instructions': 17,
        'explanation': 'The sample program, two FP adders, two FP multipliers, and two address units are retained. The assignment requires one memory port, so the sample report\'s two memory units are reduced to one. Final F4=1^2+6^2=37; R1=24. One CDB is retained.'},
    'starter_branch': {
        'title': 'Starter branch control flow with word offset',
        'instructions': 15,
        'explanation': 'The starter\'s -12-byte displacement is converted to -3 words. At PC7 the target is PC5, so only pointer updates and the branch repeat. Mem[32]=7, F20=2, R1=0, R2=20.'},
    'full_loop': {
        'title': 'Additional complete three-iteration loop',
        'instructions': 25,
        'explanation': 'The separately named full loop uses -8 words from PC7 to PC0. Final stores are Mem[32]=7, Mem[28]=11, Mem[24]=15; F20=6, R1=0, R2=20.'},
    'independent_throughput': {
        'title': 'Additional independent integer throughput test',
        'instructions': 24,
        'explanation': 'Twenty-four independent Addi instructions write R3 through R26 with 1 through 24. Four unpipelined one-cycle integer units allow concurrent execution, but one CDB and one commit slot limit overall throughput.'},
    'assignment_demo': {
        'title': 'Terminating variation of the handout example',
        'instructions': 3,
        'explanation': 'Only the handout\'s initial R3 is changed from its default zero to 20, equal to R2. The branch is not taken. F1=30.1, F4=0 because the unaligned load reads four zero bytes beginning at byte address 18.'},
    'branch_recovery': {
        'title': 'Additional one-bit branch recovery test',
        'instructions': 13,
        'explanation': 'The same branch produces T, T, N. The one-bit predictor learns taken and must recover on the first taken and final not-taken outcomes. Wrong-path instructions cannot alter architectural state. Final R1=0, R2=12, R3=99, Mem[12]=7.'},
    'memory_replay': {
        'title': 'Additional unknown-store-address replay test',
        'instructions': 5,
        'explanation': 'A younger load reads address 16 before an older store resolves its address. Discovering the alias triggers replay. The corrected load gets 7, not the stale 1; final F2=7, F4=14, R3=99, Mem[16]=7.'},
    'integer_unpipelined': {
        'title': 'Additional three-cycle integer-unit test',
        'instructions': 3,
        'explanation': 'Three independent Addi operations share one three-cycle unpipelined integer unit. Their EX intervals must not overlap; final R3=1,R4=2,R5=3.'},
}


def assembly_lines(text):
    """Extract this benchmark suite's numeric-offset assembly independently."""
    lines=[]
    for raw in text.splitlines():
        clean=re.split(r'[#;]',raw,maxsplit=1)[0].strip()
        if re.match(r'^(Add(?:\.d|i)?|Sub(?:\.d)?|Mult\.d|Ld|Sd|Bne|Beq)\s',clean,re.I):
            lines.append(clean)
    return lines


def sequential_reference(lines, initial_registers, initial_memory, memory_size=256):
    """Independent ISA oracle with real bytes and little-endian binary32 memory.

    It shares no decoder, scheduler, ROB, RAT, or memory implementation with the
    simulator. The benchmark suite uses numeric word branch offsets.
    """
    registers={f'{prefix}{i}':0 for prefix in ('R','F') for i in range(32)}
    registers.update(initial_registers)
    registers['R0']=0
    storage=bytearray(memory_size)
    written=set()
    def store(address,value):
        assert 0 <= address <= memory_size-4
        struct.pack_into('<f',storage,address,float(value))
        written.add(address)
    for address,value in initial_memory.items():store(address,value)
    pc=0;committed=0
    while pc<len(lines):
        if committed>=10000:raise RuntimeError('Sequential reference reached the loop guard')
        tokens=lines[pc].replace(',',' ').split()
        op=tokens[0].lower();destination=tokens[1].upper();next_pc=pc+1
        if op in ('ld','sd'):
            match=re.fullmatch(r'([+-]?\d+)\((R\d+)\)',tokens[2],re.I)
            offset,base=match.groups();address=int(registers[base.upper()])+int(offset)
            assert 0<=address<=memory_size-4
            if op=='ld':registers[destination]=struct.unpack_from('<f',storage,address)[0]
            else:store(address,registers[destination])
        elif op in ('beq','bne'):
            equal=registers[destination]==registers[tokens[2].upper()]
            if equal==(op=='beq'):next_pc=pc+1+int(tokens[3])
        else:
            left=registers[tokens[2].upper()]
            right=int(tokens[3]) if op=='addi' else registers[tokens[3].upper()]
            value=left*right if op=='mult.d' else left-right if op in ('sub','sub.d') else left+right
            if destination!='R0':registers[destination]=value
        registers['R0']=0
        committed+=1;pc=next_pc
    views=written|{a for a in range(0,memory_size-3,4) if struct.unpack_from('<f',storage,a)[0]!=0}
    memory={a:struct.unpack_from('<f',storage,a)[0] for a in sorted(views)}
    return registers,memory,committed


def verify_state(case,result,expected):
    registers,memory,count=expected
    assert count==case['instructions'],(case['title'],'hand-derived instruction count',count)
    assert result['committed_instruction_count']==count,(case['title'],'committed count')
    for key,value in registers.items():
        assert math.isclose(result['registers'][key],value,rel_tol=1e-12,abs_tol=1e-12),(case['title'],key,result['registers'][key],value)
    actual={int(k):v for k,v in result['memory'].items()}
    for key in actual.keys()|memory.keys():
        assert actual.get(key,0)==memory.get(key,0),(case['title'],'memory',key,actual.get(key,0),memory.get(key,0))
    assert all(row['commit'] is not None or row['squashed'] for row in result['instructions'])


def peak(result,stage):
    return max(Counter(row[stage] for row in result['instructions'] if row[stage] is not None).values(),default=0)


def interval(row,kind):
    begin,end=row[kind+'_start'],row[kind+'_end']
    return '-' if begin is None else f'{begin}-{end if end is not None else "?"}'


def render_text(case,result,width):
    lines=[case['title'],f'Author: {AUTHOR}',f'Issue width: {width}; CDB/WB=1; commit=1; memory ports=1',
           'Configuration: '+json.dumps(result['config'],sort_keys=True),'',
           f'{"ID":>3} {"PC":>3} {"Instruction":<24} {"ISSUE":>5} {"EXE":>9} {"MEM":>9} {"WB":>5} {"COMMIT":>6} STATUS']
    for row in result['instructions']:
        lines.append(f'{row["id"]:>3} {row["pc"]:>3} {row["text"]:<24} {row["issue"]:>5} {interval(row,"exe"):>9} {interval(row,"mem"):>9} {str(row["wb"]) if row["wb"] is not None else "-":>5} {str(row["commit"]) if row["commit"] is not None else "-":>6} {row["status"]}')
    lines.extend(['',f'Cycles: {result["cycles"]}',f'Committed: {result["committed_instruction_count"]}; issued: {result["issued_instruction_count"]}; squashed: {result["squashed_instruction_count"]}',
                  f'IPC: {result["ipc"]:.6f}',f'Branch mispredictions: {result["branch_misprediction_count"]}; memory replays: {result["memory_replay_count"]}',
                  'Independent sequential final-state check: PASS',case['explanation'],'','REGISTER VALUES'])
    for prefix in ('R','F'):
        lines.append(' '.join(f'{prefix}{i}={result["registers"][prefix+str(i)]}' for i in range(32)))
    lines.append('NON-ZERO MEMORY VALUES')
    lines.extend(f'{address}: {value}' for address,value in sorted((int(k),v) for k,v in result['memory'].items()) if value!=0)
    lines.append('RECOVERY EVENTS')
    lines.extend(json.dumps(event,sort_keys=True) for event in result['branch_mispredictions']+result['memory_replays'])
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'results')
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    summaries=[]
    for name,case in CASES.items():
        text=(ROOT/'examples'/f'{name}.in').read_text()
        program,base,registers,memory=parse_input(text)
        expected=sequential_reference(assembly_lines(text),registers,memory,base.memory_size)
        baseline=None
        for width in range(1,5):
            config=replace(base,issue_width=width,writeback_width=1,commit_width=1,mem_units=1,branch_prediction=True)
            result=Simulator(program,config,registers,memory).run()
            verify_state(case,result,expected)
            assert peak(result,'issue') <= width
            assert peak(result,'wb') <= 1 and peak(result,'commit') <= 1
            if name=='branch_recovery':assert result['branch_misprediction_count']==2
            if name=='memory_replay':assert result['memory_replay_count']>=1
            if name=='integer_unpipelined':
                rows=result['instructions']
                assert all(a['exe_end'] < b['exe_start'] for a,b in zip(rows,rows[1:]))
            baseline=baseline or result['cycles']
            summary={
                'case':name,'title':case['title'],'policy':'assignment','issue_width':width,
                'writeback_width':1,'commit_width':1,'memory_ports':1,
                'cycles':result['cycles'],'instructions':result['committed_instruction_count'],
                'issued':result['issued_instruction_count'],'squashed':result['squashed_instruction_count'],
                'ipc':round(result['ipc'],6),'speedup':round(baseline/result['cycles'],6),
                'peak_issue':peak(result,'issue'),'peak_wb':peak(result,'wb'),'peak_commit':peak(result,'commit'),
                'branch_mispredictions':result['branch_misprediction_count'],'memory_replays':result['memory_replay_count'],
                'state_check':'PASS',
            }
            summaries.append(summary)
            result['experiment']={**summary,'author':AUTHOR,'expected_state_explanation':case['explanation']}
            expected_registers,expected_memory,expected_count=expected
            result['reference']={
                'method':'Independent sequential assembly interpreter with little-endian binary32 byte memory',
                'check':'PASS','committed_instruction_count':expected_count,
                'registers':expected_registers,'memory':expected_memory,
            }
            stem=f'experiment_{name}_assignment_w{width}'
            (args.output_dir/f'{stem}.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
            (args.output_dir/f'{stem}.txt').write_text(render_text(case,result,width))
            print(f'{name:24} W={width} cycles={result["cycles"]:3} committed={result["committed_instruction_count"]:2} squashed={result["squashed_instruction_count"]:2} mispred={result["branch_misprediction_count"]} replay={result["memory_replay_count"]} PASS')
    bounded=[]
    program,base,registers,memory=parse_input((ROOT/'examples/handout_literal.in').read_text())
    for width in range(1,5):
        simulator=Simulator(program,replace(base,issue_width=width,max_cycles=200),registers,memory)
        try:simulator.run()
        except RuntimeError as error:
            assert '200 cycles' in str(error)
            bounded.append({'case':'handout_literal','issue_width':width,'max_cycles':200,'status':'EXPECTED_CYCLE_LIMIT','reason':'R2 remains 20 and R3 remains 0; Bne -3 always branches to PC0.'})
        else:raise AssertionError('The literal handout program unexpectedly terminated')
    (args.output_dir/'handout_literal_checks.json').write_text(json.dumps(bounded,indent=2)+'\n')
    (args.output_dir/'experiment_summary.json').write_text(json.dumps({
        'author':AUTHOR,'policy':'Assignment primary profile: only issue width changes; one CDB, one commit, one nonpipelined memory port.',
        'source_note':'Actual assignment handout controls behavior. The sample report supplies program ideas and formatting, not a correctness oracle.',
        'sample_case2_adaptation':'Retain two FP adders, two FP multipliers, and two address units; use one memory port per the assignment.',
        'experiments':summaries,'literal_handout_checks':bounded},indent=2)+'\n')
    with (args.output_dir/'experiment_summary.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(summaries[0]));writer.writeheader();writer.writerows(summaries)
    print(f'PASS: {len(summaries)} terminating experiments and {len(bounded)} expected literal-input cycle-limit checks.')


if __name__=='__main__':main()
