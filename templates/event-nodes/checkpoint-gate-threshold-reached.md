---
id: event/checkpoint-gate-threshold-reached
type: event
context: {{CONTEXT}}
summary: The checkpoint-gate knob's observation window holds its declared sample size; the per-class would-set verdicts are now evidence a decision can be filed on, never before.
representation: file(ledger/knob-state.jsonl) — the state row whose `state` begins `threshold-reached`
emitted-by: [command/knob-observe]
consumed-by: [policy/checkpoint-gate-license]
status: current
---
The event the licensing policy (`templates/policy-nodes/checkpoint-gate-license.md`) names in
its `trigger:`. It is not a separate stream row: `scripts/knob-observe.py evaluate` records it
as the `state` field of the knob's state row (`threshold-reached n=30/30 (...)`) the first
boundary at which the window holds `n_min` observations. In `mode: shadow` the row carries the
per-class `would_set` only and nothing is filed; in `mode: recommend`, with this event's policy
node present and every `then:` command resolving, the evaluator files exactly one class-plan
decision row through `command/file-gate-decision`. Without the policy node the evaluator
degrades to shadow (H-241) and the state row names the missing license. The event licenses
nothing by itself; the policy does. See `docs/knobs.md` for the state-row grammar.
