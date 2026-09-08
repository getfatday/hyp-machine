---
bump: minor
---
`scripts/hyp-lab.py`: one named entry point for the read-only lab instruments — `status | stalls | preflight | recent | prior | mine | next-id`, one subcommand per `make lab-*` target of the reference consumer layer. `status`, `stalls` and `preflight` dispatch to the shipped scripts (`status` appends one `DRAFTS:` line counting the draft-handle specs the released readers skip); `recent`, `prior`, `mine` and `next-id` are built in over the `.claude/hyp.json` paths with `hyp_status.py` as the status reader; `next-id` prints the H-148 draft handle (`H-DRAFT-<hash8>-<slug>`) and never fetches. Zero LLM, nothing written under the repository (lab H-DRAFT-6ec1691d-consumer-lab-entrypoint).
