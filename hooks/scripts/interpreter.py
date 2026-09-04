#!/usr/bin/env python3
"""Generic PreToolUse policy interpreter (hyp) — deny + advisory paths.

Reads the consumer repository's operating-model policy nodes as data
(operating-model/policies/*.md and operating-model/*/policies/*.md): the
node file IS the configuration; enforcement changes are node-file edits,
never code changes.

Rule table:

  - enforcement: hook (exact) + mechanism block WITHOUT action: -> deny
    path (rule 3): exit 2, stderr reason.
  - action: advise (any enforcement value) OR enforcement: advisory + block
    with action absent -> advise path (rule 4): on match, collect one
    line "ADVISORY <node-id>: <message>".
  - The advise path never affects the exit code (rule 5 — the
    no-hard-block honesty guarantee): all matches collected, printed to
    stdout sorted by node id, deduplicated; exit stays 0 unless a deny-path
    node (rule 3) matched.
  - action: deny (explicit) is honored only when enforcement: hook backs it,
    then behaves exactly like rule 3 (rule 6).
    The key exists so a future edit can flip a node advisory -> deny by
    editing the node file alone. A
    node that asks for action: deny without enforcement: hook is a
    self-contradictory honesty-rule violation (an advisory may never
    escalate itself to a guaranteed block via a stray key) — warn-and-skip,
    not a silent block and not a silent downgrade.
  - Malformed mechanism content — scalar mechanism (rule 2), non-list
    deny-tools/deny-paths/exclude-paths, a non-scalar
    message, or an unknown action value (rule 7) — warn-and-skip on
    stderr, never crash: a crashing
    PreToolUse hook blocks every tool call.
  - Bad stdin JSON -> exit 1 (rule 8, unchanged).

Only PreToolUse is interpreted (mechanism `event:`, default when absent);
other event values are silently ignored — the key reserves the extension
point (Stop/SessionStart policies).
"""
import fnmatch
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import resolve_root  # shared, worktree-aware (one resolver for every hook)


def parse_node(path):
    """Minimal YAML-frontmatter parser for policy nodes (no dependencies).

    Supports scalar keys, a nested `mechanism:` block, and lists in either
    flow style ([a, b]) or block style (- a / - b). The mechanism keys
    (event, action, message, exclude-paths) are ordinary scalar/list
    sub-keys this parser handles generically.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    node = {}
    mech = {}
    in_mech = False
    cur_list_key = None

    def parse_flow_list(v):
        return [x.strip().strip("\"'") for x in v.strip()[1:-1].split(",") if x.strip()]

    for raw in text[3:end].splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if indent == 0:
            in_mech = False
            cur_list_key = None
            if ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip()
                if k == "mechanism" and v == "":
                    in_mech = True
                    node["mechanism"] = mech
                else:
                    node[k] = v.strip("\"'")
        elif in_mech:
            if line.startswith("- ") and cur_list_key is not None:
                mech.setdefault(cur_list_key, []).append(line[2:].strip().strip("\"'"))
            elif ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip()
                if v == "":
                    cur_list_key = k
                    mech[k] = []
                elif v.startswith("["):
                    mech[k] = parse_flow_list(v)
                    cur_list_key = None
                else:
                    mech[k] = v.strip("\"'")
                    cur_list_key = None
    return node


def norm(p):
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def path_matches(fpath, patterns, root):
    candidates = {norm(fpath)}
    if os.path.isabs(fpath):
        rel = os.path.relpath(fpath, root)
        if not rel.startswith(".."):
            candidates.add(norm(rel))
    return any(fnmatch.fnmatch(c, norm(pat)) for c in candidates for pat in patterns)


def policy_files(root):
    base = os.path.join(root, "operating-model")
    out = []
    for pattern in ("policies/*.md", "*/policies/*.md"):
        out.extend(glob.glob(os.path.join(base, pattern)))
    return sorted(out)


def _as_list_or_none(mech, key):
    """(value, ok). ok is False iff the key is present and not a list."""
    v = mech.get(key)
    if v is None:
        return [], True
    if not isinstance(v, list):
        return None, False
    return v, True


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(1)
    root = resolve_root(payload)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    fpath = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("notebook_path")
    )

    advisories = {}  # node-id -> message ; dict dedupes by id, sorted at emit time

    for pf in policy_files(root):
        node = parse_node(pf)
        if not node:
            continue
        name = node.get("id", os.path.basename(pf))
        enforcement = node.get("enforcement")
        mech = node.get("mechanism") or {}

        # Rule 2: scalar/non-block mechanism -> warn, never crash. Reached
        # for every node because rule 4 needs to inspect the mechanism
        # block for action: advise under ANY enforcement value.
        if not isinstance(mech, dict):
            sys.stderr.write(
                "policy %s: mechanism is not a block (got scalar); skipping\n" % name)
            continue

        # v1: only PreToolUse is interpreted; other event values are
        # silently ignored (the key reserves the extension point).
        if mech.get("event", "PreToolUse") != "PreToolUse":
            continue

        deny_tools, ok1 = _as_list_or_none(mech, "deny-tools")
        deny_paths, ok2 = _as_list_or_none(mech, "deny-paths")
        exclude_paths, ok3 = _as_list_or_none(mech, "exclude-paths")
        if not (ok1 and ok2 and ok3):
            # Rule 7: non-list
            # deny-tools/deny-paths/exclude-paths cannot be interpreted.
            sys.stderr.write(
                "policy %s: deny-tools/deny-paths/exclude-paths must be a "
                "list; skipping\n" % name)
            continue

        message = mech.get("message")
        if message is not None and not isinstance(message, str):
            sys.stderr.write("policy %s: message is not a scalar; skipping\n" % name)
            continue

        action = mech.get("action")
        if action is not None and action not in ("advise", "deny"):
            sys.stderr.write(
                "policy %s: unknown action %r; skipping\n" % (name, action))
            continue

        # Route to deny (rule 3 + rule 6), advise (rule 4),
        # or nothing (silent skip: no action, and neither
        # enforcement: hook nor enforcement: advisory backs a decision).
        if action == "deny":
            if enforcement != "hook":
                # Rule 6's honesty gate: action: deny is only ever honored
                # when enforcement: hook backs it. A node that asks for
                # action: deny under any other enforcement value is
                # self-contradictory (advisory nodes may never escalate
                # themselves to a guaranteed block via a stray key) --
                # warn-and-skip rather than silently blocking or silently
                # downgrading to advisory.
                sys.stderr.write(
                    "policy %s: action: deny without enforcement: hook; "
                    "skipping\n" % name)
                continue
            route = "deny"
        elif action == "advise":
            route = "advise"       # rule 4, clause 1: any enforcement value
        elif enforcement == "hook":
            route = "deny"         # rule 3: the hook default
        elif enforcement == "advisory":
            route = "advise"       # rule 4, clause 2: enforcement: advisory
        else:
            continue                # unchanged: nothing to enforce here

        if tool not in deny_tools:
            continue
        if deny_paths and not (fpath and path_matches(fpath, deny_paths, root)):
            continue
        if fpath and exclude_paths and path_matches(fpath, exclude_paths, root):
            continue

        if route == "deny":
            if not deny_paths:
                sys.stderr.write("denied by %s: tool %s is denied\n" % (name, tool))
            else:
                sys.stderr.write("denied by %s: %s on %s\n" % (name, tool, fpath))
            sys.exit(2)

        # route == "advise": rule 5 — never affects exit code; collected
        # here, emitted sorted + deduplicated after the full node scan.
        advisories[name] = message or node.get("summary") or ""

    for name in sorted(advisories):
        print("ADVISORY %s: %s" % (name, advisories[name]))
    sys.exit(0)


if __name__ == "__main__":
    main()
