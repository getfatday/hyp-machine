---
id: event/ledger-record-appended
type: event
context: {{CONTEXT}}
summary: One typed JSON record landed in the work ledger — an intent, amendment, commitment, directive, or decision row.
representation: file(ledger/events.jsonl) — one stream record per append; the ledger row itself stays in the work ledger
emitted-by: [command/append-ledger-record]
consumed-by: [read-model/event-stream]
status: current
---
Canonical node template for the H-238 unified event stream (kept 2026-09-02; copy into
your `operating-model/<context>/events/`). One event for all row kinds because the
physical fact is one line in one file and the `kind` field carries the distinction.
Payload contract: `{kind, slug}`. If your `.claude/hyp.json` overrides `events_file`,
name that path in the representation line instead.
