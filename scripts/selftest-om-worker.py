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
  prior-om-feedback-file-survives-reinit     a prior om_feedback_file (and ledger_file, and a non-string
                                             consumer key) survive a re-run of init-scaffold; the union
                                             row for the configured path is rendered and check-attr sees it
  absolute-override-falls-back-in-every-reader  an absolute om_feedback_file renders the DEFAULT row (never
                                             a mangled one), the worker writes to the default path and
                                             merge-attrs-check expects the default: one rule, three readers
  union-merge-keeps-both-appended-rows       two worktrees each append one row; the merge keeps both
  zero-model-calls-claude-shim               a PATH-first `claude` shim logs zero spawns
  installed-copy-stdlib-and-no-lab-paths     stdlib imports only; no home or lab path in the shipped file

Ported from lab H-DRAFT-a4a14ff4-om-outbox-carry-forward (kept 2026-09-14, five counted looks
A1-A5 5/5, VERDICT.json + VERIFY.md, journal fragment 0537) on a scratch repo with a main
checkout and two linked worktrees, one removed after its pointer is written:

  outbox-landing-for-missing-root            a pointer whose `root` no longer exists lands its row in
                                             the repository-keyed outbox with `landed_in: outbox`,
                                             never in any checkout's ledger; the live worktree's own
                                             pointer still lands `root` in its own ledger
  carry-forward-exactly-once                 the next drain for a live checkout of the same repository
                                             carries the outbox row in with `landed_in: carried` and
                                             `carried_from`, byte-identical otherwise, renames the
                                             outbox, and a further drain carries nothing twice
  not-a-checkout-root-quarantines-unconditionally  a pointer whose root exists but was never a git
                                             checkout of its recorded `common_dir` quarantines the same
                                             way regardless of the outbox rule; so does (ship fix
                                             round 3 B1) a pointer whose root is GONE but that carries
                                             no `common_dir` -- it must never borrow the draining
                                             repository's outbox and be carried into a repository it
                                             never named
  pointer-with-no-root-field-drains-as-before  a pointer carrying no `root`/`common_dir` (the pre-outbox
                                             shape) lands into the drain target exactly as before
  concurrent-drains-carry-outbox-exactly-once  two DIFFERENT live checkouts of one repository (main
                                             and a linked worktree) draining a 400-row outbox at once
                                             (pre-mortem (ii), ship fix round 1 B1: claim the outbox by
                                             rename BEFORE reading it, not after) land every row in
                                             exactly one of the two ledgers, never both, and the loser
                                             finds no outbox file rather than re-carrying it
  malformed-root-quarantines-not-outbox     (ship fix round 1 A2) a pointer with `root: null` quarantines
                                             as a malformed pointer instead of entering the outbox path
                                             under a meaningless key
  outbox-write-blocks-behind-concurrent-carry-no-row-lost  (ship fix round 2 B1) a write into the
                                             outbox for a dead-worktree pointer, started while a
                                             carry is confirmed mid-body (deterministic, via a
                                             monkeypatched delay), must block on the same
                                             `.outbox-carry.lock` the carrier holds rather than
                                             race straight through -- proven on the wall clock --
                                             and land safely once the carry finishes
  git-timeout-refuses-drain-not-fallback     (ship fix round 4 B1) a `git rev-parse` that cannot answer
                                             for the drain's own --root within GIT_TIMEOUT_S (a shim
                                             that sleeps past it) refuses the whole drain with one
                                             stderr line and an all-zero result -- never a silent
                                             fallback to the sha256 single-path inbox
  git-timeout-defers-live-pointer-not-quarantine  (ship fix round 4 B1) a LIVE worktree's pointer whose
                                             root git cannot answer for in time is put back into
                                             inbox/ for the next wake (`deferred`), no quarantine row;
                                             the next drain with a working git lands it `root`
  concurrent-wakes-same-inbox-process-each-pointer-once  (ship fix round 4 B2) two live checkouts of
                                             one repository waking on the SAME shared inbox (15
                                             dead-root + 5 live pointers) land every pointer exactly
                                             once, no false quarantine row, processing/ empty --
                                             the second wake backs off on `.drain.lock` rather than
                                             sweep the first's in-flight pointer back into inbox/
  resumed-claim-dedupes-against-prior-carrier  (ship fix round 4 A1) a `.carrying.jsonl` claim left by
                                             one checkout after it carried 2 of 4 rows, resumed by a
                                             DIFFERENT checkout, carries only the 2 rows the first
                                             never landed (dedupe against the prior carrier's ledger
                                             via the claim's `.roots` sidecar), then finalizes
  refused-claim-files-row-finalizes-then-claims-live-outbox  (ship fix round 5 B2) a leftover claim
                                             holding a row the target ledger refuses on EVERY attempt
                                             (the redaction self-check) no longer starves the
                                             repository: the refused row is filed verbatim into
                                             `outbox.<epoch>.refused.jsonl`, one quarantine row names
                                             that file, the claim finalizes, and the live
                                             `outbox.jsonl` behind it is claimed and carried in the
                                             SAME drain; a second drain changes nothing
  floor-refusal-halts-carry-leaves-claim-intact  (ship fix round 5 B2) a free-space-floor refusal
                                             mid-carry (the host's state, not the row's) halts the
                                             carry with the claim left in place and nothing filed;
                                             the next carry resumes it AND claims the live outbox

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
import threading
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

_MISSING = object()  # sentinel: ob_pointer's default distinguishes "omit the field" from None


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
    try:
        print("host load-avg %.2f %.2f %.2f (covariate: the concurrent and wall-clock cases are time-sensitive)"
              % os.getloadavg())
    except (OSError, AttributeError):
        pass
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
    # compile-check regenerates operating-model/ops/model.md as a working-tree-only side effect
    # (H-DRAFT-4e06e157-om-rows-merge-shape); this fixture keeps model.md TRACKED (a consumer
    # that has not re-run /hyp:init to retire it yet), so discard that regen before the commits
    # below or an unrelated `git add -A` would sweep it in and skew the staleness dates this
    # check measures.
    git(root, "checkout", "--", "operating-model/ops/model.md")
    write(os.path.join(root, "compiled", "SOP.md"), "<!-- COMPILED ARTIFACT -->\nSOP v2 (regenerated)\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "compiled v2 (regenerated, fresh)", date=COMMIT_DATES["compiled_v2"])
    rc2, out2, err2 = worker(root, "compile-check")
    post = [r for r in rows_of(led) if r and r.get("kind") == "model-evaluated"][-1]
    git(root, "checkout", "--", "operating-model/ops/model.md")
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
    k_tdir = os.path.join(TMP, "kill", "bulky")
    os.makedirs(k_tdir)
    bulky = []
    for t in transcripts:
        dest = os.path.join(k_tdir, os.path.basename(t["path"]))
        bulky_copy(t["path"], dest)
        bulky.append(dict(t, path=dest))
    # A3 (ship fix round 4): the kill must land while a pointer sits in processing/ for the resume
    # path to be exercised at all; the gap between one pointer's move to processed/ and the next
    # one's move into processing/ is microseconds, but a loaded host can park the drain there
    # exactly when the signal arrives (seen once at load 12.5: stranded 0, everything else
    # passing). A miss is a harness timing miss, not a worker behaviour -- retry on a fresh tree.
    k_attempt = 0
    for k_attempt in range(3):
        k_root, k_state = os.path.join(TMP, "kill", "consumer-%d" % k_attempt), os.path.join(TMP, "kill", "state-%d" % k_attempt)
        copy_consumer(root, k_root)
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
        if stranded_before >= 1 or not alive_at_kill or not (5 <= rows_at_kill < 20):
            break
    rc, out, _ = worker(k_root, "drain", "--inbox", k_state)
    res = json.loads(out.split("rc 0 ", 1)[1]) if "rc 0 " in out else {}
    after = read_bytes(k_led).splitlines()
    stranded_after = len(glob.glob(os.path.join(k_state, "processing", "*.json")))
    check("sigkill-mid-drain-resumes-without-duplicates-or-strands",
          alive_at_kill and 5 <= rows_at_kill < 20 and all_parse and rc == 0 and len(after) == len(set(after)) and stranded_after == 0
          and res.get("recovered") == stranded_before and 1 <= stranded_before and res.get("landed") == 20 and len(after) <= 40,
          {"alive": alive_at_kill, "rows_at_kill": rows_at_kill, "parse": all_parse, "stranded": (stranded_before, stranded_after),
           "res": res, "dups": len(after) - len(set(after)), "attempt": k_attempt})

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

    # 21b. a prior om_feedback_file (and ledger_file) override survives a re-init and its union row is rendered
    #      (om-worker ship fix round 1, refuter B1: the cfg rewrite kept only DEFAULTS keys)
    r_root = os.path.join(TMP, "reinit")
    os.makedirs(r_root)
    git(r_root, "init", "-q", "-b", "main")
    run([PY, "-B", INIT, r_root, "--profile", "capture", "--context", "selftest"], cwd=r_root)
    r_cfg_path = os.path.join(r_root, ".claude", "hyp.json")
    r_cfg = json.loads(read_bytes(r_cfg_path).decode("utf-8"))
    r_roles = {"maintainer": ["owner@example.invalid"]}
    r_cfg.update({"om_feedback_file": "ledger/custom-feedback.jsonl", "ledger_file": "ledger/work-ledger.jsonl",
                  "decision_roles": r_roles, "decision_door_legacy_max_id": 35})
    write(r_cfg_path, json.dumps(r_cfg) + "\n")
    rc_r, out_r, err_r = run([PY, "-B", INIT, r_root, "--profile", "capture", "--context", "selftest"], cwd=r_root)
    r_after = json.loads(read_bytes(r_cfg_path).decode("utf-8"))
    r_ga = read_bytes(os.path.join(r_root, ".gitattributes")).decode("utf-8", "replace").splitlines()
    r_attr = git(r_root, "check-attr", "merge", "--", "ledger/custom-feedback.jsonl", "ledger/work-ledger.jsonl").strip().splitlines()
    write(os.path.join(r_root, "ledger", "custom-feedback.jsonl"), '{"kind":"session-observed","schema":1,"date":null}\n')
    write(os.path.join(r_root, "ledger", "work-ledger.jsonl"), "")
    rc_rc, out_rc, _ = run([PY, "-B", CHECK, "--root", r_root])
    rc_r2, out_r2, _ = run([PY, "-B", INIT, r_root, "--profile", "capture", "--context", "selftest"], cwd=r_root)
    check("prior-om-feedback-file-survives-reinit",
          rc_r == 0 and r_after.get("om_feedback_file") == "ledger/custom-feedback.jsonl"
          and r_after.get("ledger_file") == "ledger/work-ledger.jsonl" and r_after.get("decision_roles") == r_roles
          and r_after.get("decision_door_legacy_max_id") == 35 and r_after.get("profile") == "capture"
          and "ledger/custom-feedback.jsonl merge=union" in r_ga and "ledger/work-ledger.jsonl merge=union" in r_ga
          and sorted(r_attr) == ["ledger/custom-feedback.jsonl: merge: union", "ledger/work-ledger.jsonl: merge: union"]
          and rc_rc == 0 and "MERGE-ATTR-MISSING" not in out_rc
          and rc_r2 == 0 and any(l.startswith("unchanged .claude/hyp.json") for l in out_r2.splitlines()),
          (rc_r, sorted(r_after), r_attr, rc_rc, out_rc.strip().replace("\n", " | ")[:200], err_r[-200:]))

    # 21c. one validator, three readers (refuter A1): an absolute om_feedback_file falls back to the default everywhere
    a_root = os.path.join(TMP, "absfb")
    os.makedirs(a_root)
    git(a_root, "init", "-q", "-b", "main")
    run([PY, "-B", INIT, a_root, "--profile", "capture", "--context", "selftest"], cwd=a_root)
    a_cfg_path = os.path.join(a_root, ".claude", "hyp.json")
    a_cfg = json.loads(read_bytes(a_cfg_path).decode("utf-8"))
    a_cfg["om_feedback_file"] = "/abs/fb.jsonl"
    write(a_cfg_path, json.dumps(a_cfg) + "\n")
    rc_a, _, err_a = run([PY, "-B", INIT, a_root, "--profile", "capture", "--context", "selftest"], cwd=a_root)
    a_ga = read_bytes(os.path.join(a_root, ".gitattributes")).decode("utf-8", "replace").splitlines()
    rc_ao, _, _ = worker(a_root, "observe", transcripts[2]["path"])
    rc_as, out_as, _ = worker(a_root, "status")
    a_st = json.loads(out_as.strip().splitlines()[-1]) if out_as.strip() else {}
    rc_ac, out_ac, _ = run([PY, "-B", CHECK, "--root", a_root])
    check("absolute-override-falls-back-in-every-reader",
          rc_a == 0 and "%s merge=union" % LEDGER_REL in a_ga and not any(l.split()[0] in ("abs/fb.jsonl", "/abs/fb.jsonl") for l in a_ga if l.strip())
          and rc_ao == 0 and n_lines(ledger(a_root)) == 1 and a_st.get("ledger") == LEDGER_REL
          and rc_ac == 0 and "MERGE-ATTR-MISSING" not in out_ac,
          (rc_a, [l for l in a_ga if "fb" in l], rc_ao, a_st, rc_ac, out_ac.strip().replace("\n", " | ")[:200], err_a[-200:]))

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

    # -------------------------------------------------------------- outbox carry-forward (H-DRAFT-a4a14ff4)
    # 23. a scratch repository with a main checkout and two linked worktrees (wt_a, wt_b); wt_a is
    # removed after its pointer is written, so its root is gone before any drain runs.
    ob_root = os.path.join(TMP, "outbox", "main")
    os.makedirs(ob_root)
    git(ob_root, "init", "-q", "-b", "main")
    write(os.path.join(ob_root, "f.txt"), "seed\n")
    git(ob_root, "add", "-A")
    git(ob_root, "commit", "-q", "-m", "seed")
    ob_wt_a = os.path.join(TMP, "outbox", "wt_a")
    ob_wt_b = os.path.join(TMP, "outbox", "wt_b")
    git(ob_root, "worktree", "add", "-q", ob_wt_a, "-b", "wt_a")
    git(ob_root, "worktree", "add", "-q", ob_wt_b, "-b", "wt_b")
    ob_common_dir = git(ob_root, "rev-parse", "--git-common-dir").strip()
    if not os.path.isabs(ob_common_dir):
        ob_common_dir = os.path.abspath(os.path.join(ob_root, ob_common_dir))
    # realpath, matching om-worker.py's own resolve_common_dir: a `tempfile.mkdtemp()` root lives
    # under macOS's symlinked /var (-> /private/var), and the wake lane that will write this field
    # in production uses the worker's own resolution, never an unresolved join.
    ob_common_dir = os.path.realpath(ob_common_dir)
    ob_state = os.path.join(TMP, "outbox", "state")
    ob_inbox = os.path.join(ob_state, "inbox")
    os.makedirs(ob_inbox, exist_ok=True)

    def ob_pointer(name, session_id, croot, common_dir=_MISSING):
        p = {"session_id": session_id, "transcript_path": transcripts[0]["path"]}
        if common_dir is not _MISSING:
            p["root"] = croot
            p["common_dir"] = common_dir
        write(os.path.join(ob_inbox, name + ".json"), json.dumps(p))

    ob_plain = os.path.join(TMP, "outbox", "plain-dir")
    os.makedirs(ob_plain, exist_ok=True)
    ob_pointer("wt_a", "sess-wt-a", ob_wt_a, ob_common_dir)
    ob_pointer("wt_b", "sess-wt-b", ob_wt_b, ob_common_dir)
    ob_pointer("plain", "sess-plain", ob_plain, "/bogus/common-dir")
    ob_pointer("legacy", "sess-legacy", None)   # no root/common_dir at all -- back-compat shape
    # A2 (ship fix round 1): a malformed pointer carrying an explicit `root: null` (present, not
    # merely absent) must quarantine, not enter the outbox path keyed off e.g. str(None).
    write(os.path.join(ob_inbox, "null-root.json"),
          json.dumps({"session_id": "sess-null-root", "transcript_path": transcripts[0]["path"],
                     "root": None, "common_dir": ob_common_dir}))
    # B1 (ship fix round 3): a pointer whose `root` is already gone and that carries NO
    # `common_dir` cannot prove which repository it belonged to. Before the fix it reached the
    # missing-root branch and fell back to the DRAINING repository's outbox (this scratch repo's),
    # from where the next drain carried it into a ledger the pointer never named.
    write(os.path.join(ob_inbox, "dead-nocd.json"),
          json.dumps({"session_id": "sess-dead-nocd", "transcript_path": transcripts[0]["path"],
                     "root": os.path.join(TMP, "outbox", "gone-nocd")}))
    git(ob_root, "worktree", "remove", "--force", ob_wt_a)

    rc_ob1, out_ob1, err_ob1 = worker(ob_wt_b, "drain", "--inbox", ob_state)
    res_ob1 = json.loads(out_ob1.split("rc 0 ", 1)[1]) if "rc 0 " in out_ob1 else {}
    ob_outbox_path = os.path.join(ob_state, "outbox.jsonl")
    ob_outbox_rows = rows_of(ob_outbox_path)
    ob_wtb_rows = rows_of(ledger(ob_wt_b))
    ob_main_rows_after1 = rows_of(ledger(ob_root))
    ob_quarantine_reasons = {r.get("reason") for r in ob_main_rows_after1 + ob_wtb_rows if r.get("kind") == "quarantine"}
    ob_wtb_session_rows = [r for r in ob_wtb_rows if r.get("kind") == "session-observed"]
    check("outbox-landing-for-missing-root",
          res_ob1.get("outbox") == 1 and res_ob1.get("quarantined") == 3
          and len(ob_outbox_rows) == 1 and ob_outbox_rows[0].get("session") == "sess-wt-a"
          and ob_outbox_rows[0].get("landed_in") == "outbox" and ob_outbox_rows[0].get("origin_root_key")
          # wt_b's ledger gets its own pointer's row plus the back-compat "legacy" pointer's row
          # (both land in the drain target); wt_a's row is neither here nor in main's ledger
          and {r.get("session") for r in ob_wtb_session_rows} == {"sess-wt-b", "sess-legacy"}
          and all(r.get("landed_in") == "root" for r in ob_wtb_session_rows)
          and all(r.get("session") != "sess-wt-a" for r in ob_main_rows_after1)
          and "NotAGitCheckout" in ob_quarantine_reasons,
          (res_ob1, ob_outbox_rows, ob_wtb_rows, ob_quarantine_reasons))
    check("malformed-root-quarantines-not-outbox",
          all(r.get("session") != "sess-null-root" for r in ob_outbox_rows + ob_main_rows_after1 + ob_wtb_rows
              if r.get("kind") == "session-observed"),
          ob_outbox_rows)
    check("pointer-with-no-root-field-drains-as-before",
          any(r.get("session") == "sess-legacy" and r.get("landed_in") == "root" for r in ob_main_rows_after1 + ob_wtb_rows),
          ob_main_rows_after1 + ob_wtb_rows)
    ob_quarantine_files = {r.get("file"): r.get("reason") for r in ob_main_rows_after1 + ob_wtb_rows if r.get("kind") == "quarantine"}
    check("not-a-checkout-root-quarantines-unconditionally",
          ob_quarantine_files.get("plain.json") == "NotAGitCheckout"
          # B1 (ship fix round 3): dead root + no common_dir quarantines too, and its row is in
          # no outbox and no ledger -- not this repository's, which the pointer never named
          and ob_quarantine_files.get("dead-nocd.json") == "NotAGitCheckout"
          and all(r.get("session") != "sess-dead-nocd" for r in ob_outbox_rows + ob_main_rows_after1 + ob_wtb_rows
                  if r.get("kind") == "session-observed"),
          (ob_quarantine_files, ob_outbox_rows))

    # 24. the next drain for a live checkout of the same repository carries the outbox row in
    rc_ob2, out_ob2, err_ob2 = worker(ob_root, "drain", "--inbox", ob_state)
    res_ob2 = json.loads(out_ob2.split("rc 0 ", 1)[1]) if "rc 0 " in out_ob2 else {}
    ob_main_rows_after2 = rows_of(ledger(ob_root))
    ob_carried = [r for r in ob_main_rows_after2 if r.get("session") == "sess-wt-a"]
    ob_carried_row = ob_carried[0] if ob_carried else {}
    ob_outbox_row_fields = {k: v for k, v in ob_outbox_rows[0].items() if k not in ("landed_in", "origin_root_key")}
    ob_carried_row_fields = {k: v for k, v in ob_carried_row.items() if k not in ("landed_in", "carried_from")}
    check("carry-forward-exactly-once",
          res_ob2.get("carried") == 1 and len(ob_carried) == 1 and ob_carried_row.get("landed_in") == "carried"
          and ob_carried_row.get("carried_from") == ob_outbox_rows[0].get("origin_root_key")
          and ob_carried_row_fields == ob_outbox_row_fields
          and not os.path.isfile(ob_outbox_path)
          and len(glob.glob(os.path.join(ob_state, "outbox.*.carried.jsonl"))) == 1,
          (res_ob2, ob_carried, ob_outbox_row_fields, ob_carried_row_fields))

    # 25. a further drain of a different live checkout of the same repository carries nothing twice
    rc_ob3, out_ob3, _ = worker(ob_wt_b, "drain", "--inbox", ob_state)
    res_ob3 = json.loads(out_ob3.split("rc 0 ", 1)[1]) if "rc 0 " in out_ob3 else {}
    check("carry-forward-second-drain-carries-nothing",
          res_ob3.get("carried") == 0 and res_ob3.get("outbox") == 0
          and len([r for r in rows_of(ledger(ob_wt_b)) if r.get("session") == "sess-wt-a"]) == 0,
          res_ob3)

    # 26. every landed row's landed_in is one of root/outbox/carried (A4)
    ob_all_landed_in = {r.get("landed_in") for r in ob_main_rows_after2 + ob_wtb_rows + rows_of(ob_outbox_path)
                        if r.get("kind") == "session-observed"}
    check("outbox-rows-landed-in-allowlist", ob_all_landed_in <= {"root", "outbox", "carried"} and ob_all_landed_in,
          ob_all_landed_in)

    # 27. pre-mortem (ii), ship fix round 1 B1: two DIFFERENT live checkouts of one repository
    # (main and a linked worktree) draining a 400-row outbox AT ONCE must land every row in exactly
    # one of the two ledgers, never both -- the defect this replaces let both racers read the
    # outbox before either renamed it away, so both carried the full set into their OWN ledgers.
    cc_state = os.path.join(TMP, "outbox-concurrent", "state")
    cc_root = os.path.join(TMP, "outbox-concurrent", "main")
    os.makedirs(cc_root)
    git(cc_root, "init", "-q", "-b", "main")
    write(os.path.join(cc_root, "f.txt"), "seed\n")
    git(cc_root, "add", "-A")
    git(cc_root, "commit", "-q", "-m", "seed")
    cc_wt_b = os.path.join(TMP, "outbox-concurrent", "wt_b")
    git(cc_root, "worktree", "add", "-q", cc_wt_b, "-b", "wt_b")
    cc_common_dir = git(cc_root, "rev-parse", "--git-common-dir").strip()
    if not os.path.isabs(cc_common_dir):
        cc_common_dir = os.path.abspath(os.path.join(cc_root, cc_common_dir))
    cc_common_dir = os.path.realpath(cc_common_dir)
    cc_inbox = os.path.join(cc_state, "inbox")
    os.makedirs(cc_inbox, exist_ok=True)
    cc_dead_root = os.path.join(TMP, "outbox-concurrent", "gone")
    CC_N = 400
    for i in range(CC_N):
        write(os.path.join(cc_inbox, "dead-%03d.json" % i),
              json.dumps({"session_id": "sess-cc-dead-%03d" % i,
                         "transcript_path": transcripts[i % len(transcripts)]["path"],
                         "root": cc_dead_root, "common_dir": cc_common_dir}))
    # seed from a PLAIN (non-git) directory, never from cc_root/cc_wt_b: a live checkout matching
    # `cc_common_dir` would carry the outbox straight back into its own ledger on its very next
    # drain call (n_cap=20 needs three calls for 50 pointers), contaminating the race's starting
    # state before it begins. A non-git seed root's `target_common_dir` is None, so its own
    # `_carry_outbox` no-ops every call and the outbox only ever grows.
    cc_seed_dir = os.path.join(TMP, "outbox-concurrent", "seed-dir")
    os.makedirs(cc_seed_dir, exist_ok=True)
    outbox_seeded, seed_guard, out_seed = 0, 0, ""
    while outbox_seeded < CC_N and seed_guard < 30:
        rc_seed, out_seed, err_seed = worker(cc_seed_dir, "drain", "--inbox", cc_state)
        res_seed = json.loads(out_seed.split("rc 0 ", 1)[1]) if "rc 0 " in out_seed else {}
        outbox_seeded += res_seed.get("outbox", 0)
        seed_guard += 1
    assert outbox_seeded == CC_N, (outbox_seeded, out_seed)
    procs = [subprocess.Popen([PY, "-B", WORKER, "drain", "--root", r, "--inbox", cc_state], env=env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for r in (cc_root, cc_wt_b)]
    outs = [p.communicate(timeout=180) for p in procs]   # A3 (round 4): 60 s was tight at load 12-20
    cc_rcs = [p.returncode for p in procs]
    cc_main_rows = [r for r in rows_of(ledger(cc_root)) if str(r.get("session", "")).startswith("sess-cc-dead-")]
    cc_wtb_rows = [r for r in rows_of(ledger(cc_wt_b)) if str(r.get("session", "")).startswith("sess-cc-dead-")]
    cc_main_sessions = {r.get("session") for r in cc_main_rows}
    cc_wtb_sessions = {r.get("session") for r in cc_wtb_rows}
    check("concurrent-drains-carry-outbox-exactly-once",
          cc_rcs == [0, 0]
          and not (cc_main_sessions & cc_wtb_sessions)
          and len(cc_main_rows) + len(cc_wtb_rows) == CC_N
          and (len(cc_main_rows) == CC_N or len(cc_wtb_rows) == CC_N)
          and all(r.get("landed_in") == "carried" for r in cc_main_rows + cc_wtb_rows)
          and not os.path.isfile(os.path.join(cc_state, "outbox.jsonl"))
          and not glob.glob(os.path.join(cc_state, "outbox.*.carrying.jsonl"))
          and len(glob.glob(os.path.join(cc_state, "outbox.*.carried.jsonl"))) == 1,
          (cc_rcs, len(cc_main_rows), len(cc_wtb_rows), [o[1].decode("utf-8", "replace")[-300:] for o in outs]))

    # 28. B1 fix (ship fix round 2, cold refuter): the missing-root outbox write and
    # `_carry_outbox`'s claim must be mutually exclusive on `.outbox-carry.lock` for `outbox_dir`.
    # Deterministic reproduction, through the REAL `drain()` entrypoint (not a direct call to the
    # locked helper, so this also proves the missing-root branch actually wires to it): a
    # carrier's `read_rows` call is monkeypatched (restored after) to signal it has reached the
    # claimed file and sleep 0.4s before actually reading -- a concurrently-started writer thread
    # wakes on that signal (so it starts genuinely mid-carry, after the claim rename already
    # happened) and calls `om.drain()` on a pending dead-root pointer, landing that row through
    # `drain()`'s own missing-root branch. Before this fix that branch's write (bare
    # `append_to_path`) never touched the carry lock and would proceed immediately; with the fix
    # it must block on `.outbox-carry.lock` for the remainder of the carrier's critical section --
    # provable on the wall clock, not by luck -- and land safely afterward, carried by the very
    # next drain.
    b1_root = os.path.join(TMP, "outbox-mutex", "main")
    os.makedirs(b1_root)
    git(b1_root, "init", "-q", "-b", "main")
    write(os.path.join(b1_root, "f.txt"), "seed\n")
    git(b1_root, "add", "-A")
    git(b1_root, "commit", "-q", "-m", "seed")
    b1_common_dir = git(b1_root, "rev-parse", "--git-common-dir").strip()
    if not os.path.isabs(b1_common_dir):
        b1_common_dir = os.path.abspath(os.path.join(b1_root, b1_common_dir))
    b1_common_dir = os.path.realpath(b1_common_dir)
    b1_dead_root = os.path.join(TMP, "outbox-mutex", "gone")
    prior_hyp_state_dir = os.environ.get("HYP_STATE_DIR")
    os.environ["HYP_STATE_DIR"] = os.path.join(TMP, "state")
    real_read_rows = om.read_rows
    writer_result = {}
    wt = None
    try:
        b1_outbox_dir = om.state_root_for_repo(b1_common_dir)
        b1_outbox_path = os.path.join(b1_outbox_dir, "outbox.jsonl")
        existing_row = {"kind": "session-observed", "schema": om.SCHEMA, "session": "sess-b1-existing",
                        "through": "2026-01-01T00:00:00Z", "landed_in": "outbox", "origin_root_key": "k"}
        om.append_to_path(b1_outbox_path, existing_row)
        b1_inbox = os.path.join(b1_outbox_dir, "inbox")
        os.makedirs(b1_inbox, exist_ok=True)
        write(os.path.join(b1_inbox, "pending.json"),
              json.dumps({"session_id": "sess-b1-late", "transcript_path": transcripts[0]["path"],
                         "root": b1_dead_root, "common_dir": b1_common_dir}))

        carrier_reading = threading.Event()

        def slow_read_rows(path):
            carrier_reading.set()
            time.sleep(0.4)
            return real_read_rows(path)

        om.read_rows = slow_read_rows

        def writer_job():
            if not carrier_reading.wait(2.0):
                writer_result["timed_out"] = True
                return
            writer_result["drain"] = om.drain(b1_root, SCRIPTS, None, n_cap=20, t_cap=60.0)
            writer_result["t"] = time.monotonic()

        wt = threading.Thread(target=writer_job)
        wt.start()
        t_carrier_start = time.monotonic()
        b1_carried = om._carry_outbox(b1_root, b1_outbox_dir, b1_common_dir)
        t_carrier_end = time.monotonic()
        wt.join(timeout=5.0)
    finally:
        om.read_rows = real_read_rows
        if prior_hyp_state_dir is None:
            os.environ.pop("HYP_STATE_DIR", None)
        else:
            os.environ["HYP_STATE_DIR"] = prior_hyp_state_dir
    b1_writer_blocked = writer_result.get("t", 0.0) >= t_carrier_end - 0.05
    rc_b1, out_b1, err_b1 = worker(b1_root, "drain")
    res_b1 = json.loads(out_b1.split("rc 0 ", 1)[1]) if "rc 0 " in out_b1 else {}
    b1_ledger_rows = rows_of(ledger(b1_root))
    b1_sessions = {r.get("session") for r in b1_ledger_rows if r.get("kind") == "session-observed"}
    check("outbox-write-blocks-behind-concurrent-carry-no-row-lost",
          b1_carried == 1 and wt is not None and not wt.is_alive()
          and writer_result.get("drain", {}).get("outbox") == 1 and b1_writer_blocked
          and {"sess-b1-existing", "sess-b1-late"} <= b1_sessions,
          (b1_carried, b1_writer_blocked, writer_result.get("drain"), t_carrier_end - t_carrier_start,
           writer_result.get("t", 0.0) - t_carrier_end, sorted(b1_sessions), res_b1, err_b1[-300:]))

    # 29. B1 (ship fix round 4, cold refuter): a git that cannot ANSWER in time is not a git that
    # answered "not a checkout". A selective shim sleeps past GIT_TIMEOUT_S only when the
    # command line names a path containing "slowroot" and execs the real git otherwise, so the
    # drain target and the pointer root can be made slow independently.
    real_git = shutil.which("git")
    slow_bin = os.path.join(TMP, "slow-git-bin")
    write(os.path.join(slow_bin, "git"),
          "#!/bin/sh\ncase \"$*\" in *slowroot*) sleep %d; exit 0;; esac\nexec \"%s\" \"$@\"\n"
          % (int(om.GIT_TIMEOUT_S) + 2, real_git))
    os.chmod(os.path.join(slow_bin, "git"), 0o755)
    slow_env = {"PATH": slow_bin + os.pathsep + env()["PATH"]}
    gt_root = os.path.join(TMP, "git-timeout", "main")
    os.makedirs(gt_root)
    git(gt_root, "init", "-q", "-b", "main")
    write(os.path.join(gt_root, "f.txt"), "seed\n")
    git(gt_root, "add", "-A")
    git(gt_root, "commit", "-q", "-m", "seed")
    gt_slow_wt = os.path.join(TMP, "git-timeout", "slowroot-wt")
    git(gt_root, "worktree", "add", "-q", gt_slow_wt, "-b", "slowroot")
    gt_common_dir = os.path.realpath(os.path.join(gt_root, ".git"))
    gt_state = os.path.join(TMP, "git-timeout", "state")
    gt_inbox = os.path.join(gt_state, "inbox")
    os.makedirs(gt_inbox)
    write(os.path.join(gt_inbox, "live-slow.json"),
          json.dumps({"session_id": "sess-gt-slow", "transcript_path": transcripts[0]["path"],
                     "root": gt_slow_wt, "common_dir": gt_common_dir}))
    # 29a. the drain's OWN root cannot be resolved in time: refuse everything, move nothing, and
    # the result JSON still has every key (A4). No --inbox here on purpose: the default-location
    # path is the one that silently fell back to the sha256 inbox before the fix.
    t0 = time.monotonic()
    rc_gt1, out_gt1, err_gt1 = worker(gt_slow_wt, "drain", extra_env=slow_env)
    gt1_wall = time.monotonic() - t0
    res_gt1 = json.loads(out_gt1.split("rc 0 ", 1)[1]) if "rc 0 " in out_gt1 else {}
    # the child's HYP_STATE_DIR is TMP/state (env()), so compute the v0.29.0 fallback path there,
    # not through om.state_root() in THIS process (whose environment has no HYP_STATE_DIR)
    sha_fallback_inbox = os.path.join(TMP, "state", "om",
                                      hashlib.sha256(os.path.realpath(gt_slow_wt).encode("utf-8")).hexdigest()[:16], "inbox")
    check("git-timeout-refuses-drain-not-fallback",
          rc_gt1 == 0 and set(res_gt1) == {"rows", "quarantined", "rotated", "landed", "recovered", "outbox", "carried", "deferred"}
          and not any(v for v in res_gt1.values())
          and len(err_gt1.splitlines()) == 1 and "refusing" in err_gt1 and "git" in err_gt1
          and not os.path.isdir(sha_fallback_inbox) and not os.path.isfile(ledger(gt_slow_wt))
          and gt1_wall >= om.GIT_TIMEOUT_S,
          (rc_gt1, res_gt1, err_gt1.strip()[:200], os.path.isdir(sha_fallback_inbox), round(gt1_wall, 2)))
    # 29b. the drain's root resolves (real git for `main`) but the live pointer's root does not:
    # the pointer is deferred to the next wake, not quarantined; a drain with a working git then
    # lands it `root` in the worktree's own ledger.
    rc_gt2, out_gt2, err_gt2 = worker(gt_root, "drain", "--inbox", gt_state, extra_env=slow_env)
    res_gt2 = json.loads(out_gt2.split("rc 0 ", 1)[1]) if "rc 0 " in out_gt2 else {}
    gt_inbox_after = sorted(os.path.basename(p) for p in glob.glob(os.path.join(gt_inbox, "*.json")))
    gt_processing_after = glob.glob(os.path.join(gt_state, "processing", "*.json"))
    gt_quarantine_after = glob.glob(os.path.join(gt_state, "quarantine", "*.json"))
    gt_main_rows_after2 = rows_of(ledger(gt_root))
    rc_gt3, out_gt3, err_gt3 = worker(gt_root, "drain", "--inbox", gt_state)
    res_gt3 = json.loads(out_gt3.split("rc 0 ", 1)[1]) if "rc 0 " in out_gt3 else {}
    gt_wt_rows = rows_of(ledger(gt_slow_wt))
    check("git-timeout-defers-live-pointer-not-quarantine",
          rc_gt2 == 0 and res_gt2.get("deferred") == 1 and res_gt2.get("quarantined") == 0 and res_gt2.get("landed") == 0
          and gt_inbox_after == ["live-slow.json"] and not gt_processing_after and not gt_quarantine_after
          and not any(r.get("kind") == "quarantine" for r in gt_main_rows_after2)
          and len(err_gt2.splitlines()) == 1 and "deferred" in err_gt2
          and rc_gt3 == 0 and res_gt3.get("deferred") == 0 and res_gt3.get("landed") == 1 and res_gt3.get("rows") == 1
          and [r.get("session") for r in gt_wt_rows if r.get("kind") == "session-observed"] == ["sess-gt-slow"]
          and gt_wt_rows[0].get("landed_in") == "root"
          and not glob.glob(os.path.join(gt_inbox, "*.json")),
          (res_gt2, err_gt2.strip()[:200], gt_inbox_after, res_gt3, err_gt3.strip()[:200], gt_wt_rows[:1]))

    # 30. B2 (ship fix round 4, cold refuter): two live checkouts of ONE repository waking on the
    # SAME shared inbox at once. Without `.drain.lock`, the second wake's crash-recovery sweep
    # moved the first wake's in-flight pointer back into inbox/ and it was processed twice, the
    # first wake writing a false FileNotFoundError quarantine row for it. Whichever of the two
    # holds the lock processes everything; the other backs off (or, if it started late, finds an
    # empty inbox) -- either way every pointer lands exactly once and nothing is quarantined.
    sw_root = os.path.join(TMP, "same-inbox", "main")
    os.makedirs(sw_root)
    git(sw_root, "init", "-q", "-b", "main")
    write(os.path.join(sw_root, "f.txt"), "seed\n")
    git(sw_root, "add", "-A")
    git(sw_root, "commit", "-q", "-m", "seed")
    sw_wt_b = os.path.join(TMP, "same-inbox", "wt_b")
    git(sw_root, "worktree", "add", "-q", sw_wt_b, "-b", "wt_b")
    sw_common_dir = os.path.realpath(os.path.join(sw_root, ".git"))
    sw_state = os.path.join(TMP, "same-inbox", "state")
    sw_inbox = os.path.join(sw_state, "inbox")
    os.makedirs(sw_inbox)
    sw_dead_root = os.path.join(TMP, "same-inbox", "gone")
    SW_DEAD, SW_LIVE = 15, 5
    for i in range(SW_DEAD):
        write(os.path.join(sw_inbox, "dead-%02d.json" % i),
              json.dumps({"session_id": "sess-sw-dead-%02d" % i, "transcript_path": transcripts[i % len(transcripts)]["path"],
                         "root": sw_dead_root, "common_dir": sw_common_dir}))
    for i in range(SW_LIVE):
        write(os.path.join(sw_inbox, "live-%02d.json" % i),
              json.dumps({"session_id": "sess-sw-live-%02d" % i, "transcript_path": transcripts[i % len(transcripts)]["path"],
                         "root": sw_wt_b, "common_dir": sw_common_dir}))
    sw_procs = [subprocess.Popen([PY, "-B", WORKER, "drain", "--root", r, "--inbox", sw_state], env=env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for r in (sw_root, sw_wt_b)]
    sw_outs = [p.communicate(timeout=180) for p in sw_procs]
    sw_rcs = [p.returncode for p in sw_procs]
    sw_results = []
    for o in sw_outs:
        so = o[0].decode("utf-8", "replace")
        sw_results.append(json.loads(so.split("rc 0 ", 1)[1]) if "rc 0 " in so else {})
    sw_ledger_rows = rows_of(ledger(sw_root)) + rows_of(ledger(sw_wt_b))
    sw_outbox_live = rows_of(os.path.join(sw_state, "outbox.jsonl"))
    sw_sessions = [r.get("session") for r in sw_ledger_rows + sw_outbox_live if r.get("kind") == "session-observed"]
    sw_expected = {"sess-sw-dead-%02d" % i for i in range(SW_DEAD)} | {"sess-sw-live-%02d" % i for i in range(SW_LIVE)}
    sw_live_rows = [r for r in rows_of(ledger(sw_wt_b)) if str(r.get("session", "")).startswith("sess-sw-live-")]
    check("concurrent-wakes-same-inbox-process-each-pointer-once",
          sw_rcs == [0, 0]
          and sorted(sw_sessions) == sorted(sw_expected)      # every pointer exactly once, none twice
          and not any(r.get("kind") == "quarantine" for r in sw_ledger_rows)
          and len(sw_live_rows) == SW_LIVE and all(r.get("landed_in") == "root" for r in sw_live_rows)
          and sum(r.get("landed", 0) for r in sw_results) == SW_DEAD + SW_LIVE
          and sum(r.get("outbox", 0) for r in sw_results) == SW_DEAD
          and not glob.glob(os.path.join(sw_state, "processing", "*.json"))
          and not glob.glob(os.path.join(sw_inbox, "*.json")),
          (sw_rcs, sw_results, sorted(set(sw_sessions) ^ sw_expected)[:5], len(sw_sessions),
           [o[1].decode("utf-8", "replace")[-200:] for o in sw_outs]))

    # 31. A1 (ship fix round 4, advisory): a claim left behind by one checkout (crash, or a refused
    # row -- the round-3 refusal path leaves exactly this state) after it carried some rows, then
    # resumed by a DIFFERENT checkout of the same repository. The resumer must not re-carry the
    # rows the first carrier already landed: the claim's `.roots` sidecar names the first carrier
    # and the resumer dedupes against that ledger too, then finalizes the claim.
    rs_state = os.path.join(TMP, "resume", "state")
    os.makedirs(rs_state)
    rs_claim = os.path.join(rs_state, "outbox.1700000000.carrying.jsonl")
    rs_rows = [{"kind": "session-observed", "schema": om.SCHEMA, "session": "sess-rs-c%d" % i,
                "through": "2026-01-01T00:00:0%dZ" % i, "landed_in": "outbox", "origin_root_key": "k"} for i in range(4)]
    for r in rs_rows:
        om.append_to_path(rs_claim, r)
    write(rs_claim[:-len(".jsonl")] + ".roots", os.path.realpath(ob_root) + "\n")
    for r in rs_rows[:2]:
        prior = dict(r)
        prior.pop("origin_root_key")
        prior["landed_in"] = "carried"
        prior["carried_from"] = "k"
        om.append_row(ob_root, prior)     # main already landed c0 and c1 before it stopped
    rs_main_before = read_bytes(ledger(ob_root))
    rc_rs, out_rs, err_rs = worker(ob_wt_b, "drain", "--inbox", rs_state)
    res_rs = json.loads(out_rs.split("rc 0 ", 1)[1]) if "rc 0 " in out_rs else {}
    rs_wtb_sessions = sorted(r.get("session") for r in rows_of(ledger(ob_wt_b)) if str(r.get("session", "")).startswith("sess-rs-"))
    check("resumed-claim-dedupes-against-prior-carrier",
          rc_rs == 0 and res_rs.get("carried") == 2 and rs_wtb_sessions == ["sess-rs-c2", "sess-rs-c3"]
          and read_bytes(ledger(ob_root)) == rs_main_before
          and not os.path.isfile(rs_claim) and not glob.glob(os.path.join(rs_state, "*.roots"))
          and len(glob.glob(os.path.join(rs_state, "outbox.*.carried.jsonl"))) == 1,
          (rc_rs, res_rs, rs_wtb_sessions, err_rs.strip()[:200], sorted(os.listdir(rs_state))))

    # 32. B2 (ship fix round 5, cold refuter): a leftover claim holding a row the target ledger
    # refuses on EVERY attempt (the redaction self-check firing on an absolute-path string) used to
    # be resumed alone, left in place again on the refusal, and the live outbox.jsonl behind it
    # never claimed -- three consecutive drains, carried 0 each. Now the refused row is filed
    # verbatim beside the claim, one quarantine row names the file, the claim finalizes, and the
    # live outbox is claimed and carried in the SAME drain; a second drain changes nothing.
    rf_state = os.path.join(TMP, "refused", "state")
    os.makedirs(rf_state)
    rf_claim = os.path.join(rf_state, "outbox.1700000000.carrying.jsonl")
    rf_rows = [{"kind": "session-observed", "schema": om.SCHEMA, "session": "sess-rf-c%d" % i,
                "through": "2026-01-02T00:00:0%dZ" % i, "landed_in": "outbox", "origin_root_key": "k"} for i in range(3)]
    rf_rows[1]["note"] = "/abs/path-the-self-check-refuses"
    with open(rf_claim, "wb") as fh:          # append_to_path would refuse rf_rows[1] itself
        fh.write(b"".join(om.canonical_bytes(r) for r in rf_rows))
    write(rf_claim[:-len(".jsonl")] + ".roots", os.path.realpath(ob_root) + "\n")
    rf_live = {"kind": "session-observed", "schema": om.SCHEMA, "session": "sess-rf-live",
               "through": "2026-01-02T00:00:09Z", "landed_in": "outbox", "origin_root_key": "k"}
    om.append_to_path(os.path.join(rf_state, "outbox.jsonl"), rf_live)
    rc_rf, out_rf, err_rf = worker(ob_wt_b, "drain", "--inbox", rf_state)
    res_rf = json.loads(out_rf.split("rc 0 ", 1)[1]) if "rc 0 " in out_rf else {}
    rf_wtb_rows = rows_of(ledger(ob_wt_b))
    rf_landed = sorted((r.get("session"), r.get("landed_in")) for r in rf_wtb_rows
                       if str(r.get("session", "")).startswith("sess-rf-"))
    rf_q_rows = [r for r in rf_wtb_rows if r.get("kind") == "quarantine" and str(r.get("file", "")).startswith("outbox.")]
    rf_refused_path = os.path.join(rf_state, "outbox.1700000000.refused.jsonl")
    rf_listing_1 = sorted(os.listdir(rf_state))
    rf_wtb_bytes_1 = read_bytes(ledger(ob_wt_b))
    rc_rf2, out_rf2, _ = worker(ob_wt_b, "drain", "--inbox", rf_state)
    res_rf2 = json.loads(out_rf2.split("rc 0 ", 1)[1]) if "rc 0 " in out_rf2 else {}
    check("refused-claim-files-row-finalizes-then-claims-live-outbox",
          rc_rf == 0 and res_rf.get("carried") == 3 and res_rf.get("quarantined") == 1
          and rf_landed == [("sess-rf-c0", "carried"), ("sess-rf-c2", "carried"), ("sess-rf-live", "carried")]
          and len(rf_q_rows) == 1 and set(rf_q_rows[0]) == QUARANTINE_KEYS
          and rf_q_rows[0].get("file") == "outbox.1700000000.refused.jsonl"
          and rf_q_rows[0].get("reason") == "CarryRefused-redaction"
          and os.path.isfile(rf_refused_path) and read_bytes(rf_refused_path) == om.canonical_bytes(rf_rows[1])
          and not os.path.isfile(rf_claim) and not glob.glob(os.path.join(rf_state, "*.roots"))
          and not os.path.isfile(os.path.join(rf_state, "outbox.jsonl"))
          and len(glob.glob(os.path.join(rf_state, "outbox.*.carried.jsonl"))) == 2
          and sum(1 for l in err_rf.splitlines() if "refus" in l.lower()) == 2
          and "filed in outbox.1700000000.refused.jsonl" in err_rf
          and rc_rf2 == 0 and res_rf2.get("carried") == 0 and res_rf2.get("quarantined") == 0
          and read_bytes(ledger(ob_wt_b)) == rf_wtb_bytes_1 and sorted(os.listdir(rf_state)) == rf_listing_1,
          (rc_rf, res_rf, rf_landed, rf_q_rows, err_rf.strip()[:300], rf_listing_1, res_rf2))

    # 33. B2 (ship fix round 5): the OTHER refusal. A free-space-floor refusal mid-carry is about
    # the host, not the row, so the carry halts with the claim intact and nothing filed or
    # finalized; the next carry (in-process, the locked body itself) resumes the claim -- skipping
    # the row already landed -- AND claims the live outbox in the same call.
    fl_state = os.path.join(TMP, "floor-carry", "state")
    os.makedirs(fl_state)
    fl_claim = os.path.join(fl_state, "outbox.1700000001.carrying.jsonl")
    fl_rows = [{"kind": "session-observed", "schema": om.SCHEMA, "session": "sess-fl-c%d" % i,
                "through": "2026-01-03T00:00:0%dZ" % i, "landed_in": "outbox", "origin_root_key": "k"} for i in range(2)]
    for r in fl_rows:
        om.append_to_path(fl_claim, r)
    om.append_to_path(os.path.join(fl_state, "outbox.jsonl"),
                      {"kind": "session-observed", "schema": om.SCHEMA, "session": "sess-fl-live",
                       "through": "2026-01-03T00:00:09Z", "landed_in": "outbox", "origin_root_key": "k"})
    real_append_row = om.append_row
    fl_calls = []

    def floor_on_second(root_, row, allow_below_floor=False):
        fl_calls.append(row.get("session"))
        if len(fl_calls) == 2:
            return "refused-floor"
        return real_append_row(root_, row, allow_below_floor=allow_below_floor)

    fl_stderr = io.StringIO()
    real_stderr = sys.stderr
    try:
        om.append_row = floor_on_second
        sys.stderr = fl_stderr
        fl_first = om._carry_claim(ob_wt_b, fl_claim)
    finally:
        om.append_row = real_append_row
        sys.stderr = real_stderr
    fl_mid_listing = sorted(os.listdir(fl_state))
    fl_second = om._carry_outbox_locked(ob_wt_b, fl_state)
    fl_landed = sorted(r.get("session") for r in rows_of(ledger(ob_wt_b))
                       if str(r.get("session", "")).startswith("sess-fl-"))
    check("floor-refusal-halts-carry-leaves-claim-intact",
          fl_first == (1, 0, True) and fl_calls == ["sess-fl-c0", "sess-fl-c1"]
          and fl_mid_listing == ["outbox.1700000001.carrying.jsonl", "outbox.1700000001.carrying.roots", "outbox.jsonl"]
          and "left for the next drain" in fl_stderr.getvalue() and len(fl_stderr.getvalue().splitlines()) == 1
          and fl_second == (2, 0) and fl_landed == ["sess-fl-c0", "sess-fl-c1", "sess-fl-live"]
          and not os.path.isfile(fl_claim) and not os.path.isfile(os.path.join(fl_state, "outbox.jsonl"))
          and not glob.glob(os.path.join(fl_state, "*.roots")) and not glob.glob(os.path.join(fl_state, "*.refused.jsonl"))
          and len(glob.glob(os.path.join(fl_state, "outbox.*.carried.jsonl"))) == 2,
          (fl_first, fl_calls, fl_mid_listing, fl_stderr.getvalue().strip()[:200], fl_second, fl_landed, sorted(os.listdir(fl_state))))

    # 34. zero model calls
    spawns = read_bytes(SHIM_LOG).decode("utf-8", "replace").splitlines()
    check("zero-model-calls-claude-shim", len(spawns) == 0, spawns[:2])

    # 35. the installed copy: stdlib only, no home or lab path
    src = read_bytes(WORKER).decode("utf-8")
    tree_ = ast.parse(src)
    imported = set()
    for node in tree_.body:
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    stdlib = {"errno", "fcntl", "getpass", "glob", "hashlib", "json", "os", "re", "shutil", "socket", "subprocess", "sys", "time", "zlib"}
    lab_tokens = ("/Users/", "cause-n-effect", "experiments/runs/", "experiments/deploy")
    check("installed-copy-stdlib-and-no-lab-paths", imported <= stdlib and not any(tok in src for tok in lab_tokens)
          and os.access(WORKER, os.X_OK), (sorted(imported - stdlib), [t for t in lab_tokens if t in src]))

    ok = all(RESULTS)
    print("om-worker sha256 %s (the installed copy this run exercised)" % hashlib.sha256(read_bytes(WORKER)).hexdigest())
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(RESULTS), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
