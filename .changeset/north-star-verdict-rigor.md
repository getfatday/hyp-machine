---
bump: minor
---

North-star verdict rigor. `scripts/north-star-check.py` gains the `probe-passed=<lane>` closes-when
predicate (done only on a yes verdict; a no leaves the row open with outcome no, so its `C-NN:yes`
dependents retire and its `C-NN:no` dependents proceed) and reads a probe `VERDICT.json` strictly:
keep/kept/pass/passed/yes/true is a yes, fail/failed/discard/discarded/no/false a no, and every other
verdict (ambiguous, refine, void, empty, missing key, non-string, unparsable JSON) is unresolved and
never a yes, named by the advisory `PROBE-VERDICT-UNRESOLVED`; a `reached-when` row that is done with
outcome no derives the new status `refuted` with the hard finding `REACHED-WHEN-REFUTED`; a file whose
every `reached-when` row retired derives `abandoned` with the advisory `DESTINATION-ABANDONED`; and
`reached` now requires at least one satisfied row. `scripts/compile-north-star-progress.py` files any
derived status word under a panel of its own (no KeyError on `refuted`), counts only satisfied rows
toward `reached_count` (never retired, refuted or unresolved-probe rows), emits `abandoned` /
`advisories` and a style+script addendum only when a stop needs them (pages the base vocabulary covers
are byte-identical), and fixes a cross-file `needs` token that was read as the local id and recursed
forever when the ids collided. `scripts/closes_when.py` learns `probe-passed=` with the checker's
yes-set. `templates/north-star.md`, `templates/north-stars-README.md`, `docs/north-star.md` and the
README document the predicate and the refuted / abandoned vocabulary. Why: before this change every
verdict word outside pass/fail read as a yes, a discard in a `reached-when` row read as done, and an
all-retired destination read as reached. Evidence: source lab commits 8b7c1ed7 and ce46291b (journal
fragments 0452 and 0454), selftests 128/0 (checker), 33/0 in the lab and 31/0 on this copy (progress
compiler; this copy has no late-born-stops lane) and 30/0 (closes_when), both refute-reviews LAND.
