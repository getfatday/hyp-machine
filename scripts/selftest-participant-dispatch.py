#!/usr/bin/env python3
"""selftest-participant-dispatch.py -- regression test for the Stop dispatcher's
participation gate (lab lane dispatch-participation-gate): the dispatcher re-presents
open work only to sessions that declared themselves backlog participants, decided BEFORE
the dispatch read from bytes the session carries -- never the transcript, never git.

Builds its own throwaway consumer repository under a temp dir (no dependence on the
host, the caller's cwd, or any lab path), then checks the INSTALLED plugin (the tree
this file lives in):

  bystander (no signal)        -> exit 0, reason not-a-participant, no dispatch read,
                                  cycle untouched, ONE join message (marker path for
                                  this session id, HYP_DISPATCH=1, "dispatch": "all")
  bystander, second Stop       -> exit 0, same reason, stdout empty (informed once)
  HYP_DISPATCH=1               -> exit 2, block re-present (a participant; the plugin's
                                  own drivers launch children with it)
  HYP_DISPATCH=0 + dispatch=all-> exit 0, not-a-participant, source env-off (explicit wins)
  marker for this session      -> exit 2, source marker
  marker for another session   -> exit 0, not-a-participant (scope is the session)
  hyp.json "dispatch": "all"   -> exit 2, source config (the pre-gate behaviour, opted in)
  snooze + HYP_DISPATCH=1      -> exit 0, reason snoozed (the kill-switch still wins)
  capture profile              -> exit 0, no log record (silent below experiments)
  load_config                  -> default dispatch == "participants"; "all" honoured
  spawn sites                  -> install-resume-timer.sh and compile-model-workflow.py
                                  carry HYP_DISPATCH=1 (shell or Python env spelling)
  transcript-blind             -> stop-dispatch.py never names transcript_path or
                                  last_assistant_message

Usage: python3 scripts/selftest-participant-dispatch.py        exit 0 = PASS, 1 = FAIL
Provenance: source lab cause-n-effect, lane dispatch-participation-gate (2026-09-08).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(PLUGIN, "hooks", "scripts", "stop-dispatch.py")
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "scripts"))
from hyp_config import load_config  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
SPEC = ("# %s: selftest spec\n\n## Status\n%s\n\n## Hypothesis\nx\n\n## Method\nx\n\n"
        "## Binary assertions\n1. x\n\n## Verdict rule\nx\n\n## Runs\n")
FAILS = []
N = 0


def check(name, cond, detail=""):
    global N
    N += 1
    print("%s %s%s" % ("ok  " if cond else "FAIL", name, (" -- " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd] + list(args), check=True, capture_output=True, env=GIT_ENV)


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def mk_consumer(path, profile="experiments", dispatch=None):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    cfg = {"profile": profile, "context": "selftest"}
    if dispatch:
        cfg["dispatch"] = dispatch
    write(path, ".claude/hyp.json", json.dumps(cfg))
    write(path, "hypotheses/TEMPLATE.md", "# H-NNN-slug\n")
    write(path, "hypotheses/H-001-open.md", SPEC % ("H-001-open", "draft"))
    write(path, "experiments/runs/.keep", "")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")


def records(consumer):
    p = os.path.join(consumer, ".claude", "stop-driver", "hook-log.jsonl")
    out = []
    if os.path.isfile(p):
        for line in open(p, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def stop(consumer, sid, env_value=None, active=False):
    env = {"PATH": GIT_ENV["PATH"], "HOME": GIT_ENV["HOME"], "LC_ALL": "C",
           "CLAUDE_PLUGIN_ROOT": PLUGIN, "CLAUDE_PROJECT_DIR": consumer}
    if env_value is not None:
        env["HYP_DISPATCH"] = env_value
    payload = json.dumps({"session_id": sid, "transcript_path": "/dev/null", "cwd": consumer,
                          "hook_event_name": "Stop", "stop_hook_active": active})
    n0 = len(records(consumer))
    p = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True,
                       env=env, cwd=consumer, timeout=90)
    recs = records(consumer)[n0:]
    dec = [r for r in recs if "decision" in r]
    reads = [r for r in recs if r.get("phase") == "dispatch-read-start"]
    return {"rc": p.returncode, "out": p.stdout, "err": p.stderr,
            "dec": dec[0] if len(dec) == 1 else None, "reads": len(reads), "n": len(recs)}


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-selftest-participant-")
    try:
        # bystander: released, no read, informed once
        c = os.path.join(tmp, "bystander"); mk_consumer(c)
        s1 = stop(c, "sid-a")
        check("bystander stop1 exit 0 not-a-participant", s1["rc"] == 0 and s1["dec"]
              and s1["dec"]["reason"] == "not-a-participant", "rc=%s dec=%s" % (s1["rc"], s1["dec"]))
        check("bystander performs no dispatch read", s1["reads"] == 0)
        check("bystander record carries participation none", (s1["dec"] or {}).get("participation")
              == {"decision": "no", "source": "none"})
        msg = ""
        try:
            msg = json.loads(s1["out"]).get("systemMessage", "")
        except ValueError:
            pass
        check("bystander join message names the three ways", "HYP_DISPATCH=1" in msg
              and "participants/sid-a" in msg and '"dispatch": "all"' in msg, msg[:80])
        s2 = stop(c, "sid-a")
        check("bystander stop2 silent, still released", s2["rc"] == 0 and s2["out"].strip() == ""
              and (s2["dec"] or {}).get("reason") == "not-a-participant")
        st = json.load(open(os.path.join(c, ".claude", "stop-driver", "state-sid-a.json")))
        check("bystander cycle untouched", st.get("cycles") == 0, "cycles=%s" % st.get("cycles"))

        # env participant
        c = os.path.join(tmp, "env"); mk_consumer(c)
        s = stop(c, "sid-b", env_value="1")
        check("HYP_DISPATCH=1 -> block re-present", s["rc"] == 2 and (s["dec"] or {}).get("reason")
              == "re-present" and s["dec"].get("participation", {}).get("source") == "env",
              "rc=%s dec=%s" % (s["rc"], s["dec"]))

        # explicit off wins over config all
        c = os.path.join(tmp, "envoff"); mk_consumer(c, dispatch="all")
        s = stop(c, "sid-c", env_value="0")
        check("HYP_DISPATCH=0 wins over dispatch=all", s["rc"] == 0
              and (s["dec"] or {}).get("participation", {}).get("source") == "env-off")

        # marker: this session vs another
        c = os.path.join(tmp, "marker"); mk_consumer(c)
        write(c, ".claude/stop-driver/participants/sid-d", "")
        s = stop(c, "sid-d")
        check("marker for this session -> block", s["rc"] == 2
              and (s["dec"] or {}).get("participation", {}).get("source") == "marker")
        s = stop(c, "sid-e")
        check("marker for another session -> released", s["rc"] == 0
              and (s["dec"] or {}).get("reason") == "not-a-participant")

        # config all
        c = os.path.join(tmp, "all"); mk_consumer(c, dispatch="all")
        s = stop(c, "sid-f")
        check("dispatch=all -> block (pre-gate behaviour, opted in)", s["rc"] == 2
              and (s["dec"] or {}).get("participation", {}).get("source") == "config")

        # snooze still wins
        c = os.path.join(tmp, "snooze"); mk_consumer(c)
        write(c, ".claude/stop-snooze", "")
        s = stop(c, "sid-g", env_value="1")
        check("snooze wins over participation", s["rc"] == 0 and (s["dec"] or {}).get("reason") == "snoozed")

        # capture profile silent
        c = os.path.join(tmp, "capture"); mk_consumer(c, profile="capture")
        s = stop(c, "sid-h", env_value="1")
        check("capture profile silent", s["rc"] == 0 and s["n"] == 0)

        # late join: a bystander Stop must not start the lineage wall clock
        c = os.path.join(tmp, "latejoin"); mk_consumer(c)
        s1 = stop(c, "sid-i")
        sp = os.path.join(c, ".claude", "stop-driver", "state-sid-i.json")
        st = json.load(open(sp))
        check("bystander state carries no t0", s1["rc"] == 0 and "t0" not in st, "state=%s" % st)
        write(c, ".claude/stop-driver/participants/sid-i", "")
        s2 = stop(c, "sid-i")
        check("late joiner gets full headroom (block at cycle 1)", s2["rc"] == 2
              and (s2["dec"] or {}).get("reason") == "re-present" and s2["dec"].get("cycle") == 1
              and s2["dec"].get("participation", {}).get("source") == "marker",
              "rc=%s dec=%s" % (s2["rc"], s2["dec"]))

        # no dispatch surface: silent as before, no join nag
        c = os.path.join(tmp, "nosurface"); os.makedirs(c)
        git(c, "init", "-q", "-b", "main")
        write(c, ".claude/hyp.json", json.dumps({"profile": "experiments", "context": "selftest"}))
        write(c, "README.md", "no hypotheses dir\n")
        git(c, "add", "-A"); git(c, "commit", "-q", "-m", "seed")
        s = stop(c, "sid-j")
        check("no dispatch surface -> no-dispatch-surface, silent", s["rc"] == 0
              and (s["dec"] or {}).get("reason") == "no-dispatch-surface" and s["out"].strip() == "")

        # unrecognised env spelling is visible in the record
        c = os.path.join(tmp, "typo"); mk_consumer(c)
        s = stop(c, "sid-k", env_value="ture")
        check("unrecognised HYP_DISPATCH logged", s["rc"] == 0
              and (s["dec"] or {}).get("participation", {}).get("env_unrecognized") == "ture")

        # launchd exec-string contract: the space after -p is part of it
        rt = open(os.path.join(PLUGIN, "scripts", "install-resume-timer.sh"), encoding="utf-8").read()
        check("resume-timer exec string intact",
              'export HYP_DISPATCH=1 &amp;&amp; exec claude -p "' in rt and 'exec claude -p"' not in rt)

        # config defaults
        check("load_config default dispatch=participants",
              load_config(os.path.join(tmp, "bystander")).get("dispatch") == "participants")
        check("load_config honours dispatch=all",
              load_config(os.path.join(tmp, "all")).get("dispatch") == "all")

        # static: spawn sites + transcript-blind
        for rel in ("scripts/install-resume-timer.sh", "scripts/compile-model-workflow.py"):
            text = open(os.path.join(PLUGIN, rel), encoding="utf-8").read()
            check("%s carries HYP_DISPATCH=1" % rel, bool(re.search(r'HYP_DISPATCH\s*=\s*"?1"?', text)))
        hook = open(HOOK, encoding="utf-8").read()
        check("stop-dispatch.py is transcript-blind",
              "transcript_path" not in hook and "last_assistant_message" not in hook)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("selftest-participant-dispatch: %d/%d" % (N - len(FAILS), N))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
