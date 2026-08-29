#!/usr/bin/env python3
"""Gate->GWT harvester: recorded gate outcomes -> gwt-case v1 records in
slice GWT slots, candidate state.

PROVENANCE — COUNTED, byte-preserving port of the kept H-118 fixture harvester
(experiments/runs/H-118/fixture/impl/harvest_gwt.py in the source lab;
hypothesis H-118-gwt-accretion-loop KEPT 2026-08-28, two consecutive counted
4/4: at least one valid gwt-case v1 record per executed gate class on the
owning slice, every emitted case lint-clean and byte-stable through a
serialize->parse->serialize round trip, harvest reproduced over the lab's real
gate records). Only this provenance framing differs from the counted fixture
copy.

Reads the workflow-fact stream, resolves the OWNING SLICE from the board
mechanically (run-close facts belong to the slice whose member command is
command/run-experiment -- executing a counted run IS that command), and emits
one case per (record, executed gate):

  Given = the board's fixture events for the slice (every event with a
          projection flow into one of the slice's member read models --
          id-level per D15), sorted;
  When  = the slice's member command id (EM-L7 A3 reading);
  Then  = the slice's drawn close event [event/run-completed] (id-level list
          form, the lint-compatible SS2a bare-array rendering of {events});
  state = "candidate" (board-contract GWT chip states; promotion is a
          reconciliation act, never an auto-write).

Results are stored SEPARATELY from cases (SS2a: specs are canon, outcomes are
runs): harvest-results.json maps case id -> {outcome, evidence}.

Outputs (canonical doc form, byte-stable):
  <out-dir>/harvested-cases.json    {schema: gwt-case-set/v1, cases: [...]}
  <out-dir>/harvest-results.json    the separated outcomes
  --board-out                       board copy with slice/gwt filled (optional)

Exit 0 ok, 2 unusable input.
"""
import argparse
import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facts_lib import canonical_doc, load_jsonl, validate_case, validate_fact

RUN_COMMAND = "command/run-experiment"
ASSERT_RE = re.compile(r"^A(\d+)$")


def entity_ref(placement):
    for key, value in placement.items():
        if key.endswith("/id") and not key.startswith("placement/"):
            return key.split("/", 1)[0], value
    return None, None


def board_lookups(board):
    """(slice_id, when_id, then_events, given_events) for the owning slice."""
    placements = {p.get("placement/id"): p for p in board.get("placements", [])}
    owner = None
    for sl in board.get("slices", []):
        for pid in sl.get("slice/members", sl.get("members", [])):
            kind, eid = entity_ref(placements.get(pid, {}))
            if kind == "command" and eid == RUN_COMMAND:
                if owner is not None:
                    raise ValueError("more than one slice owns %s" % RUN_COMMAND)
                owner = sl
    if owner is None:
        raise ValueError("no slice owns %s" % RUN_COMMAND)
    sid = owner.get("slice/id") or owner.get("id")
    members = set(owner.get("slice/members", owner.get("members", [])))
    flows = board.get("flows", {})
    flow_iter = flows.items() if isinstance(flows, dict) else \
        ((f.get("flow/id"), f) for f in flows)
    flow_list = [f for _, f in flow_iter]

    drawn = set()
    member_read_models = set()
    for pid in members:
        kind, eid = entity_ref(placements.get(pid, {}))
        if kind == "read-model":
            member_read_models.add(eid)
    given = set()
    for f in flow_list:
        fp, tp = f.get("flow/from"), f.get("flow/to")
        ftype = f.get("flow/type")
        fk, fe = entity_ref(placements.get(fp, {}))
        tk, te = entity_ref(placements.get(tp, {}))
        if ftype == "emission" and fp in members and tp in members \
                and fk == "command" and fe == RUN_COMMAND and tk == "event":
            drawn.add(te)
        if ftype == "projection" and fk == "event" and tk == "read-model" \
                and te in member_read_models:
            given.add(fe)
    if not drawn:
        raise ValueError("owning slice draws no emission from %s" % RUN_COMMAND)
    then = ["event/run-completed"] if "event/run-completed" in drawn \
        else [sorted(drawn)[0]]
    return sid, RUN_COMMAND, then, sorted(given)


def harvest(records, board):
    sid, when, then, given = board_lookups(board)
    cases, results = [], {}
    for rec in sorted(records, key=lambda r: (r.get("workflow", ""),
                                              r.get("sha", ""))):
        errors = validate_fact(rec)
        if errors:
            raise ValueError("invalid fact record %r: %s"
                             % (rec.get("workflow"), "; ".join(errors)))
        if rec.get("kind") != "workflow-closed":
            continue
        hypothesis = rec["links"].get("hypothesis")
        if not hypothesis:
            continue
        for gate in rec["gates"]:
            if gate["outcome"] == "skip":
                results["skip:%s:%s" % (rec["workflow"], gate["gate"])] = {
                    "outcome": "skip",
                    "evidence": "%s#%s" % (rec["workflow"], gate["detail"]),
                }
                continue
            m = ASSERT_RE.match(gate["detail"])
            assertion = int(m.group(1)) if m else 0
            cid = "gwt/%s--%s--%s" % (rec["workflow"],
                                      gate["detail"].lower(), gate["gate"])
            case = {
                "schema_version": "gwt-case/v1",
                "gwt/id": cid,
                "gwt/source": {"hypothesis": hypothesis,
                               "assertion": assertion},
                "gwt/slice": sid,
                "gwt/given": given,
                "gwt/when": when,
                "gwt/then": then,
                "gwt/state": "candidate",
                "gwt/tags": ["arm:on", "gate:%s" % gate["gate"],
                             "workflow:%s" % rec["workflow"]],
            }
            errors = validate_case(case)
            if errors:
                raise ValueError("harvester emitted an invalid case %s: %s"
                                 % (cid, "; ".join(errors)))
            cases.append(case)
            results[cid] = {"outcome": gate["outcome"],
                            "evidence": "%s#%s" % (rec["workflow"],
                                                   gate["detail"])}
    cases.sort(key=lambda c: c["gwt/id"])
    return sid, cases, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--board-out", default=None)
    args = ap.parse_args()

    try:
        records = load_jsonl(args.stream)
        with open(args.board, "r", encoding="utf-8") as f:
            board = json.load(f)
        sid, cases, results = harvest(records, board)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("harvest failed: %s\n" % exc)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    caseset = {"schema": "gwt-case-set/v1", "slice": sid, "cases": cases}
    with open(os.path.join(args.out_dir, "harvested-cases.json"), "w",
              encoding="utf-8") as f:
        f.write(canonical_doc(caseset))
    with open(os.path.join(args.out_dir, "harvest-results.json"), "w",
              encoding="utf-8") as f:
        f.write(canonical_doc({"schema": "gwt-results/v1", "results": results}))

    if args.board_out:
        board2 = copy.deepcopy(board)
        for sl in board2.get("slices", []):
            if (sl.get("slice/id") or sl.get("id")) == sid:
                sl["slice/gwt"] = cases
        with open(args.board_out, "w", encoding="utf-8") as f:
            f.write(canonical_doc(board2))

    print(json.dumps({"slice": sid, "cases": len(cases),
                      "results": len(results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
