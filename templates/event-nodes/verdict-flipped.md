---
id: event/verdict-flipped
type: event
context: {{CONTEXT}}
summary: A hypothesis spec's Status line changed verdict state — kept, discarded, or refined.
representation: file(ledger/events.jsonl) — one JSON object per line, append-only
emitted-by: [command/flip-verdict]
consumed-by: [read-model/event-stream]
status: current
---
Canonical node template for the H-238 unified event stream (kept 2026-09-02; copy into
your `operating-model/<context>/events/` — the emitters validate against this node's
declared representation, so without it the event does not exist). The flip already has
scattered artifacts (the spec Status line, any findings index); this node gives the
*fact of the flip* one canonical record. Payload contract: `{spec, from, to, evidence}`.
Attribution stays in git — no author fields. If your `.claude/hyp.json` overrides
`events_file`, name that path in the representation line instead.
