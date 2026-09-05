---
bump: minor
---

The runs dashboard: `/hyp:runs` compiles every run attempt in your repo's own runs directory
into one deterministic, self-checking offline page — stat tiles, verdict stack,
processing-vs-wait, state timeline, cost bars — where void, stopped, and budget-exceeded
render as their own classes (never counted as failures), a null cost means unrecorded (never
zero), and `--grafana` writes a flat epoch-second JSONL export (lab H-269, kept 2026-09-05
counted 5/5: byte-deterministic generation, 100% figure-to-census traceability, board readers
beat raw-artifact readers on the frozen cost/outcome/stopped-early probes). The census scanner
`scripts/runs-census.py` carries the recorded H-254 future-date clamp: an end timestamp ahead
of the census clock is clamped to null with one `CENSUS-WARN` line instead of poisoning the
board's data-derived stamp.
