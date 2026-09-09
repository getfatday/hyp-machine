# Preflight rigor: report-only rows beside the gate

> **REPORT-ONLY BY DESIGN.** `scripts/preflight-rigor.py` prints findings and always exits
> 0 — it gates nothing. Two row families ride on it: the six **ethics rows** (H-132) and the
> thirteen **spec-rigor rows** (H-DRAFT-50b0c1da-spec-rigor-rows-v2). Any enforcement flip
> — a required section in `scripts/preflight.py`, a FAIL row turned into ESCALATE — is
> **maintainer-gated**, decided per row on a recorded fire rate over a named population, applies
> to specs registered after the flip commit only, and never re-gates pre-existing specs. Do not
> wire this script into a blocking path.

## Usage

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preflight-rigor.py" <repo-root> hypotheses/H-*.md   # rows per spec
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preflight-rigor.py" --census <repo-root>            # every spec + CENSUS fire counts
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preflight-rigor.py" --selftest                      # PASS/FAIL per check; exit 0 when all pass
```

Output: byte-stable `STATUS<TAB>check<TAB>relpath<TAB>detail` rows (PASS/FAIL/SKIP) — per spec
the six ethics rows, then the thirteen rigor rows, then one `META` line (the ethics keys `signals`,
`section_present`, `subjects`, `route` as before, plus a `rigor` block with `fails`, `skips`, `id`
and, when computed, `lineage_depth`, `frozen_span_sha` / `no_frozen_span`). The six ethics rows are
byte-identical to what the script printed before the rigor rows landed; the rigor rows are appended.
Exit 0 always; findings never change the exit code. Run it alongside — not inside — the shipped
`scripts/preflight.py` (the 8-check deterministic gate), whose PASS/ESCALATE/MALFORMED semantics
are untouched. `--census` evaluates every `<hypotheses_dir>/H-*.md` and appends one
`CENSUS<TAB>row<TAB>FAIL=n<TAB>PASS=n<TAB>SKIP=n<TAB>n=N` line per row: the measured fire rate a
flip decision is made on.

Repository layout comes from `<repo-root>/.claude/hyp.json` when present (`hypotheses_dir`,
`runs_dir`, `raw_dir`, `model_dir`, `ledger_file`) and defaults to the lab layout (`hypotheses`,
`experiments/runs`, `research/raw`, `operating-model`). Decision rows are read from the configured
`ledger_file` (plugin default `ledger/ledger.jsonl`) **and** from `ledger/work-ledger.jsonl`, the
ledger the counted contract names.

## The thirteen spec-rigor rows

Every row is one PASS/FAIL/SKIP line per spec; a slashed name is one row with two legs (the detail
names each leg) and the row FAILs when any leg fails. SKIP owns absence — no trigger, no artifact —
so each defect class trips exactly one row. Nothing here changes the exit code; all thirteen rows
are report-only. `<id>` is `H-NNN` when the spec stem matches `^H-[0-9]+`, else the full stem (the
keep-ship-gate id rule); the run directory is `<runs_dir>/<id>/`.

| # | Row | What it checks (FAIL when) | SKIP when | Evidence class | Report-only |
|---|---|---|---|---|---|
| 1 | `DISCARD-BANKS/NULL-CLASS` | Spec leg: no sentence in the Verdict-rule span matches `discard (driven by\|on\|where\|when) assertion(s) <ids> ... banks` and no `Discard banks:` keyed line names an assertion id present in the assertions span. Run leg: `VERDICT.json` with verdict `discard` lacks `null_class` in {refuted-claim, threshold-miss, instrument, ambiguous, annulled} (threshold-miss also needs `observed` and `bar`) | run leg only: no `VERDICT.json`, or verdict not discard | advisory (6 blind mutants, min 5) | yes |
| 2 | `CONTROL-PRESENT/OFF-FAILS` | No numbered assertion binds an OFF-arm token (OFF, baseline, control, arm-B, unpatched, shipped) to a comparator and a number on that arm — a bare "ON exceeds OFF" does not count — AND the numbered assertions declare no positive control or no negative control (the "declares a control" definition below). Method mentions are reported in the detail (`method-mentions:pos=,neg=`) and never counted | never | advisory (6, min 5) — repaired detector | yes |
| 3 | `TREATMENT-DELIVERED` | Spec leg: no `Manipulation check:` keyed line in Method and no numbered assertion carrying premise, pre-check, manipulation check, marker or treatment delivered. Run leg: a graded run directory (`grade.json` present) whose `run.json` lacks `treatment_delivered: true` | run leg only: no run directory | advisory (6, min 5) | yes |
| 4 | `FROZEN-SPAN-SHA` | `<runs_dir>/<id>/frozen/span.sha` exists and differs from the sha256 of the current assertion span (from the `## Binary assertions` heading line through the `## Verdict rule` heading line inclusive), and no `Amendment N` record in `AMENDMENTS.md` carries the keyed lines `motivation:`, `results-influence:` and `prior-runs:` in {re-graded, voided, unchanged} | no `span.sha` (detail `no-frozen-span`; counted in META, never a FAIL) | hard candidate (11, min 10); false-positive bound asserted | yes |
| 5 | `DIRECTIVE-INTERPRETATION/SOURCE-LINE` | The spec cites a directive (a `research/raw/` path, or the word directive or ruling) and either carries no fenced `interpretation-set` block with `source:`, `verbatim:`, >= 2 numbered readings and `chosen:`, or no `research/raw/<file>.md:<L>-<L>` citation whose file exists and whose range lies within its line count; a DESIGN-doc-only source adds detail `design-doc-only` | no directive trigger | advisory (8, min 5) | yes |
| 6 | `STAGE1-REVIEWED` | Claim type normative or the directive trigger, and no `reviewed-by:` keyed line whose value resolves to a decision (definition below) or names a committed file under `<runs_dir>/<id>/stage1/` | neither trigger | advisory (7, min 5) | yes |
| 7 | `REFUTE-REVIEW` | `SHIP.md` exists (or `VERDICT.json` `files_changed_in_on` names a plugin-shipped path by keep-ship-gate's rule) and `REFUTE.md` is absent, lacks `blocking-findings: 0`, lacks `refuter-session:`, or its refuter equals a recorded builder or executor id (`SHIP.md`, `fixture/README.md`, `run.json`) | no `SHIP.md` and no plugin-shipped change | advisory (6, min 5) | yes |
| 8 | `LINEAGE-CAP` | The spec has >= 3 ancestors by `refined-into:` pointers in the Status blocks of the repository's specs and no `lineage-decision:` keyed line that resolves to a decision (definition below); the detail carries depth, root id and the root-vs-current assertion-span sha pair | depth < 3 (detail `cap-not-reached`) | hard candidate (10, min 10); false-positive bound asserted — repaired detector | yes |
| 9 | `VOID-CLASS` | Spec leg: the Verdict-rule span names a void class (void, uncounted, not counted) and does not type it with both `ambiguous` and `annulled`. Run leg: a void record (definition below) lacks `void_class` in {ambiguous, annulled} | no void clause and no void record | advisory (6, min 5) — repaired detector | yes |
| 10 | `WHO-CARES` | The `- **What "keep" means:**` line is absent, names no actor id (a filename under `<model_dir>/*/actors/*.md`), or carries no magnitude token (a number followed by %, x, /n, s, ms, or the word today) | never | advisory (6, min 5) | yes |
| 11 | `PREMORTEM-PRESENT` | Method carries no `Pre-mortem:` keyed list with >= 2 items each mapped `-> A<n>`, `-> void:<class>` or `-> banks` | never | advisory (6, min 5) | yes |
| 12 | `BLIND-SEEDS` | Method mentions seeded, mutant or planted and `<runs_dir>/<id>/fixture/` exists without a `README.md` `seed-author:` keyed line (same-line value; empty is absent) whose session id differs from the recorded `row-author:` id | no seeded/mutant mention, or no fixture directory yet | advisory (6, min 5) — repaired detector | yes |
| 13 | `SUBSTANTIVE-ASSERTIONS` | Fewer than 3 numbered assertions are substantive, where an assertion is run-validity iff its label (text before the first colon within 45 characters) is one of determinism, grading determinism, containment, isolation, isolation and scope, two-pass, byte-identical, selftest, zero writes, reproducibility, or its whole text is a two-pass, byte-identical or zero-writes statement | fewer than 3 assertions (MALFORMED belongs to preflight) | advisory (6, min 5) | yes |

"Evidence class" is the class the counted lane assigned: *advisory* rows are proposed to stay
report-only and their fire rate is recorded as the flip input; the two *hard candidates* are the
rows proposed for a later ESCALATE flip, so the lane also asserted their false-positive bound (<= 4
fires on the 80 modern kept specs). "Repaired detector" marks the four rows whose v1 detectors
missed blind mutants and were repaired at named sites before the counted runs; their 100% is a
repair verification against known mutants, not blind detection, and their flip cards carry that
disclosure. Nothing in this table is enforced; the "Report-only" column is the same for every row.

## The shared definitions (frozen with the counted lane; quoted)

The row contracts use three terms whose definitions the lane froze in its spec's Method
(`hypotheses/H-DRAFT-50b0c1da-spec-rigor-rows-v2.md`) and recorded as executable repairs in its
`fixture/DECLARATION.md`. They apply wherever a contract above uses the term:

- **Keyed line.** "`<key>:` at the start of a line (an optional list marker and optional bold
  allowed), whose value is the text after the colon on the SAME line, stripped; an empty value is
  an absent line." (The v1 regexes used `:\s*` after the colon, so an empty value swallowed the
  newline and captured the next line; the repair is `:[ \t]*` at every keyed site — `reviewed-by:`,
  `lineage-decision:`, `seed-author:` / `row-author:` / `refuter-session:`, `Discard banks:`,
  `prior-runs:`.)
- **Resolves to a decision.** "the stripped keyed value is exactly one `DEC-NNN` id (a full
  match — no prose around it, no prefix or suffix) that is the `id` of a `kind:decision` row joined
  by a `kind:decision-resolution` row with the same id in `ledger/work-ledger.jsonl`. Applies to
  `lineage-decision:` (row 8) and to the ledger alternative of `reviewed-by:` (row 6); row 6's
  stage1-file alternative is unchanged." (So `see DEC-901` and `DEC-901-draft` do not resolve, and
  a decision row without its resolution row does not resolve. In this plugin the ledger is the
  configured `ledger_file` plus `ledger/work-ledger.jsonl`.)
- **Void record.** "a top-level `VERDICT.json` or `grade.json` of the run directory carrying any of
  `verdict: "void"`, a `terminal` string beginning `void`, `voided: true`, `void: true`, or a
  `void_class` key with any value. The lab's keep-with-voided-runs shapes (`void_runs`,
  `void_reasons`, `void: null`) are not void records." Consequence worth knowing before the rows
  run over your run directories: a non-void `VERDICT.json` that carries `void_class: null` **is** a
  void record under this definition and reads FAIL on row 9's run leg — omit the key on non-void
  verdicts and canonicalise void strings to `ambiguous` | `annulled` (the lane's verifier routed
  this as a maintainer/lander ruling; the shipped detector reads the definition as frozen).

Row 2's contract carries a fourth definition the same repair round made explicit — **declares a
control**: "a numbered assertion (wrapped lines folded, comments stripped) carries a
positive-control token (seeded, mutant, mutants, planted, sabotaged) or a negative-control token
(clean corpus, must-silent, zero findings, control range); Method prose never declares a control,
and the row's detail reports Method mentions for the record only."

## Recorded fire rates (the flip input)

Measured by the lane at the pinned lab snapshot `09e7d11e` (369 specs; the modern kept population
is the 80 specs first added on or after 2026-09-01 whose Status word is kept/keep). FAIL/PASS/SKIP
counts with the one-sided 95% Clopper-Pearson upper bound on the FAIL rate. A row that fires on
most of the historical corpus describes a practice the corpus predates; that is why every row
ships report-only and the flip waits on a forward population (specs registered after the rows
land), measured with `--census`.

| Row | modern kept (n=80) FAIL/PASS/SKIP | CP95 upper | all specs (n=369) FAIL/PASS/SKIP | CP95 upper |
|---|---|---|---|---|
| `DISCARD-BANKS/NULL-CLASS` | 80/0/0 | 1.000 | 366/3/0 | 0.998 |
| `CONTROL-PRESENT/OFF-FAILS` | 53/27/0 | 0.750 | 271/98/0 | 0.772 |
| `TREATMENT-DELIVERED` | 75/5/0 | 0.975 | 347/22/0 | 0.959 |
| `FROZEN-SPAN-SHA` | 0/0/80 (SKIP-only: no spec carried `frozen/span.sha`) | 0.037 | 0/0/369 | 0.008 |
| `DIRECTIVE-INTERPRETATION/SOURCE-LINE` | 69/0/11 | 0.921 | 338/7/24 | 0.939 |
| `STAGE1-REVIEWED` | 75/0/5 | 0.975 | 353/0/16 | 0.973 |
| `REFUTE-REVIEW` | 22/0/58 | 0.369 | 22/0/347 | 0.084 |
| `LINEAGE-CAP` | 1/0/79 | 0.058 | 14/0/355 | 0.059 |
| `VOID-CLASS` | 11/0/69 | 0.217 | 23/8/338 | 0.087 |
| `WHO-CARES` | 80/0/0 | 1.000 | 368/1/0 | 1.000 |
| `PREMORTEM-PRESENT` | 80/0/0 | 1.000 | 368/1/0 | 1.000 |
| `BLIND-SEEDS` | 51/0/29 | 0.727 | 156/0/213 | 0.467 |
| `SUBSTANTIVE-ASSERTIONS` | 0/80/0 | 0.037 | 3/366/0 | 0.021 |

## Selftest

`--selftest` rebuilds the counted fixture's seed root (three clean seeds, their lineage ancestors,
ledger, actor nodes, raw source and run artifacts, byte for byte) and checks, printing `PASS` /
`FAIL` per check: the three clean seeds reproduce the lane's recorded 39 row lines exactly (0 FAIL
rows — the must-silent negative control); one seeded positive per blind-manifest class (53 mutants
across the thirteen rows, including the eight repaired-detector classes) turns exactly its intended
row to FAIL while the six ethics rows stay byte-identical between mutant and seed (the assay-
sensitivity control); five alternative clean paths (an amended frozen span, a `stage1/` file, a
typed void record, a typed discard, a two-assertion spec) read PASS or SKIP; the lane's sentinel
fires all thirteen rows; and the exit code is 0 with 0 FAIL rows and with 13 FAIL rows alike,
`--census` is byte-identical across two runs, the grammar is 6 + 13 rows + META per spec, and the
usage error still exits 2.

## The ethics rows (H-132)

The gap they close: a lab that runs simulated humans and prepares real-person invites with no named
ethical-assumption check anywhere in its preflight. Five contract checks, rendered as six report
rows (the tier cross-check is its own row), over each hypothesis spec:

| Row | Fires when |
|---|---|
| `ethics-section-present` | Subject signals hit in Hypothesis+Method AND `## Ethical assumptions` is absent. A sectionless spec with no signal hits PASSes — pre-existing specs are never re-gated. When the section is absent, the other five rows report SKIP so this class trips exactly one check. |
| `ethics-declared` | Signals hit but the `subjects:` line is missing, placeholder, or a bare `none` — the calibrated over-trigger escape is one clause: `none — <reason>`. |
| `ethics-nonempty` | Subjects are declared but any of the `consent` / `data` / `withdrawal` / `deception` keyed lines is missing or empty after comment strip. |
| `ethics-consent-artifact` | A real-human subject is named and the `consent:` line resolves no committed artifact path. |
| `ethics-tier-mismatch` | Method names a real-human interaction (invite / second-human / real human) while `subjects:` declares neither `real-human` nor `none — <reason>`. |
| `ethics-sim-dignity` | A sim-persona subject lacks a `sim-dignity:` line, or a transcript-grounded persona's consent line resolves no committed path (grounded cards inherit the source humans' consent surface). |

The spec section they read:

```
## Ethical assumptions

subjects: <who is affected — or `none — <reason>` when subject-signal words over-trigger>
consent: <the committed consent artifact path, when a real human participates>
data: <what is collected/retained>
withdrawal: <how a subject exits>
deception: <any, and its debrief>
sim-dignity: <for sim-persona subjects: the dignity constraints the simulation honors>
```

## Evidence

**H-DRAFT-50b0c1da-spec-rigor-rows-v2** (source lab, kept 2026-09-09, 5/5 in two consecutive
counted runs, the second by a cold executor working from the fixture README alone; journal
fragments 0465 and 0469): each of the thirteen rows reported FAIL on 100% of its blind-authored
single-defect mutants (90 admissible over three clean seeds, authored by two sessions that never
saw a detector), while the pinned preflight and the ethics rows printed 0 differing lines between
every mutant and its seed and 0 FAIL rows on the clean seeds; every must-FAIL / must-PASS /
must-SKIP pair of the 43-pair historical incident key matched at the pinned snapshot; the two hard
candidates fired on <= 4 of the 80 modern kept specs; two passes were byte-identical. The shipped
detectors are the counted fixture's `rigor_rows.py` bytes with the harness sabotage hook removed
and the repository paths resolved through `.claude/hyp.json`; the port was verified by replaying
all 90 mutants (90/90 intended-row FAIL, 90/90 thirteen-row vectors byte-identical to the counted
run's raw output) and by a census over the lab tree byte-identical to the lane's.

**H-132-ethics-gate** (source lab, kept 2026-08-27, two consecutive counted 4/4, scripts-only,
~$0): every seeded human-subject defect spec tripped exactly its intended check with zero seeds
missed and zero cross-fires; zero ethics FAILs across the frozen must-silent corpus classification
(the over-trigger cost capped at the one `none — <reason>` clause); the four retrofitted active
specs passed all checks with diffs confined to the inserted section; the full-corpus double pass
was byte-identical.

The enforcement flip of any row and any retrofit of a consumer's pre-existing specs remain
maintainer decisions, out of this plugin's hands.
