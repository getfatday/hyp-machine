# Upgrading to hyp from lab-intake + lab-loop

These steps are the counted upgrade path (hypothesis H-161 in the source lab, kept
2026-08-26: two runs, zero byte loss, guards and loop verified, rollback proven).

1. **Uninstall the lab pair.** Disable or remove `lab-intake` and `lab-loop`
   (marketplace uninstall, or set them false under `enabledPlugins` in
   `.claude/settings.json`). Your repository's artifacts — journal fragments, raw
   captures, ledger, hypothesis specs, dashboard — are files in your repo; uninstalling
   plugins never touches them.
2. **Install hyp** from the same marketplace and run `/hyp:init` in the repository.
   Init detects existing lab artifacts and upgrades in place: it preserves every
   existing file byte-for-byte, replaces the legacy lab CLAUDE.md marker blocks with
   the hyp block, and wires the write-once guards and the dashboard.
3. **Verify** (what the counted runs checked): pre-existing write-once files unchanged;
   an attempted edit of a landed journal fragment is denied; the dashboard recompiles
   with your open decisions intact; registering a hypothesis and landing a fragment
   works under the `/hyp:*` prefixes.
4. **Rollback (if you need it):** uninstall hyp and re-enable the lab pair — the
   counted runs verified the write-once history remains byte-identical in that
   direction too. The swap is a two-way door.

Since 0.3.3, ids follow the draft-then-allocate contract: a hypothesis registered anywhere
other than the default branch takes a draft handle instead of a numeric id, and the numeric id
is allocated at land by `scripts/id-rectify.py`. Nothing about an existing corpus changes on
upgrade; every landed `H-NNN` and fragment id keeps resolving exactly as it does today, and the
new rule only governs registrations made after you upgrade. See `docs/id-allocation.md` for the
full contract.

From the release that carries the decision-brief gate, every new decision card needs a plain-English
brief (`decisions.py add --brief <brief.json>`; `docs/decisions.md`, "The plain-English brief"), and a
card without a valid one is never shown as a question. Cards already on file are exempt by id: set
`decision_brief_legacy_max_id` in `.claude/hyp.json` to your highest pre-upgrade decision id (absent,
the boundary is shape and order: every card appended before the first briefed one is legacy). Each open
legacy card renders exactly as before plus one `brief: BRIEF-MISSING` line naming the retrofit command
(`decisions.py brief <id> --brief brief.json`); resolved cards render byte for byte as before. The three
admission-tiered prose rules (B6, B9, B10) are report-only until you list them in
`decision_brief_admitted_rules`; the shipped writers that file cards (`retest-trigger.py`,
`knob-observe.py`, `dispatch-gate.py ingest`, `reflex-surface file`) are refused with the recipe until each
carries a brief. `python3 scripts/selftest-decision-briefs.py --live .` checks the marker rendering over your
own ledger.
