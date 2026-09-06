---
bump: minor
---
`drift-check` now compares the copies `init` installs into a repository (the preflight script, the hypothesis template, and `compile-journal.py`) against the plugin's canonical bytes and prints one advisory line per stale copy with the line delta and a review command; it never overwrites a consumer-owned file. On a 256-spec consumer it reports the preflight at 73 lines against the shipped 151 and the template at 49 against 79 where 0.4.0 printed `clean`; a fresh scaffold prints `clean`; a one-byte seed is reported by path; every other drift-check line is byte-identical. Lab H-DRAFT-4c0dadb8-installed-copy-drift, kept 5/5 in two consecutive runs (run 2 by a cold executor), cause-n-effect research/consumer-parity-gaps.md gap G6.
