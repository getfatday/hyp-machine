#!/usr/bin/env python3
"""incident-anchors-lint.py -- every claim line carries a checkable anchor (H-252).

A claim line is any line starting with "- " in COLLECTED.md (or a sections/*.md
file). It must END with exactly one anchor:

  [anchor: path=<estate-rel> mtime=<YYYY-MM-DDTHH:MM:SSZ>]
  [anchor: cmd="<what ran>" ts=<YYYY-MM-DDTHH:MM:SSZ> out=<incident-rel>]

Checkability, not just grammar:
  path anchors: the path must exist in the estate and the mtime must MATCH --
    against --mtimes <tsv> (rel<TAB>epoch<TAB>isoZ, the frozen record) when
    given, else against a live stat under --root.
  cmd anchors: the out= file must exist under the incident dir.

Usage:
  incident-anchors-lint.py --collected <COLLECTED.md> --incident <dir>
      (--mtimes <mtimes.tsv> | --root <estate-root>)

Exit 0 all claims anchored+checkable; 1 findings (printed, sorted); 2 usage.
Stdlib only; deterministic output (no timestamps).

Landed from the KEPT reference implementation (H-252 reflex-collector-fidelity,
2x5/5 2026-09-02) experiments/runs/H-252/fixture/bin/incident-anchors-lint.py —
anchor grammar and checkability logic unchanged; only this provenance note added.
Joins the lint estate over experiments/incidents/INC-*/COLLECTED.md records.
"""

import argparse
import datetime
import os
import re
import sys

ANCHOR_RE = re.compile(
    r"\[anchor: (?:"
    r"path=(?P<path>\S+) mtime=(?P<mtime>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
    r"|"
    r'cmd="(?P<cmd>[^"]+)" ts=(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)'
    r" out=(?P<out>\S+)"
    r")\]$")


def load_mtimes(path):
    table = {}
    for line in open(path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3:
            table[parts[0]] = parts[2]
    return table


def live_mtime_iso(root, rel):
    p = os.path.join(root, rel)
    if not os.path.isfile(p):
        return None
    return datetime.datetime.fromtimestamp(
        int(os.stat(p).st_mtime),
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collected", required=True)
    ap.add_argument("--incident", required=True)
    ap.add_argument("--mtimes")
    ap.add_argument("--root")
    args = ap.parse_args()
    if not args.mtimes and not args.root:
        print("USAGE\t-\tneed --mtimes or --root")
        return 2

    table = load_mtimes(args.mtimes) if args.mtimes else None
    findings = []
    claims = 0
    for lineno, raw in enumerate(
            open(args.collected, encoding="utf-8"), start=1):
        line = raw.rstrip("\n")
        if not line.startswith("- "):
            continue
        claims += 1
        m = ANCHOR_RE.search(line)
        if not m:
            findings.append(("UNANCHORED-CLAIM", "line %d" % lineno,
                             line[:90]))
            continue
        if m.group("path"):
            rel, mt = m.group("path"), m.group("mtime")
            truth = (table.get(rel) if table is not None
                     else live_mtime_iso(args.root, rel))
            if truth is None:
                findings.append(("DANGLING-PATH-ANCHOR", "line %d" % lineno,
                                 rel))
            elif truth != mt:
                findings.append(("STALE-PATH-ANCHOR", "line %d" % lineno,
                                 "%s anchor=%s actual=%s" % (rel, mt, truth)))
        else:
            out_rel = m.group("out")
            if not os.path.isfile(os.path.join(args.incident, out_rel)):
                findings.append(("DANGLING-CMD-ANCHOR", "line %d" % lineno,
                                 "out=%s missing" % out_rel))
    findings.sort()
    for cls, ptr, detail in findings:
        print("%s\t%s\t%s" % (cls, ptr, detail))
    print("ANCHORS\tclaims=%d findings=%d" % (claims, len(findings)))
    if claims == 0:
        print("EMPTY\t-\tno claim lines found")
        return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
