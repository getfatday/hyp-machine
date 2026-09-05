#!/usr/bin/env python3
"""Workflow-facts close-time emitter: ONE fact record per workflow close.

PROVENANCE — COUNTED, byte-preserving port of the kept H-118 fixture emitter
(experiments/runs/H-118/fixture/impl/emit_workflow_fact.py in the source lab;
hypothesis H-118-gwt-accretion-loop KEPT 2026-08-28, two consecutive counted
4/4: canonically byte-identical replays, zero duplicate appends against an
already-appended ledger — idempotence key workflow+sha). Point --ledger at
your repo's ledger/workflow-facts.jsonl (the stream scripts/derive-metrics.py
reads). Two named divergences from the counted fixture copy: this provenance
framing, and the H-238 choke-point emitter (kept 2026-09-02) — each
REAL append additionally lands one event/workflow-closed record on the
consumer's unified event stream via events_lib (experiments profile only,
guarded: an emission failure never fails the fact append; the summary line
reports events_appended/events_skipped). Repo root for the stream derives from
the ledger path's grandparent, overridable with --repo-root.

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


def _emit_stream_events(repo_root, appended_recs):
    """H-238 choke point (workflow close): one event/workflow-closed stream
    record per REAL fact append. Experiments-profile machinery, guarded end to
    end -- a stream failure never fails the fact append. Returns
    (events_appended, events_skipped)."""
    if not appended_recs or not repo_root:
        return 0, 0
    ok, dup = 0, 0
    try:
        try:
            with open(os.path.join(repo_root, ".claude", "hyp.json"),
                      encoding="utf-8") as f:
                profile = (json.load(f) or {}).get("profile", "capture")
        except Exception:
            profile = "capture"
        if profile not in ("experiments", "modeling"):
            return 0, 0
        import events_lib
        for rec in appended_recs:
            gates = rec.get("gates") or []
            passed = sum(1 for g in gates
                         if isinstance(g, dict) and g.get("outcome") == "pass")
            ev = events_lib.make_record(
                "event/workflow-closed", "workflow-fact/%s" % rec.get("id"),
                str(rec.get("ts", ""))[:10], str(rec.get("workflow")),
                {"workflow": rec.get("workflow"), "sha": rec.get("sha"),
                 "gates_passed": passed, "gates_total": len(gates)})
            result = events_lib.emit_event(repo_root, ev)
            if result["status"] == "appended":
                ok += 1
            elif result["status"] == "skipped":
                dup += 1
    except Exception:
        pass
    return ok, dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--close-events", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--repo-root", default=None,
                    help="consumer repo root for the H-238 event stream "
                         "(default: the ledger path's grandparent)")
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
    appended_recs = []
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
                appended_recs.append(rec)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    repo_root = args.repo_root or os.path.dirname(
        os.path.dirname(os.path.abspath(args.ledger)))
    ev_ok, ev_dup = _emit_stream_events(repo_root, appended_recs)

    print(json.dumps({"appended": appended, "skipped": skipped,
                      "events_appended": ev_ok, "events_skipped": ev_dup,
                      "ledger_sha256": sha256_file(args.ledger)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
