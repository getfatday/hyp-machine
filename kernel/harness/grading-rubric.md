# Grading rubric (FROZEN)

This rubric is frozen; graders apply it verbatim. Changes require a new verified revision,
never an in-place edit.

## The referent rule (governs everything below)

An element is a *(type, evidence-pointer)* pair. **Grade referents, not labels.** Two elements
match when their evidence points to the same transcript behavior, regardless of naming. Phrasing,
synonyms, and coined names are never themselves errors.

## Classification of every analyst claim

1. **MATCH** — evidence overlaps a ground-truth element (any name, any phrasing) → counts as
   recovered.
2. **DISCOVERY** — a coined name whose evidence resolves to something real in the transcript that
   ground truth does not list → report in `discoveries`, never penalize. Extraction exists to
   find unregistered elements; discoveries are candidate nodes for human ratification.
3. **FABRICATION** — an evidence pointer that resolves to nothing in the transcript (an action,
   actor, policy, or fact that did not occur) → counts as invented. This is the only meaning of
   "invented."

## Mechanical checks (run, do not judge)

- Citation accuracy: run `validate_trace.py <trace.json> <transcript.jsonl>` (this directory) on any trace
  array; report the ACCURACY line and BAD lines verbatim. Citation slips are reported, not counted
  as fabrications.

## Error accounting (for the non-inferiority assertion)

Per analyst: errors = missed ground-truth elements + missed seeded deviations (seeded transcripts
only) + fabrications. Discoveries and naming differences contribute zero.

## Symmetry

Apply identical standards to both executors. When uncertain whether a claim is a discovery or a
fabrication, verify against the transcript before deciding; if it cannot be resolved from the
transcript, classify as discovery and note the uncertainty.
