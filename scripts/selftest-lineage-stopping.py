#!/usr/bin/env python3
"""selftest-lineage-stopping.py -- regression test for the lineage stopping rule (R0-R4).

Runs the INSTALLED plugin's two stopping-rule selftests from the tree this file lives in, forwards their
per-check lines, then prints one PASS/FAIL line per stage and a RESULT line:

  policy-bytes                        rules/lineage-sprt.json hashes to the sealed policy sha (8052bda9...) and
                                      rules/frozen/lineage-sprt.json to the sealed frozen copy (ce73163a...) whose
                                      inner rule_text hashes to the policy sha; the instrument's --freeze from the
                                      plugin root reproduces the frozen copy byte for byte (the R0 identity)
  lineage-stopping-selftest           lineage-stopping.py --selftest: the 26 typing cases and 7 walk/settle cases
                                      ported from the lab fixture's lineage.py, the truncation derivation, and the
                                      R0-R4 bookkeeping end to end (spend header, R1 refusal at 13 caps and on the
                                      US$ component, five refusal-0 looks reading promote at look 5 with state.jsonl
                                      byte-identical to the lab lineage's, hold at look 2, max-looks at 13, pending
                                      root causes settled, R4 inheritance, the CLI surface)
  stopping-rule-selftest              stopping-rule.py --selftest: the kept instrument's six seeded state files
                                      (two clean, four violations), unchanged by this port

Usage: python3 scripts/selftest-lineage-stopping.py    exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-DRAFT-5810517d-verdict-lineage-stopping (kept 2026-09-11, five counted looks each
5/5, four by cold executors; fragment 0490). Standard library, Python 3.9; writes only under a temp dir it
creates and removes.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
POLICY_SHA256 = "8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1"
FROZEN_REF_SHA256 = "ce73163aeda7147edfc0d6fab1fbca9e5dac6ad4bb37ff9ca1c86cf64f85b027"


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def stage_policy_bytes(tmp):
    pol = os.path.join(PLUGIN, "rules", "lineage-sprt.json")
    ref = os.path.join(PLUGIN, "rules", "frozen", "lineage-sprt.json")
    probe = os.path.join(tmp, "freeze-probe.json")
    ok_pol = os.path.isfile(pol) and sha(pol) == POLICY_SHA256
    ok_ref = os.path.isfile(ref) and sha(ref) == FROZEN_REF_SHA256
    proc = subprocess.run([sys.executable, os.path.join(HERE, "stopping-rule.py"), "--freeze", "rules/lineage-sprt.json", "--into", probe],
                          cwd=PLUGIN, capture_output=True, text=True)
    ok_freeze = proc.returncode == 0 and os.path.isfile(probe) and open(probe, "rb").read() == open(ref, "rb").read() if ok_ref else False
    for name, ok in (("policy-sha", ok_pol), ("frozen-copy-sha", ok_ref), ("freeze-reproduces-frozen-copy", ok_freeze)):
        print("POLICY-%s %s" % ("PASS" if ok else "FAIL", name))
    return ok_pol and ok_ref and ok_freeze, "3 check(s), %d failed" % sum(1 for x in (ok_pol, ok_ref, ok_freeze) if not x)


def stage_script(tmp, script, args, sentinel_prefix, pass_prefix, fail_prefix):
    proc = subprocess.run([sys.executable, os.path.join(HERE, script)] + args, capture_output=True, text=True, cwd=tmp)
    sys.stdout.write(proc.stdout)
    lines = proc.stdout.splitlines()
    checks = [l for l in lines if l.startswith(pass_prefix) or l.startswith(fail_prefix)]
    failed = [l for l in lines if l.startswith(fail_prefix)]
    sentinel = [l for l in lines if l.startswith(sentinel_prefix)]
    ok = proc.returncode == 0 and bool(sentinel) and bool(checks) and not failed
    if not ok and proc.stderr.strip():
        sys.stdout.write(proc.stderr)
    return ok, "exit %d, %d check(s), %d failed, %s" % (proc.returncode, len(checks), len(failed), sentinel[-1] if sentinel else "no summary line")


def main():
    tmp = tempfile.mkdtemp(prefix="selftest-lineage-stopping-")
    failures = 0
    try:
        ok, detail = stage_policy_bytes(tmp)
        print("%s policy-bytes -- %s" % ("PASS" if ok else "FAIL", detail))
        failures += 0 if ok else 1
        ok, detail = stage_script(tmp, "lineage-stopping.py", ["--selftest", "--into", os.path.join(tmp, "lineage")], "selftest: ", "PASS ", "FAIL ")
        print("%s lineage-stopping-selftest -- %s" % ("PASS" if ok else "FAIL", detail))
        failures += 0 if ok else 1
        ok, detail = stage_script(tmp, "stopping-rule.py", ["--selftest", "--into", os.path.join(tmp, "instrument")], "selftest ok", "PASS ", "FAIL ")
        print("%s stopping-rule-selftest -- %s" % ("PASS" if ok else "FAIL", detail))
        failures += 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT %s selftest-lineage-stopping: %d of 3 stage(s) failed" % ("PASS" if not failures else "FAIL", failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
