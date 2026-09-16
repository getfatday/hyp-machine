# H-DRAFT-routing-candidate: route {{CLASS}} from {{FROM_TIER}} to {{TO_TIER}}
<!-- Lane-owned template (source: lab H-DRAFT-d5a8d9b6-routing-derive, ported by intent).
     scripts/routing-derive.py's `propose` verb fills these four placeholders and writes the
     result to candidate-spec.md; a filled body is never registered by this script (Method:
     "as a draft hypothesis spec, and never as a table edit"). {{CLASS}} names the routing
     CLASS this repository's ledger and table key on (mechanical/execute/think/adversarial),
     not an individual role -- every role sharing that class moves together. Byte-identical
     for the same candidate + template across processes: no date, no fragment id embedded
     here -- those are stamped at registration, not by this step. -->

## Status
draft <!-- draft-then-allocate; a human or the hypothesis skill allocates H-NNN at land -->
Claim type: normative

## Hypothesis
Routing class `{{CLASS}}` moves from tier `{{FROM_TIER}}` to tier `{{TO_TIER}}` without a
quality regression, at a measured saving of `${{SAVING_USD}}` over the trailing window.

## Variable under test
Exactly one: the `{{CLASS}}` class's model tier -- `{{FROM_TIER}}` (incumbent) vs.
`{{TO_TIER}}` (candidate), everything else constant. Every role mapped to `{{CLASS}}` in
`rules/routing-default.json` (or `.claude/routing.json`) moves together; this is not a
per-role change.

## Method
A/B: every role in class `{{CLASS}}` runs on `{{FROM_TIER}}` for the incumbent arm and on
`{{TO_TIER}}` for the candidate arm over the same task set; outcomes graded pass/fail per the
frozen stopping rule (`rules/lineage-sprt.json`), pooled as a matched-pair stream.

## Binary assertions
1. The candidate arm's matched-pair stream reaches a stopping-rule terminal (promote or hold).
2. The candidate arm's per-task outcome is graded blind to which tier served it.

## Verdict rule
Keep (adopt `{{TO_TIER}}` for class `{{CLASS}}`) iff the matched-pair stream reads
evidence-sufficient promote; hold at `{{FROM_TIER}}` iff it reads evidence-sufficient hold.
