---
bump: minor
---
After every session, one committed row per workflow agent now records which model actually
served it, its class, tokens, cost, and outcome: a synchronous `Stop` hook (and a `SubagentStop`
hook) append an `agent-route/v1` row per finished workflow agent to
`ledger/routing-ledger.jsonl`, unioned across worktrees, joined to a workflow's own run record
when its driver opts in. Why: the lab keep `H-DRAFT-38f86fad-routing-ledger-row` (`VERDICT.json`:
evidence-sufficient promote, five counted looks 5/5, llr 2.9389 >= the 2.8904 promote bound).
Caveats: only the `Stop` row is evidenced on real workflow agents (`SubagentStop`'s firing on a
live subagent was never measured in the source lane's build); the writer's own wall grows with
host load. What to do after upgrading: run `/hyp:init` once so the `.gitattributes` union row for
the new ledger file is scaffolded. How to undo: revert this release's merge commit. See
`docs/model-routing.md`, "The routing ledger".
