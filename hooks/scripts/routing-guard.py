#!/usr/bin/env python3
"""routing-guard.py -- PreToolUse hook, matcher `Workflow` (deny/advise per
`routing.enforce`) and matcher `Agent` (advise-only). Reads `tool_input.script`
inline or the file at `tool_input.scriptPath`, resolves the repository root
through `hyp_config.resolve_root`, reads `routing.enforce` from `.claude/hyp.json`
(`deny|advise|off`, default `advise`), scans the script with
`routing_lib.scan_script` against the effective table (this plugin's
`rules/routing-default.json` merged with the repository's `.claude/routing.json`),
and denies or advises naming each finding's script, line and class.

Deny is conveyed the way every other guard in this plugin conveys it --
`hookSpecificOutput.permissionDecision: deny` on stdout, exit 0 (write-once-guard.py,
preflight-gate.py) -- never a bare exit code. Advise prints one line per finding to
stdout and exits 0, same as license-join-hook.py's advisory contract.

Fail-open is reserved for payload- or IO-level errors (a missing scriptPath file, a
malformed payload, a missing or unreadable table) -- never for a parse exception over
the script's own bytes, which is its own `cannot-parse` finding, denied like any other.
A crashing PreToolUse hook would block every Workflow/Agent call, which is worse than a
missed check, so every exception path here writes a durable record (an error-log line
plus a `guard-error` ledger row) and still exits 0.

Ported from the lab keep H-DRAFT-314c8d17-routing-guard (VERDICT.json,
experiments/runs/H-DRAFT-314c8d17-routing-guard/ in the source repository); the
scanner (`routing_lib.py`) is byte-for-byte from that keep. Two drifts from the
graded fixture, both disclosed in the shipping changeset: (1) deny/advise use this
plugin's JSON convention rather than the fixture's exit-code-2 convention: the fixture
graded the hook script directly by exit code for grading simplicity, but every other
guard this plugin ships answers through hookSpecificOutput, and a mixed convention
inside one hooks.json is its own footgun; (2) the fixture's `ROUTING_GUARD_FINDINGS_JSON`
debug-only side channel is dropped -- it existed only for the harness grader, and the
JSON `permissionDecisionReason` already carries the same finding lines.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hyp_config  # noqa: E402
import routing_lib as rl  # noqa: E402

MARKS_DIRNAME = os.path.join(".claude", "routing-guard-marks")
ERROR_LOG = os.path.join(".claude", "routing-guard-errors.log")
LOCAL_LEDGER = os.path.join(".claude", "routing-guard-ledger.jsonl")
# 10 s row timeout (hooks.json); routing_lib indexes newlines and lexical spans once
# per scan and looks both up with bisect, so a 1 MB script scans in well under 1 s on
# an unloaded host (source keep's A4: 0.38 s at 1 MB, 0.52 s on the largest corpus
# script observed, 46,580 B). A script above this bound is denied loud on size alone,
# never silently truncated or let run past the timeout.
MAX_SCRIPT_BYTES = 3 * 1024 * 1024

DEFAULT_TABLE_RELPATH = os.path.join("rules", "routing-default.json")
OVERRIDE_TABLE_RELPATH = os.path.join(".claude", "routing.json")


def _plugin_root():
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _append_line(path, line):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "a") as f:
        f.write(line.rstrip("\n") + "\n")


def _append_ledger(root, row):
    _append_line(os.path.join(root, LOCAL_LEDGER), json.dumps(row, sort_keys=True))


def read_routing_enforce(root):
    """`routing.enforce` from `.claude/hyp.json`, read directly rather than through
    `hyp_config.load_config`: that shared loader's DEFAULTS only overlay known
    string-valued keys, so a nested `routing: {enforce: ...}` block is silently
    dropped by the released reader. Extending the shared DEFAULTS schema is out of
    this change's scope (a later routing release); reading the raw file here is the
    two-way-door workaround that does not touch hyp_config.py. Defaults to
    `advise`, never raises."""
    path = os.path.join(root, ".claude", "hyp.json")
    try:
        with open(path) as f:
            data = json.load(f)
        enforce = ((data or {}).get("routing") or {}).get("enforce")
        if enforce in ("deny", "advise", "off"):
            return enforce
    except Exception:
        pass
    return "advise"


def load_effective_table(root):
    default_path = os.path.join(_plugin_root(), DEFAULT_TABLE_RELPATH)
    with open(default_path) as f:
        default_obj = json.load(f)
    override_path = os.path.join(root, OVERRIDE_TABLE_RELPATH)
    override_obj = {}
    if os.path.isfile(override_path):
        with open(override_path) as f:
            override_obj = json.load(f)
    return rl.merge_table(default_obj, override_obj), override_obj


def _safe_mark_name(tool_use_id):
    """A real tool_use_id is a short opaque token; sanitize anyway (defense in depth)
    so a caller that ever passes something with a path separator can never make this
    write escape MARKS_DIRNAME or crash with FileNotFoundError -- a guard that crashes
    on a housekeeping write is worse than a slightly ugly mark filename."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", tool_use_id)[:200]


def write_mark(root, tool_use_id, which):
    if not tool_use_id:
        return
    d = os.path.join(root, MARKS_DIRNAME)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s.%s" % (_safe_mark_name(tool_use_id), which)), "w") as f:
        f.write(which + "\n")


def guard_ran(root, tool_use_id):
    """True iff both a `started` and a `finished` mark exist for tool_use_id -- a
    `started` mark with no matching `finished` mark (a forced timeout, or a real
    hook-row timeout kill) reads guard_ran: false."""
    d = os.path.join(root, MARKS_DIRNAME)
    name = _safe_mark_name(tool_use_id)
    started = os.path.isfile(os.path.join(d, name + ".started"))
    finished = os.path.isfile(os.path.join(d, name + ".finished"))
    return started and finished


def format_findings(script_label, findings):
    """One line per finding -- every applicable class is reported, never capped or
    deduplicated to a first-found class."""
    lines = []
    for f in findings:
        fix = rl.FINDING_FIX.get(f["class"], "fix this routing option")
        line = "routing-guard: %s:%s %s -- %s" % (script_label, f["line"], f["class"], fix)
        if f["class"] == "phase-mismatch" and f.get("detail"):
            # the one class whose defect sits on a line other than the call's (the
            # meta.phases[] entry): name both lines in the finding itself
            line += " (%s)" % f["detail"]
        lines.append(line)
    return lines


def check_override_marker(text, findings):
    """`// route-override: guard-false-positive <reason>` on the line immediately
    before a finding's line admits exactly that finding when <reason> is non-empty."""
    lines = text.split("\n")
    kept = []
    admitted = []
    for f in findings:
        ln = f["line"]
        marker_line = lines[ln - 2] if 1 <= ln - 1 <= len(lines) else ""
        m = None
        if "route-override: guard-false-positive" in marker_line:
            after = marker_line.split("route-override: guard-false-positive", 1)[1].strip()
            m = after if after else None
        if m:
            admitted.append({"finding": f, "reason": m})
        else:
            kept.append(f)
    return kept, admitted


def _deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def _advise(lines):
    for line in lines:
        print("(advisory) " + line)
    return 0


def _fail_open(root, tool_use_id, note):
    """Write the durable error-log line and `guard-error` ledger row, print one
    advisory note, and return 0 -- the one path every payload/IO/table error takes."""
    try:
        _append_line(os.path.join(root, ERROR_LOG), "routing-guard: " + note)
        _append_ledger(root, {"kind": "guard-error", "v": 1, "tool_use_id": tool_use_id, "error": note})
    except Exception:
        pass  # never let the error-log write itself break fail-open
    print("(advisory) routing-guard: failing open -- " + note)
    return 0


def main():
    raw = sys.stdin.read()
    tool_use_id = None
    root = None
    try:
        payload = json.loads(raw)
        tool_use_id = payload.get("tool_use_id")
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {}) or {}
        root = hyp_config.resolve_root(payload)
    except Exception as e:
        # Payload-level error before we even know the root (malformed JSON, or no
        # stdin at all): CLAUDE_PROJECT_DIR (always set by the hook environment) is
        # the fallback root, else the cwd -- resolve_root itself is not reachable
        # without a parsed payload.
        fallback_root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        return _fail_open(fallback_root, tool_use_id, "payload error: %s" % (e,))

    write_mark(root, tool_use_id, "started")

    matcher = "Agent" if tool_name == "Agent" else "Workflow"
    enforce = read_routing_enforce(root)
    if matcher == "Agent":
        enforce = "advise"  # the Agent-matcher row only ever advises

    if enforce == "off":
        write_mark(root, tool_use_id, "finished")
        return 0

    script_text = tool_input.get("script")
    script_path = tool_input.get("scriptPath")
    script_label = script_path or "<inline>"
    try:
        if script_text is None and script_path:
            with open(script_path) as f:
                script_text = f.read()
        if script_text is None:
            raise ValueError("payload carries neither script nor scriptPath")
    except Exception as e:
        write_mark(root, tool_use_id, "finished")
        return _fail_open(root, tool_use_id, "IO error reading script: %s" % (e,))

    try:
        table, override_obj = load_effective_table(root)
    except Exception as e:
        write_mark(root, tool_use_id, "finished")
        return _fail_open(root, tool_use_id, "table load error: %s" % (e,))

    pinned_default_sha = override_obj.get("default_sha")
    findings = []
    if pinned_default_sha and pinned_default_sha != table["default_sha"]:
        findings.append({"class": "default-sha-mismatch", "line": 1,
                          "detail": "installed default_sha disagrees with .claude/routing.json pin"})
    if os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL"):
        findings.append({"class": "subagent-model-env", "line": 1,
                          "detail": "CLAUDE_CODE_SUBAGENT_MODEL is set in the invoking environment"})

    if len(script_text.encode("utf-8", "replace")) > MAX_SCRIPT_BYTES:
        findings.append({"class": "too-large", "line": 1,
                          "detail": "script exceeds %d bytes, the scanner's timeout-safe bound" % MAX_SCRIPT_BYTES})
        findings, _admitted = check_override_marker(script_text, findings)
        write_mark(root, tool_use_id, "finished")
        lines = format_findings(script_label, findings)
        if not lines:
            return 0
        if enforce == "deny":
            return _deny("\n".join(lines))
        return _advise(lines)

    try:
        scan_findings, _calls = rl.scan_script(script_text, table)
        findings.extend(scan_findings)
    except rl.ParseError as e:
        findings.append({"class": "cannot-parse", "line": rl.line_of(script_text, e.offset),
                          "detail": str(e)})

    findings, admitted = check_override_marker(script_text, findings)
    for a in admitted:
        _append_ledger(root, {"kind": "guard-override", "v": 1, "tool_use_id": tool_use_id,
                               "line": a["finding"]["line"], "class": a["finding"]["class"],
                               "reason": a["reason"]})

    write_mark(root, tool_use_id, "finished")

    if not findings:
        return 0

    lines = format_findings(script_label, findings)
    if enforce == "deny":
        return _deny("\n".join(lines))
    return _advise(lines)


if __name__ == "__main__":
    sys.exit(main())
