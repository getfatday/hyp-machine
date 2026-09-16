---
bump: minor
---

Adds `scripts/om-integrate.py` and the `integrate` skill: a probe tells you which background
mechanism your machine and repository actually support for the passive operating-model feedback
loop (a launchd job on a Mac, GitHub Actions via the already-shipped `om-ci.py` on any host with
`gh`), sets one up with a `test` and a one-command `uninstall` (whose dry-run prints the exact
`launchctl unload ... && rm ~/Library/LaunchAgents/<label>` reversal of the one disclosed
activation step), and never activates anything by itself -- every claim is the exit code of a command it actually ran, and `emit` only ever prints
the activation command it did not run. Nine candidate handles are probed for real
(`launchd-queue`, `systemd-user`, `cron-anacron`, `schtasks-idle`, `desktop-task`,
`hook-oneshot`, `ci-tier0`, `ampersand`, `routine`); rows land in `ledger/om-substrates.jsonl`
(`merge=union` via `/hyp:init`, `.claude/hyp.json` `om_substrates_file` overrides the path). Why:
the lab keep `H-DRAFT-e2a5e911-om-integrate-probe` (2026-09-15, five counted looks, A1-A5 pass in
every one). What to do after upgrading: run the `integrate` skill when you want the passive loop
to run itself instead of by hand. How to undo: revert this merge, or run
`python3 scripts/om-integrate.py uninstall --root .` first if you already emitted something.
