---
bump: patch
---

The next session start now shows a short digest of what the background operating-model worker
last found for your checkout: up to eight lines prefixed `OM-FEEDBACK: `, the first saying how
old the reading is, and nothing at all when there is nothing to show yet. The lines never carry
raw session content -- a line that would leak a transcript field or a path is replaced with
`<held: 1 line>` before it prints. Why: lab hypothesis `H-DRAFT-3aef12a5-om-startup-reading-surface`
(kept 2026-09-15, `VERDICT.json`, five counted looks, A1-A5 passing in every one). What to do
after upgrading: nothing. How to undo: revert the release's merge commit.
