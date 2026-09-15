#!/usr/bin/env python3
"""selftest-commit-backstop.py -- regression test for hooks/scripts/commit-backstop.py,
specifically the om-feedback staging clause (lab keep H-DRAFT-fb9c08b9-om-ledger-commit-path)
ported onto it, and a proof that the pre-existing backstop behaviour is unchanged.

Runs the INSTALLED hook (the copy beside `hooks/scripts/hyp_config.py` this file's plugin
ships) over throwaway consumer repositories under a temp dir, exactly as `hooks.json` invokes
it (stdin payload, no argv):

  om-untracked-ledger-staged        a brand-new, untracked ledger with three rows stages with
                                     the exact `OM-FEEDBACK-STAGED 3 rows` line; the commit
                                     that follows carries it
  om-appended-tracked-ledger-staged a committed ledger with two appended rows stages with the
                                     exact `OM-FEEDBACK-STAGED 2 rows` line (n counts only the
                                     new lines); the commit that follows carries the append
  om-clean-ledger-silent            a committed, unmodified ledger -> silent, exit 0, nothing
                                     staged
  om-absent-ledger-silent           no ledger file at all -> silent, exit 0
  om-non-commit-payload-silent      `git status` (not `git commit`) over a dirty ledger ->
                                     silent, nothing staged (the gate is not fooled into
                                     staging on an unrelated Bash call)
  om-heredoc-mentioning-git-commit-silent   a command whose BODY contains the substring
                                     "git commit" inside a heredoc, but which does not itself
                                     begin with `git commit` -> silent, nothing staged
                                     (anchored match, not a substring scan)
  om-forbidden-key-held              a ledger row carrying a forbidden key ("tool_input") ->
                                     exactly one `OM-FEEDBACK-HELD forbidden key tool_input`
                                     line, nothing staged
  om-linked-worktree-stages-its-own-ledger   a `git commit` payload from a linked worktree
                                     stages THAT worktree's ledger; the main checkout's own
                                     dirty ledger is untouched
  om-runs-at-capture-profile          the clause fires with no `.claude/hyp.json` at all (the
                                     default `capture` profile) -- proof it runs independently
                                     of the backstop's own experiments-profile gate
  backstop-filename-signal-unchanged  the pre-existing behaviour: a staged scratch-prefixed new
                                     file with no hypothesis spec staged still prints the
                                     `BACKSTOP\\tfilename:` line and exits 1, at the experiments
                                     profile, om clause silent alongside it
  om-clean-case-hook-wall-under-row-timeout   the hook wall on the fully-silent (om clause +
                                     backstop) case stays well under the hook row's own 10 s
                                     timeout (hooks/hooks.json)
  om-forbidden-keys-mirrors-worker-canary    the hook's mirrored OM_FORBIDDEN_KEYS constant
                                     equals scripts/om-worker.py's CANARY_KEYS_FORBIDDEN,
                                     parsed with `ast` from both files (no import at runtime)

Usage: python3 scripts/selftest-commit-backstop.py        exit 0 = all PASS, 1 = any FAIL
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

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(PLUGIN, "hooks", "scripts", "commit-backstop.py")
HOOK_ROW_TIMEOUT_S = 10.0

GIT_ENV = {"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

RESULTS = []


def check(name, cond, detail):
    RESULTS.append(cond)
    print(("PASS " if cond else "FAIL ") + name + ": " + detail)


def module_level_tuple_constant(path, name):
    """The literal string tuple a module-level `<name> = (...)` assignment holds, parsed with
    `ast` (never imported, so this selftest never executes either hook module to compare them).
    None if the file has no such top-level assignment."""
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


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), check=True, capture_output=True,
                           text=True, env=GIT_ENV)


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def mk_repo(path, profile="experiments"):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if profile is not None:
        write(path, ".claude/hyp.json", json.dumps({"profile": profile}))
    write(path, "README.md", "# consumer\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")


def run_hook(root, command, cwd=None, project_dir=None):
    """Invoke the installed hook exactly as hooks.json does: no argv, the payload on stdin."""
    payload = {"session_id": "selftest", "cwd": cwd or root, "hook_event_name": "PreToolUse",
               "tool_name": "Bash", "tool_input": {"command": command}}
    env = dict(GIT_ENV)
    env["CLAUDE_PROJECT_DIR"] = project_dir or root
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                        text=True, env=env, cwd=cwd or root, timeout=60)
    wall = time.monotonic() - t0
    return p, wall


def staged_paths(root):
    out = git(root, "diff", "--cached", "--name-only").stdout
    return [l for l in out.split("\n") if l]


def status_line(root, rel):
    out = git(root, "status", "--porcelain", "--", rel).stdout.strip("\n")
    return out[:2] if out else ""


def main():
    # ---- om-forbidden-keys-mirrors-worker-canary (no tmp dir needed) ------------------
    worker = os.path.join(PLUGIN, "scripts", "om-worker.py")
    hook_keys = module_level_tuple_constant(HOOK, "OM_FORBIDDEN_KEYS")
    worker_keys = module_level_tuple_constant(worker, "CANARY_KEYS_FORBIDDEN")
    check("om-forbidden-keys-mirrors-worker-canary",
          hook_keys is not None and hook_keys == worker_keys,
          "commit-backstop.OM_FORBIDDEN_KEYS=%r om-worker.CANARY_KEYS_FORBIDDEN=%r"
          % (hook_keys, worker_keys))

    tmp = tempfile.mkdtemp(prefix="hyp-selftest-commit-backstop-")
    try:
        # ---- om-untracked-ledger-staged ------------------------------------------------
        r1 = os.path.join(tmp, "untracked")
        mk_repo(r1)
        write(r1, "ledger/om-feedback.jsonl",
              "".join(json.dumps({"kind": "session-observed", "n": i}) + "\n" for i in range(3)))
        p, _ = run_hook(r1, "git commit -m ok")
        check("om-untracked-ledger-staged",
              p.stdout == "OM-FEEDBACK-STAGED 3 rows\n" and p.returncode == 0
              and staged_paths(r1) == ["ledger/om-feedback.jsonl"],
              "stdout=%r rc=%d staged=%s" % (p.stdout, p.returncode, staged_paths(r1)))
        git(r1, "commit", "-q", "-m", "carries the ledger")
        show = git(r1, "show", "--stat", "HEAD").stdout
        check("om-untracked-ledger-committed", "ledger/om-feedback.jsonl" in show,
              show.strip()[-200:])

        # ---- om-appended-tracked-ledger-staged -----------------------------------------
        r2 = os.path.join(tmp, "appended")
        mk_repo(r2)
        write(r2, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 0}) + "\n")
        git(r2, "add", "-A")
        git(r2, "commit", "-q", "-m", "seed ledger")
        with open(os.path.join(r2, "ledger", "om-feedback.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "session-observed", "n": 1}) + "\n")
            fh.write(json.dumps({"kind": "session-observed", "n": 2}) + "\n")
        p, _ = run_hook(r2, "git commit -m ok")
        check("om-appended-tracked-ledger-staged",
              p.stdout == "OM-FEEDBACK-STAGED 2 rows\n" and p.returncode == 0
              and staged_paths(r2) == ["ledger/om-feedback.jsonl"],
              "stdout=%r rc=%d staged=%s" % (p.stdout, p.returncode, staged_paths(r2)))
        git(r2, "commit", "-q", "-m", "carries the append")
        numstat = git(r2, "show", "--numstat", "HEAD").stdout.strip().splitlines()[-1]
        check("om-appended-tracked-ledger-committed",
              numstat.split("\t") == ["2", "0", "ledger/om-feedback.jsonl"], numstat)

        # ---- om-clean-ledger-silent ------------------------------------------------------
        r3 = os.path.join(tmp, "clean")
        mk_repo(r3)
        write(r3, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 0}) + "\n")
        git(r3, "add", "-A")
        git(r3, "commit", "-q", "-m", "seed ledger")
        p, wall_clean = run_hook(r3, "git commit -m ok")
        check("om-clean-ledger-silent",
              p.stdout == "" and p.returncode == 0 and staged_paths(r3) == [],
              "stdout=%r rc=%d" % (p.stdout, p.returncode))

        # ---- om-absent-ledger-silent -------------------------------------------------
        r4 = os.path.join(tmp, "absent")
        mk_repo(r4)
        p, _ = run_hook(r4, "git commit -m ok")
        check("om-absent-ledger-silent", p.stdout == "" and p.returncode == 0,
              "stdout=%r rc=%d" % (p.stdout, p.returncode))

        # ---- om-non-commit-payload-silent ---------------------------------------------
        r5 = os.path.join(tmp, "noncommit")
        mk_repo(r5)
        write(r5, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 0}) + "\n")
        p, _ = run_hook(r5, "git status")
        check("om-non-commit-payload-silent",
              p.stdout == "" and p.returncode == 0 and staged_paths(r5) == [],
              "stdout=%r rc=%d staged=%s" % (p.stdout, p.returncode, staged_paths(r5)))

        # ---- om-heredoc-mentioning-git-commit-silent -----------------------------------
        r6 = os.path.join(tmp, "heredoc")
        mk_repo(r6)
        write(r6, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 0}) + "\n")
        heredoc_cmd = "cat <<'EOF' > notes.txt\nremember to git commit later\nEOF\n"
        p, _ = run_hook(r6, heredoc_cmd)
        check("om-heredoc-mentioning-git-commit-silent",
              p.stdout == "" and p.returncode == 0 and staged_paths(r6) == [],
              "stdout=%r rc=%d staged=%s" % (p.stdout, p.returncode, staged_paths(r6)))

        # ---- om-forbidden-key-held ------------------------------------------------------
        r7 = os.path.join(tmp, "forbidden")
        mk_repo(r7)
        write(r7, "ledger/om-feedback.jsonl",
              json.dumps({"kind": "session-observed", "tool_input": "rm -rf /"}) + "\n")
        p, _ = run_hook(r7, "git commit -m ok")
        check("om-forbidden-key-held",
              p.stdout == "OM-FEEDBACK-HELD forbidden key tool_input\n" and p.returncode == 0
              and staged_paths(r7) == [],
              "stdout=%r rc=%d staged=%s" % (p.stdout, p.returncode, staged_paths(r7)))

        # ---- om-linked-worktree-stages-its-own-ledger ----------------------------------
        r8 = os.path.join(tmp, "wtmain")
        mk_repo(r8)
        wt = os.path.join(tmp, "wt-branch")
        git(r8, "worktree", "add", "-q", wt, "-b", "wt-branch")
        write(wt, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 0}) + "\n")
        write(r8, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 99}) + "\n")
        p, _ = run_hook(r8, "git commit -m ok", cwd=wt, project_dir=r8)
        check("om-linked-worktree-stages-its-own-ledger",
              p.stdout == "OM-FEEDBACK-STAGED 1 rows\n" and p.returncode == 0
              and staged_paths(wt) == ["ledger/om-feedback.jsonl"],
              "stdout=%r rc=%d staged=%s" % (p.stdout, p.returncode, staged_paths(wt)))
        check("om-linked-worktree-leaves-main-ledger-untouched",
              staged_paths(r8) == [] and status_line(r8, "ledger/om-feedback.jsonl") == "??",
              "main staged=%s main status=%r" % (staged_paths(r8), status_line(r8, "ledger/om-feedback.jsonl")))

        # ---- om-runs-at-capture-profile (no .claude/hyp.json at all) -------------------
        r9 = os.path.join(tmp, "captureprofile")
        mk_repo(r9, profile=None)
        write(r9, "ledger/om-feedback.jsonl", json.dumps({"kind": "session-observed", "n": 0}) + "\n")
        p, _ = run_hook(r9, "git commit -m ok")
        check("om-runs-at-capture-profile",
              p.stdout == "OM-FEEDBACK-STAGED 1 rows\n" and p.returncode == 0
              and staged_paths(r9) == ["ledger/om-feedback.jsonl"],
              "stdout=%r rc=%d" % (p.stdout, p.returncode))

        # ---- backstop-filename-signal-unchanged (pre-existing behaviour, om silent) ---
        r10 = os.path.join(tmp, "filenamesignal")
        mk_repo(r10)
        write(r10, "scratch-idea.py", "# a scratch file\n")
        git(r10, "add", "-A")
        p, _ = run_hook(r10, "git commit -m ok")
        check("backstop-filename-signal-unchanged",
              p.returncode == 1 and p.stdout.startswith("BACKSTOP\tfilename:")
              and "scratch-idea.py" in p.stdout and "OM-FEEDBACK" not in p.stdout,
              "stdout=%r rc=%d" % (p.stdout, p.returncode))

        # ---- om-clean-case-hook-wall-under-row-timeout ---------------------------------
        check("om-clean-case-hook-wall-under-row-timeout", wall_clean < HOOK_ROW_TIMEOUT_S,
              "wall=%.3fs row-timeout=%.1fs" % (wall_clean, HOOK_ROW_TIMEOUT_S))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(RESULTS)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(RESULTS), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
