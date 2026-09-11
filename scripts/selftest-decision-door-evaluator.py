#!/usr/bin/env python3
"""selftest-decision-door-evaluator.py -- regression test for the decision-door evaluator (records vs cards).

Runs the INSTALLED plugin's two door-evaluator selftests from the tree this file lives in, forwards their
per-check lines, then prints one PASS/FAIL line per stage and a RESULT line:

  decision_door_check.py --selftest  the evaluator's own defect matrix in a throwaway git repository (the clauses
                                     W1-W2, H1, T1-T3, the amendment guard, the six hard classes, the fail-closed
                                     seeds -- crash, stall, dangling pointer, external host, unparseable denial
                                     record -- undo none, ledger-row corroboration, STREAK, two-run determinism,
                                     the sha7 stamp, the CLI contract, and the consumer's ledger through
                                     .claude/hyp.json)
  decisions.py --selftest            the decision kit's end-to-end loop with the evaluator wired into add: a
                                     zero-information two-way card RECORDS (one DECISION-DOOR line, `recorded`,
                                     the resolution pair with basis two-way-door and a 7-day veto window), never
                                     enters the surface state file, `resolve --deny --comment veto` flips it to
                                     denied and runs the undo (the revert of the self-declaring landing commit
                                     restores the tree), `show` renders the outcome, a side-door row is
                                     DOOR-UNAUDITED at exit 0, and the crash seed renders a card

Usage: python3 scripts/selftest-decision-door-evaluator.py    exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-DRAFT-73404199-decision-door-evaluator (kept 2026-09-11, 5/5 in two counted
runs, the second by a cold executor; fragment 0489). Standard library, Python 3.9; writes only under the temp
dirs the two selftests create and remove themselves.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = (
    ("evaluator-selftest", "decision_door_check.py", "door-selftest: 0 failure(s)", "DOOR-SELFTEST-"),
    ("decisions-selftest", "decisions.py", "selftest: 0 failure(s)", "SELFTEST-"),
)
EVALUATOR_CHECKS = ("door-evaluator-records-two-way", "door-record-pair-on-file", "door-record-never-opens",
                    "door-veto-executes-undo", "door-veto-deny-wins-join", "door-show-renders-outcome",
                    "door-check-reports-side-door-row", "door-crash-seed-renders-a-card")
PORT_CHECKS = ("consumer-ledger-row-records-through-hyp-json", "consumer-ledger-row-on-lab-path-uncorroborated")


def main():
    failures = 0
    for name, script, sentinel, prefix in STAGES:
        proc = subprocess.run([sys.executable, os.path.join(HERE, script), "--selftest"],
                              capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        lines = proc.stdout.splitlines()
        checks = [l for l in lines if l.startswith(prefix + "PASS ") or l.startswith(prefix + "FAIL ")]
        failed = [l for l in lines if l.startswith(prefix + "FAIL ")]
        ok = proc.returncode == 0 and sentinel in lines and checks and not failed
        detail = "%d check(s), %d failed" % (len(checks), len(failed))
        wanted = EVALUATOR_CHECKS if script == "decisions.py" else PORT_CHECKS
        present = [c for c in wanted if any(l.startswith(prefix + "PASS " + c) for l in lines)]
        ok = ok and len(present) == len(wanted)
        detail += ", door-evaluator checks %d/%d" % (len(present), len(wanted))
        print("%s %s -- exit %d, %s" % ("PASS" if ok else "FAIL", name, proc.returncode, detail))
        if not ok:
            failures += 1
            if proc.stderr.strip():
                sys.stdout.write(proc.stderr)
    print("RESULT %s selftest-decision-door-evaluator: %d of %d stage(s) failed"
          % ("PASS" if not failures else "FAIL", failures, len(STAGES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
