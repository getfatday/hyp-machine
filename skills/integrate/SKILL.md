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
   rows only as covariates. If the repository has requested a model-calling tier's credential
   (`--tier`/`--credential-class`, or `.claude/hyp.json` `om_credential_tier`/
   `om_credential_class`) and its `authors_90d` census exceeds one, `compose` refuses a shared
   subscription token for that tier and prints three alternatives (`api-key`, `federation`,
   `platform-identity`) instead — see `docs/passive-feedback.md`, "Credential policy"; no shipped
   handle asks for one yet, so this is silent today.

   Before you emit, put two or three concrete options to the user with their tradeoffs, in plain
   English, built from what `compose` picked: (a) emit and run the on-device handle (for
   `launchd-queue`: the worker runs on this machine while you are logged in, nothing leaves the
   host, and you run the one activation command yourself); (b) emit and use only the remote handle
   (`ci-tier0`: the workflow runs in GitHub Actions on push and on its schedule, costs Actions
   minutes, and does nothing until you commit and push `.github/`; leave the staged plist
   unloaded); (c) install nothing and keep draining by hand with `om-worker.py drain` (zero
   footprint, nothing runs unless you run it). `emit` writes every artifact `compose` picked
   regardless; the choice is which activation step the user takes, and `uninstall` removes all of
   it either way.

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
     byte-for-byte no-op. When this host's answer HAS changed (a handle that composed last time no
     longer probes usable -- one failed `gh auth status` is enough), `emit` prints
     `no longer holds: <handle>` and keeps that handle's earlier artifacts in the lock, so
     `uninstall` still removes them; a lock that no longer parses is a typed
     `void: corrupt-json <path>` and `emit` writes nothing (exit 2).
   - `emit` also creates the worker's state root (`~/.hyp-state/om/<key>/`, or
     `$HYP_STATE_DIR/om/<key>/` when that variable is set) so the plist's `QueueDirectories` names
     a directory that exists. It is the worker's own inbox and log directory, shared with direct
     `drain` runs, so `uninstall` leaves it in place and names it in its reversal output.
   - The lock, the staged plist and the `test` verb's `.claude/om-state/` scratch all carry
     absolute paths of THIS machine: `/hyp:init` appends ignore rows for
     `.claude/om-offload.lock.json`, `.claude/om-staged-agents/` and `.claude/om-state/` to your
     `.gitignore` (`templates/gitignore`); until you have run it, expect them in `git status`.
     If `HYP_STATE_DIR` is set when you run `emit`, the plist bakes it in as
     `EnvironmentVariables`, so the worker launchd starts drains the same override directory
     `QueueDirectories` watches.
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
   A direct run leaves its seeds and logs under `.claude/om-state/` (`inbox/scn-session-0.json`,
   `inbox/poison-*.json`, `poison-transcript.jsonl`, `launch-log.*.jsonl`); step 6's `uninstall`
   removes that directory, lock or no lock.

5. **Report drift.** Any time later, from any worktree:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-integrate.py" report --root .
   ```
   Prints `holds` when the recorded handle still probes usable, or `no longer holds: <handle>
   (propose: <new-handle>)` when it does not (a host capability changed).
   `report` also checks that every absolute path the recorded plist's `ProgramArguments` names
   still exists, so a plugin-cache upgrade that removed the worker the plist points at reads
   `no longer holds: ... -- missing path: <p> (re-run emit)` instead of `holds`. A `usable: true`
   probe row whose recorded exit is not 0 is refused with one `om-integrate: refused <handle>`
   line and never counted (a probe that lies is not a probe). A lock, `.claude/hyp.json` or
   `--installed-plugins` file that does not parse prints `void: corrupt-json <path>`; `report`
   and `uninstall` then exit 2 and change nothing. Add
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
   Prints exactly what would be removed (every emitted file that still exists, the lock, the
   `.claude/om-state/` test scratch) and the reversal commands without changing anything. For
   a plist the reversal line is
   `launchctl unload ~/Library/LaunchAgents/<label> && rm ~/Library/LaunchAgents/<label>  # only if you ran the activation step; the staged copy is removed by this verb`
   — the staged copy under `--agents-dir` is removed by the verb itself; the `~/Library/LaunchAgents/`
   copy exists only if you ran step 3's activation command, and leaving it there would let
   launchd re-register the job at your next login, so run that line yourself. For the workflow
   the line is `git rm ...` (and push). Drop `--dry-run` to actually remove: the staged plist,
   the workflow and its vendored tree (plus the emptied `.github/workflows/` and `.github/`
   directories when the workflow was the only thing in them), the lock file, the
   `.claude/om-state/` test scratch, and the `om_offload` key — zero artifacts left behind.

## What this skill does not do

It never runs `launchctl load`, never enables a systemd unit, never pushes a commit, and never
calls an LLM. Every verb above is a deterministic, stdlib-only Python script; reading its output
back to the user in plain English is this skill's only job.
