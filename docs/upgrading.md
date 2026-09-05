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
