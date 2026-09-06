# Knobs: bounded settings a script moves between runs

A **knob** is a bounded setting a script moves between runs, never during one (vocabulary:
`experiments/runs/DESIGN-destination-map/NAMING.md`). A knob is a committed node under
`operating-model/<context>/knobs/<slug>.md` carrying a `controller:` block; `scripts/knob-observe.py`
is the evaluator that reads it at a session boundary, appends one state row per new signal state,
and -- in `mode: recommend` only -- files one evidenced decision row at the declared sample size.
First knob: `policy/checkpoint-gate-stance` (lane checkpoint-gate-shadow-promotion).

## Node grammar (frontmatter)

```yaml
mode: shadow            # shadow | recommend | off      (act is a separate hypothesis)
controller:
  signal: event/checkpoint-compiled    # the committed event rows the ladder reads
  window: 30 observations              # the sample size; n_min = the window
  rule: ladder                         # promote on 0 refusals-on-counted-runs across the window; hold on any
  bounds:                              # per class, the closed value set
    10: [advise, deny]
  hysteresis: demote-on-first          # a promoted class is demoted on the first refusal
  actuator: action                     # the node's own per-class field a maintainer (or act mode) edits
  kill_switch: mode off | .claude/knob-freeze | open kind:knob-pin row
action:
  10: advise
```

A node with any per-class value outside its bounds is refused (exit 2) before a row is written.

## Verbs

```
knob-observe.py evaluate <knob> [--root DIR] [--at SHA] [--replay] [--json]
knob-observe.py check <knob> [--root DIR] [--json]
knob-observe.py --selftest
```

`evaluate` is the boundary step: it reads the signal file (`.claude/hyp.json` `events_file`), the
knob node, the licensing policy node (a policy whose `trigger:` names
`event/checkpoint-gate-threshold-reached` with every `then:` command resolving to a command node,
the H-241 rule), the kill-switch surfaces, and lane state (`experiments/runs/<lane>/VERDICT.json`
or the spec's `## Status` word) to derive each row's label. `--at SHA` and `--replay` are
read-only. `check` validates the two ledgers' invariants (no early filing, no value outside the
bounds, no filing without the license, no promotion on a landed contradiction) and exits 1 on any
violation; `--selftest` seeds each violation and proves `check` bites.

Dates: the evaluator reads no clock. `DECISIONS_TODAY` (YYYY-MM-DD) is passed through to
`decisions.py add --date`; recommend mode refuses to file when it is unset (exit 3).

## State row (`ledger/knob-state.jsonl`, canonical JSON, one per line)

| field | meaning |
|---|---|
| `schema` | `knob-state/v1` |
| `knob`, `mode`, `signal`, `n_min` | the node's slug, mode word, signal event id, window size |
| `n`, `total_observations` | rows in the window (the last `n_min`) and in the whole file |
| `signal_sha256` | sha256 of the signal file's bytes; with `mode`, `kill_switch`, `license` the idempotence key: an evaluation whose key equals the latest row's appends nothing |
| `kill_switch` | `null`, or `mode-off` / `knob-freeze` / `knob-pin` (joined by `\|` when several) |
| `license` | `ok`, or `missing: <reason>` (recommend degrades to shadow) |
| `state` | `evidence-insufficient n=k/30` / `threshold-reached n=k/30 (shadow: would_set only)` / `threshold-reached n=k/30 (filed DEC-NNN)` / `threshold-reached n=k/30 (open decision DEC-NNN)` / `unlicensed n=k/30: ...` / `killed: <cond> n=k/30` |
| `per_class.<c>` | `observations`, `refusals_on_counted_runs`, `action` (the node's current value), `would_set` (the ladder's verdict; `deny` only when the window is full and the class shows 0 refusals on counted runs) |
| `advisory` | `landed-contradiction: class <c> refusal on counted run <subject>[; contradicts open decision DEC-NNN (plan deny)]` for classes 10, 11, 15 |
| `contradicts`, `open_decision`, `filed` | the open decision a contradiction demotes against; the open decision for the knob; the ids filed by this evaluation |

The filed decision row is class `plan`, its default `IF YOU DO NOTHING: nothing changes` (the
node stays at advise), and its `note` carries `plan: 10=deny 11=deny ...` plus the per-class
counts; `context_pointers` name the knob node, the signal file at its sha256, and the state row.
The SessionStart resolver line reads the latest state row:
`KNOB checkpoint-gate-stance n=k/30 would=10:advise,...`.
