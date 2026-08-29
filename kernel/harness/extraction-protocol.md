# Extraction protocol (FROZEN)

This protocol text is frozen: changes require a new verified revision, never an in-place
edit.

Apply this structured trace-to-model extraction protocol. The transcript is a JSONL session log:
"assistant" records contain tool_use calls with per-message usage.output_tokens; "user" records
carry tool_result payloads. Reconstruct the operating model of the session:

1. ACTORS — who/what executed (roles).
2. COMMANDS — the operation(s) performed, steps in the order they actually happened (from
   tool_use sequence, not narration).
3. POLICIES — rules observably applied (evidence: which record) AND rules the session's context
   implies but that were skipped or violated.
4. READ MODELS — data sources consulted to inform decisions.
5. EVENTS — durable facts produced, each with its physical representation.
6. DEVIATIONS — every departure from the process the session was evidently supposed to follow:
   undeclared actions, missing steps, steps out of contractual order.
7. TOP TOKEN STEP — from usage.output_tokens, the single step consuming the most output tokens.
8. TRACE — an ordered list of every step, each tagged with the model node ids it touched
   (actor/*, command step, read-model/* consumed, policy/* checkpointed, event/* emitted) and its
   output_tokens. Cite the message id (msg_...) for each trace step when available — citations
   are mechanically validated by validate_trace.py.

Context rule: harness-injected context (system reminders, skill listings, attachments) is
consumed WITHOUT tool calls — before flagging "claimed but unread" as a deviation, check whether
the artifact appears in the transcript's injected/attachment blocks; only flag if it appears
nowhere. Ground every claim in specific records. Do not invent elements the transcript does not
evidence.
