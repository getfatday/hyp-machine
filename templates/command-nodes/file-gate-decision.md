---
id: command/file-gate-decision
type: command
context: {{CONTEXT}}
summary: File one class-plan decision row for a knob through scripts/decisions.py add --no-open, carrying the per-class plan, the counts, the signal sha256 and the default nothing changes.
issued-by: policy/checkpoint-gate-license
executor: agent
handler: script/scripts/knob-observe.py
freedom: low
reads: [read-model/event-stream]
emits: [event/ledger-record-appended]
invariants-enforced: [policy/checkpoint-gate-stance]
status: current
---
The one verb the licensing policy issues. Class `plan`; the default on silence is the literal
`nothing changes` (the node stays at advise for every class), so no dated backstop is armed. The
row is appended by `decisions.py add --no-open` with `--date` pinned by the caller
(`DECISIONS_TODAY`); the evaluator reads no clock.
