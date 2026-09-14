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
`landed_in` and `date`; `landed_in` is one of `root` (the checkout the session worked in), `outbox`
(that checkout no longer existed at drain time; see "The outbox and carry-forward" below) or
`carried` (a later drain moved an outbox row into a live checkout's own ledger).

`session-observed`: `session` (the transcript's basename, or the pointer's `session_id`), `through`
(the transcript line count the row covers -- the cursor), `head` (sha256 of the transcript bytes),
`counts` (`modeled_deterministic`, `modeled_stochastic`, `delegated`, `unmodeled`), `leverage`,
`determinism`, `handoff_share` (the classifier's own ratio arithmetic; `null` when a denominator is
0), `top_step` (`{msg, output_tokens}`: the assistant step with the most output tokens),
`unmodeled_top` (`[{op, tool, count, suggested_node}]`: program basenames only, never arguments),
`hook_timeouts`, `date` (the transcript's last timestamp), and, only when `landed_in` is `outbox`,
`origin_root_key` (the dead root's state key), or only when `landed_in` is `carried`,
`carried_from` (the origin key it was carried from) -- lab H-DRAFT-a4a14ff4-om-outbox-carry-forward,
kept 2026-09-14.

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

`drain` reads pointer files `<name>.json` from an inbox, each `{"session_id": ...,
"transcript_path": ..., "root": ..., "common_dir": ...}` -- `root` and `common_dir` are optional
(no lane writes them yet; see "The outbox and carry-forward" below). A pointer WITHOUT a `root`
key drains exactly as the first release of this worker did: straight into the `drain` call's own
`--root` target (the branch is `"root" not in pointer`, not "both fields absent" -- a pointer
with `root` but no `common_dir` instead quarantines as `NotAGitCheckout`, and one with
`common_dir` but no `root` takes this same back-compat path).

Which INBOX DIRECTORY a drain reads from by default changed in this release, independent of any
one pointer's shape: for a `root` that is itself a live git checkout, `drain` with no `--inbox`
override now always reads `<state>/om/<repo-key>/inbox/` (`repo-key` derived from `root`'s own
live `common_dir`; state root `~/.hyp-state`, or `$HYP_STATE_DIR`) -- NOT the previous release's
`<state>/om/<sha256(realpath root)[:16]>/inbox/`, which is still used, unchanged, only as the
fallback for a `root` that is not (or is no longer) a git checkout. A pointer hand-written
straight into that old path is no longer found by a default-location drain; move it under the
new path, or pass `--inbox` naming the old directory explicitly (`--inbox DIR` overrides which
directory holds `inbox/`, never whether the carry step below runs). Each pointer is renamed into
`processing/` before it is parsed,
then into `processed/` (or `quarantine/` with a `quarantine` row naming the basename and the
exception class). Bounds per wake: at most N = 20 pointers and T = 60 s on a monotonic clock
(checked between files); a 1 GiB free-space floor below which nothing is appended (one refusal
line, exit 0; `$HYP_OM_FREE_FLOOR_BYTES` raises it, never lowers it); an inbox over 1 MiB rotates
its oldest files by mtime into `overflow/<epoch>/` and writes one `spool-overflow` row. A drain
killed mid-way leaves only complete lines (canonical bytes under `O_APPEND`); the next drain sweeps
`processing/` back into the inbox and dedupe makes the re-run harmless. Nothing here schedules the
worker: run it by hand until the wake ships (below).

## The outbox and carry-forward

Evidence: lab `H-DRAFT-a4a14ff4-om-outbox-carry-forward`, kept 2026-09-14 -- five counted looks,
A1-A5 passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote bound,
cold-verified (`VERDICT.json`, `VERIFY.md`, journal fragment 0537 in the lab; five cold refute
rounds preceded the looks). The keep rules out the reading that a row produced in a worktree
removed before the worker runs is lost, or that it must be written into a checkout the session
never worked in.

For each pointer with a `root` (the checkout the session worked in) and a `common_dir` (that
repository's `git rev-parse --git-common-dir` at pointer-write time, as an ABSOLUTE, realpath-
resolved directory -- `drain` normalizes the field the same way it normalizes git's own live
output, joining a relative value such as the bare `.git` a main checkout's own `git rev-parse`
prints to `root` and then resolving it, before comparing), `drain` resolves the pointer's own
landing root -- never just the `--root` target it was called with -- before deciding where the
row goes:

- **live** (`root` exists and its own `git rev-parse --git-common-dir`, normalized the same way,
  still matches the recorded `common_dir`): the row lands in that checkout's own ledger,
  `landed_in: root`, exactly as a pointer with no `root` field always has.
- **missing** (`root` is a non-empty string naming a path that no longer exists -- the checkout
  was removed): the row lands in the outbox instead, `landed_in: outbox`, with `origin_root_key`
  naming the dead root's own state key. The outbox is keyed by the POINTER's own recorded
  `common_dir` (`<state>/om/<repo-key>/outbox.jsonl`), not by whichever repository happens to be
  draining -- a pointer for repository X waits under X's own key even when it is found sitting in
  repository Y's inbox. Nothing is lost; the row waits for a live checkout of the same repository.
- **not a checkout** (`root` exists but its live `common_dir` does not match the recorded one, or
  resolves to nothing; OR `root` is null, empty, or not a string -- a malformed pointer either
  way, not the case above): the pointer quarantines exactly as it always has, unconditionally,
  whether or not the outbox rule exists.

At the START of every `drain` for a live checkout, before any pointer is read, every row waiting in
that repository's outbox is carried into the checkout's own ledger: `landed_in: carried` and
`carried_from` (the origin key) replace `landed_in: outbox` and `origin_root_key`; every other field
is byte-identical to the outbox copy. Carries dedupe by `(session, through)`, not by exact bytes
(a carry's bytes differ from the outbox copy by construction). The whole claim step runs under an
exclusive, non-blocking `flock` on a per-`inbox_root` lock file: only the drain that acquires it
proceeds to claim the outbox (renamed from `outbox.jsonl` to `outbox.<epoch>.carrying.jsonl`),
read it, and carry every row; a drain that cannot get the lock backs off immediately and carries
nothing, rather than reading the outbox at all (`scripts/selftest-om-worker.py` proves this for
two DIFFERENT live checkouts of one repository racing a 400-row outbox at once, not only a
same-checkout pair). A rename claim alone, without that lock, still races: two drains starting
close together can both pass a lockless existence check on `outbox.jsonl`, and the second one's
crash-recovery sweep (below) then "resumes" the first one's still-in-flight claim in parallel,
carrying the same rows a second time into a DIFFERENT ledger -- caught while porting this lane
into the plugin, not present in the lane's own looks (its selftest never raced two DIFFERENT
checkouts). Once every row is carried, the claimed file is renamed on to
`outbox.<epoch>.carried.jsonl` (never truncated), so a further drain finds no outbox file and
carries nothing twice; a claim left behind by a drain that crashed mid-carry (and so never held
the lock at the same time as anyone else) is resumed, not stranded, the next time the lock is
free. A repository whose `common_dir` is itself gone (the whole repository deleted) has no live
checkout to carry into; its rows stay in the outbox with `landed_in: outbox` -- the design's
disclosed residual, not a failure.

Nothing writes `root`/`common_dir` into a pointer yet: that is the startup wake lane's job
(`H-DRAFT-10383178`, not yet kept). Until it keeps, every pointer lacks both fields and every row
that IS found still lands `root`, per-pointer landing exactly as before this lane -- but which
inbox directory it is found in follows the default-location change noted above, not "before this
lane" (that change ships with this release regardless of the wake lane).

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

`python3 scripts/selftest-om-worker.py` -- 34 checks over throwaway consumers, the fixture grade
behaviours ported: parity with `observatory.tally_ratios` on planted transcripts (the
Skill-in-catalogue branch included), lint equality with `model-lint.py`, staleness true then false,
the six canary classes absent, every self-check net, the mutant pair, idempotence, the cursor and
latest-wins, byte identity across two trees, the N and T caps, the floor, rotation, quarantine, a
SIGKILL mid-drain with a clean resume, `schema: 2` tolerance, the configured path, the scaffold's
union row, a configured path surviving a re-init with its row rendered, an absolute value falling
back to the default in every reader, the two-worktree union merge, zero `claude` spawns, stdlib-only
imports, and (lab `H-DRAFT-a4a14ff4-om-outbox-carry-forward`, plus ship fix round 1) a scratch
repository with a main checkout and two linked worktrees, one removed after its pointer is
written: the outbox landing keyed by the pointer's own `common_dir`, a malformed `root: null`
pointer quarantining rather than entering the outbox, the exactly-once carry, a `not-a-checkout`
quarantine unconditional in both cases, a pointer with no `root` field draining as before, and two
DIFFERENT live checkouts racing a 400-row outbox landing every row in exactly one ledger.

## What does not ship yet, and why

The design (lab `experiments/runs/DESIGN-passive-om-feedback/DESIGN.md`, section 6, changeset A)
names four more pieces. Each ships only after its own lane keeps -- plugin bytes change only after
the keep that licenses them -- and none had kept when this worker shipped:

- **the wake** (lane 8, `H-DRAFT-10383178`): the `SessionStart` row that runs `drain` at startup
  and writes `root`/`common_dir` into every pointer it produces. Until it keeps, nothing runs the
  worker for you, and every pointer lacks both fields (its row always lands `root`, per "The
  outbox and carry-forward" above) -- you name transcripts by hand (`observe`) or write pointers
  yourself.
- **the commit path** (lane 7): the clause that stages the appended row into the session's commit.
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

`H-DRAFT-a4a14ff4-om-outbox-carry-forward`'s kept bytes are the lane fixture's `impl/om-worker.py`
(baseline-plus-rule) ported the same way: `repo_key`/`path_key` hash with the plugin's existing
crc32+adler32 state-directory key (`hooks/scripts/session-start-budget.py`, kept
`H-DRAFT-a10fd3f7`) rather than the fixture's own sha256[:16] -- the lane's spec names "the shipped
state key" and its `VERIFY.md` flagged the fixture's literal bytes as unpinned to it. One bug found
while porting, fixed and not present in the lane's own looks (its fixture scratch root was always
`/private/tmp`, never symlinked): `resolve_common_dir` now `realpath`s its result, because `git`'s
own worktree admin file already stores a canonicalized absolute path and a checkout under a
symlinked mount (macOS `/tmp`, `/var`) would otherwise resolve to a different string for its main
checkout than for one of its own linked worktrees, quarantining a live worktree as
`not-a-checkout`.

Ship fix round 1 (cold refuter, applied before release): the concurrent-drain case its `VERIFY.md`
carried as untested (pre-mortem (ii)) is now a selftest case racing two DIFFERENT live checkouts
against a 400-row outbox, not only a same-checkout pair -- it caught a real defect the lane's own
looks never exercised (a rename-only claim, with no lock, let both racers carry the full outbox
into two different ledgers), fixed by moving the whole claim+carry step under a per-`inbox_root`
`flock`. Three more findings from that same round: `common_dir` on a pointer is now normalized
(joined to `root`, then realpath'd) the same way `resolve_common_dir` normalizes git's own live
output, so a pointer written per the documented contract for a main checkout, or reached through a
symlinked mount, no longer false-quarantines; the default inbox location for any live git checkout
changed in this release even for a pointer with neither `root` nor `common_dir` (documented above,
not silently claimed "exactly as before"); and the outbox is now keyed by the POINTER's own
recorded `common_dir` rather than by whichever repository happens to be draining. A malformed
pointer (`root: null`) now quarantines instead of entering the outbox under a meaningless
key.

Undo: revert the release's merge commit. Rows already written are plain JSON lines in your ledger;
the attribute row is plain text in your `.gitattributes`.
