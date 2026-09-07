#!/usr/bin/env python3
"""selftest-gated-dispatch.py -- regression test for issue #28: the Stop dispatcher
must block on the ACTIONABLE open list, never the raw open list, and allow with the
typed reason `all-open-gated` (printing the gate list once) when every open item is
gated on a human step (PARKED / BLOCKED-* / COUNTING marker in its committed status
block).

Builds its own throwaway consumer repository under a temp dir (no dependence on the
host, the caller's cwd, or any lab path), then checks the INSTALLED plugin (the tree
this file lives in):

  dispatch-status --json   carries `actionable` and `gated`; open = actionable + gated
  dispatch-status --json   gated items carry the marker and the comment note naming the gate
  dispatch-status --json   an orphan (state=running, dead pid) stays actionable despite a marker
  dispatch-status text     prints one GATED line per gated item, never ranks them
  stop-dispatch mixed      -> exit 2, top item is the actionable spec, gated ones named as such
  stop-dispatch all gated  -> exit 0, reason all-open-gated, gate list on stdout (systemMessage)
  stop-dispatch all gated  -> a second Stop still allows and consumes no cycle
  stop-dispatch all landed -> exit 0, reason artifact-check-pass (unchanged)
  stop-dispatch old surface (JSON without `actionable`) -> exit 2 as before (compat)

Usage: python3 scripts/selftest-gated-dispatch.py        exit 0 = PASS, 1 = FAIL
Provenance: hyp-machine issue #28 (consumer vault, 2026-09-04 / re-verified 2026-09-07).
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GIT_ENV = {"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd] + list(args), check=True, capture_output=True, env=GIT_ENV)


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


SPEC = ("# %s: selftest spec\n\n## Status\n%s\n\n## Hypothesis\nx\n\n## Method\nx\n\n"
        "## Binary assertions\n1. x\n\n## Verdict rule\nx\n\n## Runs\n")

PARKED = "draft <!-- PARKED — awaiting the maintainer: label the 40 samples -->"
BLOCKED = "draft <!-- BLOCKED-UPSTREAM: waiting on the vendor API key -->"
COUNTING = "draft\n<!-- COUNTING: 3 of 5 runs landed -->"


def dead_pid():
    p = subprocess.Popen(["true"], env=GIT_ENV)
    p.wait()
    return p.pid


def mk_consumer(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    write(path, ".claude/hyp.json", json.dumps({"profile": "experiments", "context": "selftest"}))
    write(path, "hypotheses/TEMPLATE.md", "# H-NNN-slug\n")
    write(path, "hypotheses/H-001-landed.md", SPEC % ("H-001-landed", "kept"))
    write(path, "hypotheses/H-002-actionable.md", SPEC % ("H-002-actionable", "draft"))
    write(path, "hypotheses/H-003-parked.md", SPEC % ("H-003-parked", PARKED))
    write(path, "hypotheses/H-004-blocked.md", SPEC % ("H-004-blocked", BLOCKED))
    write(path, "hypotheses/H-005-counting.md", SPEC % ("H-005-counting", COUNTING))
    write(path, "hypotheses/H-006-parked-orphan.md", SPEC % ("H-006-parked-orphan", PARKED))
    write(path, "experiments/runs/H-006/LANE-STATE.json", json.dumps(
        {"state": "running", "pid": dead_pid(), "host": platform.node(), "run": 1,
         "heartbeat_unix": int(time.time()), "ttl_s": 1800, "executor": "selftest"}))
    write(path, "experiments/journal-fragments/.keep", "")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")


def run_surface(root, *args):
    env = dict(GIT_ENV)
    p = subprocess.run([sys.executable, os.path.join(PLUGIN, "scripts", "dispatch-status.py"),
                        "--root", root] + list(args), capture_output=True, text=True, env=env,
                       timeout=120)
    return p


def run_hook(root, session_id, plugin_root=PLUGIN):
    env = dict(GIT_ENV)
    env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    env["CLAUDE_PROJECT_DIR"] = root
    payload = {"session_id": session_id, "cwd": root, "hook_event_name": "Stop",
               "stop_hook_active": False}
    p = subprocess.run([sys.executable, os.path.join(PLUGIN, "hooks", "scripts", "stop-dispatch.py")],
                       input=json.dumps(payload), capture_output=True, text=True, env=env,
                       cwd=root, timeout=120)
    return p


def last_log(root):
    p = os.path.join(root, ".claude", "stop-driver", "hook-log.jsonl")
    with open(p, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    return json.loads(lines[-1])


def state_cycles(root, session_id):
    p = os.path.join(root, ".claude", "stop-driver", "state-%s.json" % session_id)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("cycles")
    except OSError:
        return None


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-selftest-gated-")
    results = []

    def check(name, cond, detail):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name + ": " + str(detail)[:200])

    try:
        repo = os.path.join(tmp, "consumer")
        mk_consumer(repo)

        # --- the surface -----------------------------------------------------------
        p = run_surface(repo, "--json")
        d = json.loads(p.stdout)
        ids = lambda key: [i["id"] for i in d.get(key, [])]  # noqa: E731
        check("json-actionable-field", ids("actionable") == ["H-002", "H-006"], ids("actionable"))
        check("json-gated-field", ids("gated") == ["H-003", "H-004", "H-005"], ids("gated"))
        check("json-open-is-union", sorted(ids("open")) == sorted(ids("actionable") + ids("gated")),
              ids("open"))
        gates = {i["id"]: i.get("gate") or {} for i in d.get("gated", [])}
        check("json-gate-markers",
              [gates[i].get("marker") for i in ["H-003", "H-004", "H-005"]]
              == ["PARKED", "BLOCKED-UPSTREAM", "COUNTING"],
              [gates[i].get("marker") for i in ["H-003", "H-004", "H-005"]])
        check("json-gate-note-from-comment",
              "awaiting the maintainer" in (gates["H-003"].get("note") or "")
              and "<!--" not in (gates["H-003"].get("note") or ""),
              gates["H-003"].get("note"))
        check("json-orphan-stays-actionable", "H-006" in ids("actionable")
              and "H-006" in d.get("orphans", []), d.get("orphans"))
        p = run_surface(repo)
        txt = p.stdout
        check("text-header-counts", "6 open" not in txt and "5 open -- 2 actionable, 3 gated" in txt,
              txt.splitlines()[0] if txt else p.stderr[:120])
        check("text-gated-lines", txt.count("GATED H-00") == 3 and "GATED H-003 -- draft -- PARKED:" in txt,
              [l for l in txt.splitlines() if l.startswith("GATED")][:1])
        check("text-gated-not-ranked", not any(l.split(". ")[1].startswith("H-003")
                                               for l in txt.splitlines()
                                               if l[:1].isdigit() and ". " in l),
              "gated items never appear as ranked items")

        # --- the hook: mixed frontier -> block on the actionable top ----------------
        p = run_hook(repo, "selftest-mixed")
        check("hook-mixed-blocks", p.returncode == 2 and "Top item: H-002" in p.stderr,
              "rc=%d stderr=%s" % (p.returncode, p.stderr.strip()[:140]))
        check("hook-mixed-names-gated", "gated on a human step" in p.stderr
              and "H-003, H-004, H-005" in p.stderr, "gated ids listed, not re-presented")
        rec = last_log(repo)
        check("hook-mixed-log-fields", rec.get("reason") == "re-present"
              and rec.get("actionable") == ["H-002", "H-006"]
              and rec.get("gated") == ["H-003", "H-004", "H-005"], rec.get("actionable"))

        # --- the hook: everything open is gated -> allow, typed, gate list once ------
        write(repo, "hypotheses/H-002-actionable.md", SPEC % ("H-002-actionable", "kept"))
        os.remove(os.path.join(repo, "experiments", "runs", "H-006", "LANE-STATE.json"))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "H-002 lands; H-006 lane state retired")
        p = run_hook(repo, "selftest-gated")
        out = p.stdout.strip()
        try:
            msg = json.loads(out).get("systemMessage", "")
        except ValueError:
            msg = ""
        check("hook-all-gated-allows", p.returncode == 0, "rc=%d stderr=%s" % (p.returncode, p.stderr[:120]))
        rec = last_log(repo)
        check("hook-all-gated-reason", rec.get("decision") == "allow"
              and rec.get("reason") == "all-open-gated", rec.get("reason"))
        check("hook-all-gated-log-gates",
              [g.get("id") for g in rec.get("gates", [])] == ["H-003", "H-004", "H-005", "H-006"]
              and rec["gates"][0].get("marker") == "PARKED", rec.get("gates"))
        check("hook-all-gated-prints-list", "all-open-gated" in msg and "H-003 -- PARKED:" in msg
              and "awaiting the maintainer" in msg and "H-006" in msg, msg[:160] or out[:160])
        check("hook-all-gated-no-cycle", state_cycles(repo, "selftest-gated") in (None, 0),
              state_cycles(repo, "selftest-gated"))
        p2 = run_hook(repo, "selftest-gated")
        check("hook-all-gated-second-stop-allows", p2.returncode == 0
              and last_log(repo).get("reason") == "all-open-gated"
              and state_cycles(repo, "selftest-gated") in (None, 0),
              "rc=%d cycles=%s" % (p2.returncode, state_cycles(repo, "selftest-gated")))

        # --- the hook: everything landed -> artifact-check-pass, unchanged ------------
        for hid, slug in [("H-003", "parked"), ("H-004", "blocked"), ("H-005", "counting"),
                          ("H-006", "parked-orphan")]:
            write(repo, "hypotheses/%s-%s.md" % (hid, slug), SPEC % ("%s-%s" % (hid, slug), "discarded"))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "all land")
        p = run_hook(repo, "selftest-landed")
        check("hook-all-landed-artifact-check-pass", p.returncode == 0
              and last_log(repo).get("reason") == "artifact-check-pass", last_log(repo).get("reason"))

        # --- compat: an older surface without `actionable` grades as before ----------
        fake = os.path.join(tmp, "old-plugin")
        write(fake, "scripts/dispatch-status.py",
              "import json\nprint(json.dumps({'corpus': 'hypotheses', 'at': 'x', 'landed': {},\n"
              "  'open': [{'id': 'H-009', 'lane': 'experiments/runs/H-009', 'kind': 'draft'}]}))\n")
        p = run_hook(repo, "selftest-old-surface", plugin_root=fake)
        check("hook-old-surface-compat", p.returncode == 2 and "Top item: H-009" in p.stderr,
              "rc=%d stderr=%s" % (p.returncode, p.stderr.strip()[:120]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
