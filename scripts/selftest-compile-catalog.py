#!/usr/bin/env python3
"""selftest-compile-catalog.py -- regression test for the operating-model catalogue projection
(lab H-DRAFT-4e06e157-om-rows-merge-shape, kept 2026-09-14: five counted looks, A1-A5 pass in
every one, SPRT llr 2.9389 over the 2.8904 promote bound; VERDICT.json beside the lane).

Builds throwaway consumer repositories under a temp dir (no dependence on the host, the
caller's cwd, or any lab path) and checks the INSTALLED plugin (the tree this file lives in):

  compile-catalog.py   byte-identical on re-run over an unchanged node tree; rows sorted by type
                        then id; a zero-node context renders the stub sections; never edits a
                        node file; never reads model.md (a stale model.md is fully overwritten,
                        never merged with)
  init-scaffold         ensure_gitignore appends the model.md row once, is byte-stable on
                        re-run, and keeps a consumer's own ignore line
  init-scaffold         retire_tracked_model_md removes model.md from the index exactly once,
                        keeps the work-tree file, and is a silent no-op once untracked
  union merge (A1)      two linked worktrees each add one new command node to the SAME context;
                        with the ignore row in place the merge exits 0 in both orders (the
                        catalogue is never a tracked path to conflict on), and a fresh clone of
                        either resulting main renders the union of both nodes once regenerated
  om-worker              compile-check's row records that the renderer ran, and fails closed
                        (rc 1) when scripts/compile-catalog.py is missing
  extra types/spellings  an external, an aggregate and a read-models/ node each render their
                        row and the Externals/Aggregates headings (absent for a core-only
                        context); model-lint.py reads 0 E-CATALOG over the result
  templates/skills grep  no shipped template or skill text tells a reader to `git add` or
                        `git commit` model.md (the A2 assumption check from the lane's grade.py)

Usage: python3 scripts/selftest-compile-catalog.py        exit 0 = PASS, 1 = FAIL
Stdlib only, Python 3.9.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(PLUGIN, "scripts", "init-scaffold.py")
COMPILE_CATALOG = os.path.join(PLUGIN, "scripts", "compile-catalog.py")
OM_WORKER = os.path.join(PLUGIN, "scripts", "om-worker.py")
GIT_ENV = {"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "PYTHONDONTWRITEBYTECODE": "1"}


def git(cwd, *args, check=True, env_extra=None):
    env = dict(GIT_ENV)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(["git", "-C", cwd, "-c", "commit.gpgsign=false"] + list(args),
                       capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed (%d): %s" % (" ".join(args), p.returncode, p.stderr.strip()[:300]))
    return p


def py(argv, cwd=None, timeout=300):
    return subprocess.run([sys.executable, "-B"] + argv, capture_output=True, text=True,
                          env=GIT_ENV, cwd=cwd, timeout=timeout)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def mk_repo(path, hyp_json=None):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    if hyp_json is not None:
        write(os.path.join(path, ".claude", "hyp.json"), json.dumps(hyp_json) + "\n")


def node(path, ntype, nid, summary, extra=""):
    write(path, "---\nid: %s\ntype: %s\ncontext: ops\nsummary: %r\nstatus: current\n%s---\n%s.\n"
          % (nid, ntype, summary, extra, nid))


def seed_context(ctx_dir):
    node(os.path.join(ctx_dir, "actors", "builder.md"), "actor", "actor/builder", "the builder")
    node(os.path.join(ctx_dir, "commands", "build.md"), "command", "command/build", "build the node",
        "handler: script/build.py\nissued-by: actor/builder\nexecutor: agent\n")
    node(os.path.join(ctx_dir, "events", "built.md"), "event", "event/built", "node built",
        "representation: row\n")
    node(os.path.join(ctx_dir, "policies", "gate.md"), "policy", "policy/gate", "clean gate",
        "then: [command/build]\n")


def run_init(root, profile="modeling", context="ops"):
    p = py([INIT, root, "--profile", profile, "--context", context], cwd=root)
    if p.returncode != 0:
        raise RuntimeError("init-scaffold failed: %s" % (p.stderr or p.stdout)[-400:])
    return p.stdout


def commit_all(cwd, msg):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-catalog-selftest-")
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name + (": " + str(detail) if detail else ""))

    try:
        # 1. byte-identical on re-run over an unchanged tree
        r1 = os.path.join(tmp, "render")
        ctx = os.path.join(r1, "operating-model", "ops")
        seed_context(ctx)
        out1 = py([COMPILE_CATALOG, ctx]).stdout
        out2 = py([COMPILE_CATALOG, ctx]).stdout
        check("renderer-byte-identical-on-rerun", out1 == out2 and out1, "%d bytes" % len(out1))

        # 2. sorted by type then id, regardless of add order
        node(os.path.join(ctx, "commands", "zzz-last.md"), "command", "command/zzz-last", "last alphabetically")
        node(os.path.join(ctx, "commands", "aaa-first.md"), "command", "command/aaa-first", "first alphabetically")
        out3 = py([COMPILE_CATALOG, ctx]).stdout
        cmd_lines = [l for l in out3.splitlines() if l.startswith("- [command/")]
        check("renderer-sorted-by-type-then-id",
              cmd_lines == sorted(cmd_lines) and cmd_lines[0].startswith("- [command/aaa-first]")
              and "actor/builder" in out3.split("## Commands")[0],
              cmd_lines)

        # 3. zero nodes renders the stub sections
        empty_ctx = os.path.join(tmp, "empty", "operating-model", "ops")
        os.makedirs(empty_ctx)
        out_empty = py([COMPILE_CATALOG, empty_ctx]).stdout
        stub = read(os.path.join(PLUGIN, "templates", "model.md"))
        stub_headings = [l for l in stub.splitlines() if l.startswith("## ")]
        got_headings = [l for l in out_empty.splitlines() if l.startswith("## ")]
        check("renderer-zero-nodes-renders-stub-sections",
              got_headings == stub_headings and out_empty.count("(none yet)") == len(stub_headings),
              {"got": got_headings, "want": stub_headings})

        # 4. never edits a node file
        node_path = os.path.join(ctx, "actors", "builder.md")
        before = read_bytes(node_path)
        py([COMPILE_CATALOG, ctx, "--write"])
        check("renderer-never-touches-node-files", read_bytes(node_path) == before)

        # 5. never reads model.md -- a stale one is fully overwritten, never merged with
        write(os.path.join(ctx, "model.md"), "# stale garbage nobody should see again\n")
        py([COMPILE_CATALOG, ctx, "--write"])
        fresh = read(os.path.join(ctx, "model.md"))
        check("renderer-never-reads-model-md", "stale garbage" not in fresh and "actor/builder" in fresh)

        # 6. ensure_gitignore: appends once, byte-stable on re-run, keeps a consumer's own row
        r2 = os.path.join(tmp, "gitignore")
        mk_repo(r2)
        write(os.path.join(r2, ".gitignore"), "*.pyc\n")
        out = run_init(r2)
        gi = read(os.path.join(r2, ".gitignore"))
        check("ensure-gitignore-appends-row-keeps-consumer-line",
              gi.startswith("*.pyc\n") and "operating-model/*/model.md" in gi
              and "created   .gitignore" not in out and "updated   .gitignore" in out,
              gi)
        gi_before = gi
        out2 = run_init(r2)
        check("ensure-gitignore-rerun-byte-stable",
              read(os.path.join(r2, ".gitignore")) == gi_before and "unchanged .gitignore" in out2)

        # 7. retire: removes the index entry once, keeps the work-tree file, no-op once untracked.
        # Simulates an upgrade from the OLD hand-maintained shape: model.md tracked and committed
        # BEFORE the ignore row ever existed (running the new init-scaffold straight away would
        # never track it in the first place, since the ignore row and the stub write land in the
        # same call -- git never stages a path its own .gitignore already excludes).
        r3 = os.path.join(tmp, "retire")
        mk_repo(r3)
        model_md = os.path.join(r3, "operating-model", "ops", "model.md")
        write(model_md, "# hand-maintained catalogue, the old shape\n\n- [command/old](commands/old.md)\n")
        commit_all(r3, "old hand-maintained model.md, pre-upgrade")
        tracked_before = git(r3, "ls-files", "--", "operating-model/ops/model.md").stdout.strip()
        out = run_init(r3)
        tracked_after = git(r3, "ls-files", "--", "operating-model/ops/model.md").stdout.strip()
        check("retire-removes-index-entry-keeps-worktree-file",
              tracked_before == "operating-model/ops/model.md" and tracked_after == ""
              and os.path.isfile(model_md) and "retired   operating-model/ops/model.md" in out,
              {"before": tracked_before, "after": tracked_after})
        out3 = run_init(r3)
        check("retire-is-a-silent-no-op-once-untracked",
              "retired" not in out3
              and git(r3, "ls-files", "--", "operating-model/ops/model.md").stdout.strip() == "")

        # 8. A1 shape: two worktrees add one node each to the SAME context; merge exits 0 both
        #    orders with the ignore row in place, and a fresh clone renders the union.
        def two_worktree_merge(tag, order):
            base = os.path.join(tmp, "merge-%s" % tag)
            mk_repo(base)
            run_init(base)
            seed_context(os.path.join(base, "operating-model", "ops"))
            py([COMPILE_CATALOG, os.path.join(base, "operating-model", "ops"), "--write"])
            commit_all(base, "seed")
            wts = {}
            for side in ("a", "b"):
                wt = os.path.join(tmp, "merge-%s-wt-%s" % (tag, side))
                git(base, "worktree", "add", "-q", wt, "-b", "%s-%s" % (tag, side))
                node(os.path.join(wt, "operating-model", "ops", "commands", "side-%s.md" % side),
                    "command", "command/side-%s" % side, "side %s node" % side,
                    "handler: script/side.py\nissued-by: actor/builder\nexecutor: agent\n")
                py([COMPILE_CATALOG, os.path.join(wt, "operating-model", "ops"), "--write"])
                commit_all(wt, "side %s" % side)
                wts[side] = wt
            first, second = order
            m = git(wts[first], "merge", "--no-edit", "%s-%s" % (tag, second), check=False)
            unmerged = git(wts[first], "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
            return base, wts[first], m.returncode, unmerged

        base_ab, wt_ab, rc_ab, unmerged_ab = two_worktree_merge("ab", ("a", "b"))
        base_ba, wt_ba, rc_ba, unmerged_ba = two_worktree_merge("ba", ("b", "a"))
        check("a1-merge-exits-0-both-orders",
              rc_ab == 0 and rc_ba == 0 and not unmerged_ab and not unmerged_ba,
              {"ab": (rc_ab, unmerged_ab), "ba": (rc_ba, unmerged_ba)})

        clone_dir = os.path.join(tmp, "clone")
        git(tmp, "clone", "-q", wt_ab, clone_dir)
        clone_ctx = os.path.join(clone_dir, "operating-model", "ops")
        py([COMPILE_CATALOG, clone_ctx, "--write"])
        clone_render = read(os.path.join(clone_ctx, "model.md"))
        check("a1-fresh-clone-renders-the-union",
              "command/side-a" in clone_render and "command/side-b" in clone_render,
              clone_render)

        # 9. om-worker compile-check records the renderer run, and fails closed when it is missing
        r4 = os.path.join(tmp, "worker")
        mk_repo(r4)
        run_init(r4)
        seed_context(os.path.join(r4, "operating-model", "ops"))
        ledger = os.path.join(r4, "ledger", "om-feedback.jsonl")
        p = py([OM_WORKER, "compile-check", "--root", r4])
        rows = [json.loads(l) for l in read(ledger).splitlines() if l.strip()] if os.path.isfile(ledger) else []
        cc_rows = [r for r in rows if r.get("kind") == "model-evaluated"]
        check("om-worker-compile-check-records-renderer-run",
              p.returncode == 0 and len(cc_rows) == 1
              and cc_rows[0].get("catalog_regen") == {"ran": True, "rc": 0, "renderer_found": True},
              cc_rows[0].get("catalog_regen") if cc_rows else None)

        fake_scripts = os.path.join(tmp, "fake-plugin-scripts")
        os.makedirs(fake_scripts, exist_ok=True)
        for fn in ("observatory.py", "model-lint.py"):
            shutil.copy(os.path.join(PLUGIN, "scripts", fn), os.path.join(fake_scripts, fn))
        p2 = py([OM_WORKER, "compile-check", "--root", r4, "--plugin-scripts", fake_scripts])
        check("om-worker-compile-check-fails-closed-without-the-renderer",
              p2.returncode == 1 and "compile-catalog.py missing" in p2.stderr,
              (p2.returncode, p2.stderr.strip()[-160:]))

        # 10. EXTRA_TYPE_DIRS + read-model spellings: an external, an aggregate and a
        # read-models/ node render their rows and the two extra headings only when present;
        # a core-only context (ctx, seeded in checks 1-2, no externals/aggregates) renders
        # neither heading; model-lint.py reads 0 E-CATALOG over the regenerated catalogue.
        r5 = os.path.join(tmp, "extra-types")
        ectx = os.path.join(r5, "operating-model", "ops")
        seed_context(ectx)
        node(os.path.join(ectx, "externals", "vendor-api.md"), "external", "external/vendor-api",
            "third-party vendor API")
        node(os.path.join(ectx, "aggregates", "order-aggregate.md"), "aggregate",
            "aggregate/order-aggregate", "order aggregate root")
        node(os.path.join(ectx, "read-models", "inventory.md"), "read-model",
            "read-model/inventory", "inventory read model", "maintainer: command/build\n")
        py([COMPILE_CATALOG, ectx, "--write"])
        rendered = read(os.path.join(ectx, "model.md"))
        check("renderer-extra-types-and-spellings-render-rows-and-headings",
              "## Externals" in rendered and "## Aggregates" in rendered
              and "external/vendor-api" in rendered and "aggregate/order-aggregate" in rendered
              and "read-model/inventory" in rendered,
              [l for l in rendered.splitlines() if l.startswith("## ") or "vendor-api" in l
               or "order-aggregate" in l or "read-model/inventory" in l])

        core_only = py([COMPILE_CATALOG, ctx]).stdout
        check("renderer-core-only-context-has-no-extra-headings",
              "## Externals" not in core_only and "## Aggregates" not in core_only,
              [l for l in core_only.splitlines() if l.startswith("## ")])

        lint = py([os.path.join(PLUGIN, "scripts", "model-lint.py"), ectx])
        check("model-lint-zero-e-catalog-over-extra-types-context",
              lint.returncode == 0 and "E-CATALOG" not in lint.stdout,
              (lint.returncode, lint.stdout.strip()[-400:]))

        # 11. A2: no shipped template or skill tells a reader to git add/commit model.md
        pattern = re.compile(r"git\s+(add|commit)[^\n]*model\.md")
        hits = []
        for sub in ("templates", "skills"):
            d = os.path.join(PLUGIN, sub)
            for dirpath, _dirnames, filenames in os.walk(d):
                for fn in filenames:
                    if fn in ("init-scaffold.py", "model.md", "gitignore"):
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        text = read(p)
                    except (OSError, UnicodeDecodeError):
                        continue
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if pattern.search(line):
                            hits.append("%s:%d: %s" % (os.path.relpath(p, PLUGIN), lineno, line.strip()))
        check("a2-no-template-or-skill-assumes-model-md-is-tracked", not hits, hits)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
