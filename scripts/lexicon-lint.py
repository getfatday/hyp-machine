#!/usr/bin/env python3
# ADVISORY lexicon lint (verified against seeded corpora in two consecutive runs before
# shipping). Corpus arg: a dir holding a glossary (JSON or *GLOSSARY*.md)
# + node .md files; the grounding wordlist resolves from the corpus dir or beside this
# script. Changes require re-validation.
# -*- coding: utf-8 -*-
"""lexicon_lint.py — lexicon lint over a GLOSSARY-v2 + node-file corpus (ADVISORY:
findings inform the glossary owner; they certify nothing by themselves).

USAGE
    python3 lexicon_lint.py <corpus-dir>
    python3 lexicon_lint.py --self-test   # A18-A20 conformance checks;
                                          # PASS/FAIL lines, exit 0 iff all pass

CORPUS
    <corpus-dir> holds one glossary (JSON; every *.json under the dir is read as
    glossary material) plus markdown files: node files (YAML-style frontmatter,
    `definition:` block per the parent design §2 / addendum §3.1 reference form)
    and plain fixture documents. Optional shared fixture files honored if present
    (corpus dir first, then this script's dir): grounding-wordlist.txt (one word
    per line, '#' comments) and d5-stoplist.txt.

OUTPUT CONTRACT (house / corpus-lint shape)
    One finding per line: CLASS<TAB>pointer<TAB>detail, pointer = path:line
    (path relative to corpus-dir). Lines sorted lexicographically (byte sort of
    the full line). WARN classes (D5, L5G) carry a detail beginning "WARN: " and
    are excluded from exit-code logic. Exit 0 = clean or warns only; exit 1 =
    at least one fail-class finding; exit 2 = usage/corpus error. Deterministic:
    byte-identical output across runs on the same corpus.

CLASSES (frozen by the designs)
    D1 def-path-token        path/URL/extension token in a node definition subkey
    D2 def-code-ident        code identifier in a node definition subkey
    D3 def-node-id           node-id vocabulary in a node definition subkey
    D4 def-missing-layer     node lacks definition block / block incomplete
    D5 def-term-unregistered coined term >= 3 uses, unregistered      [WARN]
    L1 overlong definition   glossary definition line > 25 words (P1)
    L2 headword echo         headword/expansion token (P2 variant table) in own
                             definition line
    L3 link rule (P3)        dangling [[link]] / dangling see-also target /
                             unlinked registered term / unregistered jargon
                             (defining-vocabulary closure) / unparseable entry
    L4 circularity (P4)      exact SCC clusters (size >= 2) and self-links over
                             definition-line [[link]] edges; see-also excluded
    L5 acronym (P5)          entry-level: acronym headword without expansion;
                             doc-level: registered acronym unexpanded on first
                             use in a document body
    L5G acronym gap          honest `UNKNOWN-FROM-CORPUS — <evidence> — ask:
                             <route>` expansion                      [WARN]
    L6 wiring leak (P6)      D1/D2/D3 regexes (shared implementation) over
                             glossary definition:/object: fields; expansion: is
                             the one exempt surface

AMBIGUITY — where the designs underdetermine, the reading chosen (never silent):
 A1  Glossary format: the designs give a markdown v2 grammar (§2.1) but this
     hypothesis's corpus carries a glossary JSON. Accepted JSON shapes: a list
     of entry objects, {"entries": [...]}, or a {slug: {fields}} mapping; field
     keys term/expansion/definition/object/see-also (also see_also)/rejected;
     see-also/rejected values may be strings ("[[a]], [[b]]", "a, b", "—") or
     lists. Files named *GLOSSARY*.md are additionally parsed per the §2.1 text
     grammar. Shape violations (missing required field, duplicate slug, bad
     slug, unparseable file) fold into L3 "unparseable entry" per §2.1.
 A2  Pointers: the contract says path:line. For JSON glossaries the line is
     recovered by deterministic raw-text search (the slug's first key-position
     occurrence; field lines located within the entry's span). If a compact
     JSON defeats line recovery, line 1 is used — still deterministic.
 A3  Sort: "sorted" is read as a plain lexicographic byte sort of the emitted
     lines (corpus-lint contract's simplest mechanical reading).
 A4  WARN marking: the addendum names L5G a "warn row" and the design says "the L5G
     WARN line"; parent §5 rules "D5 warns". Both are emitted with their class
     token (L5G / D5) and a detail prefixed "WARN: ", and both are excluded
     from the exit code ("warns excluded from exit").
 A5  Wiring emission granularity: one finding per (surface-field, D-subclass),
     naming the first matched span — so the anti-exemplar c1 block fires D1 AND
     D2 AND D3 exactly once each, and a v1-row seed "fires L6 with the matched
     sub-pattern named" once per sub-pattern. Per-span emission was rejected:
     the parent's own examples attribute identical token shapes to different
     classes, so span-level attribution is not derivable from the design.
 A6  D-class overlaps: D1's slash-path regex excludes D3-shaped tokens (node
     ids are their own class; otherwise every D3 seed would double-fire D1).
     D2's dotted-ident check excludes tokens ending in a D1 code/doc extension
     (filenames are D1's jurisdiction) and the common abbreviations e.g./i.e.;
     the digit-dotted version carve-out is per design. camelCase/snake/backtick
     /braces/call checks stay independent (a platformCapabilities.ts-shaped
     token legitimately fires D1 and D2, matching the c1 attribution).
 A7  P2 scope: ban-set tokens are checked against the definition line's prose
     tokens only — [[link]] spans are excluded (a self-link is L4's finding,
     not an echo), and tokens in the entry's own ban set are likewise skipped
     by every P3 check so one seeded echo fires exactly one class (L2).
 A8  P2 ban sources: `term:` + `expansion:` tokens; an expansion holding the
     UNKNOWN-FROM-CORPUS marker (or "—") contributes no ban tokens ("the
     UNKNOWN-FROM-CORPUS marker words exempt") — required for the DSRS
     exemplar to stay silent ("publishes" appears in marker and definition).
 A9  Plainness: a token is plain iff it (or a P2-style stripped stem, plus an
     i→y restoration used for plain-matching only) appears in the grounding
     wordlist. The designs make grounding-wordlist.txt a shared committed
     fixture; this lint loads it from the corpus dir or beside the script and
     only falls back to an embedded common-English list (which covers all
     seven exemplar definition lines) if no file exists. Hyphenated tokens are
     plain when the whole compound or all alphabetic parts are plain
     ("hyphen/space-joined compound, checked as bigram/trigram").
 A10 Registered-term matching in definition prose (P3 unlinked-registered):
     n-grams up to 3 words, longest first, hyphen/space- and case-insensitive;
     single-word registered terms also match via their P2 variant sets. Words
     consumed by a registered n-gram are not re-checked for jargon. One
     finding per (entry, term); jargon deduped per (entry, word).
 A11 L4 graph: edges only for definition-line links that resolve to glossary
     entries (directly or via a rejected: synonym); links resolving to node
     labels are legal (P3) but are not entry edges. Each SCC of size >= 2 (or
     self-link) is ONE finding pointing at the lexicographically first
     member's definition line, members listed sorted — exact clusters, never
     supersets/subsets (Tarjan).
 A12 P5 doc mode: a "registered acronym" is an acronym-shaped token
     (\\b[A-Z]{2,}\\b) in a glossary entry's term: (slug as fallback). First
     occurrence per document body must sit in an expansion pattern, checked by
     SHAPE: `... (ACRO)` or `ACRO (<non-empty>)` (the unknown-expansion form
     "ACRO (expansion unknown — …)" is the second shape). Verbatim match of
     the parenthetical against the entry's expansion text is NOT required (the
     design specifies patterns, not text equality). Document body = markdown
     text minus frontmatter; the glossary JSON is not a document; in a
     *GLOSSARY*.md a [[link]] to the acronym's entry counts as
     expanded-at-source, and entry-machinery lines (## headings and the six
     field lines) are exempt from doc mode — the entry IS the source, and a
     bare acronym inside another entry's definition is already L3's
     jurisdiction (unlinked registered term). Only exact-case occurrences
     count.
 A13 D4 scope: every .md with frontmatter is a node file (plain docs carry
     none); *GLOSSARY*.md and model.md are never node files. Complete =
     is:+object:+stakes: inline, OR the addendum §3.1 reference form (term:
     resolving to a glossary entry, plus stakes:).
 A14 D5 candidates: hyphenated alpha compounds (case-folded), counted across
     the §4 scan surface (node definition subkeys + summary: lines + model.md
     + all glossary fields). "Multiword node-label matches" are by the §4
     registry predicate always registered, so they cannot fire and are not
     separately detected. Spaced variants are NOT counted toward a compound's
     tally (keeps counts predictable from seeded placements). Threshold >= 3
     occurrences; one WARN per term at its first occurrence, count in detail.
 A15 UNKNOWN marker grammar: `UNKNOWN-FROM-CORPUS — <evidence> — ask: <route>`
     with em-dash or `--` separators. A marker missing evidence/ask is not the
     explicit gap marker and fires L5 (fail), not L5G.
 A16 L5 entry-level checks the term: line only (slug consulted when term: is
     absent); expansion "—" is legal for non-acronym headwords (clippings are
     craft, not lint, per P5).
 A17 Jurisdiction separation for "fires exactly once per seeded instance"
     (wiring-mask rule): spans matched by the wiring regexes are masked out
     of a definition line before the P2/P3 token scans, exactly as the
     entry's own ban-set tokens are skipped — a backticked `git diff
     --cached` seed fires L6 only, never a side L3 "unregistered jargon" on
     'git'. L1's word count stays over the raw line (length is a property of
     the whole line).
 A18 (design D1 row AMENDED:) the original slash leg was
     RE_D1_PATH = r"[\\w.-]+/[\\w./-]+", which also matched natural-language
     alternations like "person/role", contradicting the design's own frozen
     must-silent calibration text. Per the amended row the slash leg now
     fires ONLY on a slash token with >= 2 slashes OR any segment containing
     a dot, underscore, or digit; single-slash all-alpha(-hyphen) tokens are
     prose, not wiring, and stay silent (and, being prose, now flow unmasked
     into the P2/P3 token scans). The extension and :// legs are unchanged.
     The amendment lives in the ONE shared implementation (d_scan + the A17
     mask), so it flows into L6 and the node D-classes identically — the
     addendum's "L6 imports D1-D3 unchanged: one implementation, two badges"
     still holds. Trailing sentence dots are stripped from the slash token
     before the predicate ("person/role." stays prose; the detail span stays
     clean).
 A19 (2026-08-17) grounding-wordlist.txt is now a landed fixture beside this
     script (9,894 lemmas, google-10000-english; provenance in
     grounding-wordlist-source.md), so the A9 corpus-dir-or-beside-script
     resolution finds a real file in these runs and the embedded fallback is
     last-resort only — it should no longer engage. The design's "wordlist
     minus every registered term's P2 variant set" holds at match time,
     term-level: the A10 registered-term checks (n-grams + variant_to_norm)
     run BEFORE the plainness check, so a registered term is never plain.
     Token-level subtraction of multiword terms' bare tokens was rejected:
     it would false-fire the frozen must-silent exemplars (e.g. "the build"
     in the pinned entry vs the registered term "build contract").
 A20 (addendum P3 "Plain" AMENDED:) morphological normalization beyond the A9 plural/verb
     stems: a token is ALSO plain if (a) it ends in "ly" and an in-list
     base rescues it under standard orthography — strip "ly" (quietly ->
     quiet), "-ily" -> "y" (happily -> happy), and a doubled final
     consonant collapsed — or (b) it is the closed compound "cannot"
     (= in-list "can" + "not"). A frequency list tokenizes running text,
     so regular morphology of plain bases is plain by construction; the
     wordlist file stays a verbatim external artifact, never locally
     edited. A coined "-ly" token off an out-of-list base is NOT rescued
     and still fires L3 unregistered jargon. The A10/A19 registered-
     before-plain order is unchanged: the rescue lives inside is_plain,
     which the closure consults only after the registered-term checks, so
     a registered term is never plain.
"""

import json
import os
import re
import sys

# ---------------------------------------------------------------- constants

CODE_EXTS = ("py", "ts", "tsx", "js", "json", "yaml", "yml", "md", "sh", "html")
NODE_TYPES = "actor|command|event|policy|read-?model|external|aggregate"
WORD_LIMIT = 25          # P1
D5_THRESHOLD = 3         # parent §4, N=3 frozen at registration
FAIL_CLASSES = {"D1", "D2", "D3", "D4", "L1", "L2", "L3", "L4", "L5", "L6"}
WARN_CLASSES = {"D5", "L5G"}
REQUIRED_FIELDS = ("term", "expansion", "definition", "object", "see-also")
SLUG_RE = re.compile(r"^[A-Za-z0-9-]+$")
LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
ACRO_RE = re.compile(r"\b[A-Z]{2,}\b")
DASH = r"(?:—|--)"  # em dash or --
UNKNOWN_MARKER_RE = re.compile(
    r"^UNKNOWN-FROM-CORPUS\s*" + DASH + r"\s*(?P<evidence>.+?)\s*" + DASH +
    r"\s*ask:\s*(?P<route>.+)$", re.S)
UNKNOWN_PREFIX = "UNKNOWN-FROM-CORPUS"

# D-class regexes (parent design §5, frozen; shared with L6 — one
# implementation, two badges).
RE_D3 = re.compile(r"(?<![A-Za-z0-9-])(?:%s)/[a-z0-9][a-z0-9-]*" % NODE_TYPES)
RE_D1_EXT = re.compile(r"(?<![\w/])[\w.-]*\.(?:%s)\b" % "|".join(CODE_EXTS))
RE_D1_URL = re.compile(r"[A-Za-z][\w+.-]*://[^\s]+")
# D1 slash leg AMENDED 2026-08-17 (A18; was RE_D1_PATH = r"[\w.-]+/[\w./-]+"):
# tokenize maximal slash runs, then fire only per d1_slash_fires().
RE_D1_SLASH_RUN = re.compile(r"[\w.-]*/[\w./-]*")
RE_D1_SEG_WIRING = re.compile(r"[._0-9]")
RE_D2_BACKTICK = re.compile(r"`[^`\n]+`")
RE_D2_CAMEL = re.compile(r"[a-z]+[A-Z][A-Za-z0-9]*")
RE_D2_SNAKE = re.compile(r"\w+_\w+")
RE_D2_CALL = re.compile(r"\w+\(")
RE_D2_BRACES = re.compile(r"\{[^{}\n]*\}")
RE_D2_DOTTED = re.compile(r"[A-Za-z][\w-]*\.[A-Za-z][\w-]*")
DOTTED_ABBREV = {"e.g", "i.e"}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
HYPH_CAND_RE = re.compile(r"(?<![\w-])[A-Za-z]+(?:-[A-Za-z]+)+(?![\w-])")

# Frozen D5 stoplist (parent §5): common-English hyphenations + SCHEMA
# key/type vocabulary (caps-spoken keys included via case-folding).
D5_STOPLIST = {
    "two-way", "one-way", "second-guess", "second-guesses", "past-tense",
    "left-to-right", "right-to-left", "top-down", "bottom-up", "date-stamp",
    "day-to-day", "up-to-date", "long-term", "short-term", "high-level",
    "low-level", "real-time", "one-off", "built-in", "follow-up", "hands-on",
    "trade-off", "well-formed", "so-called", "sign-off", "round-trip",
    "self-reference", "write-once", "append-only", "third-person",
    "genus-differentia", "unknown-from-corpus",
    # SCHEMA key/type vocabulary
    "issued-by", "cast-as", "invoked-on", "projects-from", "consumed-by",
    "read-model", "read-models", "see-also", "closes-when", "node-id",
}

VOWELS = set("aeiou")
STRIP_SUFFIXES = ("ing", "ed", "es", "d", "s")
ADD_SUFFIXES = ("s", "es", "d", "ed", "ing")

# Embedded fallback grounding wordlist (used ONLY when no
# grounding-wordlist.txt is found; covers the seven exemplar definition lines
# plus core common English — see AMBIGUITY A9).
_FALLBACK_WORDS = """
a about above accept across act action after again against agree agreed all
almost alone along already also always am an and answer any anyone anything
are around as ask asked at away back bar batch be because been before begin
behind being below beside between big board both box bring brought build
builder builders building built but by call can cannot care carry case catalog
cause change check checklist choose chosen clean clear come complete
component copied copies copy could count cover cycle cycles date day decide
defined definition did different do does done door down during each early
edition editions either end enough entry even ever every everyone everything
exact exactly expansion expect factory fail fair fall far fast few final find
finish finished first fix fixed follow followed follows for form found four
from front full get give given glance go goes good great group grow had hand
happen has have he head hear held help her here high him his hold home hour
house how however if in inside into is it item its itself job join just keep
kept kind know known language large last late later lead learn leave left
less let letter level like line lines list little live long look loose lose
low machine made main make manage manages many mark may me mean means measure
measured meet might miss missing mirror more most move moves much must my
name named near need never new next no nobody none not nothing now number of
off official often old on once one only open or order other our out outside
over own packet page pages pair paper part pass past people per person piece
pin place related whose acronym trap
own packet page pages pair paper part pass past per person piece pin place
plain plan point present print process promise prove publish publishes pull
put quality question quick quiet quietly rail ratchet reach read ready real
record right room round row rule run said same say school second see seen
send sent service set seven shall she shelf shop short should show side sign
signed simple since single sit small so some someone something soon sound
stand staple stapled start state stay step still stop story such take taken
talk team tell ten test than that the their them then there these they thing
think this those three through ticked ticket time times to today together
told too took top toward treat treated true try turn two under understand
until up upon upward us use used version very wait walk wall want was watch way we
week well went were what when where whether which while who whole why will
win wins with within without word words work works world would write written
wrong year yes yet you your
"""

# ------------------------------------------------------- variant machinery


def _strip_stems(tok):
    """P2 strip-step: remove suffixes, restore final e, collapse doubling."""
    stems = set()
    for suf in STRIP_SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            stem = tok[: -len(suf)]
            stems.add(stem)
            stems.add(stem + "e")
            if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in VOWELS:
                stems.add(stem[:-1])
    return stems


def _add_variants(stem):
    """P2 add-step: {+s,+es,+d,+ed,+ing} with e-drop and consonant doubling."""
    out = set()
    for suf in ADD_SUFFIXES:
        out.add(stem + suf)
        if stem.endswith("e"):
            out.add(stem[:-1] + suf)
        if stem and stem[-1].isalpha() and stem[-1] not in VOWELS:
            out.add(stem + stem[-1] + suf)
    return out


def norm_word(tok):
    """Lowercase and strip possessives/quote cruft."""
    t = tok.lower().strip("'’")
    t = re.sub(r"['’]s$", "", t)
    return t


def variant_set(token):
    """Frozen P2 generation table over one token."""
    tok = norm_word(token)
    if not tok:
        return set()
    base = {tok} | _strip_stems(tok)
    full = set(base)
    for s in base:
        full |= _add_variants(s)
    return full


def phrase_variant_set(phrase):
    """Union of per-token variant sets for a term/expansion line."""
    out = set()
    for t in TOKEN_RE.findall(phrase):
        out |= variant_set(t)
    return out


def norm_term(s):
    """Case-, space/hyphen-insensitive normal form for registry matching."""
    s = norm_word(s.strip())
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")

# ------------------------------------------------------------ corpus loading


def load_wordfile(paths):
    for p in paths:
        if os.path.isfile(p):
            words = set()
            with open(p, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.split("#", 1)[0].strip().lower()
                    if ln:
                        words.add(ln)
            if words:
                return words
    return None


def _ly_bases(t):
    """A20 -ly rescue bases, standard orthography: strip "ly"; "-ily" ->
    "y" (happily -> happy); collapse a doubled final consonant."""
    bases = set()
    if len(t) >= 4 and t.endswith("ly"):
        stem = t[:-2]
        bases.add(stem)
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in VOWELS:
            bases.add(stem[:-1])
        if t.endswith("ily") and len(t) >= 5:
            bases.add(t[:-3] + "y")
    return bases


def is_plain(token, wordlist):
    """A9 + A20: token plain iff itself or a rescued base is in the
    grounding wordlist — a stripped stem (with i->y restore, plain-matching
    only), a regular -ly adverb's base (A20), or the closed compound
    "cannot" (A20)."""
    t = norm_word(token)
    if not t:
        return True
    if t in wordlist:
        return True
    if t == "cannot" and "can" in wordlist and "not" in wordlist:
        return True
    cands = _strip_stems(t)
    for s in set(cands):
        if s.endswith("i"):
            cands.add(s[:-1] + "y")
    cands |= _ly_bases(t)
    return any(s in wordlist for s in cands)


def compound_plain(token, wordlist):
    """Hyphenated compound: plain as a unit, or all alphabetic parts plain."""
    if is_plain(token, wordlist):
        return True
    parts = [p for p in token.split("-") if p]
    if len(parts) > 1 and all(is_plain(p, wordlist) for p in parts):
        return True
    return False


class Entry(object):
    __slots__ = ("slug", "path", "line", "fields", "field_lines")

    def __init__(self, slug, path, line):
        self.slug = slug
        self.path = path
        self.line = line
        self.fields = {}       # field name -> string value
        self.field_lines = {}  # field name -> line number


def _listify(v):
    """see-also / rejected value -> list of raw target strings."""
    if v is None:
        return []
    if isinstance(v, list):
        items = [str(x) for x in v]
    else:
        s = str(v).strip()
        if s in ("", "—", "-"):
            return []
        found = LINK_RE.findall(s)
        items = found if found else [x for x in s.split(",")]
    out = []
    for it in items:
        it = it.strip()
        it = re.sub(r"^\[\[|\]\]$", "", it).strip()
        if it and it not in ("—", "-"):
            out.append(it)
    return out


def _find_line(raw_lines, needle_res, start=0, end=None):
    """1-based line of first regex match in raw text lines; None if absent."""
    end = len(raw_lines) if end is None else end
    for i in range(start, min(end, len(raw_lines))):
        for nr in needle_res:
            if nr.search(raw_lines[i]):
                return i + 1
    return None


def parse_glossary_json(relpath, text, findings):
    """A1/A2: accept list / {"entries": [...]} / {slug: fields} shapes."""
    entries = []
    raw_lines = text.splitlines()
    try:
        data = json.loads(text)
    except ValueError as exc:
        findings.append(("L3", relpath, 1,
                         "unparseable entry: glossary JSON does not parse (%s)"
                         % exc.__class__.__name__))
        return entries
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        items = [(None, e) for e in data["entries"]]
    elif isinstance(data, list):
        items = [(None, e) for e in data]
    elif isinstance(data, dict):
        items = list(data.items())
    else:
        findings.append(("L3", relpath, 1,
                         "unparseable entry: glossary JSON is not a list or object"))
        return entries
    for key, obj in items:
        if not isinstance(obj, dict):
            findings.append(("L3", relpath, 1,
                             "unparseable entry: non-object glossary item"))
            continue
        slug = str(key if key is not None else obj.get("slug", "")).strip()
        if key is not None:
            line_res = [re.compile(r'"%s"\s*:' % re.escape(slug))]
        else:
            line_res = [re.compile(r'"slug"\s*:\s*"%s"' % re.escape(slug))]
        line = _find_line(raw_lines, line_res) or 1
        if not slug or not SLUG_RE.match(slug):
            findings.append(("L3", relpath, line,
                             "unparseable entry: missing or malformed slug %r" % slug))
            continue
        ent = Entry(slug, relpath, line)
        nxt = None  # end of this entry's span for field-line search
        for fname in ("term", "expansion", "definition", "object",
                      "see-also", "see_also", "seealso", "rejected"):
            if fname not in obj:
                continue
            canon = "see-also" if fname in ("see_also", "seealso") else fname
            val = obj[fname]
            ent.fields[canon] = val
            fl = _find_line(raw_lines,
                            [re.compile(r'"%s"' % re.escape(fname))],
                            start=line - 1, end=nxt)
            ent.field_lines[canon] = fl or line
        entries.append(ent)
    return entries


GLOSSARY_MD_FIELD_RE = re.compile(
    r"^(term|expansion|definition|object|see-also|rejected):\s?(.*)$")


def parse_glossary_md(relpath, text, findings):
    """Addendum §2.1 text grammar: ^## <slug> headings + one-line fields."""
    entries = []
    cur = None
    for i, raw in enumerate(text.splitlines()):
        lineno = i + 1
        if raw.startswith("## "):
            slug = raw[3:].strip()
            if not SLUG_RE.match(slug):
                findings.append(("L3", relpath, lineno,
                                 "unparseable entry: malformed slug %r" % slug))
                cur = None
                continue
            cur = Entry(slug, relpath, lineno)
            entries.append(cur)
            continue
        if cur is None:
            continue
        if not raw.strip():
            cur = None  # blank line ends the entry
            continue
        m = GLOSSARY_MD_FIELD_RE.match(raw)
        if m:
            cur.fields[m.group(1)] = m.group(2).strip()
            cur.field_lines[m.group(1)] = lineno
        else:
            findings.append(("L3", relpath, lineno,
                             "unparseable entry: stray line inside entry '%s'"
                             % cur.slug))
    return entries


class Node(object):
    __slots__ = ("path", "label", "node_id", "summary", "summary_line",
                 "def_line", "def_fields", "def_field_lines", "body",
                 "body_start", "has_def")

    def __init__(self, path):
        self.path = path
        self.label = None
        self.node_id = None
        self.summary = None
        self.summary_line = None
        self.def_line = None
        self.def_fields = {}
        self.def_field_lines = {}
        self.body = ""
        self.body_start = 1
        self.has_def = False


FM_KEY_RE = re.compile(r"^([A-Za-z][\w-]*):\s?(.*)$")
DEF_SUBKEY_RE = re.compile(r"^(\s+)(is|object|stakes|term):\s?(.*)$")


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def parse_node_md(relpath, text):
    """Minimal frontmatter parse (stdlib, no yaml): returns Node or None
    (None = no frontmatter => plain document)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    node = Node(relpath)
    i = 1
    in_def = False
    def_indent = 0
    cur_sub = None
    while i < len(lines):
        raw = lines[i]
        if raw.strip() == "---":
            node.body = "\n".join(lines[i + 1:])
            node.body_start = i + 2
            break
        sub = DEF_SUBKEY_RE.match(raw)
        if in_def and sub:
            cur_sub = sub.group(2)
            def_indent = len(sub.group(1))
            node.def_fields[cur_sub] = _unquote(sub.group(3))
            node.def_field_lines[cur_sub] = i + 1
            i += 1
            continue
        if in_def and cur_sub and raw.strip() and \
                (len(raw) - len(raw.lstrip())) > def_indent:
            # continuation of a wrapped scalar
            node.def_fields[cur_sub] = (
                node.def_fields[cur_sub].rstrip() + " " +
                _unquote(raw.strip())).strip()
            i += 1
            continue
        in_def = False
        cur_sub = None
        m = FM_KEY_RE.match(raw)
        if m:
            key, val = m.group(1), _unquote(m.group(2))
            if key == "definition" and not val:
                node.has_def = True
                node.def_line = i + 1
                in_def = True
            elif key == "summary":
                node.summary = val
                node.summary_line = i + 1
            elif key == "id":
                node.node_id = val
                if "/" in val:
                    node.label = val.split("/", 1)[1].strip()
        i += 1
    if node.label is None:
        base = os.path.basename(relpath)
        node.label = os.path.splitext(base)[0]
    return node

# --------------------------------------------------------------- wiring scan


def d1_slash_fires(token):
    """AMENDED D1 slash leg (design row 2026-08-17, A18): a slash token is
    wiring iff it has >= 2 slashes OR any segment contains a dot,
    underscore, or digit. Single-slash all-alpha(-hyphen) alternations
    ("person/role") are prose, not wiring."""
    if token.count("/") >= 2:
        return True
    return any(RE_D1_SEG_WIRING.search(seg) for seg in token.split("/"))


def iter_d1_slash(text):
    """Yield (start, token) for every AMENDED-D1-firing slash token; trailing
    sentence dots stripped before the predicate (A18)."""
    for m in RE_D1_SLASH_RUN.finditer(text):
        tok = m.group(0).rstrip(".")
        if "/" not in tok:
            continue
        if d1_slash_fires(tok):
            yield (m.start(), tok)


def d_scan(text):
    """Shared D1/D2/D3 detection (parent §5 regexes, A5/A6 overlap rules).
    Returns {class: first matched span} for classes that match."""
    out = {}
    if not text:
        return out
    m3 = RE_D3.search(text)
    if m3:
        out["D3"] = m3.group(0)
    # D1: extension token, URL, or AMENDED slash leg (A18) not D3-shaped
    cands = []
    m = RE_D1_EXT.search(text)
    if m:
        cands.append((m.start(), -len(m.group(0)), m.group(0)))
    m = RE_D1_URL.search(text)
    if m:
        cands.append((m.start(), -len(m.group(0)), m.group(0)))
    for start, tok in iter_d1_slash(text):
        if RE_D3.fullmatch(tok):
            continue
        cands.append((start, -len(tok), tok))
        break
    if cands:
        out["D1"] = min(cands)[2]
    # D2: backtick span, camelCase, snake_case, call syntax, braces,
    # alphabetic dotted ident (version-literal carve-out is inherent: the
    # dotted regex requires letters flanking the dot).
    d2c = []
    for rex in (RE_D2_BACKTICK, RE_D2_CAMEL, RE_D2_SNAKE, RE_D2_CALL,
                RE_D2_BRACES):
        m = rex.search(text)
        if m:
            d2c.append(m)
    for m in RE_D2_DOTTED.finditer(text):
        span = m.group(0)
        tail = span.rsplit(".", 1)[-1].lower()
        if tail in CODE_EXTS:
            continue  # filename => D1's jurisdiction (A6)
        if span.lower() in DOTTED_ABBREV:
            continue
        d2c.append(m)
        break
    if d2c:
        m = min(d2c, key=lambda mm: (mm.start(), -len(mm.group(0))))
        out["D2"] = m.group(0)
    return out


WIRING_MASK_RES = (RE_D3, RE_D1_EXT, RE_D1_URL, RE_D2_BACKTICK,
                   RE_D2_CAMEL, RE_D2_SNAKE, RE_D2_CALL, RE_D2_BRACES,
                   RE_D2_DOTTED)


def mask_wiring(text):
    """A17: blank every wiring-regex span (expanded to its whole
    non-whitespace run) so P2/P3 never re-flag what is L6/D-class
    jurisdiction. Slash tokens are masked only when the AMENDED D1 leg
    fires (A18) — prose alternations stay visible to the token scans."""
    if not text:
        return text
    chars = list(text)
    spans = []
    for rex in WIRING_MASK_RES:
        for m in rex.finditer(text):
            spans.append((m.start(), m.end()))
    for start, tok in iter_d1_slash(text):
        spans.append((start, start + len(tok)))
    for s, e in spans:
        while s > 0 and not text[s - 1].isspace():
            s -= 1
        while e < len(text) and not text[e].isspace():
            e += 1
        for i in range(s, e):
            chars[i] = " "
    return "".join(chars)

# ------------------------------------------------------------------ registry


class Registry(object):
    """Parent §4 predicate (v2 surface per addendum ext. map item 4):
    registered iff glossary term/slug, rejected synonym, or node label."""

    def __init__(self, entries, nodes):
        self.entries_by_norm = {}   # norm -> Entry (slug + term forms)
        self.rejected_to_slug = {}  # norm(synonym) -> owning slug
        self.node_labels = set()
        self.registered_norms = set()
        self.display = {}           # norm -> display form
        for ent in entries:
            for form in (ent.slug, str(ent.fields.get("term", "") or "")):
                n = norm_term(form)
                if n:
                    self.entries_by_norm.setdefault(n, ent)
                    self.registered_norms.add(n)
                    self.display.setdefault(n, form.strip())
            for syn in _listify(ent.fields.get("rejected")):
                n = norm_term(syn)
                if n:
                    self.rejected_to_slug.setdefault(n, ent.slug)
                    self.registered_norms.add(n)
                    self.display.setdefault(n, syn)
        for nd in nodes:
            n = norm_term(nd.label or "")
            if n:
                self.node_labels.add(n)
                self.registered_norms.add(n)
                self.display.setdefault(n, nd.label)
        # single-word registered terms: P2 variant sets for prose matching
        self.variant_to_norm = {}
        for n in sorted(self.registered_norms):
            if "-" not in n:
                for v in sorted(variant_set(n)):
                    self.variant_to_norm.setdefault(v, n)

    def resolve_entry(self, target):
        """Entry slug a [[target]] resolves to, or None (node labels are
        resolvable but not entries)."""
        n = norm_term(target)
        if n in self.entries_by_norm:
            return self.entries_by_norm[n].slug
        if n in self.rejected_to_slug:
            return self.rejected_to_slug[n]
        return None

    def resolves(self, target):
        n = norm_term(target)
        return (n in self.entries_by_norm or n in self.rejected_to_slug or
                n in self.node_labels)

# --------------------------------------------------------------- ban sets


def ban_set(ent):
    """P2 ban set: term + expansion tokens with variants; '—' and the
    UNKNOWN-FROM-CORPUS marker contribute nothing (A8)."""
    out = set()
    term = str(ent.fields.get("term", "") or "")
    out |= phrase_variant_set(term)
    exp = str(ent.fields.get("expansion", "") or "").strip()
    if exp and exp not in ("—", "-") and not exp.startswith(UNKNOWN_PREFIX):
        out |= phrase_variant_set(exp)
    return out

# ------------------------------------------------------------------- checks


def count_words(defline):
    """P1: whitespace tokens after stripping punctuation/brackets; a [[link]]
    counts as one word."""
    text = LINK_RE.sub(" LINKTOKEN ", defline)
    n = 0
    for tok in text.split():
        if tok.strip("[](){}.,;:!?\"'—–-“”‘’"):
            n += 1
    return n


def strip_links(defline):
    """Replace [[...]] spans with spaces (offsets preserved)."""
    return LINK_RE.sub(lambda m: " " * len(m.group(0)), defline)


def check_entry_local(ent, reg, wordlist, findings):
    """L1, L2, L3 (links/registered/jargon), L5/L5G, L6 for one entry."""
    fields = ent.fields
    dline = ent.field_lines.get("definition", ent.line)

    # -- required-field shape (folds into L3, A1)
    missing = [f for f in REQUIRED_FIELDS if not str(
        fields.get(f, "") or "").strip()]
    if missing:
        findings.append(("L3", ent.path, ent.line,
                         "unparseable entry '%s': missing required field(s) %s"
                         % (ent.slug, ", ".join(missing))))

    definition = str(fields.get("definition", "") or "")
    objectline = str(fields.get("object", "") or "")

    # -- L1 overlong
    if definition:
        n = count_words(definition)
        if n > WORD_LIMIT:
            findings.append(("L1", ent.path, dline,
                             "overlong definition: %d words (limit %d)"
                             % (n, WORD_LIMIT)))

    # -- L2 headword echo (prose tokens only, links excluded — A7)
    bans = ban_set(ent)
    prose = mask_wiring(strip_links(definition))
    echoed = []
    for tok in TOKEN_RE.findall(prose):
        t = norm_word(tok)
        if t in bans and t not in [norm_word(x) for x in echoed]:
            echoed.append(tok)
    for tok in sorted(set(norm_word(t) for t in echoed)):
        findings.append(("L2", ent.path, dline,
                         "headword echo: definition of '%s' uses banned token '%s'"
                         % (ent.slug, tok)))

    # -- L3: dangling definition links
    for tgt in sorted(set(LINK_RE.findall(definition))):
        if not reg.resolves(tgt):
            findings.append(("L3", ent.path, dline,
                             "dangling link [[%s]] in definition of '%s'"
                             % (tgt, ent.slug)))

    # -- L3: dangling see-also targets (navigational; must still resolve)
    sline = ent.field_lines.get("see-also", ent.line)
    for tgt in sorted(set(_listify(fields.get("see-also")))):
        if not reg.resolves(tgt):
            findings.append(("L3", ent.path, sline,
                             "dangling see-also target [[%s]] on '%s'"
                             % (tgt, ent.slug)))

    # -- L3: unlinked registered terms + unregistered jargon (closure), A10
    words = [t for t in TOKEN_RE.findall(prose)]
    seen_unlinked = set()
    seen_jargon = set()
    i = 0
    while i < len(words):
        matched = 0
        for n in (3, 2):
            if i + n <= len(words):
                gram = norm_term("-".join(norm_word(w) for w in words[i:i + n]))
                if gram in reg.registered_norms:
                    if not all(norm_word(w) in bans for w in words[i:i + n]) \
                            and gram not in seen_unlinked:
                        seen_unlinked.add(gram)
                        findings.append((
                            "L3", ent.path, dline,
                            "unlinked registered term '%s' in definition of "
                            "'%s' (write [[%s]])"
                            % (reg.display.get(gram, gram), ent.slug, gram)))
                    matched = n
                    break
        if matched:
            i += matched
            continue
        w = norm_word(words[i])
        i += 1
        if not w or w in bans:
            continue  # own headword: L2's jurisdiction (A7)
        wn = norm_term(w)
        hit = wn if wn in reg.registered_norms else reg.variant_to_norm.get(w)
        if hit:
            if hit not in seen_unlinked:
                seen_unlinked.add(hit)
                findings.append((
                    "L3", ent.path, dline,
                    "unlinked registered term '%s' in definition of '%s' "
                    "(write [[%s]])"
                    % (reg.display.get(hit, hit), ent.slug, hit)))
            continue
        if compound_plain(w, wordlist):
            continue
        if w not in seen_jargon:
            seen_jargon.add(w)
            findings.append((
                "L3", ent.path, dline,
                "unregistered jargon '%s' in definition of '%s' (not in "
                "grounding wordlist, no glossary/node target)" % (w, ent.slug)))

    # -- L5 / L5G entry-level acronym rule (A15/A16)
    term = str(fields.get("term", "") or "") or ent.slug
    acro = ACRO_RE.search(term)
    if acro:
        exp = str(fields.get("expansion", "") or "").strip()
        eline = ent.field_lines.get("expansion", ent.line)
        if not exp or exp in ("—", "-"):
            findings.append(("L5", ent.path, eline,
                             "unexpanded acronym headword '%s': expansion "
                             "missing or '—'" % acro.group(0)))
        elif exp.startswith(UNKNOWN_PREFIX):
            m = UNKNOWN_MARKER_RE.match(exp)
            if m:
                findings.append((
                    "L5G", ent.path, eline,
                    "WARN: acronym '%s' expansion unknown from corpus; "
                    "ask: %s" % (acro.group(0), m.group("route").strip())))
            else:
                findings.append((
                    "L5", ent.path, eline,
                    "unexpanded acronym headword '%s': malformed "
                    "UNKNOWN-FROM-CORPUS marker (need — <evidence> — ask: "
                    "<route>)" % acro.group(0)))

    # -- L6 wiring ban over definition: and object: (expansion exempt), A5
    for fname, text in (("definition", definition), ("object", objectline)):
        fl = ent.field_lines.get(fname, ent.line)
        hits = d_scan(text)
        for cls in sorted(hits):
            findings.append((
                "L6", ent.path, fl,
                "wiring token (%s) in %s: of '%s': '%s'"
                % (cls, fname, ent.slug, hits[cls])))


def check_l4(entries, reg, findings):
    """P4: exact SCCs (size >= 2) + self-links over definition-link edges."""
    slugs = sorted(e.slug for e in entries)
    by_slug = {e.slug: e for e in entries}
    edges = {}
    selfloop = set()
    for s in slugs:
        ent = by_slug[s]
        outs = []
        for tgt in LINK_RE.findall(str(ent.fields.get("definition", "") or "")):
            r = reg.resolve_entry(tgt)
            if r is None or r not in by_slug:
                continue
            if r == s:
                selfloop.add(s)
            elif r not in outs:
                outs.append(r)
        edges[s] = outs
    # iterative Tarjan
    index = {}
    low = {}
    onstack = {}
    stack = []
    counter = [0]
    sccs = []
    for root in slugs:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, pi = work.pop()
            if pi == 0:
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                onstack[v] = True
            recurse = False
            outs = edges[v]
            for j in range(pi, len(outs)):
                w = outs[j]
                if w not in index:
                    work.append((v, j + 1))
                    work.append((w, 0))
                    recurse = True
                    break
                elif onstack.get(w):
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    onstack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(sorted(comp))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    for comp in sorted(sccs):
        if len(comp) >= 2:
            first = comp[0]
            ent = by_slug[first]
            findings.append((
                "L4", ent.path, ent.field_lines.get("definition", ent.line),
                "circular definition cluster (%d entries): %s"
                % (len(comp), ", ".join(comp))))
    for s in sorted(selfloop):
        ent = by_slug[s]
        findings.append((
            "L4", ent.path, ent.field_lines.get("definition", ent.line),
            "self-referential definition link on '%s'" % s))


def check_node(node, reg, findings):
    """D1-D3 over definition subkeys; D4 completeness (incl. addendum §3.1
    reference form)."""
    if not node.has_def:
        findings.append(("D4", node.path, 1,
                         "definition block missing (no definition: in "
                         "frontmatter)"))
        return
    f = node.def_fields
    inline_ok = all(str(f.get(k, "") or "").strip()
                    for k in ("is", "object", "stakes"))
    ref = str(f.get("term", "") or "").strip()
    ref_ok = bool(ref) and str(f.get("stakes", "") or "").strip()
    if not inline_ok and not ref_ok:
        missing = [k for k in ("is", "object", "stakes")
                   if not str(f.get(k, "") or "").strip()]
        findings.append((
            "D4", node.path, node.def_line or 1,
            "definition block incomplete: missing %s" % ", ".join(missing)))
    if ref and not reg.resolves(ref):
        findings.append((
            "D4", node.path, node.def_field_lines.get("term", node.def_line or 1),
            "definition term reference '%s' does not resolve to a glossary "
            "entry" % ref))
    # D1-D3 wiring ban inside definition subkey text (parent §5, A5)
    for key in ("is", "object", "stakes"):
        text = str(f.get(key, "") or "")
        if not text:
            continue
        line = node.def_field_lines.get(key, node.def_line or 1)
        hits = d_scan(text)
        for cls in sorted(hits):
            findings.append((
                cls, node.path, line,
                "wiring token in definition.%s: '%s'" % (key, hits[cls])))


def acronym_registry(entries):
    """A12: acronym-shaped tokens in term: (slug fallback) -> entry."""
    out = {}
    for ent in sorted(entries, key=lambda e: e.slug):
        term = str(ent.fields.get("term", "") or "") or ent.slug
        for m in ACRO_RE.finditer(term):
            out.setdefault(m.group(0), ent)
    return out


GLOSSARY_MACHINERY_RE = re.compile(
    r"^(?:## |(?:term|expansion|definition|object|see-also|rejected):).*$",
    re.M)


def check_doc_acronyms(relpath, body, start_line, acros, is_glossary_md,
                       findings):
    """P5 doc mode: first use per document must sit in an expansion pattern."""
    if not body:
        return
    if is_glossary_md:  # A12: entry machinery is expanded-at-source
        body = GLOSSARY_MACHINERY_RE.sub(
            lambda m: " " * len(m.group(0)), body)
    for acro in sorted(acros):
        m = re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(acro),
                      body)
        if not m:
            continue
        s, e = m.start(), m.end()
        before, after = body[:s], body[e:]
        ok = False
        if re.search(r"\(\s*$", before) and re.match(r"\s*\)", after):
            ok = True                       # Full Form (ACRO)
        elif re.match(r"\s*\([^)\s][^)]*\)", after):
            ok = True                       # ACRO (full form / expansion unknown — …)
        elif is_glossary_md and before.rfind("[[") > before.rfind("]]"):
            ok = True                       # [[link]] inside GLOSSARY.md itself
        if not ok:
            line = start_line + body.count("\n", 0, s)
            findings.append((
                "L5", relpath, line,
                "first use of registered acronym '%s' not expanded (doc "
                "mode: need 'Full Form (%s)' or '%s (…)')"
                % (acro, acro, acro)))


def check_d5(entries, nodes, model_texts, reg, stoplist, findings):
    """D5: hyphenated coined term >= 3 occurrences across the §4 scan
    surface, unregistered (A14). WARN, one finding per term."""
    surfaces = []  # (path, line, text) in deterministic order
    for ent in sorted(entries, key=lambda e: (e.path, e.line, e.slug)):
        for fname in sorted(ent.fields):
            val = ent.fields[fname]
            val = " ".join(str(x) for x in val) if isinstance(val, list) \
                else str(val or "")
            surfaces.append((ent.path, ent.field_lines.get(fname, ent.line),
                             val))
    for nd in sorted(nodes, key=lambda n: n.path):
        for key in sorted(nd.def_fields):
            surfaces.append((nd.path,
                             nd.def_field_lines.get(key, nd.def_line or 1),
                             str(nd.def_fields[key] or "")))
        if nd.summary:
            surfaces.append((nd.path, nd.summary_line or 1, nd.summary))
    for relpath, text in sorted(model_texts):
        for i, ln in enumerate(text.splitlines()):
            surfaces.append((relpath, i + 1, ln))
    counts = {}
    first = {}
    order = 0
    for path, line, text in surfaces:
        order += 1
        for m in HYPH_CAND_RE.finditer(text or ""):
            cand = m.group(0).lower()
            if cand in stoplist:
                continue
            counts[cand] = counts.get(cand, 0) + 1
            key = (path, line, order, m.start())
            if cand not in first or key < first[cand]:
                first[cand] = key
    for cand in sorted(counts):
        if counts[cand] < D5_THRESHOLD:
            continue
        n = norm_term(cand)
        if n in reg.registered_norms or reg.variant_to_norm.get(n):
            continue
        path, line = first[cand][0], first[cand][1]
        findings.append((
            "D5", path, line,
            "WARN: coined term '%s' used %dx across model surfaces, "
            "unregistered (needs a GLOSSARY entry)" % (cand, counts[cand])))

# ---------------------------------------------------------------------- main


def run(corpus_dir):
    corpus_dir = os.path.abspath(corpus_dir)
    if not os.path.isdir(corpus_dir):
        sys.stderr.write("lexicon_lint: not a directory: %s\n" % corpus_dir)
        return 2
    script_dir = os.path.dirname(os.path.abspath(__file__))
    findings = []  # (class, relpath, line, detail)

    wordlist = load_wordfile([
        os.path.join(corpus_dir, "grounding-wordlist.txt"),
        os.path.join(script_dir, "grounding-wordlist.txt")])
    if wordlist is None:
        wordlist = set(_FALLBACK_WORDS.split())
    stoplist = set(D5_STOPLIST)
    extra = load_wordfile([os.path.join(corpus_dir, "d5-stoplist.txt"),
                           os.path.join(script_dir, "d5-stoplist.txt")])
    if extra:
        stoplist |= extra

    # deterministic file walk
    relpaths = []
    for root, dirs, files in os.walk(corpus_dir):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            relpaths.append(os.path.relpath(p, corpus_dir))
    relpaths.sort()

    entries = []
    nodes = []
    docs = []         # (relpath, body, start_line, is_glossary_md)
    model_texts = []  # (relpath, text) for the D5 model.md surface
    seen_slugs = {}
    for rp in relpaths:
        full = os.path.join(corpus_dir, rp)
        base = os.path.basename(rp)
        low = base.lower()
        if low in ("grounding-wordlist.txt", "d5-stoplist.txt"):
            continue
        try:
            with open(full, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, IOError):
            continue
        if low.endswith(".json"):
            for ent in parse_glossary_json(rp, text, findings):
                key = norm_term(ent.slug)
                if key in seen_slugs:
                    findings.append((
                        "L3", ent.path, ent.line,
                        "unparseable entry: duplicate slug '%s' (first at "
                        "%s:%d)" % (ent.slug, seen_slugs[key][0],
                                    seen_slugs[key][1])))
                else:
                    seen_slugs[key] = (ent.path, ent.line)
                    entries.append(ent)
            continue
        if not low.endswith(".md"):
            continue
        if "glossary" in low:
            for ent in parse_glossary_md(rp, text, findings):
                key = norm_term(ent.slug)
                if key in seen_slugs:
                    findings.append((
                        "L3", ent.path, ent.line,
                        "unparseable entry: duplicate slug '%s' (first at "
                        "%s:%d)" % (ent.slug, seen_slugs[key][0],
                                    seen_slugs[key][1])))
                else:
                    seen_slugs[key] = (ent.path, ent.line)
                    entries.append(ent)
            docs.append((rp, text, 1, True))
            continue
        if low == "model.md":
            model_texts.append((rp, text))
            docs.append((rp, text, 1, False))
            continue
        node = parse_node_md(rp, text)
        if node is None:
            docs.append((rp, text, 1, False))
        else:
            nodes.append(node)
            docs.append((rp, node.body, node.body_start, False))

    reg = Registry(entries, nodes)

    for ent in sorted(entries, key=lambda e: (e.path, e.line, e.slug)):
        check_entry_local(ent, reg, wordlist, findings)
    check_l4(entries, reg, findings)
    for node in sorted(nodes, key=lambda n: n.path):
        check_node(node, reg, findings)
    acros = acronym_registry(entries)
    if acros:
        for rp, body, start, is_gmd in sorted(docs):
            check_doc_acronyms(rp, body, start, acros, is_gmd, findings)
    check_d5(entries, nodes, model_texts, reg, stoplist, findings)

    lines = sorted("%s\t%s:%d\t%s" % (cls, path, line, detail)
                   for cls, path, line, detail in set(findings))
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
    fail = any(ln.split("\t", 1)[0] in FAIL_CLASSES for ln in lines)
    return 1 if fail else 0


def self_test():
    """Conformance checks for the AMENDED D1 slash leg (A18), the landed
    grounding wordlist + closure (A19), and the amended P3 morphological
    normalization (A20). One PASS/FAIL line per check; exit 0 iff all
    pass."""
    checks = []

    # A18: person/role-shaped prose alternation is silent.
    hits = d_scan("Any person/role pair may hold the pen for one round.")
    checks.append(("person/role prose alternation silent", not hits))

    # A18: dot-bearing-segment path fires D1 (and only D1).
    hits = d_scan("The board compiles via scripts/model-to-board.py "
                  "each round.")
    checks.append(("scripts/model-to-board.py fires D1",
                   hits == {"D1": "scripts/model-to-board.py"}))

    # A18: two-slash all-alpha token fires D1 (>= 2 slashes).
    hits = d_scan("Keep the packet under kitchen/pass/rail until called.")
    checks.append(("two-slash all-alpha token fires D1",
                   hits == {"D1": "kitchen/pass/rail"}))

    # A18: extension and :// legs unchanged.
    hits = d_scan("Stored as fleet-status.json on the shelf.")
    checks.append(("extension leg unchanged (fleet-status.json fires D1)",
                   hits.get("D1") == "fleet-status.json"))
    hits = d_scan("See https://example.org/x for the shelf.")
    checks.append((":// leg unchanged (URL fires D1)",
                   hits.get("D1") == "https://example.org/x"))

    # A19: the beside-script wordlist leg resolves; the embedded fallback
    # (a few hundred words) does not engage.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    wordlist = load_wordfile(
        [os.path.join(script_dir, "grounding-wordlist.txt")])
    checks.append(("grounding-wordlist.txt resolves beside script "
                   "(>5000 words, fallback disengaged)",
                   bool(wordlist) and len(wordlist) > 5000))

    # A19: wordlist-loaded closure — plain-English line passes...
    if wordlist:
        ent = Entry("widget", "self-test", 1)
        ent.fields = {"term": "widget", "expansion": "—",
                      "definition": "A small tool that helps people finish "
                                    "simple work quickly.",
                      "object": "A small tool kept on the bench.",
                      "see-also": "—"}
        f1 = []
        check_entry_local(ent, Registry([ent], []), wordlist, f1)
        checks.append(("closure passes a plain-English line", not f1))

        # ...and flags a jargon coinage (L3 unregistered jargon), only it.
        ent2 = Entry("sorter", "self-test", 9)
        ent2.fields = {"term": "sorter", "expansion": "—",
                       "definition": "A machine that runs the flembotron "
                                     "over daily work.",
                       "object": "A machine on the bench.",
                       "see-also": "—"}
        f2 = []
        check_entry_local(ent2, Registry([ent2], []), wordlist, f2)
        checks.append(("closure flags jargon coinage 'flembotron'",
                       len(f2) == 1 and f2[0][0] == "L3" and
                       "flembotron" in f2[0][3]))

        # A20: amended P3 morphological normalization.
        checks.append(("A20 'quietly' plain (-ly adverb of in-list base)",
                       is_plain("quietly", wordlist)))
        checks.append(("A20 'cannot' plain (closed compound can + not)",
                       is_plain("cannot", wordlist)))
        checks.append(("A20 'happily' plain (-ily -> y base restore)",
                       is_plain("happily", wordlist)))
        checks.append(("A20 verb stems still plain (manages, publishes)",
                       is_plain("manages", wordlist) and
                       is_plain("publishes", wordlist)))

        # A20: a coined -ly word off an out-of-list base is NOT rescued.
        ent3 = Entry("whirler", "self-test", 17)
        ent3.fields = {"term": "whirler", "expansion": "—",
                       "definition": "A machine that runs flembotronly "
                                     "over daily work.",
                       "object": "A machine on the bench.",
                       "see-also": "—"}
        f3 = []
        check_entry_local(ent3, Registry([ent3], []), wordlist, f3)
        checks.append(("A20 coined -ly word 'flembotronly' still fires L3",
                       len(f3) == 1 and f3[0][0] == "L3" and
                       "flembotronly" in f3[0][3]))
    else:
        checks.append(("closure passes a plain-English line", False))
        checks.append(("closure flags jargon coinage 'flembotron'", False))
        checks.append(("A20 'quietly' plain (-ly adverb of in-list base)",
                       False))
        checks.append(("A20 'cannot' plain (closed compound can + not)",
                       False))
        checks.append(("A20 'happily' plain (-ily -> y base restore)",
                       False))
        checks.append(("A20 verb stems still plain (manages, publishes)",
                       False))
        checks.append(("A20 coined -ly word 'flembotronly' still fires L3",
                       False))

    ok = True
    for name, passed in checks:
        sys.stdout.write("%s\t%s\n" % ("PASS" if passed else "FAIL", name))
        ok = ok and passed
    return 0 if ok else 1


def main(argv):
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 2:
        sys.stderr.write("usage: lexicon_lint.py <corpus-dir> | --self-test\n")
        return 2
    return run(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
