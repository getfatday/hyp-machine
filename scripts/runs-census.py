#!/usr/bin/env python3
"""runs-census.py -- one row per run attempt under the consumer's runs dir (R2 census port).

PROVENANCE -- port of the source lab's R2 attempt census
(experiments/runs/DESIGN-trial-management/census.py; the data layer whose
generated board H-269-runs-census-board KEPT 2026-09-05, run-1 5/5 -- readers
of the compiled board beat raw-artifact readers on the frozen cost/outcome/
stopped-early probes). Read-only over lane dirs; writes only runs.jsonl.

Named divergences from the lab copy (consumer resolution + one recorded fix):
  - paths resolve from --root (default cwd) + `.claude/hyp.json`
    (`runs_dir` default experiments/runs, `hypotheses_dir` default hypotheses);
    output default <runs_dir>/runs.jsonl -- the census emits into the consumer
    repo's own runs dir (the lab wrote into its design lane);
  - THE H-254 FUTURE-DATE FIX (the lab census defect recorded open in the R4
    write-race ruling: three rows carried an end_ts two days ahead of the
    census clock, fabricated from a run-record field, poisoning the board's
    data-derived stamp): any derived end timestamp AHEAD of the census clock is
    clamped to null with one CENSUS-WARN line on stderr naming the row and the
    rejected value; wall_seconds then falls back per the normal source rules.

One row per run attempt (run-* / counting-run-* / calibration* subdir, plus
lane-level gate attempts evidenced by chain-terminal.gate / gate-stdout.log).
Timestamps are best-effort: min st_birthtime (start) / max st_mtime (end) of
files inside the attempt dir, overridden by run-record.json fields when present.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

RUN_DIR_RE = re.compile(r"^(run|counting-run|calibration)")
NOFM_RE = re.compile(r"counted-(\d+)of(\d+)")
CLASS_ORDER = [
    (re.compile(r"void[-_]?(.*)$"), lambda m: "void-" + (m.group(1) or "unspecified")),
    (re.compile(r"budget-exceeded"), lambda m: "budget-exceeded"),
    (re.compile(r"refuse[-_]?(.*)$"), lambda m: "refused-" + (m.group(1) or "unspecified")),
    (re.compile(r"hold[-_]?(.*)$"), lambda m: "hold-" + (m.group(1) or "unspecified")),
    (re.compile(r"specimen"), lambda m: "specimen"),
    (re.compile(r"smoke"), lambda m: "smoke"),
    (re.compile(r"uncounted"), lambda m: "uncounted"),
]


def consumer_cfg(root):
    cfg = {"runs_dir": "experiments/runs", "hypotheses_dir": "hypotheses"}
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in cfg:
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    cfg[key] = v.strip().strip("/")
    except Exception:
        pass
    return cfg


def iso(ts):
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def clamp_future(ts, now, lane, label):
    """The H-254 fix: an end timestamp ahead of the census clock is a recorded
    defect class (fabricated/misread run-record field), never data -- clamp to
    null with one warning naming the row."""
    if ts is not None and ts > now:
        print("CENSUS-WARN future end_ts %s on %s/%s clamped to null "
              "(ahead of the census clock; H-254 defect class)"
              % (iso(ts), lane, label), file=sys.stderr)
        return None
    return ts


def lane_statuses(root, hyp_dir):
    """Map H-NNN -> first word of ## Status section in its hypothesis spec."""
    st = {}
    for p in glob.glob(os.path.join(root, hyp_dir, "H-*.md")):
        m = re.match(r"(H-\d+)", os.path.basename(p))
        if not m:
            continue
        lane = m.group(1)
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        sm = re.search(r"^## Status\s*\n+\s*([A-Za-z][A-Za-z-]*)", txt, re.M)
        if sm:
            st[lane] = sm.group(1).lower()
    return st


def last_json_line(path, tail_bytes=16384):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            data = f.read().decode("utf-8", errors="replace")
        for line in reversed(data.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return None


def scan_dir(d):
    """Stats over an attempt dir: file count, bytes, min birthtime, max mtime,
    stream costs, run-record fields, claims, amendments, score files."""
    info = dict(files=0, bytes=0, tmin=None, tmax=None, streams=0,
                stream_cost=0.0, stream_dur_ms=0, stream_children=0,
                run_record=None, claim=None, amendments=0, scores=[],
                has_driver_log=False)
    for base, dirs, files in os.walk(d):
        # fixture corpora copied inside a run dir are artifacts of the run;
        # count their bytes but never their stream files as run children/cost
        in_fixture = "/fixture" in base or "/corpus" in base
        for fn in files:
            p = os.path.join(base, fn)
            try:
                stt = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            info["files"] += 1
            info["bytes"] += stt.st_size
            bt = getattr(stt, "st_birthtime", stt.st_mtime)
            info["tmin"] = bt if info["tmin"] is None else min(info["tmin"], bt)
            info["tmax"] = stt.st_mtime if info["tmax"] is None else max(info["tmax"], stt.st_mtime)
            if fn.startswith("stream--") and fn.endswith(".jsonl") and not in_fixture:
                info["streams"] += 1
                d2 = last_json_line(p)
                if d2 and d2.get("type") == "result":
                    info["stream_children"] += 1
                    if isinstance(d2.get("total_cost_usd"), (int, float)):
                        info["stream_cost"] += d2["total_cost_usd"]
                    if isinstance(d2.get("duration_ms"), (int, float)):
                        info["stream_dur_ms"] += int(d2["duration_ms"])
            elif fn.startswith("run-record") and fn.endswith(".json") and base == d:
                try:
                    info["run_record"] = json.load(open(p))
                except (OSError, json.JSONDecodeError):
                    pass
            elif fn == "RUN-CLAIM.json" and base == d:
                try:
                    info["claim"] = json.load(open(p))
                except (OSError, json.JSONDecodeError):
                    pass
            elif "AMENDMENT" in fn.upper():
                info["amendments"] += 1
            elif "score" in fn and fn.endswith(".json") and not in_fixture and base == d:
                info["scores"].append(fn)
            elif fn == "driver-stdout.log":
                info["has_driver_log"] = True
    return info


def parse_ts(s):
    if not isinstance(s, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def extract_record_fields(rr):
    """Best-effort pull of wall/cost/children/start/end from heterogeneous run-records."""
    out = dict(wall=None, cost=None, children=None, start=None, end=None, driver=None)
    if not isinstance(rr, dict):
        return out
    b = rr.get("budget") if isinstance(rr.get("budget"), dict) else {}
    for k in ("wall_clock_s", "wall_s"):
        v = b.get(k, rr.get(k))
        if isinstance(v, (int, float)):
            out["wall"] = float(v); break
    cands = [b.get("spent_usd_counted"), b.get("spent_usd"), rr.get("cost_usd"),
             rr.get("spent_usd"), b.get("cost_usd")]
    for v in cands:
        if isinstance(v, (int, float)):
            out["cost"] = float(v); break
    for k in ("children", "legs", "arms", "trials", "phases", "stages"):
        v = rr.get(k)
        if isinstance(v, dict):
            out["children"] = len(v); break
        if isinstance(v, list):
            out["children"] = len(v); break
    for k in ("started_at", "started", "t0", "utc", "start"):
        t = parse_ts(rr.get(k))
        if t:
            out["start"] = t; break
    for k in ("finished", "ended_at", "end"):
        t = parse_ts(rr.get(k))
        if t:
            out["end"] = t; break
    if isinstance(rr.get("driver"), str):
        out["driver"] = rr["driver"]
    return out


def classify(name):
    low = name.lower()
    for rx, fn in CLASS_ORDER:
        m = rx.search(low)
        if m:
            return fn(m)
    if low.startswith("calibration"):
        return "calibration"
    m = NOFM_RE.search(low)
    if m:
        return "counted"
    return "counted"  # plain run-N: counted attempt; final class overlaid from lane status


def overlay(cls, lane_status, tmax, now):
    if cls != "counted":
        return cls
    s = (lane_status or "")
    if s.startswith("kept"):
        return "kept"
    if s.startswith("discard"):
        return "counted-fail"
    if s.startswith("refine"):  # refined, refined-into, refine
        return "counted-refine"
    if s.startswith("parked"):
        return "parked"
    if s.startswith("void"):
        return "void-at-build"
    recent = tmax and (now - tmax) < 7 * 86400
    if s.startswith(("active", "draft", "registered")):
        return "in-flight" if recent else "parked"  # de facto parked: active spec, stale artifacts
    if recent:
        return "in-flight"
    return "counted-unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd(),
                    help="consumer repo root (default: cwd)")
    ap.add_argument("--out", default=None,
                    help="output path (default: <runs_dir>/runs.jsonl)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    cfg = consumer_cfg(root)
    runs = os.path.join(root, *cfg["runs_dir"].split("/"))
    out = args.out or os.path.join(runs, "runs.jsonl")
    if not os.path.isdir(runs):
        print("runs-census: no runs dir at %s" % runs, file=sys.stderr)
        return 2

    now = datetime.datetime.now().timestamp()
    statuses = lane_statuses(root, cfg["hypotheses_dir"])
    rows = []
    for lane in sorted(os.listdir(runs)):
        lane_dir = os.path.join(runs, lane)
        if not os.path.isdir(lane_dir) or lane.startswith("."):
            continue
        lane_key = re.match(r"(H-\d+)", lane)
        lane_status = statuses.get(lane_key.group(1)) if lane_key else None
        # lane-level chain markers
        terminals = {}
        for p in glob.glob(os.path.join(lane_dir, "chain-terminal.*")):
            stage = os.path.basename(p).split(".", 1)[1]
            try:
                terminals[stage] = dict(
                    exit=open(p).read().strip(),
                    mtime=os.stat(p).st_mtime)
            except OSError:
                pass
        has_chain_sh = bool(glob.glob(os.path.join(lane_dir, "chain*.sh")))
        gate_log = os.path.join(lane_dir, "gate-stdout.log")

        run_dirs = []
        for e in sorted(os.listdir(lane_dir)):
            full = os.path.join(lane_dir, e)
            if os.path.isdir(full) and RUN_DIR_RE.match(e):
                run_dirs.append(e)

        # gate attempt row (chain gate probe is an LLM attempt)
        if "gate" in terminals or os.path.exists(gate_log):
            g = terminals.get("gate", {})
            gm = g.get("mtime") or (os.stat(gate_log).st_mtime if os.path.exists(gate_log) else None)
            gm = clamp_future(gm, now, lane, "(gate)")
            rows.append(dict(
                lane=lane, run_label="(gate)", verdict_class="gate",
                gate_exit=g.get("exit"), lane_status=lane_status,
                start_ts=None, end_ts=iso(gm),
                wall_seconds=None, llm_cost_usd=None, cost_source=None,
                children=None, launch_mode="chain" if has_chain_sh else "unknown",
                files=None, bytes=None, amendments=0, score=None,
                evidence=dict(chain_terminal=sorted(terminals),
                              gate_stdout=os.path.exists(gate_log))))

        for rd in run_dirs:
            full = os.path.join(lane_dir, rd)
            info = scan_dir(full)
            rr = extract_record_fields(info["run_record"])
            cls = classify(rd)
            score = None
            m = NOFM_RE.search(rd)
            if m:
                score = "%sof%s" % (m.group(1), m.group(2))
            start = rr["start"] or info["tmin"]
            end = rr["end"] or info["tmax"]
            end = clamp_future(end, now, lane, rd)  # the H-254 fix
            wall = rr["wall"]
            wall_src = "run-record" if wall is not None else None
            if wall is None and start and end and end > start:
                wall = round(end - start)
                wall_src = "mtime-span"
            cost = rr["cost"]; cost_src = "run-record" if cost is not None else None
            if cost is None and info["stream_cost"] > 0:
                cost = round(info["stream_cost"], 4); cost_src = "streams"
            children = rr["children"] if rr["children"] is not None else (info["stream_children"] or None)
            # launch mode
            if rr["driver"]:
                mode = "driver-script"
            elif info["claim"] is not None or info["has_driver_log"]:
                mode = "chain"
            elif has_chain_sh and terminals:
                mode = "chain"
            elif has_chain_sh:
                mode = "chain-prepped"
            else:
                mode = "unknown"
            vclass = overlay(cls, lane_status, info["tmax"], now)
            rows.append(dict(
                lane=lane, run_label=rd, verdict_class=vclass,
                lane_status=lane_status, score=score,
                start_ts=iso(start), end_ts=iso(end),
                wall_seconds=int(wall) if wall is not None else None,
                wall_source=wall_src,
                llm_cost_usd=round(cost, 4) if cost is not None else None,
                cost_source=cost_src,
                children=children,
                launch_mode=mode,
                files=info["files"], bytes=info["bytes"],
                streams=info["streams"], amendments=info["amendments"],
                evidence=dict(run_record=info["run_record"] is not None,
                              claim=info["claim"] is not None,
                              scores=info["scores"][:4],
                              chain_terminal=sorted(terminals))))

        # lane with chain markers but zero run dirs -> in-flight/prepped lane row
        if not run_dirs and (terminals or has_chain_sh):
            tmax = max((t["mtime"] for t in terminals.values()), default=None)
            tmax = clamp_future(tmax, now, lane, "(lane-root)")
            rows.append(dict(
                lane=lane, run_label="(lane-root)",
                verdict_class="in-flight" if tmax and (now - tmax) < 7*86400 else "prepped-no-run",
                lane_status=lane_status, score=None,
                start_ts=None, end_ts=iso(tmax), wall_seconds=None,
                llm_cost_usd=None, cost_source=None, children=None,
                launch_mode="chain-prepped" if has_chain_sh else "unknown",
                files=None, bytes=None, amendments=0,
                evidence=dict(chain_terminal=sorted(terminals))))

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    # summary to stdout
    from collections import Counter
    c = Counter(r["verdict_class"].split("-")[0] if r["verdict_class"].startswith("void") else r["verdict_class"] for r in rows)
    print("rows:", len(rows))
    print("classes:", c.most_common())
    tot_cost = sum(r["llm_cost_usd"] or 0 for r in rows)
    print("total recorded LLM cost USD:", round(tot_cost, 2))
    walls = [r["wall_seconds"] for r in rows if r.get("wall_seconds")]
    print("attempts with wall:", len(walls), "sum wall hours:", round(sum(walls)/3600, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
