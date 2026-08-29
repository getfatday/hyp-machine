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
