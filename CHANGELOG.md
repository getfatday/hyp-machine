# Changelog

Newest first. This file is written by `scripts/release.py` from the pending
`.changeset/*.md` files on every push to main; do not edit it by hand (see
`.changeset/README.md`).

## 0.4.0 (2026-09-05)

### Added

- CI now owns the version and the changelog. A pull request adds a `.changeset/<slug>.md` file (a `bump:` line and a paragraph like this one) and never edits the version in `.claude-plugin/plugin.json` or `CHANGELOG.md`; the required `changeset-check` status fails PRs that break the rule. On every merge to main the release job computes the next version from the highest reachable `v*` tag, writes it into plugin.json, prepends a CHANGELOG.md section, deletes the consumed changesets, tags `v<version>`, and publishes the GitHub release. The README changelog moved to CHANGELOG.md. Contract: `.changeset/README.md`; selftest: `python3 scripts/selftest-release.py`. Lab evidence: H-DRAFT-bfb3323b (cause-n-effect), research/release-automation-prior-art.md. (ci-owned-release-flow.md)

## 0.3.3 — fail-closed dispatch, draft-then-allocate ids (issues #8, #4)
- **Fail-closed dispatch read** (lab H-280, kept twice at 5/5 with write-ahead proof; fixes #8): the
  Stop driver no longer grades a failed dispatch read (timeout, nonzero exit, unparseable output) as
  an allow — the open-work state is unknown, not a pass. The first consecutive failure blocks the
  stop once (exit 2) with a visible retry reason; a second consecutive failure allows under the
  typed reason `dispatch-error-open` instead of `hook-error`, so a persistently failing read costs
  at most one extra cycle and can never trap a session. A durable `dispatch-read-start` line lands
  in `.claude/stop-driver/hook-log.jsonl` before every read, so a hook killed by the outer Stop
  budget still leaves a trace; error records carry `elapsed_s` / `error_class` / `fail_streak`.
  Healthy-path decisions stay byte-identical to the previous release.
- **Draft-then-allocate ids and the land-time id gate** (lab H-293, kept 5/5 — 16/16 concurrent
  registrations landed where the stock tree collided in 4 of 4 cohorts; lab H-295, kept 5/5 — the
  contract survives a cold-session resume in an offline consumer repository; fixes the id half
  of #4 — the run-directory half stays open): registering on the default branch and landing
  immediately still takes `H-NNN`; on any other branch the spec is
  `hypotheses/H-DRAFT-<hash8>-<slug>.md` and the handle stands in wherever the id would appear,
  with fragment ids under the same contract. The new `scripts/id-rectify.py` gate allocates
  canonical ids at land — collision renumber, draft allocation and dedupe, fragment-id allocation,
  tolerant of a fragment without an integer id, a hash-less handle, and a handle inside any
  filename — and `--lint` names each finding class on the unrepaired branch.
  `skills/hypothesis`, `skills/intake`, and both templates carry the contract. Nothing historical
  is renamed; downgrading restores the next-free rule with zero corpus damage.
- **Async dashboard recompile** (lab H-291, kept 5/5): the Stop-event `compile-dashboard.py
  --quiet` entry is consumer-less — nothing reads its stdout — and now carries `"async": true`,
  taking its wall off the stop hot path (455 ms -> ~1.4 ms added p50) with dashboard freshness
  preserved 10/10 and zero error rows. One hooks.json flag; the script's bytes and the
  SessionStart entries are untouched.

- **Consumer contract and regression test**: `docs/id-allocation.md` (the rule, the lander's
  commands, exit codes, tolerances, upgrade and rollback) and
  `python3 scripts/selftest-id-allocation.py` (47 checks, including the tolerances for a
  fragment without an integer id, a hash-less handle, and a handle inside a filename).

## 0.3.2 — worktree-aware hook root (issue #6)
- **One resolver, the session's real tree** (lab H-DRAFT-e90628b6, counted 2x 5/5 zero-LLM): in a
  worktree-isolated session `CLAUDE_PROJECT_DIR` keeps naming the launching checkout while the
  hook payload's `cwd` lives in the worktree, so every hook graded the wrong tree — the Stop
  driver let a consumer session end with five specs open on its branch. `hyp_config.resolve_root`
  now returns the worktree's toplevel when the payload cwd sits in a **linked worktree of the same
  repository** (decided from the `.git` pointer file and its `commondir`, the two files git reads;
  no subprocess, ~1 ms), and keeps the 0.3.1 order byte-for-byte everywhere else (main checkout,
  subdirectories, foreign repositories, submodules, non-git cwds, unset variable).
- The policy interpreter imports that resolver instead of a private copy; `commit-backstop.py`
  and `session_resolver.py` route their argv root through the same check; `rel_to_root` tolerates
  symlinked prefixes (`/var` vs `/private/var`). Hardened after adversarial review of the counted
  patch: pointer and `commondir` reads accept regular files only, bounded to 4 KB (a planted FIFO
  no longer hangs the hook), and a cwd reached through a symlink into a worktree's interior
  resolves to that worktree.
- **Regression test shipped**: `python3 scripts/selftest-worktree-root.py` builds its own fixture
  under a temp dir and checks the installed plugin (13 checks, exit 0 on PASS) — run it after any
  change to the hooks.

## 0.3.1 — consumer-hardening patch (issues #3, #2)
- **Fixture-freshness gate** (H-262, kept 5/5): preflight now re-hashes every `Fixture-SHA256:` pin
  and fails closed on drift, printing the blast radius (every co-pinning spec). Pin-less specs get
  one advisory line; pins become first-class in a later corpus migration.
- **Claim-type gate** (H-258, kept 5/5): specs declare `Claim type: descriptive | normative`; a
  descriptive spec carrying decision-class On-keep rows fails with the bridging route (register a
  normative successor). Missing line = advisory only — the existing corpus predates the field.
- Both checks ride `scripts/preflight.py` + the template; zero flips on a 264-spec live corpus.

## 0.3.0 (hyp)

The flow-governance layer, plus the ratified work-graph part 2. The
flow-leak meter (`scripts/leak-meter.py` + sealed constants + `scripts/leak-status.sh`
— H-246 kept 2x5/5 2026-09-02: alarmed 227.8/1259.9/936.3 minutes before the recorded
human catch on three held-out episodes, 0 false alarms on 47 healthy ticks; harden
ADVISORY-30). The reflex chain, breach → autopsy → decision → consumption
(`scripts/reflex-check` H-251 kept 2x5/5, `reflex-collect` H-252 kept 2x5/5,
`reflex-surface` H-253 kept 2x4/4, `reflex-selftest` H-254 kept 2x4/4 — all
2026-09-02 — plus `reflex-consume.py` + ADVISORY-31, the lab's throughput-floor
consumption patch: an alarm unconsumed for 30 minutes surfaces until an action citing
it is recorded; timer plist emitted-never-loaded). The rules registry
(`scripts/compile-laws.py` H-247 kept 2x5/5, `scripts/rule-lint.py` H-248 kept 2x5/5,
the `rule-retest` decision class in `scripts/decisions.py` H-249 kept 2x5/5, and the
license-join PreToolUse advisory `hooks/scripts/license-join-check.py` H-250 kept
2x5/5 — all 2026-09-02; see `docs/flow-governance.md` for the whole layer). Direction
hygiene (`scripts/direction-lint.py`, H-243 kept 2x5/5 2026-09-01) and move fidelity
(`scripts/fidelity-manifest.py`, H-104 port). Dispatch REFILL: the actionable
frontier masks PARKED/BLOCKED-*/COUNTING status markers, and when it drops below 2
the dispatch names the licensed follow-up lanes of your `followups_file` (grammar:
`docs/workgraph.md`) — uncounted, never reordering counted runs. The 0.2.0
model-compiled orchestration (`scripts/compile-model-workflow.py`) is now ratified by
its lab keep (H-177, kept 2x5/5 2026-09-02 — frontier-exact dispatch, halt-for-ruling
discipline, 100% lineage rows on the amended fixture). This release also formalizes
the migration patches already live on 0.2.0: end-state-verified
`migrate-from-crux.sh`, standalone-safe `hyp.json` seeding, and init preserving a
consumer's customized preflight.

## 0.2.0 (hyp)

The first release under the new name; entries below this one are the
crux-era versions that 0.1.0 consolidated. Work-graph dispatch and session
durability: ranked next-item dispatch at session boundaries, claim TTL takeover,
K-strikes quarantine, reboot-surviving resume, compression-surviving re-hydration
protocol (`docs/workgraph.md`). The dispatch surface (`scripts/dispatch-status.py` +
the Stop-boundary dispatcher hook — H-213 kept 2x5/5 2026-08-29; the named-top-item
block shape measured by H-230, kept 2x5/5 2026-08-30 with the <=8-tool-call naming
window): an item is open until its COMMITTED spec-status verdict lands — no promise
string ends a cycle. The claim layer (`scripts/lane-takeover.py` — H-216 kept 2x5/5
2026-08-29; the claim join, H-215 kept 2x5/5 2026-08-29): heartbeat-TTL liveness
(ttl_s = 1800) with typed refusal and record-before-write takeover. The relaunch
governor (`scripts/dispatch-gate.py` — H-218 kept 2x5/5 2026-08-29): K=2 consecutive
non-green terminals quarantines a lane behind one committed decision card. The
scheduled resume (`scripts/hyp-resume.sh` + `scripts/install-resume-timer.sh` +
`scripts/resume-prompt.md` — H-217 kept 2x5/5 2026-08-29): an emitted-never-loaded
RunAtLoad timer plist; each firing = one dispatch read + at most one capped
adoption. The re-hydration protocol (H-231 kept 2x5/5 2026-08-30, successor to
H-162's 3/3 discard) ships as the `docs/workgraph.md` procedure: verify from the
graph observe-only, assert back in writing, dispatch by the recomputed frontier,
end only at empty frontier or a FAILURE record. The corpus layer staged at 0.1.0
flips to shipped (`scripts/compile-findings-index.py` + `scripts/prior-art-sweep.py`
— H-226/H-227/H-228 all kept 2x5/5 2026-08-29). All ports are the source lab's live
installs with paths/config adaptation only (consumer repo-root and `.claude/hyp.json`
resolution; item enumeration from the consumer's own hypotheses corpus instead of
the lab's release-train wave plan; quarantine rows typed `needs-maintainer`).
Deliberately NOT shipped: the lab's release-train reader (lab infrastructure),
`graph-check.py` + the durability-check command and the SessionStart ranked
injection (follow-on tranche per H-231/H-230's On-keep routing — not yet landed in
the lab either), and the lab's machine-specific reference plist (the installer
emits per-repo).

## 0.4.0 (crux)

Decide less, see more: the decision kit and the observatory. The decision kit
(`scripts/decisions.py` + `scripts/decisions-template.html` +
`scripts/proactive-open.sh` + `scripts/closes_when.py` + the compile-dashboard v3
merge + the SessionStart resolver v5 — see `docs/decisions.md`): one append-only
decision store in your work ledger, AskUserQuestion-grammar cards rendered FIRST on
the dashboard and regenerated whole into `decisions.html`, resolution by one CLI line
that commits just the resolution row, and decided-by/at/commit derived from git —
never stored (the source lab's H-084 keep + name-neutrality law; the CLI's
`--selftest` proves the whole loop in a throwaway repo). The clarity canon
(`docs/communication-contract.md` + `scripts/house-vocabulary.json` +
`scripts/clarity-lint.py`, measured in the source lab: naive-reader comprehension
78.6%→100% at −35% reader effort; counted hardening specs H-207..H-211 registered
there). The observatory (six counted instruments, each 2x consecutive full-pass
counted runs in the source lab, keep dates 2026-08-28 — see `docs/observatory.md`):
stall signals (`scripts/stall-signals.py`, H-154), typed flow waste
(`scripts/flow-metrics.py`, H-192 — joins 0.2.0's `waste-status.py` as the counted
typed alarm surface), identity attribution + the YOURS/OTHERS lens
(`scripts/identity-resolve.py`, H-156), metric trend derivation
(`scripts/derive-metrics.py`, H-129), the workflow-facts loop
(`scripts/emit_workflow_fact.py` + `scripts/harvest_gwt.py` + `scripts/facts_lib.py`,
H-118), and the per-keep case-study renderer (`scripts/render-case-study.py` +
`scripts/fact_fidelity.py` + `scripts/content_lint.py` + `scripts/jargon.json`,
H-201). All counted scripts ship byte-preserving from their counted artifacts; only
provenance framing, script names, and consumer-repo path resolution (`.claude/hyp.json`
`ledger_file`, plugin-home fallbacks) differ, and each file's header names its exact
divergences. Deliberately NOT shipped: the source lab's board renderer (H-036
discarded on three counted content-quality failures; successor H-212 registered and
pending) and the H-200 ask-triage reference gates (the ship ask was converted to
experiment H-206, registered; they ship on its keep — `docs/ask-triage.md`).

## 0.3.0 (crux)

External input, safely: the audited GitHub-issues intake
(`scripts/issueops-fetch.py` / `issueops-reply.py` / `issueops-teardown.py` /
`issueops_gh.py`, counted H-136 in the source lab on LIVE GitHub — 2x5/5 with outward
writes confined to the frozen allowlist; the reply templater counted twice, H-106 +
H-136 — see `docs/issueops.md`); the directive-intake kit
(`templates/DIRECTIVE-TEMPLATE.md` + `scripts/directive-lint.py` +
`scripts/directive_emitter.py`, counted H-199 — declared-before git-order,
named-divergence verification — see `docs/directive-intake.md`); the report-only ethics
extension to preflight (`scripts/preflight-rigor.py`, counted H-132; the enforcement
flip stays maintainer-gated — see `docs/preflight-rigor.md`); the channel consent
discipline (`templates/channel/` + `docs/channel-consent.md`, counted H-125 sandboxed;
live wiring stays gated on the maintainer channel-deploy ruling); the CI scaffold
normative reference (`docs/ci-scaffold.md`, counted H-198 — the excerpt-complete
refine-to-keep); and the ask-triage finding (`docs/ask-triage.md`, H-200 discarded —
the reference gates ship only on a maintainer ruling, deliberately NOT ported). All
counted scripts ship as counted from their fixture copies; only provenance framing,
script names, and consumer-repo path/account resolution differ (offline byte-parity
verified at port time).

## 0.2.0 (crux)

Environment health and review flow: the doctor pair
(`scripts/doctor-classify.py` + `scripts/doctor-remediate.py`, counted H-182/H-183 in the
source lab — see `docs/doctor.md`), the verdict-forcing review cadence
(`scripts/review-cadence.py`, counted H-188 — see `docs/review-cadence.md`), the install
parity checker (`scripts/parity-check.py`, counted H-181), and the advisory waste/flow
instrument (`scripts/waste-status.py`, uncounted-but-measured; proving specs H-192..H-196
registered in the source lab). All counted scripts ship byte-preserving from their counted
fixture copies; only provenance framing and consumer-repo path resolution differ.

## 0.1.0 (crux)

First consolidated release: the capture and experiment-loop capabilities of the
retired predecessor plugins (capture 0.1.4, experiment loop 0.1.0) fold into one
profile-gated install, joined by the operating-model lifecycle (adopt / observe / evaluate / compile /
run / verify, the node grammar + Event Modeling layer, the policy interpreter, the
diagram lane, and the model-to-executable compiler). `/hyp:init` migrates repositories
initialized by the retired plugins.
