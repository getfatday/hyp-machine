#!/usr/bin/env python3
"""selftest-decision-door-fields.py -- regression test for the decision-card door fields.

Runs the INSTALLED plugin's two door-field selftests from the tree this file lives in, forwards
their per-check lines, then prints one PASS/FAIL line per stage and a RESULT line:

  decision_card_lint.py --selftest   the lint's own synthetic-card checks (rules D0-D9, the
                                     corroboration table, malformed-suppresses-escalate, the seeded
                                     stall, two-pass determinism) in a throwaway git repository
  decisions.py --selftest            the decision kit's end-to-end loop with the six fields on every
                                     add, plus the door wiring: a field-less card refused with exit 2
                                     (D0/D1/D3/D4/D5/D6 listed), a self-declared two-way card appended
                                     with a D8 finding and exit 1, `show` rendering the door lines,
                                     `--door-git-timeout 0` yielding one ADD-TIMEOUT line and exit 0,
                                     `check` exempting legacy ids

Usage: python3 scripts/selftest-decision-door-fields.py    exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-DRAFT-5f02c694-decision-card-door-fields (kept 2026-09-11, 5/5 in two
counted runs; fragment 0484). Standard library, Python 3.9; writes only under the temp dirs the two
selftests create and remove themselves.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = (
    ("lint-selftest", "decision_card_lint.py", "lint-selftest: 0 failure(s)", "LINT-SELFTEST-"),
    ("decisions-selftest", "decisions.py", "selftest: 0 failure(s)", "SELFTEST-"),
)
DOOR_CHECKS = ("door-missing-fields-refused", "door-self-declared-escalates", "door-object-on-row",
               "door-show-renders", "door-stall-exit-neutral", "door-check-exempts-legacy-ids")


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
        if script == "decisions.py":
            present = [c for c in DOOR_CHECKS if any(l.startswith("SELFTEST-PASS " + c) for l in lines)]
            ok = ok and len(present) == len(DOOR_CHECKS)
            detail += ", door checks %d/%d" % (len(present), len(DOOR_CHECKS))
        print("%s %s -- exit %d, %s" % ("PASS" if ok else "FAIL", name, proc.returncode, detail))
        if not ok:
            failures += 1
            if proc.stderr.strip():
                sys.stdout.write(proc.stderr)
    print("RESULT %s selftest-decision-door-fields: %d of %d stage(s) failed"
          % ("PASS" if not failures else "FAIL", failures, len(STAGES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
