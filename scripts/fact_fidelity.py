#!/usr/bin/env python3
"""Fact-fidelity checker — THE FROZEN FACT GRAMMAR for compiled case pages.

PROVENANCE — COUNTED, byte-preserving port of the kept H-201 fixture grammar
(experiments/runs/H-201/fixture/fact_fidelity.py in the source lab; hypothesis
H-201-keep-case-study-v2 KEPT 2026-08-28, 2x5/5). Only this provenance framing
differs from the counted fixture copy. Frozen at the source fixture build.

THE FROZEN FACT GRAMMAR — the single source of truth for what counts as a fact on the
compiled case page and how it is verified. render_case_study.py imports these functions
for its render-time self-check; grade_h201.py invokes this file by its documented CLI
only (grader<->lib separation, the H-108 rule).

Grammar, per physical page line:
  pointer span   [source: <relpath>(; <relpath>)*]  — names the artifact(s) of record.
                 Pointer spans are provenance labels, excluded from fact extraction.
  quote span     U+201C ... U+201D (curly double quotes) — a verbatim byte-slice of ONE
                 artifact line. Verified as an exact byte substring of at least one
                 artifact pointed to on the SAME line. The page reserves curly double
                 quotes for artifact quotes; straight double quotes are banned in prose.
  number token   \\d+(?:[.,/:%-]\\d+)*%? found in prose AFTER pointer spans, quote spans,
                 and slug names ([HD]-\\d{3} — governed by the bare-slug lint, not the
                 numeral rule) are removed. Each token must appear byte-exact (fallback:
                 with a trailing '%' or '.' stripped) in at least one artifact pointed to
                 on the SAME line.
  coverage       every extracted quote and number must be covered by an extraction-
                 manifest entry of the same kind and value whose artifact set intersects
                 the line's pointed artifacts. A token or quote on a line with NO pointer
                 span is a pointerless fact; a token or quote absent from the manifest is
                 a renderer-invented fact. Both are failures (assertion 1: zero of each).

Artifact resolution: pointers carry repo-relative paths; bytes come from the pinned
fixture copy at <source-dir>/<relpath> (SOURCE-PIN.json is the pin record).

CLI: fact_fidelity.py --page <case-study.md> --manifest <extraction-manifest.json>
                      --source <fixture/source> [--out <report.json>]
Prints the report JSON; exit 0 iff ok. Deterministic: sorted problems, no timestamps.
"""
import argparse
import json
import os
import re
import sys

POINTER_RE = re.compile(r"\[source: ([^\]]+)\]")
QUOTE_RE = re.compile("\u201c([^\u201d]*)\u201d")
SLUG_RE = re.compile(r"\b[HD]-\d{3}\b")
TOKEN_RE = re.compile(r"\d+(?:[.,/:%-]\d+)*%?")
STRAIGHT_QUOTE = '"'


def parse_line(line):
    """One physical line -> {pointers, quotes, tokens} per the frozen grammar."""
    pointers = []
    for m in POINTER_RE.finditer(line):
        pointers.extend(p.strip() for p in m.group(1).split(";") if p.strip())
    stripped = POINTER_RE.sub(" ", line)
    quotes = QUOTE_RE.findall(stripped)
    prose = QUOTE_RE.sub(" ", stripped)
    prose = SLUG_RE.sub(" ", prose)
    tokens = TOKEN_RE.findall(prose)
    return {"pointers": pointers, "quotes": quotes, "tokens": tokens,
            "straight_quotes": STRAIGHT_QUOTE in prose}


def byte_candidates(token):
    cands = [token]
    if token.endswith("%"):
        cands.append(token[:-1])
    if token.endswith("."):
        cands.append(token[:-1])
    return cands


def load_sources(source_dir, relpaths):
    out = {}
    for rel in sorted(set(relpaths)):
        p = os.path.join(source_dir, rel)
        out[rel] = open(p, "rb").read() if os.path.exists(p) else None
    return out


def check(page_text, manifest, source_dir):
    """The leg-2 verdict. manifest: {"facts": [{"kind","value","artifacts"}...]}."""
    problems = []
    facts_seen = []
    lines = page_text.split("\n")
    all_pointers = []
    for line in lines:
        all_pointers.extend(parse_line(line)["pointers"])
    man_facts = manifest.get("facts", [])
    for f in man_facts:
        all_pointers.extend(f.get("artifacts", []))
    sources = load_sources(source_dir, all_pointers)

    # manifest-side: every entry's value must byte-appear in every artifact it names
    for f in sorted(man_facts, key=lambda x: (x.get("kind", ""), x.get("value", ""))):
        val, kind = f.get("value", ""), f.get("kind", "")
        for rel in f.get("artifacts", []):
            data = sources.get(rel)
            if data is None:
                problems.append("manifest: %s fact %r points at missing artifact %s"
                                % (kind, val[:60], rel))
                continue
            cands = [val] if kind == "quote" else byte_candidates(val)
            if not any(c.encode("utf-8") in data for c in cands):
                problems.append("manifest: %s fact %r does not byte-match artifact %s"
                                % (kind, val[:60], rel))

    def covered(kind, value, line_pointers):
        for f in man_facts:
            if f.get("kind") == kind and f.get("value") == value \
                    and set(f.get("artifacts", [])) & set(line_pointers):
                return True
        return False

    # page-side: independent extraction, then pointer + byte-match + coverage
    for i, line in enumerate(lines, 1):
        rec = parse_line(line)
        if rec["straight_quotes"]:
            problems.append("line %d: straight double quote in prose (reserved for "
                            "artifact quotes, which use curly quotes)" % i)
        facts_here = [("quote", q) for q in rec["quotes"]] + \
                     [("number", t) for t in rec["tokens"]]
        if facts_here and not rec["pointers"]:
            for kind, val in facts_here:
                problems.append("line %d: pointerless %s fact %r" % (i, kind, val[:60]))
            continue
        for kind, val in facts_here:
            facts_seen.append({"line": i, "kind": kind, "value": val})
            cands = [val] if kind == "quote" else byte_candidates(val)
            matched = False
            for rel in rec["pointers"]:
                data = sources.get(rel)
                if data is not None and any(c.encode("utf-8") in data for c in cands):
                    matched = True
                    break
            if not matched:
                problems.append("line %d: %s fact %r byte-matches none of its pointed "
                                "artifacts %s" % (i, kind, val[:60], rec["pointers"]))
            if not covered(kind, val, rec["pointers"]):
                problems.append("line %d: %s fact %r not covered by the extraction "
                                "manifest (renderer-invented)" % (i, kind, val[:60]))

    return {"ok": not problems,
            "facts_extracted": len(facts_seen),
            "quotes_extracted": sum(1 for f in facts_seen if f["kind"] == "quote"),
            "numbers_extracted": sum(1 for f in facts_seen if f["kind"] == "number"),
            "manifest_facts": len(man_facts),
            "problems": sorted(problems)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--source", required=True, help="fixture/source dir (pinned copy)")
    ap.add_argument("--out", default="")
    o = ap.parse_args()
    page = open(o.page, encoding="utf-8").read()
    manifest = json.load(open(o.manifest, encoding="utf-8"))
    report = check(page, manifest, os.path.abspath(o.source))
    text = json.dumps(report, indent=1, sort_keys=True)
    if o.out:
        with open(o.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
