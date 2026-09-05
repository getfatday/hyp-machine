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

### Many north-star files: the set block

A repository may carry many destinations (the lab's fixture is shaped like a portfolio of 22
strategies and 3 programs, each with its own owner). A plain `north-star-check.py` invocation
reads every committed `ledger/north-stars/*.md` in one pass; each file derives independently
and its per-file output is byte-unchanged. Whenever at least one file is read the checker also
emits a `set` block (a one-destination reduction while only one file exists) (`--json` top-level key `set`; in text, a trailing `set:` block), reduced
from the per-file blocks alone — no file gains a field and no authored order between files
exists. `union_frontier` lists each distinct effective lane once with the `<slug>#C-NN` pairs
it closes (`serves`), the count of binding files (`n_serves`), the smallest per-file distance
(`min_distance`) and a `claimed_fresh` flag, sorted by `n_serves` descending, `min_distance`
ascending, lane id — derived numbers only, so one shared piece of work is counted once and
ranked by how many destinations it advances. `shared_bounds` maps every lane bound in more
than one file to its pairs. `exit_strict_by_slug` gives each file its own exit bit: one
malformed sibling fails its own bit while the others still derive, and the flat `exit_strict`
keeps its meaning (any file, any hard finding).

### Cross-file needs

A `needs` token may name a sibling file's condition: `<slug>#C-NN`, `<slug>#C-NN:yes`,
`<slug>#C-NN:no`. It resolves against `ledger/north-stars/<slug>.md` committed at the same
commit (never the working tree), takes that condition's derived status and outcome, counts
in distance exactly as a local token does (continuing into the sibling's own chain), and
retires across the boundary with the boundary token as root (`retired:<slug>#C-NN`). Write a
shared prerequisite once, in its own file; never copy the row — a copy tracks the bound item
but goes stale the moment the sibling retires. Three lints extend across files: an unknown
slug or sibling id is `DANGLING-REF`, a cycle over the union of local and cross-file edges is
`CYCLE`, and two committed paths under `ledger/north-stars/` (recursive) sharing a basename is
`DUPLICATE-SLUG`, because the slug is the resolution key. A sibling with hard findings makes
the token derive `unbound` in the referencer, which still derives. Files without the token
are byte-unchanged in `--json`; a qualified need entry adds `slug` and `status`.

### The all-files read is hook-safe

The checker's git layer is batched: per commit, one `ls-tree -r` answers every existence
query and two `cat-file --batch` rounds fetch the north-star files, the work ledger, the
specs and the probe verdicts, instead of one subprocess per resolver path. On the lab's
25-file, 15-condition fixture the all-files read dropped from 69-78 s and 364 git invocations
to under 1.1 s and 4 invocations, with `--json`, text and `--strict` exit codes byte-identical
to the per-path reader, claim overlay included. That makes the read cheap enough for a
SessionStart hook; whether the session resolver surfaces the set block's one-line summary is
a maintainer call, not something this release does.

### Set-mode compile: `--all` and `--check --all`

`compile-north-star-progress.py --all [--repo R]` compiles every committed north-star file at
HEAD to `ledger/north-stars/<slug>.progress.html` (never to the shared
`north-star-progress.html`) plus `ledger/north-stars/index.html`, one row per destination
(distance, frontier size, reached, claimed count, shared lanes; `not derived` for slugs in
`set.not_derived`), every value copied from the checker's derivation at HEAD. The stop rule
is the single-file one, except that a default stop predating a file is skipped for that
file's page (HEAD is always kept). `--check --all` re-derives every expected page and the
index: exit 0 fresh; 1 stale, naming each page or the index that is missing, unreadable or
differs; 2 only for an unreadable repository (HEAD unresolvable or `ledger/north-stars/`
absent). The single-file positional path is unchanged byte for byte, so earlier pages still
check fresh.

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
| Which shared lane advances the most destinations, across every north-star file | the `set` block of `scripts/north-star-check.py --json` (`union_frontier`, first entry with `claimed_fresh` false) |
| One page per destination plus an index, checkable as a set | `scripts/compile-north-star-progress.py --all` / `--check --all` |

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
HEAD is always appended. The lab's own first page was compiled this way. In set mode
(`--all`) a default stop that predates a file is skipped for that file's page; the single-file
path keeps the explicit `--stops` rule.

**Two destinations both need the same hypothesis. Do I add the row twice?** Add it once, in
the file that owns it, and let the other file's `needs` cell name it as `<slug>#C-NN` (or
`<slug>#C-NN:yes`). The dependent file inherits the sibling's derived status and its retire
cascade with zero edits to either file; a copied row would go stale the moment the sibling
retires.

**A lane is on the frontier of three files. How many times does the set count it?** Once.
`union_frontier` keys on the effective lane, lists the three `<slug>#C-NN` pairs under
`serves`, and ranks it above a nearer lane that serves one destination (`n_serves` sorts
before `min_distance`). A fresh claim heartbeat sets `claimed_fresh` on that one entry in
every file that binds the lane; it never reorders or removes it.

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
- With two or more north-star files committed, `python3 scripts/north-star-check.py --json`
  carries a `set` block; a lane bound on the frontier of two files appears once in
  `union_frontier` with `n_serves` 2, and two invocations (or a clone) give identical bytes.
- Adding a `status` column to one file flips only that slug's bit in `exit_strict_by_slug`;
  the sibling files still derive.
- A condition that needs `<slug>#C-NN:yes` reads `retired:<slug>#C-NN` the commit after the
  sibling's hypothesis discards, with no edit to either file; a nested copy
  `ledger/north-stars/team-a/<slug>.md` makes `--strict` exit 1 with `DUPLICATE-SLUG`.
- The all-files read at 25 files finishes in seconds, not minutes (`time python3
  scripts/north-star-check.py --json >/dev/null`).
- `compile-north-star-progress.py --all` twice gives one sha256 per page and for `index.html`;
  `--check --all` exits 0, then 1 naming exactly the one page you truncate, then 0 after a
  recompile.
- `python3 scripts/north-star-check.py --selftest` (115 checks), `compile-run-checkpoint.py
  --selftest`, `compile-north-star-progress.py --selftest` (23 checks) and `closes_when.py
  --selftest` (19 checks) all exit 0 on the installed copy.

Provenance: the three keeps of the source lab's destination-map wave (2026-09-04, journal
fragments 0259 and 0260): `H-DRAFT-2cae0933-derived-condition-status` (kept 2x 5/5),
`H-DRAFT-2cae0933-run-checkpoint-fidelity` (kept 2x 5/5),
`H-DRAFT-2cae0933-north-star-progress-view` (kept 2x 5/5); the lab's own north-star file for
the wave was the first real north-star file and its progress page the first real "where are we"
view. The many-north-stars wave (2026-09-05, journal fragments 0275 and 0276) added the set
block, the cross-file `needs` grammar, the batched all-files read and set-mode compile, four
keeps each at 5/5 twice with a cold-context executor on run 2 and zero LLM calls in any arm:
`H-DRAFT-d6a0a6ef-north-star-set-union-frontier`, `H-DRAFT-d6a0a6ef-north-star-set-cross-file-needs`,
`H-DRAFT-d6a0a6ef-north-star-set-batched-reads`, `H-DRAFT-d6a0a6ef-north-star-set-progress-index`;
every fixture was synthesized from the shape of a portfolio repository that stayed read-only.
The four-section frame of this page (what it does / when to reach for it / common questions
/ it is working if) is the docs-page pattern the lab's pattern census kept as a gap worth
adopting from an external repository; the terms here are the lab's own (north-star file,
condition, frontier, horizon, excluded, retired, distance, checkpoint).
