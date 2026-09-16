#!/usr/bin/env python3
"""routing-derive-cadence.py -- runs scripts/routing-derive.py `report` and `propose` on the
run-completed cadence: a synchronous `Stop` hook that runs in the SAME `Stop` event as
`routing-ledger.py` (Stop, timeout 15 s) -- hooks attached to one event run in parallel, so this
hook's report reflects agent-route rows appended by EARLIER turns, never a guarantee of this
turn's own rows. Source: lab H-DRAFT-d5a8d9b6-routing-derive (VERDICT.json evidence-sufficient
promote). Never applies a rollback or a default-bump -- those persist a table and stay
separate, manual verbs (see scripts/routing-derive.py's module docstring).

Bounded and fail-open, matching hooks/scripts/routing-ledger.py's own contract: any error is
swallowed and the hook exits 0 (an observational hook never blocks the turn). Silent when
<checkout>/ledger/routing-ledger.jsonl does not exist yet or carries zero rows -- a repository
with no routing history yet gets no report and no candidate, not an error.

Writes (consumer checkout, never the plugin tree):
  <root>/routing-report.md                        -- compiled projection, sibling to DASHBOARD.md
  <root>/.claude/routing-candidates/candidate.json      -- {"candidate": null|{...}}
  <root>/.claude/routing-candidates/candidate-spec.md   -- only when a candidate opened
  <root>/.claude/routing-candidates/actions.json        -- rollbacks/pin advisories (observational)
  <root>/.claude/routing-derive-cache/frozen-rule.json  -- the stopping-rule freeze copy (this
                                                             hook's --workdir); undeclared before
                                                             this fix
  <root>/ledger/routing-derive-state.json          -- read if present; NEVER written here
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from hyp_config import resolve_root  # noqa: E402

LEDGER_RELPATH = os.path.join("ledger", "routing-ledger.jsonl")
STATE_RELPATH = os.path.join("ledger", "routing-derive-state.json")
REPORT_RELPATH = "routing-report.md"
CANDIDATES_RELDIR = os.path.join(".claude", "routing-candidates")


def _plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(HERE))


def _load_rd():
    import importlib.util
    plugin_root = _plugin_root()
    path = os.path.join(plugin_root, "scripts", "routing-derive.py")
    spec = importlib.util.spec_from_file_location("_routing_derive_cadence", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(root):
    ledger_path = os.path.join(root, LEDGER_RELPATH)
    if not os.path.isfile(ledger_path) or os.path.getsize(ledger_path) == 0:
        return {"ran": False, "reason": "no ledger yet"}
    plugin_root = _plugin_root()
    rule_path = os.path.join(plugin_root, "rules", "routing-derive.json")
    prices_path = os.path.join(plugin_root, "rules", "model-prices.json")
    template_path = os.path.join(plugin_root, "templates", "routing-candidate-template.md")
    state_path = os.path.join(root, STATE_RELPATH)
    workdir = os.path.join(root, ".claude", "routing-derive-cache")
    os.makedirs(workdir, exist_ok=True)

    rd = _load_rd()
    rows = rd.load_ledger(ledger_path)
    if not rows:
        return {"ran": False, "reason": "ledger has no agent-route/matched-pair rows"}
    table = rd.load_state_table(state_path)
    table = rd.seed_missing_classes(table, rows, root, plugin_root)
    rule = rd.load_rule(rule_path)

    report_text, per_class = rd.build_report(rows, table, rule, workdir)
    with open(os.path.join(root, REPORT_RELPATH), "w", encoding="utf-8") as fh:
        fh.write(report_text)

    out_dir = os.path.join(root, CANDIDATES_RELDIR)
    os.makedirs(out_dir, exist_ok=True)
    prices_doc = json.load(open(prices_path)) if os.path.isfile(prices_path) else {}
    prices = rd.prices_by_tier(prices_doc, rule.get("tier_order", ["haiku", "sonnet", "opus", "fable"]))
    cand = rd.propose_candidate(rows, table, rule, prices, workdir, 1.3)
    with open(os.path.join(out_dir, "candidate.json"), "w", encoding="utf-8") as fh:
        fh.write(rd.canon_pretty({"candidate": cand}))
    if cand and os.path.isfile(template_path):
        template_text = open(template_path, encoding="utf-8").read()
        body = rd.candidate_spec_body(cand, template_text)
        with open(os.path.join(out_dir, "candidate-spec.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
    rb = rd.rollback_check(rows, table, rule, workdir)
    pins = rd.pin_advisories(table)
    with open(os.path.join(out_dir, "actions.json"), "w", encoding="utf-8") as fh:
        fh.write(rd.canon_pretty({"rollbacks": rb, "pin_advisories": pins}))

    return {"ran": True, "classes": sorted(per_class), "candidate": bool(cand),
            "rollbacks": len(rb), "pin_advisories": len(pins)}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        root = resolve_root(payload)
        summary = run(root)
        sys.stdout.write(json.dumps({"routing_derive": summary}) + "\n")
    except SystemExit:
        pass  # a die() call already wrote its refusal word to stderr; fail open below regardless
    except Exception as exc:  # fail open: an observational hook never blocks the turn.
        sys.stderr.write("routing-derive-cadence: internal error (fail-open, nothing written): %r\n" % (exc,))
    sys.exit(0)


if __name__ == "__main__":
    main()
