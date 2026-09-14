---
bump: patch
---
`scripts/om-worker.py drain` no longer loses a session's feedback row when the worktree it ran in
has since been removed: a pointer whose own `root` no longer exists now lands its row in a
per-repository outbox (`<state>/om/<repo-key>/outbox.jsonl`, `landed_in: outbox`) instead of the
pointer being quarantined, and the next `drain` of any live checkout of that same repository
carries the row into its own ledger (`landed_in: carried`, `carried_from`), renaming the outbox so
a second drain carries nothing twice; a pointer whose root exists but was never a git checkout of
its recorded `common_dir` still quarantines, unconditionally, as before. `templates/event-nodes/
session-observed.md` documents the new `landed_in` values and `carried_from`/`origin_root_key`;
`docs/passive-feedback.md` documents the pointer's optional `root`/`common_dir` fields and the
outbox and carry-forward contract; `scripts/selftest-om-worker.py` gains seven cases on a scratch
repository with two linked worktrees, including two drains racing the same outbox. Why: the lab
keep H-DRAFT-a4a14ff4-om-outbox-carry-forward (2026-09-14; five counted looks, every assertion
passing, cold-verified -- its `VERDICT.json` and journal fragment 0537 are the evidence) showed the
rule reaches every live checkout exactly once while the worker without it drops the row. After
upgrading: nothing to do -- no pointer carries `root`/`common_dir` yet (the startup wake lane,
H-DRAFT-10383178, writes them), so every drain still behaves exactly as before this release until
that lane keeps. Undo: revert the merge; rows already written are plain JSON lines, distinguishable
only by their `landed_in` value.
