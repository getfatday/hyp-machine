# The decision kit: one ledger-backed decision store, one consolidated surface

Ported from the source lab's decision kit (landed there 2026-08-28 under the consolidated
decision-making directive; the schema below is the contract the lab's code cites as
`decisions-schema.md` — this document is its shipped form, section numbers preserved
because the shipped code cites them). The kit's parts:

| Part | Role |
|---|---|
| `scripts/decisions.py` | The CLI: add / list / show / resolve / check / surface / open (+ `--selftest`, the port's own end-to-end proof in a throwaway git repo) |
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
          "options": [{"label": "...", "description": "..."}, ...]},
 "context_pointers": ["<repo-relative pointer>", ...],
 "blocks": ["<what waits>", ...],
 "urgency": "high|normal|low",
 "class": "publish|spend|schema|live-surface|plan|hygiene",
 "why_only_you": "<one clause>",
 "shadows": ["maintainer-ruling=<slug>", ...],
 "note": "<optional>"}
```

Validation (`decisions.py check`, also run at `add`): id matches `DEC-NNN`; date, title,
requested_by, why_only_you non-empty; urgency and class from the enums; ask carries a
question, a 1-12-char header, a boolean multiSelect, and 2-4 options each with label +
description. `decided_by` / `decided_at` / `resolution_commit` are FORBIDDEN on any row —
see section 3.

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

## CLI reference (`python3 scripts/decisions.py ...`)

| Command | Effect |
|---|---|
| `add --title ... --question ... --header ... --option L:D --option L:D --requested-by ... --class ... --why-only-you ... [--urgency high] [--pointer P] [--blocks "a, b"] [--shadows maintainer-ruling=slug] [--multi] [--no-open]` | Validate + append one decision row (id race-checked), then proactive-open |
| `list [--json]` | One line per decision with derived status (join, no git) |
| `show <id>` | The full card with git-derived resolution provenance |
| `resolve <id> --accept "<label-or-free-text>" [--comment "..."]` | Accept (repeat `--accept` when multiSelect); commits JUST the resolution line |
| `resolve <id> --deny [--comment "..."]` | Deny and close |
| `resolve <id> --comment "..."` | Comment — the decision STAYS OPEN |
| `resolve <id> ... --reopen` | Append another closing row over an already-closed id |
| `resolve --legacy <slug> --accept "done"` | Compat shim: answer a legacy maintainer-ruling bracket with no decision row (emits + commits the raw-dir ruling capture) |
| `check` | Schema + join validation over every row; exit 1 on findings |
| `surface [--no-open]` | Print the open-decision lines + summary; proactive-open (once-per-id guard) |
| `open [--all]` | Open `decisions.html` (`--all` also opens `DASHBOARD.md`) |
| `--selftest` | The full loop in a throwaway git repo; exits 0 only if every assertion passes |

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
