# Stopping rules: a frozen rule decides when an observation has earned a verdict

A **stopping rule** is a committed JSON document that says when a stream of observations has
produced enough evidence to decide, and in which direction. It is **frozen** at the gate -- copied
with its sha256 into a `frozen/` document before the first look -- and the evaluator
`scripts/stopping-rule.py` reads only that copy, never the source rule, so a rule edit after the
gate changes nothing and a missing or altered copy halts with a typed exit. Its only verdict
vocabulary is **evidence-sufficient** (with a direction, `promote` or `hold`) and
**evidence-insufficient** (with the look count, or `max-looks` when the window is spent). The
words keep and discard belong to the hypothesis loop and never appear in a state line: an
observation that has not earned a decision has a typed way to say so, and it is not a failure.
Lane: `observation-stopping-rule` (a split of the decision-durability design "observation lane
shape"); vocabulary: `experiments/runs/DESIGN-destination-map/NAMING.md`.

Why frozen: a controller that tests its threshold at every look and acts on the first pass
inflates its false promotions without anyone choosing to (optional stopping). Fixing the rule
before the first observation -- a fixed sample size or a sequential test whose boundaries were set
in advance -- is the standard remedy; the lane measured a peeking decider at 41/200 false promotes
on null streams against 0/200 (fixed-n) and 8/200 (sequential) for the frozen rules.

## Rule grammar

```json
{"kind": "fixed-n", "n_min": 60, "max_looks": 120, "threshold": {"13": 2}}
{"kind": "sprt", "alpha": 0.05, "beta": 0.05, "p0": 0.15, "p1": 0.01, "max_looks": 120}
```

| kind | fires when | direction |
|---|---|---|
| `fixed-n` | at look `n_min` exactly, never before | `promote` when the class's refusal count over the first `n_min` rows is at or below `threshold[class]`, else `hold` |
| `sprt` | at the first look where the running log-likelihood ratio crosses a boundary | `promote` when llr >= log((1-beta)/alpha); `hold` when llr <= log(beta/(1-alpha)); the ratio adds log(p1/p0) on a refusal and log((1-p1)/(1-p0)) otherwise (p0 = the null per-look refusal rate, p1 = the effect rate) |
| either | look `max_looks` reached with no terminal | `evidence-insufficient max-looks n=<max_looks>` |

Windows are denominated in looks, never wall time. A rule without an `n_min` (sprt) reports its
`max_looks` as the denominator in its insufficient lines.

## Rows

One JSON object per line, looks numbered from 1 in order:

```
{"look": 1, "class": "13", "refusal": 0}
```

## Verbs

```
stopping-rule.py --freeze rules/<kind>.json --into frozen/<kind>.json
stopping-rule.py --evaluate <rows.jsonl> --rule frozen/<kind>.json --looks <k|all> [--json]
stopping-rule.py --check <state.jsonl> --rule frozen/<kind>.json
stopping-rule.py --selftest [--into DIR] [--json]
```

`--freeze` writes `{"frozen": "stopping-rule", "rule_text": <the rule file's bytes>, "sha256":
<sha256 of those bytes>, "source": <path as given>}`. `--evaluate` verifies the copy (sha over
`rule_text`) before reading a single row, then computes each look's state from the first k rows
alone; `--looks k` prints the one line for look k (exit 3 `stream-terminated n=<t>` if the stream
already terminated at t < k), `--looks all` prints looks 1..terminal from one process -- the same
per-look computation batched, byte-identical line for line. `--check` is the invariant checker
over a recorded state file (frozen copy present and intact; every row's `rule_sha` equals the
copy's sha; looks consecutive; exactly one terminal, last; never early, never late against the
rule; no keep/discard). `--selftest` seeds six state files -- two clean (a fixed-n stream ending
at look 60, a sequential stream that never crosses and ends `evidence-insufficient max-looks
n=120`) and four violations (a fixed-n terminal at look 59, a sequential terminal one look before
the crossing, a row whose rule sha differs from the copy, insufficient lines with the copy absent)
-- and exits 0 only when the checker passes both clean files and names the violated invariant on
each of the four.

## State lines

`--json` emits canonical JSON (sorted keys): `look`, `n_min`, `rule`, `rule_sha`, `state`,
`stream`, plus `llr` under `sprt`.

```
evidence-insufficient n=<k>/<n_min>        before the rule fires (n_min = max_looks for sprt)
evidence-sufficient promote                terminal
evidence-sufficient hold                   terminal
evidence-insufficient max-looks n=<max>    terminal: the window is spent with no decision
```

## Exit codes

| code | meaning |
|---|---|
| 0 | ok |
| 1 | `--check`: an invariant is violated; stderr `invariant-violated <name>` |
| 2 | usage, malformed rows, or malformed rule (`rule-malformed`, `rows-malformed`, `rule-class-unknown`) |
| 3 | `--looks k` past the stream's terminal (`stream-terminated n=<t>`) |
| 12 | `frozen-rule-missing`: the frozen copy is absent (0 output lines) |
| 13 | `frozen-rule-tampered`: the copy's bytes disagree with its recorded sha, or it lacks the frozen fields (0 output lines) |

## Containment

The evaluator reads exactly two paths, the rows and the frozen copy; no clock, no environment,
no process spawn, no network. The lane's fixture audits every `open()` and scans the source for
`os.open`, `subprocess`, `importlib`, `__import__`, `socket` and the fixture's key directory name.

## Relation to knobs

`scripts/knob-observe.py` (docs/knobs.md) carries its window as a constant `window: <n>
observations` inside the knob node. The On-keep row `checkpoint-gate-n-min-derived` is the
follow-on where a knob node names `rule: fixed-n` with a `frozen/` copy in place of that constant;
it is a separate amendment, not part of this evaluator.

## Relation to lineages

The same frozen SPRT, read over a whole lineage's pooled counted looks instead of one lane's observation
stream, is the **lineage stopping rule** (`scripts/lineage-stopping.py`, `rules/lineage-sprt.json`,
`docs/lineage-stopping.md`): typed voids re-take a look without counting, a spend budget derived from the
spec's Budget-per-run line is the only non-evidence exit, and no launch count exists. This evaluator is
unchanged by it; the lineage script writes the stream and reads the terminal back (lab
H-DRAFT-5810517d-verdict-lineage-stopping, kept 2026-09-11).
