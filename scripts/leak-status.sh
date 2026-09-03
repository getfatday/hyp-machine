#!/bin/sh
# leak-status.sh — the live wrapper for the KEPT flow-leak meter (H-246, 2x5/5).
# Builds the terminals manifest from committed chain-terminal files' git commit
# times (committed-bytes law: mtimes lie across clones; git author time doesn't),
# then runs leak-meter.py against the sealed constants. Read-only; exit 0 always.
cd "$(dirname "$0")/.." || exit 0
TSV=$(mktemp)
git ls-files 'experiments/runs/*/chain-terminal.*' | while IFS= read -r f; do
  ep=$(git log -1 --format=%at -- "$f" 2>/dev/null)
  [ -n "$ep" ] && printf '%s\t%s\n' "$f" "$ep"
done > "$TSV"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
timeout 45 python3 scripts/leak-meter.py --repo . --pinned HEAD \
  --terminals "$TSV" --constants scripts/leak-meter-constants.json \
  --now "$NOW" --selflog .claude/leak-meter-fires.log 2>/dev/null
/bin/rm -f "$TSV"
exit 0
