#!/usr/bin/env python3
"""selftest-preflight-assertions.py -- regression test for the widened binary-assertions check
in the shipped scripts/preflight.py (H-DRAFT-44f18a2e-preflight-assertion-gate, lab keep
2026-09-16; research/assertion-count-rule.md in the lab).

Builds minimal, otherwise-fully-compliant hypothesis-spec fixtures (every OTHER preflight check
passes: two-way-door, sandboxing, ground-truth-isolation, frozen-protocol, budget-declared,
mechanical-verdict; no Fixture-SHA256 pins so fixture-fresh reads its no-op ADVISORY; no
`Claim type:` line so claim-type reads its no-op ADVISORY) that vary only the number of numbered
Binary-assertions lines and whether the Verdict-rule section carries a `SUBSTANTIVE-ASSERTIONS:`
line plus the word "void"/"voids" -- the two inputs the amended check reads -- then runs the
INSTALLED scripts/preflight.py (the tree this file lives in) against each and checks its
`binary-assertions` PASS/FAIL line and exit code:

  typed-one-assertion      n=1, typed  -> PASS binary-assertions, exit 0
  untyped-one-assertion    n=1, untyped -> FAIL binary-assertions (detail string pinned), exit 1
  typed-two-assertions     n=2, typed  -> PASS binary-assertions, exit 0
  untyped-two-assertions   n=2, untyped -> FAIL binary-assertions, exit 1
  three-assertions         n=3, untyped -> PASS binary-assertions, exit 0 (n>=3 bypasses typing)
  five-assertions          n=5, untyped -> PASS binary-assertions, exit 0
  six-assertions           n=6, untyped -> FAIL binary-assertions, exit 1 (cap unchanged)

Usage: python3 scripts/selftest-preflight-assertions.py     exit 0 = PASS, 1 = FAIL
Provenance: lab H-DRAFT-44f18a2e-preflight-assertion-gate (kept 2026-09-16, five counted looks,
A1-A3 pass in every one); ships beside preflight.py's own `check("binary-assertions", ...)` at
line 114. Standard library, Python 3.9; writes only under a temp dir it creates and removes.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
PREFLIGHT = os.path.join(PLUGIN, "scripts", "preflight.py")

UNTYPED_DETAIL = (
    "fewer than 3 admitted only with a SUBSTANTIVE-ASSERTIONS: line and a typed void "
    "in the Verdict rule"
)


def spec_text(n_assertions, typed):
    """A minimal spec that passes every preflight check except (deliberately) whatever
    binary-assertions decides, varying only n_assertions and whether the Verdict rule
    carries a typed SUBSTANTIVE-ASSERTIONS: line."""
    assertions = "\n".join("%d. Assertion number %d holds." % (i, i)
                            for i in range(1, n_assertions + 1))
    verdict = "Keep if the counted assertions pass; refine or discard otherwise."
    if typed:
        verdict += ("\nSUBSTANTIVE-ASSERTIONS: A1 is the one substantive assertion; "
                    "a void class ambiguous covers an incomplete run.")
    return """# H-TEST-selftest: fixture spec

## Status
draft
Claim type: descriptive

## Hypothesis
A test hypothesis sentence for the selftest fixture.

## Motivation
Exercises the binary-assertions check in isolation.

## Variable under test
The fixture variable, on or off.

## Baseline
The fixture baseline.

## Method
1. Run the fixture in an isolated worktree sandbox.
The grader and answer key live harness-side and are never shown to arms.
Frozen at registration: the corpus, keys, and rubric.
- Budget per run: 5 minutes wall-clock.

## Binary assertions
%s

## Verdict rule
%s

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
""" % (assertions, verdict)


def run_preflight(path):
    proc = subprocess.run(
        [sys.executable or "python3", PREFLIGHT, path],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    return proc.returncode, proc.stdout


CASES = [
    # (name, n_assertions, typed, want_pass, want_exit)
    ("typed-one-assertion", 1, True, True, 0),
    ("untyped-one-assertion", 1, False, False, 1),
    ("typed-two-assertions", 2, True, True, 0),
    ("untyped-two-assertions", 2, False, False, 1),
    ("three-assertions", 3, False, True, 0),
    ("five-assertions", 5, False, True, 0),
    ("six-assertions", 6, False, False, 1),
]


def selftest():
    failures = 0
    tmp = tempfile.mkdtemp(prefix="selftest-preflight-assertions-")
    try:
        for name, n, typed, want_pass, want_exit in CASES:
            spec_path = os.path.join(tmp, "%s.md" % name)
            with open(spec_path, "w") as fh:
                fh.write(spec_text(n, typed))
            code, out = run_preflight(spec_path)
            line = next((l for l in out.splitlines()
                         if l.startswith("PASS binary-assertions")
                         or l.startswith("FAIL binary-assertions")), "")
            got_pass = line.startswith("PASS")
            ok = got_pass == want_pass and code == want_exit and line != ""
            if not want_pass and ok:
                ok = UNTYPED_DETAIL in line
            failures += 0 if ok else 1
            print("%s %s: binary-assertions-line=%r exit=%d (want pass=%s exit=%d)"
                  % ("PASS" if ok else "FAIL", name, line, code, want_pass, want_exit))
    finally:
        for name, *_ in CASES:
            p = os.path.join(tmp, "%s.md" % name)
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(tmp)
    total = len(CASES)
    print("RESULT: %s (%d/%d)" % ("PASS" if not failures else "FAIL", total - failures, total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest())
