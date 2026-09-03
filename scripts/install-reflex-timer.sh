#!/bin/bash
# install-reflex-timer.sh <target-dir> — EMIT the reflex-check sensor timer plist.
# STATIC LEG ONLY (H-217 emitted-never-auto-loaded law, reused by H-251): this
# script only writes the plist file into <target-dir> and NEVER loads it —
# loading is a separate, deliberate post-keep deployment step (the maintainer's
# physical act):
#
#   bash scripts/install-reflex-timer.sh ~/Library/LaunchAgents
#   launchctl load ~/Library/LaunchAgents/com.cause-n-effect.reflex-check.plist
#
# Modeled byte-for-byte on the kept H-217 pattern
# (scripts/install-resume-timer.sh) with the payload swapped for the SENSOR:
# reflex-check is a separate, dumber process whose firing cannot hang with the
# thing it watches (sensor/actuator split; it never dispatches, so the payload
# carries no claude invocation, no token, no turn caps). A linted reference
# emission for this checkout ships at scripts/com.cause-n-effect.reflex-check.plist.
#
# Landed from the KEPT reference implementation (H-251 reflex-timer-cold-detection,
# 2x5/5 2026-09-02) experiments/runs/H-251/fixture/impl/install-reflex-timer.sh —
# emission logic unchanged; label adapted from the mini-lab's com.minilab.* to
# this repo's com.cause-n-effect.* naming.
#
# Persistence contract (checked by the H-251 static plist leg with plutil -lint
# + key presence): Label (stable identity), StartInterval (1800 s = the frozen
# 30m tick), RunAtLoad true (first firing on load).
set -u
TARGET=${1:-}
if [ -z "$TARGET" ]; then
  echo "usage: install-reflex-timer.sh <target-dir>" >&2
  exit 2
fi
mkdir -p "$TARGET" || exit 1
ROOT_ABS=$(cd "$(dirname "$0")/.." && pwd)
LABEL="com.cause-n-effect.reflex-check"
PLIST="$TARGET/$LABEL.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>/usr/bin/python3</string>
		<string>$ROOT_ABS/scripts/reflex-check</string>
		<string>--root</string>
		<string>$ROOT_ABS</string>
		<string>--trigger</string>
		<string>timer</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$ROOT_ABS</string>
	<key>StartInterval</key>
	<integer>1800</integer>
	<key>RunAtLoad</key>
	<true/>
	<key>StandardOutPath</key>
	<string>$ROOT_ABS/.claude/reflex/launchd.out.log</string>
	<key>StandardErrorPath</key>
	<string>$ROOT_ABS/.claude/reflex/launchd.err.log</string>
</dict>
</plist>
EOF
RC=$?
if [ "$RC" != "0" ]; then
  exit 1
fi
echo "EMITTED $PLIST (NOT loaded; static persistence leg — loading is a separate deployment step)"
exit 0
