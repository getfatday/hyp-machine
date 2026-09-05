# H-NNN-slug: <short title>
<!-- Copy to {{HYPOTHESES_DIR}}/H-NNN-slug.md when landing immediately on the default branch (NNN = one above the largest landed spec number, starting at 001), or to {{HYPOTHESES_DIR}}/H-DRAFT-<hash8>-slug.md on any other branch — the land-time id gate allocates the canonical id (draft-then-allocate; see the hypothesis skill). -->
<!-- Title rule (contract L15): the prose after the colon is <= 25 words, carries at
most ONE house-only term (scripts/house-vocabulary.json status field), zero unregistered
coinages, zero score shorthand. Slugs and filenames never change after registration —
only title prose is ever migrated. -->

## Status
draft <!-- draft | active | kept | discarded | refined-into: H-NNN. draft → active when its first run starts. -->
Claim type: <!-- REQUIRED — fill exactly one: descriptive (measures what is) | normative (proposes what should be). A descriptive keep may not bind a maintainer decision: decision-class On-keep rows (maintainer-ruling=) FAIL preflight on descriptive specs; route them through a bridging normative spec. -->


## In plain terms
<!-- For a reader with general software knowledge and zero session context.
     Three lines, each one sentence <= 25 words, house terms glossed per
     scripts/house-vocabulary.json on first use (checked by clarity-lint spec mode).
     Compatibility law: the eight ## headings in this template never rename or
     reorder, and migration or lint never edits bytes from the Binary assertions
     heading through the Runs table — that span is frozen assertion grammar. -->
- **What we're testing:** <impact first: what changes in the world if this idea is true>
- **What "keep" means:** <the concrete practice adopted as standard if the pre-declared checks pass>
- **Terms:** <first-use glosses for every house term or id appearing in this spec's title and hypothesis; omit the line if none>
## Hypothesis
<!-- One falsifiable sentence. -->
<!-- e.g. "Requiring a written plan before any edit reduces reverted diffs on multi-file tasks." -->

## Motivation
<!-- Why this might be a better way of working. 2-3 sentences max. -->

## Variable under test
<!-- Exactly one. e.g. "Plan-before-edit rule present in CLAUDE.md (on/off)." -->

## Baseline
<!-- The current practice this is compared against. e.g. "No planning rule; edit immediately." -->

## Prior work
<!-- What this spec builds on or knowingly retries: kept mechanisms, banked nulls
     (failed experiments recorded so nobody retries them blindly), and refine lineage.
     Start from scripts/prior-art-sweep.py (advisory) over the findings index
     (research/findings-index.md), then verify every line by hand. One line per prior:
     id (verdict, date): why it bears on this spec — evidence pointer.
     A clean sweep is recorded, not omitted: "- none surfaced (sweep run <date>)". -->
- <!-- e.g. "Builds on H-110 (kept, 2026-08-16): reuses its grade_wiring stdin pattern — experiments/runs/H-110/fixture/grade_wiring.py" -->
- <!-- e.g. "Banked null H-155 (discarded, 2026-08-28): in-prompt surfaces do not suppress tool-reaching; this spec does not retry that class" -->

## Method
<!-- The preflight gate requires two statements this template does not carry for you
     (write them in your own words; the gate's FAIL messages show accepted examples):
     1. that graders and answer keys stay harness-side, invisible to the arms
     2. that the protocol/corpus/rubric were pinned before any run began -->
<!-- Numbered steps; fixed budget per run so runs are comparable. -->
1. <!-- e.g. Give the same 3-task set to a fresh session with the variable ON. -->
2. <!-- e.g. Repeat with the variable OFF (baseline). -->
- Fixture: <!-- Pinned starting state both arms share — task set, repo, commit. e.g. "tasks T1-T3 in repo X at commit abc1234" -->
- Repetitions per arm: <!-- e.g. 1 per task (note noise limitation) or 3 -->
- Budget per run: <!-- e.g. 30 min wall-clock or 1 session per task -->

## Binary assertions
<!-- 3-5 pass/fail checks. The ONLY basis for the verdict. No subjective scores. -->
1. <!-- e.g. All 3 tasks completed within budget. -->
2. <!-- e.g. Zero changed lines outside the requested scope. -->
3. <!-- e.g. Fewer clarification round-trips than baseline run. -->

## Verdict rule
<!-- Mechanical: all assertions pass = keep-eligible; any failure = discard or refine. -->
<!-- e.g. "keep if 4/4 assertions pass in 2 consecutive runs; refine after 1 failed run if the failure is a spec bug; discard after 3 failed runs." -->

## On keep
<!-- Machine-readable follow-ups: one commitment per line, each with a closes-when bracket, so
follow-through on a kept hypothesis stays checkable. Predicates:
path-exists=<path> | commit-grep=<needle> | hypothesis-kept=H-NNN | maintainer-ruling=<slug>.
Write '- none' if a keep genuinely has no follow-ups. -->
- none

## Runs
<!-- Append-only; link each row to the run's write-once journal fragment. -->
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
<!-- | 1 | <YYYY-MM-DD> | 3/4 | [fragment](../experiments/journal-fragments/<id>-<slug>.md) | -->
