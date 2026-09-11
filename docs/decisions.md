# The decision kit: one ledger-backed decision store, one consolidated surface

Ported from the source lab's decision kit (landed there 2026-08-28 under the consolidated
decision-making directive; the schema below is the contract the lab's code cites as
`decisions-schema.md` — this document is its shipped form, section numbers preserved
because the shipped code cites them). The kit's parts:

| Part | Role |
|---|---|
| `scripts/decisions.py` | The CLI: add / list / show / resolve / check / surface / open (+ `--selftest`, the port's own end-to-end proof in a throwaway git repo) |
| `scripts/decision_card_lint.py` | The door-field lint, rules D0-D9 with the corroboration table, called by `decisions.py add` after the shape check (standard library; `--selftest`, synthetic cards in a throwaway git repo); see "The six door fields" below |
| `scripts/decision_door_check.py` | The door evaluator, called by `decisions.py add` after the lint: every candidate is RECORDED as a two-way decision (with a veto window and a one-line undo) or RENDERED as a card carrying the findings, fail-closed (standard library; `--selftest`; a stdin/argv CLI); see "Records vs cards" below |
| `scripts/compile-dashboard.py` | Renders `DASHBOARD.md` sections 1 (DECISIONS WAITING) and 1b (DECIDED FOR YOU: the door's records with their veto windows) and regenerates `decisions.html` (the cards, plus the records block) from the template at every compile |
| `scripts/decisions-template.html` | The decision-surface template (cards, gloss tooltips, keyboard handling, resolution tray); the compiler injects SNAPSHOT / REPO / DECISIONS / stamp |
| `scripts/proactive-open.sh` | Opens `decisions.html` front-and-center ONCE per new decision id (state: `.claude/decision-surface-state.json`); a recorded id — one joined to a closing resolution — never opens and never notifies; called by `decisions.py add`/`surface` only — the compiler never opens anything |
| `scripts/closes_when.py` | The shared closes-when predicate evaluator, including `decision-resolved` (section 4) |
| `hooks/scripts/session_resolver.py` | SessionStart surfacing: open decisions print first, then the door's records (`DECISION-RECORD` lines), then unresolved ledger rows (section 6) |

One store: the configured work ledger (`.claude/hyp.json` `ledger_file`, default
`ledger/ledger.jsonl`), append-only. No second file, no parallel queue.

## 1. The card text grammar (AskUserQuestion form)

Every open decision renders — in `DASHBOARD.md` section 1, in `decisions.html`, and in
`decisions.py show` — as one card in the AskUserQuestion grammar, so the artifact a human
reads is the same shape an agent would raise interactively:

```
- [<id> | <urgency> | <age> | asked-by <requester> | class <class>( | pick many)?]
  ask: <one question, answerable by picking one option>
  [ ] <option label> — <what choosing it causes>
  [ ] <option label> — <what choosing it causes>
  (comment (stays open, <who>): "<queued comment>")*
  other: free text is a first-class answer — accept with text and no option makes the
         text the answer; option + text rides as --comment
  why-only-you: <the physical or irreversible part, one clause>
  evidence: <pointer> · <pointer>            (machine line)
  blocks: <what waits on this>               (when anything does)
  answer: python3 scripts/decisions.py resolve <id> --accept "<label>" [--comment "..."]
          deny: ... --deny · comment: ... --comment "..."
```

Card prose follows the communication contract (`docs/communication-contract.md`): impact
first, house terms glossed (`scripts/house-vocabulary.json`; `decisions.html` renders the
glosses as tooltips), 2-4 verb-labeled options, consequence and reversibility per option.
`scripts/clarity-lint.py card <card.md>` is the mechanical check.

Legacy compat: an open `[closes-when: maintainer-ruling=<slug>]` ledger row that no open
decision `shadows` renders as a compat card with a `resolve --legacy <slug>` answer line,
so nothing waits invisibly during migration.

## 2. The decision row (`kind:"decision"`)

One JSONL line in the work ledger, appended by `decisions.py add` (id race-checked:
max-on-file + 1):

```json
{"kind": "decision", "id": "DEC-001", "date": "YYYY-MM-DD",
 "requested_at": "YYYY-MM-DD", "requested_by": "<lane or person asking>",
 "title": "<one line>",
 "ask": {"question": "...", "header": "<=12 chars", "multiSelect": false,
          "options": [{"label": "...", "description": "...",
                       "undo": "git-revert|flag|amendment|ledger-row|none"}, ...]},
 "context_pointers": ["<repo-relative pointer>", ...],
 "blocks": ["<what waits>", ...],
 "urgency": "high|normal|low",
 "class": "publish|spend|schema|live-surface|plan|hygiene",
 "why_only_you": "<one clause>",
 "shadows": ["maintainer-ruling=<slug>", ...],
 "retest_when": "<predicate>=<argument>",
 "note": "<optional>",
 "staged_artifact": ["<repo-relative path>", ...] | "<one command line>" | "none",
 "evidence": "<repo-path>@<sha40>#L<a>-L<b>" | "none-exists",
 "externality": "none|other-humans|external-publication|spend-beyond-granted-budget|physical-act|classifier-flagged|reserved-in-his-words",
 "recommended": "<one option label>|none",
 "default_on_silence": "<one option label>|nothing-changes",
 "amount_usd": <number >= 0, required iff class is spend>,
 "door": {"fields_sha": "<sha256 of the six fields as canonical JSON>",
          "outcome": "RECORD|CARD", "evaluator": "<sha7 of decision_door_check.py>", "head": "<sha7>",
          "clauses": {"W1": "yes", "W2": "no", "H1": "...", "T1": "...", "T2": "...", "T3": "...", "STREAK": "no"},
          "hard": "<class, on a hard card>", "corroborated_by": "<its observable>",
          "findings": ["D8:...", "NOT-EVIDENCE-DECIDED: ...", ...]}}
```

Validation (`decisions.py check`, also run at `add`): id matches `DEC-NNN`; date, title,
requested_by, why_only_you non-empty; urgency and class from the enums; ask carries a
question, a 1-12-char header, a boolean multiSelect, and 2-4 options each with label +
description. `decided_by` / `decided_at` / `resolution_commit` are FORBIDDEN on any row —
see section 3. The six door fields and the `door` object are the lint's ("The six door fields" below): `add`
refuses a candidate that lacks or malforms one, and `check` re-validates their shape on every row past
the legacy boundary (`.claude/hyp.json` `decision_door_legacy_max_id`, or shape and order when the key is
absent -- "Legacy rows and the boundary" below).

## 3. The resolution row (`kind:"decision-resolution"`) and git-derived attribution

Resolving appends one row and commits JUST that line under the invoker's git identity
(message: `decision: <id> <disposition> — decision-resolved=<id>`):

```json
{"kind": "decision-resolution", "id": "DEC-001", "date": "YYYY-MM-DD",
 "disposition": "accepted|denied|commented",
 "chosen_options": ["<label or free text>", ...],
 "comment": "<optional>"}
```

A record's resolution row — appended by `add` itself when the door routes RECORD, never by a human —
carries three more keys: `basis: "two-way-door"`, `undo` (one executable line) and `veto_open_until`
(`requested_at` + 7 days); its `comment` names the evidence, the undo and the veto command ("Records vs
cards" below).

Status is DERIVED at read time by joining resolutions on id: no resolution = `open`; a
latest `commented` row = `commented` (STAYS OPEN); the latest `accepted`/`denied` wins.

**The attribution law (multi-user).** Who decided, when, and in which commit are NEVER
stored in the row — they derive from the git commit that introduced the resolution line
(the compiler binary-searches the ledger-touching commits; the store is append-only, so
a line's presence is monotone). This is the source lab's H-084 keep plus its
name-neutrality ruling applied to decisions: git author identity is the only identity,
a stored name could drift from it, and an uncommitted resolution honestly renders as
`staged (provenance pending its commit)`. Because attribution is the commit author,
every decider resolves under their own `git config` identity — multiple users share one
store with zero coordination beyond ordinary commits, and `.mailmap` /
`contributors.json` (see the README's identity section) make the rendered names legible.

**Routing (optional).** A `DECIDERS` JSONL file beside the ledger routes cards:
`{"match": "<DEC-id or class>", "owner": "<who>"}`. Unrouted rows default to owner
`you` — absence of routing fails toward asking, never toward silence. Section 1's header
counts `yours N | others N` from these routes.

## 4. The `decision-resolved` closes-when predicate

`[closes-when: decision-resolved=DEC-NNN]` joins the predicate grammar in
`scripts/closes_when.py`, the compiler, and the session resolver: satisfied iff an
`accepted|denied` resolution row for that id exists in the ledger AT HEAD. Committed
state only, like every other predicate — a staged resolution closes nothing. This lets
ordinary commitment rows wait on a decision without dual bookkeeping.

## 5. The three-shape ledger normalizer

Readers of the work ledger (`compile-dashboard.py`, `session_resolver.py`,
`decisions.py`) normalize THREE row shapes; a line is malformed only when it is
unparseable JSON or none of the three:

1. legacy `{date, slug, hit[, kind, assignee]}` — unchanged;
2. v2 `{kind, id, date, text[, closes_when][, assignee]}` — normalized as `slug := id`,
   `hit := text + " [closes-when: <closes_when>]"`;
3. the decision pair (`kind:"decision"` / `kind:"decision-resolution"`) — joined on id
   at read time, never rendered as ordinary rows.

## 6. The surfaces

- `DASHBOARD.md` section 1 — DECISIONS WAITING, first thing a cold reader sees; ages
  derive from the header stamp (HEAD commit date), never the wall clock, so the render
  stays deterministic and committable. Section 1b — DECIDED FOR YOU — lists the door's
  records with their veto windows, undo and veto commands ("Records vs cards" below).
- `decisions.html` — regenerated whole at every compile; answering on the page stages
  the exact `decisions.py resolve` command in a visible tray (the ledger row is the
  record; the page is its shadow).
- SessionStart — the resolver prints one line per open decision FIRST (the hook pipes
  through `head -20`), then the summary, then unresolved ledger rows:
  `DECISION-LEDGER\t<id>\t<urgency>\t<title>\t<blocks>` … `DECISIONS-OPEN\t<count>\toldest <id> <age>d`,
  then one `DECISION-RECORD\t<id>\tveto-until <date> (open|closed)\t<title>\tundo=<line>` per live record
  and `DECISION-RECORDS-OPEN\t<open windows>\toldest <id> <age>d`.
- Proactive open — `decisions.py add`/`surface` run `proactive-open.sh`: recompile,
  open `decisions.html` once per NEW id, notify; a crash before the state write re-fires
  safely; a surface with no new ids does nothing (no re-open spam). A recorded id is never
  new: `add` skips the opener for a RECORD and the script skips every id joined to a
  closing resolution.

## 7. The `retest_when` evidence trigger (optional)

A decision that is parked on missing information carries what would reopen it as a
committed field, `retest_when: "<predicate>=<argument>"`, in the shared retest-when grammar
of `scripts/closes_when.py` — `event-count=<event id>[:<subject prefix>]>=N`,
`metric-crosses=<metric id><op><threshold>@last=K`, `evidence-received=<target>`. That module
is the only parser: `decisions.py add` validates the field through it (an unknown predicate
or a malformed argument is `ADD-INVALID` with one typed reason) and nothing here re-implements
the grammar. Evidence is read at committed HEAD only; a date is never a trigger.

`decisions.py check` prints two EXIT-NEUTRAL report classes — they never count as findings and
never change the land gate's exit code:

| Line | Meaning |
|---|---|
| `DECISIONS-CHECK<TAB>RETEST-DUE<TAB><id><TAB><path>@<sha40>#L<n><TAB><predicate>=<argument>` | an accepted or denied decision whose predicate holds at HEAD; the pointer is the last line of the evidence span |
| `DECISIONS-CHECK<TAB>REVISIT-UNARMED<TAB><id><TAB><field>` | a decision whose scanned text matches `\b(revisit\|later)\b` while the row carries no `retest_when` (field scope: open rows — title, question, every option; closed rows — chosen options and the resolution comment) |

`scripts/review-cadence.py` re-presents every `RETEST-DUE` row in a `RETEST DUE` block above
`REVIEW DEBT`, carrying the evidence pointer; `next-touch <date>` stays legal only for one-way-door
holds (`docs/review-cadence.md`). Arming an already-filed decision is an appended row, never an
edit. Kept in the source lab as H-DRAFT-d564bb31-decision-retest-when (5/5 twice); the wider
story is `docs/decision-durability.md`.

## CLI reference (`python3 scripts/decisions.py ...`)

| Command | Effect |
|---|---|
| `add --title ... --question ... --header ... --option L:D --option L:D --requested-by ... --class ... --why-only-you ... --undo U --undo U --staged-artifact P\|CMD\|none --evidence PTR\|none-exists --externality CLASS --recommended LABEL\|none --default-on-silence LABEL\|nothing-changes [--amount-usd N] [--urgency high] [--pointer P] [--blocks "a, b"] [--shadows maintainer-ruling=slug] [--multi] [--no-open] [--door-git-timeout S] [--door-inject-fault]` | Validate (shape, then the door lint D0-D9, then the door evaluator) + append one decision row (id race-checked) — and, when the evaluator RECORDS it, its resolution row right after (`recorded <id>:`, no card opens) — then proactive-open for a CARD (`added <id>:`). Exit 0 PASS; 1 ESCALATE (appended with `door.findings`, one `ADD-FINDING` line first); 2 MALFORMED / MALFORMED-BATCH (`ADD-REFUSED` lines, nothing appended) |
| `list [--json]` | One line per decision with derived status (join, no git) |
| `show <id>` | The full card with git-derived resolution provenance and, on an evaluated row, the `door-outcome:` line |
| `resolve <id> --accept "<label-or-free-text>" [--comment "..."]` | Accept (repeat `--accept` when multiSelect); commits JUST the resolution line |
| `resolve <id> --deny [--comment "..."]` | Deny and close. On a recorded id (`basis: two-way-door`) this is the veto: accepted without `--reopen`, the deny wins the join, and the record's undo runs as an attributed follow-up (`VETO` line) |
| `resolve <id> --comment "..."` | Comment — the decision STAYS OPEN |
| `resolve <id> ... --reopen` | Append another closing row over an already-closed id |
| `resolve --legacy <slug> --accept "done"` | Compat shim: answer a legacy maintainer-ruling bracket with no decision row (emits + commits the raw-dir ruling capture) |
| `check` | Schema + join validation over every row; exit 1 on findings; also prints the exit-neutral `RETEST-DUE` / `REVISIT-UNARMED` lines (section 7) and `DOOR-UNAUDITED` for a non-legacy decision row with no door outcome |
| `surface [--no-open]` | Print the open-decision lines + summary; proactive-open (once-per-id guard) |
| `open [--all]` | Open `decisions.html` (`--all` also opens `DASHBOARD.md`) |
| `--selftest` | The full loop in a throwaway git repo, plus the `retest_when` scenario (unknown predicate refused; an armed row fires `RETEST-DUE` only after its evidence commit; a "later" option with no trigger is `REVISIT-UNARMED`); exits 0 only if every assertion passes; plus the door wiring (a field-less card refused with exit 2, a self-declared two-way card appended with a D8 finding and exit 1, `show` rendering the door lines, the seeded git stall exit-neutral, `check` exempting legacy ids); plus the door evaluator (a zero-information two-way card RECORDED with its resolution pair and a 7-day veto window, never entering the surface state file, `resolve --deny --comment veto` flipping it to denied and running the undo that restores the tree, `show` rendering the outcome, a side-door row `DOOR-UNAUDITED` at exit 0, the crash seed rendering a card). `python3 scripts/selftest-decision-door-fields.py` runs this and the lint's own `--selftest` together; `python3 scripts/selftest-decision-door-evaluator.py` runs this and the evaluator's |

Flags `--no-commit` (stage the resolution uncommitted) and `--no-recompile` exist for
tests. `resolve` refuses to run while the ledger has unrelated uncommitted changes — the
resolution commit must contain just the resolution line (pass `--no-commit` to stage
instead).

## What a decision is for

A decision card exists only for what is genuinely a human's: irreversible effects, other
people, or a physical act only the human can perform. Everything reversible — anything
that lands as a commit — proceeds without a card (`docs/communication-contract.md`,
"Clarity is subtraction"). If a card keeps needing a third option, the ask is
unconverted work: send it back to triage, convert it into a commit or an experiment.

### The six door fields

Every candidate carries six structured fields, so a script — not a reader — tells a two-way
question from one only the maintainer can answer. Ported from the source lab's
H-DRAFT-5f02c694-decision-card-door-fields (kept 2026-09-11, 5/5 in two counted runs: 123/123
planted single-defect mutants caught with their rule id, 0 findings on the row-clean historical
cards, 34/34 re-cuts labelled from the fields alone, legacy rows byte-identical; the unpatched
`add` accepted 170/170 of the same cards). `add` copies them from these flags:

| Field | Level | Flag | Vocabulary or shape |
|---|---|---|---|
| `undo` | per option | `--undo` (repeat; pairs positionally with `--option`) | `git-revert` \| `flag` \| `amendment` \| `ledger-row` \| `none` |
| `staged_artifact` | card | `--staged-artifact` (repeat per path) | a non-empty list of repo-relative paths the recommended option changes; or the one command line it runs (one value containing whitespace); or the literal `none`, permitted only while `recommended` is `none` |
| `evidence` | card | `--evidence` | `<repo-path>@<sha40>#L<a>-L<b>`, or the literal `none-exists` (the honest "this is a hypothesis, not a question") |
| `externality` | card | `--externality` | `none` \| `other-humans` \| `external-publication` \| `spend-beyond-granted-budget` \| `physical-act` \| `classifier-flagged` \| `reserved-in-his-words` |
| `recommended` | card | `--recommended` | exactly one existing option label, or `none` |
| `default_on_silence` | card | `--default-on-silence` | one existing option label, or `nothing-changes` |
| `amount_usd` | card, required iff `class=spend` | `--amount-usd` | a number >= 0 |

The externality vocabulary is closed. `none` means a two-way door: nothing leaves the
repository and no human but the decider is affected. Each hard class has one observable over
`staged_artifact` and the cited artifacts; the lint corroborates the declaration against it
(rule D4): `externality: none` beside a POSITIVE observable is the finding
`EXTERNALITY-UNDECLARED:<class>`, a declared hard class beside a NEGATIVE observable is
`DECLARED-HARD-UNCORROBORATED:<class>`.

| `externality` | Meaning | Observable (POSITIVE when) |
|---|---|---|
| `other-humans` | the staged command reaches people outside this repository | a command whose target repository or host lies outside the owned set (`getfatday/cause-n-effect`, `getfatday/hyp-machine`; `localhost` is local), or that carries one of the words `invite`, `email`, `slack`, `reply` |
| `external-publication` | the staged command publishes outside the worktree | `gh release`; `gh repo create\|rename\|edit`; a `marketplace.json` file among the paths or in the command; `git push` with an explicit refspec to a branch other than the current one (or `--tags`); a public URL |
| `spend-beyond-granted-budget` | `amount_usd` exceeds a budget the maintainer granted | a `$N`, `US$N`, `N USD` or `N dollars` number on the cited lines of a `research/raw/` maintainer capture or `program.md` named in `context_pointers`; no such line is NEGATIVE |
| `physical-act` | only a human at the keyboard can perform it | a token from the frozen list in the staged artifact: `device code`, `op signin`, `signing key`, `gpg`, `launchctl load`, `click`, `keystroke`, `your eyes` |
| `classifier-flagged` | a harness denial for the staged command is on record | a row of `ledger/hook-denials.jsonl` at HEAD whose `command` equals the staged command |
| `reserved-in-his-words` | the maintainer reserved this in his own committed words | `why_only_you` or `context_pointers` cites a `research/raw/` maintainer capture (filename ending `-ruling.md`, `-directive.md` or `-grant.md`, or first five lines matching `maintainer.*(verbatim\|ruling\|directive\|grant)`) whose cited lines carry the noun reserve/reserved |

The lint (`scripts/decision_card_lint.py`) runs inside `add` after the shape check, under the
preflight exit contract, and prints one tab-separated audit line per finding before anything
else — no refusal is silent:

| Exit | Rules | `add` prints | Row |
|---|---|---|---|
| 0 PASS | none fired | the `added ...` lines | appended with `door.fields_sha` (sha256 of the six values as canonical JSON) |
| 1 ESCALATE | D2 UNDO-CORROBORATED (a revertible `undo` beside a staged path outside the worktree, or `none` beside all-tracked paths); D3 EVIDENCE-RESOLVES, resolution leg (`git show <sha>:<path>` fails, the lines do not exist, or the target is not an evidence class: `experiments/runs/**/{VERDICT.json,VERIFY.md,grade*,RUN-RECORD*}`, a `research/raw/` maintainer capture, `kind:"decision-resolution"` lines of the configured work ledger -- `.claude/hyp.json` `ledger_file`, default `ledger/ledger.jsonl` -- a `hypotheses/*.md` Runs-table row or Status `kept` line); D4 EXTERNALITY-CORROBORATED, corroboration leg (table above); D8 SELF-DECLARED-TWO-WAY (`why_only_you` or `note` carries `two-way door`, `proceeds under the standing grant`, `Nothing is blocked`, `default advisory` or `keep-advisory`); D9 BLOCKING-TWO-WAY (`blocks` non-empty while no option's `undo` is `none` and `externality` is `none`) | `ADD-FINDING<TAB><id><TAB><rule><TAB><detail>` per finding, then `added ...` | appended with the findings under `door.findings`; renders as before |
| 2 MALFORMED | D0 STAGED-ARTIFACT, D1 UNDO-VOCAB, D3 EVIDENCE-RESOLVES (form), D4 EXTERNALITY-CORROBORATED (form: vocabulary; `class=spend` without a numeric `amount_usd`), D5 DEFAULT-STATED, D6 RECOMMENDED-ONE — every defect listed in one pass; then D7 DEDUP-WHY (at least two uncommitted decision rows already share the candidate's `why_only_you` byte-identically: one policy question filed N times) | `ADD-REFUSED<TAB>MALFORMED<TAB><rule><TAB><detail>` per defect (`MALFORMED-BATCH` for D7) | nothing appended |

A malformed card gets its field list and nothing else: D2-D4 and D8-D9 are not evaluated. Every
git read runs under one timeout (`--door-git-timeout`, default 20 s); a stall prints one
exit-neutral `ADD-TIMEOUT<TAB><rule>` line for that rule and never a finding.

#### Legacy rows and the boundary

Legacy rows — cards appended before the fields existed — are exempt and never re-validated. The
boundary is the consumer's, read by `check` from `.claude/hyp.json`:

| `decision_door_legacy_max_id` | Legacy rows |
|---|---|
| an integer N (set it when you know your pre-upgrade count) | every row whose numeric id is at or below N |
| absent (the default) | shape and order: every row appended before the first row that carries a `door` object; a door-less row appended after that first row is gated |

Either way a consumer with any number of pre-upgrade cards upgrades cleanly, every post-upgrade
path is gated, and `add` exempts nothing (a legacy-shaped card is refused whatever its id). `check`
re-validates only the fields' shape (the exit-2 class) on the other rows; `show` prints the
`door:` / `door-evidence:` / `door-finding:` lines only for rows that carry a `door` object, so
legacy rows render byte-identically.

#### Callers: all shipped callers pass the fields

Every shipped writer of a `kind:"decision"` row passes the six fields and the lint, with values
that say what its card is:

| Writer | `undo` per option | `staged_artifact` | `evidence` | `externality` | `recommended` | `default_on_silence` | Lint result on these values |
|---|---|---|---|---|---|---|---|
| `retest-trigger.py` (rule-retest card) | `ledger-row`, `ledger-row` — a retest's verdict flips the registry by appended row, a retirement is an appended status row | `none` | the first committed `<path>@<sha40>#La-Lb` span the predicate matched (`context_pointers` carries every span) | `none` | `none` | `nothing-changes` | ESCALATE, appended: `D3 EVIDENCE-NOT-AN-AUTHORITY` (a stream span is a signal, not a verdict or ruling) and `D9 BLOCKING-TWO-WAY` (`blocks: ["rule/<id>"]`, the dedup key, beside two revertible options) |
| `knob-observe.py` (gate-stance card) | `git-revert` (apply-plan: one node edit in its own commit), `ledger-row` (hold-advisory: the resolution row is the record) | the knob node path | `none-exists` (its pointers are a node path, a stream sha256 and a state-row key, none a committed span) | `none` | `apply-plan` | `nothing-changes` | PASS in a git consumer whose node is tracked |
| `dispatch-gate.py ingest` (K-strikes quarantine row, class `spend`) | `ledger-row` (relaunch: the closing row lifts the quarantine), `git-revert` (retire: a spec-status commit) | `none` | `none-exists` (strike terminals are run artifacts) | `none` | `none` | `nothing-changes` (the quarantine stands) | ESCALATE, appended: `D9 BLOCKING-TWO-WAY` (the row gates the lane's relaunch); `amount_usd` is the declared per-run budget, else the recorded spend per run |
| `reflex-surface file` (breach-autopsy row) | `git-revert` (fix-now: a remediation commit), `ledger-row` (accept-risk: the closing row) | `none` | `none-exists` (an incident dir has no committed span) | `none` | `none` | `nothing-changes` (the bucket keeps surfacing) | PASS |

`retest-trigger.py` and `knob-observe.py` pass the flags to `add` and read the `added <id>:` line
as the filed signal — exit 1 (ESCALATE) is a filed row, only exit 2 files nothing; the trigger
echoes `ADD-FINDING` / `ADD-TIMEOUT` lines as `# door <rule-id>: ...` commentary. `dispatch-gate.py`
and `reflex-surface` build their rows in-process and call `door_lint_row()` (the lint `add` runs,
stamping `door` on PASS and ESCALATE) before `append_line`; a MALFORMED row is refused — the gate
reports it under its JSON `error` (exit 2) and the reflex exits `FATAL` — and both surface the
audit lines (`door_audit_lines()`; the gate under its JSON `door` key). `append_line` itself
refuses a `kind:"decision"` row that carries no `door` object, so no path, shipped or not, appends
a field-less card. A copy of `decisions.py` needs `decision_card_lint.py` beside it, or `add`
exits with `FATAL: scripts/decision_card_lint.py ... is not beside decisions.py`; the callers'
selftests copy both.

#### Ported constants

The lint shipped here differs from the lab's sealed copy
(`experiments/runs/H-DRAFT-5f02c694-decision-card-door-fields/fixture/impl/decision_card_lint.py`
in the source lab, sha256 `fa88cde74093cafc5d04a2177ffaf12e5c3a30c75a0873d82b975b7362289c82`) in
one constant and its plumbing, so the ledger it reads is the consumer's: `LEDGER_REL_DEFAULT` is
the plugin default `ledger/ledger.jsonl` (the sealed copy carried the lab path
`ledger/work-ledger.jsonl`), `ledger_rel_for(root)` resolves `.claude/hyp.json` `ledger_file`
over it (the key and default `decisions.py` uses), `lint()` takes that resolved path when none is
handed in and passes its repo-relative form to D3, whose decision-resolution authority is the
configured ledger (`evidence_class` and `d3_evidence_resolves` gain a `ledger_rel` parameter).
The shipped file's sha256 is `3268bb88cc408c591d69fdb19c7d812909712697ff737253b40507e8daeb694d`:
14 lines removed and 49 added against the sealed copy — one docstring paragraph, the two constant
lines, the resolver, two signatures and their two call sites, two lines in `lint()`, and the
selftest's scratch repository configuring its ledger through `.claude/hyp.json` plus five new checks
(64 to 69). Rules D0-D9, their vocabularies, the corroboration table and the output grammar are
unchanged; the lab keeps its path because its `hyp.json` says `ledger/work-ledger.jsonl`. Still
lab-shaped by the sealed treatment and left for a refine lane: the refusal sentence's
`research/raw/...-grant.md` citation, `ledger/hook-denials.jsonl`, `research/raw/`, `program.md`,
`experiments/runs/`, `hypotheses/` and the owned-repository set `LAB_OWNED_REPOS`.

### Records vs cards

Ported from the source lab's H-DRAFT-73404199-decision-door-evaluator (kept 2026-09-11, 5/5 in two
counted runs, the second by a cold README-only executor, two independent cold verifiers agreeing:
0/300 false refusals over the keyed one-way cards -- the maintainer's own three choices included --
55/55 planted two-way cards recorded, 86/86 undos restoring the pre-record state byte for byte, 5/5
seeded vetoes denied and undone, the control `add` on the same fields base admitting 204/204 two-way
cards; journal fragment 0489). The six fields say what a card is; the evaluator acts on it. Between
the lint and the append, `scripts/decision_door_check.py` evaluates every candidate from its fields
and committed state alone -- no clock beyond `requested_at`, no environment, no network, no process
beyond `git`, no LLM -- and either RECORDS it as the repository's own two-way decision or lets it
RENDER as a card. Anything it cannot verify renders. No bypass flag exists.

#### The clauses (top to bottom; the first routing clause that fires wins)

| Clause | Reads | Routes |
|---|---|---|
| W1 FIELDS | every option's `undo` in vocabulary; `staged_artifact`, `evidence`, `externality`, `recommended`, `default_on_silence` present and in vocabulary; `class=spend` => `amount_usd`; `recommended != none` => an existing option label | no -> MALFORMED: every defect listed as `ADD-REFUSED<TAB>MALFORMED<TAB>W1<TAB>...`, exit 2, nothing appended |
| W2 DEDUP-WHY | at least two UNCOMMITTED decision rows already share the candidate's `why_only_you` byte for byte | yes -> MALFORMED-BATCH ("one policy question filed N times: file one"), exit 2, nothing appended |
| H1 HARD | `externality != none`: the class's observable (the lint's table above; `classifier-flagged` reads `.claude/hook-denials.jsonl` in the worktree, a row whose `command` equals the staged command) | POSITIVE -> CARD carrying `hard=<class>` and `corroborated_by`; UNVERIFIABLE -> CARD (`CORROBORATION-UNVERIFIABLE`); NEGATIVE -> finding `DECLARED-HARD-UNCORROBORATED:<class>`, continue |
| T1 UNDO | every option's `undo != none`; the recommended option's undo corroborated against the staged artifact: `git-revert` / `flag` / `amendment` need every staged path inside the worktree, `ledger-row` needs the staged artifact to be exactly the configured ledger; a command line corroborates nothing (`UNDO-UNVERIFIABLE`) | `none` on all-tracked paths -> `UNDO-MISDECLARED`; any `none` -> `UNDO-NONE`; outside the worktree -> `UNDO-UNCORROBORATED`; each is a no |
| T2 EVIDENCE | in order: (a) a pointer that fails to resolve at HEAD -> CARD (`EVIDENCE-UNRESOLVED`); (b) `recommended != none` and `default_on_silence == recommended` -> yes (zero-information: silence already yields the recommendation); (c) `recommended != none` and the pointer resolves inside the evidence classes (a `VERDICT.json`, a run grade, a `RUN-RECORD`, a `VERIFY.md`, a maintainer capture, a `decision-resolution` line of the configured ledger, a Runs-table row) -> yes; (d) otherwise no (`NOT-EVIDENCE-DECIDED`). Amendment guard after a yes: a staged `hypotheses/*.md` whose frozen span (the `## Binary assertions` heading line through the `## Verdict rule` heading line) differs from HEAD -> no (`FROZEN-SPAN-TOUCHED`) | |
| T3 EXTERNALITY | all six observables over the candidate, a class already NEGATIVE at H1 staying NEGATIVE | any POSITIVE -> no (`EXTERNALITY-UNDECLARED:<class>`); any UNVERIFIABLE -> CARD; a `.github/` or `.changeset/` path with no `SHIP.md` staged beside it is an undeclared `external-publication` |
| ROUTING | T1 and T2 and T3 | all yes -> RECORD; otherwise CARD with every finding; an exception or a git read past `--door-git-timeout` (default 20 s) -> CARD (`EVALUATOR-FAIL-CLOSED`) |
| STREAK | the last 10 door outcomes on file (committed rows plus the uncommitted tail) all RECORD with no later veto | yes -> this candidate renders (`EVALUATOR-STREAK`): the auto-mode fallback shape, so a run of records is never silent |

#### What `add` prints and appends

| Outcome | `add` prints (after the lint's own lines) | Rows appended | Exit |
|---|---|---|---|
| RECORD | one `DECISION-DOOR<TAB><id><TAB>RECORD<TAB>T1=...<TAB>T2=...<TAB>T3=...<TAB>H1=...` audit line, a plain-English block ending "Proceed now. Do not wait on this row, and never gate a driver on it.", then `recorded <id>: ...` | the decision row with `door.outcome RECORD`, then the resolution row below; `proactive-open.sh` does not run -- nothing opens, nothing notifies | 0 (1 when the lint attached a finding) |
| CARD | one `DECISION-DOOR<TAB><id><TAB>CARD<TAB>H1=yes(<class>: ...)` line (a hard card) or `...CARD<TAB>fail-closed|not-proven|EVALUATOR-STREAK(...)<TAB><findings><TAB>T1=...` (an unproven one), then `added <id>: ...`; the card opens as before | the decision row with `door.outcome CARD` and every finding under `door.findings` | 0 (1 when the lint attached a finding) |
| MALFORMED / MALFORMED-BATCH | `ADD-REFUSED` lines and the two refusal sentences | nothing | 2 |

The `door` object on every evaluated row: `{"fields_sha", "outcome": "RECORD|CARD", "evaluator": <sha7 of
decision_door_check.py>, "head": <sha7>, "clauses": {"W1", "W2", "H1", "T1", "T2", "T3", "STREAK"?}, "hard"?,
"corroborated_by"?, "findings"?}` -- the lint's `D<k>:` findings first, the evaluator's after. `show` prints it
as `door-outcome: RECORD | evaluator <sha7> | head <sha7>[ | hard <class>]`.

#### The RECORD row

```json
{"kind": "decision-resolution", "id": "DEC-NNN", "date": "YYYY-MM-DD", "disposition": "accepted",
 "chosen_options": ["<recommended>"], "basis": "two-way-door",
 "undo": "git revert --no-edit $(git log -1 --format=%H --grep='^landing: decision-record=DEC-NNN ')",
 "veto_open_until": "<requested_at + 7 days>",
 "comment": "decided by policy/no-card-for-two-way-doors on <the evidence pointer, or zero-information>; undo: ...; veto: python3 scripts/decisions.py resolve DEC-NNN --deny --comment veto"}
```

`add` appends it right after the decision row and stamps `date` itself (the evaluator reads no clock). It is
an ordinary resolution row: the join reads it as `accepted`, `check` validates it as before, `list` and
`show` render it, and attribution derives from the commit that lands it like any other. The `undo` line is
one executable command: for `git-revert`, `flag` and `amendment` records it reverts the landing commit --
the commit that lands the recorded change declares itself with a subject starting `landing:
decision-record=DEC-NNN ` (the revert commit's own subject, `Revert "landing: ..."`, never matches the
anchored pattern); for a `ledger-row` record the undo IS a superseding deny row
(`python3 scripts/decisions.py resolve DEC-NNN --deny --comment '...'`).

#### The veto window and the undo

Every record stays open to a one-word veto for seven days from `requested_at` (`veto_open_until`), and the
undo stays available after the window too. The veto is

```
python3 scripts/decisions.py resolve DEC-NNN --deny --comment veto
```

`resolve --deny` on a `basis: two-way-door` id is accepted although the id is already closed (no `--reopen`
needed): the deny row is appended and committed as usual -- the latest closing disposition wins the join, so
the record reads `denied` everywhere -- and then the resolver runs the record's `undo` line as an attributed
follow-up, printing `VETO<TAB><id><TAB>undo executed rc=<n><TAB><line>` (and `VETO-UNDO-FAILED<TAB><id><TAB><tail>`
when the command fails: for example no landing commit exists yet, in which case nothing else changes). A
`ledger-row` record prints `VETO<TAB><id><TAB>undo is this superseding row (ledger-row); nothing else to execute`;
`--no-commit` prints the undo without executing it. The dashboard header counts vetoed records; a vetoed record
leaves the DECIDED FOR YOU list and the resolver's `DECISION-RECORD` lines.

#### The streak guard and the fail-closed seeds

Ten consecutive records on file with no veto make the eleventh two-way candidate render as a card with
`EVALUATOR-STREAK`; a veto, or any card, breaks the streak. The evaluator never fails open: an exception
inside it (`add --door-inject-fault` is the test seed), a git read past `--door-git-timeout`, a dangling or
malformed `evidence` pointer, an unparseable row in `.claude/hook-denials.jsonl`, a staged spec absent from
the worktree, an external host in the staged command -- each renders a card carrying its finding, and the
record fields are dropped.

#### Surfaces

- `DASHBOARD.md` section `1b. DECIDED FOR YOU (veto open: N · recorded: N · vetoed: N · cards with findings: N)`:
  one block per live record -- `decided:`, `because:`, `undo:` (one command), `veto:` (one word) and the lab's
  note when the row carries findings; `(none — ...)` when nothing has been recorded. `decisions.html` carries
  the same records in a `<section class="decided-for-you">` block before `</body>`. Both derive ages and windows
  from the header stamp, never the wall clock.
- SessionStart, after the open cards: `DECISION-RECORD<TAB><id><TAB>veto-until <date> (open|closed)<TAB><title><TAB>undo=<line>`
  per live record, then `DECISIONS-RECORDS-OPEN`-style summary `DECISION-RECORDS-OPEN<TAB><open windows><TAB>oldest <id> <age>d`
  (`DECISIONS_TODAY` pins the window computation for tests).
- `proactive-open.sh` skips every id joined to an accepted or denied resolution, so a record never opens the
  browser and never fires the OS notification; only cards do.
- `check` prints `DECISIONS-CHECK<TAB>DOOR-UNAUDITED<TAB><id><TAB>decision row carries no door outcome (filed outside add, or before the evaluator)`
  for a non-legacy decision row with no `door.outcome` -- exit-neutral, surfaced, never enforced.

#### Callers

Every shipped writer passes the evaluator after the lint. `retest-trigger.py` and `knob-observe.py` go
through `add` and read `added <id>:` OR `recorded <id>:` as the filed signal (their current fields render:
`recommended none`, or `evidence none-exists` beside a default that is not the recommendation). `dispatch-gate.py
ingest` and `reflex-surface file` call `decisions.door_evaluate_row(row, root)` after `door_lint_row()` and
before `append_line`; on RECORD they append `decisions.door_record_row(result)` beside the row and skip the
opener; a MALFORMED-BATCH verdict refuses the row (the gate under its JSON `error`, the reflex `FATAL`). A copy
of `decisions.py` needs `decision_door_check.py` and `decision_card_lint.py` beside it; the callers' selftests
copy all three. `scripts/selftest-decision-door-evaluator.py` runs the evaluator's and the kit's selftests
together.

#### Ported constant

The evaluator shipped here differs from the lab's sealed copy
(`experiments/runs/H-DRAFT-73404199-decision-door-evaluator/fixture/impl/decision_door_check.py` in the
source lab, sha256 `b5640819c2c6e8d3d53f71b7cfc7f6ad8b468ee8b2d2232c158a6df36a92fd63`) in one constant and its
plumbing, so the ledger it reads is the consumer's: the lab path constant `LEDGER_REL = "ledger/work-ledger.jsonl"`
is gone; `run()` resolves the ledger through the lint's `ledger_rel_for(root)` (`.claude/hyp.json` `ledger_file`,
default `ledger/ledger.jsonl`) when none is handed in, stores its repo-relative form on the result
(`ledger_rel_of`), T1 corroborates a `ledger-row` undo against that path, and T2 hands it to the lint's
`evidence_class` as the decision-resolution authority. The stamp `door.evaluator` is the sha7 of the shipped
file (so it differs from the counted runs' `b564081`), and the selftest gains two checks for the consumer's
ledger through `.claude/hyp.json`. One docstring paragraph names the port. Every clause, text, exit code and
the record shape are unchanged, and the source still carries no card or spec id literal. Still lab-shaped by
the sealed treatment and left for a refine lane: the citations in the plain-English block
(`research/raw/...-grant.md`, `...-directive.md`, a lab journal fragment), the `hypotheses/` prefix of the
amendment guard, and the evidence classes the lint hard-codes (`experiments/runs/`, `research/raw/`,
`program.md`).
