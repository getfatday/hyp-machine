#!/bin/bash
# checkpoint-shadow.sh -- the grade leg's compile line with one durable row per build
# (checkpoint-compiled-events lane: the signal emitter the checkpoint-gate knob consumes).
#
# Stands in for the advisory line
#     "$PY" "$COMPILER" "$RD" --out "$OUT" || echo "checkpoint advisory rc=$?"
# byte-for-byte on stdout and exit status: it runs the compiler untouched with stdout and
# stderr passed through, prints `checkpoint advisory rc=<rc>` to stdout exactly when rc is
# non-zero, then appends ONE canonical `event/checkpoint-compiled` row and exits 0 whatever
# happens after the compiler (the append step can never change the leg's exit; everything
# the append step prints goes to the leg's stderr, never its stdout).
#
# usage: checkpoint-shadow.sh <run-dir> [compiler args...]        (e.g. --out PATH)
# env:   PY                     python (default /usr/bin/python3)
#        REPO_ROOT              subject root: the run dir is recorded relative to it
#                               (default: nearest ancestor of <run-dir> holding hypotheses/)
#        COMPILER               the compiler (default $REPO_ROOT/scripts/compile-run-checkpoint.py)
#        HYP_ROOT               consumer root whose .claude/hyp.json may name an events_file
#                               (default $REPO_ROOT): the row goes through emit-event.py
#                               checkpoint-compiled when it does (profile gate and node
#                               validation as shipped), else the same canonical line is
#                               appended, deduplicated on exact bytes, to
#                               $HYP_ROOT/ledger/knob-signals/checkpoint-compiled.jsonl
#        EVENTS_IMPL            dir holding events_lib.py + emit-event.py (default ../impl of this file)
#        CHECKPOINT_EVENT_DATE  caller-pinned YYYY-MM-DD; unset = append nothing, one stderr line
#
# Row (schema v1, closed key set):
#   {"schema":"v1","instance-of":"event/checkpoint-compiled",
#    "caused-by":"scripts/compile-run-checkpoint.py@<first 12 hex of sha256($COMPILER as invoked)>",
#    "date":$CHECKPOINT_EVENT_DATE,"subject":<run dir relative to REPO_ROOT>,
#    "payload":{"rc":<rc>,"class":<exit-table name; emitted for 0; untyped off-table>,
#               "lane":<run dir's parent basename>,"run":<run dir's basename>}}
# No wall clock is read anywhere in this file; the date is the caller's.
set -u
RD=${1:?usage: checkpoint-shadow.sh <run-dir> [compiler args...]}
shift
PY=${PY:-/usr/bin/python3}
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -z "${REPO_ROOT:-}" ]; then
  REPO_ROOT=$(cd "$RD" 2>/dev/null && pwd)
  while [ -n "$REPO_ROOT" ] && [ "$REPO_ROOT" != "/" ] && [ ! -d "$REPO_ROOT/hypotheses" ]; do
    REPO_ROOT=$(dirname "$REPO_ROOT")
  done
fi
COMPILER=${COMPILER:-$REPO_ROOT/scripts/compile-run-checkpoint.py}
HYP_ROOT=${HYP_ROOT:-$REPO_ROOT}
EVENTS_IMPL=${EVENTS_IMPL:-$HERE/../impl}

# --- the compiler, bytes untouched, stdout/stderr passed through; the advisory line's bytes
"$PY" "$COMPILER" "$RD" ${1+"$@"}
RC=$?
if [ "$RC" != "0" ]; then
  echo "checkpoint advisory rc=$RC"
fi

# --- one row, appended with `|| true`; this step's stdout is redirected to the leg's stderr
CHECKPOINT_SHADOW_RC="$RC" CHECKPOINT_SHADOW_RD="$RD" CHECKPOINT_SHADOW_REPO_ROOT="$REPO_ROOT" \
CHECKPOINT_SHADOW_COMPILER="$COMPILER" CHECKPOINT_SHADOW_HYP_ROOT="$HYP_ROOT" \
CHECKPOINT_SHADOW_IMPL="$EVENTS_IMPL" "$PY" - 1>&2 <<'PYEOF' || true
import hashlib
import json
import os
import subprocess
import sys

rc = int(os.environ["CHECKPOINT_SHADOW_RC"])
rd = os.path.abspath(os.environ["CHECKPOINT_SHADOW_RD"])
root = os.path.abspath(os.environ["CHECKPOINT_SHADOW_REPO_ROOT"])
compiler = os.environ["CHECKPOINT_SHADOW_COMPILER"]
hyp_root = os.path.abspath(os.environ["CHECKPOINT_SHADOW_HYP_ROOT"])
impl = os.path.abspath(os.environ["CHECKPOINT_SHADOW_IMPL"])
date = os.environ.get("CHECKPOINT_EVENT_DATE", "").strip()


def note(msg):
    sys.stdout.write("checkpoint-shadow: %s\n" % msg)


if not date:
    note("CHECKPOINT_EVENT_DATE unset; no row appended for %s" % rd)
    sys.exit(0)
sys.path.insert(0, impl)
try:
    import events_lib
except Exception as e:  # noqa: BLE001
    note("events_lib unavailable under %s (%s); no row appended" % (impl, e))
    sys.exit(0)
try:
    with open(compiler, "rb") as f:
        csha = hashlib.sha256(f.read()).hexdigest()[:12]
except OSError as e:
    note("compiler unreadable (%s); no row appended" % e)
    sys.exit(0)
subject = os.path.relpath(rd, root).replace(os.sep, "/")
if subject.startswith(".") or subject.startswith("/"):
    note("subject %r escapes REPO_ROOT %s; no row appended" % (subject, root))
    sys.exit(0)
payload = {"rc": rc, "class": events_lib.checkpoint_class(rc),
           "lane": os.path.basename(os.path.dirname(rd)), "run": os.path.basename(rd)}
caused_by = "scripts/compile-run-checkpoint.py@" + csha

events_file = None
try:
    with open(os.path.join(hyp_root, ".claude", "hyp.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    v = cfg.get("events_file") if isinstance(cfg, dict) else None
    if isinstance(v, str) and v.strip():
        events_file = v.strip()
except Exception:  # noqa: BLE001
    events_file = None

if events_file:
    cmd = [sys.executable, os.path.join(impl, "emit-event.py"), "--root", hyp_root,
           "checkpoint-compiled", "--subject", subject, "--rc", str(rc),
           "--class", payload["class"], "--lane", payload["lane"], "--run", payload["run"],
           "--date", date, "--caused-by", caused_by]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    note("emit-event rc=%d %s %s" % (p.returncode, p.stdout.strip(), p.stderr.strip()))
    sys.exit(0)

# fallback: the same canonical line, deduplicated on exact bytes, under the consumer root
rec = events_lib.make_record("event/checkpoint-compiled", caused_by, date, subject, payload)
line = events_lib.canonical(rec)
path = os.path.join(hyp_root, "ledger", "knob-signals", "checkpoint-compiled.jsonl")
os.makedirs(os.path.dirname(path), exist_ok=True)
existing = []
if os.path.isfile(path):
    with open(path, encoding="utf-8") as f:
        existing = [ln for ln in f.read().splitlines(True) if ln.strip()]
if line in existing:
    note("fallback: identical row already in %s" % path)
    sys.exit(0)
with open(path, "a", encoding="utf-8") as f:
    f.write(line)
note("fallback: appended to %s" % path)
PYEOF
exit 0
