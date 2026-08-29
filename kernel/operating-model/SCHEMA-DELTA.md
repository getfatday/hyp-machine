# SCHEMA-DELTA — this shipped copy vs its source of record

`SCHEMA.md` in this directory is the plugin's travel copy of the node grammar. Its source of
record lives in the laboratory repository that develops hyp; this file records exactly how
the shipped copy differs, so a reader comparing the two sees deliberate deltas, not drift.

## Deltas in this copy

1. **De-housing.** Source-repository example ids, experiment citations, and internal
   research links are replaced with neutral equivalents or plain-prose conclusions. The
   grammar itself — node kinds, frontmatter keys, relational keys, lint classes — is
   unchanged.
2. **Evidence phrasing.** Where the source cites specific experiment runs for a rule (the
   relational-key additions were ratified through two converging blind-graded extraction
   runs), the shipped copy states the conclusion without the source repository's run
   numbering.

## The Event Modeling layer (additive)

The `grammar/` directory layers a reconciled Event Modeling metamodel over this schema:
interface and stream node kinds, reified placements/flows/slices in the board
serialization, the four-pattern slice grammar, and GWT cases per slice. Those additions are
documented in `grammar/em-metamodel.md` and mapped key-by-key to this schema in
`grammar/schema-to-em.md`; none of them rewrites an existing SCHEMA key (every proposal is
additive). The board conformance rules live in `grammar/slice-board.md` and are checked
mechanically by `scripts/em-slice-lint.py`.
