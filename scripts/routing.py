#!/usr/bin/env python3
# agent tier: build / sonnet / high
"""
scripts/routing.py -- the CLI over routing_lib. Verbs: table, resolve <role>,
lint <script>, rewrite <script> --out <dir>.
"""
import argparse
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_THIS_DIR), "hooks", "scripts"))
import routing_lib as rl  # noqa: E402

DEFAULT_TABLE_RELPATH = os.path.join("rules", "routing-default.json")
OVERRIDE_TABLE_RELPATH = os.path.join(".claude", "routing.json")


def _plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(_THIS_DIR))


def load_effective_table(root):
    with open(os.path.join(_plugin_root(), DEFAULT_TABLE_RELPATH)) as f:
        default_obj = json.load(f)
    override_obj = {}
    override_path = os.path.join(root, OVERRIDE_TABLE_RELPATH)
    if os.path.isfile(override_path):
        with open(override_path) as f:
            override_obj = json.load(f)
    return rl.merge_table(default_obj, override_obj)


def cmd_table(args):
    table = load_effective_table(args.root)
    for role, cls in sorted(table["roles"].items()):
        row = table["classes"].get(cls, {})
        print("%-16s %-12s %-8s %-6s %s" % (role, cls, row.get("model"), row.get("effort"), row.get("basis")))
    return 0


def cmd_resolve(args):
    table = load_effective_table(args.root)
    cls = rl.role_class(table, args.role)
    if cls is None:
        sys.stderr.write("routing.py: unknown role %r\n" % (args.role,))
        return 1
    row = table["classes"][cls]
    print(json.dumps({"model": row["model"], "effort": row["effort"], "agentType": "hyp:" + args.role}))
    return 0


def cmd_lint(args):
    table = load_effective_table(args.root)
    with open(args.script) as f:
        text = f.read()
    try:
        findings, _calls = rl.scan_script(text, table)
    except rl.ParseError as e:
        findings = [{"class": "cannot-parse", "line": rl.line_of(text, e.offset), "detail": str(e)}]
    for f in findings:
        print("%s:%s %s -- %s" % (args.script, f["line"], f["class"], f.get("detail", "")))
    return 1 if findings else 0


def cmd_rewrite(args):
    """Write a table-conformant copy of `script`, touching only routing option
    values (model, effort, agentType, meta.phases[].model) for calls whose label
    head is in the roles map -- never label text, never an unmapped/no-label call."""
    table = load_effective_table(args.root)
    with open(args.script) as f:
        text = f.read()
    try:
        findings, calls = rl.scan_script(text, table)
    except rl.ParseError:
        sys.stderr.write("routing.py rewrite: cannot parse %s\n" % (args.script,))
        return 1

    out_text = text
    # Rewrite from the end backwards so earlier offsets remain valid; recompute
    # per-call spans by re-extracting (simpler and safer than tracking offsets
    # through this best-effort scanner).
    raw_calls = rl.extract_agent_calls(text)
    for call in sorted(raw_calls, key=lambda c: -c["open_idx"]):
        arg_text = out_text[call["open_idx"] + 1:call["close_idx"]]
        opts, has_spread = rl.parse_call_options(arg_text)
        label_kind, label_val = opts["label"]
        if label_kind != "literal" or not label_val:
            continue  # no-label call: never rewritten (A3)
        head = rl.label_head(label_val)
        cls = rl.role_class(table, head)
        if cls is None:
            continue  # unmapped head: never rewritten (A3)
        row = table["classes"][cls]
        # Only the options-object argument is touched -- never a leading prompt
        # string argument (agent(promptTemplateLiteral, {...}) is a real
        # convention in this corpus; a naive whole-call_text.find('{') landed
        # inside a `${...}` template interpolation and corrupted the script).
        opts_offset = rl.find_options_object_offset(arg_text)
        head_text = arg_text[:opts_offset]
        opts_text = arg_text[opts_offset:]
        import re as _re
        to_insert = []
        for key, new_val in (("model", row["model"]), ("effort", row["effort"]), ("agentType", "hyp:" + head)):
            kind, _old = opts[key]
            if kind == "literal":
                opts_text = _re.sub(
                    r"(\b%s\s*:\s*)(['\"])[^'\"]*\2" % key,
                    lambda m: "%s%s%s%s" % (m.group(1), m.group(2), new_val, m.group(2)),
                    opts_text, count=1)
            elif kind == "missing":
                # option absent entirely: insert it (the only way to make a
                # model-less/effort-less call table-conformant); a spread,
                # template or variable value is left untouched -- that call
                # stays a non-literal finding, never silently "fixed".
                to_insert.append("%s: '%s'" % (key, new_val))
        if to_insert:
            # Round-2 finding 3: insert immediately after the brace, matching
            # whatever spacing the brace already carries -- the previous
            # unconditional " " here turned a source `{label:` (no space) into
            # `{ label:` after insertion, which diff_confined's whitelist strip
            # (grade.py) does not strip, so 51/437 rewrites failed A3.
            brace = opts_text.find("{")
            if brace != -1:
                opts_text = opts_text[:brace + 1] + ", ".join(to_insert) + ", " + opts_text[brace + 1:]
        new_arg = head_text + opts_text
        out_text = out_text[:call["open_idx"] + 1] + new_arg + out_text[call["close_idx"]:]

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        out_path = os.path.join(args.out, os.path.basename(args.script))
        with open(out_path, "w") as f:
            f.write(out_text)
        print(out_path)
    else:
        sys.stdout.write(out_text)
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=os.getcwd())
    sub = p.add_subparsers(dest="verb", required=True)
    sub.add_parser("table")
    rp = sub.add_parser("resolve")
    rp.add_argument("role")
    lp = sub.add_parser("lint")
    lp.add_argument("script")
    wp = sub.add_parser("rewrite")
    wp.add_argument("script")
    wp.add_argument("--out", default=None)
    args = p.parse_args()
    return {"table": cmd_table, "resolve": cmd_resolve, "lint": cmd_lint, "rewrite": cmd_rewrite}[args.verb](args)


if __name__ == "__main__":
    sys.exit(main())
