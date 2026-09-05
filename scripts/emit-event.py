#!/usr/bin/env python3
"""emit-event.py -- the five choke-point event emitters as one gated CLI (H-238).

PROVENANCE -- consumer port of the kept H-238 fixture's five choke-point
emitters (experiments/runs/H-238/fixture/impl/minilab-scripts/{flip_verdict,
append_ledger,land_terminal,interpreter,close_workflow}.py in the source lab;
H-238 KEPT 2026-09-02, consecutive 5/5 pair). In the lab, three of the five
choke points are scripts of their own (verdict flip, ledger append, terminal
land); in a consumer repository those moments pass through skill steps, so this
CLI is their one choke-point entry -- each subcommand builds exactly the
payload its fixture emitter built and hands it to events_lib.emit_event
(canonical bytes, node validation, idempotent append). The other two choke
points are instrumented in place: `hooks/scripts/interpreter.py`
(event/advisory-surfaced) and `scripts/emit_workflow_fact.py`
(event/workflow-closed) -- this CLI carries their verbs too for manual or
skill-driven emission.

Gating: the event stream is experiments-profile machinery (`.claude/hyp.json`
`profile`); below it this CLI refuses with a typed reason (exit 3). Validation
is the second gate either way: no committed event node declaring the stream, no
event (SCHEMA.md representation law; templates in templates/event-nodes/).

Dates are caller-pinned (--date), never a wall clock read -- determinism is
the caller's to keep. Exit: 0 appended/skipped, 2 invalid record, 3 refused.

Usage:
  emit-event.py [--root DIR] verdict-flipped --spec hypotheses/H-001-x.md
      --from active --to kept --evidence "run-2 5/5" --date D --caused-by C
  emit-event.py [--root DIR] ledger-record-appended --kind intent --slug s
      --date D --caused-by C
  emit-event.py [--root DIR] chain-terminal-landed --lane experiments/runs/H-001
      --phase run1 --rc 0 [--halt-class CLASS] --date D --caused-by C
  emit-event.py [--root DIR] advisory-surfaced --policy policy/x --subject s
      --message "..." --date D --caused-by C
  emit-event.py [--root DIR] workflow-closed --workflow w --sha HEX
      --gates-passed N --gates-total M --date D --caused-by C
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import events_lib  # noqa: E402


def _profile(root):
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            p = data.get("profile")
            if isinstance(p, str) and p.strip():
                return p.strip()
    except Exception:
        pass
    return "capture"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd(),
                    help="consumer repo root (default: cwd)")
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("verdict-flipped")
    p.add_argument("--spec", required=True, help="repo-relative spec path")
    p.add_argument("--from", dest="frm", required=True)
    p.add_argument("--to", required=True, choices=["kept", "discarded", "refine"])
    p.add_argument("--evidence", required=True)

    p = sub.add_parser("ledger-record-appended")
    p.add_argument("--kind", required=True,
                   choices=["intent", "amendment", "commitment", "directive",
                            "decision", "decision-resolution", "advisory"])
    p.add_argument("--slug", required=True)

    p = sub.add_parser("chain-terminal-landed")
    p.add_argument("--lane", required=True, help="repo-relative lane dir")
    p.add_argument("--phase", required=True)
    p.add_argument("--rc", required=True, type=int)
    p.add_argument("--halt-class", default=None,
                   help="required when rc != 0 (budget-exceeded|grade-error|"
                        "probe-failed|env-void)")

    p = sub.add_parser("advisory-surfaced")
    p.add_argument("--policy", required=True, help="policy node id")
    p.add_argument("--subject", required=True)
    p.add_argument("--message", required=True)

    p = sub.add_parser("workflow-closed")
    p.add_argument("--workflow", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--gates-passed", required=True, type=int)
    p.add_argument("--gates-total", required=True, type=int)

    for sp in sub.choices.values():
        sp.add_argument("--date", required=True, help="YYYY-MM-DD, caller-pinned")
        sp.add_argument("--caused-by", required=True,
                        help="pointer to the causing artifact (commit, lane, node)")

    args = ap.parse_args()
    root = os.path.abspath(args.root)

    profile = _profile(root)
    if profile not in ("experiments", "modeling"):
        print(json.dumps({"status": "refused",
                          "reason": "event stream is experiments-profile machinery; "
                                    "profile is %r (set profile in .claude/hyp.json)"
                                    % profile}))
        return 3

    if args.verb == "verdict-flipped":
        hid = "-".join(os.path.basename(args.spec).split("-")[:2])
        rec = events_lib.make_record(
            "event/verdict-flipped", args.caused_by, args.date, hid,
            {"spec": args.spec, "from": args.frm, "to": args.to,
             "evidence": args.evidence})
    elif args.verb == "ledger-record-appended":
        rec = events_lib.make_record(
            "event/ledger-record-appended", args.caused_by, args.date,
            args.slug, {"kind": args.kind, "slug": args.slug})
    elif args.verb == "chain-terminal-landed":
        if args.rc != 0 and not args.halt_class:
            print(json.dumps({"status": "error",
                              "reason": "non-green landing needs --halt-class"}))
            return 2
        payload = {"lane": args.lane, "phase": args.phase, "rc": args.rc,
                   "green": args.rc == 0}
        if args.rc != 0:
            payload["halt-class"] = args.halt_class
        rec = events_lib.make_record(
            "event/chain-terminal-landed", args.caused_by, args.date,
            os.path.basename(args.lane.rstrip("/")), payload)
    elif args.verb == "advisory-surfaced":
        rec = events_lib.make_record(
            "event/advisory-surfaced", args.caused_by, args.date, args.subject,
            {"policy": args.policy, "message": args.message})
    else:  # workflow-closed
        rec = events_lib.make_record(
            "event/workflow-closed", args.caused_by, args.date, args.workflow,
            {"workflow": args.workflow, "sha": args.sha,
             "gates_passed": args.gates_passed, "gates_total": args.gates_total})

    result = events_lib.emit_event(root, rec)
    print(json.dumps({"status": result["status"],
                      **({"errors": result["errors"]} if result["errors"] else {}),
                      **({"reason": result["reason"]} if "reason" in result else {})},
                     sort_keys=True))
    return 2 if result["status"] == "invalid" else 0


if __name__ == "__main__":
    sys.exit(main())
