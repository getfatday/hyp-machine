#!/bin/bash
# install-watch-plist.sh <target-dir> [repo-root] — EMIT the launchd WatchPaths
# plist for event-triggered dispatch on a repository's unified event stream
# (H-240 watch-triggered-dispatch, kept 2x5/5 2026-09-02). STATIC LEG ONLY:
# this script only writes the plist file into <target-dir> and NEVER loads it —
# loading is a separate, deliberate deployment step (the maintainer's physical
# act; the H-217/H-240 emitted-never-auto-loaded law):
#
#   cd <your-repo> && bash "$CLAUDE_PLUGIN_ROOT/scripts/install-watch-plist.sh" ~/Library/LaunchAgents
#   launchctl load ~/Library/LaunchAgents/com.hyp.<repo>-watch.plist
#
# The watch variant lands BESIDE the interval plist (install-resume-timer.sh);
# the human chooses which to load — both emitted, never auto-loaded (the H-240
# On-keep routing). Same firing contract either way: launchd wakes
# scripts/hyp-resume.sh (one dispatch read + at most one capped adoption;
# relaunch-class actions consult the K-strikes dispatch gate) — the trigger is
# the ONLY variable the keep swapped. launchd's WatchPaths uses the same kernel
# facility as the shipped foreground watcher (scripts/watch-dispatch.py, the
# kqueue entrypoint for hosts without launchd wiring); launchd runs at most one
# instance per label, which bounds burst delivery the way the watcher's settle
# debounce does.
#
# Ported for hyp from the source lab's H-240 fixture emitter
# (experiments/runs/H-240/fixture/watchlab-template/scripts/install-watch-plist.sh)
# merged with the shipped install-resume-timer.sh conventions (H-217 port) —
# consumer adaptations: label derives from the repo directory name, WatchPaths
# targets the repo's `events_file` (.claude/hyp.json, default
# ledger/events.jsonl), hyp-resume.sh resolves next to this script, the capped
# invocation mirrors the validated H-217 firing caps (--max-turns 40,
# --max-budget-usd 1.50, sonnet), and the headless-auth env file is
# configurable via HYP_OAUTH_ENV (default ~/.claude/hyp-oauth-token.env — a
# file exporting CLAUDE_CODE_OAUTH_TOKEN, minted with `claude setup-token`;
# sourced in the SAME shell invocation, never ambient). The fixture's
# LaunchAgents-target refusal is not carried: that guard kept experiment ARMS
# from touching launchd paths; for the maintainer this emitter, like the
# interval one, writes wherever told and simply never invokes the loader.
#
# Persistence contract (the H-240 static plist leg, plutil -lint + key
# presence):
#   Label        stable identity for the job
#   WatchPaths   the event stream — launchd fires the program on writes to it
#   RunAtLoad    false — a watch job fires on appends, not on load (the
#                interval plist owns reboot recovery; this one owns latency)
set -u
TARGET=${1:-}
if [ -z "$TARGET" ]; then
  echo "usage: install-watch-plist.sh <target-dir> [repo-root]" >&2
  exit 2
fi
mkdir -p "$TARGET" || exit 1
SELF_DIR=$(cd "$(dirname "$0")" && pwd) || exit 1
REPO_ABS=$(cd "${2:-$PWD}" && pwd) || exit 1
REPO_SLUG=$(basename "$REPO_ABS" | tr -cd 'A-Za-z0-9._-')
LABEL="com.hyp.${REPO_SLUG:-repo}-watch"
PLIST="$TARGET/$LABEL.plist"
OAUTH_ENV=${HYP_OAUTH_ENV:-~/.claude/hyp-oauth-token.env}
PROMPT="$SELF_DIR/resume-prompt.md"
EVENTS_REL=$(/usr/bin/python3 - "$REPO_ABS" <<'PY'
import json, os, sys
rel = "ledger/events.jsonl"
try:
    with open(os.path.join(sys.argv[1], ".claude", "hyp.json"), encoding="utf-8") as f:
        v = (json.load(f) or {}).get("events_file")
    if isinstance(v, str) and v.strip():
        rel = v.strip().strip("/")
except Exception:
    pass
print(rel)
PY
) || exit 1
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$SELF_DIR/hyp-resume.sh</string>
		<string>--</string>
		<string>/bin/bash</string>
		<string>-c</string>
		<string>source $OAUTH_ENV &amp;&amp; exec claude -p "\$(sed "s|{{PLUGIN_SCRIPTS}}|$SELF_DIR|g" '$PROMPT')" --model sonnet --max-turns 40 --max-budget-usd 1.50 --output-format stream-json --verbose --allowedTools Read Write Edit Glob Grep Bash --disallowedTools WebFetch WebSearch Task</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$REPO_ABS</string>
	<key>WatchPaths</key>
	<array>
		<string>$REPO_ABS/$EVENTS_REL</string>
	</array>
	<key>RunAtLoad</key>
	<false/>
	<key>StandardOutPath</key>
	<string>$TARGET/$LABEL.out.log</string>
	<key>StandardErrorPath</key>
	<string>$TARGET/$LABEL.err.log</string>
</dict>
</plist>
EOF
RC=$?
if [ "$RC" != "0" ]; then
  exit 1
fi
echo "EMITTED $PLIST (NOT loaded; static persistence leg — loading is a separate deployment step)"
exit 0
