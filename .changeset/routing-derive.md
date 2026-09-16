---
bump: minor
---
Adds a model-routing derive loop: `scripts/routing-derive.py` reads a repository's own
`ledger/routing-ledger.jsonl` and, on a bounded run-completed cadence
(`hooks/scripts/routing-derive-cadence.py`, a synchronous `Stop` hook run right after the
existing routing ledger writer), proposes at most one rule-conformant routing-tier change per
run as a draft hypothesis spec — never as an automatic table edit. `scripts/compile-
dashboard.py` gains a `## 4. ROUTING` section compiled from the new `routing-report.md`,
present only once that file exists. Ships `rules/routing-derive.json` (frozen the same way
`rules/lineage-sprt.json` is, sibling copy plus a hardcoded sha) and
`scripts/selftest-routing-derive.py`. Source: lab hypothesis `H-DRAFT-d5a8d9b6-routing-derive`
(`VERDICT.json`: evidence-sufficient promote, five counted looks 5/5). After upgrading:
nothing — the loop is fully inert until your ledger has rows, and a candidate is never applied
automatically. To undo: revert this merge, or remove the `Stop` hook row for
`routing-derive-cadence.py` in `hooks/hooks.json` to stop the cadence alone.
