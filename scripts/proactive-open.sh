#!/bin/sh
# proactive-open.sh — open the decision surface front-and-center when NEW decisions land.
#
# PROVENANCE — port of the source lab's scripts/proactive-open.sh (decision kit,
# consolidated decision-making directive 2026-08-28; selftest-proven once-per-id
# guard). Differences from the lab copy: the ledger default resolves through
# .claude/hyp.json ledger_file (default ledger/ledger.jsonl), and the compile
# fallback finds compile-dashboard.py beside this script (plugin home) when the
# consumer repo has no scripts/ copy. Behavior is otherwise identical.
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
#   DECISIONS_LEDGER   ledger path          (default: $ROOT/<hyp.json ledger_file>, ledger/ledger.jsonl)
#   DECISIONS_STATE    state file           (default: $ROOT/.claude/decision-surface-state.json)
#   COMPILE_CMD        recompile command    (default: compile-dashboard.py from $ROOT/scripts/, else beside this script)
#   OPEN_CMD           opener               (default: open on darwin, xdg-open elsewhere)
#   NOTIFY_CMD         notifier             (default: osascript display notification; no-op without it)

ROOT="${DECISIONS_ROOT:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"
HERE=$(dirname "$0")
if [ -z "${DECISIONS_LEDGER:-}" ]; then
  DECISIONS_LEDGER="$ROOT/$(python3 - "$ROOT" <<'PYEOF2'
import json, os, sys
rel = "ledger/ledger.jsonl"
try:
    data = json.load(open(os.path.join(sys.argv[1], ".claude", "hyp.json"), encoding="utf-8"))
    val = data.get("ledger_file") if isinstance(data, dict) else None
    if isinstance(val, str) and val.strip():
        rel = val.strip().strip("/")
except (OSError, ValueError):
    pass
print(rel)
PYEOF2
)"
fi
LEDGER="$DECISIONS_LEDGER"
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
elif [ -f "$ROOT/scripts/compile-dashboard.py" ]; then
  python3 "$ROOT/scripts/compile-dashboard.py" "$ROOT" --quiet >/dev/null 2>&1
elif [ -f "$HERE/compile-dashboard.py" ]; then
  python3 "$HERE/compile-dashboard.py" "$ROOT" --quiet >/dev/null 2>&1
fi

# 2. Open front-and-center (once per fire, however many new ids landed together).
if [ -f "$ROOT/decisions.html" ]; then
  if [ -n "${OPEN_CMD:-}" ]; then
    $OPEN_CMD "$ROOT/decisions.html" 2>/dev/null
  elif command -v open >/dev/null 2>&1; then
    open "$ROOT/decisions.html" 2>/dev/null
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$ROOT/decisions.html" 2>/dev/null
  fi
fi

# 3. Push the notification.
msg="$n new decision(s) waiting: $new_ids — decisions.html is open"
if [ -n "${NOTIFY_CMD:-}" ]; then
  $NOTIFY_CMD "$msg" 2>/dev/null
elif command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"$msg\" with title \"Hyp Machine — decisions waiting\"" 2>/dev/null
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
