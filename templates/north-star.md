# North star: <slug>

destination: <one line, at most 25 words: the state of the lab this file points at>
reached-when: C-01, C-02

## Conditions

| id | condition | resolver | bound | closes-when | needs |
|---|---|---|---|---|---|
| C-01 | <what must be true, one clause> | hypothesis | H-NNN | hypothesis-kept=H-NNN | - |
| C-02 | <what must be true, one clause> | decision | DEC-NNN | decision-resolved=DEC-NNN | C-01 |
| C-03 | <a condition that only matters if C-01 keeps> | capture | research/raw/<date>-<slug>.md | path-exists=research/raw/<date>-<slug>.md | C-01:yes |
| C-04 | <a condition not yet bound to a resolver> | probe | <lane> | - | - |

## Horizon
- Z-01 (YYYY-MM-DD): <an in-scope question not yet sharp enough to be a condition>

## Excluded
- X-01: <a terminal exclusion> — banks: H-NNN

<!--
Column rules (ledger/north-stars/README.md is canonical; scripts/north-star-check.py lints):
  id          C-NN, unique, ascending.
  condition   one clause, no status words; status is DERIVED, never stored (STATUS-STORED).
  resolver    hypothesis | decision | capture | probe -- exactly one per condition.
  bound       the resolver's item id: H-NNN / DEC-NNN / research/raw/<file> / <probe lane>.
  closes-when the shipped closes-when grammar binding that item: hypothesis-kept=H-NNN,
              hypothesis-verdict=H-NNN (kept OR discarded), decision-resolved=DEC-NNN,
              path-exists=research/raw/<file>, path-exists=experiments/runs/<lane>/VERDICT.json;
              `-` = unbound (counts in distance, never in the frontier).
  needs       `-`, or comma-separated C-NN (must be done) and C-NN:yes | C-NN:no
              (must resolve that way; the other outcome retires this row and its dependents);
              <slug>#C-NN[:yes|no] names a condition in the sibling file
              ledger/north-stars/<slug>.md at the same commit -- write a shared prerequisite
              once there, never copy its row here; retire root reads retired:<slug>#C-NN.
Derived at read time: done / open / retired:C-NN / unbound; frontier with verbs
(register / run / add / resolve / capture / probe); distance; reached. Delete this comment
in a real file.
-->
