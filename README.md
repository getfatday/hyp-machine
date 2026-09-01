# Hyp Machine

Prove a way of working before adopting it — a plugin (an add-on for Claude Code, the AI coding assistant) for hypothesis-driven development.

## What it does

You write an idea as a hypothesis with pass-or-fail checks declared up front. The plugin runs the experiment and records the outcome.

- keep (passed every declared check) — adopted as standard practice.
- discard (failed) — dropped, with the failure on record.
- refine (partial pass) — revise and run again.

Outcomes land in a shared corpus (a base of proven findings) people and AI agents build on.

## Why it exists

AI coding help lives and dies inside one chat session. Shared instruction files run on hunches nobody tested. This plugin gives a team durable, tested memory: every practice carries its proof.

## The ethos

- Evidence over vibes: measurements beat opinions.
- Discarded ideas stay recorded; each mistake is made once.
- Reversible changes proceed; irreversible ones wait for a human.

## How to think about hypothesis-driven development

A practice from lean product teams: treat each change as a bet you can prove wrong. State the bet before building: we believe X; we will know when Y. Then run the smallest test that could settle it. It follows the double diamond (a design method: diverge to discover, converge to deliver); work is not done until measured.

evidence: Barry O'Reilly, "How to Implement Hypothesis-Driven Development" (ThoughtWorks) · UK Design Council, the Double Diamond (2005) · this repository's experiment journal and corpus of kept and discarded hypotheses

## Install

```
claude plugin marketplace add getfatday/hyp-machine
claude plugin install hyp@hyp-machine
```

Then, in your repository: `/hyp:init` (add `--profile experiments` or `--profile modeling`
for the fuller loops). Capture triggers on "note this".

## Lineage

Hyp Machine 0.1.0 is the crux plugin's 0.4.0 feature state under a new name (renamed
2026-08-29 after a blind naming study; the evidence lives in the source lab's decision
ledger). If you installed `crux` from the getfatday-skills marketplace, it keeps working
and stays installable — an existing `.claude/crux.json` config and `CRUX_GH_ACCOUNT`
setting are still honored, and `/hyp:init` seeds `.claude/hyp.json` from
`.claude/crux.json` (profile and path overrides; the crux file is left in place). The
guided migration is `scripts/migrate-from-crux.sh`: it runs the marketplace-add /
install-hyp / uninstall-crux steps tolerantly (every step's exit code is recorded, none
is fatal mid-way) and accepts on the end-state artifacts alone — hyp enabled at project
scope, crux absent from project settings, `.claude/hyp.json` present — so a step that
finds its work already done never fails a correct migration, which a bare `&&`-chained
one-liner does. Migrating never deletes `.claude/crux.json` and leaves the crux
marketplace entry known, so rollback stays a two-way door.

**Scope.** Hyp Machine is a way-of-working discipline — append-only capture, evidence-grade
experiments, an explicit process model — not a typed CRUD document lifecycle, a task
tracker, or a CI system. If you need managed document types with create/update/delete
workflows, use a document-management plugin instead.

## Provenance

The capture method is adapted from — not identical to — the ingest flow in Andrej
Karpathy's LLM Wiki pattern, which contributes the linked-wiki, index, and append-only log
ideas but is explicitly abstract and optional. The experiment loop is adapted from — not
identical to — Karpathy's autoresearch (modify, train five minutes, check improvement, keep
or discard, repeat); the spec/assertion/budget/verdict machinery is this plugin's design.
The working-principle phrasing echoed in the capture rule comes via the community
karpathy-skills write-up, authored by Forrest Chang from Karpathy's observations, not by
Karpathy. The modeling grammar synthesizes an event-storming-derived node schema with the
public Event Modeling canon (see `grammar/`).

## Quick start

1. Install the plugin from your marketplace (`claude plugin install hyp@hyp-machine`
   — the hosting marketplace's README carries the exact id), or load a checkout directly
   with `claude --plugin-dir <path-to>/hyp`.
2. In your repository, run `/hyp:init` (add `--profile experiments` or
   `--profile modeling` to activate more). It scaffolds the directories, installs a
   marker-delimited rules block into `CLAUDE.md`, and writes settings deny rules — then
   commit the scaffold.
3. Say "note this: ..." to capture; "test whether X actually works" to run the experiment
   loop; "model how we work here" to mine the operating model.

Upgrading from the retired predecessor plugins (the earlier standalone capture and
experiment-loop plugins): install hyp and re-run `/hyp:init` — it migrates the legacy
config files and CLAUDE.md rules blocks in place.

## Skills

| Skill | Profile | Purpose |
|---|---|---|
| `init` | — | Profile-gated scaffold (idempotent; repairs drift; migrates legacy installs) |
| `intake` | capture | The capture process: raw-first, classify, minimize, link + index, journal, commit |
| `hypothesis` | experiments | The experiment loop: spec, preflight, budgeted run, binary evaluation, mechanical verdict, journal |
| `adopt` | modeling | Mine the repo + recorded sessions into a SCHEMA-shaped operating model |
| `observe` | modeling | Trace a recorded session against the model: node-tagged, token-attributed, citation-validated |
| `evaluate` | modeling | Audit the model for defects with evidence pointers and zero fabrications |
| `compile` | modeling | Regenerate executable artifacts (workflows, runners, skills, rule blocks) deterministically from model nodes |
| `run` | modeling | Execute a compiled flow to a mechanical verdict (adopt-first refusal routing) |
| `verify` | modeling | Controlled A/B experiments over way-of-working interventions |
| `durability-check` | — | Walk the work-graph re-hydration protocol after any context loss: verify from the graph observe-only, assert back in writing, dispatch by the recomputed frontier, end only at an empty frontier or a FAILURE record — counted H-231 (see `docs/workgraph.md`) |

## What ships

| Component | Purpose |
|---|---|
| `skills/` | The ten skills above |
| `hooks/hooks.json` + `hooks/scripts/` | Deterministic guards (see table below) |
| `scripts/compile-journal.py` | Renders the compiled journal view from write-once fragments (copied into your repo by init) |
| `scripts/compile-dashboard.py` | Compiles a DASHBOARD.md status projection from your repo's own ledger and journal fragments (every source is optional) — v3 renders DECISIONS WAITING first as AskUserQuestion-grammar cards, normalizes three ledger row shapes, and regenerates `decisions.html` from the template at every compile (see `docs/decisions.md`) |
| `scripts/decisions.py` + `scripts/decisions-template.html` + `scripts/proactive-open.sh` | The decision kit: one ledger-backed decision store (append-only rows; status derived by join; decided-by/at/commit derive from git, never stored), the decision-surface template, and the once-per-new-id proactive opener — see `docs/decisions.md` |
| `scripts/closes_when.py` | The shared closes-when predicate evaluator (path-exists, commit-grep, hypothesis-kept, maintainer-ruling, decision-resolved) — one evaluator for the dashboard and the session resolver, so two readers of the same ledger can never disagree |
| `docs/communication-contract.md` + `scripts/house-vocabulary.json` + `scripts/clarity-lint.py` | The clarity canon: the decision-card/session-report anatomy with ceilings (measured in the source lab: naive-reader comprehension 78.6%→100% at −35% reader effort), the L3 gloss list, and the L1-L11 mechanical lint |
| `scripts/stall-signals.py` | Read-side stall detector: the S1-S5 signal strip (quiet experiments, idle claims, orphaned siblings, frozen close-conditions, journal-less runs) with gate-aware exemptions and tracked-file snooze — counted H-154 (see `docs/observatory.md`) |
| `scripts/flow-metrics.py` | Typed waste detector: machine-joinable `FLOW <CLASS> lane=...` lines over five classes (idle-runnable, stale-gate, unruled-terminal, void-cluster, WIP-breach) — counted H-192; joins `waste-status.py`, the prose report |
| `scripts/identity-resolve.py` | Per-user attribution + the YOURS/OTHERS lens: mailmap-canonicalized registering-commit owners, agent-assist disclosure from Co-authored-by trailers, render-time acting-as, offline initials avatars — counted H-156 |
| `scripts/derive-metrics.py` | Deterministic metric derivation into an append-only time series with `--trend` direction verdicts against each metric node's declared direction-of-good — counted H-129 |
| `scripts/emit_workflow_fact.py` + `scripts/harvest_gwt.py` + `scripts/facts_lib.py` | The workflow-facts loop: one validated fact record per workflow close (idempotent, append-only) and the gate→GWT harvester emitting candidate `gwt-case/v1` records onto their owning slice — counted H-118 |
| `scripts/render-case-study.py` + `scripts/fact_fidelity.py` + `scripts/content_lint.py` + `scripts/jargon.json` | The per-keep case-study renderer with its frozen fact grammar and content lint: every number extracted from artifact bytes, every quote byte-verified, fail-closed self-checks — counted H-201 |
| `scripts/init-scaffold.py` | The deterministic profile-gated scaffold init runs |
| `scripts/preflight.py` | The deterministic spec preflight (copied into your repo by init at the experiments profile) |
| `scripts/compile-model-workflow.py` | The model-to-executable compiler: one flow in, a dynamic-workflow target + a portable runner + a shared GWT assertion manifest out |
| `scripts/model-to-board.py`, `scripts/render_flow.py`, `scripts/flow_composer.py` | The diagram lane: model nodes to a board serialization to rendered flow views |
| `scripts/em-slice-lint.py` | Mechanical conformance lint over the board serialization (rules EM-L1..EM-L10) |
| `scripts/lexicon-lint.py` | Glossary conformance lint over model text (advisory) |
| `scripts/currency-lint.py` | Stale-value lint: current-state claims in node bodies verified against the cited artifacts (ADVISORY, non-certifying) |
| `scripts/doctor-classify.py` | Environment-health doctor, detection half: types the OAuth credential surface (CLEAN / FLAP-DEGRADED / HARD-EXPIRED / INDETERMINATE) from recorded probe streams or a live read-only snapshot — see `docs/doctor.md` |
| `scripts/doctor-remediate.py` | Environment-health doctor, remediation half: the frozen four-state ladder — exactly one next step per typed state, fail-closed heal verification |
| `scripts/review-cadence.py` | The verdict-forcing review cadence: ranked REVIEW DEBT surface + append-only verdict appender (multi-evidence) — see `docs/review-cadence.md` |
| `scripts/waste-status.py` | Advisory flow instrument: idle-runnable, terminal-pickup, and void-meter waste metrics from committed timestamps alone (exit 0 always, never a gate) |
| `scripts/dispatch-status.py` | The ranked next-item dispatch surface: open items from COMMITTED spec statuses only (no promise string closes an item), claim-joined (fresh-heartbeat filter) and orphan-joined (dead-pid recovery verbs) — counted H-213/H-215/H-217; the Stop dispatcher hook joins to it (see `docs/workgraph.md`) |
| `scripts/lane-takeover.py` | The claim door: TTL-gated takeover of another executor's lane — typed exit-3 refusal while the heartbeat is fresh, attributed record committed BEFORE any lane write on grant — counted H-216 |
| `scripts/dispatch-gate.py` | The shared relaunch governor: permit/deny consults + K-strikes quarantine (K=2) behind one committed decision card only a human ruling closes — counted H-218 |
| `scripts/hyp-resume.sh` + `scripts/install-resume-timer.sh` + `scripts/resume-prompt.md` | The reboot-surviving scheduled resume: emitted (never auto-loaded) launchd plist, one dispatch read + at most one capped adoption per firing — counted H-217 |
| `scripts/graph-check.py` | The work-graph checker: report-only (exit 0 always) lint + derived dispatch state over `ledger/graphs/*.md` — recomputed frontier, false-dones against the disk, stale downstream re-run candidates, dangling refs/cycles, stale `next-dispatch` pointers, expired claims — counted H-231; step 0 of `/hyp:durability-check` (see `docs/workgraph.md`) |
| `scripts/compile-findings-index.py` | The corpus layer, index half: one plain-language line per resolved hypothesis (id, verdict, date, finding, evidence pointer) plus lineage edges — counted H-226/H-228 |
| `scripts/prior-art-sweep.py` | The corpus layer, consult half: typed OVERLAP/LINEAGE flags plus a ready-to-paste Prior-work section for any draft spec, so registration mechanically consults everything already proven or disproven — counted H-227/H-228 |
| `scripts/parity-check.py` | Byte-parity checker between an installed hyp copy and a published manifest or pinned reference tree (see “Install parity” below) |
| `scripts/migrate-from-crux.sh` | The guided crux-to-hyp migration: runs the marketplace-add / install-hyp / uninstall-crux steps tolerantly (every step rc recorded, never fatal), then exits 0 iff the end-state artifacts verify — hyp enabled at project scope, crux absent from project settings, `.claude/hyp.json` present (seeded from `.claude/crux.json` when init has not run) — one plain verdict line per check (see “Lineage” above) |
| `scripts/issueops-fetch.py`, `scripts/issueops-reply.py`, `scripts/issueops-teardown.py`, `scripts/issueops_gh.py` | The audited GitHub-issues intake: CRLF-normalizing transport adapter, deterministic reply templater, manifest-scoped teardown, and the account-pinned audited gh helper they share — outward writes confined to a frozen allowlist (see `docs/issueops.md`) |
| `scripts/preflight-rigor.py` | The ethics extension to preflight, REPORT-ONLY: six calibrated rows over each spec's `## Ethical assumptions` section; the enforcement flip is maintainer-gated (see `docs/preflight-rigor.md`) |
| `scripts/directive-lint.py` + `scripts/directive_emitter.py` | The directive-intake closure join: level-triggered lint over `directives/D-*.md` (five finding classes) + the On-close commitment emitter into the work ledger (see `docs/directive-intake.md`) |
| `templates/DIRECTIVE-TEMPLATE.md` | The D-doc shape: acceptance assertions declared before any mutation, verification record, machine-readable On-close block |
| `templates/channel/` | The consent-screen and templated-reply files of the channel consent discipline — inert until the channel-deploy ruling (see `docs/channel-consent.md`) |
| `docs/ci-scaffold.md` | The CI scaffold story: three-artifact keep-regression net, self-test == CI, least-privilege `permissions: contents: read` on every emitted workflow (normative reference; the scaffold script ships when promoted) |
| `docs/ask-triage.md` | The ask-triage finding: safety-critical hook discipline ships as reference implementations, not arm-built artifacts — no gate ships pending the maintainer ruling |
| `kernel/operating-model/SCHEMA.md` | The node grammar (+ `SCHEMA-DELTA.md`, this copy's deltas) |
| `kernel/harness/` | The frozen extraction protocol, grading rubric, and trace-citation validator |
| `grammar/` | The Event Modeling layer: metamodel, slice-board layout + lint rules, schema-to-EM mapping |
| `docs/workflow-format-reference.md` | The dynamic-workflow emission format the compiler targets |
| `templates/` | Canonical copies of everything init writes (drift is checked against these) |
| `evals/` | One eval suite per skill, `claude plugin eval` format (neutral fixtures) |

## The three layers

A plugin cannot inject project memory or permission rules into a consumer repository, so
every mechanism ships three ways:

1. **Hooks** — deterministic guards, active while the plugin is enabled.
2. **Durable repository artifacts** — written into your repo by `init` and drift-checked at
   session start: the `CLAUDE.md` rules block, `.claude/settings.json` deny rules,
   `GOVERNANCE.md`, and the installed scripts. These survive a plugin disable.
3. **Skill prose** — the procedures themselves.

## Hooks

| Event | Behavior |
|---|---|
| PreToolUse (`Edit\|Write\|NotebookEdit\|Bash`) | Write-once guard: Edit/NotebookEdit under the raw directory is always denied; Write there is denied only when the target already exists (creation stays legal, including shell heredoc creation). Journal fragments get the same shape; the base journal file is fully frozen. The Bash branch is a mistake-net denying plain destructive commands aimed at an existing raw file or fragment. |
| PreToolUse (same matcher) | Generic policy interpreter: reads `operating-model/*/policies/*.md` as data. `enforcement: hook` nodes with a `mechanism:` block deny; `enforcement: advisory` nodes print one advisory line and never affect the exit code. No model, no effect. |
| PreToolUse (`Bash`, run-shaped) | Preflight gate (experiments profile): headless agent invocations tied to an experiment are denied when the spec is missing or fails the shipped preflight. |
| PreToolUse (`Bash`, `git commit`) | Advisory backstop (experiments profile): a tinker-shaped commit with no hypothesis spec staged prints a one-line nudge. Never blocks. |
| UserPromptSubmit | Capture-intent nudge on phrases like "note this" / "save that". Precision-first; silent otherwise. |
| SessionStart | Standing rules pointer + drift check against the plugin's canonical templates; uncommitted-capture warning; stale-dashboard check and refresh; ledger resolver (`hooks/scripts/session_resolver.py`): open decisions surface first (`DECISION-LEDGER` lines + summary), then unresolved intent/amendment/commitment/directive rows, capped at 20 lines. |
| Stop | Verdict-gated dispatch (experiments profile): non-empty dispatch with cap headroom re-presents the TOP open item by name — ending a cycle is permitted only by the committed artifact check, never a promise string; frozen caps (12 cycles / 1800 s per lineage) then it stands down; `touch .claude/stop-snooze` silences it 24 h (see `docs/workgraph.md`). Unjournaled-work backstop (blocks once with instructions when new knowledge files have no journal fragment); dashboard refresh. |

All hook scripts are stdlib-only Python, fail open on any error, and use consumer-generic
paths.

## Configuration

`init` writes `.claude/hyp.json` at your repo root; hooks, skills, and the compile scripts
read it. All paths are repo-relative.

| Key | Default |
|---|---|
| `profile` | `capture` (`experiments` and `modeling` add layers) |
| `raw_dir` | `research/raw` |
| `notes_dir` | `research/notes` |
| `index_file` | `research/index.md` |
| `journal_dir` | `experiments/journal-fragments` |
| `journal_file` | `experiments/journal.md` (optional base; only used if your repo already has one) |
| `compiled_file` | `experiments/journal-compiled.md` |
| `hypotheses_dir` | `hypotheses` |
| `runs_dir` | `experiments/runs` |
| `ledger_file` | `ledger/ledger.jsonl` (the work ledger the dashboard, decision kit, and session resolver share; the optional `DECIDERS` routing file lives beside it) |
| `template_file` | `hypotheses/TEMPLATE.md` |
| `preflight_file` | `experiments/preflight.py` |
| `model_dir` | `operating-model` |
| `context` | the repository directory name (slugified) |

## Install parity

Drift between an installed hyp copy and its published referent is measured, never assumed.
`scripts/parity-check.py --install <dir> (--manifest <published-manifest> | --reference <tree>)`
prints one finding per diverged/missing/extra file with its shipped file class and exits
nonzero; silent exit 0 is parity. Repair direction matters: a drifted INSTALL is restored
from the published referent; when the SOURCE moved ahead, the repair is a new versioned
publish — never an in-place overwrite of a published version. The checker is the counted
instrument of the parity law (hypothesis H-181 in the source lab, kept 2026-08-26: a clean
install grades zero findings, every seeded divergence is detected with path and class, and
the sync procedure restores byte parity without touching counted history).

## Journal convention

One write-once fragment file per entry — `<journal_dir>/<id>-<slug>.md` with frontmatter
`id:` (monotonic integer at land time) and `date:`; no author names in the text, because
git blame on the fragment file is attribution. `scripts/compile-journal.py` renders the
compiled view deterministically, prepending a pre-existing base journal file verbatim if
your repo has one. The compiled file is a build artifact: regenerate it, never edit it.

## Identity and attribution (multi-user)

The unique id of every capture is the pair (capture-commit sha, git author email). Two
optional committed maps make identities legible: a git-native `.mailmap` and a
`contributors.json` at the repo root keyed by canonical email. Display names live only in
that map; raw bodies stay verbatim; everything you author refers to people by canonical
email or not at all. The dashboard resolves the current identity at render time into an
`acting as` line; ledger decision rows may carry one optional `assignee` key. Both maps are
optional: the plugin renders fully without them.

## Known limitations

- The capture-intent nudge covers interactive sessions; it is not verified to fire under
  headless `-p` runs. Enforcement does not depend on it.
- The budget halt is procedural (skill prose plus the spec's budget line), not a timer.
- The preflight gate resolves specs from the command text; a run launched through an
  indirection it cannot see is gated only by the skill discipline.
- Source-mining adapters beyond the proven slice (repo tree, git history, session JSONL)
  are a documented seam (`templates/sources.yaml`), not shipped code.
- The compiler refuses to price flows containing steps with no measured cost — fill the
  consumer cost table from your own run ledgers first (`templates/cost-table.json`).

## Evals

```
claude plugin eval . --scaffold
```

`--scaffold` is required: each case's `scaffold_script` git-inits a neutral fixture repo.
One suite per skill under `evals/<skill>/<case>/case.yaml`; see `evals/README.md`.

## Changelog

- 0.2.0 (hyp — the first release under the new name; entries below this one are the
  crux-era versions that 0.1.0 consolidated) — work-graph dispatch and session
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
- 0.4.0 — decide less, see more: the decision kit and the observatory. The decision kit
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
- 0.3.0 — external input, safely: the audited GitHub-issues intake
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
- 0.2.0 — environment health and review flow: the doctor pair
  (`scripts/doctor-classify.py` + `scripts/doctor-remediate.py`, counted H-182/H-183 in the
  source lab — see `docs/doctor.md`), the verdict-forcing review cadence
  (`scripts/review-cadence.py`, counted H-188 — see `docs/review-cadence.md`), the install
  parity checker (`scripts/parity-check.py`, counted H-181), and the advisory waste/flow
  instrument (`scripts/waste-status.py`, uncounted-but-measured; proving specs H-192..H-196
  registered in the source lab). All counted scripts ship byte-preserving from their counted
  fixture copies; only provenance framing and consumer-repo path resolution differ.
- 0.1.0 — first consolidated release: the capture and experiment-loop capabilities of the
  retired predecessor plugins (capture 0.1.4, experiment loop 0.1.0) fold into one
  profile-gated install, joined by the operating-model lifecycle (adopt / observe / evaluate / compile /
  run / verify, the node grammar + Event Modeling layer, the policy interpreter, the
  diagram lane, and the model-to-executable compiler). `/hyp:init` migrates repositories
  initialized by the retired plugins.
