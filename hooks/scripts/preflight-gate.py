#!/usr/bin/env python3
"""PreToolUse preflight gate (hyp, experiments profile).

Denies run-shaped Bash invocations whose hypothesis spec is missing or fails
the shipped deterministic preflight. Run-shaped means: a headless agent
invocation (`claude -p` / `claude --print`) tied to an experiment — the
command references a spec path under the hypotheses directory, or a path
under the runs directory whose first segment is matched against registered
specs. Reads and ordinary commands are never gated.

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

HEADLESS_RE = re.compile(r"\bclaude\b[^\n]*\s(-p|--print)\b")
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


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
