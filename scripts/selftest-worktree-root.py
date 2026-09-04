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

Usage: python3 scripts/selftest-worktree-root.py        exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-DRAFT-e90628b6 worktree-aware-hook-root (2026-09-03).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

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


def run_hook(rel, payload, env_root):
    env = dict(GIT_ENV)
    env["CLAUDE_PLUGIN_ROOT"] = PLUGIN
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
