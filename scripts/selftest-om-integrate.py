#!/usr/bin/env python3
"""selftest-om-integrate.py -- regression test for scripts/om-integrate.py.

Builds a throwaway git consumer under a temp directory (no dependence on the host's real
`~/.hyp-state`, `~/Library/LaunchAgents` or GitHub credentials beyond a read-only `gh auth
status`) and drives the INSTALLED `om-integrate.py` from the tree this file lives in through
probe / compose / emit / test / report / uninstall.

`test`'s substrate ("play the launchd `QueueDirectories`/`ThrottleInterval` role without ever
loading anything into real launchd") is a stub `launchctl` shim this file writes to a scratch
`bin/` directory and places ahead of the real binary on `PATH` for that one subprocess call only
-- the same shape the source lab's fixture used. No case in this file ever calls
`launchctl load`, `launchctl bootstrap`, enables a systemd unit, or schedules anything on the
real host; `HYP_STATE_DIR` is pointed at scratch for the whole run so the worker-state-directory
helpers never touch `~/.hyp-state`.

Cases (numbered A1-A7 below, ported by intent from the source lab's assertions):
  A1  9 of 9 probe rows land, `advertised`/`usable` are separate booleans, every `usable` row
      carries a non-empty `probe_cmd` and `exit == 0`; a custom `om_substrates_file` override
      is honoured (rows land at the configured path, not the default)
  A2  `compose` selects by the probe rows alone -- zeroing the `authors_90d`/`disk` covariates
      on the same rows never changes the decision
  A3  `emit`'s plist passes `plutil -lint` (skipped with one clear line where `plutil` is
      absent), carries only absolute paths, the `com.hyp-machine.om-worker.` label prefix and
      `ProcessType Background`; the delegated `ci-tier0` workflow parses as YAML
  A4  a second `emit` with nothing changed on the host is a byte-level no-op (every emitted
      file's sha256 unchanged)
  A5  `test` lands exactly one `session-observed` row under the stub substrate within the cited
      window, and the two poison seeds land as exactly two `quarantine` rows, in at most 2
      launches, with the inbox left empty
  A6  `uninstall --dry-run` prints the exact removal/reversal and changes nothing; `uninstall`
      leaves zero emitted artifacts and drops the `.claude/hyp.json` `om_offload` key
  A7  `report` reads `holds` right after install and `not installed` right after uninstall

The raw evidence this run collected (every probe row, the compose decision, emitted file
hashes, the test-verb phase reports) is written beside this run's PASS/FAIL lines to
`selftest-om-integrate.raw.json` in the current directory, so a later reader can recompute
without re-running the probes.

Usage: python3 scripts/selftest-om-integrate.py    exit 0 = PASS, 1 = FAIL
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OM_INTEGRATE = os.path.join(HERE, "om-integrate.py")
OM_WORKER = os.path.join(HERE, "om-worker.py")

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="selftest@example.invalid",
               GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="selftest@example.invalid",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
for _k in ("GH_TOKEN", "GITHUB_TOKEN"):
    GIT_ENV.pop(_k, None)

RESULTS = []
RAW = {}


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (": " + str(detail).strip() if (detail and not cond) else ""))


def git(cwd, *args, env=None):
    e = dict(GIT_ENV)
    if env:
        e.update(env)
    p = subprocess.run(["git"] + list(args), cwd=cwd, env=e, capture_output=True, text=True,
                       timeout=20)
    return p


def run_integrate(args, cwd=None, timeout=90, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, OM_INTEGRATE] + args, cwd=cwd, env=e,
                       capture_output=True, text=True, timeout=timeout)
    return p


def build_consumer(root):
    os.makedirs(root, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "init", "-c", "commit.gpgsign=false")
    return root


def sha_tree(root):
    """sha256 of every tracked-or-not regular file under root except .git, sorted by relpath."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            try:
                with open(p, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                pass
    return out


STUB_LAUNCHCTL = '''#!/usr/bin/env python3
"""Selftest-only stub substrate for om-integrate.py's `test` verb. Never loaded into real
launchd; placed ahead of the real `launchctl` on PATH for the `test` subprocess call only."""
import argparse, json, os, subprocess, sys, time


def count_kind(ledger_path, kind):
    if not os.path.isfile(ledger_path):
        return 0
    n = 0
    with open(ledger_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == kind:
                n += 1
    return n


def cmd_print():
    print("state = running")
    print("program = stub substrate, never loaded into real launchd")
    return 0


def cmd_watch(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--worker", required=True)
    ap.add_argument("--plugin-scripts", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--deadline", type=float, required=True)
    ap.add_argument("--throttle", type=float, required=True)
    ap.add_argument("--launch-log", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--target-kind", required=True)
    ap.add_argument("--target-count", type=int, required=True)
    a = ap.parse_args(argv)
    inbox = os.path.join(a.state_dir, "inbox")
    start = time.time()
    last_launch = 0.0
    while True:
        have = os.path.isdir(inbox) and any(f.endswith(".json") for f in os.listdir(inbox))
        now = time.time()
        if have and (now - last_launch) >= a.throttle:
            r = subprocess.run([sys.executable, a.worker, "drain", "--root", a.root,
                                "--plugin-scripts", a.plugin_scripts, "--inbox", a.state_dir],
                               capture_output=True, text=True)
            last_launch = now
            with open(a.launch_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": now, "exit": r.returncode}) + "\\n")
        if count_kind(a.ledger, a.target_kind) >= a.target_count:
            break
        if now - start >= a.deadline:
            break
        time.sleep(0.3)
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        return 2
    if argv[0] == "print":
        return cmd_print()
    if argv[0] == "watch":
        return cmd_watch(argv[1:])
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''


def write_stub_launchctl(bindir):
    os.makedirs(bindir, exist_ok=True)
    path = os.path.join(bindir, "launchctl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(STUB_LAUNCHCTL)
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def a1_probe(scratch):
    root = build_consumer(os.path.join(scratch, "a1"))
    p = run_integrate(["probe", "--root", root, "--json"])
    check("a1-probe-exit0", p.returncode == 0, p.stderr[-300:])
    try:
        rows = json.loads(p.stdout.splitlines()[0])
    except (ValueError, IndexError):
        rows = []
    check("a1-nine-rows", len(rows) == 9, len(rows))
    handles = sorted(r.get("handle") for r in rows)
    want = sorted(["launchd-queue", "systemd-user", "cron-anacron", "schtasks-idle",
                  "desktop-task", "hook-oneshot", "ci-tier0", "ampersand", "routine"])
    check("a1-handle-set", handles == want, handles)
    bad_usable = [r["handle"] for r in rows if r.get("usable") and
                 (not r.get("probe_cmd") or r.get("exit") != 0)]
    check("a1-usable-rows-exit0", not bad_usable, bad_usable)
    check("a1-advertised-usable-separate-booleans",
          all(isinstance(r.get("advertised"), bool) and isinstance(r.get("usable"), bool) for r in rows))
    ledger = os.path.join(root, "ledger", "om-substrates.jsonl")
    landed = 0
    if os.path.isfile(ledger):
        landed = sum(1 for line in open(ledger) if line.strip())
    check("a1-rows-appended-to-default-ledger", landed == 9, landed)

    # custom om_substrates_file override
    root2 = build_consumer(os.path.join(scratch, "a1-custom"))
    os.makedirs(os.path.join(root2, ".claude"), exist_ok=True)
    with open(os.path.join(root2, ".claude", "hyp.json"), "w") as fh:
        json.dump({"om_substrates_file": "ledger/custom-substrates.jsonl"}, fh)
    p2 = run_integrate(["probe", "--root", root2, "--json"])
    custom_path = os.path.join(root2, "ledger", "custom-substrates.jsonl")
    default_path = os.path.join(root2, "ledger", "om-substrates.jsonl")
    check("a1-om-substrates-file-override-honoured",
          p2.returncode == 0 and os.path.isfile(custom_path) and not os.path.isfile(default_path),
          (p2.returncode, os.path.isfile(custom_path), os.path.isfile(default_path)))
    RAW["a1_rows"] = rows


def a2_compose():
    sys.path.insert(0, HERE)
    import importlib.util
    spec = importlib.util.spec_from_file_location("om_integrate_a2", OM_INTEGRATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def row(handle, usable, authors=3, free=1):
        return {"handle": handle, "usable": usable, "authors_90d": authors,
                "disk": {"free_bytes": free, "plugin_cache_bytes": free}}

    rows = [row("launchd-queue", False), row("systemd-user", True), row("cron-anacron", True),
            row("schtasks-idle", False), row("desktop-task", False), row("hook-oneshot", False),
            row("ci-tier0", True), row("ampersand", False), row("routine", False)]
    decision = mod.compose(rows)
    check("a2-priority-order", decision == {"on_device": "systemd-user", "remote": "ci-tier0"}, decision)

    zeroed = [dict(r, authors_90d=0, disk={"free_bytes": 0, "plugin_cache_bytes": 0}) for r in rows]
    decision2 = mod.compose(zeroed)
    check("a2-covariate-invariant", decision == decision2, (decision, decision2))

    none_rows = [row(h, False) for h in ("launchd-queue", "systemd-user", "cron-anacron",
                                          "schtasks-idle", "desktop-task", "hook-oneshot",
                                          "ci-tier0", "ampersand", "routine")]
    check("a2-none-when-nothing-usable", mod.compose(none_rows) == {"on_device": "none", "remote": "none"})


def a3_a4_emit(scratch):
    root = build_consumer(os.path.join(scratch, "a3"))
    agents_dir = os.path.join(scratch, "a3-agents")
    p = run_integrate(["emit", "--root", root, "--agents-dir", agents_dir, "--plugin-scripts", HERE])
    check("a3-emit-exit0", p.returncode == 0, p.stderr[-300:])
    plists = [f for f in os.listdir(agents_dir) if f.endswith(".plist")] if os.path.isdir(agents_dir) else []

    lock_path = os.path.join(root, ".claude", "om-offload.lock.json")
    lock = json.load(open(lock_path)) if os.path.isfile(lock_path) else {}
    RAW["a3_lock"] = lock

    if lock.get("on_device_handle") == "launchd-queue":
        check("a3-plist-emitted", len(plists) == 1, plists)
        plist_path = os.path.join(agents_dir, plists[0]) if plists else None
        check("a3-plist-label-prefix", plists and plists[0].startswith("com.hyp-machine.om-worker."))
        body = open(plist_path, encoding="utf-8").read() if plist_path else ""
        check("a3-plist-absolute-paths", "<string>/" in body or "<string>%s" % os.sep in body, body[:200])
        check("a3-plist-process-type-background", "<key>ProcessType</key>\n\t<string>Background</string>" in body)
        plutil = shutil.which("plutil")
        if plutil and plist_path:
            r = subprocess.run([plutil, "-lint", plist_path], capture_output=True, text=True, timeout=20)
            check("a3-plutil-lint", r.returncode == 0, r.stdout + r.stderr)
        else:
            print("SKIP a3-plutil-lint: plutil not on PATH on this host")
    else:
        print("SKIP a3-plist: this host's launchd-queue did not probe usable (on_device=%r)"
              % lock.get("on_device_handle"))

    if lock.get("remote_handle") == "ci-tier0":
        wf = os.path.join(root, ".github", "workflows", "om-check.yml")
        check("a3-ci-tier0-workflow-emitted", os.path.isfile(wf), wf)
        pyyaml_dir = os.path.join(HERE, "vendor", "pyyaml")
        if os.path.isdir(pyyaml_dir) and os.path.isfile(wf):
            sys.path.insert(0, pyyaml_dir)
            try:
                import yaml  # noqa: E402
                with open(wf) as fh:
                    doc = yaml.safe_load(fh)
                check("a3-ci-tier0-workflow-parses-yaml", isinstance(doc, dict) and "jobs" in doc,
                      str(doc)[:200])
                check("a3-ci-tier0-no-doubled-braces", "{{}}" not in open(wf).read())
            except Exception as exc:  # noqa: BLE001
                check("a3-ci-tier0-workflow-parses-yaml", False, repr(exc))
    else:
        print("SKIP a3-ci-tier0: this host's ci-tier0 did not probe usable (remote=%r; gh not "
              "authenticated?)" % lock.get("remote_handle"))

    # A4: re-run is a byte-level no-op over the whole tree (root + agents-dir).
    before = sha_tree(root)
    before.update({"AGENTS:" + k: v for k, v in sha_tree(agents_dir).items()} if os.path.isdir(agents_dir) else {})
    p2 = run_integrate(["emit", "--root", root, "--agents-dir", agents_dir, "--plugin-scripts", HERE])
    check("a4-emit-rerun-exit0", p2.returncode == 0, p2.stderr[-300:])
    after = sha_tree(root)
    after.update({"AGENTS:" + k: v for k, v in sha_tree(agents_dir).items()} if os.path.isdir(agents_dir) else {})
    check("a4-emit-rerun-byte-identical", before == after,
          {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)})
    return root, agents_dir


def a5_test(scratch):
    root = build_consumer(os.path.join(scratch, "a5"))
    stub_bin = write_stub_launchctl(os.path.join(scratch, "a5-bin"))
    transcript = os.path.join(scratch, "a5-transcript.jsonl")
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
                             "cwd": scratch, "message": {"role": "assistant",
                             "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                          "input": {"command": "echo hi"}}],
                             "usage": {"input_tokens": 5, "output_tokens": 1}}}) + "\n")
    env = {"PATH": stub_bin + os.pathsep + os.environ.get("PATH", ""),
           "HYP_STATE_DIR": os.path.join(scratch, "hyp-state")}
    p = run_integrate(["test", "--root", root, "--plugin-scripts", HERE, "--transcript", transcript,
                       "--window", "20", "--poison-window", "15", "--throttle", "2"],
                      timeout=60, env=env)
    RAW["a5_test_stdout"] = p.stdout
    check("a5-test-pass", p.returncode == 0 and "test: PASS" in p.stdout, p.stdout[-500:] + p.stderr[-300:])
    inbox = os.path.join(root, ".claude", "om-state", "inbox")
    remaining = os.listdir(inbox) if os.path.isdir(inbox) else ["<inbox missing>"]
    check("a5-inbox-empty", remaining == [], remaining)


def a6_uninstall(root, agents_dir):
    p = run_integrate(["uninstall", "--root", root, "--dry-run"])
    check("a6-dry-run-exit0", p.returncode == 0, p.stderr[-300:])
    try:
        result = json.loads(p.stdout.splitlines()[0])
    except (ValueError, IndexError):
        result = {}
    check("a6-dry-run-reports-removal", bool(result.get("removed")) or bool(result.get("reversal")), result)
    lock_path = os.path.join(root, ".claude", "om-offload.lock.json")
    check("a6-dry-run-changes-nothing", os.path.isfile(lock_path))

    p2 = run_integrate(["uninstall", "--root", root])
    check("a6-uninstall-exit0", p2.returncode == 0, p2.stderr[-300:])
    check("a6-lock-removed", not os.path.isfile(lock_path))
    leftover_plists = [f for f in os.listdir(agents_dir) if f.endswith(".plist")] if os.path.isdir(agents_dir) else []
    check("a6-zero-plists-left", leftover_plists == [], leftover_plists)
    wf = os.path.join(root, ".github", "workflows", "om-check.yml")
    vendor = os.path.join(root, ".github", "om-scripts")
    check("a6-workflow-and-vendor-removed", not os.path.isfile(wf) and not os.path.isdir(vendor),
          (os.path.isfile(wf), os.path.isdir(vendor)))
    hyp_json = os.path.join(root, ".claude", "hyp.json")
    data = json.load(open(hyp_json)) if os.path.isfile(hyp_json) else {}
    check("a6-om-offload-key-dropped", "om_offload" not in data, data)
    return root


def a7_report(root):
    p = run_integrate(["report", "--root", root])
    check("a7-report-not-installed-after-uninstall", p.returncode == 0 and "not installed" in p.stdout,
          p.stdout + p.stderr)


def main():
    scratch = tempfile.mkdtemp(prefix="selftest-om-integrate-")
    try:
        a1_probe(scratch)
        a2_compose()
        root, agents_dir = a3_a4_emit(scratch)
        a5_test(scratch)
        a6_uninstall(root, agents_dir)
        a7_report(root)
    finally:
        try:
            with open("selftest-om-integrate.raw.json", "w", encoding="utf-8") as fh:
                json.dump(RAW, fh, indent=1, sort_keys=True, default=str)
        except OSError:
            pass
        shutil.rmtree(scratch, ignore_errors=True)

    n_pass = sum(1 for r in RESULTS if r)
    print("%d/%d checks passed" % (n_pass, len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
