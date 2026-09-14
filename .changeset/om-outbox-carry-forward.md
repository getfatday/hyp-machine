---
bump: patch
---
`scripts/om-worker.py drain` no longer loses a session's feedback row when the worktree it ran in
has since been removed: a pointer whose own `root` no longer exists now lands its row in a
per-repository outbox (`<state>/om/<repo-key>/outbox.jsonl`, `landed_in: outbox`, keyed by the
pointer's own recorded `common_dir`) instead of the pointer being quarantined, and the next
`drain` of any live checkout of that same repository carries the row into its own ledger
(`landed_in: carried`, `carried_from`) under a per-inbox `flock` that lets only one drain claim
and carry an outbox at a time, renaming it (`outbox.jsonl` -> `outbox.<epoch>.carrying.jsonl` ->
`outbox.<epoch>.carried.jsonl`) so a second drain -- or a second live checkout racing the first --
carries nothing twice; a pointer whose root exists but was never a git checkout of its recorded
`common_dir`, whose `root` is null/empty/non-string, or that carries no `common_dir` at all
(whether or not its `root` still exists) still quarantines unconditionally.
`templates/event-nodes/session-observed.md` documents the new `landed_in` values and
`carried_from`/`origin_root_key`; `docs/passive-feedback.md` documents the pointer's optional
`root`/`common_dir` fields, the outbox and carry-forward contract, and the default inbox
location's change (below); `scripts/selftest-om-worker.py` gains nine cases on a scratch
repository with two linked worktrees, including two DIFFERENT live checkouts racing a 400-row
outbox and a deterministic race between a carry and the missing-root write path itself. Why: the
lab keep H-DRAFT-a4a14ff4-om-outbox-carry-forward (2026-09-14; five counted looks, every
assertion passing, cold-verified -- its `VERDICT.json` and journal fragment 0537 are the evidence)
showed the rule reaches every live checkout exactly once while the worker without it drops the
row; a cold-refuter round on the ship itself then caught a concurrent-carry defect (two DIFFERENT
checkouts could both carry the full outbox) the lane's own looks never exercised, fixed with the
lock above. A second cold-refuter round then caught the missing-root write path itself never
taking that same lock before its append -- a write already open on `outbox.jsonl` when a
concurrent carry renames it away could land inside the `.carrying.jsonl` the carrier already read
and be finalized away unread; the write path now blocks on the same per-`inbox_root`
`.outbox-carry.lock` before appending. A third round caught a pointer with a dead `root` and no
`common_dir` slipping past the documented quarantine into the DRAINING repository's outbox, from
where the next drain carried it into a ledger the pointer never named; the `common_dir` test now
runs before the existence test, so a pointer that cannot prove its repository quarantines either
way, and a carry the target ledger refuses leaves its claim for the next drain instead of
finalizing the unread row away. A fourth round, run on a loaded host, caught two more: a `git
rev-parse` timeout (5 s) or a git that could not be run was read as "not a checkout", so live
worktrees' pointers quarantined `NotAGitCheckout` under load and a default-location drain fell
back silently to the previous release's inbox and found nothing -- the worker now tells "git did
not answer" apart from "not a checkout", defers such a pointer to the next wake (result key
`deferred`) and refuses the whole drain with one stderr line when it cannot resolve its own
`--root`; and two wakes on the now-shared per-repository inbox raced on `processing/`, one wake's
crash-recovery sweep re-queuing the other's in-flight pointer (processed twice, plus a false
quarantine row) -- the sweep, rotation, carry and pointer loop now run under a per-inbox
`.drain.lock`, the second wake backing off with one stderr line. A claim resumed by a different
checkout now dedupes against the first carrier's ledger too (`.roots` sidecar), and the drain
result JSON carries the same keys on every exit. After upgrading: no pointer carries `root`/`common_dir` yet (the startup wake
lane, H-DRAFT-10383178, writes them), so every pointer that IS found still lands exactly as
before -- BUT for any `root` that is itself a live git checkout, `drain` with no `--inbox`
override now reads a DIFFERENT default inbox directory than the release before this one
(`docs/passive-feedback.md`, "Running it by hand"); a pointer hand-written straight into the old
default path needs moving, or an explicit `--inbox` naming it. Undo: revert the merge; rows
already written are plain JSON lines, distinguishable only by their `landed_in` value.
