---
bump: minor
---

harden-check gains ADVISORY-34 branch-without-pr: when the session's HEAD is a non-default branch at least HARDEN_PR_MIN (default 1) commits ahead of origin's default branch and gh reports no open pull request for it, one advisory line names the branch, the ahead count and the `gh pr create --draft` command; silent on the default branch, detached HEAD, without gh, or with HARDEN_PR_CHECK=0; the gh call is pinned to the remote owner's account first and says so when pull requests cannot be read instead of hiding. Motivation: 52 pushed commits sat on a lab worktree branch for two days with no PR while sibling branches had drafts — "pushed" was green, review was blind (lab experiments/runs/DESIGN-durability-gaps/research/why-no-pr.md; H-DRAFT-ee81f74d-branch-without-pr-advisory). Ships with scripts/selftest-branch-without-pr.py.
