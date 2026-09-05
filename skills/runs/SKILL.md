---
name: runs
description: Compile and open the run-census dashboard — what ran, verdicts, cost, busy/idle. Use when asked what experiments ran, what they cost, what voided or stopped early, what is in flight, or to refresh/open the runs board.
---

# /hyp:runs — the trial dashboard

The stub shipped by H-269-runs-census-board (kept 2026-09-05, run-1 counted 5/5: outside
readers of the compiled board beat raw-artifact readers on the frozen cost/outcome/
stopped-early probes; the wording below is the design note's snippet, paths adapted to
the plugin). Everything here is deterministic and read-only over the lanes.

1. Refresh the census and compile (deterministic; safe to re-run; from the repo root):
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/runs-dashboard.py" --rescan`
   The census scanner emits into your repo's own runs dir
   (`<runs_dir>/runs.jsonl`, default `experiments/runs/runs.jsonl`) and the page lands
   beside it as `runs.html`. Any `CENSUS-WARN future end_ts ...` line on stderr is the
   H-254 future-date clamp doing its job (a fabricated/misread end timestamp was
   nulled, not charted) — report it, don't suppress it.
2. Confirm the output line says `SELF-CHECK PASS` (any FAIL: stop and report it
   verbatim — the compiler caught its own output disagreeing with the census).
3. Open `<runs_dir>/runs.html` for the maintainer (`open` on macOS) and report the
   headline: attempts, kept/void shares, recorded cost, busy share in the last 7 days,
   and anything in flight. A null cost means unrecorded, never zero; voided and
   stopped-early attempts are their own classes, never counted failures.

Read-only over `<runs_dir>/**`; never edits lanes. Grafana export: add
`--grafana <runs_dir>/runs-grafana.jsonl` (flat JSONL, epoch-second fields,
State-Timeline-ready — point Grafana's JSON/Infinity datasource at it).
