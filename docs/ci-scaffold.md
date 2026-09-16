# The CI scaffold: the keep-regression net for consumer repos

The CI story for hyp-conventioned repositories, per the counted keep-regression contract
(source lab: `experiments/runs/H-198/fixture/keep-regression-contract.md` §3, frozen at
registration; excerpt-complete edition). This document is the normative reference; the
deterministic scaffold script itself (`scripts/ci-scaffold.py`) ships in a later release
when the counted implementation is promoted — what is fixed now, by contract and by two
counted 5/5 runs, is the shape below.

## Mechanism (decided)

An **opt-in** CI subcommand (`/hyp:ci` in this plugin's namespace; `/lab-loop:ci` in the
counted contract) running a deterministic `ci-scaffold.py` that inherits the init-scaffold
conventions byte-for-byte: idempotent, byte-stable, created/updated/unchanged/kept lines,
refusal exit 1 with instructions and zero writes when the plugin's config files are absent.
CI is NOT folded into plain init: writing workflow files triggers paid compute on the
consumer's GitHub account, so it must be an explicit ask. Plain init writes nothing under
`.github/` — proven as a counted should-NOT-act assertion.

**Single source of truth:** the tier-0 job commands live in ONE JOBS table inside
ci-scaffold.py, (a) rendered into the workflow's `run:` blocks and (b) executed locally by
`ci-scaffold.py --self-test` (exit 0/1). Self-test == CI by construction — the no-GitHub
verification path for consumers and for the plugin's own evals.

## The three written artifacts (decided)

1. `.github/workflows/lab-ci.yml` — **plugin-owned**: overwrite=True, restored
   byte-for-byte on re-run. Three tier-0 jobs:
   - **preflight** — on PR, the merge-base diff filtered to the configured hypotheses dir;
     the repo's INSTALLED preflight runs per changed spec (MALFORMED and ESCALATE both fail
     the check — ESCALATE in CI terms means a human resolves it in review). On the weekly
     sweep/dispatch: all non-terminal specs.
   - **journal-integrity** — the journal compiler (exits 2 on duplicate/missing ids) plus
     write-once-by-history: no modify/delete/rename rows in the PR range under the raw or
     fragment dirs and no modification of the frozen journal file. This is the CI mirror of
     the write-once PreToolUse hook, closing the hole where pushes made OUTSIDE a Claude
     session bypass hooks entirely.
   - **sha-pin** — installed plugin-owned scripts byte-match the pins in
     `.claude/lab-ci.json`: a consumer silently weakening their preflight gets a red check.
2. `.github/workflows/lab-ci-local.yml` — **consumer-owned stub**: created once, never
   overwritten; where repo-specific jobs go. This file split IS the ships/lab-only
   boundary: extending your CI can never fork the shipped artifact. Normative initial shape
   — **and the doctrine: the least-privilege permissions block applies to EVERY workflow
   file the scaffold emits, the stub included**:

   ```yaml
   # .github/workflows/lab-ci-local.yml — consumer-owned: repo-specific CI jobs go here.
   # Created once by the CI scaffold and never overwritten again.
   name: lab-ci-local
   on:
     workflow_dispatch: {}
   permissions:
     contents: read
   jobs: {}
   ```

3. `.claude/lab-ci.json` — plugin-owned config: chosen tier, the paths copied from the
   plugin config at scaffold time, and an `installed_sha256` map pinning the
   plugin-installed executables.

## Tiers and the secrets doctrine (decided)

- **Tier 0 — scripts-only, THE DEFAULT** and the only thing the plain command emits:
  python3 stdlib + git; no secrets, no network beyond checkout, no model calls; pennies of
  Actions minutes.
- **Tier 1 — plugin eval suites, opt-in via `--with-evals`:** real model sessions per case.
  Three honesty parts: the scaffold NEVER touches secret values (it prints named-secret
  setup instructions and the generated job's first step exits 0 with a notice when the
  secret is absent, so fork PRs never fail on it); a cost-warning block is rendered atop
  the generated job AND printed at write time; the evals job defaults to
  `schedule` + `workflow_dispatch` only — putting model-session evals on every PR requires
  a second explicit flag.
- **Version pinning:** action refs pinned to SHAs vendored in the plugin, never floating
  tags; `permissions: contents: read` on every emitted workflow; no secret values written
  anywhere.
- **Deliberately NOT scaffolded:** no plugin install inside CI, no drift-check job (needs
  session-side templates; no fake coverage).

## Evidence

**H-198-ci-scaffold-v2** (source lab, kept 2026-08-27, two consecutive counted 5/5): the
scaffold wrote exactly the three artifacts and was idempotent and refusal-safe; `--self-test`
ran the same command table the workflow renders (byte-wise equality checked) and went red on
a seeded malformed spec and a seeded write-once violation, green on restore; plain init
wrote nothing under `.github/`; generated files carried no secret values, only SHA-pinned
action refs and `permissions: contents: read`; both runs byte-identical with zero model API
calls in the scripts tier.

The refine lineage is itself the load-bearing lesson: H-131's two counted 4/5s shared one
identical omission — both independent arms left the `permissions:` stanza out of the
consumer-owned stub — root-caused to the arm-visible contract excerpt never rendering the
stub's normative YAML. The excerpt-complete edition (one change: the stub shape above plus
the every-emitted-file doctrine sentence, rendered normatively) went from diagnosis to a
kept 2x5/5 in under two hours. A contract that wants a property in every emitted artifact
must SHOW the artifact, not imply it.

## Shipped ahead of the scaffold: the operating-model check (`om-check.yml`)

The tier-0 job shape above is registered but not yet implemented (`ci-scaffold.py` "ships in a
later release"). One tier-0 job shipped separately and sooner, scoped to a narrower question:
`scripts/om-ci.py emit ci-tier0` writes `.github/workflows/om-check.yml`, a two-job workflow
(`lint`, `compile-check`) that answers "does this push or pull request leave the operating model
lint-clean and its compiled catalogue caught up" on every change under `operating-model/**` — it
never runs a model session, never touches a secret beyond the checkout's own `GITHUB_TOKEN`, and
never scaffolds anything beyond that one question (no preflight, no journal-integrity, no
sha-pin job — those remain `ci-scaffold.py`'s to build). `lint` regenerates the catalogue
(`compile-catalog.py --model-dir`) and runs `model-lint.py` per context, failing the check on
any `ERROR` (an `E-LINK` dangling reference, for instance). `compile-check` re-runs the same
regeneration in check mode, reports whether the compiled artifact under `compiled/**` is stale
against the model tree, and — only for a normal contributor's push, never the bot's own — runs
the declared `compile_command` and commits the regenerated bytes once as `om-check[bot]`,
pushing that commit with the checkout-persisted token.

**One `paths:` list per event, never `paths-ignore:` beside it.** The hosting service's
workflow-syntax reference is explicit: a `push` or `pull_request` block cannot carry both
filters on the same event. Excluding `compiled/**` from the trigger (so the bot's own
regenerate-commit push can't re-fire the check on that path alone) therefore rides inside the
one `paths:` list as a `!compiled/**` entry, evaluated in order — the last matching pattern
decides. `scripts/om_check_jobs.py` renders and self-tests this same list, so the rule can't
drift out of sync with what actually ships (Amendment #1 in the source lab's
`H-DRAFT-a28b91c9-om-ci-tier0`, filed before any counted look).

**Vendoring, not a plugin-path reference.** A GitHub-hosted runner has no `${CLAUDE_PLUGIN_ROOT}`
and no `~/.claude` plugin cache, so `emit ci-tier0` copies `om-worker.py`, `model-lint.py`,
`compile-catalog.py`, `observatory.py` (required by `om-worker.py`'s own validation, never
called directly), this check's two glue scripts, and pure-Python PyYAML (no compiled
accelerator — `model-lint.py`'s `import yaml` otherwise fails outright under an empty `HOME`,
the exact CI-runner shape) into `.github/om-scripts/`, with a sha256 `MANIFEST.json`. The copy
is idempotent and byte-stable, and never overwrites a file a consumer hand-edited without
`--force`.

**The stale verdict is asked of `om-worker.py`, not read back from its ledger.** The two glue
scripts load the `om-worker.py` vendored beside them (same `emit`, same `MANIFEST.json` sha)
and call its own `_model_trees`, `_compiled_staleness` and `ledger_rel` on the checkout; the
report step prints one `STALE:` line per tree from that call plus an informational `LEDGER:
<path> present|absent` line. Ship fix round 5 (B1): rounds 1-4 read the last N rows of a
hardcoded `ledger/om-feedback.jsonl` as "the rows the compile-check step just appended", and
both halves of that fail on documented `om-worker.py` behaviour -- it resolves the ledger
through `.claude/hyp.json` `om_feedback_file` (the refuter's measurement on a consumer that set
the key: `om-worker.py` wrote one file, the report step read another, `STALE: unknown`,
`COMMIT: no (nothing stale)`, green on a stale tree), and its `append_to_path` dedupes rows by
exact bytes against the whole file and returns `duplicate` silently, so a committed ledger that
already carries this checkout's byte-identical `model-evaluated` row (a session hook evaluated
the same commit locally, session rows followed, the ledger was committed) followed by any other
row leaves the tail without a row from this pass: no `STALE:` line, the regenerate step never
runs, green on a stale tree -- the same vacuous-green class as the depth-1 checkout in round 4.
The second shape surfaced in the self-test itself once the scratch commit dates were pinned
(A2) and two scenarios' rows became identical: the detached scenario's regenerate commit
silently did not happen, and the rejected-push scenario still passed because a push to an
unreachable origin fails with or without a commit; both checks now require the local bot
commit. The self-test seeds the override consumer (a stale mutant whose `hyp.json` sets
`om_feedback_file`) on a fresh clone, pre-builds the dedupe shape in its ledger, and requires
`STALE: True`, exactly one bot commit, the ledger unchanged by the pass and the default path
still absent. The report step is fail-closed on the one shape that yields no verdict (A1): it
exits non-zero when the vendored `om-worker.py` cannot be loaded; a `None` verdict (a date
`om-worker.py` cannot read) is printed as-is and exits 0, as before.

**The bot-loop guard.** The regenerate-and-commit step, and the push step after it, run under
`if: ${{ github.actor != 'om-check[bot]' }}` — re-checked a second time inside
`om_check_regen_commit.py` itself, which reads `GITHUB_ACTOR` and no-ops if it is somehow still
the bot identity (it parses nothing; it is a belt-and-suspenders re-read of the same env var the
step-level `if` already gates on), so a future template edit that drops the step-level `if`
cannot reopen the loop silently. Hosted, this guard is expected to be belt-and-suspenders only
(ship fix round 4, A3): the hosting service documents that pushes made with the workflow's own
`GITHUB_TOKEN` do not create new workflow runs, and if one did, its `github.actor` would read
`github-actions[bot]`, not `om-check[bot]`, so the rendered `if:` predicate is expected never to
evaluate false hosted -- the loop is closed by the token's own semantics first and by this
predicate second. That expectation is stated here up front and recorded, not asserted, by the
hosted acceptance check below.

**Full-history checkout (`fetch-depth: 0`).** Both jobs' `actions/checkout@v4` steps render
`fetch-depth: 0`, never the action's default depth-1 checkout (ship fix round 4, B1).
`om-worker.py`'s staleness read dates the model tree and the newest compiled artifact by
`git log -1 --format=%ct -- <path>`; on a depth-1 clone every path's last commit is the one
grafted tip, so both dates collapse to the same epoch and a stale tree reads `STALE: False` /
`COMMIT: no (nothing stale)` -- the compile-check half of the check would be vacuous hosted,
green on every push, regenerating nothing. Measured in the self-test on two fresh `file://`
clones of the same pre-regeneration stale ref: the depth-1 arm reads clean and commits nothing;
the full-history arm reads stale and regenerates exactly one bot commit. The lane's
`BUILD-RECORD.json` (`open_questions[3]`) reasoned that a depth-1 checkout "would work
identically" because the harness diff needs only the tip's parent; that reasoning covered the
trigger diff, not the per-path dating, and this document supersedes it (A2). The self-test pins
the four-line checkout block as literal text once per job (`_assert_a1_guardrails`, and
`scripts/selftest-om-ci.py` against the committed template) because the local executor never
runs the YAML's checkout step itself -- a renderer that dropped the key would still equal its
own re-render. Cost: one full-history fetch per job, bounded by `timeout-minutes: 5`; a
consumer whose history is too large for that cap should say so in an issue rather than lower
the depth.

**Two departures from the decided doctrine above.** `om-check.yml` references
`actions/checkout@v4` and `actions/setup-python@v5` by floating tag, not by pinned SHA, and its
`compile-check` job carries `permissions: contents: write`, not `read` (`lint` stays `read`).
The tags are the kept ON bytes: the lane graded the rendered template as measured, and a SHA
pin is a template change no counted look covered, so it is a follow-up ship with its own
self-test rather than a silent edit here. `write` is what the regenerate push needs, scoped to
the one job that pushes. Both are recorded so the scaffold's own doctrine lines are not read as
already satisfied by this template (ship fix round 5, A3).

**What is still owed.** Everything above is proven against a scratch git consumer under a
simulated CI-runner constraint (`scripts/om-ci.py self-test ci-tier0`, 31 checks; 34-check
`scripts/selftest-om-ci.py`), never against the real hosting service. The push step's own `run:`
skips only on a detached HEAD; a failed push on an attached head fails the job. The detached
case (ship fix round 2, B1): `actions/checkout@v4`'s default ref state for a `pull_request`
event is a detached merge ref, unlike this check's own local-branch self-test, and a naive
`git push origin HEAD` there failed the whole job regardless of whether anything was actually
stale; the self-test checks out a detached SHA of a stale mutant and requires the job to still
exit 0 with the push step printing a skip notice. The attached case (ship fix round 3, B1): the
round-2 guard was an `A && push || echo` chain, which routed a FAILED push on an attached head
into the skip notice -- a stale catalogue whose regenerate commit never landed read green on
the exact push-event shape the step exists for -- so the guard is now an `if/then/else`, and
the self-test points `origin` at a path that does not exist on an attached stale branch and
requires the compile-check job to fail with the push step as the failing step (and, on the
reachable-origin run, that the bot commit actually landed on origin). Two things the lab keep
disclosed as ship-time acceptance checks remain not yet run: whether the hosting service
actually accepts one `paths:` list with a `!` exclusion exactly as rendered; and what
`github.actor` reads as hosted for a `GITHUB_TOKEN` push on a normal branch push (never a
pull_request, which always skips), whether that push re-triggers the workflow at all, and --
after round 3 -- that a push the hosting service rejects (branch protection, a read-only token)
goes red rather than green; and -- after round 4 -- that the hosted `fetch-depth: 0` checkout
lets the compile-check job read a seeded stale tree as stale and regenerate it once. `SHIP.md`
records the result once that check runs.
