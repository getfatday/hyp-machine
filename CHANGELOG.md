# Changelog

Newest first. This file is written by `scripts/release.py` from the pending
`.changeset/*.md` files on every push to main; do not edit it by hand (see
`.changeset/README.md`).

## 0.26.0 (2026-09-12)

### Added

- Decision cards now need a plain-English brief before they can be filed. A decision card is a question the plugin writes into your ledger for a person to answer; from this release `decisions.py add` refuses a card that does not come with a brief (`--brief brief.json`): seven short fields saying what is being decided, the situation, why it is the reader's call, the choices with what each one does and how it is undone, what happens if nobody answers, and what is known. A script checks the brief before anything is written: a missing or malformed brief is refused with three lines pointing at the schema (`docs/decision-brief.schema.json`), the draft command (`decisions.py brief-skeleton`) and a worked example (`docs/decision-brief.exemplar.json`); a brief with prose problems is filed, with the findings stamped on the card. Every surface that shows a card (`decisions.py show`, the compiled dashboard, the session-start resolver) now shows the brief first, and a card that reached the ledger without one renders as a NOT READY block rather than as a question, with any pre-committed default suspended until the card is readable. Cards already on file are exempt: set `decision_brief_legacy_max_id` in `.claude/hyp.json` to your highest existing card id, and each open older card renders exactly as before plus one `brief: BRIEF-MISSING` line naming the retrofit command (`decisions.py brief <id> --brief brief.json`) until it is re-briefed; resolved cards render unchanged. Details in `docs/decisions.md` ("The plain-English brief") and `docs/upgrading.md`; `python3 scripts/selftest-decision-briefs.py --live .` checks the rendering over your own ledger. Why: the cause-n-effect lab kept hypothesis H-DRAFT-1c840b86 on 2026-09-12 (five counted looks, every assertion passing; its VERDICT.json is in that repository's run directory for the hypothesis) and landed the same change in its own checkout first (cause-n-effect PR #38, merge bbfc84fe59ce69b617f4d08a5f4b7c0e6076cf02). To undo: revert this pull request's merge commit; the brief is the only new requirement, so nothing else needs to change. (decision-briefs.md)

## 0.25.0 (2026-09-12)

### Added

- Generated case-study pages now explain the lab's coined terms by default. `scripts/render-case-study.py` writes a one-page, plain-language case study for each experiment whose result passed its pre-declared checks; until now that page explained only eight long-standing terms, and any other coined word stayed bare unless whoever ran the renderer named a vocabulary file on the command line. From this version, when the bundle of files behind a page carries notes beyond its nine fixed record files and those notes use a coined term the page also uses bare, the page's first use of that term gains the term's listed explanation in brackets, copied word for word from the shipped `scripts/house-vocabulary.json`; a bundle of only the nine record files renders exactly as before, byte for byte. To make that work, the everyday-word list the renderer needs, `scripts/vocab-wordlist.txt`, now ships beside the vocabulary (the render refuses and writes nothing when either file is missing or unreadable); eight vocabulary explanations that carried digits or parentheses, which the page's own rule refuses to insert, are reworded with their meaning kept; "counted runs" is added as a variant of "counted run" so the renderer recognises the page's existing explanation of that phrase instead of refusing the page; and the renderer's row in `docs/observatory.md` describes the new default. Why: this records decision DEC-037 of the source lab (2026-09-11), a reversible decision the lab recorded itself under the maintainer's grant of 2026-08-18, on the evidence of hypothesis H-225, kept 2026-08-31 on two complete runs passing all five checks (the explained page reproduces the reference page byte for byte, explains a seeded coined term at its first use in the vocabulary's own words, and reports an unregistered word rather than inventing an explanation), and it follows the maintainer's plain-English directives of 2026-08-28 and 2026-09-11. To opt out, pass `--no-vocab` for the bare page, byte-identical to the previous default, or `--vocab PATH` to explain from a different vocabulary file (its word list must sit beside it). To revert, revert this change and let CI release it as a patch; on the lab side, vetoing DEC-037 before its window closes on 2026-09-18 reverts the lab commit. (case-study-vocab-default.md)

## 0.24.3 (2026-09-11)

### Fixed

- The run-shaped PreToolUse gate (`hooks/scripts/preflight-gate.py`) recognizes a headless run only when `claude` is a command word — at the start of the command or after whitespace or one of `; & | (` or a backtick, never after `.`, `/`, `-` or a word character — followed by `-p` or `--print` as a standalone token among its own arguments (bare words and quoted strings joined by spaces, tabs or a backslash-newline; a separator or a bare newline ends the argument list). Its previous pattern (`\bclaude\b[^\n]*\s(-p|--print)\b`) took any mention of a `.claude/…` path, or the word anywhere, followed later by an unrelated `-p` token (`mkdir -p`) for a CLI invocation, then resolved whatever spec or lane path the command named and denied the whole command when that spec was missing or failed preflight: in the lab session of 2026-09-11 a porter's smoke fixture (`mkdir -p "$G/.claude" … hypotheses/H-901-smoke-lane.md`, evaluator-port refute advisory 5), two orchestrator `gate-consumer` fixtures naming `experiments/runs/H-901`, several `.claude/jobs/…` staging lines and `tee` journal-fragment heredocs were all blocked. Real invocations (`claude -p "…"`, `claude --model x -p`, `source env && claude -p`, `CLAUDE_CONFIG_DIR=/tmp/x claude --print`, `$(timeout 90 claude -p …)`) still match and every other gate decision, reason string and exit path is byte-identical. Heredoc bodies and quoted strings that spell out the invocation itself still gate, as before: the exact 2026-09-11 `tee` fragment (its body names the CLI and its flag in backticks) is denied on both the old and the new rule, because the gate treats a quoted argument as one token but has no general quoted-string awareness (README, Known limitations).
  
  Detection is bounded so the hook stays cheap on every Bash call (the hooks.json `if` filter is not a prefilter under current CLIs): a command is scanned only when it contains a standalone CLI word and a standalone flag token after it (two linear passes), and each CLI word is then checked within a window of 64 argument tokens / 4096 characters, so a long single line with many mentions of the CLI costs mentions × window, never mentions × line. Measured on this host (Python 3.9): a 1 MB single line with 1,000 mentions 47 s → 0.5 ms, a 93 KB chain of 1,000 commit messages 3.5 s → 0.1 ms, the original pattern's pathological `(claude) ` × 20,000 18 s → 0.1 ms; the flag-appended variants that pass the pre-check finish in 3-70 ms; the remaining worst case, a 1 MB line of 66,000 CLI words each hiding a flag inside quotes, takes 1.5 s. The lab's 394 recorded launches place the flag at most 20 tokens / 393 characters after the CLI word (3x / 10x under the caps); a launch beyond either cap is gated only by the skill discipline. Regression tests: `python3 hooks/scripts/preflight-gate.py --selftest` (32 regex cases — the quoted-commit-message case renamed to what it proves, `word-quote-is-not-command-position`, two pinned `residual-*` cases, four `bound-*` cases — plus 6 timing cases under 0.5 s each) and `python3 scripts/selftest-preflight-gate.py` (the gate end to end in a throwaway consumer, including the blocked fixtures rewritten without a CLI call, each proven to have matched the old pattern, and the residual heredoc deny). (preflight-gate-headless-regex.md)

## 0.24.2 (2026-09-11)

### Fixed

- `scripts/lineage-stopping.py` closes the round-2 refute advisories on the lineage stopping rule (lab H-DRAFT-5810517d-verdict-lineage-stopping, REFUTE.md round 2: verdict LAND with A13-A18 and NIT-1..4 riding this follow-up). `void --run N` and `append-look --run N` now refuse a run parked in `pending.jsonl` (exit 3, naming `settle <lane> N --root-cause A#=<class>` as the one verb that types it), so a manual verb aimed at a pending run can no longer wedge the lineage (A13). `record` refuses (exit 2) a run whose record files carry no readable `wall_s` / `cost_usd` unless the driver charges by its own clock and meter through the new `--wall-s` / `--cost-usd` overrides (the override wins, the spend row carries `charge_source: override`, `--wall-s 0` refuses too), so a crash-lost run is never charged as free and the spec's rejected reading (c) -- a lineage whose every launch voids re-takes forever -- stays unreachable (A14). The frozen copy is verified before any R2 write in `record`, `append-look`, `void` and `settle`: a tampered `frozen/rule.json` refuses with the ledger, stream, looks and pending rows untouched instead of after charging and appending (A15). A pending row carries `record_sha256` of the record files `record` read and `settle` re-hashes them: a record edited or lost since then settles as the `ambiguous` void with `record_changed` (parked vs now, per file) on the void row and the settled pending row, never as a look (A16). `settle` refuses a root-cause class for an id that is not pending (NIT-1); the already-recorded guard keys on the run directory as well as the run number, so `record run-5 --run 7` then `record run-5` is refused (NIT-2); `charge` refuses a second row for the same numbered run (`already-charged`) and `record` refuses a run charged in `spend.jsonl` but typed nowhere, pointing at the manual verbs that finish it without a second charge (NIT-3); a `--run-validity` id the record carries no assertion for is warned on stderr and listed in `meta.run_validity_unknown` instead of being silently ignored (NIT-4). `docs/lineage-stopping.md` gains a "Refusals and guards" table, the new CLI flags and ledger keys, drops "or truncation" from the A6 limitation (the kind and truncation checks run before the freeze; A17) and documents `init --policy` skipping the reference byte check beside A5 (A18). `--selftest` grows from 85 to 94 checks, one or more per finding, each through the library and the CLI; `rules/lineage-sprt.json`, `rules/frozen/lineage-sprt.json`, the typing table, the SPRT constants and the lab parity hashes (`state.jsonl` a56f0dcf..., `stream.jsonl` b17fd8a3...) are unchanged. (lineage-stopping-followups.md)

## 0.24.1 (2026-09-11)

### Fixed

- `scripts/preflight-rigor.py`: the `LINEAGE-CAP` row (row 8) is re-pointed at the kept lineage stopping rule and reads it before the legacy decision path. At depth >= 3 it resolves the lineage root (the successor's `lineage/inherits.json` chain first, then the `refined-into:` root, then the spec's own run directory), verifies the frozen copy `<runs_dir>/<root>/lineage/frozen/rule.json` against the policy sha `8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1` (its `rule_text` bytes and its `sha256` field), and reports the stream and spend ledger: `PASS lineage-rule:<promote|hold|max-looks|insufficient n=k/13|spend-exhausted|no-looks-yet> looks=<k> voids=<v> spend=<usd>/<wall_s>`, or `FAIL lineage-rule:tampered` on a frozen copy that does not hash to the policy. A lineage under the rule is never asked for a count; lineages with no lineage directory read exactly as before through the `lineage-decision:` path, depth < 3 stays `SKIP cap-not-reached`, every other row is byte-identical, and the META rigor block gains `lineage_rule` (the state token, or null). `--selftest` grows from 181 to 239 checks with the seeded lineage cases (clean stream, promote, hold, spend-exhausted, no looks, tampered, inherits pointer and chain, legacy unchanged); `docs/preflight-rigor.md` row 8 and a new "Row 8 under the lineage stopping rule" section, `docs/lineage-stopping.md` and the README follow. Evidence: lab keep `H-DRAFT-5810517d-verdict-lineage-stopping` (cause-n-effect, kept 2026-09-11, journal fragment 0490, On-keep row "the preflight-rigor LINEAGE-CAP row is re-pointed at the lineage stream and spend ledger instead of a depth-3 count request"). (preflight-rigor-lineage-rule.md)

## 0.24.0 (2026-09-11)

### Added

- The lineage stopping rule (R0-R4) ships: a lineage stops on evidence or on spend, never on a launch count. `rules/lineage-sprt.json` is the frozen SPRT policy (alpha 0.05, beta 0.10, p0 0.5, p1 0.1, Wald truncation 13; sha256 `8052bda9c051…`, byte-identical to the lab's sealed policy) with its reference frozen copy `rules/frozen/lineage-sprt.json` (`ce73163a…`, what `stopping-rule.py --freeze` writes from the plugin root); `scripts/lineage-stopping.py` carries R0 (freeze once, spend budget = the spec's Budget-per-run caps x the recomputed truncation length, recorded with its derivation in `spend.jsonl`'s header), R1 (launch refusal on wall or US$ exhaustion, exit 3), R2 (every launched run charged then typed `counted` / `ambiguous` / `annulled` / `budget-exceeded` by the lab's typing rule, ported byte-for-byte; an unclassed failing assertion is parked pending its root cause, never typed by guess), R3 (the pooled counted stream evaluated by the unchanged `scripts/stopping-rule.py` against the frozen copy, prefix invariants in progress and `--check` at the terminal; `promote` = KEEP, `hold` = DISCARD with the exclusion banked, `max-looks` = closed without a verdict) and R4 (a refine successor inherits the root's frozen copy, stream, ledger and budget; nothing resets), resolving paths through `.claude/hyp.json`; `docs/lineage-stopping.md` (the rule, the typing table, every constant, the ledger, the CLI, adoption, what "no launch count" means, the evidence); `scripts/selftest-lineage-stopping.py` (the 33 lineage.py cases plus spend / refusal / R3 / R4 / settle-refusal / already-recorded cases, 85 checks, and the kept instrument's own selftest); and the hypothesis template's Verdict-rule comment gains the rigor-row grammar with the lineage rule in place of "a lineage cap on launches across refines". Evidence: lab H-DRAFT-5810517d-verdict-lineage-stopping kept 2026-09-11 (fragment 0490) — on 14,000 seeded lineages the rule promoted 4.2% / 3.8% / 2.5% of coin-flip mechanisms against 48.4% for the template rule with its 4-launch cap, closed 0 lineages on bookkeeping and 0 over budget under both charge models, replayed the four recorded lineages with 0 count requests against 3 recorded, and closed its own lineage `evidence-sufficient promote` at look 5 (runs 2-6 each 5/5, four by cold executors) with 17,646 s of its 23,400 s spend unspent. Fix pass after the ship refute review (B1, A1-A3; A4-A11 documented): the hypothesis template's count-shaped Verdict-rule example ("keep if 4/4 ... discard after 3 failed runs") is replaced by the lineage-rule example; `settle` refuses (exit 2, the look stays pending) a pending id given no root-cause class instead of defaulting it to variable-side -- an unclassed root cause is parked pending, never typed by guess; `record`, `append-look` and `void` refuse an already-recorded (spec, run) with exit 3 `already-recorded` and one `refusals.jsonl` row, so a driver retry never double-counts; `docs/lineage-stopping.md` gains a "Known limitations (from the refute)" section (A4-A7, A9, A11, none fixed). (lineage-stopping-rule.md)

## 0.23.0 (2026-09-11)

### Added

- A fail-closed door evaluator sits inside `decisions.py add` between the shape check plus door lint and the append: `scripts/decision_door_check.py` (standard library; `--selftest`, 41 checks; a stdin/argv CLI) evaluates every candidate from its six door fields and committed state alone — W1 FIELDS and W2 DEDUP-WHY (MALFORMED / MALFORMED-BATCH, exit 2, nothing appended), H1 HARD (a declared externality corroborated by the lint's own observable renders a card carrying `hard=<class>`), T1 UNDO (every option revertible and the recommended option's undo corroborated against the staged artifact; `ledger-row` only when the staged artifact is the configured ledger), T2 EVIDENCE (a resolving evidence pointer inside the evidence classes, or a recommendation that already is the default on silence; a staged spec whose frozen Binary-assertions..Verdict-rule span differs from HEAD is FROZEN-SPAN-TOUCHED), T3 EXTERNALITY (all six observables NEGATIVE), and a STREAK guard (ten consecutive records with no veto renders the eleventh) — and either RECORDS it or lets it render. A RECORD appends the decision row with `door.outcome RECORD` plus one `kind:"decision-resolution"` row (`disposition accepted`, `chosen_options [<recommended>]`, `basis two-way-door`, a one-line `undo`, `veto_open_until` = `requested_at` + 7 days), prints one `DECISION-DOOR<TAB><id><TAB>RECORD` audit line and a plain-English block ending "Proceed now. Do not wait on this row, and never gate a driver on it.", opens nothing and fires no notification (`proactive-open.sh` skips every id joined to a closing resolution); a CARD appends the row with `door.outcome CARD` and the findings and renders as before; anything unverifiable — a dangling pointer, a git stall past `--door-git-timeout`, an unparseable denial record, an exception (`--door-inject-fault` is the test seed) — renders (`EVALUATOR-FAIL-CLOSED`), and no bypass flag exists. The veto is `decisions.py resolve DEC-NNN --deny --comment veto`: the deny wins the join and the resolver runs the record's undo as an attributed follow-up (`git revert --no-edit` of the landing commit whose subject starts `landing: decision-record=DEC-NNN `; a `ledger-row` record's undo is the deny row itself). Surfaces: `hooks/scripts/session_resolver.py` prints `DECISION-RECORD` lines and one `DECISION-RECORDS-OPEN` summary after the open cards; `compile-dashboard.py` renders section `1b. DECIDED FOR YOU (veto open: N · recorded: N · vetoed: N · cards with findings: N)` in `DASHBOARD.md` and a records block before `</body>` in `decisions.html`; `show` prints a `door-outcome:` line; `check` prints the exit-neutral `DOOR-UNAUDITED` line for a non-legacy decision row with no door outcome. Every shipped writer passes the evaluator: `add`; `dispatch-gate.py ingest` and `reflex-surface file` through the new `decisions.door_evaluate_row()` / `door_record_row()` after `door_lint_row()` (a RECORD lands the pair and skips the opener); `retest-trigger.py` and `knob-observe.py` read `recorded <id>:` as a filed signal beside `added <id>:`, and their selftests stage the evaluator beside the kit (a copy of `decisions.py` needs `decision_door_check.py` and `decision_card_lint.py` beside it). One ported constant, recorded in `docs/decisions.md` ("Records vs cards"): the evaluator's ledger is the consumer's — `.claude/hyp.json` `ledger_file`, default `ledger/ledger.jsonl`, through the lint's `ledger_rel_for` — for T1's `ledger-row` corroboration and T2's decision-resolution authority; every other byte is the lab's sealed evaluator. `scripts/selftest-decision-door-evaluator.py` runs the evaluator's and the kit's selftests together (the record, the missing pop-up, the veto's undo restoring the tree, the side-door row, the crash seed). Evidence: source lab H-DRAFT-73404199-decision-door-evaluator, kept 2026-09-11 at 5/5 in two consecutive counted runs (the second by a cold README-only executor) with two independent cold verifiers recomputing every cell — 0/300 false refusals over the keyed one-way cards (the maintainer's own three choices included), 55/55 planted two-way cards plus the DEC-018 re-cut and 11/11 declared-hard-uncorroborated cards recorded, 86/86 undos restoring the pre-record state byte-identically (git-revert 26, ledger-row 34, amendment 11, flag 10), 5/5 seeded vetoes denied and undone, records surfacing in the DECIDED FOR YOU section and the resolver lines and never opening the browser, every fail-closed seed a card, the batch refused, the streak guard firing at the eleventh, the control add on the same fields base admitting 204/204 two-way cards; journal fragment 0489. (decision-door-evaluator.md)

## 0.22.0 (2026-09-11)

### Added

- `scripts/lane_containment.py` ships the lane-scoped containment instrument every lane driver imports (Rule B: no containment function of a lane's own): one standard-library, Python 3.9 file with the frozen `begin`/`end`/`replay`/`selftest` CLI and the `Session` library (`open_w`, `makedirs` + `note_dir`, `child_env`, `ingest_transcript`, `event`, `close`). It reads before/after inventories of exactly two roots — the lane directory and the run's declared scratch root — and never a whole-tree diff; derives declared writes from the run's own audit (`open_w` events, audited `note_dir` directories, ingested transcripts, the child audit shim, committed `events.jsonl` in replay) and accepts no declared-writes list; records sibling-lane paths and denied read probes as covariates that change no clause; and types its reading as a run-validity void class, never a numbered assertion — a write finding (`out_of_lane_writes`, `undeclared_writes`) is `ambiguous`, a read or key finding (`forbidden_reads_succeeded`, `key_marker_hits`) is `annulled`, a record the evidence names but that is missing or unparseable is `void`/`ambiguous`; exit 0 clean, 10 violation, 11 void, 2 usage. The file is byte-identical to the lab-kept instrument (sha256 `d7d9b339c8f82e12c94a9083fe5e946da7e953df2c6a4fa9d8165400862a79df`, the `instrument_sha256` every kept report carries); `scripts/selftest-lane-containment.py` pins that sha and runs the instrument's 9-check selftest plus eight further live/CLI/interface checks, including the known covariate at this sha: a clean live pass whose record directory lies inside the lane reads `violation`/`ambiguous` on exactly the record directory's own `before.json`, `config.json`, `events.jsonl` (the lab's driver exempts the record directory in its recount; live mode is proven at the first adopter). `docs/lane-containment.md` documents the five clauses and the class law, the declared-from-audit rule, the interface and report grammar, the replay evidence map, and adoption (import + `Session` lifecycle, the record-directory covariate, real paths, the `experiments/runs/<lane>` layout the instrument assumes); `templates/HYPOTHESIS-TEMPLATE.md` gains one Method comment stating that containment is a void class recorded by `scripts/lane_containment.py`, never a numbered assertion; README lists the instrument. Evidence: source lab H-DRAFT-c2b572ab-lane-containment-instrument-v2, kept 2026-09-11 at 5/5 in two consecutive counted runs, the second by a cold executor from the fixture README alone with a fresh seed set — 84/84 blind seeded plants reported in their own class (12 per class over V1-V4, N1, N2, C), 0 findings on the 16 recorded runs replayed from committed evidence (the one void a crash-lost record), the four historical false failures (646 sibling paths, 411 unaudited bookkeeping files, 3 denied probes, one lost record) re-graded exactly as sealed, every one of the five bespoke implementations failing as a control, two passes byte-identical; zero LLM calls, US$0 (fragment 0488). (lane-containment-instrument.md)

## 0.21.0 (2026-09-11)

### Added

- Decision cards carry six structured door fields at `decisions.py add` — per-option `--undo`, and `--staged-artifact`, `--evidence`, `--externality`, `--recommended`, `--default-on-silence` (plus `--amount-usd` when `--class spend`) — linted by the new `scripts/decision_card_lint.py` (rules D0-D9 with the corroboration table; standard library; `--selftest`, 69 checks) under the preflight exit contract: 0 PASS (appended, the row carries `door.fields_sha`), 1 ESCALATE (appended with the finding under `door.findings`, one `ADD-FINDING` line first, renders as before), 2 MALFORMED (every missing or invalid field listed as `ADD-REFUSED` lines, nothing appended). A git read that stalls prints one exit-neutral `ADD-TIMEOUT` line and never a finding. The lint reads the consumer's ledger — `.claude/hyp.json` `ledger_file`, default `ledger/ledger.jsonl` — for its D7 tail and as D3's decision-resolution authority (the one departure from the lab's sealed lint, byte-recorded in `docs/decisions.md`, "Ported constants"). Legacy rows are exempt and never re-validated; the boundary is the consumer's — `.claude/hyp.json` `decision_door_legacy_max_id` (an integer: ids at or below it) or, when absent, shape and order (every row before the first row carrying a `door` object) — so any number of pre-upgrade cards upgrades cleanly while every post-upgrade path is gated; `check` re-validates only the fields' shape on the other rows; `list`, `show` and `surface` render legacy rows byte-identically. Every shipped writer of a decision row passes the fields: `retest-trigger.py` (undo `ledger-row` per option, `evidence` the first committed stream span, `staged_artifact` and `recommended` `none`, `externality` `none`, `default_on_silence` `nothing-changes`) and `knob-observe.py` (`recommended` `apply-plan`, the knob node staged, undo `git-revert` / `ledger-row`, `evidence` `none-exists`) pass the flags to `add` and read the `added <id>:` line as the filed signal (exit 1 ESCALATE is a filed row); `dispatch-gate.py ingest` and `reflex-surface file` run the same lint in-process (`decisions.door_lint_row`) before `append_line` and refuse a MALFORMED row; `append_line` refuses any `kind:"decision"` row without its `door` stamp. Their selftests copy the lint beside `decisions.py` (retest-trigger 13 checks, knob-observe 24 in a git-backed mini-lab). `docs/decisions.md` gains the field table, the externality vocabulary, the exit contract, the boundary and caller tables; `scripts/selftest-decision-door-fields.py` runs the lint's and the kit's selftests. Evidence: source lab H-DRAFT-5f02c694-decision-card-door-fields, kept 2026-09-11 at 5/5 in two counted runs (the second by a cold executor): 123/123 planted single-defect mutants refused or flagged with their rule id, 0 findings on the 15 row-clean historical cards, 34/34 re-cuts labelled two-way or one-way from the fields alone, 0/170 cards separated by the unpatched add (fragment 0484); the caller, boundary and ledger-resolution fixes answer the ship's refute review (round 1: B1, A1, A2, A4). (decision-door-fields.md)

## 0.20.0 (2026-09-09)

### Added

- `scripts/preflight-rigor.py` gains thirteen REPORT-ONLY spec-rigor rows beside the six ethics rows — DISCARD-BANKS/NULL-CLASS, CONTROL-PRESENT/OFF-FAILS, TREATMENT-DELIVERED, FROZEN-SPAN-SHA, DIRECTIVE-INTERPRETATION/SOURCE-LINE, STAGE1-REVIEWED, REFUTE-REVIEW, LINEAGE-CAP, VOID-CLASS, WHO-CARES, PREMORTEM-PRESENT, BLIND-SEEDS, SUBSTANTIVE-ASSERTIONS — each one PASS/FAIL/SKIP line per spec read from the spec's own spans and its committed run artifacts (`VERDICT.json`, `grade.json`, `run.json`, `SHIP.md`, `REFUTE.md`, `AMENDMENTS.md`, `frozen/span.sha`, `fixture/README.md`, `stage1/`, the work ledger's decision rows, the operating-model actor nodes, the cited raw source), appended after the unchanged ethics rows with the `META` line carrying a new `rigor` block; the exit code stays 0 whatever the findings, `--census <repo-root>` prints one `CENSUS` fire-count line per row over every spec (the measured rate a per-row flip decision is made on), and `--selftest` (181 checks, PASS/FAIL per check) rebuilds the counted fixture's three clean seeds byte for byte, asserts they reproduce the lane's recorded rows with 0 FAIL, turns exactly the intended row to FAIL for 53 seeded mutants mirroring the blind manifest's classes (including the eight repaired-detector classes: same-line keyed values, exact-id decision resolution joined to a resolution row, the five-shape void record, the assertion-bound control scan) while the ethics rows stay byte-identical mutant vs seed, and proves the exit code is 0 with 0 and with 13 FAIL rows alike. `docs/preflight-rigor.md` carries the thirteen-row table (what each row checks, when it SKIPs, its evidence class, report-only), the three frozen shared definitions quoted (keyed line, resolves to a decision, void record) plus row 2's declares-a-control definition, the recorded fire rates with Clopper-Pearson bounds, and the one consequence to know before the rows run over existing run directories (a non-void `VERDICT.json` carrying `void_class: null` reads as an untyped void record under the frozen definition). Repository paths resolve through `.claude/hyp.json` (`hypotheses_dir`, `runs_dir`, `raw_dir`, `model_dir`, `ledger_file`) with the lab layout as default, and decision rows are read from the configured ledger and from `ledger/work-ledger.jsonl`. Evidence: source lab H-DRAFT-50b0c1da-spec-rigor-rows-v2, kept 5/5 in two consecutive counted runs, run 2 by a cold README-only executor (journal fragments 0465 and 0469): 100% blind-mutant detection per row (90 admissible mutants authored by two sessions that never saw a detector) under a control that could not tell any mutant from its seed, every historical incident re-found at the pinned snapshot, the two hard candidates under one fire in twenty on the modern kept population, two passes byte-identical; the port replays all 90 mutants (90/90 intended-row FAIL, 90/90 thirteen-row vectors byte-identical to the counted run's raw output) and its census over the lab tree is byte-identical to the lane's detector. Report-only, forward-only, maintainer-flipped per row: nothing gates, and a later ESCALATE flip applies to specs registered after the flip commit only. (spec-rigor-rows.md)

## 0.19.0 (2026-09-09)

### Added

- North-star verdict rigor. `scripts/north-star-check.py` gains the `probe-passed=<lane>` closes-when
  predicate (done only on a yes verdict; a no leaves the row open with outcome no, so its `C-NN:yes`
  dependents retire and its `C-NN:no` dependents proceed) and reads a probe `VERDICT.json` strictly:
  keep/kept/pass/passed/yes/true is a yes, fail/failed/discard/discarded/no/false a no, and every other
  verdict (ambiguous, refine, void, empty, missing key, non-string, unparsable JSON) is unresolved and
  never a yes, named by the advisory `PROBE-VERDICT-UNRESOLVED`; a `reached-when` row that is done with
  outcome no derives the new status `refuted` with the hard finding `REACHED-WHEN-REFUTED`; a file whose
  every `reached-when` row retired derives `abandoned` with the advisory `DESTINATION-ABANDONED`; and
  `reached` now requires at least one satisfied row. `scripts/compile-north-star-progress.py` files any
  derived status word under a panel of its own (no KeyError on `refuted`), counts only satisfied rows
  toward `reached_count` (never retired, refuted or unresolved-probe rows), emits `abandoned` /
  `advisories` and a style+script addendum only when a stop needs them (pages the base vocabulary covers
  are byte-identical), and fixes a cross-file `needs` token that was read as the local id and recursed
  forever when the ids collided. `scripts/closes_when.py` learns `probe-passed=` with the checker's
  yes-set. `templates/north-star.md`, `templates/north-stars-README.md`, `docs/north-star.md` and the
  README document the predicate and the refuted / abandoned vocabulary. Why: before this change every
  verdict word outside pass/fail read as a yes, a discard in a `reached-when` row read as done, and an
  all-retired destination read as reached. Evidence: source lab commits 8b7c1ed7 and ce46291b (journal
  fragments 0452 and 0454), selftests 128/0 (checker), 33/0 in the lab and 31/0 on this copy (progress
  compiler; this copy has no late-born-stops lane) and 30/0 (closes_when), both refute-reviews LAND. (north-star-verdict-rigor.md)

## 0.18.0 (2026-09-09)

### Breaking

- The Stop dispatcher now re-presents open work only to sessions that declared themselves backlog participants. After its no-dispatch-surface check and before its dispatch read, `hooks/scripts/stop-dispatch.py` decides participation from bytes the session itself carries — never the transcript, never git: `HYP_DISPATCH=0` (explicit opt-out, wins) → `HYP_DISPATCH=1` (participant; the plugin's own drivers — the resume timer plist and the portable runner's children — now launch with it) → a per-session marker `.claude/stop-driver/participants/<session_id>` → `.claude/hyp.json` `"dispatch": "all"` (every experiments-profile session, the pre-gate behaviour, opted into per repository) → otherwise not a participant. A non-participant Stop is allowed under the typed reason `not-a-participant`, performs no dispatch read, spends no cycle, does not start the lineage wall clock (a session that joins later gets its full 1800 s), and receives one `systemMessage` per session lineage naming the three ways to join (the marker path for its own session id filled in); every later log record carries `participation.decision` and `participation.source` (an unrecognised `HYP_DISPATCH` spelling is logged as `env_unrecognized`). Participants see decisions byte-identical to before; a repository with nothing dispatchable stays silent as before. **Breaking for consumers who relied on every interactive session being held at Stop:** set `"dispatch": "all"` in `.claude/hyp.json` to restore that. Why: a session opened to debug an unrelated failure was held at Stop for a verdict on a draft spec it had never seen; the directive this dispatcher implements named every boundary at which the backlog is re-derived but never which sessions are its workers. Lab H-DRAFT-c5309f03-dispatch-participation-gate-v2 (refined from H-DRAFT-15e9bdd8, whose 5/5 ×2 patch an adversarial review refuted for gluing `-p` to the prompt in the resume-timer plist — the v2 fixture audits both launchd exec strings, a late-join lineage, and a no-surface repository), kept 5/5 in two consecutive counted runs (the second by a cold executor), an independent recompute, and a clean second adversarial review. Regression test: `scripts/selftest-participant-dispatch.py` (23 checks); `selftest-gated-dispatch.py` and `selftest-worktree-root.py` now declare their simulated sessions participants. (dispatch-participation-gate.md)

### Fixed

- The Stop dispatcher no longer re-presents specs that the dispatch surface already knows are not actionable. `scripts/dispatch-status.py --json` now carries `actionable` and `gated` (open items whose committed `## Status` block carries a `PARKED` / `BLOCKED-*` / `COUNTING` marker, each with the marker and the comment note naming the human step; `open` is unchanged as their union), and the text output prints one `GATED` line per gated item instead of ranking it. `hooks/scripts/stop-dispatch.py` blocks on `actionable`, names gated items as gated in the re-present message, and when every open item is gated allows the stop under the typed reason `all-open-gated`, consuming no cycle and printing the gate list once to the user via `systemMessage`. A surface without the `actionable` field grades as before. Fixes #28 (consumer vault 2026-09-04: four human-gated specs re-presented for the full 12-cycle cap; re-verified against 0.14.2 on 2026-09-07). Regression test: `scripts/selftest-gated-dispatch.py` (20 checks). (issue-28-gated-dispatch.md)

## 0.17.2 (2026-09-08)

### Fixed

- SessionStart foreground budget (consumer gap G14; lab H-DRAFT-a10fd3f7-session-start-hook-budget-v4, successor of H-DRAFT-bf4dc77d, H-DRAFT-b8c360f4 and H-DRAFT-45585281). Five of the six SessionStart commands (harden-check, session-recovery-warning, compile-dashboard --check, session_resolver, compile-dashboard --quiet) now run through `hooks/scripts/session-start-budget.py`, started as `python3 -S -E` and importing only os, sys, time and zlib: the command runs in its own process session, the wrapper waits up to HYP_SESSION_START_BUDGET seconds (default 5) from its own start, prints the command's bytes and exits with its rc when it finishes in time, and otherwise prints the last cached reading plus one `SESSION-START-BUDGET:` line while the detached runner lands the reading for the next startup (one runner per command at a time, H-309 lock rule). The SessionStart hooks match `startup` for the wrapped group; `resume|clear|compact` run `drift-check.py` and one `cached` command that prints the readings and spawns nothing. `drift-check.py` and every 60 s timeout are unchanged. (session-start-hook-budget.md)

## 0.17.1 (2026-09-08)

### Fixed

- `scripts/hyp-lab.py` resolves the plugin root through the real path of the file rather than its invoked path, so a repository that links the script into a mirrored plugin tree (the source lab links every shared script into its deploy tree, one source per file) imports `hooks/scripts/hyp_config.py` beside the real file instead of raising ModuleNotFoundError; invoked from the plugin tree itself nothing changes. Found landing lab H-DRAFT-e9c49cbd-consumer-lab-entrypoint-v2 (v0.17.0). (hyp-lab-realpath-root.md)

## 0.17.0 (2026-09-08)

### Added

- `scripts/hyp-lab.py`: one named entry point for the read-only lab instruments — `status | stalls | preflight | recent | prior | mine | next-id`, one subcommand per `make lab-*` target of the reference consumer layer. `status`, `stalls` and `preflight` dispatch to the shipped scripts (`status` appends one `DRAFTS:` line counting the draft-handle specs the released readers skip); `recent`, `prior`, `mine` and `next-id` are built in over the `.claude/hyp.json` paths with `hyp_status.py` as the status reader; `next-id` prints the H-148 draft handle (`H-DRAFT-<hash8>-<slug>`) and never fetches. Zero LLM, nothing written under the repository (lab H-DRAFT-6ec1691d-consumer-lab-entrypoint). (consumer-lab-entrypoint.md)

### Fixed

- `scripts/harden-check.sh` resolves every helper it calls through one resolver: the plugin root first, else the repository's own `scripts/<helper>`, else the block is skipped as before. A repository that keeps helpers the plugin does not ship (the lab's twelve: amendment-detector, check-governance-drift, check-submission-connectivity, claim-lint, coincidence-check, commitment-lint, corpus-lint, dashboard-features.json, journal-freeze.sha, plugin-parity-check, repo-coverage-lint, wave-status) gets their advisory lines back from the plugin-rooted SessionStart fire — 12 of 26 lines had gone dark there — while a consumer without such helpers prints byte-identical output. Lab H-DRAFT-e9c49cbd-harden-helper-repo-fallback (consumer gap G12), kept twice at 5/5. (H-DRAFT-e9c49cbd-harden-helper-repo-fallback.md)

## 0.16.0 (2026-09-08)

### Added

- `scripts/eval-delta.py` materializes a session's delta tree for `scripts/eval-grade.py`: every path the session added or modified relative to a base commit (working tree included, untracked-not-ignored included, deleted paths listed never copied, symlinks copied as links) copied out with paths preserved plus a JSON manifest, so a case file's graders read only what the session changed; `--selftest` builds a throwaway repository and checks the rule. Stdlib + git. Lab H-DRAFT-6c54bb27-lab-plugin-skill-swap-v3 (kept 5/5 in two consecutive runs, the second cold) graded eight headless sessions with it against the shipped scorer; the lab now runs the plugin's intake and hypothesis skills in place of its own (north-star condition C-06). (H-DRAFT-6c54bb27-lab-plugin-skill-swap-v3-eval-delta.md)

## 0.15.1 (2026-09-07)

### Fixed

- `evals/intake/save-external-finding` names its reserved `example.com` source as a deliberate placeholder to record verbatim and never fetch or verify. Without that sentence, the intake skill run against a repository carrying a citation registry (the source lab) ended the turn at turn 4 asking whether the URL was real and wrote nothing in four of the lab's graded capture sessions across both arms (lab H-DRAFT-6ec1691d run-5/run-6, H-DRAFT-70769657 run-1/run-3, fragment 0411); in the case's own bare scaffold the decline never fires, so the graders and expected outcome are unchanged. (intake-case-placeholder-source.md)

## 0.15.0 (2026-09-07)

### Added

- `scripts/id-collision-lint.py` reports hypothesis ids shared by more than one spec file and journal-fragment ids used more than once, and harden-check surfaces the counts as ADVISORY-35 `id-collisions: spec=N fragment=M`. On a 256-spec consumer that mints ids with its own next-free counter it reads spec=57 fragment=131; on a repository that lands ids through the plugin's draft-then-allocate gate it stays silent. Lab H-DRAFT-6ec1691d-consumer-id-collision-advisory, kept 5/5 in two consecutive runs (the second cold); consumer gap G4. (H-DRAFT-6ec1691d-consumer-id-collision-advisory.md)

## 0.14.3 (2026-09-07)

### Fixed

- harden-check advisory 20 no longer renders a consumer's dashboard: it called `compile-dashboard.py --triage-check` whenever `experiments/runs/DESIGN-decision-triage/triage.json` existed, and the shipped compile-dashboard has no such branch, so the call rendered DASHBOARD.md and decisions.html into the consumer's tree from a read-only advisory hook. The block is guarded on the branch being supported; every other line is byte-identical on the consumer and in the source lab. Lab H-DRAFT-45585281-harden-advisory20-consumer-write, kept 5/5 in two consecutive runs; consumer gap G13. (H-DRAFT-45585281-harden-advisory20-consumer-write.md)
- `scripts/dispatch-status.py` reads every committed spec body in `git cat-file --batch` chunks of 64 under one wall ceiling, `DISPATCH_STATUS_MAX` seconds (environment, default 20), instead of one `git show` subprocess per spec: a 180-303 id corpus that read in 38-151 s on a loaded host reads in about one second, so the Stop dispatcher's 45 s inner read finishes. A chunk that times out is discarded whole and its ids are disclosed as UNREAD (open with kind `unread`, never landed, never actionable) through a `partial` object in `--json` or one `DISPATCH-PARTIAL:` line in text; whenever the budget is not hit the output is byte-identical to before. Consumer gap G12 (lab H-DRAFT-45585281). (dispatch-status-time-budget.md)

## 0.14.2 (2026-09-07)

### Fixed

- The harden-check background refresh has a time budget: every scanner block runs under a per-block ceiling (`HARDEN_BLOCK_MAX`, default 120 s), no block starts after the total ceiling (`HARDEN_TOTAL_MAX`, default 600 s), a block killed at its ceiling has its output discarded rather than read as zero findings, and one `HARDEN-PARTIAL` line names every deferred block. On a 35k-file consumer the refresh fell from 380 to 485 s down to 126 s by deferring exactly the over-budget whole-tree name scan, with every other advisory line byte-identical and a raised-ceiling control equal to the unbounded run byte for byte; the source lab's refresh through its deploy-tree link is unchanged. Lab H-305 (harden-refresh-cost-bound), kept 5/5 in two consecutive runs after Amendment 1, the second by a cold executor; consumer gap G11 of the lab-plugin convergence program. (H-305-harden-refresh-budget.md)

## 0.14.1 (2026-09-07)

### Fixed

- Every plugin SessionStart hook now has a 60 s timeout (was 10 to 20 s). Under normal host load on a lab-sized repository the six hooks run concurrently and the 10 s ones were cancelled at their limit, which silently drops the hook's context line from the session (observed: compile-dashboard --check cancelled at 10.1 s, drift-check and the session resolver cancelled at 10 to 15 s when the host was busy). With 60 s no SessionStart hook was cancelled in any measured session while total SessionStart wall stayed under 45 s; nothing else in hooks.json changes. Lab H-DRAFT-d8609270-plugin-hook-timeout-margin, kept 5/5 in two consecutive runs (the second cold, whose OFF arm reproduced a cancellation at load 12); consumer gap G10 of the lab-plugin convergence program. (H-DRAFT-d8609270-plugin-hook-timeout-margin.md)

## 0.14.0 (2026-09-07)

### Added

- The north-star follow-through keeps (lab H-DRAFT-c1c1344b, fragment 0282, each kept 5/5 in two counted runs): `scripts/knob-observe.py` accepts `rule: band` alongside the kept ladder (integer `bounds: [min, max]`, `band`, `sense`, `step`, `hysteresis: one-change-per-window`, held `would_set` and a recorded `proposal` under every kill switch, resumable `--replay`; `--selftest` 23/23 with seeded out-of-bounds, double-change and kill-switch violations each exiting 1) and `docs/knobs.md` documents the band grammar; `scripts/stopping-rule.py` lands with `docs/stopping-rules.md` (`--freeze <rule> --into <copy>`, `--evaluate <rows> --looks k --rule <copy> [--json]`, `--selftest` 6/6; typed exits 12 `frozen-rule-missing`, 13 `frozen-rule-tampered`; verdict vocabulary evidence-sufficient / evidence-insufficient, never keep or discard); `scripts/compile-dashboard.py --check` exits 1 when the compile raises instead of reading a swallowed crash as fresh (the lab's keep-flip sort key has no anchor in the plugin's compiler yet and rides the next compiler sync). (H-DRAFT-c1c1344b-north-star-follow-through.md)

## 0.13.0 (2026-09-07)

### Added

- harden-check gains ADVISORY-34 branch-without-pr: when the session's HEAD is a non-default branch at least HARDEN_PR_MIN (default 1) commits ahead of origin's default branch and gh reports no open pull request for it, one advisory line names the branch, the ahead count and the `gh pr create --draft` command; silent on the default branch, detached HEAD, without gh, or with HARDEN_PR_CHECK=0; the gh call is pinned to the remote owner's account first and says so when pull requests cannot be read instead of hiding. Motivation: 52 pushed commits sat on a lab worktree branch for two days with no PR while sibling branches had drafts — "pushed" was green, review was blind (lab experiments/runs/DESIGN-durability-gaps/research/why-no-pr.md; H-DRAFT-ee81f74d-branch-without-pr-advisory). Ships with scripts/selftest-branch-without-pr.py. (branch-without-pr-advisory.md)
- `scripts/observatory.py` ships the live observatory board: a standard-library OTLP/HTTP receiver (`serve`: JSON and lazily-decoded protobuf on `/v1/logs|metrics|traces`, plus `/hook`) fed by Claude Code hooks through an async `/bin/sh` spool transport that `install-hooks --scope user` writes and `uninstall-hooks` reverts exactly, with end-of-session transcript backfill so a headless exit never loses the tail; every tool call is classified against the repository's operating model (one program token per call, never an argument; catalog paths resolved through `.claude/hyp.json` via `hyp_config`; classes modeled-deterministic / modeled-stochastic / delegated / unmodeled) and rolled into per-session leverage and determinism ratios that `ratios` recomputes byte-identically from the events JSONL; `board` and `web` (Textual via `uv run`) render sessions, traces, logs and metrics. `scripts/selftest-observatory.py` ports the 25 non-TUI regression checks plus four plugin checks (hyp.json overrides, data-directory defaults, stdlib-only import, no lab paths); `docs/observatory.md` gains the section describing the board, the four provenance classes and ratios, the data-directory contract, and the install commands. Ported byte-preserving from the source lab's kept fixture (sha256 4b643fb1…) with six documented divergences (provenance header; hyp_config path resolution; `CLAUDE_CONFIG_DIR` honoured; `python3` in the emitted post-transport command; `SELF_PATH` aliased to `THIS_FILE`; `--state-file`/`--events`/`--spool` default under `$HYP_OBSERVATORY_DIR` or `<config_dir>/observatory`). Four lab keeps, each 5/5 in two consecutive counted runs by cold executors: H-DRAFT-2c1fc974-hook-fed-observatory-board (2026-09-04), H-DRAFT-fcf7b3fa-op-provenance-classification and H-DRAFT-fcf7b3fa-model-leverage-read-model (2026-09-06), H-DRAFT-2652d478-async-spool-backfill (2026-09-06: +1.0 to +4.5 ms per tool call, completeness 20/20). Hooks are opt-in and deliberately not wired in `hooks/hooks.json` (an undrained spool grows without bound; user scope keeps machine-local paths out of committed settings). (observatory-board.md)

## 0.12.0 (2026-09-07)

### Added

- Decision durability: a rule or a decision parked on missing information can now say, in a committed field, what evidence reopens it, the plugin emits that evidence at its choke points, a consumer can send it, and the trigger fires from committed bytes, never from a date (see `docs/decision-durability.md`). Five kept lanes ship together. (1) Retest-when predicates: `scripts/closes_when.py` gains the sibling `retest-when` family — `event-count=<event>[:<subject prefix>]>=N`, `metric-crosses=<metric><op><threshold>@last=K`, `evidence-received=<target>` — evaluated at HEAD only, with `--selftest` now 19 + 24 checks; `scripts/retest-trigger.py` files exactly one deduplicated `rule-retest` decision row per armed registry rule the first time its predicate holds, pointers `<path>@<sha40>#La-Lb`, exit 0 always, `--dry-run`, and a `--selftest` that exits 1 if an uncommitted packet or a cross-then-revert series files a row; `scripts/compile-laws.py lint-registry` accepts `retest_when` in place of `retest_by`; `scripts/rule-lint.py` `RULE-EXPIRED` no longer fires on a date-less row that carries a well-formed predicate (`--selftest` added). Lab H-DRAFT-d564bb31-retest-when-predicates KEPT 5/5, 5/5: six seeded rules fired at exactly the key's boundaries, date-only and never-true rules filed nothing across nine boundaries, a staged-but-uncommitted packet filed nothing, a metric that crossed and reverted filed nothing, the real registry (no evidence triggers yet) fires zero rows. (2) Run-page events: `scripts/checkpoint-shadow.sh <run-dir> --out <page>` replaces the grade leg's advisory compile line byte-for-byte on stdout and exit status and appends one `event/checkpoint-compiled` row `{rc, class, lane, run}`; the payload contract and the class-versus-rc rule land in `scripts/events_lib.py`, the `checkpoint-compiled` verb in `scripts/emit-event.py`, the node in `templates/event-nodes/checkpoint-compiled.md` (the five sibling event-node templates the other verbs validate against ship beside it; the emitters were previously only in the lab's deploy tree). The grade-leg snippet is two lines: `export CHECKPOINT_EVENT_DATE=<pinned date>` then `EVENTS_IMPL="$CLAUDE_PLUGIN_ROOT/scripts" COMPILER="$CLAUDE_PLUGIN_ROOT/scripts/compile-run-checkpoint.py" "$CLAUDE_PLUGIN_ROOT/scripts/checkpoint-shadow.sh" "$RD" --out "$OUT"`. Lab H-DRAFT-d564bb31-checkpoint-compiled-events KEPT 5/5, 5/5: the refusal census of 28 real run directories (20 refused with code 13) plus six seeded corruptions reproduced from committed rows while the grade leg's own outputs stayed byte-identical. (3) The shadow gate knob: `scripts/knob-observe.py` (verbs `evaluate <knob> [--at SHA] [--replay] [--json]`, `check`, `--selftest`), `docs/knobs.md`, and the node templates `templates/knob-nodes/checkpoint-gate-stance.md` (`mode: shadow`, 30-observation window, ladder rule, per-class bounds, demote-on-first, three kill switches), `templates/policy-nodes/checkpoint-gate-license.md`, `templates/command-nodes/file-gate-decision.md`, `templates/event-nodes/checkpoint-gate-threshold-reached.md` — the first bounded controller in the plugin: it appends one state row per new signal state and files the advisory-to-deny decision only in recommend mode, only at n=30, only when the licensing node resolves. Lab H-DRAFT-d564bb31-checkpoint-gate-shadow-promotion KEPT 5/5, 5/5 (28 real rows labelled, nothing filed at 29, exactly one evidenced row at 30, idempotent at the same HEAD, three kill switches held, four seeded violations bit). (4) Decision rows with `retest_when`: `scripts/decisions.py add` validates the optional field through the shared parser, `check` prints the exit-neutral `RETEST-DUE` (with the evidence pointer) and `REVISIT-UNARMED` classes, `scripts/review-cadence.py` renders a `RETEST DUE` block above `REVIEW DEBT` (byte-identical render when nothing is due), `docs/decisions.md` gains the field's section. Lab H-DRAFT-d564bb31-decision-retest-when KEPT 5/5 (runs 2 and 3; run 1 lost one assertion to version-control latency under machine load, fixture unchanged). (5) Evidence packets: `scripts/hyp-evidence-export.py --target <target>` is consent-gated by `.claude/hyp.json` `evidence_export` (`off` | `packet` | `submit`; absent means off; never seeded on), projects the event stream through the lab's committed allow-list (`templates/exports/checkpoint-gate-stance.export-config.json`, the first target), pseudonymizes subjects, and refuses any packet the leak scan catches; `scripts/evidence-ingest.py <pointer.json>` lands a packet exactly once as a write-once raw capture whose arrival satisfies `evidence-received=<target>`. Lab H-DRAFT-d564bb31-evidence-packet-roundtrip KEPT 5/5, 5/5 (eight bait rows stripped or hashed in the packet and every one caught in the raw copy; a second identical ingest added nothing). Two ports, not byte copies: the plugin's `decisions.py` resolves the ledger path through `.claude/hyp.json` (the lab's `retest_when` delta was merged three-way onto it, one selftest constant renamed) and `knob-observe.py` reads the same `ledger_file` key so the evaluator and the decision kit agree on where decisions live; every other shipped script is byte-identical to its lab copy. Not in this release, deferred with their needles open: the `checkpoint-compiled` case of `selftest-events.py` and the resolver `KNOB` line (the H-239 event-stream resolver join is still unshipped), `runs-census.py --checkpoint-codes` (no such script in either tree), the `evidence_export` key in `init-scaffold.py`'s writer (absent already means off), the issueops `hyp-evidence` fenced-block parser, and the network dispatch pair (needs a maintainer-minted token). Every arm in every lane was a script; run 2 of each was a cold-context executor. Provenance: source lab cause-n-effect, branch worktree-wayfinding-patterns, journal fragments 0278 (registration) and 0280 (verdicts); stacked on PR #13 (branch north-star-set). (H-DRAFT-d564bb31-decision-durability.md)

## 0.11.0 (2026-09-07)

### Added

- Many north-star files in one repository. `scripts/north-star-check.py` reads every committed `ledger/north-stars/*.md` in one invocation and emits a `set` block (present with a single file too, as a one-destination reduction) (`--json` key `set`, text: a trailing `set:` block) reduced from the per-file blocks alone: `union_frontier` lists each distinct effective lane once with the `<slug>#C-NN` pairs it closes, ranked by destinations served, then minimal distance, then lane id (derived numbers only, never an authored order); `shared_bounds`; and `exit_strict_by_slug`, so one malformed sibling fails its own bit while the others still derive. A `needs` token may name a sibling file's condition, `<slug>#C-NN[:yes|no]`, inheriting its derived status, outcome, and retire cascade with zero edits to either file; unknown slugs are `DANGLING-REF`, cross-file cycles are `CYCLE`, and two committed paths under `ledger/north-stars/` sharing a basename are the new `DUPLICATE-SLUG` lint (`templates/north-star.md` and `templates/north-stars-README.md` carry the grammar, the Set rows and the Reading lines). The checker's git layer is batched (one `ls-tree -r` plus two `cat-file --batch` rounds per commit instead of one subprocess per resolver path), so the all-files read is hook-safe: 25 files by 15 conditions went from 364 git invocations and 69-78 s to 4 invocations and under 1.1 s with `--json`, text and `--strict` exit codes byte-identical. `scripts/compile-north-star-progress.py --all` compiles one `<slug>.progress.html` per north-star file plus `ledger/north-stars/index.html` (one row per destination, every value copied from the checker), and `--check --all` exits 1 naming each stale page; single-file mode is byte-unchanged. `scripts/closes_when.py` additionally carries the `frontmatter-status=<path>:<done-values>[!<no-values>]` predicate and the checker a `document` resolver kind (verb `sync`): a condition binds a committed typed document and derives from the `status:` line of its frontmatter at the commit, a no-value settles outcome `no` and retires `C-NN:yes` dependents while the row stays open, presence is decided by `git cat-file -e` (never by `git show`'s exit code, which is 0 with empty output for some deleted paths) and a bound path absent at the commit is a `DANGLING-REF` hard finding. Evidence: H-DRAFT-d6a0a6ef-document-resolver-milestones KEPT 5/5, 5/5 (journal fragment 0277; twelve bound milestone documents over ten Jira-style sync commits matched a blind reference evaluator at every non-seeded commit with zero north-star edits, a cancellation retired dependents and resume reopened them, a deleted file dangled exactly once, the status-scope lint fired exactly once on the seeded column). Selftests: checker 115 checks, progress compiler 23, closes_when 19. Evidence, from the source lab (cause-n-effect, branch worktree-wayfinding-patterns, journal fragments 0275 and 0276): H-DRAFT-d6a0a6ef-north-star-set-union-frontier KEPT 5/5, 5/5 (24-destination fixture, four planted shared lanes, eight seeded states matched a seed-authored key, byte-identical across invocations and a clone, per-file arrays unchanged, a lane serving three destinations outranked a nearer lane serving one); H-DRAFT-d6a0a6ef-north-star-set-cross-file-needs KEPT 5/5, 5/5 (198 of 198 per-file entries byte-identical for files without the token); H-DRAFT-d6a0a6ef-north-star-set-batched-reads KEPT 5/5, 5/5 (21/21 byte-identical outputs, 42/42 exit codes); H-DRAFT-d6a0a6ef-north-star-set-progress-index KEPT 5/5, 5/5 (24 index rows equal to the checker, 25/25 pages byte-identical across runs). Run 2 of every lane was a cold-context executor; no arm made an LLM call; every fixture was synthesized from the shape of a portfolio repository read only. Stacked on PR #10 (branch north-star-0.4.0), which ships the checker itself; every shipped script and template is byte-identical to its lab copy under `scripts/parity-check.py`. (H-DRAFT-d6a0a6ef-north-star-set.md)

## 0.10.0 (2026-09-07)

### Added

- The destination-map program lands as the north-star layer. A north-star file (`ledger/north-stars/<slug>.md`, template `templates/north-star.md`, convention `templates/north-stars-README.md`) states one destination, a `reached-when:` list, and a table of conditions each bound to one resolver (hypothesis, decision, capture, probe); the file stores no status, and `scripts/north-star-check.py` derives every condition's state at HEAD (done / open / retired / unbound), the frontier with verbs, and the distance, with `--strict` lint, `--at <sha>` replay, `--json`, and `--selftest` (67 checks). `scripts/compile-run-checkpoint.py` compiles one openable single-file `run-checkpoint.html` per counted run from results.json, grade.txt and the spec's frozen assertion span, copying byte slices and never computing a number, refusing six spec-versus-run corruptions with pinned exit codes 10-15, advisory in the grade leg. `scripts/compile-north-star-progress.py` renders the "where are we" page (`north-star-progress.html`: condition cards by derived status, needs edges, frontier, distance, drift chips, replay slider) with `--check`. `scripts/closes_when.py` gains the additive predicate `hypothesis-verdict=H-NNN` (kept OR discarded at HEAD) and a 12-check `--selftest`. `/hyp:init --profile experiments` scaffolds `ledger/north-stars/README.md` and nothing else; `skills/hypothesis` gains a "North star and checkpoints" subsection; `docs/north-star.md` is the orientation page. Evidence, from the source lab (cause-n-effect, branch worktree-wayfinding-patterns): H-DRAFT-2cae0933-derived-condition-status KEPT 5/5, 5/5 (nine seeded verdict events matched a hand-authored key at every commit, byte-identical `--at` replay, all four malformed files rejected); H-DRAFT-2cae0933-run-checkpoint-fidelity KEPT 5/5, 5/5 (six real runs, 1338 numeric tokens traced to a source, six corruptions refused, sha256-reproducible recompile); H-DRAFT-2cae0933-north-star-progress-view KEPT 5/5, 5/5 (12/12 sampled commits matched an independent reference evaluator, one sha256 across three compiles, seeded flip makes `--check` exit 1). Lab fragments 0272, 0273, 0274; ship record experiments/runs/SHIP-hyp-machine-north-star/SHIP-RECORD.md; PR #10. Every shipped script and template is byte-identical to its lab copy under `scripts/parity-check.py`. (H-DRAFT-2cae0933-destination-map.md)

## 0.9.1 (2026-09-06)

### Fixed

- `harden-check.sh`'s background cache refresh is single-flight per repository: a pid file under `.claude/` lets exactly one `--fresh` chain run at a time, a stale lock (dead or rewritten pid) is reclaimed, and the foreground output is unchanged. Four simultaneous session starts on a 35k-file consumer spawned four refresh chains in 0.9.0 (9 to 16 orphan chains were observed machine-wide, each running 17 to 23 minutes) and spawn exactly one now, with the cache still refreshed once. Lab H-DRAFT-c9ec26d9-harden-refresh-single-flight, kept 5/5 in two consecutive runs, the second by a cold executor; consumer gap G9 of the lab-plugin convergence program. (H-DRAFT-c9ec26d9-harden-refresh-single-flight.md)

## 0.9.0 (2026-09-06)

### Added

- `harden-check.sh` runs as a SessionStart hook in every install: every helper it invokes resolves under the plugin root instead of the consumer's `scripts/`, every advisory inspects the repository the session works in (`leak-status.sh` no longer changes into the plugin tree), whole-tree scans are guarded by a tracked-file ceiling so a cache-less consumer run stays under 15 s, and the advisory cache is written only when `.claude/` exists. Measured on a 256-spec consumer: rc 0 with zero stderr on every run (0.4.0: one error line), 8 of 8 helper paths resolve (0.4.0: 3 missing), the printed advisories map one-to-one to blocks whose inputs exist, walls 2.9 to 6.6 s (0.4.0: 7.5 to 13.3 s with nothing useful printed); in the source lab every existing line is preserved. Lab H-DRAFT-40ec0bc2-portable-harden-advisories-v2 (successor of H-303), kept 5/5 in two consecutive runs, the second by a cold executor; closes consumer gap G1 of the lab-plugin convergence program. (H-DRAFT-40ec0bc2-portable-harden-advisories-v2.md)

## 0.8.0 (2026-09-06)

### Added

- `scripts/keep-ship-gate.py` reads every kept hypothesis from committed HEAD, joins the files its run changed against the plugin-shipped paths (the deploy tree in the source lab, or the plugin's own tree in a consumer), and prints one `KEEP-UNSHIPPED` line per keep that has no committed ship record (`experiments/runs/<id>/SHIP.md` carrying a `pr: <n>` line); `harden-check.sh` surfaces the count as ADVISORY-33 at every session start, so a keep that changes shipped bytes stays visible until it ships instead of depending on someone remembering. Lab H-298 (lab-plugin-keep-ships-gate), kept 5/5 in three consecutive runs, the last by a cold executor; north-star condition C-03 of the lab-plugin convergence program. (H-298-keep-ship-gate.md)

## 0.7.0 (2026-09-06)

### Added

- `scripts/eval-grade.py` scores a skill's `claude plugin eval` case files from their bytes alone: it reads each case.yaml with a stdlib parser, applies the deterministic graders (file_exists and tree-targeted regex) to a target tree, and prints one PASS/FAIL line per grader plus a CASE k/n line, so a consumer can measure its intake and hypothesis eval suites without the org-gated eval command or a live session. Lab H-297 (eval-grade-scorer), kept 5/5 in two consecutive runs: golden 15/0 on both sweeps, 8 cases n/n, the OFF path (`import run_cases`) fails with ModuleNotFoundError. First instrument of the lab-plugin convergence north star (C-06). (H-297-eval-grade-scorer.md)

## 0.6.0 (2026-09-06)

### Added

- `scripts/hook-parity-check.py` normalizes a repository's `.claude/settings.json` hooks and a plugin's `hooks/hooks.json` into (event, matcher, guard) rows and prints one line per guard that runs on only one side; `harden-check.sh` gains ADVISORY-32 carrying the count. Measured against the source lab's own wiring, the released plugin shows 23 one-side-only rows, which is why lab and consumer sessions have been protected by different guards. Lab H-DRAFT-4c0dadb8-hook-wiring-parity, kept 5/5 in two consecutive runs after Amendment 1, cause-n-effect research/consumer-parity-gaps.md. (H-DRAFT-4c0dadb8-hook-wiring-parity.md)

## 0.5.0 (2026-09-06)

### Added

- `init` creates an empty `ledger/ledger.jsonl` on every profile and never overwrites it, so the decision kit, the commitment resolver, the claim join, and DASHBOARD.md sections 1 and 2 work in every install instead of reading `source missing`. Re-running init on an existing install adds exactly that file and nothing else; a second run is a no-op. Lab H-DRAFT-4c0dadb8-init-scaffolds-ledger, kept 5/5 in two consecutive runs after Amendment 1 (the baseline's own second-run residue, `.claude/reflex/selftest/report.json` rewritten by the reflex self-test, is recorded as a separate horizon item), cause-n-effect research/consumer-parity-gaps.md gap G2. (H-DRAFT-4c0dadb8-init-scaffolds-ledger.md)
- `drift-check` now compares the copies `init` installs into a repository (the preflight script, the hypothesis template, and `compile-journal.py`) against the plugin's canonical bytes and prints one advisory line per stale copy with the line delta and a review command; it never overwrites a consumer-owned file. On a 256-spec consumer it reports the preflight at 73 lines against the shipped 151 and the template at 49 against 79 where 0.4.0 printed `clean`; a fresh scaffold prints `clean`; a one-byte seed is reported by path; every other drift-check line is byte-identical. Lab H-DRAFT-4c0dadb8-installed-copy-drift, kept 5/5 in two consecutive runs (run 2 by a cold executor), cause-n-effect research/consumer-parity-gaps.md gap G6. (H-DRAFT-4c0dadb8-installed-copy-drift.md)

### Fixed

- `dispatch-status.py`, `stall-signals.py`, and `compile-findings-index.py` read a spec's status through one shared canonicalizer, `hooks/scripts/hyp_status.py`: `keep` is `kept`, `discard` is `discarded`, `refined-into:` and `refined (into` are terminal, a `<canonical>-<qualifier>` token maps to its canonical word, matching is case-insensitive, and `hyp_status.py --lint <root>` lists every non-canonical status with its rewrite. On a 256-spec consumer the dispatch surface drops from 74 open items to the true 50, so 24 closed specs stop re-presenting through the Stop dispatcher; on a vocabulary-clean tree the three scripts' output is unchanged. Lab H-DRAFT-4c0dadb8-status-vocabulary-canon, kept 5/5 in two consecutive runs after Amendment 1, cause-n-effect research/consumer-parity-gaps.md gap G3. (H-DRAFT-4c0dadb8-status-vocabulary-canon.md)

## 0.4.0 (2026-09-05)

### Added

- CI now owns the version and the changelog. A pull request adds a `.changeset/<slug>.md` file (a `bump:` line and a paragraph like this one) and never edits the version in `.claude-plugin/plugin.json` or `CHANGELOG.md`; the required `changeset-check` status fails PRs that break the rule. On every merge to main the release job computes the next version from the highest reachable `v*` tag, writes it into plugin.json, prepends a CHANGELOG.md section, deletes the consumed changesets, tags `v<version>`, and publishes the GitHub release. The README changelog moved to CHANGELOG.md. Contract: `.changeset/README.md`; selftest: `python3 scripts/selftest-release.py`. Lab evidence: H-DRAFT-bfb3323b (cause-n-effect), research/release-automation-prior-art.md. (ci-owned-release-flow.md)

## 0.3.3 — fail-closed dispatch, draft-then-allocate ids (issues #8, #4)
- **Fail-closed dispatch read** (lab H-280, kept twice at 5/5 with write-ahead proof; fixes #8): the
  Stop driver no longer grades a failed dispatch read (timeout, nonzero exit, unparseable output) as
  an allow — the open-work state is unknown, not a pass. The first consecutive failure blocks the
  stop once (exit 2) with a visible retry reason; a second consecutive failure allows under the
  typed reason `dispatch-error-open` instead of `hook-error`, so a persistently failing read costs
  at most one extra cycle and can never trap a session. A durable `dispatch-read-start` line lands
  in `.claude/stop-driver/hook-log.jsonl` before every read, so a hook killed by the outer Stop
  budget still leaves a trace; error records carry `elapsed_s` / `error_class` / `fail_streak`.
  Healthy-path decisions stay byte-identical to the previous release.
- **Draft-then-allocate ids and the land-time id gate** (lab H-293, kept 5/5 — 16/16 concurrent
  registrations landed where the stock tree collided in 4 of 4 cohorts; lab H-295, kept 5/5 — the
  contract survives a cold-session resume in an offline consumer repository; fixes the id half
  of #4 — the run-directory half stays open): registering on the default branch and landing
  immediately still takes `H-NNN`; on any other branch the spec is
  `hypotheses/H-DRAFT-<hash8>-<slug>.md` and the handle stands in wherever the id would appear,
  with fragment ids under the same contract. The new `scripts/id-rectify.py` gate allocates
  canonical ids at land — collision renumber, draft allocation and dedupe, fragment-id allocation,
  tolerant of a fragment without an integer id, a hash-less handle, and a handle inside any
  filename — and `--lint` names each finding class on the unrepaired branch.
  `skills/hypothesis`, `skills/intake`, and both templates carry the contract. Nothing historical
  is renamed; downgrading restores the next-free rule with zero corpus damage.
- **Async dashboard recompile** (lab H-291, kept 5/5): the Stop-event `compile-dashboard.py
  --quiet` entry is consumer-less — nothing reads its stdout — and now carries `"async": true`,
  taking its wall off the stop hot path (455 ms -> ~1.4 ms added p50) with dashboard freshness
  preserved 10/10 and zero error rows. One hooks.json flag; the script's bytes and the
  SessionStart entries are untouched.

- **Consumer contract and regression test**: `docs/id-allocation.md` (the rule, the lander's
  commands, exit codes, tolerances, upgrade and rollback) and
  `python3 scripts/selftest-id-allocation.py` (47 checks, including the tolerances for a
  fragment without an integer id, a hash-less handle, and a handle inside a filename).

## 0.3.2 — worktree-aware hook root (issue #6)
- **One resolver, the session's real tree** (lab H-DRAFT-e90628b6, counted 2x 5/5 zero-LLM): in a
  worktree-isolated session `CLAUDE_PROJECT_DIR` keeps naming the launching checkout while the
  hook payload's `cwd` lives in the worktree, so every hook graded the wrong tree — the Stop
  driver let a consumer session end with five specs open on its branch. `hyp_config.resolve_root`
  now returns the worktree's toplevel when the payload cwd sits in a **linked worktree of the same
  repository** (decided from the `.git` pointer file and its `commondir`, the two files git reads;
  no subprocess, ~1 ms), and keeps the 0.3.1 order byte-for-byte everywhere else (main checkout,
  subdirectories, foreign repositories, submodules, non-git cwds, unset variable).
- The policy interpreter imports that resolver instead of a private copy; `commit-backstop.py`
  and `session_resolver.py` route their argv root through the same check; `rel_to_root` tolerates
  symlinked prefixes (`/var` vs `/private/var`). Hardened after adversarial review of the counted
  patch: pointer and `commondir` reads accept regular files only, bounded to 4 KB (a planted FIFO
  no longer hangs the hook), and a cwd reached through a symlink into a worktree's interior
  resolves to that worktree.
- **Regression test shipped**: `python3 scripts/selftest-worktree-root.py` builds its own fixture
  under a temp dir and checks the installed plugin (13 checks, exit 0 on PASS) — run it after any
  change to the hooks.

## 0.3.1 — consumer-hardening patch (issues #3, #2)
- **Fixture-freshness gate** (H-262, kept 5/5): preflight now re-hashes every `Fixture-SHA256:` pin
  and fails closed on drift, printing the blast radius (every co-pinning spec). Pin-less specs get
  one advisory line; pins become first-class in a later corpus migration.
- **Claim-type gate** (H-258, kept 5/5): specs declare `Claim type: descriptive | normative`; a
  descriptive spec carrying decision-class On-keep rows fails with the bridging route (register a
  normative successor). Missing line = advisory only — the existing corpus predates the field.
- Both checks ride `scripts/preflight.py` + the template; zero flips on a 264-spec live corpus.

## 0.3.0 (hyp)

The flow-governance layer, plus the ratified work-graph part 2. The
flow-leak meter (`scripts/leak-meter.py` + sealed constants + `scripts/leak-status.sh`
— H-246 kept 2x5/5 2026-09-02: alarmed 227.8/1259.9/936.3 minutes before the recorded
human catch on three held-out episodes, 0 false alarms on 47 healthy ticks; harden
ADVISORY-30). The reflex chain, breach → autopsy → decision → consumption
(`scripts/reflex-check` H-251 kept 2x5/5, `reflex-collect` H-252 kept 2x5/5,
`reflex-surface` H-253 kept 2x4/4, `reflex-selftest` H-254 kept 2x4/4 — all
2026-09-02 — plus `reflex-consume.py` + ADVISORY-31, the lab's throughput-floor
consumption patch: an alarm unconsumed for 30 minutes surfaces until an action citing
it is recorded; timer plist emitted-never-loaded). The rules registry
(`scripts/compile-laws.py` H-247 kept 2x5/5, `scripts/rule-lint.py` H-248 kept 2x5/5,
the `rule-retest` decision class in `scripts/decisions.py` H-249 kept 2x5/5, and the
license-join PreToolUse advisory `hooks/scripts/license-join-check.py` H-250 kept
2x5/5 — all 2026-09-02; see `docs/flow-governance.md` for the whole layer). Direction
hygiene (`scripts/direction-lint.py`, H-243 kept 2x5/5 2026-09-01) and move fidelity
(`scripts/fidelity-manifest.py`, H-104 port). Dispatch REFILL: the actionable
frontier masks PARKED/BLOCKED-*/COUNTING status markers, and when it drops below 2
the dispatch names the licensed follow-up lanes of your `followups_file` (grammar:
`docs/workgraph.md`) — uncounted, never reordering counted runs. The 0.2.0
model-compiled orchestration (`scripts/compile-model-workflow.py`) is now ratified by
its lab keep (H-177, kept 2x5/5 2026-09-02 — frontier-exact dispatch, halt-for-ruling
discipline, 100% lineage rows on the amended fixture). This release also formalizes
the migration patches already live on 0.2.0: end-state-verified
`migrate-from-crux.sh`, standalone-safe `hyp.json` seeding, and init preserving a
consumer's customized preflight.

## 0.2.0 (hyp)

The first release under the new name; entries below this one are the
crux-era versions that 0.1.0 consolidated. Work-graph dispatch and session
durability: ranked next-item dispatch at session boundaries, claim TTL takeover,
K-strikes quarantine, reboot-surviving resume, compression-surviving re-hydration
protocol (`docs/workgraph.md`). The dispatch surface (`scripts/dispatch-status.py` +
the Stop-boundary dispatcher hook — H-213 kept 2x5/5 2026-08-29; the named-top-item
block shape measured by H-230, kept 2x5/5 2026-08-30 with the <=8-tool-call naming
window): an item is open until its COMMITTED spec-status verdict lands — no promise
string ends a cycle. The claim layer (`scripts/lane-takeover.py` — H-216 kept 2x5/5
2026-08-29; the claim join, H-215 kept 2x5/5 2026-08-29): heartbeat-TTL liveness
(ttl_s = 1800) with typed refusal and record-before-write takeover. The relaunch
governor (`scripts/dispatch-gate.py` — H-218 kept 2x5/5 2026-08-29): K=2 consecutive
non-green terminals quarantines a lane behind one committed decision card. The
scheduled resume (`scripts/hyp-resume.sh` + `scripts/install-resume-timer.sh` +
`scripts/resume-prompt.md` — H-217 kept 2x5/5 2026-08-29): an emitted-never-loaded
RunAtLoad timer plist; each firing = one dispatch read + at most one capped
adoption. The re-hydration protocol (H-231 kept 2x5/5 2026-08-30, successor to
H-162's 3/3 discard) ships as the `docs/workgraph.md` procedure: verify from the
graph observe-only, assert back in writing, dispatch by the recomputed frontier,
end only at empty frontier or a FAILURE record. The corpus layer staged at 0.1.0
flips to shipped (`scripts/compile-findings-index.py` + `scripts/prior-art-sweep.py`
— H-226/H-227/H-228 all kept 2x5/5 2026-08-29). All ports are the source lab's live
installs with paths/config adaptation only (consumer repo-root and `.claude/hyp.json`
resolution; item enumeration from the consumer's own hypotheses corpus instead of
the lab's release-train wave plan; quarantine rows typed `needs-maintainer`).
Deliberately NOT shipped: the lab's release-train reader (lab infrastructure),
`graph-check.py` + the durability-check command and the SessionStart ranked
injection (follow-on tranche per H-231/H-230's On-keep routing — not yet landed in
the lab either), and the lab's machine-specific reference plist (the installer
emits per-repo).

## 0.4.0 (crux)

Decide less, see more: the decision kit and the observatory. The decision kit
(`scripts/decisions.py` + `scripts/decisions-template.html` +
`scripts/proactive-open.sh` + `scripts/closes_when.py` + the compile-dashboard v3
merge + the SessionStart resolver v5 — see `docs/decisions.md`): one append-only
decision store in your work ledger, AskUserQuestion-grammar cards rendered FIRST on
the dashboard and regenerated whole into `decisions.html`, resolution by one CLI line
that commits just the resolution row, and decided-by/at/commit derived from git —
never stored (the source lab's H-084 keep + name-neutrality law; the CLI's
`--selftest` proves the whole loop in a throwaway repo). The clarity canon
(`docs/communication-contract.md` + `scripts/house-vocabulary.json` +
`scripts/clarity-lint.py`, measured in the source lab: naive-reader comprehension
78.6%→100% at −35% reader effort; counted hardening specs H-207..H-211 registered
there). The observatory (six counted instruments, each 2x consecutive full-pass
counted runs in the source lab, keep dates 2026-08-28 — see `docs/observatory.md`):
stall signals (`scripts/stall-signals.py`, H-154), typed flow waste
(`scripts/flow-metrics.py`, H-192 — joins 0.2.0's `waste-status.py` as the counted
typed alarm surface), identity attribution + the YOURS/OTHERS lens
(`scripts/identity-resolve.py`, H-156), metric trend derivation
(`scripts/derive-metrics.py`, H-129), the workflow-facts loop
(`scripts/emit_workflow_fact.py` + `scripts/harvest_gwt.py` + `scripts/facts_lib.py`,
H-118), and the per-keep case-study renderer (`scripts/render-case-study.py` +
`scripts/fact_fidelity.py` + `scripts/content_lint.py` + `scripts/jargon.json`,
H-201). All counted scripts ship byte-preserving from their counted artifacts; only
provenance framing, script names, and consumer-repo path resolution (`.claude/hyp.json`
`ledger_file`, plugin-home fallbacks) differ, and each file's header names its exact
divergences. Deliberately NOT shipped: the source lab's board renderer (H-036
discarded on three counted content-quality failures; successor H-212 registered and
pending) and the H-200 ask-triage reference gates (the ship ask was converted to
experiment H-206, registered; they ship on its keep — `docs/ask-triage.md`).

## 0.3.0 (crux)

External input, safely: the audited GitHub-issues intake
(`scripts/issueops-fetch.py` / `issueops-reply.py` / `issueops-teardown.py` /
`issueops_gh.py`, counted H-136 in the source lab on LIVE GitHub — 2x5/5 with outward
writes confined to the frozen allowlist; the reply templater counted twice, H-106 +
H-136 — see `docs/issueops.md`); the directive-intake kit
(`templates/DIRECTIVE-TEMPLATE.md` + `scripts/directive-lint.py` +
`scripts/directive_emitter.py`, counted H-199 — declared-before git-order,
named-divergence verification — see `docs/directive-intake.md`); the report-only ethics
extension to preflight (`scripts/preflight-rigor.py`, counted H-132; the enforcement
flip stays maintainer-gated — see `docs/preflight-rigor.md`); the channel consent
discipline (`templates/channel/` + `docs/channel-consent.md`, counted H-125 sandboxed;
live wiring stays gated on the maintainer channel-deploy ruling); the CI scaffold
normative reference (`docs/ci-scaffold.md`, counted H-198 — the excerpt-complete
refine-to-keep); and the ask-triage finding (`docs/ask-triage.md`, H-200 discarded —
the reference gates ship only on a maintainer ruling, deliberately NOT ported). All
counted scripts ship as counted from their fixture copies; only provenance framing,
script names, and consumer-repo path/account resolution differ (offline byte-parity
verified at port time).

## 0.2.0 (crux)

Environment health and review flow: the doctor pair
(`scripts/doctor-classify.py` + `scripts/doctor-remediate.py`, counted H-182/H-183 in the
source lab — see `docs/doctor.md`), the verdict-forcing review cadence
(`scripts/review-cadence.py`, counted H-188 — see `docs/review-cadence.md`), the install
parity checker (`scripts/parity-check.py`, counted H-181), and the advisory waste/flow
instrument (`scripts/waste-status.py`, uncounted-but-measured; proving specs H-192..H-196
registered in the source lab). All counted scripts ship byte-preserving from their counted
fixture copies; only provenance framing and consumer-repo path resolution differ.

## 0.1.0 (crux)

First consolidated release: the capture and experiment-loop capabilities of the
retired predecessor plugins (capture 0.1.4, experiment loop 0.1.0) fold into one
profile-gated install, joined by the operating-model lifecycle (adopt / observe / evaluate / compile /
run / verify, the node grammar + Event Modeling layer, the policy interpreter, the
diagram lane, and the model-to-executable compiler). `/hyp:init` migrates repositories
initialized by the retired plugins.
