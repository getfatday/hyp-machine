# Draft-then-allocate ids

## Why

Two branches that each compute a hypothesis or fragment id as "highest existing + 1" from
their own view collide the moment both land: whichever lands second either overwrites a
binding or ships a duplicate. A consumer repository hit this directly (issue #4): concurrent
registration branches minted the same `H-NNN` and the same fragment id, and journal fragments
306/334/335 and specs H-194..H-206 ended up numbered per-branch instead of per-repository. The
lab's own concurrent-registration measurement (H-148, kept 2x5/5) reproduced the same collision
under "highest existing + 1" in 4 of 4 cohorts.

The fix is to stop minting the canonical number away from the place that can see the whole
repository. Off the default branch, nobody can see what else is about to land, so nobody
mints a canonical id there. The canonical id is allocated once, at land, by the one thing that
*can* see the whole repository at that moment: the merge itself.

## The rule

| | Hypothesis id | Fragment id |
|---|---|---|
| **Registering on the default branch and landing immediately** | `H-NNN`, NNN = one above the largest landed spec number (re-checked right before the write, since the lander is you, so mint-at-land collapses to this) | The next free integer, re-checked right before the write |
| **Registering on any other branch** (a feature branch, a clone, a headless registrar, anywhere the land is not immediate) | `H-DRAFT-<hash8>-<slug>.md`; hash8 = the first 8 hex characters of `sha256(spec body + branch name + UTC minute)` | The next free integer *as a draft claim*: always an integer, never a hash or handle, in filename and `id:` line alike |

A hypothesis id is never a bare guess and never invented: the handle stands in wherever the
canonical number would otherwise go, in the spec filename, the spec title, and every citation,
including any journal fragment that records the registration. A fragment id is always an
integer, on every branch, at every stage; a hash or a draft handle never appears in a fragment's
filename. Only the hypothesis side gets the `H-DRAFT-` treatment, because only the hypothesis id
is ever displayed as a short citable number a person types from memory, while a fragment is
filed by date order, not recalled by number.

## What the lander runs

Land the branch by bringing canon into it first (fast-forward or merge `main`, or whatever your
default branch is called, into the registration branch) so the gate can see both sides, then
run the id gate:

```
python3 scripts/id-rectify.py --repo . --base main --head <branch>          # repair: writes
python3 scripts/id-rectify.py --repo . --base main --head <branch> --lint   # lint: read-only
```

Repair mode allocates the canonical `H-NNN` for every draft handle on the branch, renumbers any
fragment id that collides with something already on `main` or with another incoming fragment,
and mechanically rewrites every in-branch reference: the spec's own filename and title, every
citation in every other file changed on the branch, and the fragment that recorded the
registration. Rectification *is* the allocation; there is no separate "mint an id" step. Lint
mode runs the same detection and reports findings without writing anything, so you can check a
branch before landing it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Repaired (or nothing to do) in repair mode; branch clean in lint mode |
| 1 | Lint-blocked: findings reported, nothing written |
| 2 | Usage or tool error |
| 3 | Refusal: the tree is left untouched |

## What it rewrites

- A head-added spec whose id collides with a different spec already bound at base:
  renumbered to one above the highest known id, incoming side only.
- A head-added `H-DRAFT-<hash8>-<slug>.md`: renamed to `H-NNN-<slug>.md`, every in-branch
  citation of the handle rewritten to the allocated id.
- A head-added fragment whose integer id collides with base or with another incoming
  fragment: renumbered to the next free integer, filename and `id:` line both updated.
- A draft spec byte-identical to one already landed at base: treated as the same registration
  arriving twice, so the incoming copy is removed and an alias is recorded, nothing is allocated.

## What it refuses

Refusal leaves the tree completely untouched (exit 3), before any write: a diff that touches
the repository's directives file; a collision id or draft handle cited on head-added lines of a
fragment that is already landed at base, or of the frozen base journal file; a line that mixes
a rewritten and a base-bound token together.

## What it tolerates

Three narrower cases than an outright refusal, each caught and repaired rather than left to
land broken, each with its own lint finding class on the unrepaired branch:

| Case | Lint class | What happens at land |
|---|---|---|
| **P6**: an incoming journal fragment has no integer id at all, no integer filename prefix, no `id:` line (a registrar wrote `H-DRAFT-<hash8>-....md` for a fragment, or omitted the id line entirely) | `FRAGMENT-WITHOUT-INTEGER-ID` | Allocated the next free integer at land: renamed to `NNNN-<rest>.md`, `id:` line set or inserted |
| **P7**: a head-added `hypotheses/H-DRAFT-*.md` whose name has no well-formed hash8 (an executor skipped the hash step and just wrote a slug) | `MALFORMED-DRAFT-HANDLE` | The whole stem is treated as the draft handle, allocated and renamed like any other draft |
| **P8**: a draft handle appears inside the *filename* of a head-added non-spec file (a fragment or note named with the handle instead of just citing it in text) | `DRAFT-HANDLE-SURVIVES` | Rewritten to the allocated id at land, composed with any fragment renumber that also applies |

## The alias row

When a draft handle resolves to a canonical id, or a byte-identical draft is deduplicated
against something already landed, the gate appends one row to the alias ledger recording the
old handle and the new id, so a citation written before land can still be traced afterward
even if some copy of it survives outside the rewritten files (a rectification report, a stale
comment thread). See "Follow-ups" in the release PR for the one place this ledger path is
currently hardcoded rather than read from config.

## Upgrading an existing corpus

Nothing historical is renamed. Every `H-NNN` that already resolves keeps resolving; the
per-repository floor for new allocations is simply the largest id already landed. There is no
migration step: the next hypothesis registered on a branch is the first one that follows the
new rule.

## Rollback

Downgrading to 0.3.2 restores the "highest existing + 1" rule with zero corpus damage: existing
`H-NNN` and integer fragment ids are exactly what that rule already expects, and no file written
under 0.3.3 needs to change to be read under 0.3.2. It is a two-way door.

## Selftest

```
python3 scripts/selftest-id-allocation.py
```

Builds a throwaway consumer repository under a temp directory, registers colliding branches
through the installed gate, and checks the id allocation, the P6/P7/P8 tolerances, the refusal
and exit-code surfaces, and the dedupe path. One PASS/FAIL line per check, a RESULT line, exit 0
on PASS.
