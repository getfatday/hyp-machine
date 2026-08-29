<!-- communication-contract.md — the hyp clarity canon (ported from the source lab's
     research/communication-contract.md, landed there 2026-08-28 under the maintainer
     decision-clarity directive: "I'm not looking for more words, I'm just looking for
     clarity in language" / "prove out that it's clear for me without necessarily having
     me in the loop."
     Evidence: the lab's naive-reader eval — contract-conformant cards scored 100%
     comprehension under all three grading standards vs 78.6% as-judged for status-quo
     cards; 6/6 vs 0/6 readers able to state the default-on-silence; reader effort -29%
     wall-clock, -35% tokens. Counted hardening specs H-207..H-211 are registered in the
     source lab; the contract ships as measured canon pending those verdicts.
     Companions in this plugin: scripts/house-vocabulary.json (the L3 gloss list) +
     scripts/clarity-lint.py (the L1-L11 mechanical half). The decision surface renders
     cards in this anatomy: compile-dashboard.py's DECISIONS WAITING section and
     decisions.html (gloss tooltips read house-vocabulary.json) — see docs/decisions.md.
     Extend house-vocabulary.json with your own repository's insider terms. -->

# Communication contract — decision cards and session reports

## Who this is written for

Every decision card and every session report is read by someone with **general software
knowledge and zero session context** — no hypothesis numbers, no lab vocabulary, no memory of
last night — who has **under a minute per item** to decide or catch up. The artifact must
carry its entire case alone: the reader cannot ask follow-up questions, and may be tired.
Write as if the author is not in the room (the silent-read test).

**Clarity is subtraction.** Every rule below sets a MAXIMUM, never a minimum. If an ask
cannot fit these ceilings, the ask is not ready: convert it into a commit or an experiment
instead of explaining it harder. A decision card exists only for what is genuinely a human's
— irreversible effects, other people, or a physical act only the human can perform.
Everything reversible (anything that lands as a commit) proceeds without a card.

## Fixed anatomy

**Decision card**, sections in this exact order:

```
HEADLINE          one sentence: what changes in the world, and by when
CONTEXT           <= 2 sentences: only what this reader lacks
ASK               one question, answerable by picking one option
OPTIONS           2-3, each: Verb label -> consequence. Reversible:/Not reversible: ...
IF YOU DO NOTHING the default outcome and its absolute date
WHY ONLY YOU      the physical or irreversible part, one clause
evidence:         <= 3 pointers (machine line, outside the word budget)
answer:           the resolve command (machine line, outside the word budget)
```

**Session report**, sections in this exact order:

```
WHAT CHANGED             one sentence (the bottom line, first)
DONE                     <= 5 bullets: outcomes, not activity
NEEDS YOU                pointers to open decision cards, or the word "nothing"
NEXT — NO ACTION NEEDED  <= 3 bullets: what will happen without the reader
evidence:                <= 3 pointers (machine line)
```

## Lintable rules (mechanically checkable)

**L1 — Impact first.** The first sentence states the outcome or impact — what changes in the
world and for whom — never process or history. Lint: first sentence <= 25 words; does not
open with a process marker (During, While, Following, As part of, Per, In the course of, The
lane/run/sweep/wave...); contains at most one id token. *(cards + reports)*

**L2 — Every id carries a plain-noun gloss.** On first use, every id (H-NNN, DEC-NNN, N-id,
version like 0.3.0, row/fragment number) is followed by a parenthetical apposition in plain
words: "H-125 (an experiment that tested community-channel setup)". Later uses may be bare.
Maximum 3 distinct ids in a card body. Commit hashes appear only in evidence lines, never in
prose. *(cards + reports)*

**L3 — House vocabulary is glossed or avoided.** No term (or variant) listed in
`house-vocabulary.json` appears without its gloss on first use — or is simply replaced by the
gloss. Lint: case-insensitive scan against the term/variant list. *(cards + reports)*

**L4 — Option labels are verbs.** Every option label is an imperative verb phrase of at most
4 words ("Re-sign now", "Accept unsigned", "Park it"). Lint: label length; first token
matches the verb lexicon (unknown first tokens escalate to the judge). *(cards)*

**L5 — Every option carries consequence + reversibility.** Each option reads:
`Label -> what concretely happens next. Reversible: how | Not reversible: what becomes
permanent.` Lint: presence of the arrow and exactly one reversibility tag per option.
*(cards)*

**L6 — Default on silence.** Exactly one line starting `IF YOU DO NOTHING`, naming the
outcome and an absolute date (YYYY-MM-DD) or the literal words "nothing changes". Lint: line
presence + date regex. *(cards)*

**L7 — One ask, one recommendation, three options max.** At most one question mark in the
card; exactly one option tagged `(recommended)`; 2-3 options. If a third option keeps feeling
necessary, the ask is unconverted work — send it back to triage. *(cards)*

**L8 — Ceilings (maximums, never targets).** Lint: word counts.

| unit | maximum |
|---|---|
| card body (HEADLINE through WHY ONLY YOU) | 120 words |
| HEADLINE | 1 sentence, 25 words |
| CONTEXT | 2 sentences, 40 words |
| option: label / consequence+reversibility | 4 words / 30 words |
| report body | 200 words |
| any bullet | 30 words |
| any sentence, anywhere | 25 words |
| evidence pointers per artifact | 3 |

Word counts exclude section labels, option labels' `(recommended)` tags, and the machine
lines (`evidence:` / `answer:`).

**L9 — Absolute time, expanded scores.** No relative time ("yesterday", "this morning",
"recently", "soon") — absolute dates. Score shorthand (2x5/5, 7/7, "doors green") never
appears in body prose without expansion ("passed all 5 checks in both scored runs"). Lint:
banned-token list + score-notation regex. *(cards + reports)*

**L10 — Anatomy and pointer placement.** Sections appear with the exact names and order
above; file paths, commands, and line references appear only in the trailing `evidence:` /
`answer:` lines, never inside body prose. Lint: section order; path regex in body = fail.
*(cards + reports)*

**L11 — Answer-key sidecar.** Every card ships with a 3-field sidecar (`<card>.key.json`:
intended ask, per-option consequence, default-on-silence), written by the card's author and
read only by judges — never rendered. Lint: file exists, three fields non-empty. *(cards)*

## Judged rules (naive-reader evaluation)

Graded on the reader's restatement, never on a claim of understanding — a reader who says
"clear!" but paraphrases wrongly is a FAIL of the artifact, not of the reader.

**J1 — Restate the ask.** After one read within 60 seconds, the naive reader states in their
own words what is being decided. Pass = restatement matches the sidecar's intended ask.

**J2 — Consequences per option.** The reader states, for each option, what choosing it causes
— including which choices can be undone and how. Pass = matches the sidecar per option.

**J3 — Default.** The reader states what happens if nobody answers, and by when.

**J4 — Self-contained.** A fixed question set — who acts? by when? what does it cost? what
breaks if this goes wrong? — is answerable from the artifact text alone. Any question the
judge can only answer with outside knowledge is a fail (the card leaked context).

**J5 — Why a human.** The reader can say why this could not have been handled as a commit or
an experiment. If the naive reader reasonably concludes "why are you even asking me?", the
card fails triage — the fix is conversion, not rewording.

**J6 — Report catch-up.** After one read of a report, the reader states what changed since
the last report and what, if anything, they must personally do.

### How judged rules are evaluated

- The judge role-plays the preamble's reader (general software knowledge, zero session
  context) and sees ONLY the artifact — no repository, no session history.
- Grading compares the judge's paraphrase to the L11 answer key, question by question.
- Comparative evals are position-swapped and length-capped; prefer a judge from a different
  model family than the author (same-family judges favor their own).
- Judge calibration uses decoy cards with substituted facts; a judge answering from its own
  knowledge instead of the card text is disqualified.
- Readability formulas (Flesch-Kincaid etc.) are never pass criteria — they measure syllable
  counts, not understanding.

## Grounding (why these rules, one line each)

- Impact first: BLUF, US Army AR 25-50 — ineffective writing fails to "quickly transmit a
  focused message". First position is enforceable, hence lintable.
- Silent-read self-sufficiency: Amazon 6-pager — the memo is read without the author
  narrating; PR-FAQ writes for a fictional outsider.
- Yes/no answerability + one recommendation: UK ministerial submissions — "phrase your advice
  so that the Minister can just say yes or no", options each with merits/demerits.
- Consequence + reversibility per option: Nygard ADRs (consequences are a required section)
  + Bezos 2015 one-way/two-way doors (reversibility sizes the attention a decision deserves —
  and decides whether to ask at all).
- Vocabulary control and glossing: plain-language law (plainlanguage.gov), GOV.UK ("open up,
  not dumb down" — experts also read plain text faster), ASD-STE100 (restricted vocabulary).
- Status values, not shorthand; critical items early: NASA/Degani-Wiener aviation checklist
  findings.
- Paraphrase grading: plainlanguage.gov paraphrase testing / AHRQ teach-back — hearing it
  back is the only proof of understanding; readers claim understanding they don't have.
- Answerable-from-artifact: QuestEval/QAEval — score a text by whether questions are
  answerable from it alone.
- Judge hygiene: knowledge leakage (judges ignore given text under conflict), preference
  leakage (same-family bias), position/verbosity bias — all replicated LLM-judge failures.
