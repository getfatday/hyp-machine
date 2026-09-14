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
| `evaluate --root R` / `compile-check --root R` | every `<model_dir>/<context>/` tree, regenerated in place first by the shipped `scripts/compile-catalog.py`, then linted by `scripts/model-lint.py`; the last commit dates of the tree and of `compiled/*.md`; the declared `compile_command` | one `model-evaluated` row per tree carrying `catalog_regen`, plus (`compile-check` only) a fail-closed exit: a missing renderer or a nonzero exit fails the whole verb, rc 1, rather than report rc 0 over an unregenerated catalogue (today both verbs write the same row and run the same regeneration; `compile-check` is the name the startup wake will call, and the one that fails closed) |
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

`model-evaluated`: `model_tree` (repository-relative), `catalog_regen` (`{ran, rc, renderer_found}`:
whether `scripts/compile-catalog.py` was invoked over this tree before the lint and staleness reads
below, its exit code, and whether the renderer script was even found; `null` for every key when
`evaluate`/`compile-check` is called with regeneration off), `lint` (`errors`, sorted `findings`
lines exactly as `model-lint.py` prints them, `parse_skipped` when pyyaml was missing and the lint
could not parse), `compiled` (`stale`: the newest `compiled/*.md` by commit date predates the
tree's last commit; `compiled_path`, `compiled_commit_date`, `model_commit_date`; `null` when a
date is unknown), `compile_command` (`{command, rc}` of the `.claude/hyp.json` `compile_command`,
when declared).
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
three readers of the override (the worker, init, the check) apply one rule
(`hyp_config.safe_rel_path`): an absolute value or one with a `..` segment falls back to the default
path everywhere, so the row rendered is always the row the worker writes to.

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

`python3 scripts/selftest-compile-catalog.py` -- 17 checks over throwaway consumers and worktree
pairs: the renderer byte-identical on re-run and sorted by type then id, a zero-node context
rendering the template's stub sections, never touching a node file or reading the prior `model.md`,
the scaffold's ignore row appended once and byte-stable on re-run without disturbing a consumer's
own line, the retire step removing exactly one index entry while keeping the work-tree file and
no-op-ing once untracked, two worktrees adding one node each to the same context merging with
exit 0 in both orders and a fresh clone rendering the union, `compile-check`'s row recording the
renderer's run and failing closed when the renderer script is missing, Externals/Aggregates rows
and headings rendered (and every read-model directory spelling read) only when such nodes exist
with a core-only context gaining no extra heading, `model-lint.py` reporting 0 `E-CATALOG` over a
regenerated extra-types context, and the A2 grep proving no shipped template or skill tells a
reader to `git add`/`git commit` `model.md`.

## The catalogue projection

`operating-model/<context>/model.md` is a regenerated projection, never hand-maintained and never
tracked: `/hyp:init` installs a `.gitignore` row (`templates/gitignore`) naming it and, the first
time it runs in a repository that still tracks a copy from the old hand-maintained shape, retires
that copy from the index with one `git rm --cached` (the work-tree file is left in place).
`evaluate` and `compile-check` regenerate it first with `scripts/compile-catalog.py`, so the lint
and staleness reads that follow always see the current node set, never a copy two branches might
otherwise have edited into conflict; `observe` reads the catalogue through the classifier as it
stands (`observatory.Catalog`) and never regenerates it. `compile-catalog.py` never
edits a node file and never reads the existing `model.md` before overwriting it; run it by hand
(`compile-catalog.py operating-model/<context> --write`, or `--model-dir operating-model` to
regenerate every context at once) any time you want a fresh catalogue without waiting for
`evaluate`. Before the first regeneration in a fresh clone (nothing has run `evaluate` or
`compile-check` there yet), `model.md` is simply whatever the last committed copy was -- absent
entirely once a repository has retired tracking, present and possibly stale if it has not yet.

Evidence: lab `H-DRAFT-4e06e157-om-rows-merge-shape`, kept 2026-09-14 -- five counted looks, every
assertion passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote bound
(`VERDICT.json` beside the lane; six cold refute rounds of the fixture preceded the looks). The
keep proves two worktrees each adding a node to the same context merge with `git merge` exiting 0
in both orders, and a fresh clone renders the union once regenerated, where a tracked `model.md`
conflicted in both orders of the same scenario (the OFF control). It claims nothing about a
custom merge driver or a central store, and nothing about the feedback ledger's own `merge=union`
row above (a different keep, H-DRAFT-b9e771b2's extension).

## What does not ship yet, and why

The design (lab `experiments/runs/DESIGN-passive-om-feedback/DESIGN.md`, section 6, changeset A)
names three more pieces beyond the catalogue projection above (shipped this release). Each ships
only after its own lane keeps -- plugin bytes change only after the keep that licenses them -- and
none had kept when this worker shipped:

- **the wake** (lane 8): the `SessionStart` row that runs `drain` at startup. Until it keeps, nothing
  runs the worker for you.
- **the outbox carry-forward** (lane 6): the recorder hook that writes a pointer file per finished
  session into the inbox. Until it keeps, you name transcripts by hand (`observe`) or write pointers yourself.
- **the commit path** (lane 7): the clause that stages the appended row into the session's commit.
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

The catalogue projection above ports the lane fixture's `render_catalog.py` prototype as
`scripts/compile-catalog.py`, and drifts from those kept bytes in four places: the rendered header
line (names the shipped script, not the fixture's); an added `--model-dir` multi-context form,
available for regenerating every context in one call by hand -- the worker's `compile-check`
instead calls the single-context form once per model tree it finds; `EXTRA_TYPE_DIRS`, rendering
Externals/Aggregates headings only when the context has at least one such node, which the fixture
had no equivalent for; and read-model's two extra accepted directory spellings, `read-models` and
`read-model`, alongside the fixture's single `readmodels`. `scripts/init-scaffold.py` gains
`ensure_gitignore` (the `ensure_gitattributes` shape, for a plain ignore file) and
`retire_tracked_model_md` (a one-time `git rm --cached`), wired immediately before the existing
`model.md` stub write, ported from the lane's `impl/patch_on.py` with one drift: the ignore row
`templates/gitignore` installs is anchored (`/{{MODEL_DIR}}/*/model.md`), unlike `patch_on.py`'s
un-anchored row. `om-worker.py`'s `evaluate()` gained one step,
`catalog_regen`, run before the lint and staleness reads it now precedes; fail-closed is new here
(every other subprocess call in this file is fail-open) because a compile-check that reported rc 0
over a catalogue it never actually regenerated would be worse than one that visibly failed.

Undo: revert the release's merge commit. Rows already written are plain JSON lines in your ledger;
the attribute row is plain text in your `.gitattributes`.
