#!/usr/bin/env python3
"""license-join-hook.py -- the PreToolUse (Edit|Write|MultiEdit) entry for the H-250 license-join advisory.

Replaces the inline `sh` wrapper hooks.json carried through 0.26.0, which rooted the corpus
directory, the `artifacts` symlink and the fires log at `${CLAUDE_PROJECT_DIR:-.}` -- the MAIN
checkout in a session that entered a worktree after launch (Claude Code keeps
CLAUDE_PROJECT_DIR at the launch directory; lab H-DRAFT-b9e771b2-hook-writes-worktree, the
baseline table). The root is now `hyp_config.resolve_root(payload)`, the one contract every
hook writer shares. What is written is unchanged, only where:

    <root>/.claude/license-join-corpus/            mkdir -p on every event
    <root>/.claude/license-join-corpus/artifacts   -> <root> (ln -sfn semantics)
    <root>/.claude/license-join-fires.log          touched, then one `<utc stamp> RULE-LICENSE...`
                                                   line per finding -- only when the check finds one

The check itself is `license-join-check.py` beside this file (a pure reader), called in
process with the payload as its event. Its lines are printed verbatim. Exit 0 always; never a
traceback to the hook host. Stdlib only, Python 3.9.
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FINDING_PREFIX = "RULE-LICENSE"


def _load_check():
    spec = importlib.util.spec_from_file_location(
        "license_join_check", os.path.join(HERE, "license-join-check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _symlink_force(target, link):
    """`ln -sfn target link`: replace an existing symlink (never dereferenced); leave a real
    directory or file at `link` alone."""
    try:
        if os.path.islink(link):
            os.unlink(link)
        elif os.path.lexists(link):
            return
        os.symlink(target, link)
    except OSError:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    from hyp_config import resolve_root
    root = resolve_root(payload)
    corpus = os.path.join(root, ".claude", "license-join-corpus")
    fires = os.path.join(root, ".claude", "license-join-fires.log")
    try:
        os.makedirs(corpus, exist_ok=True)
    except OSError:
        pass
    _symlink_force(root, os.path.join(corpus, "artifacts"))
    lines = _load_check().check(payload, corpus)
    if not lines:
        return 0
    for line in lines:
        print(line)
    try:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(fires, "a", encoding="utf-8") as fh:
            for line in lines:
                if line.startswith(FINDING_PREFIX):
                    fh.write("%s %s\n" % (stamp, line))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - advisory contract: never non-zero, never a traceback
        try:
            print("# license-join-hook: skipped (internal error)")
        except Exception:
            pass
        sys.exit(0)
