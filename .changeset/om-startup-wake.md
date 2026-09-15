---
bump: minor
---

Every session start now wakes the passive operating-model feedback worker in the background, so
the previous session's row lands in your checkout's ledger (`ledger/om-feedback.jsonl`) with no
command of yours and no added foreground interpreter start: `hooks.json`'s existing `SessionStart`
resolver row gains one `--also om-worker '...'` clause that writes a single pointer for the prior
session and forks `scripts/om-worker.py drain` as a detached, background-priority process under
its own lock. Two linked worktrees of one repository each land their own session's row in their
own ledger, never the other's. Caveat: under heavy host load the row can arrive one session-start
boundary late (never lost, never duplicated) -- see `docs/passive-feedback.md`, "The startup wake",
for the measured margins. Evidence: lab `H-DRAFT-10383178-om-startup-wake`, kept 2026-09-15 (five
counted looks, every assertion passing in every one; `VERDICT.json`, `VERIFY.md`, journal fragment
0547). After upgrading: nothing to do. To undo: revert the merge, or remove the `--also om-worker`
clause from the resolver row in your own `hooks/hooks.json` if you have forked it.
