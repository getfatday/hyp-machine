---
id: event/advisory-surfaced
type: event
context: {{CONTEXT}}
summary: Standing hygiene state reached the session as injected context — a policy advisory line surfaced to a working session.
representation: file(ledger/events.jsonl) — one JSON object per line, append-only; plus injected-context (hook stdout — the live, lossy surface)
emitted-by: [command/run-advisory-suite]
consumed-by: [read-model/event-stream]
status: current
---
Canonical node template for the H-238 unified event stream (kept 2026-09-02; copy into
your `operating-model/<context>/events/`). Before the stream this event left no repo
artifact — hook stdout was its whole trail ("an advisory that never reaches the model
does not exist" — and one that only reaches the model exists for nobody else). The
stream record `{policy, message}` (subject = what the advisory is about) is the
committed fact; the shipped PreToolUse interpreter emits it for every surfaced advisory
at the experiments profile. If your `.claude/hyp.json` overrides `events_file`, name
that path in the representation line instead.
