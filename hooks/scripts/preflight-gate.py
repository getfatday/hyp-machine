#!/usr/bin/env python3
"""PreToolUse preflight gate (hyp, experiments profile).

Denies run-shaped Bash invocations whose hypothesis spec is missing or fails
the shipped deterministic preflight. Run-shaped means: a headless agent
invocation (`claude -p` / `claude --print`) tied to an experiment — the
command references a spec path under the hypotheses directory, or a path
under the runs directory whose first segment is matched against registered
specs. Reads and ordinary commands are never gated. A headless invocation is the
CLI name as a command word followed by its own `-p`/`--print` (HEADLESS_RE below);
a `.claude/...` path or an unrelated `mkdir -p` elsewhere in the command never counts.

Everything else passes through untouched, and any internal error fails open:
a crashing PreToolUse hook would block every tool call, which is worse than a
missed gate. The loop discipline itself (spec before anything runs) lives in
the hypothesis skill; this hook is the deterministic backstop.
"""
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import load_config, profile_at_least, resolve_root

# A headless run is the CLI name in COMMAND position followed by the print flag among its
# OWN arguments. Command position: the start of the command, or right after whitespace or
# a shell separator (; & | ( `) -- never after '.', '/', '-' or a word character, so a
# `.claude/...` path, `~/.claude/local/claude`, `--claude`, `claude-code` and
# `CLAUDE_CONFIG_DIR=` are not the CLI. The argument list is a run of tokens (bare words,
# '...' and "..." strings) joined by spaces, tabs or a backslash-newline continuation; a
# separator or a bare newline ends it, so `mkdir -p` in the next command never counts.
# The previous pattern, `\bclaude\b[^\n]*\s(-p|--print)\b`, took `mkdir -p "$G/.claude" ...`
# and `S=~/.claude/jobs/x; mkdir -p $S` for headless runs and, once the command also named
# a spec or lane path, denied the whole command (lab session 2026-09-11).
_ARG_WS = r"(?:[ \t]|\\\r?\n)+"
_ARG_TOKEN = r"(?:[^\s;&|()`'\"]|'[^']*'|\"(?:\\.|[^\"\\])*\")+"
HEADLESS_RE = re.compile(
    r"(?:^|[\s;&|(`])claude"
    r"(?:" + _ARG_WS + _ARG_TOKEN + r")*?"
    + _ARG_WS + r"(?:-p|--print)(?=[\s;&|()`]|$)"
)
MAX_DETAIL_LINES = 4


def plugin_root():
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def run_preflight(spec_abs):
    """(exit code, detail lines) from the shipped preflight; None on gate error."""
    script = os.path.join(plugin_root(), "scripts", "preflight.py")
    if not os.path.isfile(script):
        return None
    try:
        result = subprocess.run(
            [sys.executable or "python3", script, spec_abs],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            text=True,
        )
    except Exception:
        return None
    lines = [line for line in result.stdout.splitlines()
             if line.startswith("FAIL") or line.startswith("MALFORMED")]
    return result.returncode, lines[:MAX_DETAIL_LINES]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if payload.get("tool_name", "") != "Bash":
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command")
    if not command or not isinstance(command, str):
        sys.exit(0)

    root = resolve_root(payload)
    cfg = load_config(root)
    if not profile_at_least(cfg, "experiments"):
        sys.exit(0)  # experiments layer not active in this repository
    hyp_dir = cfg["hypotheses_dir"].strip("/")
    runs_prefix = cfg["runs_dir"].strip("/") + "/"

    if not HEADLESS_RE.search(command):
        sys.exit(0)  # not a headless agent invocation; never gate reads or plumbing
    runs_ref = runs_prefix in command

    # 1. An explicit spec path in the command wins (the template itself is not a spec).
    spec_abs = None
    m = re.search(r"(?:^|[\s\"'=(:])(" + re.escape(hyp_dir) + r"/[A-Za-z0-9._-]+\.md)",
                  command)
    if m and m.group(1) != cfg["template_file"].strip("/"):
        spec_rel = m.group(1)
        spec_abs = os.path.join(root, spec_rel)
        if not os.path.isfile(spec_abs):
            deny("this command is run-shaped and references %s, which does not exist. "
                 "Spec before anything runs: create it from %s (hypothesis skill), "
                 "pass the preflight, then re-run." % (spec_rel, cfg["template_file"]))

    # 2. Otherwise resolve the spec from the runs-directory path segment.
    if spec_abs is None and runs_ref:
        m2 = re.search(re.escape(runs_prefix) + r"([A-Za-z0-9._-]+)", command)
        run_id = m2.group(1) if m2 else None
        if run_id:
            template_base = os.path.basename(cfg["template_file"])
            candidates = sorted(
                p for p in glob.glob(os.path.join(root, hyp_dir, "*.md"))
                if os.path.basename(p) != template_base
                and (os.path.basename(p).startswith(run_id)
                     or run_id in os.path.basename(p)))
            if candidates:
                spec_abs = candidates[0]
            else:
                deny("this command is run-shaped (it references %s%s) but no "
                     "hypothesis spec matches '%s' under %s/. Spec before anything "
                     "runs: register %s/H-NNN-<slug>.md from %s (hypothesis skill), "
                     "pass the preflight, then re-run."
                     % (runs_prefix, run_id, run_id, hyp_dir, hyp_dir,
                        cfg["template_file"]))

    if spec_abs is None:
        sys.exit(0)  # headless but tied to no spec or runs path; stay narrow

    checked = run_preflight(spec_abs)
    if checked is None:
        sys.exit(0)  # gate machinery failed; fail open
    code, lines = checked
    if code == 0:
        sys.exit(0)
    spec_rel = os.path.relpath(spec_abs, root)
    deny("the referenced spec %s fails the deterministic preflight (exit %d): %s. "
         "Fix the FAIL lines (python3 %s %s), then re-run."
         % (spec_rel, code, " | ".join(lines) if lines else "see preflight output",
            cfg["preflight_file"], spec_rel))


# --- selftest ---------------------------------------------------------------------------
# (name, command, headless?) for `python3 preflight-gate.py --selftest`. The `cli-*` cases
# are real invocations that must match; the `path-*` cases are the commands the previous
# pattern blocked (lab session 2026-09-11: a porter's smoke fixture, evaluator-port refute
# advisory 5; two orchestrator gate-consumer fixtures; `.claude/jobs` staging lines), paths
# neutralized and no CLI call inside; the `word-*` cases pin the edges of the rule.
SELFTEST_CASES = [
    ("cli-print-flag-first", 'claude -p "hello"', True),
    ("cli-long-flag-alone", "claude --print", True),
    ("cli-flags-before-print",
     'claude --model sonnet --max-turns 50 -p "$(cat prompt.txt)"', True),
    ("cli-after-source-and", "source ~/.claude/.token.env && claude -p 'go'", True),
    ("cli-env-prefixed", "CLAUDE_CONFIG_DIR=/tmp/x claude --print", True),
    ("cli-inside-substitution",
     'OUT=$(timeout 90 claude -p "Reply with exactly: OK" --model haiku --output-format json '
     '--settings \'{"disableAllHooks": true}\' 2>/dev/null)', True),
    ("cli-after-pipe", "echo hi | claude -p", True),
    ("cli-eval-case",
     "mkdir -p experiments/runs/parallel-runner/run-1 && claude -p 'go' > out.md", True),
    ("cli-line-continuation", 'nohup claude --model sonnet \\\n  -p "run" > log 2>&1 &', True),
    ("cli-quoted-arg-then-flag", 'claude --settings \'{"a": "b -p c"}\' -p x', True),
    ("cli-flag-then-separator", "cd /tmp && claude --print; echo done", True),
    ("cli-in-subshell-tab", "(cd x &&\tclaude -p y)", True),
    ("path-porter-smoke-fixture",
     'WT=/Users/u/src/hyp-machine/.claude/worktrees/decision-door-evaluator; '
     'G=/private/tmp/crux/smoke-gate; rm -rf "$G"; '
     'mkdir -p "$G/ledger" "$G/.claude" "$G/hypotheses" "$G/experiments/runs/H-901" && '
     'cd "$G" && printf \'# H-901\\n\' > hypotheses/H-901-smoke-lane.md', False),
    ("path-orchestrator-gate-consumer",
     "cd /Users/u/src/hyp-machine/.claude/worktrees/decision-door-fields && "
     "R=/private/tmp/crux/gate-consumer && rm -rf $R && "
     "mkdir -p $R/.claude $R/hypotheses $R/experiments/runs/H-901 $R/ledger && "
     "printf '# H-901\\n' > $R/hypotheses/H-901-scratch-lane.md", False),
    ("path-staging-under-claude-jobs",
     "S=/Users/u/.claude/jobs/6fce2307/tmp/evaluator-landing-stage; rm -rf $S; "
     "mkdir -p $S/scripts; "
     "cp experiments/runs/H-DRAFT-73404199-decision-door-evaluator/fixture/impl/a.py $S/scripts/",
     False),
    ("path-otel-spike-mkdir",
     "mkdir -p /Users/u/.claude/jobs/6fce2307/tmp/otel-spike && "
     "mkdir -p /Users/u/src/lab/experiments/runs/DESIGN-otel-third-leg/spike && "
     "python3 -c 'print(1)'", False),
    ("path-root-flag-then-mkdir", "python3 x.py --root .claude/foo && mkdir -p bar", False),
    ("path-prefixed-binary", ".claude/plugins/cache/hyp-machine/hyp/0.17.2/bin/claude -p x", False),
    ("path-oauth-env-driver",
     "rm -rf /Users/u/.claude/jobs/6fce2307/tmp/h108/repro && "
     "mkdir -p /Users/u/.claude/jobs/6fce2307/tmp/h108/repro && "
     "CLAUDE_CODE_OAUTH_TOKEN=calibration-dummy python3 experiments/runs/H-108/fixture/run_h108.py "
     "--run-n 1", False),
    ("word-flag-belongs-to-next-command", "claude --version && mkdir -p out", False),
    ("word-newline-separates-commands", "claude\nmkdir -p out", False),
    ("word-inside-quoted-string", 'git commit -m "claude -p test"', False),
    ("word-hyphenated-name", "claude-code -p x", False),
    ("word-suffix-of-longer-word", "xclaude -p x", False),
    ("word-grep-then-mkdir", "grep -rn claude hooks/ && mkdir -p out", False),
    ("word-heredoc-fragment",
     "tee experiments/journal-fragments/0448-x.md <<'EOF'\n# Claude Code north star\n"
     "Scratch is staged with mkdir -p under the job directory.\nEOF", False),
]


def selftest():
    """Regex regression cases: exit 0 when every case reads as expected."""
    failures = 0
    for name, command, want in SELFTEST_CASES:
        got = bool(HEADLESS_RE.search(command))
        ok = got == want
        failures += 0 if ok else 1
        print("%s %s: headless=%s (want %s)" % ("PASS" if ok else "FAIL", name, got, want))
    print("RESULT: %s (%d/%d)" % ("PASS" if not failures else "FAIL",
                                  len(SELFTEST_CASES) - failures, len(SELFTEST_CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
