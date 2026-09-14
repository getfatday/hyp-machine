# Passive feedback: one row per session about the operating model, at no token cost

`scripts/om-worker.py` turns a finished Claude Code session into one row of numbers about how the
session used the repository's operating model, and turns each model tree into one row about its
health -- with no model call, from the transcript file and git alone. It is the deterministic half
of the `observe` and `evaluate` skills (the census arithmetic and the lint), factored out so it can
run without a session and accumulate evidence the model improves on over time.

Evidence: lab `H-DRAFT-35397146-om-worker-deterministic`, kept 2026-09-13 -- five counted looks,
every assertion passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote
bound, cold-verified (`VERDICT.json`, `VERIFY.md`, journal fragment 0528 in the lab; ten cold refute
rounds of the fixture preceded the looks). The keep rules out the reading that `observe` and
`evaluate` need a model call to leave any durable trace. It claims nothing about the judgment half
of those skills (deviations, node prose, defect-versus-discovery), which stays a later tier.

## What the worker does

| Verb | Reads | Writes |
|---|---|---|
| `observe <transcript.jsonl> --root R` | the transcript's `tool_use` blocks and `usage`; the repository's catalogue (`operating-model/*/model.md`, `skills/`, `scripts/`) through the live board's own classifier (`observatory.Catalog.classify`, `op_tokens_bash`, `ratio_block`, `unmodeled_top`) | one `session-observed` row |
| `evaluate --root R` / `compile-check --root R` | every `<model_dir>/<context>/` tree through the shipped `scripts/model-lint.py`; the last commit dates of the tree and of `compiled/*.md`; the declared `compile_command` | one `model-evaluated` row per tree (today both verbs write the same row; `compile-check` is the name the startup wake will call) |
| `drain [--root R] [--inbox DIR]` | pointer files in the per-root inbox | one row per pointer, `quarantine` rows for poison files, one `spool-overflow` row when the inbox rotated |
| `status --root R` | the feedback ledger | one JSON line: row count, rows per `schema` value, unparsed lines, the ledger's repository-relative path |
| `latest --root R` | the feedback ledger | the latest-wins view: the highest-`through` `session-observed` row per session, canonical bytes, session order |

Every verb exits 0 on its own refusals and prints one marker line, `om-worker 1 <verb> rc 0 [written|duplicate|refused-...]`;
stderr carries refusal lines only, so "zero rows and one refusal line" is countable on stderr alone.
One exception, kept as the lane graded it: `observe` handed a transcript whose assistant rows lack
`usage` (a format shift) raises -- a traceback, exit 1, nothing written -- where `drain` quarantines
the same file with a `quarantine` row and exits 0.

## The row schema (`schema: 1`)

Rows are one JSON object per line, keys sorted, no spaces, newline-terminated (canonical bytes), so
the ledger dedupes by exact bytes and merges by union. Every row carries `kind`, `schema`,
`landed_in` (`root`) and `date`.

`session-observed`: `session` (the transcript's basename, or the pointer's `session_id`), `through`
(the transcript line count the row covers -- the cursor), `head` (sha256 of the transcript bytes),
`counts` (`modeled_deterministic`, `modeled_stochastic`, `delegated`, `unmodeled`), `leverage`,
`determinism`, `handoff_share` (the classifier's own ratio arithmetic; `null` when a denominator is
0), `top_step` (`{msg, output_tokens}`: the assistant step with the most output tokens),
`unmodeled_top` (`[{op, tool, count, suggested_node}]`: program basenames only, never arguments),
`hook_timeouts`, `date` (the transcript's last timestamp).

`model-evaluated`: `model_tree` (repository-relative), `lint` (`errors`, sorted `findings` lines
exactly as `model-lint.py` prints them, `parse_skipped` when pyyaml was missing and the lint could
not parse), `compiled` (`stale`: the newest `compiled/*.md` by commit date predates the tree's last
commit; `compiled_path`, `compiled_commit_date`, `model_commit_date`; `null` when a date is
unknown), `compile_command` (`{command, rc}` of the `.claude/hyp.json` `compile_command`, when declared).
The command string is stored verbatim, so declare it repository-relative (`node tools/compile.js`,
`sh bin/compile.sh`): a declared command carrying an absolute or `~/` path makes every
`model-evaluated` row refuse on the absolute-path net -- fail-closed, one stderr line per `evaluate`,
zero rows.

`templates/event-nodes/session-observed.md` and `templates/event-nodes/model-evaluated.md` are the
copy-into-your-model event nodes for the two row kinds (the representation line names this ledger).

`quarantine`: `file` (basename), `reason` (the exception class name). `spool-overflow`: `moved`.

Readers tolerate unknown fields and higher `schema` values: a `schema: 2` row is read, reported once
by `status`, and never rewritten. A transcript that grew produces a second row with a higher
`through`; the ledger keeps both and `latest` shows the newer.

## Where rows land and how they merge

Rows append to `.claude/hyp.json` `om_feedback_file`, default `ledger/om-feedback.jsonl`, under an
`flock`'d `O_APPEND` descriptor, deduped by exact bytes. `/hyp:init` appends
`ledger/om-feedback.jsonl merge=union` (or your configured path) to `.gitattributes` through the
same mechanism as the work ledger's row, so two worktrees that each appended a row merge without a
conflict (`scripts/selftest-om-worker.py` proves it with two worktrees, add/add included);
`scripts/merge-attrs-check.py` (harden `ADVISORY-36`) names the row as missing once the file exists,
and lints its rows like the work ledger's. A re-run of `/hyp:init` keeps every key your
`.claude/hyp.json` carries beyond the plugin's defaults (`om_feedback_file`, `ledger_file`,
`compile_command`, the decision settings) and renders the union row for the configured path; the
four readers of the override (the worker, init, the check, and the commit-path clause below) apply
one rule (`hyp_config.safe_rel_path`): an absolute value or one with a `..` segment falls back to
the default path everywhere, so the row rendered is always the row the worker writes to.

## The commit path

`hooks/scripts/commit-backstop.py`'s `git commit` PreToolUse row also stages the feedback ledger
for you, one clause, additive to the advisory backstop above and independent of its
experiments-profile gate (the feedback ledger is a capture-profile feature): on a real `git
commit` command, when the configured ledger (`om_feedback_file`, default
`ledger/om-feedback.jsonl`) is dirty (untracked or modified), the clause stages it and prints one
line. Two lines you may see:

- `OM-FEEDBACK-STAGED <n> rows` — the ledger is staged; `<n>` counts the newly-added lines (the
  whole file for a brand-new ledger, just the appended lines for one already tracked). The rows
  ride your next commit without your naming the file.
- `OM-FEEDBACK-HELD forbidden key <key>` — a row on disk carries a forbidden key (`tool_input`,
  `prompt`, or `last_assistant_message`; the same lint `scripts/om-worker.py` itself applies
  before writing). Nothing is staged; the ledger stays as it was. Fix the offending row (or the
  writer that produced it) and commit again — the clause re-checks every time.

Gate limitation: the clause fires only on a command that itself *begins* with `git commit`
(`^\s*git\s+commit\b`), matching the same shape `hooks.json`'s matcher targets. A command
wrapped in `timeout 45 git commit ...`, `cd <dir> && git commit ...`, or `git -c ... commit ...`
does not begin that way and is silent — the clause will not have staged anything for that commit.
Run `git commit` directly, or stage the ledger yourself (`git add -- ledger/om-feedback.jsonl`)
first.

Undo, one command: `git restore --staged -- ledger/om-feedback.jsonl` unstages it again before
you commit (the working-tree bytes are untouched either way); after a commit that carried it,
the row is an ordinary tracked line like any other — revert or edit it as you would any file.

Evidence: lab `H-DRAFT-fb9c08b9-om-ledger-commit-path`, kept 2026-09-14 — five counted looks,
every assertion passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote
bound, cold-verified (`VERDICT.json`, `VERIFY.md`, journal fragment 0538).

## Running it by hand

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-worker.py" observe ~/.claude/projects/<project>/<session>.jsonl --root .
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-worker.py" evaluate --root .
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-worker.py" status --root .
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-worker.py" latest --root .
```

`drain` reads pointer files `<state>/om/<sha256(realpath root)[:16]>/inbox/<name>.json`, each
`{"session_id": ..., "transcript_path": ...}` (state root `~/.hyp-state`, or `$HYP_STATE_DIR`;
`--inbox DIR` names the directory holding `inbox/` directly). Each pointer is renamed into
`processing/` before it is parsed, then into `processed/` (or `quarantine/` with a `quarantine` row
naming the basename and the exception class). Bounds per wake: at most N = 20 pointers and T = 60 s
on a monotonic clock (checked between files); a 1 GiB free-space floor below which nothing is
appended (one refusal line, exit 0; `$HYP_OM_FREE_FLOOR_BYTES` raises it, never lowers it); an inbox
over 1 MiB rotates its oldest files by mtime into `overflow/<epoch>/` and writes one
`spool-overflow` row. A drain killed mid-way leaves only complete lines (canonical bytes under
`O_APPEND`); the next drain sweeps `processing/` back into the inbox and dedupe makes the re-run
harmless. Nothing here schedules the worker: run it by hand until the wake ships (below).

## What never enters a row

The row path never captures transcript bytes: no prompt text, no tool argument, no file body, no
`tool_input`, no absolute path (the classifier reduces every program to its basename and the worker
records nothing about where the transcript or the repository lives). A fail-closed self-check runs
on every row before the write and refuses -- one stderr line naming the class, never the bytes --
on a forbidden key (`tool_input`, `prompt`, `last_assistant_message`), a `/Users/` or `$HOME` marker,
an absolute-path-shaped string (`/...`, `~/...`), a relative path that leaves the repository
(`../...`), a bare spelling of a well-known absolute root (`Users/...`, `home/...`, `private/...`,
`tmp/...`, `var/...`, `opt/...`, `etc/...`), or the executor's login or host name. Two disclosed
heuristics: a consumer whose rows legitimately carry a string starting with one of those root
names would be refused, and a login shorter than four characters, a generic word, or a word the
rows carry anyway (a tool name, a row key) is not detectable as an identity leak on that host. The
converse false positive: the net skips only the worker's own row vocabulary, not the transcript's
program names, so a login equal to a program basename the session ran (`printf`, say) refuses every
`session-observed` row on that host -- fail-closed, one stderr line per row, zero rows.
`$HYP_OM_MUTANT` exists only for the selftest's known-answer control (`redact-disabled`: the
self-check must refuse; `blind`: the leak must reach the file); production never sets it.

## Regression test

`python3 scripts/selftest-om-worker.py` -- 26 checks over throwaway consumers, the fixture grade
behaviours ported: parity with `observatory.tally_ratios` on planted transcripts (the
Skill-in-catalogue branch included), lint equality with `model-lint.py`, staleness true then false,
the six canary classes absent, every self-check net, the mutant pair, idempotence, the cursor and
latest-wins, byte identity across two trees, the N and T caps, the floor, rotation, quarantine, a
SIGKILL mid-drain with a clean resume, `schema: 2` tolerance, the configured path, the scaffold's
union row, a configured path surviving a re-init with its row rendered, an absolute value falling
back to the default in every reader, the two-worktree union merge, zero `claude` spawns, stdlib-only
imports.

`python3 scripts/selftest-commit-backstop.py` -- the commit path's own regression test: an
untracked and an appended-tracked ledger both stage with the exact line and ride the next
commit, a clean or absent ledger and a non-`git commit`/heredoc-mentioning payload stay silent,
a forbidden-key row holds with one line and stages nothing, a linked worktree's commit stages
only that worktree's ledger, the clause fires with no `.claude/hyp.json` at all (independent of
the backstop's own experiments-profile gate), the pre-existing backstop behaviour (a
scratch-prefixed staged file with no hypothesis spec still warns) is unchanged, and the
fully-silent case's hook wall stays under the hook row's own timeout.

## What does not ship yet, and why

The design (lab `experiments/runs/DESIGN-passive-om-feedback/DESIGN.md`, section 6, changeset A)
names four more pieces. Each ships only after its own lane keeps -- plugin bytes change only after
the keep that licenses them. The commit path (lane 7) has since kept and shipped -- see "The commit
path" below. Still not here when this worker shipped:

- **the wake** (lane 8): the `SessionStart` row that runs `drain` at startup. Until it keeps, nothing
  runs the worker for you.
- **the outbox carry-forward** (lane 6): the recorder hook that writes a pointer file per finished
  session into the inbox. Until it keeps, you name transcripts by hand (`observe`) or write pointers yourself.
- **the catalogue projection** (lane 2): the `model.md` renderer the worker's `compile-check` would run first.
- **the `om_feedback_file` key in `hooks/scripts/hyp_config.py` `DEFAULTS`** (named by the design and
  by the lane's on-keep row; deferred, recorded here and in the lane's `SHIP.md`): every reader of the
  override today (the worker, `/hyp:init`'s union row, `merge-attrs-check.py`) reads `.claude/hyp.json`
  directly through the one shared validator, so the key has no hook-side reader yet. Putting it in
  `DEFAULTS` would rewrite every consumer's `.claude/hyp.json` on the next `/hyp:init` and widen
  `load_config` for all hooks before the wake lane (lane 8) -- the first hook that will read it -- has
  kept. It ships with the wake. The two event-node templates the same row names do ship (above).

Also not here: the judgment tier (a model reading these rows for deviations and node prose), which
registers as its own spec citing this row schema.

## Provenance and drifts resolved at ship

The kept bytes are the lane fixture's `impl/om-worker.py`; this file is that worker adapted to the
plugin layout (the plugin `scripts/` directory defaults to its own; the ledger path reads
`om_feedback_file`) with the lane's carried non-blocking findings resolved: the self-check now runs
the fixture grader's A3 nets instead of two markers; a drain sweeps `processing/` first, so a kill
mid-drain strands no pointer; staleness reads the newest compiled artifact by commit date rather
than the first by name. Left as kept: the T cap is checked between files only (one very slow
transcript can overrun it); `evaluate` and `compile-check` write the same row; `hook_timeouts` is
recorded but nothing reads it yet, and it counts every attachment with `timedOut` or type
`hook_cancelled` (one real 85-line transcript read 18, more than its hook timeouts), so the wake lane
reads it as an unread, over-counting field until a lane pins the attachment shape.

The commit path's kept bytes are the lane fixture's `impl/commit-backstop.py` clause, ported onto
this plugin's own (0.29.0) `hooks/scripts/commit-backstop.py` with two drifts resolved: the ledger
path now reads the `om_feedback_file` override through `hyp_config.safe_rel_path` (the fixture
predates that key and hardcoded the default path), and the git subprocess calls the clause makes
now carry an explicit timeout under the hook row's own 10 s budget (the fixture's had none). Left
as kept: the gate matches only a command that itself begins with `git commit` (see "The commit
path" above); the forbidden-key set is a constant mirrored from, not imported from,
`scripts/om-worker.py`'s `CANARY_KEYS_FORBIDDEN`, so the two are asserted equal by
`scripts/selftest-commit-backstop.py` rather than sharing one name at runtime; the lint scans
every row on disk, not only the ledger's unstaged rows.

Undo: revert the release's merge commit. Rows already written are plain JSON lines in your ledger;
the attribute row is plain text in your `.gitattributes`; a staged-but-uncommitted ledger unstages
with `git restore --staged -- ledger/om-feedback.jsonl`.
