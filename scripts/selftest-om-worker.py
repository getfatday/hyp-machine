#!/usr/bin/env python3
"""selftest-om-worker.py -- regression test for scripts/om-worker.py (the passive feedback worker).

Runs the INSTALLED copy (the scripts/om-worker.py beside this file, resolving observatory.py and
model-lint.py from its own directory) over throwaway consumers under a temp dir, with the fixture
grade behaviours of lab H-DRAFT-35397146-om-worker-deterministic ported as PASS/FAIL checks:

  observe-parity-with-observatory-referent   counts, leverage, determinism, handoff share, top step and
                                             unmodeled_top equal observatory.tally_ratios / unmodeled_top
                                             over records derived independently from the same transcripts
  skill-in-catalogue-classifies-modeled-stochastic   the Skill branch of Catalog.classify (never exercised
                                             by the lane fixture) counts a catalogued skill as modeled
  evaluate-lint-equals-model-lint-referent   lint.findings == model-lint.py's sorted ERROR/WARN lines,
                                             the seeded E-LINK named; lint-parse-not-skipped needs pyyaml
  compile-staleness-stale-then-fresh         stale true while compiled/ predates the model tree, false
                                             after a regenerated file is committed; compile_command ran
  newest-compiled-artifact-wins              staleness reads the newest compiled file by commit date
  redaction-by-omission-no-canary-in-ledger  none of the six planted canary classes, no forbidden key
  self-check-refuses-each-net                forbidden keys, the two markers, absolute paths, ../ escapes,
                                             bare well-known roots and identity strings each refuse with
                                             one stderr line and no bytes written; relative paths pass
  mutant-controls-redact-refuses-blind-leaks the known-answer control: M-redact 0 rows + 1 refusal line,
                                             M-blind leaks a canary
  idempotent-rerun-appends-nothing / cursor-rises-and-latest-equals-full-row
  rows-byte-identical-across-two-trees       the same inputs on a second tree write the same bytes
  drain-n-cap-20-then-rest / drain-t-cap-monotonic / free-space-floor-refuses-once
  inbox-rotation-at-1mib / poison-files-quarantined
  sigkill-mid-drain-resumes-without-duplicates-or-strands
  schema-2-row-read-reported-untouched / om-feedback-file-override-honoured
  init-writes-union-row-and-check-attr       init-scaffold appends `ledger/om-feedback.jsonl merge=union`;
                                             merge-attrs-check names the row once the file exists
  union-merge-keeps-both-appended-rows       two worktrees each append one row; the merge keeps both
  zero-model-calls-claude-shim               a PATH-first `claude` shim logs zero spawns
  installed-copy-stdlib-and-no-lab-paths     stdlib imports only; no home or lab path in the shipped file

Usage: python3 scripts/selftest-om-worker.py        exit 0 = all PASS, 1 = any FAIL
Standard library only, Python 3.9; pyyaml is needed by model-lint.py for the lint checks.
"""
import ast
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import contextlib

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(PLUGIN, "scripts")
WORKER = os.path.join(SCRIPTS, "om-worker.py")
INIT = os.path.join(SCRIPTS, "init-scaffold.py")
CHECK = os.path.join(SCRIPTS, "merge-attrs-check.py")
PY = sys.executable
LEDGER_REL = "ledger/om-feedback.jsonl"

CANARIES = {
    "fake_key": "sk-CANARY-FAKE-KEY-9c8f9b6a1d",
    "personal_name": "P7-jordan-canary",
    "prompt_sentinel": "CANARY-PROMPT-SENTINEL-41a2",
    "file_body_sentinel": "CANARY-FILE-BODY-SENTINEL-7e10",
    "abs_path_outside_repo": "/Users/nobody/outside-the-repo/secret.txt",
    "bash_arg_sentinel": "CANARY-BASH-ARG-SENTINEL-c301",
}
FORBIDDEN_KEYS = ("tool_input", "prompt", "last_assistant_message")
CATALOGUE_SCRIPTS = ["build_node.py", "check_model.py", "sync_ledger.py"]
CONSUMER_README = "# consumer\n\nTODO: describe the ops model.\n\nbody sentinel: %s\n" % CANARIES["file_body_sentinel"]
COMPILE_WITNESS = ".compile-witness"
COMMIT_DATES = {"model_v1": "2026-09-01T00:00:00+00:00", "compiled_v1": "2026-09-01T00:05:00+00:00",
                "hyp_json": "2026-09-01T00:10:00+00:00", "model_v2": "2026-09-01T00:15:00+00:00",
                "compiled_v2": "2026-09-01T00:20:00+00:00", "compiled_older": "2026-09-01T00:17:00+00:00"}
QUARANTINE_KEYS = {"kind", "schema", "landed_in", "file", "reason", "date"}
OVERFLOW_KEYS = {"kind", "schema", "landed_in", "moved", "date"}

RESULTS = []
TMP = None
SHIM_LOG = None


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((": " + str(detail)[:300]) if (detail and not cond) else ""))


def env(extra=None):
    e = {"PATH": os.path.join(TMP, "shim-bin") + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
         "HOME": os.environ.get("HOME", TMP), "HYP_STATE_DIR": os.path.join(TMP, "state"),
         "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1",
         "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
         "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
         "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    for k in ("PYTHONPATH", "USER", "LOGNAME", "TMPDIR"):
        if os.environ.get(k):
            e[k] = os.environ[k]
    if extra:
        e.update(extra)
    return e


def run(argv, cwd=None, extra_env=None, timeout=180):
    p = subprocess.run(argv, cwd=cwd, env=env(extra_env), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def git(cwd, *args, date=None):
    extra = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
    rc, out, err = run(["git", "-C", cwd, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"] + list(args),
                       extra_env=extra)
    if rc != 0:
        raise RuntimeError("git %s failed (%d): %s" % (" ".join(args), rc, err.strip()[:300]))
    return out


def worker(root, verb, *extra, extra_env=None):
    return run([PY, "-B", WORKER, verb, "--root", root] + list(extra), extra_env=extra_env)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def rows_of(path):
    out = []
    for line in read_bytes(path).splitlines():
        if line.strip():
            try:
                out.append(json.loads(line.decode("utf-8")))
            except ValueError:
                out.append(None)
    return out


def n_lines(path):
    return len(read_bytes(path).splitlines())


def ledger(root):
    return os.path.join(root, *LEDGER_REL.split("/"))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- consumer + transcripts
def build_consumer(root):
    os.makedirs(root, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for name in CATALOGUE_SCRIPTS:
        write(os.path.join(root, "scripts", name), "#!/usr/bin/env python3\nprint(%r)\n" % name)
    write(os.path.join(root, "README.md"), CONSUMER_README)
    write(os.path.join(root, ".gitignore"), COMPILE_WITNESS + "\n")
    # the Skill-in-catalogue branch (VERIFY.md finding 8): a skills/capture directory so the planted
    # Skill "capture" classifies modeled-stochastic while "foreign-skill-x" stays unmodeled
    write(os.path.join(root, "skills", "capture", "SKILL.md"), "---\nname: capture\n---\ncapture\n")
    om = os.path.join(root, "operating-model", "ops")
    write(os.path.join(om, "model.md"), "# ops model\n\n"
          "- [command/build](commands/build.md)\n- [command/check](commands/check.md)\n"
          "- [policy/gate-clean](policies/gate-clean.md)\n- [policy/gate-broken](policies/gate-broken.md)\n"
          "- [event/built](events/built.md)\n- [actor/builder](actors/builder.md)\n")
    write(os.path.join(om, "commands", "build.md"),
          "---\nid: command/build\ntype: command\ncontext: ops\nsummary: build the node\n"
          "status: current\nhandler: script/build_node.py\nissued-by: actor/builder\n"
          "executor: agent\nfreedom: bounded\nreads: []\nemits: [event/built]\n---\nBuild.\n")
    write(os.path.join(om, "commands", "check.md"),
          "---\nid: command/check\ntype: command\ncontext: ops\nsummary: check the model\n"
          "status: current\nhandler: script/check_model.py\nissued-by: actor/builder\n"
          "executor: agent\nfreedom: bounded\nreads: []\nemits: [event/built]\n---\nCheck.\n")
    write(os.path.join(om, "policies", "gate-clean.md"),
          "---\nid: policy/gate-clean\ntype: policy\ncontext: ops\nsummary: clean gate\n"
          "status: current\nthen: [command/check]\n---\nClean.\n")
    write(os.path.join(om, "policies", "gate-broken.md"),
          "---\nid: policy/gate-broken\ntype: policy\ncontext: ops\nsummary: broken gate\n"
          "status: current\nthen: [command/does-not-exist]\n---\nBroken (seeded E-LINK).\n")
    write(os.path.join(om, "events", "built.md"),
          "---\nid: event/built\ntype: event\ncontext: ops\nsummary: node built\n"
          "status: current\nrepresentation: row\n---\nBuilt.\n")
    write(os.path.join(om, "actors", "builder.md"),
          "---\nid: actor/builder\ntype: actor\ncontext: ops\nsummary: the builder\n"
          "status: current\n---\nBuilder.\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "model tree v1", date=COMMIT_DATES["model_v1"])
    write(os.path.join(root, "compiled", "SOP.md"), "<!-- COMPILED ARTIFACT -->\nSOP v1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "compiled v1 (seeded stale)", date=COMMIT_DATES["compiled_v1"])
    write(os.path.join(root, "bin", "compile-stub.sh"),
          "#!/bin/sh\necho compiled\nprintf 'ran\\n' > %s\nexit 0\n" % COMPILE_WITNESS)
    os.chmod(os.path.join(root, "bin", "compile-stub.sh"), 0o755)
    write(os.path.join(root, ".claude", "hyp.json"),
          json.dumps({"profile": "modeling", "model_dir": "operating-model",
                      "compile_command": "sh bin/compile-stub.sh"}, indent=1) + "\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "hyp.json + compile stub", date=COMMIT_DATES["hyp_json"])
    write(os.path.join(om, "actors", "builder.md"),
          "---\nid: actor/builder\ntype: actor\ncontext: ops\nsummary: the builder (edited)\n"
          "status: current\n---\nBuilder, edited after the compiled artifact (seeds staleness).\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "model tree v2 (post-compile edit)", date=COMMIT_DATES["model_v2"])


def _msg(uuid_, ts, content, root, extra_usage=None):
    usage = {"input_tokens": 5, "output_tokens": 1}
    if extra_usage:
        usage.update(extra_usage)
    return {"type": "assistant", "uuid": uuid_, "timestamp": ts, "cwd": root,
            "message": {"role": "assistant", "content": content, "usage": usage}}


def _tool_use(name, input_):
    return {"type": "tool_use", "id": "toolu_%s" % hashlib.sha256(json.dumps(input_, sort_keys=True).encode()).hexdigest()[:8],
            "name": name, "input": input_}


def plant_transcripts(root, tdir):
    os.makedirs(tdir, exist_ok=True)
    out = []
    for idx in range(3):
        sid = "scn-transcript-%d" % idx
        lines = []
        t0 = 1700000000 + idx * 1000
        n = [0]

        def ts():
            n[0] += 1
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 + n[0]))

        for i, name in enumerate(CATALOGUE_SCRIPTS + [CATALOGUE_SCRIPTS[0]]):
            lines.append(_msg("bash-cat-%d" % i, ts(), [_tool_use("Bash", {"command": "python3 scripts/%s" % name})], root))
        for i, name in enumerate(CATALOGUE_SCRIPTS[:2]):
            lines.append(_msg("bash-chain-%d" % i, ts(), [_tool_use("Bash", {"command": "echo hi && python3 scripts/%s" % name})], root))
        noncat = ["ls -la", "ls", "ls -1 scripts && grep TODO README.md", "grep TODO README.md",
                  "grep -n TODO README.md", "printf '%s' " + CANARIES["bash_arg_sentinel"],
                  "cat " + CANARIES["abs_path_outside_repo"],
                  "python3 /Users/nobody/outside-the-repo/tool.py --x"]
        for i, cmd in enumerate(noncat):
            lines.append(_msg("bash-un-%d" % i, ts(), [_tool_use("Bash", {"command": cmd})], root))
        for i, skill in enumerate(["capture", "foreign-skill-x"]):
            lines.append(_msg("skill-%d" % i, ts(), [_tool_use("Skill", {"skill": skill})], root))
        for i, sub in enumerate(["general-purpose", "Explore"]):
            lines.append(_msg("agent-%d" % i, ts(), [_tool_use("Agent", {"subagent_type": sub})], root))
        for i in range(20):
            tool = "Read" if i % 2 == 0 else "Edit"
            path = CANARIES["abs_path_outside_repo"] if i == 0 else "README.md"
            lines.append(_msg("edit-%d" % i, ts(), [_tool_use(tool, {"file_path": path})], root))
            if i == 2:
                lines.append({"type": "user", "timestamp": ts(), "cwd": root,
                              "message": {"role": "user", "content": [
                                  {"type": "tool_result", "tool_use_id": "toolu_readme", "content": CONSUMER_README}]}})
        top_uuid, top_tokens = "top-step-%d" % idx, 9000 + idx
        lines.append(_msg(top_uuid, ts(), [_tool_use("Bash", {"command": "python3 scripts/%s" % CATALOGUE_SCRIPTS[0]})], root,
                          extra_usage={"output_tokens": top_tokens}))
        if idx == 0:
            lines.append({"type": "user", "timestamp": ts(), "cwd": root,
                          "message": {"role": "user", "content": "please use " + CANARIES["prompt_sentinel"]}})
            lines.append({"type": "attachment", "timestamp": ts(), "attachment": {"type": "hook_cancelled", "timedOut": True}})
            lines.append({"type": "user", "timestamp": ts(), "cwd": root,
                          "message": {"role": "user", "content": "key %s name %s" % (CANARIES["fake_key"], CANARIES["personal_name"])}})
        path = os.path.join(tdir, sid + ".jsonl")
        with io.open(path, "w", encoding="utf-8") as fh:
            for rec in lines:
                fh.write(json.dumps(rec) + "\n")
        out.append({"session_id": sid, "path": path, "top_uuid": top_uuid, "top_tokens": top_tokens,
                    "hook_timeouts": 1 if idx == 0 else 0})
    return out


def referent(root, transcript_path, obs):
    """observatory.tally_ratios / unmodeled_top over records derived here, never through the worker."""
    records = []
    with io.open(transcript_path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            if rec.get("type") != "assistant":
                continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if block.get("type") != "tool_use":
                    continue
                tool, ti = block.get("name"), block.get("input") or {}
                if tool == "Bash":
                    names = obs.op_tokens_bash(ti.get("command"), cwd=root)
                    op, more = (names[0] if names else None), names[1:]
                elif tool == "Skill":
                    op, more = ti.get("skill"), []
                elif tool in ("Agent", "Task"):
                    op, more = ti.get("subagent_type"), []
                else:
                    op, more = None, []
                records.append({"hook_event_name": "PostToolUse", "tool_name": tool, "op_name": op, "op_names": more, "cwd": root})
    cat = obs.Catalog()
    classified, counter = [], {}
    sid = os.path.splitext(os.path.basename(transcript_path))[0]
    for r in records:
        cls, _ = cat.classify(r)
        classified.append(dict(r, op_class=cls, session_id=sid))
        if cls == "unmodeled":
            key = (r["op_name"], r["tool_name"])
            counter[key] = counter.get(key, 0) + 1
    block = obs.tally_ratios(classified)["sessions"].get(sid) or obs.ratio_block({})
    return block, obs.unmodeled_top(counter, catalog=cat.get(root))


def plant_inbox(state_root, transcripts, n_pointers=40, poison=False):
    inbox = os.path.join(state_root, "inbox")
    os.makedirs(inbox, exist_ok=True)
    for i in range(n_pointers):
        t = transcripts[i % len(transcripts)]
        write(os.path.join(inbox, "p%02d.json" % i),
              json.dumps({"session_id": "%s-p%02d" % (t["session_id"], i), "transcript_path": t["path"]}))
    if poison:
        write(os.path.join(inbox, "poison-not-json.json"), "{ this is not json")
        shifted = os.path.join(state_root, "poison-transcript.jsonl")
        write(shifted, json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                                     "content": [{"type": "tool_call", "name": "Bash"}]}}) + "\n")
        write(os.path.join(inbox, "poison-shifted.json"), json.dumps({"session_id": "poison-shifted", "transcript_path": shifted}))
    return inbox


def plant_rotation_inbox(inbox, transcripts, cap_plus_one=(1 << 20) + 1, per_file=2048, mtime_base=1700000000):
    os.makedirs(inbox, exist_ok=True)
    n_full = (cap_plus_one // per_file) - 1
    sizes = [per_file] * n_full + [cap_plus_one - per_file * n_full]
    planted = []
    for i, size in enumerate(sizes):
        t = transcripts[i % len(transcripts)]
        name = "r%05d.json" % i
        pointer = {"session_id": "%s-r%05d" % (t["session_id"], i), "transcript_path": t["path"], "pad": ""}
        pointer["pad"] = "x" * (size - len(json.dumps(pointer, sort_keys=True)))
        body = json.dumps(pointer, sort_keys=True)
        assert len(body.encode("utf-8")) == size
        p = os.path.join(inbox, name)
        write(p, body)
        os.utime(p, (mtime_base + i, mtime_base + i))
        planted.append((name, size))
    return planted


def bulky_copy(src, dest, repeat=300):
    lines = io.open(src, encoding="utf-8").read().splitlines(True)
    with io.open(dest, "w", encoding="utf-8") as fh:
        for _ in range(repeat):
            fh.writelines(lines)


def copy_consumer(src, dest, keep_ledger=False):
    shutil.copytree(src, dest)
    if not keep_ledger and os.path.isfile(ledger(dest)):
        os.remove(ledger(dest))


# --------------------------------------------------------------------------- the checks
def main():
    global TMP, SHIM_LOG
    TMP = tempfile.mkdtemp(prefix="hyp-om-worker-selftest-")
    SHIM_LOG = os.path.join(TMP, "claude-shim.log")
    shim = os.path.join(TMP, "shim-bin", "claude")
    write(shim, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"%s\"\nexit 0\n" % ("%s", SHIM_LOG))
    os.chmod(shim, 0o755)
    try:
        return _main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


def _main():
    obs = load_module(os.path.join(SCRIPTS, "observatory.py"), "observatory_for_selftest")
    om = load_module(WORKER, "om_worker_for_selftest")
    root = os.path.join(TMP, "consumer")
    tdir = os.path.join(TMP, "transcripts")
    build_consumer(root)
    transcripts = plant_transcripts(root, tdir)
    led = ledger(root)

    # 1. observe parity + 2. the Skill branch
    parity, details, markers = [], [], []
    stochastic_seen = 0
    for t in transcripts:
        rc, out, err = worker(root, "observe", t["path"])
        markers.append((rc, out.strip(), err.strip()))
        rows = [r for r in rows_of(led) if r and r.get("kind") == "session-observed" and r.get("session") == t["session_id"]]
        ref_block, ref_top = referent(root, t["path"], obs)
        if len(rows) != 1:
            parity.append(False)
            details.append("%s: %d rows" % (t["session_id"], len(rows)))
            continue
        r = rows[0]
        got_counts = r["counts"]
        want_counts = {"modeled_deterministic": ref_block["modeled_deterministic"], "modeled_stochastic": ref_block["modeled_stochastic"],
                       "delegated": ref_block["delegated"], "unmodeled": ref_block["unmodeled"]}
        same = (got_counts == want_counts and r["leverage"] == ref_block["leverage"] and r["determinism"] == ref_block["determinism"]
                and r["handoff_share"] == ref_block["handoff_share"] and r["top_step"] == {"msg": t["top_uuid"], "output_tokens": t["top_tokens"]}
                and r["unmodeled_top"] == ref_top and r["hook_timeouts"] == t["hook_timeouts"] and r["schema"] == 1
                and r["through"] == n_lines(t["path"]) and r["head"] == hashlib.sha256(read_bytes(t["path"])).hexdigest())
        stochastic_seen += got_counts["modeled_stochastic"]
        parity.append(same)
        if not same:
            details.append("%s: got %s want %s top %s" % (t["session_id"], json.dumps(got_counts), json.dumps(want_counts), r["unmodeled_top"][:3]))
    check("observe-parity-with-observatory-referent",
          all(parity) and all(rc == 0 and out == "om-worker 1 observe rc 0 written" and err == "" for rc, out, err in markers),
          details or markers)
    check("skill-in-catalogue-classifies-modeled-stochastic", stochastic_seen == 3, "modeled_stochastic total %d (want 3: one catalogued Skill per transcript)" % stochastic_seen)

    # 3-4. evaluate against model-lint.py
    rc, out, err = worker(root, "evaluate")
    ev_rows = [r for r in rows_of(led) if r and r.get("kind") == "model-evaluated"]
    tree = os.path.join(root, "operating-model", "ops")
    p = subprocess.run([PY, os.path.join(SCRIPTS, "model-lint.py"), tree], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env())
    ref_findings = sorted(l for l in p.stdout.decode("utf-8", "replace").splitlines() if l.split(" ", 1)[0] in ("ERROR", "WARN"))
    ev = ev_rows[0] if ev_rows else {}
    check("evaluate-lint-equals-model-lint-referent",
          rc == 0 and out.strip() == "om-worker 1 evaluate rc 0" and len(ev_rows) == 1 and ev.get("model_tree") == "operating-model/ops"
          and ev.get("lint", {}).get("findings") == ref_findings and ev["lint"]["errors"] == sum(1 for l in ref_findings if l.startswith("ERROR"))
          and any("E-LINK" in l and "gate-broken.md" in l for l in ev["lint"]["findings"]),
          (rc, out.strip(), ev.get("lint"), ref_findings[:3]))
    check("lint-parse-not-skipped", ev.get("lint", {}).get("parse_skipped") is False,
          "pyyaml unavailable to %s: model-lint.py skipped its parse (install pyyaml to run this check)" % PY)

    # 5. staleness true, compile command ran; then the flip
    rc, out, err = worker(root, "compile-check")
    cc_rows = [r for r in rows_of(led) if r and r.get("kind") == "model-evaluated"]
    pre = cc_rows[-1] if cc_rows else {}
    core_snapshot = read_bytes(led)   # everything a fresh tree with the same inputs must reproduce (check 12)
    write(os.path.join(root, "compiled", "SOP.md"), "<!-- COMPILED ARTIFACT -->\nSOP v2 (regenerated)\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "compiled v2 (regenerated, fresh)", date=COMMIT_DATES["compiled_v2"])
    rc2, out2, err2 = worker(root, "compile-check")
    post = [r for r in rows_of(led) if r and r.get("kind") == "model-evaluated"][-1]
    check("compile-staleness-stale-then-fresh",
          rc == 0 and rc2 == 0 and pre.get("compiled", {}).get("stale") is True and post["compiled"]["stale"] is False
          and pre.get("compile_command") == {"command": "sh bin/compile-stub.sh", "rc": 0}
          and os.path.isfile(os.path.join(root, COMPILE_WITNESS)) and post["compiled"]["compiled_path"] == "compiled/SOP.md"
          and post["compiled"]["compiled_commit_date"] > post["compiled"]["model_commit_date"] and post["date"] == post["compiled"]["model_commit_date"],
          (pre.get("compiled"), post.get("compiled"), pre.get("compile_command")))

    # 6. the newest compiled artifact by commit date wins (an older file that sorts first does not)
    write(os.path.join(root, "compiled", "AAA-older.md"), "<!-- COMPILED ARTIFACT -->\nolder sibling\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "an older compiled sibling", date=COMMIT_DATES["compiled_older"])
    rc3, _, _ = worker(root, "compile-check")
    newest = [r for r in rows_of(led) if r and r.get("kind") == "model-evaluated"][-1]
    check("newest-compiled-artifact-wins", rc3 == 0 and newest["compiled"]["compiled_path"] == "compiled/SOP.md" and newest["compiled"]["stale"] is False,
          newest.get("compiled"))

    # 7. redaction by omission
    blob = read_bytes(led).decode("utf-8", "replace")
    leaked = [k for k, v in CANARIES.items() if v in blob]
    keys = [k for k in FORBIDDEN_KEYS if '"%s"' % k in blob]
    abs_strings = [s for r in rows_of(led) if r for s in om._walk_strings(r) if s.startswith("/") or s.startswith("~/")]
    check("redaction-by-omission-no-canary-in-ledger", not leaked and not keys and not abs_strings and all(r is not None for r in rows_of(led)),
          {"leaked": leaked, "keys": keys, "abs": abs_strings[:3]})

    # 8. the self-check nets, in-process on a copy
    net_root = os.path.join(TMP, "nets", "consumer")
    copy_consumer(root, net_root)
    base = {"kind": "session-observed", "schema": 1, "session": "nets", "landed_in": "root", "date": None}
    bad_rows = {
        "forbidden-key-tool_input": dict(base, tool_input={"command": "ls"}),
        "forbidden-key-prompt": dict(base, prompt="hi"),
        "marker-users": dict(base, note="/Users/someone/x"),
        "marker-home": dict(base, note="$HOME/x"),
        "absolute-path": dict(base, note="/private/tmp/scratch/scn-transcript-0.jsonl"),
        "absolute-path-tilde": dict(base, note="~/.ssh/id_ed25519"),
        "absolute-path-embedded": dict(base, note="see /opt/tool/bin/x for details"),
        "relative-escape": dict(base, note="../transcripts/scn-transcript-0.jsonl"),
        "relative-escape-embedded": dict(base, note="from ../../elsewhere/file"),
        "well-known-root-bare": dict(base, note="Users/nobody/outside-the-repo/secret.txt"),
        "identity-login": dict(base, user="zebrafish"),
    }
    outcomes, stderr_lines = {}, {}
    before = read_bytes(ledger(net_root))
    saved_user = os.environ.get("USER")
    os.environ["USER"] = "zebrafish"
    try:
        for name, row in bad_rows.items():
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                outcomes[name] = om.append_row(net_root, row)
            stderr_lines[name] = buf.getvalue().splitlines()
    finally:
        if saved_user is None:
            os.environ.pop("USER", None)
        else:
            os.environ["USER"] = saved_user
    unchanged = read_bytes(ledger(net_root)) == before
    all_refused = all(v == "refused-redaction" for v in outcomes.values())
    one_line_each = all(len(v) == 1 and "refus" in v[0].lower() for v in stderr_lines.values())
    no_bytes_named = all(not any(tok in l for tok in ("zebrafish", "/private/tmp", "../", "Users/nobody")) for v in stderr_lines.values() for l in v)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        clean = om.append_row(net_root, dict(base, session="nets-clean", model_tree="operating-model/ops",
                                             findings=["ERROR E-LINK policies/gate-broken.md: then: 'command/x' resolves to no node file"],
                                             compiled_path="compiled/SOP.md", command="sh bin/compile-stub.sh", op="a/../b"))
    check("self-check-refuses-each-net", all_refused and one_line_each and unchanged and no_bytes_named and clean == "written" and buf.getvalue() == "",
          {"outcomes": outcomes, "stderr": {k: v[:1] for k, v in stderr_lines.items() if len(v) != 1}, "clean": clean, "unchanged": unchanged})

    # 9. the known-answer control
    mut = {}
    for name, mode in (("M-redact", "redact-disabled"), ("M-blind", "blind")):
        m_root = os.path.join(TMP, "mutant-" + mode, "consumer")
        copy_consumer(root, m_root)
        rc, out, err = worker(m_root, "observe", transcripts[0]["path"], extra_env={"HYP_OM_MUTANT": mode})
        text = read_bytes(ledger(m_root)).decode("utf-8", "replace")
        mut[name] = {"rc": rc, "rows": n_lines(ledger(m_root)), "refusal_lines": sum(1 for l in err.splitlines() if "refus" in l.lower()),
                     "leaked": any(c in text for c in CANARIES.values()), "marker": out.strip()}
    check("mutant-controls-redact-refuses-blind-leaks",
          mut["M-redact"]["rows"] == 0 and mut["M-redact"]["refusal_lines"] == 1 and mut["M-redact"]["rc"] == 0
          and mut["M-redact"]["marker"] == "om-worker 1 observe rc 0 refused-redaction"
          and mut["M-blind"]["rows"] == 1 and mut["M-blind"]["leaked"] and mut["M-blind"]["rc"] == 0, mut)

    # 10. idempotence
    idem = os.path.join(tdir, "scn-idem-fresh.jsonl")
    shutil.copyfile(transcripts[0]["path"], idem)
    worker(root, "observe", idem)
    n1 = n_lines(led)
    rc, out, _ = worker(root, "observe", idem)
    check("idempotent-rerun-appends-nothing", rc == 0 and n_lines(led) == n1 and out.strip() == "om-worker 1 observe rc 0 duplicate", (n1, n_lines(led), out.strip()))

    # 11. cursor + latest-wins
    cur = os.path.join(tdir, "scn-cursor-fresh.jsonl")
    original = io.open(transcripts[0]["path"], encoding="utf-8").read()
    lines = original.splitlines(True)
    with io.open(cur, "w", encoding="utf-8") as fh:
        fh.writelines(lines[:int(len(lines) * 0.6)])
    worker(root, "observe", cur)
    with io.open(cur, "w", encoding="utf-8") as fh:
        fh.write(original)
    worker(root, "observe", cur)
    sess = [r for r in rows_of(led) if r and r.get("kind") == "session-observed" and r.get("session") == "scn-cursor-fresh"]
    throughs = [r["through"] for r in sess]
    rc, latest_out, _ = worker(root, "latest")
    view = [l for l in latest_out.splitlines(True) if l.strip() and json.loads(l).get("session") == "scn-cursor-fresh"]
    full_root = os.path.join(TMP, "cursor-full", "consumer")
    copy_consumer(root, full_root)
    worker(full_root, "observe", cur)
    full_lines = read_bytes(ledger(full_root)).decode("utf-8").splitlines(True)
    check("cursor-rises-and-latest-equals-full-row",
          len(sess) == 2 and throughs == sorted(throughs) and len(set(throughs)) == 2 and rc == 0
          and len(view) == 1 and len(full_lines) == 1 and view[0] == full_lines[0], (throughs, len(view), len(full_lines)))

    # 12. determinism across trees
    root2 = os.path.join(TMP, "second", "consumer")
    build_consumer(root2)
    for t in transcripts:
        worker(root2, "observe", t["path"])
    worker(root2, "evaluate")
    worker(root2, "compile-check")
    # 3 session-observed rows + ONE model-evaluated row: evaluate and compile-check write the same canonical
    # bytes (both run the compile check), so the second is a dedupe duplicate
    check("rows-byte-identical-across-two-trees", read_bytes(ledger(root2)) == core_snapshot and len(core_snapshot.splitlines()) == 4,
          "%d vs %d rows" % (len(read_bytes(ledger(root2)).splitlines()), len(core_snapshot.splitlines())))

    # 13. N cap
    b_root, b_state = os.path.join(TMP, "bounds", "consumer"), os.path.join(TMP, "bounds", "state")
    copy_consumer(root, b_root)
    plant_inbox(b_state, transcripts, n_pointers=40)
    t_start = time.monotonic()
    rc1, out1, _ = worker(b_root, "drain", "--inbox", b_state)
    wall1 = time.monotonic() - t_start
    landed1, inbox1 = n_lines(ledger(b_root)), len(glob.glob(os.path.join(b_state, "inbox", "*.json")))
    rc2, out2, _ = worker(b_root, "drain", "--inbox", b_state)
    landed2, inbox2 = n_lines(ledger(b_root)), len(glob.glob(os.path.join(b_state, "inbox", "*.json")))
    res1 = json.loads(out1.split("rc 0 ", 1)[1]) if "rc 0 " in out1 else {}
    check("drain-n-cap-20-then-rest", rc1 == 0 and rc2 == 0 and landed1 == 20 and inbox1 == 20 and landed2 == 40 and inbox2 == 0 and wall1 < 60
          and res1.get("rows") == 20 and res1.get("landed") == 20 and len(glob.glob(os.path.join(b_state, "processed", "*.json"))) == 40,
          (landed1, inbox1, landed2, inbox2, round(wall1, 2), res1))

    # 14. T cap on the monotonic clock (in-process: a zero budget processes nothing and moves nothing)
    t_root, t_state = os.path.join(TMP, "tcap", "consumer"), os.path.join(TMP, "tcap", "state")
    copy_consumer(root, t_root)
    plant_inbox(t_state, transcripts, n_pointers=3)
    res = om.drain(t_root, SCRIPTS, t_state, n_cap=20, t_cap=0.0)
    check("drain-t-cap-monotonic", res["landed"] == 0 and res["rows"] == 0 and len(glob.glob(os.path.join(t_state, "inbox", "*.json"))) == 3
          and not os.path.isfile(ledger(t_root)), res)

    # 15. the free-space floor
    f_root, f_state = os.path.join(TMP, "floor", "consumer"), os.path.join(TMP, "floor", "state")
    copy_consumer(root, f_root, keep_ledger=True)
    plant_inbox(f_state, transcripts, n_pointers=4)
    before_f = read_bytes(ledger(f_root))
    rc, out, err = worker(f_root, "drain", "--inbox", f_state, extra_env={"HYP_OM_FREE_FLOOR_BYTES": str(1 << 62)})
    buf = io.StringIO()
    saved = os.environ.get("HYP_OM_FREE_FLOOR_BYTES")
    os.environ["HYP_OM_FREE_FLOOR_BYTES"] = str(1 << 62)
    try:
        with contextlib.redirect_stderr(buf):
            floor_status = om.append_row(f_root, dict(base, session="floor"))
    finally:
        if saved is None:
            os.environ.pop("HYP_OM_FREE_FLOOR_BYTES", None)
        else:
            os.environ["HYP_OM_FREE_FLOOR_BYTES"] = saved
    check("free-space-floor-refuses-once", rc == 0 and read_bytes(ledger(f_root)) == before_f
          and sum(1 for l in err.splitlines() if "refus" in l.lower()) == 1 and len(err.splitlines()) == 1
          and len(glob.glob(os.path.join(f_state, "inbox", "*.json"))) == 4 and floor_status == "refused-floor"
          and len(buf.getvalue().splitlines()) == 1, (rc, err.strip()[:120], floor_status))

    # 16. rotation
    r_root, r_state = os.path.join(TMP, "rot", "consumer"), os.path.join(TMP, "rot", "state")
    copy_consumer(root, r_root)
    r_inbox = os.path.join(r_state, "inbox")
    planted = plant_rotation_inbox(r_inbox, transcripts)
    before_bytes = sum(os.path.getsize(f) for f in glob.glob(os.path.join(r_inbox, "*.json")))
    rc, out, _ = worker(r_root, "drain", "--inbox", r_state)
    r_rows = [r for r in rows_of(ledger(r_root)) if r]
    overflow = [r for r in r_rows if r.get("kind") == "spool-overflow"]
    moved_files = sorted(glob.glob(os.path.join(r_state, "overflow", "*", "*.json")))
    moved_bytes = sum(os.path.getsize(f) for f in moved_files)
    check("inbox-rotation-at-1mib", rc == 0 and before_bytes == (1 << 20) + 1 and len(overflow) == 1 and set(overflow[0]) == OVERFLOW_KEYS
          and overflow[0]["moved"] == len(moved_files) >= 1 and [os.path.basename(f) for f in moved_files] == [n for n, _ in planted[:len(moved_files)]]
          and before_bytes - moved_bytes <= (1 << 20) and not any(r.get("kind") == "quarantine" for r in r_rows)
          and sum(1 for r in r_rows if r.get("kind") == "session-observed") == 20,
          (before_bytes, len(overflow), [os.path.basename(f) for f in moved_files][:3], overflow[:1]))

    # 17. poison -> quarantine
    p_root, p_state = os.path.join(TMP, "poison", "consumer"), os.path.join(TMP, "poison", "state")
    copy_consumer(root, p_root)
    plant_inbox(p_state, transcripts, n_pointers=0, poison=True)
    rc, out, _ = worker(p_root, "drain", "--inbox", p_state)
    q_rows = [r for r in rows_of(ledger(p_root)) if r and r.get("kind") == "quarantine"]
    check("poison-files-quarantined", rc == 0 and len(q_rows) == 2 and all(set(r) == QUARANTINE_KEYS for r in q_rows)
          and sorted(r["file"] for r in q_rows) == ["poison-not-json.json", "poison-shifted.json"]
          and all(re.match(r"^[A-Za-z]+Error$", r["reason"]) for r in q_rows)
          and len(glob.glob(os.path.join(p_state, "inbox", "*.json"))) == 0 and len(glob.glob(os.path.join(p_state, "quarantine", "*.json"))) == 2,
          (rc, q_rows))

    # 18. SIGKILL mid-drain, then resume
    k_root, k_state, k_tdir = os.path.join(TMP, "kill", "consumer"), os.path.join(TMP, "kill", "state"), os.path.join(TMP, "kill", "bulky")
    copy_consumer(root, k_root)
    os.makedirs(k_tdir)
    bulky = []
    for t in transcripts:
        dest = os.path.join(k_tdir, os.path.basename(t["path"]))
        bulky_copy(t["path"], dest)
        bulky.append(dict(t, path=dest))
    plant_inbox(k_state, bulky, n_pointers=40)
    k_led = ledger(k_root)
    proc = subprocess.Popen([PY, "-B", WORKER, "drain", "--root", k_root, "--inbox", k_state], env=env(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    alive_at_kill, rows_at_kill = None, 0
    deadline = time.time() + 90
    while time.time() < deadline:
        rows_at_kill = n_lines(k_led)
        if rows_at_kill >= 5:
            alive_at_kill = proc.poll() is None
            proc.kill()
            break
        time.sleep(0.005)
    else:
        alive_at_kill = proc.poll() is None
        proc.kill()
    proc.wait(timeout=30)
    all_parse = all(r is not None for r in rows_of(k_led))
    stranded_before = len(glob.glob(os.path.join(k_state, "processing", "*.json")))
    rc, out, _ = worker(k_root, "drain", "--inbox", k_state)
    res = json.loads(out.split("rc 0 ", 1)[1]) if "rc 0 " in out else {}
    after = read_bytes(k_led).splitlines()
    stranded_after = len(glob.glob(os.path.join(k_state, "processing", "*.json")))
    check("sigkill-mid-drain-resumes-without-duplicates-or-strands",
          alive_at_kill and 5 <= rows_at_kill < 20 and all_parse and rc == 0 and len(after) == len(set(after)) and stranded_after == 0
          and res.get("recovered") == stranded_before and 1 <= stranded_before and res.get("landed") == 20 and len(after) <= 40,
          {"alive": alive_at_kill, "rows_at_kill": rows_at_kill, "parse": all_parse, "stranded": (stranded_before, stranded_after),
           "res": res, "dups": len(after) - len(set(after))})

    # 19. schema tolerance: a hand-appended schema: 2 row is read, reported once, never rewritten
    hand = {"kind": "session-observed", "schema": 2, "session": "hand-appended", "unknown_field": "x", "through": 0, "head": "0",
            "landed_in": "root", "counts": {}, "leverage": None, "determinism": None, "handoff_share": None,
            "top_step": {"msg": None, "output_tokens": 0}, "unmodeled_top": [], "hook_timeouts": 0, "date": None}
    with open(ledger(p_root), "ab") as fh:
        fh.write((json.dumps(hand, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    before_s = read_bytes(ledger(p_root))
    rc, out, _ = worker(p_root, "status")
    st = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    worker(p_root, "drain", "--inbox", p_state)
    check("schema-2-row-read-reported-untouched", rc == 0 and st.get("schemas", {}).get("2") == 1 and st.get("ledger") == LEDGER_REL
          and not st["ledger"].startswith("/") and st.get("rows") == 3 and read_bytes(ledger(p_root)) == before_s, st)

    # 20. the configured ledger path
    o_root = os.path.join(TMP, "override", "consumer")
    copy_consumer(root, o_root)
    cfg = json.loads(read_bytes(os.path.join(o_root, ".claude", "hyp.json")).decode("utf-8"))
    cfg["om_feedback_file"] = "ledger/custom-feedback.jsonl"
    write(os.path.join(o_root, ".claude", "hyp.json"), json.dumps(cfg) + "\n")
    rc, out, _ = worker(o_root, "observe", transcripts[1]["path"])
    rc_s, out_s, _ = worker(o_root, "status")
    st = json.loads(out_s.strip().splitlines()[-1]) if out_s.strip() else {}
    check("om-feedback-file-override-honoured", rc == 0 and n_lines(os.path.join(o_root, "ledger", "custom-feedback.jsonl")) == 1
          and not os.path.isfile(ledger(o_root)) and st.get("ledger") == "ledger/custom-feedback.jsonl" and st.get("rows") == 1, (rc, st))

    # 21. the scaffold's union row + the advisory check
    i_root = os.path.join(TMP, "init")
    os.makedirs(i_root)
    git(i_root, "init", "-q", "-b", "main")
    rc1, out1, err1 = run([PY, "-B", INIT, i_root, "--profile", "capture", "--context", "selftest"], cwd=i_root)
    ga = read_bytes(os.path.join(i_root, ".gitattributes")).decode("utf-8", "replace")
    attr = git(i_root, "check-attr", "merge", "--", LEDGER_REL).strip()
    rc2, out2, _ = run([PY, "-B", INIT, i_root, "--profile", "capture", "--context", "selftest"], cwd=i_root)
    rc_c1, out_c1, _ = run([PY, "-B", CHECK, "--root", i_root])
    bare = os.path.join(TMP, "bare")
    os.makedirs(bare)
    git(bare, "init", "-q", "-b", "main")
    write(os.path.join(bare, "ledger", "om-feedback.jsonl"), '{"kind":"session-observed","schema":1,"date":null}\n')
    write(os.path.join(bare, "ledger", "ledger.jsonl"), "")
    rc_c2, out_c2, _ = run([PY, "-B", CHECK, "--root", bare])
    check("init-writes-union-row-and-check-attr",
          rc1 == 0 and "%s merge=union" % LEDGER_REL in ga.splitlines() and attr == "%s: merge: union" % LEDGER_REL
          and rc2 == 0 and any(l.startswith("unchanged .gitattributes") for l in out2.splitlines()) and rc_c1 == 0
          and rc_c2 == 1 and "MERGE-ATTR-MISSING\t%s\tunion" % LEDGER_REL in out_c2,
          (rc1, attr, rc_c1, rc_c2, out_c2.strip().replace("\n", " | ")[:200], err1[-200:]))

    # 22. two worktrees, one appended row each, a union merge
    m_root = os.path.join(TMP, "merge", "main")
    os.makedirs(m_root)
    git(m_root, "init", "-q", "-b", "main")
    run([PY, "-B", INIT, m_root, "--profile", "capture", "--context", "selftest"], cwd=m_root)
    git(m_root, "add", "-A")
    git(m_root, "commit", "-q", "-m", "scaffold")
    wts = []
    for name, t in (("b1", transcripts[0]), ("b2", transcripts[1])):
        wt = os.path.join(TMP, "merge", name)
        git(m_root, "worktree", "add", "-q", wt, "-b", name)
        rc, out, err = worker(wt, "observe", t["path"])
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "row %s" % name)
        wts.append(wt)
    rc_m, _, err_m = run(["git", "-C", wts[0], "-c", "commit.gpgsign=false", "merge", "--no-edit", "b2"])
    merged = read_bytes(ledger(wts[0])).decode("utf-8", "replace")
    m_rows = [json.loads(l) for l in merged.splitlines() if l.strip()]
    check("union-merge-keeps-both-appended-rows", rc_m == 0 and len(m_rows) == 2 and {r["session"] for r in m_rows} == {"scn-transcript-0", "scn-transcript-1"}
          and "<<<<<<<" not in merged and merged.endswith("\n") and git(wts[0], "status", "--porcelain").strip() == "", (rc_m, err_m[-200:], len(m_rows)))

    # 23. zero model calls
    spawns = read_bytes(SHIM_LOG).decode("utf-8", "replace").splitlines()
    check("zero-model-calls-claude-shim", len(spawns) == 0, spawns[:2])

    # 24. the installed copy: stdlib only, no home or lab path
    src = read_bytes(WORKER).decode("utf-8")
    tree_ = ast.parse(src)
    imported = set()
    for node in tree_.body:
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    stdlib = {"errno", "fcntl", "getpass", "glob", "hashlib", "json", "os", "re", "shutil", "socket", "subprocess", "sys", "time"}
    lab_tokens = ("/Users/", "cause-n-effect", "experiments/runs/", "experiments/deploy")
    check("installed-copy-stdlib-and-no-lab-paths", imported <= stdlib and not any(tok in src for tok in lab_tokens)
          and os.access(WORKER, os.X_OK), (sorted(imported - stdlib), [t for t in lab_tokens if t in src]))

    ok = all(RESULTS)
    print("om-worker sha256 %s (the installed copy this run exercised)" % hashlib.sha256(read_bytes(WORKER)).hexdigest())
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(RESULTS), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
