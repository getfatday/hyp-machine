#!/usr/bin/env python3
"""merge-attrs-check.py -- do the files the plugin's hooks write carry their merge shape, and are
the ledger's rows one JSON object per line?

    merge-attrs-check.py [--root <repo>] [--quiet]

Read-only. Two checks over the repository at --root (default: hyp_config.resolve_root -- the
process cwd's checkout, a linked worktree included, then CLAUDE_PROJECT_DIR):

  1. merge shape -- `git check-attr merge -- <path>` for every path the plugin's hooks and
     writers touch (templates/gitattributes; lab H-DRAFT-b9e771b2-hook-writes-worktree):
       union   the configured ledger_file (.claude/hyp.json, default ledger/ledger.jsonl),
               .claude/leak-meter-fires.log  -> expected `merge: union`
       derived DASHBOARD.md, decisions.html, ledger/north-stars/*.html (existing files only)
               -> expected `merge: binary`
     Semantic, not textual: a consumer who declares the shape through a glob or a different
     token order passes. Outside a git work tree the check is skipped (nothing to resolve).
  2. row shape -- every non-empty line of the ledger parses as exactly one JSON object and the
     file ends with a newline: the contract under which a union merge of two branches' appends
     is a valid ledger (a multi-line object or a missing final newline would be spliced).

Output: one line per finding --
    MERGE-ATTR-MISSING\t<path>\t<expected merge value>\t<seen>
    LEDGER-ROW-MALFORMED\t<line number or 'eof'>\t<reason>
and a final `merge-attrs-check: <n> finding(s)` (suppressed by --quiet when clean).
Exit 0 clean, 1 findings, 0 when the repository cannot be read (advisory posture; the
harden-check block that wraps this prints one ADVISORY line). Stdlib only, Python 3.9.
"""
import json
import os
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "hooks", "scripts"))

DEFAULT_LEDGER = "ledger/ledger.jsonl"
UNION_FIXED = [".claude/leak-meter-fires.log"]
DERIVED = ["DASHBOARD.md", "decisions.html"]
DERIVED_GLOB_DIR = "ledger/north-stars"


def ledger_rel(root):
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        v = data.get("ledger_file") if isinstance(data, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip().strip("/")
    except (OSError, ValueError):
        pass
    return DEFAULT_LEDGER


def check_attr(root, paths):
    """{path: merge value or 'unspecified'} via one git check-attr call; None when git or the
    work tree is unavailable."""
    if not paths:
        return {}
    try:
        p = subprocess.run(["git", "-C", root, "check-attr", "merge", "--"] + paths,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    seen = {}
    for line in p.stdout.splitlines():
        # <path>: merge: <value>
        head, sep, value = line.rpartition(": ")
        path, sep2, attr = head.rpartition(": ")
        if sep and sep2 and attr == "merge":
            seen[path] = value.strip()
    return seen


def expected_rows(root):
    rows = [(ledger_rel(root), "union")] + [(p, "union") for p in UNION_FIXED]
    for p in DERIVED:
        if os.path.isfile(os.path.join(root, p)):
            rows.append((p, "binary"))
    ns = os.path.join(root, DERIVED_GLOB_DIR)
    if os.path.isdir(ns):
        for name in sorted(os.listdir(ns)):
            if name.endswith(".html"):
                rows.append((DERIVED_GLOB_DIR + "/" + name, "binary"))
    return rows


def lint_rows(root):
    findings = []
    path = os.path.join(root, ledger_rel(root))
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return findings
    if not data:
        return findings
    text = data.decode("utf-8", "replace")
    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            findings.append(("LEDGER-ROW-MALFORMED", str(n), "not one JSON value on one line"))
            continue
        if not isinstance(obj, dict):
            findings.append(("LEDGER-ROW-MALFORMED", str(n), "a row is one JSON object, not %s" % type(obj).__name__))
    if not text.endswith("\n"):
        findings.append(("LEDGER-ROW-MALFORMED", "eof", "the file does not end with a newline (a union merge would splice the last row)"))
    return findings


def main(argv):
    root = None
    quiet = "--quiet" in argv
    if "--root" in argv:
        i = argv.index("--root")
        root = argv[i + 1] if i + 1 < len(argv) else None
    if root is None:
        try:
            from hyp_config import resolve_root
            root = resolve_root(None)
        except Exception:
            root = os.getcwd()
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print("merge-attrs-check: %s is not a directory" % root)
        return 0
    findings = []
    rows = expected_rows(root)
    seen = check_attr(root, [p for p, _ in rows])
    if seen is not None:
        for path, want in rows:
            got = seen.get(path, "unspecified")
            if got != want:
                findings.append(("MERGE-ATTR-MISSING", path, want, got))
    findings.extend(lint_rows(root))
    for f in findings:
        print("\t".join(f))
    if findings or not quiet:
        print("merge-attrs-check: %d finding(s)%s" % (len(findings), "" if seen is not None else " (not a git work tree: merge shapes not checked)"))
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - advisory: never a traceback, never non-zero on our own error
        print("merge-attrs-check: skipped (%s)" % type(exc).__name__)
        sys.exit(0)
