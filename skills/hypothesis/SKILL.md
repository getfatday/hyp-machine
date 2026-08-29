---
name: hypothesis
description: Register and run hypothesis experiments using this repository's experiment loop — spec the hypothesis, run within a fixed budget, evaluate binary pass/fail assertions, reach a mechanical keep/discard/refine verdict, and journal the run. Use this skill whenever the user wants to test an idea, run or set up an experiment, register/create a hypothesis, evaluate whether a new way of working is better, or asks "does X actually work?" — even if they don't say the word "experiment". The journal and capture discipline live in the same plugin's capture layer (`intake` skill).
---

# Run a hypothesis experiment

This repository studies ideas via an explicit experiment loop — hypothesis → budgeted
experiment → binary evaluation → mechanical verdict — inspired by Karpathy's autoresearch
(whose loop is simply: modify, train 5 minutes, check improvement, keep or discard, repeat).
The hypothesis/assertion/verdict machinery is this loop's design, not Karpathy's. The loop only
produces trustworthy results when the spec is written before the run and the verdict follows
the assertions rather than impressions — so the order below matters.

## Paths

Defaults below; if `.claude/hyp.json` exists at the repo root, its values override
them (journal paths come from the same config).
Run `/hyp:init --profile experiments` once per repository to scaffold everything.

| Purpose | Default |
|---|---|
| Hypothesis specs (one per hypothesis) | `hypotheses/` |
| Spec template | `hypotheses/TEMPLATE.md` |
| Run artifacts | `experiments/runs/` |
| Deterministic spec pre-flight | `experiments/preflight.py` |
| Journal fragments (write-once, via the capture layer) | `experiments/journal-fragments/` |

## 1. Spec before anything runs

- If the hypothesis isn't registered yet, create `hypotheses/H-NNN-<slug>.md` from
  `hypotheses/TEMPLATE.md` (next NNN = highest existing + 1, starting at 001 when none exist).
  If the repository keeps a human-edited directives file, read it first for current direction —
  and never edit it.
- Registering a spec is itself a capture: file it through the `intake`
  skill discipline — journal the registration as a write-once fragment (`type: capture`) even
  if no run follows in the same session.
- The spec must have: one falsifiable sentence, exactly one variable under test, a baseline, a
  fixed budget per run, 3–5 binary assertions, and a verdict rule.
- If the user's idea has multiple variables, split it into multiple hypotheses and say so. Give
  the split hypotheses a shared fixture and identical assertions so their results are
  comparable, and run them independently first. If the user asked about the variables
  *together* (an interaction effect), note that a combined-arm hypothesis is legitimate only
  after each single variable has a verdict — register it then, with the better single arm as
  its baseline.
- If an assertion can't be checked mechanically (pass/fail with evidence), rewrite it until it
  can.
- Check the spec mechanically before running:
  `python3 experiments/preflight.py hypotheses/H-NNN-<slug>.md`. Exit 0 = run-ready; nonzero =
  fix the FAIL lines or escalate to the user. The plugin's PreToolUse gate runs the same check
  against run-shaped commands.
- Get the spec confirmed before running when the user is available; otherwise state your
  assumptions explicitly in the spec.

## 2. Run within budget

- Execute the method exactly as specced. Stop at the budget even if unfinished — a budget
  breach invalidates comparison with other runs; record the run as budget-exceeded.
- One variable changes; everything else matches the baseline.
- Save run artifacts (transcripts, outputs, measurements) to `experiments/runs/<id>/run-<k>/`;
  pinned inputs both arms share live in `experiments/runs/<id>/fixture/`.
- If a headless child dies at startup with `Not logged in · Please run /login` (error at zero
  cost), don't reflexively re-login — that is the credential-flap signature. Classify the
  surface first (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor-classify.py" --live`), then
  take exactly the step the ladder emits for the typed state
  (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor-remediate.py" step --state <STATE>`) — see
  the plugin's `docs/doctor.md`.

## 3. Evaluate and decide mechanically

- Check each assertion against the artifacts; each gets pass/fail plus one line of evidence.
- Apply the spec's verdict rule literally: keep / discard / refine. Don't override it with gut
  feel — if the rule gave the wrong answer, that's a spec bug: journal it and propose a spec
  fix for the *next* run.

## 4. Journal (always, including failures)

Record the run through the capture layer's journal discipline: one write-once
fragment `<journal dir>/<id>-<slug>.md` (id = highest existing fragment id + 1) with
frontmatter `id:`, `date:`, and `type: run`, carrying the hypothesis id, what happened,
assertion results (n/m with failures listed), the verdict, and links to the run artifacts.
Fragments are write-once — never modify one after creation; no author names in the text (git
blame is attribution). Update the hypothesis file's Status and Runs table. Never rewrite past
fragments.

## Rules

- No run without a spec; no verdict without assertions; no unjournaled runs.
- Surgical scope: an experiment touches only its own run directory, its hypothesis file, and
  its journal fragment — never the repository's directives file or other hypotheses.
