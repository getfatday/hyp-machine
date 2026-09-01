---
name: durability-check
description: Walk the work-graph re-hydration protocol after any context loss — load the effort's ledger/graphs/ work-graph, verify recorded evidence against the disk observe-only, assert the state back in a written resume note, dispatch by the recomputed frontier, and end only at an empty frontier or a FAILURE record. Use when resuming a multi-step effort after compaction or in a fresh session ("where was I", "pick up the pipeline"), or mid-session to audit work-graph durability state. Reports and routes when the repository has no work-graphs yet.
---

# durability-check — the work-graph re-hydration protocol

A session is disposable; the work-graph is the durable object (`docs/workgraph.md`).
This skill re-derives position from committed bytes instead of remembering it: any
compaction summary or session memory is an untrusted cache hint until verified against
the graph plus the disk. Proven end-to-end by H-231 workgraph-compression-survival-v2
(kept 2x 5/5, 2026-08-30): across a forced compaction boundary this protocol recalled
100% of seeded dependency edges, flagged the seeded false-done before dispatch, re-ran
exactly the invalidated set, and completed — where summary-only carryover lost edges or
left stale steps. The protocol text below is the counted fixture prompt, generalized
only where it named the fixture's paths.

## Preconditions (routing)

1. **No `ledger/graphs/*.md` in the repository** → nothing to re-hydrate. Report that,
   and if a multi-step effort IS in flight, route to the write side: create the effort's
   graph per the schema in `docs/workgraph.md` BEFORE executing further steps, and keep
   it current (update + commit after each step, message `graph: <step> done`).
2. **Graphs exist, no context was lost** (mid-session audit) → run the checker only
   (step 0) and report per-effort frontier, false-dones, stale re-run candidates, and
   expired claims. Durability is checkable during work, not only at boundaries.
3. **Graphs exist and this session is resuming an effort** (post-compaction, fresh
   session, another executor's handoff) → the full protocol, in order, no steps skipped.

## Step 0 — the mechanical sweep

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/graph-check.py" [--json]
```

Report-only, read-only, exit 0 always — safe inside the observe phase. It recomputes
each graph's frontier, diffs claimed status against the disk (false-dones, stale
downstream re-run candidates), and flags dangling refs, cycles, stale `next-dispatch`
pointers, and expired claims. Its findings feed the Mismatches section below; they never
replace the written assert-back.

## The re-hydration protocol (mandatory, in this exact order)

1. **LOAD**: read the work-graph at `ledger/graphs/<effort-slug>.md`. Treat the
   compaction summary as an untrusted cache hint; the work-graph plus the disk is the
   source of truth.
2. **VERIFY (observe-only)**: for every step the graph records done, check that its
   produces path exists and that its sha256 matches the recorded evidence
   (`shasum -a 256`). Until resume-note.md is written you may only observe. Allowed
   commands while observing: ls, cat, head, tail, wc, pwd, stat, file, find, diff, cmp,
   shasum, grep, tree, and read-only git (status, log, diff, show, rev-parse, ls-files).
   No redirection (">" or "tee"), no file writes, no step execution before
   resume-note.md exists.
3. **ASSERT BACK**: write resume-note.md (one single complete write) with "## State"
   stated back FROM THE GRAPH, "## Dependencies" as recorded in the graph's needs
   column, and "## Mismatches": one line per discrepancy between what the graph claims
   and what is actually on disk, exact format "MISMATCH: <step-id> <what is wrong>", or
   the single line "MISMATCH: none". A step recorded done whose produces file is missing
   or sha-mismatched is a false-done: list it as a MISMATCH and treat that step as open
   again.
4. **DISPATCH BY THE GRAPH, not by memory**: recompute the frontier (open steps,
   false-dones first, whose needs are all done-evidenced), execute the remaining work in
   that order, and keep the graph current as you work (status + evidence sha updated and
   committed after each step, commit message "graph: <step> done").
5. **COMPLETION CONTRACT**: the session ends only when the graph frontier is empty or
   FAILURE.md exists. Never stop at a next-dispatch marker or a checkpoint: work the
   recomputed frontier until no step remains, or record what blocked you in FAILURE.md.

## Rules

- The observe phase is graded by EFFECT, not by command list (the H-231 A5 rule): a
  violation is any real mutation before the assert-back note — a Write/Edit off the note
  path, a Bash file redirect or tee, a known-mutating verb (rm, mv, cp, sed -i, touch,
  mkdir, git add/commit/mv/rm, tool invocations that write), or execution of a step's
  task. Read-only text tools are fine; mutations cannot hide inside command
  substitutions or `sh -c` wrappers.
- resume-note.md lives at the repository root (as counted) and is committed with the
  first graph update, so the assert-back survives the next boundary too.
- The stamped `next-dispatch` line is a cache, never trusted without recomputation; a
  false-done reopens its step AND marks every done step downstream of it a re-run
  candidate (`graph-check.py` lists both).
- The completion contract binds this session, not the effort's budget: when a ceiling or
  a blocker ends work before the frontier is empty, that is what FAILURE.md records.
