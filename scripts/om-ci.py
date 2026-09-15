#!/usr/bin/env python3
"""om-ci.py -- the repository-tier verbs for the operating-model tier-0 CI check.

Usage:
  om-ci.py emit ci-tier0 [--root R] [--force]
  om-ci.py self-test ci-tier0 [--keep-scratch]

`emit ci-tier0` renders `scripts/om_check_jobs.py`'s ONE JOBS table into
`<root>/.github/workflows/om-check.yml` and vendors everything that workflow calls (the plugin
scripts it shells out to, this file's own glue, and pure-Python PyYAML) into
`<root>/.github/om-scripts/`, with a `MANIFEST.json` of shas. Idempotent and byte-stable on
re-run: every artifact prints `created` / `unchanged` / `updated` / `kept (edited; pass --force
to overwrite)` and a hand-edited file is never overwritten without `--force`.

`self-test ci-tier0` proves the emitted setup actually works WITHOUT GitHub: it builds its own
throwaway git consumer under a temp directory, runs the same JOBS table's steps against it under
the CI-runner constraint the lab keep measured (empty `HOME`, a minimal `PATH`, HTTPS/HTTP
proxies pointed at a local sink that only counts connections, a `claude` shim that fails loudly
if spawned), seeds the same defects the lab lane graded (one broken link, one stale compiled
artifact, one compiled-only touch), and prints one PASS/FAIL line per case. Exit 0 iff every
case passed.

This is the repository tier from `experiments/runs/DESIGN-passive-om-feedback/DESIGN.md`
section 6, row C, in getfatday/cause-n-effect: `scripts/om-integrate.py` (the general
integrator that would own an `emit`/`self-test` verb per shipped tier) has not landed, so this
file carries the `ci-tier0` verbs alone, sharing `om_check_jobs.py`'s JOBS table with the
rendered YAML exactly as DESIGN.md's row anticipates ("`scripts/om-ci.py` with the same JOBS
table if D has not landed"). Source: getfatday/cause-n-effect H-DRAFT-a28b91c9-om-ci-tier0,
kept 2026-09-15 (VERDICT.json, VERIFY.md).

Stdlib only, Python 3.9.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "hooks", "scripts"))
from hyp_config import load_config  # noqa: E402

sys.path.insert(0, HERE)
import om_check_jobs as J  # noqa: E402

VENDOR_PLUGIN_SCRIPTS = ("om-worker.py", "model-lint.py", "compile-catalog.py", "observatory.py")
GLUE_SCRIPTS = ("om_check_jobs.py", "om_check_report_stale.py", "om_check_regen_commit.py")
PYYAML_SRC_DIR = os.path.join(HERE, "vendor", "pyyaml", "yaml")
PYYAML_LICENSE_SRC = os.path.join(HERE, "vendor", "pyyaml", "LICENSE")
WORKFLOW_REL = os.path.join(".github", "workflows", "om-check.yml")
BOT_IDENTITY = J.BOT_IDENTITY
NORMAL_ACTOR = "consumer-contributor"

# B4 (ship fix round 1): a hung step must read as a recorded FAIL, never hang the run.
STEP_TIMEOUT_S = 60
WHOLE_RUN_CAP_S = 300

# B3: the ONE predicate shape render_yaml ever emits for a step's `if:` line -- parsed back out
# of the COMMITTED workflow text (never re-derived from the JOBS table's own, unevaluated `if`
# marker) so a mutant that flips the rendered operator is judged by what actually ships.
IF_LINE_RE = re.compile(r"^\s*if:\s*\$\{\{\s*github\.actor\s*(!=|==)\s*'([^']*)'\s*\}\}\s*$")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "om-ci-selftest", "GIT_AUTHOR_EMAIL": "om-ci-selftest@example.invalid",
    "GIT_COMMITTER_NAME": "om-ci-selftest", "GIT_COMMITTER_EMAIL": "om-ci-selftest@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "PYTHONDONTWRITEBYTECODE": "1",
}

RENDER_COMPILED_PY = (
    "#!/usr/bin/env python3\n"
    "\"\"\"render_compiled.py -- the consumer's OWN declared compile_command.\"\"\"\n"
    "import os\n"
    "HEADER = '<!-- COMPILED ARTIFACT (deterministic render of operating-model/ops) -->\\n'\n"
    "def main():\n"
    "    with open(os.path.join('operating-model', 'ops', 'model.md'), encoding='utf-8') as fh:\n"
    "        catalog = fh.read()\n"
    "    os.makedirs('compiled', exist_ok=True)\n"
    "    with open(os.path.join('compiled', 'SOP.md'), 'w', encoding='utf-8') as fh:\n"
    "        fh.write(HEADER + catalog)\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)

CLEAN_NODES = {
    "operating-model/ops/actors/builder.md":
        "---\nid: actor/builder\ntype: actor\ncontext: ops\nsummary: the builder\n"
        "status: current\n---\nBuilder.\n",
    "operating-model/ops/commands/check.md":
        "---\nid: command/check\ntype: command\ncontext: ops\nsummary: check the model\n"
        "status: current\nhandler: script/check_model.py\nissued-by: actor/builder\n"
        "executor: agent\nfreedom: bounded\nreads: []\nemits: [event/built]\n---\nCheck.\n",
    "operating-model/ops/policies/gate-clean.md":
        "---\nid: policy/gate-clean\ntype: policy\ncontext: ops\nsummary: clean gate\n"
        "status: current\nthen: [command/check]\n---\nClean.\n",
    "operating-model/ops/events/built.md":
        "---\nid: event/built\ntype: event\ncontext: ops\nsummary: node built\n"
        "status: current\nrepresentation: row\n---\nBuilt.\n",
}


# --------------------------------------------------------------------------- emit ci-tier0
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _status_for(path, fresh_bytes, force):
    """created / unchanged / updated / "kept (edited; ...)" -- never raises, never overwrites a
    consumer edit unless force is set."""
    if not os.path.isfile(path):
        _write_bytes(path, fresh_bytes)
        return "created"
    with open(path, "rb") as fh:
        current = fh.read()
    if current == fresh_bytes:
        return "unchanged"
    if force:
        _write_bytes(path, fresh_bytes)
        return "updated"
    return "kept (edited; pass --force to overwrite)"


def emit_ci_tier0(root, force=False):
    """Writes the workflow, the vendored scripts and PyYAML, and MANIFEST.json into `root`.
    Returns the list of report lines (one per artifact), in write order."""
    cfg = load_config(root)
    model_dir = (cfg.get("model_dir") or J.DEFAULT_MODEL_DIR).strip("/") or J.DEFAULT_MODEL_DIR
    lines = []
    manifest = {}

    workflow_path = os.path.join(root, WORKFLOW_REL)
    yaml_text = J.render_yaml(model_dir)
    status = _status_for(workflow_path, yaml_text.encode("utf-8"), force)
    lines.append("%-40s %s" % (status, WORKFLOW_REL))

    vendor_dir = os.path.join(root, J.VENDOR_DIR)
    for name in VENDOR_PLUGIN_SCRIPTS + GLUE_SCRIPTS:
        src = os.path.join(HERE, name)
        with open(src, "rb") as fh:
            data = fh.read()
        dest = os.path.join(vendor_dir, name)
        status = _status_for(dest, data, force)
        manifest[name] = sha256_bytes(data)
        lines.append("%-40s %s" % (status, os.path.relpath(dest, root)))

    for src_path in sorted(glob.glob(os.path.join(PYYAML_SRC_DIR, "*.py"))):
        name = os.path.basename(src_path)
        with open(src_path, "rb") as fh:
            data = fh.read()
        dest = os.path.join(vendor_dir, "pyyaml", "yaml", name)
        status = _status_for(dest, data, force)
        manifest["pyyaml/yaml/%s" % name] = sha256_bytes(data)
        lines.append("%-40s %s" % (status, os.path.relpath(dest, root)))
    if os.path.isfile(PYYAML_LICENSE_SRC):
        with open(PYYAML_LICENSE_SRC, "rb") as fh:
            data = fh.read()
        dest = os.path.join(vendor_dir, "pyyaml", "LICENSE")
        status = _status_for(dest, data, force)
        manifest["pyyaml/LICENSE"] = sha256_bytes(data)
        lines.append("%-40s %s" % (status, os.path.relpath(dest, root)))

    # MANIFEST.json is plugin-derived data (never hand-edited), so it is always regenerated
    # fresh from what was just vendored -- "force" here only ever means "these are computed
    # bytes", never "clobber a consumer's own file".
    manifest_bytes = (json.dumps({"schema": 1, "model_dir": model_dir, "sha256": manifest},
                                 indent=1, sort_keys=True) + "\n").encode("utf-8")
    manifest_path = os.path.join(vendor_dir, "MANIFEST.json")
    status = _status_for(manifest_path, manifest_bytes, True)
    lines.append("%-40s %s" % (status, os.path.relpath(manifest_path, root)))
    return lines


# --------------------------------------------------------------------------- self-test ci-tier0
def _write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _sh(cmd, cwd=None, env=None, check=True, timeout=None):
    """B4 (ship fix round 1): `timeout`, when given, is seconds; a hang raises
    `subprocess.TimeoutExpired` (the caller decides whether that is a FAIL line or a crash --
    `_run_jobs`'s per-step loop catches it, everything else lets it propagate)."""
    argv = ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)
    proc = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise RuntimeError("command failed (%r) rc=%s:\n%s" % (cmd, proc.returncode, out))
    return proc.returncode, out


def _git(cwd, *args, check=True):
    env = dict(os.environ)
    env.update(GIT_ENV)
    return _sh(["git", "-C", cwd, "-c", "commit.gpgsign=false"] + list(args), env=env, check=check)


def _start_proxy_sink(scratch_root):
    """A real localhost listener that logs one line per accepted connection and nothing else --
    so 'no network call happened' is measured, not merely assumed from an unreachable port."""
    log_path = os.path.join(scratch_root, "proxy.log")
    os.makedirs(scratch_root, exist_ok=True)
    open(log_path, "w", encoding="utf-8").close()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    srv.settimeout(0.2)
    stop_flag = {"stop": False}

    def _loop():
        while not stop_flag["stop"]:
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("%.6f accept\n" % time.time())
            try:
                conn.close()
            except OSError:
                pass
        try:
            srv.close()
        except OSError:
            pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()

    def _stop():
        stop_flag["stop"] = True
        t.join(timeout=2)

    return port, log_path, _stop


def _ci_runner_env(scratch_root, actor, proxy_port, pythonpath_extra=None):
    home = os.path.join(scratch_root, "empty-home")
    os.makedirs(home, exist_ok=True)
    shim_dir = os.path.join(scratch_root, "shim")
    os.makedirs(shim_dir, exist_ok=True)
    shim_log = os.path.join(scratch_root, "claude-shim.log")
    shim_path = os.path.join(shim_dir, "claude")
    with open(shim_path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho \"$(date -u +%%s) $*\" >> %s\nexit 1\n" % shim_log)
    os.chmod(shim_path, 0o755)
    proxy_url = "http://127.0.0.1:%d/" % proxy_port
    env = {
        "HOME": home, "PATH": "%s:/usr/bin:/bin" % shim_dir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "GITHUB_ACTIONS": "true", "GITHUB_ACTOR": actor,
        "GITHUB_TOKEN": "FIXTURE-NOT-A-CREDENTIAL-0000000000000000000000",
        "HTTPS_PROXY": proxy_url, "HTTP_PROXY": proxy_url,
        "https_proxy": proxy_url, "http_proxy": proxy_url,
        "all_proxy": proxy_url, "NO_PROXY": "", "no_proxy": "",
        "LC_ALL": "C", "TZ": "UTC",
    }
    if pythonpath_extra:
        env["PYTHONPATH"] = pythonpath_extra
    return env, shim_log


def _build_scratch_consumer(work, origin, model_dir="operating-model"):
    """A clean 4-node consumer with a declared compile_command, one fresh compiled artifact and
    the emitted workflow + vendor tree, committed on `main`, tagged `on-base`, and pushed to a
    local bare `origin` (so the workflow's own `git push` step has something real to push to)."""
    os.makedirs(work, exist_ok=True)
    _git(work, "init", "-q", "-b", "main")
    for rel, content in CLEAN_NODES.items():
        _write_text(os.path.join(work, rel), content)
    _write_text(os.path.join(work, ".claude", "hyp.json"),
               json.dumps({"model_dir": model_dir,
                           "compile_command": "python3 scripts/render_compiled.py"}, indent=1) + "\n")
    # the feedback ledger is normally a tracked, union-merged file (templates/gitattributes);
    # self-test ignores it so switching between scenario branches never fights over rows the
    # om-worker steps append to it -- that merge behaviour is scripts/selftest-om-worker.py's
    # job, not this one's.
    _write_text(os.path.join(work, ".gitignore"),
               "%s/*/model.md\nledger/om-feedback.jsonl\n" % model_dir)
    _write_text(os.path.join(work, "scripts", "render_compiled.py"), RENDER_COMPILED_PY)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "model tree v1 (clean)")

    _sh([sys.executable, "-B", os.path.join(HERE, "compile-catalog.py"),
        os.path.join(work, model_dir, "ops"), "--write"])
    _sh([sys.executable, "-B", os.path.join(work, "scripts", "render_compiled.py")], cwd=work)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "compiled v1 (fresh)")

    emit_ci_tier0(work, force=True)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "emit .github/workflows/om-check.yml (tier-0 CI)")
    _git(work, "branch", "on-base", "main")

    _git(work, "init", "-q", "--bare", "-b", "main", origin)
    _git(work, "remote", "add", "origin", origin)
    _git(work, "push", "-q", "origin", "main", "on-base")


def _seed(work, branch, rel, content, message):
    _git(work, "checkout", "-q", "-b", branch, "on-base")
    _write_text(os.path.join(work, rel), content)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", message)
    _git(work, "checkout", "-q", "main")


def parse_step_predicates(yaml_text):
    """B3 (ship fix round 1): every `if:` line in the COMMITTED workflow text, in document
    order -- the ONE predicate shape `render_yaml` ever emits. Read back out of the rendered
    text itself, never re-derived from the JOBS table's own `if` marker (which the table now
    documents as presence-only, never evaluated -- see the comment above `JOBS`), so a mutant
    that flips the rendered operator (`!=` -> `==`) is judged by what actually ships."""
    preds = []
    for line in yaml_text.splitlines():
        m = IF_LINE_RE.match(line)
        if m:
            preds.append((m.group(1), m.group(2)))
    return preds


def evaluate_predicate(op, value, actor):
    if op == "!=":
        return actor != value
    if op == "==":
        return actor == value
    raise ValueError("unknown predicate operator %r" % op)


def build_predicate_map(committed_yaml):
    """Zips the committed workflow's parsed `if:` lines onto the JOBS table's own steps that
    declare an `if` marker, in table order -- the earlier 'rendered YAML run: blocks equal the
    JOBS table' check already proves render order matches table order, so this zip is safe.
    Raises loudly on a count mismatch rather than silently zipping past the end (a dropped or
    added guard must fail the self-test, not desync it)."""
    preds = parse_step_predicates(committed_yaml)
    mapping = {}
    i = 0
    for job, step in J.iter_steps():
        if "if" in step:
            if i >= len(preds):
                raise AssertionError(
                    "committed workflow has fewer if-lines than the JOBS table declares")
            mapping[(job["name"], step["name"])] = preds[i]
            i += 1
    if i != len(preds):
        raise AssertionError(
            "committed workflow has %d if-lines, JOBS table declares %d" % (len(preds), i))
    return mapping


def _predicate_holds(job, step, actor, pred_map):
    if "if" not in step:
        return True
    op, value = pred_map[(job["name"], step["name"])]
    return evaluate_predicate(op, value, actor)


def _stale_value(step_outputs):
    """B1 (ship fix round 1): parses the `report staleness` step's own `STALE: <bool> <tree>`
    line(s) -- True if any model tree read stale, False if the step ran and every tree read
    clean, None if the step's output was never captured (predicate skipped, or the job failed
    before reaching it). Previously nothing ever read this step's output at all."""
    out = step_outputs.get("report staleness")
    if out is None:
        return None
    stale_lines = [l for l in out.splitlines() if l.startswith("STALE:")]
    if not stale_lines:
        return None
    return any(l.startswith("STALE: True") for l in stale_lines)


def _independent_compiled_sha(work, ref, model_dir, scratch_parent):
    """B2 (ship fix round 1): proves the workflow's regenerated `compiled/SOP.md` carries
    CORRECT bytes, not merely present ones. Checks `ref` (the PRE-regen commit) out into its
    own detached worktree and independently replays the consumer's own regen path from
    scratch -- `compile-catalog.py --model-dir`, then the consumer's OWN declared
    `compile_command` read from `.claude/hyp.json` -- rather than trusting the workflow's own
    regenerate step to have run correctly."""
    wt = os.path.join(scratch_parent, "indep-render")
    _git(work, "worktree", "add", "-q", "--detach", wt, ref)
    try:
        _sh([sys.executable, "-B", os.path.join(HERE, "compile-catalog.py"),
            "--model-dir", os.path.join(wt, model_dir), "--write"])
        with open(os.path.join(wt, ".claude", "hyp.json"), encoding="utf-8") as fh:
            cmd = json.load(fh)["compile_command"]
        _sh(cmd, cwd=wt)
        with open(os.path.join(wt, "compiled", "SOP.md"), "rb") as fh:
            return sha256_bytes(fh.read())
    finally:
        _git(work, "worktree", "remove", "-q", "--force", wt, check=False)


def _assert_a1_guardrails(committed_yaml, model_dir):
    """A1 (ship fix round 1): a semantic guard the earlier self-test lacked -- checked against
    LITERAL expected values, never re-derived from the (possibly mutated) render/table
    functions the rest of this self-test already leans on elsewhere. Catches T2 (lint
    permissions widened to write), T5/T6/T7 (`paths-ignore` rendered beside `paths`, an extra
    secret, `timeout-minutes: 0`) -- none of which the byte-for-byte 'rendered equals
    committed' check can catch, since that check only proves the renderer is SELF-consistent,
    not that its output matches what the template is supposed to say. Returns (ok, detail)."""
    problems = []
    lines = committed_yaml.splitlines()

    def _block_after(marker, n):
        try:
            i = lines.index(marker)
        except ValueError:
            return None
        return lines[i + 1: i + 1 + n]

    expect_paths = ["    paths:", "      - %r" % ("%s/**" % model_dir), "      - %r" % "!compiled/**"]
    for event in ("push", "pull_request"):
        block = _block_after("  %s:" % event, len(expect_paths))
        if block != expect_paths:
            problems.append("%s: paths block = %r (want %r)" % (event, block, expect_paths))
    if "paths-ignore" in committed_yaml:
        problems.append("paths-ignore rendered somewhere in the committed workflow")

    expect_job_blocks = {
        "lint": ["    runs-on: ubuntu-latest", "    timeout-minutes: 5", "    permissions:",
                "      contents: read"],
        "compile-check": ["    runs-on: ubuntu-latest", "    timeout-minutes: 5",
                          "    permissions:", "      contents: write"],
    }
    for job_name, expect_block in expect_job_blocks.items():
        block = _block_after("  %s:" % job_name, len(expect_block))
        if block != expect_block:
            problems.append("%s: job block = %r (want %r)" % (job_name, block, expect_block))

    secrets_used = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", committed_yaml))
    if secrets_used != {"GITHUB_TOKEN"}:
        problems.append("secrets referenced = %r (want {'GITHUB_TOKEN'})" % secrets_used)

    return (not problems), "; ".join(problems)


def _run_jobs(work, ref, actor, scratch_root, model_dir, pred_map):
    """Checks out `ref` and runs every job's steps in table order under the CI-runner
    constraint, honouring each step's `if` (evaluated from the COMMITTED workflow text via
    `pred_map` -- B3). Captures every step's own stdout in `step_outputs` (B1: the earlier
    shape kept only a failing step's output, so nothing could ever read a passing step's
    `STALE:` line). A step that runs past STEP_TIMEOUT_S reads as a FAIL naming that step
    rather than hanging the run (B4). Returns (per-job results, shim_hits, proxy_hits)."""
    _git(work, "checkout", "-q", ref)
    proxy_port, proxy_log, stop = _start_proxy_sink(scratch_root)
    try:
        env_base, shim_log = _ci_runner_env(scratch_root, actor, proxy_port)
        results = []
        for job in J.JOBS:
            job_rc = 0
            failing_step = None
            output = ""
            step_outputs = {}
            for step in job["steps"]:
                if not _predicate_holds(job, step, actor, pred_map):
                    continue
                cmd = J.render_step_command(step, model_dir)
                step_env = dict(env_base)
                step_env.update(J.step_env(job, step, model_dir))
                try:
                    rc, out = _sh(cmd, cwd=work, env=step_env, check=False, timeout=STEP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    job_rc, failing_step = 124, step["name"]
                    output = "TIMEOUT after %ds" % STEP_TIMEOUT_S
                    print("FAIL step timed out: %s (job=%s)" % (step["name"], job["name"]))
                    break
                step_outputs[step["name"]] = out
                if rc != 0:
                    job_rc, failing_step, output = rc, step["name"], out
                    break
            results.append({"job": job["name"], "rc": job_rc, "failing_step": failing_step,
                            "output": output, "step_outputs": step_outputs})
        shim_hits = sum(1 for l in open(shim_log, encoding="utf-8") if l.strip()) \
            if os.path.isfile(shim_log) else 0
        proxy_hits = sum(1 for l in open(proxy_log, encoding="utf-8") if l.strip()) \
            if os.path.isfile(proxy_log) else 0
        return results, shim_hits, proxy_hits
    finally:
        stop()


def _job_result(results, name):
    for r in results:
        if r["job"] == name:
            return r
    return None


class _BudgetExceeded(Exception):
    pass


def _require_budget(start, where):
    """B4: the 300s whole-run cap. Raised, never silently tolerated, so a scenario that runs
    long reads as a recorded FAIL rather than eating the rest of the caller's timeout budget."""
    elapsed = time.time() - start
    if elapsed > WHOLE_RUN_CAP_S:
        raise _BudgetExceeded("%s (elapsed=%.1fs > cap=%ds)" % (where, elapsed, WHOLE_RUN_CAP_S))


def self_test_ci_tier0(keep_scratch=False):
    checks = []
    run_started = time.time()

    def check(name, cond, detail=""):
        checks.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name + (": " + str(detail) if detail else ""))

    model_dir = J.DEFAULT_MODEL_DIR

    # -- pure-function cases: no scratch consumer needed ------------------------------------
    y1 = J.render_yaml(model_dir)
    y2 = J.render_yaml(model_dir)
    check("render byte-stable across two calls", y1 == y2)

    rendered_blocks = J.extract_run_blocks(y1)
    table_blocks = [J.render_step_command(step, model_dir) for _job, step in J.iter_steps()]
    check("rendered YAML run: blocks equal the JOBS table", rendered_blocks == table_blocks,
         "" if rendered_blocks == table_blocks else "%r != %r" % (rendered_blocks, table_blocks))

    check("paths-only mutant fires no job by the predicate",
         J.trigger_fires(["compiled/SOP.md"], model_dir) is False)
    check("a model-tree change fires the trigger",
         J.trigger_fires(["%s/ops/actors/builder.md" % model_dir], model_dir) is True)

    scratch_root = tempfile.mkdtemp(prefix="om-ci-selftest-")
    work = os.path.join(scratch_root, "work")
    origin = os.path.join(scratch_root, "origin.git")
    try:
        try:
            _build_scratch_consumer(work, origin, model_dir)
            _require_budget(run_started, "after build_scratch_consumer")

            with open(os.path.join(work, WORKFLOW_REL), "r", encoding="utf-8") as fh:
                committed_yaml = fh.read()
            check("rendered YAML equals the committed workflow",
                 J.render_yaml(model_dir) == committed_yaml)

            pred_map = build_predicate_map(committed_yaml)
            if_preds = parse_step_predicates(committed_yaml)
            check("committed workflow declares exactly the two expected if-lines",
                 if_preds == [("!=", BOT_IDENTITY), ("!=", BOT_IDENTITY)], if_preds)

            a1_ok, a1_detail = _assert_a1_guardrails(committed_yaml, model_dir)
            check("A1 trigger/permissions/timeout/secrets guardrails hold against literal "
                 "expectations", a1_ok, a1_detail)

            shim_total = proxy_total = 0

            results, shim_hits, proxy_hits = _run_jobs(work, "on-base", NORMAL_ACTOR,
                                                        os.path.join(scratch_root, "run-clean"),
                                                        model_dir, pred_map)
            shim_total += shim_hits
            proxy_total += proxy_hits
            check("lint job green on the clean tree", _job_result(results, "lint")["rc"] == 0)
            check("report-staleness step reads STALE: False on the clean tree",
                 _stale_value(_job_result(results, "compile-check")["step_outputs"]) is False)
            _require_budget(run_started, "after clean-tree run")

            _seed(work, "mutant/m-lint", "%s/ops/policies/gate-clean.md" % model_dir,
                 "---\nid: policy/gate-clean\ntype: policy\ncontext: ops\nsummary: clean gate\n"
                 "status: current\nthen: [command/does-not-exist]\n---\nBroken (seeded E-LINK).\n",
                 "seed E-LINK")
            results, shim_hits, proxy_hits = _run_jobs(work, "mutant/m-lint", NORMAL_ACTOR,
                                                        os.path.join(scratch_root, "run-mlint"),
                                                        model_dir, pred_map)
            shim_total += shim_hits
            proxy_total += proxy_hits
            lint_mlint = _job_result(results, "lint")
            check("lint job red on the E-LINK mutant",
                 lint_mlint["rc"] != 0 and "E-LINK" in lint_mlint["output"])
            _require_budget(run_started, "after m-lint run")

            _seed(work, "mutant/m-stale", "%s/ops/actors/builder.md" % model_dir,
                 "---\nid: actor/builder\ntype: actor\ncontext: ops\nsummary: the builder (edited)\n"
                 "status: current\n---\nEdited after the compiled artifact (seeds M-stale).\n",
                 "seed M-stale")
            head_before = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
            results, shim_hits, proxy_hits = _run_jobs(work, "mutant/m-stale", NORMAL_ACTOR,
                                                        os.path.join(scratch_root, "run-mstale-1"),
                                                        model_dir, pred_map)
            shim_total += shim_hits
            proxy_total += proxy_hits
            head_after = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
            rev_count = int(_git(work, "rev-list", "--count",
                                 "%s..%s" % (head_before, head_after))[1].strip()) \
                if head_before != head_after else 0
            cc = _job_result(results, "compile-check")
            check("compile-check job regenerates exactly one bot commit on the stale mutant",
                 cc["rc"] == 0 and rev_count == 1,
                 "rc=%s commits=%d failing_step=%s" % (cc["rc"], rev_count, cc["failing_step"]))
            check("report-staleness step reads STALE: True on the first m-stale run",
                 _stale_value(cc["step_outputs"]) is True)
            author = _git(work, "log", "-1", "--format=%an", "mutant/m-stale")[1].strip() \
                if rev_count else None
            check("the regenerate commit is authored as the bot identity",
                 rev_count == 0 or author == BOT_IDENTITY, author)

            independent_sha = _independent_compiled_sha(
                work, head_before, model_dir, os.path.join(scratch_root, "run-mstale-1"))
            with open(os.path.join(work, "compiled", "SOP.md"), "rb") as fh:
                regenerated_sha = sha256_bytes(fh.read())
            check("regenerated compiled/SOP.md matches an independent fresh render",
                 rev_count != 1 or regenerated_sha == independent_sha,
                 "regenerated=%s independent=%s" % (regenerated_sha, independent_sha))
            _require_budget(run_started, "after m-stale run 1 + independent render")

            head_before2 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
            results, shim_hits, proxy_hits = _run_jobs(work, "mutant/m-stale", NORMAL_ACTOR,
                                                        os.path.join(scratch_root, "run-mstale-2"),
                                                        model_dir, pred_map)
            shim_total += shim_hits
            proxy_total += proxy_hits
            head_after2 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
            check("a second run adds zero commits", head_before2 == head_after2)
            cc2 = _job_result(results, "compile-check")
            check("compile-check job still exits 0 on the second run", cc2["rc"] == 0)
            check("report-staleness step reads STALE: False on the second (post-regen) run",
                 _stale_value(cc2["step_outputs"]) is False)
            _require_budget(run_started, "after m-stale run 2")

            head_before3 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
            _run_jobs(work, "mutant/m-stale", BOT_IDENTITY,
                     os.path.join(scratch_root, "run-mstale-bot"), model_dir, pred_map)
            head_after3 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
            check("the bot actor's own push never re-triggers the regenerate step",
                 head_before3 == head_after3)
            _require_budget(run_started, "after bot-actor run")

            # B1 (ship fix round 2): actions/checkout@v4 leaves EVERY pull_request checkout
            # detached at the merge ref. Seed a fresh stale mutant and check out its bare SHA
            # (never a branch name) so `_git(work, "checkout", ...)` lands on a detached HEAD,
            # exactly like a pull_request-triggered run -- and require the compile-check job to
            # still exit 0, with the push step's own guard (not a crash) explaining why nothing
            # was pushed.
            _seed(work, "mutant/m-stale-detached", "%s/ops/actors/builder.md" % model_dir,
                 "---\nid: actor/builder\ntype: actor\ncontext: ops\nsummary: the builder "
                 "(edited again)\nstatus: current\n---\nEdited again (seeds a detached-HEAD "
                 "stale check).\n",
                 "seed M-stale (detached-checkout scenario)")
            detached_sha = _git(work, "rev-parse", "mutant/m-stale-detached")[1].strip()
            results, shim_hits, proxy_hits = _run_jobs(work, detached_sha, NORMAL_ACTOR,
                                                        os.path.join(scratch_root, "run-detached"),
                                                        model_dir, pred_map)
            shim_total += shim_hits
            proxy_total += proxy_hits
            cc_detached = _job_result(results, "compile-check")
            push_out_detached = cc_detached["step_outputs"].get("push regenerated commit if any", "")
            check("compile-check job exits 0 on a detached checkout of a stale mutant "
                 "(pull_request-style merge-ref checkout)", cc_detached["rc"] == 0, cc_detached)
            check("the push step skips with a notice (never fails) on a detached checkout",
                 "PUSH skipped" in push_out_detached, push_out_detached)
            _require_budget(run_started, "after detached-checkout run")

            check("no claude shim spawn across every scenario", shim_total == 0, shim_total)
            check("zero proxy hits across every scenario", proxy_total == 0, proxy_total)

            pyyaml_env = dict(os.environ)
            pyyaml_env["HOME"] = os.path.join(scratch_root, "pyyaml-empty-home")
            os.makedirs(pyyaml_env["HOME"], exist_ok=True)
            pyyaml_env["PYTHONPATH"] = os.path.join(work, J.PYVENDOR_DIR)
            rc, _out = _sh([sys.executable, "-s", "-B", "-c", "import yaml; yaml.safe_load('a: 1')"],
                          env=pyyaml_env, check=False)
            check("vendored PyYAML imports under an empty HOME", rc == 0)
        except _BudgetExceeded as exc:
            check("whole self-test run finishes inside the %ds cap" % WHOLE_RUN_CAP_S, False, exc)
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch_root, ignore_errors=True)

    wall_s = time.time() - run_started
    passed = sum(checks)
    total = len(checks)
    print("WALL: %.1fs" % wall_s)
    print("RESULT: %s (%d/%d)" % ("PASS" if passed == total else "FAIL", passed, total))
    return 0 if passed == total else 1



# --------------------------------------------------------------------------- CLI
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="om-ci.py")
    sub = ap.add_subparsers(dest="verb", required=True)

    e = sub.add_parser("emit")
    e.add_argument("tier", choices=["ci-tier0"])
    e.add_argument("--root", default=".")
    e.add_argument("--force", action="store_true")

    s = sub.add_parser("self-test")
    s.add_argument("tier", choices=["ci-tier0"])
    s.add_argument("--keep-scratch", action="store_true")

    args = ap.parse_args(argv)
    if args.verb == "emit":
        for line in emit_ci_tier0(os.path.abspath(args.root), force=args.force):
            print(line)
        return 0
    if args.verb == "self-test":
        return self_test_ci_tier0(keep_scratch=args.keep_scratch)
    return 2


if __name__ == "__main__":
    sys.exit(main())
