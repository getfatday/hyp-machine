---
bump: minor
---

The unified event stream ships: the source lab's whole event-emission program — four keeps
closed 4/4 on 2026-09-02 — promoted out of experiment fixtures into the plugin, closing the
"ship the stream" prerequisite the lab's 0.9.0 wave carried. `scripts/events_lib.py` holds the
frozen record grammar with node-validated, canonical-bytes idempotent append (no committed
event node, no event; the five canonical nodes ship in `templates/event-nodes/`), and
`scripts/emit-event.py` exposes the five choke-point verbs as one gated CLI, with in-place
emitters where the plugin already owns the choke point — the PreToolUse interpreter
(advisory-surfaced) and `scripts/emit_workflow_fact.py` (workflow-closed) — while the stream
file joins the write-once-guard class beside the journal (lab H-238, kept 2x5/5). The session
resolver gains the exactly-once events-cursor join: stream records a session has not seen
surface once at its boundary and never again, with non-event resolver output byte-identical
when the join is stripped — the contract test ships as `scripts/selftest-events.py` (lab
H-239, kept 2x5/5). `scripts/watch-dispatch.py` plus `scripts/install-watch-plist.sh` add
watch-triggered dispatch under the unchanged H-217 firing contract, both emitted and never
auto-loaded (lab H-240, kept 2x5/5), and `scripts/events-consume.py` acts only when a
committed policy node's `trigger:` names the event and its `then:` fully resolves to command
nodes, degrading by the kept advisory/read-model/nothing rule otherwise with actions
stub-recorded (lab H-241, kept 2x5/5). Experiments-profile machinery throughout; one-page
layer doc at `docs/event-stream.md`.
