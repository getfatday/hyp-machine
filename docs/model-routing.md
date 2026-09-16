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
frontier model is always flagged `frontier-on-execute`. The table's `overrides` block and its
`frontier_on_execute_requires: [reason, evidence]` list are carried through the merged table
but not yet consulted by the scanner (the scanner ships byte-identical to the graded keep;
consulting them is a later, selftest-backed change), so neither a per-role `overrides.<role>`
nor a `classes.execute` re-point carrying `reason` and `evidence` admits the call today -- the
finding's fix hint still names that override because the hint text is part of the same graded
bytes. The two escapes that do admit an execute role on fable/opus: the
`// route-override: guard-false-positive <reason>` marker on the line before the call (below),
or mapping the role to a `think`/`adversarial` class under `roles` in `.claude/routing.json`.

The **effective table** for a repository is this default merged with `.claude/routing.json`
(scaffolded once by `/hyp:init` from `templates/routing.json`, an empty override — your edits
to it are never overwritten by a later `/hyp:init`). Add roles or override a class's
model/effort there; `table_sha`/`default_sha` (in the merged table, and printable with
`routing.py table`) let you pin the plugin default your override was written against — a
mismatch is its own finding, `default-sha-mismatch` (denied under `deny`, advised under `advise`).

## The guard

`hooks/scripts/routing-guard.py` runs as a `PreToolUse` hook on two matchers:

- **`Workflow`** — reads `routing.enforce` from `.claude/hyp.json` (`deny` | `advise` | `off`,
  default **`advise`**). Under `deny` it blocks a workflow script whose `agent()` calls have a
  finding; under `advise` it prints one line per finding and lets the call through; under
  `off` it writes its start/finish marks and exits 0 before opening the script -- no scan, no
  finding, no advisory line. Nothing is denied anywhere until you set `routing.enforce: deny`.
- **`Agent`** — advise-only, always, regardless of `routing.enforce`.

A finding names the script, the line, and one of these classes: `no-model` / `no-effort`
(the option is missing), `unknown-role` (the label's head is not in the roles map),
`value-mismatch` (model/effort disagree with the table's row), `frontier-on-execute` (an
`execute` role on a frontier model; the `overrides` block is not consulted -- see The table
above for the two escapes that admit), `non-literal` (a routing option is a
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
`.claude/routing-guard-errors.log`, and carries the same note in the response's
`systemMessage`. The two files are durable local records, never only a transcript line — the
guard fails open on payload/IO problems, but it never fails silently.

## The CLI

`scripts/routing.py`:

- `table` — print the effective table, one role per line.
- `resolve <role>` — print the role's `{model, effort, agentType}` as JSON; exit 1 for an
  unmapped role.
- `lint <script>` — print every finding for a script; exit 1 if any, 0 if clean.
- `rewrite <script> --out <dir>` — write a table-conformant copy, touching only routing
  option values (`model`, `effort`, `agentType`) for calls whose label head is already in the
  roles map. An unmapped or unlabelled call is never rewritten — it stays an `unknown-role`
  finding — and a `meta.phases[]` entry is never rewritten either: a `phase-mismatch` finding
  stays until the phases block is edited by hand (`lint` reports it).

## The compiled agent surface

Ships from the changeset that lands the lab keep `H-DRAFT-75b03e6e-routing-determinism`
(`VERDICT.json`: evidence-sufficient promote, five counted looks 5/5, llr 2.9389 >= the
2.8904 promote bound): naming `model`/`effort` on the `agent()` call is one input the
platform reads, but a compiled agent DEFINITION whose own frontmatter pins a `model` is a
second, independent one. The lane's claim, proven live against real headless children on
CLI version 2.1.272: a routed call shape (a call literal from this table PLUS an agent
definition with pinned frontmatter naming the same tier) is served by the table's model in
every session it ran in, survives a resumed session (`resumeFromRunId`), and beats a
planted `CLAUDE_CODE_SUBAGENT_MODEL` — while a call naming no model at all is served by
whatever model the calling session happens to run.

**The resolution order the platform applied to the lane's literals**, most specific wins:

1. the called agent's own definition frontmatter (`model:` in `agents/hyp-<role>.md`), if
   the call's `agentType` names one that resolves;
2. the call's own literal `model`/`effort` options, if named;
3. the calling session's own model;
4. `CLAUDE_CODE_SUBAGENT_MODEL` in the environment never wins over a routed call (rung 1 or
   2 above) — it is what a call with none of those falls back to before rung 3, and the
   lane's A4 assertion is exactly that a routed call beats a planted one.

`scripts/compile-routing-agents.py` renders one `agents/hyp-<role>.md` per role in the
table's `roles` map — YAML frontmatter naming `name`, `description`, `model`, `role`,
`class`, and a one-line body — committed inside this plugin (deterministic and pure: same
table bytes in, same file bytes out, always). Three modes:

- (no flag) — compile every role (or `--role ROLE`, repeatable) into this plugin's own
  `agents/` directory — what this repository commits, and what a released-plugin install
  ships.
- `--check` — compare, write nothing; exit 1 and print one line per file that is missing or
  disagrees with the table, 0 if every wanted file matches. `scripts/harden-check.sh`'s
  ADVISORY-37 calls this at every session start.
- `--emit <dir>` — write into `<dir>` instead — the **project-scope install path** (e.g. a
  consumer's own `.claude/agents/`), named `hyp-<role>` with no colon.

**What was measured and what is inferred, stated plainly.** The lab lane's headless
children installed the compiled definition at PROJECT SCOPE
(`<workdir>/.claude/agents/hyp-build.md`) with `agentType: 'hyp-build'` (no colon), because a
project-scope PLUGIN install never resolved the plugin-qualified id `hyp:build` in a
headless child (round 1 of the lane's fixture fixes). That project-scope surface is what
`--emit` reproduces, and it is what the lane's resolution-order claim above was proven
against, live, on real children. The surface a released install of THIS plugin ships —
`agents/hyp-<role>.md` inside the plugin, an `agentType: 'hyp:<role>'` call resolving to it
— inherits the same resolution-order argument but is **not itself the surface the lane
measured**; it is unmeasured, and this line says so on purpose rather than implying
otherwise. Until a lane measures the plugin-qualified id directly in a headless child with
this plugin installed at project scope, treat the project-scope path (`--emit` into your own
`.claude/agents/`, calls naming `agentType: 'hyp-<role>'`) as the proven one.

## Known limitation

The scanner is a bracket-matching regex, not a JavaScript parser (deliberate: a real parser is
a much larger surface for a 10-second hook row to depend on). It sees every `agent(...)` call
site the census counted and denies what it cannot balance (`cannot-parse`) rather than missing
it silently, but it will never understand a script that builds its calls dynamically. The
`Agent`-matcher row only advises; it does not yet check whether a subagent call is a shipped
`hyp:` agent naming its own model. A real `Agent` tool call carries no `script`/`scriptPath` at
all, so this row admits it with no scan and no record -- not a targeted finding, and (since the
B1/B2 fix) not a fail-open advisory line either.

## Undo

Revert the merge commit that landed this release, or set `routing.enforce: off` in
`.claude/hyp.json` (the hook still runs -- it writes its start/finish marks -- but exits 0
before it opens the script).

## The routing ledger

Ships from the changeset that lands the lab keep `H-DRAFT-38f86fad-routing-ledger-row`
(`VERDICT.json`: evidence-sufficient promote, five counted looks 5/5, llr 2.9389 >= the
2.8904 promote bound) — see that lane for the full evidence trail.

Separately from the guard above, `hooks/scripts/routing-ledger.py` runs as a **synchronous
`Stop` hook** (timeout 15 s) and a **`SubagentStop` hook** (timeout 10 s) after every turn.
It reads the session's own workflow run directories (`<project dir>/<session
id>/subagents/workflows/wf_*`, the shape Claude Code writes: `journal.jsonl`
started/result/failed records, `agent-<id>.meta.json`, and `agent-<id>.jsonl` transcripts)
and appends one `agent-route/v1` row per finished workflow agent — a `result` record is
what makes an agent finished; a `failed` or still-running agent is counted in the hook's own
summary line and never given a row — to `<checkout>/ledger/routing-ledger.jsonl`, keyed
`(wf, agent)` so a re-run never duplicates a row and a conflicting rewrite is refused (one
stderr line) rather than silently overwritten.

**Row schema** (`kind: "agent-route"`, `v: 1`):

| Field | What |
|---|---|
| `ts`, `repo`, `session`, `wf`, `agent` | When, which repository/session/workflow/agent |
| `label`, `role`, `class` | The call's `label` and its `role:slug` head, classed by a frozen head -> class table (`mechanical`/`execute`/`think`/`adversarial`/`unknown`) kept separate from the guard's own `rules/routing-default.json` roles map — see `hooks/scripts/routing_lib.py`'s module docstring for why |
| `declared` | `{model, effort, agentType}` from the agent's own `meta.json`, verbatim (often `None`: only a call that named a tier word writes it) |
| `observed` | `{model, tier}` — the model id actually served, read from the transcript's own `message.model` lines, and its tier bucket |
| `tokens`, `wall_s` | Summed usage counters and elapsed time over the transcript's assistant lines |
| `cost_usd` | `tokens x` the matching row of `rules/model-prices.json` (prefix-matched against `observed.model`); `0.0` when no row matches |
| `prices_sha`, `table_sha`, `default_sha`, `override_sha` | The exact table/price bytes this row was computed against — the same `table_sha`/`default_sha` `routing.py table` prints |
| `lane`, `run`, `outcome_ref` | Copied verbatim from the opt-in pointer file below, never resolved by the writer |
| `outcome` | `{schema_valid, verdict, refuted}` from the agent's own structured result, `verdict` bounded to 64 characters — a token, never a report |
| `mismatch`, `void` | `true`/`"annulled"` when `declared`'s tier disagrees with `observed`'s tier |
| `host_load_1m`, `transcript_truncated` | Covariates: 1-minute load average at sweep time, and whether the transcript exceeded the 8 MB head+tail read cap |

**The join pointer.** A workflow driver that wants its rows joined to a specific run writes
`<checkout>/.claude/routing-outcomes/<wf>.json` `{"lane", "run", "outcome_ref"}` before the
workflow's agents finish; the writer copies the three fields onto every row of that
workflow verbatim and never resolves `outcome_ref` itself — resolving it (`RUN-RECORD.json`'s
own `assertions` pass share) is a job for whoever reads the ledger.

**The optional `RUN-RECORD.json` routing block.** A lane driver that wants its own run record to
carry a summary of the ledger rows its workflow produced may add a `routing: {table_sha, rows}`
block to `RUN-RECORD.json` — `table_sha` the effective-table sha its rows carry (the same
`table_sha` value written onto those rows, and the one `routing.py table` prints), `rows` the
count of `agent-route/v1` rows its workflow produced. This block is written by the driver, never
by the writer: `hooks/scripts/routing-ledger.py` never opens or edits a `RUN-RECORD.json`.

**The `SubagentStop` caveat.** Whether this row ever fires for a real workflow subagent was
never measured against a live session in the source lane's build (only the `Stop` row is
evidenced); it is wired the same way as the `Stop` row and is safe to run either way (fail-
open, idempotent, dedup on `(wf, agent)`), but treat it as unproven until your own repository
observes a row it contributed that the `Stop` row had not already written.

**The Stop-wall covariate.** The source lane's stall was interpreter start plus the resolver's
own git latency (~15 s at host load 15-24, roughly half the counted looks read as an
ambiguous void at that load). This port keeps the writer's own imports light (stdlib only, no
heavy import at module top) to stay inside its declared 15 s `Stop` row timeout, but a heavily
loaded host can still push a *different* row in the same `Stop` batch past its own timeout —
`host_load_1m` on each row is the covariate to check first if rows look sparse.

**Redaction.** The writer never reads or writes a prompt or tool-input string — only the
metadata fields listed above (`journal.jsonl` types/labels, `meta.json` model/effort/agentType,
transcript `message.model`/`message.usage`/`timestamp`). `scripts/selftest-routing-ledger.py`
plants a prompt string in a synthetic transcript and asserts it never reaches the ledger.

**A malformed run.** A `journal.jsonl` that cannot be read as one JSON object per line
(truncated, non-JSON bytes) is fail-open, never a crash: the writer emits zero stderr
lines for it and names the workflow under `skipped` in the hook's own stdout summary
(`{"reason": "no readable journal.jsonl"}`); an unreadable `agent-<id>.meta.json` or
`agent-<id>.jsonl` skips that one agent the same way. Nothing about a malformed run is
guessed or imputed.

**Config.** `.claude/hyp.json` `routing_ledger_file` overrides the default
`ledger/routing-ledger.jsonl` path for the `/hyp:init`-scaffolded `.gitattributes` union row;
the writer itself does not yet read this key (it always appends to the default path) — a
disclosed parity gap with `ledger_file`/`om_feedback_file`, open for a later release.

**Undo.** Revert the merge commit that landed this release. There is no `routing.enforce`-style
off switch for the ledger itself (it never denies anything to turn off); removing the two
hook rows from `hooks/hooks.json` locally also stops it.
