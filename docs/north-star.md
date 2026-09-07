# The destination map: north-star files, run checkpoints, the progress view

## What it does

A north-star file is one tracked Markdown file per destination, `ledger/north-stars/<slug>.md`:
a one-line destination (at most 25 words), a `reached-when:` list, and a table of conditions.
Each condition is one clause of what must be true, bound to exactly one resolver — a
hypothesis, a decision, a research capture, or a probe — through the same closes-when grammar
the rest of hyp uses (`hypothesis-kept=H-NNN`, `hypothesis-verdict=H-NNN`,
`decision-resolved=DEC-NNN`, `path-exists=...`). The defining constraint is that the file
stores no status. `scripts/north-star-check.py` derives every condition's state at the commit
you ask about — done, open, retired, or unbound — from the committed resolver state, so a
verdict landing in a spec is all it takes to move the north-star file, and nobody ever edits that
file to record it. A `needs` column expresses prerequisites, including outcome-conditioned ones
(`C-03:yes`): when a prerequisite resolves the other way, the dependent condition retires itself,
with zero edits.

Two compilers turn committed bytes into pages a human can open:

| Script | Page | Source bytes |
|---|---|---|
| `scripts/compile-run-checkpoint.py <run-dir>` | `run-checkpoint.html` beside one counted run's `grade.txt` | results.json, grade.txt, verdict.json, the spec's assertions |
| `scripts/compile-north-star-progress.py <north-star-file>` | `north-star-progress.html` beside the north-star file | the north-star file and its resolvers at every sampled commit |

Both are projections: deterministic, byte-identical on recompile, regenerable after deletion,
and `--check`-able. Neither computes a number the sources do not carry.

## When to reach for it

Reach for a north-star file when work spans more than one hypothesis and the question "what is
still between us and the destination?" keeps being answered from memory. Bind each condition as
you register the spec (one table row), then let verdicts move it. Use `hypothesis-kept` when
only a keep satisfies the condition and `hypothesis-verdict` when a discard answers the
question too.

The boundary against its siblings:

| You want | Use |
|---|---|
| The ranked next item to work on right now, across the whole corpus | `scripts/dispatch-status.py` and the Stop dispatcher (`docs/workgraph.md`) |
| A dependency graph of lanes with claims and false-done detection | `ledger/graphs/*.md` + `scripts/graph-check.py` (`docs/workgraph.md`) |
| One destination, the conditions that reach it, and how far away it is | a north-star file + `scripts/north-star-check.py` |
| An openable record of one counted run for a reader who will not open the run directory | `scripts/compile-run-checkpoint.py` |
| A "where are we" page for one destination, with replay over time | `scripts/compile-north-star-progress.py` |

Do not put a vision paragraph in a north-star file. The lab banked prose steering as a null
(H-245); the file is structure, and the `--strict` lint refuses an authored status column
(STATUS-STORED) precisely so the structure cannot drift into a status report.

The run-checkpoint compiler is advisory in the grade leg: call it after `grade.txt` lands; a
refusal (exit 10-15) or a crash never blocks the grade or the journal fragment. The refusal is
information — it means the spec and the run disagree about the assertion count, the tally, the
budget line, a linked file, or a number the page would have had to invent.

## Common questions

**Why does the file store nothing?** Because an authored status column is a second copy of
the verdict and the two copies can disagree. In the lab's counted run the authored baseline
did not drift over nine events — at a small corpus a careful session keeps up — so the keep
was not bought with drift numbers. It was bought with zero maintenance writes and a status
that is by construction the verdict it derives from.

**A hypothesis was refined into a successor. Do I edit the row?** No. The checker follows a
`refined-into: H-MMM` Status pointer to the effective resolver. The row stays bound to the
original id. (`closes_when.py`'s own `hypothesis-verdict` predicate does not follow lineage —
it answers for the named spec only; the checker is the lineage-aware reader.)

**What is the frontier, and what is distance?** The frontier is every open condition whose
needs are all satisfied, each with a verb (`register` when the spec is absent, `run` when it is
present, `add` / `resolve` for decisions, `capture`, `probe`). Distance is the largest count of
open-or-unbound conditions on any `needs` path into a `reached-when` condition — how many
things must still happen in sequence, not how many rows are open.

**The progress page says stale but nothing about the destination changed.** Any new commit
stales the page, because HEAD is always a stop. `--check` is a freshness check on the digest,
not a change detector; recompile and the page is fresh again with identical content bytes.

**The progress compiler says `north-star file ... absent at <sha>`.** Without `--stops` the
compiler samples every commit whose subject matches `KEPT|DISCARDED|decision:`, and in an
established corpus most of those predate the file. Pass `--stops <file>` naming commits at
which the file exists (`git log --format=%H -- ledger/north-stars/<slug>.md` is the list);
HEAD is always appended. The lab's own first page was compiled this way.

**Can I compile a checkpoint for a run that was graded before this release?** Yes, if the run
directory has results.json, grade.txt and verdict.json and the spec is reachable
(`<root>/hypotheses/<LANE>-*.md`, or `--spec`). The lab's first real checkpoints were compiled
for runs graded days earlier.

**Does init scaffold an example north-star file?** No. It writes `ledger/north-stars/README.md`
(the convention) and nothing else; the first real file comes from `templates/north-star.md`
when there is a real destination to point at.

## It is working if

- `python3 scripts/north-star-check.py --strict` exits 0 on your tree, and exits 1 the moment
  you add a `status` column, a dangling `C-NN`, or a `needs` cycle.
- A verdict landing in a spec's `## Status` changes the derived vector on the next read without
  any edit to the north-star file (`git log -- ledger/north-stars/` shows only rows added, never
  status changed).
- `python3 scripts/north-star-check.py --at <older-sha>` reproduces the vector the north-star
  file had at that commit, byte for byte in `--json`.
- After a discard, every condition that needed `C-NN:yes` on that hypothesis reads
  `retired:C-NN` and the distance drops — with no file edit.
- `run-checkpoint.html` opens from the file system, every link on it resolves, every number on
  it is findable in results.json or grade.txt, and deleting and recompiling it gives the same
  sha256.
- `compile-north-star-progress.py --check north-star-progress.html` exits 0 right after a
  compile and 1 after the next commit; three compiles in a row give one sha256.
- `python3 scripts/north-star-check.py --selftest`, `compile-run-checkpoint.py --selftest`,
  `compile-north-star-progress.py --selftest` and `closes_when.py --selftest` all exit 0 on the
  installed copy.

Provenance: the three keeps of the source lab's destination-map wave (2026-09-04, journal
fragments 0259 and 0260): `H-DRAFT-2cae0933-derived-condition-status` (kept 2x 5/5),
`H-DRAFT-2cae0933-run-checkpoint-fidelity` (kept 2x 5/5),
`H-DRAFT-2cae0933-north-star-progress-view` (kept 2x 5/5); the lab's own north-star file for
the wave was the first real north-star file and its progress page the first real "where are we"
view. The four-section frame of this page (what it does / when to reach for it / common questions
/ it is working if) is the docs-page pattern the lab's pattern census kept as a gap worth
adopting from an external repository; the terms here are the lab's own (north-star file,
condition, frontier, horizon, excluded, retired, distance, checkpoint).
