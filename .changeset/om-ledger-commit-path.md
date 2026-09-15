---
bump: patch
---
When you commit, a dirty operating-model feedback ledger (`ledger/om-feedback.jsonl`, or your
configured `om_feedback_file`) is staged for you and one line says so (`OM-FEEDBACK-STAGED <n>
rows`), or one line says why it was held (`OM-FEEDBACK-HELD forbidden key <key>`) instead of
staging it. This lands the commit-time clause of `hooks/scripts/commit-backstop.py` from the lab
keep `H-DRAFT-fb9c08b9-om-ledger-commit-path` (`VERDICT.json`: five counted looks, every
assertion passing in every one, the frozen SPRT walking to 2.9389 over the 2.8904 promote bound,
cold-verified). Nothing to do after upgrading. To undo for one commit, run `git restore --staged
-- ledger/om-feedback.jsonl` before committing; to undo entirely, revert this release's merge
commit.
