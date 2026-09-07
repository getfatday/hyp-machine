#!/usr/bin/env python3
"""selftest-observatory.py -- regression test for scripts/observatory.py (the live, hook-fed board).

Loads the INSTALLED plugin's scripts/observatory.py (the tree this file lives in), builds every
fixture under a temp dir, binds only free ephemeral ports (never 4318, 4319 or 8820), writes
nothing outside the temp dir, never opens the real user settings file, and prints one PASS/FAIL
line per check:

  hook-subcommand-emits-one-otlp-record        stdin hook JSON -> one classified event via POST /v1/logs
  hook-subcommand-never-fails-without-receiver garbage stdin, nothing listening -> exit 0, silent
  tool-summary-never-leaks-content             tool_response content never reaches the payload
  op-token-table-and-no-argument-leak          one program token per call, never an argument
  catalog-classifies-against-the-repo          classes + nodes from the per-repo catalog, ratios, unmodeled_top
  ratios-hand-values-and-subcommand-byte-stable hand-computed leverage/determinism; `ratios` byte-identical x2
  serve-state-has-session-and-survives-garbage receiver survives malformed bodies, state has the session
  transcript-join-most-recent-hid-wins         transcript under CLAUDE_CONFIG_DIR/projects; cwd fallback
  agents-phase-and-timeline-lanes              sub-agent phases, K lane cells, board rows, ranking
  traces-logs-metrics-read-models              turn traces, span nesting, OTLP spans/metrics, streams
  scan-hids-ignores-the-observatorys-own-path  the installed hook command line never labels a session
  otel-modules-stay-unloaded                   the JSON path never imports opentelemetry
  install-uninstall-roundtrip-preserves-keys   post transport: nine async entries, idempotent, exact restore
  posttoolusefailure-counts-as-error-outcome   a failed call is counted once, outcome error
  spool-transport-install-and-tail             /bin/sh append really works; spool_records; ingest classifies
  backfill-synthesises-only-the-missing-records transcript backfill adds exactly the lost records
  backfill-refuses-sessions-it-never-hooked    registry-only sessions are never reconstructed
  timeline-cells-bins-every-span-by-class      timeline cells, highlight/drain, waterfall, axis ticks
  trace-extent-stopped-vs-live                 extent pairs, fmt_dur, span_cell_range
  chain-aware-op-tokens-and-catalog-patches    first catalog hit along a chain; op.names never carry arguments
  chain-tokens-heredoc-newline-relative-cd     heredoc bodies ignored; relative cd stops counting
  roadmap-metrics-coverage-failed-routes       model_coverage, failed_routes, suggested_node, handoff gauge
  chain-tokens-resolve-cd-against-cwd          every cd resolved against the cwd; absolute scripts accepted
  timeout-wrapper-skips-its-duration           `timeout <dur>` is a wrapper
  chain-tokens-resolve-vars-reject-outside     $VAR paths resolved; scripts outside the toplevel rejected
  hyp-json-overrides-catalog-and-titles        .claude/hyp.json model_dir / hypotheses_dir / preflight_file are honoured
  defaults-under-data-dir                      --state-file/--events/--spool default under data_dir(); CLAUDE_CONFIG_DIR honoured
  stdlib-import-only-and-header                fresh interpreter import loads no textual/rich/protobuf; header names provenance
  no-lab-paths                                 neither shipped file names a lab or home path

Usage: python3 scripts/selftest-observatory.py        exit 0 = all PASS, 1 = any FAIL
Standard library only, Python 3.9. The seven Textual board checks of the source lab's fixture
test_observatory.py (selection survives refresh, session-to-traces link, search keys, one row per
session, drawer highlight, set-list rebuild, cross-highlight) need pytest + textual 8.2.8 and stay
lab-only: `uv run --with pytest --with textual==8.2.8 pytest -q` in the fixture directory.
Provenance: the 25 non-TUI tests of that fixture (H-DRAFT-2c1fc974, -fcf7b3fa x2, -2652d478), ported
with sys.executable for the interpreter and CLAUDE_CONFIG_DIR for the home override, plus four
plugin-specific checks.
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBS = os.path.join(PLUGIN, "scripts", "observatory.py")
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "scripts"))
import hyp_config  # noqa: E402

_spec = importlib.util.spec_from_file_location("observatory", OBS)
obs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(obs)

PY = sys.executable
SID = "11111111-2222-4333-8444-555555555555"
RESERVED_PORTS = {4318, 4319, 8820}          # the counted lanes' isolation rule: never bind these here
HYP_DIR = hyp_config.DEFAULTS["hypotheses_dir"]
MODEL_DIR = hyp_config.DEFAULTS["model_dir"]
PREFLIGHT_DIR = os.path.dirname(hyp_config.DEFAULTS["preflight_file"])
RESULTS = []


def free_port():
    for _ in range(100):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p not in RESERVED_PORTS:
            return p
    raise RuntimeError("no free port outside the reserved set")


def post(url, body, ctype="application/json"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@contextlib.contextmanager
def served(tmp):
    """A Model + receiver on a free port, registry polling disabled."""
    model = obs.Model(str(tmp / "events.jsonl"), registry_interval=0)
    port = free_port()
    srv = obs.start_receiver(model, "127.0.0.1", port)
    try:
        yield model, port
    finally:
        srv.shutdown()


@contextlib.contextmanager
def env(**kw):
    saved = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def quiet(fn, *a, **kw):
    """Run fn with stdout captured (install/uninstall print their report)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


# --------------------------------------------------------------------------- fixtures shared by checks
def _mini_repo(root):
    """A scratch repo with the miniature operating model from the spec fixtures (default hyp paths)."""
    (root / ".git").mkdir()
    (root / MODEL_DIR / "lab").mkdir(parents=True)
    (root / MODEL_DIR / "lab" / "model.md").write_text(
        "# lab\n- [command/tidy](commands/tidy.md)\n- [command/count](commands/count.md)\n"
        "- [event/session-started](events/session-started.md)\n- [read-model/journal](readmodels/journal.md)\n")
    (root / "skills" / "tidy").mkdir(parents=True)
    (root / "skills" / "tidy" / "SKILL.md").write_text("x")
    (root / "scripts").mkdir()
    (root / "scripts" / "count.py").write_text("x")
    (root / "tools").mkdir()
    (root / "tools" / "mystery.sh").write_text("x")
    (root / "sub" / "dir").mkdir(parents=True)
    return root / "sub" / "dir"


def _rec(now, sid, hook, cwd, **kw):
    r = {"arrival_ts_unix": now, "event_ts_unix": now, "source": "hook", "session_id": sid, "event_name": obs.EVENT_PREFIX + hook,
         "hook_event_name": hook, "cwd": str(cwd)}
    r.update(kw)
    return r


def _rich_turn(sid, base, pid, stop_at=None):
    recs = []

    def rec(hook, t, **kw):
        r = {"arrival_ts_unix": base + t, "event_ts_unix": base + t, "source": "hook", "session_id": sid, "cwd": "/x/repo",
             "event_name": obs.EVENT_PREFIX + hook, "hook_event_name": hook, "prompt_id": pid}
        r.update(kw)
        recs.append(r)
    rec("UserPromptSubmit", 0)
    rec("PreToolUse", 2, tool_name="Bash", tool_use_id=pid + "t1", op_class="modeled-deterministic")
    rec("PostToolUse", 12, tool_name="Bash", tool_use_id=pid + "t1", tool_outcome="ok", op_class="modeled-deterministic")
    rec("SubagentStart", 14, agent_id="ag1", agent_type="Explore")
    rec("PreToolUse", 16, tool_name="Read", tool_use_id=pid + "t2", agent_id="ag1", agent_type="Explore", op_class="unmodeled")
    rec("PostToolUse", 30, tool_name="Read", tool_use_id=pid + "t2", agent_id="ag1", agent_type="Explore", tool_outcome="ok", op_class="unmodeled")
    rec("SubagentStop", 34, agent_id="ag1", agent_type="Explore")
    rec("PreToolUse", 40, tool_name="Skill", tool_use_id=pid + "t3", op_class="modeled-stochastic")
    rec("PostToolUse", 60, tool_name="Skill", tool_use_id=pid + "t3", tool_outcome="error", op_class="modeled-stochastic")
    rec("PreToolUse", 70, tool_name="Bash", tool_use_id=pid + "t4", op_class="unmodeled")
    if stop_at is not None:
        rec("Stop", stop_at)
    return recs


def _cells_of(st, en, span_s, w):
    c0, c1 = min(w - 1, int(st / span_s * w)), min(w - 1, int(en / span_s * w))
    if c1 > c0 and c1 * span_s / w >= en:
        c1 -= 1
    return set(range(c0, c1 + 1))


# --------------------------------------------------------------------------- checks (ported test bodies)
def hook_subcommand_emits_one_otlp_record(tmp):
    with served(tmp) as (model, port):
        hook = {"session_id": SID, "cwd": str(tmp), "hook_event_name": "SessionStart", "source": "startup",
                "transcript_path": str(tmp / "t.jsonl")}
        t0 = time.time()
        r = subprocess.run([PY, OBS, "hook", "--port", str(port)], input=json.dumps(hook),
                           capture_output=True, text=True, timeout=10)
        assert r.returncode == 0 and r.stdout == ""
        assert time.time() - t0 < 4.5
        events = [json.loads(l) for l in open(tmp / "events.jsonl")]
        assert len(events) == 1
        e = events[0]
        assert e["event_name"] == "claude_code.hook.SessionStart" and e["session_id"] == SID
        assert e["source"] == "hook" and e["cwd"] == str(tmp) and e["event_ts_unix"] is not None
        assert isinstance(e["hook_self_ms"], int) and 0 <= e["hook_self_ms"] < 5000
        assert e["op_class"] == "modeled-event" and e["op_node"] == "event/session-started"


def hook_subcommand_never_fails_without_receiver(tmp):
    port = free_port()
    r = subprocess.run([PY, OBS, "hook", "--port", str(port)], input="not json at all",
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout == ""


def tool_summary_never_leaks_content(tmp):
    payload = obs.hook_to_otlp({"session_id": SID, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                                "tool_response": {"success": False, "error": "boom", "content": "X" * 5000}})
    text = json.dumps(payload)
    assert "XXXX" not in text and len(text) < 3000
    a = obs.attrs_to_dict(payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"])
    assert a["tool.outcome"] == "error" and a["event.name"] == "claude_code.hook.PostToolUse"


def op_token_table_and_no_argument_leak(tmp):
    table = [("FOO=1 cd x && uv run scripts/count.py --secret ZEBRA", "count.py"),
             ("uv run --with pytest --with textual==8.2.8 pytest -q", "pytest"),
             ("/usr/bin/python3 observatory.py serve --port 1", "observatory.py"),
             ("python3 -m pytest", "python3"), ("python3 -c 'print(1)'", "python3"),
             ("bash scripts/harden-check.sh", "harden-check.sh"), ("sudo -u root ls -la", "ls"),
             ("env FOO=bar git status", "git"), ("cd /tmp; ls", "ls"), ("git -C /x status && git log", "git"),
             ("./tools/mystery.sh --secret ZEBRA-7731", "mystery.sh"), ("node build.js", "build.js"),
             ("echo hi;", "echo"), ("", None), ("   ", None), ("cd x", None), ("'unterminated quote", "unterminated"),
             ("for f in a b; do python3 scripts/x.py $f; done", "x.py"), ("if true; then git status; fi", "git"),
             ("! grep -q x file", "grep"), ("while read l; do echo $l; done < f", "echo"), ("{ ls; }", "ls"), ("python3 -c 'print(1)' && ls", "python3")]
    for cmd, want in table:
        assert obs.op_token_bash(cmd) == want, cmd
    assert obs.derive_op({"tool_name": "Skill", "tool_input": {"skill": "tidy", "args": "ZEBRA"}}) == "tidy"
    assert obs.derive_op({"tool_name": "Skill", "tool_input": {"skill_name": "tidy"}}) == "tidy"
    assert obs.derive_op({"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "ZEBRA"}}) == "Explore"
    assert obs.derive_op({"tool_name": "Read", "tool_input": {"file_path": "/ZEBRA"}}) == "Read"
    assert obs.derive_op({"tool_name": "Bash", "tool_input": {}}) == "Bash"
    assert obs.derive_op({"hook_event_name": "Stop"}) is None
    hook = {"session_id": SID, "hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": "/x/ZEBRA-dir",
            "tool_input": {"command": "FOO=1 cd x && uv run scripts/count.py --secret ZEBRA", "description": "ZEBRA too"},
            "tool_response": {"stdout": "ZEBRA output", "interrupted": False}}
    payload = obs.hook_to_otlp(hook, self_ms=12.7)
    text = json.dumps(payload)
    assert "ZEBRA" not in text.replace("ZEBRA-dir", "")
    a = obs.attrs_to_dict(payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"])
    assert a["op.name"] == "count.py" and a["hook.self_ms"] == "12"
    flat = list(obs.flatten_logs(payload, time.time()))[0]
    assert flat["op_name"] == "count.py" and flat["hook_self_ms"] == 12


def catalog_classifies_against_the_repo(tmp):
    cwd = _mini_repo(tmp)
    cat = obs.Catalog()
    assert cat.toplevel(str(cwd)) == str(tmp)
    c = cat.get(str(tmp))
    assert c["nodes"] >= {"command/tidy", "command/count", "event/session-started", "read-model/journal"}
    assert c["skills"] == {"tidy"} and c["scripts"] == {"count.py"}
    assert cat.get(str(tmp)) is c
    assert obs.find_toplevel("/") is None and cat.get(None) == obs.EMPTY_CATALOG
    m = obs.Model(str(tmp / "events.jsonl"), registry_interval=0)
    now = time.time()
    m.ingest([_rec(now, "S1", "SessionStart", cwd), _rec(now, "S1", "UserPromptSubmit", cwd),
              _rec(now, "S1", "PostToolUse", cwd, tool_name="Skill", op_name="tidy"),
              _rec(now, "S1", "PostToolUse", cwd, tool_name="Bash", op_name="count.py"),
              _rec(now, "S1", "PostToolUse", cwd, tool_name="Bash", op_name="mystery.sh"),
              _rec(now, "S1", "PostToolUse", cwd, tool_name="Bash", op_name="mystery.sh"),
              _rec(now, "S1", "PostToolUse", cwd, tool_name="Agent", op_name="Explore"),
              _rec(now, "S1", "PostToolUse", cwd, tool_name="Read", op_name="Read"),
              _rec(now, "S1", "PreToolUse", cwd, tool_name="Bash", op_name="count.py"),
              _rec(now, "S1", "Stop", cwd), _rec(now, "S1", "SessionEnd", cwd),
              _rec(now, "S2", "PostToolUse", "/nowhere/at/norepo", tool_name="Skill", op_name="tidy")])
    got = [(r["hook_event_name"], r.get("op_name"), r["op_class"], r["op_node"]) for r in obs.read_events(str(tmp / "events.jsonl"))]
    assert got == [("SessionStart", None, "modeled-event", "event/session-started"),
                   ("UserPromptSubmit", None, "modeled-event", "event/prompt-submitted"),
                   ("PostToolUse", "tidy", "modeled-stochastic", "command/tidy"),
                   ("PostToolUse", "count.py", "modeled-deterministic", "script/count.py"),
                   ("PostToolUse", "mystery.sh", "unmodeled", None), ("PostToolUse", "mystery.sh", "unmodeled", None),
                   ("PostToolUse", "Explore", "delegated", "agent/Explore"),
                   ("PostToolUse", "Read", "unmodeled", None),
                   ("PreToolUse", "count.py", "modeled-deterministic", "script/count.py"),
                   ("Stop", None, "modeled-event", "event/turn-completed"),
                   ("SessionEnd", None, None, None),
                   ("PostToolUse", "tidy", "unmodeled", None)]
    st = m.snapshot()
    s1 = st["sessions"]["S1"]
    assert s1["op_classes"] == {"modeled-stochastic": 1, "modeled-deterministic": 1, "unmodeled": 3, "delegated": 1}
    assert s1["ratios"]["leverage"] == 0.4 and s1["ratios"]["determinism"] == 0.5 and s1["ratios"]["tool_calls"] == 5
    assert st["unmodeled_top"][0] == {"op": "mystery.sh", "tool": "Bash", "count": 2, "suggested_node": "capture-candidate"}
    assert [u["op"] for u in st["unmodeled_top"]] == ["mystery.sh", "Read", "tidy"]
    repo = os.path.basename(str(tmp))
    assert st["unmodeled_by_repo"][repo][0]["op"] == "mystery.sh"
    assert st["unmodeled_by_repo"]["norepo"] == [{"op": "tidy", "tool": "Skill", "count": 1, "suggested_node": "capture-candidate"}]
    assert st["sessions"]["S2"]["ratios"]["leverage"] == 0.0 and st["ratios"]["unmodeled"] == 4


def ratios_hand_values_and_subcommand_is_byte_stable(tmp):
    now = time.time()
    recs = [dict(_rec(now, "R1", "PostToolUse", "/x", tool_name="Bash"), op_class=c) for c in
            ("modeled-deterministic", "modeled-deterministic", "modeled-stochastic", "delegated", "unmodeled")]
    recs += [dict(_rec(now, "R2", "PostToolUse", "/x", tool_name="Read"), op_class="unmodeled") for _ in range(3)]
    recs += [dict(_rec(now, "R3", "SessionStart", "/x"), op_class="modeled-event"),
             dict(_rec(now, "R1", "PreToolUse", "/x", tool_name="Bash"), op_class="modeled-deterministic")]
    out = obs.tally_ratios(recs)
    r1, r2 = out["sessions"]["R1"], out["sessions"]["R2"]
    assert abs(r1["leverage"] - 0.75) < 1e-9 and abs(r1["determinism"] - 2 / 3.0) < 1e-9 and r1["modeled"] == 3
    assert r1["handoff"] == 1 and abs(r1["handoff_share"] - 0.2) < 1e-9 and r1["tool_calls"] == 4
    assert r2["leverage"] == 0.0 and r2["determinism"] is None and "R3" not in out["sessions"]
    assert abs(out["ratios"]["leverage"] - 3 / 7.0) < 1e-9 and out["ratios"]["tool_calls"] == 7 and out["ratios"]["handoff"] == 1
    assert obs.ratio_block({}) == {"modeled_deterministic": 0, "modeled_stochastic": 0, "delegated": 0, "handoff": 0, "unmodeled": 0,
                                   "modeled": 0, "tool_calls": 0, "leverage": None, "determinism": None, "handoff_share": None}
    events = tmp / "events.jsonl"
    events.write_text("\n".join(json.dumps(r, sort_keys=True) for r in recs) + "\nnot json\n\n")
    runs = [subprocess.run([PY, OBS, "ratios", "--events", str(events)], capture_output=True, timeout=20) for _ in range(2)]
    assert runs[0].returncode == 0 and runs[0].stdout == runs[1].stdout and runs[0].stdout
    parsed = json.loads(runs[0].stdout)
    assert parsed["sessions"]["R1"]["leverage"] == r1["leverage"] and parsed["ratios"] == out["ratios"]
    one = subprocess.run([PY, OBS, "ratios", "--events", str(events), "--session", "R2"], capture_output=True, timeout=20)
    assert json.loads(one.stdout)["ratios"]["leverage"] == 0.0
    streams = obs.metric_streams(recs, [], now + 1, 60, buckets=6)
    lev, det = streams["observatory.model_leverage"], streams["observatory.determinism"]
    assert lev["kind"] == "gauge" and set(lev["series"]) == {"R1", "R2", "all"} and set(det["series"]) == {"R1", "all"}
    assert abs(max(lev["series"]["R1"]) - 1.0) < 1e-9 and abs(lev["series"]["R1"][-1] - 0.75) < 1e-9 or abs(max(lev["series"]["R1"]) - 0.75) < 1e-9
    assert max(lev["series"]["R2"]) == 0.0
    assert abs(max(det["series"]["R1"]) - 2 / 3.0) < 1e-9 and det["current"] > 0
    t0 = now - 5
    pair = [{"arrival_ts_unix": t0, "session_id": "S9", "hook_event_name": "PostToolUse", "op_class": "modeled-deterministic"},
            {"arrival_ts_unix": t0 + 0.9, "session_id": "S9", "hook_event_name": "PostToolUse", "op_class": "modeled-stochastic"}]
    st = obs.metric_streams(pair, [], now + 1, 60, buckets=6)
    d = st["observatory.determinism"]["series"]
    key = next(k for k in d if k.startswith("S9"))
    assert abs(max(d[key]) - 0.5) < 1e-9, d[key]
    assert abs(st["observatory.determinism"]["current"] - 0.5) < 1e-9
    assert obs.ratio_text(r1) == "0.75 0.67" and obs.ratio_text(None) == "   -    -" and obs.ratio_text(r2) == "0.00    -"


def serve_state_has_session_and_survives_garbage(tmp):
    with served(tmp) as (model, port):
        url = "http://127.0.0.1:%d/v1/logs" % port
        assert post(url, obs.hook_to_otlp({"session_id": SID, "cwd": str(tmp), "hook_event_name": "SessionStart"})) == 200
        assert post(url, b"\x00\x01 not json {{{") in (200, 400)
        assert post(url, {"resourceLogs": "wrong shape"}) in (200, 400)
        assert post(url, [1, 2, 3]) in (200, 400)
        assert post("http://127.0.0.1:%d/hook" % port, {"session_id": SID, "hook_event_name": "Stop", "cwd": str(tmp)}) == 200
        assert post("http://127.0.0.1:%d/v1/metrics" % port, {"resourceMetrics": []}) == 200
        model.enrich()
        state_file = tmp / "state.json"
        obs.atomic_write_json(str(state_file), model.snapshot())
        st = json.loads(state_file.read_text())
        s = st["sessions"][SID]
        assert s["cwd"] == str(tmp) and s["counts"]["claude_code.hook.SessionStart"] == 1 and s["counts"]["claude_code.hook.Stop"] == 1
        assert s["live"] is True and st["records_total"] == 2 and st["latency"]["n"] == 2
        assert post(url, obs.hook_to_otlp({"session_id": SID, "hook_event_name": "UserPromptSubmit"})) == 200


def transcript_join_most_recent_hid_wins(tmp):
    cfg = tmp / ".claude"
    with env(CLAUDE_CONFIG_DIR=str(cfg)):
        proj = cfg / "projects" / "-home-x-repo"
        proj.mkdir(parents=True)
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "please read %s/H-901-alpha.md" % HYP_DIR}}),
                 json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "H-901 says hello; also mentions H-DRAFT-0badcafe"}]}}),
                 json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "now switching to H-902"}]}})]
        (proj / (SID + ".jsonl")).write_text("\n".join(lines) + "\n")
        assert obs.projects_dir() == str(cfg / "projects")
        hids, path = obs.join_hids(SID, "/home/x/repo")
        assert path == str(proj / (SID + ".jsonl"))
        assert hids["primary"] == "H-902" and hids["source"] == "transcript"
        assert hids["secondary"][0] == "H-DRAFT-0badcafe" and "H-901" in hids["secondary"]
        hids2, path2 = obs.join_hids("nope-" + SID, "/home/x/H-777-thing")
        assert path2 is None and hids2["primary"] == "H-777" and hids2["source"] == "cwd"
        hids3, _ = obs.join_hids("nope2", "/home/x/plain")
        assert hids3["primary"] is None and hids3["source"] == "none"


def agents_phase_and_timeline_lanes(tmp):
    m = obs.Model(events_path=os.devnull)
    now = float(int(time.time()))
    recs = []

    def rec(hook, t, **kw):
        r = {"arrival_ts_unix": now - t, "event_ts_unix": now - t, "source": "hook", "session_id": SID,
             "event_name": obs.EVENT_PREFIX + hook, "hook_event_name": hook, "cwd": "/x/repo"}
        r.update(kw)
        recs.append(r)
        return r
    rec("SessionStart", 50)
    rec("UserPromptSubmit", 40, prompt_id="p1")
    rec("SubagentStart", 30, agent_id="ag1", agent_type="Explore")
    rec("PostToolUse", 25, agent_id="ag1", agent_type="Explore", tool_name="Grep", tool_outcome="ok")
    rec("SubagentStop", 20, agent_id="ag1", agent_type="Explore")
    rec("PostToolUse", 15, tool_name="Bash", tool_outcome="error")
    rec("Stop", 10)
    with m.lock:
        for r in recs:
            m._apply(r)
    s = m.sessions[SID]
    assert s["phase"] == "waiting" and s["prompts"] == 1 and abs(s["started_at"] - (now - 50)) < 1
    assert list(s["agents"]) == ["ag1"]
    a = s["agents"]["ag1"]
    assert a["type"] == "Explore" and a["ended"] is True and a["events"] == 3 and a["last_tool"] == "Grep"
    s["live"], s["repo"], s["hid_title"] = True, "repo", "a title"
    s["hids"] = {"primary": "H-555", "secondary": [], "source": "transcript"}
    rows = obs.board_rows({SID: s}, recs, now, 60, 60)
    assert [r["kind"] for r in rows] == ["repo", "session", "hypothesis", "agent"]
    assert rows[0]["label"] == "repo" and rows[2]["label"] == "H-555" and rows[2]["sub"] == "a title"
    assert rows[1]["state"] == "waiting" and rows[3]["state"] == "ended" and rows[3]["label"] == "Explore"
    main_cells = rows[1]["cells"]
    main_txt = "".join(c for c, _ in main_cells)
    assert main_txt[10] == "▏" and main_txt[:10] == " " * 10 and "▕" not in main_txt
    assert main_cells[20] == ("▆", obs.SPIKE_COLOR["prompt"]) and main_cells[45] == ("▆", obs.SPIKE_COLOR["error"])
    assert main_cells[50] == ("▆", obs.SPIKE_COLOR["stop"]) and main_cells[30] == ("⡀", obs.blend(obs.STATE_HUE["waiting"], 0.55))
    agent_cells = rows[3]["cells"]
    agent_txt = "".join(c for c, _ in agent_cells)
    assert agent_txt[30] == "▏" and agent_txt[40] == "▕" and agent_txt[41:] == " " * 19
    assert agent_cells[35] == ("▆", obs.CLASS_COLOR["unmodeled"])
    assert rows[1]["ratios"] == "   -    -"
    bash = recs[5]
    dense = [dict(bash, arrival_ts_unix=now - 5, op_class="modeled-deterministic", tool_outcome="ok")] * 3
    sparse = [dict(bash, arrival_ts_unix=now - 2, op_class="modeled-stochastic", tool_outcome="ok")]
    b = obs.lane_buckets(dense + sparse, now, 60, 60)
    cells = obs.lane_cells(b, (SID, None), 60, "working")
    assert cells[55] == ("▆", obs.CLASS_COLOR["modeled-deterministic"])
    assert cells[58] == ("▃", obs.CLASS_COLOR["modeled-stochastic"])
    assert obs.dominant_class({"unmodeled": 2, "delegated": 2}) == "delegated" and obs.dominant_class({}) is None
    assert obs.span_columns(None, None, now, 60, 60) == (0, None) and obs.span_columns(now - 30, now - 10, now, 60, 60) == (30, 50)
    assert len(obs.timeline_axis(now, 60, 60)) == 60
    a2 = {"type": "workflow-subagent", "first_seen": now - 30, "last_seen": now - 30, "events": 4, "ended": False}
    assert obs.agent_state(a2, s, now) == "working"
    assert obs.agent_state(dict(a2, last_seen=now - 500), s, now) == "idle"
    assert obs.agent_state(a2, dict(s, registry={"state": "done"}), now) == "ended"
    assert obs.agent_state(a2, dict(s, live=False), now) == "ended"
    quiet_s = dict(s, session_id="s-q", registry={"name": "Alpha", "state": "working"})
    stuck = dict(s, session_id="s-b", registry={"name": "Zulu", "state": "blocked", "status": "waiting"})
    bad = dict(s, session_id="s-e", registry={"name": "Mike", "state": "working"}, error_flag=True)
    order = [r["label"] for r in obs.board_rows({"s-q": quiet_s, "s-b": stuck, "s-e": bad}, [], now, 60, 60, hyp_inline=True) if r["kind"] == "session"]
    assert order == ["Zulu", "Mike", "Alpha"]
    repo_row = obs.board_rows({"s-q": quiet_s, "s-b": stuck, "s-e": bad}, [], now, 60, 60, hyp_inline=True)[0]
    assert repo_row["attention"] == 2
    inline = [r for r in obs.board_rows({SID: s}, recs, now, 60, 60, hyp_inline=True) if r["kind"] == "session"][0]
    assert inline["hyp"] == "H-555  a title" and not any(r["kind"] == "hypothesis" for r in obs.board_rows({SID: s}, recs, now, 60, 60, hyp_inline=True))
    assert len(obs.timeline_axis(now, 3600, 40)) == 40 and ":" in obs.timeline_axis(now, 60, 12)
    dead = dict(s, live=False, last_seen=now - 2 * obs.RECENT_S, registry=None)
    assert obs.board_rows({SID: dead}, recs, now, 60, 60) == []
    assert [r["kind"] for r in obs.board_rows({SID: dead}, recs, now, 60, 60, recent_only=False)][:2] == ["repo", "session"]
    cells = [(" ", "dim")] * 9 + [("⡀", "dim")] + [("▂", "#4ade80"), ("▄", "#4ade80")] + [("▆", "#f8fafc")]
    runs = obs.coalesce(cells)
    assert len(runs) == 3 and "".join(t for t, _ in runs) == "".join(c for c, _ in cells)
    many = dict(s, agents={"ag%d" % i: {"type": "wf", "first_seen": now, "last_seen": now - 500, "events": 1, "ended": False} for i in range(obs.MAX_AGENT_ROWS + 3)})
    kinds = [r["kind"] for r in obs.board_rows({SID: many}, recs, now, 60, 60)]
    assert kinds.count("agent") == obs.MAX_AGENT_ROWS and kinds.count("more") == 1
    busy = dict(s, agents={"ag%d" % i: {"type": "wf", "first_seen": now, "last_seen": now, "events": 1, "ended": False} for i in range(obs.MAX_AGENT_ROWS + 3)})
    kinds = [r["kind"] for r in obs.board_rows({SID: busy}, recs, now, 60, 60)]
    assert kinds.count("agent") == obs.MAX_AGENT_ROWS + 3 and kinds.count("more") == 0
    assert [r["kind"] for r in obs.board_rows({SID: s}, recs, now, 60, 60, collapsed={"sess:" + SID})] == ["repo", "session"]
    assert [r["kind"] for r in obs.board_rows({SID: s}, recs, now, 60, 60, collapsed={"repo:repo"})] == ["repo"]
    s["live"] = False
    assert obs.board_rows({SID: s}, recs, now, 60, 60, live_only=True) == []
    flat = list(obs.flatten_logs(obs.hook_to_otlp({"session_id": SID, "hook_event_name": "SubagentStart",
                                                    "agent_id": "ag9", "agent_type": "Plan"}), now))
    assert flat[0]["agent_id"] == "ag9" and flat[0]["agent_type"] == "Plan"
    assert "SubagentStart" in obs.HOOK_EVENTS and "SubagentStop" in obs.HOOK_EVENTS


def traces_logs_metrics_read_models(tmp):
    now = time.time()
    s = obs.new_session(SID)
    s.update({"repo": "repo", "live": True, "registry": {"name": "Sess"}, "hids": {"primary": "H-555", "secondary": [], "source": "transcript"}})
    recs = []

    def rec(hook, t, **kw):
        r = {"arrival_ts_unix": now - t, "event_ts_unix": now - t - 0.01, "source": "hook", "session_id": SID,
             "event_name": obs.EVENT_PREFIX + hook, "hook_event_name": hook, "cwd": "/x/repo"}
        r.update(kw)
        recs.append(r)
        return r
    rec("SessionStart", 100)
    rec("UserPromptSubmit", 90, prompt_id="p1")
    rec("PreToolUse", 80, tool_name="Read", tool_use_id="t1")
    rec("PostToolUse", 70, tool_name="Read", tool_use_id="t1", tool_outcome="ok")
    rec("SubagentStart", 60, agent_id="ag1", agent_type="Explore")
    rec("PreToolUse", 55, tool_name="Grep", tool_use_id="t2", agent_id="ag1", agent_type="Explore")
    rec("PostToolUse", 50, tool_name="Grep", tool_use_id="t2", agent_id="ag1", agent_type="Explore", tool_outcome="error")
    rec("SubagentStop", 45, agent_id="ag1", agent_type="Explore")
    rec("Stop", 40)
    rec("UserPromptSubmit", 30, prompt_id="p2")
    rec("PreToolUse", 20, tool_name="Bash", tool_use_id="t3")
    traces = obs.build_traces(recs, {SID: s}, now)
    assert [t["trace_id"] for t in traces] == ["%s:p2" % SID[:8], "%s:p1" % SID[:8]]
    t1 = traces[1]
    assert t1["end"] == now - 40 and t1["errors"] == 1 and not t1["in_progress"]
    names = [(d, sp["name"]) for sp, d in obs.span_depths(t1["spans"])]
    assert names == [(0, "turn Sess"), (1, "tool Read"), (1, "agent Explore"), (2, "tool Grep")]
    grep = next(sp for sp in t1["spans"] if sp["name"] == "tool Grep")
    assert grep["error"] and grep["end"] - grep["start"] == 5 and grep["parent"].endswith(":agent:ag1")
    t2 = traces[0]
    assert t2["in_progress"] and any(sp["in_progress"] and sp["name"] == "tool Bash" for sp in t2["spans"])
    wf = obs.waterfall_rows(t1, 60, now)
    assert wf[0][1]["name"] == "turn Sess" and wf[0][2] == 0 and wf[0][3] == 60
    assert all(0 <= off < 60 and ln >= 1 for _, _, off, ln, _ in wf)
    payload = {"resourceSpans": [{"resource": {"attributes": [obs.kv("service.name", "svc")]}, "scopeSpans": [{"scope": {"name": "sc"}, "spans": [
        {"traceId": "T1", "spanId": "A", "name": "root", "startTimeUnixNano": str(int((now - 10) * 1e9)), "endTimeUnixNano": str(int((now - 1) * 1e9)), "attributes": [obs.kv("session.id", SID)]},
        {"traceId": "T1", "spanId": "B", "parentSpanId": "A", "name": "child", "startTimeUnixNano": str(int((now - 8) * 1e9)), "endTimeUnixNano": str(int((now - 3) * 1e9))}]}]}]}
    otlp = list(obs.flatten_traces(payload, now))
    assert otlp[0]["signal"] == "span" and otlp[1]["parent_span_id"] == "A"
    tr = [t for t in obs.build_traces(otlp, {SID: s}, now) if t["kind"] == "otlp"][0]
    assert [(d, sp["name"]) for sp, d in obs.span_depths(tr["spans"])] == [(0, "root"), (1, "child")] and tr["label"] == "Sess"
    assert len(obs.filter_text(recs, "grep", ("tool_name",))) == 2
    streams = obs.metric_streams(recs, [(now - 5, 3, 7)], now, 120, buckets=12)
    assert streams["observatory.tool_calls"]["total"] == 2 and streams["observatory.tool_errors"]["total"] == 1
    assert streams["observatory.prompts"]["total"] == 2 and streams["observatory.sessions_live"]["current"] == 3.0
    assert "Grep" in streams["observatory.tool_errors"]["series"] and streams["observatory.hook_latency_ms"]["kind"] == "gauge"
    assert len(obs.sparkline([0, 1, 2, 4], 4)) == 4
    mp = {"resourceMetrics": [{"resource": {"attributes": []}, "scopeMetrics": [{"metrics": [{"name": "claude_code.cost.usage", "unit": "USD", "sum": {"dataPoints": [
        {"asDouble": 0.5, "timeUnixNano": str(int((now - 3) * 1e9)), "attributes": [obs.kv("session.id", SID)]}]}}]}]}]}
    mrec = list(obs.flatten_metrics(mp, now))
    assert mrec[0]["metric_value"] == 0.5 and mrec[0]["metric_kind"] == "sum"
    assert obs.metric_streams(mrec, [], now, 60, 6)["claude_code.cost.usage"]["total"] == 0.5


def scan_hids_ignores_the_observatorys_own_path(tmp):
    hook_line = json.dumps({"type": "system", "content": "python3 %s hook --port 4319" % obs.SELF_PATH})
    assert obs.SELF_PATH == obs.THIS_FILE == OBS
    assert obs.scan_hids([hook_line]) == []
    assert obs.scan_hids([hook_line, "working on H-555 now"]) == ["H-555"]
    assert obs.scan_hids(["see experiments/runs/H-DRAFT-2c1fc974/run-1/RESULTS.json"]) == ["H-DRAFT-2c1fc974"]


def otel_modules_stay_unloaded(tmp):
    with served(tmp) as (model, port):
        for _ in range(3):
            assert post("http://127.0.0.1:%d/v1/logs" % port, obs.hook_to_otlp({"session_id": SID, "hook_event_name": "Stop"})) == 200
        assert model.snapshot()["otel_modules_loaded"] is False
        assert not any(m.startswith("opentelemetry") for m in sys.modules)


def install_uninstall_roundtrip_preserves_keys(tmp):
    settings = tmp / "settings.json"
    original = {"permissions": {"allow": ["Bash(ls:*)"], "deny": []}, "env": {"FOO": "bar"},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}],
                          "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": "say x"}]}]},
                "model": "opus", "nested": {"a": [1, 2, {"b": None}]}}
    settings.write_text(json.dumps(original, indent=2))
    args = obs.build_parser().parse_args(["install-hooks", "--scope", "project", "--transport", "post", "--port", "43190", "--settings", str(settings)])
    assert quiet(args.fn, args) == 0
    after = json.loads(settings.read_text())
    for ev in obs.HOOK_EVENTS:
        ours = [h for g in after["hooks"][ev] for h in g["hooks"] if obs._is_ours(h)]
        assert len(ours) == 1 and ours[0]["command"] == "python3 %s hook --port 43190" % OBS and ours[0]["timeout"] == 5
        assert ours[0]["async"] is True
    assert len(obs.HOOK_EVENTS) == 9
    assert [g for g in after["hooks"]["PreToolUse"] if obs._is_ours(g["hooks"][0])][0]["matcher"] == "*"
    old = json.loads(json.dumps(after))
    for ev in obs.HOOK_EVENTS:
        for g in old["hooks"][ev]:
            for h in g["hooks"]:
                if obs._is_ours(h):
                    h.pop("async", None)
    new, changes = obs.edit_hooks(old, port=43190, transport="post")
    assert all(": replaced observatory hook" in c for c in changes) and new == after
    assert obs.edit_hooks(new, port=43190, transport="post")[1] == ["%s: unchanged" % ev for ev in obs.HOOK_EVENTS]
    assert "matcher" not in [g for g in after["hooks"]["SessionStart"] if obs._is_ours(g["hooks"][0])][0]
    assert after["hooks"]["SessionStart"][0] == original["hooks"]["SessionStart"][0]
    assert {k: v for k, v in after.items() if k != "hooks"} == {k: v for k, v in original.items() if k != "hooks"}
    assert quiet(args.fn, args) == 0
    assert json.loads(settings.read_text()) == after
    _, changes = obs.edit_hooks(after, port=43190, transport="post")
    assert changes == ["%s: unchanged" % ev for ev in obs.HOOK_EVENTS]
    _, changes = obs.edit_hooks(after, port=4319, transport="post")
    assert all(c.endswith("replaced observatory hook (%s)" % obs.hook_command(4319, "post")) for c in changes)
    _, changes = obs.edit_hooks(original, port=43190, transport="post")
    assert all(": added observatory hook" in c for c in changes)
    _, changes = obs.edit_hooks(original, remove=True)
    assert changes == []
    uargs = obs.build_parser().parse_args(["uninstall-hooks", "--settings", str(settings)])
    assert quiet(uargs.fn, uargs) == 0
    assert json.loads(settings.read_text()) == original


def posttoolusefailure_counts_as_a_tool_call_with_error_outcome(tmp):
    assert "PostToolUseFailure" in obs.HOOK_EVENTS and "PostToolUseFailure" in obs.TOOL_EVENTS
    hook = {"session_id": SID, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
            "tool_input": {"command": "python3 scripts/count.py --secret ZEBRA"}, "error": "exit 1: boom", "cwd": "/x"}
    payload = obs.hook_to_otlp(hook)
    assert "ZEBRA" not in json.dumps(payload)
    flat = list(obs.flatten_logs(payload, time.time()))[0]
    assert flat["hook_event_name"] == "PostToolUseFailure" and flat["tool_outcome"] == "error" and flat["op_name"] == "count.py"
    assert "boom" in (flat.get("tool_summary") or "")
    recs = [dict(flat, op_class="modeled-deterministic"), {"hook_event_name": "PostToolUse", "session_id": SID, "op_class": "unmodeled"}]
    out = obs.tally_ratios(recs)
    blk = out["sessions"][SID]
    assert blk["tool_calls"] == 2 and blk["modeled"] == 1 and abs(blk["leverage"] - 0.5) < 1e-9


def spool_transport_install_and_tail(tmp):
    spool = tmp / "spool.jsonl"
    settings = tmp / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}))
    args = obs.build_parser().parse_args(["install-hooks", "--scope", "project", "--settings", str(settings), "--spool", str(spool)])
    assert quiet(args.fn, args) == 0
    after = json.loads(settings.read_text())
    for ev in obs.HOOK_EVENTS:
        ours = [h for g in after["hooks"][ev] for h in g["hooks"] if obs._is_ours(h)]
        assert len(ours) == 1 and ours[0].get("async") is True and 'cat >> "%s"' % spool in ours[0]["command"] and ours[0]["timeout"] == 30
    assert after["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo keep"
    old, _ = obs.edit_hooks({}, port=43190, transport="post")
    new, changes = obs.edit_hooks(old, transport="spool", spool=str(spool))
    assert all("replaced" in c for c in changes) and all("cat >>" in h["command"] for g in new["hooks"]["PostToolUse"] for h in g["hooks"] if obs._is_ours(h))
    _, again = obs.edit_hooks(new, transport="spool", spool=str(spool))
    assert all(c.endswith("unchanged") for c in again)
    cmd = obs.hook_command(transport="spool", spool=str(spool))
    p1 = {"session_id": SID, "hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(tmp),
          "tool_input": {"command": "python3 scripts/count.py --secret ZEBRA"}, "tool_response": {"stdout": "2"}}
    p2 = {"session_id": SID, "hook_event_name": "Stop", "cwd": str(tmp)}
    for p in (p1, p2):
        r = subprocess.run(cmd, shell=True, input=json.dumps(p, indent=2), capture_output=True, text=True, timeout=10)
        assert r.returncode == 0 and r.stdout == ""
    with open(spool, "ab") as fh:
        fh.write(b'{"session_id": "partial"')
    hooks, off = obs.spool_records(str(spool), 0)
    assert [h["hook_event_name"] for h in hooks] == ["PostToolUse", "Stop"]
    assert off == os.path.getsize(spool) - len(b'\n{"session_id": "partial"')
    hooks2, off2 = obs.spool_records(str(spool), off)
    assert hooks2 == [] and off2 == off
    model = obs.Model(str(tmp / "events.jsonl"), registry_interval=0)
    off3 = obs.spool_ingest_once(model, str(spool), 0)
    assert off3 == off
    events = [json.loads(l) for l in open(tmp / "events.jsonl")]
    assert [e["hook_event_name"] for e in events] == ["PostToolUse", "Stop"]
    assert events[0]["op_name"] == "count.py" and events[0]["source"] == "hook" and "ZEBRA" not in open(tmp / "events.jsonl").read()
    assert events[1]["op_node"] == "event/turn-completed"
    assert all(e.get("hook_self_ms") is None for e in events)


def backfill_synthesises_only_the_missing_records(tmp):
    ts0 = time.time() - 60
    iso = obs.now_iso
    transcript = tmp / (SID + ".jsonl")
    rows = [
        {"type": "user", "timestamp": iso(ts0), "cwd": str(tmp), "message": {"role": "user", "content": "invoke tidy"}},
        {"type": "assistant", "timestamp": iso(ts0 + 1), "cwd": str(tmp), "message": {"content": [
            {"type": "tool_use", "id": "toolu_A", "name": "Bash", "input": {"command": "python3 scripts/count.py --secret ZEBRA"}}]}},
        {"type": "user", "timestamp": iso(ts0 + 2), "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_A", "is_error": False}]}},
        {"type": "assistant", "timestamp": iso(ts0 + 3), "cwd": str(tmp), "message": {"content": [
            {"type": "tool_use", "id": "toolu_B", "name": "Read", "input": {"file_path": "/x/ZEBRA.txt"}}]}},
        {"type": "user", "timestamp": iso(ts0 + 4), "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_B", "is_error": True}]}},
        {"type": "assistant", "timestamp": iso(ts0 + 5), "cwd": str(tmp), "message": {"content": [
            {"type": "tool_use", "id": "toolu_C", "name": "Bash", "input": {"command": "echo hi"}}]}},
        {"type": "assistant", "timestamp": iso(ts0 + 6), "message": {"content": [{"type": "text", "text": "DONE"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    model = obs.Model(str(tmp / "events.jsonl"), registry_interval=0)
    arrived = [obs.hook_to_otlp(dict({"session_id": SID, "hook_event_name": h, "cwd": str(tmp), "transcript_path": str(transcript)}, **kw), now=ts0 + i)
               for i, (h, kw) in enumerate([("SessionStart", {}), ("UserPromptSubmit", {}),
                                            ("PreToolUse", {"tool_name": "Bash", "tool_use_id": "toolu_A", "tool_input": {"command": "python3 scripts/count.py"}}),
                                            ("PostToolUse", {"tool_name": "Bash", "tool_use_id": "toolu_A", "tool_input": {"command": "python3 scripts/count.py"}, "tool_response": {"stdout": "2"}})])]
    for p in arrived:
        model.ingest(list(obs.flatten_logs(p, ts0 + 1)))
    assert model.due_backfills(time.time()) == []
    model.schedule_backfill(SID, time.time() - 1)
    with model.lock:
        model.sessions[SID]["_registry_gone"] = True
    assert model.due_backfills(time.time()) == [SID]
    n = model.backfill_session(SID)
    assert n == 3, n
    events = [json.loads(l) for l in open(tmp / "events.jsonl")]
    back = [e for e in events if e["source"] == "backfill"]
    assert [e["hook_event_name"] for e in back] == ["PostToolUseFailure", "Stop", "SessionEnd"]
    assert back[0]["tool_use_id"] == "toolu_B" and back[0]["op_name"] == "Read" and back[0]["tool_outcome"] == "error"
    assert abs(back[0]["arrival_ts_unix"] - (ts0 + 4)) < 1e-3
    assert "ZEBRA" not in open(tmp / "events.jsonl").read()
    counted = [e["tool_use_id"] for e in events if e["hook_event_name"] in obs.COUNTED_TOOL_HOOKS]
    assert sorted(counted) == ["toolu_A", "toolu_B"] and len(counted) == len(set(counted))
    s = model.snapshot()["sessions"][SID]
    assert s["backfilled_records"] == 3 and s["ratios"]["tool_calls"] == 2 and s["phase"] == "ended"
    assert model.backfill_session(SID) == 0
    async_cfg, _ = obs.edit_hooks({}, transport="spool", spool=str(tmp / "s.jsonl"))
    sync_cfg, _ = obs.edit_hooks({}, transport="spool", spool=str(tmp / "s.jsonl"), sync=True)
    assert all(h.get("async") is True for g in async_cfg["hooks"]["PostToolUse"] for h in g["hooks"] if obs._is_ours(h))
    assert all("async" not in h for g in sync_cfg["hooks"]["PostToolUse"] for h in g["hooks"] if obs._is_ours(h))


def backfill_refuses_sessions_it_never_hooked(tmp):
    model = obs.Model(str(tmp / "events.jsonl"), registry_interval=0)
    sid = "99999999-2222-4333-8444-555555555555"
    transcript = tmp / (sid + ".jsonl")
    transcript.write_text(json.dumps({"type": "assistant", "timestamp": obs.now_iso(), "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n")
    with model.lock:
        s = model.sessions.setdefault(sid, obs.new_session(sid))
        s["registry"] = {"state": "done", "seen": time.time()}
        s["sources"] = ["registry"]
        s["transcript_path"] = str(transcript)
    model.schedule_backfill(sid, time.time() - 1)
    assert model.backfill_session(sid) == 0
    assert not os.path.exists(tmp / "events.jsonl") or open(tmp / "events.jsonl").read() == ""


def timeline_cells_bins_every_span_by_class_and_marks_lifecycle(tmp):
    base = 1_700_000_000.0
    s = obs.new_session(SID)
    s.update({"repo": "repo", "live": True, "registry": {"name": "Sess", "state": "working"}})
    now = base + 140.0
    tr = obs.build_traces(_rich_turn(SID, base, "p1"), {SID: s}, now)[0]
    t0, t_end = obs.trace_extent(tr, now)
    assert (t0, t_end) == (base, now) and tr["in_progress"] and tr["state"] == "working"
    G, B, M, U = (obs.CLASS_COLOR[k] for k in ("modeled-deterministic", "modeled-stochastic", "delegated", "unmodeled"))
    idle = obs.blend(obs.STATE_HUE["working"], 0.55)
    cells = obs.timeline_cells(tr, 28, t0, t_end, now)
    assert len(cells) == 28
    assert cells[0] == ("▆", obs.SPIKE_COLOR["prompt"])
    assert cells[1] == ("▂", G) and cells[2] == ("▃", G)
    assert cells[3:6] == [("▃", M)] * 3 and cells[6] == ("▂", M)
    assert cells[7] == (obs.IDLE_DOT, idle)
    assert cells[8:11] == [("▂", B)] * 3 and cells[11] == ("▆", obs.SPIKE_COLOR["error"])
    assert cells[12] == cells[13] == (obs.IDLE_DOT, idle)
    assert cells[14:27] == [("▂", U)] * 13
    assert cells[27] == (obs.CAP_R, obs.STATE_HUE["working"])
    assert {st.split()[0] for _, st in cells} <= set(obs.CLASS_COLOR.values()) | set(obs.SPIKE_COLOR.values()) | {idle, obs.STATE_HUE["working"]}
    read = next(sp for sp in tr["spans"] if sp["name"] == "tool Read")
    assert read["agent_id"] == "ag1" and read["attrs"]["op.class"] == "unmodeled" and obs.span_class(read) == "unmodeled"
    assert obs.span_class(next(sp for sp in tr["spans"] if sp["kind"] == "agent")) == "delegated"
    assert obs.span_class(next(sp for sp in tr["spans"] if sp["name"] == "tool Skill")) == "modeled-stochastic"
    spans = [(2, 12), (14, 34), (16, 30), (40, 60), (70, 140)]
    busy = set().union(*[_cells_of(a, b, 140.0, 28) for a, b in spans])
    assert {i for i, (g, _) in enumerate(cells) if g != obs.IDLE_DOT and i not in (0, 27)} == busy - {0, 27}
    hl = obs.timeline_cells(tr, 28, t0, t_end, now, highlight={1, 2})
    assert hl[1] == (obs.HL_GLYPH, cells[1][1] + " bold") and hl[2] == (obs.HL_GLYPH, cells[2][1] + " bold")
    assert all(g != obs.HL_GLYPH and st == obs.DRAIN_STYLE for i, (g, st) in enumerate(hl) if i not in (1, 2))
    assert hl[0][1] == obs.DRAIN_STYLE and hl[27][1] == obs.DRAIN_STYLE
    assert not any(g == obs.HL_GLYPH for g, _ in cells)
    tr2 = obs.build_traces(_rich_turn(SID, base, "p1", stop_at=100), {SID: s}, now)[0]
    t0, t_end = obs.trace_extent(tr2, now)
    assert (t0, t_end) == (base, base + 100) and not tr2["in_progress"] and tr2["state"] == "done"
    cells2 = obs.timeline_cells(tr2, 28, t0, t_end, now)
    assert cells2[0] == ("▆", obs.SPIKE_COLOR["prompt"]) and cells2[27] == ("▆", obs.SPIKE_COLOR["stop"])
    assert cells2[16] == ("▆", obs.SPIKE_COLOR["error"]) and all(c == ("▂", U) for c in cells2[20:27])
    rows = obs.waterfall_rows(tr2, 84, now)
    assert rows[0][1]["kind"] == "turn" and rows[0][2:4] == (0, 84)
    bash_open = [(off, ln) for _, sp, off, ln, _ in rows if sp["in_progress"]][0]
    assert bash_open[0] + bash_open[1] == 84
    for _, sp, off, ln, _ in rows[1:]:
        exp = _cells_of(sp["start"] - base, min(100, (sp["end"] or now) - base), 100.0, 84)
        assert set(range(off, off + ln)) == exp, sp["name"]
    ticks, labels = obs.axis_ticks(t0, t_end, 84, False)
    tick_line, label_line = "".join(t for t, _ in ticks), "".join(t for t, _ in labels)
    assert 4 <= tick_line.count("┬") <= 8 and obs.tick_step(100.0) == 15 and tick_line.count("┆") >= 4 and tick_line.endswith("┤")
    assert label_line.startswith(obs.fmt_clock(t0)) and label_line.rstrip().endswith("stop " + obs.fmt_clock(t_end))
    live_ticks, live_labels = obs.axis_ticks(base, now, 84, True)
    assert "".join(t for t, _ in live_ticks).endswith(obs.CAP_R) and "".join(t for t, _ in live_labels).rstrip().endswith("now")
    assert [obs.tick_step(x) for x in (8, 20, 45, 130, 400, 1000, 3000)] == [2, 5, 10, 30, 60, 300, 600]


def trace_extent_stopped_vs_live(tmp):
    now = 1000.0
    assert obs.trace_extent({"start": 100.0, "end": 160.0}, now) == (100.0, 160.0)
    assert obs.trace_extent({"start": 100.0, "end": None}, now) == (100.0, now)
    assert obs.trace_extent({"start": None, "end": None}, now) == (now, now)
    assert obs.trace_extent({"start": 500.0, "end": 400.0}, now) == (500.0, 500.0)
    assert obs.fmt_dur(5.25) == "5.2s" and obs.fmt_dur(60.0) == "60.0s" and obs.fmt_dur(120) == "2m" and obs.fmt_dur(None) == "0.0s"
    assert obs.span_cell_range({"start": 0.0, "end": 10.0}, 10, 0.0, 100.0, now) == (0, 0)
    assert obs.span_cell_range({"start": 0.0, "end": 10.1}, 10, 0.0, 100.0, now) == (0, 1)
    assert obs.span_cell_range({"start": 95.0, "end": None}, 10, 0.0, 100.0, 500.0) == (9, 9)
    assert obs.span_cell_range({"start": 150.0, "end": 160.0}, 10, 0.0, 100.0, now) is None
    assert obs.span_cell_range({"start": 50.0, "end": 50.0}, 10, 0.0, 100.0, now) == (5, 5)


def chain_aware_op_tokens_and_catalog_patches(tmp):
    assert obs.op_token_bash('python3 -c "import json; print(1)"') == "python3"
    assert obs.op_tokens_bash('echo "== lint"; python3 scripts/clarity-lint.py spec x.md | head -3') == ["echo", "clarity-lint.py", "head"]
    assert obs.op_tokens_bash("cd /x && grep -n foo a.py && python3 %s/preflight.py h.md; echo rc=$?" % PREFLIGHT_DIR) == ["grep", "preflight.py", "echo"]
    assert obs.op_tokens_bash("ls") == ["ls"] and obs.op_tokens_bash("") == [] and obs.op_tokens_bash(None) == []
    repo = tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "count.py").write_text("")
    (repo / PREFLIGHT_DIR).mkdir()
    (repo / PREFLIGHT_DIR / "preflight.py").write_text("")
    (repo / PREFLIGHT_DIR / "runs").mkdir()
    (repo / PREFLIGHT_DIR / "runs" / "deep.py").write_text("")
    cat = obs.Catalog()
    assert {"count.py", "preflight.py"} <= cat.get(str(repo))["scripts"] and "deep.py" not in cat.get(str(repo))["scripts"]
    now = time.time()

    def rec(cmd):
        hook = {"session_id": "C1", "hook_event_name": "PostToolUse", "cwd": str(repo), "tool_name": "Bash",
                "tool_use_id": "t-" + str(abs(hash(cmd)) % 10**6), "tool_input": {"command": cmd}, "tool_response": {"stdout": ""}}
        r = next(iter(obs.flatten_logs(obs.hook_to_otlp(hook, now=now), now)))
        return r, cat.classify(r)

    r, (cls, node) = rec("echo '== preflight'; python3 %s/preflight.py %s/x.md" % (PREFLIGHT_DIR, HYP_DIR))
    assert (r["op_name"], r["op_names"], cls, node) == ("echo", ["echo", "preflight.py"], "modeled-deterministic", "script/preflight.py")
    r, (cls, node) = rec("grep -n foo scripts/count.py")
    assert (r["op_name"], r.get("op_names"), cls, node) == ("grep", None, "unmodeled", None)
    r, (cls, node) = rec("python3 -c 'print(1)' && python3 scripts/count.py")
    assert (r["op_name"], cls, node) == ("python3", "modeled-deterministic", "script/count.py")
    payload = obs.hook_to_otlp({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "echo secret-arg; python3 scripts/count.py --token abc"}}, now=now)
    attrs = obs.attrs_to_dict(payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"])
    assert attrs["op.names"] == "echo count.py" and "secret" not in json.dumps(payload) and "abc" not in json.dumps(payload)


def chain_tokens_ignore_heredoc_bodies_and_stop_after_relative_cd(tmp):
    hd = "cat > /tmp/x.py <<'EOF'\nimport json\nfor row in rows: print(row)\nscripts/clarity-lint.py is mentioned here\nEOF\npython3 scripts/clarity-lint.py spec h.md"
    assert obs.op_tokens_bash(hd) == ["cat", "clarity-lint.py"]
    assert obs.op_token_bash(hd) == "cat"
    nl = "echo '== round 1'\ntime python3 %s/preflight.py h.md\necho rc=$?" % PREFLIGHT_DIR
    assert obs.op_tokens_bash(nl) == ["echo", "preflight.py"]
    rel = "cd apps/sub && for f in a b; do node scripts/cold-reader-lint.mjs $f; done; python3 scripts/writing-lint.py"
    assert obs.op_tokens_bash(rel) == []
    absd = "cd /opt/x/src/repo && python3 scripts/count.py && cd .. && ls"
    assert obs.op_tokens_bash(absd) == ["count.py", "ls"]
    assert obs.op_tokens_bash('cd "$CLAUDE_PROJECT_DIR" && python3 scripts/count.py') == ["count.py"]
    assert obs.op_tokens_bash("X=1 cd sub; python3 scripts/count.py") == []
    assert obs.MAX_OP_NAMES == 12
    many = " ; ".join("cmd%d" % i for i in range(20))
    assert len(obs.op_tokens_bash(many)) == 12


def roadmap_metrics_coverage_failed_routes_suggestions_and_handoff_gauge(tmp):
    repo = tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir()
    for name in ("count.py", "tidy-check.py"):
        (repo / "scripts" / name).write_text("")
    (repo / "skills" / "tidy").mkdir(parents=True)
    (repo / MODEL_DIR / "lab").mkdir(parents=True)
    (repo / MODEL_DIR / "lab" / "model.md").write_text("- [command/tidy](commands/tidy.md)\n- [policy/no-push](policies/no-push.md)\n")
    cwd = str(repo)
    now = time.time()
    m = obs.Model(str(tmp / "events.jsonl"), registry_interval=0)
    recs = []

    def hook(ev, tool, cmd_or_name, tid, t):
        h = {"session_id": "S1", "hook_event_name": ev, "cwd": cwd, "tool_name": tool, "tool_use_id": tid}
        h["tool_input"] = {"command": cmd_or_name} if tool == "Bash" else {"skill": cmd_or_name}
        if ev == "PostToolUse":
            h["tool_response"] = {"stdout": ""}
        return list(obs.flatten_logs(obs.hook_to_otlp(h, now=t), t))

    recs += hook("PreToolUse", "Bash", "python3 scripts/count.py", "t1", now - 300)
    recs += hook("PreToolUse", "Bash", "python3 scripts/count.py", "t2", now - 10)
    recs += hook("PreToolUse", "Skill", "tidy", "t3", now - 200) + hook("PostToolUse", "Skill", "tidy", "t3", now - 199)
    recs += hook("PostToolUse", "Bash", "python3 scripts/count.py", "t4", now - 100)
    recs += hook("PostToolUse", "Agent", "Explore", "t5", now - 90)
    recs += hook("PostToolUse", "Bash", "grep -n x scripts/tidy-check.py", "t6", now - 80)
    recs += hook("PostToolUse", "Bash", "python3 -c 'print(1)'", "t7", now - 70)
    m.ingest(recs)
    st = m.snapshot()
    cov = st["model_coverage"]["all"]
    assert cov["nodes_routable"] == 5 and cov["nodes_seen"] == 2 and abs(cov["coverage"] - 0.4) < 1e-9
    assert set(cov["seen"]) == {"command/tidy", "script/count.py"} and "script/tidy-check.py" in cov["never_seen_sample"]
    label = next(iter(st["model_coverage"]["by_repo"]))
    assert st["model_coverage"]["by_repo"][label]["nodes_seen"] == 2
    fr = st["failed_routes"]
    assert fr["count"] == 1 and fr["rows"][0]["tool_use_id"] == "t1" and fr["rows"][0]["node"] == "script/count.py" and fr["rows"][0]["age_s"] >= 120
    top = {(r["op"], r["tool"]): r["suggested_node"] for r in st["unmodeled_top"]}
    assert top[("grep", "Bash")] == "capture-candidate" and top[("python3", "Bash")] == "capture-candidate"
    assert obs.suggest_node("count.py", "Bash", obs.scan_catalog(cwd)) == "script/count.py"
    assert obs.suggest_node("Write", "Write") == "capture-candidate" and obs.suggest_node("Explore", "Agent") == "handoff"
    assert st["ratios"] == obs.ratio_block({"modeled-deterministic": 1, "modeled-stochastic": 1, "delegated": 1, "unmodeled": 2})
    streams = obs.metric_streams(obs.read_events(str(tmp / "events.jsonl")), [], now + 1, 600, buckets=6)
    hs = streams["observatory.handoff_share"]
    assert hs["kind"] == "gauge" and "all" in hs["series"] and abs(hs["series"]["all"][-1] - 0.2) < 1e-9


def chain_tokens_resolve_cd_against_cwd_and_accept_absolute_scripts(tmp):
    repo = tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "count.py").write_text("")
    (repo / "experiments" / "runs" / "x").mkdir(parents=True)
    cwd = str(repo)
    assert obs.op_tokens_bash("cd %s/experiments/runs/x && python3 scripts/count.py" % cwd, cwd=cwd) == []
    assert obs.op_tokens_bash("cd experiments/runs/x && python3 scripts/count.py", cwd=cwd) == []
    assert obs.op_tokens_bash("cd %s && python3 scripts/count.py" % cwd, cwd=cwd) == ["count.py"]
    assert obs.op_tokens_bash("cd experiments && cd .. && python3 scripts/count.py", cwd=cwd) == ["count.py"]
    assert obs.op_tokens_bash("cd experiments/runs/x && python3 %s/scripts/count.py" % cwd, cwd=cwd) == ["count.py"]
    assert obs.op_tokens_bash('cd "$RUN_DIR" && python3 scripts/count.py', cwd=cwd) == []
    assert obs.op_tokens_bash("python3 scripts/count.py", cwd=str(repo / "experiments")) == []
    assert obs.op_tokens_bash("python3 %s/scripts/count.py" % cwd, cwd=str(repo / "experiments")) == ["count.py"]
    assert obs.op_tokens_bash("/usr/bin/time -p python3 scripts/count.py", cwd=cwd) == ["count.py"]
    assert obs.op_token_bash("/usr/bin/time -p python3 scripts/count.py") == "count.py"
    now = time.time()
    hook = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": cwd, "tool_use_id": "t-e1",
            "tool_input": {"command": "echo go; cd experiments/runs/x && python3 scripts/count.py"}, "tool_response": {"stdout": ""}}
    r = next(iter(obs.flatten_logs(obs.hook_to_otlp(hook, now=now), now)))
    assert r["op_name"] == "echo" and r.get("op_names") is None and obs.Catalog().classify(r) == ("unmodeled", None)


def timeout_wrapper_skips_its_duration(tmp):
    assert obs.op_token_bash("timeout 30 python3 scripts/count.py") == "count.py"
    assert obs.op_token_bash("timeout 2m python3 scripts/count.py") == "count.py"
    assert obs.op_token_bash("timeout -s KILL 5 python3 scripts/count.py") == "count.py"
    assert obs.op_tokens_bash("X=1 timeout 30 python3 scripts/count.py; echo done") == ["count.py", "echo"]
    assert obs.op_token_bash("timeout") is None


def chain_tokens_resolve_vars_and_reject_scripts_outside_toplevel(tmp):
    repo = tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "count.py").write_text("")
    (repo / "hooks").mkdir()
    (repo / "hooks" / "session_resolver.py").write_text("")
    cwd = str(repo)
    clone = str(tmp / "scratch-clone")
    cmd = 'C=%s; cd "$C" && CLAUDE_PROJECT_DIR="$C" timeout 60 python3 "$C/hooks/session_resolver.py"' % clone
    assert obs.op_tokens_bash(cmd, cwd=cwd) == []
    cmd_top = 'W=%s; cd "$W" && python3 "$W/scripts/count.py"' % cwd
    assert obs.op_tokens_bash(cmd_top, cwd=cwd) == ["count.py"]
    assert obs.op_tokens_bash('python3 "$OTHER/scripts/count.py"', cwd=cwd) == []
    assert obs.op_tokens_bash("python3 /somewhere/else/scripts/count.py", cwd=cwd) == []
    assert obs.op_tokens_bash("python3 %s/scripts/count.py" % cwd, cwd=cwd) == ["count.py"]
    assert obs.op_tokens_bash('cd "$RUN_DIR" && python3 scripts/count.py', cwd=cwd) == []
    assert obs.op_tokens_bash("python3 /somewhere/else/scripts/count.py") == ["count.py"]


# --------------------------------------------------------------------------- plugin-specific checks
def hyp_json_overrides_catalog_and_titles(tmp):
    """Divergence 2: model_dir / hypotheses_dir / preflight_file from .claude/hyp.json; defaults when absent."""
    custom = tmp / "custom"
    (custom / ".git").mkdir(parents=True)
    (custom / ".claude").mkdir()
    (custom / ".claude" / "hyp.json").write_text(json.dumps({"model_dir": "om/", "hypotheses_dir": "specs", "preflight_file": "checks/preflight.py"}))
    (custom / "om" / "lab").mkdir(parents=True)
    (custom / "om" / "lab" / "model.md").write_text("- [command/tidy](commands/tidy.md)\n")
    (custom / "specs").mkdir()
    (custom / "specs" / "H-901-alpha.md").write_text("# H-901-alpha: alpha title\n\nbody\n")
    (custom / "checks" / "sub").mkdir(parents=True)
    (custom / "checks" / "preflight.py").write_text("")
    (custom / "checks" / "sub" / "deep.py").write_text("")
    (custom / MODEL_DIR / "decoy").mkdir(parents=True)                       # default-layout decoys must be ignored
    (custom / MODEL_DIR / "decoy" / "model.md").write_text("- [command/decoy](d.md)\n")
    (custom / HYP_DIR).mkdir()
    (custom / HYP_DIR / "H-901-decoy.md").write_text("# H-901-decoy: decoy title\n")
    (custom / "scripts").mkdir()
    (custom / "scripts" / "count.py").write_text("")
    cfg = obs._hyp_cfg(str(custom))
    assert cfg == {"hypotheses_dir": "specs", "model_dir": "om", "preflight_file": "checks/preflight.py"}, cfg
    cat = obs.scan_catalog(str(custom))
    assert cat["nodes"] == {"command/tidy"} and cat["scripts"] == {"count.py", "preflight.py"}, cat
    assert obs.hypothesis_title(str(custom), "H-901") == "alpha title"
    plain = tmp / "plain"
    plain.mkdir()
    _mini_repo(plain)
    (plain / HYP_DIR).mkdir()
    (plain / HYP_DIR / "H-902-beta.md").write_text("# H-902-beta: beta title\n")
    (plain / PREFLIGHT_DIR).mkdir()
    (plain / PREFLIGHT_DIR / "preflight.py").write_text("")
    d = obs._hyp_cfg(str(plain))
    assert d == {"hypotheses_dir": HYP_DIR, "model_dir": MODEL_DIR, "preflight_file": hyp_config.DEFAULTS["preflight_file"]}, d
    cat = obs.scan_catalog(str(plain))
    assert "command/tidy" in cat["nodes"] and cat["scripts"] == {"count.py", "preflight.py"}
    assert obs.hypothesis_title(str(plain), "H-902") == "beta title" and obs.hypothesis_title(str(plain), "H-999") is None
    assert obs._hyp_cfg(None) == {"hypotheses_dir": None, "model_dir": None, "preflight_file": None}
    assert obs.scan_catalog(str(tmp / "missing"))["nodes"] == set()           # never raises on a missing tree


def defaults_under_data_dir(tmp):
    """Divergence 6 + 3: file defaults under data_dir(); CLAUDE_CONFIG_DIR / HYP_OBSERVATORY_DIR honoured at call time."""
    dd = obs.data_dir()
    assert obs.DEFAULT_SPOOL == os.path.join(dd, "spool.jsonl") and obs.DEFAULT_STATE == os.path.join(dd, "state.json")
    assert obs.DEFAULT_EVENTS == os.path.join(dd, "events.jsonl")
    p = obs.build_parser()
    a = p.parse_args(["serve", "--port", "1"])
    assert (a.state_file, a.events, a.spool) == (obs.DEFAULT_STATE, obs.DEFAULT_EVENTS, obs.DEFAULT_SPOOL)
    assert p.parse_args(["serve", "--port", "1", "--spool", ""]).spool == ""
    for sub in ("board", "web"):
        a = p.parse_args([sub])
        assert (a.state_file, a.events) == (obs.DEFAULT_STATE, obs.DEFAULT_EVENTS)
    assert p.parse_args(["dump-state"]).state_file == obs.DEFAULT_STATE
    assert p.parse_args(["ratios"]).events == obs.DEFAULT_EVENTS
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            p.parse_args(["serve"])
            raise AssertionError("serve without --port must be rejected")
        except SystemExit:
            pass
    cfg = str(tmp / "cfg")
    with env(CLAUDE_CONFIG_DIR=cfg, HYP_OBSERVATORY_DIR=None):
        assert obs.config_dir() == cfg and obs.projects_dir() == os.path.join(cfg, "projects")
        assert obs.settings_path_for("user") == os.path.join(cfg, "settings.json")
        assert obs.data_dir() == os.path.join(cfg, "observatory")
        assert obs.settings_path_for("user", override=str(tmp / "o.json")) == str(tmp / "o.json")
    with env(HYP_OBSERVATORY_DIR=str(tmp / "obsdata")):
        assert obs.data_dir() == str(tmp / "obsdata")
    with env(CLAUDE_CONFIG_DIR=None):
        assert obs.config_dir() == os.path.join(os.path.expanduser("~"), ".claude")
    # user-scope install under a scratch CLAUDE_CONFIG_DIR touches only that directory
    with env(CLAUDE_CONFIG_DIR=cfg, HYP_OBSERVATORY_DIR=None):
        os.makedirs(cfg)
        with open(os.path.join(cfg, "settings.json"), "w") as fh:
            fh.write("{}\n")
        r = subprocess.run([PY, OBS, "install-hooks", "--scope", "user"], capture_output=True, text=True, timeout=20)
        assert r.returncode == 0 and r.stdout.startswith("install-hooks " + os.path.join(cfg, "settings.json")), r.stdout + r.stderr
        st = json.load(open(os.path.join(cfg, "settings.json")))
        assert sorted(st["hooks"]) == sorted(obs.HOOK_EVENTS)
        assert all(os.path.join(cfg, "observatory", "spool.jsonl") in h["command"] for g in st["hooks"]["PostToolUse"] for h in g["hooks"])
        r = subprocess.run([PY, OBS, "uninstall-hooks", "--scope", "user"], capture_output=True, text=True, timeout=20)
        assert r.returncode == 0 and json.load(open(os.path.join(cfg, "settings.json"))) == {}


def stdlib_import_only_and_header(tmp):
    """Assertion 1 of H-DRAFT-ee81f74d: a fresh interpreter imports the module with no TUI or protobuf modules."""
    code = ("import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('observatory', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "bad = sorted(k for k in sys.modules if k.split('.')[0] in ('textual', 'rich', 'textual_serve', 'google', 'opentelemetry'))\n"
            "print(bad)\n")
    r = subprocess.run([PY, "-c", code, OBS], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stdout + r.stderr
    head = open(OBS, encoding="utf-8").read(6000)
    for needle in ("Provenance", "experiments/runs/H-DRAFT-2c1fc974/fixture/observatory/observatory.py",
                   "4b643fb12088a699d33a0d0cbcaa80a3d2181445df3ec78ee78af41c559a6b5b",
                   "H-DRAFT-2c1fc974-hook-fed-observatory-board", "H-DRAFT-fcf7b3fa-op-provenance-classification",
                   "H-DRAFT-fcf7b3fa-model-leverage-read-model", "H-DRAFT-2652d478-async-spool-backfill", "Divergences"):
        assert needle in head, needle
    assert all(("    %d. " % i) in head for i in range(1, 7))


def no_lab_paths(tmp):
    """Neither shipped file names the source lab, a home directory, a login, or the two directory literals that
    must come from hyp_config. The tokens are assembled here so this file cannot trip its own check."""
    tokens = ["/" + "Users/", "cause-n-" + "effect", "iand" + "erson", '"' + 'hypotheses' + '"', '"' + 'operating-model' + '"']
    for path in (OBS, os.path.abspath(__file__)):
        text = open(path, encoding="utf-8").read()
        hits = [t for t in tokens if t in text]
        assert not hits, "%s contains %s" % (os.path.basename(path), hits)


CHECKS = [
    ("hook-subcommand-emits-one-otlp-record", hook_subcommand_emits_one_otlp_record),
    ("hook-subcommand-never-fails-without-receiver", hook_subcommand_never_fails_without_receiver),
    ("tool-summary-never-leaks-content", tool_summary_never_leaks_content),
    ("op-token-table-and-no-argument-leak", op_token_table_and_no_argument_leak),
    ("catalog-classifies-against-the-repo", catalog_classifies_against_the_repo),
    ("ratios-hand-values-and-subcommand-byte-stable", ratios_hand_values_and_subcommand_is_byte_stable),
    ("serve-state-has-session-and-survives-garbage", serve_state_has_session_and_survives_garbage),
    ("transcript-join-most-recent-hid-wins", transcript_join_most_recent_hid_wins),
    ("agents-phase-and-timeline-lanes", agents_phase_and_timeline_lanes),
    ("traces-logs-metrics-read-models", traces_logs_metrics_read_models),
    ("scan-hids-ignores-the-observatorys-own-path", scan_hids_ignores_the_observatorys_own_path),
    ("otel-modules-stay-unloaded", otel_modules_stay_unloaded),
    ("install-uninstall-roundtrip-preserves-keys", install_uninstall_roundtrip_preserves_keys),
    ("posttoolusefailure-counts-as-error-outcome", posttoolusefailure_counts_as_a_tool_call_with_error_outcome),
    ("spool-transport-install-and-tail", spool_transport_install_and_tail),
    ("backfill-synthesises-only-the-missing-records", backfill_synthesises_only_the_missing_records),
    ("backfill-refuses-sessions-it-never-hooked", backfill_refuses_sessions_it_never_hooked),
    ("timeline-cells-bins-every-span-by-class", timeline_cells_bins_every_span_by_class_and_marks_lifecycle),
    ("trace-extent-stopped-vs-live", trace_extent_stopped_vs_live),
    ("chain-aware-op-tokens-and-catalog-patches", chain_aware_op_tokens_and_catalog_patches),
    ("chain-tokens-heredoc-newline-relative-cd", chain_tokens_ignore_heredoc_bodies_and_stop_after_relative_cd),
    ("roadmap-metrics-coverage-failed-routes", roadmap_metrics_coverage_failed_routes_suggestions_and_handoff_gauge),
    ("chain-tokens-resolve-cd-against-cwd", chain_tokens_resolve_cd_against_cwd_and_accept_absolute_scripts),
    ("timeout-wrapper-skips-its-duration", timeout_wrapper_skips_its_duration),
    ("chain-tokens-resolve-vars-reject-outside", chain_tokens_resolve_vars_and_reject_scripts_outside_toplevel),
    ("hyp-json-overrides-catalog-and-titles", hyp_json_overrides_catalog_and_titles),
    ("defaults-under-data-dir", defaults_under_data_dir),
    ("stdlib-import-only-and-header", stdlib_import_only_and_header),
    ("no-lab-paths", no_lab_paths),
]


def run_check(name, fn):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="hyp-observatory-selftest-"))
    try:
        fn(tmp)
        ok, detail = True, ""
    except Exception as exc:  # noqa: BLE001
        tb = traceback.extract_tb(sys.exc_info()[2])
        where = next((f for f in reversed(tb) if f.filename == os.path.abspath(__file__)), tb[-1])
        detail = "%s%s at line %d: %s" % (type(exc).__name__, (": " + str(exc)[:200]) if str(exc) else "", where.lineno, (where.line or "").strip()[:160])
        ok = False
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    RESULTS.append(ok)
    print(("PASS " + name) if ok else ("FAIL %s: %s" % (name, detail)))
    sys.stdout.flush()


def main():
    for name, fn in CHECKS:
        run_check(name, fn)
    n = len(RESULTS)
    print("%d/%d checks pass under %s" % (sum(RESULTS), n, sys.version.split()[0]))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
