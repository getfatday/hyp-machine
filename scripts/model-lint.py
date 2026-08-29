#!/usr/bin/env python3
"""model-lint.py — mechanical self-lint for an operating-model/<context>/ scaffold.

Usage:
    model-lint.py <path-to-operating-model-context-dir>

Ships per SCHEMA.md's own deferral clause ("a lint.py is warranted once there are
enough nodes for hand-checking to fail — not before"): H-151's gate measured
hand-checking failing four distinct ways in four runs (unparseable frontmatter,
missing handler:, missing terminal-policy debt marker, dangling links). The adopt
skill's self-lint step runs this before ratifying; every ERROR must be fixed.

ERROR (exit 1) — classes SCHEMA states as invalid or that have failed graded runs:
  E-PARSE      frontmatter does not parse as YAML (quote scalars containing ": ")
  E-COMMON     missing/malformed common key (id/type/context/summary/status),
               id slug != filename, unknown type or status
  E-HANDLER    command missing handler:
  E-CMD-KEYS   command missing issued-by:/executor:
  E-EVENT-REP  event missing representation: ("or it doesn't exist")
  E-POLICY     policy with neither resolvable then: nor status: debt
  E-HOOK-MECH  enforcement: hook without a mechanism: block
  E-RM-MAINT   read-model missing maintainer: ("must name its maintainer or it rots")
  E-LINK       reads/emits/then/trigger/emitted-by/consumed-by/projects-from/
               issued-by/cast-as/invoked-on entry resolves to no node file
  E-LINK-TYPE  reference target type violates SCHEMA's stated contract
               (trigger→event, then→command, emits→event, projects-from→event,
               issued-by→actor|policy, cast-as→actor, invoked-on→actor|external,
               maintainer→command)
  E-CATALOG    node has no row in model.md

WARN (printed, exit unaffected) — block-completeness beyond the measured classes:
  W-CMD-KEYS   command missing freedom:/reads:/emits:
  W-SUMMARY    summary longer than 140 chars
  W-LINK-TYPE  reference target type outside the block's usual set for looser
               keys (reads→read-model, emitted-by→command,
               consumed-by→policy|actor|command) — observed in otherwise-clean
               models, so advisory only
"""
import os
import re
import sys

TYPE_DIRS = {
    "actor": ("actors", "actor"),
    "command": ("commands", "command"),
    "event": ("events", "event"),
    "policy": ("policies", "policy"),
    # house convention (lab model + every graded scaffold) is hyphenless
    "read-model": ("readmodels", "read-models", "read-model"),
    "external": ("externals", "external"),
    "aggregate": ("aggregates", "aggregate"),
}
STATUSES = {"current", "hotspot", "debt"}
LINK_KEYS = ("reads", "emits", "then", "trigger", "emitted-by", "consumed-by",
             "projects-from", "issued-by", "cast-as", "invoked-on", "maintainer")
NON_NODE_LITERALS = {"human", "agent", "either", "manual"}
# SCHEMA-verbatim single-set target contracts (ERROR when violated); measured
# pure across every graded scaffold except the known defects
LINK_TYPES_HARD = {
    "trigger": {"event"},
    "then": {"command"},
    "emits": {"event"},
    "projects-from": {"event"},
    "issued-by": {"actor", "policy"},
    "cast-as": {"actor"},
    "invoked-on": {"actor", "external"},
    "maintainer": {"command"},
}
# block-usual sets with observed clean-model exceptions (WARN when outside)
LINK_TYPES_SOFT = {
    "reads": {"read-model", "external", "aggregate"},
    "emitted-by": {"command"},
    "consumed-by": {"policy", "actor", "command"},
}


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    return m.group(1) if m else None


def parse_yaml(block, findings, rel):
    try:
        import yaml
    except ImportError:
        findings.append(("WARN", "W-NOYAML", rel, "pyyaml unavailable; parse check skipped"))
        return None
    try:
        data = yaml.safe_load(block)
    except Exception as e:
        findings.append(("ERROR", "E-PARSE", rel,
                         "frontmatter does not parse: %s" % str(e).split("\n")[0]))
        return None
    return data if isinstance(data, dict) else None


def as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        return 2
    root = os.path.abspath(sys.argv[1])
    findings = []
    node_ids = set()
    nodes = []

    node_dirs = {d for pair in TYPE_DIRS.values() for d in pair}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        # nodes live one-per-file under the type directories; context-root
        # files (model.md, GLOSSARY.md, ...) are auxiliary, not nodes
        if os.path.relpath(dirpath, root).split(os.sep)[0] not in node_dirs:
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            text = open(p, encoding="utf-8", errors="replace").read()
            block = frontmatter(text)
            if block is None:
                findings.append(("ERROR", "E-COMMON", rel, "no frontmatter block"))
                continue
            data = parse_yaml(block, findings, rel)
            if data is None:
                continue
            nodes.append((rel, fn, data))
            if isinstance(data.get("id"), str):
                node_ids.add(data["id"])

    for rel, fn, data in nodes:
        nid = data.get("id")
        ntype = data.get("type")
        for k in ("id", "type", "context", "summary", "status"):
            if not data.get(k):
                findings.append(("ERROR", "E-COMMON", rel, "missing common key %s:" % k))
        if isinstance(ntype, str) and ntype not in TYPE_DIRS:
            findings.append(("ERROR", "E-COMMON", rel, "unknown type: %s" % ntype))
        if isinstance(data.get("status"), str) and data["status"] not in STATUSES:
            findings.append(("ERROR", "E-COMMON", rel, "unknown status: %s" % data["status"]))
        if isinstance(data.get("summary"), str) and len(data["summary"]) > 140:
            findings.append(("WARN", "W-SUMMARY", rel, "summary > 140 chars"))
        if isinstance(nid, str) and "/" in nid:
            slug = nid.split("/", 1)[1]
            if slug != fn[:-3]:
                findings.append(("ERROR", "E-COMMON", rel,
                                 "id slug %r != filename %r" % (slug, fn[:-3])))

        if ntype == "command":
            if not data.get("handler"):
                findings.append(("ERROR", "E-HANDLER", rel, "command missing handler:"))
            for k in ("issued-by", "executor"):
                if not data.get(k):
                    findings.append(("ERROR", "E-CMD-KEYS", rel, "command missing %s:" % k))
            for k in ("freedom", "reads", "emits"):
                if k not in data:
                    findings.append(("WARN", "W-CMD-KEYS", rel, "command missing %s:" % k))
        elif ntype == "event":
            if not data.get("representation"):
                findings.append(("ERROR", "E-EVENT-REP", rel, "event missing representation:"))
        elif ntype == "policy":
            then = data.get("then")
            resolvable_then = bool(as_list(then))
            if not resolvable_then and data.get("status") != "debt":
                findings.append(("ERROR", "E-POLICY", rel,
                                 "neither resolvable then: nor status: debt (empty then: "
                                 "requires the debt marker + reason)"))
            if data.get("enforcement") == "hook" and not data.get("mechanism"):
                findings.append(("ERROR", "E-HOOK-MECH", rel,
                                 "enforcement: hook without mechanism:"))
        elif ntype == "read-model":
            if not data.get("maintainer"):
                findings.append(("ERROR", "E-RM-MAINT", rel, "read-model missing maintainer:"))

        for k in LINK_KEYS:
            for ref in as_list(data.get(k)):
                ref = ref.strip()
                if ref in NON_NODE_LITERALS or "/" not in ref:
                    continue
                rtype, rslug = ref.split("/", 1)
                if rtype not in TYPE_DIRS:
                    continue  # e.g. script/<path> handlers, file(...) forms
                if k in LINK_TYPES_HARD and rtype not in LINK_TYPES_HARD[k]:
                    findings.append(("ERROR", "E-LINK-TYPE", rel,
                                     "%s: %r targets a %s — SCHEMA requires %s"
                                     % (k, ref, rtype,
                                        "|".join(sorted(LINK_TYPES_HARD[k])))))
                elif k in LINK_TYPES_SOFT and rtype not in LINK_TYPES_SOFT[k]:
                    findings.append(("WARN", "W-LINK-TYPE", rel,
                                     "%s: %r targets a %s (usual: %s)"
                                     % (k, ref, rtype,
                                        "|".join(sorted(LINK_TYPES_SOFT[k])))))
                if not (any(os.path.isfile(os.path.join(root, d, rslug + ".md"))
                            for d in TYPE_DIRS[rtype])
                        or ref in node_ids):
                    findings.append(("ERROR", "E-LINK", rel,
                                     "%s: %r resolves to no node file" % (k, ref)))

    catalog_path = os.path.join(root, "model.md")
    catalog = open(catalog_path, encoding="utf-8", errors="replace").read() \
        if os.path.isfile(catalog_path) else ""
    if not catalog:
        findings.append(("ERROR", "E-CATALOG", "model.md", "missing catalog"))
    else:
        for rel, fn, data in nodes:
            nid = data.get("id")
            if isinstance(nid, str) and nid not in catalog and fn[:-3] not in catalog:
                findings.append(("ERROR", "E-CATALOG", rel, "no row in model.md"))

    errors = 0
    for sev, cls, rel, detail in findings:
        if sev == "ERROR":
            errors += 1
        print("%s %s %s: %s" % (sev, cls, rel, detail))
    print("model-lint: %d node(s), %d error(s), %d warning(s)"
          % (len(nodes), errors, len(findings) - errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
