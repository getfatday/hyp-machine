---
name: compile
description: Regenerate executable artifacts — skills, a CLAUDE.md section, hook configurations, SOP prompt blocks — deterministically from a repo's operating-model nodes, so instructions stay in sync with the model instead of drifting as hand-edited prose. Use when asked to generate or update a skill/CLAUDE.md/hooks from the model, or after model nodes change.
---

# compile — nodes to executable artifacts

Evidence base: kept compile experiments — a compiled skill meets or beats its hand-written
equivalent under blind referent grading, and the compiler-as-deterministic-script chain. The node
format is `${CLAUDE_PLUGIN_ROOT}/kernel/operating-model/SCHEMA.md`.

## The compile contract

- **Deterministic**: same nodes in → byte-identical artifact out. The compiler is a script over
  node frontmatter + body, not fresh prose. Two runs must hash equal.
- **Sensitive**: mutate a source node and the output must change — a compiler that ignores its
  inputs is prose with extra steps.
- **Complete**: every source node's material content appears in the artifact — commands' ordered
  steps (including refusal conditions), each policy's enforcement + mechanism, read models'
  locations, events' file representations.
- **Marked**: every compiled artifact opens with a header naming its source nodes and stating
  "COMPILED ARTIFACT — do not hand-edit; regenerate from the operating-model nodes."

## Targets

1. **Skill** — a command node + its policies/read models/events → `SKILL.md` (name := command
   slug; description := summary + trigger; body := ordered steps, refusal condition, policies in
   force, outputs).
2. **CLAUDE.md section** — the context's working rules → one clearly marked, regenerated block in
   the repo's CLAUDE.md (edited as an ordinary consented file change; the rest of the file is
   untouched).
3. **Hook config** — policy nodes with `enforcement: hook` need no compilation: the plugin's
   generic interpreter reads them as data. Compile only a summary listing which policies
   are live.
4. **SOP block** — a command node → a prompt-injectable SOP (model-compiled SOPs carry
   execution).
5. **Executable flow** — a board slice →
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/compile-model-workflow.py --flow slice/<slug>` emits a
   dynamic-workflow target + a portable runner + a shared GWT tests manifest from one model
   commit (format: `docs/workflow-format-reference.md`); `/hyp:run` executes them.

## Rules

- Never hand-patch a compiled artifact; fix the node (or the compiler) and regenerate.
- A gap found in compiled output usually means the *model* is missing a unit — enrich the node
  with evidence, don't special-case the compiler.
