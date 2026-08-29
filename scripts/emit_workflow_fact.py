#!/usr/bin/env python3
"""Workflow-facts close-time emitter: ONE fact record per workflow close.

PROVENANCE — COUNTED, byte-preserving port of the kept H-118 fixture emitter
(experiments/runs/H-118/fixture/impl/emit_workflow_fact.py in the source lab;
hypothesis H-118-gwt-accretion-loop KEPT 2026-08-28, two consecutive counted
4/4: canonically byte-identical replays, zero duplicate appends against an
already-appended ledger — idempotence key workflow+sha). Only this provenance
framing differs from the counted fixture copy. Point --ledger at your repo's
ledger/workflow-facts.jsonl (the stream scripts/derive-metrics.py reads).

Reads a close-events file (the workflow-close fixture shape or an adapter
emission), appends workflow-fact/v1 records to the stream ledger in canonical-v1
serialization. Append-only by construction: this module only ever opens the
ledger with mode "a" (the sanctioned append path of the write-once guard class;
Edit-denial is the guard's job, see workflow_facts_guard.py).

Determinism: ts comes from the close event (close time is input data), never
the wall clock; ids are monotonic at land-time (max existing id + 1); input
order is preserved. Replaying the same close-events file into a fresh ledger is
byte-identical; replaying against an already-appended ledger appends ZERO
duplicates (idempotence key: workflow + sha).

Exit: 0 emitted/skipped fine, 2 invalid input. Stdout: one summary JSON line.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facts_lib import canonical, load_jsonl, sha256_file, validate_fact

FACT_SCHEMA = "workflow-fact/v1"


def load_close_events(path):
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    events = doc["events"] if isinstance(doc, dict) else doc
    if not isinstance(events, list):
        raise ValueError("close-events file must be a list or {events: [...]}")
    return events


def build_record(event, next_id):
    rec = {
        "schema": FACT_SCHEMA,
        "id": next_id,
        "ts": event["ts"],
        "workflow": event["workflow"],
        "kind": event.get("kind", "workflow-closed"),
        "gates": event["gates"],
        "artifacts": event["artifacts"],
        "sha": event["sha"],
        "links": event["links"],
    }
    errors = validate_fact(rec)
    if errors:
        raise ValueError("invalid record for workflow %r: %s"
                         % (event.get("workflow"), "; ".join(errors)))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--close-events", required=True)
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()

    try:
        events = load_close_events(args.close_events)
    except Exception as exc:
        print(json.dumps({"error": "bad close-events input: %s" % exc}))
        return 2

    existing = []
    if os.path.exists(args.ledger):
        try:
            existing = load_jsonl(args.ledger)
        except Exception as exc:
            print(json.dumps({"error": "unreadable ledger: %s" % exc}))
            return 2
    seen = {(r.get("workflow"), r.get("sha")) for r in existing}
    next_id = max([r.get("id", 0) for r in existing] + [0]) + 1

    appended, skipped = 0, 0
    try:
        with open(args.ledger, "a", encoding="utf-8") as ledger:
            for event in events:
                key = (event.get("workflow"), event.get("sha"))
                if key in seen:
                    skipped += 1
                    continue
                rec = build_record(event, next_id)
                ledger.write(canonical(rec))
                seen.add(key)
                next_id += 1
                appended += 1
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    print(json.dumps({"appended": appended, "skipped": skipped,
                      "ledger_sha256": sha256_file(args.ledger)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
