---
bump: patch
---
A decision brief's six authored fields may now run up to 238 words together (glosses
included, labels excluded) instead of 120 -- about one minute of silent adult reading --
because measurement showed writers dropping required facts when held to the 120-word
ceiling. Also, the `routing-derive.py` module docstring now correctly describes that
`opus` and `fable` are no longer identically priced after the price-table reconciliation,
so a fable-to-opus one-step-down candidate can show a real nonzero saving (a
comment/doc correction only, no behavior change). Nothing to do after upgrading; revert
the merge to undo.
