#!/usr/bin/env python3
"""lane-takeover.py -- the ONLY lawful path into another executor's lane
(see docs/workgraph.md).

Ported for hyp 0.2.0 from the source lab's live install scripts/lane-takeover.py --
the On-keep landing of H-216 ttl-steal (kept 2x5/5 2026-08-29). The decision logic
(pure decide() over committed LANE-STATE fields, typed refusal, grant flow,
record-before-write commit order, canonical outcome JSON, exit codes) is the lab
install's, which itself landed the kept reference implementation with path/config
adaptation only. Consumer adaptations here: repo-root resolution (--root, then
CLAUDE_PROJECT_DIR, then cwd) and the lane directory through `.claude/hyp.json`
(runs_dir).

LANE-LAW (the kept rule):
  1. Liveness is read from committed LANE-STATE.json fields only --
     heartbeat_unix age against the pinned ttl_s (ttl_s = 1800, the lab's
     ratified constant). Never from a process table, never from a pid check,
     never by manual inference: any reader on any host computes the same
     decision from the same committed bytes. LANE-STATE.json is the liveness
     surface. The recorded pid is provenance, never a decision input.
  2. FRESH (now - heartbeat_unix <= ttl_s): takeover is REFUSED with a typed
     refusal and ZERO writes inside the lane scope.
  3. EXPIRED (now - heartbeat_unix > ttl_s): takeover is GRANTED, and the
     grant MUST commit the attributed takeover record (new executor, prior
     executor, heartbeat age, reason) BEFORE the first write inside the lane --
     enforced by git parent-child commit order, never by timestamps.
  4. This tool implements 1-3 and is the consult contract for every driver
     layer -- the Stop-hook dispatcher, detached chains, the scheduled cold
     resume path, and any live session -- before entering a lane whose
     LANE-STATE names another executor. Hand-editing another executor's lane
     is a violation regardless of heartbeat age.

Grant flow (record-before-write law):
  1. commit the attributed takeover record at takeovers/<lane>-takeover.json
     -- BEFORE any write inside the lane scope, enforced by commit order;
  2. rewrite <runs_dir>/<lane>/LANE-STATE.json: new executor, fresh
     heartbeat, state "adopted"; commit;
  3. execute the lane's seeded next_command (append-resume-line: append the
     resume line to the lane's WORK.md); commit. Any other next_command value
     is carried forward in the rewritten LANE-STATE for the new executor.

Both arms: the canonical decision JSON is written to takeover-outcome.json at
the repo root (add it to your .gitignore -- per-invocation scratch; the
committed record is takeovers/) and printed as the final "DECISION: " line.
The canonical form (sorted keys, compact separators, integer fields) is
byte-stable over identical (state, now) inputs.

Exit codes (typed; acceptance is artifact-based, never bare rc):
  0 grant completed   3 typed refusal   4 error
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

CONFIG_RELPATH = os.path.join(".claude", "hyp.json")
DEFAULT_RUNS_DIR = "experiments/runs"

SCHEMA_FIELDS = ("executor", "host", "pid", "heartbeat_unix", "ttl_s",
                 "state", "halt_reason", "next_command")
REFUSE_REASON = "heartbeat-fresh: age_s <= ttl_s (lane may be alive)"
GRANT_REASON = "heartbeat-expired: age_s > ttl_s (lane provably stale)"
RULE = "refuse if now_unix - heartbeat_unix <= ttl_s else grant"


def runs_dir_for(root):
    """runs_dir from <root>/.claude/hyp.json; the default on any failure.
    Never raises."""
    try:
        with open(os.path.join(root, CONFIG_RELPATH), encoding="utf-8") as f:
            data = json.load(f)
        val = data.get("runs_dir") if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip().strip("/")
    except Exception:
        pass
    return DEFAULT_RUNS_DIR


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def load_state(path):
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise ValueError("LANE-STATE is not a JSON object")
    for k in SCHEMA_FIELDS:
        if k not in state:
            raise ValueError("LANE-STATE missing schema field %r" % k)
    if not isinstance(state["heartbeat_unix"], int) \
            or not isinstance(state["ttl_s"], int):
        raise ValueError("heartbeat_unix / ttl_s must be integers")
    return state


def decide(state, now_unix):
    """Pure takeover decision from committed LANE-STATE fields alone.

    Inputs: the eight committed schema fields and one now-instant. No file
    reads, no clocks, no process inspection -- replayable byte-identically on
    any checkout of the spine.
    """
    hb = state["heartbeat_unix"]
    ttl = state["ttl_s"]
    age = now_unix - hb
    fresh = age <= ttl
    return {
        "age_s": age,
        "decision": "refuse" if fresh else "grant",
        "heartbeat_unix": hb,
        "lane": str(state.get("lane_id") or ""),
        "now_unix": now_unix,
        "prior_executor": str(state["executor"]),
        "prior_pid": state["pid"],
        "reason": REFUSE_REASON if fresh else GRANT_REASON,
        "rule": RULE,
        "ttl_s": ttl,
    }


def git(root, ident, args):
    cmd = ["git", "-C", root,
           "-c", "user.name=%s" % ident,
           "-c", "user.email=%s@lane-takeover.local" % ident.lower(),
           "-c", "commit.gpgsign=false"] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s"
                           % (args[:2], str(p.stderr).strip()[:200]))
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None,
                    help="repo root (default: CLAUDE_PROJECT_DIR, then cwd)")
    ap.add_argument("--lane", required=True,
                    help="lane id or dir, e.g. H-216 or <runs_dir>/H-216")
    ap.add_argument("--executor", required=True,
                    help="the takeover candidate (new executor)")
    ap.add_argument("--now", type=int, default=None,
                    help="decision instant override (replay/audit only; "
                         "default: current unix time)")
    o = ap.parse_args()

    if o.root:
        root = os.path.abspath(o.root)
    else:
        env_root = os.environ.get("CLAUDE_PROJECT_DIR")
        root = env_root if env_root and os.path.isdir(env_root) \
            else os.getcwd()
    runs_rel = runs_dir_for(root)
    lane_id = os.path.basename(os.path.normpath(o.lane))
    lane_dir = os.path.join(root, runs_rel, lane_id)
    state_path = os.path.join(lane_dir, "LANE-STATE.json")
    outcome_path = os.path.join(root, "takeover-outcome.json")

    def emit(dec_obj, code):
        line = canonical(dec_obj)
        with open(outcome_path, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        print("DECISION: " + line)
        return code

    try:
        state = load_state(state_path)
        state["lane_id"] = lane_id
        now = o.now if o.now is not None else int(time.time())
        dec = decide(state, now)

        if dec["decision"] == "refuse":
            # typed refusal; ZERO writes inside the lane scope
            return emit(dec, 3)

        # ---- grant: attributed record commit BEFORE any lane write ----
        record = {
            "decision_record": canonical(dec),
            "heartbeat_age_s": dec["age_s"],
            "lane": lane_id,
            "new_executor": o.executor,
            "prior_executor": dec["prior_executor"],
            "prior_pid": dec["prior_pid"],
            "reason": dec["reason"],
            "ttl_s": dec["ttl_s"],
        }
        rec_rel = os.path.join("takeovers", "%s-takeover.json" % lane_id)
        rec_abs = os.path.join(root, rec_rel)
        os.makedirs(os.path.dirname(rec_abs), exist_ok=True)
        with open(rec_abs, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=1, sort_keys=True)
            f.write("\n")
        git(root, o.executor, ["add", "--", rec_rel])
        git(root, o.executor,
            ["commit", "-q", "--no-verify", "-m",
             "takeover: %s adopted by %s (age_s=%d > ttl_s=%d) -- attributed "
             "record precedes every lane write" % (lane_id, o.executor,
                                                   dec["age_s"],
                                                   dec["ttl_s"])])

        # first lane write: LANE-STATE rewritten (new executor, fresh
        # heartbeat), only AFTER the record commit above
        new_state = {
            "executor": o.executor,
            "halt_reason": None,
            "heartbeat_unix": now,
            "host": socket.gethostname(),
            "next_command": state["next_command"],
            "pid": os.getpid(),
            "state": "adopted",
            "ttl_s": dec["ttl_s"],
        }
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=1, sort_keys=True)
            f.write("\n")
        state_rel = os.path.relpath(state_path, root)
        git(root, o.executor, ["add", "--", state_rel])
        git(root, o.executor,
            ["commit", "-q", "--no-verify", "-m",
             "lane-adopt: %s LANE-STATE rewritten (%s -> %s, fresh heartbeat)"
             % (lane_id, dec["prior_executor"], o.executor)])

        # seeded next_command
        if state["next_command"] == "append-resume-line":
            work_rel = os.path.join(os.path.relpath(lane_dir, root),
                                    "WORK.md")
            with open(os.path.join(root, work_rel), "a",
                      encoding="utf-8") as f:
                f.write("resumed-by: %s after takeover of %s "
                        "(heartbeat_age_s=%d)\n"
                        % (o.executor, lane_id, dec["age_s"]))
            git(root, o.executor, ["add", "--", work_rel])
            git(root, o.executor,
                ["commit", "-q", "--no-verify", "-m",
                 "lane-work: %s resume line appended per next_command"
                 % lane_id])
        return emit(dec, 0)
    except Exception as e:  # typed error, never a silent crash
        err = {"decision": "error", "detail": str(e)[:200],
               "lane": lane_id}
        try:
            return emit(err, 4)
        except OSError:
            print("DECISION: " + canonical(err))
            return 4


if __name__ == "__main__":
    sys.exit(main())
