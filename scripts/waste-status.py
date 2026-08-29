#!/usr/bin/env python3
# PROVENANCE — advisory flow instrument, UNCOUNTED-BUT-MEASURED: live output
# recorded read-only against the source lab 2026-08-27 (pinned --now, exit 0,
# two pinned-now invocations byte-compared IDENTICAL, writes nothing — git-log
# subprocesses and file reads only). The registered proving specs are
# H-192-flow-instrument-seeded-detection through H-196 (H-192 seeded-detection,
# H-193 frontier-dispatch-throughput, H-194 rearm-idle-ceiling,
# H-195 conwip-cap-contention-voids, H-196 audit-tick-unprompted-reflection).
# H-192 KEPT 2026-08-28 (two consecutive counted 5/5): its counted instrument
# ships beside this one as scripts/flow-metrics.py — the typed, machine-joinable
# detection surface (FLOW <CLASS> lane=... lines, five classes). This report
# stays the human-readable prose form over the same committed timestamps; both
# ship on the advisory pattern (never a gate). Only this provenance header
# and the consumer-repo-root default differ from the measured lab copy.
"""waste-status.py — the census's top-3 waste metrics from committed timestamps alone.

First-cut instrument for the 2026-08-27 waste-management program in the source lab
(maintainer directive: research/raw/2026-08-27-waste-management-kanban-directive.md —
the 24-hour question); proving specs H-192..H-196 registered there.
The advisory-instrument pattern: advisory, deterministic, exit 0 always, reads git-tracked
state plus lane files — never writes. Trace-based per METR: git author timestamps,
run-dir file mtimes, wave-plan.json, and spec Status lines; never self-report.

The three metrics are the census's top three waste classes by measured damage:

  M1 IDLE-RUNNABLE  (Waiting/mura — the 9h16m overnight-stall class)
      idle_runnable = now - max(last commit author-ts,
                                newest mtime across experiments/runs/*/
                                {chain-log.txt, chain-terminal.*, RUN-CLAIM.json})
      ALARM when idle_runnable > 30 min (2x the longest gate interval — the
      continuous-operation TTL proposal) AND the frontier is non-empty.
      Context: trailing-7d stall-window census over commit gaps.
      AUTHOR time (%at) on purpose: this estate re-signs commit batches, which
      rewrites committer time — a %ct reading fabricated a 27.3h stall and
      compressed real 08-23/08-24 work into an 18 s batch window. %at survives.

  M2 TERMINAL PICKUP  (Waiting — child-pickup lag: H-190's 9h16m unruled STOP,
      H-189's 9h43m crash wait; relaunches took 11-63 s once seen — pure queue time)
      Every chain-terminal.* (or a chain-log.txt whose tail reads CHAIN STOP/HALT)
      on a line-initial-active lane with mtime newer than the lane's last
      ORCHESTRATOR-RULING.md commit is an unruled terminal; age = now - mtime;
      SLA = 1 tick interval; oldest first with the lane dir and next command.
      (Verdicted lanes' terminals are history, not queue.)

  M3 VOID METER  (Defects/rework — the 42%-of-attempts class, $11.45 void vs
      $21.36 counted in the census window)
      Over the trailing window: void_rate = run-*-void-* dirs / all archived
      attempt dirs; void_usd = sum(budget.spent_usd_counted) over void dirs'
      run-record.json; suffix-attributed (coordination | environmental |
      instrument | arm — arm-attributed voids are the experiment working, not
      counted as waste). ALARM at void_rate > 25% or any coordination-class void.

Plus one HEARTBEAT line for the loop's tick header
(tick N | idle-runnable | WIP k/cap | oldest unruled terminal | frontier).

Frontier (first cut, labeled): current wave's non-kept hypothesis items, plus
later-wave items whose gated_on notes read start-today, plus registered-active
specs with no run dir (dispatchable builds). The full gate parse belongs to
flow-metrics.py (the flow-instrument-seeded-detection draft).

Usage: waste-status.py [--repo PATH] [--now EPOCH] [--span-days N]
Exit 0 always (advisory surface, never a gate).
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

TICK_MIN = 30            # tick interval: idle-runnable alarm and terminal SLA
IDLE_ALARM_S = TICK_MIN * 60
WIP_CAP = 3              # CONWIP cap on concurrently-claimed counted lanes
CLAIM_FRESH_S = 2 * TICK_MIN * 60   # a claim heartbeat older than 2 ticks is not WIP
VOID_ALARM_RATE = 0.25
STALL_MIN_S = 30 * 60    # historical stall-window census threshold (= alarm)
TOP = 5

LIVENESS = ("chain-log.txt", "RUN-CLAIM.json")   # + chain-terminal.* by prefix


def sh(repo, args):
    return subprocess.run(["git", "-C", repo] + args,
                          capture_output=True, text=True).stdout


def fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def fmt_h(hours):
    if hours < 1:
        return "%dm" % round(hours * 60)
    return ("%.1fh" % hours) if hours < 48 else ("%.0fh(%.1fd)" % (hours, hours / 24))


def spec_statuses(repo):
    """{H-NNN: line-initial status} over hypotheses/H-NNN-*.md."""
    out = {}
    hyp = os.path.join(repo, "hypotheses")
    if not os.path.isdir(hyp):
        return out
    for fn in sorted(os.listdir(hyp)):
        m = re.match(r"(H-\d+)-.*\.md$", fn)
        if not m:
            continue
        text = open(os.path.join(hyp, fn), encoding="utf-8", errors="replace").read()
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


def frontier(repo, statuses):
    """(labels, size): first-cut frontier — current-wave non-kept items,
    later-wave start-today items, and registered-but-unrun active specs."""
    items, plan_path = [], os.path.join(
        repo, "experiments", "runs", "DESIGN-post-010-train", "wave-plan.json")
    if os.path.isfile(plan_path):
        waves = json.load(open(plan_path, encoding="utf-8"))["plan"]["waves"]
        current_seen = False
        for w in waves:
            hyp = [i for i in w["items"] if re.match(r"^H-\d+$", i)]
            todo = [i for i in hyp if statuses.get(i) != "kept"]
            if todo and not current_seen:
                current_seen = True
                items += ["%s(current %s)" % (i, w["version"]) for i in todo]
            elif todo and any("start today" in g or "starts today" in g or
                              "start now" in g or "starts now" in g
                              for g in w.get("gated_on", [])):
                items += ["%s(start-today %s)" % (i, w["version"]) for i in todo]
    have_run = set(lane_dirs(repo))
    unrun = [h for h, s in statuses.items() if s == "active" and h not in have_run]
    items += ["%s(unrun build)" % h for h in sorted(unrun)]
    return items, len(items)


def m1_idle_runnable(repo, now, span_days, frontier_n):
    commit_ts = [int(t) for t in sh(repo, ["log", "--format=%at"]).split()]
    last_commit = max(commit_ts) if commit_ts else None
    newest_lane, newest_lane_file = None, None
    for lane in lane_dirs(repo):
        d = os.path.join(repo, "experiments", "runs", lane)
        for fn in os.listdir(d):
            if fn in LIVENESS or fn.startswith("chain-terminal."):
                t = os.path.getmtime(os.path.join(d, fn))
                if t <= now and (newest_lane is None or t > newest_lane):
                    newest_lane, newest_lane_file = t, "%s/%s" % (lane, fn)
    anchors = [t for t in (last_commit, newest_lane) if t is not None]
    idle_s = (now - max(anchors)) if anchors else 0.0
    alarm = idle_s > IDLE_ALARM_S and frontier_n > 0
    print("M1 IDLE-RUNNABLE: %s since last activity%s"
          % (fmt_h(idle_s / 3600),
             "  << ALARM (>%dm with %d frontier items)" % (TICK_MIN, frontier_n)
             if alarm else ""))
    if last_commit:
        print("  anchors: last commit %s | newest lane file %s (%s)"
              % (fmt_ts(last_commit),
                 fmt_ts(newest_lane) if newest_lane else "none",
                 newest_lane_file or "-"))
    since = now - span_days * 86400
    ts = sorted(t for t in commit_ts if since <= t <= now)
    windows = [(a, b) for a, b in zip(ts, ts[1:]) if b - a >= STALL_MIN_S]
    total_h = sum(b - a for a, b in windows) / 3600
    print("  stall history [%dd]: %d windows >=%dm, %s total stalled"
          % (span_days, len(windows), STALL_MIN_S // 60, fmt_h(total_h)))
    for a, b in sorted(windows, key=lambda w: w[0] - w[1])[:3]:
        print("    worst: %s -> %s  (%s)" % (fmt_ts(a), fmt_ts(b),
                                             fmt_h((b - a) / 3600)))
    return idle_s


def m2_terminal_pickup(repo, now, rulings, statuses):
    unruled = []
    for lane in lane_dirs(repo):
        if statuses.get(lane) != "active":
            continue    # a terminal on a verdicted lane is history, not queue
        d = os.path.join(repo, "experiments", "runs", lane)
        ruled_at = rulings.get(lane, 0)
        for fn in sorted(os.listdir(d)):
            path = os.path.join(d, fn)
            hit = fn.startswith("chain-terminal.")
            if not hit and fn == "chain-log.txt":
                try:
                    with open(path, "rb") as f:
                        f.seek(max(0, os.path.getsize(path) - 4096))
                        hit = bool(re.search(rb"CHAIN (STOP|HALT)", f.read()))
                except OSError:
                    hit = False
            if hit:
                t = os.path.getmtime(path)
                if t > ruled_at and t <= now:
                    nxt = ("rule, then relaunch experiments/runs/%s/chain.sh" % lane
                           if os.path.isfile(os.path.join(d, "chain.sh"))
                           else "rule terminal")
                    unruled.append((now - t, lane, fn, nxt))
    lanes = {}
    for age, lane, fn, nxt in sorted(unruled, reverse=True):
        lanes.setdefault(lane, (age, fn, nxt))
    print("M2 TERMINAL PICKUP: %d unruled terminal file(s) across %d active lane(s)  "
          "[SLA = 1 tick = %dm]" % (len(unruled), len(lanes), TICK_MIN))
    oldest = None
    for lane, (age, fn, nxt) in sorted(lanes.items(), key=lambda r: -r[1][0])[:TOP]:
        flag = "  << OVER SLA" if age > TICK_MIN * 60 else ""
        print("  unruled: %s/%s  age %s%s\n           next: %s"
              % (lane, fn, fmt_h(age / 3600), flag, nxt))
        if oldest is None:
            oldest = (lane, age)
    return oldest


def m3_void_meter(repo, now, span_days):
    since = now - span_days * 86400
    KIND = (("coordination", r"race|dashboard|hook|orchestrat"),
            ("environmental", r"timeout|crash|network|oauth|trust|env|auth|flap"),
            ("instrument", r"guard|fp|harness|fixture|instrument|grader|driver"),
            ("arm", r"arm"))
    attempts, voids, void_usd, by_kind, new_coord = 0, 0, 0.0, {}, []
    for lane in lane_dirs(repo):
        d = os.path.join(repo, "experiments", "runs", lane)
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            m = re.match(r"run-\d+[a-z]?(?:-(.+))?$", fn)
            if not m or not os.path.isdir(p) or not (since <= os.path.getmtime(p) <= now):
                continue
            suffix = m.group(1)
            if suffix is None:
                continue                      # live run-N dir, not an archived attempt
            attempts += 1
            if suffix.startswith("void-"):
                voids += 1
                cause = suffix[5:]
                kind = next((k for k, pat in KIND if re.search(pat, cause)), "unattributed")
                by_kind[kind] = by_kind.get(kind, 0) + 1
                if kind == "coordination":
                    new_coord.append("%s/%s" % (lane, fn))
                for rr in glob.glob(os.path.join(p, "**", "run-record.json"),
                                    recursive=True):
                    try:
                        void_usd += float(json.load(open(rr))
                                          .get("budget", {}).get("spent_usd_counted", 0))
                    except (ValueError, OSError):
                        pass
    rate = (voids / attempts) if attempts else 0.0
    alarm = rate > VOID_ALARM_RATE or bool(new_coord)
    print("M3 VOID METER [%dd window]: %d/%d archived attempts void (%.0f%%), "
          "$%.2f voided%s"
          % (span_days, voids, attempts, rate * 100, void_usd,
             "  << ALARM" if alarm else ""))
    if by_kind:
        print("  by attribution: " + ", ".join(
            "%s %d" % (k, n) for k, n in sorted(by_kind.items(), key=lambda r: -r[1]))
            + "  (arm-attributed = the experiment working, not waste)")
    for path in new_coord[:TOP]:
        print("  coordination-class void: %s" % path)
    return rate


def heartbeat(repo, now, idle_s, oldest_terminal, frontier_items):
    wip = []
    for lane in lane_dirs(repo):
        d = os.path.join(repo, "experiments", "runs", lane)
        for fn in ("RUN-CLAIM.json", "LANE-STATE.json"):
            p = os.path.join(d, fn)
            if os.path.isfile(p) and now - os.path.getmtime(p) <= CLAIM_FRESH_S:
                wip.append(lane)
                break
    ot = ("%s %s" % (oldest_terminal[0], fmt_h(oldest_terminal[1] / 3600))
          if oldest_terminal else "none")
    print("HEARTBEAT: idle-runnable %s | WIP %d/%d%s | oldest unruled terminal: %s | "
          "frontier: %d item(s): %s"
          % (fmt_h(idle_s / 3600), len(wip), WIP_CAP,
             " << BREACH" if len(wip) > WIP_CAP else "", ot,
             len(frontier_items), ", ".join(frontier_items[:3]) or "-"))


def main():
    ap = argparse.ArgumentParser()
    # Plugin convention: this script ships inside the plugin, so __file__
    # never locates the consumer repository.
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    default_repo = env_root if env_root and os.path.isdir(env_root) \
        else os.getcwd()
    ap.add_argument("--repo", default=default_repo)
    ap.add_argument("--now", type=float, default=None)
    ap.add_argument("--span-days", type=int, default=7)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    now = a.now if a.now is not None else time.time()
    if not os.path.isdir(os.path.join(repo, ".git")):
        print("WASTE-STATUS: not a git repo (%s)" % repo)
        return 0
    print("WASTE-STATUS @ %s  repo=%s" % (fmt_ts(now), repo))
    statuses = spec_statuses(repo)
    fr_items, fr_n = frontier(repo, statuses)
    idle_s = m1_idle_runnable(repo, now, a.span_days, fr_n)
    oldest = m2_terminal_pickup(repo, now, ruling_times(repo), statuses)
    m3_void_meter(repo, now, a.span_days)
    heartbeat(repo, now, idle_s, oldest, fr_items)
    print("WASTE-STATUS: advisory only, exit 0 — thresholds TICK=%dm IDLE_ALARM=%dm "
          "WIP_CAP=%d VOID_ALARM=%d%%; definitions frozen in the "
          "flow-instrument-seeded-detection draft" %
          (TICK_MIN, TICK_MIN, WIP_CAP, int(VOID_ALARM_RATE * 100)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
