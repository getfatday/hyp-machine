#!/usr/bin/env python3
"""PreToolUse preflight gate (hyp, experiments profile).

Denies run-shaped Bash invocations whose hypothesis spec is missing or fails
the shipped deterministic preflight. Run-shaped means: a headless agent
invocation (`claude -p` / `claude --print`) tied to an experiment — the
command references a spec path under the hypotheses directory, or a path
under the runs directory whose first segment is matched against registered
specs. Reads and ordinary commands are never gated. A headless invocation is the
CLI name as a command word followed by its own `-p`/`--print` within a bounded window
(`is_headless` below); a `.claude/...` path or an unrelated `mkdir -p` in another command
never counts. The rule has no general quoted-string awareness: prose or a heredoc body
that spells out an invocation reads as one, exactly as before (README, Known limitations).

Everything else passes through untouched, and any internal error fails open:
a crashing PreToolUse hook would block every tool call, which is worse than a
missed gate. The loop discipline itself (spec before anything runs) lives in
the hypothesis skill; this hook is the deterministic backstop.
"""
import bisect
import glob
import json
import os
import re
import subprocess
import sys
import time

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
#
# Cost is bounded so the hook stays cheap on EVERY Bash call (the hooks.json `if` filter is
# not a prefilter under current CLIs): a command is scanned only when it contains a standalone
# CLI word AND a standalone flag token after it (two linear passes), and each CLI word is then
# checked within a window of MAX_ARG_TOKENS argument tokens / MAX_ARG_SPAN characters, so a
# long single line with many mentions of the CLI costs mentions x window, never mentions x
# line. One unbounded regex re-scanned the rest of the line per mention: 47 s on a 1 MB line
# with 1,000 mentions and 3.5 s on a 93 KB chain of commit messages; the original pattern took
# 18 s on `(claude) ` x 20,000 (all Python 3.9, this host). The lab's 394 recorded launches put
# the flag at most 20 tokens / 393 characters after the CLI word; the caps sit 3x / 10x above.
# A launch whose flag sits beyond either cap is gated only by the skill discipline.
_ARG_WS = r"(?:[ \t]|\\\r?\n)+"
_ARG_TOKEN = r"(?:[^\s;&|()`'\"]|'[^']*'|\"(?:\\.|[^\"\\])*\")+"
_FLAG = r"(?:-p|--print)(?=[\s;&|()`]|$)"
MAX_ARG_TOKENS = 64
MAX_ARG_SPAN = 4096
# The CLI name in command position followed by argument whitespace: the same set as
# `(?:^|[\s;&|(`])` written as a lookbehind so the separator is not consumed.
_CLI_WORD_RE = re.compile(r"(?<![^\s;&|(`])claude(?=[ \t]|\\\r?\n)")
# A standalone flag token, preceded by the last character of an _ARG_WS run. Necessary, not
# sufficient (it also sees a flag inside quotes or after a bare newline); the full scan decides.
_FLAG_TOKEN_RE = re.compile(r"(?<=[ \t\n])" + _FLAG)
# The argument run from one CLI word to its own flag, at most MAX_ARG_TOKENS tokens long.
_ARGS_THEN_FLAG_RE = re.compile(
    r"(?:" + _ARG_WS + _ARG_TOKEN + r"){0,%d}?" % MAX_ARG_TOKENS + _ARG_WS + _FLAG)
MAX_DETAIL_LINES = 4


def is_headless(command):
    """True when the command holds the CLI as a command word followed by its own flag."""
    if "claude" not in command or "-p" not in command:  # `--print` contains `-p`
        return False
    first = _CLI_WORD_RE.search(command)
    if first is None:
        return False
    flags = [f.start() for f in _FLAG_TOKEN_RE.finditer(command, first.end())]
    if not flags:
        return False
    n = len(command)
    for m in _CLI_WORD_RE.finditer(command, first.start()):
        base = m.end()
        i = bisect.bisect_left(flags, base)
        if i == len(flags):
            return False  # no flag token after this or any later CLI word
        if flags[i] > base + MAX_ARG_SPAN:
            continue  # the nearest flag token lies outside this word's window
        endpos = min(n, base + MAX_ARG_SPAN + 1)
        mm = _ARGS_THEN_FLAG_RE.match(command, base, endpos)
        # A match ending exactly at an artificial endpos satisfied the trailing `$` against the
        # window edge, not the command; only a real boundary character or the real end counts.
        if mm is not None and (mm.end() < endpos or endpos == n):
            return True
    return False


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

    if not is_headless(command):
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
# neutralized and no CLI call inside; the `word-*` cases pin the edges of the rule; the
# `residual-*` cases pin what the rule still reads as a launch (README, Known limitations);
# the `bound-*` cases pin the window; `timing_cases()` adds the cost bound.
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
    ("word-quote-is-not-command-position", 'git commit -m "claude -p test"', False),
    ("word-hyphenated-name", "claude-code -p x", False),
    ("word-suffix-of-longer-word", "xclaude -p x", False),
    ("word-grep-then-mkdir", "grep -rn claude hooks/ && mkdir -p out", False),
    ("word-heredoc-fragment",
     "tee experiments/journal-fragments/0448-x.md <<'EOF'\n# Claude Code north star\n"
     "Scratch is staged with mkdir -p under the job directory.\nEOF", False),
    # `residual-*`: not launches, but read as one -- a quote is not a command-position character,
    # yet the rule has no quoted-string awareness beyond treating a quoted argument as one token,
    # exactly like the previous pattern. Pinned so a change here is a conscious decision.
    ("residual-quoted-prose-after-space", 'git commit -m "note: claude' ' -p test"', True),
    ("residual-heredoc-quoting-invocation",
     "tee experiments/journal-fragments/0448-x.md <<'EOF'\nUse `claude" " -p` for headless runs.\nEOF",
     True),
    # `bound-*`: the flag must sit within MAX_ARG_TOKENS tokens / MAX_ARG_SPAN characters.
    ("bound-flag-within-token-cap",
     "claude " + "--opt v " * (MAX_ARG_TOKENS // 2 - 1) + "-p x", True),
    ("bound-flag-beyond-token-cap",
     "claude " + "--opt v " * (MAX_ARG_TOKENS // 2 + 1) + "-p x", False),
    ("bound-flag-within-char-span",
     'claude --append-system-prompt "' + "y" * (MAX_ARG_SPAN - 128) + '" -p x', True),
    ("bound-flag-beyond-char-span",
     'claude --append-system-prompt "' + "y" * (MAX_ARG_SPAN + 128) + '" -p x', False),
]

# Timing cases: the shapes the unbounded pattern took seconds on, each under TIMING_BUDGET_S
# (best of three). Built here rather than written out; the flag-appended variants pass the
# cheap pre-check and exercise the window bound, the dense one is the remaining worst case
# scaled to 45 KB (the 1 MB version takes ~1.5 s and is documented, not asserted).
TIMING_BUDGET_S = 0.5


def timing_cases():
    cli, flag = "claude", "-" + "p"
    one_mb_line = ("x" * 1000 + " " + cli + " ") * 1000
    commit_chain = " && ".join(
        'git commit -qm "note %d: the %s session staged scratch with mkdir under the job dir"' % (i, cli)
        for i in range(1000))
    parens = ("(" + cli + ") ") * 20000
    return [
        ("time-1MB-line-1000-cli-words", one_mb_line, False),
        ("time-93KB-commit-message-chain", commit_chain, False),
        ("time-paren-cli-x20k", parens, False),
        ("time-1MB-line-then-mkdir-flag", one_mb_line + "&& mkdir " + flag + " out", False),
        ("time-commit-chain-then-mkdir-flag", commit_chain + " && mkdir " + flag + " out", False),
        ("time-dense-quoted-flag-45KB", (cli + " 'a " + flag + " b' ") * 3000 + "; true", False),
    ]


def selftest():
    """Regex and timing cases: exit 0 when every case reads as expected, within budget."""
    failures = 0
    for name, command, want in SELFTEST_CASES:
        got = is_headless(command)
        ok = got == want
        failures += 0 if ok else 1
        print("%s %s: headless=%s (want %s)" % ("PASS" if ok else "FAIL", name, got, want))
    timing = timing_cases()
    for name, command, want in timing:
        best, got = None, None
        for _ in range(3):
            t0 = time.perf_counter()
            got = is_headless(command)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        ok = got == want and best < TIMING_BUDGET_S
        failures += 0 if ok else 1
        print("%s %s: headless=%s (want %s) %.4fs (budget %.1fs, %d chars)"
              % ("PASS" if ok else "FAIL", name, got, want, best, TIMING_BUDGET_S, len(command)))
    total = len(SELFTEST_CASES) + len(timing)
    print("RESULT: %s (%d/%d)" % ("PASS" if not failures else "FAIL", total - failures, total))
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
