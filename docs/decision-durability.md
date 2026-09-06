# Decision durability: retest-when predicates, run-page events, the shadow gate knob, evidence packets

## What it does

A decision or a rule that is parked on missing information can now say, in a committed field,
what evidence would reopen it — and the plugin emits that evidence at its choke points, a
consumer repository can send it, and the trigger fires from committed bytes without anyone
remembering a date. Four pieces, each proven in its own counted lane (source lab
`cause-n-effect`, branch `worktree-wayfinding-patterns`, journal fragments 0278 and 0280):

**Retest-when predicates replace dates.** `scripts/closes_when.py` (the shared close-condition
module; the only parser) gains a sibling family, `[retest-when: <predicate>=<argument>]` as a
bracket and `retest_when: "<predicate>=<argument>"` as a row field, with three predicates over
committed evidence streams:

| Predicate | Holds when (at HEAD only) |
|---|---|
| `event-count=<event id>[:<subject prefix>]>=N` | at least N rows of that event (distinct `caused-by`) are committed in `ledger/events.jsonl` |
| `metric-crosses=<metric id><op><threshold>@last=K` | the last K committed rows of that series in `ledger/metrics-timeseries.jsonl` all satisfy the comparison (a cross that reverts inside the window holds nothing) |
| `evidence-received=<target>` | a committed `research/raw/*-evidence-packet-<target>-*.json` exists |

`scripts/retest-trigger.py <root>` evaluates every active empirical rule's `retest_when` and
files exactly one `rule-retest` decision row (through `decisions.py add`) the first time the
predicate holds — deduplicated on `blocks: ["rule/<id>"]` across open and resolved rows, every
`context_pointers` entry a `<path>@<sha40>#La-Lb` span into the committed stream, the row's
date the HEAD commit's author date (no clock is read). Output is the rule-lint grammar
(`RETEST-DUE` / `RETEST-WHEN-MALFORMED`, tab-separated), exit 0 always; `--dry-run` files
nothing. `scripts/compile-laws.py lint-registry` accepts `retest_when` in place of `retest_by`
and reports a defect only when both are absent; `scripts/rule-lint.py` no longer flags a
date-less row as `RULE-EXPIRED` when it carries a well-formed `retest_when` (a malformed
predicate arms nothing and still fires).

**Decision rows with `retest_when`.** `scripts/decisions.py add` validates the optional field
through the shared parser (an unknown predicate is `ADD-INVALID` with one typed reason).
`decisions.py check` prints two exit-neutral report classes that never count as findings:
`DECISIONS-CHECK<TAB>RETEST-DUE<TAB><id><TAB><path>@<sha40>#L<n><TAB><predicate>=<argument>`
for an accepted or denied decision whose evidence has landed at HEAD, and
`DECISIONS-CHECK<TAB>REVISIT-UNARMED<TAB><id><TAB><field>` for a decision whose scanned text
says revisit or later while the row carries no trigger. `scripts/review-cadence.py` renders a
`RETEST DUE` block above `REVIEW DEBT` with the evidence pointer, impact first, never a date;
with nothing due the render is byte-identical to the shipped one (see `docs/review-cadence.md`).

**Run-page events and the shadow gate knob.** `scripts/checkpoint-shadow.sh <run-dir> --out
<page>` stands in for the grade leg's advisory compile line byte-for-byte on stdout and exit
status, then appends one `event/checkpoint-compiled` row whose payload is `{rc, class, lane,
run}` — `class` is the compiler's exit-table name for `rc` (0 `emitted`, 10-15 the typed
refusals, anything else `untyped`) and `scripts/events_lib.py` refuses a row whose class
contradicts its rc. The row goes through `scripts/emit-event.py checkpoint-compiled` when the
consumer's `.claude/hyp.json` names an `events_file` (profile gate and node validation as
shipped; node template `templates/event-nodes/checkpoint-compiled.md`), else the same canonical
line is appended, deduplicated on exact bytes, to `ledger/knob-signals/checkpoint-compiled.jsonl`.
The two lines a grade leg adds (the plugin has no chain template file to carry them):

```
export CHECKPOINT_EVENT_DATE="<the run's pinned YYYY-MM-DD>"
EVENTS_IMPL="$CLAUDE_PLUGIN_ROOT/scripts" COMPILER="$CLAUDE_PLUGIN_ROOT/scripts/compile-run-checkpoint.py" \
  "$CLAUDE_PLUGIN_ROOT/scripts/checkpoint-shadow.sh" "$RD" --out "$OUT"
```

(`EVENTS_IMPL` defaults to `../impl` beside the wrapper, the counted fixture's layout; in the
plugin the emitters live beside the wrapper in `scripts/`, so set it as shown.) The first
bounded knob consumes that stream: `templates/knob-nodes/checkpoint-gate-stance.md` declares
the signal, a 30-observation window, the ladder rule, per-class bounds `{advise, deny}`,
demote-on-first hysteresis, the actuator field, and three kill switches, at `mode: shadow`.
`scripts/knob-observe.py evaluate checkpoint-gate-stance` appends one state row to
`ledger/knob-state.jsonl` per new signal state — silence is a recorded state,
`evidence-insufficient n=k/30`, never an absent one — and files the advisory-to-deny decision
row only in `mode: recommend`, only at n=30, only when the licensing policy node
(`templates/policy-nodes/checkpoint-gate-license.md`, trigger
`event/checkpoint-gate-threshold-reached`, then `command/file-gate-decision`) resolves. Grammar
and verbs in `docs/knobs.md`.

**Consent-gated evidence packets and ingest.** `scripts/hyp-evidence-export.py --target
checkpoint-gate-stance` runs in a consumer repository and is gated by `.claude/hyp.json`
`evidence_export` (`off` | `packet` | `submit`; absent means `off`; init never seeds it on).
It projects the consumer's event stream through the LAB's committed allow-list
(`templates/exports/checkpoint-gate-stance.export-config.json`, committed by a lab as
`exports/<target>.export-config.json`): rows of undeclared event nodes vanish, payloads keep
only the listed keys, `subject` and `caused-by` pass only as declared enum values or as a
12-hex sha256 pseudonym, and the leak scan (`/Users/`, `/home/`, email-shaped tokens, forbidden
keys at any depth) refuses the write with exit 4. A packet is
`evidence-packets/<target>-<sha7>.json` (no date token: the retest-when pointer rule forbids
one). Lab-side, `scripts/evidence-ingest.py <pointer.json>` re-checks the packet against the
lab's own allow-list, verifies its sha256, and lands it exactly once as a write-once
`research/raw/<date>-evidence-packet-<target>-<repo_id>-<sha7>.json` plus one journal fragment
— the file `evidence-received=<target>` reads at HEAD. A second identical ingest prints
`already-ingested` and writes nothing.

## When to reach for it

Reach for a `retest_when` the moment a rule or a decision is being parked on information
rather than on time: "revisit after field feedback", "wait until we have thirty runs",
"reopen when the consumer sends numbers". Put the predicate on the row as you file it. Keep a
`retest_by` date only for a one-way-door hold whose reopening genuinely is calendar-bound.

Reach for a knob node when a setting is being flipped by feel between runs (a gate stance, a
threshold). Declare the signal, window, rule, bounds, and kill switch first, run it in `shadow`
until the state rows show what it would have set, and let `recommend` file the decision at the
declared sample size — never `act` (out of scope by design).

Reach for the packet pair when a consumer repository has evidence a lab is waiting on. The
consumer flips consent to `packet`, exports, commits the packet, and hands over a six-key
pointer; the lab ingests.

| You want | Use |
|---|---|
| a rule re-tested when a count, a metric hold, or a packet lands | `retest_when` on the registry row + `retest-trigger.py` |
| a decision re-presented when its waited-for evidence is committed | `retest_when` on the decision row; `decisions.py check` and `review-cadence.py` surface it |
| a rule re-tested on a calendar (one-way-door hold) | `retest_by` (unchanged) |
| one durable fact per run-page build | `checkpoint-shadow.sh` in the grade leg |
| a bounded setting moved from evidence, not intuition | a knob node + `knob-observe.py` (shadow first) |
| consumer evidence into a lab with names and paths never travelling | `hyp-evidence-export.py` then `evidence-ingest.py` |

## Common questions

**Why never a date?** The lab measured its own calendar before this shipped: 72 of 72 registry
retest triggers were dates, and a date fires whether or not the information it was waiting on
exists. A predicate fires exactly when the evidence is committed, and a resolved retest never
re-files (the three predicates are monotone over committed streams).

**The evidence is written but not committed. Does anything fire?** No. Every predicate reads
HEAD only; a staged packet or an uncommitted stream append is invisible (the lab's Durability
invariant). The counted lanes seeded exactly this and graded it silent.

**A metric crossed the threshold and came back. Did it fire?** No. `metric-crosses` needs the
last K committed rows to all satisfy the comparison at the moment of evaluation.

**What does shadow mode change in my repository?** State rows in `ledger/knob-state.jsonl`
and nothing else. The knob node's `action` values stay where they are; the rows record
`would_set` per class so the promotion decision arms itself from evidence when it comes.

**Does the wrapper change my grade leg's output?** No. Stdout and exit status are the advisory
line's, byte for byte (the 28-directory real corpus and six seeded corruptions were compared);
the row is appended with `|| true` and its notes go to stderr.

**What travels in a packet?** Only what the lab's allow-list names: event ids, listed payload
keys, enum values, verdict words, metric points. Subjects and causes become pseudonyms, free
text never leaves, and a seeded name, path, or email fails the scan by construction (the raw
copy of the same stream fails it as the positive control).

**Is the network pair wired?** Not in this release. `evidence-ingest.py` takes a pointer to a
local repository; the repository_dispatch sender and the lab workflow need a maintainer-minted
token and remain a maintainer ruling.

**Where is the resolver's `KNOB` line?** Not in this release; it is a hook change that rides
with the hook-parity lane. The state row grammar in `docs/knobs.md` is the contract — read the
latest row.

**Why do the plugin's `decisions.py` and `knob-observe.py` differ from the lab's copies?**
They are ports: the decision ledger path resolves through `.claude/hyp.json` `ledger_file`
(default `ledger/ledger.jsonl`) in both, so the evaluator and the decision kit can never
disagree on where decisions live. The `retest_when` delta was merged three-way onto the
`decisions.py` port (one selftest constant renamed); `knob-observe.py` differs by the
ledger-path resolver alone. Every other shipped script is byte-identical to its lab copy.

## It is working if

- `python3 scripts/closes_when.py --selftest` passes with the retest-when cases;
  `python3 scripts/retest-trigger.py --selftest` exits 0 — and exits 1 if you make a seeded
  uncommitted packet or a cross-then-revert series file a row.
- `python3 scripts/rule-lint.py --selftest` exits 0: of a date-less evidence-armed row, a
  date-less predicate-less row, a past-date row, and a malformed-predicate row, exactly the
  last three are `RULE-EXPIRED`.
- `python3 scripts/decisions.py --selftest` exits 0: `on-full-moon=...` is refused at `add`,
  an armed row is silent on an uncommitted append and prints one `RETEST-DUE` line after the
  evidence commit, and a "later" option with no trigger is `REVISIT-UNARMED` and exit-neutral.
- `python3 scripts/knob-observe.py --selftest` exits 0: 0 rows filed at n=29, exactly 1 at
  n=30, and each of the four seeded violations makes `check` exit 1.
- `python3 scripts/hyp-evidence-export.py --selftest` and `python3 scripts/evidence-ingest.py
  --selftest` exit 0: every bait row refuses the write; a flipped byte and a replayed pointer
  write nothing.
- A grade leg that calls `checkpoint-shadow.sh` prints the same stdout and exits the same as
  the advisory line, and the stream gains one row whose `class` is the exit-table name for its
  `rc`; running the leg again appends nothing.
- `python3 scripts/retest-trigger.py .` over a registry with no `retest_when` prints nothing
  and files nothing (the lab's real registry at ship time: 0 rows).
- The history of `ledger/` shows decision and state rows added, never a spec or a north-star
  file edited to record a retest.
