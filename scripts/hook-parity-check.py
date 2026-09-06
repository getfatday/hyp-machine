#!/usr/bin/env python3
"""Hook-wiring parity: the guards that run on only one side of the lab-plugin boundary.

Script bytes and dashboard features are parity-checked between the lab and the
plugin, but hook WIRING was not, and it drifted silently. This lint normalizes two
hook registries — a project's `.claude/settings.json` and a plugin's
`hooks/hooks.json` — into (event, matcher, guard) rows and prints one line per row
present on exactly one side.

Normalization: guard = basename of the first `.py` or `.sh` token in the command
(tokens are runs of non-whitespace, non-quote characters; trailing shell punctuation
is ignored), else the first 40 characters of the command. A group with no matcher
is `*`. Duplicate rows on one side collapse (a set).

    python3 scripts/hook-parity-check.py .claude/settings.json hooks/hooks.json

Output: HOOK-PARITY<TAB>lab-only|plugin-only<TAB>event<TAB>matcher<TAB>guard, sorted;
sides are named by argument order (first = lab, second = plugin). Silent when both
registries carry the same rows. Exit 0 whenever both inputs parse (advisory: the
harden-check block counts the lines); exit 2 on an unreadable input.
"""
import json
import os
import re
import sys

TOKEN = re.compile(r"[^\s\"']+")
TRAILING = ");|&"
PREFIX_LEN = 40
SIDES = ("lab-only", "plugin-only")


def load_registry(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    hooks = data.get("hooks") if isinstance(data, dict) else None
    return hooks if isinstance(hooks, dict) else {}


def guard_name(command):
    for tok in TOKEN.findall(command):
        tok = tok.rstrip(TRAILING)
        if tok.endswith(".py") or tok.endswith(".sh"):
            return os.path.basename(tok)
    return command[:PREFIX_LEN]


def rows(registry):
    out = set()
    for event, groups in registry.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher") or "*"
            for hook in group.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                if hook.get("type", "command") != "command":
                    continue
                command = hook.get("command")
                if not isinstance(command, str):
                    continue
                out.add((event, matcher, guard_name(command)))
    return out


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: hook-parity-check.py <settings.json> <hooks.json>\n")
        return 2
    try:
        lab = rows(load_registry(argv[1]))
        plugin = rows(load_registry(argv[2]))
    except (OSError, ValueError) as exc:
        sys.stderr.write("hook-parity-check: unreadable input: %s\n" % exc)
        return 2
    lines = [(row, SIDES[0]) for row in lab - plugin]
    lines += [(row, SIDES[1]) for row in plugin - lab]
    for (event, matcher, guard), side in sorted(lines):
        print("HOOK-PARITY\t%s\t%s\t%s\t%s" % (side, event, matcher, guard))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
