# Directive intake: declare acceptance before you mutate

The contract kit that makes a session executing an imperative change-ask ("unify X into Y",
"move this", "consolidate that") commit a **D-doc with binary acceptance assertions BEFORE
the mutation commit**, verify fidelity afterward, and leave machine-readable follow-ups.
Three pieces ship: `templates/DIRECTIVE-TEMPLATE.md` (the D-doc shape),
`scripts/directive-lint.py` (the level-triggered closure join), and
`scripts/directive_emitter.py` (the On-close commitment emitter).

The measured zero this kit repairs: in every observed ungoverned directive execution —
including the counted baseline arms — sessions produced **no committed pre-mutation
acceptance artifact and never named a plant divergence**; one baseline session silently
dropped a README in a merge collision and nothing caught it. The kit turns both zeros into
committed, lintable state at zero disruption to the asked-for work.

## The three disciplines

1. **Declared-before, in git order.** The D-doc (copied from
   `templates/DIRECTIVE-TEMPLATE.md` into `directives/D-NNN-<slug>.md`) is COMMITTED before
   the mutation commit — the acceptance assertions exist in history before anything they
   judge. The counted grading was mechanical git-order over end-states: commit containing
   the D-doc strictly precedes the first mutating commit.
2. **Named-divergence verification.** Acceptance assertions for move/merge/unify-class asks
   include an inventory-parity check (pre-move inventory == post-move inventory at
   destination). After execution, the Verification record holds one row per assertion —
   PASS with an evidence pointer, or the divergence NAMED explicitly with the
   maintainer-visible reason. Fidelity means: the moved content arrives byte-identical, OR
   its divergence is named by the declared verification. Carrying a file along unexamined
   is an observation, never a catch — only a named divergence counts.
3. **The acceptance-assertions discipline.** 3-5 binary pass/fail checks declared before
   any mutation, each with the command or method that verifies it — the only basis for
   flipping Status to `executed`. Status flips are gated: open -> executed only when the
   verification record is complete; executed -> closed only when the journal fragment
   exists.

## The closure machinery

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/directive-lint.py" <repo-root>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/directive_emitter.py" <repo-root> <ledger-path> <date>
```

**directive-lint** is a deterministic state lint, level-triggered (findings re-derive from
current state every run and surface every session until fixed), silent when clean, exit 0
always. Five finding classes: `RAW-UNANSWERED` (a committed raw directive file no D-doc's
Ask section answers — scoped OUT when a D-doc answering the ask exists, even mid-flight),
`D-DANGLING-ASK`, `D-MALFORMED`, `D-EXECUTED-UNVERIFIED` (verification rows missing, or a
non-PASS row with no divergence note), `D-CLOSED-UNJOURNALED`. Paths resolve through
`.claude/hyp.json` (`directives_dir`, `raw_dir`, `journal_dir`, `journal_file`); the
`EPOCH_SHA` constant grandfathers pre-install raw directive files when set at install.

**directive_emitter** turns each executed/closed D-doc's `## On close` block into ordinary
kind:commitment ledger rows (emit-once by slug, never raises, backstop row for a missing
block), so follow-ups resurface every session until their closes-when predicate satisfies —
the same resolver machinery the rest of the ledger uses, zero new predicate work.

## Evidence

**H-199-directive-intake-v2** (source lab, kept 2026-08-28, two consecutive counted 5/5
live headless sessions per arm, fresh seedings and a prompt variant across runs): with the
kit installed, arm-A sessions committed D-docs with binary acceptance assertions before the
mutation commit and held move fidelity (plant byte-identical or divergence NAMED by the
declared verification), with directive-lint clean on every passing end-state and the
asked-for work completed in budget; arm-B sessions (no kit) produced zero pre-mutation
acceptance artifacts and named zero divergences — including a real uncaught README
merge-drop. The full grading pipeline was byte-identical across double passes. The spec is
the assertion-repaired successor to H-108, whose first run was ruled environmental plus
fixture-defect by diagnosis, not hypothesis evidence; the repaired contract went to
2x5/5.

All three artifacts ship as counted; only provenance framing and consumer-repo path
resolution (the `.claude/hyp.json` overlay) differ from the counted fixture copies.
