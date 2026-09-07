#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "textual==8.2.8",
#   "textual-serve==1.1.3",
#   "textual-dev==1.8.0",
#   "opentelemetry-proto",
#   "protobuf",
# ]
# ///
"""observatory.py - hook-fed local observatory for Claude Code sessions (H-DRAFT-2c1fc974).

One file, six roles:
  serve           stdlib OTLP/HTTP receiver (+ POST /hook) -> events JSONL + state JSON, joined to
                  `claude agents --json` and to the session transcript for branch and H-id; classifies
                  every hook record by operating-model provenance (op.class / op.node) against a
                  per-repo catalog and keeps leverage / determinism ratios (H-DRAFT-fcf7b3fa lanes)
  hook            Claude Code hook entrypoint: hook JSON on stdin -> OTLP/JSON log record (with one op
                  token, never an argument, and its own self time) -> serve
  install-hooks   write/remove the async hook block in a settings.json (project or user scope)
  board           Textual TUI, a stateless tailer of the state file + events JSONL
  web             the same board served in a browser via textual-serve
  dump-state      print the state JSON
  ratios          recompute the leverage / determinism blocks from the events JSONL (deterministic)

The module top level imports ONLY stdlib so `hook` and `serve` start under python3 in
well under 100 ms. Textual is imported inside `board`/`web`; opentelemetry-proto only when a
protobuf body actually arrives.

Provenance
  Ported byte-preserving from the source lab's kept fixture
  experiments/runs/H-DRAFT-2c1fc974/fixture/observatory/observatory.py
  (sha256 4b643fb12088a699d33a0d0cbcaa80a3d2181445df3ec78ee78af41c559a6b5b, pinned in that
  directory's SHA256SUMS). Four lab keeps, each 5/5 in two consecutive counted runs:
    H-DRAFT-2c1fc974-hook-fed-observatory-board      (2026-09-04) receiver, transcript join, board
    H-DRAFT-fcf7b3fa-op-provenance-classification    (2026-09-06) one program token per call, never an argument
    H-DRAFT-fcf7b3fa-model-leverage-read-model       (2026-09-06) leverage / determinism ratios, byte-stable
    H-DRAFT-2652d478-async-spool-backfill            (2026-09-06) async spool transport + transcript backfill
  Divergences from the fixture (everything else is byte-identical):
    1. this header (provenance and the divergence list)
    2. consumer path resolution through the plugin's hooks/scripts/hyp_config.py: `hypotheses_dir`,
       `model_dir` and the directory of `preflight_file` come from `.claude/hyp.json` overlaid on the
       plugin DEFAULTS (`_hyp_cfg`); `skills/`, `.claude/skills/`, `scripts/`, `hooks/` stay conventional
       (hyp_config has no keys for them). Without hyp_config on the path (a detached copy) only an
       explicit hyp.json value counts and node / hypothesis-title lookups degrade to empty, never raise
    3. `CLAUDE_CONFIG_DIR` is honoured wherever the fixture assumed `~/.claude` (`config_dir()`):
       transcripts under `<config>/projects`, the default spool, `--scope user` settings
    4. the post-transport hook command is `python3 <this file> hook --port N` (plugin convention) instead
       of an absolute interpreter path; `_is_ours` still matches on `observatory.py hook --port`
    5. `SELF_PATH` is an alias of `THIS_FILE` (one name for the file's own path; cosmetic)
    6. `--state-file`, `--events` and `--spool` default to `<data_dir>/{state.json,events.jsonl,spool.jsonl}`
       where data_dir is `$HYP_OBSERVATORY_DIR` or `<config_dir>/observatory`; the fixture required them.
       `--spool ''` disables spool tailing. `--port` stays required for `serve` and `hook`
  Standard-library carve-out: everything a hook or the receiver runs (`hook`, `serve`, `install-hooks`,
  `uninstall-hooks`, `ratios`, `dump-state`) is stdlib under Python 3.9. `board` and `web` import
  Textual lazily and are meant to run under `uv run` (the inline metadata above); the shipped
  selftest (scripts/selftest-observatory.py) does not cover them.
"""
import argparse
import glob
import json
import math
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_T0 = time.monotonic()      # process start (module import) -> hook.self_ms is measured from here to the POST

H_RE = re.compile(r"H-\d{3}|H-DRAFT-[0-9a-f]{8}")
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd",
               "SubagentStart", "SubagentStop"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
# a tool call is COUNTED (ratios, tool_calls metric, spans) once per completion, success or failure
# (amendment 3 of H-DRAFT-fcf7b3fa-model-leverage: failed calls must not vanish from the denominator)
COUNTED_TOOL_HOOKS = {"PostToolUse", "PostToolUseFailure"}
# work phase a session is in, derived from its last lifecycle hook
PHASE_OF = {"SessionStart": "started", "UserPromptSubmit": "working", "PreToolUse": "working", "PostToolUse": "working",
            "PostToolUseFailure": "working", "SubagentStart": "working", "SubagentStop": "working", "Stop": "waiting", "SessionEnd": "ended"}
SERVICE_NAME = "claude-code-hooks"
EVENT_PREFIX = "claude_code.hook."
LIVE_WINDOW_S = 60.0
ERROR_WORDS = ("error", "blocked", "reject", "denied", "fail")
THIS_FILE = os.path.abspath(__file__)
SELF_PATH = THIS_FILE        # divergence 5: one name for the file's own path (scan_hids masks it)


# --------------------------------------------------------------------------- consumer path resolution (divergences 2, 3, 6)
def _find_hyp_config_dir():
    """hooks/scripts of the plugin tree this file lives in, else of $CLAUDE_PLUGIN_ROOT; None when neither exists."""
    roots = [os.path.dirname(os.path.dirname(THIS_FILE)), os.environ.get("CLAUDE_PLUGIN_ROOT") or ""]
    for root in roots:
        cand = os.path.join(root, "hooks", "scripts")
        if root and os.path.isfile(os.path.join(cand, "hyp_config.py")):
            return cand
    return None


try:
    _HYP_CONFIG_DIR = _find_hyp_config_dir()
    if _HYP_CONFIG_DIR and _HYP_CONFIG_DIR not in sys.path:
        sys.path.insert(0, _HYP_CONFIG_DIR)
    from hyp_config import load_config as _load_hyp_config  # noqa: E402  (plugin tree: DEFAULTS + .claude/hyp.json overlay)
except Exception:  # noqa: BLE001  detached copy: no defaults, only what the consumer's hyp.json names
    def _load_hyp_config(root):
        try:
            with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v.strip().strip("/") for k, v in data.items() if isinstance(v, str) and v.strip()}

_HYP_KEYS = ("hypotheses_dir", "model_dir", "preflight_file")


def _hyp_cfg(top):
    """The three consumer paths this file reads (repo-root-relative, or None when unknown). Never raises.
    Callers sit behind 60 s caches (Catalog, hypothesis_title), so this is one small JSON read per minute per repo."""
    try:
        cfg = _load_hyp_config(top) if top else {}
    except Exception:  # noqa: BLE001
        cfg = {}
    return {k: (cfg.get(k) or None) for k in _HYP_KEYS}


def config_dir():
    """Claude Code's per-user configuration directory: $CLAUDE_CONFIG_DIR when set, else ~/.claude."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def data_dir():
    """Where the receiver keeps its files (spool, events, state): $HYP_OBSERVATORY_DIR, else <config_dir>/observatory."""
    return os.environ.get("HYP_OBSERVATORY_DIR") or os.path.join(config_dir(), "observatory")


DEFAULT_STATE = os.path.join(data_dir(), "state.json")
DEFAULT_EVENTS = os.path.join(data_dir(), "events.jsonl")


# --------------------------------------------------------------------------- small helpers
def now_iso(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, tz=timezone.utc).isoformat()


def parse_iso(s):
    """ISO-8601 -> unix seconds, or None. Python 3.9 fromisoformat needs 'Z' rewritten."""
    if not s or not isinstance(s, str):
        return None
    try:
        s2 = s.strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s2)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def kv(key, value):
    """One OTLP/JSON KeyValue."""
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def attrs_to_dict(lst):
    out = {}
    for item in lst or []:
        if not isinstance(item, dict):
            continue
        v = item.get("value", {})
        out[item.get("key")] = next(iter(v.values()), None) if isinstance(v, dict) and v else v
    return out


def short(s, n=200):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def eprint(*a):
    sys.stderr.write(" ".join(str(x) for x in a) + "\n")
    sys.stderr.flush()


def atomic_write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


# --------------------------------------------------------------------------- hook -> OTLP
def tool_summary(hook):
    """Outcome + <=200-char status summary from tool_response. Never the tool's content."""
    resp = hook.get("tool_response")
    if resp is None:
        return None, None
    outcome, pieces = "ok", {}
    if isinstance(resp, dict):
        for k in ("success", "is_error", "error", "interrupted", "exit_code", "returncode", "status"):
            if k in resp:
                pieces[k] = resp[k] if isinstance(resp[k], (bool, int, float)) else short(str(resp[k]), 80)
        if resp.get("is_error") or resp.get("error") or resp.get("success") is False:
            outcome = "error"
        if resp.get("interrupted"):
            outcome = "interrupted"
    elif isinstance(resp, str):
        pieces["len"] = len(resp)
        if resp.lower().startswith("error"):
            outcome = "error"
    else:
        pieces["type"] = type(resp).__name__
    return outcome, short(json.dumps(pieces, sort_keys=True), 200)


# --------------------------------------------------------------------------- op token (H-DRAFT-fcf7b3fa)
# One program token per tool call, never an argument: Skill -> skill name, Agent -> sub-agent type,
# Bash -> basename of the first program (through VAR=x prefixes, `cd X &&`, sudo/env, and an
# interpreter's script path), anything else -> the tool name. NOTHING else from tool_input is read.
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_WRAPPERS = {"sudo", "env", "nohup", "nice", "time", "exec", "command", "doas", "timeout"}
DURATION_RE = re.compile(r"^\d+(\.\d+)?[smhd]?$")        # `timeout 30 cmd`, `timeout 2m cmd`
INTERPRETERS = {"uv", "python3", "python", "bash", "sh", "zsh", "node"}
UV_SUBCOMMANDS = {"run", "tool", "uvx"}
VALUE_FLAGS = {"--with", "--python", "-p", "--project", "--directory", "--from", "-w", "--with-requirements",
               "-c"}      # `python3 -c "<code>"`: the code string is not a script path (C1 patch A)
PATHLIKE_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
TRAILING_PUNCT = ".,;:)]}'\"`"


def _pathlike(tok):
    return ("/" in tok or bool(PATHLIKE_RE.search(tok))) and "==" not in tok and not tok.startswith("-")


CHAIN_SEPS = ("&&", ";", "||", "|")
MAX_OP_NAMES = 12


def _shell_tokens(command):
    try:
        return shlex.split(command, posix=True)
    except ValueError:                                     # unbalanced quotes: fall back to whitespace
        return [t.strip("'\"`") for t in command.split()]


def op_tokens_bash(command, cwd=None):
    """One program basename per chain segment (`;`, `&&`, `||`, `|`, statement newlines), in order,
    de-duplicated, at most MAX_OP_NAMES — still never an argument (C1 amendment #2 patch C: the first program
    of a chain is often `echo` or `grep` while the catalog script runs second). Every `cd` is resolved
    against `cwd` (H-DRAFT-64674bd0 amendment #1): once the working directory is no longer the cwd's git
    toplevel, a script named by a RELATIVE path is not the catalog's and stops counting; a script named by an
    absolute path keeps counting. Without a cwd, a relative `cd` is treated as leaving (patch D's rule) and an
    absolute one as unknown. [] when nothing names a program."""
    if not command or not isinstance(command, str):
        return []
    toks = _shell_tokens(_chain_text(command))
    segs, cur = [], []
    for t in toks:
        if t in CHAIN_SEPS:
            segs.append(cur); cur = []
        elif t.endswith(";") and len(t) > 1:
            cur.append(t[:-1]); segs.append(cur); cur = []
        else:
            cur.append(t)
    segs.append(cur)
    top = find_toplevel(cwd) if cwd else None
    here = os.path.abspath(cwd) if cwd else None
    off_top = bool(here) and here != top                 # a cwd below the toplevel starts off it
    assigns = {}                                          # NAME -> value from `NAME=value` words in this command
    for seg in segs:
        for t in seg:
            if ASSIGN_RE.match(t):
                k, _, v = t.partition("=")
                assigns[k] = v
    out = []
    for seg in segs:
        target = _cd_target(seg)
        if target is not None:
            target = _expand_var(target, assigns)
            if target.startswith("$") or target == "-":
                off_top = off_top or here is not None     # unresolvable with a known cwd: assume we left (run-3 false match)
            elif here is None:
                off_top = off_top or not target.startswith(("/", "~", ".."))
            else:
                here = os.path.normpath(os.path.join(here, os.path.expanduser(target)))
                off_top = (top is None) or (here != top)
            continue
        op, raw = _first_program_tok(seg)
        raw = _expand_var(raw, assigns)
        if raw.startswith("$"):
            counts = False                                # a path under an unknown variable is nowhere we can vouch for
        elif raw.startswith("/"):
            counts = top is None or raw.startswith(top + "/")   # absolute: only inside the cwd's toplevel
        else:
            counts = not off_top
        if op and counts and op not in out:
            out.append(op)
        if len(out) >= MAX_OP_NAMES:
            break
    return out


VAR_PREFIX_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def _expand_var(tok, assigns):
    """Expand a leading `$NAME` / `${NAME}` from same-command assignments; unchanged when unknown."""
    m = VAR_PREFIX_RE.match(tok or "")
    if not m or m.group(1) not in assigns:
        return tok
    return assigns[m.group(1)] + tok[m.end():]


HEREDOC_RE = re.compile(r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


def _chain_text(command):
    """The command with heredoc bodies removed and statement newlines turned into `;` (patch D,
    H-DRAFT-64674bd0-chain-aware-op-token): prose inside a heredoc never names a program, and a script on
    its own line after the terminator is a chain segment like any other."""
    text = HEREDOC_RE.sub(r"\1", command)
    text = re.sub(r"\\\n", " ", text)
    return text.replace("\n", " ; ")


def _cd_target(seg):
    """The directory a `cd`/`pushd` segment moves to (after VAR=x prefixes), "" for a bare `cd`, None when
    the segment is not a cd."""
    i = 0
    while i < len(seg) and ASSIGN_RE.match(seg[i]):
        i += 1
    if i >= len(seg) or seg[i] not in ("cd", "pushd"):
        return None
    return seg[i + 1] if i + 1 < len(seg) else "~"


def op_token_bash(command):
    """Basename of the first real program in a shell command, or None."""
    if not command or not isinstance(command, str):
        return None
    return _first_program(_shell_tokens(command))


def _first_program(toks):
    return _first_program_tok(toks)[0]


def _first_program_tok(toks):
    """(basename, raw token) of the first real program in one segment, or (None, "")."""
    n, i = len(toks), 0
    while i < n:
        t = toks[i]
        if ASSIGN_RE.match(t) or t in ("&&", ";", "||", "|", "{", "(", "!", "then", "do", "else", "elif", "fi", "done"):
            i += 1                                          # shell keywords never name the program (ROADMAP layer 0.2)
        elif t in ("for", "while", "until", "if"):
            # skip the compound header up to and including its do/then
            while i < n and toks[i].rstrip(";") not in ("do", "then"):
                i += 1
            i += 1
        elif os.path.basename(t) in SHELL_WRAPPERS:        # `time`, `/usr/bin/time -p`, `env`, `nohup`, `timeout N`, ...
            wrapper = os.path.basename(t)
            i += 1
            while i < n and toks[i].startswith("-"):      # sudo -u x, env -i, nice -n 5, time -p, timeout -s KILL
                i += 2 if toks[i] in ("-u", "-g", "-n", "-C", "-h", "-s", "-k") else 1
            if wrapper == "timeout" and i < n and DURATION_RE.match(toks[i]):
                i += 1                                    # the duration is not the program
        elif t == "cd" or t == "pushd":
            while i < n and toks[i] not in ("&&", ";", "||") and not toks[i].endswith((";", "&&")):
                i += 1
            i += 1
        else:
            break
    if i >= n:
        return None, ""
    raw = toks[i].rstrip(TRAILING_PUNCT)
    base = os.path.basename(raw) or None
    if base in INTERPRETERS:
        j = i + 1
        while j < n:
            t = toks[j]
            if t.startswith("-"):
                j += 2 if t in VALUE_FLAGS else 1
            elif base == "uv" and t in UV_SUBCOMMANDS:
                j += 1
            else:
                # `uv run X` runs X whatever it is; python3/bash/node only when X is a script path
                if base == "uv" or _pathlike(t):
                    raw = t.rstrip(TRAILING_PUNCT)
                    base = os.path.basename(raw) or base
                break
    if not base:
        return None, ""
    return (base.rstrip(TRAILING_PUNCT) or None), raw


def derive_op(hook):
    """The op token for a tool hook, or None for non-tool events."""
    tool = hook.get("tool_name")
    if not tool:
        return None
    ti = hook.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    op = None
    if tool == "Skill":
        op = ti.get("skill") or ti.get("skill_name") or ti.get("name")
    elif tool in ("Agent", "Task"):
        op = ti.get("subagent_type")
    elif tool == "Bash":
        op = op_token_bash(ti.get("command"))
    op = str(op).strip().rstrip(TRAILING_PUNCT) if op not in (None, "") else None
    return short(op, 80) if op else str(tool)


def process_cpu_ms():
    """CPU time this process has consumed so far (user+system), INCLUDING interpreter start-up and
    imports — the honest cost of a hook run. None on platforms without resource."""
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return (ru.ru_utime + ru.ru_stime) * 1000.0
    except Exception:
        return None


def hook_to_otlp(hook, now=None, self_ms=None, cpu_ms=None):
    """Wrap one Claude Code hook payload as an OTLP/JSON ExportLogsServiceRequest."""
    now = time.time() if now is None else now
    ev = str(hook.get("hook_event_name") or "Unknown")
    attrs = [kv("event.name", EVENT_PREFIX + ev), kv("event.timestamp", now_iso(now)), kv("hook_event_name", ev)]
    op = derive_op(hook)
    if op:
        attrs.append(kv("op.name", op))
    if hook.get("tool_name") == "Bash":
        ti = hook.get("tool_input")
        names = op_tokens_bash((ti or {}).get("command") if isinstance(ti, dict) else None, cwd=hook.get("cwd"))
        if len(names) > 1:                                   # the chain's other programs, names only (patch C)
            attrs.append(kv("op.names", " ".join(short(x, 40) for x in names)))
    if self_ms is not None:
        attrs.append(kv("hook.self_ms", int(self_ms)))
    if cpu_ms is not None:
        attrs.append(kv("hook.cpu_ms", int(cpu_ms)))
    for key, attr in (("session_id", "session.id"), ("cwd", "cwd"), ("prompt_id", "prompt.id"),
                      ("tool_name", "tool_name"), ("tool_use_id", "tool_use_id"),
                      ("transcript_path", "transcript_path"), ("source", "hook.source"),
                      ("agent_id", "agent.id"), ("agent_type", "agent.type"),
                      ("permission_mode", "permission_mode"), ("reason", "reason"), ("stop_hook_active", "stop_hook_active")):
        if hook.get(key) not in (None, ""):
            attrs.append(kv(attr, hook[key]))
    outcome, summary = tool_summary(hook)
    if ev == "PostToolUseFailure":       # the harness reports the failure; there is no tool_response to summarise
        outcome = "error"
        summary = short(json.dumps({"error": short(str(hook.get("error") or "tool failed"), 120),
                                    "interrupt": bool(hook.get("is_interrupt"))}), 200)
    if outcome:
        attrs.append(kv("tool.outcome", outcome))
        attrs.append(kv("tool.summary", summary))
    body = " ".join(x for x in (ev, hook.get("tool_name"), os.path.basename(str(hook.get("cwd") or ""))) if x)
    return {"resourceLogs": [{
        "resource": {"attributes": [kv("service.name", SERVICE_NAME), kv("host.name", socket.gethostname())]},
        "scopeLogs": [{"scope": {"name": "observatory.hook"}, "logRecords": [{
            "timeUnixNano": str(int(now * 1e9)), "severityText": "INFO",
            "body": {"stringValue": body}, "attributes": attrs}]}]}]}


def post_json(url, obj, timeout=1.5):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def cmd_hook(args):
    """Never blocks Claude: always exit 0, nothing on stdout, all exceptions swallowed."""
    try:
        raw = sys.stdin.read()
        hook = json.loads(raw) if raw.strip() else {}
        if not isinstance(hook, dict):
            hook = {"hook_event_name": "Unknown", "raw_type": type(hook).__name__}
        payload = hook_to_otlp(hook, self_ms=(time.monotonic() - _T0) * 1000.0, cpu_ms=process_cpu_ms())
        post_json("http://127.0.0.1:%d/v1/logs" % args.port, payload, timeout=1.5)
    except Exception as e:  # noqa: BLE001
        try:
            eprint("observatory hook: %s" % e)
        except Exception:
            pass
    return 0


# --------------------------------------------------------------------------- OTLP -> flat records
def _as_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def flatten_logs(payload, arrival):
    for rl in payload.get("resourceLogs", []) or []:
        res = attrs_to_dict((rl.get("resource") or {}).get("attributes"))
        for sl in rl.get("scopeLogs", []) or []:
            for rec in sl.get("logRecords", []) or []:
                a = attrs_to_dict(rec.get("attributes"))
                body = rec.get("body")
                body_s = body.get("stringValue") if isinstance(body, dict) else (str(body) if body else "")
                ev_ts = parse_iso(a.get("event.timestamp"))
                if ev_ts is None:
                    try:
                        ev_ts = int(rec.get("timeUnixNano") or rec.get("observedTimeUnixNano") or 0) / 1e9 or None
                    except (TypeError, ValueError):
                        ev_ts = None
                yield {
                    "arrival_ts_unix": arrival,
                    "source": "hook" if res.get("service.name") == SERVICE_NAME else "otlp",
                    "session_id": a.get("session.id") or res.get("session.id") or "unknown",
                    "event_name": a.get("event.name") or rec.get("eventName") or short(body_s, 60) or "log",
                    "cwd": a.get("cwd"),
                    "hook_event_name": a.get("hook_event_name"),
                    "tool_name": a.get("tool_name"),
                    "tool_outcome": a.get("tool.outcome"),
                    "tool_summary": a.get("tool.summary"),
                    "op_name": a.get("op.name"),
                    "op_names": (str(a.get("op.names")).split() if a.get("op.names") else None),
                    "hook_self_ms": _as_int(a.get("hook.self_ms")),
                    "hook_cpu_ms": _as_int(a.get("hook.cpu_ms")),
                    "prompt_id": a.get("prompt.id"),
                    "tool_use_id": a.get("tool_use_id"),
                    "agent_id": a.get("agent.id"),
                    "agent_type": a.get("agent.type"),
                    "transcript_path": a.get("transcript_path"),
                    "event_ts_unix": ev_ts,
                    "body": short(body_s, 120),
                }


def flatten_metrics(payload, arrival):
    for rm in payload.get("resourceMetrics", []) or []:
        res = attrs_to_dict((rm.get("resource") or {}).get("attributes"))
        for sm in rm.get("scopeMetrics", []) or []:
            for m in sm.get("metrics", []) or []:
                for kind in ("sum", "gauge", "histogram"):
                    for dp in (m.get(kind) or {}).get("dataPoints", []) or []:
                        a = attrs_to_dict(dp.get("attributes"))
                        val = dp.get("asDouble", dp.get("asInt", dp.get("sum")))
                        try:
                            val = float(val) if val is not None else None
                        except (TypeError, ValueError):
                            val = None
                        try:
                            ts = int(dp.get("timeUnixNano") or 0) / 1e9 or None
                        except (TypeError, ValueError):
                            ts = None
                        yield {"arrival_ts_unix": arrival, "source": "otlp", "signal": "metric",
                               "session_id": a.get("session.id") or res.get("session.id") or "unknown",
                               "event_name": "metric:" + str(m.get("name")), "metric_name": m.get("name"),
                               "metric_kind": kind, "metric_unit": m.get("unit"), "metric_value": val,
                               "attributes": {k: v for k, v in a.items() if k != "session.id"},
                               "resource": res, "body": "", "event_ts_unix": ts}


def flatten_traces(payload, arrival):
    for rs in payload.get("resourceSpans", []) or []:
        res = attrs_to_dict((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans", []) or []:
            for sp in ss.get("spans", []) or []:
                a = attrs_to_dict(sp.get("attributes"))

                def ns(k):
                    try:
                        return int(sp.get(k) or 0) / 1e9 or None
                    except (TypeError, ValueError):
                        return None
                yield {"arrival_ts_unix": arrival, "source": "otlp", "signal": "span",
                       "session_id": a.get("session.id") or res.get("session.id") or "unknown",
                       "event_name": "span:" + str(sp.get("name")), "span_name": sp.get("name"),
                       "trace_id": sp.get("traceId"), "span_id": sp.get("spanId"), "parent_span_id": sp.get("parentSpanId") or None,
                       "span_start": ns("startTimeUnixNano"), "span_end": ns("endTimeUnixNano"),
                       "status": (sp.get("status") or {}).get("code"),
                       "attributes": a, "resource": res, "scope": (ss.get("scope") or {}).get("name"),
                       "span_events": [{"name": e.get("name"), "ts": None} for e in (sp.get("events") or [])][:20],
                       "body": "", "event_ts_unix": ns("startTimeUnixNano")}


FLATTENERS = {"/v1/logs": flatten_logs, "/v1/metrics": flatten_metrics, "/v1/traces": flatten_traces}


def decode_protobuf(path, raw):
    """Lazy: only imported when a protobuf body actually arrives."""
    from google.protobuf.json_format import MessageToDict  # noqa: PLC0415
    if path == "/v1/logs":
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest as C
    elif path == "/v1/metrics":
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest as C
    else:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest as C
    msg = C()
    msg.ParseFromString(raw)
    return MessageToDict(msg)


# --------------------------------------------------------------------------- repo / branch / transcript join
class GitInfo:
    def __init__(self):
        self.top = {}      # cwd -> toplevel or None (permanent)
        self.branch = {}   # cwd -> (ts, branch)
        self.lock = threading.Lock()

    @staticmethod
    def _git(cwd, *a):
        try:
            r = subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True, timeout=3)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def repo(self, cwd):
        if not cwd:
            return None
        with self.lock:
            if cwd not in self.top:
                self.top[cwd] = self._git(cwd, "rev-parse", "--show-toplevel") if os.path.isdir(cwd) else None
            top = self.top[cwd]
        if not top:
            return os.path.basename(cwd.rstrip("/")) or cwd
        if "/.claude/worktrees/" in top:
            main = top.split("/.claude/worktrees/")[0]
            return "%s:%s" % (os.path.basename(main), os.path.basename(top))
        return os.path.basename(top)

    def toplevel(self, cwd):
        with self.lock:
            return self.top.get(cwd)

    def branch_of(self, cwd):
        if not cwd:
            return None
        now = time.time()
        with self.lock:
            hit = self.branch.get(cwd)
        if hit and now - hit[0] < 15:
            return hit[1]
        b = self._git(cwd, "rev-parse", "--abbrev-ref", "HEAD") if os.path.isdir(cwd) else None
        with self.lock:
            self.branch[cwd] = (now, b)
        return b


_TITLE_CACHE = {}


def hypothesis_title(toplevel, hid):
    """Title prose after the colon on the spec's H1 (`# H-NNN-slug: title`), or None. Cached 60 s."""
    if not toplevel or not hid:
        return None
    key, now = (toplevel, hid), time.time()
    hit = _TITLE_CACHE.get(key)
    if hit and now - hit[0] < 60:
        return hit[1]
    title = None
    hdir = _hyp_cfg(toplevel)["hypotheses_dir"]
    for path in glob.glob(os.path.join(toplevel, hdir, hid + "-*.md"))[:1] if hdir else []:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                first = fh.readline().strip()
            if first.startswith("#") and ":" in first:
                title = first.split(":", 1)[1].strip() or None
        except OSError:
            pass
    _TITLE_CACHE[key] = (now, title)
    return title


def projects_dir():
    return os.path.join(config_dir(), "projects")


def find_transcript(session_id, hint=None):
    if hint and os.path.isfile(hint):
        return hint
    hits = glob.glob(os.path.join(projects_dir(), "*", session_id + ".jsonl"))
    return hits[0] if hits else None


def tail_lines(path, n=300, chunk=1 << 19):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - chunk))
        data = fh.read()
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-n:]


def scan_hids(lines):
    """Return H-ids ordered most-recent-first: the LAST mention in the text wins as primary.

    This file's own absolute path is masked first: once the hooks are installed, every
    transcript carries the hook command line, and that path contains this lane's H-id
    (found live 2026-09-04: a scratch session got labeled with the observatory's own id)."""
    last_pos = {}
    pos = 0
    for ln in lines:
        if SELF_PATH in ln:
            ln = ln.replace(SELF_PATH, "<observatory>")
        for m in H_RE.finditer(ln):
            last_pos[m.group(0)] = pos + m.start()
        pos += len(ln) + 1
    return [h for h, _ in sorted(last_pos.items(), key=lambda x: -x[1])]


def join_hids(session_id, cwd, transcript_hint=None):
    path = find_transcript(session_id, transcript_hint)
    hids, source = [], "none"
    if path:
        try:
            hids = scan_hids(tail_lines(path))
            source = "transcript" if hids else "none"
        except Exception as e:  # noqa: BLE001
            eprint("transcript scan failed for %s: %s" % (session_id, e))
    if not hids and cwd:
        hids = list(dict.fromkeys(H_RE.findall(cwd)))[::-1]
        source = "cwd" if hids else "none"
    return {"primary": hids[0] if hids else None, "secondary": hids[1:], "source": source}, path


# --------------------------------------------------------------------------- provenance catalog (H-DRAFT-fcf7b3fa)
# Per git toplevel: operating-model node ids (link text of `[kind/name](path.md)` in
# operating-model/*/model.md), skill directory names (skills/ and .claude/skills/), and script /
# hook file names (scripts/, hooks/). Cached 60 s. Classification of each hook record:
#   PostToolUse Skill  op in skills            -> modeled-stochastic     node command/<op> | skill/<op>
#   PostToolUse Bash   op in scripts | hooks   -> modeled-deterministic  node script/<op>
#                      (scripts also lists top-level experiments/*.py; the op and each further program name
#                       of a `;` / `&&` / `||` / `|` chain are tried in order — first catalog hit wins; C1 patches B, C)
#   PostToolUse Agent                          -> delegated              node agent/<subagent_type>
#   SessionStart / UserPromptSubmit / Stop     -> modeled-event          node event/...
#   every other PostToolUse                    -> unmodeled              node None
# PreToolUse gets the same label as its PostToolUse would (so the lane can colour it) but only
# PostToolUse records are COUNTED, once per tool call.
CATALOG_TTL_S = 60.0
NODE_LINK_RE = re.compile(r"\[([a-z][a-z-]*/[A-Za-z0-9_.-]+)\]\([^)]+\.md\)")
LIFECYCLE_NODE = {"SessionStart": "event/session-started", "UserPromptSubmit": "event/prompt-submitted",
                  "Stop": "event/turn-completed"}
CLASSES = ("modeled-deterministic", "modeled-stochastic", "delegated", "unmodeled")
# Vocabulary (ROADMAP-passive-routing §1, 2026-09-06): a "delegated" record is the parent's HANDOFF to a
# sub-agent — not work; the sub-agent's own calls are classified on their own — so handoffs are excluded
# from the leverage ratio and reported as their own share. The class label stays "delegated" in records
# for compatibility; the ratios call it handoff.
MODELED = ("modeled-deterministic", "modeled-stochastic")


def find_toplevel(cwd):
    """Git toplevel by walking up for a `.git` entry (file for worktrees, dir otherwise). No subprocess,
    so it is safe on the ingest path; None when cwd is not inside a repo."""
    if not cwd or not isinstance(cwd, str):
        return None
    p = os.path.abspath(cwd)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def repo_label(top, cwd=None):
    if not top:
        return (os.path.basename(cwd.rstrip("/")) or cwd) if cwd else None
    if "/.claude/worktrees/" in top:
        main = top.split("/.claude/worktrees/")[0]
        return "%s:%s" % (os.path.basename(main), os.path.basename(top))
    return os.path.basename(top)


def _names_under(root, files=True, dirs=False, depth=2):
    out = set()
    if not os.path.isdir(root):
        return out
    for cur, dnames, fnames in os.walk(root):
        rel_depth = cur[len(root):].count(os.sep)
        if rel_depth >= depth:
            dnames[:] = []
        if dirs:
            out.update(d for d in dnames if not d.startswith((".", "_")))
        if files:
            out.update(f for f in fnames if not f.startswith("."))
    return out


def scan_catalog(top):
    cfg = _hyp_cfg(top)
    nodes = set()
    for path in glob.glob(os.path.join(top, cfg["model_dir"], "*", "model.md")) if cfg["model_dir"] else []:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                nodes.update(NODE_LINK_RE.findall(fh.read()))
        except OSError:
            pass
    skills = set()
    for sk in (os.path.join(top, "skills"), os.path.join(top, ".claude", "skills")):
        if os.path.isdir(sk):
            try:
                skills.update(e for e in os.listdir(sk) if not e.startswith(".") and os.path.isdir(os.path.join(sk, e)))
            except OSError:
                pass
    scripts = _names_under(os.path.join(top, "scripts")) | _names_under(os.path.join(top, "hooks"))
    # the preflight script's directory, top-level *.py only, e.g. experiments/preflight.py (C1 patch B)
    exp_rel = os.path.dirname(cfg["preflight_file"]) if cfg["preflight_file"] else None
    exp = os.path.join(top, exp_rel) if exp_rel else None
    try:
        if exp:
            scripts |= {f for f in os.listdir(exp) if f.endswith(".py") and os.path.isfile(os.path.join(exp, f))}
    except OSError:
        pass
    return {"nodes": nodes, "skills": skills, "scripts": scripts, "top": top}


EMPTY_CATALOG = {"nodes": frozenset(), "skills": frozenset(), "scripts": frozenset(), "top": None}


class Catalog:
    def __init__(self, ttl=CATALOG_TTL_S):
        self.ttl = ttl
        self.cache = {}       # toplevel -> (ts, catalog)
        self.tops = {}        # cwd -> toplevel (permanent; a cwd does not change repos)
        self.lock = threading.Lock()

    def toplevel(self, cwd):
        if not cwd:
            return None
        with self.lock:
            if cwd in self.tops:
                return self.tops[cwd]
        top = find_toplevel(cwd)
        with self.lock:
            self.tops[cwd] = top
        return top

    def get(self, top):
        if not top:
            return EMPTY_CATALOG
        now = time.time()
        with self.lock:
            hit = self.cache.get(top)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        cat = scan_catalog(top)
        with self.lock:
            self.cache[top] = (now, cat)
        return cat

    def classify(self, r):
        """(op.class, op.node) for one flat record; (None, None) when the record is not classifiable."""
        hook = r.get("hook_event_name")
        if hook in LIFECYCLE_NODE:
            return "modeled-event", LIFECYCLE_NODE[hook]
        if hook not in TOOL_EVENTS:
            return None, None
        tool, op = r.get("tool_name"), r.get("op_name")
        if tool in ("Agent", "Task"):
            return "delegated", "agent/%s" % (op if op and op not in ("Agent", "Task") else "?")
        cat = self.get(self.toplevel(r.get("cwd")))
        if tool == "Skill" and op and op in cat["skills"]:
            node = "command/%s" % op
            return "modeled-stochastic", node if node in cat["nodes"] else "skill/%s" % op
        if tool == "Bash":
            for name in [op] + list(r.get("op_names") or []):     # first catalog hit along the chain (patch C)
                if name and name in cat["scripts"]:
                    return "modeled-deterministic", "script/%s" % name
        return "unmodeled", None


def ratio_block(counts):
    """Leverage and determinism from per-class PostToolUse counts. Lifecycle events are excluded;
    both ratios are None when their denominator is 0. Pure and deterministic (graders recompute it)."""
    det, sto, dele, unm = (int(counts.get(c, 0) or 0) for c in CLASSES)
    modeled = det + sto                       # handoffs are not work (see MODELED)
    work = modeled + unm
    return {"modeled_deterministic": det, "modeled_stochastic": sto, "delegated": dele, "handoff": dele, "unmodeled": unm,
            "modeled": modeled, "tool_calls": work,
            "leverage": (modeled / float(work)) if work else None,
            "determinism": (det / float(det + sto)) if det + sto else None,
            "handoff_share": (dele / float(work + dele)) if work + dele else None}


def tally_ratios(records):
    """{"ratios": global block, "sessions": {sid: block}} from flat records carrying op_class
    (PostToolUse only). The `ratios` subcommand and the serve snapshot share this function."""
    per, total = {}, {}
    for r in records:
        if r.get("hook_event_name") not in COUNTED_TOOL_HOOKS or r.get("op_class") not in CLASSES:
            continue
        sid = r.get("session_id") or "unknown"
        c = per.setdefault(sid, {})
        c[r["op_class"]] = c.get(r["op_class"], 0) + 1
        total[r["op_class"]] = total.get(r["op_class"], 0) + 1
    return {"ratios": ratio_block(total), "sessions": {sid: ratio_block(c) for sid, c in sorted(per.items())}}


# ROADMAP-passive-routing §3: the discovery list becomes a routing worklist. The hook forwards program
# names only (never arguments), so a suggestion can only be op-level: a catalog script of the same name
# (the op ran from a cwd whose catalog lacks it), a maintainer-extendable table, else capture-candidate.
SUGGESTED_NODE = {
    "Write": "capture-candidate", "Edit": "capture-candidate", "Read": "capture-candidate",
    "python3": "capture-candidate", "python": "capture-candidate",
    "StructuredOutput": "harness", "Agent": "handoff", "Task": "handoff",
}


def suggest_node(op, tool, catalog=None):
    if op in (catalog or EMPTY_CATALOG)["scripts"]:
        return "script/%s" % op
    if op in (catalog or EMPTY_CATALOG)["skills"]:
        return "skill/%s" % op
    return SUGGESTED_NODE.get(op) or SUGGESTED_NODE.get(tool) or "capture-candidate"


def unmodeled_top(counter, n=20, catalog=None):
    """[{op, tool, count, suggested_node}] sorted by count desc, then op, from {(op, tool): count}."""
    rows = [{"op": op, "tool": tool, "count": c, "suggested_node": suggest_node(op, tool, catalog)}
            for (op, tool), c in counter.items()]
    rows.sort(key=lambda x: (-x["count"], str(x["op"]), str(x["tool"])))
    return rows[:n]


def routable_nodes(cat):
    """Every node a tool call can land on in this catalog: operating-model node ids plus script/ and skill/
    ids for programs and skills the model.md does not list (ROADMAP §3 model_coverage denominator)."""
    return set(cat["nodes"]) | {"script/%s" % s for s in cat["scripts"]} | {"skill/%s" % k for k in cat["skills"]} \
        | {"command/%s" % k for k in cat["skills"] if "command/%s" % k in cat["nodes"]}


def coverage_block(seen, routable):
    hit = sorted(seen & routable)
    return {"nodes_seen": len(hit), "nodes_routable": len(routable),
            "coverage": (len(hit) / float(len(routable))) if routable else None,
            "seen": hit[:50], "never_seen_sample": sorted(routable - seen)[:20]}


FAILED_ROUTE_AGE_S = 120.0


# --------------------------------------------------------------------------- the model
def new_session(sid):
    return {"session_id": sid, "cwd": None, "repo": None, "branch": None,
            "hids": {"primary": None, "secondary": [], "source": "none"}, "transcript_path": None,
            "registry": None, "counts": {}, "events_total": 0, "prompts": 0,
            "last_event": None, "last_hook": None, "last_tool": None, "last_tool_outcome": None,
            "first_seen": None, "last_seen": None, "live": False, "error_flag": False, "sources": [],
            "phase": None, "phase_since": None, "started_at": None, "agents": {}, "hid_title": None,
            "op_classes": {}, "ratios": ratio_block({}), "hook_self_ms": {"n": 0, "p50": None, "p95": None},
            "_hid_scan_ts": 0.0, "_self_ms": [], "_unmodeled": {}}


class Model:
    def __init__(self, events_path, registry_interval=5):
        self.events_path = events_path
        self.registry_interval = registry_interval
        self.sessions = {}
        self.lock = threading.Lock()
        self.git = GitInfo()
        self.catalog = Catalog()
        self.records_total = 0
        self.latencies = []
        self.class_totals = {}            # op.class -> PostToolUse count (global)
        self.unmodeled = {}               # (op, tool) -> count (global)
        self.nodes_seen = {}              # toplevel -> {op_node} exercised by counted modeled calls (model_coverage)
        self._top_for_label = {}          # repo label -> toplevel (for unmodeled_top suggestions)
        self.pre_modeled = {}             # tool_use_id -> {sid, node, t}: modeled PreToolUse awaiting its Post (failed_routes)
        self.unmodeled_by_repo = {}       # repo label -> {(op, tool): count}
        self.registry_ok = False
        self.registry_last_poll = None
        self.registry_error = None
        self.started = time.time()

    # ingest ---------------------------------------------------------------
    def classify(self, r):
        """Stamp op_class / op_node on a flat record (before it is written, so the JSONL carries them)."""
        if r.get("hook_event_name") and "op_class" not in r:
            try:
                cls, node = self.catalog.classify(r)
            except Exception as e:  # noqa: BLE001  a broken repo layout must never drop a record
                eprint("observatory serve: classify failed: %s" % e)
                cls, node = None, None
            r["op_class"], r["op_node"] = cls, node
        return r

    def ingest(self, records):
        records = [self.classify(r) for r in records]        # catalog reads happen outside the lock
        with self.lock:
            with open(self.events_path, "a") as fh:
                for r in records:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
                    self._apply(r)

    def _apply(self, r):
        self.records_total += 1
        sid = r.get("session_id") or "unknown"
        s = self.sessions.get(sid) or self.sessions.setdefault(sid, new_session(sid))
        t = r["arrival_ts_unix"]
        if r.get("hook_event_name") in COUNTED_TOOL_HOOKS and r.get("tool_use_id"):
            s.setdefault("_tool_ids", set()).add(r["tool_use_id"])          # backfill dedupe key
        if r.get("hook_event_name") == "PreToolUse" and r.get("op_class") in MODELED and r.get("tool_use_id"):
            self.pre_modeled[r["tool_use_id"]] = {"session_id": sid, "node": r.get("op_node"), "t": t}
        if r.get("hook_event_name") in COUNTED_TOOL_HOOKS and r.get("tool_use_id"):
            self.pre_modeled.pop(r["tool_use_id"], None)
        if r.get("hook_event_name") in COUNTED_TOOL_HOOKS and r.get("op_class") in CLASSES:
            cls = r["op_class"]
            s["op_classes"][cls] = s["op_classes"].get(cls, 0) + 1
            self.class_totals[cls] = self.class_totals.get(cls, 0) + 1
            s["ratios"] = ratio_block(s["op_classes"])
            if cls in MODELED and r.get("op_node"):
                self.nodes_seen.setdefault(self.catalog.toplevel(r.get("cwd")) or "?", set()).add(r["op_node"])
            if cls == "unmodeled":
                key = (r.get("op_name") or r.get("tool_name") or "?", r.get("tool_name") or "?")
                self.unmodeled[key] = self.unmodeled.get(key, 0) + 1
                s["_unmodeled"][key] = s["_unmodeled"].get(key, 0) + 1
        if r.get("hook_self_ms") is not None:
            s["_self_ms"].append(int(r["hook_self_ms"]))
            if len(s["_self_ms"]) > 2000:
                s["_self_ms"] = s["_self_ms"][-2000:]
        s["first_seen"] = s["first_seen"] or t
        s["last_seen"] = max(s["last_seen"] or 0, t)
        if r.get("source") not in s["sources"]:
            s["sources"].append(r.get("source"))
        if r.get("cwd") and r.get("source") == "hook":
            s["cwd"] = r["cwd"]                      # hooks outrank the registry for cwd
        elif r.get("cwd") and not s["cwd"]:
            s["cwd"] = r["cwd"]
        if r.get("transcript_path"):
            s["transcript_path"] = r["transcript_path"]
        name = r.get("event_name") or "?"
        s["counts"][name] = s["counts"].get(name, 0) + 1
        s["events_total"] += 1
        s["last_event"] = name
        hook = r.get("hook_event_name")
        if hook:
            s["last_hook"] = hook
            if hook == "UserPromptSubmit":
                s["prompts"] += 1
            if hook == "SessionStart":
                s["started_at"] = s["started_at"] or r.get("event_ts_unix") or t
            # phase: a sub-agent's events never move the parent session out of "working"
            if hook in PHASE_OF and not r.get("agent_id"):
                if PHASE_OF[hook] != s["phase"]:
                    s["phase_since"] = t
                s["phase"] = PHASE_OF[hook]
            if hook == "SessionEnd" and not r.get("agent_id") and r.get("source") != "backfill":
                s["_backfill_due"] = t + 3.0            # let the async tail land first, then reconcile
        if r.get("agent_id"):
            a = s["agents"].get(r["agent_id"]) or s["agents"].setdefault(
                r["agent_id"], {"type": r.get("agent_type"), "first_seen": t, "last_seen": t, "events": 0,
                                "ended": False, "last_tool": None})
            a["type"] = a["type"] or r.get("agent_type")
            a["last_seen"], a["events"] = t, a["events"] + 1
            if r.get("tool_name"):
                a["last_tool"] = r["tool_name"]
            if hook == "SubagentStop":
                a["ended"] = True
        if r.get("tool_name"):
            s["last_tool"] = r["tool_name"]
            s["last_tool_outcome"] = r.get("tool_outcome")
        blob = " ".join(str(x) for x in (name, r.get("tool_outcome"), r.get("body")) if x).lower()
        s["error_flag"] = any(w in blob for w in ERROR_WORDS)
        if r.get("event_ts_unix"):
            self.latencies.append(max(0.0, t - r["event_ts_unix"]))
            if len(self.latencies) > 5000:
                self.latencies = self.latencies[-5000:]

    # backfill (H-DRAFT-2652d478) -------------------------------------------
    # Async hooks can lose their in-flight tail when a session exits. The transcript is the lossless
    # record of every tool call, so once a session has ended the receiver reads it and synthesises
    # the records that never arrived: one per tool_use with a tool_result and no counted record for
    # that tool_use_id, plus Stop / SessionEnd if absent. Backfilled records carry source=backfill and
    # arrival = the transcript row's own timestamp so they land where the work happened on the lane.
    def schedule_backfill(self, sid, when):
        s = self.sessions.get(sid)
        if s is not None and not s.get("_backfilled"):
            s["_backfill_due"] = when

    def due_backfills(self, now):
        with self.lock:
            return [sid for sid, s in self.sessions.items()
                    if s.get("_backfill_due") and now >= s["_backfill_due"] and not s.get("_backfilled")]

    def backfill_session(self, sid, now=None):
        """Returns the number of records synthesised (0 when nothing was missing or no transcript)."""
        now = time.time() if now is None else now
        with self.lock:
            s = self.sessions.get(sid)
            if s is None or s.get("_backfilled"):
                return 0
            s["_backfilled"] = True
            if "hook" not in (s.get("sources") or []):
                return 0        # never backfill a session this receiver never hooked (registry-only foreign sessions)
            path = s.get("transcript_path")
            seen_ids = set(s.get("_tool_ids") or ())
            have = set(s["counts"].keys())
            cwd = s.get("cwd")
            ended_observed = "claude_code.hook.SessionEnd" in have or bool(s.get("_registry_gone"))
        path = find_transcript(sid, path)
        if not path:
            return 0
        rows = []
        try:
            with open(path, "rb") as fh:
                for ln in fh:
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        continue
        except OSError:
            return 0
        uses, results, first_ts, last_ts, last_assistant_ts, first_user_ts = {}, {}, None, None, None, None
        last_assistant_text_only = False
        for row in rows:
            ts = parse_iso(row.get("timestamp"))
            if ts:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            msg = row.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if row.get("type") == "assistant" and ts:
                last_assistant_ts = ts
                # a turn ends (Stop) only when the assistant's last row is text, not a pending tool_use
                last_assistant_text_only = isinstance(content, str) or (
                    isinstance(content, list) and all(isinstance(i, dict) and i.get("type") != "tool_use" for i in content))
            if row.get("type") == "user" and ts and first_user_ts is None and isinstance(content, str):
                first_user_ts = ts
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("id"):
                    uses[item["id"]] = {"name": item.get("name"), "input": item.get("input") or {}, "ts": ts, "cwd": row.get("cwd") or cwd}
                elif item.get("type") == "tool_result" and item.get("tool_use_id"):
                    results[item["tool_use_id"]] = {"is_error": bool(item.get("is_error")), "ts": ts}
        synth = []

        def payload(hook, ts):
            p = hook_to_otlp(hook, now=ts or now)
            p["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(kv("hook.transport", "backfill"))
            recs = list(flatten_logs(p, ts or now))
            for r in recs:
                r["source"] = "backfill"
            return recs
        for uid, u in uses.items():
            res = results.get(uid)
            if res is None or uid in seen_ids:
                continue
            hook = {"session_id": sid, "hook_event_name": "PostToolUseFailure" if res["is_error"] else "PostToolUse",
                    "tool_name": u["name"], "tool_use_id": uid, "tool_input": u["input"], "cwd": u["cwd"], "transcript_path": path}
            if res["is_error"]:
                hook["error"] = "tool_result is_error (backfilled from transcript)"
            else:
                hook["tool_response"] = {"success": True}
            synth.extend(payload(hook, res["ts"]))
        if "claude_code.hook.SessionStart" not in have and first_ts:
            synth.extend(payload({"session_id": sid, "hook_event_name": "SessionStart", "cwd": cwd, "transcript_path": path, "source": "backfill"}, first_ts))
        if "claude_code.hook.UserPromptSubmit" not in have and (first_user_ts or first_ts):
            synth.extend(payload({"session_id": sid, "hook_event_name": "UserPromptSubmit", "cwd": cwd, "transcript_path": path}, first_user_ts or first_ts))
        if "claude_code.hook.Stop" not in have and last_assistant_ts and last_assistant_text_only:
            synth.extend(payload({"session_id": sid, "hook_event_name": "Stop", "cwd": cwd, "transcript_path": path}, last_assistant_ts))
        if "claude_code.hook.SessionEnd" not in have and last_ts and ended_observed:
            synth.extend(payload({"session_id": sid, "hook_event_name": "SessionEnd", "cwd": cwd, "transcript_path": path, "reason": "backfill"}, last_ts))
        if synth:
            synth.sort(key=lambda r: r.get("arrival_ts_unix") or 0)
            self.ingest(synth)
            with self.lock:
                s = self.sessions.get(sid)
                if s is not None:
                    s["backfilled_records"] = len(synth)
        return len(synth)

    # registry -------------------------------------------------------------
    def poll_registry(self):
        exe = shutil.which("claude")
        if not exe:
            self.registry_ok, self.registry_error = False, "claude not on PATH"
            return
        try:
            r = subprocess.run([exe, "agents", "--json"], capture_output=True, text=True, timeout=8)
            rows = json.loads(r.stdout[r.stdout.find("["):]) if r.returncode == 0 and "[" in r.stdout else None
        except Exception as e:  # noqa: BLE001
            rows, r = None, None
            self.registry_error = str(e)
        now = time.time()
        with self.lock:
            self.registry_last_poll = now
            if not isinstance(rows, list):
                self.registry_ok = False
                self.registry_error = self.registry_error or ("rc=%s" % (r.returncode if r else "?"))
                return
            self.registry_ok, self.registry_error = True, None
            listed = {row.get("sessionId") for row in rows if row.get("sessionId")}
            for sid, s in self.sessions.items():
                reg = s.get("registry")
                if not reg:
                    continue
                if sid in listed:
                    s["_missing_polls"] = 0
                    continue
                # the registry listing flaps (a live background session was seen missing for one poll and
                # its whole transcript got backfilled); require two consecutive misses before treating
                # the session as gone
                s["_missing_polls"] = s.get("_missing_polls", 0) + 1
                if s["_missing_polls"] >= 2 and not s.get("_backfilled") and not s.get("_backfill_due"):
                    s["_backfill_due"] = now + 3.0       # the session left the registry: reconcile its transcript
                    s["_registry_gone"] = True
            for row in rows:
                sid = row.get("sessionId")
                if not sid:
                    continue
                s = self.sessions.get(sid) or self.sessions.setdefault(sid, new_session(sid))
                s["registry"] = {"state": row.get("state"), "status": row.get("status"), "name": row.get("name"),
                                 "kind": row.get("kind"), "pid": row.get("pid"), "seen": now,
                                 "started_at": (row.get("startedAt") or 0) / 1000.0 or None}
                if row.get("cwd") and not s["cwd"]:
                    s["cwd"] = row["cwd"]
                if "registry" not in s["sources"]:
                    s["sources"].append("registry")
                s["first_seen"] = s["first_seen"] or now

    def registry_loop(self, stop):
        while not stop.is_set():
            self.poll_registry()
            stop.wait(max(5, self.registry_interval))   # never faster than every 5 s

    # enrichment + snapshot ------------------------------------------------
    @staticmethod
    def is_live(s, now):
        reg = s.get("registry") or {}
        reg_live = bool(reg) and now - (reg.get("seen") or 0) < LIVE_WINDOW_S and reg.get("state") not in ("done", "ended", "exited")
        return reg_live or (s["last_seen"] is not None and now - s["last_seen"] < LIVE_WINDOW_S)

    def enrich(self):
        """Slow work (git subprocesses, transcript glob+tail). Runs in its own thread; git can take
        0.1-1.5 s per call on a loaded machine, so the state writer must never wait on this."""
        with self.lock:
            items = list(self.sessions.values())
        for s in items:
            now = time.time()
            cwd = s["cwd"]
            repo, branch = self.git.repo(cwd), self.git.branch_of(cwd)
            hids, tpath = s["hids"], s["transcript_path"]
            if (self.is_live(s, now) or hids["primary"] is None) and now - s["_hid_scan_ts"] >= 5:
                hids, tpath = join_hids(s["session_id"], cwd, s["transcript_path"])
                s["_hid_scan_ts"] = now
            title = hypothesis_title(self.git.toplevel(cwd), hids.get("primary"))
            with self.lock:
                s.update({"repo": repo, "branch": branch, "hids": hids, "hid_title": title,
                          "transcript_path": tpath or s["transcript_path"]})

    def enrich_loop(self, stop):
        while not stop.is_set():
            try:
                self.enrich()
                for sid in self.due_backfills(time.time()):
                    n = self.backfill_session(sid)
                    if n:
                        eprint("observatory serve: backfilled %d record(s) for %s from its transcript" % (n, sid[:8]))
            except Exception as e:  # noqa: BLE001
                eprint("observatory serve: enrich failed: %s" % e)
            stop.wait(1.0)

    def snapshot(self):
        now = time.time()
        with self.lock:
            lat = sorted(self.latencies)
            sessions, self_ms, by_repo = {}, [], {}
            for sid, s in self.sessions.items():
                s["live"] = self.is_live(s, now)
                if s["_self_ms"]:
                    sm = sorted(s["_self_ms"])
                    s["hook_self_ms"] = {"n": len(sm), "p50": percentile(sm, 0.5), "p95": percentile(sm, 0.95)}
                    self_ms.extend(sm)
                if s["_unmodeled"]:
                    top = self.catalog.toplevel(s.get("cwd"))
                    repo = s.get("repo") or repo_label(top, s.get("cwd")) or "?"
                    self._top_for_label[repo] = top
                    agg = by_repo.setdefault(repo, {})
                    for key, c in s["_unmodeled"].items():
                        agg[key] = agg.get(key, 0) + c
                sessions[sid] = {k: v for k, v in s.items() if not k.startswith("_")}
            self_ms.sort()
            # model_coverage: nodes exercised / nodes routable, per toplevel and overall (ROADMAP §3)
            cov_by_repo, seen_all, routable_all = {}, set(), set()
            for top, seen in sorted(self.nodes_seen.items()):
                cat = self.catalog.get(top) if top != "?" else EMPTY_CATALOG
                routable = routable_nodes(cat)
                cov_by_repo[repo_label(top) if top != "?" else "?"] = coverage_block(seen, routable)
                seen_all |= seen
                routable_all |= routable
            failed = [dict(v, tool_use_id=k, age_s=round(now - v["t"], 1)) for k, v in self.pre_modeled.items()
                      if now - v["t"] >= FAILED_ROUTE_AGE_S]
            failed.sort(key=lambda x: -x["age_s"])
            return {
                "generated_at": now_iso(), "generated_at_unix": time.time(), "serve_started": now_iso(self.started),
                "sessions": sessions, "sessions_total": len(sessions),
                "sessions_live": sum(1 for s in sessions.values() if s["live"]),
                "records_total": self.records_total,
                "ratios": ratio_block(self.class_totals),
                "model_coverage": {"all": coverage_block(seen_all, routable_all), "by_repo": cov_by_repo},
                "failed_routes": {"count": len(failed), "rows": [{k: v for k, v in f.items() if k != "t"} for f in failed[:20]]},
                "unmodeled_top": unmodeled_top(self.unmodeled),
                "unmodeled_by_repo": {repo: unmodeled_top(agg, catalog=self.catalog.get(self._top_for_label.get(repo)))
                                      for repo, agg in sorted(by_repo.items())},
                "hook_self_ms": {"n": len(self_ms), "p50": percentile(self_ms, 0.5), "p95": percentile(self_ms, 0.95)},
                "latency": {"n": len(lat), "p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95), "max": lat[-1] if lat else None},
                "otel_modules_loaded": any(m.startswith("opentelemetry") for m in sys.modules),
                "registry_ok": self.registry_ok, "registry_last_poll": self.registry_last_poll, "registry_error": self.registry_error,
            }


# --------------------------------------------------------------------------- the receiver
def make_handler(model):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # keep stderr quiet; the TUI may share it
            pass

        def _reply(self, code, body=b"{}"):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") == "/state":
                return self._reply(200, json.dumps(model.snapshot()).encode())
            self._reply(200, b'{"ok":true}')

        def do_POST(self):
            arrival = time.time()
            path = self.path.split("?")[0].rstrip("/")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                ctype = (self.headers.get("Content-Type") or "").lower()
                if path == "/hook":
                    hook = json.loads(raw.decode("utf-8", "replace") or "{}")
                    if not isinstance(hook, dict):
                        raise ValueError("hook body must be a JSON object")
                    payload, flat = hook_to_otlp(hook, arrival), flatten_logs
                elif path in FLATTENERS:
                    flat = FLATTENERS[path]
                    if "protobuf" in ctype:
                        payload = decode_protobuf(path, raw)
                    else:
                        payload = json.loads(raw.decode("utf-8", "replace") or "{}")
                    if not isinstance(payload, dict):
                        raise ValueError("OTLP body must be a JSON object")
                else:
                    return self._reply(404, b'{"error":"unknown path"}')
                recs = list(flat(payload, arrival))
                for r in recs:
                    r["path"] = path
                model.ingest(recs)
                self._reply(200)
            except Exception as e:  # noqa: BLE001  never crash on a bad body
                eprint("observatory serve: bad %s body: %s" % (path, short(str(e), 200)))
                try:
                    self._reply(400, json.dumps({"error": short(str(e), 200)}).encode())
                except Exception:
                    pass
    return Handler


def start_receiver(model, host, port):
    srv = ThreadingHTTPServer((host, port), make_handler(model))
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="observatory-receiver")
    t.start()
    return srv


def spool_records(path, offset):
    """Read complete spool records after `offset`: (records, new_offset). A record is the bytes up to
    the next SPOOL_SEP; the trailing fragment after the last separator is left for the next call."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:               # truncated or rotated: start over
        offset = 0
    if size == offset:
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    parts = data.split(SPOOL_SEP)
    complete, rest = parts[:-1], parts[-1]
    out = []
    for raw in complete:
        raw = raw.strip()
        if not raw:
            continue
        try:
            hook = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if isinstance(hook, dict):
            out.append(hook)
    return out, offset + len(data) - len(rest)


def spool_loop(model, path, stop, interval=0.5):
    """Tail the spool the /bin/sh hooks append to and ingest each payload exactly as the Python
    hook would have posted it (same wrapper, same flattening, same classification), plus
    hook.transport=spool. The offset persists next to the spool so a restart neither replays nor skips."""
    off_path = path + ".offset"
    try:
        offset = int(open(off_path).read().strip() or 0)
    except Exception:
        offset = 0
    while not stop.is_set():
        try:
            offset2 = spool_ingest_once(model, path, offset)
            if offset2 != offset:
                offset = offset2
                tmp = off_path + ".tmp"
                with open(tmp, "w") as fh:
                    fh.write(str(offset))
                os.replace(tmp, off_path)
        except Exception as e:  # noqa: BLE001
            eprint("observatory serve: spool tail failed: %s" % e)
        stop.wait(interval)


def spool_ingest_once(model, path, offset, now=None):
    """One tail step: read complete records after offset, wrap + flatten + ingest them, return the new offset."""
    hooks, offset2 = spool_records(path, offset)
    if hooks:
        now = time.time() if now is None else now
        recs = []
        for hook in hooks:
            payload = hook_to_otlp(hook, now=now)
            payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(kv("hook.transport", "spool"))
            recs.extend(flatten_logs(payload, now))
        model.ingest(recs)
    return offset2


def cmd_serve(args):
    if args.port == 4318:
        eprint("refusing to bind 4318 (spec isolation rule)")
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(args.events)), exist_ok=True)
    model = Model(args.events, registry_interval=args.registry_interval)
    srv = start_receiver(model, args.host, args.port)
    stop = threading.Event()
    if args.registry_interval > 0:
        threading.Thread(target=model.registry_loop, args=(stop,), daemon=True, name="registry").start()
    threading.Thread(target=model.enrich_loop, args=(stop,), daemon=True, name="enrich").start()
    spool = getattr(args, "spool", None)
    if spool:
        os.makedirs(os.path.dirname(os.path.abspath(spool)), exist_ok=True)
        threading.Thread(target=spool_loop, args=(model, os.path.abspath(spool), stop), daemon=True, name="spool").start()
        eprint("observatory serve tailing spool %s" % os.path.abspath(spool))
    eprint("observatory serve on http://%s:%d  (POST /v1/logs|/v1/metrics|/v1/traces|/hook, GET /state)" % (args.host, args.port))
    eprint("state -> %s   events -> %s" % (args.state_file, args.events))
    try:
        while True:
            t0 = time.time()
            try:
                atomic_write_json(args.state_file, model.snapshot())
            except Exception as e:  # noqa: BLE001
                eprint("observatory serve: tick failed: %s" % e)
            time.sleep(max(0.1, 1.0 - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()
    return 0


def cmd_dump_state(args):
    with open(args.state_file) as fh:
        sys.stdout.write(json.dumps(json.load(fh), indent=2, sort_keys=True) + "\n")
    return 0


def read_events(path):
    with open(path, "rb") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def cmd_ratios(args):
    """Recompute the leverage / determinism blocks from the events JSONL alone (no state file, no
    clock): the same `tally_ratios` the receiver uses, printed with sorted keys, so two runs over the
    same file are byte-identical (H-DRAFT-fcf7b3fa-model-leverage-read-model A2)."""
    out = tally_ratios(read_events(args.events))
    if getattr(args, "session", None):
        out = {"ratios": out["sessions"].get(args.session, ratio_block({})), "sessions": {args.session: out["sessions"].get(args.session, ratio_block({}))}}
    sys.stdout.write(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return 0


# --------------------------------------------------------------------------- hook install
SPOOL_SEP = b"\x1e"          # ASCII record separator between hook payloads in the spool file
DEFAULT_SPOOL = os.path.join(data_dir(), "spool.jsonl")


def hook_command(port=None, transport="spool", spool=None):
    """The installed hook command.
    spool (default): a synchronous /bin/sh append — no interpreter to start, completes before the
    harness moves on, so a session's exit can never drop it (H-DRAFT-2dabe424-spool-hook-budget).
    post: the Python entrypoint that wraps and POSTs to serve (kept behind --transport post)."""
    if transport == "post":
        return "python3 %s hook --port %d" % (THIS_FILE, int(port))
    path = os.path.abspath(spool or DEFAULT_SPOOL)
    return "/bin/sh -c 'cat >> \"%s\"; printf \"\\036\\n\" >> \"%s\"'" % (path, path)


def _is_ours(entry):
    cmd = entry.get("command", "") if isinstance(entry, dict) else ""
    return "observatory.py hook --port" in cmd or ('printf "\\036\\n" >>' in cmd and "cat >>" in cmd)


def settings_path_for(scope, override=None):
    if override:
        return os.path.abspath(override)
    if scope == "user":
        return os.path.join(config_dir(), "settings.json")
    return os.path.join(os.getcwd(), ".claude", "settings.json")


def edit_hooks(settings, port=None, remove=False, transport="spool", spool=None, sync=False):
    """Pure: returns (new_settings, changes). Other keys untouched.
    transport spool + sync=False (default): async /bin/sh append, +ms per call, tail backfilled from the
    transcript by serve (H-DRAFT-2652d478); sync=True: lossless by itself but ~100 ms per firing on macOS
    (H-DRAFT-2dabe424 run 2); transport post: async python hook."""
    changes = []
    cmd = None if remove else hook_command(port, transport, spool)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    hooks = dict(hooks)
    for ev in HOOK_EVENTS:
        groups = list(hooks.get(ev) or [])
        kept, ours = [], []
        for g in groups:
            inner = [h for h in (g.get("hooks") or [])] if isinstance(g, dict) else []
            if inner and all(_is_ours(h) for h in inner):
                ours.append(g)
                continue
            if inner and any(_is_ours(h) for h in inner):
                g = dict(g)
                g["hooks"] = [h for h in inner if not _is_ours(h)]
                changes.append("%s: removed observatory hook from a shared group" % ev)
            kept.append(g)
        if remove:
            if ours:
                changes.append("%s: removed observatory hook group" % ev)
        else:
            # spool: synchronous, so the timeout is the only way a record can be lost — under pathological
            # host load (1-min load > cores) a /bin/sh append was observed to exceed 5 s and be killed
            # (H-DRAFT-2dabe424 run 1: 19/20). 30 s is a ceiling, not a cost; the append normally takes ms.
            entry = {"type": "command", "command": cmd, "timeout": 30 if transport == "spool" else 5}
            if transport == "post" or not sync:
                # async: Claude Code runs the hook in the background and never waits for it (H-291 on
                # main). Cheap on the hot path; a headless session's exit can drop in-flight hooks
                # (H-DRAFT-fcf7b3fa-hook-overhead run 4), which serve's end-of-session transcript
                # backfill repairs (H-DRAFT-2652d478). --sync opts into the lossless-but-slow form.
                entry["async"] = True
            grp = {"hooks": [entry]}
            if ev in TOOL_EVENTS:
                grp["matcher"] = "*"
            kept.append(grp)
            if len(ours) == 1 and ours[0] == grp:
                changes.append("%s: unchanged" % ev)              # idempotent re-run: same command, same target
            elif ours:
                changes.append("%s: replaced observatory hook (%s)" % (ev, cmd))
            else:
                changes.append("%s: added observatory hook (%s)" % (ev, cmd))
        if kept:
            hooks[ev] = kept
        elif ev in hooks:
            del hooks[ev]
    out = dict(settings)
    if hooks:
        out["hooks"] = hooks
    elif "hooks" in out:
        del out["hooks"]
    return out, changes


def cmd_install_hooks(args, remove=False):
    path = settings_path_for(args.scope, args.settings)
    settings = {}
    if os.path.exists(path):
        with open(path) as fh:
            settings = json.load(fh)
    transport = getattr(args, "transport", "spool")
    if not remove and transport == "post" and getattr(args, "port", None) is None:
        eprint("install-hooks: --transport post needs --port")
        return 2
    new, changes = edit_hooks(settings, port=getattr(args, "port", None), remove=remove,
                              transport=transport, spool=getattr(args, "spool", None), sync=bool(getattr(args, "sync", False)))
    if new != settings:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(new, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    print("%s %s" % ("uninstall-hooks" if remove else "install-hooks", path))
    for c in changes or ["no change"]:
        print("  - " + c)
    return 0


# --------------------------------------------------------------------------- timeline (pure, testable)
# The K treatment (gallery.py render_K, chosen 2026-09-06): a dotted baseline `⡀` in the session-state
# hue (dimmed), solid bars `▂..▆` sized by events per cell relative to the lane's busiest cell and
# coloured by the DOMINANT op.class in the cell, full-height `▆` spikes for prompt (white), stop
# (yellow) and error (red) — never ▇/█ so rows keep a gap — and `▏ ▕` caps where the session or
# sub-agent starts and ends. Cells outside the lane's span are blank.
TL_BARS = "▁▂▃▄▅▆▇█"
TL_WINDOWS = [120, 300, 900, 3600]
STATE_STYLE = {"working": "bold", "waiting": "yellow", "blocked": "bold yellow", "needs input": "bold yellow",
               "done": "green", "ended": "green", "idle": "dim", "started": "bold"}
STATE_HUE = {  # colour = STATE (agent-view convention); the K baseline is this hue dimmed
    "needs input": "#f2c14e", "blocked": "#f2c14e", "working": "#7dd3fc", "started": "#7dd3fc",
    "waiting": "#a3a3a3", "idle": "#6b7280", "done": "#4ade80", "ended": "#4ade80", "exited": "#4ade80",
    "error": "#f87171", "live": "#7dd3fc",
}
CLASS_COLOR = {"modeled-deterministic": "#4ade80", "modeled-stochastic": "#7dd3fc", "delegated": "#c084fc",
               "unmodeled": "#9ca3af"}
SPIKE_COLOR = {"prompt": "#f8fafc", "stop": "#f2c14e", "error": "#f87171"}
SOLID_LEVELS = " ▁▂▃▄▅▆"     # 1 + level 1..5 -> ▂..▆ ; ▇/█ never used for work so rows keep a gap
HL_GLYPH = "█"               # only a cross-highlighted cell is full height (amendment #2, H-DRAFT-4ac9ae01)
DRAIN_STYLE = CLASS_COLOR["unmodeled"] + " dim"   # every other cell of a marked strip (H-DRAFT-64674bd0-cross-highlight-legibility)
IDLE_DOT = "⡀"
CAP_L, CAP_R = "▏", "▕"
LIFECYCLE_KINDS = {"SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop"}


def blend(hex_color, factor):
    """Scale a #rrggbb toward black (factor < 1 dims)."""
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def event_kind(r):
    if r.get("tool_outcome") in ("error", "interrupted"):
        return "error"
    hook = r.get("hook_event_name")
    return hook if hook in LIFECYCLE_KINDS else "tool"


def dominant_class(cls_counts):
    """The op.class with the most events in a cell; ties go to the more-modeled class."""
    if not cls_counts:
        return None
    return sorted(cls_counts.items(), key=lambda kv: (-kv[1], CLASSES.index(kv[0]) if kv[0] in CLASSES else 9))[0][0]


def span_columns(start, end, now, window_s, width):
    """(c0, c1): first and last lane column a session/agent occupies; c1 is None while it is still
    running (the lane reaches the right edge). Both None when it has not started in the window yet."""
    win_start, per = now - window_s, window_s / float(max(1, width))
    if start is None or start <= win_start:
        c0 = 0
    else:
        c0 = min(width - 1, int((start - win_start) / per))
    c1 = None
    if end is not None:
        c1 = max(c0, min(width - 1, int((end - win_start) / per))) if end > win_start else c0
    return c0, c1


def session_label(s):
    reg = s.get("registry") or {}
    name = reg.get("name") or ""
    repo = s.get("repo") or os.path.basename(s.get("cwd") or "") or "?"
    return name, repo, s.get("branch") or "-"


def session_state(s):
    reg = s.get("registry") or {}
    st = reg.get("state") or s.get("phase") or ("live" if s.get("live") else "idle")
    if st == "blocked" and reg.get("status") == "waiting":
        st = "needs input"
    return st


def agent_state(a, s, now):
    """A sub-agent is ended on its own SubagentStop, or when its parent session is done/ended, or
    when the parent is no longer live; otherwise idle once unseen for the live window, else working.
    (SubagentStop never arrives for agents started before the Subagent hooks were installed, and
    Workflow-tool agents may not emit it; deriving from the parent keeps the label honest.)"""
    if a.get("ended"):
        return "ended"
    parent = session_state(s)
    if parent in ("done", "ended", "exited") or not s.get("live"):
        return "ended"
    if now - (a.get("last_seen") or 0) > LIVE_WINDOW_S:
        return "idle"
    return "working"


def lane_buckets(events, now, window_s, width):
    """(sid, agent_id) -> {col: {"n": events, "kinds": {kind: n}, "cls": {op.class: n}}}."""
    start, per = now - window_s, window_s / float(width)
    buckets = {}
    for r in events:
        t = r.get("arrival_ts_unix") or 0
        if t < start or not r.get("session_id"):
            continue
        col = min(width - 1, int((t - start) / per))
        b = buckets.setdefault((r["session_id"], r.get("agent_id")), {}).setdefault(col, {"n": 0, "kinds": {}, "cls": {}})
        b["n"] += 1
        k = event_kind(r)
        b["kinds"][k] = b["kinds"].get(k, 0) + 1
        if r.get("hook_event_name") in TOOL_EVENTS:
            c = r.get("op_class") if r.get("op_class") in CLASSES else "unmodeled"   # pre-classification records: grey
            b["cls"][c] = b["cls"].get(c, 0) + 1
    return buckets


def lane_cells(buckets, lane, width, state=None, span=(0, None)):
    """K lane for one (sid, agent_id): [(glyph, style)] * width. `span` = (c0, c1) from span_columns;
    cells before c0 / after c1 are blank, c0 > 0 draws ▏, an ended lane draws ▕ at c1."""
    hue = STATE_HUE.get(state or "", STATE_HUE["working"])
    base = blend(hue, 0.55)
    cells = buckets.get(lane, {})
    c0, c1 = span if span else (0, None)
    c0 = c0 or 0
    mx = max([b["n"] for b in cells.values()] or [1]) or 1
    out = []
    for col in range(width):
        if col < c0 or (c1 is not None and col > c1):
            out.append((" ", base))
            continue
        b = cells.get(col)
        kinds = b["kinds"] if b else {}
        cls_here = dominant_class(b["cls"]) if b and b["cls"] else None
        if "error" in kinds:
            out.append((SOLID_LEVELS[6], SPIKE_COLOR["error"]))
        elif "UserPromptSubmit" in kinds or "Stop" in kinds:
            # amendment 4 (leverage lane): a cell that carries both a lifecycle event and tool work keeps
            # the full ▆ height but takes the WORK's class colour, so a short session's provenance is
            # never hidden behind its own prompt/stop marks; white/yellow only when the cell has no work
            if cls_here:
                out.append((SOLID_LEVELS[6], CLASS_COLOR.get(cls_here, base)))
            else:
                out.append((SOLID_LEVELS[6], SPIKE_COLOR["prompt" if "UserPromptSubmit" in kinds else "stop"]))
        elif "SessionEnd" in kinds or "SubagentStop" in kinds or (c1 is not None and col == c1):
            out.append((CAP_R, hue))
        elif "SessionStart" in kinds or "SubagentStart" in kinds or (col == c0 and c0 > 0):
            out.append((CAP_L, hue))
        elif b and b["n"]:
            level = min(5, max(1, int(math.ceil(b["n"] / float(mx) * 5))))
            cls = dominant_class(b["cls"])
            out.append((SOLID_LEVELS[1 + level], CLASS_COLOR.get(cls, base) if cls else base))
        else:
            out.append((IDLE_DOT, base))
    return out


def fmt_ratio(v):
    return "-" if v is None else "%.2f" % v


def ratio_text(ratios):
    r = ratios or {}
    return "%4s %4s" % (fmt_ratio(r.get("leverage")), fmt_ratio(r.get("determinism")))


def coalesce(cells):
    """Merge adjacent same-style cells into (text, style) runs: a 120-cell lane becomes a handful
    of appends instead of 120 Rich style objects (the render cost was 274k style constructions per
    frame at 1,240 rows)."""
    runs = []
    for ch, style in cells:
        if runs and runs[-1][1] == style:
            runs[-1][0].append(ch)
        else:
            runs.append(([ch], style))
    return [("".join(chars), style) for chars, style in runs]


def timeline_axis(now, window_s, width):
    """Tick labels for the strip: start, middle, now. HH:MM at the 1 h window or when three
    HH:MM:SS labels would overlap; only start + now below ~20 cells."""
    width = max(10, width)
    fmt = "%H:%M" if (window_s >= 3600 or width < 30) else "%H:%M:%S"
    lab = lambda t: time.strftime(fmt, time.localtime(t))
    s, m, e = lab(now - window_s), lab(now - window_s / 2), lab(now)
    line = list(" " * width)
    ticks = [(0, s), (max(0, width - len(e)), e)]
    if width >= 3 * len(m) + 4:
        ticks.insert(1, (max(0, width // 2 - len(m) // 2), m))
    for pos, txt in ticks:
        for i, ch in enumerate(txt):
            if pos + i < width:
                line[pos + i] = ch
    return "".join(line)


RECENT_S = 3600.0        # default view: live sessions plus anything seen in the last hour
MAX_AGENT_ROWS = 8       # per session; the rest are counted, not listed


def session_recent(s, now, horizon=RECENT_S):
    reg = s.get("registry") or {}
    seen = max(s.get("last_seen") or 0, reg.get("seen") or 0)
    return bool(s.get("live")) or (now - seen) < horizon


# Attention rank: rows that need the maintainer rise; a state change is exactly when a row should move.
# Within a rank the order is alphabetical, so the board is still stable between refreshes.
STATE_RANK = {"needs input": 0, "blocked": 1, "error": 2, "working": 3, "started": 3, "waiting": 4, "live": 5,
              "idle": 6, "done": 7, "ended": 7, "exited": 7}


def session_rank(s):
    st = session_state(s)
    if s.get("error_flag") and st not in ("done", "ended", "exited"):
        return STATE_RANK["error"]
    return STATE_RANK.get(st, 5)


def board_rows(sessions, events, now, window_s, width, collapsed=frozenset(), live_only=False, recent_only=True,
               hyp_inline=False):
    """The single view: repo -> session -> hypothesis -> sub-agents.
    Repos alphabetical; within a repo sessions rank by attention (needs input, blocked, error, working,
    waiting, idle, done) then name. Returns dicts {key, depth, kind, label, sub, hyp, state, cells,
    right, live}. Pure: no Textual. recent_only hides sessions neither live nor seen within RECENT_S
    (dead scratch sessions otherwise pile up into hundreds of rows and the render dominates the event
    loop). hyp_inline=True folds the hypothesis into the session row (field "hyp") instead of a row."""
    width = max(10, width)
    buckets = lane_buckets(events, now, window_s, width)
    groups = {}
    for s in sessions.values():
        if live_only and not s.get("live"):
            continue
        if recent_only and not session_recent(s, now):
            continue
        name, repo, branch = session_label(s)
        main = repo.split(":")[0]
        groups.setdefault(main, []).append(s)
    rows = []
    for main in sorted(groups, key=str.lower):
        rk = "repo:" + main
        live_n = sum(1 for s in groups[main] if s.get("live"))
        attn = sum(1 for s in groups[main] if session_rank(s) <= STATE_RANK["error"])
        rows.append({"key": rk, "depth": 0, "kind": "repo", "label": main, "sub": "", "hyp": "", "state": "",
                     "cells": None, "right": "%d live / %d" % (live_n, len(groups[main])), "live": live_n > 0,
                     "attention": attn})
        if rk in collapsed:
            continue
        for s in sorted(groups[main], key=lambda s: (session_rank(s), (session_label(s)[0] or session_label(s)[1]).lower())):
            sid = s["session_id"]
            name, repo, branch = session_label(s)
            sk = "sess:" + sid
            reg = s.get("registry") or {}
            wt = repo.split(":", 1)[1] if ":" in repo else ""
            sub = " ".join(x for x in (wt, ("(%s)" % branch) if branch not in ("-", None) and branch != wt else "") if x)
            age = now - (reg.get("started_at") or s.get("started_at") or s.get("first_seen") or now)
            hid = (s.get("hids") or {}).get("primary")
            title = s.get("hid_title")
            hyp = (hid + ("  " + title if title else "")) if hid else ""
            st = session_state(s)
            s_start = reg.get("started_at") or s.get("started_at") or s.get("first_seen")
            s_end = s.get("last_seen") if st in ("done", "ended", "exited") else None
            rows.append({"key": sk, "depth": 1, "kind": "session", "label": name or repo, "sub": sub, "hyp": hyp if hyp_inline else "",
                         "state": st, "cells": lane_cells(buckets, (sid, None), width, st, span_columns(s_start, s_end, now, window_s, width)),
                         "ratios": ratio_text(s.get("ratios")),
                         "right": fmt_age(age), "live": bool(s.get("live"))})
            if sk in collapsed:
                continue
            if not hyp_inline:
                rows.append({"key": sk + ":hyp", "depth": 2, "kind": "hypothesis",
                             "label": hid or "no hypothesis", "sub": title or "", "hyp": "", "state": "",
                             "cells": None, "right": "", "live": bool(s.get("live"))})
            agents = s.get("agents") or {}
            # working agents first (most recent first), then idle, then ended; alphabetical within a state
            # so the rows the maintainer needs are never behind the "… N more" fold
            ranked = sorted(((agent_state(a, s, now), aid, a) for aid, a in agents.items()),
                            key=lambda t: ({"working": 0, "idle": 1, "ended": 2}.get(t[0], 3),
                                           (t[2].get("type") or "agent").lower(), str(t[1])))
            shown, hidden = 0, {"idle": 0, "ended": 0}
            for astate, aid, a in ranked:
                if astate == "ended" and (a.get("last_seen") or 0) < now - window_s:
                    continue
                if shown >= MAX_AGENT_ROWS and astate != "working":
                    hidden[astate] = hidden.get(astate, 0) + 1
                    continue
                shown += 1
                rows.append({"key": "agent:%s:%s" % (sid, aid), "depth": 3 if not hyp_inline else 2, "kind": "agent",
                             "label": a.get("type") or "agent", "sub": str(aid)[:8], "hyp": "", "state": astate,
                             # Idle = no event in the last 60 s while the lane spans the whole window; show the
                             # gap so the label and the lane marks agree at a glance
                             "since": fmt_age(now - a["last_seen"]) if astate == "idle" and a.get("last_seen") else "",
                             "cells": lane_cells(buckets, (sid, aid), width, astate,
                                                 span_columns(a.get("first_seen"), a.get("last_seen") if astate == "ended" else None, now, window_s, width)),
                             "right": "%d ev" % a.get("events", 0),
                             "live": astate == "working"})
            more = " · ".join("%d %s" % (n, k) for k, n in hidden.items() if n)
            if more:
                rows.append({"key": "agents-more:" + sid, "depth": 3 if not hyp_inline else 2, "kind": "more",
                             "label": "… %s sub-agents not listed" % more,
                             "sub": "", "hyp": "", "state": "", "cells": None, "right": "", "live": False})
    return rows


# --------------------------------------------------------------------------- the board (Textual)
def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def fmt_age(secs):
    if secs is None:
        return "-"
    secs = int(secs)
    if secs < 90:
        return "%ds" % secs
    if secs < 5400:
        return "%dm" % (secs // 60)
    return "%dh%02dm" % (secs // 3600, (secs % 3600) // 60)


def fmt_clock(t):
    return "-" if not t else time.strftime("%H:%M:%S", time.localtime(t))


def fmt_window(secs):
    return ("%dm" % (secs // 60)) if secs < 3600 else ("%dh" % (secs // 3600))


def build_board_class():
    """Textual is imported here only, so `hook`/`serve` never pay for it."""
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.widgets import Footer, Header, Static

    STATE_W, RATIO_W, RIGHT_W = 12, 9, 9      # RATIO_W: "lev  det" -> "0.62 0.80" per session

    def label_width(total_w):
        """Name column scales with the terminal: 30 % of the width, clamped 28..60 (fixed 44 clipped
        narrow terminals to a 35-cell lane; see the 2026-09-05 critique)."""
        return max(28, min(60, int(total_w * 0.30)))

    class Board(App):
        TITLE = "Claude observatory"
        ENABLE_COMMAND_PALETTE = False      # the ^p palette exposed Textual's theme picker; irrelevant here
        CSS = "#board {width: auto;} #totals {height: 1; background: $panel;}"
        # priority=True: these fire even when a scroll container or (in the browser) the xterm
        # input holds focus — without it up/down scrolled the pane and never reached move().
        BINDINGS = [Binding("q", "quit", "Quit", priority=True),
                    Binding("f", "cycle_filter", "Scope", priority=True),
                    Binding("w", "cycle_window", "Window", priority=True),
                    Binding("enter,space", "toggle", "Collapse/expand", priority=True),
                    Binding("up,k", "move(-1)", "Up", show=False, priority=True),
                    Binding("down,j", "move(1)", "Down", show=False, priority=True),
                    Binding("pageup", "page(-1)", "Page up", show=False, priority=True),
                    Binding("pagedown", "page(1)", "Page down", show=False, priority=True),
                    Binding("home", "home", "Top", show=False, priority=True),
                    Binding("end", "end", "Bottom", show=False, priority=True),
                    Binding("question_mark", "help", "Legend", priority=True)]

        def action_end(self):
            self.action_move(len(self.rows))

        def __init__(self, state_file, events_path):
            super().__init__()
            self.state_file, self.events_path = state_file, events_path
            self.live_only, self.recent_only, self.window_s = False, True, TL_WINDOWS[1]
            self.offset, self.state, self.events = 0, None, []
            self.collapsed, self.cursor, self.rows = set(), 0, []
            self._render_sig, self._rendering, self._dirty = None, False, True

        def compose(self) -> ComposeResult:
            # no Header: its only payload was the title and a mouse-only palette icon (one row reclaimed)
            yield VerticalScroll(Static("", id="board"), can_focus=False)
            yield Static("starting...", id="totals")
            yield Footer()

        def action_page(self, delta):
            step = max(1, (self.query_one(VerticalScroll).size.height or 20) - 3)
            self.action_move(delta * step)

        def action_home(self):
            self.action_move(-len(self.rows))

        def keep_cursor_visible(self):
            """Scroll the board so the cursor row (2 header lines above row 0) stays on screen."""
            try:
                sc = self.query_one(VerticalScroll)
                line = self.cursor + 2
                top, h = int(sc.scroll_y), sc.size.height or 20
                if line < top + 1:
                    sc.scroll_to(y=max(0, line - 1), animate=False)
                elif line >= top + h - 1:
                    sc.scroll_to(y=line - h + 2, animate=False)
            except Exception:
                pass

        def on_mount(self):
            self.refresh_state()
            self.tail_events()
            self.render_board()
            self.set_interval(1.0, self.refresh_state)
            self.set_interval(1.0, self.tail_events)
            self.set_interval(2.0, self.render_board)     # the lane strip moves 1 cell per ~2.5 s at the 5 m window

        # actions ----------------------------------------------------------------
        def action_cycle_filter(self):
            # all-recent -> live only -> everything (incl. dead sessions) -> all-recent
            if self.recent_only and not self.live_only:
                self.live_only = True
            elif self.live_only:
                self.live_only, self.recent_only = False, False
            else:
                self.recent_only = True
            self._dirty = True
            self.render_board()

        def action_cycle_window(self):
            self.window_s = TL_WINDOWS[(TL_WINDOWS.index(self.window_s) + 1) % len(TL_WINDOWS)]
            self._dirty = True
            self.render_board()

        def action_move(self, delta):
            if self.rows:
                self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
                self._dirty = True
                self.render_board()
                self.keep_cursor_visible()

        def action_toggle(self):
            self.toggle_row(self.cursor)

        def toggle_row(self, idx):
            if 0 <= idx < len(self.rows) and self.rows[idx]["kind"] in ("repo", "session"):
                key = self.rows[idx]["key"]
                self.collapsed.symmetric_difference_update({key})
                self.cursor = idx
                self._dirty = True
                self.render_board()

        def on_click(self, ev):
            # rows start after the two header lines (axis + legend)
            idx = ev.y - 2
            if 0 <= idx < len(self.rows):
                self.cursor = idx
                self.toggle_row(idx)

        # data -------------------------------------------------------------------
        def refresh_state(self):
            st = read_json(self.state_file)
            if not st:
                self.query_one("#totals", Static).update("waiting for state file %s" % self.state_file)
                return
            if st.get("generated_at") == (self.state or {}).get("generated_at"):
                return                                     # receiver hasn't written a new snapshot
            self.state, self._dirty = st, True
            lat = st.get("latency") or {}
            p50 = lat.get("p50")
            scope = "live only" if self.live_only else ("recent (1h)" if self.recent_only else "all incl. dead")
            self.query_one("#totals", Static).update(
                "live %d · total %d · events %d · scope %s · window %s · latency p50 %s · registry %s" % (
                    st.get("sessions_live", 0), st.get("sessions_total", 0), st.get("records_total", 0),
                    scope, fmt_window(self.window_s), ("%.0f ms" % (p50 * 1000)) if p50 is not None else "-",
                    "ok" if st.get("registry_ok") else "unavailable"))

        def tail_events(self):
            try:
                size = os.path.getsize(self.events_path)
            except OSError:
                return
            if size < self.offset:
                self.offset, self.events = 0, []
            if size == self.offset:
                return
            with open(self.events_path, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
                self.offset = fh.tell()
            for ln in data.decode("utf-8", "replace").splitlines():
                try:
                    self.events.append(json.loads(ln))
                except Exception:
                    continue
            if len(self.events) > 50000:
                self.events = self.events[-50000:]
            self._dirty = True

        # render -----------------------------------------------------------------
        def render_board(self):
            if self._rendering:
                return                                     # never queue a second render behind a slow one
            widget = self.query_one("#board", Static)
            sc = self.query_one(VerticalScroll)
            region = getattr(sc, "scrollable_content_region", None)
            total_w = (region.width if region and region.width else 0) or sc.size.width or self.size.width or 160
            self.label_w = label_width(total_w)
            width = max(20, total_w - self.label_w - STATE_W - RATIO_W - RIGHT_W - 5)
            now = time.time()
            # the strip only moves one cell per (window / width) seconds; skip ticks in between
            col = int(now / max(0.5, self.window_s / float(width)))
            stale = self.state_age(now) > 10
            if not self._dirty and self._render_sig == (col, width, stale):
                return
            self._rendering = True
            try:
                self._render_sig, self._dirty = (col, width, stale), False
                self._render_board(widget, width, now)
            finally:
                self._rendering = False

        def state_age(self, now):
            """Seconds since the receiver last wrote the state file (its own clock), or 0 if unknown."""
            g = (self.state or {}).get("generated_at_unix")
            return (now - g) if g else 0.0

        # the K lane legend: bars coloured by op.class, spikes by lifecycle, baseline = state hue
        LEGEND = (("▂▄▆", "script", CLASS_COLOR["modeled-deterministic"]),
                  ("▂▄▆", "skill", CLASS_COLOR["modeled-stochastic"]),
                  ("▂▄▆", "agent", CLASS_COLOR["delegated"]),
                  ("▂▄▆", "unmodeled", CLASS_COLOR["unmodeled"]),
                  ("▆", "prompt", SPIKE_COLOR["prompt"]), ("▆", "stop", SPIKE_COLOR["stop"]), ("▆", "error", SPIKE_COLOR["error"]),
                  ("⡀", "idle", blend(STATE_HUE["working"], 0.55)),
                  ("▏▕", "start/end", STATE_HUE["working"]))
        HELP = ("state: Needs input = the session asked you something · Blocked = the registry says it is stuck · "
                "Working / Waiting = hooks · Idle Nm = no event for N minutes (lane marks may be older) · Done/Ended = finished.\n"
                "lane: one cell per time slice; bar height = events in the slice vs the lane's busiest slice; bar colour = the "
                "dominant provenance class of the tool calls in it: green deterministic = a script from scripts/ or hooks/, blue "
                "stochastic = a compiled skill from skills/, magenta delegated = a sub-agent, grey unmodeled = plain Claude tool use "
                "(Read, Edit, an unknown program); ▆ spikes: white prompt, yellow stop, red error; ⡀ baseline in the state colour.\n"
                "lev = leverage = modeled / (modeled + unmodeled) tool calls — how much of the work touched the operating model; "
                "det = determinism = deterministic / (deterministic + stochastic) — how much of that was scripts rather than skills; "
                "'-' = no tool calls yet.\n")

        def _render_board(self, widget, width, now):
            sessions = (self.state or {}).get("sessions") or {}
            label_w = self.label_w
            # the hypothesis folds into the session row (its own line cost a full row for a 44-char payload)
            self.rows = board_rows(sessions, self.events, now, self.window_s, width, self.collapsed, self.live_only, self.recent_only,
                                   hyp_inline=True)
            out = Text()
            age = self.state_age(now)
            if age > 10:
                out.append("  receiver stale: no state update for %s — is `observatory.py serve` running?\n" % fmt_age(age), style="bold yellow")
            out.append("%-*s %-*s %-*s " % (label_w, "repo / session · hypothesis / agent", STATE_W, "state", RATIO_W, "lev  det"), style="bold")
            out.append(timeline_axis(now, self.window_s, width), style="dim")
            out.append(" %*s\n" % (RIGHT_W, "age · ev"), style="bold")
            # legend in the lanes' own colors, as many named entries as fit the lane width (the class
            # colours come first so they are never the ones dropped); the rest is glyphs + [?]
            out.append(" " * (label_w + STATE_W + RATIO_W + 3))
            used, shown = 0, 0
            for glyph, name, style in self.LEGEND:
                w = len(glyph) + len(name) + 3
                if used + w > width - 4:
                    break
                out.append(glyph, style=style)
                out.append(" %s  " % name, style="dim")
                used, shown = used + w, shown + 1
            if shown < len(self.LEGEND):
                for glyph, _, style in self.LEGEND[shown:]:
                    if used + len(glyph) + 1 > width - 4:
                        break
                    out.append(glyph + " ", style=style)
                    used += len(glyph) + 1
                out.append("[?]", style="dim")
            out.append("\n")
            if getattr(self, "show_help", False):
                for line in self.HELP.splitlines():
                    out.append(" " * (label_w + STATE_W + RATIO_W + 3))
                    out.append(line + "\n", style="dim")
            for i, row in enumerate(self.rows):
                cur = i == self.cursor
                base = "" if row["live"] else "dim"
                if cur:
                    base = (base + " reverse").strip()
                indent = "  " * row["depth"]
                if row["kind"] == "repo":
                    label = Text(indent + row["label"], style=("bold " + base).strip())
                    if row.get("attention"):
                        label.append("  %d need attention" % row["attention"], style="bold yellow")
                    if row["key"] in self.collapsed:
                        label.append("  …", style="dim")
                else:
                    mark = "↳ " if row["kind"] == "agent" else ""
                    label = Text(indent + mark + row["label"], style=("bold " + base).strip() if row["kind"] == "session" else base)
                    if row.get("hyp"):
                        label.append("  " + row["hyp"], style=("cyan " + ("reverse" if cur else "")).strip())
                    elif row["kind"] == "session" and not row.get("hyp") and row["sub"]:
                        pass
                    if row["sub"] and not (row["kind"] == "session" and row.get("hyp") and label.cell_len > label_w):
                        label.append("  " + row["sub"], style=("dim " + ("reverse" if cur else "")).strip())
                    if row["kind"] == "session" and row["key"] in self.collapsed:
                        label.append("  …", style="dim")
                label.truncate(label_w, overflow="ellipsis")
                label.pad_right(label_w - label.cell_len)
                out.append_text(label)
                out.append(" ")
                st = row["state"]
                st_txt = st.capitalize() + ((" " + row["since"]) if row.get("since") else "")
                out.append("%-*s" % (STATE_W, st_txt[:STATE_W]), style=(STATE_STYLE.get(st, "") + (" reverse" if cur else "")).strip())
                out.append(" ")
                out.append("%-*s" % (RATIO_W, row.get("ratios") or ""), style=("cyan " + base).strip() if row.get("ratios") else base)
                out.append(" ")
                if row["cells"]:
                    for text, style in coalesce(row["cells"]):
                        out.append(text, style=style)
                else:
                    out.append(" " * width)
                out.append(" %*s\n" % (RIGHT_W, row["right"]), style="dim")
            if not self.rows:
                msg = ("no live sessions right now — [f] widens the scope" if self.live_only
                       else "no sessions yet (registry polls every 5 s; hooks post as sessions work)")
                out.append("\n  %s\n" % msg, style="dim")
            hidden = sum(1 for s in sessions.values() if (self.recent_only and not session_recent(s, now)) or (self.live_only and not s.get("live")))
            if hidden:
                out.append("\n  %d older session%s hidden — [f] cycles scope (recent / live / all)\n" % (hidden, "" if hidden == 1 else "s"), style="dim")
            widget.update(out)

        def action_help(self):
            self.show_help = not getattr(self, "show_help", False)
            self._dirty = True
            self.render_board()

    return Board


# --------------------------------------------------------------------------- signal views (pure, testable)
# otel-desktop-viewer-shaped read models over the flat event records: traces (turn -> sub-agent ->
# tool spans, or real OTLP spans when a host sends them), logs (every record), metrics (derived
# streams plus real OTLP datapoints).

def _span(span_id, parent, name, start, end, kind, attrs, session_id, agent_id=None):
    return {"span_id": span_id, "parent": parent, "name": name, "start": start, "end": end, "kind": kind,
            "attrs": attrs, "session_id": session_id, "agent_id": agent_id, "in_progress": end is None}


def build_traces(events, sessions, now):
    """Turn-shaped traces from hook records. A trace = one turn of one session (UserPromptSubmit ..
    Stop); spans = the turn, one per sub-agent (SubagentStart..SubagentStop), one per tool call
    (PreToolUse..PostToolUse paired by tool_use_id, else by order). Real OTLP spans group by trace_id."""
    traces = []
    by_sid, otlp = {}, {}
    for r in events:
        if r.get("signal") == "span" and r.get("trace_id"):
            otlp.setdefault(r["trace_id"], []).append(r)
        elif r.get("hook_event_name"):
            by_sid.setdefault(r.get("session_id"), []).append(r)
    for sid, evs in by_sid.items():
        s = sessions.get(sid) or {}
        name, repo, branch = session_label(s)
        hid = (s.get("hids") or {}).get("primary")
        base = {"session.name": name or repo, "repo": repo, "branch": branch, "hypothesis": hid, "cwd": s.get("cwd")}
        turns, cur = [], None
        for r in evs:
            h = r.get("hook_event_name")
            if h == "UserPromptSubmit" and not r.get("agent_id") and cur is not None and cur["prompt_id"] is None \
                    and all(e.get("hook_event_name") == "SessionStart" for e in cur["events"]):
                cur["prompt_id"] = r.get("prompt_id")          # fold the session-start prelude into the first turn
            elif cur is None or (h == "UserPromptSubmit" and not r.get("agent_id")):
                cur = {"start": r.get("arrival_ts_unix"), "prompt_id": r.get("prompt_id"), "events": [], "end": None}
                turns.append(cur)
            cur["events"].append(r)
            if h in ("Stop", "SessionEnd") and not r.get("agent_id"):
                cur["end"] = r.get("arrival_ts_unix")
        for i, t in enumerate(turns):
            tid = "%s:%s" % (sid[:8], t["prompt_id"] or ("turn%d" % i))
            root_id = tid + ":root"
            spans = [_span(root_id, None, "turn %s" % (name or repo), t["start"], t["end"], "turn",
                           dict(base, **{"prompt.id": t["prompt_id"], "session.id": sid}), sid)]
            agents, open_tools = {}, {}
            for r in t["events"]:
                h, aid, ts = r.get("hook_event_name"), r.get("agent_id"), r.get("arrival_ts_unix")
                if aid:
                    a = agents.get(aid)
                    if a is None:
                        a = agents[aid] = _span(tid + ":agent:" + str(aid), root_id, "agent %s" % (r.get("agent_type") or "?"),
                                                ts, None, "agent", {"agent.id": aid, "agent.type": r.get("agent_type"), "session.id": sid}, sid, aid)
                        spans.append(a)
                    if h == "SubagentStop":
                        a["end"], a["in_progress"] = ts, False
                    elif a["in_progress"]:
                        a["_last"] = ts
                parent = (tid + ":agent:" + str(aid)) if aid else root_id
                if h == "PreToolUse":
                    key = r.get("tool_use_id") or "%s|%s|%s" % (aid, r.get("tool_name"), len(open_tools))
                    sp = _span(tid + ":tool:" + key, parent, "tool %s" % (r.get("tool_name") or "?"), ts, None, "tool",
                               {"tool.name": r.get("tool_name"), "tool_use_id": r.get("tool_use_id"), "prompt.id": r.get("prompt_id"),
                                "session.id": sid, "agent.id": aid, "op.class": r.get("op_class"), "op.node": r.get("op_node")}, sid, aid)
                    spans.append(sp)
                    open_tools.setdefault((aid, r.get("tool_name"), r.get("tool_use_id")), []).append(sp)
                elif h in COUNTED_TOOL_HOOKS:
                    cands = (open_tools.get((aid, r.get("tool_name"), r.get("tool_use_id")))
                             or open_tools.get((aid, r.get("tool_name"), None)) or [])
                    if cands:
                        sp = cands.pop(0)
                    else:  # Post without a Pre (hook installed mid-flight): zero-length span
                        sp = _span(tid + ":tool:%s:%d" % (r.get("tool_name"), len(spans)), parent, "tool %s" % (r.get("tool_name") or "?"),
                                   ts, None, "tool", {"tool.name": r.get("tool_name"), "session.id": sid, "agent.id": aid}, sid, aid)
                        spans.append(sp)
                    sp["end"], sp["in_progress"] = ts, False
                    sp["attrs"].update({"tool.outcome": r.get("tool_outcome"), "tool.summary": r.get("tool_summary")})
                    # the counted (Post) record carries the class the lane draws; keep it on the span so the
                    # waterfall bar and the strip cell speak the same provenance colour
                    if r.get("op_class") in CLASSES or not sp["attrs"].get("op.class"):
                        sp["attrs"]["op.class"], sp["attrs"]["op.node"] = r.get("op_class"), r.get("op_node")
                    sp["error"] = r.get("tool_outcome") in ("error", "interrupted")
            for a in agents.values():
                if a["in_progress"] and t["end"] is not None:
                    a["end"], a["in_progress"] = a.get("_last", t["end"]), False
                a.pop("_last", None)
            end = t["end"] if t["end"] is not None else max([sp["end"] or sp["start"] or 0 for sp in spans] + [t["start"] or 0])
            traces.append({"trace_id": tid, "session_id": sid, "label": name or repo, "repo": repo, "hypothesis": hid,
                           "start": t["start"], "end": t["end"], "last": end, "spans": spans,
                           "in_progress": t["end"] is None, "errors": sum(1 for sp in spans if sp.get("error")),
                           "kind": "turn", "source": "hooks",
                           # hue of the strip's baseline / now edge: the session's state while the turn is open, done after
                           "state": (session_state(s) if s else "working") if t["end"] is None else "done"})
    for trace_id, recs in otlp.items():
        spans = [_span(r.get("span_id"), r.get("parent_span_id"), r.get("span_name") or "span", r.get("span_start"), r.get("span_end"),
                       "otlp", dict(r.get("attributes") or {}, **{"scope": r.get("scope"), "status": r.get("status")}), r.get("session_id"))
                 for r in recs]
        starts = [sp["start"] for sp in spans if sp["start"]]
        ends = [sp["end"] for sp in spans if sp["end"]]
        s = sessions.get(recs[0].get("session_id")) or {}
        traces.append({"trace_id": trace_id, "session_id": recs[0].get("session_id"), "label": session_label(s)[0] or (recs[0].get("resource") or {}).get("service.name") or "otlp",
                       "repo": session_label(s)[1], "hypothesis": (s.get("hids") or {}).get("primary"),
                       "start": min(starts) if starts else None, "end": max(ends) if ends else None, "last": max(ends or starts or [0]),
                       "spans": spans, "in_progress": False, "errors": sum(1 for r in recs if r.get("status") in (2, "STATUS_CODE_ERROR")),
                       "kind": "otlp", "source": "otlp", "state": "done"})
    traces.sort(key=lambda t: -(t["start"] or 0))
    return traces


def span_depths(spans):
    """Depth-first order with depth, parents before children (unknown parents -> root)."""
    by_id = {sp["span_id"]: sp for sp in spans}
    kids = {}
    for sp in spans:
        p = sp["parent"] if sp["parent"] in by_id else None
        kids.setdefault(p, []).append(sp)
    out = []

    def walk(parent, depth):
        for sp in sorted(kids.get(parent, []), key=lambda x: (x["start"] or 0)):
            out.append((sp, depth))
            walk(sp["span_id"], depth + 1)
    walk(None, 0)
    return out


# The Traces view (H-DRAFT-4ac9ae01): one extent per trace, one strip function at any width, one
# colour vocabulary (the lane's provenance classes) — the drawer sparkline is the waterfall's turn
# row rendered at 28 cells, so a mark in one can be found in the other.
SPARK_W = 28                                    # drawer strip cells; the axis marks every 4th boundary
TICK_STEPS = (5, 10, 15, 30, 60, 120, 300)      # axis tick step, chosen to give 4..8 ticks


def fmt_dur(secs):
    """The one duration format of the Traces view (header, drawer, gutter)."""
    secs = max(0.0, secs or 0.0)
    return ("%.1fs" % secs) if secs < 90 else fmt_age(secs)


def trace_extent(trace, now):
    """(t0, t_end): start .. end once the turn stopped, start .. now while it is open. EVERY printed
    duration and every bar scale in the Traces view (drawer label, header, gutter, sparkline, waterfall)
    derives from this pair, so the two panels cannot disagree about time."""
    t0 = trace.get("start") if trace.get("start") is not None else now
    t_end = trace.get("end") if trace.get("end") is not None else now
    return t0, max(t0, t_end)


def span_class(sp):
    """Provenance class a span is drawn in: tool -> its record's op.class (unmodeled grey when the
    receiver could not classify it), sub-agent -> delegated, real OTLP span -> unmodeled, turn -> None."""
    kind = sp.get("kind")
    if kind == "agent":
        return "delegated"
    if kind == "tool":
        c = (sp.get("attrs") or {}).get("op.class")
        return c if c in CLASSES else "unmodeled"
    if kind == "otlp":
        return "unmodeled"
    return None


def span_cell_range(sp, width, t0, t_end, now):
    """Inclusive (c0, c1) cells a span covers on a `width`-cell strip over [t0, t_end], or None when it
    lies outside. Open spans run to now; everything clips to the extent; a span ending exactly on a
    cell boundary does not enter the next cell. Bars, strip cells and cross-highlights all use this."""
    width = max(1, int(width))
    per = max(1e-6, t_end - t0) / float(width)
    st = sp["start"] if sp.get("start") is not None else t0
    en = sp["end"] if sp.get("end") is not None else now
    en = max(st, en)
    if en < t0 or st > t_end:
        return None
    st, en = max(st, t0), min(en, t_end)
    c0 = min(width - 1, int((st - t0) / per))
    c1 = min(width - 1, int((en - t0) / per))
    if c1 > c0 and t0 + c1 * per >= en:
        c1 -= 1
    return c0, max(c0, c1)


def timeline_cells(trace, width, t0, t_end, now, highlight=None):
    """The trace's own strip at any width: [(glyph, style)] * width. Per cell, the non-root spans
    overlapping that slice of [t0, t_end]: height ▂..▆ by how many run at once (SOLID_LEVELS[1 + min(5,
    concurrency)]), colour = the provenance class covering most of the slice (tool spans in their
    record's op.class, sub-agent spans magenta; ties to the more-modeled class via dominant_class), ⡀
    in the dim state hue when nothing runs. Cell 0 = the prompt (white ▆); the last cell = the stop
    (yellow ▆) once the turn ended, or the now edge ▕ in the state hue while it is open; a cell holding
    an error span's end is red ▆. `highlight` = cell indices to mark: they become full-height █ in bold
    (keeping their colour) while every other cell of the strip dims — reverse video was illegible on the
    drawer's selected row (H-DRAFT-4ac9ae01 run 2, amendment #2). Pure: the drawer renders it at SPARK_W
    cells, the waterfall's turn row at pane width."""
    width = max(1, int(width))
    state = trace.get("state") or ("working" if trace.get("in_progress") else "done")
    hue = STATE_HUE.get(state, STATE_HUE["working"])
    base = blend(hue, 0.55)
    per = max(1e-6, t_end - t0) / float(width)
    conc = [0] * width
    cls_time = [{} for _ in range(width)]
    err_cells = set()
    for sp in trace.get("spans") or ():
        if sp.get("kind") == "turn":
            continue
        rng = span_cell_range(sp, width, t0, t_end, now)
        if rng is None:
            continue
        cls = span_class(sp) or "unmodeled"
        st = max(t0, sp["start"] if sp.get("start") is not None else t0)
        en = min(t_end, max(st, sp["end"] if sp.get("end") is not None else now))
        for c in range(rng[0], rng[1] + 1):
            conc[c] += 1
            lo, hi = t0 + c * per, t0 + (c + 1) * per
            cover = max(1e-6, min(en, hi) - max(st, lo))          # zero-length spans still count a little
            cls_time[c][cls] = cls_time[c].get(cls, 0.0) + cover
        if sp.get("error") and sp.get("end") is not None:
            err_cells.add(rng[1])
    turn = trace.get("kind") == "turn"
    out = []
    for c in range(width):
        if c in err_cells:
            cell = (SOLID_LEVELS[6], SPIKE_COLOR["error"])
        elif c == 0 and turn:
            cell = (SOLID_LEVELS[6], SPIKE_COLOR["prompt"])
        elif c == width - 1 and trace.get("in_progress"):
            cell = (CAP_R, hue)
        elif c == width - 1 and turn:
            cell = (SOLID_LEVELS[6], SPIKE_COLOR["stop"])
        elif conc[c]:
            cls = dominant_class({k: round(v, 6) for k, v in cls_time[c].items()})
            cell = (SOLID_LEVELS[1 + min(5, conc[c])], CLASS_COLOR.get(cls, base))
        else:
            cell = (IDLE_DOT, base)
        if highlight:
            # the mark is the only coloured cell: every other cell (lifecycle glyphs included) drains to the
            # neutral grey, dimmed (H-DRAFT-64674bd0-cross-highlight-legibility)
            cell = (HL_GLYPH, (cell[1] + " bold").strip()) if c in highlight else (cell[0], DRAIN_STYLE)
        out.append(cell)
    return out


def slice_cells(slice_idx, width, spark_w=SPARK_W):
    """The pane-width cells one drawer-strip slice (0..spark_w-1) maps onto."""
    lo = int(slice_idx * width / float(spark_w))
    hi = max(lo, int((slice_idx + 1) * width / float(spark_w)) - 1)
    return set(range(lo, min(width - 1, hi) + 1))


def tick_step(span_s, lo=4, hi=8):
    """Smallest step from TICK_STEPS (then multiples of 5 m) giving at most `hi` ticks; below the set's
    reach (traces under ~20 s) fall back to 1 s / 2 s so short traces still get `lo`..`hi` ticks."""
    for step in TICK_STEPS:
        n = int(span_s // step) + 1
        if n > hi:
            continue
        if n < lo:                                   # even 5 s gives too few: a trace shorter than ~15 s
            for small in (1, 2):
                if int(span_s // small) + 1 <= hi:
                    return small
        return step
    step = TICK_STEPS[-1]
    while int(span_s // step) + 1 > hi:
        step += TICK_STEPS[-1]
    return step


def axis_ticks(t0, t_end, width, in_progress, spark_w=SPARK_W):
    """Two axis lines for the bar area as [(text, style)] runs: ticks ┬ at tick_step (─ between, a dim ┆
    at every 4th of the drawer strip's cell boundaries so its 28 cells map onto the bars, ▕ at the now
    edge or ┤ at the stop), and HH:MM:SS under the first / middle / last tick with 'now' (or 'stop
    HH:MM:SS' in yellow) at the right edge. Same extent as timeline_cells, so ticks sit on cell edges."""
    width = max(10, int(width))
    span_s = max(1e-6, t_end - t0)
    per = span_s / float(width)
    step = tick_step(span_s)
    cols, k = [], 0
    while t0 + k * step <= t_end + 1e-9:
        cols.append(min(width - 1, int(k * step / per)))
        k += 1
    line = ["─"] * width
    style = [""] * width
    for j in range(1, spark_w // 4):
        c = int(j * 4 * width / float(spark_w))
        if 0 < c < width - 1:
            line[c], style[c] = "┆", "dim"
    for c in cols:
        if c < width - 1:
            line[c], style[c] = "┬", ""
    line[width - 1], style[width - 1] = (CAP_R, STATE_HUE["working"]) if in_progress else ("┤", "")
    lab = [" "] * width
    lab_style = [""] * width
    right = "now" if in_progress else ("stop " + fmt_clock(t_end))
    r0 = max(0, width - len(right))
    for i, ch in enumerate(right):
        if r0 + i < width:
            lab[r0 + i], lab_style[r0 + i] = ch, ("bold " + STATE_HUE["working"]) if in_progress else ("bold " + SPIKE_COLOR["stop"])
    placed = [(r0, width)]

    def put(col, text, center=False):
        pos = max(0, col - len(text) // 2) if center else col
        pos = min(pos, width - len(text))
        if pos < 0 or any(not (pos + len(text) + 1 <= a or pos >= b + 1) for a, b in placed):
            return
        for i, ch in enumerate(text):
            lab[pos + i] = ch
        placed.append((pos, pos + len(text)))
    put(0, fmt_clock(t0))
    if len(cols) >= 3:
        put(cols[-1], fmt_clock(t0 + (len(cols) - 1) * step))
        put(cols[len(cols) // 2], fmt_clock(t0 + (len(cols) // 2) * step), center=True)
    tick_runs = coalesce([(line[i], style[i] or "dim") for i in range(width)])
    label_runs = coalesce([(lab[i], lab_style[i] or "dim") for i in range(width)])
    return tick_runs, label_runs


def waterfall_rows(trace, width, now):
    """[(depth, span, bar_offset, bar_len, dur_s)] on the trace's shared extent (trace_extent) at `width`
    cells. Offsets and lengths are span_cell_range, so bars, the turn row's strip cells and the drawer
    strip agree cell for cell; the root row spans the full width. Open spans clip at the extent's end."""
    t0, t_end = trace_extent(trace, now)
    rows = []
    for sp, depth in span_depths(trace["spans"]):
        if sp["kind"] == "turn":
            rows.append((depth, sp, 0, width, t_end - t0))
            continue
        rng = span_cell_range(sp, width, t0, t_end, now)
        off, ln = (rng[0], rng[1] - rng[0] + 1) if rng else (width - 1, 1)
        st = sp["start"] if sp["start"] is not None else t0
        en = min(t_end, sp["end"] if sp["end"] is not None else now)
        rows.append((depth, sp, off, ln, max(0.0, en - st)))
    return rows


def filter_text(rows, query, fields):
    q = (query or "").strip().lower()
    if not q:
        return rows
    return [r for r in rows if any(q in str(r.get(f) or "").lower() for f in fields)]


def sparkline(values, width):
    vals = list(values)[-width:] if width else list(values)
    if not vals:
        return ""
    hi = max(vals) or 1
    return "".join(TL_BARS[min(7, int(v / hi * 7.999))] if v > 0 else " " for v in vals)


def metric_streams(events, samples, now, window_s, buckets=60, sessions=None):
    """Derived streams (from hook records + board state samples) plus real OTLP datapoints, bucketed.
    Returns {name: {"name","unit","kind","values":[...], "current", "total", "series": {label: [...]}}}.
    Per-session series are labeled by session name when `sessions` (the state dict) is given."""
    start, per = now - window_s, window_s / float(buckets)
    streams = {}
    names = {sid: (session_label(s)[0] or session_label(s)[1]) for sid, s in (sessions or {}).items()}

    def stream(name, unit, kind):
        return streams.setdefault(name, {"name": name, "unit": unit, "kind": kind, "values": [0.0] * buckets, "series": {},
                                         "_n": [0] * buckets, "_sn": {}})

    def add(st, t, v, label=None, last=False):
        """last=True: the bucket holds the LAST value seen (running ratios), not the mean of the
        values that landed in it — two events 0.9 s apart must read as the later ratio, not their
        average (H-DRAFT-fcf7b3fa leverage run 2, A3)."""
        if t is None or t < start:
            return
        col = min(buckets - 1, int((t - start) / per))
        if last:
            st["values"][col], st["_n"][col] = v, 1
        else:
            st["values"][col] += v
            st["_n"][col] += 1
        if label:
            sv = st["series"].setdefault(label, [0.0] * buckets)
            sn = st["_sn"].setdefault(label, [0] * buckets)
            if last:
                sv[col], sn[col] = v, 1
            else:
                sv[col] += v
                sn[col] += 1
    cls_by_sid, cls_all = {}, {}

    def ratio_points(t, sid):
        # running leverage / determinism over the events in the window, per session and 'all' (gauges)
        for label, counts in ((sid, cls_by_sid[sid]), ("all", cls_all)):
            blk = ratio_block(counts)
            if blk["leverage"] is not None:
                add(stream("observatory.model_leverage", "ratio", "gauge"), t, blk["leverage"], label, last=True)
            if blk["determinism"] is not None:
                add(stream("observatory.determinism", "ratio", "gauge"), t, blk["determinism"], label, last=True)
            if blk["handoff_share"] is not None:                       # ROADMAP §1b/§3: the excluded class stays visible
                add(stream("observatory.handoff_share", "ratio", "gauge"), t, blk["handoff_share"], label, last=True)
    for r in events:
        t = r.get("arrival_ts_unix")
        sid = names.get(r.get("session_id")) or (r.get("session_id") or "?")[:8]
        if r.get("signal") == "metric" and r.get("metric_name"):
            st = stream(r["metric_name"], r.get("metric_unit") or "", r.get("metric_kind") or "gauge")
            add(st, r.get("event_ts_unix") or t, r.get("metric_value") or 0.0, sid)
            continue
        h = r.get("hook_event_name")
        if not h:
            continue
        add(stream("observatory.hook_events", "1", "sum"), t, 1.0, sid)
        if r.get("hook_self_ms") is not None:
            add(stream("observatory.hook_self_ms", "ms", "gauge"), t, float(r["hook_self_ms"]))
        if h in COUNTED_TOOL_HOOKS:
            add(stream("observatory.tool_calls", "1", "sum"), t, 1.0, r.get("tool_name") or "?")
            if r.get("tool_outcome") in ("error", "interrupted"):
                add(stream("observatory.tool_errors", "1", "sum"), t, 1.0, r.get("tool_name") or "?")
            if r.get("op_class") in CLASSES:
                c = cls_by_sid.setdefault(sid, {})
                c[r["op_class"]] = c.get(r["op_class"], 0) + 1
                cls_all[r["op_class"]] = cls_all.get(r["op_class"], 0) + 1
                ratio_points(t, sid)
        if h == "UserPromptSubmit":
            add(stream("observatory.prompts", "1", "sum"), t, 1.0, sid)
        if h in ("SubagentStart",):
            add(stream("observatory.subagents_started", "1", "sum"), t, 1.0, r.get("agent_type") or "?")
        if r.get("event_ts_unix") and t:
            add(stream("observatory.hook_latency_ms", "ms", "gauge"), t, max(0.0, (t - r["event_ts_unix"]) * 1000.0))
    for t, live, total in samples:
        add(stream("observatory.sessions_live", "1", "gauge"), t, float(live))
        add(stream("observatory.sessions_total", "1", "gauge"), t, float(total))
    for st in streams.values():
        if st["kind"] == "gauge":   # gauges average within a bucket (aggregate AND each series), sums add
            st["values"] = [v / n if n else 0.0 for v, n in zip(st["values"], st["_n"])]
            for label, vs in st["series"].items():
                st["series"][label] = [v / n if n else 0.0 for v, n in zip(vs, st["_sn"].get(label, [0] * buckets))]
        st["total"] = sum(st["values"]) if st["kind"] == "sum" else None
        nz = [v for v in st["values"] if v]
        st["current"] = nz[-1] if nz else 0.0
        st["peak"] = max(st["values"]) if st["values"] else 0.0
        del st["_n"], st["_sn"]
    return dict(sorted(streams.items()))


def build_viewer_class():
    """otel-desktop-viewer-shaped TUI: Sessions | Traces | Logs | Metrics, three panes each."""
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import ContentSwitcher, Footer, Header, Input, ListItem, ListView, Static

    Board = build_board_class()
    MODES = [("sessions", "1 Sessions"), ("traces", "2 Traces"), ("logs", "3 Logs"), ("metrics", "4 Metrics")]

    def soft_break(value, every=24):
        """Insert zero-width break opportunities after '/', '_', '.', '-' and every `every` chars so
        Rich wraps paths and ids at natural boundaries instead of mid-token (inspectors hard-wrapped
        'hook_event_n|ame' at 12 chars in the 2026-09-05 critique)."""
        s = str(value)
        out, run = [], 0
        for ch in s:
            out.append(ch)
            run += 1
            if ch in "/_.-" or run >= every:
                out.append("​")
                run = 0
        return "".join(out)

    def kvtext(title, pairs, style_key="cyan"):
        t = Text()
        t.append(title + "\n", style="bold underline")
        for k, v in pairs:
            if v in (None, "", [], {}):
                continue
            t.append("%s" % k, style=style_key)
            t.append("  %s\n" % soft_break(json.dumps(v, sort_keys=True, default=str) if isinstance(v, (dict, list)) else v))
        return t

    class SignalPane(Vertical):
        """Left drawer (search + list) | center | right inspector. Subclasses fill the three."""
        # drawer and inspector take fixed shares; the inspector was ~20 cells at 170 columns and
        # wrapped every value (critique + maintainer screenshot 2026-09-05). Inspector text does not
        # wrap; long values soft-break at / _ . - only.
        DEFAULT_CSS = ("SignalPane {height: 1fr;} SignalPane Horizontal {height: 1fr;} "
                       "SignalPane #drawer {width: 30%; min-width: 30; border-right: solid $accent;} SignalPane #center {width: 1fr; min-width: 40;} "
                       "SignalPane #inspector {width: 34%; min-width: 44; border-left: solid $accent; padding: 0 1;} "
                       "SignalPane #inspector Static {width: 1fr;} SignalPane Input {height: 3;} SignalPane ListView {height: 1fr;} "
                       "SignalPane #spans ListItem Static {text-wrap: nowrap; text-overflow: clip;} "
                       "SignalPane #wf-head {text-wrap: nowrap; text-overflow: ellipsis;}")   # a wrapped header would push the axis off the turn row

        def __init__(self, app_ref, **kw):
            super().__init__(**kw)
            self.store = app_ref
            self.items, self.selected, self.query, self._sig = [], None, "", None
            self._label_of, self._key_of, self._pending, self._building = None, None, ([], ()), False

        def compose(self) -> ComposeResult:
            with Horizontal():
                with Vertical(id="drawer"):
                    yield Input(placeholder=self.PLACEHOLDER, id="search")
                    yield ListView(id="list")
                yield VerticalScroll(Static("", id="center"))
                yield VerticalScroll(Static("", id="inspector"))

        def on_input_changed(self, ev):
            self.query, self._sig = ev.value, None
            self.rebuild()

        def on_list_view_highlighted(self, ev):
            # only the drawer list selects an item; panes with a second ListView (the span waterfall)
            # override this and route by list id — query_one(ListView) picked the wrong list before
            if ev.list_view.id != "list":
                return
            if ev.item is not None and getattr(ev.item, "key", None) is not None:
                self.selected = ev.item.key
                self.render_detail()

        @staticmethod
        def live_children(lv):
            """A ListView's children that are not being pruned: Textual removes children asynchronously,
            so right after clear() the old rows still sit in lv.children (they are flagged _pruning)."""
            return [c for c in lv.children if not getattr(c, "_pruning", False)]

        @staticmethod
        def static_of(li):
            """The row's Static, or None while a rebuild is still mounting it (an in-place update then waits
            for the next tick instead of raising inside a timer callback)."""
            try:
                return li.query_one(Static)
            except Exception:
                return None

        def set_list(self, items, key_of, label_of):
            """Whenever the KEY sequence changes, CLEAR the drawer list and rebuild it in item order, then
            restore the highlight by key (the index of self.selected); when only labels changed, update
            them in place. Never move_child: the old key-reconcile-and-move step left the visual order
            and the highlight out of step with the item list (H-DRAFT-4ac9ae01, cause 1). The rebuild
            awaits the removal before appending, because ListView.index is positional over a node list
            that still holds the pruned rows until their tasks exit."""
            lv = self.query_one("#list", ListView)
            sig = tuple(key_of(i) for i in items)
            keep = self.selected if self.selected in sig else (sig[0] if sig else None)
            self.selected, self._label_of, self._key_of = keep, label_of, key_of
            if sig != self._sig:
                self._sig = sig
                self._pending = (list(items), sig)
                self.run_worker(self._rebuild_list(), group="drawer-list", exclusive=True, exit_on_error=False)
                return
            rows = self.live_children(lv)
            if len(rows) == len(items) and not self._building:
                for li, item in zip(rows, items):
                    st = self.static_of(li)
                    if st is not None:
                        st.update(label_of(item))                  # refresh live counters in place
                if keep is not None and (lv.index is None or lv.index >= len(rows) or getattr(rows[lv.index], "key", None) != keep):
                    lv.index = sig.index(keep)
            self.render_detail()

        async def _rebuild_list(self):
            lv = self.query_one("#list", ListView)
            items, sig = self._pending
            label_of = self._label_of
            self._building = True
            try:
                await lv.clear()
                new = []
                for item, k in zip(items, sig):
                    li = ListItem(Static(label_of(item)))
                    li.key = k
                    new.append(li)
                if new:
                    await lv.extend(new)
            finally:
                self._building = False
            if self._sig != sig:
                return                                             # a newer rebuild took over
            keep = self.selected if self.selected in sig else (sig[0] if sig else None)
            self.selected = keep
            if keep is not None:
                lv.index = sig.index(keep)
            self.render_detail()

        def refresh_label(self, key):
            """Re-render one drawer row in place (e.g. the selected trace's strip after a cross-highlight)."""
            label_of = getattr(self, "_label_of", None)
            if label_of is None or self._sig is None or key not in self._sig:
                return
            lv = self.query_one("#list", ListView)
            rows = self.live_children(lv)
            pos = self._sig.index(key)
            item = next((i for i in self.items if self._key_of(i) == key), None)
            if item is not None and pos < len(rows) and getattr(rows[pos], "key", None) == key:
                st = self.static_of(rows[pos])
                if st is not None:
                    st.update(label_of(item))

    class TracesPane(SignalPane):
        PLACEHOLDER = "search traces: session, repo, hypothesis, tool"
        session_filter = None          # set by the Sessions view's "t" jump; cleared with escape
        # mode-2 legend: the Sessions lane legend plus the two glyphs only the waterfall draws
        LEGEND = Board.LEGEND + (("▶", "live", STATE_HUE["working"]), ("✖", "error", SPIKE_COLOR["error"]))
        HELP = ("strip / turn row: one cell per slice of the trace's extent (start → stop, or → now while it is open); bar height = "
                "spans running at once; colour = the provenance class covering most of the slice (green script, blue skill, magenta "
                "sub-agent, grey unmodeled); ▆ white = prompt, yellow = stop, red = a span ended in error there; ▕ = the now edge.",
                "bars: one per span in its class colour; ▶ = still running (drawn to now), ✖ = ended in error; ┆ on the axis = every "
                "4th cell boundary of the drawer strip. Highlighting a span marks its cells as bold full-height blocks (the rest dim) in the turn row and the drawer strip.",
                "←/→ pick a strip slice (▌ marks the spans running in it, the first scrolls into view); enter selects the first of "
                "them; the inspector's 'window' block spells the slice out in words.")
        DUP_ATTRS = {"session.id", "session.name", "repo", "branch", "hypothesis", "cwd", "op.class"}   # shown once, in resource/class
        CLASS_WORD = {"modeled-deterministic": "script (green)", "modeled-stochastic": "skill (blue)",
                      "delegated": "sub-agent (magenta)", "unmodeled": "unmodeled (grey)"}

        def __init__(self, app_ref, **kw):
            super().__init__(app_ref, **kw)
            self.traces, self.span_sel, self.slice_sel, self.show_help = [], None, None, False
            self._wf_sig, self._wf, self._span_pending = None, None, []

        def rebuild(self):
            now = time.time()
            self.traces = build_traces(self.store.events, self.store.sessions(), now)
            rows = [dict(t, tools=",".join(sp["attrs"].get("tool.name") or "" for sp in t["spans"] if sp["kind"] == "tool")) for t in self.traces]
            if self.session_filter:
                rows = [t for t in rows if t["session_id"] == self.session_filter]
            rows = filter_text(rows, self.query, ("label", "repo", "hypothesis", "tools", "trace_id"))[:400]
            self.items = rows
            self.set_list(rows, lambda t: t["trace_id"], lambda t: self.drawer_label(t, time.time()))
            ph = self.query_one(Input)
            if self.session_filter:
                s = self.store.sessions().get(self.session_filter) or {}
                name = session_label(s)[0] or session_label(s)[1] if s else self.session_filter[:8]
                ph.placeholder = "session: %s  (esc clears)  · search traces" % name
            else:
                ph.placeholder = self.PLACEHOLDER

        # shared geometry -----------------------------------------------------------------------
        def selected_span(self, t):
            return next((sp for sp in t["spans"] if sp["span_id"] == self.span_sel), None)

        def strip_highlight(self, t, width, t0, t_end, now):
            """Cells to mark (bold █) on a `width`-cell strip of trace t: the highlighted span's cells plus the
            picked slice (mapped from the drawer's SPARK_W cells). None when nothing is highlighted."""
            cells = set()
            sp = self.selected_span(t)
            if sp is not None and sp["kind"] != "turn":
                rng = span_cell_range(sp, width, t0, t_end, now)
                if rng:
                    cells.update(range(rng[0], rng[1] + 1))
            if self.slice_sel is not None:
                cells.update(slice_cells(self.slice_sel, width))
            return cells or None

        @staticmethod
        def spans_in_slice(t, s, t0, t_end, now):
            """Non-root spans (waterfall order) whose drawer-strip cells include slice s."""
            out = []
            for sp, _ in span_depths(t["spans"]):
                if sp["kind"] == "turn":
                    continue
                rng = span_cell_range(sp, SPARK_W, t0, t_end, now)
                if rng and rng[0] <= s <= rng[1]:
                    out.append(sp)
            return out

        def drawer_label(self, t, now):
            """`label  H-id` / `t0 → now|t_end  N spans  ● live  ✖ n` / the trace's own strip (timeline_cells at
            SPARK_W) with the shared duration at its right — the same extent, clock and duration the header prints."""
            t0, t_end = trace_extent(t, now)
            head = Text.assemble((t["label"][:22], "bold"), ("  %s" % (t["hypothesis"] or ""), "cyan"))
            # live = bold (the Sessions state style), never green: green is the script class everywhere else in this view
            head.append("\n  %s → %s  %d spans%s" % (fmt_clock(t0), "now" if t["in_progress"] else fmt_clock(t_end), len(t["spans"]),
                                                       "  ● live" if t["in_progress"] else ""), style=STATE_STYLE["working"] if t["in_progress"] else "dim")
            if t["errors"]:
                head.append("  ✖ %d" % t["errors"], style="bold red")
            hl = self.strip_highlight(t, SPARK_W, t0, t_end, now) if t["trace_id"] == self.selected else None
            head.append("\n  ")
            for text, style in coalesce(timeline_cells(t, SPARK_W, t0, t_end, now, hl)):
                head.append(text, style=style)
            head.append("  %s" % fmt_dur(t_end - t0), style="dim")
            return head

        def compose(self) -> ComposeResult:
            # center is a selectable waterfall (one ListItem per span) so the inspector follows the cursor
            with Horizontal():
                with Vertical(id="drawer"):
                    yield Input(placeholder=self.PLACEHOLDER, id="search")
                    yield ListView(id="list")
                with Vertical(id="center"):
                    yield Static("", id="wf-head")
                    yield Static("", id="wf-axis")
                    yield ListView(id="spans")
                yield VerticalScroll(Static("", id="inspector"))

        # events ----------------------------------------------------------------------------------
        def on_list_view_highlighted(self, ev):
            if ev.item is None or getattr(ev.item, "key", None) is None:
                return
            if ev.list_view.id == "spans":
                if ev.item.key != self.span_sel:
                    self.span_sel = ev.item.key
                    self.refresh_rows()
                    self.refresh_label(self.selected)
                self.render_inspector()
            elif ev.list_view.id == "list":
                if ev.item.key != self.selected:
                    prev = self.selected
                    self.selected, self.span_sel, self.slice_sel, self._wf_sig = ev.item.key, None, None, None
                    self.render_detail()
                    if prev is not None:
                        self.refresh_label(prev)                # drop the old row's cross-highlight
                    self.refresh_label(self.selected)

        def on_list_view_selected(self, ev):
            # enter on the drawer with a slice picked: select the first span running in that slice
            if ev.list_view.id == "list" and self.slice_sel is not None:
                self.select_first_in_slice()

        def select_first_in_slice(self):
            t = self.current_trace()
            if not t or self.slice_sel is None or not self._wf:
                return
            in_it = self.spans_in_slice(t, self.slice_sel, self._wf[3], self._wf[4], self._wf[5])
            if not in_it:
                return
            spans_lv = self.query_one("#spans", ListView)
            keys = [getattr(li, "key", None) for li in self.live_children(spans_lv)]
            if in_it[0]["span_id"] in keys:
                spans_lv.index = keys.index(in_it[0]["span_id"])      # Highlighted -> span_sel

        def move_slice(self, delta):
            """←/→: move the picked drawer-strip slice; ▌ marks the span rows running in it and the first one
            scrolls into view (the highlight does not move until enter)."""
            if self.slice_sel is None:
                self.slice_sel = 0 if delta > 0 else SPARK_W - 1
            else:
                self.slice_sel = max(0, min(SPARK_W - 1, self.slice_sel + delta))
            self.refresh_rows()
            self.refresh_label(self.selected)
            self.render_inspector()
            t = self.current_trace()
            if t and self._wf:
                in_it = self.spans_in_slice(t, self.slice_sel, self._wf[3], self._wf[4], self._wf[5])
                if in_it:
                    spans_lv = self.query_one("#spans", ListView)
                    li = next((li for li in self.live_children(spans_lv) if getattr(li, "key", None) == in_it[0]["span_id"]), None)
                    if li is not None:
                        spans_lv.scroll_to_widget(li, animate=False)

        def toggle_help(self):
            self.show_help = not self.show_help
            self.render_detail()

        def current_trace(self):
            return next((x for x in self.items if x["trace_id"] == self.selected), None)

        # center ----------------------------------------------------------------------------------
        def render_detail(self):
            now = time.time()
            head, axis, spans_lv = self.query_one("#wf-head", Static), self.query_one("#wf-axis", Static), self.query_one("#spans", ListView)
            t = self.current_trace()
            if not t:
                head.update(Text("no trace selected", style="dim"))
                axis.update("")
                spans_lv.clear()
                self._wf_sig, self._wf = None, None
                self.query_one("#inspector", Static).update("")
                return
            center_w = self.query_one("#center").size.width or 80
            name_w = max(18, min(32, int(center_w * 0.35)))
            # name column | 8-cell duration | ▌ gutter mark | │ | bars; 3 for the ListView scrollbar/padding
            width = max(12, center_w - name_w - 9 - 4)
            t0, t_end = trace_extent(t, now)
            head.update(self.head_text(t, t0, t_end))
            axis.update(self.axis_text(t, width, name_w, t0, t_end))
            rows = waterfall_rows(t, width, now)
            self._wf = (rows, width, name_w, t0, t_end, now, t)
            keys = tuple(sp["span_id"] for _, sp, _, _, _ in rows)
            if keys != self._wf_sig:
                # the key sequence changed: clear and rebuild in order, restore the highlight by key (never move_child)
                self._wf_sig, self._span_pending = keys, rows
                self.run_worker(self._rebuild_spans(), group="spans-list", exclusive=True, exit_on_error=False)
            else:
                self.refresh_rows()                                   # bars grow / marks move: update in place
            self.render_inspector()

        async def _rebuild_spans(self):
            spans_lv = self.query_one("#spans", ListView)
            rows = self._span_pending
            await spans_lv.clear()
            new = []
            for row in rows:
                li = ListItem(Static(self.span_row_text(row)))
                li.key = row[1]["span_id"]
                new.append(li)
            if new:
                await spans_lv.extend(new)
            keys = [r[1]["span_id"] for r in rows]
            if tuple(keys) != self._wf_sig:
                return                                                # a newer rebuild took over
            if self.span_sel in keys:
                spans_lv.index = keys.index(self.span_sel)

        def refresh_rows(self):
            if not self._wf:
                return
            rows = self._wf[0]
            lis = self.live_children(self.query_one("#spans", ListView))
            if len(lis) != len(rows):
                return
            for li, row in zip(lis, rows):
                st = self.static_of(li)
                if st is not None:
                    st.update(self.span_row_text(row))

        def head_text(self, t, t0, t_end):
            head = Text.assemble((t["label"], "bold"), "  ", (t["hypothesis"] or "", "cyan"),
                                 "  %s → %s  %s  %d spans" % (fmt_clock(t0), "now" if t["in_progress"] else fmt_clock(t_end),
                                                              fmt_dur(t_end - t0), len(t["spans"])))
            if t["errors"]:
                head.append("  ✖ %d" % t["errors"], style="bold red")
            return head

        def legend_runs(self, avail):
            """Two lines of legend runs, each padded to `avail` cells: named entries as they fit (class colours
            first so they are never the ones dropped), then glyph-only entries and [?] on the second line."""
            lines, used, cur, i = [[], []], 0, 0, 0
            entries = list(self.LEGEND)
            while i < len(entries):
                glyph, name, style = entries[i]
                w = len(glyph) + len(name) + 3
                if used + w > avail - (4 if cur == 1 else 0):
                    if cur == 1:
                        break
                    lines[0].append((" " * (avail - used), ""))
                    cur, used = 1, 0
                    continue
                lines[cur].append((glyph, style))
                lines[cur].append((" %s  " % name, "dim"))
                used, i = used + w, i + 1
            if i < len(entries):
                for glyph, _, style in entries[i:]:
                    if used + len(glyph) + 1 > avail - 4:
                        break
                    lines[1].append((glyph + " ", style))
                    used += len(glyph) + 1
                if used + 3 <= avail:
                    lines[1].append(("[?]", "dim"))
                    used += 3
            lines[cur if i >= len(entries) else 1].append((" " * max(0, avail - used), ""))
            if cur == 0:
                lines[1].append((" " * avail, ""))
            return lines

        def axis_text(self, t, width, name_w, t0, t_end):
            """Legend (left, under the name/duration columns) beside the two shared-axis lines over the bars;
            the HELP paragraphs under them while '?' is on."""
            left_w = name_w + 9
            ticks, labels = axis_ticks(t0, t_end, width, t["in_progress"])
            legend = self.legend_runs(left_w)
            out = Text()
            for runs, right in ((legend[0], ticks), (legend[1], labels)):
                for text, style in runs:
                    out.append(text, style=style)
                out.append(" ")                                       # the │ column of the rows
                for text, style in right:
                    out.append(text, style=style)
                out.append("\n")
            if self.show_help:
                for line in self.HELP:
                    out.append(line + "\n", style="dim")
            out.rstrip()
            return out

        def span_row_text(self, row):
            """One waterfall row. Turn root: name, duration, then the trace's timeline_cells at pane width (no █
            bar). Others: name in the span's class colour (italic while running, bold red on error), duration
            dim, ▌ when the span runs in the picked slice, │, then the class-coloured bar — ▶ end cell while
            running (never green-for-running), ✖ red end cell on error."""
            depth, sp, off, ln, dur = row
            _, width, name_w, t0, t_end, now, t = self._wf
            if sp["kind"] == "turn":
                line = Text("%-*s" % (name_w, sp["name"][:name_w - 1]), style="bold", no_wrap=True, overflow="crop")
                line.append("%8s" % fmt_dur(dur), style="dim")
                line.append(" ")
                line.append("│", style="dim")
                for text, style in coalesce(timeline_cells(t, width, t0, t_end, now, self.strip_highlight(t, width, t0, t_end, now))):
                    line.append(text, style=style)
                return line
            color = CLASS_COLOR.get(span_class(sp), "")
            in_slice = self.slice_sel is not None and sp in self.spans_in_slice(t, self.slice_sel, t0, t_end, now)
            name = ("  " * depth + sp["name"])[:name_w - 1]
            name_style = "bold red" if sp.get("error") else (color + (" italic" if sp["in_progress"] else "")).strip()
            line = Text("%-*s" % (name_w, name), style=name_style, no_wrap=True, overflow="crop")
            line.append("%8s" % fmt_dur(dur), style="dim")
            line.append("▌" if in_slice else " ", style="bold " + SPIKE_COLOR["stop"])
            line.append("│", style="dim")
            line.append(" " * off)
            if sp["in_progress"]:
                line.append("█" * max(0, ln - 1), style=color)
                line.append("▶", style=("bold " + color).strip())
            elif sp.get("error"):
                line.append("█" * max(0, ln - 1), style=color)
                line.append("✖", style="bold " + SPIKE_COLOR["error"])
            else:
                line.append("█" * ln, style=color)
            return line

        # inspector -------------------------------------------------------------------------------
        def window_pairs(self, t, span, t0, t_end, now):
            """The 'window' block: the picked slice (range, count, spans in it) or the highlighted span's strip
            cells, plus a textual equivalent of the row for screen readers."""
            per = max(1e-6, t_end - t0) / float(SPARK_W)
            if self.slice_sel is not None:
                s = self.slice_sel
                lo, hi = t0 + s * per, t0 + (s + 1) * per
                in_it = self.spans_in_slice(t, s, t0, t_end, now)
                names = ", ".join(sp["name"] for sp in in_it[:12]) + (" …" if len(in_it) > 12 else "")
                glyph, style = timeline_cells(t, SPARK_W, t0, t_end, now)[s]
                word = next((self.CLASS_WORD[k] for k, v in CLASS_COLOR.items() if v == style.split()[0]), None)
                row = "cell %d of %d, %s → %s: %d span%s running%s%s" % (
                    s + 1, SPARK_W, fmt_clock(lo), fmt_clock(hi), len(in_it), "" if len(in_it) == 1 else "s",
                    (", mostly " + word) if word else "", (": " + names) if names else "")
                return [("slice", "%d of %d" % (s + 1, SPARK_W)), ("range", "%s → %s  (%s)" % (fmt_clock(lo), fmt_clock(hi), fmt_dur(per))),
                        ("count", len(in_it)), ("spans", names or "none"), ("row", row)]
            if span["kind"] != "turn":
                rng = span_cell_range(span, SPARK_W, t0, t_end, now)
                cells = ("%d–%d of %d" % (rng[0] + 1, rng[1] + 1, SPARK_W)) if rng else "outside the strip"
                st = span["start"] if span["start"] is not None else t0
                en = min(t_end, span["end"] if span["end"] is not None else now)
                state = "still running" if span["in_progress"] else ("ended in error" if span.get("error") else "ended")
                row = "%s, %s, %s from %s, %s; strip cells %s" % (span["name"], self.CLASS_WORD.get(span_class(span), "-"),
                                                                  fmt_dur(en - st), fmt_clock(st), state, cells)
                return [("cells", cells), ("row", row)]
            return []

        def render_inspector(self):
            now = time.time()
            t = self.current_trace()
            insp = self.query_one("#inspector", Static)
            if not t:
                insp.update("")
                return
            t0, t_end = trace_extent(t, now)
            span = self.selected_span(t) or t["spans"][0]
            st = span["start"] if span["start"] is not None else t0
            en = min(t_end, span["end"] if span["end"] is not None else now)
            pairs = [("span", span["name"]), ("kind", span["kind"]), ("class", span_class(span) or "-"), ("start", fmt_clock(span["start"])),
                     ("end", fmt_clock(span["end"]) if span["end"] else "in progress"), ("duration", fmt_dur(en - st)),
                     ("parent", span["parent"] or "(root)")] + sorted((k, v) for k, v in span["attrs"].items() if k not in self.DUP_ATTRS)
            res = [("session.id", t["session_id"]), ("session.name", t["label"]), ("repo", t["repo"]), ("hypothesis", t["hypothesis"]), ("source", t["source"])]
            blocks = [kvtext("span", pairs)]
            win = self.window_pairs(t, span, t0, t_end, now)
            if win:
                blocks += ["\n", kvtext("window", win, "yellow")]
            blocks += ["\n", kvtext("resource", res, "magenta")]
            insp.update(Text.assemble(*blocks))

    class LogsPane(SignalPane):
        PLACEHOLDER = "search logs: any field"

        def rebuild(self):
            sessions = self.store.sessions()
            rows = []
            for i, r in enumerate(self.store.events[-3000:]):
                s = sessions.get(r.get("session_id")) or {}
                name, repo, _ = session_label(s) if s else ("", os.path.basename(r.get("cwd") or "") or "?", "-")
                rows.append(dict(r, _idx=i, _who=name or repo, _repo=repo, _text=" ".join(str(v) for v in r.values() if isinstance(v, str))))
            rows = filter_text(rows, self.query, ("_who", "_repo", "event_name", "tool_name", "agent_type", "_text"))[-500:]
            rows.reverse()
            self.items = rows

            def label(r):
                lvl = "ERROR" if event_kind(r) == "error" else "INFO"
                t = Text.assemble((fmt_clock(r.get("arrival_ts_unix")), "dim"), "  ", (lvl, "bold red" if lvl == "ERROR" else "green"), "  ", (r["_who"][:18], "bold"))
                t.append("\n  %s %s%s" % (r.get("hook_event_name") or r.get("event_name") or "?", r.get("tool_name") or "", ("  ↳ " + r["agent_type"]) if r.get("agent_type") else ""), style="dim")
                return t
            # stable key: arrival time + session + event (the old _idx was a slice position and changed every rebuild)
            self.set_list(rows, lambda r: "%s:%s:%s" % (r.get("arrival_ts_unix"), (r.get("session_id") or "")[:8], r.get("hook_event_name") or r.get("event_name")), label)

        def render_detail(self):
            center, insp = self.query_one("#center", Static), self.query_one("#inspector", Static)
            r = next((x for x in self.items if "%s:%s" % (x.get("arrival_ts_unix"), x.get("_idx")) == self.selected), None)
            if not r:
                center.update(Text("no log record selected", style="dim"))
                insp.update("")
                return
            body = Text()
            body.append("%s\n" % (r.get("event_name") or "?"), style="bold")
            body.append("%s  arrival %s  latency %s\n\n" % (fmt_clock(r.get("event_ts_unix")), fmt_clock(r.get("arrival_ts_unix")),
                                                          ("%.0f ms" % ((r["arrival_ts_unix"] - r["event_ts_unix"]) * 1000)) if r.get("event_ts_unix") else "-"), style="dim")
            body.append(r.get("body") or "", style="")
            if r.get("tool_summary"):
                body.append("\n\ntool summary: %s" % r["tool_summary"], style="dim")
            center.update(body)
            attrs = [(k, v) for k, v in sorted(r.items()) if not k.startswith("_") and k not in ("body", "resource", "attributes", "span_events")
                     and v not in (None, "")]
            insp.update(Text.assemble(kvtext("attributes", attrs), "\n",
                                      kvtext("resource", [("session.name", r["_who"]), ("repo", r["_repo"]), ("cwd", r.get("cwd"))] + sorted((r.get("resource") or {}).items()), "magenta")))

    class MetricsPane(SignalPane):
        PLACEHOLDER = "search metrics: name"

        def rebuild(self):
            now = time.time()
            # only the events inside the window can contribute; slicing them first is what keeps the
            # cold first render under a second (critique: '4' looked dead for >1 s over 28k events)
            start = now - self.store.window_s
            events = self.store.events
            lo = 0
            for lo in range(len(events) - 1, -1, -1):
                if (events[lo].get("arrival_ts_unix") or 0) < start:
                    lo += 1
                    break
            self.streams = metric_streams(events[lo:], [s for s in self.store.samples if s[0] >= start], now, self.store.window_s,
                                          sessions=self.store.sessions())
            rows = filter_text(list(self.streams.values()), self.query, ("name",))
            self.items = rows

            def label(st):
                t = Text.assemble((st["name"], "bold"), ("  %s" % st["kind"], "dim"))
                t.append("\n  " + sparkline(st["values"], 28), style="blue")
                t.append("  %s%s" % (("%.1f" % st["current"]) if st["current"] < 1000 else ("%.0f" % st["current"]), st["unit"] if st["unit"] != "1" else ""), style="dim")
                return t
            self.set_list(rows, lambda st: st["name"], label)

        def render_detail(self):
            center, insp = self.query_one("#center", Static), self.query_one("#inspector", Static)
            st = self.streams.get(self.selected) if getattr(self, "streams", None) else None
            if not st:
                center.update(Text("no metric selected", style="dim"))
                insp.update("")
                return
            now = time.time()
            width = max(20, (self.query_one("#center").size.width or 80) - 12)
            out = Text()
            out.append("%s  (%s, %s)  window %ds\n\n" % (st["name"], st["kind"], st["unit"] or "-", self.store.window_s), style="bold")
            hi = max(st["values"]) or 1.0
            step = max(1, len(st["values"]) // width) if len(st["values"]) > width else 1
            vals = st["values"][::step][-width:]
            for level in range(8, 0, -1):     # 8-row column chart
                thr = hi * (level - 0.5) / 8.0
                out.append("%8s │" % (("%.1f" % (hi * level / 8.0)) if level in (8, 4, 1) else ""), style="dim")
                out.append("".join("█" if v >= thr else " " for v in vals) + "\n", style="blue")
            out.append("%8s └" % "" + "─" * len(vals) + "\n", style="dim")
            out.append("%10s%s\n" % ("", timeline_axis(now, self.store.window_s, len(vals))), style="dim")
            if st["series"]:
                out.append("\nseries (top 8)\n", style="bold")
                for lbl, vs in sorted(st["series"].items(), key=lambda kv: -sum(kv[1]))[:8]:
                    out.append("  %-28s %s  %s\n" % (lbl[:28], sparkline(vs, 30), ("%.1f" % sum(vs)) if st["kind"] == "sum" else ("%.1f" % max(vs))), style="")
            center.update(out)
            pairs = [("kind", st["kind"]), ("unit", st["unit"]), ("current", "%.2f" % st["current"]), ("peak (bucket)", "%.2f" % st["peak"]),
                     ("total (window)", ("%.2f" % st["total"]) if st["total"] is not None else "-"), ("series", len(st["series"])), ("buckets", len(st["values"]))]
            insp.update(kvtext("aggregates", pairs))

    class SessionsPane(Vertical):
        """The Agent-View-style board, hosted as the first mode."""
        DEFAULT_CSS = "SessionsPane {height: 1fr;} SessionsPane #board {width: auto;}"

        def compose(self) -> ComposeResult:
            yield VerticalScroll(Static("", id="board"))

    class Viewer(Board):
        """Reuses Board's store (state/events/rows/collapse) and adds the three signal panes."""
        TITLE = "Claude observatory"
        CSS = ("#modes {height: 1; background: $panel;} #totals {height: 1; background: $panel;} "
               "ContentSwitcher {height: 1fr;}")
        # Mode keys are priority so they fire from any focus (the browser terminal delivers keys via
        # xterm.js and focus lands on containers), but check_action releases them while an Input has
        # focus so "1".."4" still type into the search box. Escape leaves the search box.
        BINDINGS = Board.BINDINGS + [Binding("1", "mode('sessions')", "Sessions", priority=True),
                                     Binding("2", "mode('traces')", "Traces", priority=True),
                                     Binding("3", "mode('logs')", "Logs", priority=True),
                                     Binding("4", "mode('metrics')", "Metrics", priority=True),
                                     Binding("slash", "focus_search", "Search", priority=True),
                                     Binding("t", "open_traces", "Traces of session", priority=True),
                                     Binding("left,h", "slice(-1)", "Slice left", show=False, priority=True),
                                     Binding("right,l", "slice(1)", "Slice right", show=False, priority=True),
                                     Binding("escape", "blur_search", "Leave search", show=False, priority=True)]

        def __init__(self, state_file, events_path, initial="sessions"):
            super().__init__(state_file, events_path)
            self.mode, self.samples = initial, []

        def action_blur_search(self):
            # escape: leave the search box first; if it was not focused, drop the session filter instead
            if isinstance(self.focused, Input):
                self.set_focus(None)
            elif self.mode == "traces":
                self.action_clear_trace_filter()

        def check_action(self, action, parameters):
            # While the search Input has focus, every letter/digit binding yields to typing (escape
            # and slash stay). In the signal modes the Board's cursor keys belong to the focused list;
            # the Sessions view has no search box, so hide that key there (dead affordance otherwise).
            if isinstance(self.focused, Input) and action in ("mode", "quit", "cycle_filter", "cycle_window", "help",
                                                              "move", "toggle", "page", "home", "end", "open_traces", "slice"):
                return False
            if self.mode != "sessions" and action in ("move", "toggle", "page", "home", "end", "open_traces"):
                return False
            if action == "help" and self.mode not in ("sessions", "traces"):
                return False
            if action == "slice" and self.mode != "traces":       # ←/→ pick a drawer-strip slice in Traces only
                return False
            if self.mode == "sessions" and action in ("focus_search", "blur_search"):
                return False
            return True

        def action_help(self):
            if self.mode == "traces":
                self.query_one("#traces").toggle_help()
            else:
                super().action_help()

        def action_slice(self, delta):
            if self.mode == "traces":
                self.query_one("#traces").move_slice(delta)

        def sessions(self):
            return (self.state or {}).get("sessions") or {}

        def compose(self) -> ComposeResult:
            yield Static("", id="modes")      # the mode strip is the only chrome row; no Header
            with ContentSwitcher(initial=self.mode):
                yield SessionsPane(id="sessions")
                yield TracesPane(self, id="traces")
                yield LogsPane(self, id="logs")
                yield MetricsPane(self, id="metrics")
            yield Static("starting...", id="totals")
            yield Footer()

        def on_mount(self):
            self.refresh_state()
            self.tail_events()
            self.render_board()
            self.render_modes()
            self.rebuild_active()                    # `--view traces|logs|metrics` renders at once, not after the first tick
            self.set_interval(0.5, self.refresh_state)
            self.set_interval(0.5, self.tail_events)
            self.set_interval(1.0, self.render_board)
            self.set_interval(1.0, self.rebuild_active)

        def refresh_state(self):
            before = (self.state or {}).get("generated_at")
            super().refresh_state()
            st = self.state or {}
            if st and st.get("generated_at") != before:
                self.samples.append((time.time(), st.get("sessions_live", 0), st.get("sessions_total", 0)))
                if len(self.samples) > 20000:
                    self.samples = self.samples[-20000:]

        def action_mode(self, mode):
            prev = self.mode
            self._dirty = True
            self.mode = mode
            self.query_one(ContentSwitcher).current = mode
            self.render_modes()
            self.rebuild_active()
            if mode == "sessions" and prev == "traces":
                # return path: put the board cursor on the session whose trace was selected
                pane = self.query_one("#traces")
                t = pane.current_trace()
                if t:
                    self.render_board()
                    for i, row in enumerate(self.rows):
                        if row["key"] == "sess:" + t["session_id"]:
                            self.cursor = i
                            self._dirty = True
                            self.render_board()
                            self.keep_cursor_visible()
                            break

        def action_open_traces(self):
            """Sessions view: 't' (or double-click) on a session or sub-agent row opens Traces filtered to
            that session, newest trace selected — the lane marks under the cursor are that trace's spans."""
            if self.mode != "sessions" or not self.rows:
                return
            row = self.rows[self.cursor]
            sid = None
            if row["kind"] == "session":
                sid = row["key"][len("sess:"):]
            elif row["kind"] in ("agent", "more"):
                sid = row["key"].split(":")[1]
            if not sid:
                return
            pane = self.query_one("#traces")
            pane.session_filter, pane.selected, pane.span_sel, pane._sig, pane._wf_sig = sid, None, None, None, None
            self._pane_sig = None
            self.action_mode("traces")

        def action_clear_trace_filter(self):
            pane = self.query_one("#traces")
            if pane.session_filter:
                pane.session_filter, pane._sig = None, None
                self._pane_sig = None
                pane.rebuild()
                return True
            return False

        def action_focus_search(self):
            if self.mode != "sessions":
                self.query_one("#" + self.mode).query_one(Input).focus()

        def render_modes(self):
            t = Text()
            self._mode_spans = []                      # (x0, x1, key) so a click on the strip switches mode
            x = 0
            for key, lbl in MODES:
                cell = " %s " % lbl
                self._mode_spans.append((x, x + len(cell), key))
                t.append(cell, style="reverse bold" if key == self.mode else "")
                t.append("  ")
                x += len(cell) + 2
            t.append("   Claude observatory", style="dim")
            self.query_one("#modes", Static).update(t)

        def on_click(self, ev):
            # y == 0 is the mode strip (it is the first row of the screen)
            if ev.screen_y == 0:
                for x0, x1, key in getattr(self, "_mode_spans", []):
                    if x0 <= ev.screen_x < x1:
                        self.action_mode(key)
                        return
            if self.mode == "sessions":
                idx = ev.y - 2
                if getattr(ev, "chain", 1) >= 2 and 0 <= idx < len(self.rows):   # double-click: open traces
                    self.cursor = idx
                    self.action_open_traces()
                    return
                super().on_click(ev)

        def rebuild_active(self):
            if self.mode == "sessions":
                return
            # only rebuild when new events arrived or the user changed something (search/window/mode)
            sig = (self.mode, len(self.events), self.window_s, (self.state or {}).get("generated_at"))
            if sig == getattr(self, "_pane_sig", None):
                return
            self._pane_sig = sig
            pane = self.query_one("#" + self.mode)
            pane.rebuild()

        def render_board(self):
            if self.mode == "sessions":                  # the sessions board is invisible in the other modes
                super().render_board()

        def _board_static(self):
            try:
                return self.query_one("#board", Static)
            except Exception:
                return None

        def action_toggle(self):
            if self.mode == "sessions":
                super().action_toggle()

        def action_move(self, delta):
            if self.mode == "sessions":
                super().action_move(delta)

        def action_cycle_window(self):
            super().action_cycle_window()
            self._pane_sig = None
            self.rebuild_active()

    return Viewer


def cmd_board(args):
    view = getattr(args, "view", "sessions")
    if view == "board":
        build_board_class()(args.state_file, args.events).run()
    else:
        build_viewer_class()(args.state_file, args.events, initial=view).run()
    return 0


def board_command(args):
    """The command textual-serve spawns per browser tab. `uv run` resolves the inline deps."""
    if args.runner == "python" or (args.runner == "auto" and not shutil.which("uv")):
        runner = "%s %s" % (sys.executable, THIS_FILE)
    else:
        runner = "%s run %s" % (shutil.which("uv") or "uv", THIS_FILE)
    return "%s board --state-file %s --events %s --view %s" % (runner, args.state_file, args.events, getattr(args, "view", "sessions"))


def cmd_web(args):
    from textual_serve.server import Server  # noqa: PLC0415
    cmd = board_command(args)
    eprint("observatory web on http://%s:%d  serving: %s" % (args.host, args.port, cmd))
    Server(cmd, host=args.host, port=args.port, title="Claude observatory").serve()
    return 0


# --------------------------------------------------------------------------- CLI
def build_parser():
    ap = argparse.ArgumentParser(prog="observatory.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="receiver: OTLP/JSON + /hook -> events JSONL + state JSON")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--state-file", default=DEFAULT_STATE, help="state JSON (default %s)" % DEFAULT_STATE)
    p.add_argument("--events", default=DEFAULT_EVENTS, help="events JSONL (default %s)" % DEFAULT_EVENTS)
    p.add_argument("--registry-interval", type=int, default=5, help="seconds between `claude agents --json` polls (min 5; 0 disables)")
    p.add_argument("--spool", default=DEFAULT_SPOOL, help="tail this spool file written by the /bin/sh hooks (default %s; '' disables)" % DEFAULT_SPOOL)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("hook", help="Claude Code hook entrypoint (stdin JSON -> OTLP/JSON -> serve)")
    p.add_argument("--port", type=int, required=True)
    p.set_defaults(fn=cmd_hook)

    for name, remove in (("install-hooks", False), ("uninstall-hooks", True)):
        p = sub.add_parser(name)
        p.add_argument("--scope", choices=["project", "user"], default="project")
        p.add_argument("--settings", default=None, help="explicit settings.json path (overrides --scope)")
        if not remove:
            p.add_argument("--transport", choices=["spool", "post"], default="spool",
                           help="spool (default): sync /bin/sh append to --spool, lossless; post: async python POST to --port")
            p.add_argument("--spool", default=None, help="spool file path (default %s)" % DEFAULT_SPOOL)
            p.add_argument("--sync", action="store_true", help="spool hooks synchronous (lossless alone, ~100 ms per firing on macOS); default async + transcript backfill")
            p.add_argument("--port", type=int, default=None, help="receiver port (required for --transport post)")
        p.set_defaults(fn=lambda a, r=remove: cmd_install_hooks(a, remove=r))

    for name, fn in (("board", cmd_board), ("web", cmd_web)):
        p = sub.add_parser(name)
        p.add_argument("--state-file", default=DEFAULT_STATE, help="state JSON (default %s)" % DEFAULT_STATE)
        p.add_argument("--events", default=DEFAULT_EVENTS, help="events JSONL (default %s)" % DEFAULT_EVENTS)
        p.add_argument("--view", choices=["sessions", "traces", "logs", "metrics", "board"], default="sessions",
                       help="initial mode; 'board' = the sessions view alone without the signal panes")
        if name == "web":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8820)
            p.add_argument("--runner", choices=["auto", "uv", "python"], default="auto")
        p.set_defaults(fn=fn)

    p = sub.add_parser("dump-state")
    p.add_argument("--state-file", default=DEFAULT_STATE, help="state JSON (default %s)" % DEFAULT_STATE)
    p.set_defaults(fn=cmd_dump_state)

    p = sub.add_parser("ratios", help="recompute leverage/determinism blocks from the events JSONL (deterministic JSON)")
    p.add_argument("--events", default=DEFAULT_EVENTS, help="events JSONL (default %s)" % DEFAULT_EVENTS)
    p.add_argument("--session", default=None, help="restrict to one session id")
    p.set_defaults(fn=cmd_ratios)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
