#!/usr/bin/env python3
"""selftest-preflight-gate.py -- regression test for the run-shaped PreToolUse gate.

Builds a throwaway consumer repository under a temp dir (experiments profile, one spec
that passes the shipped preflight, one that is MALFORMED) and drives the INSTALLED
hooks/scripts/preflight-gate.py (the tree this file lives in) with PreToolUse payloads:

  deny   a real headless run naming a spec that does not exist          (true positive)
  deny   a real headless run naming a lane path with no matching spec   (true positive)
  deny   a real headless run whose spec fails the shipped preflight     (true positive)
  deny   the same through `source ... &&`, an env-prefixed `--print`,
         a `$(timeout 90 ...)` substitution, and the eval-case command  (true positives)
  allow  a real headless run whose spec passes the shipped preflight
  allow  the porter's smoke fixture: mkdir -p "$G/.claude" ... hypotheses/H-901-smoke-lane.md
  allow  the orchestrator's two gate-consumer fixtures naming experiments/runs/H-901
  allow  a .claude/jobs staging line naming a lane fixture path; the otel-spike mkdir;
         a `tee` journal-fragment heredoc naming a lane path; a `--root .claude/foo`
         flag followed by `mkdir -p` in the next command
  allow  capture-profile consumer (the gate is inert below the experiments profile)
  allow  a non-Bash payload carrying a headless command string
  deny   (documented residual) a `tee` heredoc whose body spells out the invocation in
         backticks and names a lane path -- the gate has no quoted-string awareness beyond
         treating a quoted argument as one token, exactly as the previous pattern
  pass   the gate's own `--selftest` (regex, residual, bound and timing cases) exits 0

Every allow case that used to be blocked is also checked against the PREVIOUS pattern so
the suite proves each one is a genuine regression, not a command the old gate ignored too.
None of the false-positive fixtures contains a CLI call; they are the blocked commands
with paths neutralized.

Usage: python3 scripts/selftest-preflight-gate.py        exit 0 = PASS, 1 = FAIL
Provenance: lab session 2026-09-11 -- a porter's smoke command blocked (evaluator-port
refute advisory 5) and two orchestrator commands naming a lane path blocked by the
`\\bclaude\\b[^\\n]*\\s(-p|--print)\\b` pattern; changeset preflight-gate-headless-regex.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(PLUGIN, "hooks", "scripts", "preflight-gate.py")

# The pattern this change replaces; kept here only to prove each fixture used to match.
OLD_HEADLESS_RE = re.compile(r"\bclaude\b[^\n]*\s(-p|--print)\b")

PASSING_SPEC = """# H-001-passing: a spec the shipped preflight accepts

## Status
active

## Hypothesis
A worktree-scoped fixture is graded the same by both arms.

## Variable under test
One.

## Baseline
The current practice.

## Method
1. Run both arms in a scratch worktree with the pinned fixture.
- Fixture: pinned starting state both arms share
- Repetitions per arm: 3
- Budget per run: 10 min wall-clock
The answer key stays harness-side and is never shown to arms.
Frozen at registration: the corpus, keys, prompts, and grading rubric.

## Binary assertions
1. a
2. b
3. c

## Verdict rule
Keep if all assertions pass; otherwise refine or discard.

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""

MALFORMED_SPEC = """# H-002-failing: a spec missing most required sections

## Status
active

## Hypothesis
x
"""


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def mk_consumer(path, profile):
    os.makedirs(path)
    write(path, ".claude/hyp.json", json.dumps({"profile": profile, "context": "selftest"}))
    write(path, "hypotheses/TEMPLATE.md", "# H-NNN-slug\n")
    write(path, "hypotheses/H-001-passing.md", PASSING_SPEC)
    write(path, "hypotheses/H-002-failing.md", MALFORMED_SPEC)
    write(path, "experiments/runs/.keep", "")


def run_gate(command, root, tool_name="Bash"):
    """(exit code, decision, reason) for one PreToolUse payload against the gate."""
    payload = {"session_id": "selftest-gate", "cwd": root, "hook_event_name": "PreToolUse",
               "tool_name": tool_name, "tool_input": {"command": command}}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"),
           "CLAUDE_PLUGIN_ROOT": PLUGIN, "CLAUDE_PROJECT_DIR": root}
    p = subprocess.run([sys.executable, GATE], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, cwd=root, timeout=120)
    decision, reason = "allow", ""
    if p.stdout.strip():
        try:
            out = json.loads(p.stdout)["hookSpecificOutput"]
            decision = out.get("permissionDecision", "?")
            reason = out.get("permissionDecisionReason", "")
        except Exception:
            decision, reason = "unparseable", p.stdout.strip()[:120]
    return p.returncode, decision, reason


# --- true positives: real headless runs the gate must still deny -------------------------
DENIES = [
    ("deny-missing-spec",
     'claude -p "$(cat hypotheses/H-999-missing.md)"',
     "references hypotheses/H-999-missing.md, which does not exist"),
    ("deny-lane-without-spec",
     'claude -p "run it" > experiments/runs/H-777/out.md',
     "no hypothesis spec matches 'H-777'"),
    ("deny-spec-fails-preflight",
     'cat hypotheses/H-002-failing.md | claude -p "run this spec"',
     "hypotheses/H-002-failing.md fails the deterministic preflight (exit 2)"),
    ("deny-after-source-and",
     'source ~/.claude/.token.env && claude -p "go" hypotheses/H-999-missing.md',
     "references hypotheses/H-999-missing.md, which does not exist"),
    ("deny-env-prefixed-print",
     "CLAUDE_CONFIG_DIR=/tmp/x claude --print < hypotheses/H-999-missing.md",
     "references hypotheses/H-999-missing.md, which does not exist"),
    ("deny-inside-substitution",
     'OUT=$(timeout 90 claude -p "Reply with exactly: OK" --model haiku --output-format json '
     '--settings \'{"disableAllHooks": true}\' 2>/dev/null); cat hypotheses/H-999-missing.md',
     "references hypotheses/H-999-missing.md, which does not exist"),
    ("deny-eval-case-command",
     "mkdir -p experiments/runs/parallel-runner/run-1 && claude -p 'Switch the sample project to "
     "the parallel test runner' > experiments/runs/parallel-runner/run-1/output.md",
     "no hypothesis spec matches 'parallel-runner'"),
]

# --- false-positive regressions: the blocked commands, rewritten without a CLI call ------
# Each names a spec or lane path the consumer lacks, so the OLD pattern denied it.
REGRESSIONS = [
    ("allow-porter-smoke-fixture",
     'WT=/Users/u/src/hyp-machine/.claude/worktrees/decision-door-evaluator; '
     'G=/private/tmp/crux/ship-evaluator/smoke-gate; rm -rf "$G"; '
     'mkdir -p "$G/ledger" "$G/.claude" "$G/hypotheses" "$G/experiments/runs/H-901" && cd "$G" && '
     'git init -q . && git config user.name smoke && git config commit.gpgsign false && '
     'printf \'{"ledger_file": "ledger/ledger.jsonl", "profile": "experiments"}\\n\' > .claude/hyp.json && '
     ': > ledger/ledger.jsonl && '
     'printf \'# H-901\\n\\n## Status\\nactive\\n\\nBudget per run: US$3\\n\' > hypotheses/H-901-smoke-lane.md && '
     "printf '1\\n' > experiments/runs/H-901/chain-terminal.run1"),
    ("allow-orchestrator-gate-consumer",
     "cd /Users/u/src/hyp-machine/.claude/worktrees/decision-door-fields && "
     "R=/private/tmp/crux/ship-door-fields-2/gate-consumer && rm -rf $R && "
     "mkdir -p $R/.claude $R/hypotheses $R/experiments/runs/H-901 $R/ledger && "
     "echo '{\"profile\":\"experiments\",\"ledger_file\":\"ledger/work-ledger.jsonl\"}' > $R/.claude/hyp.json && "
     "printf '# H-901 scratch lane\\n\\n## Status\\nactive\\n' > $R/hypotheses/H-901-scratch-lane.md && "
     "echo 1 > $R/experiments/runs/H-901/chain-terminal.run1 && git -C $R init -q"),
    ("allow-orchestrator-gate-consumer-nobudget",
     "cd /Users/u/src/hyp-machine/.claude/worktrees/decision-door-fields && "
     "R=/private/tmp/crux/ship-door-fields-2/gate-consumer-nobudget && rm -rf $R && "
     "mkdir -p $R/.claude $R/hypotheses $R/experiments/runs/H-901 $R/ledger && "
     "printf '# H-901 scratch lane\\n\\n## Status\\nactive\\n' > $R/hypotheses/H-901-scratch-lane.md && "
     "echo 1 > $R/experiments/runs/H-901/chain-terminal.run1"),
    ("allow-staging-under-claude-jobs",
     "cd /Users/u/src/lab\n"
     "E=experiments/runs/H-DRAFT-73404199-decision-door-evaluator/fixture\n"
     "S=/Users/u/.claude/jobs/6fce2307/tmp/evaluator-landing-stage; rm -rf $S; mkdir -p $S/scripts\n"
     "command cp -f $E/impl/decisions_on.py $S/scripts/decisions.py\n"
     "cd $S && python3 scripts/decisions.py --selftest"),
    ("allow-otel-spike-mkdir",
     "mkdir -p /Users/u/.claude/jobs/6fce2307/tmp/otel-spike && "
     "mkdir -p /Users/u/src/lab/experiments/runs/DESIGN-otel-third-leg/spike && "
     "python3 -c \"import socket; print('free')\""),
    ("allow-tee-fragment-heredoc",
     "cd /Users/u/src/lab\n"
     "tee experiments/journal-fragments/0448-distributed-network-north-star.md >/dev/null <<'FRAGEOF'\n"
     "---\nid: 0448\n---\n\n# Distributed-network North Star landed (design-only)\n\n"
     "Research pack in experiments/runs/DESIGN-distributed-network/research/. Sessions stage\n"
     "scratch under ~/.claude/jobs/<id>/tmp with mkdir -p; nothing here launches a run.\n"
     "FRAGEOF"),
    ("allow-root-flag-then-mkdir",
     "python3 x.py --root .claude/foo hypotheses/H-999-missing.md && mkdir -p bar"),
]


# --- documented residuals: not launches, read as one exactly as the previous pattern did --
# The exact 2026-09-11 `tee` fragment stayed denied after the pattern change because its body
# quotes the invocation in backticks (a backtick is a command-position character). Pinned so
# the behavior is explicit; the fix is quoted-string awareness, not a wider pattern.
RESIDUALS = [
    ("residual-deny-heredoc-quoting-invocation",
     "cd /Users/u/src/lab\n"
     "tee experiments/journal-fragments/0448-x.md >/dev/null <<'FRAGEOF'\n"
     "Research pack in experiments/runs/H-777/research/. Sessions launch arms with `claude" " -p`\n"
     "under the job directory; nothing here launches a run.\nFRAGEOF",
     "no hypothesis spec matches 'H-777'"),
]


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-selftest-gate-")
    results = []

    def check(name, cond, detail):
        results.append(cond)
        print(("PASS " if cond else "FAIL ") + name + ": " + detail)

    try:
        consumer = os.path.join(tmp, "consumer")
        mk_consumer(consumer, "experiments")
        capture = os.path.join(tmp, "capture-only")
        mk_consumer(capture, "capture")

        for name, command, want_reason in DENIES:
            rc, decision, reason = run_gate(command, consumer)
            check(name, rc == 0 and decision == "deny" and want_reason in reason,
                  "rc=%d decision=%s reason=%s" % (rc, decision, (reason or "(none)")[:110]))

        rc, decision, reason = run_gate('cat hypotheses/H-001-passing.md | claude -p "run this spec"',
                                        consumer)
        check("allow-spec-passes-preflight", rc == 0 and decision == "allow",
              "rc=%d decision=%s %s" % (rc, decision, reason[:80]))

        for name, command in REGRESSIONS:
            check(name + "-was-blocked-before", bool(OLD_HEADLESS_RE.search(command)),
                  "the previous pattern matched this command")
            rc, decision, reason = run_gate(command, consumer)
            check(name, rc == 0 and decision == "allow",
                  "rc=%d decision=%s %s" % (rc, decision, reason[:110]))

        for name, command, want_reason in RESIDUALS:
            rc, decision, reason = run_gate(command, consumer)
            check(name, rc == 0 and decision == "deny" and want_reason in reason,
                  "rc=%d decision=%s reason=%s" % (rc, decision, (reason or "(none)")[:110]))

        rc, decision, reason = run_gate('claude -p "go" hypotheses/H-999-missing.md', capture)
        check("allow-below-experiments-profile", rc == 0 and decision == "allow",
              "rc=%d decision=%s" % (rc, decision))
        rc, decision, reason = run_gate('claude -p "go" hypotheses/H-999-missing.md', consumer,
                                        tool_name="Read")
        check("allow-non-bash-tool", rc == 0 and decision == "allow",
              "rc=%d decision=%s" % (rc, decision))

        p = subprocess.run([sys.executable, GATE, "--selftest"], capture_output=True, text=True,
                           timeout=120)
        last = (p.stdout.strip().splitlines() or ["(no output)"])[-1]
        check("gate-regex-selftest", p.returncode == 0 and last.startswith("RESULT: PASS"),
              "rc=%d %s" % (p.returncode, last[:80]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
