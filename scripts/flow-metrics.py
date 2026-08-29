#!/usr/bin/env python3
# PROVENANCE — COUNTED, byte-preserving port of the kept H-192 fixture instrument
# (experiments/runs/H-192/fixture/flow-metrics.py in the source lab; hypothesis
# H-192-flow-instrument-seeded-detection KEPT 2026-08-28, two consecutive counted
# 5/5: all five seeded waste classes flagged at their hand-keyed onsets with the
# correct type and lane, zero false alarms on the replayed healthy window,
# byte-identical re-invocation under pinned --now, exit 0 always, read-only).
# Joins scripts/waste-status.py (0.2.0, the human-readable status report): this
# instrument is the TYPED, machine-joinable alarm surface the H-192 keep promotes;
# waste-status.py remains the prose report over the same committed timestamps.
# Only this provenance header and the consumer-repo-root default differ from the
# counted fixture copy. Optional inputs it cannot find in your repo (a wave-plan
# file, lane dirs) degrade gracefully to empty — the frozen detection logic is
# untouched.
"""flow-metrics.py — typed waste detector over committed artifacts (H-192 candidate).

Lineage: scripts/waste-status.py (the census's metrics report). Where waste-status
prints a status report, this candidate emits TYPED DETECTION LINES — machine-joinable,
one line per detection, from the frozen vocabulary:

  FLOW IDLE-RUNNABLE     lane=<newest-activity anchor>   (Waiting/mura — the 9h16m class)
  FLOW STALE-GATE        lane=wave-plan:<wave>:<H-id>    (the H-170/H-179 sleep class)
  FLOW UNRULED-TERMINAL  lane=<terminal path>            (child-pickup lag — H-190/H-189)
  FLOW VOID-CLUSTER      lane=<lane dir>                 (the 42%-void-rate class)
  FLOW WIP-BREACH        lane=<claimed lanes, sorted>    (contention behind the cap of 3)

Every other line is '# '-prefixed commentary. Exit 0 always — alarm states included
(advisory surface, never a gate). Reads git author timestamps, run-dir lane files,
wave-plan.json, and spec Status lines; NEVER writes. Trace-based per METR: committed
timestamps and lane files only, never self-report. AUTHOR time (%at) on purpose: this
estate re-signs commit batches, which rewrites committer time; %at survives.

Frozen rules (thresholds from the waste-management loop-prompt + waste-status header;
definitions frozen at H-192 registration):

  IDLE-RUNNABLE   idle = now - max(last commit %at, newest EXECUTION-trace mtime across
                  experiments/runs/*/{chain-log.txt, chain-terminal.*}). ALARM when
                  idle > 30m AND the frontier is non-empty. Divergence from
                  waste-status M1, with reason: RUN-CLAIM.json heartbeats are intent,
                  not activity — a claim must not silence the stall it sits inside
                  (the 9h16m stall had live state files but zero execution traces).
  STALE-GATE      any gated_on string of a LIVE wave (>=1 non-kept H-item) matching
                  '(H-NNN) keep' where that spec's line-initial Status is already
                  'kept', unless the string carries a RESOLVED marker. The gate's
                  predicate is satisfied; the sleep behind it is pure waiting.
  UNRULED-TERMINAL  a chain-terminal.* (or chain-log.txt whose tail reads CHAIN
                  STOP/HALT) on a line-initial-active lane, mtime newer than the
                  lane's newest ORCHESTRATOR-RULING.md commit, older than the SLA of
                  one tick (30m). Verdicted/ruled terminals are history, not queue.
  VOID-CLUSTER    over the trailing span, an archived attempt is any suffixed
                  run-N-<suffix> dir; a void (run-*-void-*) is UNRULED while the dir
                  mtime is newer than the lane's newest ORCHESTRATOR-RULING.md commit
                  (the ruling that archives a void rules it; ties are ruled). A lane
                  alarms when it holds an unruled coordination-class void, or holds
                  any unruled void while the estate unruled-void rate > 25%.
                  Divergence from waste-status M3, with reason: ruled voids are
                  acknowledged history — an instrument that alarms forever on every
                  archived void can never be silent on a healthy window.
  WIP-BREACH      fresh claim heartbeats (RUN-CLAIM.json | LANE-STATE.json mtime
                  within 2 ticks) on more than WIP_CAP=3 lanes (CONWIP law).

Usage: flow-metrics.py [--repo PATH] [--now EPOCH] [--span-days N]
Output timestamps are UTC (machine-independent); pinned --now makes re-invocation
over identical inputs byte-identical.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

TICK_MIN = 30
IDLE_ALARM_S = TICK_MIN * 60
SLA_S = TICK_MIN * 60
WIP_CAP = 3
CLAIM_FRESH_S = 2 * TICK_MIN * 60
VOID_ALARM_RATE = 0.25

CLAIM_FILES = ("RUN-CLAIM.json", "LANE-STATE.json")
COORD_RE = r"race|dashboard|hook|orchestrat"
VOCAB = ("IDLE-RUNNABLE", "STALE-GATE", "UNRULED-TERMINAL", "VOID-CLUSTER",
         "WIP-BREACH")


def sh(repo, args):
    try:
        return subprocess.run(["git", "-C", repo] + args,
                              capture_output=True, text=True).stdout
    except OSError:
        return ""


def utc(ts):
    return datetime.datetime.fromtimestamp(
        int(ts), tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mins(seconds):
    return "%dm" % round(seconds / 60.0)


def spec_statuses(repo):
    out = {}
    hyp = os.path.join(repo, "hypotheses")
    if not os.path.isdir(hyp):
        return out
    for fn in sorted(os.listdir(hyp)):
        m = re.match(r"(H-\d+)-.*\.md$", fn)
        if not m:
            continue
        try:
            text = open(os.path.join(hyp, fn), encoding="utf-8",
                        errors="replace").read()
        except OSError:
            continue
        s = re.search(r"^## Status\s*\n(\w+)", text, re.M)
        out[m.group(1)] = s.group(1) if s else "unparsed"
    return out


def ruling_times(repo):
    """{H-NNN: newest author-ts of a commit touching that lane's ORCHESTRATOR-RULING.md}."""
    out = {}
    log = sh(repo, ["log", "--format=%x01%at", "--name-only", "--",
                    "experiments/runs/*/ORCHESTRATOR-RULING.md"])
    ts = None
    for line in log.splitlines():
        if line.startswith("\x01"):
            ts = int(line[1:])
            continue
        m = re.match(r"experiments/runs/(H-\d+)/ORCHESTRATOR-RULING\.md", line.strip())
        if m and ts is not None and m.group(1) not in out:   # newest-first walk
            out[m.group(1)] = ts
    return out


def lane_dirs(repo):
    runs = os.path.join(repo, "experiments", "runs")
    if not os.path.isdir(runs):
        return []
    return sorted(d for d in os.listdir(runs)
                  if re.match(r"H-\d+$", d) and os.path.isdir(os.path.join(runs, d)))


def load_plan(repo):
    p = os.path.join(repo, "experiments", "runs", "DESIGN-post-010-train",
                     "wave-plan.json")
    try:
        return json.load(open(p, encoding="utf-8"))["plan"]["waves"]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def frontier(repo, statuses, waves):
    """First-cut frontier (waste-status lineage): current-wave non-kept items,
    later-wave start-today items, registered-active specs with no run dir."""
    items, current_seen = [], False
    for w in waves:
        hyp = [i for i in w.get("items", []) if re.match(r"^H-\d+$", i)]
        todo = [i for i in hyp if statuses.get(i) != "kept"]
        if todo and not current_seen:
            current_seen = True
            items += ["%s(current %s)" % (i, w.get("version", "?")) for i in todo]
        elif todo and any("start today" in g or "starts today" in g or
                          "start now" in g or "starts now" in g
                          for g in w.get("gated_on", [])):
            items += ["%s(start-today %s)" % (i, w.get("version", "?")) for i in todo]
    have_run = set(lane_dirs(repo))
    unrun = [h for h, s in statuses.items() if s == "active" and h not in have_run]
    items += ["%s(unrun build)" % h for h in sorted(unrun)]
    return items


def is_terminal_file(path, fn):
    if fn.startswith("chain-terminal."):
        return True
    if fn == "chain-log.txt":
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 4096))
                return bool(re.search(rb"CHAIN (STOP|HALT)", f.read()))
        except OSError:
            return False
    return False


def detect_idle(repo, now, frontier_n, detections):
    commit_ts = [int(t) for t in sh(repo, ["log", "--format=%at"]).split()]
    last_commit = max(commit_ts) if commit_ts else None
    newest_exec, newest_path = None, None
    for lane in lane_dirs(repo):
        d = os.path.join(repo, "experiments", "runs", lane)
        for fn in sorted(os.listdir(d)):
            if fn == "chain-log.txt" or fn.startswith("chain-terminal."):
                try:
                    t = os.path.getmtime(os.path.join(d, fn))
                except OSError:
                    continue
                if t <= now and (newest_exec is None or t > newest_exec):
                    newest_exec = t
                    newest_path = "experiments/runs/%s/%s" % (lane, fn)
    anchors = [t for t in (last_commit, newest_exec) if t is not None]
    if not anchors:
        return 0
    anchor = max(anchors)
    idle_s = now - anchor
    if idle_s > IDLE_ALARM_S and frontier_n > 0:
        if newest_exec is not None and newest_exec >= (last_commit or 0):
            lane = newest_path
        else:
            short = sh(repo, ["log", "-1", "--format=%h"]).strip() or "unknown"
            lane = "commit:%s" % short
        detections.append(("IDLE-RUNNABLE", lane,
                           "idle=%s frontier=%d since=%s (claims are intent, not activity)"
                           % (mins(idle_s), frontier_n, utc(anchor))))
    return idle_s


def detect_stale_gates(repo, statuses, waves, detections):
    seen = set()
    for w in waves:
        hyp = [i for i in w.get("items", []) if re.match(r"^H-\d+$", i)]
        todo = [i for i in hyp if statuses.get(i) != "kept"]
        if not todo:
            continue                       # shipped/non-live wave: gates are history
        for g in w.get("gated_on", []):
            if "RESOLVED" in g:
                continue
            for m in re.finditer(r"(H-\d+)\s+keep\b", g):
                hid = m.group(1)
                key = (w.get("version", "?"), hid)
                if statuses.get(hid) == "kept" and key not in seen:
                    seen.add(key)
                    ts = sh(repo, ["log", "-1", "--format=%at", "--",
                                   "hypotheses/%s-*.md" % hid]).strip()
                    since = utc(int(ts)) if ts.isdigit() else "unknown"
                    detections.append(
                        ("STALE-GATE", "wave-plan:%s:%s" % key,
                         "status=kept satisfied-since=%s gate=\"%s\""
                         % (since, g[:48])))


def detect_unruled_terminals(repo, now, statuses, rulings, detections):
    for lane in lane_dirs(repo):
        if statuses.get(lane) != "active":
            continue                       # a terminal on a verdicted lane is history
        d = os.path.join(repo, "experiments", "runs", lane)
        ruled_at = rulings.get(lane, 0)
        for fn in sorted(os.listdir(d)):
            path = os.path.join(d, fn)
            if not os.path.isfile(path) or not is_terminal_file(path, fn):
                continue
            try:
                t = os.path.getmtime(path)
            except OSError:
                continue
            if t <= now and t > ruled_at and (now - t) > SLA_S:
                detections.append(
                    ("UNRULED-TERMINAL", "experiments/runs/%s/%s" % (lane, fn),
                     "age=%s sla=%dm ruled=%s"
                     % (mins(now - t), TICK_MIN,
                        utc(ruled_at) if ruled_at else "never")))


def detect_void_clusters(repo, now, span_days, rulings, detections):
    since = now - span_days * 86400
    attempts, by_lane = 0, {}
    for lane in lane_dirs(repo):
        d = os.path.join(repo, "experiments", "runs", lane)
        ruled_at = rulings.get(lane, 0)
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            m = re.match(r"run-\d+[a-z]?(?:-(.+))?$", fn)
            if not m or not os.path.isdir(p):
                continue
            suffix = m.group(1)
            if suffix is None:
                continue                   # live run-N dir, not an archived attempt
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if not (since <= t <= now):
                continue
            attempts += 1
            if suffix.startswith("void-") and t > ruled_at:
                rec = by_lane.setdefault(lane, {"unruled": 0, "coord": 0, "usd": 0.0})
                rec["unruled"] += 1
                if re.search(COORD_RE, suffix[5:]):
                    rec["coord"] += 1
                for rr in glob.glob(os.path.join(p, "**", "run-record.json"),
                                    recursive=True):
                    try:
                        rec["usd"] += float(json.load(open(rr))
                                            .get("budget", {})
                                            .get("spent_usd_counted", 0))
                    except (ValueError, OSError, AttributeError):
                        pass
    total_unruled = sum(r["unruled"] for r in by_lane.values())
    rate = (total_unruled / attempts) if attempts else 0.0
    for lane in sorted(by_lane):
        rec = by_lane[lane]
        if rec["coord"] > 0 or rate > VOID_ALARM_RATE:
            detections.append(
                ("VOID-CLUSTER", "experiments/runs/%s" % lane,
                 "unruled-voids=%d coordination=%d attempts=%d unruled-rate=%.0f%% usd=%.2f"
                 % (rec["unruled"], rec["coord"], attempts, rate * 100, rec["usd"])))


def detect_wip_breach(repo, now, detections):
    wip = []
    for lane in lane_dirs(repo):
        d = os.path.join(repo, "experiments", "runs", lane)
        for fn in CLAIM_FILES:
            p = os.path.join(d, fn)
            try:
                if os.path.isfile(p) and 0 <= now - os.path.getmtime(p) <= CLAIM_FRESH_S:
                    wip.append(lane)
                    break
            except OSError:
                continue
    if len(wip) > WIP_CAP:
        detections.append(("WIP-BREACH", ",".join(sorted(wip)),
                           "wip=%d/%d claim-fresh<=%dm"
                           % (len(wip), WIP_CAP, CLAIM_FRESH_S // 60)))
    return len(wip)


def main():
    # Plugin convention: this script ships inside the plugin, so a bare "."
    # default would point at wherever the hook ran; prefer the consumer repo.
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    default_repo = env_root if env_root and os.path.isdir(env_root) else "."
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=default_repo)
    ap.add_argument("--now", type=int, default=None)
    ap.add_argument("--span-days", type=int, default=7)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    now = a.now if a.now is not None else int(time.time())
    print("# FLOW-METRICS @ %s repo=%s now=%d span=%dd "
          "thresholds: idle>%dm sla>%dm unruled-void-rate>%d%% wip>%d"
          % (utc(now), repo, now, a.span_days, TICK_MIN, TICK_MIN,
             int(VOID_ALARM_RATE * 100), WIP_CAP))
    if not os.path.isdir(os.path.join(repo, ".git")):
        print("# flow: not a git repo — nothing to read | exit 0 (advisory)")
        return 0
    statuses = spec_statuses(repo)
    waves = load_plan(repo)
    fr = frontier(repo, statuses, waves)
    rulings = ruling_times(repo)
    detections = []
    idle_s = detect_idle(repo, now, len(fr), detections)
    detect_stale_gates(repo, statuses, waves, detections)
    detect_unruled_terminals(repo, now, statuses, rulings, detections)
    detect_void_clusters(repo, now, a.span_days, rulings, detections)
    wip = detect_wip_breach(repo, now, detections)
    order = {c: i for i, c in enumerate(VOCAB)}
    for cls, lane, detail in sorted(detections, key=lambda d: (order[d[0]], d[1])):
        print("FLOW %s lane=%s %s" % (cls, lane, detail))
    print("# flow: %d detection(s) | idle-runnable %s | wip %d/%d | frontier %d "
          "item(s): %s | exit 0 (advisory)"
          % (len(detections), mins(idle_s), wip, WIP_CAP, len(fr),
             ", ".join(fr[:3]) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
