#!/usr/bin/env python3
"""derive-metrics.py -- deterministic metric derivation runner.

PROVENANCE — COUNTED, byte-preserving port of the kept H-129 run-2 authored
runner (experiments/runs/H-129/run-2/authored/derive-metrics.py in the source
lab; hypothesis H-129-autonomy-trend KEPT 2026-08-28, two consecutive counted
5/5: byte-identical double derivation with zero rows re-appended on unchanged
inputs, exact seeded-window shares, census-t0 rows emitted exactly once as
reconstruction-grade, --trend direction verdicts correct on improving/flat/
degrading seeded histories per axis, and lineage recompute leaving superseded
rows byte-untouched). Implements the lab's metrics-model contract section 4 per
its mechanical runner-interface pins. Only this provenance framing differs from
the counted copy; every path is CLI-driven (--repo-root/--model-dir) and the
derived series lands append-only in <repo-root>/ledger/metrics-timeseries.jsonl,
reading tagged journal fragments and <repo-root>/ledger/workflow-facts.jsonl
(the stream scripts/emit_workflow_fact.py writes). Python 3 stdlib only.

  DERIVE mode:
      derive-metrics.py --repo-root <path> --model-dir <relpath> --as-of YYYY-MM-DD
  TREND mode (read-only):
      derive-metrics.py --repo-root <path> --model-dir <relpath> --as-of YYYY-MM-DD \
          --trend <metric-id>
"""

import argparse
import datetime
import glob
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys

SERIES_SCHEMA = "metric-point/v1"

# ---------------------------------------------------------------------------
# Metric-node frontmatter parsing (runner-interface.md sec 2)
# ---------------------------------------------------------------------------


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _is_list_marker(line, indent):
    return _indent_of(line) == indent and line.lstrip(" ").startswith("- ")


def _parse_dict(lines, i, indent):
    d = {}
    n = len(lines)
    while i < n:
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        cur_indent = _indent_of(raw)
        if cur_indent != indent:
            break
        content = raw.strip()
        if content.startswith("- "):
            # A list item at dict-parsing level means our caller mis-dispatched;
            # treat as end of this dict.
            break
        if ": " in content:
            key, val = content.split(": ", 1)
            d[key.strip()] = val.strip()
            i += 1
        elif content.endswith(":"):
            key = content[:-1].strip()
            i += 1
            if i < n and lines[i].strip() and _indent_of(lines[i]) > indent:
                child, i = _parse_block(lines, i, _indent_of(lines[i]))
                d[key] = child
            else:
                d[key] = None
        else:
            i += 1
    return d, i


def _parse_list(lines, i, indent):
    items = []
    n = len(lines)
    while i < n and _indent_of(lines[i]) == indent and _is_list_marker(lines[i], indent):
        rest = lines[i].lstrip(" ")[2:]  # strip "- "
        item_indent = indent + 2
        item_lines = [(" " * item_indent) + rest]
        i += 1
        while i < n and lines[i].strip() and _indent_of(lines[i]) > indent and not (
            _indent_of(lines[i]) == indent and _is_list_marker(lines[i], indent)
        ):
            item_lines.append(lines[i])
            i += 1
        item_dict, _ = _parse_dict(item_lines, 0, item_indent)
        items.append(item_dict)
    return items, i


def _parse_block(lines, i, indent):
    if i < len(lines) and _is_list_marker(lines[i], indent):
        return _parse_list(lines, i, indent)
    return _parse_dict(lines, i, indent)


def _extract_frontmatter_lines(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx]
    return None


def parse_frontmatter(text):
    """Parse the `key: value` (two-space nesting) frontmatter grammar into a dict."""
    fm_lines = _extract_frontmatter_lines(text)
    if fm_lines is None:
        return None
    d, _ = _parse_dict(fm_lines, 0, 0)
    return d


class DerivationError(Exception):
    pass


def load_metric_nodes(model_dir):
    nodes = []
    for path in sorted(glob.glob(os.path.join(model_dir, "metric-*.md"))):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm = parse_frontmatter(text)
        if fm is None or "metric" not in fm:
            continue
        metric = fm.get("metric")
        if not isinstance(metric, dict):
            raise DerivationError(f"malformed node (metric: not a block): {path}")
        node_id = fm.get("id")
        if not node_id:
            raise DerivationError(f"malformed node (missing id:): {path}")
        derivation = metric.get("derivation")
        if not derivation or "#" not in derivation:
            raise DerivationError(f"malformed node (bad derivation:): {path}")
        func_name = derivation.split("#", 1)[1]
        direction = metric.get("direction-of-good")
        if direction not in ("up", "down"):
            raise DerivationError(
                f"unknown enum value for direction-of-good: {direction!r} in {path}"
            )
        sources = metric.get("sources")
        if not isinstance(sources, list) or not sources:
            raise DerivationError(f"malformed node (missing sources:): {path}")
        baseline = metric.get("baseline")
        if not isinstance(baseline, dict):
            raise DerivationError(f"malformed node (missing baseline:): {path}")
        try:
            baseline_norm = {
                "value": float(baseline["value"]),
                "n": int(baseline["n"]),
                "date": baseline["date"],
                "evidence": baseline["evidence"],
            }
        except (KeyError, ValueError) as exc:
            raise DerivationError(f"malformed baseline block in {path}: {exc}") from exc
        nodes.append(
            {
                "id": node_id,
                "path": path,
                "derivation_func": func_name,
                "unit": metric.get("unit"),
                "cadence": metric.get("cadence"),
                "direction": direction,
                "sources": sources,
                "baseline": baseline_norm,
            }
        )
    nodes.sort(key=lambda n: n["id"])
    return nodes


# ---------------------------------------------------------------------------
# Stream resolution and inputs_sha (runner-interface.md sec 3 & 4)
# ---------------------------------------------------------------------------

_REPR_RE = re.compile(r"^(file|journal-fragment)\((.*)\)$")


def _resolve_source(representation, repo_root):
    m = _REPR_RE.match(representation or "")
    if not m:
        raise DerivationError(f"unknown source representation: {representation!r}")
    kind, arg = m.group(1), m.group(2)
    if kind == "file":
        return "file", os.path.join(repo_root, arg)
    # journal-fragment(<key>) always resolves to the fixed fragments dir.
    return "journal-fragment", os.path.join(repo_root, "experiments", "journal-fragments")


def resolve_stream_files(node, repo_root):
    """Ordered, de-duplicated list of concrete file paths feeding this metric."""
    files = []
    seen = set()
    for src in node["sources"]:
        kind, target = _resolve_source(src.get("representation"), repo_root)
        if kind == "file":
            if target not in seen:
                seen.add(target)
                files.append(target)
        else:
            if target in seen:
                continue
            seen.add(target)
            if os.path.isdir(target):
                for fname in sorted(os.listdir(target)):
                    if fname.endswith(".md"):
                        fp = os.path.join(target, fname)
                        if fp not in seen:
                            seen.add(fp)
                            files.append(fp)
    return files


def compute_inputs_sha(node, repo_root):
    h = hashlib.sha256()
    for fp in resolve_stream_files(node, repo_root):
        if not os.path.isfile(fp):
            raise DerivationError(f"unreadable stream file: {fp}")
        with open(fp, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def get_git_sha(repo_root):
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Window arithmetic (runner-interface.md sec 4: ISO calendar weeks, Mon-Sun)
# ---------------------------------------------------------------------------


def week_bounds(d):
    monday = d - datetime.timedelta(days=d.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def _read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _windows_to_rows(windows, as_of_date):
    rows = []
    for (wfrom, wto), (num, den) in windows.items():
        if den == 0:
            continue
        if not (datetime.date.fromisoformat(wto) < as_of_date):
            continue
        rows.append({"window": {"from": wfrom, "to": wto}, "value": num / den, "n": den})
    rows.sort(key=lambda r: r["window"]["from"])
    return rows


# ---------------------------------------------------------------------------
# The three named derivation functions (runner-interface.md sec 4)
# ---------------------------------------------------------------------------

_ORIGINATION_ENUM = {
    "maintainer-directive",
    "refine-successor",
    "instrument-demanded",
    "agent-initiated",
    "external-issue",
}

_ASK_CLASS_ENUM = {
    "genuine-preference",
    "discoverable-fact",
    "experiment-answerable",
    "mixed",
}


def origination_autonomy(repo_root, as_of_date):
    # DERIVATION-VERSION origination-autonomy 1
    frag_dir = os.path.join(repo_root, "experiments", "journal-fragments")
    windows = {}
    if os.path.isdir(frag_dir):
        for fname in sorted(os.listdir(frag_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(frag_dir, fname)
            with open(path, "r", encoding="utf-8") as fh:
                fm = parse_frontmatter(fh.read())
            if not fm or "origination" not in fm:
                continue
            origination = fm["origination"]
            if origination not in _ORIGINATION_ENUM:
                raise DerivationError(f"unknown origination enum value: {origination!r} in {path}")
            date_str = fm.get("date")
            if not date_str:
                raise DerivationError(f"fragment missing date:: {path}")
            d = datetime.date.fromisoformat(date_str)
            monday, sunday = week_bounds(d)
            key = (monday.isoformat(), sunday.isoformat())
            num, den = windows.get(key, (0, 0))
            den += 1
            if origination == "agent-initiated":
                num += 1
            windows[key] = (num, den)
    return _windows_to_rows(windows, as_of_date)


def execution_autonomy(repo_root, as_of_date):
    # DERIVATION-VERSION execution-autonomy 1
    path = os.path.join(repo_root, "ledger", "workflow-facts.jsonl")
    facts = _read_jsonl(path)
    ask_workflows = set()
    closed = []
    for obj in facts:
        if obj.get("schema") != "workflow-fact/v1":
            continue
        kind = obj.get("kind")
        if kind == "maintainer-ask":
            wf = obj.get("workflow")
            if wf:
                ask_workflows.add(wf)
        elif kind == "workflow-closed":
            if obj.get("workflow_class") == "hypothesis-lifecycle":
                closed.append(obj)
    windows = {}
    for obj in closed:
        ts = obj.get("ts")
        if not ts:
            raise DerivationError(f"workflow-closed row missing ts: {obj}")
        d = datetime.date.fromisoformat(ts[:10])
        monday, sunday = week_bounds(d)
        key = (monday.isoformat(), sunday.isoformat())
        num, den = windows.get(key, (0, 0))
        den += 1
        if obj.get("workflow") not in ask_workflows:
            num += 1
        windows[key] = (num, den)
    return _windows_to_rows(windows, as_of_date)


def ask_rate(repo_root, as_of_date):
    # DERIVATION-VERSION ask-rate 1
    path = os.path.join(repo_root, "ledger", "workflow-facts.jsonl")
    facts = _read_jsonl(path)
    windows = {}
    for obj in facts:
        if obj.get("schema") != "workflow-fact/v1":
            continue
        if obj.get("kind") != "maintainer-ask":
            continue
        cls = obj.get("class")
        if cls not in _ASK_CLASS_ENUM:
            raise DerivationError(f"unknown maintainer-ask class enum value: {cls!r}")
        ts = obj.get("ts")
        if not ts:
            raise DerivationError(f"maintainer-ask row missing ts: {obj}")
        d = datetime.date.fromisoformat(ts[:10])
        monday, sunday = week_bounds(d)
        key = (monday.isoformat(), sunday.isoformat())
        num, den = windows.get(key, (0, 0))
        den += 1
        if cls in ("discoverable-fact", "experiment-answerable"):
            num += 1
        windows[key] = (num, den)
    return _windows_to_rows(windows, as_of_date)


DERIVATION_FUNCS = {
    "origination_autonomy": origination_autonomy,
    "execution_autonomy": execution_autonomy,
    "ask_rate": ask_rate,
}


def derivation_sha(func):
    return hashlib.sha256(inspect.getsource(func).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# DERIVE mode
# ---------------------------------------------------------------------------


def canonical_dump(obj):
    return json.dumps(obj, sort_keys=True)


def existing_series_keys(series_path):
    keys = set()
    for obj in _read_jsonl(series_path):
        key = (
            obj.get("metric"),
            (obj.get("window", {}).get("from"), obj.get("window", {}).get("to")),
            obj.get("derivation_sha"),
            obj.get("inputs_sha"),
        )
        keys.add(key)
    return keys


def run_derive(repo_root, model_dir, as_of_str):
    as_of_date = datetime.date.fromisoformat(as_of_str)
    nodes = load_metric_nodes(model_dir)
    series_path = os.path.join(repo_root, "ledger", "metrics-timeseries.jsonl")
    os.makedirs(os.path.dirname(series_path), exist_ok=True)
    keys = existing_series_keys(series_path)
    sha_head = get_git_sha(repo_root)

    new_rows = []
    for node in nodes:
        func = DERIVATION_FUNCS.get(node["derivation_func"])
        if func is None:
            raise DerivationError(f"unknown derivation function: {node['derivation_func']}")
        dsha = derivation_sha(func)
        isha = compute_inputs_sha(node, repo_root)

        # t0 row (reconstruction-grade), emitted exactly once, never under any
        # later lineage.
        baseline = node["baseline"]
        t0_key = (
            node["id"],
            (baseline["date"], baseline["date"]),
            "reconstruction",
            "reconstruction",
        )
        if t0_key not in keys:
            t0_row = {
                "schema": SERIES_SCHEMA,
                "metric": node["id"],
                "window": {"from": baseline["date"], "to": baseline["date"]},
                "value": baseline["value"],
                "unit": node["unit"],
                "n": baseline["n"],
                "reconstruction_grade": True,
                "derivation_sha": "reconstruction",
                "inputs_sha": "reconstruction",
                "sha": sha_head,
                "ts": as_of_str,
                "evidence": baseline["evidence"],
            }
            new_rows.append(t0_row)
            keys.add(t0_key)

        for row in func(repo_root, as_of_date):
            key = (node["id"], (row["window"]["from"], row["window"]["to"]), dsha, isha)
            if key in keys:
                continue
            out_row = {
                "schema": SERIES_SCHEMA,
                "metric": node["id"],
                "window": row["window"],
                "value": row["value"],
                "unit": node["unit"],
                "n": row["n"],
                "reconstruction_grade": False,
                "derivation_sha": dsha,
                "inputs_sha": isha,
                "sha": sha_head,
                "ts": as_of_str,
            }
            new_rows.append(out_row)
            keys.add(key)

    if new_rows:
        with open(series_path, "a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(canonical_dump(row) + "\n")

    print(f"derive-metrics: appended {len(new_rows)} row(s) to {series_path}")
    return 0


# ---------------------------------------------------------------------------
# TREND mode
# ---------------------------------------------------------------------------


def run_trend(repo_root, model_dir, as_of_str, metric_id):
    nodes = load_metric_nodes(model_dir)
    node = next((n for n in nodes if n["id"] == metric_id), None)
    if node is None:
        raise DerivationError(f"unknown metric id: {metric_id}")

    series_path = os.path.join(repo_root, "ledger", "metrics-timeseries.jsonl")
    all_rows = [r for r in _read_jsonl(series_path) if r.get("metric") == metric_id]

    non_recon = [r for r in all_rows if r.get("derivation_sha") != "reconstruction"]
    newest_sha = non_recon[-1]["derivation_sha"] if non_recon else ""
    lineage_rows = [r for r in non_recon if r.get("derivation_sha") == newest_sha]
    lineage_rows.sort(key=lambda r: r["window"]["from"])

    direction_good = node["direction"]
    series = []
    prev_value = None
    for i, r in enumerate(lineage_rows):
        if i == 0:
            direction = None
        elif r["value"] == prev_value:
            direction = "flat"
        elif direction_good == "up":
            direction = "improving" if r["value"] > prev_value else "degrading"
        else:
            direction = "improving" if r["value"] < prev_value else "degrading"
        series.append(
            {
                "direction": direction,
                "n": r["n"],
                "value": r["value"],
                "window": r["window"],
            }
        )
        prev_value = r["value"]

    non_null = [s["direction"] for s in series if s["direction"] is not None]
    if not non_null or all(d == "flat" for d in non_null):
        verdict = "flat"
    elif all(d in ("improving", "flat") for d in non_null):
        verdict = "improving"
    elif all(d in ("degrading", "flat") for d in non_null):
        verdict = "degrading"
    else:
        verdict = "mixed"

    doc = {
        "direction_of_good": direction_good,
        "metric": metric_id,
        "newest_derivation_sha": newest_sha,
        "series": series,
        "verdict": verdict,
    }
    print(canonical_dump(doc))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--trend", default=None)
    args = parser.parse_args(argv)

    try:
        datetime.date.fromisoformat(args.as_of)
    except ValueError as exc:
        print(f"error: bad --as-of date: {exc}", file=sys.stderr)
        return 1

    try:
        if args.trend:
            return run_trend(args.repo_root, args.model_dir, args.as_of, args.trend)
        return run_derive(args.repo_root, args.model_dir, args.as_of)
    except DerivationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
