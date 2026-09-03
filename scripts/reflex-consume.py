#!/usr/bin/env python3
"""reflex-consume.py — the leak meter's mechanical consumer (throughput-floor
directive, research/raw/2026-09-02-throughput-floor-directive.md).

The gap this closes (measured 2026-09-02): the meter MEASURED stalls — five
BURN-SLOW fires in .claude/leak-meter-fires.log (07:46:57Z, 10:52:10Z,
12:57:51Z, 14:02:45Z, 22:54:40Z), every row "acted": null (dead wiring) — but
nothing CONSUMED the alarm: fires 1-4 drew zero actions; fire 5 drew one
commit (f403cf03) 43 seconds later, recorded nowhere the machinery reads. The
H-253 escalation ladder never saw the fires because no mechanical consumer was
installed (the reflex sensor timer plists are emitted-never-loaded).

What this does (check mode, the default — bounded, stdlib-only, exit 0
always: advisory, never a gate):
  - reads the meter self-log (.claude/leak-meter-fires.log, leak-selflog/v1
    rows) and the reflex invocation ledger (.claude/reflex/invocations.jsonl);
  - a BURN*/ALARM* fire is CONSUMED when its own row carries a truthy "acted",
    OR when an invocations.jsonl row with kind=leak-consumption cites its
    exact ts (fire_ts) — the record this script's --record verb writes;
  - every unconsumed fire older than 30 minutes prints one line:
      CONSUMPTION-DUE\t<ts>\t<alarm>\t<window>\tage=<m>m\t<record-hint>
    (scripts/harden-check.sh counts these as ADVISORY-31, count-only);
  - each due fire ALSO lands exactly one T0 incident row via the H-253
    surfacing path — scripts/reflex-surface's incident_record(), the
    FIXTURE-SIDE store ledger/incident-records.jsonl per the pending DEC-013
    gate (never a work-ledger row; anti-clog C2). Dedup is exactly-once per
    bucket "leak-fire:<ts>" (H-204 semantics): re-runs re-print the standing
    advisory but never re-append the row.

Record mode (`--record <fire-ts> --action TEXT`) closes the loop the "acted"
field never did: appends {"ts", "trigger": "consumption",
"kind": "leak-consumption", "fire_ts", "action"} to
.claude/reflex/invocations.jsonl (safe for reflex-check's WD reader — it
filters trigger=="timer" rows only) and the fire stops surfacing. Consumption
means an ACTION citing the fire (a launch, a commit, a lane) — surfacing an
incident row is not consumption (H-253: consumed buckets stop escalating;
surfaced-but-unconsumed ones do not).

Usage:
  reflex-consume.py [ROOT] [--window-min 30] [--dry-run]
  reflex-consume.py [ROOT] --record <fire-ts-ISO-Z> --action TEXT
"""
import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import os
import sys
import time

FIRES_REL = os.path.join(".claude", "leak-meter-fires.log")
INVOCATIONS_REL = os.path.join(".claude", "reflex", "invocations.jsonl")
INCIDENT_STORE_REL = os.path.join("ledger", "incident-records.jsonl")
MAX_LINES = 500          # bound every file read (advisory budget)
DEFAULT_WINDOW_MIN = 30  # directive: consumption due after 30 unconsumed min


def tail_rows(path, max_lines=MAX_LINES):
    """Last max_lines parseable JSON rows of a jsonl file; missing file = []."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-max_lines:]
    except OSError:
        return []
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def parse_ts_z(ts):
    """ISO-8601 Z timestamp -> epoch seconds, or None."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.strptime(
            ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def alarm_fires(root):
    """BURN*/ALARM* rows of the meter self-log (the ADVISORY-30 alarm
    grammar), newest-bounded, each as (ts_str, epoch, row)."""
    out = []
    for rec in tail_rows(os.path.join(root, FIRES_REL)):
        alarm = str(rec.get("alarm") or "")
        if not (alarm.startswith("BURN") or alarm.startswith("ALARM")):
            continue
        epoch = parse_ts_z(rec.get("ts"))
        if epoch is None:
            continue
        out.append((rec["ts"], epoch, rec))
    return out


def consumption_records(root):
    """fire_ts values with a leak-consumption record in the invocation
    ledger."""
    return set(rec.get("fire_ts")
               for rec in tail_rows(os.path.join(root, INVOCATIONS_REL))
               if rec.get("kind") == "leak-consumption")


def surfaced_buckets(root):
    """Buckets already holding an incident row in the H-253 fixture-side
    store (dedup: exactly-once per bucket)."""
    return set(rec.get("bucket")
               for rec in tail_rows(os.path.join(root, INCIDENT_STORE_REL))
               if rec.get("kind") == "incident")


def load_surface(root):
    """The H-253 surfacing module (scripts/reflex-surface) — its
    incident_record() IS the DEC-013-gated fixture-side append path.
    Explicit SourceFileLoader: the file is extensionless."""
    path = os.path.join(root, "scripts", "reflex-surface")
    loader = importlib.machinery.SourceFileLoader(
        "reflex_consume_surface", path)
    spec = importlib.util.spec_from_loader("reflex_consume_surface", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def now_z():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def cmd_check(root, window_min, dry_run):
    now = time.time()
    consumed = consumption_records(root)
    surfaced = surfaced_buckets(root)
    fires = alarm_fires(root)
    due, landed = [], 0
    for ts, epoch, rec in fires:
        if rec.get("acted") or ts in consumed:
            continue
        age_min = (now - epoch) / 60.0
        if age_min < window_min:
            continue
        due.append((ts, epoch, rec))
        print("CONSUMPTION-DUE\t%s\t%s\t%s\tage=%dm\trecord: python3 "
              "scripts/reflex-consume.py . --record %s --action "
              "\"<commit/lane/decision that consumed it>\""
              % (ts, rec.get("alarm"), rec.get("window"), int(age_min), ts))
    if due and not dry_run:
        bucket_rows = [(ts, rec) for ts, _e, rec in due
                       if ("leak-fire:%s" % ts) not in surfaced]
        if bucket_rows:
            try:
                surface = load_surface(root)
            except Exception as exc:  # advisory: never crash a hook
                print("REFLEX-CONSUME-WARNING: scripts/reflex-surface "
                      "unavailable (%s) — incident row(s) NOT landed" % exc)
                surface = None
            if surface is not None:
                for ts, rec in bucket_rows:
                    # kind:"incident" row grammar per reflex-surface cmd_file;
                    # no incident dir exists for a log-row fire, so nothing is
                    # cited (H-104 intact: citing requires a verified manifest)
                    surface.incident_record(root, {
                        "kind": "incident",
                        "bucket": "leak-fire:%s" % ts,
                        "signature": {
                            "predicate": "leak-meter-alarm-unconsumed",
                            "subject": FIRES_REL,
                            "failure_class": rec.get("alarm"),
                        },
                        "incident_dir": None, "manifest": None,
                        "first_seen": ts, "times_surfaced": 0,
                        "consumed_by": None, "tier": "T0",
                    })
                    landed += 1
    print("reflex-consume: %d alarm fire(s) read, %d due (unconsumed >%dm), "
          "%d incident row(s) landed%s"
          % (len(fires), len(due), window_min, landed,
             " [dry-run]" if dry_run else ""))
    return 0


def cmd_record(root, fire_ts, action):
    known = set(ts for ts, _e, _r in alarm_fires(root))
    if fire_ts not in known:
        print("FATAL: %s is not an alarm fire ts in %s"
              % (fire_ts, FIRES_REL), file=sys.stderr)
        return 2
    if fire_ts in consumption_records(root):
        print("CONSUMPTION-DEDUP\t%s\talready recorded" % fire_ts)
        return 0
    path = os.path.join(root, INVOCATIONS_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()),
                            "trigger": "consumption",
                            "kind": "leak-consumption",
                            "fire_ts": fire_ts, "action": action,
                            "recorded_at": now_z()}, sort_keys=True) + "\n")
    print("CONSUMPTION-RECORDED\t%s\t%s" % (fire_ts, action))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="reflex-consume")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--window-min", type=int, default=DEFAULT_WINDOW_MIN)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--record", metavar="FIRE_TS")
    ap.add_argument("--action")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if args.record:
        if not args.action:
            print("FATAL: --record requires --action", file=sys.stderr)
            return 2
        return cmd_record(root, args.record, args.action)
    return cmd_check(root, args.window_min, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
