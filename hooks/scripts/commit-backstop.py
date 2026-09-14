#!/usr/bin/env python3
"""Commit-time backstop (hyp, experiments profile): detect unregistered-experiment-shaped staged changes.

Reads the staged state of a git repo (`git diff --cached --name-status`) and the
pending commit message (`<repo>/.git/COMMIT_EDITMSG`, falling back to an optional
message-file argument), then flags iff:

    (tinker-verb message OR a scratch/tmp/experiment/probe-named new file)
    AND no hypothesis spec file is staged

On a flag: print exactly one line "BACKSTOP<TAB><signal>: <detail>" and exit 1.
Otherwise: print nothing and exit 0.

Usage:
    commit-backstop.py [<repo-path>] [message-file]

Root: without <repo-path> (the hooks.json form) the repository is hyp_config.resolve_root
over the hook payload on stdin -- the checkout the session works in, a linked worktree
included, never the launch directory alone (lab H-DRAFT-b9e771b2-hook-writes-worktree).
With <repo-path>, that path wins, refined to the payload cwd's worktree of the same
repository as before (hyp_config.worktree_root).

Deterministic and offline: the only subprocess invoked is git; no network, no
randomness, no timestamps, no unordered-collection iteration in any output path.

Advisory only — the hook wrapper always exits 0, and any parse failure or
unexpected error here fails OPEN (silent, exit 0): this is a nudge toward
registering a hypothesis spec, never an enforcement gate.

Om-feedback staging (lab keep H-DRAFT-fb9c08b9-om-ledger-commit-path): on the same
`git commit`-shaped payload, before the check above and regardless of profile, this hook
also stages the plugin's own append-only feedback ledger (`ledger/om-feedback.jsonl`, or
your `.claude/hyp.json` `om_feedback_file`) when it is dirty, printing exactly one line
(`OM-FEEDBACK-STAGED <n> rows`, or `OM-FEEDBACK-HELD <reason>` when a row carries a
forbidden key) so the worker's rows ride your next commit without your naming the file.
See `docs/passive-feedback.md`.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import load_config, profile_at_least, resolve_root, safe_rel_path, worktree_root

# Tinker-verb tokens for the commit message, checked in this fixed order so a
# message matching more than one token still reports deterministically.
_TINKER_TOKENS = [
    ("try", re.compile(r"\btry\b", re.IGNORECASE)),
    ("test", re.compile(r"\btest\b", re.IGNORECASE)),
    ("see if", re.compile(r"\bsee\s+if\b", re.IGNORECASE)),
    ("experiment", re.compile(r"\bexperiment\b", re.IGNORECASE)),
    ("what if", re.compile(r"\bwhat\s+if\b", re.IGNORECASE)),
    ("quick check", re.compile(r"\bquick\s+check\b", re.IGNORECASE)),
]

# Scratch-shaped basename prefixes for new files, checked in this fixed order.
_SCRATCH_PREFIXES = ["scratch", "tmp", "experiment", "probe"]


def _hypothesis_re(hyp_dir):
    """<hypotheses dir>/H-NNN-slug.md, flat directory only."""
    return re.compile(r"^" + re.escape(hyp_dir.strip("/")) + r"/H-[^/]+\.md$")


def _read_message(repo, message_file):
    """COMMIT_EDITMSG takes priority; fall back to an optional message file."""
    edit_msg_path = os.path.join(repo, ".git", "COMMIT_EDITMSG")
    for path in (edit_msg_path, message_file):
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue
            # Drop git's editor-template comment lines. Never present after a
            # scripted `git commit -m`, but harmless to strip either way.
            return "".join(line for line in lines if not line.startswith("#"))
    return ""


def _staged_name_status(repo):
    """Raw `git diff --cached --name-status` lines for repo (git-only subprocess)."""
    result = subprocess.run(
        ["git", "-C", repo, "diff", "--cached", "--name-status"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
        text=True,
    )
    return [line for line in result.stdout.split("\n") if line]


def _parse_staged(lines):
    """Return (added_paths, all_paths) from `diff --cached --name-status` lines."""
    added, all_paths = [], []
    for line in lines:
        fields = line.split("\t")
        status, paths = fields[0], fields[1:]
        all_paths.extend(paths)
        if status == "A" and paths:
            added.append(paths[0])
    return added, all_paths


def _hypothesis_staged(all_paths, hyp_re):
    return any(hyp_re.match(p) for p in all_paths)


def _message_signal(message):
    for token, pattern in _TINKER_TOKENS:
        if pattern.search(message):
            return token
    return None


def _filename_signal(added_paths):
    for path in sorted(added_paths):
        base = os.path.basename(path).lower()
        for prefix in _SCRATCH_PREFIXES:
            if base.startswith(prefix):
                return path, prefix
    return None, None


def _payload():
    """The hook payload on stdin; {} when stdin is a tty or unreadable (read once)."""
    try:
        if sys.stdin.isatty():
            return {}
        payload = json.loads(sys.stdin.read() or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _session_repo(repo, payload):
    """With an argv root (a human or an older wiring): that root, refined to the payload
    cwd's worktree of the same repository (hyp_config.worktree_root). Without one (the
    hooks.json form): hyp_config.resolve_root(payload) -- the one root contract."""
    try:
        if not repo:
            return resolve_root(payload)
        cwd = payload.get("cwd")
        return worktree_root(cwd, repo) or repo
    except Exception:
        return repo or "."


# --------------------------------------------------------------------------- om-feedback staging
# H-DRAFT-fb9c08b9-om-ledger-commit-path (lab keep; VERDICT.json, journal fragment
# 0538-fb9c08b9-verdict.md): one clause that stages the plugin's own append-only feedback
# ledger on a real `git commit` so the worker's rows ride the person's next commit without
# anyone naming the file. Additive only: no existing name above this point is touched, and
# this clause runs before -- and independently of -- the backstop's own experiments-profile
# gate below, because the feedback ledger is a capture-profile feature, not an
# experiments-profile one.
#
# Ported onto this 0.29.0 hook from the lane's 0.28.0-based fixture with two drifts
# resolved: (1) the ledger path now reads the consumer's `.claude/hyp.json`
# `om_feedback_file` override through the one shared validator, hyp_config.safe_rel_path --
# the same rule scripts/om-worker.py's `ledger_rel`, init-scaffold.py's union row and
# scripts/merge-attrs-check.py's `om_feedback_rel` already apply (the lane's fixture predates
# that key and hardcoded the default path); (2) every git subprocess call below carries an
# explicit timeout under this hook row's own 10 s budget (the fixture's calls had none --
# VERIFY.md finding 9.7). The forbidden-key set is a mirrored constant, not an import: it
# must equal scripts/om-worker.py's CANARY_KEYS_FORBIDDEN (asserted by
# scripts/selftest-commit-backstop.py), kept local so this hook stays one stdlib+git file
# with no cross-directory import at hook runtime.
OM_LEDGER_DEFAULT_REL = "ledger/om-feedback.jsonl"
OM_FORBIDDEN_KEYS = ("tool_input", "prompt", "last_assistant_message")
OM_STAGEABLE_STATUSES = (" M", "MM", "AM", "??")
OM_GIT_TIMEOUT_S = 5
_OM_GIT_COMMIT_RE = re.compile(r"^\s*git\s+commit\b")


def _om_is_git_commit(payload):
    """True iff the payload's tool_input.command is itself a `git commit` invocation (anchored
    at the start, never a substring match) -- guards against a command that merely mentions
    "git commit" inside a string or a heredoc (pre-mortem risk iv). Widening this gate to also
    catch `timeout ... git commit`, `cd <dir> && git commit` or `git -c ... commit` is a refine
    successor, never a ship-time edit of these kept bytes (VERIFY.md finding 9.2)."""
    command = ((payload or {}).get("tool_input") or {}).get("command") or ""
    return bool(_OM_GIT_COMMIT_RE.match(command))


def _om_ledger_rel(root):
    """The configured ledger path: `.claude/hyp.json` `om_feedback_file` through
    hyp_config.safe_rel_path -- the same rule scripts/om-worker.py, init-scaffold.py and
    merge-attrs-check.py apply to the same key, so this clause never stages a path none of
    those three would recognize. An absolute value or one with a `..` segment falls back to
    OM_LEDGER_DEFAULT_REL, exactly as it does everywhere else."""
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        value = data.get("om_feedback_file") if isinstance(data, dict) else None
    except (OSError, ValueError):
        value = None
    return safe_rel_path(value, OM_LEDGER_DEFAULT_REL)


def _om_ledger_status(root, ledger_rel):
    """`git status --porcelain -- <ledger_rel>`'s two-char code for `root`, "" when clean or
    absent, None when git itself could not be asked (including a timeout)."""
    try:
        result = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--", ledger_rel],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, text=True,
            timeout=OM_GIT_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    line = result.stdout.strip("\n")
    return line[:2] if line else ""


def _om_forbidden_reason(root, ledger_rel):
    """The first forbidden-key class among the ledger's rows on disk, or None -- the worker
    lane's own lint (H-DRAFT-35397146-om-worker-deterministic CANARY_KEYS_FORBIDDEN, mirrored
    above), re-run here so a row that must never be written is also never staged."""
    path = os.path.join(root, *ledger_rel.split("/"))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        blob = json.dumps(row)
        for key in OM_FORBIDDEN_KEYS:
            if ('"%s"' % key) in blob:
                return "forbidden key %s" % key
    return None


def _om_staged_new_line_count(root, ledger_rel):
    """Count of newly staged lines in the cached diff of the ledger -- correct whether the file
    is a brand-new addition (diff from /dev/null) or an appended tracked file (a few new `+`
    hunk lines): every `+` line except the `+++` header."""
    try:
        result = subprocess.run(
            ["git", "-C", root, "diff", "--cached", "--", ledger_rel],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, text=True,
            timeout=OM_GIT_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError, ValueError):
        return 0
    return sum(1 for line in result.stdout.split("\n")
               if line.startswith("+") and not line.startswith("+++"))


def _om_feedback_staging_clause(payload, root):
    """Advisory, additive, fail-open: prints at most one line, never raises past itself, never
    changes this hook's exit code. Runs once per `git commit`-shaped payload:
      - not a `git commit` command, or the ledger absent/clean -> silent, nothing staged
      - the ledger carries unstaged rows and none is forbidden -> `git add` it, print
        "OM-FEEDBACK-STAGED <n> rows"
      - the ledger carries unstaged rows and one is forbidden -> print
        "OM-FEEDBACK-HELD <reason>", stage nothing
    """
    try:
        if not root or not _om_is_git_commit(payload):
            return
        ledger_rel = _om_ledger_rel(root)
        status = _om_ledger_status(root, ledger_rel)
        if not status or status not in OM_STAGEABLE_STATUSES:
            return
        reason = _om_forbidden_reason(root, ledger_rel)
        if reason is not None:
            print("OM-FEEDBACK-HELD %s" % reason)
            return
        subprocess.run(["git", "-C", root, "add", "--", ledger_rel], check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=OM_GIT_TIMEOUT_S)
        print("OM-FEEDBACK-STAGED %d rows" % _om_staged_new_line_count(root, ledger_rel))
    except Exception:
        return


def main(argv):
    payload = _payload()
    repo = _session_repo(argv[1] if len(argv) > 1 and argv[1] else None, payload)
    if not repo or not os.path.isdir(repo):
        return 0
    _om_feedback_staging_clause(payload, repo)
    message_file = argv[2] if len(argv) > 2 else None

    try:
        lines = _staged_name_status(repo)
    except (subprocess.CalledProcessError, OSError, ValueError):
        return 0

    added_paths, all_paths = _parse_staged(lines)
    cfg = load_config(repo)
    if not profile_at_least(cfg, "experiments"):
        return 0
    hyp_re = _hypothesis_re(cfg["hypotheses_dir"])
    if _hypothesis_staged(all_paths, hyp_re):
        return 0

    token = _message_signal(_read_message(repo, message_file))
    if token is not None:
        print("BACKSTOP\tmessage: tinker-verb '%s' in the commit message and no "
              "hypothesis spec staged — if this is an experiment, register a spec "
              "first (hypothesis skill); advisory only, not a block" % token)
        return 1

    path, prefix = _filename_signal(added_paths)
    if path is not None:
        print("BACKSTOP\tfilename: staged new file '%s' matches scratch-naming "
              "prefix '%s' and no hypothesis spec staged — if this is an "
              "experiment, register a spec first (hypothesis skill); advisory "
              "only, not a block" % (path, prefix))
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        sys.exit(0)
