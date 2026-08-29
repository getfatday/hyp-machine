# The dynamic-workflow format — first-hand reference

Ground truth for the model→workflow compiler (target 1). This is the format a session's
Workflow tool executes. Nothing here is guessed.

## Shape

A workflow is ONE JavaScript file (plain JS, NOT TypeScript — annotations fail to parse),
beginning with a PURE-LITERAL meta export (no variables, calls, spreads, interpolation):

    export const meta = {
      name: 'kebab-name',
      description: 'one line',            // required
      whenToUse: 'optional listing line',
      phases: [ { title: 'Phase', detail: '...' }, ... ],  // matched EXACTLY to phase() calls
    }
    // body: async context — top-level await is fine

## Primitives (the whole API)

- agent(prompt, opts?) -> Promise<any>. Spawns one subagent. Without schema returns final
  text; with opts.schema (JSON Schema) the return is the validated object (retry on
  mismatch). opts: label (display), phase (progress group — use inside pipeline/parallel to
  avoid racing the global phase()), model ('sonnet'|'opus'|'haiku'|... — OMIT to inherit;
  only set when a tier clearly fits), effort ('low'|'medium'|'high'|'xhigh'|'max' — 'low'
  for mechanical stages), isolation: 'worktree' (EXPENSIVE, only for parallel file
  mutation), agentType (custom agent from the registry). Returns null if skipped/dead —
  filter with .filter(Boolean).
- parallel([thunks]) — concurrency with a BARRIER; a throwing thunk resolves null, never
  rejects. Use ONLY when stage N needs ALL of stage N-1.
- pipeline(items, stage1, stage2, ...) — per-item chains, NO barrier between stages; the
  DEFAULT for multi-stage work. Stage callbacks get (prev, originalItem, index). A throwing
  stage drops that item to null.
- phase('Title') — progress grouping; log('msg') — narrator line to the user.
- args — the Workflow call's args value, verbatim (real JSON values, never a stringified
  list). THE parameterization channel for saved workflows.
- budget — {total, spent(), remaining()}: the turn's shared token target; agent() throws
  once spent() reaches total. Guard loops: while (budget.total && budget.remaining() > N).
- workflow(nameOrRef, args) — run a saved workflow ({scriptPath} or name) inline as a
  sub-step; one nesting level only; shares caps/budget.

## Hard constraints (compiler MUST honor)

- Date.now()/Math.random()/argless new Date() THROW (resume/replay safety). Timestamps come
  in via args; randomness = vary prompts by index.
- No filesystem or Node API in the script body — agents do all I/O through their tools.
- Concurrency cap min(16, cpus-2) per workflow; lifetime cap 1000 agents; 4096 items/call.
- meta.phases titles must byte-match phase() calls.

## Durability + testability (why this format fits the directive)

- Every invocation persists its script to a session path and returns it; re-invoke with
  {scriptPath} to iterate, {scriptPath, resumeFromRunId} to RESUME: the longest unchanged
  prefix of agent() calls replays from cache (same prompt+opts = cached result). Same
  script + same args = 100% cache = the repeatability lever.
- The run's journal.jsonl records every agent's actual return — the mechanical test surface
  (assert on it, never on prose reports).
- Subagents are told their final text IS the return value → raw data out, schemas enforce
  shape.

## Invocation + portability (honest)

- The tool exists in Claude Code sessions like this one; a SKILL can instruct the session to
  call Workflow({scriptPath, args}) — that is the "skill invokes the dynamic workflow"
  wrapper the maintainer proposed. Availability caveat: consumers without the Workflow tool
  need the fallback lane.
- THE PROVEN FALLBACK LANE: a deterministic
  python runner + `claude -p` child sessions + mechanical graders — the counted harness
  shape proven across many kept runs. The compiler should emit BOTH targets from one model:
  workflow JS (rich, resumable) and a portable runner (stdlib python + claude -p), sharing
  the same GWT-derived assertions.

## Token-savviness mapping (the directive's cost requirement)

- Deterministic model nodes (pure projections, lints, compiles) → SCRIPT steps: zero tokens.
- Judgment nodes → agent() with the SMALLEST sufficient tier: effort:'low' for mechanical
  transforms, default inherit for real work, big tiers only for adversarial/verify stages.
- GWT Thens → schema-validated assertions graded by script wherever the Then is mechanical;
  panel/grader agents only for semantic Thens.
