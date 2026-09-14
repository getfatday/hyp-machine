---
bump: minor
---
Every workflow `agent()` call is now expected to name its model and effort from a committed
routing table (`rules/routing-default.json`, overridable per repository in
`.claude/routing.json`); a new `PreToolUse` hook on the `Workflow` and `Agent` tools checks
each call and advises when one omits or misroutes `model`/`effort` — nothing is denied until
`routing.enforce` is set to `deny` in `.claude/hyp.json`. Evidence: the lab keep
`H-DRAFT-314c8d17-routing-guard` (`VERDICT.json`: evidence-sufficient promote, five counted
looks 5/5, llr 2.9389 >= the 2.8904 bound — 100% recall on model-less calls and eight seeded
defect classes, zero false denials on the rewritten conformant subset, under one second on the
largest persisted script). After upgrading: run `/hyp:init` once to scaffold
`.claude/routing.json`, read `docs/model-routing.md`, then set `routing.enforce: deny` when
ready. To undo: revert this merge, or set `routing.enforce: off`.
