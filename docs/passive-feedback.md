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
| `drain [--root R] [--inbox DIR]` | pointer files in the per-repository inbox | one row per pointer, `quarantine` rows for poison files, one `spool-overflow` row when the inbox rotated; a pointer git cannot answer for in time is deferred to the next wake; a pointer whose checkout has since been removed lands in a per-repository outbox and rides into the next live checkout of the same repository exactly once |
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

## The startup wake

Every `startup` `SessionStart` fires `drain` for you now: `hooks/hooks.json`'s existing
`resolver` row gains one `--also om-worker '...'` clause, so no per-prompt work and no extra
foreground interpreter start is needed for a row to land. What happens, in order, inside
`hooks/scripts/session-start-budget.py`'s `run` verb, right after it resolves the checkout root
and before it waits on the resolver itself: it writes ONE pointer file for the session that
started this checkout the time before (never this session's own -- its own transcript has not
been written yet), one `os.write` on an `O_CREAT|O_EXCL` descriptor, idempotent; then it forks
`om-worker.py drain --boundary startup --plugin-scripts ...` as a second, detached process at
background priority (`os.nice(19)`) under its own lock, never waited on. The wake pays for
none of this on the row you actually see: the primary `resolver` row's own foreground budget and
output are unchanged, and the side-runner's cost lands entirely in the background.

The pointer names `root` and `common_dir`, so it lands in the shared, per-repository inbox
`drain` reads by default (see "Where rows land and how they merge") -- the same inbox for a main
checkout and every one of its linked worktrees, each still landing its own row in its own
`ledger/om-feedback.jsonl`, never in another worktree's.

Caveat under load: the row for one session is written by the *next* session's startup, not its
own -- by construction, a checkout is never mid-session when its own wake fires. Under heavy
host load the lane measured this landing arriving late against the sealed clock tolerance in a
minority of gated launches (1 of 6) and more often in concurrent (two-worktree) launches (2 of
12), by well under five seconds every time. The practical read: the previous session's row is
usually there by the time the next session starts, and can be one session boundary stale under
load -- never lost, never duplicated (the pointer write is `O_CREAT|O_EXCL`-idempotent).

Evidence: lab `H-DRAFT-10383178-om-startup-wake`, kept 2026-09-15 -- five counted looks, every
assertion passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote bound,
cold-verified (`VERDICT.json`, `VERIFY.md`, journal fragment 0547). The keep claims nothing about
same-session freshness, the row's own content, how a reading is shown, or landing under load
beyond the measured margins above.

## The reading surface

Every session start that runs the wake (see "The startup wake" above) also shows you, right
after the fork and before the wrapper's own output, the LAST reading the side-runner already
landed for this checkout: at most 8 lines from `<state>/<key>/om-worker.out` (this wrapper's
OWN state key, `state_dir(root)` -- never the worker's separate sha256/`common_dir` inbox key,
so a session in another worktree or repository never shows another root's reading), each
prefixed `OM-FEEDBACK: `, the first carrying `age=<seconds>` (the file's mtime age) ahead of
its own text. A ninth `OM-FEEDBACK: ... <n> more` line is added when the reading holds more
than 8 lines; nothing prints when the file is absent or empty -- a fresh checkout, or one that
has not yet completed a second startup, shows nothing extra. Every line passes through the SAME
two structural checks `scripts/om-worker.py`'s own `_forbidden_hit` applies before it ever
writes a row -- the three forbidden JSON-shaped keys (`tool_input`, `prompt`,
`last_assistant_message`) and the `/users/`/`$home` path markers, case-folded -- never the
worker's own test-only canary vocabulary, so the hold catches a CLASS of leak rather than
memorizing a fixture's literals; a line that matches either check prints as one literal
`<held: 1 line>` line instead of its own text, every other line printed unchanged. No new
process: the read and the print happen inside the wrapper's own foreground path, the same one
`do_also_wake` already runs on, so they cost nothing beyond the fork the wake already pays for
(one fork per startup, exactly as before this lane).

The `resume|clear|compact` `SessionStart` row's `cached` call gains the same `om-worker` name
the other cached readings already carry, so `cmd_cached` reports it in its own
`SESSION-START-CACHE:` line the same way it reports every other name -- and applies the SAME
per-line hold before printing its body: a `cached` replay of a 12-line reading prints all 12
lines (not capped at 8 -- that bound is `print_om_feedback`'s own, startup-only) plus the
`SESSION-START-CACHE:` summary line, each held line replaced the same way. A session working in
a DIFFERENT worktree or repository shows nothing for `om-worker` on either route: its own state
key resolves to a different `<key>` directory, which never holds another checkout's reading.

Evidence: lab `H-DRAFT-3aef12a5-om-startup-reading-surface`, kept 2026-09-15 -- five counted
looks, A1-A5 passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote
bound, cold-verified (`VERDICT.json`, `VERIFY.md`, journal fragment
`0553-3aef12a5-verdict.md`; five cold fixture refute rounds preceded the looks). The keep claims
nothing about the judgment tier reading these lines, and nothing about a reading landing
same-session (see "The startup wake" above for that caveat, which this surface inherits
unchanged).

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
with `root` but no `common_dir` instead quarantines as `NotAGitCheckout` whether or not that
`root` still exists, and one with `common_dir` but no `root` takes this same back-compat path).

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
`processing/` back into the inbox and dedupe makes the re-run harmless. That sweep, the rotation,
the carry and the pointer loop all run under an exclusive, non-blocking `flock` on
`<inbox_root>/.drain.lock` (ship fix round 4): every checkout of one repository now shares one
inbox, and two wakes inside it at once (main and a linked worktree starting together) made the
second wake's sweep move the first wake's IN-FLIGHT pointer back into `inbox/`, so it was
processed twice and the first wake wrote a false `FileNotFoundError` quarantine row for it. A
drain that cannot get the lock prints one stderr line, moves nothing, and exits 0 with an all-zero
result; the wake that holds it does the work. The backing-off wake lands NO pointer that wake, not
only no carry: with two frequent wakers on one repository, the wake that loses the lock is a
no-op and its pointers wait for whichever wake next takes the lock -- correct and lossless, but
not free work. Every `git rev-parse` the worker runs is bounded by a
5 s timeout, and a timeout is NOT read as "not a checkout" (ship fix round 4, B1 -- on a loaded
host it was, and live worktrees' pointers were quarantined while a default-location drain fell
back silently to the old single-path inbox and found nothing): when git cannot answer for the
drain's own `--root` in time the whole drain refuses with one stderr line and moves nothing (the
next wake retries); when it cannot answer for one POINTER's live root in time, that pointer is put
back into `inbox/` for the next wake, counted as `deferred` in the result and named in one stderr
line, never quarantined. The drain result JSON always carries the same keys on every exit
(`rows`, `quarantined`, `rotated`, `landed`, `recovered`, `outbox`, `carried`, `deferred`). Nothing
here schedules the worker: run it by hand until the wake ships (below).

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
  was removed -- AND the pointer carries a usable `common_dir` naming the repository it belonged
  to): the row lands in the outbox instead, `landed_in: outbox`, with `origin_root_key`
  naming the dead root's own state key. The outbox is keyed by the POINTER's own recorded
  `common_dir` (`<state>/om/<repo-key>/outbox.jsonl`), not by whichever repository happens to be
  draining -- a pointer for repository X waits under X's own key even when it is found sitting in
  repository Y's inbox. Nothing is lost; the row waits for a live checkout of the same repository.
- **not a checkout** (`root` exists but its live `common_dir` does not match the recorded one, or
  resolves to nothing; OR `root` is null, empty, or not a string; OR the pointer carries no usable
  `common_dir` at all, whether or not its `root` still exists -- a malformed pointer either way,
  not the case above): the pointer quarantines exactly as it always has, unconditionally, whether
  or not the outbox rule exists. The `common_dir` test runs before the existence test: a pointer
  that cannot prove which repository it belonged to never enters the outbox under a borrowed key
  (ship fix round 3 -- before it, a dead `root` with no `common_dir` fell into the DRAINING
  repository's outbox and was carried into a ledger the pointer never named). A git TIMEOUT on a
  root that exists is none of these: git did not answer, so nothing about the pointer was decided
  -- it is deferred back into `inbox/` for the next wake, neither landed nor quarantined (ship fix
  round 4, B1; see "Running it by hand").

At the START of every `drain` for a live checkout, before any pointer is read, every row waiting in
that repository's outbox is carried into the checkout's own ledger: `landed_in: carried` and
`carried_from` (the origin key) replace `landed_in: outbox` and `origin_root_key`; every other field
is byte-identical to the outbox copy. Carries dedupe by `(session, through)`, not by exact bytes
(a carry's bytes differ from the outbox copy by construction). The whole claim step runs under an
exclusive, non-blocking `flock` on `<inbox_root>/.outbox-carry.lock`, a file every consumer's
state directory now gains beside `outbox.jsonl`: only the drain that acquires it proceeds to claim
the outbox (renamed from `outbox.jsonl` to `outbox.<epoch>.carrying.jsonl`), read it, and carry
every row; a drain that cannot get the lock backs off immediately and carries nothing, rather than
reading the outbox at all (`scripts/selftest-om-worker.py` proves this for two DIFFERENT live
checkouts of one repository racing a 400-row outbox at once, not only a same-checkout pair). A
rename claim alone, without that lock, still races: two drains starting close together can both
pass a lockless existence check on `outbox.jsonl`, and the second one's crash-recovery sweep
(below) then "resumes" the first one's still-in-flight claim in parallel, carrying the same rows a
second time into a DIFFERENT ledger -- caught while porting this lane into the plugin, not present
in the lane's own looks (its selftest never raced two DIFFERENT checkouts). The missing-root
write path (the row for a dead worktree landing into the outbox in the first place) takes the
SAME lock, blocking, before its append: without that, a write already open on `outbox.jsonl` when
a concurrent carry renames it away can land inside the very `.carrying.jsonl` the carrier is
already reading, and be finalized to `.carried.jsonl` -- a file no later drain ever rereads --
before the write completes, losing the row (ship fix round 2, `scripts/selftest-om-worker.py`
races this deterministically). The carrier's non-blocking attempt also loses to that blocking
write while it is in flight -- a drain of ANOTHER repository landing a misplaced dead pointer into
this repository's outbox at that instant -- and the carry then skips that wake; the rows wait one
more wake and the next drain carries them (harmless, noted here so it is not read as a loss). Once every row is carried, the claimed file is renamed on to
`outbox.<epoch>.carried.jsonl` (never truncated), so a further drain finds no outbox file and
carries nothing twice; a claim left behind by a drain that crashed mid-carry (and so never held
the lock at the same time as anyone else) is resumed, not stranded, the next time the lock is
free -- EVERY leftover claim is resumed, oldest first, and the live `outbox.jsonl` is then claimed
in the same drain (ship fix round 5, B2: a drain used to resume only the oldest leftover and never
reach `outbox.jsonl` behind it). A row the target ledger REFUSES has one of two fates. A row the
redaction self-check refuses is refused for what it contains, so every later attempt would refuse
it too: it is filed verbatim into `outbox.<epoch>.refused.jsonl` beside its claim (kept for a
reader, never re-carried), one `quarantine` row naming that file with reason
`CarryRefused-redaction` lands in the target ledger (counted in the result's `quarantined`), and
the claim finalizes -- before round 5 any refusal left the claim in place "for the next drain",
and one such permanently refused row held the live outbox unclaimed on every later drain of the
repository (three consecutive drains of a live checkout, carried 0 each), stranding every later
dead-worktree row behind it for as long as the claim lived. A row refused on the free-space floor
is about the host's state, not the row's, and the drain's own start-of-drain floor check makes it
a race window only: the carry halts for that wake with the claim left intact and nothing filed,
and the next drain that passes the floor check resumes it and goes on to the live outbox. Beside every claim sits a
`.roots` sidecar (`outbox.<epoch>.carrying.roots`, removed with the claim when it finalizes) naming
each checkout that has carried from it (by realpath, so absolute checkout paths do live in the state directory: in this sidecar and in the pointer files the drain moves into `processed/` and `quarantine/`, whose `root` the producer wrote; never in a row or a ledger); a DIFFERENT checkout resuming the claim dedupes against
those checkouts' ledgers as well as its own, so rows the first carrier already landed are not
carried a second time into the resumer's ledger (ship fix round 4, A1 -- before it, main carrying 2
of 4 rows and a worktree resuming carried all 4 into the worktree). Under `--inbox DIR` the
outbox lives in `DIR` itself and the carry does not check repository membership (outbox rows
carry no repository key), so `--inbox` must name a directory used by ONE repository -- a live
checkout of repository Y draining a directory shared with repository X would carry X's
dead-worktree rows into Y's ledger. A repository whose `common_dir` is itself gone (the whole repository deleted) has no live
checkout to carry into; its rows stay in the outbox with `landed_in: outbox` -- the design's
disclosed residual, not a failure.

The startup wake (`H-DRAFT-10383178`, kept 2026-09-15, shipped -- see "The startup wake" above)
is what writes `root`/`common_dir` into a pointer. A pointer with neither field (written by hand,
or by a pre-wake consumer) still lands `root`, per-pointer landing unchanged -- but which inbox
directory it is found in follows the default-location change noted above regardless of whether
the wake wrote it.

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

`python3 scripts/selftest-om-worker.py` -- 41 checks over throwaway consumers, the fixture grade
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
pointer quarantining rather than entering the outbox, the exactly-once carry, a second drain of
another live checkout carrying nothing twice, every landed row's `landed_in` inside the
`root`/`outbox`/`carried` allowlist, a `not-a-checkout` quarantine unconditional in both cases
(a live root under the wrong `common_dir`, and a dead root with no `common_dir` at all -- ship
fix round 3), a pointer with no `root` field draining as before, two DIFFERENT live checkouts
racing a 400-row outbox landing every row in exactly one ledger, the missing-root write
blocking behind a concurrent carry on the same lock (ship fix round 2), and (ship fix round 4) a
git that cannot answer in time refusing the whole drain rather than falling back to the old inbox,
a live pointer whose root git cannot answer for being deferred rather than quarantined and landing
`root` on the next drain, two live checkouts waking on the SAME shared inbox landing every pointer
exactly once with no false quarantine row, a claim resumed by a different checkout carrying
only the rows the first carrier never landed, and (ship fix round 5) a leftover claim holding a
permanently refused row filing that row beside itself, finalizing, and the live outbox being
carried in the same drain (three rows landed, one quarantine row, a second drain changing
nothing), plus a floor refusal mid-carry halting with the claim intact and the next carry
resuming it and the live outbox together. The run prints the host load average first: the
concurrent and wall-clock cases are time-sensitive, and the whole run has needed over 300 s at a
load of 12-20 (the SIGKILL case retries on a fresh tree, up to three times, when the kill lands in
the gap between two pointers and strands nothing -- a harness miss seen once at load 12.5, not a
worker behaviour).

`python3 scripts/selftest-commit-backstop.py` -- the commit path's own regression test: an
untracked and an appended-tracked ledger both stage with the exact line and ride the next
commit, a clean or absent ledger and a non-`git commit`/heredoc-mentioning payload stay silent,
a forbidden-key row holds with one line and stages nothing, a linked worktree's commit stages
only that worktree's ledger, the clause fires with no `.claude/hyp.json` at all (independent of
the backstop's own experiments-profile gate), the pre-existing backstop behaviour (a
scratch-prefixed staged file with no hypothesis spec still warns) is unchanged, and the
fully-silent case's hook wall stays under the hook row's own timeout.

`python3 scripts/selftest-compile-catalog.py` -- 19 checks over throwaway consumers and worktree
pairs: the renderer byte-identical on re-run and sorted by type then id, a zero-node context
rendering the template's stub sections, never touching a node file or reading the prior `model.md`,
the scaffold's ignore row appended once and byte-stable on re-run without disturbing a consumer's
own line (an un-anchored `operating-model/*/model.md` row already present counts as the row, so no
duplicate is appended), the retire step removing exactly one index entry while keeping the
work-tree file and no-op-ing once untracked, a retire git declines (a staged `model.md` edit
differing from both HEAD and the work tree) reported as one `retire-refused` line with no
`retired` line and the path still tracked, two worktrees adding one node each to the same context merging with
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
that copy from the index with one `git rm --cached` (the work-tree file is left in place); if
git declines -- a staged `model.md` edit differing from both HEAD and the work tree -- init prints
one `retire-refused` line, never `retired`, and leaves the file tracked for you to resolve.
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
names three more pieces beyond the catalogue projection above: the wake, the outbox carry-forward,
and the commit path (see "The commit path" above). The wake, the outbox carry-forward and the
commit path have all shipped since this worker did; each shipped only after its own lane kept --
plugin bytes change only after the keep that licenses them. See "The startup wake" above for what
the wake does now that it has shipped. Not yet shipped:

- **the `om_feedback_file` key in `hooks/scripts/hyp_config.py` `DEFAULTS`** (named by the design and
  by the lane's on-keep row; deferred, recorded here and in the lane's `SHIP.md`): every reader of the
  override today (the worker, `/hyp:init`'s union row, `merge-attrs-check.py`, and now the
  commit path's clause) reads `.claude/hyp.json` directly through the one shared validator, so
  the key has no hook-side reader through `load_config`/`DEFAULTS` yet. Putting it in
  `DEFAULTS` would rewrite every consumer's `.claude/hyp.json` on the next `/hyp:init` and widen
  `load_config` for all hooks before a reader through that path exists. The wake (lane 8, now
  shipped -- see "The startup wake" above) never needed it: it only writes pointer files, and
  reads no ledger path at all. The two event-node templates the same row names do ship (above).

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

Ship fix round 4 (cold refuter, applied before release), two blocking findings the earlier
rounds' fixture never reached because it never ran on a loaded host and never woke two checkouts
on one inbox: a `git rev-parse` timeout (or an `OSError` running git) was folded into "not a
checkout", so live worktrees' pointers quarantined as `NotAGitCheckout` under load (5 of 20 in one
wake at a load of 13-20) and a default-location drain fell back silently to the v0.29.0 sha256
inbox and found nothing -- `resolve_common_dir` now answers `UNKNOWN` distinctly, such a pointer is
deferred to the next wake and such a drain refuses outright; and the inbox every checkout of a
repository now shares let two concurrent wakes race on `processing/`, one wake's crash-recovery
sweep re-queuing the other's in-flight pointer (21 landings for 20 pointers, plus a false
`FileNotFoundError` quarantine row) -- the sweep, rotation, carry and pointer loop now run under a
per-inbox `.drain.lock`, mirroring the round-1 `.outbox-carry.lock`. Advisories applied: a
resumed claim dedupes against the prior carrier's ledger through a `.roots` sidecar; the drain
result carries the same keys on every exit; the selftest prints the host load and allows 180 s
for the 400-row race. Left as is, on purpose: a pointer with a live `root` but no `common_dir`
still quarantines (the kept lane's own bytes quarantine that shape -- `not common_dir_p` is the
first test in its `resolve_pointer_root` -- so landing it would ship beyond the evidence; the
startup wake lane writes both fields together).

Ship fix round 5 (cold refuter, applied before release), one blocking finding on the code and one
on the docs. The code: `_carry_outbox_locked` resumed only the OLDEST leftover `.carrying.jsonl`
and, when the target ledger refused any row in it, returned without finalizing and without ever
reaching `outbox.jsonl` -- so one permanently refused row (a claim row carrying an absolute-path
string, which the redaction self-check refuses on every attempt) held the live outbox unclaimed
across three consecutive drains of a live checkout, stranding every later dead-worktree row of
that repository for as long as the claim lived. Every leftover claim is now resumed and the live
outbox claimed in the same drain; a redaction-refused row is filed verbatim into
`outbox.<epoch>.refused.jsonl` beside its claim with one `quarantine` row
(`CarryRefused-redaction`) in the target ledger and the claim finalizes; a floor refusal alone
still leaves the claim for the next drain, since the floor is the host's state, not the row's. The
docs: the README's and the changeset's selftest counts had drifted from what the script ran; both
now name the script's own count (41 with the two round-5 cases). Advisories applied: the
resumed-claim sentence above rewritten around the two fates; one sentence each on the backing-off
wake landing nothing that wake and on a carry skipping a wake behind a concurrent outbox write;
the finalize path proven by the refused-claim case, not only the resume path. Recorded, no
change: v0.29.0's `free_bytes` already walks up to an existing ancestor and that release resolves
no git common-dir at all, so neither a free-space-floor nor a relative-`.git` defect exists there.

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
`model.md` stub write, ported from the lane's `impl/patch_on.py` with two drifts: the ignore row
`templates/gitignore` installs is anchored (`/{{MODEL_DIR}}/*/model.md`), unlike `patch_on.py`'s
un-anchored row (and `ensure_gitignore` accepts an already present un-anchored row as the row, so
a consumer carrying the fixture's form is not handed a duplicate); and the retire step reads
`git rm --cached`'s exit status -- `patch_on.py` printed `retired` unconditionally after a
`check=False` call, so a retire git refused (staged content differing from both HEAD and the work
tree, exit 1) left the file tracked while init reported it retired; the shipped step prints
`retired` only on exit 0 and one `retire-refused <path>: <git's first stderr line>` line
otherwise, still fail-open (cold-refuter finding, ship round 4). `om-worker.py`'s `evaluate()` gained one step,
`catalog_regen`, run before the lint and staleness reads it now precedes; fail-closed is new here
(every other subprocess call in this file is fail-open) because a compile-check that reported rc 0
over a catalogue it never actually regenerated would be worse than one that visibly failed.

`H-DRAFT-10383178-om-startup-wake`'s kept bytes are the lane fixture's `impl/session-start-budget.py`
patch onto `hooks/scripts/session-start-budget.py` (byte-identical between the lane's pinned 0.28.0
baseline and this release, so the patch applied with no wrapper-side merge). One drift resolved at
ship: the lane's own pointer write targeted `om-worker.py`'s single-path `state_root()` (sha256 of
the root, matching the worker bytes the lane's fixture pinned -- `H-DRAFT-35397146`, which shipped
before the outbox carry-forward lane existed). This plugin ships `H-DRAFT-a4a14ff4-om-outbox-carry-forward`
already, whose default (`--inbox`-less) `drain` reads the shared, `common_dir`-keyed inbox instead
(see "The outbox and carry-forward" above) -- so writing to the old key would have landed every
pointer somewhere a default drain never looks. The wrapper's `om_inbox_root` now computes the same
`common_dir`-keyed path the worker's own `_resolve_inbox_root` does (pure Python, the wrapper's
existing `git_common_dir`, no subprocess), falling back to the old single-path key only when
`common_dir` cannot be resolved -- proven equal to the installed worker's own resolution, not
assumed, by `scripts/selftest-session-start-budget.py`. Checked and NOT present in the shipped
worker (carried finding, `VERIFY.md` section 10): `free_bytes` crashing `statvfs` on a brand-new
consumer whose `ledger/` does not exist yet -- the shipped version already walks up to the nearest
existing ancestor directory (landed by the outbox carry-forward lane's own fix rounds, unrelated to
this one); no fix was needed here.

`H-DRAFT-3aef12a5-om-startup-reading-surface`'s kept bytes are the lane fixture's
`impl/session-start-budget.py` and `impl/hooks.json`, patched onto this release's own copies --
byte-identical between the lane's pinned 0.32.0 baseline and this release (`patch_on.py`'s own
`BASELINE_SHA256` check), so the port applied with no wrapper-side merge and no drift to
resolve. `scripts/om-worker.py`'s `CANARY_KEYS_FORBIDDEN` and its two path markers are asserted,
not merely assumed, equal to the two constants this surface mirrors
(`scripts/selftest-session-start-budget.py`) -- the same "mirrored, not imported" shape the
commit path's own hold already uses (see above), for the same reason: the wrapper's own minimal
import set (`os`, `sys`, `time`, `zlib`, `hashlib`) must not grow to import `scripts/om-worker.py`
on every session's hot path.

### The integrator

`scripts/om-integrate.py` (skill `integrate`) is the piece that turns "you can run the worker by
hand" into "something on this host runs it for you": nine handles -- six on-device
(`launchd-queue`, `systemd-user`, `cron-anacron`, `schtasks-idle`, `desktop-task`, `hook-oneshot`)
and three remote (`ci-tier0`, `ampersand`, `routine`) -- each probed by running its own real
command and reading the exit code, never by sniffing the OS. `probe --json` appends the nine rows
to `ledger/om-substrates.jsonl` (`.claude/hyp.json` `om_substrates_file` overrides the path,
`merge=union` through `/hyp:init` the same way `om_feedback_file` does); `compose` picks exactly
one on-device and one remote handle from the `usable` rows alone, in a frozen priority order,
never from the `authors_90d`/`disk`/`host_key` covariates the rows also carry; `emit` writes --
but never loads or activates -- a `plutil`-linted launchd plist for `launchd-queue` (watching the
same inbox directory `om-worker.py`'s own default `drain` reads) or delegates the `ci-tier0`
handle whole to `scripts/om-ci.py emit ci-tier0` (never a second copy of that workflow's
template); `test` drives one real transcript and the worker lane's own two poison seeds through
the pinned worker under a stub substrate that plays the `QueueDirectories`/`ThrottleInterval`
role without ever touching real launchd, and reports `test: PASS`/`FAIL` from what actually
landed on disk; `report` reads back whether the recorded handle still probes usable, and, when
invoked with `--installed-plugins <path>` naming a hand-built `{<worktree-path>: <version>}`
JSON map, flags mixed plugin versions across a repository's worktrees (the real
`~/.claude/plugins/installed_plugins.json` has a different shape and is not read directly);
`uninstall --dry-run` prints the exact
removal and reversal commands (for a plist: `launchctl unload ~/Library/LaunchAgents/<label>
&& rm ~/Library/LaunchAgents/<label>`, to run only if you ran the disclosed activation step --
the staged copy the verb removes itself), naming every emitted file that still exists, and
`uninstall` leaves zero emitted artifacts (the staged plist, the workflow and its vendored tree
with the `.github/workflows/` and `.github/` directories they emptied, the `test` verb's
`.claude/om-state/` scratch) and drops the `.claude/hyp.json` `om_offload` key; `compose`
refuses a probe row that claims `usable` without a recorded exit 0; `report` also flags a plist
whose baked worker path no longer exists (`-- missing path: <p>`); a lock, hyp.json or
`--installed-plugins` file that does not parse is a typed `void: corrupt-json <path>` (exit 2,
nothing changed). A later `emit` on a host whose answer changed (a handle that composed earlier
no longer probes usable) prints `no longer holds: <handle>` and unions the prior lock's artifacts
into the new lock so `uninstall` still removes them (B1, ship fix round 4).

Ported by intent from the lab keep `H-DRAFT-e2a5e911-om-integrate-probe` (kept 2026-09-15: five
counted looks, A1-A5 pass in every one). Three drifts from the kept fixture bytes: the `ci-tier0`
handle no longer carries its own workflow template (the fixture's copy rendered
`workflow_dispatch: {{}}`, a `str.format` escaping defect PyYAML rejects -- VERIFY.md finding 1
of that lane; this ship delegates to the already-shipped `om-ci.py` emitter instead); the
`launchd-queue` plist's `QueueDirectories` and log paths now name the worker's own real inbox
directory (`state_root_for_repo`/`state_root`, duplicated in `om-integrate.py` with attribution)
instead of a fixture-only scratch path the plist's own `ProgramArguments` never actually read
from; and every probe row gains a `host_key` field beside `authors_90d` and `disk`, so a
`ledger/om-substrates.jsonl` shared across machines can tell which host produced which row.
`scripts/selftest-om-integrate.py` proves probe/compose/emit/test/report/uninstall against a
throwaway consumer and a selftest-only stub substrate, never against the real host's launchd or
GitHub Actions.

**Credential policy.** `compose` never binds a model-calling tier to a shared subscription token
in a repository with more than one recent author: given a consumer's own request for a
model-calling tier's credential (`.claude/hyp.json` `om_credential_tier` + `om_credential_class`,
or `--tier`/`--credential-class` for a cold caller), it runs `scripts/om-credential-policy.py`
(one call site, its own imported entry, never folded into `compose`'s mechanism picks) over the
same probe rows: `authors_90d` > 1 and class `shared-subscription-token` prints one
`CREDENTIAL-REFUSED tier=<tier> class=shared-subscription-token authors_90d=<n>` line and three
`OFFER` lines (`api-key repository-owned`, `federation workload-identity`, `platform-identity
oidc`, in that order), `emit` writes no `credential` entry into `.claude/om-offload.lock.json`
for that tier and exits 3; `authors_90d` = 1, or any of the three offered classes, proceeds
with no refusal (a single CREDENTIAL-OK line) and `emit`'s lock carries `credential: {tier,
class, allowed: true}`. An OAuth token
from `claude setup-token` is tied to the subscription of the person who ran it, so wiring it into
a repository more than one person commits to makes every model call by every author look like
one person's -- the attribution failure GOVERNANCE.md's Recoverability invariant names, and its
Isolation invariant wants concurrent work to reach the shared line through attributed changes
rather than a shared credential. No handle shipped today (`launchd-queue`, `ci-tier0`, ...) calls
a model, so no consumer declares this request yet and the clause is a no-op on every call this
plugin itself makes -- it guards the place a future model-calling tier's credential would be
bound, never invents a token-binding feature of its own; `ci-tier0` in particular is the
zero-credential CI tier (`om-ci.py self-test ci-tier0` proves it never spawns `claude`) and needs
no credential decision at all. Evidence: lab `H-DRAFT-744a5773-om-credential-policy`, kept
2026-09-16 -- five counted looks, A1 pass in every one, cold-verified (`VERDICT.json`,
`VERIFY.md`, journal fragment 0581; five cold fixture refute rounds preceded the looks).
`python3 scripts/selftest-om-credential-policy.py` ports A1 onto the live plugin tip.

Undo: revert the release's merge commit; if you already ran `emit` or `test`, run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" uninstall --root .` first (dry-run with
`--dry-run`) -- it removes everything those verbs wrote and prints the one
`launchctl unload ... && rm ~/Library/LaunchAgents/<label>` line you run only if you had
activated the plist. Rows already written are plain JSON lines in your ledger; the attribute
row is plain text in your `.gitattributes`; a staged-but-uncommitted ledger unstages with
`git restore --staged -- ledger/om-feedback.jsonl`.
