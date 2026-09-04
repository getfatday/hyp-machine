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
    commit-backstop.py <repo-path> [message-file]

Deterministic and offline: the only subprocess invoked is git; no network, no
randomness, no timestamps, no unordered-collection iteration in any output path.

Advisory only — the hook wrapper always exits 0, and any parse failure or
unexpected error here fails OPEN (silent, exit 0): this is a nudge toward
registering a hypothesis spec, never an enforcement gate.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import load_config, profile_at_least, worktree_root

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


def _session_repo(repo):
    """hooks.json passes CLAUDE_PROJECT_DIR as argv[1]; in a worktree-isolated session
    the staged files live in the worktree the hook payload's cwd names. Prefer that
    toplevel when it is a linked worktree of the same repository (hyp_config.worktree_root);
    otherwise keep argv[1]. Reads the payload from stdin only when stdin is not a tty."""
    try:
        if sys.stdin.isatty():
            return repo
        payload = json.loads(sys.stdin.read() or "{}")
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        return worktree_root(cwd, repo) or repo
    except Exception:
        return repo


def main(argv):
    if len(argv) < 2 or not argv[1]:
        return 0
    repo = _session_repo(argv[1])
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
