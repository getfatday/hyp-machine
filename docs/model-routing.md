# Model routing

Every `agent()` call inside a Workflow script names a role in its `label` (`role:slug`, for
example `build:port-selftest`). A committed table maps each role to a model tier and an
effort, so the tier a call runs on is a repository decision, not whichever model the calling
session happened to inherit. A `PreToolUse` hook checks every call against that table before
the workflow starts.

Ships from the changeset that lands the lab keep `H-DRAFT-314c8d17-routing-guard`
(`VERDICT.json`: evidence-sufficient promote, five counted looks 5/5, llr 2.9389 >= the 2.8904
promote bound) — see that lane for the full evidence trail.

## The table

`rules/routing-default.json` (plugin-shipped) ships five classes:

| class | model | effort | why |
|---|---|---|---|
| `mechanical` | haiku | low | rote, deterministic steps |
| `execute` | sonnet | high | building, fixing, shipping, syncing |
| `think` | fable | high | design, research, synthesis, judgment |
| `adversarial` | fable | high | refuting, verifying, reviewing |
| `unmodeled` | sonnet | high | explicit escape valve, used only under `advise` |

...and a `roles` map from label head (`build`, `fix`, `design`, `refute`, ...) to one of those
classes. `frontier: [fable, opus]` names the expensive tier; an `execute`-class role on a
frontier model is flagged unless the call carries a repository override with a `reason` and an
`evidence` pointer (a deliberate escalation, not a default).

The **effective table** for a repository is this default merged with `.claude/routing.json`
(scaffolded once by `/hyp:init` from `templates/routing.json`, an empty override — your edits
to it are never overwritten by a later `/hyp:init`). Add roles or override a class's
model/effort there; `table_sha`/`default_sha` (in the merged table, and printable with
`routing.py table`) let you pin the plugin default your override was written against — a
mismatch is its own advisory finding, `default-sha-mismatch`.

## The guard

`hooks/scripts/routing-guard.py` runs as a `PreToolUse` hook on two matchers:

- **`Workflow`** — reads `routing.enforce` from `.claude/hyp.json` (`deny` | `advise` | `off`,
  default **`advise`**). Under `deny` it blocks a workflow script whose `agent()` calls have a
  finding; under `advise` it prints one line per finding and lets the call through; under
  `off` it does nothing. Nothing is denied anywhere until you set `routing.enforce: deny`.
- **`Agent`** — advise-only, always, regardless of `routing.enforce`.

A finding names the script, the line, and one of these classes: `no-model` / `no-effort`
(the option is missing), `unknown-role` (the label's head is not in the roles map),
`value-mismatch` (model/effort disagree with the table's row), `frontier-on-execute` (an
`execute` role on a frontier model with no override), `non-literal` (a routing option is a
spread, variable, or template instead of a string literal), `phase-mismatch` (a
`meta.phases[]` entry names a different model than the call), `agent-type-relabel`
(`agentType` disagrees with the label's head), `alias` (the identifier `agent` used somewhere
other than a direct call), `cannot-parse` (the scanner cannot balance the call's parens/strings
— a finding, never a silent pass), `too-large` (the script exceeds the guard's timeout-safe
byte bound), `default-sha-mismatch`, and `subagent-model-env`
(`CLAUDE_CODE_SUBAGENT_MODEL` is set in the invoking environment).

A false positive on a specific line is admitted, one line at a time, with a marker on the line
immediately before it:

```
// route-override: guard-false-positive <a non-empty reason>
```

An empty reason does not admit. The override is logged as a `guard-override` row in
`.claude/routing-guard-ledger.jsonl`; every fail-open path (a missing table, an unreadable
script, a malformed payload) logs a `guard-error` row there instead, and one line in
`.claude/routing-guard-errors.log`. Both are durable local records, never only a transcript
line — the guard fails open on payload/IO problems, but it never fails silently.

## The CLI

`scripts/routing.py`:

- `table` — print the effective table, one role per line.
- `resolve <role>` — print the role's `{model, effort, agentType}` as JSON; exit 1 for an
  unmapped role.
- `lint <script>` — print every finding for a script; exit 1 if any, 0 if clean.
- `rewrite <script> --out <dir>` — write a table-conformant copy, touching only routing
  option values (`model`, `effort`, `agentType`, `meta.phases[].model`) for calls whose label
  head is already in the roles map. An unmapped or unlabelled call is never rewritten — it
  stays an `unknown-role` finding.

## Known limitation

The scanner is a bracket-matching regex, not a JavaScript parser (deliberate: a real parser is
a much larger surface for a 10-second hook row to depend on). It sees every `agent(...)` call
site the census counted and denies what it cannot balance (`cannot-parse`) rather than missing
it silently, but it will never understand a script that builds its calls dynamically. The
`Agent`-matcher row only advises; it does not yet check whether a subagent call is a shipped
`hyp:` agent naming its own model, so a routed `Agent` tool call the same session makes may log
a fail-open advisory line rather than a targeted finding.

## Undo

Revert the merge commit that landed this release, or set `routing.enforce: off` in
`.claude/hyp.json` (the hook still runs but exits 0 immediately, before it opens the script).
