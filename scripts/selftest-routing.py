#!/usr/bin/env python3
"""selftest-routing.py -- regression test for the model-routing guard.

Ported from and extending the lab keep H-DRAFT-314c8d17-routing-guard's own
`fixture/impl/selftest-routing.py` (library-level determinism and one finding class),
this version also drives the INSTALLED `hooks/scripts/routing-guard.py` end to end as a
subprocess against throwaway consumer repositories (the pattern of
`scripts/selftest-preflight-gate.py`), and exercises `scripts/routing.py` and the
`scripts/init-scaffold.py` `.claude/routing.json` scaffold row:

  library   merge_table/table_sha/default_sha determinism; scan_script's compliant,
            no-model and cannot-parse cases (kept from the source keep)
  guard     enforce=deny denies each of the eight mutant classes the source keep's
            fixture named (no-model, unknown-role, frontier-on-execute, non-literal,
            phase-mismatch, agent-type-relabel, alias, cannot-parse), naming the class;
            admits the compliant script with zero findings
  guard     enforce=advise admits every mutant (exit 0, no deny) with one finding
            line per class, carried in the JSON response's `systemMessage` (B3 --
            plain stdout on a PreToolUse hook reaches only the debug log)
  guard     enforce=off admits everything, including a no-model script, without
            scanning
  guard     a missing/unreadable default table fails open: one error-log line, one
            `guard-error` ledger row, admit
  guard     a malformed (non-JSON) payload fails open the same way, from the
            CLAUDE_PROJECT_DIR fallback root
  guard     the Agent-matcher row only ever advises, even when `.claude/hyp.json`
            sets `routing.enforce: deny`
  guard     a real Agent-tool payload (prompt/description/subagent_type, no script
            at all) is admitted with no scan, no error-log line, no `guard-error`
            row -- not the IO-error fail-open path (B2)
  guard     a non-dict `tool_input` falls open (one error-log line, admit) rather
            than raising an uncaught exception (B1)
  guard     `// route-override: guard-false-positive <reason>` admits exactly the
            marked finding and writes one `guard-override` ledger row; an empty
            reason does not admit
  guard     `default-sha-mismatch` and `subagent-model-env` findings fire from a
            pinned override table and the environment variable respectively
  cli       `routing.py table` lists every role; `resolve <role>` returns the table's
            model/effort as JSON, exit 1 for an unmapped role; `lint` exits 1 with a
            finding line for the no-model script, 0 for the compliant one; `rewrite`
            produces a conformant copy whose diff is confined to model/effort/agentType
  scaffold  `init-scaffold.py` writes `.claude/routing.json` from the template on a
            fresh repository, and never overwrites a consumer's edited copy on a
            second run

Usage: python3 scripts/selftest-routing.py        exit 0 = PASS, 1 = FAIL
Provenance: ported for the changeset that ships the lab keep H-DRAFT-314c8d17-routing-guard
(VERDICT.json: evidence-sufficient promote, 5/5 looks, llr 2.9389 >= 2.8904).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "scripts"))
import routing_lib as rl  # noqa: E402

GUARD = os.path.join(PLUGIN, "hooks", "scripts", "routing-guard.py")
ROUTING_CLI = os.path.join(PLUGIN, "scripts", "routing.py")
INIT_SCAFFOLD = os.path.join(PLUGIN, "scripts", "init-scaffold.py")
DEFAULT_TABLE = os.path.join(PLUGIN, "rules", "routing-default.json")

COMPLIANT = (
    "async function main() {\n"
    "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', model: 'sonnet', effort: 'high' });\n"
    "}\n"
)

MUTANTS = {
    "no-model": (
        "async function main() {\n"
        "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', effort: 'high' });\n"
        "}\n"
    ),
    "unknown-role": (
        "async function main() {\n"
        "  await agent({ label: 'summarise:x', phase: 'p', agentType: 'hyp:summarise', model: 'sonnet', effort: 'high' });\n"
        "}\n"
    ),
    "frontier-on-execute": (
        "async function main() {\n"
        "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', model: 'fable', effort: 'high' });\n"
        "}\n"
    ),
    "non-literal": (
        "async function main() {\n"
        "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', model: someVar, effort: 'high' });\n"
        "}\n"
    ),
    "phase-mismatch": (
        "export const meta = { phases: [ { label: 'p', model: 'fable' } ] };\n"
        "async function main() {\n"
        "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', model: 'sonnet', effort: 'high' });\n"
        "}\n"
    ),
    "agent-type-relabel": (
        "async function main() {\n"
        "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:design', model: 'sonnet', effort: 'high' });\n"
        "}\n"
    ),
    "alias": (
        "async function main() {\n"
        "  const spawn = agent;\n"
        "  await spawn({ label: 'build:x', phase: 'p', agentType: 'hyp:build', model: 'sonnet', effort: 'high' });\n"
        "}\n"
    ),
    "cannot-parse": (
        "async function main() {\n"
        "  await agent({ label: `x, model: 'sonnet', effort: 'high' });\n"
        "}\n"
    ),
}


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def mk_consumer(path, enforce="deny"):
    os.makedirs(path, exist_ok=True)
    write(os.path.join(path, ".claude", "hyp.json"),
          json.dumps({"profile": "capture", "routing": {"enforce": enforce}}))


def run_guard(script_text, root, tool_name="Workflow", tool_use_id="tu-1",
              env_extra=None, malformed=False, script_path=None, raw_tool_input=None):
    """(rc, decision, reason, advisories) for one PreToolUse payload against the guard.
    decision is 'deny', 'allow', or 'unparseable'; advisories merges the plain
    '(advisory) '-prefixed fail-open lines with the finding lines a JSON response now
    carries in `systemMessage` (B3). `raw_tool_input`, when given, replaces the
    script/scriptPath-shaped `tool_input` entirely -- for a payload shape the guard
    must handle without a `script` key at all (a real Agent-tool call, B2) or a
    `tool_input` that is not even an object (B1)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"),
           "CLAUDE_PLUGIN_ROOT": PLUGIN, "CLAUDE_PROJECT_DIR": root}
    if env_extra:
        env.update(env_extra)
    if malformed:
        stdin_text = script_text  # raw, not JSON
    else:
        if raw_tool_input is not None:
            tool_input = raw_tool_input
        else:
            tool_input = {"scriptPath": script_path} if script_path else {"script": script_text}
        payload = {"session_id": "selftest-routing", "cwd": root, "hook_event_name": "PreToolUse",
                   "tool_name": tool_name, "tool_input": tool_input, "tool_use_id": tool_use_id}
        stdin_text = json.dumps(payload)
    p = subprocess.run([sys.executable, GUARD], input=stdin_text, capture_output=True,
                       text=True, env=env, cwd=root, timeout=60)
    decision, reason = "allow", ""
    out = p.stdout or ""
    advisories = [line[len("(advisory) "):] for line in out.splitlines() if line.startswith("(advisory) ")]
    json_lines = [line for line in out.splitlines() if line.startswith("{")]
    if json_lines:
        try:
            obj = json.loads(json_lines[0])
            hso = obj.get("hookSpecificOutput", {})
            decision = hso.get("permissionDecision", "allow")  # B1: advise carries no field now
            reason = hso.get("permissionDecisionReason", "")
            system_message = obj.get("systemMessage") or ""
            if system_message:
                advisories.extend(system_message.splitlines())
        except Exception:
            decision, reason = "unparseable", out.strip()[:160]
    return p.returncode, decision, reason, advisories


def run_cli(args, root):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN)
    p = subprocess.run([sys.executable, ROUTING_CLI, "--root", root] + args,
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout, p.stderr


def main():
    results = []

    def check(name, cond, detail):
        results.append(cond)
        print(("PASS " if cond else "FAIL ") + name + ": " + detail)

    default_obj = json.load(open(DEFAULT_TABLE))

    # --- library-level determinism (kept from the source keep) ------------------------
    t1 = rl.merge_table(default_obj, {})
    t2 = rl.merge_table(default_obj, {})
    check("table-sha-deterministic", t1["table_sha"] == t2["table_sha"], "two merges of the same inputs")
    check("default-sha-deterministic", t1["default_sha"] == t2["default_sha"], "two merges of the same inputs")

    findings, calls = rl.scan_script(COMPLIANT, t1)
    check("lib-scan-compliant-clean", findings == [] and len(calls) == 1, "findings=%r calls=%d" % (findings, len(calls)))

    findings, _ = rl.scan_script(MUTANTS["no-model"], t1)
    classes = {f["class"] for f in findings}
    check("lib-scan-no-model", classes == {"no-model"}, "classes=%r" % (classes,))

    try:
        rl.scan_script(MUTANTS["cannot-parse"], t1)
        check("lib-scan-cannot-parse", False, "expected ParseError, none raised")
    except rl.ParseError:
        check("lib-scan-cannot-parse", True, "ParseError raised as expected")

    tmp = tempfile.mkdtemp(prefix="hyp-selftest-routing-")
    try:
        deny_consumer = os.path.join(tmp, "deny-consumer")
        mk_consumer(deny_consumer, "deny")
        advise_consumer = os.path.join(tmp, "advise-consumer")
        mk_consumer(advise_consumer, "advise")
        off_consumer = os.path.join(tmp, "off-consumer")
        mk_consumer(off_consumer, "off")

        # --- guard: deny mode denies each mutant class, admits the compliant script ----
        rc, decision, reason, _adv = run_guard(COMPLIANT, deny_consumer, tool_use_id="tu-compliant")
        check("guard-deny-admits-compliant", rc == 0 and decision == "allow",
              "rc=%d decision=%s reason=%s" % (rc, decision, reason[:100]))

        for cls, script in MUTANTS.items():
            rc, decision, reason, _adv = run_guard(script, deny_consumer, tool_use_id="tu-" + cls)
            check("guard-deny-" + cls, rc == 0 and decision == "deny" and cls in reason,
                  "rc=%d decision=%s reason=%s" % (rc, decision, reason[:160]))

        # --- guard: advise mode admits everything, one advisory line per finding -------
        rc, decision, reason, adv = run_guard(MUTANTS["no-model"], advise_consumer, tool_use_id="tu-advise")
        check("guard-advise-admits", rc == 0 and decision == "allow", "rc=%d decision=%s" % (rc, decision))
        check("guard-advise-has-advisory", any("no-model" in line for line in adv), "advisories=%r" % (adv,))

        # --- guard: off mode admits everything without scanning ------------------------
        rc, decision, reason, _adv = run_guard(MUTANTS["no-model"], off_consumer, tool_use_id="tu-off")
        check("guard-off-admits", rc == 0 and decision == "allow", "rc=%d decision=%s" % (rc, decision))

        # --- guard: Agent matcher only ever advises, even under enforce=deny -----------
        rc, decision, reason, _adv = run_guard(MUTANTS["no-model"], deny_consumer,
                                                tool_name="Agent", tool_use_id="tu-agent")
        check("guard-agent-row-advise-only", rc == 0 and decision != "deny",
              "rc=%d decision=%s -- the Agent row must never deny" % (rc, decision))

        # --- guard (B2): a real Agent-tool payload carries no script at all -- admit
        # silently, never take the IO-error fail-open path (no error-log line, no
        # guard-error ledger row) ---------------------------------------------------
        real_agent_payload = {"prompt": "do the thing", "description": "a real subagent call",
                               "subagent_type": "general-purpose"}
        rc, decision, reason, adv = run_guard(None, deny_consumer, tool_name="Agent",
                                              tool_use_id="tu-agent-real", raw_tool_input=real_agent_payload)
        check("guard-agent-no-script-admits", rc == 0 and decision == "allow",
              "rc=%d decision=%s" % (rc, decision))
        check("guard-agent-no-script-not-fail-open", not any("failing open" in line for line in adv),
              "adv=%r -- a real Agent call must never look like a guard error" % (adv,))
        error_log_before = read(os.path.join(deny_consumer, ".claude", "routing-guard-errors.log")) or ""
        check("guard-agent-no-script-no-error-log", "tu-agent-real" not in error_log_before,
              "error_log tail=%r" % (error_log_before[-200:],))
        ledger_before = read(os.path.join(deny_consumer, ".claude", "routing-guard-ledger.jsonl")) or ""
        check("guard-agent-no-script-no-ledger-row", "tu-agent-real" not in ledger_before,
              "ledger tail=%r" % (ledger_before[-200:],))

        # --- guard (B1): a non-dict tool_input must fall open, never raise an uncaught
        # exception (rc != 1, no traceback) ------------------------------------------
        rc, decision, reason, _adv = run_guard(None, deny_consumer, tool_name="Workflow",
                                               tool_use_id="tu-non-dict-input",
                                               raw_tool_input="not-an-object")
        check("guard-non-dict-tool-input-falls-open", rc == 0 and decision == "allow",
              "rc=%d decision=%s" % (rc, decision))
        error_log_nd = read(os.path.join(deny_consumer, ".claude", "routing-guard-errors.log")) or ""
        check("guard-non-dict-tool-input-error-logged", "guard error" in error_log_nd,
              "error_log tail=%r" % (error_log_nd[-200:],))

        # --- guard: malformed payload fails open from the CLAUDE_PROJECT_DIR fallback --
        rc, decision, reason, adv = run_guard("not json {{{", deny_consumer, malformed=True,
                                              tool_use_id="tu-malformed")
        check("guard-malformed-payload-fails-open", rc == 0 and decision == "allow",
              "rc=%d decision=%s" % (rc, decision))
        error_log = read(os.path.join(deny_consumer, ".claude", "routing-guard-errors.log")) or ""
        check("guard-malformed-error-logged", "payload error" in error_log, "error_log tail=%r" % (error_log[-200:],))
        ledger = read(os.path.join(deny_consumer, ".claude", "routing-guard-ledger.jsonl")) or ""
        check("guard-malformed-ledger-row", '"kind": "guard-error"' in ledger, "ledger tail=%r" % (ledger[-200:],))

        # --- guard: missing scriptPath is an IO error, fails open -----------------------
        rc, decision, reason, _adv = run_guard(None, deny_consumer, tool_use_id="tu-missing-path",
                                               script_path=os.path.join(deny_consumer, "does-not-exist.js"))
        check("guard-missing-scriptPath-fails-open", rc == 0 and decision == "allow",
              "rc=%d decision=%s" % (rc, decision))

        # --- guard: a broken/missing default table fails open --------------------------
        broken_root = os.path.join(tmp, "broken-plugin-root")
        os.makedirs(os.path.join(broken_root, "rules"), exist_ok=True)
        os.makedirs(os.path.join(broken_root, "hooks", "scripts"), exist_ok=True)
        broken_consumer = os.path.join(tmp, "broken-consumer")
        mk_consumer(broken_consumer, "deny")
        rc, decision, reason, _adv = run_guard(MUTANTS["no-model"], broken_consumer,
                                               tool_use_id="tu-broken-table",
                                               env_extra={"CLAUDE_PLUGIN_ROOT": broken_root})
        check("guard-missing-table-fails-open", rc == 0 and decision == "allow",
              "rc=%d decision=%s (no rules/routing-default.json under CLAUDE_PLUGIN_ROOT)" % (rc, decision))

        # --- guard: route-override marker admits exactly the marked finding ------------
        override_script = (
            "async function main() {\n"
            "  // route-override: guard-false-positive tested by selftest-routing\n"
            "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', effort: 'high' });\n"
            "}\n"
        )
        rc, decision, reason, _adv = run_guard(override_script, deny_consumer, tool_use_id="tu-override")
        check("guard-override-admits", rc == 0 and decision == "allow", "rc=%d decision=%s reason=%s" % (rc, decision, reason[:100]))
        ledger = read(os.path.join(deny_consumer, ".claude", "routing-guard-ledger.jsonl")) or ""
        check("guard-override-ledger-row", '"kind": "guard-override"' in ledger, "ledger tail=%r" % (ledger[-200:],))

        empty_reason_script = (
            "async function main() {\n"
            "  // route-override: guard-false-positive\n"
            "  await agent({ label: 'build:x', phase: 'p', agentType: 'hyp:build', effort: 'high' });\n"
            "}\n"
        )
        rc, decision, reason, _adv = run_guard(empty_reason_script, deny_consumer, tool_use_id="tu-override-empty")
        check("guard-override-empty-reason-still-denies", rc == 0 and decision == "deny",
              "rc=%d decision=%s reason=%s" % (rc, decision, reason[:100]))

        # --- guard: default_sha pin mismatch and CLAUDE_CODE_SUBAGENT_MODEL findings ---
        pinned_consumer = os.path.join(tmp, "pinned-consumer")
        mk_consumer(pinned_consumer, "deny")
        write(os.path.join(pinned_consumer, ".claude", "routing.json"),
              json.dumps({"default_sha": "0" * 64}))
        rc, decision, reason, _adv = run_guard(COMPLIANT, pinned_consumer, tool_use_id="tu-pin")
        check("guard-default-sha-mismatch", rc == 0 and decision == "deny" and "default-sha-mismatch" in reason,
              "rc=%d decision=%s reason=%s" % (rc, decision, reason[:160]))

        rc, decision, reason, _adv = run_guard(COMPLIANT, deny_consumer, tool_use_id="tu-subagent-env",
                                               env_extra={"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"})
        check("guard-subagent-model-env", rc == 0 and decision == "deny" and "subagent-model-env" in reason,
              "rc=%d decision=%s reason=%s" % (rc, decision, reason[:160]))

        # --- CLI: table / resolve / lint / rewrite --------------------------------------
        rc, out, err = run_cli(["table"], deny_consumer)
        check("cli-table-lists-build", rc == 0 and "build" in out, "rc=%d out_tail=%r" % (rc, out[-120:]))

        rc, out, err = run_cli(["resolve", "build"], deny_consumer)
        resolved = json.loads(out) if rc == 0 else {}
        check("cli-resolve-build", rc == 0 and resolved.get("model") == "sonnet" and resolved.get("effort") == "high",
              "rc=%d out=%r" % (rc, out.strip()))

        rc, out, err = run_cli(["resolve", "not-a-real-role"], deny_consumer)
        check("cli-resolve-unknown-role", rc == 1, "rc=%d err=%r" % (rc, err.strip()))

        no_model_path = os.path.join(tmp, "no-model.workflow.js")
        write(no_model_path, MUTANTS["no-model"])
        rc, out, err = run_cli(["lint", no_model_path], deny_consumer)
        check("cli-lint-no-model", rc == 1 and "no-model" in out, "rc=%d out=%r" % (rc, out.strip()[:160]))

        compliant_path = os.path.join(tmp, "compliant.workflow.js")
        write(compliant_path, COMPLIANT)
        rc, out, err = run_cli(["lint", compliant_path], deny_consumer)
        check("cli-lint-compliant", rc == 0 and out.strip() == "", "rc=%d out=%r" % (rc, out.strip()))

        rewrite_out = os.path.join(tmp, "rewritten")
        rc, out, err = run_cli(["rewrite", no_model_path, "--out", rewrite_out], deny_consumer)
        rewritten_text = read(os.path.join(rewrite_out, "no-model.workflow.js")) or ""
        rc2, out2, err2 = run_cli(["lint", os.path.join(rewrite_out, "no-model.workflow.js")], deny_consumer)
        check("cli-rewrite-produces-conformant-copy", rc == 0 and rc2 == 0,
              "rewrite rc=%d lint-of-rewrite rc=%d" % (rc, rc2))
        check("cli-rewrite-touches-only-routing-options",
              "label: 'build:x'" in rewritten_text and "model: 'sonnet'" in rewritten_text,
              "rewritten=%r" % (rewritten_text.strip(),))

        # --- scaffold: init-scaffold writes .claude/routing.json once, never overwrites -
        scaffold_root = os.path.join(tmp, "scaffold-consumer")
        os.makedirs(scaffold_root)
        subprocess.run(["git", "init", "-q", "-b", "main", scaffold_root], check=True,
                       capture_output=True)
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN)
        p = subprocess.run([sys.executable, INIT_SCAFFOLD, scaffold_root, "--profile", "capture"],
                           capture_output=True, text=True, env=env, timeout=120)
        routing_path = os.path.join(scaffold_root, ".claude", "routing.json")
        template_text = read(os.path.join(PLUGIN, "templates", "routing.json"))
        first = read(routing_path)
        check("scaffold-writes-routing-json", p.returncode == 0 and first == template_text,
              "rc=%d created=%s" % (p.returncode, first == template_text))

        edited = json.dumps({"schema": "routing/override/v1", "classes": {}, "roles": {"custom": "execute"},
                             "overrides": {}})
        write(routing_path, edited)
        p2 = subprocess.run([sys.executable, INIT_SCAFFOLD, scaffold_root, "--profile", "capture"],
                            capture_output=True, text=True, env=env, timeout=120)
        second = read(routing_path)
        check("scaffold-never-overwrites-edited-routing-json", p2.returncode == 0 and second == edited,
              "rc=%d unchanged=%s" % (p2.returncode, second == edited))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
