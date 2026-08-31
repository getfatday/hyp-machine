#!/usr/bin/env python3
"""prior-art-sweep.py — FIRST CUT (corpus program, staged 2026-08-29).

Advisory prior-art sweep for a draft hypothesis spec. Matches the draft's text
(title + slug + Hypothesis + Motivation) against the findings index
(research/findings-index.md) and every registered spec title, and emits:

  1. typed OVERLAP flags for priors above the pinned threshold (near-duplicates),
     with lineage-declared ancestors classed LINEAGE instead of OVERLAP;
  2. a ranked relevant-priors list (top K) with classes
     PRIOR-KEEP | PRIOR-NULL | LINEAGE | PRIOR-RETIRED | PRIOR-OPEN | OVERLAP;
  3. a ready-to-paste "## Prior work" section candidate carrying each cited
     prior's verdict and date from the index.

ADVISORY CONTRACT: always exits 0; never blocks; read-only over the repo (writes
only --emit). DETERMINISM: pure text similarity — idf-weighted token coverage
(each prior's signature = slug + title + Hypothesis section + index finding text;
idf computed over the prior corpus itself), stable sort (score desc, id asc);
re-runs over identical inputs are byte-identical.

Usage:
  prior-art-sweep.py <draft.md> [--repo PATH] [--index PATH] [--top K]
                     [--threshold F] [--section-floor F] [--emit PATH]
"""
import argparse
import glob
import os
import re
import sys

DEFAULT_THRESHOLD = 0.25     # OVERLAP flag — calibrated on the preview pair (near-dup 0.37,
                             # highest non-target 0.12, novel-stub top 0.09); the counted
                             # lane freezes this constant, it is never tuned per run
DEFAULT_SECTION_FLOOR = 0.15 # a prior enters the section candidate at or above this
DEFAULT_TOP_K = 5
DF_STOP_FRACTION = 0.50      # tokens in > half the prior signatures are pure boilerplate

STOPWORDS = set("""
a an and are as at be been being both but by can do does each else for from has
have if in into is it its no nor not of on one only or other over per so than
that the their them then there these this those to two under until via was we
were what when where which while will with without zero
""".split())

TOKEN_RE = re.compile(r"[a-z][a-z0-9_.-]{2,}")
LINEAGE_DECL_RE = re.compile(
    r"(?:refin\w+\s+from|refines|successor(?:\s+\w+)?\s+(?:to|of)|v\d+\s+of)\s+(H-\d+)", re.I)


def tokens(text):
    return set(t.strip(".-") for t in TOKEN_RE.findall(text.lower())) - STOPWORDS


def norm_ws(s):
    return re.sub(r"\s+", " ", s).strip()


def section(text, name):
    m = re.search(r"^## %s\s*\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.M | re.S)
    return m.group(1) if m else ""


def load_index(index_path):
    """Parse findings-index.md table rows -> {id: {verdict, date, finding, evidence, successor}}."""
    rows = {}
    if not index_path or not os.path.exists(index_path):
        return rows
    current = None
    for line in open(index_path, encoding="utf-8"):
        h = re.match(r"^## (\w+)", line)
        if h:
            current = {"Kept": "kept", "Discarded": "discarded",
                       "Lineage": "refined", "Retired": "retired"}.get(h.group(1))
            continue
        m = re.match(r"^\|\s*(H-\d+)\s*\|(.*)\|\s*$", line)
        if not m or current is None:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        rid = m.group(1)
        if current == "refined" and len(cells) >= 3:
            rows[rid] = {"verdict": "refined-into: " + cells[0], "successor": cells[0],
                         "date": cells[1], "finding": cells[2], "evidence": ""}
        elif len(cells) >= 3:
            rows[rid] = {"verdict": current, "successor": "",
                         "date": cells[0], "finding": cells[1], "evidence": cells[2]}
    return rows


def load_spec_titles(repo):
    """{id: (slug, title, hypothesis-section)} from hypotheses/H-*.md."""
    out = {}
    for p in sorted(glob.glob(os.path.join(repo, "hypotheses", "H-*.md"))):
        m = re.match(r"H-(\d+)-(.+)\.md$", os.path.basename(p))
        if not m:
            continue
        rid = "H-" + m.group(1)
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            text = ""
        first = text.splitlines()[0] if text else ""
        tm = re.match(r"#\s*H-\d+-[^:]+:\s*(.+)", first)
        out[rid] = (m.group(2).replace("-", " "),
                    norm_ws(tm.group(1)) if tm else "",
                    section(text, "Hypothesis"))
    return out


def lineage_closure(declared, index_rows):
    """Declared ancestors plus everything reachable over refined-into edges (both ways)."""
    fwd = {rid: r["successor"] for rid, r in index_rows.items() if r.get("successor")}
    rev = {}
    for a, b in fwd.items():
        rev.setdefault(b, set()).add(a)
    seen, todo = set(), list(declared)
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        if x in fwd:
            todo.append(fwd[x])
        todo.extend(rev.get(x, ()))
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--index", default=None,
                    help="findings-index.md path (default: <repo>/research/findings-index.md)")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--section-floor", type=float, default=DEFAULT_SECTION_FLOOR)
    ap.add_argument("--emit", default=None, help="write the Prior-work section candidate here")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    index_path = args.index or os.path.join(repo, "research", "findings-index.md")
    index_rows = load_index(index_path)
    titles = load_spec_titles(repo)

    draft_text = open(args.draft, encoding="utf-8").read()
    draft_base = os.path.basename(args.draft)
    draft_id_m = re.match(r"(H-(?:DRAFT|\d+))-(.+)\.md$", draft_base)
    draft_slug = draft_id_m.group(2).replace("-", " ") if draft_id_m else ""
    first = draft_text.splitlines()[0] if draft_text else ""
    draft_title = first.split(":", 1)[1] if ":" in first else first.lstrip("# ")
    draft_sig = tokens(" ".join([draft_title, draft_slug,
                                 section(draft_text, "Hypothesis"),
                                 section(draft_text, "Motivation")]))

    # prior signatures: slug + title + Hypothesis section (+ index finding text)
    sigs = {}
    for rid, (slug, title, hyp) in sorted(titles.items()):
        parts = [slug, title, hyp]
        if rid in index_rows:
            parts.append(index_rows[rid]["finding"])
        sigs[rid] = tokens(" ".join(parts))
    # never match the draft against its own registered self
    if draft_id_m and draft_id_m.group(1) in sigs:
        del sigs[draft_id_m.group(1)]

    # corpus-derived weighting (deterministic): idf over prior signatures;
    # tokens in more than half of all priors are pure boilerplate and dropped.
    import math
    df = {}
    for s in sigs.values():
        for t in s:
            df[t] = df.get(t, 0) + 1
    n_docs = max(len(sigs), 1)
    boiler = {t for t, c in df.items() if c / n_docs > DF_STOP_FRACTION}
    draft_sig -= boiler
    sigs = {rid: (s - boiler) for rid, s in sigs.items()}

    def idf(t):
        return math.log(n_docs / df.get(t, 1))

    def mass(toks):
        return sum(idf(t) for t in toks)

    declared = set(LINEAGE_DECL_RE.findall(draft_text))
    lineage = lineage_closure(declared, index_rows) if declared else set()

    # score = idf-weighted coverage of the smaller signature by the intersection
    scored = []
    dmass = mass(draft_sig)
    for rid, sig in sorted(sigs.items()):
        if not sig or not draft_sig:
            continue
        inter = draft_sig & sig
        denom = min(dmass, mass(sig))
        score = (mass(inter) / denom) if denom else 0.0
        scored.append((rid, score, sorted(inter, key=lambda t: (-idf(t), t))))
    scored.sort(key=lambda x: (-x[1], x[0]))

    def klass(rid, score):
        if rid in lineage:
            return "LINEAGE"
        if score >= args.threshold:
            return "OVERLAP"
        row = index_rows.get(rid)
        if not row:
            return "PRIOR-OPEN"
        v = row["verdict"]
        if v == "kept":
            return "PRIOR-KEEP"
        if v == "discarded":
            return "PRIOR-NULL"
        if v == "retired":
            return "PRIOR-RETIRED"
        return "LINEAGE"

    out = []
    out.append("prior-art-sweep (advisory) — draft: %s" % draft_base)
    out.append("corpus: %d spec titles, %d resolved index rows (%s)"
               % (len(titles), len(index_rows),
                  os.path.relpath(index_path, repo) if os.path.exists(index_path) else "index missing"))
    out.append("pinned: threshold=%.2f top=%d section-floor=%.2f df-stop=%.2f"
               % (args.threshold, args.top, args.section_floor, DF_STOP_FRACTION))
    out.append("")

    overlaps = [(rid, sc, ov) for rid, sc, ov in scored
                if sc >= args.threshold and rid not in lineage]
    for rid, sc, ov in overlaps:
        out.append("OVERLAP %s score=%.2f — near-duplicate of a registered/resolved spec; "
                   "shared: %s" % (rid, sc, ", ".join(ov[:12])))
    lineage_hits = [(rid, sc) for rid, sc, _ in scored if rid in lineage and sc >= args.section_floor]
    for rid, sc in lineage_hits:
        out.append("LINEAGE %s score=%.2f — declared refine ancestry; not an overlap" % (rid, sc))
    if not overlaps:
        non_lineage = [sc for rid, sc, _ in scored if rid not in lineage]
        top_score = non_lineage[0] if non_lineage else 0.0
        out.append("CLEAN — no overlap at threshold %.2f (top non-lineage score %.2f)"
                   % (args.threshold, top_score))
    out.append("")

    out.append("relevant priors (top %d):" % args.top)
    top = scored[: args.top]
    if not top:
        out.append("  none found — the findings index is empty and no spec titles matched")
    for rid, sc, _ in top:
        row = index_rows.get(rid, {})
        out.append("  %-12s %s score=%.2f (%s%s) %s"
                   % (klass(rid, sc), rid, sc,
                      row.get("verdict", "open"),
                      (", " + row["date"]) if row.get("date") and row["date"] != "—" else "",
                      titles.get(rid, ("", ""))[1][:90]))
    out.append("")

    sec = ["## Prior work",
           "<!-- Candidate emitted by scripts/prior-art-sweep.py (advisory) from the",
           "     findings index — verify each line before registration; delete rows that",
           "     do not actually bear on this spec. -->"]
    cited = [(rid, sc) for rid, sc, _ in top if sc >= args.section_floor]
    if not cited:
        sec.append("- none surfaced (sweep over %d priors; top score %.2f below floor %.2f)"
                   % (len(sigs), (scored[0][1] if scored else 0.0), args.section_floor))
    for rid, sc in cited:
        row = index_rows.get(rid)
        k = klass(rid, sc)
        if row:
            body = "%s (%s%s): %s" % (rid, row["verdict"],
                                      (", " + row["date"]) if row.get("date") and row["date"] != "—" else "",
                                      row["finding"])
            if row.get("evidence"):
                body += " — evidence: " + row["evidence"]
        else:
            body = "%s (open — registered, unresolved): %s" % (rid, titles.get(rid, ("", ""))[1])
        prefix = {"OVERLAP": "OVERLAP — reconcile before registering: ",
                  "LINEAGE": "Refines ", "PRIOR-NULL": "Banked null — do not re-propose: ",
                  "PRIOR-KEEP": "Builds on "}.get(k, "See ")
        sec.append("- " + prefix + body)
    out.extend(sec)

    report = "\n".join(out) + "\n"
    sys.stdout.write(report)
    if args.emit:
        with open(args.emit, "w", encoding="utf-8") as f:
            f.write("\n".join(sec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
