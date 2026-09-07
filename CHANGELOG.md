# Changelog

Newest first. This file is written by `scripts/release.py` from the pending
`.changeset/*.md` files on every push to main; do not edit it by hand (see
`.changeset/README.md`).

## 0.15.1 (2026-09-07)

### Fixed

- `evals/intake/save-external-finding` names its reserved `example.com` source as a deliberate placeholder to record verbatim and never fetch or verify. Without that sentence, the intake skill run against a repository carrying a citation registry (the source lab) ended the turn at turn 4 asking whether the URL was real and wrote nothing in four of the lab's graded capture sessions across both arms (lab H-DRAFT-6ec1691d run-5/run-6, H-DRAFT-70769657 run-1/run-3, fragment 0411); in the case's own bare scaffold the decline never fires, so the graders and expected outcome are unchanged. (intake-case-placeholder-source.md)

## 0.15.0 (2026-09-07)

### Added

- `scripts/id-collision-lint.py` reports hypothesis ids shared by more than one spec file and journal-fragment ids used more than once, and harden-check surfaces the counts as ADVISORY-35 `id-collisions: spec=N fragment=M`. On a 256-spec consumer that mints ids with its own next-free counter it reads spec=57 fragment=131; on a repository that lands ids through the plugin's draft-then-allocate gate it stays silent. Lab H-DRAFT-6ec1691d-consumer-id-collision-advisory, kept 5/5 in two consecutive runs (the second cold); consumer gap G4. (H-DRAFT-6ec1691d-consumer-id-collision-advisory.md)

## 0.14.3 (2026-09-07)

### Fixed

- harden-check advisory 20 no longer renders a consumer's dashboard: it called `compile-dashboard.py --triage-check` whenever `experiments/runs/DESIGN-decision-triage/triage.json` existed, and the shipped compile-dashboard has no such branch, so the call rendered DASHBOARD.md and decisions.html into the consumer's tree from a read-only advisory hook. The block is guarded on the branch being supported; every other line is byte-identical on the consumer and in the source lab. Lab H-DRAFT-45585281-harden-advisory20-consumer-write, kept 5/5 in two consecutive runs; consumer gap G13. (H-DRAFT-45585281-harden-advisory20-consumer-write.md)
- `scripts/dispatch-status.py` reads every committed spec body in `git cat-file --batch` chunks of 64 under one wall ceiling, `DISPATCH_STATUS_MAX` seconds (environment, default 20), instead of one `git show` subprocess per spec: a 180-303 id corpus that read in 38-151 s on a loaded host reads in about one second, so the Stop dispatcher's 45 s inner read finishes. A chunk that times out is discarded whole and its ids are disclosed as UNREAD (open with kind `unread`, never landed, never actionable) through a `partial` object in `--json` or one `DISPATCH-PARTIAL:` line in text; whenever the budget is not hit the output is byte-identical to before. Consumer gap G12 (lab H-DRAFT-45585281). (dispatch-status-time-budget.md)

## 0.14.2 (2026-09-07)

### Fixed

- The harden-check background refresh has a time budget: every scanner block runs under a per-block ceiling (`HARDEN_BLOCK_MAX`, default 120 s), no block starts after the total ceiling (`HARDEN_TOTAL_MAX`, default 600 s), a block killed at its ceiling has its output discarded rather than read as zero findings, and one `HARDEN-PARTIAL` line names every deferred block. On a 35k-file consumer the refresh fell from 380 to 485 s down to 126 s by deferring exactly the over-budget whole-tree name scan, with every other advisory line byte-identical and a raised-ceiling control equal to the unbounded run byte for byte; the source lab's refresh through its deploy-tree link is unchanged. Lab H-305 (harden-refresh-cost-bound), kept 5/5 in two consecutive runs after Amendment 1, the second by a cold executor; consumer gap G11 of the lab-plugin convergence program. (H-305-harden-refresh-budget.md)

## 0.14.1 (2026-09-07)

### Fixed

- Every plugin SessionStart hook now has a 60 s timeout (was 10 to 20 s). Under normal host load on a lab-sized repository the six hooks run concurrently and the 10 s ones were cancelled at their limit, which silently drops the hook's context line from the session (observed: compile-dashboard --check cancelled at 10.1 s, drift-check and the session resolver cancelled at 10 to 15 s when the host was busy). With 60 s no SessionStart hook was cancelled in any measured session while total SessionStart wall stayed under 45 s; nothing else in hooks.json changes. Lab H-DRAFT-d8609270-plugin-hook-timeout-margin, kept 5/5 in two consecutive runs (the second cold, whose OFF arm reproduced a cancellation at load 12); consumer gap G10 of the lab-plugin convergence program. (H-DRAFT-d8609270-plugin-hook-timeout-margin.md)

## 0.14.0 (2026-09-07)

### Added

- The north-star follow-through keeps (lab H-DRAFT-c1c1344b, fragment 0282, each kept 5/5 in two counted runs): `scripts/knob-observe.py` accepts `rule: band` alongside the kept ladder (integer `bounds: [min, max]`, `band`, `sense`, `step`, `hysteresis: one-change-per-window`, held `would_set` and a recorded `proposal` under every kill switch, resumable `--replay`; `--selftest` 23/23 with seeded out-of-bounds, double-change and kill-switch violations each exiting 1) and `docs/knobs.md` documents the band grammar; `scripts/stopping-rule.py` lands with `docs/stopping-rules.md` (`--freeze <rule> --into <copy>`, `--evaluate <rows> --looks k --rule <copy> [--json]`, `--selftest` 6/6; typed exits 12 `frozen-rule-missing`, 13 `frozen-rule-tampered`; verdict vocabulary evidence-sufficient / evidence-insufficient, never keep or discard); `scripts/compile-dashboard.py --check` exits 1 when the compile raises instead of reading a swallowed crash as fresh (the lab's keep-flip sort key has no anchor in the plugin's compiler yet and rides the next compiler sync). (H-DRAFT-c1c1344b-north-star-follow-through.md)

## 0.13.0 (2026-09-07)

### Added

- harden-check gains ADVISORY-34 branch-without-pr: when the session's HEAD is a non-default branch at least HARDEN_PR_MIN (default 1) commits ahead of origin's default branch and gh reports no open pull request for it, one advisory line names the branch, the ahead count and the `gh pr create --draft` command; silent on the default branch, detached HEAD, without gh, or with HARDEN_PR_CHECK=0; the gh call is pinned to the remote owner's account first and says so when pull requests cannot be read instead of hiding. Motivation: 52 pushed commits sat on a lab worktree branch for two days with no PR while sibling branches had drafts — "pushed" was green, review was blind (lab experiments/runs/DESIGN-durability-gaps/research/why-no-pr.md; H-DRAFT-ee81f74d-branch-without-pr-advisory). Ships with scripts/selftest-branch-without-pr.py. (branch-without-pr-advisory.md)
- `scripts/observatory.py` ships the live observatory board: a standard-library OTLP/HTTP receiver (`serve`: JSON and lazily-decoded protobuf on `/v1/logs|metrics|traces`, plus `/hook`) fed by Claude Code hooks through an async `/bin/sh` spool transport that `install-hooks --scope user` writes and `uninstall-hooks` reverts exactly, with end-of-session transcript backfill so a headless exit never loses the tail; every tool call is classified against the repository's operating model (one program token per call, never an argument; catalog paths resolved through `.claude/hyp.json` via `hyp_config`; classes modeled-deterministic / modeled-stochastic / delegated / unmodeled) and rolled into per-session leverage and determinism ratios that `ratios` recomputes byte-identically from the events JSONL; `board` and `web` (Textual via `uv run`) render sessions, traces, logs and metrics. `scripts/selftest-observatory.py` ports the 25 non-TUI regression checks plus four plugin checks (hyp.json overrides, data-directory defaults, stdlib-only import, no lab paths); `docs/observatory.md` gains the section describing the board, the four provenance classes and ratios, the data-directory contract, and the install commands. Ported byte-preserving from the source lab's kept fixture (sha256 4b643fb1…) with six documented divergences (provenance header; hyp_config path resolution; `CLAUDE_CONFIG_DIR` honoured; `python3` in the emitted post-transport command; `SELF_PATH` aliased to `THIS_FILE`; `--state-file`/`--events`/`--spool` default under `$HYP_OBSERVATORY_DIR` or `<config_dir>/observatory`). Four lab keeps, each 5/5 in two consecutive counted runs by cold executors: H-DRAFT-2c1fc974-hook-fed-observatory-board (2026-09-04), H-DRAFT-fcf7b3fa-op-provenance-classification and H-DRAFT-fcf7b3fa-model-leverage-read-model (2026-09-06), H-DRAFT-2652d478-async-spool-backfill (2026-09-06: +1.0 to +4.5 ms per tool call, completeness 20/20). Hooks are opt-in and deliberately not wired in `hooks/hooks.json` (an undrained spool grows without bound; user scope keeps machine-local paths out of committed settings). (observatory-board.md)

## 0.12.0 (2026-09-07)

### Added

- Decision durability: a rule or a decision parked on missing information can now say, in a committed field, what evidence reopens it, the plugin emits that evidence at its choke points, a consumer can send it, and the trigger fires from committed bytes, never from a date (see `docs/decision-durability.md`). Five kept lanes ship together. (1) Retest-when predicates: `scripts/closes_when.py` gains the sibling `retest-when` family — `event-count=<event>[:<subject prefix>]>=N`, `metric-crosses=<metric><op><threshold>@last=K`, `evidence-received=<target>` — evaluated at HEAD only, with `--selftest` now 19 + 24 checks; `scripts/retest-trigger.py` files exactly one deduplicated `rule-retest` decision row per armed registry rule the first time its predicate holds, pointers `<path>@<sha40>#La-Lb`, exit 0 always, `--dry-run`, and a `--selftest` that exits 1 if an uncommitted packet or a cross-then-revert series files a row; `scripts/compile-laws.py lint-registry` accepts `retest_when` in place of `retest_by`; `scripts/rule-lint.py` `RULE-EXPIRED` no longer fires on a date-less row that carries a well-formed predicate (`--selftest` added). Lab H-DRAFT-d564bb31-retest-when-predicates KEPT 5/5, 5/5: six seeded rules fired at exactly the key's boundaries, date-only and never-true rules filed nothing across nine boundaries, a staged-but-uncommitted packet filed nothing, a metric that crossed and reverted filed nothing, the real registry (no evidence triggers yet) fires zero rows. (2) Run-page events: `scripts/checkpoint-shadow.sh <run-dir> --out <page>` replaces the grade leg's advisory compile line byte-for-byte on stdout and exit status and appends one `event/checkpoint-compiled` row `{rc, class, lane, run}`; the payload contract and the class-versus-rc rule land in `scripts/events_lib.py`, the `checkpoint-compiled` verb in `scripts/emit-event.py`, the node in `templates/event-nodes/checkpoint-compiled.md` (the five sibling event-node templates the other verbs validate against ship beside it; the emitters were previously only in the lab's deploy tree). The grade-leg snippet is two lines: `export CHECKPOINT_EVENT_DATE=<pinned date>` then `EVENTS_IMPL="$CLAUDE_PLUGIN_ROOT/scripts" COMPILER="$CLAUDE_PLUGIN_ROOT/scripts/compile-run-checkpoint.py" "$CLAUDE_PLUGIN_ROOT/scripts/checkpoint-shadow.sh" "$RD" --out "$OUT"`. Lab H-DRAFT-d564bb31-checkpoint-compiled-events KEPT 5/5, 5/5: the refusal census of 28 real run directories (20 refused with code 13) plus six seeded corruptions reproduced from committed rows while the grade leg's own outputs stayed byte-identical. (3) The shadow gate knob: `scripts/knob-observe.py` (verbs `evaluate <knob> [--at SHA] [--replay] [--json]`, `check`, `--selftest`), `docs/knobs.md`, and the node templates `templates/knob-nodes/checkpoint-gate-stance.md` (`mode: shadow`, 30-observation window, ladder rule, per-class bounds, demote-on-first, three kill switches), `templates/policy-nodes/checkpoint-gate-license.md`, `templates/command-nodes/file-gate-decision.md`, `templates/event-nodes/checkpoint-gate-threshold-reached.md` — the first bounded controller in the plugin: it appends one state row per new signal state and files the advisory-to-deny decision only in recommend mode, only at n=30, only when the licensing node resolves. Lab H-DRAFT-d564bb31-checkpoint-gate-shadow-promotion KEPT 5/5, 5/5 (28 real rows labelled, nothing filed at 29, exactly one evidenced row at 30, idempotent at the same HEAD, three kill switches held, four seeded violations bit). (4) Decision rows with `retest_when`: `scripts/decisions.py add` validates the optional field through the shared parser, `check` prints the exit-neutral `RETEST-DUE` (with the evidence pointer) and `REVISIT-UNARMED` classes, `scripts/review-cadence.py` renders a `RETEST DUE` block above `REVIEW DEBT` (byte-identical render when nothing is due), `docs/decisions.md` gains the field's section. Lab H-DRAFT-d564bb31-decision-retest-when KEPT 5/5 (runs 2 and 3; run 1 lost one assertion to version-control latency under machine load, fixture unchanged). (5) Evidence packets: `scripts/hyp-evidence-export.py --target <target>` is consent-gated by `.claude/hyp.json` `evidence_export` (`off` | `packet` | `submit`; absent means off; never seeded on), projects the event stream through the lab's committed allow-list (`templates/exports/checkpoint-gate-stance.export-config.json`, the first target), pseudonymizes subjects, and refuses any packet the leak scan catches; `scripts/evidence-ingest.py <pointer.json>` lands a packet exactly once as a write-once raw capture whose arrival satisfies `evidence-received=<target>`. Lab H-DRAFT-d564bb31-evidence-packet-roundtrip KEPT 5/5, 5/5 (eight bait rows stripped or hashed in the packet and every one caught in the raw copy; a second identical ingest added nothing). Two ports, not byte copies: the plugin's `decisions.py` resolves the ledger path through `.claude/hyp.json` (the lab's `retest_when` delta was merged three-way onto it, one selftest constant renamed) and `knob-observe.py` reads the same `ledger_file` key so the evaluator and the decision kit agree on where decisions live; every other shipped script is byte-identical to its lab copy. Not in this release, deferred with their needles open: the `checkpoint-compiled` case of `selftest-events.py` and the resolver `KNOB` line (the H-239 event-stream resolver join is still unshipped), `runs-census.py --checkpoint-codes` (no such script in either tree), the `evidence_export` key in `init-scaffold.py`'s writer (absent already means off), the issueops `hyp-evidence` fenced-block parser, and the network dispatch pair (needs a maintainer-minted token). Every arm in every lane was a script; run 2 of each was a cold-context executor. Provenance: source lab cause-n-effect, branch worktree-wayfinding-patterns, journal fragments 0278 (registration) and 0280 (verdicts); stacked on PR #13 (branch north-star-set). (H-DRAFT-d564bb31-decision-durability.md)

## 0.11.0 (2026-09-07)

### Added

- Many north-star files in one repository. `scripts/north-star-check.py` reads every committed `ledger/north-stars/*.md` in one invocation and emits a `set` block (present with a single file too, as a one-destination reduction) (`--json` key `set`, text: a trailing `set:` block) reduced from the per-file blocks alone: `union_frontier` lists each distinct effective lane once with the `<slug>#C-NN` pairs it closes, ranked by destinations served, then minimal distance, then lane id (derived numbers only, never an authored order); `shared_bounds`; and `exit_strict_by_slug`, so one malformed sibling fails its own bit while the others still derive. A `needs` token may name a sibling file's condition, `<slug>#C-NN[:yes|no]`, inheriting its derived status, outcome, and retire cascade with zero edits to either file; unknown slugs are `DANGLING-REF`, cross-file cycles are `CYCLE`, and two committed paths under `ledger/north-stars/` sharing a basename are the new `DUPLICATE-SLUG` lint (`templates/north-star.md` and `templates/north-stars-README.md` carry the grammar, the Set rows and the Reading lines). The checker's git layer is batched (one `ls-tree -r` plus two `cat-file --batch` rounds per commit instead of one subprocess per resolver path), so the all-files read is hook-safe: 25 files by 15 conditions went from 364 git invocations and 69-78 s to 4 invocations and under 1.1 s with `--json`, text and `--strict` exit codes byte-identical. `scripts/compile-north-star-progress.py --all` compiles one `<slug>.progress.html` per north-star file plus `ledger/north-stars/index.html` (one row per destination, every value copied from the checker), and `--check --all` exits 1 naming each stale page; single-file mode is byte-unchanged. `scripts/closes_when.py` additionally carries the `frontmatter-status=<path>:<done-values>[!<no-values>]` predicate and the checker a `document` resolver kind (verb `sync`): a condition binds a committed typed document and derives from the `status:` line of its frontmatter at the commit, a no-value settles outcome `no` and retires `C-NN:yes` dependents while the row stays open, presence is decided by `git cat-file -e` (never by `git show`'s exit code, which is 0 with empty output for some deleted paths) and a bound path absent at the commit is a `DANGLING-REF` hard finding. Evidence: H-DRAFT-d6a0a6ef-document-resolver-milestones KEPT 5/5, 5/5 (journal fragment 0277; twelve bound milestone documents over ten Jira-style sync commits matched a blind reference evaluator at every non-seeded commit with zero north-star edits, a cancellation retired dependents and resume reopened them, a deleted file dangled exactly once, the status-scope lint fired exactly once on the seeded column). Selftests: checker 115 checks, progress compiler 23, closes_when 19. Evidence, from the source lab (cause-n-effect, branch worktree-wayfinding-patterns, journal fragments 0275 and 0276): H-DRAFT-d6a0a6ef-north-star-set-union-frontier KEPT 5/5, 5/5 (24-destination fixture, four planted shared lanes, eight seeded states matched a seed-authored key, byte-identical across invocations and a clone, per-file arrays unchanged, a lane serving three destinations outranked a nearer lane serving one); H-DRAFT-d6a0a6ef-north-star-set-cross-file-needs KEPT 5/5, 5/5 (198 of 198 per-file entries byte-identical for files without the token); H-DRAFT-d6a0a6ef-north-star-set-batched-reads KEPT 5/5, 5/5 (21/21 byte-identical outputs, 42/42 exit codes); H-DRAFT-d6a0a6ef-north-star-set-progress-index KEPT 5/5, 5/5 (24 index rows equal to the checker, 25/25 pages byte-identical across runs). Run 2 of every lane was a cold-context executor; no arm made an LLM call; every fixture was synthesized from the shape of a portfolio repository read only. Stacked on PR #10 (branch north-star-0.4.0), which ships the checker itself; every shipped script and template is byte-identical to its lab copy under `scripts/parity-check.py`. (H-DRAFT-d6a0a6ef-north-star-set.md)

## 0.10.0 (2026-09-07)

### Added

- The destination-map program lands as the north-star layer. A north-star file (`ledger/north-stars/<slug>.md`, template `templates/north-star.md`, convention `templates/north-stars-README.md`) states one destination, a `reached-when:` list, and a table of conditions each bound to one resolver (hypothesis, decision, capture, probe); the file stores no status, and `scripts/north-star-check.py` derives every condition's state at HEAD (done / open / retired / unbound), the frontier with verbs, and the distance, with `--strict` lint, `--at <sha>` replay, `--json`, and `--selftest` (67 checks). `scripts/compile-run-checkpoint.py` compiles one openable single-file `run-checkpoint.html` per counted run from results.json, grade.txt and the spec's frozen assertion span, copying byte slices and never computing a number, refusing six spec-versus-run corruptions with pinned exit codes 10-15, advisory in the grade leg. `scripts/compile-north-star-progress.py` renders the "where are we" page (`north-star-progress.html`: condition cards by derived status, needs edges, frontier, distance, drift chips, replay slider) with `--check`. `scripts/closes_when.py` gains the additive predicate `hypothesis-verdict=H-NNN` (kept OR discarded at HEAD) and a 12-check `--selftest`. `/hyp:init --profile experiments` scaffolds `ledger/north-stars/README.md` and nothing else; `skills/hypothesis` gains a "North star and checkpoints" subsection; `docs/north-star.md` is the orientation page. Evidence, from the source lab (cause-n-effect, branch worktree-wayfinding-patterns): H-DRAFT-2cae0933-derived-condition-status KEPT 5/5, 5/5 (nine seeded verdict events matched a hand-authored key at every commit, byte-identical `--at` replay, all four malformed files rejected); H-DRAFT-2cae0933-run-checkpoint-fidelity KEPT 5/5, 5/5 (six real runs, 1338 numeric tokens traced to a source, six corruptions refused, sha256-reproducible recompile); H-DRAFT-2cae0933-north-star-progress-view KEPT 5/5, 5/5 (12/12 sampled commits matched an independent reference evaluator, one sha256 across three compiles, seeded flip makes `--check` exit 1). Lab fragments 0272, 0273, 0274; ship record experiments/runs/SHIP-hyp-machine-north-star/SHIP-RECORD.md; PR #10. Every shipped script and template is byte-identical to its lab copy under `scripts/parity-check.py`. (H-DRAFT-2cae0933-destination-map.md)

## 0.9.1 (2026-09-06)

### Fixed

- `harden-check.sh`'s background cache refresh is single-flight per repository: a pid file under `.claude/` lets exactly one `--fresh` chain run at a time, a stale lock (dead or rewritten pid) is reclaimed, and the foreground output is unchanged. Four simultaneous session starts on a 35k-file consumer spawned four refresh chains in 0.9.0 (9 to 16 orphan chains were observed machine-wide, each running 17 to 23 minutes) and spawn exactly one now, with the cache still refreshed once. Lab H-DRAFT-c9ec26d9-harden-refresh-single-flight, kept 5/5 in two consecutive runs, the second by a cold executor; consumer gap G9 of the lab-plugin convergence program. (H-DRAFT-c9ec26d9-harden-refresh-single-flight.md)

## 0.9.0 (2026-09-06)

### Added

- `harden-check.sh` runs as a SessionStart hook in every install: every helper it invokes resolves under the plugin root instead of the consumer's `scripts/`, every advisory inspects the repository the session works in (`leak-status.sh` no longer changes into the plugin tree), whole-tree scans are guarded by a tracked-file ceiling so a cache-less consumer run stays under 15 s, and the advisory cache is written only when `.claude/` exists. Measured on a 256-spec consumer: rc 0 with zero stderr on every run (0.4.0: one error line), 8 of 8 helper paths resolve (0.4.0: 3 missing), the printed advisories map one-to-one to blocks whose inputs exist, walls 2.9 to 6.6 s (0.4.0: 7.5 to 13.3 s with nothing useful printed); in the source lab every existing line is preserved. Lab H-DRAFT-40ec0bc2-portable-harden-advisories-v2 (successor of H-303), kept 5/5 in two consecutive runs, the second by a cold executor; closes consumer gap G1 of the lab-plugin convergence program. (H-DRAFT-40ec0bc2-portable-harden-advisories-v2.md)

## 0.8.0 (2026-09-06)

### Added

- `scripts/keep-ship-gate.py` reads every kept hypothesis from committed HEAD, joins the files its run changed against the plugin-shipped paths (the deploy tree in the source lab, or the plugin's own tree in a consumer), and prints one `KEEP-UNSHIPPED` line per keep that has no committed ship record (`experiments/runs/<id>/SHIP.md` carrying a `pr: <n>` line); `harden-check.sh` surfaces the count as ADVISORY-33 at every session start, so a keep that changes shipped bytes stays visible until it ships instead of depending on someone remembering. Lab H-298 (lab-plugin-keep-ships-gate), kept 5/5 in three consecutive runs, the last by a cold executor; north-star condition C-03 of the lab-plugin convergence program. (H-298-keep-ship-gate.md)

## 0.7.0 (2026-09-06)

### Added

- `scripts/eval-grade.py` scores a skill's `claude plugin eval` case files from their bytes alone: it reads each case.yaml with a stdlib parser, applies the deterministic graders (file_exists and tree-targeted regex) to a target tree, and prints one PASS/FAIL line per grader plus a CASE k/n line, so a consumer can measure its intake and hypothesis eval suites without the org-gated eval command or a live session. Lab H-297 (eval-grade-scorer), kept 5/5 in two consecutive runs: golden 15/0 on both sweeps, 8 cases n/n, the OFF path (`import run_cases`) fails with ModuleNotFoundError. First instrument of the lab-plugin convergence north star (C-06). (H-297-eval-grade-scorer.md)

## 0.6.0 (2026-09-06)

### Added

- `scripts/hook-parity-check.py` normalizes a repository's `.claude/settings.json` hooks and a plugin's `hooks/hooks.json` into (event, matcher, guard) rows and prints one line per guard that runs on only one side; `harden-check.sh` gains ADVISORY-32 carrying the count. Measured against the source lab's own wiring, the released plugin shows 23 one-side-only rows, which is why lab and consumer sessions have been protected by different guards. Lab H-DRAFT-4c0dadb8-hook-wiring-parity, kept 5/5 in two consecutive runs after Amendment 1, cause-n-effect research/consumer-parity-gaps.md. (H-DRAFT-4c0dadb8-hook-wiring-parity.md)

## 0.5.0 (2026-09-06)

### Added

- `init` creates an empty `ledger/ledger.jsonl` on every profile and never overwrites it, so the decision kit, the commitment resolver, the claim join, and DASHBOARD.md sections 1 and 2 work in every install instead of reading `source missing`. Re-running init on an existing install adds exactly that file and nothing else; a second run is a no-op. Lab H-DRAFT-4c0dadb8-init-scaffolds-ledger, kept 5/5 in two consecutive runs after Amendment 1 (the baseline's own second-run residue, `.claude/reflex/selftest/report.json` rewritten by the reflex self-test, is recorded as a separate horizon item), cause-n-effect research/consumer-parity-gaps.md gap G2. (H-DRAFT-4c0dadb8-init-scaffolds-ledger.md)
- `drift-check` now compares the copies `init` installs into a repository (the preflight script, the hypothesis template, and `compile-journal.py`) against the plugin's canonical bytes and prints one advisory line per stale copy with the line delta and a review command; it never overwrites a consumer-owned file. On a 256-spec consumer it reports the preflight at 73 lines against the shipped 151 and the template at 49 against 79 where 0.4.0 printed `clean`; a fresh scaffold prints `clean`; a one-byte seed is reported by path; every other drift-check line is byte-identical. Lab H-DRAFT-4c0dadb8-installed-copy-drift, kept 5/5 in two consecutive runs (run 2 by a cold executor), cause-n-effect research/consumer-parity-gaps.md gap G6. (H-DRAFT-4c0dadb8-installed-copy-drift.md)

### Fixed

- `dispatch-status.py`, `stall-signals.py`, and `compile-findings-index.py` read a spec's status through one shared canonicalizer, `hooks/scripts/hyp_status.py`: `keep` is `kept`, `discard` is `discarded`, `refined-into:` and `refined (into` are terminal, a `<canonical>-<qualifier>` token maps to its canonical word, matching is case-insensitive, and `hyp_status.py --lint <root>` lists every non-canonical status with its rewrite. On a 256-spec consumer the dispatch surface drops from 74 open items to the true 50, so 24 closed specs stop re-presenting through the Stop dispatcher; on a vocabulary-clean tree the three scripts' output is unchanged. Lab H-DRAFT-4c0dadb8-status-vocabulary-canon, kept 5/5 in two consecutive runs after Amendment 1, cause-n-effect research/consumer-parity-gaps.md gap G3. (H-DRAFT-4c0dadb8-status-vocabulary-canon.md)

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
