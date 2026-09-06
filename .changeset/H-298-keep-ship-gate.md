---
bump: minor
---
`scripts/keep-ship-gate.py` reads every kept hypothesis from committed HEAD, joins the files its run changed against the plugin-shipped paths (the deploy tree in the source lab, or the plugin's own tree in a consumer), and prints one `KEEP-UNSHIPPED` line per keep that has no committed ship record (`experiments/runs/<id>/SHIP.md` carrying a `pr: <n>` line); `harden-check.sh` surfaces the count as ADVISORY-33 at every session start, so a keep that changes shipped bytes stays visible until it ships instead of depending on someone remembering. Lab H-298 (lab-plugin-keep-ships-gate), kept 5/5 in three consecutive runs, the last by a cold executor; north-star condition C-03 of the lab-plugin convergence program.
