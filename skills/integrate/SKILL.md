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

3. **Emit.** Writes, but never loads or activates, whatever `compose` picked. `--agents-dir`
   names where the plist is STAGED, not where it runs from — never point it at
   `~/Library/LaunchAgents` itself: launchd scans that directory at your next login (macOS 13+
   also registers it as a background login item), so a plist merely sitting there stops being
   "not activated" the moment you log in again. Stage it anywhere else, for example:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" emit --root . \
     --agents-dir .claude/om-staged-agents
   ```
   - `launchd-queue`: a `plutil`-linted plist at `<agents-dir>/com.hyp-machine.om-worker.<key>.plist`,
     `ProcessType Background`, watching the SAME inbox directory `om-worker.py`'s own `drain`
     reads by default. Staged, not loaded, and not inside `~/Library/LaunchAgents`. The ONE
     disclosed activation step, run only when and if you want this running: copy the staged
     plist into `~/Library/LaunchAgents/` and load it from there —
     `cp <agents-dir>/<label>.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/<label>.plist`.
   - `ci-tier0`: delegated whole to `scripts/om-ci.py emit ci-tier0` (already shipped; see
     `docs/ci-scaffold.md`) — the workflow and its vendored dependencies land under
     `.github/workflows/om-check.yml` and `.github/om-scripts/`. Nothing here carries a second
     copy of that template.
   - Records the decision in `.claude/om-offload.lock.json` and sets `.claude/hyp.json`
     `om_offload` to the chosen handle. Re-running `emit` with nothing changed on the host is a
     byte-for-byte no-op.
   - Tell the user plainly: "nothing is running yet — this wrote the job description; loading it
     is a separate step you run yourself" and give them the exact printed command.

4. **Test** is a disclosed gap for a direct consumer run today: it exercises the worker
   integration end to end against a substrate that plays the launchd role (a `launchctl watch`
   verb no real `launchctl` implements), so running
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" test --root . --transcript <path>`
   directly on your own host will report `test: FAIL` -- that failure is expected, not a bug in
   your setup. The integration this verb exercises is validated by the plugin's own cold
   selftest instead: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/selftest-om-integrate.py"`, which
   stages the stub substrate ahead of the real binary on `PATH` for the duration of that one
   check and reports the same `session-observed` / `quarantine` outcome this step describes.
   Skip straight to step 5 (report) on a real host.

5. **Report drift.** Any time later, from any worktree:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" report --root .
   ```
   Prints `holds` when the recorded handle still probes usable, or `no longer holds: <handle>
   (propose: <new-handle>)` when it does not (a host capability changed). Add
   `--installed-plugins <path>` to also get a `mixed-installs:` line naming which of this
   repository's worktrees carry which plugin version, when they differ: `<path>` names a JSON
   file YOU build, shaped `{"<absolute worktree path>": "<version>", ...}` — one entry per
   worktree you want checked. This is NOT the real Claude Code
   `~/.claude/plugins/installed_plugins.json` (a different shape this verb does not read
   directly); build the map yourself from whatever you already know about your worktrees'
   plugin versions.

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
