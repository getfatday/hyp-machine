---
id: event/workflow-closed
type: event
context: {{CONTEXT}}
summary: A governed workflow reached its close — gates adjudicated, one workflow-fact row landed.
representation: file(ledger/events.jsonl) — one JSON object per line, append-only; mirrors the workflow-fact/v1 row in ledger/workflow-facts.jsonl
emitted-by: [command/close-workflow]
consumed-by: [read-model/event-stream]
status: current
---
Canonical node template for the H-238 unified event stream (kept 2026-09-02; copy into
your `operating-model/<context>/events/`), generalizing the kept H-118 close-time
emitter (workflow-facts.jsonl was already an event stream — the pattern the H-238
program unifies; the shipped `scripts/emit_workflow_fact.py` emits this record on every
real close at the experiments profile). Payload contract:
`{workflow, sha, gates_passed, gates_total}`; idempotence key upstream is workflow+sha
(H-118), and the stream record inherits it via canonical-bytes dedupe. If your
`.claude/hyp.json` overrides `events_file`, name that path in the representation line.
