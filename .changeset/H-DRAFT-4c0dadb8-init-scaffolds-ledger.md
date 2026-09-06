---
bump: minor
---
`init` creates an empty `ledger/ledger.jsonl` on every profile and never overwrites it, so the decision kit, the commitment resolver, the claim join, and DASHBOARD.md sections 1 and 2 work in every install instead of reading `source missing`. Re-running init on an existing install adds exactly that file and nothing else; a second run is a no-op. Lab H-DRAFT-4c0dadb8-init-scaffolds-ledger, kept 5/5 in two consecutive runs after Amendment 1 (the baseline's own second-run residue, `.claude/reflex/selftest/report.json` rewritten by the reflex self-test, is recorded as a separate horizon item), cause-n-effect research/consumer-parity-gaps.md gap G2.
