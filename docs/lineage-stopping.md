# Lineage stopping: a lineage stops on evidence or on spend, never on a launch count

A **lineage** is a hypothesis spec plus its refine successors. Until this rule, a lineage ended by a
count someone chose: "keep if 4/4 assertions pass in 2 consecutive runs ... discard after 3 failed
runs" and, since the rigor rows, "a lineage cap on launches across refines". The count has no error
model (a coin-flip mechanism passes two consecutive runs 25% of the time and is kept 50.0% of the time
under the capped rule, 57.8% uncapped) and the cap has no evidence model: it ends lineages on
bookkeeping, and four recorded lineages in one week spent their launches on harness defects that were
never leaks or misses and then asked a human for a number.

The **lineage stopping rule** replaces both. It reads the kept sequential instrument
(`scripts/stopping-rule.py`, `docs/stopping-rules.md`) over the lineage's **pooled counted looks**
with a **frozen policy**, re-takes **typed voids** without counting them, and stops otherwise only when
a **spend budget derived from the spec's Budget-per-run line** is spent. No spec carries a launch count
and no decision card ever asks for one. Source lab: `H-DRAFT-5810517d-verdict-lineage-stopping`, kept
2026-09-11 (fragment 0490); the lane decided its own verdict by this rule.

## The rule as a decision procedure (R0-R4)

| step | when | what |
|---|---|---|
| **R0 freeze** | once, at the lineage root's registration | `rules/lineage-sprt.json` -> `<runs_dir>/<root>/lineage/frozen/rule.json` by the instrument's `--freeze`; the copy must be byte-identical to `rules/frozen/lineage-sprt.json` (refused otherwise). Open `stream.jsonl`, `looks.jsonl`, `spend.jsonl`. Derive the **spend budget** = the spec's Budget-per-run caps (wall-clock seconds, US$) x the frozen rule's **truncation length**, recomputed from the four policy numbers (refused unless it equals `max_looks`); both numbers and their derivation go into `spend.jsonl`'s header row. |
| **R1 refuse** | before every launch | Refuse (exit 3, `budget-exhausted`, a row in `refusals.jsonl`) when cumulative spend + one per-run cap would exceed **either** budget component; also refuse while a look is pending its root cause (`look-pending`) and once the lineage has read a terminal (`terminal:<kind>`). Otherwise `launch`. |
| **R2 charge + type** | after every launch | Charge the run's recorded `cost_usd` and `wall_s` to `spend.jsonl` -- every launch, counted or void. Then type the run by the table below: a **counted look** appends `{"look": k, "class": "lineage", "refusal": 0|1}` to the stream; a **void** appends no look, is recorded in `voids.jsonl`, and is re-taken by the next launch while spend remains. |
| **R3 evaluate** | after every counted look | `stopping-rule.py --evaluate stream.jsonl --rule frozen/rule.json --looks all --json` -> `state.jsonl`; the prefix invariants on an in-progress file, `--check` only on a terminated one (a stream that carries an annul row is checked by the annul-aware invariants instead -- "Annulment after record" below). `evidence-sufficient promote` = the lineage's current spec is **KEPT**; `evidence-sufficient hold` = **DISCARDED** with its exclusion banked; `evidence-insufficient max-looks n=13` = **closed without a verdict** (the mechanism's pass rate sits in the indifference zone between p1 and p0; the pooled counts are banked; a change of policy numbers is a lab decision, never this lineage's). Otherwise continue at R1. |
| **R4 inherit** | at a refine (new id) | The successor inherits R0's artifacts unchanged -- the frozen copy, the stream, the looks, the ledger, the spend budget. `init <successor> --inherit <root>` writes one pointer (`inherits.json`); every verb on the successor resolves to the root's files. Nothing resets; no count exists to reset. |

## The frozen policy and its constants

`rules/lineage-sprt.json` is exactly the bytes the lab spec quotes, terminated by one newline:

```json
{"kind": "sprt", "alpha": 0.05, "beta": 0.1, "p0": 0.5, "p1": 0.1, "max_looks": 13}
```

| constant | value | where it comes from |
|---|---|---|
| policy sha256 | `8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1` | sha256 of the policy bytes; recorded as `rule_sha` on every state line and as `frozen_rule_sha256` in the spend header |
| frozen copy sha256 | `ce73163aeda7147edfc0d6fab1fbca9e5dac6ad4bb37ff9ca1c86cf64f85b027` | `rules/frozen/lineage-sprt.json`, what `--freeze rules/lineage-sprt.json` writes from the plugin root (`source` is that relative path) |
| alpha, beta | 0.05, 0.10 | the kept observation-stopping-rule lineage's SPRT numbers, held four times at 46 and 54 null promotes of 1000 |
| p0, p1 | 0.5, 0.1 | null per-look refusal rate (a coin flip) and effect refusal rate |
| inc0 = log((1-p1)/(1-p0)) | 0.5877866649021191 | added to the llr on a refusal-0 look |
| inc1 = log(p1/p0) | -1.6094379124341003 | added on a refusal-1 look |
| promote boundary log((1-beta)/alpha) | 2.8903717578961645 | `evidence-sufficient promote` at llr >= this: five straight refusal-0 looks (2.9389), eight of nine, eleven of thirteen |
| hold boundary log(beta/(1-alpha)) | -2.251291798606495 | `evidence-sufficient hold` at llr <= this: two straight refusal-1 looks (-3.2189) |
| max_looks = 13 | Wald's truncation | the smallest n with P_p0(T > n) <= alpha and P_p1(T > n) <= beta: 0.0439 and 0.0595 at n = 13 (n = 12 reads 0.0586 / 0.1446 and does not qualify). Derived from the four numbers, never chosen; `init` recomputes it and refuses any other value. Reached only in the indifference zone and read as `evidence-insufficient`, never a promote. |
| spend budget | per-run caps x 13 | e.g. the lab lane's Budget-per-run line (30 min, US$0.10) -> 23,400 s = 6.5 wall-hours and US$1.30 across every launch of the lineage, counted or void, root and successors alike |
| R1 predicate | `cum + cap > budget + 1e-9` on either component | the reserve is one full per-run cap; 12 full-cap launches leave 21,600 s and the 13th is permitted (21,600 + 1,800 = 23,400), the 14th refused |
| prefix-invariant tolerance | 1e-9 | every in-progress `llr` must equal the harness recomputation within this and sit strictly inside both boundaries |
| stream class | `lineage` | the `class` field of every pooled stream row |
| Wald's bound on chance keeps | alpha/(1-beta) = 5.6% (exact 4.4%) | against 50.0% for the template rule with its 4-launch cap |
| expected looks | 5.0 at p = 1.0, 7.0 at p = 0.9 | the price of the bound: about five counted looks instead of two for a reliable mechanism |

**Frozen-copy procedure.** `init` performs it; by hand it is, from the plugin root,
`python3 scripts/stopping-rule.py --freeze rules/lineage-sprt.json --into <runs_dir>/<root>/lineage/frozen/rule.json`.
The result must hash to `ce73163a...` (its inner `rule_text` to `8052bda9...`); `--evaluate` reads only
that copy and refuses a missing or tampered one with exits 12 / 13. Successors cite the frozen copy's sha
and never re-freeze.

## The typing rule (R2), in order

Every launched run is typed from its run directory (`grade.json` assertions; the run record's `wall_s`,
`cost_usd`, `cap_s`, `terminal` / `budget_exceeded`; the grader's `manipulation_check`, `control_held`,
`void_class`, `void_triggers` when present). Which assertions are **run-validity** (determinism,
containment, isolation, two-pass, byte-identical, selftest, zero writes, reproducibility, plus the pin and
cost-record clauses a spec files under them) is the spec's SUBSTANTIVE-ASSERTIONS declaration: the
driver passes `--run-validity A5,...`, or the grader marks the entry `"kind": "run-validity"`. Every
other assertion is **substantive**.

| clause | type | when | look? |
|---|---|---|---|
| (i) | VOID `ambiguous` -- reality unclear | a run-validity assertion fails or is unresolved; a substantive result is unresolved (`null`); the record is crash-lost or unparseable; the run ended `budget-exceeded`; a failing substantive assertion's recorded root cause is `harness/run-validity`; the grader named an `ambiguous` void | no; re-taken |
| (ii) | VOID `annulled` -- the question unclear | the known-answer control landed outside its band (`control_held` false, or a grader-named `annulled` trigger), the treatment was not delivered, or the key is contested | no; fixed by a disclosed amendment (frozen span untouched) or a refine successor that continues the SAME stream, ledger and frozen copy |
| (iii) | COUNTED look, refusal 1 | validity and controls hold and at least one failing substantive assertion is `variable-side` -- a contract ambiguity in ANOTHER assertion of the same run does not void it | yes |
| (iv) | VOID `annulled` | validity and controls hold and every failing substantive assertion is `fixture/manifest/contract-side` or `instrument`-side -- typed at `record` / `settle`, or AFTER record by `annul` when a counterfactual regrade shows it (the typing is post-record by construction; "Annulment after record" below) | no |
| (v) | COUNTED look, refusal 0 | everything holds (`deferred` is neither a failure nor unresolved) | yes |
| nl | `not-launched` | a spec-level close with nothing launched (an annulment recorded before any launch -- a contested key, a control outside its band -- is (ii)) | no |

(iii) and (iv) partition every run with a failing substantive assertion by the recorded root cause: one
variable-side entry -> (iii); none -> (iv). A failing assertion the record leaves **unclassed** is never
typed by guess: `type` reads it `ambiguous`, and `record` parks it as **pending** (`pending.jsonl`; R1
refuses `look-pending`) until `settle` supplies a root-cause class for every pending id
(`--root-cause A2=<class>`); a `settle` that leaves an id unclassed refuses (exit 2, one line naming the
pending look and the four classes) and the look stays pending -- the fixture's typing law: an unclassed
root cause is parked pending, never typed by guess, never defaulted to `variable-side`.
A run that ended `budget-exceeded` is reported by that word; it is the `ambiguous` void (charged, no
look, re-taken). Root-cause classes: `variable-side`, `fixture/manifest/contract-side`,
`harness/run-validity`, `instrument`.

## The spend ledger and the lineage files

All under `<runs_dir>/<root>/lineage/` (paths from `.claude/hyp.json`: `runs_dir`, default
`experiments/runs`; `hypotheses_dir`; `ledger_file` is resolved and reported by `paths`, never written).
Rows are canonical JSON (sorted keys, compact separators), one per line, append-only.

| file | rows |
|---|---|
| `frozen/rule.json` | the frozen copy (R0) |
| `spend.jsonl` | header `{"header": true, "lineage", "frozen_rule_sha256", "frozen_copy", "per_run_cap": {"wall_s", "usd"}, "truncation_length", "budget": {"wall_s", "usd"}, "derivation", "opened"}`, then one row per launch `{"spec", "run", "run_record", "cost_usd", "wall_s", "cum_usd", "cum_wall_s", "charged", "class"}` with class `counted`, `void:ambiguous`, `void:annulled` or `pending-root-cause` (+ `charge_source: override` when the driver's `--wall-s` / `--cost-usd` charged it) |
| `stream.jsonl` | the pooled counted looks `{"class": "lineage", "look": k, "refusal": 0|1}` -- what the instrument reads; an **annul row** `{"class": "lineage", "look": k, "annul": 1}` (from `annul`) removes look k from the counted stream, and the instrument is then handed the counted stream with the annulled looks absent (renumbered 1..m in a scratch `stream.jsonl`) |
| `looks.jsonl` | look k -> `{"spec", "run", "run_record", "refusal", "clause", "appended"}` (+ `root_causes` when settled); an annulled look keeps its row and gains a **tombstone** `{"spec", "run", "look": k, "annulled": true, "by": "<path>#<anchor>", "recorded"}` |
| `state.jsonl` | the instrument's state lines, one per stream row, up to the terminal (`look`, `llr`, `n_min`, `rule`, `rule_sha`, `state`, `stream`); an **annul line** re-evaluates the non-annulled looks and carries `look` = the highest look label so far and `annulled` = every annulled label so far (`{"look": 2, "annulled": [1], "llr": -1.6094379124341003, "state": "evidence-insufficient n=1/13", ...}`) |
| `voids.jsonl` | `{"spec", "run", "class", "clause", "run_record", "recorded", "re_take"}` (+ `terminal: budget-exceeded`, + `root_causes`, + `record_changed` from a `settle` whose record files no longer hash as parked); from `annul`: `{"spec", "run", "class": "annulled", "clause": "iv", "annuls_look": k, "root_causes": {"A#": class}, "amendment": "<path>#<anchor>", "counterfactual_sha" (when given), "record_sha256" (every record file as it stands at the annul), "run_record", "recorded", "re_take": "the same card at the next look number ..."}` |
| `pending.jsonl` | `{"spec", "run", "run_dir", "failing", "settled", "recorded", "run_validity_ids", "record_sha256", "how"}` -- `record_sha256` is the sha256 of every record file `record` read; rewritten with `settled: true`, the typing and `root_causes` at settle (+ `record_changed` when the files no longer hash as parked) |
| `refusals.jsonl` | R1 `{"at", "terminal": "budget-exhausted", "cum_usd", "cum_wall_s", "budget", "launches_so_far"}`; a duplicate run `{"at", "reason": "already-recorded", "verb", "spec", "run", "found_in"}` (+ `matched_by: run_dir`, `run_dir`, `recorded_as` when the run directory matched under another number); a duplicate charge `{"at", "reason": "already-charged", "verb": "charge", "spec", "run", "found_in": "spend.jsonl"}`; an annul of a run that is no counted look `{"at", "reason": "not-a-counted-look", "verb": "annul", "spec", "run", "found_in": "voids.jsonl" \| "pending.jsonl" \| null}`; a second annul `{"at", "reason": "already-annulled", "verb": "annul", "spec", "run", "look", "found_in": "looks.jsonl", "by"}` |
| `inherits.json` | R4 pointer on a successor: `{"lineage_root", "via", "frozen_rule_sha256", "recorded", "r4"}` |

The lab lineage's own files under `experiments/runs/H-DRAFT-5810517d-verdict-lineage-stopping/lineage/`
are the reference: `init` with (1800 s, US$0.10) followed by five refusal-0 looks reproduces its
`stream.jsonl` and `state.jsonl` byte for byte (the selftest asserts both hashes).

## The CLI

```
lineage-stopping.py init <lane> --budget-s S --budget-usd U [--inherit ROOT] [--policy P] [--spec-id ID]
lineage-stopping.py paths <lane>
lineage-stopping.py may-launch <lane>                          exit 0 `launch` | exit 3 `budget-exhausted` / `look-pending` / `terminal:<kind>`
lineage-stopping.py type <run-dir> [--run-validity A5,..] [--root-cause A#=<class> ...]
                                                               -> counted | ambiguous | annulled | budget-exceeded
lineage-stopping.py record <lane> <run-dir> [--run N] [--run-validity ..] [--root-cause ..] [--wall-s W --cost-usd C]
lineage-stopping.py charge <lane> --wall-s W --cost-usd C --class CLS [--run N] [--run-record P]
lineage-stopping.py append-look <lane> <0|1> [--run N] [--clause iii|v] [--run-record P]
lineage-stopping.py void <lane> --class ambiguous|annulled [--clause i|ii|iv] [--run N]
lineage-stopping.py settle <lane> <run> --root-cause A#=<class> [...]     one class per pending id and no other; an id left unclassed -> exit 2, the look stays pending
lineage-stopping.py annul <lane> <run> --root-cause A#=<class> [...] --amendment <path>#<anchor> [--counterfactual-sha S]
                                                               R2 (iv) AFTER record, append-only: exit 0 `annulled` | exit 2 `amendment-anchor-missing` /
                                                               `variable-side-stays-counted` / an unclassed or non-failing id | exit 3 `not-a-counted-look` /
                                                               `already-annulled` / `state-stale` / `frozen-rule-tampered`
lineage-stopping.py evaluate <lane>
lineage-stopping.py state <lane>                               -> promote | hold | insufficient | max-looks | spend-exhausted
lineage-stopping.py walk <launches.jsonl> --model A|B [--ratios r,..] [--budget-caps N] --out <stream.jsonl>
lineage-stopping.py --selftest [--into DIR]
```

Every verb takes `--root <repo-root>` (default: the nearest ancestor of the cwd with `.claude/hyp.json` or
`.git`) and `--json`. `record` is the one call a driver makes after grading: it charges, types, appends the
look or the void or parks the pending root cause, and evaluates. `charge`, `append-look` and `void` are the
same steps for a driver that types its runs itself. `state` is the read-back: the instrument's terminal when
one exists, else `spend-exhausted` when R1 would refuse the next launch, else `insufficient` (with `n`,
the llr, spend and remaining budget under `--json`). Exit codes: 0 ok; 1 an invariant or `--check`
violated (named, files left as written -- a failing run is recorded, not repaired); 2 usage, a lineage
not initialised, a `settle` that leaves a pending id unclassed or names an id that is not pending, a
`record` whose files carry no readable `wall_s` / `cost_usd` and no override, or an `annul` whose classes
leave a failing id unclassed, name a non-failing id or a variable-side class or whose amendment anchor is
missing; 3 refused (R1, a pending look, a terminated lineage, an R0 byte or truncation mismatch, a tampered
frozen copy, a run already recorded, parked or charged, an `annul` of a run that is not a counted look or is
already annulled, a state file that is not the stream's own derivation). A `--run-validity` id the record carries no assertion for is warned on
stderr (`warning: --run-validity A7 names no assertion the record carries ...`) and listed in
`meta.run_validity_unknown`; it types nothing.
The instruction field carries the hypothesis-loop words (KEEP / DISCARD / closed without a verdict); the
state lines never do.

### Refusals and guards

Every (spec, run) is charged and typed once, and nothing is written past a check that fails:

| guard | verbs | what happens |
|---|---|---|
| **frozen copy first** | `record`, `append-look`, `void`, `settle`, `annul` | `frozen/rule.json` is verified before any write: a tampered copy exits 3 `frozen-rule-tampered` with the ledger, stream, looks, voids and pending rows untouched (the retry is not `already-recorded`); `evaluate` and the instrument itself (exits 12/13) refuse it too |
| **already-recorded** | `record`, `append-look`, `void` | a run already in `looks.jsonl` or `voids.jsonl` exits 3 `already-recorded <lane> run-N` with one `refusals.jsonl` row; a driver retry after a partial failure appends nothing |
| **parked pending** | `record`, `append-look`, `void` | a run parked in `pending.jsonl` exits 3 `already-recorded ... parked PENDING`, naming `settle <lane> N --root-cause A#=<class>` as the one verb that types it -- a manual verb aimed at a pending run can no longer wedge the lineage |
| **run directory** | `record` | the guard keys on the run DIRECTORY as well as the number: `record run-5 --run 7` then `record run-5` is refused (`matched_by: run_dir`, `recorded_as: 7` in the refusals row); one directory is one look |
| **charged, typed nowhere** | `record` | a run with a `spend.jsonl` row but no look, void or pending row (a crash between R2's charge and its look) exits 3 pointing at `append-look <lane> <0\|1> --run N` / `void <lane> --class .. --run N`, which finish it without a second charge |
| **already-charged** | `charge` | a second `charge` of the same numbered run exits 3 `already-charged` with one refusals row (`reason: already-charged`) |
| **charge readable** | `record` | the charge is the record's `wall_s` and `cost_usd`, or the driver's `--wall-s` / `--cost-usd` (its own clock and meter -- how the lab driver charged; the override wins and the spend row carries `charge_source: override`); a record carrying neither with no override exits 2 naming both flags, and `--wall-s 0` exits 2 too. A crash-lost run is never charged as free, so the spec's rejected reading (c) -- a lineage whose every launch voids re-takes forever -- stays unreachable |
| **settle ids** | `settle` | one class per pending id (an id left unclassed exits 2, the look stays pending) and for no other id (`A9` beside a pending `A2` exits 2) |
| **settle snapshot** | `settle` | the pending row carries `record_sha256` of the record files `record` read; a record edited or lost since then never settles as a look -- it is the `ambiguous` void (R2 (i): not the record that parked the look) with `record_changed` `{file: {parked, now}}` on the void row and the settled pending row, the supplied classes recorded but not applied, the run re-taken |
| **not-a-counted-look** | `annul` | only a run recorded as a COUNTED look in `looks.jsonl` can be annulled: a void, a run parked pending or a run recorded nowhere exits 3 `not-a-counted-look <lane> run-N` with one refusals row (`found_in` names the file, or null) |
| **already-annulled** | `annul` | a second annul of the same run exits 3 `already-annulled <lane> run-N` with one refusals row (`look`, `by`); an erroneous annulment is undone by reverting its pull request, never by a second row |
| **amendment-anchor-missing** | `annul` | `--amendment <path>#<anchor>` must name an existing file whose heading, lowercased with spaces as hyphens, is the anchor or starts with it followed by a hyphen (`amendment-2` matches a line starting `## Amendment 2`, never `## Amendment 20`); a missing file or heading exits 2 and nothing is written |
| **variable-side-stays-counted** | `annul` | every failing substantive id of the look (read from the record the look row points at, through the reader `record` uses) must receive exactly one class from `fixture/manifest/contract-side` \| `instrument`; a `variable-side` class exits 2 (typing iii keeps the look counted), as do a `harness/run-validity` class (typing i), an unclassed failing id and a class for a non-failing id; a refusal-0 look has no failing id and cannot be annulled by this verb |
| **state-stale** | `annul` | `state.jsonl` must be the stream's own derivation before a line is appended to it (a stream row ahead of its state line refuses, exit 3, pointing at `evaluate`); nothing is written |

Unguarded by design: a `charge`, `append-look` or `void` given no `--run` number is unidentified (and
`record` of a directory recorded nowhere under no number likewise).

## Annulment after record

Typing (iv) is **post-record by construction**: it is shown by a counterfactual regrade of a run that has
already been recorded -- a grader's default label counted the look, a cold verifier later settled the
failing assertion instrument- or fixture/manifest/contract-side with the variable bytes unchanged -- so no
verb that runs at record time can carry it, and `record` is guarded once per run, `settle` refuses a run
that is not pending, `void` refuses a recorded run and `may-launch` refuses at a terminal. `annul` is the
verb that carries it, and it is append-only:

```
lineage-stopping.py annul <lane> <run> --root-cause A#=<class> [A#=<class> ...] --amendment <path>#<anchor> [--counterfactual-sha <sha256>]
```

In order: the frozen copy is verified first (as `record` does); the run must be a recorded COUNTED look in
`looks.jsonl` (`not-a-counted-look`, `already-annulled` otherwise, one refusals row each); every failing
substantive id of that run -- read from the record the look row points at, through the reader `record`
uses -- receives exactly one class, `fixture/manifest/contract-side` or `instrument` (a variable-side class
exits 2: the look stays counted, typing iii); the amendment file must exist and carry the anchor as a
heading. Then, each appended and nothing rewritten: one `voids.jsonl` row (`class annulled`, `clause iv`,
`annuls_look k`, the classes, the amendment pointer, the counterfactual's sha when given, the record files'
sha256 as they stand, `re_take`); one `looks.jsonl` tombstone `{spec, run, look k, annulled true, by,
recorded}`; one `stream.jsonl` row `{"class": "lineage", "look": k, "annul": 1}`; one `state.jsonl` line
re-evaluated over the non-annulled looks, labelled with the highest look label so far and carrying
`annulled: [k, ...]`. `spend.jsonl` is untouched: the run stays charged (a void is charged and never
counted).

**How the stream is read afterwards.** Everywhere the stream is read -- `state`, `evaluate`, `may-launch`,
the checks, a successor's `--inherit` -- an annulled look is absent from the counted stream. The kept
evaluator (`stopping-rule.py`, unchanged) is handed the counted stream with the annulled looks absent,
renumbered 1..m in a scratch `stream.jsonl`, so the SPRT llr after an annul equals, to the bit, the llr of
a stream that never held that look, and the appended state line is exactly the evaluator's last line for
that stream (with `look` and `annulled` added). `state.jsonl` is the replay of the event log `stream.jsonl`
-- one line per stream row, a counted row's line evaluated over the stream as it stood, an annul row's line
over the stream with the look absent -- so `evaluate` re-derives it byte-for-byte without rewriting
history; the recorded terminal line stays where it was written, superseded by the annul line after it.
`may-launch` reads the LAST state line: a terminal the annul withdraws admits a launch again (budget
permitting, no look pending); an annul that leaves the filtered stream terminal (or completes one -- five
straight passes once a refusal between them is annulled promote) leaves the lineage closed. The checks
on a stream with annul rows are the annul-aware invariants -- `line-count` (one line per stream row),
`looks-consecutive` over the launch labels including tombstoned ones (labels 1..n with no gap; an annul
line carries the highest label), `rule-sha-mismatch`, `tombstones` (an annul line's `annulled` is the set
so far), `terminal-mid-file` (a terminal line may be followed only by an annul line, the re-evaluation that
supersedes it), `state-text` and `llr-recompute` over the filtered stream, `vocabulary` -- and, at a
terminal, the instrument's own `--check` over the filtered stream's lines.

**Labels and the re-take.** `record` after an annul assigns the next look label max existing label + 1 --
a tombstoned label is never re-used, and the state text `n=k/13` counts the non-annulled looks, so a line
`{"look": 3, "state": "evidence-insufficient n=2/13"}` is the normal shape after one annul. The instrument
knows no cards: the lane rotation re-takes the annulled card at that next look label (the lab's ruling
R10 (c): "the re-take of DEC-006 takes the next look number on the annulled card; the rotation resumes
from that card's position"). A refine successor (`init <successor> --inherit <root>`) resolves to the
root's files, so the tombstones and annul rows are carried unchanged and it reads the same state.

**Undoing an annulment.** An erroneous annulment is undone by reverting the pull request (or commit) that
landed it, never by editing or deleting a row: the ledger is append-only, a second `annul` of the same run
is refused `already-annulled`, and the stream, looks, voids and state files after a revert are byte-for-byte
the files before the annul. Source ruling: cause-n-effect `H-DRAFT-2ad5a02e-decision-brief-comprehension-v2`,
AMENDMENTS.md Amendment 2 (R7-R10), PR 47 (merge a07bb36e6), journal fragment 0509 -- look 1 of that
lineage was counted as a refusal on a grader matcher defect (five readers who named `parks` and `blocked`
were counted as naming the glossed word `lane`), the cold verifier's counterfactual regrade with the
variable bytes unchanged passed the assertion, and the instrument had no verb to record the correction.

## How a lane adopts the rule

1. **Spec.** The Verdict rule says the lineage stops by this rule and by nothing else, names the
   lineage files under `<runs_dir>/<id>/lineage/`, declares which assertions are run-validity and which
   controls are known-answer, and carries no launch count (the template's Verdict-rule comment has the
   sentence). The Budget-per-run line already exists (directive: every run's cost and wall recorded); the
   lineage total is derived from it, introducing no new number. Raising a budget is a disclosed resource
   amendment, never a decision card.
2. **Register (R0).** `lineage-stopping.py init <id> --budget-s <per-run wall cap in s> --budget-usd <per-run US$ cap>`
   from the repository root, once, at the root spec. Commit the lineage directory with the registration.
3. **Every launch.** `may-launch <id>` (exit 0) -> run and grade into `<runs_dir>/<id>/run-N/` ->
   `record <id> <runs_dir>/<id>/run-N --run-validity A5` -> read the instruction. A pending root cause is
   settled by whoever records it (the cold verifier, usually): `settle <id> N --root-cause A2=<class>`,
   one class per failing id (`settle` refuses without one; the look stays pending). Leave the run directory
   untouched between `record` and `settle`: `settle` re-hashes the record files against what `record`
   parked, and a changed record settles as the `ambiguous` void, never a look. A run whose child died before
   writing its record (no `wall_s` / `cost_usd`) is recorded with the driver's own clock and meter,
   `record ... --wall-s <s> --cost-usd <US$>`; without them `record` refuses rather than charge the launch
   as free. `record` is safe to retry: an already-recorded run is refused, never charged or counted twice.
   A look that was counted on a root-cause class a cold verifier later settles instrument- or
   fixture/manifest/contract-side is re-typed after the fact -- commit the disclosed amendment first, then
   `annul <id> N --root-cause A#=<class> --amendment <runs_dir>/<id>/AMENDMENTS.md#<anchor>` ("Annulment
   after record" above); the next `record` takes the next look label and the rotation re-takes that card.
   Declare the lineage files as writes for the lane's containment instrument.
4. **Refine.** A successor with a new id runs `init <successor> --inherit <root>` and continues with the
   same verbs; its runs are charged to the root's ledger and its looks pool into the root's stream.
5. **Close.** `state <id>` reads `promote` (KEEP the current spec), `hold` (DISCARD, exclusion banked),
   `max-looks` or `spend-exhausted` (closed without a verdict on the recorded evidence). Journal the
   close; the spec's Status follows the instruction.

## What "no launch count" means

There is no number anywhere in this rule that says how many times a lineage may launch. The lineage ends
when the frozen instrument reads a terminal over the pooled counted looks, or when the derived spend budget
would be exceeded by one more per-run cap. Consequences: a void (a harness defect, a crash, a contested
key) costs its recorded spend and nothing else -- it burns no look and consumes no cap; nothing resets at
a refine, so a successor cannot buy fresh launches by renaming; a spec never carries a blank N and no
decision card asks for one ("I will not sit here and tell you how many tests to run", the 2026-09-10
ruling). `max_looks` is not a hidden cap: it is Wald's truncation, derived from the four policy numbers,
reached only by a mechanism in the indifference zone, and read as `evidence-insufficient`, never a
promote. The preflight-rigor `LINEAGE-CAP` row, once a depth-3 `lineage-decision:` count request, now
reads this rule first: at depth >= 3 it resolves the lineage root (the successor's `inherits.json` chain,
then the `refined-into:` root, then the spec's own run directory), verifies the frozen copy against the
policy sha and reports the stream and spend ledger (`lineage-rule:<state> looks= voids= spend=`), reads
`FAIL lineage-rule:tampered` on a frozen copy that does not hash to the policy, and falls back to the
`lineage-decision:` path only for lineages with no lineage directory (`docs/preflight-rigor.md`, "Row 8
under the lineage stopping rule"). The row stays report-only.

## Evidence (lab H-DRAFT-5810517d-verdict-lineage-stopping, kept 2026-09-11)

Zero-LLM, script-only fixture: 14 seeded classes (pass rate p in {0.5, 0.7, 0.9, 0.99} x void rate v in
{0, 0.25, 0.5}, plus p = 1.0 and p = 0.0 controls) x 1000 lineages x 73 launches, both arms over identical
launches; the ON arm under two charge models (A: every launch charges its full cap, funding 13 launches;
B: the recorded wall/cap ratios of seven real runs, funding 73); a replay of the four recorded lineages
from their committed `VERDICT.json` files; a cold verifier recomputing 99 checks per pass, two passes
identical.

- **A1 null bound, with a control that had to fail:** ON promoted 42 / 38 / 25 of 1000 coin-flip lineages
  (bound 70; Wald 5.56%) at v = 0 / 0.25 / 0.5; the template rule with its 4-launch cap promoted 484 (band
  [445, 555]; exact 500).
- **A2 effect bound and truncation:** ON held 48 / 57 / 43 of 1000 at p = 0.9 (bound 80), promoted 889 at
  v = 0 (bound 850), read `max-looks` 63 times (bound 100), every one at look 13; p = 1.0 promoted at look 5
  and p = 0.0 held at look 2, 1000/1000 each.
- **A3 voids never burn evidence:** 0 of 14,000 lineages closed on a void, 0 over budget, under either
  charge model; the OFF rule ended 408 of the (0.9, 0.5) lineages and 201 coin-flip lineages on its cap with
  9 launches of spend remaining and no verdict; under charge model B 0 lineages closed spend-exhausted.
- **A4 replay of the four recorded lineages:** typed run by run against the pinned records with 0 count
  requests emitted, against exactly 3 recorded (DEC-018, DEC-034, DEC-035); the one `hold` agrees in
  direction with its recorded close.
- **A5 determinism and lane-scoped containment**, every run.
- **The lane's own lineage:** run-1 voided `ambiguous` on its own harness's containment bookkeeping
  (re-taken after a disclosed one-line amendment, never counted; 950.1 s charged); runs 2-6 read 5/5 each,
  four of them by cold README-only executors; the frozen SPRT walked llr 0.5878 -> 1.1756 -> 1.7634 ->
  2.3511 -> 2.9389 and read `evidence-sufficient promote` at look 5, the shortest path; spend 5,753.6 s of
  23,400 s and US$0 of US$1.30; R1 permitted every launch. Under the template rule the void would have
  consumed one of four launches.

Caveat the verifier carried: the five counted looks were five byte-identical re-executions of a
deterministic fixture -- what the five looks independently tested was run validity under five host states;
the claim-class split (deterministic vs stochastic looks) is a named future hypothesis. The keep excludes
the opposite reading, that lineages need a launch-count cap to terminate or to stay honest, and changes no
recorded Status.

## Relation to the observation stopping rule

`scripts/stopping-rule.py` is unchanged and is the only thing that reads evidence here: this script writes
the pooled stream it reads and reads its terminal back (after an annul it is handed the counted stream with
the annulled looks absent, renumbered in a scratch file; the annul-aware invariants are this script's). The observation rule decides one lane's observation
stream; the lineage rule decides a whole lineage from its counted looks. Both are frozen at the gate, both
speak only `evidence-sufficient` / `evidence-insufficient`, and both refuse to run on a missing or tampered
frozen copy.

## Known limitations (from the refute)

Found by the adversarial reviews of the port. Round 1's A2 and A3 and round 2's A13-A16 and NIT-1..NIT-4
are fixed ("Refusals and guards" above; every fix has a selftest case); these remain as they are:

- **A4** -- `may-launch` and `state` answer from `state.jsonl` and `spend.jsonl` without re-verifying the
  frozen copy; a tampered `frozen/rule.json` is refused before any write by `record`, `append-look`, `void`,
  `settle` and `annul` (exit 3 `frozen-rule-tampered`), by `evaluate`, and by the instrument itself (exits
  12/13), not by those two read verbs.
- **Labels past a re-evaluated terminal** -- an annul can move the filtered stream's terminal to an earlier
  counted look (seven looks PPPPFPP with the F annulled promote at the fifth pass); the counted looks recorded
  after that position stay in `looks.jsonl` and `stream.jsonl`, charged and counted in `state --json`'s
  `looks`, but the evaluator stops at the terminal and never reads them. The lineage is closed at that line;
  nothing rewrites the labels.
- **A5** -- R0's byte check of the lineage's frozen copy against `rules/frozen/lineage-sprt.json` is skipped
  silently when that reference file is absent from an install; the inner-rule sha check and the
  truncation derivation still run, so the docs' "byte-identical, refused otherwise" holds only with the
  reference present.
- **A18** -- `init --policy <path>` skips that reference byte check by design (a policy other than the
  shipped one is a lab decision): a whitespace-only rewrite of the same policy is accepted, the lineage's
  `frozen_rule_sha256` then differs from `8052bda9...`, and only the spend header records it; the inner-sha
  and truncation checks still run.
- **A6** -- an `init` refused after the freeze (an R0 byte mismatch) leaves a stray `lineage/frozen/rule.json`
  behind with no ledgers opened; a later `init` recovers (the freeze overwrites, the header is opened). The
  kind and truncation checks run before the freeze, so those refusals create nothing.
- **A7** -- every refused `may-launch` appends one `refusals.jsonl` row (parity with the lab fixture's
  `r1_check`); a polling driver accumulates rows.
- **A9** -- `record <lane> <run-dir>` resolves a relative run directory against the current working
  directory, not `--root`; drivers should pass absolute run directories.
- **A11** -- `charge --class` accepts any string; the four classes this script writes (`counted`,
  `void:ambiguous`, `void:annulled`, `pending-root-cause`) are not enforced on that verb.
- **Unidentified runs** -- a `charge`, `append-look` or `void` given no `--run` is not guarded (the residue
  of NIT-3), and a pending row written before `record_sha256` existed settles by re-reading its run
  directory as before.
