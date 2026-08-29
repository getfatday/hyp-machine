#!/usr/bin/env python3
"""SessionStart uncommitted-capture warning (hyp).

Committed history is the durability floor: a committed raw file or fragment
wiped by any command restores byte-identical via `git checkout <sha> -- <path>`,
but an UNCOMMITTED capture that gets destroyed is gone forever — between
creation and its capture commit, a file is one Bash command from unrecoverable.
This hook prints one warning line naming uncommitted files under the raw and
journal-fragment directories so the session commits them first.

Advisory only: never blocks, fails open on any error, always exits 0. Silent
when git is unavailable, the directory is not a repository, or everything
under the guarded directories is committed.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import in_dir, load_config, resolve_root


def uncommitted_paths(root):
    """Repo-relative paths with ANY uncommitted state, or None when no git."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "-uall"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    paths = []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    root = resolve_root(payload)
    cfg = load_config(root)
    paths = uncommitted_paths(root)
    if not paths:
        sys.exit(0)
    exposed = sorted(p for p in paths
                     if (in_dir(p, cfg["raw_dir"]) or in_dir(p, cfg["journal_dir"]))
                     and not p.endswith(".gitkeep"))
    if not exposed:
        sys.exit(0)
    shown = ", ".join(exposed[:3])
    more = "" if len(exposed) <= 3 else " (+%d more)" % (len(exposed) - 3)
    print("hyp WARNING: %d uncommitted capture file(s): one Bash command "
          "from unrecoverable — commit now: %s%s (a capture is complete only "
          "when its commit lands; committed files recover via git checkout)."
          % (len(exposed), shown, more))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
