#!/usr/bin/env python3
# agent tier: build / sonnet / high
"""scripts/compile-routing-agents.py -- compiles one agents/hyp-<role>.md per role in the
routing table's `roles` map: YAML frontmatter naming `model` (the class's model, literal)
plus a one-line body. Deterministic and pure: same table bytes in, same agent-definition
bytes out, always -- no clock, no environment, no network.

Ported by intent from the lab keep H-DRAFT-75b03e6e-routing-determinism's fixture ON bytes
(fixture/impl/scripts/compile_routing_agents.py, VERDICT.json evidence-sufficient promote,
five counted looks 5/5, llr 2.9389 >= 2.8904): that fixture compiled only role `build`
against a copy of the table installed at project scope in a throwaway consumer, because a
project-scope PLUGIN install never resolved the plugin-qualified agent id `hyp:build` in a
headless child (VERIFY.md finding 2, AMENDMENTS.md 4). This script generalizes the same
render function to every role in the table and ships the output as agents/hyp-<role>.md
INSIDE the plugin -- the plugin-qualified id `hyp:<role>` this surface would resolve to
under a released-plugin install is unmeasured by the lane; the project-scope surface it did
measure (a consumer's own `.claude/agents/hyp-<role>.md`, `agentType: 'hyp-<role>'` with no
colon) is what `--emit` reproduces. See docs/model-routing.md, "The compiled agent surface".

Three modes, one table in:
  (no flag)      compile every role (or --role ROLE, repeatable) into this plugin's own
                 agents/ directory (the files this repository commits) -- the render is
                 idempotent, so running it twice reproduces byte-identical output.
  --check        compare the table's expected bytes against agents/ (or --emit's directory,
                 if given) WITHOUT writing anything; print one line per file that is missing
                 or disagrees, exit 1 if any, 0 if every file matches (or none is wanted).
  --emit DIR     write into DIR instead of the plugin's own agents/ -- the project-scope
                 install path the lane measured (e.g. a consumer's `.claude/agents/`).

Usage:
  python3 -B scripts/compile-routing-agents.py rules/routing-default.json [--role ROLE ...]
  python3 -B scripts/compile-routing-agents.py rules/routing-default.json --check [--role ROLE ...]
  python3 -B scripts/compile-routing-agents.py rules/routing-default.json --emit <dir> [--role ROLE ...]
"""
import argparse
import hashlib
import json
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(THIS_DIR)
DEFAULT_AGENTS_DIR = os.path.join(PLUGIN_ROOT, "agents")


def render_agent_md(role, cls, row):
    """Byte-for-byte the lab keep's render function (fixture/impl/scripts/
    compile_routing_agents.py `render_agent_md`), with this script's own basename in the
    body sentence -- the only intentional byte the port changes."""
    model = row.get("model")
    effort = row.get("effort")
    lines = [
        "---",
        "name: hyp-%s" % role,
        "description: Compiled routing agent for role %s (class %s, model %s, effort %s)." % (role, cls, model, effort),
        "model: %s" % model,
        "role: %s" % role,
        "class: %s" % cls,
        "---",
        "",
        "# hyp:%s" % role,
        "",
        "Compiled by scripts/compile-routing-agents.py from the routing table's `%s` class row "
        "(model %s, effort %s). Do not hand-edit; recompile from the table." % (cls, model, effort),
        "",
    ]
    return "\n".join(lines)


def load_table(table_path):
    with open(table_path) as f:
        table = json.load(f)
    if table.get("schema") != "routing/v1":
        sys.exit("REFUSE: %s is not schema routing/v1" % table_path)
    return table


def wanted_roles(table, roles_arg):
    roles = table["roles"]
    wanted = roles_arg or sorted(roles.keys())
    for role in wanted:
        if role not in roles:
            sys.exit("REFUSE: role %r not in table's roles map" % role)
    return sorted(wanted)


def compile_rows(table, roles):
    rows = []
    for role in roles:
        cls = table["roles"][role]
        row = table["classes"][cls]
        text = render_agent_md(role, cls, row)
        rows.append({
            "role": role, "class": cls, "model": row.get("model"), "effort": row.get("effort"),
            "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    return rows


def cmd_emit(table, roles, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for row in compile_rows(table, roles):
        path = os.path.join(out_dir, "hyp-%s.md" % row["role"])
        with open(path, "w") as f:
            f.write(row["text"])
        print(json.dumps({"role": row["role"], "class": row["class"], "model": row["model"],
                          "path": path, "sha256": row["sha256"]}))
    return 0


def cmd_check(table, roles, check_dir):
    problems = []
    for row in compile_rows(table, roles):
        path = os.path.join(check_dir, "hyp-%s.md" % row["role"])
        if not os.path.isfile(path):
            problems.append("missing:%s" % path)
            continue
        with open(path) as f:
            actual = f.read()
        if actual != row["text"]:
            problems.append("disagrees:%s" % path)
    for p in problems:
        print(p)
    return 1 if problems else 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("table", help="path to a routing/v1 table json")
    ap.add_argument("--role", action="append", default=None,
                    help="repeatable; default is every role in the table's roles map")
    ap.add_argument("--check", action="store_true",
                    help="compare, write nothing; exit 1 if any file is missing or disagrees")
    ap.add_argument("--emit", default=None,
                    help="write (or check, with --check) into this directory instead of the "
                         "plugin's own agents/ -- the project-scope install path "
                         "(e.g. a consumer's .claude/agents/)")
    a = ap.parse_args(argv)

    table = load_table(a.table)
    roles = wanted_roles(table, a.role)
    out_dir = a.emit or DEFAULT_AGENTS_DIR

    if a.check:
        return cmd_check(table, roles, out_dir)
    return cmd_emit(table, roles, out_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
