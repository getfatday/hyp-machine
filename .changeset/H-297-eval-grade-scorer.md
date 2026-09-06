---
bump: minor
---
`scripts/eval-grade.py` scores a skill's `claude plugin eval` case files from their bytes alone: it reads each case.yaml with a stdlib parser, applies the deterministic graders (file_exists and tree-targeted regex) to a target tree, and prints one PASS/FAIL line per grader plus a CASE k/n line, so a consumer can measure its intake and hypothesis eval suites without the org-gated eval command or a live session. Lab H-297 (eval-grade-scorer), kept 5/5 in two consecutive runs: golden 15/0 on both sweeps, 8 cases n/n, the OFF path (`import run_cases`) fails with ModuleNotFoundError. First instrument of the lab-plugin convergence north star (C-06).
