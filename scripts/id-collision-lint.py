#!/usr/bin/env python3
"""id-collision-lint.py -- report hypothesis-spec and journal-fragment ids that more than one file carries.

Reads the repository at <root> (default .) and nothing else: no git, no network, no LLM, no writes.
Directories come from .claude/hyp.json (hypotheses_dir, journal_dir) when present, else the default
layout (hypotheses/, experiments/journal-fragments/), the same rule scripts/id-rectify.py applies (P1).

  spec id      the integer N in <hypotheses_dir>/H-N-<slug>.md (3-4 digits; H-DRAFT-* and TEMPLATE are not ids)
  fragment id  the integer prefix N in <journal_dir>/N-<slug>.md (any width: 0068- and 68- are both 68)
  mismatch     a fragment whose `id:` frontmatter line names a different integer than its filename prefix

Output, one tab-separated line per finding, sorted by class then id:
  ID-COLLISION<TAB>spec<TAB><id><TAB><count><TAB><path1><TAB><path2>
  ID-COLLISION<TAB>fragment<TAB><id><TAB><count><TAB><path1><TAB><path2>
  ID-MISMATCH<TAB>fragment<TAB><prefix-id><TAB><id-line><TAB><path>
Paths are repository-relative; only the first two (filename order) are printed per collision.
--summary prints one line instead: SUMMARY<TAB>spec=<n>[TAB]fragment=<n>[TAB]mismatch=<n>.
Exit 0 when nothing is found, 1 when anything is, 2 on usage / unreadable root.

Why this exists (lab lane H-DRAFT-6ec1691d, consumer gap G4): on one consumer at b7c77473, 57 numeric spec
ids were shared by more than one file and 131 of 344 fragment ids were duplicated (one id 15 times) while
the plugin's land gate (id-rectify.py) only inspects the base->head delta and no session-start line says so.
"""
import json
import os
import re
import sys

DEFAULTS = {"hypotheses_dir": "hypotheses", "journal_dir": "experiments/journal-fragments"}
SPEC_RE = re.compile(r"^H-(\d{3,4})-.+\.md$")
FRAG_RE = re.compile(r"^(\d+)-.+\.md$")
ID_LINE_RE = re.compile(r"^id:\s*(\d+)\s*$", re.M)


def load_dirs(root):
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg
    for key in DEFAULTS:
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            cfg[key] = val.strip().strip("/")
    return cfg


def listing(root, rel):
    path = os.path.join(root, rel)
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    return [n for n in names if os.path.isfile(os.path.join(path, n))]


def frontmatter_id(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    end = head.find("\n---", 3)
    block = head[3:end] if end != -1 else head[3:]
    m = ID_LINE_RE.search(block)
    return int(m.group(1)) if m else None


def collect(root):
    dirs = load_dirs(root)
    specs, frags, mismatches = {}, {}, []
    for name in listing(root, dirs["hypotheses_dir"]):
        m = SPEC_RE.match(name)
        if m:
            specs.setdefault(int(m.group(1)), []).append(dirs["hypotheses_dir"] + "/" + name)
    for name in listing(root, dirs["journal_dir"]):
        m = FRAG_RE.match(name)
        if not m:
            continue
        rel = dirs["journal_dir"] + "/" + name
        pid = int(m.group(1))
        frags.setdefault(pid, []).append(rel)
        lid = frontmatter_id(os.path.join(root, rel))
        if lid is not None and lid != pid:
            mismatches.append((pid, lid, rel))
    return specs, frags, mismatches


def findings(specs, frags, mismatches):
    out = []
    for kind, table in (("spec", specs), ("fragment", frags)):
        for i in sorted(table):
            paths = table[i]
            if len(paths) > 1:
                out.append("ID-COLLISION\t%s\t%d\t%d\t%s\t%s" % (kind, i, len(paths), paths[0], paths[1]))
    for pid, lid, rel in sorted(mismatches):
        out.append("ID-MISMATCH\tfragment\t%d\t%d\t%s" % (pid, lid, rel))
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    summary = "--summary" in argv
    if len(args) > 1 or any(a.startswith("--") and a != "--summary" for a in argv):
        print(__doc__.strip().splitlines()[0])
        print("usage: id-collision-lint.py [<root>] [--summary]")
        return 2
    root = args[0] if args else "."
    if not os.path.isdir(root):
        print("id-collision-lint: not a directory: %s" % root)
        return 2
    specs, frags, mismatches = collect(root)
    lines = findings(specs, frags, mismatches)
    if summary:
        ns = sum(1 for l in lines if l.startswith("ID-COLLISION\tspec\t"))
        nf = sum(1 for l in lines if l.startswith("ID-COLLISION\tfragment\t"))
        nm = sum(1 for l in lines if l.startswith("ID-MISMATCH\t"))
        print("SUMMARY\tspec=%d\tfragment=%d\tmismatch=%d" % (ns, nf, nm))
    else:
        for l in lines:
            print(l)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
