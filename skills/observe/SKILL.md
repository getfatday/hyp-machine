---
name: observe
description: Trace a recorded Claude session against the repo's operating model — a node-tagged, token-attributed, citation-validated trace that surfaces deviations and the top token-consuming step. Use when asked to analyze a session, find waste or deviations in how work happened, audit whether a session followed the repo's process, or attribute token cost to process steps.
---

# observe — trace a session against the model

Evidence base: kept extraction experiments. The extraction protocol and grading rubric are frozen artifacts:
`${CLAUDE_PLUGIN_ROOT}/kernel/harness/extraction-protocol.md` and
`${CLAUDE_PLUGIN_ROOT}/kernel/harness/grading-rubric.md`.

## Process

1. **Locate the transcript**: session JSONL under `~/.claude/projects/<project-dir>/`. Assistant
   records carry tool_use calls with per-message `usage.output_tokens`; user records carry
   tool_result payloads.
2. **Apply the frozen extraction protocol** — all eight elements, ending in the TRACE: an ordered
   list of every step, each tagged with the operating-model node ids it touched (actor/*, command
   step, read-model/* consumed, policy/* checkpointed, event/* emitted) and its output_tokens,
   citing the message id (msg_...) per step.
3. **Validate mechanically**: `python3 ${CLAUDE_PLUGIN_ROOT}/kernel/harness/validate_trace.py
   <trace.json> <transcript.jsonl>` — citations must resolve; a trace with dangling citations is
   not a trace.
4. **Report deviations and hotspots**: every departure from the modeled process (undeclared
   actions, missing steps, steps out of contractual order) with record-level evidence, plus the
   single highest output-token step.

## Rules

- Trust traces, not self-reports: agent final messages routinely claim actions their transcripts
  do not evidence (durable finding, 4+ replications). Every claim cites a record.
- Harness-injected context (system reminders, skill listings, attachments) is consumed WITHOUT
  tool calls — check injected blocks before flagging "claimed but unread".
- Deviations against nodes the model doesn't have yet are discoveries — candidate nodes for
  ratification, never penalties.
