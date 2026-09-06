#!/usr/bin/env python3
"""Shared tolerant reader for the spec `## Status` word (one canonicalizer for every
status consumer: scripts/dispatch-status.py, scripts/stall-signals.py,
scripts/compile-findings-index.py).

Consumers write the status word in more spellings than the template names: `keep` for
kept, `discard` for discarded, `refined-into: H-NNN` and `refined (into H-NNN ...)` for
a refined-into edge, a bare `refined` for a refine rerun, a qualified compound such as
`discarded-with-findings` or `kept (2x 5/5)` for the canonical word it starts with, and
any casing. A reader that compares the raw token against `kept`/`discarded` shows every
one of those as open, so the dispatch surface (and the Stop hook grading against it)
disagrees with what the spec's author meant. This module maps the FIRST status token,
case-insensitively, onto the canonical vocabulary; unknown tokens pass through verbatim
so nothing is invented.

Canonical: draft | active | refine | kept | discarded | refined-into.
Terminal (committed exit artifacts): kept, discarded, refined-into.
Qualifier rule: `<canonical>-<qualifier>` and `<canonical> (<qualifier>)` map to
`<canonical>` (discarded-with-findings -> discarded; `refined (into ...)` is the one
parenthetical whose head is itself a synonym, and maps to refined-into).

    python3 hyp_status.py --lint [<repo-root>]   # every non-canonical spec + its rewrite
    python3 hyp_status.py --selftest             # 16 mapping cases; exit 1 on any miss

Stdlib only; read-only.
"""
import glob
import os
import re
import sys

CANONICAL = ("draft", "active", "refine", "kept", "discarded", "refined-into")
TERMINAL = ("kept", "discarded", "refined-into")
# raw first token (lowercased) -> canonical word; `refined-into...` is matched by prefix
SYNONYMS = {
    "keep": "kept",
    "discard": "discarded",
    "refined": "refine",
}

_TOKEN_RE = re.compile(r"^\s*([A-Za-z]\S*)")
# `refined (into H-NNN ...)` / `refined (protocol into H-NNN ...)`: a parenthetical
# successor pointer is a refined-into edge, not a bare refine rerun
_REFINED_PAREN_RE = re.compile(r"^\s*refined\s*\([^)]*\binto\b", re.I)


def canonical_status(text):
    """Canonical status word for `text` -- a Status block, its first line, or the bare
    token. None when no alphabetic first token exists (the caller's 'unparsed' case).
    Unknown tokens are returned verbatim."""
    if not text:
        return None
    if _REFINED_PAREN_RE.match(text):
        return "refined-into"
    m = _TOKEN_RE.match(text)
    if not m:
        return None
    tok = m.group(1)
    low = tok.lower()
    if low.startswith("refined-into"):
        return "refined-into"
    if low in CANONICAL:
        return low
    if low in SYNONYMS:
        return SYNONYMS[low]
    # qualifier rule: `<canonical>-<qualifier>` (discarded-with-findings,
    # kept-with-caveats) names the canonical word it starts with; the qualifier is
    # commentary. (`<canonical> (<qualifier>)` is already the bare first token.)
    head = low.split("-", 1)[0]
    if "-" in low and head in CANONICAL:
        return head
    return tok


def is_terminal(text):
    return canonical_status(text) in TERMINAL


def status_line(spec_text):
    """First non-blank line of the spec's `## Status` block, or None when the spec
    has no such block."""
    m = re.search(r"^## Status\s*\n\s*(\S.*)$", spec_text, re.M)
    if not m or m.group(1).startswith("## "):
        return None
    return m.group(1)


# --- lint -------------------------------------------------------------------------------

def lint(root):
    """Print one line per spec whose raw status word is not the canonical spelling,
    with its rewrite; status words outside the canonical vocabulary (no rewrite
    exists) and specs without a `## Status` block are listed separately and not
    counted. Returns the non-canonical count."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hyp_config import load_config
    hyp_rel = load_config(root)["hypotheses_dir"]
    paths = sorted(glob.glob(os.path.join(root, hyp_rel, "H-*.md")))
    bad, foreign, unparsed = [], [], []
    for p in paths:
        rel = os.path.relpath(p, root)
        try:
            line = status_line(open(p, encoding="utf-8").read())
        except OSError:
            continue
        canon = canonical_status(line)
        if canon is None:
            unparsed.append(rel)
            continue
        raw = line.strip()
        if _REFINED_PAREN_RE.match(raw):
            head = raw[:_REFINED_PAREN_RE.match(raw).end()] + " ...)"
        else:
            head = raw.split()[0]
        if canon not in CANONICAL:
            foreign.append((rel, head))
        elif head != canon:
            bad.append((rel, head, canon))
    for rel, head, canon in bad:
        print("%s: %r -> %r" % (rel, head, canon))
    for rel, head in foreign:
        print("%s: %r is outside the canonical vocabulary (no rewrite; not counted)"
              % (rel, head))
    for rel in unparsed:
        print("%s: no '## Status' block (unparsed; not counted)" % rel)
    print("lint: %d non-canonical status word(s) in %d spec(s); %d outside the canonical "
          "vocabulary; %d without a '## Status' block"
          % (len(bad), len(paths), len(foreign), len(unparsed)))
    return len(bad)


# --- selftest ---------------------------------------------------------------------------

SELFTEST = [
    # the nine observed first tokens
    ("kept", "kept"),
    ("draft", "draft"),
    ("discarded", "discarded"),
    ("refined-into: H-036", "refined-into"),
    ("active", "active"),
    ("refine", "refine"),
    ("keep", "kept"),
    ("refined", "refine"),
    ("discard", "discarded"),
    # three casing variants
    ("Kept", "kept"),
    ("DRAFT", "draft"),
    ("Discard", "discarded"),
    # qualifier rule: <canonical>-<qualifier> and <canonical> (<qualifier>)
    ("discarded-with-findings", "discarded"),
    ("DISCARDED-WITH-FINDINGS", "discarded"),
    ("kept-with-caveats", "kept"),
    ("refined (into H-036, 2026-09-05)", "refined-into"),
]


def selftest():
    ok = 0
    for raw, want in SELFTEST:
        got = canonical_status(raw)
        good = got == want
        ok += good
        print("%s %r -> %r (want %r)" % ("ok  " if good else "FAIL", raw, got, want))
    print("selftest: %d/%d" % (ok, len(SELFTEST)))
    return 0 if ok == len(SELFTEST) else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if "--lint" in argv:
        rest = [a for a in argv if a != "--lint"]
        root = os.path.abspath(rest[0] if rest else
                               os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        lint(root)
        return 0
    print(__doc__.strip().splitlines()[0])
    print("usage: hyp_status.py --lint [<repo-root>] | --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
