#!/usr/bin/env python3
"""parity-check.py -- hyp install byte-parity checker (the parity law's
counted instrument: H-181-hyp-lab-parity in the source lab, kept 2026-08-26,
two consecutive 5/5 — clean install grades zero findings, every seeded
divergence detected with path+class, sync restores byte parity. Shipped
byte-preserving from the counted fixture copy `hyp_parity_check.py`; only
this provenance framing and the script name differ).

Findings one per line as `KIND<TAB>id<TAB>detail`, sorted, exit 1 when any
exist, exit 0 and SILENT when clean; deterministic (no timestamps), stdlib
only, read-only.

Referent: the frozen published manifest -- sorted '<rel> <sha256>' lines whose
own file sha256 IS the published aggregate pin (.publish-manifest.txt formula:
"sorted '<rel> <sha256>' lines" over the shipped files). Alternatively
--reference walks a pinned source tree and derives the same manifest in
memory (install vs the plugin staging tree).

Every finding names the file path (id column) and its shipped file class
(detail column, `class=<class>`), first-match classification:

  hook        hooks/**
  skill-text  skills/**
  kernel-doc  kernel/** ending .md
  manifest    .claude-plugin/plugin.json and any other *.json
  script      any *.py, anything under scripts/
  other       everything else shipped (docs, templates prose, grammar, evals)

Findings:
  PARITY-DIVERGED  installed bytes differ from the referent sha for this path
  PARITY-MISSING   in the referent manifest, absent from the install
  PARITY-EXTRA     present in the install, absent from the referent manifest
  MANIFEST-ERROR   manifest/install/reference unreadable or malformed

.publish-manifest.txt and .git are outside the parity surface everywhere (the
manifest cannot carry its own sha; the publish stamp legitimately differs).

Usage: parity-check.py --install <dir>
                       (--manifest <file> | --reference <dir>)
"""
import argparse
import hashlib
import os
import sys

EXCLUDED_REL = frozenset([".publish-manifest.txt"])
KNOWN_KINDS = ("PARITY-DIVERGED", "PARITY-MISSING", "PARITY-EXTRA",
               "MANIFEST-ERROR")


def classify(rel):
    """Shipped file class, first-match (frozen H-181 recipe mapping)."""
    rel = rel.replace(os.sep, "/")
    if rel.startswith("hooks/"):
        return "hook"
    if rel.startswith("skills/"):
        return "skill-text"
    if rel.startswith("kernel/") and rel.endswith(".md"):
        return "kernel-doc"
    if rel == ".claude-plugin/plugin.json" or rel.endswith(".json"):
        return "manifest"
    if rel.endswith(".py") or rel.startswith("scripts/"):
        return "script"
    return "other"


def tree_shas(root):
    """{rel: sha256} over the tree, .git pruned, parity-surface excludes out."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fn in sorted(filenames):
            ap = os.path.join(dirpath, fn)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            if rel in EXCLUDED_REL:
                continue
            with open(ap, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def aggregate(shas):
    """The publish formula: sha256 over sorted '<rel> <sha256>\\n' lines."""
    return hashlib.sha256("".join(
        "%s %s\n" % (k, shas[k]) for k in sorted(shas)).encode()).hexdigest()


def read_manifest(path):
    """{rel: sha256} from a frozen '<rel> <sha256>' line manifest."""
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.rsplit(" ", 1)
            if len(parts) != 2 or len(parts[1]) != 64 \
                    or any(c not in "0123456789abcdef" for c in parts[1]):
                raise ValueError("malformed manifest line %d: %r" % (i, line))
            rel = parts[0].strip()
            if not rel or rel in EXCLUDED_REL:
                raise ValueError("bad manifest rel at line %d: %r" % (i, line))
            out[rel] = parts[1]
    if not out:
        raise ValueError("empty manifest")
    return out


def parity_findings(install_shas, referent):
    """Sorted (KIND, rel, detail) tuples; empty when byte parity holds."""
    findings = []
    for rel in sorted(referent):
        if rel not in install_shas:
            findings.append(("PARITY-MISSING", rel,
                             "class=%s in the published manifest, absent "
                             "from the install" % classify(rel)))
        elif install_shas[rel] != referent[rel]:
            findings.append(("PARITY-DIVERGED", rel,
                             "class=%s installed sha %s != published sha %s"
                             % (classify(rel), install_shas[rel][:12],
                                referent[rel][:12])))
    for rel in sorted(install_shas):
        if rel not in referent:
            findings.append(("PARITY-EXTRA", rel,
                             "class=%s present in the install, absent from "
                             "the published manifest" % classify(rel)))
    return sorted(findings)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", required=True,
                    help="installed hyp tree to check")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--manifest", help="frozen '<rel> <sha256>' manifest file")
    g.add_argument("--reference", help="pinned source tree to derive the "
                                       "referent manifest from")
    o = ap.parse_args(argv)

    install = os.path.abspath(o.install)
    if not os.path.isdir(install):
        print("MANIFEST-ERROR\t%s\tinstall dir absent or not a directory"
              % o.install)
        return 1
    try:
        install_shas = tree_shas(install)
    except OSError as exc:
        print("MANIFEST-ERROR\t%s\tinstall unreadable: %s" % (o.install, exc))
        return 1
    try:
        if o.manifest:
            referent = read_manifest(os.path.abspath(o.manifest))
        else:
            ref_dir = os.path.abspath(o.reference)
            if not os.path.isdir(ref_dir):
                raise ValueError("reference dir absent: %s" % o.reference)
            referent = tree_shas(ref_dir)
    except (OSError, ValueError) as exc:
        print("MANIFEST-ERROR\t%s\t%s" % (o.manifest or o.reference, exc))
        return 1

    findings = parity_findings(install_shas, referent)
    for kind, rel, detail in findings:
        print("%s\t%s\t%s" % (kind, rel, detail))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
