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
  1b. the session is not a DISPATCH PARTICIPANT (lab lane
     dispatch-participation-gate)                                 -> allow, typed
     reason not-a-participant, decided AFTER the no-dispatch-surface check (a
     repository with nothing dispatchable stays silent, as before) and BEFORE the
     dispatch read (a bystander pays nothing, not even the read), without touching
     the cycle counter, and without starting the lineage wall clock (the informed
     flag is persisted with no t0, so a session that joins later gets its full
     1800 s from its first participant Stop).
     Participation is decided from bytes the session itself carries -- never
     inferred from the transcript, never from git:
       env HYP_DISPATCH=0 / off                  -> not a participant (explicit; wins)
       env HYP_DISPATCH=1 / on                   -> participant (the plugin's own
                                                    drivers -- the resume timer, the
                                                    watch-paths driver, the portable
                                                    runner -- launch their children
                                                    with it; any other spelling reads
                                                    as unset and is logged as
                                                    participation.env_unrecognized)
       <root>/.claude/stop-driver/participants/<session_id> exists -> participant
       .claude/hyp.json "dispatch": "all"        -> every session participates (the
                                                    pre-gate behaviour, opted into per
                                                    repository; default "participants")
       otherwise                                 -> not a participant
     The first non-participant Stop of a lineage prints ONE systemMessage naming the
     three ways to join (the marker path for this session id filled in); later Stops
     in the lineage stay silent. Why: a session opened to debug an unrelated failure
     was held at Stop for a verdict on a draft spec it had never seen (source lab,
     2026-09-08). The directive this dispatcher implements named every BOUNDARY at
     which the backlog is re-derived, never which SESSIONS are backlog workers; this
     step supplies that missing boundary as explicit consent, not inference.
  2. dispatch read FAILED (timeout, nonzero exit, unparseable output) -- the
     open-work state is UNKNOWN, never graded like a pass (issue #8: a 45 s
     TimeoutExpired on a slow disk used to end the session as allow/hook-error
     with empty stderr): first consecutive failure               -> BLOCK: exit 2
     once, with a visible retry reason on stderr; second consecutive failure
     -> allow under the typed reason dispatch-error-open carrying the error
     class, so a persistently failing read costs at most one extra cycle and
     can never trap a session. Error records carry structured elapsed_s /
     error_class / root. A durable read-start line lands in the log BEFORE the
     read, so a hook killed by the outer Stop budget still leaves a trace.
  3. dispatch empty (all eligible items landed at HEAD)           -> allow, reason
     artifact-check-pass (basis recorded: landed map + HEAD sha, from git only)
  3b. dispatch non-empty but NOTHING ACTIONABLE (issue #28: every open item is
     GATED -- its committed status block carries a PARKED / BLOCKED-* / COUNTING
     marker, a human-only step the dispatch surface already masks out of its
     actionable count)                                            -> allow, reason
     all-open-gated -- only when every open item is graded (an id the surface left
     UNREAD under its read budget is unknown, re-presented like actionable, so a
     partial read never ends a session); the gate list (id, marker, note) is printed ONCE to the user
     via systemMessage so the human sees exactly what only they can do, and is
     recorded in the log. No cycle is consumed. Before this the driver read the raw
     `open` list and re-presented human-gated specs for the full 12-cycle cap
     (consumer vault, 2026-09-04). A surface without the `actionable` field (an
     older dispatch-status) grades as before: actionable = open.
  4. no cap headroom (cycles or lineage wall exhausted)           -> allow, reason
     cap-headroom-exhausted
  5. otherwise                                                    -> BLOCK: exit 2
     with the top open item re-presented on stderr (the documented Stop-hook path
     that reaches the model), cycle counter incremented

Never crashes a session: the dispatch read's own failures grade fail-closed-once
as above; any OTHER internal error still logs a traceback and allows the stop
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


PARTICIPANTS_DIRNAME = "participants"   # <root>/.claude/stop-driver/participants/<sid>
ENV_ON = ("1", "on", "yes", "true", "participant")
ENV_OFF = ("0", "off", "no", "false")


def participation(root, cfg, session_id):
    """('yes' | 'no', source, env_raw). Decided from bytes the session carries --
    its environment, a per-session marker file, or the repository's config --
    never the transcript, never git. Never raises; an unreadable signal reads as
    absent. Sources, in precedence order: env-off, env, marker, config, none.
    env_raw is the HYP_DISPATCH value when set (None when unset) so an
    unrecognised spelling (a typo) is visible in the log instead of silently
    reading as absent."""
    env_raw = os.environ.get("HYP_DISPATCH")
    try:
        env = (env_raw or "").strip().lower()
        if env in ENV_OFF:
            return "no", "env-off", env_raw
        if env in ENV_ON:
            return "yes", "env", env_raw
        sid = re.sub(r"[^A-Za-z0-9._-]", "", session_id or "")
        if sid and os.path.isfile(os.path.join(
                root, ".claude", "stop-driver", PARTICIPANTS_DIRNAME, sid)):
            return "yes", "marker", env_raw
        if str(cfg.get("dispatch") or "").strip().lower() == "all":
            return "yes", "config", env_raw
    except Exception:
        pass
    return "no", "none", env_raw


def join_message(session_id):
    sid = re.sub(r"[^A-Za-z0-9._-]", "", session_id or "") or "<session-id>"
    marker = os.path.join(".claude", "stop-driver", PARTICIPANTS_DIRNAME, sid)
    return ("Work dispatcher: this session is not a dispatch participant, so open "
            "lab work is not re-presented here (reason not-a-participant; nothing "
            "was read). A session that works the backlog joins in one of three "
            "ways: launch it with HYP_DISPATCH=1 (the plugin's own drivers do); "
            "`touch %s` to join this session only; or set \"dispatch\": \"all\" "
            "in .claude/hyp.json to make every session in this repository a "
            "participant. A bystander (a debugging session, a one-off edit) needs "
            "to do nothing. Shown once per session." % marker)


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
        # Participation gate (step 1b): consent before cost. Decided AFTER the
        # surface check (a repository with nothing dispatchable stays silent, as
        # shipped) and BEFORE the dispatch read, so a bystander never pays the
        # read. It never spends a participant's headroom: the informed flag is
        # persisted WITHOUT a lineage t0, so the 1800 s wall clock starts at the
        # first participant Stop, not at a bystander Stop that later joins. Every
        # record from here on carries the decision and its source.
        part, source, env_raw = participation(root, cfg, base["session_id"])
        base.update(participation={"decision": part, "source": source})
        if env_raw is not None and source not in ("env", "env-off"):
            base["participation"]["env_unrecognized"] = env_raw
        if part != "yes":
            informed = bool(st.get("participation_informed"))
            if not informed:
                keep = {"cycles": st.get("cycles", 0), "participation_informed": True}
                if os.path.isfile(spath):
                    keep["t0"] = st["t0"]   # a participant lineage already runs
                os.makedirs(runtime, exist_ok=True)
                save_state(spath, keep)
            base.update(decision="allow", reason="not-a-participant",
                        mechanism="exit0-systemMessage" if not informed else "exit0")
            log_line(runtime, base)
            if not informed:
                print(json.dumps({"systemMessage": join_message(base["session_id"])}))
            return 0
        # Fail-closed-once (H-280, issue #8). The dispatch read is the only
        # call here that can burn the 45 s inner budget, and a hook killed by
        # the OUTER Stop budget writes nothing at all -- so land a durable
        # read-start line BEFORE the read, then grade any read failure as
        # UNKNOWN: block once with a visible retry reason; allow only on the
        # second consecutive failure, typed dispatch-error-open. The retry
        # block leaves the cycle counter alone -- its own counter, the fail
        # streak, caps at 2, so it can never trap a session.
        wa = dict(base)
        wa.update(phase="dispatch-read-start", root=root)
        log_line(runtime, wa)
        t_read = time.monotonic()
        try:
            d = dispatch(root, surface)
            read_error = None
        except Exception as e:
            read_error = e
        elapsed_s = round(time.monotonic() - t_read, 3)
        if read_error is not None:
            streak = int(st.get("dispatch_fail_streak") or 0) + 1
            st["dispatch_fail_streak"] = streak
            os.makedirs(runtime, exist_ok=True)
            save_state(spath, st)
            with open(os.path.join(runtime, "hook-errors.log"), "a",
                      encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
            base.update(elapsed_s=elapsed_s,
                        error_class=type(read_error).__name__, root=root,
                        fail_streak=streak, detail=str(read_error)[:300])
            if streak >= 2:
                base.update(decision="allow", reason="dispatch-error-open")
                log_line(runtime, base)
                return 0
            base.update(decision="block", reason="dispatch-error-retry",
                        mechanism="exit2-stderr")
            log_line(runtime, base)
            print("Work dispatcher: the dispatch read did not complete "
                  "(%s after %.1fs), so the open-work state is UNKNOWN and "
                  "this stop is blocked once as a retry. Stop again: a "
                  "completed read resumes normal grading; a second "
                  "consecutive failure ends the session under the typed "
                  "reason dispatch-error-open. (Trail: "
                  ".claude/stop-driver/hook-log.jsonl)"
                  % (type(read_error).__name__, elapsed_s), file=sys.stderr)
            return 2
        if st.get("dispatch_fail_streak"):
            st.pop("dispatch_fail_streak", None)
            save_state(spath, st)
        open_items = d.get("open", [])
        landed = d.get("landed", {})
        # issue #28: block on the ACTIONABLE list, never the raw open list.
        # Older surfaces carry no `actionable` field -> every open item is
        # actionable, the pre-#28 grading.
        actionable = d.get("actionable")
        if not isinstance(actionable, list):
            actionable = open_items
        gated = d.get("gated") if isinstance(d.get("gated"), list) else []
        # Items in neither list (an id the surface left UNREAD under its read
        # budget) have an UNKNOWN status: graded like actionable -- re-presented,
        # never counted as gated -- so a partial read can never end a session.
        graded = set(i["id"] for i in actionable) | set(i["id"] for i in gated)
        ungraded = [i for i in open_items if i["id"] not in graded]
        base.update(head=d.get("at"), corpus=d.get("corpus"),
                    open=[i["id"] for i in open_items],
                    actionable=[i["id"] for i in actionable],
                    gated=[i["id"] for i in gated],
                    ungraded=[i["id"] for i in ungraded], landed_n=len(landed))
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
        if not actionable and not ungraded:
            gates = [{"id": i["id"], "marker": (i.get("gate") or {}).get("marker"),
                      "note": (i.get("gate") or {}).get("note"),
                      "lane": i.get("lane")} for i in gated] or \
                    [{"id": i["id"], "marker": None, "note": None,
                      "lane": i.get("lane")} for i in open_items]
            base.update(decision="allow", reason="all-open-gated",
                        gates=gates, mechanism="exit0-systemMessage")
            log_line(runtime, base)
            lines = ["Work dispatcher: %d registered item(s) open, 0 actionable "
                     "by the machine -- every open item is gated on a human "
                     "step, so this stop is allowed (reason all-open-gated). "
                     "Only you can move these:" % len(open_items)]
            for g in gates:
                lines.append("  %s -- %s: %s" % (g["id"], g["marker"] or "gated",
                                                 g["note"] or "(no note)"))
            lines.append("Each closes only by a committed terminal spec status "
                         "(kept/discarded); clear its marker once the human "
                         "step lands to make it actionable again.")
            print(json.dumps({"systemMessage": "\n".join(lines)}))
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
        top = (actionable + ungraded)[0]
        gated_note = ""
        if ungraded:
            gated_note = (" (%d open item(s) were not read within the dispatch "
                          "surface's budget and are graded as unknown: %s.)"
                          % (len(ungraded), ", ".join(i["id"] for i in ungraded)))
        if gated:
            gated_note += (" (%d further open item(s) are gated on a human "
                           "step and are not re-presented: %s.)"
                           % (len(gated), ", ".join(i["id"] for i in gated)))
        msg = ("Work dispatcher (cycle %d/%d): %d registered item(s) still "
               "open, %d actionable -- ending here is permitted only by the "
               "artifact check, and it did not pass. Top item: %s -- lane %s. "
               "Land its committed exit artifact: a line-initial spec Status "
               "verdict (kept/discarded) with its journal fragment, decided "
               "mechanically from the lane's run record -- never a promise "
               "string. One item this cycle: land the commit, then end your "
               "turn. (To silence this dispatcher for 24h: touch "
               ".claude/stop-snooze.)%s"
               % (st["cycles"], MAX_CYCLES, len(open_items), len(actionable),
                  top["id"], top["lane"], gated_note))
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
