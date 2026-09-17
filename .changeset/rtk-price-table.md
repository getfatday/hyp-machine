---
bump: patch
---
`rules/model-prices.json` is corrected: the previous table overstated every dollar figure by
about 1.5x-2.2x (Fable 5.1 cache reads were priced at US$1.50 per million tokens against the
real US$0.25), reconciled against the claude-api skill bundled with Claude Code and verified to
reproduce Claude Code's own cost-state transcript row within 1% (lab program
`experiments/runs/DESIGN-rtk-token-cost/research/token-baseline.md` section 1 and `FINDINGS.md`).
Three model-specific rows (`claude-fable-5-1`, `claude-mythos-5-1`, `claude-sonnet-5`) were added
alongside the four tier rows; `cost_usd` already matches the longest prefix, so nothing else
changes. Every `cost_usd` the plugin prints from here on is about 2.2x lower for Fable-heavy
work; historical ledger rows keep their `tokens` fields and re-price on the next
`routing-derive` cadence run. Undo: revert this release's merge commit.
