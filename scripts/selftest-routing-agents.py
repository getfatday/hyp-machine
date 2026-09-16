#!/usr/bin/env python3
"""selftest-routing-agents.py -- regression test for the compiled agent surface
(source lab H-DRAFT-75b03e6e-routing-determinism, VERDICT.json: evidence-sufficient
promote, five counted looks 5/5, llr 2.9389 >= the 2.8904 promote bound).

  compiler   scripts/compile-routing-agents.py is byte-stable across two runs; every role
             in the table gets an agents/hyp-<role>.md naming the table's model; --check
             passes against this repository's own committed agents/ (they must already
             agree -- that IS the changeset) and fails on a planted disagreement; --emit
             lands the files in a scratch consumer directory
  runner     scripts/compile-model-workflow.py's emitted-runner template carries a
             `resolve_role_model` function (extracted from the live source and exec'd in
             isolation -- this tests the actual shipped bytes, not a re-implementation):
             it resolves the `gate` role's model from a routing table under
             CLAUDE_PLUGIN_ROOT, an override in <repo>/.claude/routing.json wins over the
             default, and a missing CLAUDE_PLUGIN_ROOT or table returns the fallback
             unchanged (never raises)
  runner     the template's own `--model-low` wiring: the flag is not the literal default
             "haiku" any longer (a planted disagreement in the argparse default would flip
             this), and the deprecation print plus the `resolve_role_model(o.repo, "gate",
             "haiku")` call site are both present in the template text -- a structural check
             on the shipped bytes; this file does not drive a full `--case` run of the
             emitted runner (that spawns real `claude -p` children and needs a whole
             board/model-dir/gwt-dir/cost-table pipeline no selftest should carry)

Usage: python3 scripts/selftest-routing-agents.py     exit 0 = PASS, 1 = FAIL
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPILER = os.path.join(PLUGIN, "scripts", "compile-routing-agents.py")
DEFAULT_TABLE = os.path.join(PLUGIN, "rules", "routing-default.json")
COMMITTED_AGENTS_DIR = os.path.join(PLUGIN, "agents")
RUNNER_COMPILER = os.path.join(PLUGIN, "scripts", "compile-model-workflow.py")


def run(args, **kw):
    return subprocess.run([sys.executable, COMPILER] + args, capture_output=True, text=True, timeout=60, **kw)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def extract_resolve_role_model():
    """Pulls the literal `def resolve_role_model(...): ...` block out of the RUNNER_TEMPLATE
    string in scripts/compile-model-workflow.py (the actual shipped bytes, not a
    reimplementation) and execs it in an isolated namespace so its behaviour can be unit
    tested without driving the whole board/model-dir/gwt-dir/cost-table pipeline."""
    with open(RUNNER_COMPILER, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"\ndef resolve_role_model\(.*?\n\n\ndef cap_text", text, re.DOTALL)
    if not m:
        return None, "def resolve_role_model(...) not found before def cap_text in %s" % RUNNER_COMPILER
    func_src = m.group(0).strip()[:-len("\n\ndef cap_text")].strip()
    ns = {"os": os, "json": json}
    try:
        exec(compile(func_src, "<resolve_role_model>", "exec"), ns)
    except Exception as e:
        return None, "extracted function failed to exec: %r" % (e,)
    return ns.get("resolve_role_model"), None


def main():
    results = []

    def check(name, cond, detail):
        results.append(cond)
        print(("PASS " if cond else "FAIL ") + name + ": " + detail)

    default_obj = json.load(open(DEFAULT_TABLE))
    all_roles = sorted(default_obj["roles"].keys())

    tmp = tempfile.mkdtemp(prefix="hyp-selftest-routing-agents-")
    try:
        # --- compiler: byte-stability across two independent --emit runs --------------
        out1 = os.path.join(tmp, "emit-1")
        out2 = os.path.join(tmp, "emit-2")
        r1 = run([DEFAULT_TABLE, "--emit", out1])
        r2 = run([DEFAULT_TABLE, "--emit", out2])
        names1 = sorted(os.listdir(out1)) if os.path.isdir(out1) else []
        names2 = sorted(os.listdir(out2)) if os.path.isdir(out2) else []
        bytes_equal = names1 == names2 and all(read(os.path.join(out1, n)) == read(os.path.join(out2, n)) for n in names1)
        check("compiler-byte-stable-across-runs", r1.returncode == 0 and r2.returncode == 0 and bytes_equal,
              "rc1=%d rc2=%d names_equal=%s bytes_equal=%s" % (r1.returncode, r2.returncode, names1 == names2, bytes_equal))

        # --- compiler: every table role gets a file naming the table's model -----------
        model_ok = True
        detail_bad = []
        for role in all_roles:
            cls = default_obj["roles"][role]
            expect_model = default_obj["classes"][cls]["model"]
            path = os.path.join(out1, "hyp-%s.md" % role)
            text = read(path)
            if text is None or ("model: %s" % expect_model) not in text or ("name: hyp-%s" % role) not in text:
                model_ok = False
                detail_bad.append(role)
        check("compiler-every-role-emitted-with-table-model", model_ok and len(names1) == len(all_roles),
              "roles=%d files=%d bad=%r" % (len(all_roles), len(names1), detail_bad[:5]))

        # --- compiler: --check passes against THIS repository's own committed agents/ --
        rc_check = run([DEFAULT_TABLE, "--check"])
        check("compiler-check-passes-against-committed-agents", rc_check.returncode == 0,
              "rc=%d stdout=%r" % (rc_check.returncode, rc_check.stdout.strip()[:200]))

        # --- compiler: --check fails on a planted disagreement --------------------------
        corrupt_dir = os.path.join(tmp, "corrupt")
        shutil.copytree(out1, corrupt_dir)
        victim = os.path.join(corrupt_dir, "hyp-build.md")
        with open(victim, "w", encoding="utf-8") as f:
            f.write(read(victim).replace("model: sonnet", "model: haiku"))
        rc_bad = run([DEFAULT_TABLE, "--check", "--emit", corrupt_dir])
        check("compiler-check-fails-on-planted-disagreement",
              rc_bad.returncode == 1 and "disagrees:" in rc_bad.stdout and "hyp-build.md" in rc_bad.stdout,
              "rc=%d stdout=%r" % (rc_bad.returncode, rc_bad.stdout.strip()[:200]))

        # --- compiler: --check reports a missing file, not a silent pass ----------------
        missing_dir = os.path.join(tmp, "missing")
        shutil.copytree(out1, missing_dir)
        os.remove(os.path.join(missing_dir, "hyp-refute.md"))
        rc_missing = run([DEFAULT_TABLE, "--check", "--emit", missing_dir])
        check("compiler-check-reports-missing-file",
              rc_missing.returncode == 1 and "missing:" in rc_missing.stdout and "hyp-refute.md" in rc_missing.stdout,
              "rc=%d stdout=%r" % (rc_missing.returncode, rc_missing.stdout.strip()[:200]))

        # --- compiler: --role narrows to exactly the roles asked -------------------------
        narrow_dir = os.path.join(tmp, "narrow")
        rc_narrow = run([DEFAULT_TABLE, "--emit", narrow_dir, "--role", "build", "--role", "refute"])
        narrow_names = sorted(os.listdir(narrow_dir)) if os.path.isdir(narrow_dir) else []
        check("compiler-role-flag-narrows-output", rc_narrow.returncode == 0 and narrow_names == ["hyp-build.md", "hyp-refute.md"],
              "rc=%d names=%r" % (rc_narrow.returncode, narrow_names))

        # --- runner template: resolve_role_model extracted from the shipped bytes -------
        resolve_role_model, extract_err = extract_resolve_role_model()
        check("runner-resolve-role-model-extracted", resolve_role_model is not None, extract_err or "ok")

        if resolve_role_model is not None:
            repo = os.path.join(tmp, "consumer-repo")
            os.makedirs(repo, exist_ok=True)
            plugin_root = os.path.join(tmp, "synthetic-plugin")
            os.makedirs(os.path.join(plugin_root, "rules"), exist_ok=True)
            shutil.copyfile(DEFAULT_TABLE, os.path.join(plugin_root, "rules", "routing-default.json"))

            had_root = "CLAUDE_PLUGIN_ROOT" in os.environ
            saved_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
            try:
                os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
                got = resolve_role_model(repo, "gate", "FALLBACK")
                check("runner-resolve-falls-back-with-no-plugin-root", got == "FALLBACK", "got=%r" % (got,))

                os.environ["CLAUDE_PLUGIN_ROOT"] = plugin_root
                got = resolve_role_model(repo, "gate", "FALLBACK")
                expect = default_obj["classes"][default_obj["roles"]["gate"]]["model"]
                check("runner-resolve-reads-default-table", got == expect, "got=%r expect=%r" % (got, expect))

                got_unknown = resolve_role_model(repo, "not-a-real-role", "FALLBACK")
                check("runner-resolve-falls-back-on-unknown-role", got_unknown == "FALLBACK", "got=%r" % (got_unknown,))

                os.makedirs(os.path.join(repo, ".claude"), exist_ok=True)
                with open(os.path.join(repo, ".claude", "routing.json"), "w") as f:
                    json.dump({"roles": {"gate": "think"}}, f)
                got_override = resolve_role_model(repo, "gate", "FALLBACK")
                expect_override = default_obj["classes"]["think"]["model"]
                check("runner-resolve-override-wins", got_override == expect_override,
                      "got=%r expect=%r" % (got_override, expect_override))
            finally:
                os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
                if had_root:
                    os.environ["CLAUDE_PLUGIN_ROOT"] = saved_root

        # --- runner template: the deprecation wiring is present as literal text ---------
        with open(RUNNER_COMPILER, encoding="utf-8") as f:
            runner_text = f.read()
        check("runner-model-low-default-is-not-haiku-literal",
              'ap.add_argument("--model-low", dest="model_low", default="haiku")' not in runner_text,
              "the old unconditional default must be gone")
        check("runner-model-low-deprecation-message-present",
              "--model-low is deprecated" in runner_text, "deprecation line missing")
        check("runner-model-low-resolves-gate-role",
              'resolve_role_model(o.repo, "gate", "haiku")' in runner_text, "resolution call site missing")

        # --- compiler: a non-routing/v1 table is refused, not silently misread ----------
        bad_table = os.path.join(tmp, "bad-table.json")
        with open(bad_table, "w") as f:
            json.dump({"schema": "not-routing/v1"}, f)
        rc_bad_schema = run([bad_table, "--check"])
        check("compiler-refuses-non-routing-v1-table", rc_bad_schema.returncode != 0,
              "rc=%d stderr=%r" % (rc_bad_schema.returncode, rc_bad_schema.stderr.strip()[:200]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
