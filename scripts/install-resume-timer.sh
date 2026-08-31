#!/bin/bash
# install-resume-timer.sh <target-dir> [repo-root] — EMIT the reboot-surviving timer
# plist for a repository's scheduled resume path (scripts/hyp-resume.sh). STATIC LEG
# ONLY: this script only writes the plist file into <target-dir> and NEVER loads it —
# loading is a separate, deliberate deployment step (the maintainer's physical act):
#
#   cd <your-repo> && bash "$CLAUDE_PLUGIN_ROOT/scripts/install-resume-timer.sh" ~/Library/LaunchAgents
#   launchctl load ~/Library/LaunchAgents/com.hyp.<repo>-resume.plist
#
# so no scheduler state is ever written here. Prints the emitted path. repo-root
# defaults to the current working directory. macOS launchd only (the plist format).
#
# Ported for hyp 0.2.0 from the source lab's live install
# scripts/install-resume-timer.sh (H-217 reboot-relaunch, kept 2x5/5 2026-08-29) —
# emission logic unchanged; consumer adaptations: the label derives from the repo
# directory name, hyp-resume.sh resolves next to this script, the lab-only plugin
# toggles are dropped from the capped invocation, and the headless-auth env file is
# configurable via HYP_OAUTH_ENV (default ~/.claude/hyp-oauth-token.env — a file
# exporting CLAUDE_CODE_OAUTH_TOKEN, minted with `claude setup-token`; sourced in
# the SAME shell invocation per the headless-automation auth rule, never ambient).
# The capped invocation mirrors the validated H-217 firing caps (--max-turns 40,
# --max-budget-usd 1.50, sonnet). Wall bounding: launchd runs at most one instance
# per label, and the turn/budget caps bound each firing.
#
# Persistence contract (checked in the source lab's H-217 harness with plutil -lint
# + key presence):
#   Label          stable identity for the job
#   StartInterval  the firing cadence in seconds (1800 — the lab's ratified constant)
#   RunAtLoad      true — the first firing happens as soon as the job loads,
#                  which after a reboot is what turns timer persistence into
#                  orphan recovery.
set -u
TARGET=${1:-}
if [ -z "$TARGET" ]; then
  echo "usage: install-resume-timer.sh <target-dir> [repo-root]" >&2
  exit 2
fi
mkdir -p "$TARGET" || exit 1
SELF_DIR=$(cd "$(dirname "$0")" && pwd) || exit 1
REPO_ABS=$(cd "${2:-$PWD}" && pwd) || exit 1
REPO_SLUG=$(basename "$REPO_ABS" | tr -cd 'A-Za-z0-9._-')
LABEL="com.hyp.${REPO_SLUG:-repo}-resume"
PLIST="$TARGET/$LABEL.plist"
OAUTH_ENV=${HYP_OAUTH_ENV:-~/.claude/hyp-oauth-token.env}
PROMPT="$SELF_DIR/resume-prompt.md"
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
	<key>StartInterval</key>
	<integer>1800</integer>
	<key>RunAtLoad</key>
	<true/>
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
