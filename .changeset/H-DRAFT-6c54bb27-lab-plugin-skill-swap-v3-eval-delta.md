---
bump: minor
---
`scripts/eval-delta.py` materializes a session's delta tree for `scripts/eval-grade.py`: every path the session added or modified relative to a base commit (working tree included, untracked-not-ignored included, deleted paths listed never copied, symlinks copied as links) copied out with paths preserved plus a JSON manifest, so a case file's graders read only what the session changed; `--selftest` builds a throwaway repository and checks the rule. Stdlib + git. Lab H-DRAFT-6c54bb27-lab-plugin-skill-swap-v3 (kept 5/5 in two consecutive runs, the second cold) graded eight headless sessions with it against the shipped scorer; the lab now runs the plugin's intake and hypothesis skills in place of its own (north-star condition C-06).
