---
name: run
description: Execute a compiled operating-model flow to a mechanical verdict — run the portable runner (or the dynamic-workflow target) that the compile skill emitted from the repository's model, grade its GWT assertions, and report pass/fail per case. Use when asked to run a compiled workflow, execute a modeled process, or replay a flow the model compiled. Refuses with an adopt-first pointer when the repository has no operating model or compiled flow yet.
---

# run — execute a compiled flow

The model is SOURCE, compiled artifacts are EXECUTABLES, and GWT cases are the TEST SUITE.
This skill runs what `compile` emitted; it never improvises a process that was not compiled
from the model.

## Preconditions (refusal routing)

1. **No `operating-model/<context>/` in the repository** → refuse and route: "this
   repository has no operating model yet — run `/hyp:adopt` to mine one from the repo and
   its recorded sessions, then `/hyp:compile`." Do not fabricate a model or a flow.
2. **A model exists but nothing is compiled** (no `<name>.runner.py` / `<name>.workflow.js`
   / `<name>.tests.json` triple) → refuse and route to `/hyp:compile`.
3. Both exist → run.

## Process

1. **Locate the compiled triple** for the requested flow: `<name>.workflow.js` (dynamic
   workflow target), `<name>.runner.py` (portable runner), `<name>.tests.json` (the shared
   GWT assertion manifest both targets consume). The consumer's compiled directory is named
   in its config (`.claude/hyp.json`, key `model_dir`) — default
   `operating-model/<context>/compiled/`.
2. **Prefer the portable runner** in headless or tool-limited sessions:
   `python3 <name>.runner.py` from the repository root. Deterministic (tier-D) steps run as
   subprocesses at zero model cost; judgment steps spawn budgeted child sessions. In a
   session with the Workflow tool, the workflow target is the richer, resumable lane —
   invoke it with the flow's declared args. If a spawned child dies at startup with a
   not-logged-in error at zero cost, classify the credential surface before re-logging —
   the plugin's environment-health doctor types the state and names the one next step
   (`docs/doctor.md`).
3. **Grade mechanically**: the runner writes its results next to the compiled triple; every
   GWT case gets pass/fail from its assertion manifest — never from impressions of the
   transcript. Report per-case results and the run's verdict line verbatim.
4. **Respect the budget**: compiled flows carry a ceiling priced from the consumer's cost
   table. Halt at the ceiling and record the run as budget-exceeded rather than finishing
   over it.

## Rules

- Never hand-edit a compiled artifact to make a run pass; fix the model node (or the
  compiler input) and recompile — compiled artifacts open with a header saying exactly this.
- A failing GWT case is a result, not an obstacle: record it. The model evolves through
  evidence, and a red case is evidence.
- Runs write only their declared surfaces (the results file and the artifacts the flow's
  own steps name). Anything else the run wants to touch is a model gap to report.
