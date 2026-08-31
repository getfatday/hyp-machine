#!/bin/bash
# hyp-resume.sh — the scheduled cold-resume entrypoint: what the persistent timer
# fires (the plist emitted by scripts/install-resume-timer.sh names this script).
# One bounded firing = (1) the deterministic dispatch read (the H-217 orphan join:
# scripts/dispatch-status.py), then (2) at most ONE capped headless invocation that
# adopts at most one item.
#
# Ported for hyp 0.2.0 from the source lab's live install scripts/crux-resume.sh
# (H-217 reboot-relaunch, kept 2x5/5 2026-08-29) — firing logic unchanged; the two
# consumer adaptations: the repo root is the CURRENT WORKING DIRECTORY (launchd sets
# it via the plist's WorkingDirectory key; run it from your repo root by hand), and
# the dispatch read is the shipped dispatch-status.py resolved next to this script.
# Per the resume contract the timer's configuration supplies the capped headless
# invocation (see the plist), and adoption goes through the H-216 claim door
# (scripts/lane-takeover.py) per the dispatch's printed verbs.
#
# Usage: hyp-resume.sh [--dispatch-out PATH] [-- <invocation-cmd ...>]
#   --dispatch-out PATH  tee the dispatch read to PATH (default: stderr only)
#   -- <cmd ...>         the capped headless invocation to exec after the dispatch
#                        read (cwd stays the repo). In deployment the timer's
#                        configuration supplies this command. With no command, the
#                        firing is dispatch-only (static mode) and exits 0.
set -u
SELF_DIR=$(cd "$(dirname "$0")" && pwd) || exit 1
REPO_ROOT=$PWD
DISPATCH_OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dispatch-out)
      DISPATCH_OUT=${2:-}
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "hyp-resume: unknown arg $1" >&2
      exit 2
      ;;
  esac
done
# (1) the dispatch read — the firing's committed-state recomputation
DISPATCH=$(python3 "$SELF_DIR/dispatch-status.py" --root "$REPO_ROOT" 2>&1)
DRC=$?
if [ -n "$DISPATCH_OUT" ]; then
  printf '%s\n' "$DISPATCH" > "$DISPATCH_OUT"
fi
printf '%s\n' "$DISPATCH" >&2
if [ "$DRC" != "0" ]; then
  echo "hyp-resume: dispatch read failed rc=$DRC" >&2
  exit "$DRC"
fi
# (2) the capped headless invocation (at most one adoption per firing)
if [ $# -eq 0 ]; then
  echo "hyp-resume: dispatch-only firing (no invocation command supplied)" >&2
  exit 0
fi
exec "$@"
