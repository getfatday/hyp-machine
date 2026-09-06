#!/usr/bin/env python3
"""compile-findings-index.py — FIRST CUT (corpus program, staged 2026-08-29).

Compiles the findings index read-model: walks hypotheses/H-*.md Status lines,
Status-comment resolution notes, Runs tables, and Verdict footers, and emits ONE
line per resolved spec — id, verdict, date, the plain finding, evidence pointer —
plus lineage edges, so a session (or the prior-art sweep) can answer "has the lab
already tried this?" without re-reading 200 specs.

Land target: research/findings-index.md (a GENERATED read-model, regenerated after
any verdict flip; cataloged in research/index.md at land). This staged copy writes
ONLY to --out (default stdout) and reads the repo strictly read-only.

Determinism: output derives from file bytes alone — no timestamps, no environment.
Re-running over the same tree is byte-identical. HEAD sha is included when git
resolves one (deterministic for a fixed checkout; omitted silently otherwise).

Usage:
  compile-findings-index.py [--repo PATH] [--out PATH] [--hypotheses-dir NAME]

Exit 0 always (advisory read-model compiler; malformed specs are reported in-band
as PARSE-GAP rows rather than crashing the compile).
"""
import argparse
import glob
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "hooks", "scripts"))
from hyp_status import canonical_status  # noqa: E402  (shared status reader)

# CANONICAL status word (hooks/scripts/hyp_status.py) -> verdict family. Anything
# not listed is OPEN (not indexed); a bare `refined` canonicalizes to refine (open);
# `discarded-with-findings` canonicalizes to discarded (qualifier rule).
RESOLVED_FAMILIES = {
    "kept": "kept",
    "discarded": "discarded",
    "refined-into": "refined",
    "retired": "retired",
    "retired-by-design-review;": "retired",
}

FINDING_MARKERS = [r"FINDINGS?\s+BANKED:\s*", r"FINDING:\s*"]
MAX_FINDING_CHARS = 220


def norm_ws(s):
    return re.sub(r"\s+", " ", s).strip()


def first_sentences(text, limit=MAX_FINDING_CHARS):
    """Deterministic prefix: whole sentences until the limit, else word-boundary cut."""
    text = norm_ws(text)
    if len(text) <= limit:
        return text
    out = ""
    for m in re.finditer(r".*?[.!?](?:\s|$)", text):
        cand = (out + m.group(0)).strip()
        if len(cand) > limit:
            break
        out = cand
    if out:
        return out
    cut = text[:limit]
    return cut[: cut.rfind(" ")] + " ..."


def parse_spec(path):
    text = open(path, encoding="utf-8").read()
    base = os.path.basename(path)
    m = re.match(r"H-(\d+)-(.+)\.md$", base)
    if not m:
        return None
    rec = {
        "id": "H-%s" % m.group(1),
        "num": int(m.group(1)),
        "slug": m.group(2),
        "path": "hypotheses/" + base,
        "title": "",
        "status_word": "",
        "family": "open",
        "date": "",
        "finding": "",
        "evidence": "",
        "refined_into": "",
        "runs": 0,
        "verdict_footer": "",
        "parse_gap": [],
    }
    tm = re.match(r"#\s*H-\d+-[^:]+:\s*(.+)", text.splitlines()[0]) if text else None
    if tm:
        rec["title"] = norm_ws(tm.group(1))
    else:
        rec["parse_gap"].append("title")

    sm = re.search(r"^## Status\s*\n(.*?)(?=^## )", text, re.M | re.S)
    if not sm:
        rec["parse_gap"].append("status-section")
        return rec
    status_block = sm.group(1)
    wm = re.search(r"^\s*(\S+)", status_block)
    if not wm:
        rec["parse_gap"].append("status-word")
        return rec
    rec["status_word"] = wm.group(1)
    rec["family"] = RESOLVED_FAMILIES.get(
        (canonical_status(status_block) or "").lower(), "open")

    rim = re.search(r"refined-into:\s*(H-\d+)", status_block)
    if rim:
        rec["refined_into"] = rim.group(1)

    comments = re.findall(r"<!--(.*?)-->", status_block, re.S)
    note = norm_ws(comments[0]) if comments else ""
    if note:
        dm = re.match(r"(\d{4}-\d{2}-\d{2})", note)
        if dm:
            rec["date"] = dm.group(1)
        if not rec["refined_into"]:
            sm2 = re.search(r"(?:successor|→ successor|-> successor)\s+(H-\d+)", note)
            if sm2:
                rec["refined_into"] = sm2.group(1)
        finding_src = note
        for marker in FINDING_MARKERS:
            fm = re.search(marker + r"(.*)", note)
            if fm:
                finding_src = fm.group(1)
                break
        else:
            finding_src = re.sub(r"^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?:?\s*", "", note)
        rec["finding"] = first_sentences(finding_src)
        frm = re.search(r"[Ff]ragment\s+(\d{4})", note)
        if frm:
            rec["evidence"] = "fragment " + frm.group(1)

    vm = re.findall(r"^Verdict:\s*(.+)$", text, re.M)
    if vm:
        rec["verdict_footer"] = norm_ws(vm[-1])
    rec["runs"] = len(re.findall(r"^\|\s*\d+\s*\|", text, re.M))
    if not rec["evidence"]:
        jm = re.findall(r"^\|\s*\d+\s*\|[^|]*\|[^|]*\|\s*([^|]+?)\s*\|", text, re.M)
        rec["evidence"] = norm_ws(jm[-1]) if jm else rec["path"]
    return rec


def resolve_fragment_paths(records, repo):
    frag_dir = os.path.join(repo, "experiments", "journal-fragments")
    for rec in records:
        fm = re.match(r"fragment (\d{4})$", rec.get("evidence", ""))
        if fm:
            hits = sorted(glob.glob(os.path.join(frag_dir, fm.group(1) + "-*.md")))
            if hits:
                rec["evidence"] = "experiments/journal-fragments/" + os.path.basename(hits[0])


def cell(s):
    return norm_ws(str(s)).replace("|", "/") or "—"


def compile_index(repo, hyp_dir_name):
    hyp_dir = os.path.join(repo, hyp_dir_name)
    paths = sorted(glob.glob(os.path.join(hyp_dir, "H-*.md")))
    records = [r for r in (parse_spec(p) for p in paths) if r]
    records.sort(key=lambda r: r["num"])
    resolve_fragment_paths(records, repo)

    kept = [r for r in records if r["family"] == "kept"]
    disc = [r for r in records if r["family"] == "discarded"]
    refi = [r for r in records if r["family"] == "refined"]
    reti = [r for r in records if r["family"] == "retired"]
    open_ = [r for r in records if r["family"] == "open"]
    gaps = [r for r in records if r["parse_gap"]]

    head_sha = ""
    try:
        head_sha = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        pass

    L = []
    L.append("<!-- audience: reader -->")
    L.append("<!-- GENERATED read-model — do not hand-edit. Compiled by")
    L.append("     scripts/compile-findings-index.py from hypotheses/*.md Status + Runs +")
    L.append("     Verdict lines. Regenerate after any verdict flip. -->")
    L.append("# Findings index")
    L.append("")
    L.append("**In one sentence:** every resolved experiment in this lab — what was kept as")
    L.append("standard practice, what failed and was recorded so nobody retries it blindly,")
    L.append("and what evolved into what — one line each, with its evidence.")
    L.append("")
    src = "Compiled from %d specs" % len(records)
    if head_sha:
        src += " at HEAD %s" % head_sha
    L.append(src + ": **%d kept · %d discarded · %d refined (lineage) · %d retired**;"
             % (len(kept), len(disc), len(refi), len(reti)))
    L.append("%d open (draft/active/other) are not indexed as findings. %d spec(s) with"
             % (len(open_), len(gaps)))
    L.append("parse gaps are listed at the bottom rather than silently dropped.")
    L.append("")

    def table(rows, title, blurb, lineage=False):
        L.append("## " + title)
        L.append("")
        L.append(blurb)
        L.append("")
        if not rows:
            L.append("_none yet_")
            L.append("")
            return
        if lineage:
            L.append("| id | -> successor | date | why |")
            L.append("|---|---|---|---|")
            for r in rows:
                L.append("| %s | %s | %s | %s |" % (
                    cell(r["id"]), cell(r["refined_into"] or "?"),
                    cell(r["date"]), cell(r["finding"])))
        else:
            L.append("| id | date | finding | evidence |")
            L.append("|---|---|---|---|")
            for r in rows:
                L.append("| %s | %s | %s | %s |" % (
                    cell(r["id"]), cell(r["date"]), cell(r["finding"]), cell(r["evidence"])))
        L.append("")

    table(kept, "Kept (mechanisms adopted as standard)",
          "A row here means the pre-declared checks passed twice; the mechanism is house practice. Build on these before re-deriving.")
    table(disc, "Discarded (banked nulls)",
          "A row here means the idea was tried and failed its checks; the finding text says what the failure taught. Do not retry blindly — cite the row and change a variable.")
    table(refi, "Lineage (refined-into edges)",
          "A row here is not a dead end: the claim moved to the successor id after a spec or harness defect. The successor carries the live claim.", lineage=True)
    table(reti, "Retired",
          "Withdrawn by design review or superseded by rulings; kept for the record.")

    if gaps:
        L.append("## Parse gaps")
        L.append("")
        L.append("| id | missing |")
        L.append("|---|---|")
        for r in gaps:
            L.append("| %s | %s |" % (cell(r["id"]), cell(", ".join(r["parse_gap"]))))
        L.append("")
    return "\n".join(L) + "\n", records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="-")
    ap.add_argument("--hypotheses-dir", default="hypotheses")
    args = ap.parse_args()
    text, _ = compile_index(os.path.abspath(args.repo), args.hypotheses_dir)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
