---
name: verify
description: Run a controlled A/B experiment proving whether an intervention (a compiled skill, hook, SOP, or way-of-working change) actually works — fixture isolation, blind referent-based grading, binary assertions, mechanical keep/discard/refine verdict. Use when asked whether a change to how work happens is an improvement, or before shipping any compiled artifact as the new standard.
---

# verify — the experiment harness

Evidence base: the method itself, proven across many kept runs. This skill wraps the
`hypothesis` skill (spec → budgeted run → assertions → mechanical verdict → journal) with
the harness discipline for A/B arms. Frozen references:
`${CLAUDE_PLUGIN_ROOT}/kernel/harness/extraction-protocol.md`,
`${CLAUDE_PLUGIN_ROOT}/kernel/harness/grading-rubric.md`,
`${CLAUDE_PLUGIN_ROOT}/templates/HYPOTHESIS-TEMPLATE.md`.

## Harness discipline (what makes a verdict trustworthy)

1. **Spec first** — falsifiable sentence, ONE variable, baseline, fixed budget, 3–5 binary
   assertions, mechanical verdict rule ("keep if N/N pass in 2 consecutive runs; failures →
   refine or discard"). No run without a spec.
2. **Freeze at registration** — protocols, rubrics, and fixtures are immutable for the life of
   the hypothesis; a needed change is a refine into a new hypothesis, never an in-place edit.
3. **Isolate** — arms run in clones/worktrees/sandboxes pinned at a commit; ground truth and
   grading manifests are NEVER arm-visible; headless arms (`claude -p`) avoid leaking the host
   session's context.
4. **Grade blind, by referent** — graders see unlabeled artifacts and match on evidence pointers,
   never names: MATCH (same referent, any phrasing), DISCOVERY (real but unlisted — never
   penalized), FABRICATION (evidence resolves to nothing — the only "invented").
5. **Verdict mechanically, journal always** — apply the verdict rule literally, including on
   failures; journal the run as one write-once fragment (the capture layer's journal
   discipline).

## Rules

- One variable per experiment; two changes = two hypotheses.
- Compare non-inferiority against stochastic baselines (strict superiority on small N is noise).
- A component ships only when its backing hypothesis is kept.
