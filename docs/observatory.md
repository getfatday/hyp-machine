# The observatory: read-side instruments over your repository's own tracked files

Six counted instruments, each a pure projection — they read git history and tracked
files, write nothing (or only their declared output paths), and answer one question a
cold session otherwise answers by archaeology. Every one ships byte-preserving from the
counted artifact of a kept hypothesis in the source lab (2x consecutive full-pass counted
runs each; keep dates 2026-08-28); only provenance framing and consumer path resolution
differ, and each file's header names its exact divergences. A seventh, the live hook-fed board,
is the one opt-in daemon and has its own section below.

## What each instrument answers

| Instrument | The question it answers | Kept as |
|---|---|---|
| `scripts/stall-signals.py` | "What is silently stalled RIGHT NOW, with evidence?" — the S1-S5 signal strip: experiment gone quiet, claimed-but-idle, forgotten sibling follow-up, close-condition stopped moving, running-with-no-journal-entry. Gate-aware (a spec gated on an open hypothesis is not stalled), snooze-aware (a tracked "snoozed until YYYY-MM-DD" append suppresses a chip and re-arms after), one tunable block of window defaults (S1 3d, S2 24h, S3 7d, S4 7d, S5 2d). `--now` pins the instant; `--json` for machines. | H-154, 2x5/5: all five planted stall classes flagged at seeded onsets with evidence, zero flags on three decoys, snooze round-trip from file state alone, compiled dashboard byte-identical with detector on vs off |
| `scripts/flow-metrics.py` | "Which of the five typed waste classes is happening?" — machine-joinable `FLOW <CLASS> lane=...` lines: IDLE-RUNNABLE, STALE-GATE, UNRULED-TERMINAL, VOID-CLUSTER, WIP-BREACH. Exit 0 always (advisory, never a gate); pinned `--now` makes re-invocation byte-identical. Joins `scripts/waste-status.py` (0.2.0): waste-status remains the human-readable prose report, flow-metrics is the counted typed alarm surface over the same committed timestamps. | H-192, 2x5/5: 5/5 seeded classes flagged with correct type and lane, zero false alarms on the replayed healthy window, byte-identical re-runs, read-only tree hash unchanged |
| `scripts/identity-resolve.py` | "Whose is each artifact, and which of this is MINE?" — mailmap-canonicalized registering-commit attribution, agent-assist share from Co-authored-by trailers, the acting-as resolution with YOURS/OTHERS partitions (render-time only: the committed projection stays viewer-independent), and the privacy-first avatar ladder (committed image, then deterministic local initials; the remote tier is an opt-in log-only stub, OFF). Fully offline by construction. | H-156, 2x5/5 fully offline: exact ground-truth attribution with alias folding, correct acting-as + partitions under both fixture identities, projection bytes identical across viewers, zero outbound avatar attempts, zero third-party-name leaks |
| `scripts/derive-metrics.py` | "Which direction is this metric actually moving?" — deterministic derivation of declared metric nodes (origination share, zero-touch execution share, ask rate) from tagged journal fragments and the workflow-facts stream into an append-only `ledger/metrics-timeseries.jsonl`; `--trend` classifies improving/flat/degrading against each node's declared direction-of-good; lineage bumps recompute a back-series without touching superseded rows. | H-129, 2x5/5: byte-identical double derivation, zero re-appends on unchanged input, exact seeded-window shares, reconstruction-grade t0 rows emitted exactly once, correct direction verdicts on all seeded histories |
| `scripts/emit_workflow_fact.py` + `scripts/harvest_gwt.py` (+ `scripts/facts_lib.py`) | "What actually closed, and what test cases did it prove?" — the workflow-facts loop: one validated `workflow-fact/v1` record per workflow close (append-only, idempotence key workflow+sha), and the harvester compiling executed gate outcomes into `gwt-case/v1` records on their owning slice (candidate state; outcomes stored separately — specs are canon, outcomes are runs). | H-118, 2x4/4: byte-identical replays with zero duplicate appends, all planted amendment classes proposed with zero findings on the clean control, every harvested case lint-clean and round-trip byte-identical, meters moved exactly as seeded |
| `scripts/render-case-study.py` (+ `scripts/fact_fidelity.py`, `scripts/content_lint.py`, `scripts/jargon.json`, `scripts/house-vocabulary.json`, `scripts/vocab-wordlist.txt`) | "Can an outside reader understand one kept experiment from a single page?" — the per-keep case-study renderer: a plain-language page where every number is regex-extracted from artifact bytes at render time, every quote is verified as an exact byte substring, every fact carries a `[source: ...]` pointer, and the render refuses to emit a page failing its own fidelity grammar or content lint. The shipped file renders the source lab's pinned H-188 keep and is the template: repoint its artifact constants at your keep, keep the machinery. Since decision record DEC-037 (2026-09-11) the page explains the lab's coined terms by default: when the bundle carries files beyond the nine fact-table artifacts and those files use a house-only vocabulary term that the page uses bare, the term's first use gains its plain-language gloss, byte-for-byte from `scripts/house-vocabulary.json` (the pinned `scripts/vocab-wordlist.txt` must sit beside it; the render refuses without either). `--no-vocab` renders the bare page exactly as before; `--vocab PATH` glosses from another vocabulary file. | H-201, 2x5/5: zero renderer-invented facts, the cold outside reader answered 5/5 synthesis questions from the page while the raw-artifacts reader answered strictly fewer, lint clean, byte-identical recompilation; H-225, 2x5/5: the glossed render reproduces the reference page byte-identically, glosses a seeded coined term at first use in the vocabulary's own words, and reports an unregistered coinage instead of inventing a gloss (default flipped by DEC-037) |

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

## The live board: hook-fed sessions, traces, logs and metrics (the one opt-in daemon)

`scripts/observatory.py` answers a question none of the read-side instruments can: **what is
every Claude Code session on this machine doing right now — in which repo, on which branch,
under which hypothesis — and how much of its tool work runs through the operating model?** It is
a single file (stdlib at import; Textual only inside `board`/`web`) with a regression test in
`scripts/selftest-observatory.py`.

**Why hooks feed it.** On a managed host, first-party Claude Code OpenTelemetry is pinned to the
corporate gateway and local endpoint overrides are dropped, so the session's own exporter can
never reach a local receiver. Claude Code **hooks** run outside that managed block, so they are
the feed. The receiver still speaks OTLP/HTTP (JSON, and protobuf through a lazy decoder) on
`/v1/logs`, `/v1/metrics`, `/v1/traces`, so an unmanaged host can point Claude's own exporter at
it unchanged.

**How the one daemon stays inside the discipline above.** `serve` is a process, but everything it
derives is a pure projection: `ratios` and `dump-state` recompute byte-identically from the
events JSONL; the receiver writes only its declared paths (spool offset, events, state); the
hooks never block a session (exit 0, nothing on stdout, a `/bin/sh` append or a 1.5 s POST
timeout); nothing here is a gate. The board is a tailer that never writes.

| Kept as | What it proved |
|---|---|
| H-DRAFT-2c1fc974-hook-fed-observatory-board (2026-09-04, 2x5/5) | the receiver, the transcript join (repo / branch / most-recent H-id), the sessions board |
| H-DRAFT-fcf7b3fa-op-provenance-classification (2026-09-06, 2x5/5) | one program token per tool call, never an argument; classification against the per-repo catalog |
| H-DRAFT-fcf7b3fa-model-leverage-read-model (2026-09-06, 2x5/5) | per-session and global leverage / determinism ratios, byte-stable recompute, metrics gauges |
| H-DRAFT-2652d478-async-spool-backfill (2026-09-06, 2x5/5) | the async `/bin/sh` spool transport (+1.0 to +4.5 ms per tool call) with end-of-session transcript backfill, completeness 20/20 |

### The four provenance classes and the two ratios

Every counted tool record (`PostToolUse` and `PostToolUseFailure`; lifecycle events never count)
gets `op_class` and `op_node` before it is appended, from a catalog of the session's git toplevel
(cached 60 s): operating-model node ids from the link text of `[kind/name](path.md)` in
`<model_dir>/*/model.md`, skill names from `skills/` and `.claude/skills/`, script names from
`scripts/`, `hooks/` and the top-level `*.py` of the preflight script's directory.

| record | `op_class` | `op_node` |
|---|---|---|
| `Skill` whose op is a repo skill | `modeled-stochastic` | `command/<op>` if that node exists, else `skill/<op>` |
| `Bash` whose op (or a later program in a `;`/`&&`/`\|` chain, first catalog hit wins) is a repo script | `modeled-deterministic` | `script/<op>` |
| `Agent` | `delegated` (a handoff, not work) | `agent/<subagent_type>` |
| every other tool call (Read, Edit, an unknown program, a skill the repo does not own) | `unmodeled` | `null` |

- `leverage = modeled / (modeled + unmodeled)` where modeled = deterministic + stochastic
- `determinism = deterministic / (deterministic + stochastic)`
- `handoff_share = handoff / (modeled + unmodeled + handoff)` — handoffs are excluded from leverage and reported on their own
- all `null` when the denominator is 0

`unmodeled_top` (per machine and per repo, with a `suggested_node`) is the discovery list: the
programs and tools sessions reach for that the model does not name yet. `model_coverage`
(`nodes_seen / nodes_routable`) and `failed_routes` (modeled `PreToolUse` with no `PostToolUse`
after 120 s) complete the read model.

### Subcommands

| subcommand | runs under | what it does |
|---|---|---|
| `serve --port N` | `python3` (stdlib) | the receiver: `POST /v1/logs\|/v1/metrics\|/v1/traces` (OTLP/JSON or protobuf) and `POST /hook`; tails the spool; polls `claude agents --json` every 5 s; joins each session to its transcript and to git; appends classified records to the events JSONL and writes the state JSON atomically every second; `GET /state`. Refuses port 4318. |
| `hook --port N` | `python3` (stdlib) | the post-transport entrypoint: hook JSON on stdin → one OTLP/JSON log record (one `op.name` token, a ≤200-char outcome summary, never tool content or arguments) → `serve`, 1.5 s timeout, always exit 0, silent |
| `install-hooks` / `uninstall-hooks` | `python3` (stdlib) | idempotently add / remove the nine hook entries (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure, Stop, SessionEnd, SubagentStart, SubagentStop) in a settings.json; every other key is preserved and uninstall restores the file exactly |
| `ratios [--session SID]` | `python3` (stdlib) | recompute the leverage / determinism blocks from the events JSONL alone, sorted keys, byte-identical across runs |
| `dump-state` | `python3` (stdlib) | print the state JSON |
| `board [--view sessions\|traces\|logs\|metrics\|board]` | `uv run` (Textual 8.2.8) | the terminal board: repo → session → hypothesis → sub-agent rows with provenance-coloured lanes, `lev det` per session; keys `1`–`4` switch Sessions / Traces / Logs / Metrics |
| `web --port 8820` | `uv run` (textual-serve) | the same board in a browser; one `board` process per tab |

### Quick start

```sh
# 1. hooks, user scope: every session on this machine from now on (edits <config_dir>/settings.json, reversible)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/observatory.py" install-hooks --scope user

# 2. the receiver (long-lived, stdlib only). Never 4318.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/observatory.py" serve --port 4319

# 3. the board, in a browser or in the terminal (uv resolves Textual from the file's inline metadata)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/observatory.py" web --port 8820
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/observatory.py" board
```

`CLAUDE_PLUGIN_ROOT` is set inside hooks; from a shell use the installed copy,
`~/.claude/plugins/cache/hyp-machine/hyp/<version>`. Undo step 1 with `uninstall-hooks --scope user`.
The hooks are **not** wired in the plugin's `hooks/hooks.json` on purpose: an undrained spool grows
without bound (252 MB in two days on the source host even with a receiver draining it, and nothing
rotates it yet), the append is paid on every tool call by every consumer whether or not anyone runs
`serve`, and the installed command embeds an absolute spool path that belongs in user scope, not in a
committed project settings file.

### Hook transports

| transport | command installed | cost per call | completeness |
|---|---|---|---|
| `spool` (default) | `/bin/sh -c 'cat >> "SPOOL"; printf "\036\n" >> "SPOOL"'`, `async: true`, timeout 30 | +1.0 to +4.5 ms per tool call (H-DRAFT-2652d478 runs 1–2) | a headless exit can drop in-flight async hooks; `serve` backfills the lost tail from the session transcript once the session ends (20/20 complete) |
| `spool --sync` | the same command, synchronous | ~100 ms per firing (process-spawn floor on macOS) | lossless by itself |
| `post --port N` | `python3 <plugin>/scripts/observatory.py hook --port N`, `async: true`, timeout 5 | ~75 ms CPU (interpreter start), off the hot path | lossy at exit; embeds the plugin's absolute path, which changes on every plugin upgrade — prefer `spool` |

### Path resolution

- Consumer paths come from `.claude/hyp.json` overlaid on the plugin defaults, through
  `hooks/scripts/hyp_config.py`: `hypotheses_dir` (spec titles on the board), `model_dir` (node
  ids), the directory of `preflight_file` (top-level scripts). `skills/`, `.claude/skills/`,
  `scripts/` and `hooks/` are conventional (hyp_config has no keys for them).
- `CLAUDE_CONFIG_DIR` is honoured wherever `~/.claude` was assumed: transcripts under
  `<config_dir>/projects`, `--scope user` settings, the default data directory.

### The data directory contract

Everything the receiver produces lives in one directory outside every repository, resolved in
this order (first hit wins): the explicit `--state-file` / `--events` / `--spool` flags,
`$HYP_OBSERVATORY_DIR`, then `<CLAUDE_CONFIG_DIR or ~/.claude>/observatory/`. Inside it:

| file | class | contract |
|---|---|---|
| `spool.jsonl` | persist, **never commit** | raw hook stdin, verbatim: prompt text and full tool inputs. Transcript-grade sensitive; never ship it in a bug report |
| `spool.jsonl.offset` | regenerable | the receiver's read cursor; deleting it replays the spool |
| `events.jsonl` | persist, never commit | classified records: op tokens, classes, outcomes, timestamps; no prompts or arguments, but machine paths and session ids |
| `state.json` | ephemeral | the 1 Hz snapshot, rebuilt from events + registry |

The default is user-level because the board is machine-wide (one receiver sees every repo).
`install-hooks` bakes the resolved spool path into the nine commands at install time, which is
why the block belongs in user scope. Not yet built, tracked as separate hypotheses in the source
lab: spool rotation in `serve`, a `.claude/hyp.json` `observatory_dir` key for repo-scoped
installs, and a launchd plist emitter in the style of `install-resume-timer.sh`.

### Dependency carve-out and known gaps

Everything a hook or the receiver runs (`hook`, `serve`, `install-hooks`, `uninstall-hooks`,
`ratios`, `dump-state`) is standard-library Python 3.9 and covered by
`python3 scripts/selftest-observatory.py` (29 checks; binds only free ports, writes only under
a temp dir). `board` and `web` need `uv run` (Textual 8.2.8, textual-serve 1.1.3, declared in
the file's PEP 723 block) and are not covered by the selftest; their seven Pilot tests stay in
the source lab. Known gaps: the spool and events files never rotate (size them; delete when
`serve` is down); port 4318 is refused; the registry is polled no faster than every 5 s; the
`post` transport embeds the plugin path.

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
