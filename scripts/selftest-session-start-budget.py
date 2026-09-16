#!/usr/bin/env python3
"""selftest-session-start-budget.py -- regression test for hooks/scripts/session-start-budget.py,
in particular the `--also <name> <command>` startup wake it gained porting lab
H-DRAFT-10383178-om-startup-wake (kept 2026-09-15, five counted looks, A1-A5 5/5; VERDICT.json,
VERIFY.md, journal fragment 0547-10383178-verdict.md).

Runs the INSTALLED wrapper (the file beside this one at ../hooks/scripts/session-start-budget.py)
and the installed scripts/om-worker.py as real subprocesses over throwaway git repositories under
a temp dir -- no live `claude` child anywhere (payload replays plus a `python3` PATH shim, exactly
the fixture's own approach):

  lock-replay-clean               no lock directory: never reclaimed-as-stale, treated as free
  lock-replay-held-fresh          a lock with this process's own live pid and current `ps -o
                                 lstart=`, held moments ago: NOT stale (ordinary in-flight case)
  lock-replay-live-matching-lstart-age-1800-not-reclaimed  the H-309 amendment under test: a lock
                                 whose recorded pid is alive AND whose `ps -o lstart=` still
                                 matches the one captured at lock creation is never reclaimed by
                                 age alone, even at exactly LOCK_MAX_S (1800s)
  lock-replay-dead-pid-reclaimed  a lock naming a pid that has since exited (reaped, confirmed
                                 dead via os.kill) reclaims regardless of age
  lock-replay-changed-lstart-reclaimed  a lock naming a live pid whose CURRENT `ps -o lstart=` no
                                 longer matches the recorded one (the pid was reused by a
                                 different process) reclaims even though the pid is alive

  two-worktrees-distinct-wrapper-locks     the wrapper's own per-name lock lives under
                                 `state_dir(root)`, keyed by the literal (worktree) root path --
                                 a main checkout and its linked worktree never share that
                                 directory, so their `om-worker.lock` paths differ and neither
                                 startup ever waits on or steals the other's lock
  two-worktrees-share-om-inbox-drift-fix   the wake's pointer lands under the SAME shared,
                                 common_dir-keyed inbox the installed om-worker.py's own default
                                 (`--inbox`-less) drain reads for BOTH the main checkout and the
                                 worktree -- the ported drift fix (om_inbox_root), proven against
                                 the installed worker's own resolve_common_dir/state_root_for_repo,
                                 not a re-implementation assumed to agree
  two-worktrees-each-lands-own-previous-session-row  startup 1 in each of two worktrees primes a
                                 pending pointer and spawns exactly one side-runner that lands
                                 nothing (nothing was pending yet); startup 2 in each delivers
                                 startup 1's OWN pointer and it drains into THAT worktree's own
                                 ledger (ledger/om-feedback.jsonl), never the other's, even though
                                 both pointers passed through one shared inbox
  no-added-foreground-interpreter-start    the primary row's own `.fg` record is written and its
                                 `mode` reaches `finished` on both startups (the wake never blocks
                                 or replaces the primary's own foreground path); the side-runner's
                                 own `.fg` record shows `mode: spawned` with `foreground_s` ~0
                                 (never waited on)

  ported-file-stdlib-only         the installed wrapper imports only os, sys, time, zlib, hashlib
  installed-copy-executable       the file this test exercises is the one hooks.json actually runs

  reading-absent-no-feedback-lines         no om-worker.out at all: no OM-FEEDBACK: line printed
  reading-empty-no-feedback-lines          an empty om-worker.out: no OM-FEEDBACK: line printed
  reading-3-lines-prints-3 / reading-8-lines-prints-8   a reading of 3 (or 8) lines prints
                                 exactly that many OM-FEEDBACK: lines, the first carrying
                                 age=<seconds>, no more-line
  reading-12-lines-prints-8-plus-more      a 12-line reading prints 8 OM-FEEDBACK: lines plus one
                                 OM-FEEDBACK: ... 4 more line (the cap is startup-only)
  reading-canary-forbidden-key-held        a line containing a forbidden JSON-shaped key prints
                                 as <held: 1 line>; every other line prints unchanged
  reading-canary-marker-held               a line containing /Users/ or $HOME prints as
                                 <held: 1 line>
  wrong-key-not-shown                      a reading planted under the WORKER's own sha256/
                                 common_dir inbox key never shows: only this wrapper's own
                                 state_dir(root) key is read
  reading-surface-no-added-spawn           a planted reading (any size) never adds a fork: the
                                 primary command's own .fg row is always exactly one row
  cached-route-holds-content               the resume|clear|compact cached row applies the SAME
                                 hold to a planted canary reading under the om-worker name
  cached-route-off-shaped-no-om-worker      a cached call that never names om-worker (the
                                 pre-upgrade row shape) never mentions it
  reading-surface-static-no-spawn-calls    print_om_feedback/_feedback_forbidden/_held_bytes
                                 contain no call to any process-spawning name (os.fork,
                                 subprocess, posix, _posixsubprocess, asyncio, concurrent.futures,
                                 multiprocessing) -- a static census the source-token scan cannot
                                 evade (REFUTE-FIXTURE-5 advisory 1)
  reading-surface-forbidden-keys-mirror-worker-canary / -markers-mirror-worker-canary
                                 the wrapper's mirrored FEEDBACK_FORBIDDEN_KEYS/MARKERS constants
                                 are asserted equal to scripts/om-worker.py's own
                                 CANARY_KEYS_FORBIDDEN/CANARY_MARKERS, parsed via ast (neither
                                 module executed to compare) -- proves the two never drift apart

Usage: python3 scripts/selftest-session-start-budget.py     exit 0 = all PASS, 1 = any FAIL
Standard library only, Python 3.9.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
WRAPPER = os.path.join(PLUGIN, "hooks", "scripts", "session-start-budget.py")
WORKER = os.path.join(PLUGIN, "scripts", "om-worker.py")
PY = sys.executable

RESULTS = []
TMP = None


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((": " + str(detail)[:300]) if (detail and not cond) else ""))


def sh(cmd, cwd=None, env=None, timeout=30):
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def sh_stdin(cmd, cwd, env, payload, timeout=30):
    p = subprocess.run(cmd, cwd=cwd, env=env, input=payload.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def git_repo(path):
    os.makedirs(path, exist_ok=True)
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                "HOME": TMP, "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid"})
    sh(["git", "init", "-q", path], env=env)
    sh(["git", "-C", path, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "init"], env=env)
    return env


def import_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_ps_fields(fields, pid):
    """`ps -o <fields>= -p pid`, stripped, or None."""
    try:
        out = subprocess.run(["ps", "-o", fields + "=", "-p", str(pid)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        txt = out.stdout.decode("utf-8", "replace").strip()
        return txt or None
    except Exception:
        return None


def dead_pid():
    """A pid guaranteed dead: fork, let the child exit immediately, wait on it, return its pid."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


def make_lock(sd, name, pid, lstart, age_s):
    lock = os.path.join(sd, name + ".lock")
    os.mkdir(lock)
    with open(os.path.join(lock, "pid"), "w") as f:
        f.write("%s\n%s\n%s\n" % (pid, int(time.time()), lstart or ""))
    old = time.time() - age_s
    os.utime(lock, (old, old))
    return lock


def test_lock_replay(ssb):
    sd = os.path.join(TMP, "lock-replay")
    os.makedirs(sd, exist_ok=True)

    # 1. clean: no lock at all.
    clean_lock = os.path.join(sd, "clean.lock")
    check("lock-replay-clean", ssb.reclaim_if_stale(clean_lock) is True and not os.path.isdir(clean_lock))

    # 2. held, fresh: this process's own pid and its OWN current lstart, age 1s.
    self_lstart = load_ps_fields("lstart", os.getpid())
    held = make_lock(sd, "held", os.getpid(), self_lstart, 1)
    check("lock-replay-held-fresh", ssb.reclaim_if_stale(held) is False and os.path.isdir(held))
    shutil.rmtree(held, ignore_errors=True)

    # 3. H-309 amendment: live pid, matching lstart, age exactly LOCK_MAX_S (1800s) -- NOT reclaimed.
    old_lock = make_lock(sd, "old-live", os.getpid(), self_lstart, ssb.LOCK_MAX_S)
    check("lock-replay-live-matching-lstart-age-1800-not-reclaimed",
          ssb.reclaim_if_stale(old_lock) is False and os.path.isdir(old_lock),
          (ssb.lock_holder(old_lock),))
    shutil.rmtree(old_lock, ignore_errors=True)

    # 4. dead pid -> reclaimed regardless of age.
    dpid = dead_pid()
    dead_lock = make_lock(sd, "dead", dpid, "Mon Jan  1 00:00:00 2020", 5)
    check("lock-replay-dead-pid-reclaimed",
          ssb.reclaim_if_stale(dead_lock) is True and not os.path.isdir(dead_lock))

    # 5. live pid, but recorded lstart no longer matches the CURRENT one (pid reused): reclaimed.
    changed_lock = make_lock(sd, "changed", os.getpid(), "Mon Jan  1 00:00:00 2020", 5)
    check("lock-replay-changed-lstart-reclaimed",
          ssb.reclaim_if_stale(changed_lock) is True and not os.path.isdir(changed_lock),
          (self_lstart, ssb.lock_holder(changed_lock)))


def state_dir_for(root, state_dir):
    """The wrapper's own crc32+adler32 key for `root`, replicated via zlib directly."""
    import zlib
    b = os.path.realpath(root).encode("utf-8", "replace")
    key = "%08x%08x" % (zlib.crc32(b) & 0xFFFFFFFF, zlib.adler32(b) & 0xFFFFFFFF)
    return os.path.join(state_dir, "session-start", key)


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except OSError:
        return []


def startup(root, state_dir, session_id, transcript_path, home):
    payload = json.dumps({"session_id": session_id, "transcript_path": transcript_path,
                          "cwd": root, "source": "startup"})
    also_cmd = ('%s "%s" drain --boundary startup --plugin-scripts "%s"'
               % (PY, WORKER, os.path.join(PLUGIN, "scripts")))
    env = dict(os.environ)
    env["HYP_STATE_DIR"] = state_dir
    env["HOME"] = home
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return sh_stdin([PY, "-S", "-E", WRAPPER, "run", "resolver", "true", "--also", "om-worker", also_cmd],
                    root, env, payload)


def test_two_worktrees(ssb, om):
    base = os.path.join(TMP, "tw")
    main = os.path.join(base, "main")
    home = os.path.join(base, "home")
    state = os.path.join(base, "state")
    os.makedirs(home, exist_ok=True)
    os.makedirs(state, exist_ok=True)
    git_repo(main)
    wt = os.path.join(base, "wt")
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "HOME": home})
    rc, out, err = sh(["git", "-C", main, "worktree", "add", "-q", wt, "-b", "wtbranch"], env=env)
    check("two-worktree-fixture-setup", rc == 0 and os.path.isdir(wt), (rc, out, err))

    main_common = ssb.git_common_dir(main)
    wt_common = ssb.git_common_dir(wt)
    check("two-worktrees-share-om-inbox-drift-fix",
          bool(main_common) and main_common == wt_common
          and ssb.om_inbox_root(main, main_common) == ssb.om_inbox_root(wt, wt_common)
          and ssb.om_inbox_root(main, main_common) == om.state_root_for_repo(om.resolve_common_dir(main))
          and ssb.om_inbox_root(wt, wt_common) == om.state_root_for_repo(om.resolve_common_dir(wt)),
          (main_common, wt_common))

    main_sd = state_dir_for(main, state)
    wt_sd = state_dir_for(wt, state)
    check("two-worktrees-distinct-wrapper-locks", main_sd != wt_sd, (main_sd, wt_sd))

    # startup 1 in each worktree: primes pending, nothing to land yet.
    t_main1 = os.path.join(base, "t-main1.jsonl")
    with open(t_main1, "w") as f:
        f.write('{"turn":1}\n')
    t_wt1 = os.path.join(base, "t-wt1.jsonl")
    with open(t_wt1, "w") as f:
        f.write('{"turn":1}\n')
    rc1, out1, err1 = startup(main, state, "sess-main-1", t_main1, home)
    rc2, out2, err2 = startup(wt, state, "sess-wt-1", t_wt1, home)
    time.sleep(2.5)
    fg_main1 = read_json(os.path.join(main_sd, "om-worker.fg"))
    fg_wt1 = read_json(os.path.join(wt_sd, "om-worker.fg"))
    check("no-added-foreground-interpreter-start",
          rc1 == 0 and rc2 == 0 and out1.strip() == "" and out2.strip() == ""
          and len(fg_main1) == 1 and fg_main1[0].get("mode") == "spawned"
          and abs(fg_main1[0].get("foreground_s", 1)) < 0.2
          and len(fg_wt1) == 1 and fg_wt1[0].get("mode") == "spawned",
          (rc1, out1, err1[:200], fg_main1, fg_wt1))

    # startup 2 in each worktree: each delivers its OWN previous session's pointer.
    t_main2 = os.path.join(base, "t-main2.jsonl")
    with open(t_main2, "w") as f:
        f.write('{"turn":1}\n')
    t_wt2 = os.path.join(base, "t-wt2.jsonl")
    with open(t_wt2, "w") as f:
        f.write('{"turn":1}\n')
    rc3, out3, err3 = startup(main, state, "sess-main-2", t_main2, home)
    time.sleep(2.5)
    rc4, out4, err4 = startup(wt, state, "sess-wt-2", t_wt2, home)
    time.sleep(2.5)

    main_ledger = os.path.join(main, "ledger", "om-feedback.jsonl")
    wt_ledger = os.path.join(wt, "ledger", "om-feedback.jsonl")
    main_rows = read_json(main_ledger)
    wt_rows = read_json(wt_ledger)
    main_sessions = sorted(r.get("session") for r in main_rows if r.get("kind") == "session-observed")
    wt_sessions = sorted(r.get("session") for r in wt_rows if r.get("kind") == "session-observed")
    check("two-worktrees-each-lands-own-previous-session-row",
          rc3 == 0 and rc4 == 0 and main_sessions == ["sess-main-1"] and wt_sessions == ["sess-wt-1"],
          (main_sessions, wt_sessions, err3[:200], err4[:200]))


def _plant_reading(sd, name, lines):
    path = os.path.join(sd, name + ".out")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return path


def feedback_lines(out_text):
    return [l for l in out_text.splitlines() if l.startswith("OM-FEEDBACK: ")]


def run_reading(root, state, home, om_cmd="true", hold_om_lock=True):
    """Runs the wrapper's `run resolver true --also om-worker <om_cmd>` once. When
    `hold_om_lock` (default), pre-holds the om-worker spawn lock so `do_also_wake` never forks a
    real side-runner and never touches a planted `.out` file out from under the read this test
    is checking -- the only real spawn in every call below is the primary `true` command."""
    sd = state_dir_for(root, state)
    os.makedirs(sd, exist_ok=True)
    lock = None
    if hold_om_lock:
        self_lstart = load_ps_fields("lstart", os.getpid())
        lock = make_lock(sd, "om-worker", os.getpid(), self_lstart, 1)
    try:
        payload = json.dumps({"session_id": "s1", "transcript_path": os.path.join(state, "t.jsonl"),
                              "cwd": root, "source": "startup"})
        env = dict(os.environ)
        env["HYP_STATE_DIR"] = state
        env["HOME"] = home
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        rc, out_text, err = sh_stdin([PY, "-S", "-E", WRAPPER, "run", "resolver", "true",
                                      "--also", "om-worker", om_cmd], root, env, payload)
        return sd, rc, out_text, err
    finally:
        if lock:
            shutil.rmtree(lock, ignore_errors=True)


def test_reading_surface_startup(ssb):
    base = os.path.join(TMP, "rs")
    root = os.path.join(base, "root")
    home = os.path.join(base, "home")
    state = os.path.join(base, "state")
    os.makedirs(home, exist_ok=True)
    os.makedirs(state, exist_ok=True)
    git_repo(root)

    sd, rc, out_text, err = run_reading(root, state, home)
    check("reading-absent-no-feedback-lines", rc == 0 and not feedback_lines(out_text), (rc, out_text, err[:200]))

    _plant_reading(sd, "om-worker", [])
    sd, rc, out_text, err = run_reading(root, state, home)
    check("reading-empty-no-feedback-lines", rc == 0 and not feedback_lines(out_text), (rc, out_text))

    _plant_reading(sd, "om-worker", ["l1", "l2", "l3"])
    sd, rc, out_text, err = run_reading(root, state, home)
    fb = feedback_lines(out_text)
    check("reading-3-lines-prints-3",
          rc == 0 and len(fb) == 3 and fb[0].startswith("OM-FEEDBACK: age=") and fb[0].endswith("l1")
          and fb[1] == "OM-FEEDBACK: l2" and fb[2] == "OM-FEEDBACK: l3", (fb,))

    _plant_reading(sd, "om-worker", ["r%d" % i for i in range(8)])
    sd, rc, out_text, err = run_reading(root, state, home)
    fb = feedback_lines(out_text)
    check("reading-8-lines-prints-8", rc == 0 and len(fb) == 8 and not any("more" in l for l in fb), (fb,))

    _plant_reading(sd, "om-worker", ["r%d" % i for i in range(12)])
    sd, rc, out_text, err = run_reading(root, state, home)
    fb = feedback_lines(out_text)
    check("reading-12-lines-prints-8-plus-more",
          rc == 0 and len(fb) == 9 and fb[-1] == "OM-FEEDBACK: ... 4 more", (fb,))

    _plant_reading(sd, "om-worker", ['line with "prompt" key', "clean-line"])
    sd, rc, out_text, err = run_reading(root, state, home)
    fb = feedback_lines(out_text)
    check("reading-canary-forbidden-key-held",
          rc == 0 and len(fb) == 2 and fb[0].endswith("<held: 1 line>") and fb[1] == "OM-FEEDBACK: clean-line",
          (fb,))

    _plant_reading(sd, "om-worker", ["path /Users/example/secret", "found $HOME/x", "clean-line-2"])
    sd, rc, out_text, err = run_reading(root, state, home)
    fb = feedback_lines(out_text)
    check("reading-canary-marker-held",
          rc == 0 and len(fb) == 3 and fb[0].endswith("<held: 1 line>")
          and fb[1] == "OM-FEEDBACK: <held: 1 line>" and fb[2] == "OM-FEEDBACK: clean-line-2",
          (fb,))

    decoy_root = ssb.om_state_root(root)
    os.makedirs(decoy_root, exist_ok=True)
    with open(os.path.join(decoy_root, "om-worker.out"), "w", encoding="utf-8") as f:
        f.write("decoy-should-never-print\n")
    own = os.path.join(sd, "om-worker.out")
    if os.path.exists(own):
        os.remove(own)
    sd, rc, out_text, err = run_reading(root, state, home)
    check("wrong-key-not-shown",
          rc == 0 and "decoy-should-never-print" not in out_text and not feedback_lines(out_text), (out_text,))

    _plant_reading(sd, "om-worker", ["r%d" % i for i in range(12)])
    sd, rc, out_text, err = run_reading(root, state, home)
    fg = read_json(os.path.join(sd, "resolver.fg"))
    check("reading-surface-no-added-spawn", rc == 0 and len(fg) == 1 and fg[0].get("mode") == "finished", (fg,))


def test_cached_route_holds():
    base = os.path.join(TMP, "rs-cached")
    root = os.path.join(base, "root")
    home = os.path.join(base, "home")
    state = os.path.join(base, "state")
    os.makedirs(home, exist_ok=True)
    os.makedirs(state, exist_ok=True)
    git_repo(root)
    sd = state_dir_for(root, state)
    os.makedirs(sd, exist_ok=True)
    _plant_reading(sd, "om-worker", ['line with "prompt" key', "clean-line"])

    payload = json.dumps({"cwd": root, "source": "resume"})
    env = dict(os.environ)
    env["HYP_STATE_DIR"] = state
    env["HOME"] = home
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    rc, out_text, err = sh_stdin([PY, "-S", "-E", WRAPPER, "cached", "harden-check", "recovery-warning",
                                  "dashboard-check", "resolver", "dashboard-refresh", "om-worker"],
                                 root, env, payload)
    check("cached-route-holds-content",
          rc == 0 and '"prompt"' not in out_text and "<held: 1 line>" in out_text
          and "clean-line" in out_text and "om-worker" in out_text, (out_text,))

    rc, out_text, err = sh_stdin([PY, "-S", "-E", WRAPPER, "cached", "harden-check", "recovery-warning",
                                  "dashboard-check", "resolver", "dashboard-refresh"],
                                 root, env, payload)
    check("cached-route-off-shaped-no-om-worker", rc == 0 and "om-worker" not in out_text, (out_text,))


BANNED_SPAWN_NAMES = {
    "fork", "forkpty", "posix_spawn", "posix_spawnp", "system", "popen", "spawnv", "spawnve",
    "spawnl", "spawnle", "execv", "execve", "execvp", "execvpe", "startfile",
}
BANNED_SPAWN_MODULES = {"subprocess", "posix", "_posixsubprocess", "asyncio", "concurrent",
                        "multiprocessing", "threading"}


def _call_name_chain(node):
    parts = []
    f = node.func
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return list(reversed(parts))


def test_reading_surface_static_census():
    src = open(WRAPPER, "r", encoding="utf-8").read()
    tree = ast.parse(src)
    targets = {"print_om_feedback", "_feedback_forbidden", "_held_bytes"}
    found = set()
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found.add(node.name)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    chain = _call_name_chain(sub)
                    if any(part in BANNED_SPAWN_MODULES or part in BANNED_SPAWN_NAMES for part in chain):
                        offenders.append((node.name, chain))
    check("reading-surface-static-no-spawn-calls",
          found == targets and not offenders, (sorted(found), offenders))


def module_level_tuple_constant(path, name):
    """The literal tuple a module-level `<name> = (...)` assignment holds, parsed with `ast`
    (never imported, so this selftest never executes either module to compare them). None if
    the file has no such top-level assignment. Same approach
    `scripts/selftest-commit-backstop.py` uses for its own mirrored constant."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != name:
            continue
        return ast.literal_eval(node.value)
    return None


def test_reading_surface_mirrors_worker_canary():
    wrapper_keys = module_level_tuple_constant(WRAPPER, "FEEDBACK_FORBIDDEN_KEYS")
    wrapper_markers = module_level_tuple_constant(WRAPPER, "FEEDBACK_FORBIDDEN_MARKERS")
    worker_keys = module_level_tuple_constant(WORKER, "CANARY_KEYS_FORBIDDEN")
    worker_markers = module_level_tuple_constant(WORKER, "CANARY_MARKERS")
    check("reading-surface-forbidden-keys-mirror-worker-canary",
          wrapper_keys is not None and wrapper_keys == worker_keys,
          "wrapper=%r worker=%r" % (wrapper_keys, worker_keys))
    check("reading-surface-forbidden-markers-mirror-worker-canary",
          wrapper_markers is not None and wrapper_markers == worker_markers,
          "wrapper=%r worker=%r" % (wrapper_markers, worker_markers))


def test_ported_file():
    src = open(WRAPPER, "r", encoding="utf-8").read()
    tree = ast.parse(src)
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("ported-file-stdlib-only", imported <= {"os", "sys", "time", "zlib", "hashlib"}, sorted(imported))
    check("installed-copy-executable", os.access(WRAPPER, os.X_OK))


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="selftest-session-start-budget-")
    try:
        ssb = import_module("ssb_under_test", WRAPPER)
        om = import_module("om_under_test", WORKER)
        test_lock_replay(ssb)
        test_two_worktrees(ssb, om)
        test_reading_surface_startup(ssb)
        test_cached_route_holds()
        test_reading_surface_static_census()
        test_reading_surface_mirrors_worker_canary()
        test_ported_file()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    ok = all(RESULTS)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(RESULTS), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
