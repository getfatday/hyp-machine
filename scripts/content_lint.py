#!/usr/bin/env python3
"""Content-law lint over a compiled case page (bare slugs + jargon gloss).

PROVENANCE — COUNTED, byte-preserving port of the kept H-201 fixture lint
(experiments/runs/H-201/fixture/content_lint.py in the source lab; hypothesis
H-201-keep-case-study-v2 KEPT 2026-08-28, 2x5/5). Only this provenance framing
differs from the counted fixture copy. Frozen at the source fixture build.

Two checks over the compiled case page's PROSE — the page text with pointer spans
([source: ...]) and quoted artifact spans (curly double quotes) removed line by line,
because pointers are provenance labels and quotes are verbatim artifact bytes that
cannot be reworded:

  bare slugs      pattern [HD]-\\d{3}. The FIRST occurrence of each distinct slug in
                  prose must be immediately followed (allowing possessive 's and one
                  space) by ' (' opening a gloss of at least ten characters that closes
                  on the same line, or by ' — ' (spaced em-dash) and prose. Later
                  occurrences may be bare. Zero unglossed first uses = zero bare slugs.
  jargon gloss    every term in jargon.json, at its FIRST prose occurrence
                  (case-insensitive, optional plural 's'), must be immediately followed
                  by ' (' opening a gloss of at least ten characters closing on the same
                  line. Terms that never occur in prose pass vacuously.

CLI: content_lint.py --page <case-study.md> --jargon <jargon.json> [--out <report.json>]
Prints the report JSON; exit 0 iff clean. Deterministic: sorted findings, no timestamps.
"""
import argparse
import json
import re
import sys

POINTER_RE = re.compile(r"\[source: ([^\]]+)\]")
QUOTE_RE = re.compile("“([^”]*)”")
SLUG_RE = re.compile(r"\b[HD]-\d{3}\b")
GLOSS_PAREN = r"(?:'s)? ?\(([^)]{10,})\)"
GLOSS_DASH = " — "


def prose_lines(page_text):
    out = []
    for i, line in enumerate(page_text.split("\n"), 1):
        p = POINTER_RE.sub(" ", line)
        p = QUOTE_RE.sub(" ", p)
        out.append((i, p))
    return out


def first_use_glossed(lines, pattern, allow_dash):
    """Find the first case-insensitive match of `pattern` across prose lines; return
    (found, glossed, line_no). Gloss = paren gloss right after the match (>=10 chars,
    closing same line), or — when allow_dash — a spaced em-dash then prose."""
    rx = re.compile(pattern, re.IGNORECASE)
    for i, line in lines:
        m = rx.search(line)
        if not m:
            continue
        rest = line[m.end():]
        if re.match(GLOSS_PAREN, rest):
            return True, True, i
        if allow_dash and rest.startswith(GLOSS_DASH) and len(rest) > 4:
            return True, True, i
        return True, False, i
    return False, False, 0


def lint(page_text, jargon):
    findings = []
    lines = prose_lines(page_text)

    # bare slugs: first use of each DISTINCT slug must be glossed
    slugs = []
    for _, line in lines:
        slugs.extend(SLUG_RE.findall(line))
    for slug in sorted(set(slugs)):
        found, glossed, ln = first_use_glossed(lines, re.escape(slug) + r"\b", True)
        if found and not glossed:
            findings.append("bare slug: %s first used unglossed at line %d" % (slug, ln))

    # jargon terms: first use must carry a paren gloss
    for t in jargon.get("terms", []):
        pattern = r"\b(?:%s)s?\b" % t["pattern"]
        found, glossed, ln = first_use_glossed(lines, pattern, False)
        if found and not glossed:
            findings.append("jargon: %r first used unglossed at line %d"
                            % (t["term"], ln))

    return {"clean": not findings,
            "distinct_slugs": sorted(set(slugs)),
            "terms_checked": [t["term"] for t in jargon.get("terms", [])],
            "findings": sorted(findings)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--jargon", required=True)
    ap.add_argument("--out", default="")
    o = ap.parse_args()
    page = open(o.page, encoding="utf-8").read()
    jargon = json.load(open(o.jargon, encoding="utf-8"))
    report = lint(page, jargon)
    text = json.dumps(report, indent=1, sort_keys=True)
    if o.out:
        with open(o.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
