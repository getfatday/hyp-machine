---
name: integrate
description: Probe this machine and repository for a background mechanism that can drive the passive operating-model feedback loop (a launchd job on a Mac, GitHub Actions on any host with `gh`), set one up with a test and a one-command undo, and never activate anything without your say-so. Use when the user asks to turn on background feedback, wants the operating-model worker running automatically, asks "what can run this in the background", or wants to check/remove a previous install.
disable-model-invocation: true
---

# integrate — set up the passive feedback loop's background mechanism

Everything the `om-worker.py` passive feedback worker (`docs/passive-feedback.md`) needs to run
without a human remembering to invoke it is ONE deterministic script: `scripts/om-integrate.py`.
It never guesses your OS — every claim it makes is the exit code of a command it actually ran —
and it never turns anything on by itself: it only ever writes files and prints the one command
you would run to activate them.

## What it checks (the "handle table")

Nine candidate background mechanisms, in this frozen priority order (first probe-verified wins):

| Kind | Order | Handles |
|---|---|---|
| on-device | 1st match wins | `launchd-queue` (macOS), `systemd-user` (Linux), `schtasks-idle` (Windows), `desktop-task` (a login item — advertised only, never claimed usable), `cron-anacron` (deterministic tier only), `hook-oneshot` (currently unusable — a prior lab hold) |
| remote | 1st match wins | `ci-tier0` (GitHub Actions, delegated whole to `scripts/om-ci.py`), `ampersand`, `routine` (cloud schedule — no local probe exists) |

## Steps

1. **Probe.** From the repository root:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" probe --root . --json
   ```
   Runs all nine probes for real (`command -v`, `launchctl print`, `systemctl --user
   is-system-running`, `gh auth status`, ...) and appends the nine rows to
   `ledger/om-substrates.jsonl` (`.claude/hyp.json` `om_substrates_file` overrides the path).
   Read the rows back to the user in plain English: which mechanisms this machine actually has,
   not just which ones are installed. A `probe-void: <handle>:<void>` line means that one
   probe's own subprocess stalled or errored — read it as unresolved, never as "not usable".

2. **Compose.** (optional — `emit` runs this itself)
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" compose --root .
   ```
   Prints the one on-device handle and the one remote handle the priority order picks from the
   probe-verified (`usable`) rows alone — never from `authors_90d` or disk space, which ride the
   rows only as covariates.

3. **Emit.** Writes, but never loads or activates, whatever `compose` picked:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" emit --root . \
     --agents-dir ~/Library/LaunchAgents
   ```
   - `launchd-queue`: a `plutil`-linted plist at `<agents-dir>/com.hyp-machine.om-worker.<key>.plist`,
     `ProcessType Background`, watching the SAME inbox directory `om-worker.py`'s own `drain`
     reads by default. Not loaded. The command that would activate it is printed —
     `launchctl load <path>` — and is the user's own separate, deliberate act.
   - `ci-tier0`: delegated whole to `scripts/om-ci.py emit ci-tier0` (already shipped; see
     `docs/ci-scaffold.md`) — the workflow and its vendored dependencies land under
     `.github/workflows/om-check.yml` and `.github/om-scripts/`. Nothing here carries a second
     copy of that template.
   - Records the decision in `.claude/om-offload.lock.json` and sets `.claude/hyp.json`
     `om_offload` to the chosen handle. Re-running `emit` with nothing changed on the host is a
     byte-for-byte no-op.
   - Tell the user plainly: "nothing is running yet — this wrote the job description; loading it
     is a separate step you run yourself" and give them the exact printed command.

4. **Test** (optional, before or after loading). Exercises the worker integration end to end
   under a substrate that plays the launchd role without ever touching real launchd:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" test --root . \
     --transcript <any .jsonl session transcript, e.g. one of your own under ~/.claude/projects/>
   ```
   Reports `test: PASS` once one `session-observed` row lands from a real transcript AND two
   poison seeds (a non-JSON pointer, a format-shifted transcript) land as `quarantine` rows
   without spinning (at most 2 launches). `test` never itself loads a job into real launchd.

5. **Report drift.** Any time later, from any worktree:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" report --root .
   ```
   Prints `holds` when the recorded handle still probes usable, or `no longer holds: <handle>
   (propose: <new-handle>)` when it does not (a host capability changed) — plus a
   `mixed-installs:` line naming which of this repository's worktrees carry which plugin
   version, when they differ.

6. **Uninstall.** The one removal command, always available:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" uninstall --root . --dry-run
   ```
   Prints exactly what would be removed and the reversal commands (`launchctl unload ...` for a
   loaded plist, `git rm ...` for the workflow) without changing anything. Drop `--dry-run` to
   actually remove: the plist, the workflow and its vendored tree, the lock file, and the
   `om_offload` key — zero artifacts left behind.

## What this skill does not do

It never runs `launchctl load`, never enables a systemd unit, never pushes a commit, and never
calls an LLM. Every verb above is a deterministic, stdlib-only Python script; reading its output
back to the user in plain English is this skill's only job.
