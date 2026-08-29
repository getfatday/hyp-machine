---
name: adopt
description: Turn a repository and its recorded Claude sessions into an explicit operating model — a SCHEMA-shaped operating-model/<context>/ scaffold of actors, commands, events, policies, and read models mined from the repo's artifacts and session transcripts. Use when installing the operating-model lifecycle in a repo, when asked to "model how we work here", extract a team's way of working, or bootstrap operating-model/ in a repo that has none.
---

# adopt — mine a repo into an operating model

Evidence base: kept extraction experiments — structured extraction meets referent-graded
bars, and the packaged method transplants to foreign repositories. Extraction follows the frozen protocol at
`${CLAUDE_PLUGIN_ROOT}/kernel/harness/extraction-protocol.md`; the node format follows
`${CLAUDE_PLUGIN_ROOT}/kernel/operating-model/SCHEMA.md`.

## Process

1. **Scaffold first** if the repo has no method substrate: run `/hyp:init --profile modeling`
   (the capture and hypothesis layers, write guards, and the operating-model scaffold). The
   substrate is proven load-bearing: adopting without it degrades downstream capture and
   experiment quality.
2. **Gather evidence surfaces**: the repo tree (docs, scripts, CI, hooks, templates), git history
   (recurring operations, gates), and — the richest input — recorded session transcripts
   (`~/.claude/projects/<project-dir>/*.jsonl`). Never model from vibes; every node needs an
   evidence pointer.
3. **Extract per the frozen protocol** (elements 1–5): actors (roles, not individuals — an
   executor is *cast into* a role), commands (steps in the order they actually happen, with
   reads/emits), policies (rules observably applied AND rules implied but violated), read models
   (data consulted for decisions), events (durable facts, each with its physical file
   representation — an event that isn't reified as a file does not exist).
4. **Write the scaffold**: `operating-model/<context>/` with one node per file per SCHEMA
   frontmatter, plus `model.md` cataloging every node in one line each. State each node's
   provenance (which transcript/file evidences it). Two frontmatter conventions agents most
   often drop — treat them as mandatory checks before finishing:
   - Every **command** carries `handler:` (`skill/<name>` | `script/<path>` | `manual`) —
     it names how the command executes and SCHEMA requires it alongside issued-by/executor/
     reads/emits.
   - A **terminal policy** (a hard deny/refuse whose reaction never cascades into a command)
     is written with `then: []` **and `status: debt`** plus the reason line — the body prose
     alone does not satisfy SCHEMA's "resolvable `then:` or `status: debt` + reason" rule.
     `status: current` with an empty `then:` is invalid even when the reasoning is right.
5. **Ratify**: present the model to the repo's owner as *their way of working*, listing any
   low-confidence nodes separately. Owner sign-off is recorded; unratified nodes stay flagged.

## Rules

- Ground truth discipline: never invent elements the evidence does not show (fabrication is the
  only failure that counts; coined names for real behavior are discoveries, not errors).
- Policies that should be machine-enforced get `enforcement: hook` frontmatter with a
  `mechanism:` block (deny-tools/deny-paths) — the plugin's generic interpreter enforces them
  with no code changes. The block form is load-bearing — an inline string is NOT
  machine-readable. Emit exactly this shape:

  ```yaml
  enforcement: hook
  mechanism:
    event: PreToolUse
    deny-tools: [Edit, Write]
    deny-paths: [./program.md]
  ```
- **`enforcement: hook` means THIS interpreter enforces it**: reserve it for policies the
  PreToolUse interpreter can actually block — non-empty `deny-tools` AND `deny-paths`. A gate
  enforced elsewhere (git pre-commit, CI, a pipeline script) is `enforcement: procedural` with
  the real gate named in prose; declaring it hook with empty deny lists misstates the
  enforcement in force (a hook-labeled node with empty deny lists gives the interpreter nothing
  to deny).
- **The plugin's own installed guards are model content**: init installs settings.json
  Edit/Write denies (the raw and journal-fragment directories). Post-install these are real,
  observable enforcement in the repo — model them as policy nodes with `enforcement: hook`
  and the block mechanism above (omitting them understates the enforcement actually in
  force).
- **Self-lint before ratifying**: run
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/model-lint.py operating-model/<context>` and fix every
  ERROR before presenting the model — it checks mechanically what hand-checking has measurably
  dropped: frontmatter parses (quote any scalar containing `: `), per-type required keys,
  id slug equals filename, every `reads:`/`emits:`/`then:` reference resolves to an existing
  node, every node has a one-line row in model.md. Hand-check that list only if the script is
  unavailable (one unlinted node breaks the catalog contract).
