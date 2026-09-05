#!/usr/bin/env python3
"""runs-dashboard.py — compile runs.jsonl into runs.html (single-file, offline dashboard).

PROVENANCE — COUNTED port of the source lab's runs board generator
(experiments/runs/DESIGN-trial-management/runs-dashboard.py, the exact bytes
pinned by the H-269 fixture as runs-dashboard.pinned.py, sha256 d1cc8496…;
H-269-runs-census-board KEPT 2026-09-05, run-1 counted 5/5: byte-deterministic
generation, 100% figure-to-census traceability with void/stopped/budget as
their own classes, ON readers 4-5/5 on the frozen probes with every OFF reader
strictly lower). Named divergences from the counted copy (consumer resolution
only): paths resolve from --root + `.claude/hyp.json` `runs_dir` (default
experiments/runs) instead of the lane directory; --rescan invokes the shipped
scripts/runs-census.py (the R2 census port carrying the H-254 future-date
clamp) instead of the lab's census.py; the page title/heading says Hyp instead
of the lab's pre-rename plugin name; the footer source line prints the
repo-relative source path. Chart grammar, palette order (validated), stamp
derivation, self-checks, and the Grafana export are the counted bytes.

Contract (mirrors scripts/compile-dashboard.py conventions):
  - stdlib only, no network, no CDN; output is one self-contained HTML file.
  - Deterministic: the stamp derives from the DATA (max end_ts), never the wall
    clock — compiling the same runs.jsonl twice yields byte-identical output.
  - Atomic write (temp file + os.replace), write-only-if-changed.
  - Self-checking: after writing, the compiler re-reads its own output, parses
    the embedded data island back out, and verifies row/class/lane/cost/wall
    counts against the source. Any mismatch exits non-zero. The page's own JS
    independently recomputes the same manifest at runtime and paints a
    pass/fail line in the footer (screenshot-free verification, both sides).

Usage (defaults: <root>/<runs_dir>/runs.jsonl -> <root>/<runs_dir>/runs.html):
  python3 runs-dashboard.py                 # compile runs.jsonl -> runs.html
  python3 runs-dashboard.py --rescan        # re-run runs-census.py first, then compile
  python3 runs-dashboard.py --check         # verify runs.html is byte-identical
                                            # to a fresh compile (no write)
  python3 runs-dashboard.py --grafana F     # also emit flat JSONL (epoch seconds)
                                            # for a Grafana/SQL future
  python3 runs-dashboard.py --src S --out O # override paths

Grafana path (2 steps, documented in DASHBOARD-DESIGN.md):
  1. python3 runs-dashboard.py --grafana runs-grafana.jsonl
  2. point Grafana's JSON/Infinity datasource (or a jsonl->SQLite one-liner) at
     that file; start_epoch_s/end_epoch_s are ready-made State Timeline fields.

Verdict-class display palette: 8 state classes drawn from the dataviz skill's
reference palette (status colors for judgment states, slot-1 blue / slot-7
violet for directional states, de-emphasis grays for machinery). The segment
ORDER below is load-bearing: it was chosen by running validate_palette.js on
candidate orderings until adjacency cleared both hard gates in both modes
(worst adjacent CVD dE 9.1, worst normal-vision dE 18.9). Do not reorder
without re-running the validator. Sub-3:1 light-surface members (void amber,
budget serious, other gray) ride the relief rule: every colored mark on the
page carries a text label and a table-view twin.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# (key, label, light hex, dark hex) — order = stack/legend order (validated).
CLASSES = [
    ("kept",     "kept",            "#0ca30c", "#0ca30c"),
    ("refine",   "refine",          "#2a78d6", "#3987e5"),
    ("budget",   "budget-exceeded", "#ec835a", "#ec835a"),
    ("inflight", "in-flight",       "#4a3aa7", "#9085e9"),
    ("void",     "void",            "#fab219", "#fab219"),
    ("fail",     "fail",            "#d03b3b", "#d03b3b"),
    ("gate",     "gate",            "#898781", "#898781"),
    ("other",    "other",           "#c3c2b7", "#c3c2b7"),
]
CLASS_KEYS = [c[0] for c in CLASSES]


def classify(raw):
    """Map census verdict_class vocabulary onto the 8 display classes."""
    if raw == "kept":
        return "kept"
    if raw == "counted-refine":
        return "refine"
    if raw == "counted-fail":
        return "fail"
    if raw == "budget-exceeded":
        return "budget"
    if raw == "in-flight":
        return "inflight"
    if raw == "gate":
        return "gate"
    if raw.startswith(("void", "refused", "hold")):
        return "void"
    # calibration, parked, specimen, smoke, uncounted, counted-unknown,
    # prepped-no-run, anything future -> other (never dropped)
    return "other"


def lane_kind(lane):
    if lane.startswith("H-"):
        return "hypothesis"
    if lane.startswith("DESIGN-"):
        return "design"
    return "misc"


def parse_ts(ts):
    """ISO-8601 with offset -> (epoch_seconds:int, utcoffset_minutes:int) or (None, None)."""
    if not ts:
        return None, None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None, None
    off = dt.utcoffset()
    return int(dt.timestamp()), (int(off.total_seconds() // 60) if off is not None else 0)


def load_rows(src):
    rows = []
    with open(src, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def reduce_rows(rows):
    """Project source rows to the compact array form the page embeds.

    Field order (documented for the JS side):
      0 lane, 1 run_label, 2 class_index, 3 start_epoch_s, 4 end_epoch_s,
      5 wall_seconds, 6 wall_source, 7 llm_cost_usd, 8 children,
      9 launch_mode, 10 score, 11 owner, 12 category, 13 lane_status
    Deliberately omitted from the page (kept in --grafana export): files,
    bytes, streams, evidence, amendments, cost_source, gate_exit.
    """
    reduced = []
    tz_min = 0
    tz_at = None
    for r in rows:
        s, off_s = parse_ts(r.get("start_ts"))
        e, off_e = parse_ts(r.get("end_ts"))
        # display offset = offset of the latest timestamp seen (data-derived, not wall clock)
        for t, off in ((s, off_s), (e, off_e)):
            if t is not None and (tz_at is None or t > tz_at):
                tz_at, tz_min = t, off
        cost = r.get("llm_cost_usd")
        reduced.append([
            r.get("lane", "?"),
            r.get("run_label", "?"),
            CLASS_KEYS.index(classify(r.get("verdict_class", ""))),
            s,
            e,
            r.get("wall_seconds"),
            r.get("wall_source"),
            (float(cost) if cost is not None else None),
            r.get("children"),
            r.get("launch_mode"),
            r.get("score"),
            r.get("owner"),
            r.get("category"),
            r.get("lane_status"),
        ])
    return reduced, tz_min


def build_manifest(reduced, tz_min, src_rel):
    counts = {k: 0 for k in CLASS_KEYS}
    lanes = set()
    cost_total = 0.0
    cost_rows = 0
    wall_total = 0
    stamp_epoch = 0
    for a in reduced:
        counts[CLASS_KEYS[a[2]]] += 1
        lanes.add(a[0])
        if a[7] is not None:
            cost_total += a[7]
            cost_rows += 1
        if a[5] is not None:
            wall_total += a[5]
        if a[4] is not None and a[4] > stamp_epoch:
            stamp_epoch = a[4]
        if a[3] is not None and a[3] > stamp_epoch:
            stamp_epoch = a[3]
    return {
        "rows": len(reduced),
        "lanes": len(lanes),
        "class_counts": counts,
        "cost_total": round(cost_total, 2),
        "cost_rows": cost_rows,
        "wall_total": wall_total,
        "stamp_epoch": stamp_epoch,
        "tz_min": tz_min,
        "src": src_rel,
    }


def build_html(reduced, manifest):
    data = {"manifest": manifest, "classes": [list(c) for c in CLASSES], "rows": reduced}
    blob = json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    blob = blob.replace("</", "<\\/")  # keep the data island script-safe
    html = TEMPLATE.replace("@@DATA@@", blob)
    return html.encode("utf-8")


def atomic_write(path, payload):
    """Write payload atomically; return 'unchanged'|'written'."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == payload:
                return "unchanged"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp-", suffix=".out")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return "written"


DATA_RE = re.compile(
    r'<script id="runs-data" type="application/json">(.*?)</script>', re.S)


def self_check(out_path, src_rows):
    """Parse the compiled page's data island back out and verify it against source."""
    failures = []
    ran = []

    def check(name, ok, detail):
        line = "SELF-CHECK %s: %s (%s)" % ("ok" if ok else "FAIL", name, detail)
        print(line)
        ran.append(name)
        if not ok:
            failures.append(name)

    with open(out_path, "r", encoding="utf-8") as f:
        page = f.read()
    m = DATA_RE.search(page)
    check("data island present", m is not None, "script#runs-data")
    if m is None:
        return failures
    data = json.loads(m.group(1))
    rows, man = data["rows"], data["manifest"]

    check("row count", len(rows) == len(src_rows) == man["rows"],
          "embedded %d / source %d / manifest %d" % (len(rows), len(src_rows), man["rows"]))

    src_counts = {k: 0 for k in CLASS_KEYS}
    for r in src_rows:
        src_counts[classify(r.get("verdict_class", ""))] += 1
    emb_counts = {k: 0 for k in CLASS_KEYS}
    for a in rows:
        emb_counts[CLASS_KEYS[a[2]]] += 1
    check("class counts", src_counts == emb_counts == man["class_counts"],
          json.dumps(emb_counts, sort_keys=True))
    check("class sum = rows", sum(emb_counts.values()) == man["rows"],
          "%d classes over %d rows" % (sum(emb_counts.values()), man["rows"]))

    src_lanes = {r.get("lane", "?") for r in src_rows}
    emb_lanes = {a[0] for a in rows}
    check("lane count", src_lanes == emb_lanes and len(emb_lanes) == man["lanes"],
          "%d lanes" % len(emb_lanes))

    # mirror the projection contract: costs are carried at per-row 2dp precision
    src_cost = round(sum(float(r["llm_cost_usd"]) for r in src_rows
                         if r.get("llm_cost_usd") is not None), 2)
    check("cost total", abs(src_cost - man["cost_total"]) < 0.005,
          "$%.2f over %d costed rows" % (man["cost_total"], man["cost_rows"]))

    src_wall = sum(r["wall_seconds"] for r in src_rows if r.get("wall_seconds") is not None)
    check("wall total", src_wall == man["wall_total"], "%d s" % man["wall_total"])

    src_stamp = 0
    for r in src_rows:
        for k in ("start_ts", "end_ts"):
            t, _ = parse_ts(r.get(k))
            if t is not None and t > src_stamp:
                src_stamp = t
    check("stamp epoch (data-derived)", src_stamp == man["stamp_epoch"], str(man["stamp_epoch"]))
    print("SELF-CHECK ran %d checks" % len(ran))
    return failures


def grafana_export(rows, path):
    """Flat JSONL, one object per attempt, epoch seconds precomputed."""
    out = []
    for r in rows:
        s, _ = parse_ts(r.get("start_ts"))
        e, _ = parse_ts(r.get("end_ts"))
        ev = r.get("evidence") or {}
        out.append(json.dumps({
            "lane": r.get("lane"),
            "run_label": r.get("run_label"),
            "verdict_class": r.get("verdict_class"),
            "class8": classify(r.get("verdict_class", "")),
            "kind": lane_kind(r.get("lane", "")),
            "lane_status": r.get("lane_status"),
            "start_epoch_s": s,
            "end_epoch_s": e,
            "wall_seconds": r.get("wall_seconds"),
            "wall_source": r.get("wall_source"),
            "llm_cost_usd": r.get("llm_cost_usd"),
            "cost_source": r.get("cost_source"),
            "children": r.get("children"),
            "launch_mode": r.get("launch_mode"),
            "score": r.get("score"),
            "files": r.get("files"),
            "bytes": r.get("bytes"),
            "owner": r.get("owner"),
            "category": r.get("category"),
            "evidence_run_record": bool(ev.get("run_record")) if isinstance(ev, dict) else None,
        }, separators=(",", ":"), sort_keys=True))
    payload = ("\n".join(out) + "\n").encode("utf-8")
    state = atomic_write(path, payload)
    print("grafana export: %d rows -> %s (%s)" % (len(out), path, state))


def consumer_runs_dir(root):
    """<root>/<runs_dir> from .claude/hyp.json (default experiments/runs)."""
    rel = "experiments/runs"
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as f:
            v = (json.load(f) or {}).get("runs_dir")
        if isinstance(v, str) and v.strip():
            rel = v.strip().strip("/")
    except Exception:
        pass
    return os.path.join(root, *rel.split("/"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd(),
                    help="consumer repo root (default: cwd)")
    ap.add_argument("--src", default=None,
                    help="census rows (default: <runs_dir>/runs.jsonl)")
    ap.add_argument("--out", default=None,
                    help="page path (default: <runs_dir>/runs.html)")
    ap.add_argument("--rescan", action="store_true",
                    help="run runs-census.py first to refresh runs.jsonl")
    ap.add_argument("--check", action="store_true",
                    help="verify existing output is byte-identical to a fresh compile")
    ap.add_argument("--grafana", metavar="PATH",
                    help="also write flat JSONL with epoch-second fields")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    runs_dir = consumer_runs_dir(root)
    if args.src is None:
        args.src = os.path.join(runs_dir, "runs.jsonl")
    if args.out is None:
        args.out = os.path.join(runs_dir, "runs.html")

    if args.rescan:
        census = os.path.join(HERE, "runs-census.py")
        print("rescan: %s" % census)
        subprocess.run([sys.executable, census, "--root", root,
                        "--out", args.src], check=True, cwd=root)

    src_rows = load_rows(args.src)
    reduced, tz_min = reduce_rows(src_rows)
    try:
        src_rel = os.path.relpath(args.src, root)
    except ValueError:
        src_rel = args.src
    manifest = build_manifest(reduced, tz_min, src_rel)
    payload = build_html(reduced, manifest)
    sha = hashlib.sha256(payload).hexdigest()

    if args.check:
        with open(args.out, "rb") as f:
            on_disk = f.read()
        same = on_disk == payload
        print("check: fresh compile sha256 %s" % sha)
        print("check: on-disk        sha256 %s" % hashlib.sha256(on_disk).hexdigest())
        print("check: %s" % ("byte-identical" if same else "DIFFERS"))
        failures = self_check(args.out, src_rows)
        sys.exit(0 if same and not failures else 2)

    state = atomic_write(args.out, payload)
    print("compiled %s -> %s (%s, %d bytes, sha256 %s)"
          % (os.path.basename(args.src), args.out, state, len(payload), sha))

    if args.grafana:
        grafana_export(src_rows, args.grafana)

    failures = self_check(args.out, src_rows)
    if failures:
        print("SELF-CHECK FAILED: %s" % ", ".join(failures))
        sys.exit(1)
    print("SELF-CHECK PASS")


# ---------------------------------------------------------------------------
# Page template. House chrome follows the decisions.html precedent (paper page,
# white cards, hairline borders); chart marks use the dataviz reference palette
# (see module docstring for the validator provenance). All data-derived text is
# inserted via textContent — never innerHTML.
# ---------------------------------------------------------------------------
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hyp runs — trial management</title>
<style>
:root {
  color-scheme: light;
  --page:#fafaf7; --surface:#ffffff; --ink:#1a1a1a; --ink-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --hairline:#e8e8e3; --accent:#2563eb;
  --c-kept:#0ca30c; --c-refine:#2a78d6; --c-budget:#ec835a; --c-inflight:#4a3aa7;
  --c-void:#fab219; --c-fail:#d03b3b; --c-gate:#898781; --c-other:#c3c2b7;
  --proc:#2a78d6; --wait:#c3c2b7; --busy:#2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --hairline:#2c2c2a; --accent:#6ea3f5;
    --c-refine:#3987e5; --c-inflight:#9085e9; --proc:#3987e5; --wait:#898781; --busy:#3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --hairline:#2c2c2a; --accent:#6ea3f5;
  --c-refine:#3987e5; --c-inflight:#9085e9; --proc:#3987e5; --wait:#898781; --busy:#3987e5;
}
* { box-sizing: border-box; }
body {
  margin:0; background:var(--page); color:var(--ink);
  font:14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width:1080px; margin:0 auto; padding:28px 20px 60px; }
header h1 { font-size:21px; font-weight:650; margin:0 0 2px; }
.stamp { color:var(--ink-2); font-size:13px; margin:0 0 18px; }
.stamp b { font-weight:600; color:var(--ink); }
.themebtn { float:right; font:12px system-ui,sans-serif; color:var(--ink-2);
  background:var(--surface); border:1px solid var(--hairline); border-radius:6px;
  padding:4px 10px; cursor:pointer; }
.filters { display:flex; flex-wrap:wrap; gap:14px; align-items:center; margin:0 0 18px; }
.filters .flabel { font-size:12px; color:var(--muted); }
.seg { display:inline-flex; border:1px solid var(--hairline); border-radius:7px;
  overflow:hidden; background:var(--surface); }
.seg button { font:13px system-ui,sans-serif; border:0; background:transparent;
  color:var(--ink-2); padding:5px 12px; cursor:pointer; border-right:1px solid var(--hairline); }
.seg button:last-child { border-right:0; }
.seg button[aria-pressed="true"] { color:var(--ink); font-weight:600; background:var(--page); }
.kpis { display:grid; grid-template-columns:minmax(150px,1.2fr) repeat(5,1fr); gap:10px; margin:0 0 22px; }
.tile { background:var(--surface); border:1px solid var(--hairline); border-radius:8px; padding:12px 14px; }
.tile .tl { font-size:12px; color:var(--muted); }
.tile .tv { font-size:22px; font-weight:650; margin-top:2px; }
.tile.hero .tv { font-size:48px; font-weight:700; line-height:1.05; }
.tile .ts { font-size:11.5px; color:var(--ink-2); margin-top:2px; }
section.card { background:var(--surface); border:1px solid var(--hairline);
  border-radius:8px; padding:16px 18px 14px; margin:0 0 18px; }
section.card h2 { font-size:15px; font-weight:650; margin:0 0 2px; }
section.card .sub { font-size:12.5px; color:var(--ink-2); margin:0 0 12px; }
.legend { display:flex; flex-wrap:wrap; gap:10px 16px; margin:10px 0 4px; font-size:12.5px; color:var(--ink-2); }
.legend .li { display:inline-flex; align-items:center; gap:6px; }
.legend .sw { width:11px; height:11px; border-radius:3px; display:inline-block; }
.legend .ct { color:var(--muted); font-variant-numeric:tabular-nums; }
.stack { display:flex; gap:2px; height:24px; border-radius:5px; overflow:hidden; }
.stack .segm { position:relative; min-width:1px; display:flex; align-items:center;
  justify-content:center; cursor:default; }
.stack .segm:first-child { border-radius:4px 0 0 4px; }
.stack .segm:last-child { border-radius:0 4px 4px 0; }
.stack .segm .slab { font-size:11.5px; font-weight:600; white-space:nowrap; pointer-events:none; }
.barrow { display:grid; grid-template-columns:190px 1fr 86px; gap:10px; align-items:center; margin:0 0 6px; }
.barrow .bl { font-size:12.5px; color:var(--ink-2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.barrow .btrack { display:flex; gap:2px; height:16px; }
.barrow .bseg { border-radius:0; min-width:0; }
.barrow .bseg.rounded-end { border-radius:0 4px 4px 0; }
.barrow .bseg:first-child { border-radius:4px 0 0 4px; }
.barrow .bseg.solo { border-radius:4px; }
.barrow .bv { font-size:12px; color:var(--ink-2); font-variant-numeric:tabular-nums; text-align:left; }
.tl-wrap { position:relative; margin-top:6px; }
.tl-grid { position:absolute; inset:0 0 22px 130px; pointer-events:none; }
.tl-grid .gl { position:absolute; top:0; bottom:0; width:1px; background:var(--grid); }
.tl-row { display:grid; grid-template-columns:130px 1fr; align-items:center; margin:0 0 5px; }
.tl-row .rl { font-size:12px; color:var(--ink-2); padding-right:10px; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.tl-track { position:relative; height:18px; background:var(--page);
  border:1px solid var(--hairline); border-radius:4px; }
.tl-iv { position:absolute; top:1px; bottom:1px; border-radius:2px; min-width:2px; }
.tl-axis { display:grid; grid-template-columns:130px 1fr; margin-top:2px; }
.tl-axis .ax { position:relative; height:18px; }
.tl-axis .at { position:absolute; transform:translateX(-50%); font-size:11px;
  color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
table.dt { border-collapse:collapse; width:100%; font-size:12.5px; }
table.dt th { text-align:left; font-weight:600; color:var(--muted); font-size:11.5px;
  border-bottom:1px solid var(--grid); padding:5px 10px 5px 0; }
table.dt td { border-bottom:1px solid var(--hairline); padding:5px 10px 5px 0;
  color:var(--ink-2); font-variant-numeric:tabular-nums; }
table.dt td.tlane { color:var(--ink); font-weight:550; font-variant-numeric:normal; }
table.dt tr.ghead td { background:var(--page); color:var(--ink); font-weight:650;
  font-variant-numeric:normal; }
.mix { display:inline-flex; gap:1px; height:10px; width:120px; border-radius:2px; overflow:hidden; vertical-align:middle; }
.mix i { display:block; min-width:1px; }
.chip { display:inline-flex; align-items:center; gap:5px; font-size:11.5px; color:var(--ink-2); }
.chip .sw { width:9px; height:9px; border-radius:2px; }
details.tw { margin-top:10px; }
details.tw summary { font-size:12px; color:var(--accent); cursor:pointer; }
details.tw .dtwrap { max-height:340px; overflow:auto; margin-top:8px; }
#tip { position:fixed; z-index:10; background:var(--ink); color:var(--page);
  border-radius:6px; padding:7px 10px; font-size:12px; max-width:320px;
  pointer-events:none; display:none; box-shadow:0 2px 10px rgba(0,0,0,.25); }
#tip .tv { font-weight:650; font-size:13px; }
#tip .tk { opacity:.75; }
[tabindex="0"]:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
footer { color:var(--muted); font-size:12px; margin-top:26px; }
footer .ok { color:var(--c-kept); font-weight:600; }
footer .bad { color:var(--c-fail); font-weight:700; }
.note { font-size:11.5px; color:var(--muted); margin-top:8px; }
@media (max-width:900px){ .kpis{grid-template-columns:repeat(3,1fr);} .barrow{grid-template-columns:120px 1fr 80px;} }
</style>
</head>
<body>
<script id="runs-data" type="application/json">@@DATA@@</script>
<div class="wrap">
  <header>
    <button class="themebtn" id="themebtn" type="button">theme: auto</button>
    <h1>Hyp runs — trial management</h1>
    <p class="stamp" id="stamp"></p>
  </header>

  <div class="filters" id="filters">
    <span class="flabel">Range</span>
    <span class="seg" id="rangeseg" role="group" aria-label="date range"></span>
    <span class="flabel">Group lanes by</span>
    <span class="seg" id="groupseg" role="group" aria-label="lane grouping"></span>
  </div>

  <div class="kpis" id="kpis"></div>

  <section class="card">
    <h2>Verdict outcomes</h2>
    <p class="sub" id="outc-sub"></p>
    <div class="stack" id="outc-stack"></div>
    <div class="legend" id="outc-legend"></div>
    <details class="tw"><summary>Per-lane rows (table view)</summary>
      <div class="dtwrap"><table class="dt" id="lane-table"></table></div>
    </details>
  </section>

  <section class="card">
    <h2>Processing vs wait</h2>
    <p class="sub" id="pw-sub"></p>
    <div class="stack" id="pw-stack" style="height:20px"></div>
    <div class="legend" id="pw-legend"></div>
    <div id="pw-runs" style="margin-top:14px"></div>
    <p class="note">Wait = gap since the same lane's previous attempt ended (first attempt in a lane has no wait). Processing = the attempt's own wall span.</p>
    <details class="tw"><summary>Per-run wait/processing (table view)</summary>
      <div class="dtwrap"><table class="dt" id="pw-table"></table></div>
    </details>
  </section>

  <section class="card">
    <h2>Machine utilization</h2>
    <p class="sub" id="tl-sub"></p>
    <div class="tl-wrap" id="tl"></div>
    <p class="note">State-timeline grammar (Grafana State Timeline): horizontal bands, one row per lane, color = verdict class; top band merges all attempts into busy/idle (gaps &le; 15 min bridged). Lower bound — only what run records captured.</p>
    <details class="tw"><summary>Busy/idle windows (table view)</summary>
      <div class="dtwrap"><table class="dt" id="tl-table"></table></div>
    </details>
  </section>

  <section class="card">
    <h2>Recorded cost by lane</h2>
    <p class="sub" id="cost-sub"></p>
    <div id="cost-bars"></div>
    <details class="tw"><summary>All costed lanes (table view)</summary>
      <div class="dtwrap"><table class="dt" id="cost-table"></table></div>
    </details>
  </section>

  <section class="card">
    <h2>In flight at snapshot</h2>
    <p class="sub" id="if-sub"></p>
    <div class="dtwrap"><table class="dt" id="if-table"></table></div>
  </section>

  <footer>
    <div id="selfcheck"></div>
    <div id="srcline"></div>
  </footer>
</div>
<div id="tip" role="status"></div>

<script>
"use strict";
/* All data-derived strings enter the DOM via textContent. */
const DATA = JSON.parse(document.getElementById("runs-data").textContent);
const M = DATA.manifest, CL = DATA.classes;
const F = { lane:0, label:1, cls:2, s:3, e:4, wall:5, wsrc:6, cost:7, kids:8, mode:9, score:10, owner:11, cat:12, lstat:13 };
const CVAR = { kept:"--c-kept", refine:"--c-refine", budget:"--c-budget", inflight:"--c-inflight",
               void:"--c-void", fail:"--c-fail", gate:"--c-gate", other:"--c-other" };
const TZ = M.tz_min, STAMP = M.stamp_epoch, GAP = 900;

/* ---------- helpers ---------- */
function el(tag, cls, text){ const n=document.createElement(tag); if(cls) n.className=cls;
  if(text!==undefined && text!==null) n.textContent=String(text); return n; }
function css(k){ return "var(" + CVAR[k] + ")"; }
function localDate(ep){ const d=new Date((ep+TZ*60)*1000);
  return d.toISOString().slice(0,10); }
function localDT(ep){ const d=new Date((ep+TZ*60)*1000);
  return d.toISOString().slice(0,16).replace("T"," "); }
function fmtDur(s){ if(s==null) return "—"; s=Math.round(s);
  if(s<60) return s+"s";
  if(s<3600) return Math.round(s/60)+"m";
  if(s<86400) return (Math.floor(s/3600))+"h "+(Math.round(s%3600/60))+"m";
  return (Math.floor(s/86400))+"d "+(Math.round(s%86400/3600))+"h"; }
function fmtMoney(x){ return "$"+x.toFixed(2); }
function fmtHrs(s){ return (s/3600).toFixed(1)+"h"; }
function pct(x){ return (100*x).toFixed(1)+"%"; }
function tzLabel(){ const m=TZ, sg=m<0?"−":"+", a=Math.abs(m);
  return "UTC"+sg+String(Math.floor(a/60)).padStart(2,"0")+":"+String(a%60).padStart(2,"0"); }
function kindOf(lane){ return lane.startsWith("H-") ? "hypothesis"
  : lane.startsWith("DESIGN-") ? "design" : "misc"; }

/* ---------- tooltip (enhances, never gates: every value also lives in a table view) ---------- */
const tip = document.getElementById("tip");
function tipShow(lines, x, y){
  tip.replaceChildren();
  lines.forEach(function(L,i){ const d=el("div", i===0?"tv":"tk", L); tip.appendChild(d); });
  tip.style.display="block";
  const r=tip.getBoundingClientRect();
  tip.style.left=Math.min(x+14, innerWidth-r.width-8)+"px";
  tip.style.top=Math.min(y+14, innerHeight-r.height-8)+"px";
}
function tipHide(){ tip.style.display="none"; }
function attachTip(node, linesFn){
  node.addEventListener("pointermove", function(ev){ tipShow(linesFn(), ev.clientX, ev.clientY); });
  node.addEventListener("pointerleave", tipHide);
  node.setAttribute("tabindex","0");
  node.setAttribute("role","img");
  node.setAttribute("aria-label", linesFn().join("; "));
  node.addEventListener("focus", function(){ const r=node.getBoundingClientRect();
    tipShow(linesFn(), r.left, r.bottom); });
  node.addEventListener("blur", tipHide);
}

/* ---------- wait-time attachment (computed over ALL rows, once) ---------- */
const runs = DATA.rows.map(function(a,i){ return { i:i, a:a }; });
(function(){
  const byLane = {};
  runs.forEach(function(r){ (byLane[r.a[F.lane]] = byLane[r.a[F.lane]] || []).push(r); });
  Object.keys(byLane).forEach(function(k){
    const L = byLane[k].filter(function(r){ return r.a[F.s]!=null; })
                       .sort(function(x,y){ return x.a[F.s]-y.a[F.s]; });
    for(let i=0;i<L.length;i++){
      L[i].wait = i===0 ? null : Math.max(0, L[i].a[F.s] - (L[i-1].a[F.e]!=null ? L[i-1].a[F.e] : L[i-1].a[F.s]));
    }
  });
})();

/* ---------- filter state ---------- */
const RANGES = [ ["all","All time"], ["30d","Last 30 days"], ["7d","Last 7 days"] ];
const GROUPS = [ ["none","none"], ["kind","kind"], ["owner","owner"], ["cat","category"] ];
const state = { range:"7d", group:"none" };
function windowFor(range){
  if(range==="7d")  return [STAMP-7*86400, STAMP];
  if(range==="30d") return [STAMP-30*86400, STAMP];
  let mn=STAMP; runs.forEach(function(r){ if(r.a[F.s]!=null && r.a[F.s]<mn) mn=r.a[F.s]; });
  return [mn, STAMP];
}
function visible(){
  const w=windowFor(state.range), t0=w[0], t1=w[1];
  if(state.range==="all") return runs.slice();
  return runs.filter(function(r){
    const s=r.a[F.s], e=r.a[F.e]!=null?r.a[F.e]:r.a[F.s];
    return s!=null && e>=t0 && s<=t1;
  });
}
function groupKey(r){
  if(state.group==="kind")  return kindOf(r.a[F.lane]);
  if(state.group==="owner") return r.a[F.owner] || "uncategorized";
  if(state.group==="cat")   return r.a[F.cat]   || "uncategorized";
  return null;
}

/* ---------- segmented controls ---------- */
function buildSeg(id, opts, cur, onpick){
  const seg=document.getElementById(id); seg.replaceChildren();
  opts.forEach(function(o){
    const b=el("button",null,o[1]); b.type="button";
    b.setAttribute("aria-pressed", String(o[0]===cur));
    b.addEventListener("click", function(){ onpick(o[0]); });
    seg.appendChild(b);
  });
}

/* ---------- busy/idle union ---------- */
function busyIntervals(rows, t0, t1){
  const iv = rows.filter(function(r){ return r.a[F.s]!=null; })
    .map(function(r){
      const s=r.a[F.s], e=r.a[F.e]!=null?r.a[F.e]:r.a[F.s];
      return [Math.max(s,t0), Math.min(e,t1)];
    }).filter(function(p){ return p[1]>p[0]; })
      .sort(function(x,y){ return x[0]-y[0]; });
  const out=[];
  iv.forEach(function(p){
    if(out.length && p[0]-out[out.length-1][1] <= GAP)
      out[out.length-1][1] = Math.max(out[out.length-1][1], p[1]);
    else out.push([p[0],p[1]]);
  });
  return out;
}

/* ---------- renderers ---------- */
function renderKPIs(rows, t0, t1){
  const k=document.getElementById("kpis"); k.replaceChildren();
  const lanes=new Set(), counts={}; let cost=0, costed=0;
  CL.forEach(function(c){ counts[c[0]]=0; });
  rows.forEach(function(r){ lanes.add(r.a[F.lane]); counts[CL[r.a[F.cls]][0]]++;
    if(r.a[F.cost]!=null){ cost+=r.a[F.cost]; costed++; } });
  const busy=busyIntervals(rows,t0,t1).reduce(function(s,p){ return s+(p[1]-p[0]); },0);
  const span=t1-t0;
  function tile(label,val,subtext,hero){
    const t=el("div", hero?"tile hero":"tile");
    t.appendChild(el("div","tl",label)); t.appendChild(el("div","tv",val));
    if(subtext) t.appendChild(el("div","ts",subtext));
    return t;
  }
  k.appendChild(tile("Attempts in range", rows.length, RANGES.find(function(r){return r[0]===state.range;})[1], true));
  k.appendChild(tile("Lanes", lanes.size, null));
  k.appendChild(tile("Kept", counts.kept, pct(rows.length?counts.kept/rows.length:0)+" of attempts"));
  k.appendChild(tile("Void share", pct(rows.length?counts.void/rows.length:0), counts.void+" void-class attempts"));
  k.appendChild(tile("Recorded cost", fmtMoney(cost), costed+" of "+rows.length+" attempts costed"));
  k.appendChild(tile("Machine busy", span>0?pct(busy/span):"—", fmtHrs(busy)+" of "+fmtHrs(span)));
}

function fitLabels(container){
  container.querySelectorAll(".slab").forEach(function(sp){
    const seg=sp.parentElement;
    if(sp.scrollWidth > seg.clientWidth-10) sp.remove();   // measure first — never clip
  });
}

function renderOutcomes(rows){
  const counts={}; CL.forEach(function(c){ counts[c[0]]=0; });
  rows.forEach(function(r){ counts[CL[r.a[F.cls]][0]]++; });
  document.getElementById("outc-sub").textContent =
    rows.length+" attempts, part-to-whole by verdict class. Labels ride segments that fit; the legend and table carry the rest.";
  const stack=document.getElementById("outc-stack"); stack.replaceChildren();
  CL.forEach(function(c){
    const n=counts[c[0]]; if(!n) return;
    const seg=el("div","segm"); seg.style.background=css(c[0]);
    seg.style.flexGrow=String(n); seg.style.flexBasis="0";
    const lab=el("span","slab", c[1]+" "+n);
    lab.style.color = (c[0]==="void"||c[0]==="other") ? "#1a1a1a" : "#ffffff";
    seg.appendChild(lab);
    attachTip(seg, function(){ return [c[1]+": "+n, pct(n/rows.length)+" of "+rows.length+" attempts"]; });
    stack.appendChild(seg);
  });
  const leg=document.getElementById("outc-legend"); leg.replaceChildren();
  CL.forEach(function(c){
    const li=el("span","li"); const sw=el("span","sw"); sw.style.background=css(c[0]);
    li.appendChild(sw); li.appendChild(el("span",null,c[1]));
    li.appendChild(el("span","ct",counts[c[0]]));
    leg.appendChild(li);
  });
  fitLabels(stack);
  renderLaneTable(rows);
}

function renderLaneTable(rows){
  const byLane={};
  rows.forEach(function(r){
    const k=r.a[F.lane];
    const o=byLane[k]=byLane[k]||{lane:k,n:0,counts:{},wall:0,cost:null,last:0,lstat:null,g:groupKey(r)};
    o.n++; o.counts[CL[r.a[F.cls]][0]]=(o.counts[CL[r.a[F.cls]][0]]||0)+1;
    if(r.a[F.wall]!=null) o.wall+=r.a[F.wall];
    if(r.a[F.cost]!=null) o.cost=(o.cost||0)+r.a[F.cost];
    const e=r.a[F.e]!=null?r.a[F.e]:r.a[F.s]; if(e!=null&&e>o.last) o.last=e;
    if(r.a[F.lstat]) o.lstat=r.a[F.lstat];
  });
  const lanes=Object.values(byLane).sort(function(a,b){ return b.last-a.last; });
  const tb=document.getElementById("lane-table"); tb.replaceChildren();
  const hd=el("tr"); ["Lane","Status","Attempts","Mix","Wall","Cost","Last activity"].forEach(function(h){ hd.appendChild(el("th",null,h)); });
  tb.appendChild(hd);
  let groups={};
  if(state.group!=="none"){ lanes.forEach(function(o){ (groups[o.g]=groups[o.g]||[]).push(o); }); }
  else groups={"":lanes};
  Object.keys(groups).sort().forEach(function(g){
    if(g!==""){
      const gr=el("tr","ghead"); const td=el("td",null,g+" — "+groups[g].length+" lanes");
      td.colSpan=7; gr.appendChild(td); tb.appendChild(gr);
    }
    groups[g].forEach(function(o){
      const tr=el("tr");
      tr.appendChild(Object.assign(el("td","tlane",o.lane)));
      tr.appendChild(el("td",null,o.lstat||"—"));
      tr.appendChild(el("td",null,o.n));
      const tdm=el("td"); const mix=el("span","mix");
      CL.forEach(function(c){ const n=o.counts[c[0]]||0; if(!n) return;
        const i=el("i"); i.style.background=css(c[0]); i.style.flexGrow=String(n); i.style.flexBasis="0"; mix.appendChild(i); });
      tdm.appendChild(mix);
      attachTip(tdm, function(){ return [o.lane].concat(CL.filter(function(c){return o.counts[c[0]];})
        .map(function(c){ return c[1]+": "+o.counts[c[0]]; })); });
      tr.appendChild(tdm);
      tr.appendChild(el("td",null,fmtDur(o.wall)));
      tr.appendChild(el("td",null,o.cost!=null?fmtMoney(o.cost):"—"));
      tr.appendChild(el("td",null,o.last?localDate(o.last):"—"));
      tb.appendChild(tr);
    });
  });
}

function renderProcWait(rows){
  const withWait=rows.filter(function(r){ return r.wait!=null; });
  const totalWait=withWait.reduce(function(s,r){ return s+r.wait; },0);
  const totalProc=rows.reduce(function(s,r){ return s+(r.a[F.wall]||0); },0);
  document.getElementById("pw-sub").textContent =
    "Aggregate over the range: "+fmtHrs(totalProc)+" processing vs "+fmtHrs(totalWait)+
    " intra-lane wait ("+withWait.length+" follow-up attempts had a measurable wait).";
  const stack=document.getElementById("pw-stack"); stack.replaceChildren();
  const tot=totalProc+totalWait;
  [["processing",totalProc,"--proc","#ffffff"],["wait",totalWait,"--wait","#1a1a1a"]].forEach(function(d){
    if(!d[1]) return;
    const seg=el("div","segm"); seg.style.background="var("+d[2]+")";
    seg.style.flexGrow=String(Math.max(1,Math.round(1000*d[1]/(tot||1)))); seg.style.flexBasis="0";
    const lab=el("span","slab", d[0]+" "+fmtHrs(d[1])); lab.style.color=d[3];
    seg.appendChild(lab);
    attachTip(seg, function(){ return [d[0]+": "+fmtHrs(d[1]), tot?pct(d[1]/tot)+" of accounted time":""]; });
    stack.appendChild(seg);
  });
  const leg=document.getElementById("pw-legend"); leg.replaceChildren();
  [["processing","--proc"],["wait","--wait"]].forEach(function(d){
    const li=el("span","li"); const sw=el("span","sw"); sw.style.background="var("+d[1]+")";
    li.appendChild(sw); li.appendChild(el("span",null,d[0])); leg.appendChild(li);
  });
  fitLabels(stack);

  const recent=rows.filter(function(r){ return r.a[F.s]!=null; })
    .sort(function(x,y){ return y.a[F.s]-x.a[F.s]; }).slice(0,25);
  const mx=recent.reduce(function(m,r){ return Math.max(m,(r.wait||0)+(r.a[F.wall]||0)); },1);
  const host=document.getElementById("pw-runs"); host.replaceChildren();
  host.appendChild(el("div","note","Latest "+recent.length+" attempts in range (newest first):"));
  recent.forEach(function(r){
    const row=el("div","barrow");
    row.appendChild(el("span","bl", r.a[F.lane]+" · "+r.a[F.label]));
    const track=el("span","btrack");
    const w=r.wait||0, p=r.a[F.wall]||0;
    if(w>0){ const sg=el("span","bseg"); sg.style.background="var(--wait)";
      sg.style.width=(100*w/mx)+"%";
      attachTip(sg, function(){ return ["wait "+fmtDur(w), r.a[F.lane]+" · "+r.a[F.label]]; });
      track.appendChild(sg); }
    if(p>0){ const sg=el("span","bseg"+(w>0?" rounded-end":" solo")); sg.style.background="var(--proc)";
      sg.style.width=(100*p/mx)+"%";
      attachTip(sg, function(){ return ["processing "+fmtDur(p),
        r.a[F.lane]+" · "+r.a[F.label], CL[r.a[F.cls]][1]+(r.a[F.wsrc]?" · "+r.a[F.wsrc]:"")]; });
      track.appendChild(sg); }
    row.appendChild(track);
    row.appendChild(el("span","bv", fmtDur(w+p)));
    host.appendChild(row);
  });

  const tb=document.getElementById("pw-table"); tb.replaceChildren();
  const hd=el("tr"); ["Lane","Run","Start","Wait","Processing","Class"].forEach(function(h){ hd.appendChild(el("th",null,h)); });
  tb.appendChild(hd);
  rows.filter(function(r){ return r.a[F.s]!=null; })
      .sort(function(x,y){ return y.a[F.s]-x.a[F.s]; })
      .forEach(function(r){
    const tr=el("tr");
    tr.appendChild(el("td","tlane",r.a[F.lane]));
    tr.appendChild(el("td",null,r.a[F.label]));
    tr.appendChild(el("td",null,localDT(r.a[F.s])));
    tr.appendChild(el("td",null,r.wait!=null?fmtDur(r.wait):"—"));
    tr.appendChild(el("td",null,fmtDur(r.a[F.wall])));
    tr.appendChild(el("td",null,CL[r.a[F.cls]][1]));
    tb.appendChild(tr);
  });
}

function renderTimeline(rows, t0, t1){
  const host=document.getElementById("tl"); host.replaceChildren();
  const span=t1-t0;
  const busy=busyIntervals(rows,t0,t1);
  const busySec=busy.reduce(function(s,p){ return s+(p[1]-p[0]); },0);
  document.getElementById("tl-sub").textContent =
    localDate(t0)+" → "+localDate(t1)+" ("+tzLabel()+"): busy "+fmtHrs(busySec)+
    " ("+pct(span?busySec/span:0)+"), idle "+fmtHrs(span-busySec)+".";

  const grid=el("div","tl-grid");
  const dayTicks=[];
  const step=Math.max(1, Math.ceil(span/86400/10));
  for(let d=Math.ceil((t0+TZ*60)/86400)*86400 - TZ*60; d<=t1; d+=86400*step){
    if(d<t0) continue;
    dayTicks.push(d);
    const gl=el("div","gl"); gl.style.left=(100*(d-t0)/span)+"%"; grid.appendChild(gl);
  }
  host.appendChild(grid);

  function band(label, intervals){
    const row=el("div","tl-row");
    row.appendChild(el("span","rl",label));
    const track=el("div","tl-track");
    intervals.forEach(function(iv){
      const b=el("div","tl-iv");
      b.style.left=(100*(iv.s-t0)/span)+"%";
      b.style.width=Math.max(0.2,(100*(iv.e-iv.s)/span))+"%";
      b.style.background=iv.color;
      attachTip(b, iv.lines);
      track.appendChild(b);
    });
    row.appendChild(track);
    host.appendChild(row);
  }
  band("machine (all lanes)", busy.map(function(p){
    return { s:p[0], e:p[1], color:"var(--busy)",
      lines:function(){ return ["busy "+fmtDur(p[1]-p[0]), localDT(p[0])+" → "+localDT(p[1])]; } };
  }));

  const laneSec={};
  rows.forEach(function(r){ if(r.a[F.s]==null) return;
    const e=r.a[F.e]!=null?r.a[F.e]:r.a[F.s];
    laneSec[r.a[F.lane]]=(laneSec[r.a[F.lane]]||0)+Math.max(0,Math.min(e,t1)-Math.max(r.a[F.s],t0)); });
  const top=Object.keys(laneSec).sort(function(a,b){ return laneSec[b]-laneSec[a]; }).slice(0,12);
  top.forEach(function(lane){
    band(lane, rows.filter(function(r){ return r.a[F.lane]===lane && r.a[F.s]!=null; })
      .map(function(r){
        const e=r.a[F.e]!=null?r.a[F.e]:r.a[F.s];
        return { s:Math.max(r.a[F.s],t0), e:Math.min(Math.max(e,r.a[F.s]+span*0.002),t1),
          color:css(CL[r.a[F.cls]][0]),
          lines:function(){ return [r.a[F.lane]+" · "+r.a[F.label],
            CL[r.a[F.cls]][1]+" · "+fmtDur(r.a[F.wall]),
            localDT(r.a[F.s])+" → "+(r.a[F.e]!=null?localDT(r.a[F.e]):"open")]; } };
      }));
  });

  const axis=el("div","tl-axis"); axis.appendChild(el("span"));
  const ax=el("div","ax");
  dayTicks.forEach(function(d){
    const t=el("span","at",localDate(d).slice(5)); t.style.left=(100*(d-t0)/span)+"%"; ax.appendChild(t);
  });
  axis.appendChild(ax); host.appendChild(axis);

  const tb=document.getElementById("tl-table"); tb.replaceChildren();
  const hd=el("tr"); ["State","From","To","Duration"].forEach(function(h){ hd.appendChild(el("th",null,h)); });
  tb.appendChild(hd);
  let prev=t0;
  busy.forEach(function(p){
    if(p[0]-prev>60){ const tr=el("tr");
      ["idle",localDT(prev),localDT(p[0]),fmtDur(p[0]-prev)].forEach(function(v,i){ tr.appendChild(el("td",i===0?null:null,v)); });
      tb.appendChild(tr); }
    const tr=el("tr");
    ["busy",localDT(p[0]),localDT(p[1]),fmtDur(p[1]-p[0])].forEach(function(v){ tr.appendChild(el("td",null,v)); });
    tb.appendChild(tr); prev=p[1];
  });
  if(t1-prev>60){ const tr=el("tr");
    ["idle",localDT(prev),localDT(t1),fmtDur(t1-prev)].forEach(function(v){ tr.appendChild(el("td",null,v)); });
    tb.appendChild(tr); }
}

function renderCost(rows){
  const byLane={}; let total=0, costed=0;
  rows.forEach(function(r){ if(r.a[F.cost]==null) return;
    byLane[r.a[F.lane]]=(byLane[r.a[F.lane]]||0)+r.a[F.cost]; total+=r.a[F.cost]; costed++; });
  const lanes=Object.keys(byLane).sort(function(a,b){ return byLane[b]-byLane[a]; });
  document.getElementById("cost-sub").textContent =
    fmtMoney(total)+" recorded across "+lanes.length+" lanes ("+costed+" costed attempts in range; missing cost is unknown, not zero). Top "+Math.min(20,lanes.length)+" shown.";
  const host=document.getElementById("cost-bars"); host.replaceChildren();
  const mx=lanes.length?byLane[lanes[0]]:1;
  lanes.slice(0,20).forEach(function(lane){
    const row=el("div","barrow");
    row.appendChild(el("span","bl",lane));
    const track=el("span","btrack");
    const sg=el("span","bseg solo"); sg.style.background="var(--proc)";
    sg.style.width=Math.max(0.5,(100*byLane[lane]/mx))+"%";
    attachTip(sg, function(){ return [fmtMoney(byLane[lane]), lane]; });
    track.appendChild(sg); row.appendChild(track);
    row.appendChild(el("span","bv",fmtMoney(byLane[lane])));
    host.appendChild(row);
  });
  const tb=document.getElementById("cost-table"); tb.replaceChildren();
  const hd=el("tr"); ["Lane","Recorded cost"].forEach(function(h){ hd.appendChild(el("th",null,h)); });
  tb.appendChild(hd);
  lanes.forEach(function(lane){ const tr=el("tr");
    tr.appendChild(el("td","tlane",lane)); tr.appendChild(el("td",null,fmtMoney(byLane[lane])));
    tb.appendChild(tr); });
}

function renderInflight(){
  const inf=runs.filter(function(r){ return CL[r.a[F.cls]][0]==="inflight"; })
    .sort(function(x,y){ return (y.a[F.e]||0)-(x.a[F.e]||0); });
  document.getElementById("if-sub").textContent =
    inf.length+" attempts open at snapshot ("+localDT(STAMP)+" "+tzLabel()+") — mtime-span, so “end” is last touch, not completion.";
  const tb=document.getElementById("if-table"); tb.replaceChildren();
  const hd=el("tr"); ["Lane","Run","Started","Last touch","Span","Mode","Children"].forEach(function(h){ hd.appendChild(el("th",null,h)); });
  tb.appendChild(hd);
  inf.forEach(function(r){
    const tr=el("tr");
    tr.appendChild(el("td","tlane",r.a[F.lane]));
    tr.appendChild(el("td",null,r.a[F.label]));
    tr.appendChild(el("td",null,r.a[F.s]!=null?localDT(r.a[F.s]):"—"));
    tr.appendChild(el("td",null,r.a[F.e]!=null?localDT(r.a[F.e]):"—"));
    tr.appendChild(el("td",null,fmtDur(r.a[F.wall])));
    tr.appendChild(el("td",null,r.a[F.mode]||"—"));
    tr.appendChild(el("td",null,r.a[F.kids]!=null?r.a[F.kids]:"—"));
    tb.appendChild(tr);
  });
}

/* ---------- runtime self-check (independent recount of the compiler's manifest) ---------- */
function renderSelfCheck(){
  const counts={}; CL.forEach(function(c){ counts[c[0]]=0; });
  let cost=0, costed=0, wall=0; const lanes=new Set(); let stamp=0;
  runs.forEach(function(r){
    counts[CL[r.a[F.cls]][0]]++; lanes.add(r.a[F.lane]);
    if(r.a[F.cost]!=null){ cost+=r.a[F.cost]; costed++; }
    if(r.a[F.wall]!=null) wall+=r.a[F.wall];
    [r.a[F.s],r.a[F.e]].forEach(function(t){ if(t!=null&&t>stamp) stamp=t; });
  });
  const ok = runs.length===M.rows
    && lanes.size===M.lanes
    && Math.abs(cost-M.cost_total)<0.005
    && costed===M.cost_rows
    && wall===M.wall_total
    && stamp===M.stamp_epoch
    && CL.every(function(c){ return counts[c[0]]===M.class_counts[c[0]]; });
  const d=document.getElementById("selfcheck"); d.replaceChildren();
  const s=el("span", ok?"ok":"bad",
    ok ? "Runtime self-check PASS — "+runs.length+" embedded rows match the compiler manifest ("
         +M.lanes+" lanes, $"+M.cost_total.toFixed(2)+", "+fmtHrs(M.wall_total)+" wall)."
       : "Runtime self-check FAIL — embedded rows disagree with the compiler manifest. Recompile.");
  d.appendChild(s);
}

/* ---------- theme toggle ---------- */
(function(){
  const btn=document.getElementById("themebtn");
  const order=["auto","light","dark"];
  let cur="auto";
  try { cur=localStorage.getItem("runs-theme")||"auto"; } catch(e){}
  function apply(){
    if(cur==="auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme",cur);
    btn.textContent="theme: "+cur;
    try { localStorage.setItem("runs-theme",cur); } catch(e){}
  }
  btn.addEventListener("click",function(){ cur=order[(order.indexOf(cur)+1)%order.length]; apply(); });
  apply();
})();

/* ---------- top-level render ---------- */
function render(){
  buildSeg("rangeseg", RANGES, state.range, function(v){ state.range=v; render(); });
  buildSeg("groupseg", GROUPS, state.group, function(v){ state.group=v; render(); });
  const w=windowFor(state.range), rows=visible();
  renderKPIs(rows,w[0],w[1]);
  renderOutcomes(rows);
  renderProcWait(rows);
  renderTimeline(rows,w[0],w[1]);
  renderCost(rows);
}

document.getElementById("stamp").replaceChildren(
  el("span",null,"Snapshot "),
  Object.assign(el("b",null,localDate(STAMP))),
  el("span",null," · "+M.rows+" attempts · "+M.lanes+" lanes · times "+tzLabel()+
    " · compiled from "+M.src+" by runs-dashboard.py (stamp derives from the data, not the clock)"));
document.getElementById("srcline").textContent =
  "Source: "+M.src+
  " · flat export for Grafana/SQL: runs-dashboard.py --grafana runs-grafana.jsonl · filters scope every section above.";

renderInflight();
renderSelfCheck();
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
