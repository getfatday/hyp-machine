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


def _sh(cmd, cwd=None, env=None, check=True):
    argv = ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)
    proc = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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


def _predicate_holds(step, actor):
    if "if" not in step:
        return True
    return actor != BOT_IDENTITY  # the one predicate this template ever declares


def _run_jobs(work, ref, actor, scratch_root, model_dir):
    """Checks out `ref` and runs every job's steps in table order under the CI-runner
    constraint, honouring each step's `if`. Returns (per-job results, shim_hits, proxy_hits)."""
    _git(work, "checkout", "-q", ref)
    proxy_port, proxy_log, stop = _start_proxy_sink(scratch_root)
    try:
        env_base, shim_log = _ci_runner_env(scratch_root, actor, proxy_port)
        results = []
        for job in J.JOBS:
            job_rc = 0
            failing_step = None
            output = ""
            for step in job["steps"]:
                if not _predicate_holds(step, actor):
                    continue
                cmd = J.render_step_command(step, model_dir)
                step_env = dict(env_base)
                step_env.update(J.step_env(job, step, model_dir))
                rc, out = _sh(cmd, cwd=work, env=step_env, check=False)
                if rc != 0:
                    job_rc, failing_step, output = rc, step["name"], out
                    break
            results.append({"job": job["name"], "rc": job_rc, "failing_step": failing_step,
                            "output": output})
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


def self_test_ci_tier0(keep_scratch=False):
    checks = []

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
        _build_scratch_consumer(work, origin, model_dir)

        with open(os.path.join(work, WORKFLOW_REL), "r", encoding="utf-8") as fh:
            committed_yaml = fh.read()
        check("rendered YAML equals the committed workflow",
             J.render_yaml(model_dir) == committed_yaml)

        shim_total = proxy_total = 0

        results, shim_hits, proxy_hits = _run_jobs(work, "on-base", NORMAL_ACTOR,
                                                    os.path.join(scratch_root, "run-clean"), model_dir)
        shim_total += shim_hits
        proxy_total += proxy_hits
        check("lint job green on the clean tree", _job_result(results, "lint")["rc"] == 0)

        _seed(work, "mutant/m-lint", "%s/ops/policies/gate-clean.md" % model_dir,
             "---\nid: policy/gate-clean\ntype: policy\ncontext: ops\nsummary: clean gate\n"
             "status: current\nthen: [command/does-not-exist]\n---\nBroken (seeded E-LINK).\n",
             "seed E-LINK")
        results, shim_hits, proxy_hits = _run_jobs(work, "mutant/m-lint", NORMAL_ACTOR,
                                                    os.path.join(scratch_root, "run-mlint"), model_dir)
        shim_total += shim_hits
        proxy_total += proxy_hits
        lint_mlint = _job_result(results, "lint")
        check("lint job red on the E-LINK mutant",
             lint_mlint["rc"] != 0 and "E-LINK" in lint_mlint["output"])

        _seed(work, "mutant/m-stale", "%s/ops/actors/builder.md" % model_dir,
             "---\nid: actor/builder\ntype: actor\ncontext: ops\nsummary: the builder (edited)\n"
             "status: current\n---\nEdited after the compiled artifact (seeds M-stale).\n",
             "seed M-stale")
        head_before = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
        results, shim_hits, proxy_hits = _run_jobs(work, "mutant/m-stale", NORMAL_ACTOR,
                                                    os.path.join(scratch_root, "run-mstale-1"), model_dir)
        shim_total += shim_hits
        proxy_total += proxy_hits
        head_after = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
        rev_count = int(_git(work, "rev-list", "--count", "%s..%s" % (head_before, head_after))[1].strip()) \
            if head_before != head_after else 0
        cc = _job_result(results, "compile-check")
        check("compile-check job regenerates exactly one bot commit on the stale mutant",
             cc["rc"] == 0 and rev_count == 1,
             "rc=%s commits=%d failing_step=%s" % (cc["rc"], rev_count, cc["failing_step"]))
        author = _git(work, "log", "-1", "--format=%an", "mutant/m-stale")[1].strip() if rev_count else None
        check("the regenerate commit is authored as the bot identity",
             rev_count == 0 or author == BOT_IDENTITY, author)

        head_before2 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
        results, shim_hits, proxy_hits = _run_jobs(work, "mutant/m-stale", NORMAL_ACTOR,
                                                    os.path.join(scratch_root, "run-mstale-2"), model_dir)
        shim_total += shim_hits
        proxy_total += proxy_hits
        head_after2 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
        check("a second run adds zero commits", head_before2 == head_after2)
        check("compile-check job still exits 0 on the second run",
             _job_result(results, "compile-check")["rc"] == 0)

        head_before3 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
        _run_jobs(work, "mutant/m-stale", BOT_IDENTITY,
                 os.path.join(scratch_root, "run-mstale-bot"), model_dir)
        head_after3 = _git(work, "rev-parse", "mutant/m-stale")[1].strip()
        check("the bot actor's own push never re-triggers the regenerate step",
             head_before3 == head_after3)

        check("no claude shim spawn across every scenario", shim_total == 0, shim_total)
        check("zero proxy hits across every scenario", proxy_total == 0, proxy_total)

        pyyaml_env = dict(os.environ)
        pyyaml_env["HOME"] = os.path.join(scratch_root, "pyyaml-empty-home")
        os.makedirs(pyyaml_env["HOME"], exist_ok=True)
        pyyaml_env["PYTHONPATH"] = os.path.join(work, J.PYVENDOR_DIR)
        rc, _out = _sh([sys.executable, "-s", "-B", "-c", "import yaml; yaml.safe_load('a: 1')"],
                      env=pyyaml_env, check=False)
        check("vendored PyYAML imports under an empty HOME", rc == 0)
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch_root, ignore_errors=True)

    passed = sum(checks)
    total = len(checks)
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
