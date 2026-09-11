# Lane containment: one shared, lane-scoped instrument types a run's isolation as a void class

`scripts/lane_containment.py` is the one containment instrument every lane driver imports. It
reads before/after inventories of exactly two roots -- the lane directory
(`experiments/runs/<lane>/`) and the run's declared scratch root -- and nothing else is ever
walked; it derives declared writes from the run's own write audit and accepts no declared-writes
list; it records sibling-lane paths and denied read probes as covariates that change no clause; and
it types its reading as a **run-validity void class**, never a counted assertion. A containment
reading says whether a run may be counted, not whether the hypothesis passed: a numbered assertion
about containment is a template error (the comment in `templates/HYPOTHESIS-TEMPLATE.md`, Method).

The shipped file is byte-identical to the lab-kept instrument, sha256
`d7d9b339c8f82e12c94a9083fe5e946da7e953df2c6a4fa9d8165400862a79df` -- the `instrument_sha256`
every kept report carries, and the value `scripts/selftest-lane-containment.py` pins. Evidence:
source lab `cause-n-effect`, lane `H-DRAFT-c2b572ab-lane-containment-instrument-v2` (refine
successor of `H-DRAFT-e280a73f`, same bytes), kept 2026-09-11 at 5/5 in two consecutive counted
runs, the second by a cold executor from the fixture README alone with a fresh seed set: 84/84
blind seeded plants reported in their own class (12 per class over V1-V4, N1, N2, C, both runs);
0 findings over the 16 recorded runs replayed from committed evidence (the one `void` is a
crash-lost record, typed `ambiguous`); the four historical false failures re-graded exactly as
sealed; every one of the five bespoke per-lane implementations fails as a control; two passes
byte-identical. Zero LLM calls, US$0, about 2.5 s per run; journal fragment 0488.

## Why lane-scoped

Four recorded runs in the source lab were graded as containment failures by bespoke per-lane code
and were not: a whole-tree `git status` diff read 646 untracked paths of four *sibling* lanes as
this lane's leaks; a declared-list diff read 411 bookkeeping files (`lineage-states/*.jsonl`) as
undeclared because the committed audit carried counts, not paths; a strict outside-cwd read clause
read 3 *denied* Read probes (no bytes returned) as forbidden reads; a record lost to a crash read as
a violation. Under the lane-scoped law each of those is a covariate or a typed void, never a
finding -- and the same law catches every planted out-of-lane write, undeclared write, key-marker
leak and successful forbidden read. Whole-tree diffs and per-lane containment code are ruled out
by the keep; "leak" is not the name for a sibling lane's untracked path, a denied probe, a
bookkeeping gap or a crash-lost record.

## The five clauses and the class law

| clause | `finding` when | `not-evaluable` when | covariates recorded (never a finding) |
|---|---|---|---|
| `write_scope` | an **audited** write by this run's processes to a path outside the lane directory and every declared scratch root (`out_of_lane_writes`) | never: with no write audit nothing is a write | `sibling_lane_paths` `{covariate, n, by_lane}` -- a new path outside the roots that no audited process of this run wrote (a whole-tree diff would call it a leak) |
| `declared` | a new in-lane path no `open_w`, no `note_dir` directory and no ingested transcript covers (`undeclared_writes`) | the run carries no path-level write audit; new in-lane paths are then `unaudited_new_paths` | `missing_declared` (declared, never materialised), `declared_by {open_w, note_dir, transcript}` counts |
| `read_scope` | a **successful** read of a forbidden path (`forbidden_reads_succeeded`) | -- | `read_probes [{path, succeeded, allowed}]` -- a denied probe (a `permission_denials` entry that returned no bytes) is recorded with `succeeded: false` |
| `key_marker` | a lane-qualified `<lane>/fixture/keys` path, or a registered key basename (`holdout-key`, `expected-live-echo`, `judge-referent`, `seed-manifest`, `expected-historical`, or the `key_markers` given), inside an arm-visible file (`key_marker_hits [{file, marker}]`; case-insensitive) | -- | a bare, unqualified `fixture/keys` mention is not a hit |
| `record` | never | an artifact the run's evidence names is missing or does not parse (`required_missing`); in live mode the record is complete once `before.json`, `after.json` and `events.jsonl` exist | `record_complete` |

**Class law.** `class` is `violation` iff any clause is `finding` (exit 10); `void` iff no clause
is `finding` and the record clause is `not-evaluable` (exit 11); else `clean` (exit 0). `void_class`
names the run-validity type the reading induces:

| reading | `void_class` | meaning |
|---|---|---|
| a write finding (`out_of_lane_writes` or `undeclared_writes` non-empty) | `ambiguous` | attribution unclear -- re-take the run, never impute |
| a read or key finding (`forbidden_reads_succeeded` or `key_marker_hits` non-empty, no write finding) | `annulled` | the arm saw the key; the question answered was not the one posed |
| no finding, record `not-evaluable` | `ambiguous` | an incomplete record: truncated events, absent after-inventory, a crash record |
| `clean` | `null` | counted |

Sibling paths and denied probes never change any clause or the class; the exit code and the
`class` field never encode a covariate, so a downstream grader cannot re-create the whole-tree
defect one layer up. `not_evaluable[]` lists the clauses without evidence.

## Declared writes come from the audit, never from a list

The instrument accepts no declared-writes list -- not from an evidence map, not from driver
source, not typed by a person. A path is declared only by a record the run itself produced:

- `Session.open_w(path, mode)` -- an audited write, declared by construction (the `write` event is
  appended and fsync'd before the file is opened; parent directories are created);
- `Session.note_dir(path)` -- declares a directory: every path beneath it is declared. Accepted only
  for a directory whose creation this session audited through `Session.makedirs`; on the lane
  root, the scratch root, or an existing directory the session did not create it is a usage error
  (`SystemExit(2)`);
- an ingested transcript (`Session.ingest_transcript`): a child session's `Write`, `Edit`,
  `MultiEdit`, `NotebookEdit` paths become declared writes (`declared_by: transcript`) and its
  `Read`, `Glob`, `Grep`, `LS` paths become read probes, denied when the same tool and path appear
  under `permission_denials`;
- the process-level audit shim (`Session.child_env`): a `sitecustomize.py` plus
  `LANE_CONTAINMENT_AUDIT_LOG` in the child's environment log every `open()` (mode, path),
  `listdir`, `scandir`, `walk` and `Popen`; `close()` ingests the log -- writing opens become
  declared writes, reads become probes;
- in `replay`, the committed records only: `events.jsonl` write/`note_dir` rows (`write_audit.type
  = events`), a full per-path declaration + added-paths audit (`inventory_full`), or an outside
  list with counts (`counts_outside`, which evaluates `write_scope` and leaves `declared`
  not-evaluable).

Absent a path-level audit the declared clause reads `not-evaluable` and every new in-lane path is
an `unaudited_new_paths` covariate -- which is exactly how the 411 lineage-state files read.

## Interface (frozen at the keep)

```
lane_containment.py begin  --lane <lane-dir> --scratch <scratch-root> --record <record-dir>
                           [--allow-read <path>]... [--forbid-read <path>]... [--key-marker <token>]...
                           -> <record-dir>/before.json  (relpath, size, sha256 of every file under the lane
                              directory and the scratch root; nothing else is walked)
                           -> <record-dir>/config.json, <record-dir>/events.jsonl (append-only, fsync'd)
lane_containment.py end    --record <record-dir>   -> after.json, report.json;
                           prints "<class> <void_class> <clauses>"; exit 0 clean, 10 violation, 11 void, 2 usage
lane_containment.py replay --lane <evidence-root> --run <run-dir> --evidence <map.json> --out <dir>
                           (re-grades a recorded run from its committed evidence; identical grammar)
lane_containment.py selftest [--into DIR]   (plants one instance of every class; PASS/FAIL per check, 9 checks)
```

| `Session(lane, scratch, record, allow_read=(), forbid_read=(), key_markers=())` | |
|---|---|
| `.open_w(path, mode="w")` | an audited write, declared by construction; returns the open file |
| `.makedirs(path)` | creates a directory and records that this session created it (the precondition of `note_dir`) |
| `.note_dir(path)` | declares a directory this session created: every path beneath it is declared |
| `.child_env(env=None)` | a copy of `env` carrying the audit shim (`PYTHONPATH` + `LANE_CONTAINMENT_AUDIT_LOG`) for child processes |
| `.ingest_transcript(events, cwd)` | a child session's stream: Read/Glob/Grep/LS -> read probes (`succeeded` true/false), Write/Edit/MultiEdit/NotebookEdit -> declared writes |
| `.scan_arm_visible(path)` | registers a file the arm could see (a prompt, a brief) for the key-marker scan |
| `.event(kind, **fields)` | one synchronous, fsync'd append to `events.jsonl` |
| `.close()` | the `end` reading: ingests the child log, walks the after-inventory, writes `after.json` + `report.json`, returns the report dict |

`report.json`: `law: lane-scoped`; `instrument_sha256`; `lane`; `lane_dir`; `scratch_roots`;
`before_n`; `after_n`; `out_of_lane_writes[]`; `undeclared_writes[]`; `missing_declared[]`;
`unaudited_new_paths[]`; `declared_by{}`; `sibling_lane_paths{}`; `read_probes[]`;
`forbidden_reads_succeeded[]`; `key_marker_hits[]`; `clauses{write_scope, declared, read_scope,
key_marker, record}`; `not_evaluable[]`; `record_complete`; `required_missing[]`; `class`;
`void_class`. `forbid_read` entries are lane-relative (`fixture/keys/`) or absolute. Reports carry
no clock or pid; live-mode reports carry the scratch root as given (absolute when it lies outside
the repository) and `config.json`/`events.jsonl` carry absolute paths, replay reports carry
neither -- the counted lane's two passes were byte-identical over every replay report.

### Replay evidence map

`replay` reads a JSON map whose artifact paths are relative to `--lane`: `lane`, `lane_dir`
(default `experiments/runs/<lane>`), `scratch_roots[]`, `forbid_read[]`, `key_markers[]`,
`required_records[]` (each must exist; `.json` must parse), `write_audit {type: events |
inventory_full | counts_outside | none, file, at[], declared_files_field, declared_dirs_field,
added_field, outside_field}`, `inventory {type: status_diff | field_list | snapshot_diff, file,
at[], field | pre, post}` (`status_diff` strips the two-column porcelain prefix and takes the
rename target; `snapshot_diff` diffs `find -printf` style lines on the path column only),
`transcripts [{file, cwd}]`, `arm_visible [path | {file}]`. A bespoke record has no `after.json`
or `events.jsonl` and is judged on the artifacts its own README names.

## Adopting it in a lane driver

Import the shipped copy and run the whole pass inside one `Session`; every write the driver makes
goes through `open_w` or beneath a `makedirs` + `note_dir` directory, children get `child_env` or
have their transcripts ingested, and `close()` is the pass's last act so the after-inventory sees
every record:

```python
import os, subprocess, sys
sys.path.insert(0, os.path.join(os.environ["CLAUDE_PLUGIN_ROOT"], "scripts"))
import lane_containment as lc

lane = os.path.realpath("experiments/runs/H-NNN-slug")
scratch = os.path.realpath("/private/tmp/<scratch-root>/H-NNN-slug")
s = lc.Session(lane, scratch, os.path.join(lane, "run-1", "self"),
               forbid_read=("fixture/keys/",),
               key_markers=("H-NNN-slug/fixture/keys/seed-manifest.json",))
with s.open_w(os.path.join(lane, "run-1", "grade.json")) as fh:
    fh.write(grade_json)
on_dir = s.makedirs(os.path.join(lane, "run-1", "on")); s.note_dir(on_dir)
subprocess.run(child_cmd, env=s.child_env(), cwd=scratch, check=False)
s.ingest_transcript(child_stream_events, cwd=scratch)
report = s.close()          # record report["class"] / report["void_class"] in RUN-RECORD.json
```

**Rule B: define no containment function of your own.** The source lab's census pattern
`(?i)def \w*contain\w*\(|writes_outside|outside_(lane|cwd)|declared_diff|declared_files|WRITE_MANIFEST|lane_containment|containment_scan|def write_inventory|def audit_analysis`
over a lane's `fixture/**/*.py` must match only the import (`lane_containment`); anything else is a
second definition and re-creates the four false-failure classes one lane at a time. The adopting
lane records this in its fixture README (the keep's first-adopter row).

**Where the record directory goes, and the D2 covariate.** Put `--record` inside the lane
(`run-<N>/self/`, the counted layout) so the instrument's own records stay in the lane's
inventory. At the pinned sha `Session.close()` then lists its own `before.json`, `config.json` and
`events.jsonl` as `undeclared_writes` (they are written after the before-inventory and by no
`open_w`), so a clean pass reads `violation`/`ambiguous` on exactly that set -- lab v1 VERIFY.md
section 7, D2, reproduced in `scripts/selftest-lane-containment.py` (`live-clean-pass-d2-covariate`).
The counted lane treats it as a covariate: its validity is decided by a recount over the raw
`events.jsonl` write and read records with the record directory exempt from the declared test
(`self/verifier-recount.json`), never by `self/report.json`'s class. Do not work around it by
placing the record outside both roots (then the instrument writes where nothing audits) or by
appending a `note_dir` event for the record directory through `event()` (that bypasses the
audited-creation rule). Live mode is proven at the first adopter (the keep's On-keep row 3); a
change to the instrument's bytes ships as its own changeset with new evidence and moves the pin.

**Paths.** Give the instrument real paths (`os.path.realpath`): event paths spelled `/tmp/...` are
normalised to `/private/tmp/...` on macOS but the lane root is taken as given, so a lane reached
through the `/tmp` symlink reads its own writes as out-of-lane.

**Layout.** The instrument names the lane `experiments/runs/<lane>` in every report, derives the
repository root as three directories above the lane, and classifies sibling paths by that prefix
-- the plugin's default `runs_dir`. A consumer with another `runs_dir` is outside the kept
evidence.

**Budget.** Standard library, Python 3.9, one file, zero LLM calls; a live pass costs one hash walk
of the two roots before and after.

## Selftests

```
python3 scripts/lane_containment.py selftest [--into DIR]        # 9 checks: V1-V4, N1, N2, C, void, bare key mention
python3 scripts/selftest-lane-containment.py [--into DIR]         # the plugin's regression entry point
```

The wrapper runs the instrument's selftest, then eight further checks against the shipped file
from a throwaway `experiments/runs/<lane>` tree: the byte pin, the D2 covariate on a clean pass
with the record inside the lane, `clean` with it outside, an out-of-lane `open_w` typed
`ambiguous`, an ingested transcript's successful forbidden read typed `annulled` and its denied
twin recorded as a probe, `note_dir` refusing the roots, the CLI round trip (exit 0/10/2/2) and the
frozen interface names. Exit 0 only when every check passes.

## Provenance

Lab spec `hypotheses/H-DRAFT-c2b572ab-lane-containment-instrument-v2.md` (Method step 2 is the
frozen interface above; step 6 the self-application covariate); run artifacts
`experiments/runs/H-DRAFT-c2b572ab-lane-containment-instrument-v2/` (`VERDICT.json`, `VERIFY.md`,
`fixture/README.md`, the sealed `fixture/impl/lane_containment.py`); journal fragment 0488. The
v1 lane `H-DRAFT-e280a73f-lane-containment-instrument` authored the bytes; v2 changed only the
single-source clause's reading and the record-directory exemption.
