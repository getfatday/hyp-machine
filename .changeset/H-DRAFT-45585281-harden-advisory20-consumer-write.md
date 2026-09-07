---
bump: patch
---
harden-check advisory 20 no longer renders a consumer's dashboard: it called `compile-dashboard.py --triage-check` whenever `experiments/runs/DESIGN-decision-triage/triage.json` existed, and the shipped compile-dashboard has no such branch, so the call rendered DASHBOARD.md and decisions.html into the consumer's tree from a read-only advisory hook. The block is guarded on the branch being supported; every other line is byte-identical on the consumer and in the source lab. Lab H-DRAFT-45585281-harden-advisory20-consumer-write, kept 5/5 in two consecutive runs; consumer gap G13.
