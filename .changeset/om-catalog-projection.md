---
bump: patch
---
The operating-model catalogue (`operating-model/<context>/model.md`) is now a regenerated file
git ignores instead of a hand-maintained, tracked one: two people adding nodes to the same
context on separate branches no longer conflict on it, because there is no longer a shared file
for their edits to collide on. Why: lab keep `H-DRAFT-4e06e157-om-rows-merge-shape` (`VERDICT.json`,
five counted looks, all assertions passing in every one) proved a tracked catalogue conflicts on
`git merge` in both edit orders while the untracked, regenerated shape merges cleanly in both
orders and a fresh clone renders the union. What to do after upgrading: run `/hyp:init` once — it
adds the `.gitignore` row and, if your repository still tracks a `model.md` from the old shape,
retires it from the index in your next commit (the work-tree file is untouched; if git declines
because a staged `model.md` edit differs from both HEAD and the work tree, init prints
`retire-refused` and leaves the file tracked for you to resolve); regenerate any
time with `scripts/compile-catalog.py operating-model/<context> --write`. `scripts/om-worker.py`'s
`evaluate`/`compile-check` now regenerate a tree before linting or date-stamping it, `compile-check`
failing closed (rc 1) if the renderer script is missing; `evaluate` records `renderer_found: false`
and exits 0. How to undo: revert the merge commit that added the ignore row
(and, if a retire already landed, `git add -f operating-model/<context>/model.md` to re-track the
current file).
