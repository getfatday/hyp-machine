#!/usr/bin/env python3
"""selftest-om-ci.py -- regression test for the operating-model tier-0 CI check
(`scripts/om-ci.py`, `scripts/om_check_jobs.py`; source lab getfatday/cause-n-effect
H-DRAFT-a28b91c9-om-ci-tier0, kept 2026-09-15: five counted looks, A1-A5 pass in every one,
SPRT llr 2.9389 over the 2.8904 promote bound; VERDICT.json beside the lane).

Two layers:

  emit (fast, no git)    a scratch consumer directory (no git repo): emit ci-tier0 is
                        idempotent and byte-stable on re-run, the rendered workflow matches
                        the committed `templates/offload/om-check.yml` for the default
                        `model_dir`, and a hand-edited vendored file is kept, not clobbered,
                        without `--force`
  self-test (thorough)   drives `python3 scripts/om-ci.py self-test ci-tier0` as a subprocess
                        (its own scratch git consumer, under the CI-runner constraint) and
                        requires every one of its PASS-lines: byte-stable render, the rendered
                        YAML's run: blocks equal the JOBS table, the lint job red on a seeded
                        E-LINK defect and green on the clean tree, compile-check reporting
                        stale and regenerating exactly one bot-authored commit, a second run
                        adding zero commits, the bot actor's own push never re-triggering the
                        regenerate step, the compile-check job exiting 0 with the push step
                        skipping (never failing) on a detached checkout of a stale mutant
                        (the pull_request-style merge-ref shape), the paths-only mutant never
                        firing a job, zero `claude` shim spawns, zero proxy hits, and PyYAML
                        importing under an empty HOME

Usage: python3 scripts/selftest-om-ci.py        exit 0 = PASS, 1 = FAIL
Stdlib only, Python 3.9.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM_CI = os.path.join(PLUGIN, "scripts", "om-ci.py")
TEMPLATE_WORKFLOW = os.path.join(PLUGIN, "templates", "offload", "om-check.yml")

sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
import om_check_jobs as J  # noqa: E402


def run(argv, check=True):
    proc = subprocess.run([sys.executable, "-B"] + argv, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise RuntimeError("command failed (%r) rc=%s:\n%s" % (argv, proc.returncode, out))
    return proc.returncode, out


def main():
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name + (": " + str(detail) if detail else ""))

    # ---- emit ci-tier0: idempotent, byte-stable, matches the committed template -----------
    tmp = tempfile.mkdtemp(prefix="selftest-om-ci-emit-")
    try:
        rc1, out1 = run([OM_CI, "emit", "ci-tier0", "--root", tmp])
        check("emit ci-tier0 exits 0 on a fresh consumer", rc1 == 0)
        check("emit ci-tier0 reports every artifact created on first run",
             all(l.split()[0] == "created" for l in out1.splitlines() if l.strip()))

        rc2, out2 = run([OM_CI, "emit", "ci-tier0", "--root", tmp])
        check("emit ci-tier0 is idempotent (second run reports unchanged)",
             rc2 == 0 and all(l.split()[0] == "unchanged" for l in out2.splitlines() if l.strip()))

        with open(os.path.join(tmp, ".github", "workflows", "om-check.yml"), encoding="utf-8") as fh:
            emitted = fh.read()
        with open(TEMPLATE_WORKFLOW, encoding="utf-8") as fh:
            committed_template = fh.read()
        check("emitted workflow equals the committed templates/offload/om-check.yml for the "
             "default model_dir", emitted == committed_template)
        check("templates/offload/om-check.yml equals a fresh render (never hand-edited out of sync)",
             committed_template == J.render_yaml())

        # a hand-edited vendored file is kept, not clobbered, without --force
        edited_path = os.path.join(tmp, ".github", "om-scripts", "om-worker.py")
        with open(edited_path, "a", encoding="utf-8") as fh:
            fh.write("\n# consumer edit\n")
        rc3, out3 = run([OM_CI, "emit", "ci-tier0", "--root", tmp])
        kept_line = next((l for l in out3.splitlines() if "om-scripts/om-worker.py" in l), "")
        check("a hand-edited vendored file is kept without --force",
             rc3 == 0 and kept_line.startswith("kept"), kept_line)
        rc4, out4 = run([OM_CI, "emit", "ci-tier0", "--root", tmp, "--force"])
        updated_line = next((l for l in out4.splitlines() if "om-scripts/om-worker.py" in l), "")
        check("--force overwrites a hand-edited vendored file",
             rc4 == 0 and updated_line.startswith("updated"), updated_line)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- self-test ci-tier0: the thorough CI-runner-constrained pass -----------------------
    rc, out = run([OM_CI, "self-test", "ci-tier0"], check=False)
    print(out.rstrip())
    expected = [
        "render byte-stable across two calls",
        "rendered YAML run: blocks equal the JOBS table",
        "paths-only mutant fires no job by the predicate",
        "a model-tree change fires the trigger",
        "rendered YAML equals the committed workflow",
        "lint job green on the clean tree",
        "lint job red on the E-LINK mutant",
        "compile-check job regenerates exactly one bot commit on the stale mutant",
        "the regenerate commit is authored as the bot identity",
        "a second run adds zero commits",
        "compile-check job still exits 0 on the second run",
        "the bot actor's own push never re-triggers the regenerate step",
        "compile-check job exits 0 on a detached checkout of a stale mutant",
        "the push step skips with a notice (never fails) on a detached checkout",
        "no claude shim spawn across every scenario",
        "zero proxy hits across every scenario",
        "vendored PyYAML imports under an empty HOME",
    ]
    pass_lines = [l for l in out.splitlines() if l.startswith("PASS ")]
    for name in expected:
        check("self-test ci-tier0: " + name, any(name in l for l in pass_lines))
    check("self-test ci-tier0 exits 0", rc == 0)

    passed = sum(results)
    total = len(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if passed == total else "FAIL", passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
