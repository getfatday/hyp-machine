---
id: event/chain-terminal-landed
type: event
context: {{CONTEXT}}
summary: A lane chain landed a terminal rc file — green or non-green; a budget halt is the same fact with a halt-class field, never a separate node.
representation: file(ledger/events.jsonl) — one JSON object per line, append-only
emitted-by: [command/land-terminal]
consumed-by: [read-model/event-stream]
status: current
---
Canonical node template for the H-238 unified event stream (kept 2026-09-02; copy into
your `operating-model/<context>/events/`). The terminal file itself
(`<runs_dir>/<lane>/chain-terminal.<phase>`, bare rc) stays the lane-local artifact;
this record is the stream's copy of the fact. Payload contract:
`{lane, phase, rc, green}` plus `halt-class` required exactly when `green` is false
(one-variable ruling: green/non-green is payload, not node identity). If your
`.claude/hyp.json` overrides `events_file`, name that path in the representation line.
