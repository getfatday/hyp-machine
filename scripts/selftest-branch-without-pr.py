#!/usr/bin/env python3
"""selftest-branch-without-pr.py -- regression test for harden-check ADVISORY-34 branch-without-pr.

Builds a temp bare remote plus a clone, puts a fake `gh` shim first on PATH whose `pr list` answer is
controlled by GH_SHIM_MODE (0 = no open PR, 1 = one open PR, fail = every call fails), and runs ONLY the
ADVISORY-34 block (extracted from scripts/harden-check.sh next to this file) inside the clone.
Cases: fires on a feature branch ahead with no PR; silent on the default branch, on a branch 0 ahead,
with an open PR, with HARDEN_PR_CHECK=0, with HARDEN_PR_MIN above the ahead count, and with no gh on
PATH; loud (one line naming the account switch) when gh fails both calls. One PASS/FAIL line per check
and a final RESULT line; exit 1 on any FAIL. Standard library, Python 3.9; never touches the real
gh config or any real repository."""
import os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HARDEN = os.path.join(HERE, "harden-check.sh")
SHIM = """#!/bin/sh
# fake gh: `gh auth token --user X` prints a token; `gh pr list ...` answers per GH_SHIM_MODE
echo "$@" >> "$GH_SHIM_LOG"
case "$1 $2" in
  "auth token") [ "${GH_SHIM_MODE}" = fail ] && exit 1; echo shim-token; exit 0 ;;
  "pr list") [ "${GH_SHIM_MODE}" = fail ] && exit 1; echo "${GH_SHIM_MODE}"; exit 0 ;;
esac
exit 1
"""
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + detail) if detail and not ok else ""))


def extract_block():
    t = open(HARDEN).read()
    m = re.search(r"^# ADVISORY-34 branch-without-pr", t, re.M)
    if not m:
        return None
    end = t.index("\nfi\n", m.start()) + len("\nfi\n")
    return t[m.start():end]


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, check=True).stdout


def run_block(block, cwd, env_extra, gh_on_path=True, mode="0"):
    tmp = tempfile.mkdtemp(prefix="adv34-")
    script = os.path.join(tmp, "block.sh")
    open(script, "w").write("#!/bin/bash\nW=0\n" + block + "\nexit 0\n")
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir)
    if gh_on_path:
        shim = os.path.join(bindir, "gh")
        open(shim, "w").write(SHIM)
        os.chmod(shim, 0o755)
    for tool in ("git", "sed", "timeout", "bash", "sh"):
        p = shutil.which(tool)
        if p and not os.path.exists(os.path.join(bindir, tool)):
            os.symlink(p, os.path.join(bindir, tool))
    env = {"PATH": bindir, "HOME": tmp, "GH_SHIM_MODE": mode, "GH_SHIM_LOG": os.path.join(tmp, "gh.log"),
           "GH_CONFIG_DIR": os.path.join(tmp, "ghcfg"), "LANG": "C.UTF-8"}
    env.update(env_extra)
    r = subprocess.run(["bash", script], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    log = open(env["GH_SHIM_LOG"]).read() if os.path.exists(env["GH_SHIM_LOG"]) else ""
    shutil.rmtree(tmp, ignore_errors=True)
    return r.returncode, r.stdout, log


def main():
    block = extract_block()
    check("block-present-in-harden-check", block is not None)
    if block is None:
        print("RESULT: FAIL"); return 1
    work = tempfile.mkdtemp(prefix="adv34-repo-")
    try:
        bare = os.path.join(work, "remote.git"); clone = os.path.join(work, "clone")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        subprocess.run(["git", "clone", "-q", bare, clone], check=True, capture_output=True)
        git(clone, "config", "user.email", "t@example.invalid"); git(clone, "config", "user.name", "selftest")
        git(clone, "remote", "set-url", "origin", "https://github.com/fixture/repo.git")
        git(clone, "remote", "set-url", "--push", "origin", bare)
        # a local origin/main ref: commit on main, "push" by updating the remote-tracking ref directly
        open(os.path.join(clone, "a.txt"), "w").write("a\n"); git(clone, "add", "a.txt"); git(clone, "commit", "-q", "-m", "base")
        git(clone, "update-ref", "refs/remotes/origin/main", "HEAD"); git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(clone, "checkout", "-q", "-b", "feature/x")
        open(os.path.join(clone, "b.txt"), "w").write("b\n"); git(clone, "add", "b.txt"); git(clone, "commit", "-q", "-m", "ahead")
        rc, out, log = run_block(block, clone, {}, mode="0")
        lines = [l for l in out.splitlines() if "branch-without-pr" in l]
        check("fires-ahead-no-pr", rc == 0 and len(lines) == 1 and "feature/x" in lines[0] and "1 commit(s)" in lines[0]
              and "origin/main" in lines[0] and "gh pr create --draft" in lines[0], out.strip() or "no output")
        check("pins-owner-account-first", "auth token --user fixture" in log.splitlines()[0] if log else False, log)
        rc, out, _ = run_block(block, clone, {}, mode="1")
        check("silent-with-open-pr", rc == 0 and "branch-without-pr" not in out, out)
        rc, out, _ = run_block(block, clone, {"HARDEN_PR_CHECK": "0"}, mode="0")
        check("silent-when-disabled", rc == 0 and out.strip() == "", out)
        rc, out, _ = run_block(block, clone, {"HARDEN_PR_MIN": "100"}, mode="0")
        check("silent-below-min", rc == 0 and out.strip() == "", out)
        rc, out, _ = run_block(block, clone, {}, gh_on_path=False, mode="0")
        check("silent-without-gh", rc == 0 and out.strip() == "", out)
        rc, out, _ = run_block(block, clone, {}, mode="fail")
        lines = [l for l in out.splitlines() if "branch-without-pr" in l]
        check("loud-when-unverifiable", rc == 0 and len(lines) == 1 and "could not read pull requests" in lines[0]
              and "gh auth switch --user fixture" in lines[0], out.strip() or "no output")
        git(clone, "checkout", "-q", "main")
        rc, out, _ = run_block(block, clone, {}, mode="0")
        check("silent-on-default-branch", rc == 0 and out.strip() == "", out)
        git(clone, "checkout", "-q", "-b", "feature/even"); git(clone, "reset", "-q", "--hard", "origin/main")
        rc, out, _ = run_block(block, clone, {}, mode="0")
        check("silent-zero-ahead", rc == 0 and out.strip() == "", out)
        git(clone, "checkout", "-q", "--detach")
        rc, out, _ = run_block(block, clone, {}, mode="0")
        check("silent-detached-head", rc == 0 and out.strip() == "", out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
