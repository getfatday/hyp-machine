#!/usr/bin/env python3
"""direction-lint.py -- H-243 deterministic direction-currency lint.

Scans a "direction-layer corpus" (a root directory) for five drift classes and
prints one finding per line, then exits 0 (clean) or 1 (findings present).

Usage:
    python3 direction-lint.py <corpus-root>

Corpus shape the lint understands (all optional; absent inputs simply yield no
findings for the classes that read them):
  <root>/**/*.md            prose files (North Star paragraphs, provenance notes)
  <root>/**/spec-status/*.md  one hypothesis-style "## Status" block per file;
                             filename stem (no extension) is the id (H-NNN or
                             HF-NN). Status line grammar (matches
                             hypotheses/TEMPLATE.md): draft | active | kept |
                             discarded | refined-into: <ID>
  <root>/ledger-rows.jsonl   one JSON object per line; recognized fields:
                             "pointer" (a citation path, resolved relative to
                             <root>) and "note" (free text, scanned the same
                             way prose paragraphs are)
  <root>/rename-manifest.json  {"renames": [{"old": "...", "new": "..."}, ...]}

Classes emitted (first column of every finding line):
  UNRESOLVABLE-CITATION    a backtick-quoted relative path (or a ledger row's
                            "pointer" field) that does not resolve to a file
                            under <root>.
  STALE-VERDICT-ECHO       prose caches a hypothesis's resolved/pending state
                            (id "awaits builders", "gated on ... keeps (ids)",
                            bare "... kept", or the compound "refined into
                            <ID>, kept") that contradicts the status registry
                            built from spec-status/*.md.
  RENAMED-TERM-HIT         an old term from rename-manifest.json appears
                            without its new term + the word "now" anywhere in
                            the same paragraph (the safe rename-gloss idiom:
                            "originally called X ... is now Y").
  SUPERSESSION-CYCLE       the directed graph of refined-into pointers (built
                            from the status registry) contains a cycle.
  EPHEMERAL-PATH-PROVENANCE  a citation-shaped path rooted under a known
                            non-durable location (/private/tmp, /tmp,
                            /var/folders, or a ~/.claude/jobs/**/tmp path).

Output line grammar (tab-separated, per the frozen Method contract):
    DIRECTION-LINT<TAB><CLASS><TAB><file><TAB><line><TAB><referent>
Lines are sorted by (file, line, class, referent) so output is a pure function
of corpus content -- no timestamps, no filesystem-order dependence.

Exit codes: 0 clean, 1 findings present, 2 usage/corpus error.
"""
import json
import os
import re
import sys

STATUS_WORDS = ("kept", "discarded", "active", "draft")
EPHEMERAL_RE = re.compile(
    r"(?:/private/tmp|/tmp|/var/folders|/Users/[A-Za-z0-9_.-]+/\.claude/jobs"
    r"|~/\.claude/jobs)/[^\s`)\]\"]*"
)
CITATION_RE = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)`")
ID_RE = re.compile(r"\b(H-\d+|HF-\d+)\b")
P3_RE = re.compile(
    r"\b(H-\d+|HF-\d+)\b(?:(?!\.).)*?\brefined into\s+(H-\d+|HF-\d+)\b,?\s*kept",
    re.IGNORECASE,
)
AWAITS_RE = re.compile(r"\bawaits builders\b", re.IGNORECASE)
GATED_RE = re.compile(r"\bgated on\b", re.IGNORECASE)
KEEPS_RE = re.compile(r"\bkeeps?\b", re.IGNORECASE)
KEPT_RE = re.compile(r"\bkept\b", re.IGNORECASE)
NOW_RE = re.compile(r"\bnow\b", re.IGNORECASE)


class Finding(object):
    __slots__ = ("cls", "file", "line", "referent")

    def __init__(self, cls, file, line, referent):
        self.cls = cls
        self.file = file
        self.line = line
        self.referent = referent

    def key(self):
        return (self.file, self.line, self.cls, self.referent)

    def render(self):
        return "DIRECTION-LINT\t%s\t%s\t%d\t%s" % (
            self.cls, self.file, self.line, self.referent)


def rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def walk_md_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return out


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def split_paragraphs(lines):
    """[(start_line_1based, [line, ...]), ...] split on blank lines."""
    paras = []
    cur = []
    cur_start = 1
    for i, ln in enumerate(lines, start=1):
        if ln.strip() == "":
            if cur:
                paras.append((cur_start, cur))
                cur = []
        else:
            if not cur:
                cur_start = i
            cur.append(ln)
    if cur:
        paras.append((cur_start, cur))
    return paras


def line_of(lines_in_para, start_line, needle_re):
    for off, ln in enumerate(lines_in_para):
        if needle_re.search(ln):
            return start_line + off
    return start_line


# ---------------------------------------------------------------------------
# Status registry (class STALE-VERDICT-ECHO ground truth + SUPERSESSION-CYCLE
# graph edges)
# ---------------------------------------------------------------------------
def build_status_registry(root):
    """id -> {"bucket": one of kept/discarded/active/draft/refined/unknown,
              "target": id or None, "file": relpath, "line": int}"""
    reg = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if os.path.basename(dirpath) != "spec-status":
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            hid = fn[:-3]
            p = os.path.join(dirpath, fn)
            lines = read_lines(p)
            bucket, target, line_no = "unknown", None, 1
            for i, ln in enumerate(lines, start=1):
                if ln.strip() == "## Status":
                    for j in range(i, len(lines)):
                        cand = lines[j].strip()
                        if not cand:
                            continue
                        line_no = j + 1
                        m = re.match(
                            r"^refined-into:\s*(H-\d+|HF-\d+)\b", cand)
                        if m:
                            bucket, target = "refined", m.group(1)
                        else:
                            m2 = re.match(
                                r"^(kept|discarded|active|draft)\b", cand)
                            if m2:
                                bucket = m2.group(1)
                        break
                    break
            reg[hid] = {"bucket": bucket, "target": target,
                        "file": rel(root, p), "line": line_no}
    return reg


def find_cycles(reg):
    """Return a list of cycles (each a list of ids, first==last omitted,
    in walk order) among refined-into edges. Dedup by frozenset of members."""
    edges = {hid: v["target"] for hid, v in reg.items()
             if v["bucket"] == "refined" and v.get("target")}
    seen_cycles = set()
    cycles = []
    for start in sorted(edges):
        path = []
        seen_local = set()
        node = start
        while node in edges and node not in seen_local:
            seen_local.add(node)
            path.append(node)
            node = edges[node]
        if node in path:
            idx = path.index(node)
            cyc = path[idx:]
            fs = frozenset(cyc)
            if fs not in seen_cycles:
                seen_cycles.add(fs)
                cycles.append(cyc)
    return cycles


# ---------------------------------------------------------------------------
# Per-class scanners
# ---------------------------------------------------------------------------
def scan_citations(root, relfile, start_line, lines, out):
    for off, ln in enumerate(lines):
        for m in CITATION_RE.finditer(ln):
            path = m.group(1)
            if not os.path.isfile(os.path.join(root, path)):
                out.append(Finding("UNRESOLVABLE-CITATION", relfile,
                                    start_line + off, path))


def scan_ephemeral(relfile, start_line, lines, out):
    for off, ln in enumerate(lines):
        for m in EPHEMERAL_RE.finditer(ln):
            path = m.group(0).rstrip(".,;:)")
            out.append(Finding("EPHEMERAL-PATH-PROVENANCE", relfile,
                                start_line + off, path))


def scan_renamed_terms(relfile, start_line, lines, renames, out):
    joined = " ".join(lines)
    for pair in renames:
        old, new = pair["old"], pair["new"]
        old_re = re.compile(r"\b" + re.escape(old) + r"\b")
        if not old_re.search(joined):
            continue
        safe = (new in joined) and bool(NOW_RE.search(joined))
        if safe:
            continue
        ln_no = line_of(lines, start_line, old_re)
        out.append(Finding("RENAMED-TERM-HIT", relfile, ln_no, old))


def scan_verdict_echo(relfile, start_line, lines, registry, out):
    joined = " ".join(lines)
    ids = ID_RE.findall(joined)
    if not ids:
        return

    def bucket_of(hid):
        return registry.get(hid, {}).get("bucket", "unknown")

    # No-ground-truth rule: an id with no spec-status entry is UNKNOWN, not
    # evidence of drift -- absence of a registry row must never be treated
    # as a mismatch (that would make an incomplete corpus a false-positive
    # generator). Every branch below skips unknown ids rather than flagging
    # them.

    m3 = P3_RE.search(joined)
    if m3:
        subject, successor = m3.group(1), m3.group(2)
        reg = registry.get(subject)
        if reg is None or bucket_of(successor) == "unknown":
            return
        ok = (reg.get("bucket") == "refined"
              and reg.get("target") == successor
              and bucket_of(successor) == "kept")
        if not ok:
            ln_no = line_of(lines, start_line, ID_RE)
            out.append(Finding("STALE-VERDICT-ECHO", relfile, ln_no,
                                "%s->%s" % (subject, successor)))
        return

    if AWAITS_RE.search(joined):
        for hid in sorted(set(ids)):
            b = bucket_of(hid)
            if b == "unknown":
                continue
            if b != "active" and b != "draft":
                ln_no = line_of(lines, start_line, ID_RE)
                out.append(Finding("STALE-VERDICT-ECHO", relfile, ln_no, hid))
        return

    if GATED_RE.search(joined) and KEEPS_RE.search(joined):
        for hid in sorted(set(ids)):
            b = bucket_of(hid)
            if b == "unknown":
                continue
            if b not in ("active", "draft"):
                ln_no = line_of(lines, start_line, ID_RE)
                out.append(Finding("STALE-VERDICT-ECHO", relfile, ln_no, hid))
        return

    if KEPT_RE.search(joined):
        for hid in sorted(set(ids)):
            b = bucket_of(hid)
            if b == "unknown":
                continue
            if b != "kept":
                ln_no = line_of(lines, start_line, ID_RE)
                out.append(Finding("STALE-VERDICT-ECHO", relfile, ln_no, hid))
        return


def scan_ledger(root, registry, renames, out):
    path = os.path.join(root, "ledger-rows.jsonl")
    if not os.path.isfile(path):
        return
    relfile = "ledger-rows.jsonl"
    with open(path, encoding="utf-8") as fh:
        for i, raw in enumerate(fh, start=1):
            raw = raw.rstrip("\n")
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            pointer = row.get("pointer")
            if isinstance(pointer, str) and pointer:
                if not os.path.isfile(os.path.join(root, pointer)):
                    out.append(Finding("UNRESOLVABLE-CITATION", relfile, i,
                                        pointer))
                for m in EPHEMERAL_RE.finditer(pointer):
                    out.append(Finding(
                        "EPHEMERAL-PATH-PROVENANCE", relfile, i,
                        m.group(0).rstrip(".,;:)")))
            note = row.get("note")
            if isinstance(note, str) and note:
                scan_ephemeral(relfile, i, [note], out)
                scan_renamed_terms(relfile, i, [note], renames, out)
                scan_verdict_echo(relfile, i, [note], registry, out)


# ---------------------------------------------------------------------------
def load_renames(root):
    path = os.path.join(root, "rename-manifest.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    renames = obj.get("renames", [])
    out = []
    for r in renames:
        if isinstance(r, dict) and isinstance(r.get("old"), str) \
                and isinstance(r.get("new"), str):
            out.append(r)
    return out


def lint(root):
    out = []
    registry = build_status_registry(root)
    renames = load_renames(root)

    for p in walk_md_files(root):
        relfile = rel(root, p)
        lines = read_lines(p)
        scan_citations(root, relfile, 1, lines, out)
        for start_line, para_lines in split_paragraphs(lines):
            scan_ephemeral(relfile, start_line, para_lines, out)
            scan_renamed_terms(relfile, start_line, para_lines, renames, out)
            scan_verdict_echo(relfile, start_line, para_lines, registry, out)

    scan_ledger(root, registry, renames, out)

    for cyc in find_cycles(registry):
        lead = sorted(cyc)[0]
        chain = cyc + [cyc[0]]
        out.append(Finding("SUPERSESSION-CYCLE", registry[lead]["file"],
                            registry[lead]["line"], " -> ".join(chain)))

    seen = set()
    uniq = []
    for f in out:
        k = f.key()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    uniq.sort(key=lambda f: f.key())
    return uniq


def main(argv):
    if len(argv) != 1:
        sys.stderr.write("usage: direction-lint.py <corpus-root>\n")
        return 2
    root = argv[0]
    if not os.path.isdir(root):
        sys.stderr.write("REFUSE: corpus root is not a directory: %s\n" % root)
        return 2
    findings = lint(root)
    for f in findings:
        print(f.render())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
