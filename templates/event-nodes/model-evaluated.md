---
id: event/model-evaluated
type: event
context: {{CONTEXT}}
summary: One operating-model tree was linted and its compiled artifact checked for staleness — the `model-lint.py` findings and the git-date rule — with no model call.
representation: file(ledger/om-feedback.jsonl) — one `model-evaluated` JSON row per `<model_dir>/<context>/` tree per evaluate, canonical bytes, append-only, merge=union
emitted-by: [command/evaluate-model]
consumed-by: [read-model/passive-feedback]
status: current
---
Canonical node template for the passive feedback worker (lab H-DRAFT-35397146-om-worker-deterministic,
kept 2026-09-13; copy into your `operating-model/<context>/events/`). The row is written by
`scripts/om-worker.py evaluate` (or `compile-check`, the name the startup wake will call; today both
write the same row). Payload contract (`schema: 1`, docs/passive-feedback.md): `{model_tree, lint:
{errors, findings, parse_skipped}, compiled: {stale, compiled_path, compiled_commit_date,
model_commit_date}, compile_command: {command, rc}, date}` — `findings` are the lint's own sorted
lines, `stale` is the newest `compiled/*.md` by commit date predating the tree's last commit.
Attribution stays in git — no author fields. If your `.claude/hyp.json` overrides
`om_feedback_file`, name that path in the representation line instead; name your own command and
read-model ids once you have them (the ids above are placeholders the lint will flag as dangling
until you do).
