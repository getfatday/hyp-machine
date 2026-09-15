---
id: event/session-observed
type: event
context: {{CONTEXT}}
summary: One finished Claude Code session was measured against the operating model — class counts, leverage, determinism, handoff share, top token step and unmodeled programs — from its transcript and git alone, at no token cost.
representation: file(ledger/om-feedback.jsonl) — one `session-observed` JSON row per session per `through` cursor, canonical bytes, append-only, merge=union
emitted-by: [command/observe-session]
consumed-by: [read-model/passive-feedback]
status: current
---
Canonical node template for the passive feedback worker (lab
H-DRAFT-35397146-om-worker-deterministic, kept 2026-09-13; copy into your
`operating-model/<context>/events/`). The row is written by `scripts/om-worker.py observe
<transcript>` or `drain` (by hand until the startup wake ships), never by a model call. Payload
contract (`schema: 1`, docs/passive-feedback.md): `{session, through, head, counts, leverage,
determinism, handoff_share, top_step, unmodeled_top, hook_timeouts, date, landed_in,
origin_root_key, carried_from}` — program basenames only, no prompt text, tool argument, file body,
path or identity string. The row also says where it landed: `landed_in` is one of `root` (the
checkout the session worked in, unchanged behaviour), `outbox` (that checkout no longer exists; the
row waits in `<state>/om/<repo-key>/outbox.jsonl`, keyed by the pointer's own recorded repository,
with an `origin_root_key` naming the dead root) or `carried` (a later `drain` for a live checkout of
the same repository moved it into that checkout's own ledger; `carried_from` names the origin key it
came from) — lab H-DRAFT-a4a14ff4-om-outbox-carry-forward, kept 2026-09-14
(`docs/passive-feedback.md`, "The outbox and carry-forward"). A transcript that grew writes a second
row with a higher `through`; `om-worker.py latest` is the latest-wins view. Attribution stays in git
— no author fields. If your `.claude/hyp.json` overrides `om_feedback_file`, name that path in the
representation line instead; name your own command and read-model ids once you have them (the ids
above are placeholders the lint will flag as dangling until you do).
