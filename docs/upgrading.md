# Upgrading to hyp from lab-intake + lab-loop

These steps are the counted upgrade path (hypothesis H-161 in the source lab, kept
2026-08-26: two runs, zero byte loss, guards and loop verified, rollback proven).

1. **Uninstall the lab pair.** Disable or remove `lab-intake` and `lab-loop`
   (marketplace uninstall, or set them false under `enabledPlugins` in
   `.claude/settings.json`). Your repository's artifacts — journal fragments, raw
   captures, ledger, hypothesis specs, dashboard — are files in your repo; uninstalling
   plugins never touches them.
2. **Install hyp** from the same marketplace and run `/hyp:init` in the repository.
   Init detects existing lab artifacts and upgrades in place: it preserves every
   existing file byte-for-byte, replaces the legacy lab CLAUDE.md marker blocks with
   the hyp block, and wires the write-once guards and the dashboard.
3. **Verify** (what the counted runs checked): pre-existing write-once files unchanged;
   an attempted edit of a landed journal fragment is denied; the dashboard recompiles
   with your open decisions intact; registering a hypothesis and landing a fragment
   works under the `/hyp:*` prefixes.
4. **Rollback (if you need it):** uninstall hyp and re-enable the lab pair — the
   counted runs verified the write-once history remains byte-identical in that
   direction too. The swap is a two-way door.

Since 0.3.3, ids follow the draft-then-allocate contract: a hypothesis registered anywhere
other than the default branch takes a draft handle instead of a numeric id, and the numeric id
is allocated at land by `scripts/id-rectify.py`. Nothing about an existing corpus changes on
upgrade; every landed `H-NNN` and fragment id keeps resolving exactly as it does today, and the
new rule only governs registrations made after you upgrade. See `docs/id-allocation.md` for the
full contract.

From the release that carries the decision-brief gate, every new decision card needs a plain-English
brief (`decisions.py add --brief <brief.json>`; `docs/decisions.md`, "The plain-English brief"), and a
card without a valid one is never shown as a question. Cards already on file are exempt by id: set
`decision_brief_legacy_max_id` in `.claude/hyp.json` to your highest pre-upgrade decision id (absent,
the boundary is shape and order: every card appended before the first briefed one is legacy). Each open
legacy card renders exactly as before plus one `brief: BRIEF-MISSING` line naming the retrofit command
(`decisions.py brief <id> --brief brief.json`); resolved cards render byte for byte as before. The three
admission-tiered prose rules (B6, B9, B10) are report-only until you list them in
`decision_brief_admitted_rules`; the shipped writers that file cards (`retest-trigger.py`,
`knob-observe.py`, `dispatch-gate.py ingest`, `reflex-surface file`) are refused with the recipe until each
carries a brief. `python3 scripts/selftest-decision-briefs.py --live .` checks the marker rendering over your
own ledger.

From the release that carries the hook-writes fix (lab H-DRAFT-b9e771b2-hook-writes-worktree),
every hook writes into the checkout the session works in. Before it, a session that entered a
linked worktree after launch still had `DASHBOARD.md`, `decisions.html` and the license-join
housekeeping written into the MAIN checkout: Claude Code keeps `CLAUDE_PROJECT_DIR` at the launch
directory after the switch, and those writers fell back to it. Those writes never rode the
worktree's pull request and collided with everyone else's on main. Now every hook row resolves
its root through one contract, `hyp_config.resolve_root`: the payload cwd's checkout when it is
the project or another checkout of the same repository (a linked worktree), else the process
cwd's, else `CLAUDE_PROJECT_DIR`. Nothing about what the hooks write changes -- only where.

Two files the hooks and writers touch also get a declared merge shape, so two worktrees that both
appended a ledger row and both recompiled the dashboard merge without a manual edit:

- `<ledger_file>` (default `ledger/ledger.jsonl`) and `.claude/leak-meter-fires.log` are
  `merge=union`. Their contract: one self-contained JSON object per line, newline-terminated,
  each row carrying its own `date`, so file order never matters and a union merge is a valid
  ledger. The writer (`decisions.py append_line`) refuses a row that would span lines and
  repairs a missing final newline before appending; `scripts/merge-attrs-check.py` lints the
  file and the harden-check prints one `ADVISORY-36 merge-attributes` line when a row is
  malformed or a shape is undeclared.
- `DASHBOARD.md`, `decisions.html` and `ledger/north-stars/*.html` are `merge=binary -diff
  linguist-generated`: compiled projections are regenerated from their sources, never merged
  line by line, and git writes no conflict markers into them. When a merge stops on
  `DASHBOARD.md` or `decisions.html`, run exactly:

  ```
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-dashboard.py" <root>
  git add DASHBOARD.md decisions.html
  git commit --no-edit
  ```

  When it stops on a north-star page (`ledger/north-stars/*.html`, written by
  `compile-north-star-progress.py --all`: one `<slug>.progress.html` per committed north-star
  file plus `index.html`), regenerate the set the same way:

  ```
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-north-star-progress.py" --all --repo <root>
  git add ledger/north-stars
  git commit --no-edit
  ```

What you do: re-run `/hyp:init` once in each repository (it appends the missing rows to
`.gitattributes` and never removes yours), or copy the rows from the plugin's
`templates/gitattributes`. Nothing else changes; existing ledgers and projections are read as
before. To undo, revert the merge commit that landed the release (the attribute rows are plain
text in your `.gitattributes`; deleting them restores the previous merge behavior).

From the release that carries the decision queue (source lab H-DRAFT-015cb9c8-decision-queue-projection, kept
2026-09-13), every session start says the caller's own count — `Decisions: <n> are yours (...) — acting as <role>
(<basis>). Answer them: /hyp:decisions` — through a new SessionStart hook row (`decision_queue.py announce --hook`,
its own 10 s timeout, every source), and `/hyp:decisions` walks those cards through the ask-user prompt, one committed
row per answer. After upgrading: (1) set `decision_roles` in `.claude/hyp.json` — at least
`{"decision_roles": {"maintainer": ["<the canonical email from git config user.email>"]}}` (an identifier, never a
name; a repository whose ledger history carries exactly one author works without it under `basis: single-identity`,
and an unmapped role is still visible and answerable, printing the `ADDRESSEE-UNMAPPED` recipe); (2) re-run `/hyp:init`
(or add `<ledger_file> merge=union` to `.gitattributes` by hand) so two checkouts' appended rows merge without conflict
markers — `harden-check.sh` prints `ADVISORY-36 merge-attributes` while the row is missing (the merge shapes of the
hook-writes release above); (3) address a card to
another role with `decisions.py add --addressee <role>` and map that role under `decision_roles`. Existing cards and
resolution rows change no bytes: a card without an `addressee` reads `maintainer`, `list`, `show` and the board render as
before plus the multi-user finding lines, and `resolve` now commits through a single-line committer that never refuses a
dirty ledger (`RESOLVE-BLOCKED` is retired) and refuses a non-addressee's closing answer before anything is appended.
Harnesses that spawn many headless children set `HYP_DECISIONS=off` to skip the announce's git calls. Undo: revert the
release's merge commit; the rows already written carry only the admitted fields (`via`, `addressee_basis`, `override`,
`settles`, `retest_when`), which the previous kit reads as ordinary resolution rows.

From the release that carries the passive feedback worker (source lab H-DRAFT-35397146-om-worker-deterministic, kept
2026-09-13), the plugin ships `scripts/om-worker.py`: a deterministic script that turns a finished session's transcript
into one `session-observed` row and each operating-model tree into one `model-evaluated` row in
`ledger/om-feedback.jsonl` (`.claude/hyp.json` `om_feedback_file` overrides the path), spending no tokens. Nothing runs
it for you yet -- the startup wake ships with its own lane (the outbox and the commit path have
since shipped, below) -- so after
upgrading there is nothing to do; to try it, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-worker.py" observe
<transcript.jsonl> --root .` and `... evaluate --root .`, then `... status --root .`. Re-run `/hyp:init` once (or add
`ledger/om-feedback.jsonl merge=union` to `.gitattributes` by hand) so two checkouts' appended rows merge without conflict
markers -- the re-run keeps every key your `.claude/hyp.json` carries beyond the plugin defaults (`ledger_file`,
`om_feedback_file`, `compile_command`, the decision settings), which earlier releases dropped on a re-init; `merge-attrs-check.py` names the row as missing only once the file exists. No existing file changes shape. Undo:
revert the release's merge commit; rows already written are plain JSON lines. See `docs/passive-feedback.md`.

From the release that carries the outbox carry-forward (source lab H-DRAFT-a4a14ff4-om-outbox-carry-forward, kept
2026-09-14), `om-worker.py drain` stops losing the row of a session whose worktree was removed before the worker ran:
a pointer whose own `root` no longer exists lands its row in a per-repository outbox instead of the pointer being
quarantined, and the next `drain` of any live checkout of that same repository carries it in, `landed_in` reading
`root`, `outbox` or `carried`. After upgrading: no pointer carries `root`/`common_dir` yet (the startup wake lane,
H-DRAFT-10383178, writes them), so every pointer that IS found still lands exactly as before -- BUT for any `root`
that is itself a live git checkout, `drain` with no `--inbox` override now reads a DIFFERENT default inbox directory
than the release before this one (`<state>/om/<repo-key>/inbox/`, keyed by `root`'s own live `common_dir`, not
`<state>/om/<sha256(realpath root)[:16]>/inbox/`). If you hand-write pointer files straight into that old default
path without going through `--inbox`, move them under the new path or pass `--inbox` naming the old directory
explicitly -- everything scripted through `om-worker.py drain --root .` with no pointers of your own is unaffected.
See `docs/passive-feedback.md`, "The outbox and carry-forward" and "Running it by hand". Undo: revert the release's
merge commit; rows already written are plain JSON lines, distinguishable only by their `landed_in` value.

From the release that carries the commit path (source lab H-DRAFT-fb9c08b9-om-ledger-commit-path,
kept 2026-09-14), `hooks/scripts/commit-backstop.py`'s `git commit` PreToolUse row also stages a
dirty feedback ledger for you: `OM-FEEDBACK-STAGED <n> rows` when it stages it, or
`OM-FEEDBACK-HELD forbidden key <key>` when a row on disk must never be written and nothing is
staged. It runs before, and independently of, the pre-existing advisory backstop above (the
feedback ledger is a capture-profile feature), so nothing here is gated on `profile:
"experiments"`. After upgrading there is nothing to do: the clause fires only when the ledger
(`om_feedback_file`, default `ledger/om-feedback.jsonl`) is already dirty. One limitation to
know: the clause fires only on a command that itself begins with `git commit` -- `timeout 45 git
commit ...`, `cd <dir> && git commit ...` and `git -c ... commit ...` are silent, so run `git
commit` directly (or stage the ledger yourself first) when you rely on it. Undo, one command:
`git restore --staged -- ledger/om-feedback.jsonl` before you commit; the release's merge commit
can also be reverted outright. See `docs/passive-feedback.md`, "The commit path".

From the release that carries the model-routing guard (source lab H-DRAFT-314c8d17-routing-guard,
VERDICT.json evidence-sufficient promote), every `agent()` call inside a Workflow script is checked
against a committed role -> model/effort table (`rules/routing-default.json` merged with
`.claude/routing.json`) by a new `PreToolUse` hook row on the `Workflow` and `Agent` tools. Nothing
is denied yet: the row's default is `routing.enforce: advise` (one advisory line per finding,
never blocking) until you set it to `deny` in `.claude/hyp.json`. After upgrading: (1) run
`/hyp:init` once — it scaffolds `.claude/routing.json` from the plugin template (an empty
override; never overwrites an existing one on a later re-run); (2) read `docs/model-routing.md`
for the table, the finding classes, and the `// route-override: guard-false-positive <reason>`
escape for a single false positive; (3) when you are ready to enforce it, set
`{"routing": {"enforce": "deny"}}` in `.claude/hyp.json`. Existing workflow scripts that already
name `model`/`effort` on every call are unaffected either way. Undo: revert the release's merge
commit, or set `routing.enforce: off` (the hook still runs -- it writes its start/finish marks --
but exits before it opens the script).

From the release that carries the routing ledger (source lab
H-DRAFT-38f86fad-routing-ledger-row, VERDICT.json evidence-sufficient promote), a new
synchronous `Stop` hook row (timeout 15 s) and `SubagentStop` hook row (timeout 10 s) append
one `agent-route/v1` covariate row per finished workflow agent to
`ledger/routing-ledger.jsonl` in the checkout you work in — declared and observed model,
tier, class, role, label, tokens, cost, and outcome, joined to a workflow's own run record
when the driver opts in (see `docs/model-routing.md`, "The routing ledger"). Nothing is
denied: this is an observational hook, never a gate. After upgrading, run `/hyp:init` once —
it appends the `ledger/routing-ledger.jsonl merge=union` row to `.gitattributes` so two
worktrees' rows merge instead of conflicting. Nothing else to do; the writer creates the
ledger file on its first write. Caveats: only the `Stop` row is evidenced on real workflow
agents (the `SubagentStop` row's firing on a live subagent was never measured in the source
lane's build); the writer's own wall grows with host load, and a heavily loaded host may
still push a *different* row in the same `Stop` batch past its own timeout — check a row's
`host_load_1m` covariate first if rows look sparse. Undo: revert the release's merge commit
(there is no enforce-style off switch; the hook never denies anything to turn off).

From the release that carries the operating-model catalogue projection (source lab
H-DRAFT-4e06e157-om-rows-merge-shape, kept 2026-09-14), `operating-model/<context>/model.md` stops
being a file you or a teammate hand-edit: it is regenerated by `scripts/compile-catalog.py` from
the node files under it, and `/hyp:init` now installs a `.gitignore` row naming it
(`templates/gitignore`). After upgrading, run `/hyp:init` once in each repository: it adds the
ignore row (or add `/operating-model/*/model.md` to `.gitignore` by hand -- the anchored form init
installs; an un-anchored `operating-model/*/model.md` row already present also counts, so a later
`/hyp:init` appends no duplicate either way), and if the repository
still tracks a `model.md` from the old hand-maintained shape for the context `/hyp:init` is
running against, that same run retires it from the index with one `git rm --cached` -- a
multi-context repository retires each context on the run that targets it, so run `/hyp:init`
once per context, or retire the rest by hand -- the work-tree file stays exactly as it was, and the retire
shows up as an ordinary change in your NEXT commit (it does not commit anything itself). If git
declines the retire -- a staged `model.md` edit differing from both HEAD and the work tree --
`/hyp:init` prints one `retire-refused` line (never `retired`) and leaves the file tracked; unstage
or commit that edit and re-run. Until the retire lands, every `evaluate`/`compile-check` regenerates
the still-tracked `model.md` in your work tree, so it shows as modified after each run; the retire
ends that. Regenerate
the catalogue at any time with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-catalog.py"
operating-model/<context> --write`; the `adopt` skill's ratification step calls this before its
self-lint, and `scripts/om-worker.py evaluate`/`compile-check` call it before reading the tree
(`compile-check` failing closed with a nonzero exit if the renderer script is missing, rather
than lint or date-stamp a stale catalogue). The startup wake (below) does not call this: it runs
`drain`, never `evaluate`/`compile-check`, so a fresh clone with nobody having re-run either verb
yet still shows whatever `model.md` copy was last committed (absent, once retired). Undo:
revert the release's merge commit that added the ignore row; if a retire commit already landed,
`git add -f operating-model/<context>/model.md` re-tracks the current work-tree file. See
`docs/passive-feedback.md`, "The catalogue projection".

From the release that carries the operating-model tier-0 CI check (source lab
H-DRAFT-a28b91c9-om-ci-tier0, kept 2026-09-15), `scripts/om-ci.py emit ci-tier0` writes a
zero-credential GitHub Actions workflow that lints the operating model and checks compile
staleness on every push and pull request touching `operating-model/**`. Nothing runs on its
own: after upgrading, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-ci.py" emit ci-tier0`
once in the repository and commit `.github/workflows/om-check.yml` plus the vendored
`.github/om-scripts/` it names (the workflow calls those vendored scripts, never the plugin
install — a GitHub-hosted runner has neither). Both jobs check out full history
(`fetch-depth: 0`): the staleness check dates paths by their last commit, and the checkout
action's default depth-1 clone would read every stale tree as clean. The check's glue calls
the vendored `om-worker.py`'s own path and staleness functions, so a repository that moved its
feedback ledger through `om_feedback_file` or its model through `model_dir` is checked exactly
as `om-worker.py` sees it, and a ledger that already holds an identical row cannot hide a stale
tree. Re-running `emit ci-tier0` is idempotent and
byte-stable, and a file you have hand-edited is kept, not overwritten, unless you pass
`--force`. Verify locally without pushing anything with
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-ci.py" self-test ci-tier0` (also
`scripts/selftest-om-ci.py` in this plugin's own tree). What is still owed: the hosted
acceptance check (`github.actor` semantics for a bot push, whether that push re-triggers the
workflow, and the hosting service's own evaluation of the one-`paths:`-list trigger) — see
`docs/ci-scaffold.md`, "Shipped ahead of the scaffold". Undo: delete
`.github/workflows/om-check.yml` and `.github/om-scripts/`; nothing else in the repository
depends on either.

From the release that carries the startup wake (source lab `H-DRAFT-10383178-om-startup-wake`,
kept 2026-09-15), `hooks.json`'s existing `resolver` `SessionStart` row gains one `--also
om-worker '...'` clause: every `startup` now runs `scripts/om-worker.py drain` in the background,
landing the previous session's feedback row in your checkout's ledger with no command of yours
and no added foreground interpreter start on the row you actually see. After upgrading: nothing
-- it runs on the next `startup` with no configuration. The caveat: under heavy host load the row
can arrive one session-start boundary late (see `docs/passive-feedback.md`, "The startup wake").
Undo: revert the release's merge commit, or remove the `--also om-worker '...'` clause from the
resolver row in your `hooks/hooks.json` if you have forked it.

From the release that carries the startup reading surface (source lab
`H-DRAFT-3aef12a5-om-startup-reading-surface`, kept 2026-09-15), the SAME `startup` row that
already wakes the passive feedback worker in the background now also PRINTS its last landed
reading: up to 8 lines prefixed `OM-FEEDBACK: `, the first carrying how old the reading is,
never the worker's own content verbatim where it would match one of two structural leak checks
(see `docs/passive-feedback.md`, "The reading surface"). The `resume|clear|compact` cached row
gains the same reading under the same hold. After upgrading: nothing -- it shows on the next
`startup` (and next `resume`/`clear`/`compact`) with no configuration, and shows nothing at all
until a wake has landed a reading for this checkout. Undo: revert the release's merge commit, or
remove the `om-worker` name from the `cached` row's argument list and drop the
`print_om_feedback` call from `hooks/scripts/session-start-budget.py` if you have forked it.

From the release that carries the model-routing determinism port (source lab
`H-DRAFT-75b03e6e-routing-determinism`, kept: five counted looks 5/5, llr 2.9389 >= 2.8904),
every role in `rules/routing-default.json` gains a compiled agent definition at
`agents/hyp-<role>.md`, pinning that role's model in the definition's own frontmatter —
independent of, and a second input beside, the call-site `model`/`effort` literal the guard
already required. `scripts/compile-model-workflow.py`'s emitted portable runner's
`--model-low` flag is deprecated the same way: passing it still overrides the model as
before (one deprecation line on stderr), but omitting it now resolves the `gate` role's
model from this same table instead of a hardcoded `haiku` literal. After upgrading: nothing
— the committed `agents/hyp-<role>.md` files ship as-is; run
`python3 scripts/compile-routing-agents.py rules/routing-default.json --emit <dir>` to
install the project-scope copy a consumer repository's own `.claude/agents/` needs (the
surface the lab lane measured live; see docs/model-routing.md, "The compiled agent
surface", for what remains unmeasured). Undo: revert the release's merge commit.

From the release that carries the model-routing derive loop (source lab
`H-DRAFT-d5a8d9b6-routing-derive`, kept: five counted looks 5/5, the frozen SPRT walk to the
promote bound), `hooks/scripts/routing-derive-cadence.py` runs at every `Stop`, in the SAME `Stop` event as the
routing ledger's own row-append (hooks in one event run in parallel, so it reflects rows landed
by earlier turns, not necessarily this turn's own), and writes `routing-report.md` plus (if
this repository's ledger already supports one) a candidate spec under
`.claude/routing-candidates/` (that directory and `.claude/routing-derive-cache/` are ignore rows `/hyp:init` appends to your `.gitignore`) — it never
edits a routing table itself; a candidate is only ever a draft hypothesis spec for a human or
`hyp:hypothesis` to register. `scripts/compile-dashboard.py` gains a `## 4. ROUTING` section
compiled from `routing-report.md`, present only once that file exists. After upgrading:
nothing — the first `Stop` after your next agent-route row writes `routing-report.md`; a
repository with no routing history yet gets no report, no candidate, and no dashboard section
at all. Undo: revert the release's merge commit, or remove the `routing-derive-cadence.py` row
from the `Stop` hook in `hooks/hooks.json` if you have forked it — the CLI
(`scripts/routing-derive.py`) and its selftest keep working standalone either way.

From the release that carries the integrator (source lab `H-DRAFT-e2a5e911-om-integrate-probe`,
kept 2026-09-15: five counted looks, A1-A5 pass in every one), the plugin ships
`scripts/om-integrate.py` and the `integrate` skill: probe this machine for a background
mechanism that can drive the passive feedback worker, compose exactly one on-device and one
remote candidate from what actually probed usable, and emit -- never load or activate -- a plist
or (delegated to the already-shipped `om-ci.py`) a GitHub Actions workflow, with a `test` step
and a one-command `uninstall`. After upgrading: nothing runs differently -- no verb in this file
is called by any hook or session-start step; run the `integrate` skill (or
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" probe --root . --json`) when you want to
see what this host can offer. Undo: revert the release's merge commit, or run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" uninstall --root .` first if you already
had it emit something.
