---
bump: patch
---
`harden-check.sh`'s background cache refresh is single-flight per repository: a pid file under `.claude/` lets exactly one `--fresh` chain run at a time, a stale lock (dead or rewritten pid) is reclaimed, and the foreground output is unchanged. Four simultaneous session starts on a 35k-file consumer spawned four refresh chains in 0.9.0 (9 to 16 orphan chains were observed machine-wide, each running 17 to 23 minutes) and spawn exactly one now, with the cache still refreshed once. Lab H-DRAFT-c9ec26d9-harden-refresh-single-flight, kept 5/5 in two consecutive runs, the second by a cold executor; consumer gap G9 of the lab-plugin convergence program.
