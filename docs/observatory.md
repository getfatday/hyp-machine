# The observatory: read-side instruments over your repository's own tracked files

Six counted instruments, each a pure projection — they read git history and tracked
files, write nothing (or only their declared output paths), and answer one question a
cold session otherwise answers by archaeology. Every one ships byte-preserving from the
counted artifact of a kept hypothesis in the source lab (2x consecutive full-pass counted
runs each; keep dates 2026-08-28); only provenance framing and consumer path resolution
differ, and each file's header names its exact divergences.

## What each instrument answers

| Instrument | The question it answers | Kept as |
|---|---|---|
| `scripts/stall-signals.py` | "What is silently stalled RIGHT NOW, with evidence?" — the S1-S5 signal strip: experiment gone quiet, claimed-but-idle, forgotten sibling follow-up, close-condition stopped moving, running-with-no-journal-entry. Gate-aware (a spec gated on an open hypothesis is not stalled), snooze-aware (a tracked "snoozed until YYYY-MM-DD" append suppresses a chip and re-arms after), one tunable block of window defaults (S1 3d, S2 24h, S3 7d, S4 7d, S5 2d). `--now` pins the instant; `--json` for machines. | H-154, 2x5/5: all five planted stall classes flagged at seeded onsets with evidence, zero flags on three decoys, snooze round-trip from file state alone, compiled dashboard byte-identical with detector on vs off |
| `scripts/flow-metrics.py` | "Which of the five typed waste classes is happening?" — machine-joinable `FLOW <CLASS> lane=...` lines: IDLE-RUNNABLE, STALE-GATE, UNRULED-TERMINAL, VOID-CLUSTER, WIP-BREACH. Exit 0 always (advisory, never a gate); pinned `--now` makes re-invocation byte-identical. Joins `scripts/waste-status.py` (0.2.0): waste-status remains the human-readable prose report, flow-metrics is the counted typed alarm surface over the same committed timestamps. | H-192, 2x5/5: 5/5 seeded classes flagged with correct type and lane, zero false alarms on the replayed healthy window, byte-identical re-runs, read-only tree hash unchanged |
| `scripts/identity-resolve.py` | "Whose is each artifact, and which of this is MINE?" — mailmap-canonicalized registering-commit attribution, agent-assist share from Co-authored-by trailers, the acting-as resolution with YOURS/OTHERS partitions (render-time only: the committed projection stays viewer-independent), and the privacy-first avatar ladder (committed image, then deterministic local initials; the remote tier is an opt-in log-only stub, OFF). Fully offline by construction. | H-156, 2x5/5 fully offline: exact ground-truth attribution with alias folding, correct acting-as + partitions under both fixture identities, projection bytes identical across viewers, zero outbound avatar attempts, zero third-party-name leaks |
| `scripts/derive-metrics.py` | "Which direction is this metric actually moving?" — deterministic derivation of declared metric nodes (origination share, zero-touch execution share, ask rate) from tagged journal fragments and the workflow-facts stream into an append-only `ledger/metrics-timeseries.jsonl`; `--trend` classifies improving/flat/degrading against each node's declared direction-of-good; lineage bumps recompute a back-series without touching superseded rows. | H-129, 2x5/5: byte-identical double derivation, zero re-appends on unchanged input, exact seeded-window shares, reconstruction-grade t0 rows emitted exactly once, correct direction verdicts on all seeded histories |
| `scripts/emit_workflow_fact.py` + `scripts/harvest_gwt.py` (+ `scripts/facts_lib.py`) | "What actually closed, and what test cases did it prove?" — the workflow-facts loop: one validated `workflow-fact/v1` record per workflow close (append-only, idempotence key workflow+sha), and the harvester compiling executed gate outcomes into `gwt-case/v1` records on their owning slice (candidate state; outcomes stored separately — specs are canon, outcomes are runs). | H-118, 2x4/4: byte-identical replays with zero duplicate appends, all planted amendment classes proposed with zero findings on the clean control, every harvested case lint-clean and round-trip byte-identical, meters moved exactly as seeded |
| `scripts/render-case-study.py` (+ `scripts/fact_fidelity.py`, `scripts/content_lint.py`, `scripts/jargon.json`) | "Can an outside reader understand one kept experiment from a single page?" — the per-keep case-study renderer: a plain-language page where every number is regex-extracted from artifact bytes at render time, every quote is verified as an exact byte substring, every fact carries a `[source: ...]` pointer, and the render refuses to emit a page failing its own fidelity grammar or content lint. The shipped file renders the source lab's pinned H-188 keep and is the template: repoint its artifact constants at your keep, keep the machinery. | H-201, 2x5/5: zero renderer-invented facts, the cold outside reader answered 5/5 synthesis questions from the page while the raw-artifacts reader answered strictly fewer, lint clean, byte-identical recompilation |

## The shared discipline

- **Read-side, never a daemon.** Every signal is a registered expectation plus a clock,
  computed from tracked files at invocation — no heartbeat obligations on agents, no
  background process. Session-only work that has not touched a tracked file, ledger row,
  or claim is invisible to every file-based surface, and the instruments say so.
- **Advisory, never a gate.** Detection exits 0 in alarm states; alarms are lines, not
  blocks.
- **Deterministic.** Pinned `--now`/`--as-of` inputs reproduce byte-identical output;
  the counted runs assert it.
- **Path resolution.** Instruments read the hyp scaffold defaults (`hypotheses/`,
  `experiments/runs/`, `experiments/journal-fragments/`, `research/raw/`) and resolve
  the work ledger through `.claude/hyp.json` `ledger_file` where they read it; inputs
  your repo does not have degrade gracefully to empty.

## Deliberately not shipped in this wave

- **The board renderer** (the source lab's H-036 compile-render-critique loop over the
  Event Modeling board): DISCARDED 2026-08-28 after three counted content-quality
  failures — at that budget the critique loop could not hold the visual grammar to a
  full pass. Its successor (H-212: a frozen adversarial finish-review panel graded
  against recorded human design-ready calls) is registered and pending in the source
  lab; the diagram lane you already have (`model-to-board.py` + `em-slice-lint.py`)
  is unaffected.
- **The ask-triage reference gates** (H-200's calibrated fixture-side hook pair): still
  not shipped. The blocking ask itself was converted from a human decision into an
  experiment — H-206, registered in the source lab, hardens the reference
  implementations into a mechanical A/B/C classifier proven in a scratch consumer repo.
  They ship on that keep. See `docs/ask-triage.md`.
