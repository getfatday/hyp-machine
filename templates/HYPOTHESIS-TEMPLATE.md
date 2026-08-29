# H-NNN-slug: <short title>
<!-- Copy to {{HYPOTHESES_DIR}}/H-NNN-slug.md — next NNN = highest existing + 1, starting at 001. -->

## Status
draft <!-- draft | active | kept | discarded | refined-into: H-NNN. draft → active when its first run starts. -->

## Hypothesis
<!-- One falsifiable sentence. -->
<!-- e.g. "Requiring a written plan before any edit reduces reverted diffs on multi-file tasks." -->

## Motivation
<!-- Why this might be a better way of working. 2-3 sentences max. -->

## Variable under test
<!-- Exactly one. e.g. "Plan-before-edit rule present in CLAUDE.md (on/off)." -->

## Baseline
<!-- The current practice this is compared against. e.g. "No planning rule; edit immediately." -->

## Method
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
