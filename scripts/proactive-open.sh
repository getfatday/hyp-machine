#!/bin/sh
# proactive-open.sh — the directive's "Conversely, you open up the dashboard for me so
# that it's front and center" (consolidated decision-making directive 2026-08-28).
#
# Called by decisions.py add and decisions.py surface — NEVER by the compiler
# (compile-dashboard.py renders surfaces; it opens nothing). Behavior, once per NEW
# decision-row id (seen-ids kept in the JSON state file):
#   1. recompile via compile-dashboard.py (so the opened page already shows the new card),
#   2. open <root>/decisions.html front-and-center,
#   3. push a notification (osascript on macOS; NOTIFY_CMD override),
#   4. mark the ids seen LAST (a crash before this re-fires safely on the next surface).
# A run with no new ids does NOTHING (no re-open spam). Always exits 0.
#
# State: .claude/decision-surface-state.json — {"seen_ids": [...], "opens": N,
# "last_open_at": "<iso>"} — session-local untracked infra (gitignored), like
# .claude/harden-last.txt.
#
# Env overrides (all optional; tests point these at recorders):
#   DECISIONS_ROOT     repo root            (default: CLAUDE_PROJECT_DIR or cwd)
#   DECISIONS_LEDGER   ledger path          (default: $ROOT/ledger/work-ledger.jsonl)
#   DECISIONS_STATE    state file           (default: $ROOT/.claude/decision-surface-state.json)
#   COMPILE_CMD        recompile command    (default: python3 $ROOT/scripts/compile-dashboard.py $ROOT --quiet)
#   OPEN_CMD           opener               (default: open on darwin, xdg-open elsewhere)
#   NOTIFY_CMD         notifier             (default: osascript display notification; no-op without it)

ROOT="${DECISIONS_ROOT:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"
LEDGER="${DECISIONS_LEDGER:-$ROOT/ledger/work-ledger.jsonl}"
STATE="${DECISIONS_STATE:-$ROOT/.claude/decision-surface-state.json}"

[ -f "$LEDGER" ] || exit 0

# New decision ids = kind:"decision" ids on file minus the state's seen_ids.
# (Resolutions never trigger an open; this step only READS the state.)
new_ids=$(python3 - "$LEDGER" "$STATE" <<'PYEOF'
import json, sys
ledger, state = sys.argv[1], sys.argv[2]
seen = set()
try:
    data = json.load(open(state, encoding="utf-8"))
    if isinstance(data, dict):
        seen = {str(i) for i in data.get("seen_ids", [])}
except (OSError, ValueError):
    pass
out = []
try:
    for raw in open(ledger, encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if (isinstance(rec, dict) and rec.get("kind") == "decision"
                and rec.get("id") and str(rec["id"]) not in seen
                and str(rec["id"]) not in out):
            out.append(str(rec["id"]))
except OSError:
    pass
print(" ".join(out))
PYEOF
)
[ -n "$new_ids" ] || exit 0    # nothing new: silent no-op, no re-open

n=$(echo "$new_ids" | wc -w | tr -d ' ')

# 1. Recompile FIRST so the opened surface already carries the new card(s).
if [ -n "${COMPILE_CMD:-}" ]; then
  $COMPILE_CMD >/dev/null 2>&1
else
  timeout 10 python3 "$ROOT/scripts/compile-dashboard.py" "$ROOT" --quiet >/dev/null 2>&1 || true
fi

# 2. Open front-and-center (once per fire, however many new ids landed together).
if [ -f "$ROOT/decisions.html" ]; then
  if [ -n "${OPEN_CMD:-}" ]; then
    timeout 3 $OPEN_CMD "$ROOT/decisions.html" 2>/dev/null || true
  elif command -v open >/dev/null 2>&1; then
    timeout 3 open "$ROOT/decisions.html" 2>/dev/null || true
  elif command -v xdg-open >/dev/null 2>&1; then
    timeout 3 xdg-open "$ROOT/decisions.html" 2>/dev/null || true
  fi
fi

# 3. Push the notification.
msg="$n new decision(s) waiting: $new_ids — decisions.html is open"
if [ -n "${NOTIFY_CMD:-}" ]; then
  timeout 3 $NOTIFY_CMD "$msg" 2>/dev/null || true
elif command -v osascript >/dev/null 2>&1; then
  timeout 3 osascript -e "display notification \"$msg\" with title \"Crux — decisions waiting\"" 2>/dev/null || true
fi

# 4. Mark seen LAST (atomic tmp+rename; a crash above re-fires safely next surface).
python3 - "$STATE" $new_ids <<'PYEOF'
import datetime, json, os, sys
state = sys.argv[1]
new = sys.argv[2:]
data = {"seen_ids": [], "opens": 0, "last_open_at": None}
try:
    loaded = json.load(open(state, encoding="utf-8"))
    if isinstance(loaded, dict):
        data.update({k: loaded.get(k, data[k]) for k in data})
except (OSError, ValueError):
    pass
for i in new:
    if i not in data["seen_ids"]:
        data["seen_ids"].append(i)
data["opens"] = int(data.get("opens") or 0) + 1
data["last_open_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
os.makedirs(os.path.dirname(state) or ".", exist_ok=True)
tmp = state + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=1)
    fh.write("\n")
os.replace(tmp, state)
PYEOF

exit 0
