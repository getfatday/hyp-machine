---
name: init
description: Scaffold the current repository for hyp, profile-gated — capture (write-once raw, notes, index, journal fragments, guard rules), experiments (hypotheses, runs, spec template, deterministic preflight), and modeling (operating-model/ with SCHEMA, catalog, glossary, and interpreter-enforced policy nodes). Also migrates repositories initialized by the retired predecessor plugins. Idempotent; safe to re-run to repair drift.
disable-model-invocation: true
---

# Initialize hyp in this repository

Run the deterministic scaffold script, then commit the result. Everything hyp does is
activated by PROFILE inside one install — you choose how much of the discipline this
repository adopts, and you can upgrade later by re-running init with a higher profile:

| Profile | Adds |
|---|---|
| `capture` (default) | Write-once raw capture, distilled notes, the wiki index, write-once journal fragments, the compiled dashboard, guard rules |
| `experiments` | Everything above, plus the hypothesis loop: `hypotheses/` + spec template, `experiments/runs/`, the deterministic preflight, the run gate |
| `modeling` | Everything above, plus the operating-model lifecycle: `operating-model/` (SCHEMA copy, per-context catalog + glossary + sources seam) and interpreter-enforced policy nodes |

The plugin's hooks are only active while the plugin is enabled; the artifacts this step
writes into the repository (the CLAUDE.md rules block, settings deny rules, GOVERNANCE.md,
the installed scripts) are the durable layer that survives a plugin disable.

## Steps

1. **Choose the profile and paths.** Default profile `capture`; default paths: raw
   `research/raw/`, notes `research/notes/`, index `research/index.md`, journal fragments
   `experiments/journal-fragments/`; experiments adds specs `hypotheses/`, template
   `hypotheses/TEMPLATE.md`, runs `experiments/runs/`, preflight `experiments/preflight.py`;
   modeling adds `operating-model/` with one context directory (default context = the
   repository directory name). Use the defaults unless the user asked for different
   locations.
2. **Run the scaffold** from the repository root:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/init-scaffold.py" . \
     --profile capture|experiments|modeling \
     [--context NAME] [--raw-dir DIR] [--notes-dir DIR] [--index-file FILE] \
     [--journal-dir DIR] [--journal-file FILE] [--compiled-file FILE] \
     [--hypotheses-dir DIR] [--runs-dir DIR] [--template-file FILE] [--preflight-file FILE]
   ```

   The script is idempotent: it creates what is missing, repairs the plugin-owned canonical
   artifacts (the config file, the CLAUDE.md marker block, the deny rules, the installed
   scripts), and never overwrites consumer-owned content (the index, notes, raw files,
   fragments, registered specs, an edited template, model nodes, or an existing
   GOVERNANCE.md). Re-running with the same inputs is a byte-level no-op. Read its
   per-artifact output and report it to the user verbatim.
3. **Migration** (automatic, only when legacy state exists): a repository initialized by the
   retired predecessor plugins is adopted in the same pass — the legacy config files are
   merged into `.claude/hyp.json`, the legacy CLAUDE.md rules blocks are replaced by the
   hyp block, and the installed guard rules are re-pointed. Nothing consumer-owned is
   rewritten; the scaffold prints one line per migrated artifact.
4. **Review what changed** (`git status`, `git diff`) and explain the three layers:
   - hooks (from the plugin, active while enabled): write-once guard, capture-intent nudge,
     session drift check, unjournaled-work backstop, dashboard refresh; at the experiments
     profile the preflight gate and the advisory commit backstop; at the modeling profile
     the generic policy interpreter (it reads `operating-model/*/policies/*.md` as data —
     policy nodes with `enforcement: hook` deny, `enforcement: advisory` print one line);
   - durable repository artifacts (survive plugin disable): the CLAUDE.md rules block, the
     `.claude/settings.json` deny rules, GOVERNANCE.md, the installed scripts and template;
   - procedure: the `intake`, `hypothesis`, and modeling skills carry the processes.
5. **Commit** the scaffold as one attributed commit, separate from other work.
6. **Verify**: the next session start should print `hyp ... drift check: clean`. To check
   immediately:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/drift-check.py" < /dev/null
   ```

## Notes

- Re-running init is the supported way to repair drift the session-start check reports, and
  the supported way to raise the profile (`--profile experiments` on a capture repo adds
  only the experiment artifacts).
- All scaffolded content is rendered from `${CLAUDE_PLUGIN_ROOT}/templates/` and the
  plugin's shipped scripts; nothing is generated from timestamps, so re-running with the
  same inputs is byte-stable.
- At the modeling profile the scaffold seeds `operating-model/SCHEMA.md` (the node
  grammar), `operating-model/<context>/model.md` (the catalog), a GLOSSARY stub, and an
  empty-but-documented `sources.yaml` evidence-source manifest. The `adopt` skill fills the
  model from evidence; nothing here invents nodes.
