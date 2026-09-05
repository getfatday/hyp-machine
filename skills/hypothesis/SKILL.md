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

- If the hypothesis isn't registered yet, create the spec from `hypotheses/TEMPLATE.md`. The
  id follows the draft-then-allocate contract (lab H-148, kept 2x5/5 — concurrent next-free
  registrars collided in 4 of 4 measured cohorts):
  - Registering on the default branch and landing immediately: take
    `hypotheses/H-NNN-<slug>.md` with NNN = one above the largest landed spec number (H-001
    when none exist), re-checking next-free immediately before the write — the lander is you,
    so mint-at-land collapses to this.
  - Registering on any other branch, in a clone, or whenever the land is not immediate: name
    it `hypotheses/H-DRAFT-<hash8>-<slug>.md`. hash8 is REQUIRED and is computed, never
    invented or left out: after writing the spec body to a temp file, run
    `{ cat <tmp>; git branch --show-current; date -u +%Y-%m-%dT%H:%M; } | shasum -a 256 | cut -c1-8`
    and use the eight hex characters it prints (a filename like `H-DRAFT-my-slug.md` with no
    hash is malformed). The draft handle `H-DRAFT-<hash8>` stands in wherever
    the id would appear — spec filename, spec title, and every citation, including the text of
    the journal fragment that records the registration. Never claim a numeric id off the
    default branch. The canonical `H-NNN` is allocated AT LAND by the
    lander running the id gate (`python3 scripts/id-rectify.py --repo . --base <canon>
    --head <branch>`), which renumbers the draft and mechanically rewrites every in-branch
    reference — rectification is the allocation. Journal-fragment ids are ALWAYS integers:
    name the fragment `<next free integer>-<slug>.md` with a matching `id:` line exactly as on
    the default branch (never a hash or a handle in a fragment filename); off the default
    branch that integer is a draft claim, and the id gate renumbers colliding incoming
    fragment ids at land.
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
fragment `<journal dir>/<id>-<slug>.md` (id = always an integer, never a hash or handle:
the next free integer re-checked at land when you are the lander; on any other branch it is a
draft claim the land-time id gate renumbers on collision) with
frontmatter `id:`, `date:`, and `type: run`, carrying the hypothesis id, what happened,
assertion results (n/m with failures listed), the verdict, and links to the run artifacts.
Fragments are write-once — never modify one after creation; no author names in the text (git
blame is attribution). Update the hypothesis file's Status and Runs table. Never rewrite past
fragments.

## North star and checkpoints

A north-star file (`ledger/north-stars/<slug>.md`, from
`${CLAUDE_PLUGIN_ROOT}/templates/north-star.md`; convention in `ledger/north-stars/README.md`)
names one destination and the conditions that must be true to reach it. It stores no status:
every condition's state is derived at HEAD from the resolver it is bound to.

- **Bind a spec to a condition.** Add one row: `| C-NN | <what must be true> | hypothesis |
  H-NNN | hypothesis-kept=H-NNN | <needs or -> |`. Use `hypothesis-verdict=H-NNN` when a
  discard answers the question too. A verdict landing in the spec's `## Status` is all it takes
  — never edit the north-star file to record it. Lint before committing:
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/north-star-check.py" --strict`.
- **Run checkpoint (advisory).** After the grade lands, the grade leg MAY compile the run's
  single-file page: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-run-checkpoint.py"
  experiments/runs/<id>/run-<k>` writes `run-checkpoint.html` beside `grade.txt` (typed refusal
  codes 10-15 when spec and run disagree; a refusal never blocks the grade or the journal).
- **Progress page.** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-north-star-progress.py"
  ledger/north-stars/<slug>.md` renders `north-star-progress.html` beside the file (frontier,
  distance, replay over sampled commits); `--check PAGE` exits 1 when it is stale.
- **Many north-star files.** One repository may carry many destinations: the checker reads
  every `ledger/north-stars/*.md` in one invocation and its `set` block lists each shared lane
  once, ranked by how many destinations it serves (the pick across destinations is the first
  `union_frontier` entry whose `claimed_fresh` is false), and a `needs` cell may name a
  sibling file's condition as `<slug>#C-NN[:yes|no]` instead of copying its row.
- **Bind a typed document.** A row may bind a committed file instead of a spec — `| C-NN |
  <milestone> | document | docs/<file>.md | frontmatter-status=docs/<file>.md:completed!cancelled
  | - |` — and derives from that file's frontmatter `status:` line at the commit (verb `sync`;
  an absent path is `DANGLING-REF`).

## Rules

- No run without a spec; no verdict without assertions; no unjournaled runs.
- Surgical scope: an experiment touches only its own run directory, its hypothesis file, and
  its journal fragment — never the repository's directives file or other hypotheses.
