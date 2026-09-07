---
id: event/checkpoint-compiled
type: event
context: {{CONTEXT}}
summary: One compile-run-checkpoint.py invocation exited (emitted or refused); the compiler's typed exit is the payload, never a separate node per class.
representation: file(ledger/events.jsonl) — one JSON object per line, append-only
emitted-by: [command/checkpoint-shadow]
consumed-by: [read-model/event-stream]
status: current
---
Canonical node template for the checkpoint-compiled-events lane (copy into your
`operating-model/<context>/events/`). The compiled page itself (`run-checkpoint.html`, the
NAMING.md checkpoint) and the compiler's stderr stay lane-local; this record is the stream's
copy of the fact that one build ran and how it exited. Payload contract:
`{rc, class, lane, run}`: `rc` the compiler's exit, `class` its pinned exit-table name
(0 emitted, 1 internal-error, 10 assertion-count-mismatch, 11 verdict-tally-contradiction,
12 budget-line-missing, 13 dangling-evidence-pointer, 14 source-digest-stale,
15 untraceable-numeric, anything else untyped; the validator refuses a class that contradicts
its rc), `lane` the run directory's parent basename, `run` its basename. `subject` is the run
directory relative to the repository root; `caused-by` is
`scripts/compile-run-checkpoint.py@<first 12 hex of the compiler file's sha256 as invoked>`;
`date` is caller-pinned. The row is a fact and licenses nothing: no policy node names it.
If your `.claude/hyp.json` overrides `events_file`, name that path in the representation line.
