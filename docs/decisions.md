# The decision kit: one ledger-backed decision store, one consolidated surface

Ported from the source lab's decision kit (landed there 2026-08-28 under the consolidated
decision-making directive; the schema below is the contract the lab's code cites as
`decisions-schema.md` — this document is its shipped form, section numbers preserved
because the shipped code cites them). The kit's parts:

| Part | Role |
|---|---|
| `scripts/decisions.py` | The CLI: add / list / show / resolve / check / surface / open (+ `--selftest`, the port's own end-to-end proof in a throwaway git repo) |
| `scripts/decision_card_lint.py` | The door-field lint, rules D0-D9 with the corroboration table, called by `decisions.py add` after the shape check (standard library; `--selftest`, synthetic cards in a throwaway git repo); see "The six door fields" below |
| `scripts/compile-dashboard.py` | Renders `DASHBOARD.md` section 1 (DECISIONS WAITING) and regenerates `decisions.html` from the template at every compile |
| `scripts/decisions-template.html` | The decision-surface template (cards, gloss tooltips, keyboard handling, resolution tray); the compiler injects SNAPSHOT / REPO / DECISIONS / stamp |
| `scripts/proactive-open.sh` | Opens `decisions.html` front-and-center ONCE per new decision id (state: `.claude/decision-surface-state.json`); called by `decisions.py add`/`surface` only — the compiler never opens anything |
| `scripts/closes_when.py` | The shared closes-when predicate evaluator, including `decision-resolved` (section 4) |
| `hooks/scripts/session_resolver.py` | SessionStart surfacing: open decisions print first, then unresolved ledger rows (section 6) |

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
 "door": {"fields_sha": "<sha256 of the six fields as canonical JSON>", "findings": ["D8:...", ...]}}
```

Validation (`decisions.py check`, also run at `add`): id matches `DEC-NNN`; date, title,
requested_by, why_only_you non-empty; urgency and class from the enums; ask carries a
question, a 1-12-char header, a boolean multiSelect, and 2-4 options each with label +
description. `decided_by` / `decided_at` / `resolution_commit` are FORBIDDEN on any row —
see section 3. The six door fields and the `door` object are the lint's ("The six door fields" below): `add`
refuses a candidate that lacks or malforms one, and `check` re-validates their shape on every row
whose numeric id is above the legacy boundary (`DOOR_LEGACY_MAX_ID`, 35).

## 3. The resolution row (`kind:"decision-resolution"`) and git-derived attribution

Resolving appends one row and commits JUST that line under the invoker's git identity
(message: `decision: <id> <disposition> — decision-resolved=<id>`):

```json
{"kind": "decision-resolution", "id": "DEC-001", "date": "YYYY-MM-DD",
 "disposition": "accepted|denied|commented",
 "chosen_options": ["<label or free text>", ...],
 "comment": "<optional>"}
```

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
  stays deterministic and committable.
- `decisions.html` — regenerated whole at every compile; answering on the page stages
  the exact `decisions.py resolve` command in a visible tray (the ledger row is the
  record; the page is its shadow).
- SessionStart — the resolver prints one line per open decision FIRST (the hook pipes
  through `head -20`), then the summary, then unresolved ledger rows:
  `DECISION-LEDGER\t<id>\t<urgency>\t<title>\t<blocks>` … `DECISIONS-OPEN\t<count>\toldest <id> <age>d`.
- Proactive open — `decisions.py add`/`surface` run `proactive-open.sh`: recompile,
  open `decisions.html` once per NEW id, notify; a crash before the state write re-fires
  safely; a surface with no new ids does nothing (no re-open spam).

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
| `add --title ... --question ... --header ... --option L:D --option L:D --requested-by ... --class ... --why-only-you ... --undo U --undo U --staged-artifact P\|CMD\|none --evidence PTR\|none-exists --externality CLASS --recommended LABEL\|none --default-on-silence LABEL\|nothing-changes [--amount-usd N] [--urgency high] [--pointer P] [--blocks "a, b"] [--shadows maintainer-ruling=slug] [--multi] [--no-open] [--door-git-timeout S]` | Validate (shape, then the door lint D0-D9) + append one decision row (id race-checked), then proactive-open. Exit 0 PASS; 1 ESCALATE (appended with `door.findings`, one `ADD-FINDING` line first); 2 MALFORMED (`ADD-REFUSED` lines, nothing appended) |
| `list [--json]` | One line per decision with derived status (join, no git) |
| `show <id>` | The full card with git-derived resolution provenance |
| `resolve <id> --accept "<label-or-free-text>" [--comment "..."]` | Accept (repeat `--accept` when multiSelect); commits JUST the resolution line |
| `resolve <id> --deny [--comment "..."]` | Deny and close |
| `resolve <id> --comment "..."` | Comment — the decision STAYS OPEN |
| `resolve <id> ... --reopen` | Append another closing row over an already-closed id |
| `resolve --legacy <slug> --accept "done"` | Compat shim: answer a legacy maintainer-ruling bracket with no decision row (emits + commits the raw-dir ruling capture) |
| `check` | Schema + join validation over every row; exit 1 on findings; also prints the exit-neutral `RETEST-DUE` / `REVISIT-UNARMED` lines (section 7) |
| `surface [--no-open]` | Print the open-decision lines + summary; proactive-open (once-per-id guard) |
| `open [--all]` | Open `decisions.html` (`--all` also opens `DASHBOARD.md`) |
| `--selftest` | The full loop in a throwaway git repo, plus the `retest_when` scenario (unknown predicate refused; an armed row fires `RETEST-DUE` only after its evidence commit; a "later" option with no trigger is `REVISIT-UNARMED`); exits 0 only if every assertion passes; plus the door wiring (a field-less card refused with exit 2, a self-declared two-way card appended with a D8 finding and exit 1, `show` rendering the door lines, the seeded git stall exit-neutral, `check` exempting legacy ids). `python3 scripts/selftest-decision-door-fields.py` runs this and the lint's own `--selftest` together |

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
| 1 ESCALATE | D2 UNDO-CORROBORATED (a revertible `undo` beside a staged path outside the worktree, or `none` beside all-tracked paths); D3 resolution (`git show <sha>:<path>` fails, the lines do not exist, or the target is not an evidence class: `experiments/runs/**/{VERDICT.json,VERIFY.md,grade*,RUN-RECORD*}`, a `research/raw/` maintainer capture, `kind:"decision-resolution"` lines of `ledger/work-ledger.jsonl`, a `hypotheses/*.md` Runs-table row or Status `kept` line); D4 corroboration (table above); D8 SELF-DECLARED-TWO-WAY (`why_only_you` or `note` carries `two-way door`, `proceeds under the standing grant`, `Nothing is blocked`, `default advisory` or `keep-advisory`); D9 BLOCKING-TWO-WAY (`blocks` non-empty while every `undo` is revertible and `externality` is `none`) | `ADD-FINDING<TAB><id><TAB><rule><TAB><detail>` per finding, then `added ...` | appended with the findings under `door.findings`; renders as before |
| 2 MALFORMED | D0 STAGED-ARTIFACT, D1 UNDO-VOCAB, D3 form, D4 form (vocabulary; `class=spend` without a numeric `amount_usd`), D5 DEFAULT-STATED, D6 RECOMMENDED-ONE — every defect listed in one pass; then D7 DEDUP-WHY (at least two uncommitted decision rows already share the candidate's `why_only_you` byte-identically: one policy question filed N times) | `ADD-REFUSED<TAB>MALFORMED<TAB><rule><TAB><detail>` per defect (`MALFORMED-BATCH` for D7) | nothing appended |

A malformed card gets its field list and nothing else: D2-D4 and D8-D9 are not evaluated. Every
git read runs under one timeout (`--door-git-timeout`, default 20 s); a stall prints one
exit-neutral `ADD-TIMEOUT<TAB><rule>` line for that rule and never a finding. Legacy rows —
numeric id at or below `DOOR_LEGACY_MAX_ID` (35) — are exempt and never re-validated; `check`
re-validates only the fields' shape (the exit-2 class) on newer rows; `show` prints the
`door:` / `door-evidence:` / `door-finding:` lines only for rows that carry a `door` object, so
legacy rows render byte-identically. Rows appended by a caller other than `add` (a direct
`append_line`) are not linted at write time; `check` reports their missing fields once their id
is above the legacy boundary. A script that files rows through `add` must pass the six flags
(and a copy of `decisions.py` needs `decision_card_lint.py` beside it, or `add` exits with
`FATAL: scripts/decision_card_lint.py ... is not beside decisions.py`); at this port
`scripts/retest-trigger.py` and `scripts/knob-observe.py` do not yet, so their filings are
refused until they are updated.
