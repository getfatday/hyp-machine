# The unified event stream

Four keeps, one layer (the source lab's event-emission program, closed 4/4 on
2026-09-02; shipped after the lab's capability census found the machinery living
in fixtures only — every keep said "ships as", nothing had shipped):

| Piece | Keep | Shipped as |
|---|---|---|
| Event rows: append-only `ledger/events.jsonl`, five choke-point emitters | H-238 (2x5/5) | `scripts/events_lib.py` (grammar + validator + idempotent append), `scripts/emit-event.py` (the five verbs as one gated CLI), in-place emitters in `hooks/scripts/interpreter.py` (advisory-surfaced) and `scripts/emit_workflow_fact.py` (workflow-closed), `templates/event-nodes/` (the five canonical nodes), and the stream joins the write-once-guard class |
| Cross-session cursor: exactly-once surfacing at session boundaries | H-239 (2x5/5) | the events-cursor join in `hooks/scripts/session_resolver.py` (inserted mechanically from the kept block; contract test = `scripts/selftest-events.py`) |
| Watch-triggered dispatch: fire on append, gate/cap/quarantine preserved | H-240 (2x5/5) | `scripts/watch-dispatch.py` (kqueue foreground watcher) + `scripts/install-watch-plist.sh` (launchd WatchPaths plist, emitted never auto-loaded, beside the interval plist) |
| Action licensing: no committed policy node, no action | H-241 (2x5/5) | `scripts/events-consume.py` (licensed consumer, stub-recorded actions, the kept degrade rule) |

## The record grammar (frozen at the H-238 registration)

One JSON object per line in the configured `events_file` (default
`ledger/events.jsonl`), canonical-v1 serialization, closed key set:

```json
{"schema": "v1", "instance-of": "event/<node-id>", "caused-by": "<pointer>",
 "date": "YYYY-MM-DD", "subject": "<lane|H-id|slug>", "payload": {…}}
```

- **No event without a node.** A record validates only against its `instance-of`
  node's declared representation (SCHEMA.md law): the node file must exist under your
  `model_dir` (`events/<node>.md` or `*/events/<node>.md`), carry `type: event`, and
  name the stream file in its `representation:` line. Copy the canonical five from
  `templates/event-nodes/` and adapt.
- **Idempotent by bytes.** Replaying an identical record appends zero (canonical-bytes
  dedupe, the H-118 pattern generalized). Dates are caller-pinned — nothing here reads
  a wall clock.
- **No author names in records** — git carries attribution (the validator rejects
  author-shaped keys).
- **Append-only** — the stream is only ever opened in append mode, the write-once
  guard denies edits/rewrites, and `>>` stays legal.

## Activation and gating

The stream is **experiments-profile machinery**: `emit-event.py`,
`events-consume.py`, and the in-place hook/script emitters all check
`.claude/hyp.json` `profile` and stand down below `experiments`. The resolver's
cursor join needs no gate of its own — it is silent unless the stream file exists,
and only the gated emitters create it. Node validation is the second gate either
way: no committed event node, no event.

## Reading the stream

- **Sessions**: every SessionStart/compaction boundary surfaces the records this
  session has not yet seen (`EVENT-STREAM` lines + one `EVENTS-NEW` summary), exactly
  once per session, silent afterwards (H-239's suppression contract, extending H-204).
  The cursor is a consumed-line count in runtime `.claude/events-cursor/<session>.cursor`
  — per-session, never committed; a truncated/replaced stream resurfaces rather than
  loses events.
- **Actions**: nothing acts on an event without a license. A committed policy node
  whose `trigger:` names the event id AND whose `then:` fully resolves to committed
  command nodes licenses a **stub-recorded** action; everything else degrades
  deterministically — payload `severity` warn/error → advisory, payload
  `kind: status` → read-model, else nothing (the kept H-241 degrade rule). A command
  id mentioned in a payload is a mention, not a use — it licenses nothing. Binding
  `then:` to real execution is a registered successor question in the source lab and
  ships only on its keep.

## Waking on events

Two variants, one firing contract (H-217's, re-proven under the trigger swap by
H-240): one dispatch read + at most one capped adoption per firing, relaunch-class
actions consult the K-strikes dispatch gate.

- Foreground: `python3 "$CLAUDE_PLUGIN_ROOT/scripts/watch-dispatch.py"` from the repo
  root (kqueue, trailing-edge debounce, silent start on pre-existing lines,
  `touch .claude/watch-dispatch/STOP-WATCH` to stop).
- Persistent (macOS): `scripts/install-watch-plist.sh <target-dir>` emits the launchd
  WatchPaths plist beside the interval plist (`install-resume-timer.sh`). **Both are
  emitted, never auto-loaded** — loading (`launchctl load …`) is a deliberate,
  attributed maintainer step; the human chooses watch, interval, or both.

## Self-test

`python3 "$CLAUDE_PLUGIN_ROOT/scripts/selftest-events.py"` builds a scratch consumer
repo under a temp dir and proves the loop end to end: two appends land, identical
re-appends land zero, the resolver surfaces exactly once and the cursor advances,
re-boundary is silent, and the resolver's non-event output is byte-identical with the
join stripped (the H-239 contract). Exit 0 on PASS; run it after touching any of these
files.
