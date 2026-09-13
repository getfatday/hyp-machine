#!/usr/bin/env python3
"""selftest-worktree-root.py -- regression test for worktree-aware hook root resolution.

Builds its own throwaway consumer repository under a temp dir (no dependence on the
host, the caller's cwd, or any lab path), then checks the INSTALLED plugin (the tree
this file lives in):

  resolve_root  dynamic worktree  -> the worktree toplevel (<main>/.claude/worktrees/<name>)
  resolve_root  external worktree -> the worktree toplevel (git worktree add elsewhere)
  resolve_root  main checkout     -> CLAUDE_PROJECT_DIR (subdirectory cwd included)
  resolve_root  foreign repo cwd  -> CLAUDE_PROJECT_DIR (never another repository)
  resolve_root  non-git cwd       -> CLAUDE_PROJECT_DIR
  resolve_root  symlink into a worktree's interior -> that worktree's toplevel
  resolve_root  CLAUDE_PROJECT_DIR is a worktree, cwd is main -> main (same repo, both ways)
  resolve_root  FIFO planted as a commondir -> CLAUDE_PROJECT_DIR, without hanging
  resolve_root  no env            -> payload cwd (legacy order intact)
  stop-dispatch dynamic worktree  -> exit 2, re-presents the spec open only on the branch
  stop-dispatch main checkout     -> exit 0 (nothing open there)
  write-once-guard worktree raw   -> deny (the guard now grades the worktree)

Hook writes land in the session worktree (lab H-DRAFT-b9e771b2-hook-writes-worktree; the
probe-pinned contract of a session that entered a worktree after launch: CLAUDE_PROJECT_DIR =
main, hook process cwd = worktree, payload cwd = worktree):

  resolve_root  no payload, process cwd in the worktree -> the worktree (the process-cwd leg)
  resolve_root  no payload, process cwd foreign / non-git -> CLAUDE_PROJECT_DIR
  resolve_root  payload cwd = worktree, process cwd = main -> the worktree (payload first)
  compile-dashboard --quiet --hook  -> DASHBOARD.md + decisions.html in the worktree, --check --hook fresh there
  compile-dashboard <root>          -> the positional root wins (a human's CLI call)
  license-join-hook.py              -> corpus dir, artifacts symlink (-> worktree), fires log in the worktree
  harden-check.sh / leak-status.sh --print-root -> the process cwd's worktree; HYP_ROOT wins when set
  session-start-budget.py           -> hands HYP_ROOT = the worktree to the wrapped command; state keyed by it
  commit-backstop.py / session_resolver.py without argv -> resolve from the payload, no crash
  the MAIN checkout's tree           -> byte-identical before and after every write above
  plain checkout (no worktree)       -> the ON writers add exactly the released plugin's path set
                                        (DASHBOARD.md, decisions.html, .claude/license-join-corpus{,/artifacts},
                                        .claude/license-join-fires.log)

Usage: python3 scripts/selftest-worktree-root.py        exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-DRAFT-e90628b6 worktree-aware-hook-root (2026-09-03);
extended by H-DRAFT-b9e771b2-hook-writes-worktree (2026-09-13).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "scripts"))
import hyp_config  # noqa: E402

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


SPEC = "# %s: selftest spec\n\n## Status\n%s\n\n## Hypothesis\nx\n\n## Method\nx\n\n## Binary assertions\n1. x\n\n## Verdict rule\nx\n\n## Runs\n"


def mk_consumer(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    write(path, ".claude/hyp.json", json.dumps({"profile": "experiments", "context": "selftest"}))
    write(path, "hypotheses/H-001-landed.md", SPEC % ("H-001-landed", "kept"))
    write(path, "hypotheses/TEMPLATE.md", "# H-NNN-slug\n")
    write(path, "experiments/runs/.keep", "")
    write(path, "experiments/journal-fragments/.keep", "")
    write(path, "research/raw/.keep", "")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")


def add_open_spec(tree):
    write(tree, "hypotheses/H-002-open-on-branch.md", SPEC % ("H-002-open-on-branch", "draft"))
    write(tree, "research/raw/2026-09-03-seed.md", "raw\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "open spec on branch")


def tree(root, exclude=(".git", ".claude/worktrees")):
    """{relpath: descriptor} for every entry under root (files by sha256, dirs, symlinks by target)."""
    out = {}
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in list(dirnames):
            rel = (rel_dir + "/" + d) if rel_dir else d
            if any(rel == e or rel.startswith(e + "/") for e in exclude):
                continue
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                out[rel] = "link:" + os.readlink(full)
            else:
                out[rel] = "dir"
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            rel = (rel_dir + "/" + f) if rel_dir else f
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                out[rel] = "link:" + os.readlink(full)
            else:
                with open(full, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def hook_env(project_dir, state_dir, extra=None):
    env = dict(GIT_ENV)
    env.update({"CLAUDE_PLUGIN_ROOT": PLUGIN, "CLAUDE_PROJECT_DIR": project_dir,
                "HYP_STATE_DIR": state_dir, "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra or {})
    return env


def run_cmd(argv, cwd, payload, env, timeout=120):
    return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=timeout)


def ssb_key(checkout):
    b = os.path.realpath(checkout).encode("utf-8", "replace")
    return "%08x%08x" % (zlib.crc32(b) & 0xFFFFFFFF, zlib.adler32(b) & 0xFFFFFFFF)


RELEASED_PLAIN_PATH_SET = {"DASHBOARD.md", "decisions.html", ".claude/license-join-corpus",
                           ".claude/license-join-corpus/artifacts", ".claude/license-join-fires.log"}
RULE_WRITE = {"content": "# rules\n\n- always run the tests before a commit\n"}


def run_hook(rel, payload, env_root):
    env = dict(GIT_ENV)
    env["CLAUDE_PLUGIN_ROOT"] = PLUGIN
    # The simulated session is a dispatch PARTICIPANT: this suite tests root
    # resolution of the dispatch, not the participation gate (see
    # scripts/selftest-participant-dispatch.py for that side).
    env["HYP_DISPATCH"] = "1"
    if env_root:
        env["CLAUDE_PROJECT_DIR"] = env_root
    p = subprocess.run([sys.executable, os.path.join(PLUGIN, rel)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, cwd=env_root or payload["cwd"], timeout=120)
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-selftest-")
    results = []

    def check(name, cond, detail):
        results.append(cond)
        print(("PASS " if cond else "FAIL ") + name + ": " + detail)

    try:
        main_repo = os.path.join(tmp, "main")
        mk_consumer(main_repo)
        dyn = os.path.join(main_repo, ".claude", "worktrees", "wt-dyn")
        git(main_repo, "worktree", "add", "-q", dyn, "-b", "worktree-wt-dyn")
        add_open_spec(dyn)
        ext = os.path.join(tmp, "wt-ext")
        git(main_repo, "worktree", "add", "-q", ext, "-b", "worktree-wt-ext")
        add_open_spec(ext)
        foreign = os.path.join(tmp, "foreign")
        mk_consumer(foreign)
        add_open_spec(foreign)
        nongit = os.path.join(tmp, "plain-dir")
        os.makedirs(nongit)

        os.environ["CLAUDE_PROJECT_DIR"] = main_repo
        rp = os.path.realpath
        r = hyp_config.resolve_root({"cwd": os.path.join(dyn, "hypotheses")})
        check("resolve-dynamic-worktree", rp(r) == rp(dyn), r)
        r = hyp_config.resolve_root({"cwd": ext})
        check("resolve-external-worktree", rp(r) == rp(ext), r)
        r = hyp_config.resolve_root({"cwd": os.path.join(main_repo, "hypotheses")})
        check("resolve-main-subdir", rp(r) == rp(main_repo), r)
        r = hyp_config.resolve_root({"cwd": foreign})
        check("resolve-foreign-repo-falls-back", rp(r) == rp(main_repo), r)
        r = hyp_config.resolve_root({"cwd": nongit})
        check("resolve-nongit-falls-back", rp(r) == rp(main_repo), r)
        lnk = os.path.join(tmp, "lnk-into-worktree")
        os.symlink(os.path.join(dyn, "hypotheses"), lnk)
        r = hyp_config.resolve_root({"cwd": lnk})
        check("resolve-symlink-into-worktree-interior", rp(r) == rp(dyn), r)
        os.environ["CLAUDE_PROJECT_DIR"] = dyn
        r = hyp_config.resolve_root({"cwd": main_repo})
        check("resolve-project-dir-is-worktree-cwd-main", rp(r) == rp(main_repo), r)
        os.environ["CLAUDE_PROJECT_DIR"] = main_repo
        if hasattr(os, "mkfifo"):
            fifo_gitdir = os.path.join(main_repo, ".git", "worktrees", "fifo")
            os.makedirs(fifo_gitdir)
            os.mkfifo(os.path.join(fifo_gitdir, "commondir"))
            fifo_wt = os.path.join(tmp, "fifo-wt")
            os.makedirs(fifo_wt)
            write(fifo_wt, ".git", "gitdir: %s\n" % fifo_gitdir)
            code = ("import sys; sys.path.insert(0, %r); import hyp_config; "
                    "print(hyp_config.resolve_root({'cwd': %r}))"
                    % (os.path.join(PLUGIN, "hooks", "scripts"), fifo_wt))
            try:
                p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                   env=dict(GIT_ENV, CLAUDE_PROJECT_DIR=main_repo), timeout=5)
                out = p.stdout.strip()
                check("resolve-fifo-commondir-no-hang", rp(out) == rp(main_repo), out or p.stderr.strip()[-120:])
            except subprocess.TimeoutExpired:
                check("resolve-fifo-commondir-no-hang", False, "hung > 5 s on a FIFO commondir")
        else:
            print("SKIP resolve-fifo-commondir-no-hang: os.mkfifo unavailable on this platform")
        del os.environ["CLAUDE_PROJECT_DIR"]
        r = hyp_config.resolve_root({"cwd": dyn})
        check("resolve-no-env-legacy-cwd", rp(r) == rp(dyn), r)

        p = run_hook("hooks/scripts/stop-dispatch.py",
                     {"session_id": "selftest-dyn", "cwd": dyn, "hook_event_name": "Stop",
                      "stop_hook_active": False}, main_repo)
        check("stop-dispatch-blocks-in-worktree", p.returncode == 2 and "H-002" in p.stderr,
              "rc=%d stderr=%s" % (p.returncode, p.stderr.strip()[:120]))
        check("stop-dispatch-state-in-worktree",
              os.path.isfile(os.path.join(dyn, ".claude", "stop-driver", "hook-log.jsonl"))
              and not os.path.exists(os.path.join(main_repo, ".claude", "stop-driver")),
              "runtime state follows the resolved root")
        p = run_hook("hooks/scripts/stop-dispatch.py",
                     {"session_id": "selftest-main", "cwd": main_repo, "hook_event_name": "Stop",
                      "stop_hook_active": False}, main_repo)
        check("stop-dispatch-allows-on-main", p.returncode == 0, "rc=%d" % p.returncode)
        p = run_hook("hooks/scripts/write-once-guard.py",
                     {"session_id": "selftest-guard", "cwd": dyn, "hook_event_name": "PreToolUse",
                      "tool_name": "Edit",
                      "tool_input": {"file_path": os.path.join(dyn, "research", "raw", "2026-09-03-seed.md"),
                                     "old_string": "raw", "new_string": "edited"}}, main_repo)
        check("write-once-guard-denies-worktree-raw", '"deny"' in p.stdout,
              (p.stdout.strip() or "(no output)")[:120])

        # ---- hook writes land in the session worktree (H-DRAFT-b9e771b2) ----
        os.environ["CLAUDE_PROJECT_DIR"] = main_repo
        state = os.path.join(tmp, "state")
        os.makedirs(state, exist_ok=True)
        env_wt = hook_env(main_repo, state)

        def py_root(cwd, payload_expr):
            code = ("import sys; sys.path.insert(0, %r); import hyp_config; print(hyp_config.resolve_root(%s))"
                    % (os.path.join(PLUGIN, "hooks", "scripts"), payload_expr))
            q = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=cwd,
                               env=env_wt, timeout=20)
            return q.stdout.strip() or q.stderr.strip()[-120:]

        r = py_root(dyn, "None")
        check("resolve-no-payload-process-cwd-worktree", rp(r) == rp(dyn), r)
        r = py_root(foreign, "None")
        check("resolve-no-payload-process-cwd-foreign-falls-back", rp(r) == rp(main_repo), r)
        r = py_root(nongit, "None")
        check("resolve-no-payload-process-cwd-nongit-falls-back", rp(r) == rp(main_repo), r)
        r = py_root(main_repo, "{'cwd': %r}" % dyn)
        check("resolve-payload-worktree-wins-over-process-cwd-main", rp(r) == rp(dyn), r)

        before_main = tree(main_repo)
        compiler = os.path.join(PLUGIN, "scripts", "compile-dashboard.py")
        stop_payload = {"session_id": "selftest-hw", "cwd": dyn, "hook_event_name": "Stop", "stop_hook_active": False}
        start_payload = {"session_id": "selftest-hw", "cwd": dyn, "hook_event_name": "SessionStart", "source": "startup"}
        p = run_cmd([sys.executable, compiler, "--quiet", "--hook"], dyn, stop_payload, env_wt)
        check("compiler-hook-mode-writes-worktree",
              p.returncode == 0 and os.path.isfile(os.path.join(dyn, "DASHBOARD.md"))
              and os.path.isfile(os.path.join(dyn, "decisions.html"))
              and not os.path.exists(os.path.join(main_repo, "DASHBOARD.md")),
              "rc=%d dashboard=%s main-dashboard=%s" % (p.returncode, os.path.isfile(os.path.join(dyn, "DASHBOARD.md")),
                                                         os.path.exists(os.path.join(main_repo, "DASHBOARD.md"))))
        p = run_cmd([sys.executable, compiler, "--check", "--hook"], dyn, start_payload, env_wt)
        check("compiler-hook-mode-check-fresh-in-worktree", p.returncode == 0, "rc=%d %s" % (p.returncode, p.stdout.strip()[:80]))

        lj_payload = {"session_id": "selftest-hw", "cwd": dyn, "hook_event_name": "PreToolUse", "tool_name": "Write",
                      "tool_input": dict(RULE_WRITE, file_path=os.path.join(dyn, "CLAUDE.md"))}
        p = run_cmd([sys.executable, os.path.join(PLUGIN, "hooks", "scripts", "license-join-hook.py")], dyn, lj_payload, env_wt)
        corpus = os.path.join(dyn, ".claude", "license-join-corpus")
        link = os.path.join(corpus, "artifacts")
        fires = os.path.join(dyn, ".claude", "license-join-fires.log")
        fires_txt = open(fires, encoding="utf-8").read() if os.path.isfile(fires) else ""
        check("license-join-hook-writes-worktree",
              p.returncode == 0 and "RULE-LICENSE" in p.stdout and os.path.isdir(corpus) and os.path.islink(link)
              and rp(os.readlink(link)) == rp(dyn) and fires_txt.count("RULE-LICENSE") == 1
              and not os.path.exists(os.path.join(main_repo, ".claude", "license-join-corpus"))
              and not os.path.exists(os.path.join(main_repo, ".claude", "license-join-fires.log")),
              "rc=%d out=%s link=%s" % (p.returncode, p.stdout.strip()[:60], os.path.islink(link)))

        for name in ("harden-check.sh", "leak-status.sh"):
            script = os.path.join(PLUGIN, "scripts", name)
            p = run_cmd(["sh", script, "--print-root"], dyn, {}, env_wt)
            check("%s-root-is-process-cwd-worktree" % name, rp(p.stdout.strip()) == rp(dyn), p.stdout.strip()[-100:])
            p = run_cmd(["sh", script, "--print-root"], dyn, {}, hook_env(main_repo, state, {"HYP_ROOT": main_repo}))
            check("%s-root-honours-HYP_ROOT" % name, rp(p.stdout.strip()) == rp(main_repo), p.stdout.strip()[-100:])

        ssb = os.path.join(PLUGIN, "hooks", "scripts", "session-start-budget.py")
        p = run_cmd([sys.executable, "-S", "-E", ssb, "run", "probe-root", 'printf "%s\\n" "$HYP_ROOT"'], dyn,
                    start_payload, hook_env(main_repo, state, {"HYP_SESSION_START_BUDGET": "60"}), timeout=90)
        first = (p.stdout.strip().splitlines() or [""])[0]
        check("ssb-hands-HYP_ROOT-worktree", rp(first) == rp(dyn) if first.startswith("/") else False,
              (p.stdout.strip() or p.stderr.strip())[:120])
        check("ssb-state-keyed-by-resolved-root",
              os.path.isfile(os.path.join(state, "session-start", ssb_key(dyn), "probe-root.out")),
              "state/session-start/%s/probe-root.out" % ssb_key(dyn))

        p = run_cmd([sys.executable, os.path.join(PLUGIN, "hooks", "scripts", "commit-backstop.py")], dyn,
                    {"session_id": "selftest-hw", "cwd": dyn, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "tool_input": {"command": "git commit -m x"}}, env_wt)
        check("commit-backstop-no-argv-resolves", p.returncode in (0, 1) and "Traceback" not in p.stderr,
              "rc=%s stderr=%s" % (p.returncode, p.stderr.strip()[-80:]))
        write(dyn, "ledger/ledger.jsonl", json.dumps({"kind": "commitment", "id": "wt-open-1", "date": "2026-09-13",
                                                       "text": "open on the branch [closes-when: path-exists=never.md]"}) + "\n")
        p = run_cmd([sys.executable, os.path.join(PLUGIN, "hooks", "scripts", "session_resolver.py")], dyn, start_payload, env_wt)
        check("session-resolver-no-argv-reads-worktree",
              p.returncode == 0 and "Traceback" not in p.stderr and "usage:" not in p.stderr and "wt-open-1" in p.stdout,
              "rc=%s out=%s err=%s" % (p.returncode, p.stdout.strip()[:80], p.stderr.strip()[-60:]))

        check("main-checkout-untouched-by-worktree-session", tree(main_repo) == before_main,
              "changed: %s" % sorted(k for k in set(before_main) | set(tree(main_repo))
                                     if before_main.get(k) != tree(main_repo).get(k))[:6])

        # plain checkout (no worktree): the ON writers add exactly the released plugin's path set
        plain = os.path.join(tmp, "plain")
        mk_consumer(plain)
        env_plain = hook_env(plain, state)
        before = tree(plain)
        p1 = run_cmd([sys.executable, compiler, "--quiet", "--hook"], plain,
                     dict(stop_payload, cwd=plain), env_plain)
        p2 = run_cmd([sys.executable, os.path.join(PLUGIN, "hooks", "scripts", "license-join-hook.py")], plain,
                     dict(lj_payload, cwd=plain, tool_input=dict(RULE_WRITE, file_path=os.path.join(plain, "CLAUDE.md"))), env_plain)
        after = tree(plain)
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        check("plain-checkout-parity-path-set", p1.returncode == 0 and p2.returncode == 0 and changed == RELEASED_PLAIN_PATH_SET,
              "changed=%s" % sorted(changed))
        check("plain-checkout-artifacts-link-targets-checkout",
              rp(os.readlink(os.path.join(plain, ".claude", "license-join-corpus", "artifacts"))) == rp(plain),
              "the released wrapper linked artifacts -> ${CLAUDE_PROJECT_DIR:-$(pwd)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
