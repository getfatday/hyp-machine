#!/usr/bin/env python3
"""Verdict-gated Stop-boundary dispatcher (see docs/workgraph.md).

Ported for hyp 0.2.0 from the source lab's live install hooks/stop-dispatch.py --
the landing of H-213 stop-driver-unattended (kept 2x5/5 2026-08-29), whose
named-next-item block shape is the surface H-230 boundary-ranked-dispatch-v2 (kept
2x5/5 2026-08-30) measured: cold sessions handed the named top item act on it
without re-deriving priorities. Decision logic, caps, and the exit-honesty log are
the lab install's; the consumer adaptations are paths/config only (repo root and
profile via hyp_config, dispatch surface = the shipped scripts/dispatch-status.py
instead of the lab's release-train reader).

The frozen rule: Stop with non-empty dispatch AND cap headroom -> exit 2
re-presenting the top item; ending a cycle is permitted only by artifact check -- a
COMMITTED exit artifact (a line-initial spec Status verdict) -- never a promise
string. The dispatch list is scripts/dispatch-status.py --json, computed from
committed bytes only, so this hook never reads the transcript and no promise string
(nor an uncommitted working-tree edit) can end a cycle. Its artifact-check exit rule
is the shared exit condition for every lower driver layer (detached chains,
cold-start re-readers, scheduled resume firings): an item is done only when the
dispatch no longer lists it at HEAD.

Decision order at every Stop:
  1. profile below `experiments`, snoozed (.claude/stop-snooze <24h -- the standing
     kill-switch for this surface), or no dispatch surface        -> allow
  2. dispatch empty (all eligible items landed at HEAD)           -> allow, reason
     artifact-check-pass (basis recorded: landed map + HEAD sha, from git only)
  3. no cap headroom (cycles or lineage wall exhausted)           -> allow, reason
     cap-headroom-exhausted
  4. otherwise                                                    -> BLOCK: exit 2
     with the top open item re-presented on stderr (the documented Stop-hook path
     that reaches the model), cycle counter incremented

Never crashes a session: any internal error logs a traceback and allows the stop
with reason hook-error -- defects surface in the log instead of hiding. Every
invocation appends one JSON line to .claude/stop-driver/hook-log.jsonl (the
exit-honesty audit trail). Caps are frozen here (the lab's H-158 law: constants
live outside the measured band, never arguments).
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import load_config, profile_at_least, resolve_root

MAX_CYCLES = 12          # dispatcher cycle cap per lineage (frozen)
WALL_CAP_S = 1800        # lineage wall cap, seconds since lineage t0 (frozen)


def plugin_root():
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def read_stdin_payload():
    """Message-as-string guard: the payload and its fields may be absent,
    strings, or objects -- never trust shapes."""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        p = json.loads(raw or "{}")
    except ValueError:
        return {"_parse_error": (raw or "")[:200]}
    if not isinstance(p, dict):
        return {"_parse_error": "payload-not-object: %r" % str(p)[:120]}
    return p


def state_path(runtime, session_id):
    """One state file per session lineage; empty/odd ids share the fallback."""
    sid = re.sub(r"[^A-Za-z0-9._-]", "", session_id or "")
    return os.path.join(runtime, "state-%s.json" % sid if sid else "state.json")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            st = {}
    except Exception:
        st = {}
    st.setdefault("t0", time.time())
    st.setdefault("cycles", 0)
    return st


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, sort_keys=True)
    os.replace(tmp, path)


def log_line(runtime, rec):
    os.makedirs(runtime, exist_ok=True)
    rec["ts"] = time.time()
    with open(os.path.join(runtime, "hook-log.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def snoozed(root):
    try:
        p = os.path.join(root, ".claude", "stop-snooze")
        return os.path.isfile(p) and \
            time.time() - os.stat(p).st_mtime < 24 * 3600
    except Exception:
        return False


def dispatch(root, surface):
    p = subprocess.run(
        [sys.executable, surface, "--root", root, "--json"],
        capture_output=True, text=True, timeout=45, cwd=root)
    if p.returncode != 0:
        raise RuntimeError("dispatch-status rc %d: %s"
                           % (p.returncode, (p.stderr or "")[:200]))
    return json.loads(p.stdout)


def main():
    payload = read_stdin_payload()
    root = resolve_root(payload)
    runtime = os.path.join(root, ".claude", "stop-driver")
    spath = state_path(runtime, str(payload.get("session_id") or ""))
    st = load_state(spath)
    base = {"hook": "stop",
            "session_id": str(payload.get("session_id") or ""),
            "stop_hook_active": bool(payload.get("stop_hook_active")),
            "payload_parse_error": payload.get("_parse_error"),
            "cycle": st["cycles"], "basis": "git"}
    try:
        cfg = load_config(root)
        if not profile_at_least(cfg, "experiments"):
            return 0   # dispatch is experiments-profile machinery; stay silent
        if snoozed(root):
            base.update(decision="allow", reason="snoozed")
            log_line(runtime, base)
            return 0
        surface = os.path.join(plugin_root(), "scripts", "dispatch-status.py")
        if not os.path.isfile(surface) or \
                not os.path.isdir(os.path.join(root, cfg["hypotheses_dir"])):
            base.update(decision="allow", reason="no-dispatch-surface",
                        detail=surface)
            log_line(runtime, base)
            return 0
        d = dispatch(root, surface)
        open_items = d.get("open", [])
        landed = d.get("landed", {})
        base.update(head=d.get("at"), corpus=d.get("corpus"),
                    open=[i["id"] for i in open_items], landed_n=len(landed))
        if payload.get("_parse_error"):
            # a malformed payload is a defect: surface it as hook-error (allow)
            base.update(decision="allow", reason="hook-error",
                        detail="stop payload unparseable")
            log_line(runtime, base)
            return 0
        if not open_items:
            base.update(decision="allow", reason="artifact-check-pass",
                        artifacts={k: v for k, v in sorted(landed.items())})
            log_line(runtime, base)
            return 0
        wall = time.time() - float(st.get("t0") or time.time())
        if st["cycles"] + 1 > MAX_CYCLES or wall > WALL_CAP_S:
            base.update(decision="allow", reason="cap-headroom-exhausted",
                        wall_s=round(wall, 1), cycle_cap=MAX_CYCLES,
                        wall_cap_s=WALL_CAP_S)
            log_line(runtime, base)
            return 0
        st["cycles"] += 1
        os.makedirs(runtime, exist_ok=True)
        save_state(spath, st)
        top = open_items[0]
        msg = ("Work dispatcher (cycle %d/%d): %d registered item(s) still "
               "open -- ending here is permitted only by the artifact check, "
               "and it did not pass. Top item: %s -- lane %s. Land its "
               "committed exit artifact: a line-initial spec Status verdict "
               "(kept/discarded) with its journal fragment, decided "
               "mechanically from the lane's run record -- never a promise "
               "string. One item this cycle: land the commit, then end your "
               "turn. (To silence this dispatcher for 24h: touch "
               ".claude/stop-snooze.)"
               % (st["cycles"], MAX_CYCLES, len(open_items), top["id"],
                  top["lane"]))
        base.update(decision="block", reason="re-present",
                    mechanism="exit2-stderr", top_item=top["id"],
                    cycle=st["cycles"])
        log_line(runtime, base)
        print(msg, file=sys.stderr)
        return 2
    except Exception:
        try:
            os.makedirs(runtime, exist_ok=True)
            with open(os.path.join(runtime, "hook-errors.log"), "a",
                      encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
            base.update(decision="allow", reason="hook-error",
                        detail=traceback.format_exc()[-300:])
            log_line(runtime, base)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
