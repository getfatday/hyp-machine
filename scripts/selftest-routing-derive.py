#!/usr/bin/env python3
# agent tier: fix / sonnet / high
"""selftest-routing-derive.py -- a plugin-shipped self-test for scripts/routing-derive.py.

Distinct from routing-derive.py's own `--selftest` (which checks only the saving() arithmetic):
this file exercises propose_candidate / rollback_check / pin_advisories / apply_default_bump
against a dozen small, self-contained scenarios covering the shape of every seeded family the
source lab fixture (H-DRAFT-d5a8d9b6-routing-derive) graded, ported to this plugin's own live
row/table shapes (class-keyed table, `observed.tier`, no ledger `seq`) -- see the module
docstring of scripts/routing-derive.py, "Reconciling with the live tables".

Ships with the ON diff: every scenario is built from literal dicts in this file. Exit 0 if
every scenario's assertion holds; 1 otherwise. Stdlib only, python3 -B.

  selftest-routing-derive.py [--json]
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_rd():
    spec = importlib.util.spec_from_file_location("rd_selftest", os.path.join(HERE, "routing-derive.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PRICES = {
    "fable": {"in": 10, "out": 50}, "opus": {"in": 5, "out": 25},
    "sonnet": {"in": 2, "out": 10}, "haiku": {"in": 1, "out": 5},
}
RULE = {
    "tier_order": ["haiku", "sonnet", "opus", "fable"],
    "grades": {"adversarial": ["execute"]},
    "class_asymmetry": ["think", "adversarial"],
    "think_drop_licenses": ["LIC-E2-CALIBRATION-001"],
    "rollback_step": 1, "candidate_step": 1,
    "lineage_policy": {"path": "rules/lineage-sprt.json",
                        "sha256": "8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1"},
}


def route_rows(cls, tier, n_pass, n_fail, tok_in=100000, tok_out=100000, void=None, seq0=0):
    rows = []
    for i in range(n_pass + n_fail):
        rows.append({"kind": "agent-route", "seq": seq0 + i + 1, "role": cls, "class": cls,
                     "observed": {"tier": tier}, "outcome": "pass" if i < n_pass else "fail",
                     "tokens": {"in": tok_in, "out": tok_out}, "void": void, "wf": "wf", "agent": "a%d" % i})
    return rows


def pair_rows(cls, on_tier, incumbent_tier, n_fail, pair_id="p1", seq0=0):
    return [{"kind": "matched-pair", "seq": seq0 + i + 1, "class": cls, "on_tier": on_tier,
             "incumbent_tier": incumbent_tier, "outcome": "fail", "pair_id": pair_id, "void": None}
            for i in range(n_fail)]


def run_scenarios(rd, workdir):
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    # S1: 5 passes, big tokens -> candidate opens, largest saving, one step down.
    t1 = {"classes": {"execute": {"basis": "prior", "tier": "sonnet", "since_row": 0}}}
    c1 = rd.propose_candidate(route_rows("execute", "sonnet", 5, 0), t1, RULE, PRICES, workdir, 1.3)
    check("S1 promote+saving-over-budget opens a one-step candidate",
          c1 and c1["class"] == "execute" and c1["from"] == "sonnet" and c1["to"] == "haiku")

    # S2: 5 passes, small tokens -> saving under budget -> no candidate.
    t2 = {"classes": {"execute": {"basis": "prior", "tier": "sonnet", "since_row": 0}}}
    c2 = rd.propose_candidate(route_rows("execute", "sonnet", 5, 0, 10000, 10000), t2, RULE, PRICES, workdir, 1.3)
    check("S2 saving under budget opens nothing", c2 is None)

    # S3: the pair is already banked -> must not reopen even with a promote stream.
    t3 = {"classes": {"execute": {"basis": "prior", "tier": "sonnet", "since_row": 0}},
          "bank": {"execute|haiku": {"pair_stream_ids": ["x"], "expires_when_default_sha_not": "s"}}}
    c3 = rd.propose_candidate(route_rows("execute", "sonnet", 5, 0), t3, RULE, PRICES, workdir, 1.3)
    check("S3 an already-banked pair never reopens", c3 is None)

    # S4: class asymmetry -- think without a license never candidates; with one, it does.
    t4a = {"classes": {"think": {"basis": "prior", "tier": "fable", "since_row": 0}}}
    c4a = rd.propose_candidate(route_rows("think", "fable", 5, 0), t4a, RULE, PRICES, workdir, 1.3)
    check("S4a unlicensed think class never candidates", c4a is None)
    t4b = {"classes": {"think": {"basis": "prior", "tier": "fable", "since_row": 0,
                                   "license_id": "LIC-E2-CALIBRATION-001"}}}
    c4b = rd.propose_candidate(route_rows("think", "fable", 5, 0), t4b, RULE, PRICES, workdir, 1.3)
    check("S4b licensed think class candidates", c4b and c4b["class"] == "think")

    # S5: grader-stability -- a grading class never opens while its graded class has an open
    # routing lineage, even though the grader's own stream and saving would otherwise qualify.
    t5 = {"classes": {"adversarial": {"basis": "prior", "tier": "sonnet", "since_row": 0},
                       "execute": {"basis": "prior", "tier": "sonnet", "since_row": 0,
                                    "open_lineage": True}}}
    c5 = rd.propose_candidate(route_rows("adversarial", "sonnet", 5, 0), t5, RULE, PRICES, workdir, 1.3)
    check("S5 grader-stability blocks the grading class", c5 is None)

    # S6: a basis:evidence, non-top row with a holding matched-pair stream rolls back exactly
    # one step, to the destination named by the pair.
    t6 = {"classes": {"execute": {"basis": "evidence", "tier": "sonnet", "since_row": 0}}}
    a6 = rd.rollback_check(pair_rows("execute", "sonnet", "opus", 2), t6, RULE, workdir)
    check("S6 matched-pair hold rolls back exactly one step",
          len(a6) == 1 and a6[0]["kind"] == "rollback" and a6[0]["to"] == "opus")

    # S7: the same, at the TOP tier -- a routing-ceiling advisory, never a move.
    t7 = {"classes": {"think": {"basis": "evidence", "tier": "fable", "since_row": 0}}}
    a7 = rd.rollback_check(pair_rows("think", "fable", "fable", 2), t7, RULE, workdir)
    check("S7 top-tier hold raises a ceiling advisory, never a move",
          len(a7) == 1 and a7[0]["kind"] == "routing-ceiling-advisory")

    # S8: pins never move; retest_by in the past raises an advisory, in the future stays silent.
    t8 = {"classes": {"mechanical": {"basis": "pin", "tier": "haiku", "retest_by": "2020-01-01"},
                       "unmodeled": {"basis": "pin", "tier": "sonnet", "retest_by": "2099-01-01"}},
          "as_of": "2026-09-13"}
    c8 = rd.propose_candidate(route_rows("mechanical", "haiku", 5, 0), t8, RULE, PRICES, workdir, 1.3)
    p8 = rd.pin_advisories(t8)
    check("S8a a pin never candidates even on a promote stream", c8 is None)
    check("S8b only the expired pin gets the advisory",
          len(p8) == 1 and p8[0]["class"] == "mechanical" and p8[0]["kind"] == "expired-pin-advisory")

    # S9: default bump re-seeds prior rows, expires stale banks, keeps evidence tiers.
    t9 = {"default_sha": "old-sha",
          "classes": {"execute": {"basis": "prior", "tier": "sonnet"},
                       "think": {"basis": "evidence", "tier": "sonnet"}},
          "bank": {"think|opus": {"pair_stream_ids": ["x"], "expires_when_default_sha_not": "old-sha"}}}
    b9 = rd.apply_default_bump(t9, "new-sha")
    check("S9a a prior row is re-seeded from the new default",
          "reseeded_from" in b9["classes"]["execute"] and b9["classes"]["execute"]["reseeded_from"] == "old-sha")
    check("S9b a bank keyed to the old default expires", b9.get("bank") == {})
    check("S9c an evidence row's tier is kept", b9["classes"]["think"]["tier"] == "sonnet")

    # R1: void rows must never enter the stream a candidate is opened from, however many of
    # them read "pass".
    t_void = {"classes": {"execute": {"basis": "prior", "tier": "sonnet", "since_row": 0}}}
    c_void = rd.propose_candidate(route_rows("execute", "sonnet", 5, 0, void="annulled"), t_void, RULE, PRICES, workdir, 1.3)
    check("R1 void:annulled rows never open a candidate", c_void is None)

    # R2: a basis:prior row's matched-pair stream may read hold; there is nothing to roll it
    # back TO, so it must never move.
    t_prior_hold = {"classes": {"execute": {"basis": "prior", "tier": "sonnet", "since_row": 0}}}
    a_prior_hold = rd.rollback_check(pair_rows("execute", "sonnet", "opus", 2), t_prior_hold, RULE, workdir)
    check("R2 a basis:prior row's holding matched-pair stream never rolls back", a_prior_hold == [])

    # R3: a basis:evidence, non-top row with NO matched-pair stream open reads only the
    # observational stream, as a covariate, and raises the advisory -- never an applied
    # rollback. This is the live-safe-but-dormant path: the plugin never yet writes a
    # matched-pair row, so this is the ONLY rollback-family action this loop can raise today.
    t_obs = {"classes": {"execute": {"basis": "evidence", "tier": "sonnet", "since_row": 0}}}
    a_obs = rd.rollback_check(route_rows("execute", "sonnet", 0, 2, 1000, 1000), t_obs, RULE, workdir)
    check("R3 observational-only hold at a non-top tier raises the advisory, not a move",
          len(a_obs) == 1 and a_obs[0]["kind"] == "rollback-advisory" and a_obs[0]["basis"] == "observational-only")

    # R4 (this port's own regression, not in the source fixture): a live-shaped row nesting
    # tier under `observed.tier` with no `seq` field at all is read correctly once
    # load_ledger has assigned one -- exercised end-to-end via a real ledger file.
    ledger_path = os.path.join(workdir, "r4-ledger.jsonl")
    with open(ledger_path, "w", encoding="utf-8") as fh:
        for i in range(5):
            row = {"kind": "agent-route", "wf": "wf1", "agent": "a%d" % i, "role": "build",
                   "class": "execute", "observed": {"tier": "sonnet"}, "outcome": "pass",
                   "tokens": {"in": 500000, "out": 500000}}
            fh.write(json.dumps(row) + "\n")
    rows_r4 = rd.load_ledger(ledger_path)
    check("R4 a live-shaped row with no seq field is assigned one at load time",
          all(r.get("seq") == i + 1 for i, r in enumerate(rows_r4)))
    check("R4b observed.tier is read through _row_tier", rd._row_tier(rows_r4[0]) == "sonnet")

    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rd = _load_rd()
    workdir = tempfile.mkdtemp(prefix="selftest-routing-derive-")
    try:
        checks = run_scenarios(rd, workdir)
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    ok = all(passed for _, passed in checks)
    if args.json:
        print(json.dumps({"selftest": "routing-derive", "ok": ok,
                           "checks": [{"name": n, "pass": p} for n, p in checks]},
                          sort_keys=True))
    else:
        for name, passed in checks:
            print(("PASS " if passed else "FAIL ") + name)
        print("selftest %s (%d/%d)" % ("PASS" if ok else "FAIL",
                                        sum(1 for _, p in checks if p), len(checks)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
