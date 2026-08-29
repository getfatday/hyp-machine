# The review cadence

`scripts/review-cadence.py` is the verdict-forcing review over open work-ledger rows: open
rows render as REVIEW DEBT ranked aged-first, and every open row leaves a review with
exactly one recorded verdict — "seen, no action, still open" is not a state. Verdicts are
append-only rows in `ledger/review-ledger.jsonl`; the script never edits
`ledger/work-ledger.jsonl`, never rewrites history, and is purely additive.

## Usage

Repo root resolution: `--repo`, then `CLAUDE_PROJECT_DIR`, then the cwd.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py"            # render the review surface
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py" verdict \
    --slug <slug> --class <class> \
    [--evidence <path> [--evidence <path> ...]] \
    [--next-touch YYYY-MM-DD] [--reason <text>] [--superseded-by <slug>]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py" emit-doc   # print the full rules text
```

The four verdict classes (the rendered surface teaches the same rules):

| Class | When | Refused unless |
|---|---|---|
| `act-now` | the row's work is executable now: do it, then record it | every created artifact recorded via `--evidence` and every path exists |
| `next-touch` | schedule for a strictly future date | the row is younger than 7 days — AGED rows can never be re-scheduled |
| `parked-because` | the row itself names a blocker outside the repo | `--reason` quotes that blocker |
| `closed-with-cause` | the row's closes-when predicate is satisfied by evidence born strictly AFTER the row's date, or a strictly newer row supersedes it (`--superseded-by`) | on an AGED row, additionally the act evidence on disk |

Closure semantics carry a **born-after anchor**: a commit-grep match dated on/before the
row's own date never closes it. Supersession is an explicit verdict, never silence. AGED
rows (>= 7 days) must leave the session acted, parked, or closed with evidence — never
re-scheduled.

## The multi-evidence law

A verdict declares EVERY artifact the act created: `--evidence` repeats once per artifact,
each provided path is validated to exist, and all of them land in the ONE verdict row
(a single-artifact act records the bare path, byte-compatible with earlier single-evidence
rows). This is the H-188 interface repair: its predecessor's appender accepted exactly one
`--evidence` path, so an act that created two artifacts could not be declared honestly —
the assertion then flagged the undeclared artifact as foreign. The rules text, the rendered
surface, and the refusal messages all teach the same record-every-artifact rule.

## Evidence

**H-188-dangling-end-pickup-v3** (source lab, kept 2026-08-26, two consecutive counted
5/5): the arm declared every created artifact through the repeated-`--evidence` interface,
the assertion's allowlist admitted them, and the mechanism's substance (ranked
re-presentation, the verdict obligation, born-after closure) held as it had in every
counted run of the lineage (H-163 → H-187 → H-188 — the mechanism was right from run one;
the instruments took three specs to measure it honestly).

The shipped script keeps the counted appender exactly as counted; only provenance framing,
invocation paths, and consumer-repo-root resolution differ from the counted fixture copy
(verified by diff at port time).
