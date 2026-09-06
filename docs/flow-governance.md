# Flow governance: the leak meter, the reflex sensor, and the rules registry

New in 0.3.0. This layer answers three questions a hypothesis-driven repo eventually
hits, each with a mechanical tool instead of a person noticing:

1. **Is work leaking?** Finished runs sit unread; the queue moves slower than it should.
   The flow-leak meter measures it and alarms early.
2. **Did anyone react?** An alarm nobody consumes is dead wiring. The reflex chain turns
   a breach into a bounded forensics record and a standing advisory until someone acts.
3. **Are our rules still true?** Standing rules accumulate and go stale. The rules
   registry types them, compiles them into their carriers, and lints them for expiry.

Everything here is advisory and read-only over your repo (exit 0, never a gate), ships
byte-identical to the source lab's live install, and runs on stdlib Python.

## The flow-leak meter

```
bash scripts/leak-status.sh          # live reading against the sealed constants
```

`scripts/leak-meter.py` is a stateless function of your git history and a pinned
constants file (`scripts/leak-meter-constants.json`): it reads committed
`chain-terminal.*` times (git commit times, never file mtimes — mtimes lie across
clones) and emits one `FLOW <STATE>` line — healthy, `BURN-SLOW`, or alarm states —
plus the numbers behind it. `leak-status.sh` builds the terminals manifest and runs the
meter; `scripts/harden-check.sh` surfaces a firing as ADVISORY-30.

Why trust it: in the source lab's counted keep (H-246, kept 2026-09-02, 2x5/5), the
meter replayed on three held-out slowdown episodes alarmed 227.8 / 1259.9 / 936.3
minutes BEFORE the recorded human catch in each, with 0 false alarms on 47 held-out
healthy ticks, and thresholds re-fit from a bare clone byte-matched the sealed
constants. The meter's first live reading in the lab correctly flagged a real
`BURN-SLOW` on install day.

## The reflex chain: breach -> autopsy -> decision -> consumption

Five tools, one pipeline, each independently runnable:

| Tool | Role | Keep |
|---|---|---|
| `scripts/reflex-check` | Cold sensor: evaluates breach predicates on a timer tick, fires edge-triggered (once per breach onset, deduped), appends to `.claude/reflex/invocations.jsonl` | H-251, kept 2026-09-02, 2x5/5 |
| `scripts/reflex-collect` | Bounded zero-LLM forensics collector, supervisor+worker: a dead worker still lands `failure-row.json` + `PARTIAL.md` — zero silent deaths | H-252, kept 2026-09-02, 2x5/5 |
| `scripts/reflex-surface` | Turns an autopsy into one decision row with verified anchors; standing advisories escalate at N=3 unconsumed surfacings | H-253, kept 2026-09-02, 2x4/4 |
| `scripts/reflex-consume` (`reflex-consume.py`) | The meter's mechanical consumer: any BURN/ALARM fire unconsumed for 30 minutes prints `CONSUMPTION-DUE` (ADVISORY-31 in harden-check) and lands one T0 incident row; `--record <fire-ts> --action "<what you did>"` closes the loop | throughput-floor patch, 2026-09-02 |
| `scripts/reflex-selftest` | Install-time end-to-end drill: seeded synthetic breaches must fire the sensor, run the collector, and land rows on THIS machine before an install reports healthy — report-only, run automatically by `init` | H-254, kept 2026-09-02, 2x4/4 |

Consumption is the load-bearing idea (measured in the lab: five real alarm fires drew
zero recorded reactions before this chain existed). Surfacing an incident row is NOT
consumption — only an action citing the fire (a commit, a lane, a decision) recorded via
`reflex-consume.py --record` stops the advisory.

The sensor's timer plist is EMITTED, never auto-loaded
(`bash scripts/install-reflex-timer.sh ~/Library/LaunchAgents`, then a deliberate
`launchctl load` by a human) — the same emitted-never-loaded law as the scheduled
resume. Until you load the timer, `reflex-selftest` honestly reports the timer-entry
wiring check as FAIL; that is expected and report-only.

`scripts/incident-anchors-lint.py` (H-252 kit) keeps autopsies honest: every claim line
in a collected incident must end with a checkable `[anchor: ...]` whose path/mtime or
command/output actually verifies.

## The rules registry: compiled laws, expiry, retest, license

Standing rules (the "always do X" lines in CLAUDE.md blocks, memory files, advisories)
become typed rows in a registry (`rules-registry.jsonl`), each carrying its license —
the committed artifact that authorized it — plus scope and an empirical/permanent class:

- `scripts/compile-laws.py` (H-247, kept 2026-09-02, 2x5/5) compiles registry rows into
  their carrier files as fenced LAWS blocks, round-trips them back for a byte
  comparison, and reports drift between the registry and what carriers actually say.
- `scripts/rule-lint.py` (H-248, kept 2026-09-02, 2x5/5) is the four-class currency
  lint: `RULE-EXPIRED` (an empirical rule past its retest-by date), unlicensed rules,
  scope creep (written scope wider than licensed), and carrier drift.
- A `RULE-EXPIRED` finding files a `rule-retest` decision (`scripts/decisions.py`,
  H-249, kept 2026-09-02, 2x5/5): the rule is re-tested under current conditions, never
  argued from memory — retest, re-license, or retire.
- A rule is retested when its `retest_by` expires OR its `retest_when` turns true: an
  empirical row may carry an evidence predicate (`event-count=...>=N`,
  `metric-crosses=...@last=K`, `evidence-received=<target>`; grammar in
  `scripts/closes_when.py`) in place of, or beside, the date. `scripts/retest-trigger.py`
  files the `rule-retest` decision the first time the predicate holds at committed HEAD,
  `compile-laws.py lint-registry` accepts the predicate as the alternative to the date, and
  `rule-lint.py` no longer expires a date-less row that carries a well-formed predicate
  (lab H-DRAFT-d564bb31-retest-when-predicates, kept 2x5/5; see
  `docs/decision-durability.md`).
- The license-join guard (`hooks/scripts/license-join-check.py`, H-250, kept
  2026-09-02, 2x5/5) is a PreToolUse ADVISORY on rule-carrier writes: adding a standing
  rule without a resolvable license prints one `RULE-LICENSE` line (and logs the fire);
  it never blocks.

Related direction hygiene: `scripts/direction-lint.py` (H-243, kept 2026-09-01, 2x5/5)
lints a direction-layer corpus (North Star paragraphs, provenance notes) for five
stale-reference and rename-drift classes.

## Supporting tool: move fidelity

`scripts/fidelity-manifest.py` (H-104 port) makes move/merge/consolidate changes
declare-then-verify: a manifest records every source sha256 and destination BEFORE the
move; `--verify` re-checks after and reports lost or altered bytes in the house
TAB-finding grammar. The reflex chain's verify-before-cite rule builds on it.

## Evidence

All keeps counted in the source lab as script-only lanes with byte-identical
double-grading: H-243 (2026-09-01, 2x5/5), H-246 (2026-09-02, 2x5/5), H-247 / H-248 /
H-249 / H-250 (2026-09-02, 2x5/5 each), H-251 / H-252 (2026-09-02, 2x5/5 each), H-253
(2026-09-02, 2x4/4), H-254 (2026-09-02, 2x4/4). The consumption tooling
(`reflex-consume.py`, ADVISORY-31) is the lab's throughput-floor patch of 2026-09-02,
measured against five real unconsumed alarm fires. Ports are byte-identical to the lab
install; the only consumer-facing wiring differences are named in this file.
