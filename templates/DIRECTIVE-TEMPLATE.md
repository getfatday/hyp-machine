# D-NNN-slug: <imperative title, e.g. "unify toolkit into plugin">
<!-- Copy to directives/D-NNN-<slug>.md. NNN monotonic at land-time. -->

## Status
open <!-- open | executed | closed | withdrawn. open -> executed only when the
verification record is complete; executed -> closed only when the journal fragment
exists. withdrawn requires a one-line reason. -->

## Ask
<!-- Pointer to the verbatim source, raw-first: research/raw/<file>.md. If the ask
arrived in-session and no raw file exists yet, file one via capture FIRST — this section
never paraphrases without a pointer. -->
- source: research/raw/<file>.md
- answers-ledger: <slug of the surfaced DIRECTIVE-LEDGER row, copied verbatim, or `none`
  if the net missed / no row exists — a D-doc without a hit is good behavior, not a gap>

## Restatement
<!-- The ask in the executor's own words: what changes, what must remain true afterward,
what is explicitly OUT of scope. 2-4 sentences. -->

## Affected nodes
<!-- Walked from operating-model/<context>/model.md via SCHEMA.md relational keys.
Recipe (deterministic, one hop):
  1. Seed: grep the catalog + node frontmatter for every noun the ask names
     (paths, commands, artifacts). List each seed node.
  2. Expand ONE hop along: command.reads / command.emits / command.handler,
     policy.trigger / policy.then / policy.mechanism, readmodel.projects-from /
     readmodel.consumed-by, event.emitted-by / event.consumed-by.
  3. One row per node reached: id, the key that reached it, what changes for it.
`none reachable` is a legal answer and is stated explicitly with the seed greps shown.
Judgment additions (nodes you know are affected beyond one hop) are welcome, marked
`judgment` in the Reached-via column. -->
| Node | Reached via | Impact |
|---|---|---|

## Acceptance assertions
<!-- 3-5 binary pass/fail checks declared BEFORE any mutation, each with the command or
method that will verify it. The only basis for calling this directive executed. For
move/merge/unify-class asks, include an inventory-parity assertion (pre-move file
inventory == post-move inventory at destination; use the move-fidelity manifest lint if
installed, else `git ls-files | diff` against a committed pre-move listing). -->
1.
2.
3.

## Verification record
<!-- Filled AFTER execution, BEFORE Status flips to executed: one row per assertion —
PASS/FAIL + an evidence pointer (command output path, commit sha, cmp/diff result).
Any FAIL: either repair and re-verify, or record the divergence explicitly with the
maintainer-visible reason (deferred-and-documented is defensible; silent is the failure
class this artifact exists to kill). -->
| # | Result | Evidence |
|---|---|---|

## On close
<!-- Machine-readable follow-ups, one commitment per line with a closes-when bracket.
Emitted into the work ledger at the executed flip; resurfaces every session until the
predicate satisfies against committed HEAD.
Predicates: path-exists=<path> | commit-grep=<needle> | hypothesis-kept=H-NNN |
maintainer-ruling=<slug>. Write '- none' if execution leaves no residue. -->
- none
