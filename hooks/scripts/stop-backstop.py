#!/usr/bin/env python3
"""Stop backstop (hyp): unjournaled-work check, minimal form.

If the working tree holds NEW files under the raw or notes directories
(untracked or staged additions, derived deterministically from
`git status --porcelain -uall`) and no new journal fragment exists, block the
stop ONCE with a reason. When the hook input carries `stop_hook_active`, a
previous block already happened this stop cycle — stay silent so the session
can always end. Fails open when git is unavailable, the directory is not a
repository, or anything else goes wrong.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import in_dir, load_config, resolve_root


def new_paths(root):
    """Repo-relative paths of untracked or staged-added files, or None."""
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
        status, path = line[:2], line[3:]
        if status != "??" and "A" not in status:
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if payload.get("stop_hook_active"):
        sys.exit(0)

    root = resolve_root(payload)
    cfg = load_config(root)
    paths = new_paths(root)
    if paths is None:
        sys.exit(0)

    knowledge = [p for p in paths
                 if (in_dir(p, cfg["raw_dir"]) or in_dir(p, cfg["notes_dir"]))
                 and not p.endswith(".gitkeep")]
    fragments = [p for p in paths
                 if in_dir(p, cfg["journal_dir"]) and p.endswith(".md")]

    if knowledge and not fragments:
        shown = ", ".join(sorted(knowledge)[:3])
        more = "" if len(knowledge) <= 3 else " (+%d more)" % (len(knowledge) - 3)
        print(json.dumps({
            "decision": "block",
            "reason": (
                "New knowledge files exist with no journal entry: %s%s. Add one "
                "write-once journal fragment under %s/ (next integer id, date, "
                "one paragraph on what was added and why), or state why no entry "
                "is warranted and stop again." % (shown, more, cfg["journal_dir"])
            ),
        }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
