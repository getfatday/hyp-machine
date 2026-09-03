#!/usr/bin/env python3
"""H-246 flow-leak meter — read-only aggregate flow-efficiency detector (backtest form).

Stateless function of (git repo history, pinned terminal-times snapshot, CONSTANTS.json,
--now). Emits the frozen FLOW grammar on stdout; optional --emit appends metric-point/v1
rows; optional --selflog appends leak-selflog/v1 rows (acted=null). Exit 0 always.
Contract: fixture/ALGORITHM.md. It reads NO harness file, NO episode key, NO strips
manifest, and NEVER touches any work-ledger.jsonl (fragment-0235 anti-clog bound).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

UTC = dt.timezone.utc


def iso(epoch):
    return dt.datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_now(s):
    d = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int(d.timestamp())


def git(repo, *args):
    out = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    return out.returncode, out.stdout.decode("utf-8", errors="replace")


def load_events(repo, pinned, w0, now):
    rc, out = git(repo, "log", "--format=%at", pinned)
    if rc != 0:
        return None
    return sorted(int(x) for x in out.split() if w0 <= int(x) <= now)


def load_terminals(path, w0, now):
    events, lanes = [], {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            p, ep = line.split("\t")
            ep = int(ep)
            if w0 <= ep <= now:
                events.append(ep)
            d = os.path.dirname(p)
            ph = os.path.basename(p).replace("chain-terminal.", "")
            lanes.setdefault(d, {})[ph] = ep
    return sorted(events), lanes


def intervals(events, cluster, grain):
    iv = []
    for e in events:
        if iv and e - iv[-1][1] <= cluster:
            iv[-1][1] = e
        else:
            iv.append([e, e])
    return [(a - grain, b + grain) for a, b in iv]


def coverage(iv, a, b):
    return sum(max(0, min(y, b) - max(x, a)) for x, y in iv)


def eff(iv, w0, now, w):
    a = max(w0, now - w)
    if now <= a:
        return 0.0
    return coverage(iv, a, now) / float(now - a)


def burnshare(iv, w0, now, C):
    slots = [now - i * C["TICK_S"] for i in range(C["WINDOW_SLOW"] // C["TICK_S"])]
    slots = [s for s in slots if s - C["WINDOW_LONG"] >= w0]
    if not slots:
        return 0.0
    burning = sum(1 for s in slots if eff(iv, w0, s, C["WINDOW_LONG"]) < C["SLO_EFF"])
    return burning / float(len(slots))


PHASES = [("gate", "run1"), ("run1", "grade1"), ("grade1", "run2"), ("run2", "grade2")]


def cusum_states(lanes, w0, now, C):
    units = {}
    for d, ph in lanes.items():
        for a, b in PHASES:
            if a in ph and b in ph and ph[b] >= ph[a] and w0 <= ph[b] <= now:
                units.setdefault("%s-%s" % (a, b), []).append((ph[b], (ph[b] - ph[a]) / 60.0))
    states = []
    for cls in sorted(C["CUSUM"]):
        p = C["CUSUM"][cls]
        if not p.get("enabled"):
            states.append((cls, None, p))
            continue
        s = 0.0
        for _, m in sorted(units.get(cls, [])):
            s = max(0.0, s + min(m, p["wins"]) - (p["mu0"] + p["k"]))
        states.append((cls, s, p))
    return states


def frontier_open(repo, pinned, now, closed_set):
    rc, out = git(repo, "rev-list", "-1", "--before=%s" % iso(now), pinned)
    asof = out.strip()
    if rc != 0 or not asof:
        return True, "none"
    rc, out = git(repo, "grep", "-h", "-A1", "-e", "^## Status", asof, "--", "hypotheses/")
    if rc not in (0, 1):
        return True, asof[:12]
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("## Status") and i + 1 < len(lines):
            word = lines[i + 1].strip().split()[0].lower() if lines[i + 1].strip() else ""
            word = word.rstrip(";:,.")
            if word and word not in closed_set:
                return True, asof[:12]
    return False, asof[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pinned", required=True)
    ap.add_argument("--terminals", required=True)
    ap.add_argument("--constants", required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--emit")
    ap.add_argument("--selflog")
    o = ap.parse_args()

    C = json.load(open(o.constants, encoding="utf-8"))
    now = parse_now(o.now)
    w0 = parse_now(C["WINDOW_START"])

    events = load_events(o.repo, o.pinned, w0, now)
    if events is None:
        print("# error: git history unreadable (repo=%s pinned=%s)" % (o.repo, o.pinned))
        return 0
    tevents, lanes = load_terminals(o.terminals, w0, now)
    allev = sorted(set(events + tevents))
    iv = intervals(allev, C["CLUSTER_S"], C["GRAIN_S"])

    open_frontier, asof = frontier_open(o.repo, o.pinned, now, set(C["CLOSED_SET"]))

    e30 = eff(iv, w0, now, C["WINDOW_SHORT"])
    e6 = eff(iv, w0, now, C["WINDOW_LONG"])
    e3d = eff(iv, w0, now, C["WINDOW_SLOW"])
    bs = burnshare(iv, w0, now, C)
    cus = cusum_states(lanes, w0, now, C)

    alarms = []
    if e6 < C["FLOOR_FAST_6H"] and e30 <= C["FLOOR_FAST_30M"]:
        alarms.append(("FLOW BURN-FAST", "BURN-FAST", None, "6h+30m"))
    if bs >= C["SUSTAIN"]:
        alarms.append(("FLOW BURN-SLOW", "BURN-SLOW", None, "3d"))
    for cls, s, p in cus:
        if s is not None and s > p["h"]:
            alarms.append(("FLOW CUSUM-SHIFT class=%s" % cls, "CUSUM-SHIFT", cls, "series"))

    out = []
    out.append("# now=%s asof=%s frontier=%s events=%d"
               % (o.now, asof, "open" if open_frontier else "empty", len(allev)))
    out.append("FLOW EFFICIENCY window=30m value=%.6f floor=%.6f" % (e30, C["FLOOR_FAST_30M"]))
    out.append("FLOW EFFICIENCY window=6h value=%.6f floor=%.6f" % (e6, C["FLOOR_FAST_6H"]))
    out.append("FLOW EFFICIENCY window=3d value=%.6f floor=%.6f" % (e3d, C["SLO_EFF"]))
    out.append("# slow burnshare3d=%.6f sustain=%.6f" % (bs, C["SUSTAIN"]))
    for cls, s, p in cus:
        if s is None:
            out.append("# cusum %s disabled" % cls)
        else:
            out.append("# cusum %s S=%.6f h=%.6f" % (cls, s, p["h"]))
    emitted_alarms = []
    if alarms and not open_frontier:
        out.append("# alarms-gated frontier-empty")
    elif alarms:
        for line, _, _, _ in alarms:
            out.append(line)
        emitted_alarms = alarms

    print("\n".join(out))

    windows = [("30m", e30, C["WINDOW_SHORT"]), ("6h", e6, C["WINDOW_LONG"]),
               ("3d", e3d, C["WINDOW_SLOW"])]
    if o.emit:
        dsha = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
        tsha = hashlib.sha256(open(o.terminals, "rb").read()).hexdigest()
        isha = hashlib.sha256(("%s|%s|%s" % (o.pinned, tsha, o.now)).encode()).hexdigest()
        with open(o.emit, "a", encoding="utf-8") as f:
            for name, val, wsec in windows:
                a = max(w0, now - wsec)
                row = {"schema": "metric-point/v1",
                       "metric": "read-model/metric-flow-efficiency",
                       "ts": o.now, "unit": "ratio", "value": round(val, 6),
                       "n": sum(1 for e in allev if a <= e <= now),
                       "window": {"from": iso(a), "to": o.now},
                       "derivation_sha": dsha, "inputs_sha": isha,
                       "reconstruction_grade": True, "sha": ""}
                f.write(json.dumps(row, sort_keys=True) + "\n")
    if o.selflog and emitted_alarms:
        with open(o.selflog, "a", encoding="utf-8") as f:
            for _, alarm, cls, win in emitted_alarms:
                row = {"schema": "leak-selflog/v1", "channel": "flow-leak-meter",
                       "ts": o.now, "alarm": alarm, "class": cls, "window": win,
                       "acted": None}
                f.write(json.dumps(row, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # exit 0 always; the error is visible in the grammar
        print("# error: %s: %s" % (type(e).__name__, e))
        sys.exit(0)
