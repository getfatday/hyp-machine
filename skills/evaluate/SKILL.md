---
name: evaluate
description: Audit a repo's operating model for defects — schema violations, dangling references, contradictory or unenforced policies, unowned read models, unreified events — with evidence pointers and zero fabrications. Use when asked to lint, review, or find problems in an operating-model/ directory, or to verify a model is internally consistent before compiling from it.
---

# evaluate — find defects in the model

Evidence base: a kept evaluation experiment — seeded defects found with zero fabrications. The schema
contract is `${CLAUDE_PLUGIN_ROOT}/kernel/operating-model/SCHEMA.md`; the referent rule governing
what counts as a defect vs a discovery is in
`${CLAUDE_PLUGIN_ROOT}/kernel/harness/grading-rubric.md`.

## Process

1. **Schema lint (mechanical)** — run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/model-lint.py operating-model/<context>` and carry
   every ERROR line into the findings verbatim (defect class, node, detail). The script is the
   deterministic form of this pass — frontmatter parses, required keys for the node's type,
   id matches path, cross-references resolve, `model.md` catalogs exactly the nodes on disk —
   and a freeform re-derivation of the same checks misses what the script cannot. Fall back to
   hand-checking that list only if the script is unavailable.
2. **Judgment checks (each finding needs an evidence pointer)**:
   - commands whose steps read a read model no node declares, or emit an event with no file
     representation;
   - policies with `enforcement: hook` but a mechanism no interpreter vocabulary covers, or
     enforcement claims contradicted by repo settings;
   - events not reified as files; read models with no maintainer; actors that are individuals
     rather than roles;
   - contradictions between nodes, and between nodes and the repo's observable behavior.
3. **Report**: one finding per line — node, defect class, evidence pointer. Distinguish DEFECT
   (violates schema or contradicts evidence) from DISCOVERY (real behavior the model lacks —
   candidate node, not an error). Never report a defect you cannot point to.

## Rules

- Zero fabrications is the bar: a claimed defect that resolves to nothing on inspection is worse
  than a missed one.
- Fix nothing during evaluation — findings go to the owner (or into hypotheses if the fix is a
  way-of-working change).
