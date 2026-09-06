---
id: policy/checkpoint-gate-stance
type: policy
context: {{CONTEXT}}
summary: The checkpoint compiler's per-class refusal stance (advise or deny), moved between runs by knob-observe.py from committed event/checkpoint-compiled rows.
trigger: event/checkpoint-compiled
enforcement: advisory
then: [command/file-gate-decision]
status: current
mode: shadow
controller:
  signal: event/checkpoint-compiled
  window: 30 observations
  rule: ladder
  bounds:
    10: [advise, deny]
    11: [advise, deny]
    12: [advise, deny]
    13: [advise, deny]
    15: [advise, deny]
  hysteresis: demote-on-first
  actuator: action
  kill_switch: mode off | .claude/knob-freeze | open kind:knob-pin row
action:
  10: advise
  11: advise
  12: advise
  13: advise
  15: advise
---
The first canonical knob node (checkpoint-gate-shadow-promotion lane). A knob is a bounded
setting a script moves between runs, never during one; this node's per-class `action` field is
the actuator and the compiler's typed exit classes (10 assertion-count-mismatch, 11
verdict-tally-contradiction, 12 budget-line-missing, 13 dangling-evidence-pointer, 15
untraceable-numeric) are the classes.

Ladder rule (`rule: ladder`, pinned): the window is the most recent 30 observations
(`event/checkpoint-compiled` rows) and n_min is 30. A class is promoted to `deny` when the
window holds n_min observations and the class shows 0 refusals on counted runs across it; a
class showing any refusal on a counted run holds at `advise`; a promoted class is demoted on
the first such refusal (`hysteresis: demote-on-first`). A refusal on a counted run is a row
with a non-zero exit whose lane carries `experiments/runs/<lane>/VERDICT.json` at HEAD or whose
spec Status word is kept or discarded; a non-zero row on a lane with neither is not counted; an
exit-0 row is not applicable. A class 10, 11 or 15 refusal on a counted run also surfaces as an
`advisory: landed-contradiction` line and never feeds a promotion.

Modes: `shadow` records what it would set and files nothing; `recommend` files one class-plan
decision row through `scripts/decisions.py add --no-open` when the window is full, the
licensing policy node resolves, and no decision for this knob is open; `off` is a kill switch.
The other kill switches are a `.claude/knob-freeze` file and an open `kind: knob-pin` row in
the work ledger. `act` (the evaluator editing this node itself) is out of scope here.
