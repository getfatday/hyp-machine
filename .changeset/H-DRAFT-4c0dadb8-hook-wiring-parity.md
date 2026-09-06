---
bump: minor
---
`scripts/hook-parity-check.py` normalizes a repository's `.claude/settings.json` hooks and a plugin's `hooks/hooks.json` into (event, matcher, guard) rows and prints one line per guard that runs on only one side; `harden-check.sh` gains ADVISORY-32 carrying the count. Measured against the source lab's own wiring, the released plugin shows 23 one-side-only rows, which is why lab and consumer sessions have been protected by different guards. Lab H-DRAFT-4c0dadb8-hook-wiring-parity, kept 5/5 in two consecutive runs after Amendment 1, cause-n-effect research/consumer-parity-gaps.md.
