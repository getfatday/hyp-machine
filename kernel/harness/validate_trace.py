#!/usr/bin/env python3
"""Deterministic trace-citation validator (frozen harness instrument).

Usage: validate_trace.py <trace.json> <transcript.jsonl>
  trace.json: a JSON array of trace records: {"step": str, "nodes": [...], "output_tokens": int}

Checks every citation the trace makes against the transcript:
  - msg_<id> claims must exist as message ids in the transcript
  - "record N" / "records N-M" / "records N, M" claims must be in range,
    and when the same step names a tool, at least one cited record must involve that tool
Prints a per-claim report and a summary line:  ACCURACY <valid>/<checked>
Exit code 0 if all claims valid, 1 otherwise. Zero claims => vacuously ACCURACY 0/0, exit 0.
"""
import json, re, sys

def main(trace_path, transcript_path):
    lines = [json.loads(l) for l in open(transcript_path)]
    msg_ids = set()
    rec_tools = {}  # index -> set of tool names involved (tool_use names or result linkage)
    for i, o in enumerate(lines):
        m = o.get('message') or {}
        if isinstance(m, dict) and m.get('id'):
            msg_ids.add(m['id'])
        tools = set()
        cont = m.get('content') if isinstance(m, dict) else None
        if isinstance(cont, list):
            for c in cont:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    tools.add(c.get('name', ''))
        rec_tools[i] = tools

    trace = json.load(open(trace_path))
    checked = valid = 0
    known_tools = {'Read', 'Write', 'Edit', 'Bash', 'Skill', 'StructuredOutput', 'Grep', 'Glob'}
    for step in trace:
        text = step.get('step', '')
        # msg id claims
        for mid in re.findall(r'msg_[A-Za-z0-9]{6,}', text):
            checked += 1
            ok = mid in msg_ids
            valid += ok
            print(f"{'OK ' if ok else 'BAD'} msg-id {mid}")
        # record number claims
        nums = set()
        for a, b in re.findall(r'records?\s+(\d+)(?:\s*[-–]\s*(\d+))?', text):
            if b:
                nums.update(range(int(a), int(b) + 1))
            else:
                nums.add(int(a))
        for grp in re.findall(r'records?\s+((?:\d+\s*,\s*)+\d+)', text):
            nums.update(int(x) for x in re.split(r'\s*,\s*', grp))
        step_tools = {t for t in known_tools if re.search(r'\b' + t + r'\b', text)}
        for n in sorted(nums):
            checked += 1
            in_range = 0 <= n < len(lines)
            tool_ok = True
            if in_range and step_tools:
                tool_ok = any(step_tools & rec_tools.get(k, set()) for k in {n, max(0, n - 1), min(len(lines) - 1, n + 1)})
            ok = in_range and tool_ok
            valid += ok
            print(f"{'OK ' if ok else 'BAD'} record {n}{'' if in_range else ' (out of range)'}{'' if tool_ok else ' (tool mismatch: ' + ','.join(sorted(step_tools)) + ')'}")
    print(f"ACCURACY {valid}/{checked}")
    return 0 if valid == checked else 1

if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
