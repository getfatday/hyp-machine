---
bump: minor
---

The plugin now ships one compiled agent definition per routing role (`agents/hyp-<role>.md`,
generated from `rules/routing-default.json` by `scripts/compile-routing-agents.py`), pinning
that role's model in the definition's own frontmatter. A routed workflow call is now served
by the table's model regardless of the calling session's model, a resumed session, or a
planted `CLAUDE_CODE_SUBAGENT_MODEL` — proven live against real headless children (lab keep
`H-DRAFT-75b03e6e-routing-determinism`, `VERDICT.json` evidence-sufficient promote, five
counted looks 5/5, llr 2.9389 >= the 2.8904 promote bound). The compiled portable runner's
`--model-low` flag is deprecated the same way: omitting it now resolves the `gate` role's
model from this table instead of a hardcoded `haiku` literal; passing it still overrides and
prints one deprecation line. Caveat, stated plainly: the lab lane measured a project-scope
install of the compiled definition (`.claude/agents/hyp-<role>.md`, `agentType: 'hyp-<role>'`
with no colon) — reproduced by this release's `--emit` mode — not the plugin-qualified id
this release ships the definition under, which is `agentType: 'hyp:hyp-<role>'` (plugin name
`hyp` plus the definition's own `name: hyp-<role>`), not the bare `agentType: 'hyp:<role>'` a
reader might guess. A cold refute review observed `agentType: 'hyp:hyp-build'` served the
table model in one headless child on CLI 2.1.273 (a single observation, not a keep) while
`agentType: 'hyp:build'` never started an agent — so no reader writes a call that fails at
runtime; that surface inherits the resolution-order claim above but remains otherwise
unmeasured until a lane exercises it directly.
What to do after upgrading: nothing, or run
`python3 scripts/compile-routing-agents.py rules/routing-default.json --emit <dir>` to install
a project-scope copy into your own repository's `.claude/agents/`. How to undo: revert this
release's merge commit.
