---
bump: minor
---
`scripts/id-collision-lint.py` reports hypothesis ids shared by more than one spec file and journal-fragment ids used more than once, and harden-check surfaces the counts as ADVISORY-35 `id-collisions: spec=N fragment=M`. On a 256-spec consumer that mints ids with its own next-free counter it reads spec=57 fragment=131; on a repository that lands ids through the plugin's draft-then-allocate gate it stays silent. Lab H-DRAFT-6ec1691d-consumer-id-collision-advisory, kept 5/5 in two consecutive runs (the second cold); consumer gap G4.
