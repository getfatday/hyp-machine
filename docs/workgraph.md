# Work-graph dispatch and session durability

A Claude session is disposable: it compresses, hits a cap, or the machine reboots. The
work is not. This kit makes the WORK the durable object — which item is open, who holds
it, what finishes it — all computed from committed bytes, so any session (first, resumed,
post-compaction, another user's, or a scheduled firing at 3am) re-derives its position
instead of remembering it.

Five mechanisms, each proven in the source lab before shipping:

| Mechanism | What it does | Tool |
|---|---|---|
| Ranked next-item dispatch | Names the single top open item at session boundaries; sessions may not "just end" while committed work is open | `scripts/dispatch-status.py` + the Stop-boundary dispatcher hook |
| Claim TTL + takeover | Two writers never collide: a lane claim is live while its heartbeat is fresh (ttl_s = 1800); taking over a stale lane requires an attributed record committed BEFORE any lane write | `scripts/lane-takeover.py` |
| K-strikes quarantine | A lane that fails K=2 consecutive runs stops burning budget: it leaves dispatch behind a decision card only a human ruling can close | `scripts/dispatch-gate.py` |
| Reboot-surviving resume | A launchd timer refires after any reboot, reads the dispatch, and adopts at most one orphaned item per capped firing | `scripts/hyp-resume.sh` + `scripts/install-resume-timer.sh` + `scripts/resume-prompt.md` |
| Compression-surviving re-hydration | A per-effort work-graph file plus a resume protocol carries dependencies and completion across context loss | `scripts/graph-check.py` + the `/hyp:durability-check` skill + the protocol below |

## The dispatch surface

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch-status.py" [--json] [--at <sha>]
```

Repo root: `--root`, then `CLAUDE_PROJECT_DIR`, then the cwd. Requires the experiments
profile's directories (`hypotheses/`, a runs dir); with no hypotheses directory the
dispatch is empty by construction.

An item is OPEN until its committed exit artifact — the spec's line-initial `## Status`
verdict (kept/discarded) — is visible at HEAD. **The close rule is committed-only**: the
working tree never counts, so no promise string and no uncommitted edit can close an
item. That empty-dispatch check is the shared exit condition for every driver layer:
the Stop hook, detached chains, cold re-readers, and scheduled resume firings all ask
the same question of the same bytes.

The open list is then joined against two live surfaces:

- **Claim join**: each lane's `<runs_dir>/<id>/LANE-STATE.json` (committed schema
  fields only — `heartbeat_unix` age against `ttl_s`, never file mtime, never a process
  table). Fresh-claimed items are SKIPPED (printed under `SKIP`, never dispatched);
  stale-claimed items surface again as actionable. Concurrent readers are steered to
  disjoint unclaimed items at the list, not at a lock.
- **Orphan join** (this host only): an open lane whose LANE-STATE says `state=running`
  but whose recorded pid is DEAD is an ORPHAN — the post-reboot class — and stays
  actionable with a recovery verb (`land-terminal` when a nonzero chain terminal was
  written before death, else `relaunch`). A dead pid trumps a fresh heartbeat, so
  recovery never waits out the TTL. A running lane whose pid is alive with a fresh
  heartbeat prints as `LIVE` and is never dispatched.

## The Stop-boundary dispatcher (hook)

At every Stop, `hooks/scripts/stop-dispatch.py` (experiments profile only) runs the
dispatch. Non-empty dispatch with cap headroom blocks the stop once per cycle (exit 2)
and re-presents the TOP item by name with the exit rule; empty dispatch allows with
reason `artifact-check-pass`. Frozen caps bound it: 12 cycles or 1800 s per session
lineage, then it allows with `cap-headroom-exhausted`. Kill-switch: `touch
.claude/stop-snooze` silences it for 24 h. It never crashes a session — every internal
error allows the stop and logs a traceback; every invocation appends one JSON line to
`.claude/stop-driver/hook-log.jsonl` (the exit-honesty audit trail).

## The claim door

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane-takeover.py" --lane H-042 --executor <you>
```

The ONLY lawful path into another executor's lane. Fresh heartbeat (age <= ttl_s):
typed refusal, exit 3, ZERO writes inside the lane — re-run the dispatch and take the
new top item instead (the refusal-then-re-dispatch rule, binding on every driver
layer). Expired heartbeat: grant — and the grant commits the attributed takeover record
(`takeovers/<lane>-takeover.json`: new executor, prior executor, heartbeat age, reason)
BEFORE the first write inside the lane, enforced by git parent-child commit order.
LANE-STATE.json is the liveness surface; the recorded pid is provenance, never a
decision input. Add `takeover-outcome.json` (the per-invocation decision scratch at the
repo root) to your `.gitignore`.

## The relaunch governor

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch-gate.py" dispatch          # ranked actionable lanes
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch-gate.py" request <lane>   # exit 0 permit / 3 deny
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch-gate.py" ingest <lane>    # post-terminal bookkeeping
```

Every relaunch consults the gate. `ingest` recomputes the lane's consecutive non-green
terminal streak from its committed `chain-terminal.run*` artifacts; at K=2 strikes it
appends and commits ONE quarantine decision row (`type: needs-maintainer`, `reason:
k-strikes-quarantine`) through the decision store's own primitives — so the card renders
on the DASHBOARD "DECISIONS WAITING" surface and in `decisions.html` with no compiler
change, and the proactive opener surfaces it once. A quarantined lane leaves every
dispatch list and every consult denies it until a human ruling closes the row
(`python3 scripts/decisions.py resolve DEC-NNN --accept "relaunch"` after landing the
fix, or a retire ruling that also lands the spec-status change). Whether a twice-failed
lane burns another run budget is a human call, never the machinery's.

## The scheduled resume (macOS)

```
cd <your-repo>
bash "$CLAUDE_PLUGIN_ROOT/scripts/install-resume-timer.sh" ~/Library/LaunchAgents
launchctl load ~/Library/LaunchAgents/com.hyp.<repo>-resume.plist
```

The installer only EMITS the plist — loading it is your separate, deliberate act. Once
loaded, launchd refires every 1800 s and at every boot (`RunAtLoad`): each firing runs
the dispatch read, then at most ONE capped headless invocation (`--max-turns 40`,
`--max-budget-usd 1.50`, sonnet — the validated firing caps) driven by
`scripts/resume-prompt.md`, which binds the firing to the claim protocol, the LIVE-lane
prohibition, one-adoption-per-firing, and a containment rule (no nested claude, no new
timers, no writes outside the repo). Headless auth: the invocation sources an env file
exporting `CLAUDE_CODE_OAUTH_TOKEN` (default `~/.claude/hyp-oauth-token.env`, override
with `HYP_OAUTH_ENV` at emission time; mint with `claude setup-token`) in the same shell
invocation — never ambient, never committed.

## The work-graph and the re-hydration protocol

For a multi-step effort, keep ONE tracked graph file per effort at
`ledger/graphs/<effort-slug>.md`: frontmatter (`effort`, `state: open|closed`,
`invariants`) plus a step table — `id | task | needs | produces | status | evidence |
claim`. The load-bearing rules: **done is evidenced, never declared** (a step is done
iff its `produces` paths exist and match the recorded sha; claimed-done without
evidence is a false-done, treated as open); `needs` edges make "what blocks what"
queryable; the frontier (open steps whose needs are all done-evidenced) is recomputed
at every load, never trusted from a cached pointer. The graph is a living tracked file
— commit-amended, never write-once; steps are never deleted, only status-transitioned
or superseded.

The checker:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/graph-check.py" [--json] [--claim-ttl-days 3]
```

Report-only (exit 0 always), read-only, stdlib. Sweeps `ledger/graphs/*.md` (or takes
explicit paths) and derives, per graph, from disk alone: the recomputed frontier,
false-dones (produces missing on disk or sha-mismatched), stale downstream re-run
candidates, dangling refs and needs cycles, a stamped `next-dispatch` that disagrees
with the recomputed frontier, and expired step claims (window `--claim-ttl-days`,
default 3 days). The row grammar and frontier rule are the counted H-231 grader's,
generalized past the fixture's step ids; because it writes nothing it is safe inside
the protocol's observe phase.

After ANY context loss (compaction, a fresh session picking up the effort), the
re-hydration protocol, in this exact order:

1. **LOAD** the work-graph. Treat any compaction summary as an untrusted cache hint;
   the graph plus the disk is the source of truth.
2. **VERIFY (observe-only)**: for every step the graph records done, check its
   `produces` path exists and its sha matches the recorded evidence. Until the
   assert-back note is written, only observe — no file writes, no redirection, no step
   execution.
3. **ASSERT BACK**: write one resume note stating the state FROM THE GRAPH, the
   dependencies as recorded, and one line per mismatch between what the graph claims
   and what is on disk (`MISMATCH: <step> <what is wrong>`, or `MISMATCH: none`). A
   false-done is listed as a mismatch and reopened.
4. **DISPATCH BY THE GRAPH, not by memory**: recompute the frontier (false-dones
   first), execute in that order, and keep the graph current as you work (status +
   evidence sha committed after each step).
5. **COMPLETION CONTRACT**: the session ends only when the graph frontier is empty or
   a FAILURE record exists. Never stop at a checkpoint or a next-dispatch marker.

The operational walk — the counted observe-phase command allowlist, the exact
resume-note shape (`## State` / `## Dependencies` / `## Mismatches` with `MISMATCH:`
lines), and the effect-graded mutation rule — is the `/hyp:durability-check` skill
(`skills/durability-check/SKILL.md`), which also runs the checker as its mechanical
step 0.

## Evidence (source lab, counted keeps)

- **H-213 stop-driver-unattended** (kept 2x 5/5, 2026-08-29): the verdict-gated Stop
  dispatcher — ending a cycle only by committed artifact check, never a promise string —
  drove unattended sessions to land committed exit artifacts.
- **H-230 boundary-ranked-dispatch-v2** (kept 2x 5/5, 2026-08-30): boundary surfaces
  that NAME the top ready item with its derivation convert cold sessions where unranked
  oldest-first invitations measurably do not; the naming window is the measured
  distribution's worst case (<= 8 tool calls, observed p100 = 7). Successor to H-176
  (discarded 3/3 on the guessed <= 3 window — the discard purchased the constant the
  keep runs on).
- **H-215 claim-join-two-writers** (kept 2x 5/5, 2026-08-29): the fresh-heartbeat
  filter plus the refusal-then-re-dispatch rule steered two concurrent writers to
  disjoint items with zero double-adoption.
- **H-216 ttl-steal** (kept 2x 5/5, 2026-08-29): TTL-gated takeover with the attributed
  record-before-write commit order; LANE-STATE.json becomes the liveness surface,
  superseding prose claims and pid inference.
- **H-217 reboot-relaunch** (kept 2x 5/5, 2026-08-29): the orphan join (dead pid on
  this host = actionable with a recovery verb) plus the linted RunAtLoad timer plist
  turned a reboot from a silent work-killer into at most one deferred firing.
- **H-218 k-strikes-quarantine** (kept 2x 5/5, 2026-08-29): at K=2 consecutive
  non-green terminals the gate quarantined the lane behind one committed decision row
  and denied every relaunch consult until a human ruling.
- **H-231 workgraph-compression-survival-v2** (kept 2x 5/5, 2026-08-30): across a
  forced compaction boundary the work-graph + re-hydration protocol recalled 100% of
  seeded dependency edges, completed all steps done-evidenced, re-ran exactly the
  invalidated set after an upstream mutation, and flagged the seeded false-done before
  dispatch — where the summary-only baseline lost edges or left stale steps. Successor
  to H-162 (discarded 3/3: correctness survived, the observe-allowlist letter and
  completion did not — this version's effect-graded observe rule and completion
  contract are those two measured fixes).

## Port deviations (what changed from the lab installs, and what did not ship)

Ported with paths/config adaptation only — decision logic, joins, caps, constants
(TTL=1800 s, K=2, cycle cap 12, firing caps), exit codes, and row schemas unchanged.
Named adaptations: item enumeration in `dispatch-status.py` reads the consumer's own
hypotheses corpus instead of the lab's release-train wave plan; the quarantine row type
is `needs-maintainer` (lab: `needs-ian`); resume tooling drops the lab's plugin
toggles and parameterizes the auth env file. `graph-check.py` and the
`/hyp:durability-check` skill, deferred at 0.2.0, ship as of H-231's On-keep landing
(2026-09-01): the checker is byte-identical to the lab install (`scripts/graph-check.py`
both sides), and the skill carries the counted fixture protocol text verbatim.
Deliberately NOT shipped: the lab's release-train reader (wave plans are lab
infrastructure), and SessionStart ranked injection (H-230's resolver half — lands with
a follow-on tranche; the Stop-boundary half shipped here is the half already live in
the lab).
