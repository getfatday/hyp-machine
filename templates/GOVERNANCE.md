# GOVERNANCE.md

Behavioral invariants for agent sessions in this repository. These are principles with reasons,
not a checklist — apply judgment at the boundary each one names.

## Invariants

**Durability.** When work is worth persisting — decisions, config, findings, code — it lands in
a committed artifact, because anything living only in a session transcript or an uncommitted
working tree effectively does not exist for the next cold session.

**Recoverability.** Prefer changes that are two-way doors: make edits through tracked files,
commit with attribution, and keep everything git-revertable — and treat permission controls as
boundaries to work within, not around — because a change someone can see, attribute, and undo is
a change the team can trust. An artifact flagged by a security control becomes canon only after
a maintainer confirms it. Attribution resolves through git — commit author, blame, CODEOWNERS —
never through personal names in artifact text: a name gives a cold reader nothing git does not
already give better, and in measured sessions escalation resolved reliably only where a
CODEOWNERS mapping existed to route it.

**Evidence.** When choosing between approaches, prefer a measurement or test over intuition —
and when a commit changes behavior or canon, its message links the committed evidence that backs
it (the finding, run artifact, or journal entry), so blame resolves to re-runnable evidence
rather than a bare assertion. A line challenged later is re-tested under current understanding,
not argued from memory: the linked evidence is the door that lets the decision evolve.

**Isolation.** Concurrent work — multiple agents, sessions, or experiments — happens in isolated
environments (worktrees, clones, scratch copies), and changes reach the shared line through
deliberate, attributed merges rather than by writing over a shared tree. A change that raced
another change has no trustworthy attribution, however good its commit message.

## Worked example: the significance threshold

A session flips one boolean in a config file to unblock a build — a one-line change that feels
too small to matter. It still gets committed, with a message saying why and linking what showed
it was needed. In observed sessions, ungoverned agents did not treat a small config tweak as
commit-worthy on their own; the change worked, evaporated, and the next session re-derived it
from scratch. If a change was worth making, it is worth committing — smallness is not an
exemption.

## Where the hard lines live

This document carries no hard rules, and that is deliberate. Non-negotiable boundaries belong in
mechanical guards — settings deny rules, hooks, and lints — which are deterministic, survive
cold sessions, and cannot be argued with mid-task. Those guards are themselves changed only
through attributed commits. If something here ever feels like it needs enforcement rather than
judgment, the fix is a guard, not stronger wording.

---
Provenance: distilled from a governance investigation run as controlled experiments — the
durability of committed config, wording over-triggering, rule-count and placement nulls, and a
significance-threshold measurement. The canonical copy of this file ships in the hyp
plugin's templates; installed copies are drift-checked against it.
