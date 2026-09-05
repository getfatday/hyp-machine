# ledger/north-stars/ — the north-star file convention

One tracked file per destination: `ledger/north-stars/<slug>.md`, from `templates/north-star.md`.
A north-star file is structure, never a vision paragraph (H-245 banked prose steering): a one-line
destination, a `reached-when:` list, and a table of conditions. It stores NO status. Every
condition's state is derived at read time by `scripts/north-star-check.py` from the committed
resolver state at one commit, so a verdict never writes this file and the file cannot drift from
the verdicts — there is nothing in it to drift.

## File shape

```
# North star: <slug>

destination: <= 25 words
reached-when: C-NN, C-NN          # every listed condition must derive done or retired

## Conditions
| id | condition | resolver | bound | closes-when | needs |

## Horizon
- Z-NN (YYYY-MM-DD): <question not yet sharp enough to be a condition>   [-> C-NN on graduation]

## Excluded
- X-NN: <terminal exclusion> — banks: <H-NNN | DEC-NNN | research/raw/...>
```

## Column rules

| column | rule |
|---|---|
| `id` | `C-NN`, unique, ascending. |
| `condition` | One clause: what must be true. No status words — status is derived, never stored. |
| `resolver` | Exactly one of `hypothesis`, `decision`, `capture`, `probe`. |
| `bound` | The resolver's item id: `H-NNN`, `DEC-NNN`, `research/raw/<file>`, or a probe lane id (`experiments/runs/<lane>/`). |
| `closes-when` | The shipped closes-when grammar (`scripts/closes_when.py`) binding that item, or `-` for an unbound condition. |
| `needs` | `-`, or comma-separated prerequisite tokens (grammar below). |

## Resolver kinds and their predicates

| resolver | closes-when | derives `done` when | outcome (for `C-NN:yes/no`) |
|---|---|---|---|
| hypothesis | `hypothesis-kept=H-NNN` | the spec's line-initial `## Status` word is `kept` | kept = yes, discarded = no |
| hypothesis | `hypothesis-verdict=H-NNN` | the Status word is `kept` OR `discarded` (question answered either way; PROPOSED predicate, evaluated by north-star-check.py; lands in closes_when.py through the plugin ship row) | kept = yes, discarded = no |
| decision | `decision-resolved=DEC-NNN` | an `accepted` or `denied` `decision-resolution` row exists in `ledger/work-ledger.jsonl` (`commented` leaves it open) | accepted = yes, denied = no |
| capture | `path-exists=research/raw/<file>` | the capture is committed | yes |
| probe | `path-exists=experiments/runs/<lane>/VERDICT.json` | the probe's verdict artifact is committed | `verdict` pass/keep = yes, fail/discard = no |

Lineage: a hypothesis whose Status reads `refined-into: H-MMM` hands its condition to H-MMM (the
EFFECTIVE resolver); the row is not edited.

## `needs` grammar

- `C-NN` — the prerequisite must derive `done`.
- `C-NN:yes` / `C-NN:no` — the prerequisite must resolve with that outcome. If it resolves the
  other way, this condition derives `retired:C-NN` (moot), and so does everything that needs it.
- A condition with unmet needs is still `open`; it is simply not on the frontier yet.
- Cycles, references to unknown ids, and malformed tokens (`C-NN:maybe`) are lint errors.

## Derived-status vocabulary (never stored)

| status | meaning |
|---|---|
| `done` | the bound predicate is satisfied at the commit |
| `open` | bound, not yet satisfied |
| `retired:C-NN` | an outcome-conditioned prerequisite resolved the other way (root id carried); retired precedes done |
| `unbound` | `closes-when` is `-`; counts in distance, never in the frontier |

Derived lists and numbers: **frontier** = open conditions whose every need is satisfied, in
`C-NN` order, each with a resolver verb (`register` spec absent / `run` spec present / `add`
decision row absent / `resolve` decision open / `capture` / `probe`); **claimed_fresh** =
frontier members whose lane (`experiments/runs/<effective-id>/LANE-STATE.json`) carries a
heartbeat fresher than `ttl_s` (H-215/H-216; working-tree overlay, never under `--at`);
**retired**; **distance** = the largest count of open-or-unbound conditions on any `needs` path
into a `reached-when` condition; **reached** = every `reached-when` condition is done or retired.

## Lint classes

`--strict` exits 1 on any of: `SCHEMA` (shape, ids, resolver/bound/closes-when binding, token
grammar, destination length), `DANGLING-REF` (unknown `C-NN` in needs / reached-when / horizon
graduation), `CYCLE` (needs cycle), `STATUS-STORED` (an authored `status` column — the one thing
this file must never carry). `HORIZON-AGED` (a horizon line older than 60 days without `-> C-NN`)
is advisory and never changes the exit code.

## Reading

```
python3 scripts/north-star-check.py                 # every north-star file at HEAD, text
python3 scripts/north-star-check.py --json --slug X # machine form, sorted keys
python3 scripts/north-star-check.py --at <sha>      # replay at a past commit
python3 scripts/north-star-check.py --strict        # lint gate (exit 1 on hard findings)
python3 scripts/north-star-check.py --selftest      # throwaway-repo proof, exits 0 only if all pass
```

Provenance: derived-condition-status lane (hypothesis spec `H-DRAFT-2cae0933-derived-condition-status`
until land; the fixture north-star file and answer key under that lane's `fixture/` are the successor
lanes' shared inheritance).
