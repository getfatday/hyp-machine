---
bump: minor
---

A hypothesis spec may now declare one or two decision assertions in its Binary assertions
section when its Verdict rule types the run-validity conditions as voids -- a
`SUBSTANTIVE-ASSERTIONS:` line naming the primary assertion, plus the word "void"/"voids"
in the same section; three to five assertions still pass unconditionally as before, and six
or more still fails. Why: the lab kept H-DRAFT-44f18a2e-preflight-assertion-gate (five
counted looks, zero refusals) on evidence in research/assertion-count-rule.md that no
pre-registration standard or experimentation platform sets a floor above one primary
assertion -- a floor above one was this lab's own unreviewed addition. Nothing to do after
upgrading: every existing 3-5-assertion spec reads exactly as before. Undo: revert this
merge; the check reverts to requiring 3-5 assertions unconditionally.
