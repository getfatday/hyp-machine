#!/usr/bin/env python3
"""selftest-merge-safe-files.py -- regression test for the merge shapes of the files the plugin's
hooks and writers touch (lab H-DRAFT-b9e771b2-hook-writes-worktree).

Builds throwaway consumer repositories under a temp dir (no dependence on the host, the caller's
cwd, or any lab path) and checks the INSTALLED plugin (the tree this file lives in):

  init-scaffold   writes .gitattributes with the union rows (the ledger, .claude/leak-meter-fires.log)
                  and the derived rows (DASHBOARD.md, decisions.html, ledger/north-stars/*.html);
                  a second run is a byte-level no-op ("unchanged")
  init-scaffold   a consumer's own .gitattributes lines are kept; the rows are appended; byte-stable
  init-scaffold   a `ledger_file` override in .claude/hyp.json names that file in the union row
  merge-attrs-check  exit 0 on a scaffolded repo; exit 1 naming MERGE-ATTR-MISSING rows without them
  union merge     two linked worktrees each append one ledger row through decisions.append_line and
                  recompile DASHBOARD.md + decisions.html; the merge keeps BOTH rows with no conflict
                  marker (merge=union), stops on the projections without writing markers into them
                  (merge=binary), and the documented sequence -- compile-dashboard.py <root>, git add
                  DASHBOARD.md decisions.html, git commit --no-edit -- leaves a clean tree, a two-parent
                  merge commit, and --check fresh right after the regeneration
  OFF twin        the same scenario with the .gitattributes removed conflicts ON THE LEDGER (markers
                  in it): the attribute is load-bearing
  row lint        merge-attrs-check names a row spanning two lines; append_line repairs a missing
                  final newline before appending and refuses a row without a date

Usage: python3 scripts/selftest-merge-safe-files.py        exit 0 = PASS, 1 = FAIL
Stdlib only, Python 3.9.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(PLUGIN, "scripts", "init-scaffold.py")
COMPILER = os.path.join(PLUGIN, "scripts", "compile-dashboard.py")
CHECK = os.path.join(PLUGIN, "scripts", "merge-attrs-check.py")
GIT_ENV = {"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "PYTHONDONTWRITEBYTECODE": "1"}
LEDGER = "ledger/ledger.jsonl"
DERIVED = ("DASHBOARD.md", "decisions.html")


def git(cwd, *args, check=True):
    p = subprocess.run(["git", "-C", cwd, "-c", "commit.gpgsign=false"] + list(args),
                       capture_output=True, text=True, env=GIT_ENV)
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed (%d): %s" % (" ".join(args), p.returncode, p.stderr.strip()[:300]))
    return p


def py(argv, cwd=None, timeout=300):
    return subprocess.run([sys.executable, "-B"] + argv, capture_output=True, text=True, env=GIT_ENV, cwd=cwd, timeout=timeout)


def read(path):
    with open(path, encoding="utf-8") as fh:
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


def run_init(root, profile="capture"):
    p = py([INIT, root, "--profile", profile, "--context", "selftest"], cwd=root)
    if p.returncode != 0:
        raise RuntimeError("init-scaffold failed: %s" % (p.stderr or p.stdout)[-300:])
    return p.stdout


def attr_rows(text):
    rows = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            parts = line.split()
            rows.setdefault(parts[0], set()).update(parts[1:])
    return rows


def has_markers(path):
    try:
        return any(l.startswith("<<<<<<< ") or l.startswith(">>>>>>> ") for l in read(path).splitlines())
    except OSError:
        return False


def load_decisions():
    spec = importlib.util.spec_from_file_location("hyp_decisions", os.path.join(PLUGIN, "scripts", "decisions.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def commit_all(cwd, msg):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


def two_worktrees_with_rows(tmp, main, decisions, tag):
    """Scaffolded main -> compiled projections committed -> worktrees b1/b2, each appending one
    row through the plugin's writer and recompiling, each committed. Returns (wt1, wt2)."""
    commit_all(main, "scaffold")
    assert py([COMPILER, main]).returncode == 0
    commit_all(main, "projections")
    wts = []
    for name in ("b1", "b2"):
        wt = os.path.join(tmp, "%s-%s" % (tag, name))
        git(main, "worktree", "add", "-q", wt, "-b", "%s-%s" % (tag, name))
        decisions.append_line(wt, {"kind": "commitment", "id": "row-%s" % name, "date": "2026-09-13",
                                   "text": "%s appended this row [closes-when: path-exists=never-%s.md]" % (name, name)})
        assert py([COMPILER, wt]).returncode == 0
        commit_all(wt, "row %s" % name)
        wts.append(wt)
    return wts[0], wts[1]


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-merge-selftest-")
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name + (": " + str(detail) if detail else ""))

    try:
        decisions = load_decisions()

        # 1. the scaffold writes the rows; a second run is a no-op
        r1 = os.path.join(tmp, "fresh")
        mk_repo(r1)
        out = run_init(r1)
        ga = os.path.join(r1, ".gitattributes")
        rows = attr_rows(read(ga)) if os.path.isfile(ga) else {}
        check("scaffold-writes-gitattributes", "created   .gitattributes" in out and os.path.isfile(ga), out.strip().splitlines()[-1][:80])
        check("scaffold-union-rows", rows.get(LEDGER) == {"merge=union"} and rows.get(".claude/leak-meter-fires.log") == {"merge=union"}, sorted(rows))
        want_derived = {"merge=binary", "-diff", "linguist-generated"}
        check("scaffold-derived-rows", all(rows.get(p) == want_derived for p in DERIVED) and rows.get("ledger/north-stars/*.html") == want_derived,
              {p: sorted(rows.get(p, [])) for p in DERIVED})
        before = read(ga)
        out2 = run_init(r1)
        check("scaffold-rerun-unchanged", "unchanged .gitattributes" in out2 and read(ga) == before)

        # 2. consumer lines kept, rows appended, byte-stable
        r2 = os.path.join(tmp, "custom")
        mk_repo(r2)
        write(os.path.join(r2, ".gitattributes"), "*.png binary\nDASHBOARD.md merge=binary -diff linguist-generated\n")
        out = run_init(r2)
        text = read(os.path.join(r2, ".gitattributes"))
        rows2 = attr_rows(text)
        check("scaffold-keeps-consumer-lines", text.startswith("*.png binary\n") and rows2.get("*.png") == {"binary"}
              and "updated   .gitattributes" in out and text.count("DASHBOARD.md merge=binary") == 1,
              "rows appended: %s" % (out.split("appended: ")[-1].strip()[:80] if "appended: " in out else out.strip()[-80:]))
        check("scaffold-appends-missing-rows-only", rows2.get(LEDGER) == {"merge=union"} and rows2.get("decisions.html") == want_derived)
        out3 = run_init(r2)
        check("scaffold-custom-rerun-byte-stable", read(os.path.join(r2, ".gitattributes")) == text and "unchanged .gitattributes" in out3)

        # 3. a ledger_file override names that file
        r3 = os.path.join(tmp, "override")
        mk_repo(r3, {"profile": "capture", "ledger_file": "ledger/work-ledger.jsonl"})
        run_init(r3)
        rows3 = attr_rows(read(os.path.join(r3, ".gitattributes")))
        check("scaffold-honours-ledger-file-override", rows3.get("ledger/work-ledger.jsonl") == {"merge=union"} and LEDGER not in rows3, sorted(rows3))

        # 4. merge-attrs-check
        p = py([CHECK, "--root", r1])
        check("merge-attrs-check-clean-after-scaffold", p.returncode == 0, p.stdout.strip()[-100:])
        r4 = os.path.join(tmp, "bare")
        mk_repo(r4)
        write(os.path.join(r4, LEDGER), "")
        p = py([CHECK, "--root", r4])
        check("merge-attrs-check-names-missing-rows", p.returncode == 1 and "MERGE-ATTR-MISSING\t%s\tunion" % LEDGER in p.stdout
              and "MERGE-ATTR-MISSING\t.claude/leak-meter-fires.log\tunion" in p.stdout, p.stdout.strip().replace("\n", " | ")[:160])

        # 5. the union merge with the rows in place
        wt1, wt2 = two_worktrees_with_rows(tmp, r1, decisions, "on")
        m = git(wt1, "merge", "--no-edit", "on-b2", check=False)
        unmerged = git(wt1, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        check("union-ledger-merges-without-conflict", LEDGER not in unmerged and not has_markers(os.path.join(wt1, LEDGER)), "unmerged=%s" % unmerged)
        check("derived-projections-stop-the-merge-without-markers", m.returncode != 0 and unmerged and set(unmerged) <= set(DERIVED)
              and not any(has_markers(os.path.join(wt1, d)) for d in DERIVED), "rc=%d unmerged=%s" % (m.returncode, unmerged))
        regen_rc = check_rc = add_rc = commit_rc = None
        if m.returncode != 0:
            regen_rc = py([COMPILER, wt1]).returncode
            check_rc = py([COMPILER, wt1, "--check"]).returncode
            add_rc = git(wt1, "add", *DERIVED, check=False).returncode
            commit_rc = git(wt1, "commit", "--no-edit", check=False).returncode
        check("documented-regeneration-sequence-exits-0", (regen_rc, add_rc, commit_rc) == (0, 0, 0), (regen_rc, add_rc, commit_rc))
        check("dashboard-check-fresh-after-regeneration", check_rc == 0, "rc=%s" % check_rc)
        ledger_txt = read(os.path.join(wt1, LEDGER))
        lines = [l for l in ledger_txt.split("\n") if l.strip()]
        parse_ok = all(isinstance(json.loads(l), dict) for l in lines) if lines else False
        check("ledger-both-rows-one-object-per-line", "row-b1" in ledger_txt and "row-b2" in ledger_txt and parse_ok
              and ledger_txt.endswith("\n"), "%d row(s)" % len(lines))
        porcelain = git(wt1, "status", "--porcelain", check=False).stdout.strip()
        parents = git(wt1, "rev-list", "--parents", "-n", "1", "HEAD", check=False).stdout.split()
        tracked = git(wt1, "ls-files", check=False).stdout.split()
        marked = [t for t in tracked if has_markers(os.path.join(wt1, t))]
        check("merge-commit-clean-tree-no-markers", porcelain == "" and len(parents) == 3 and not marked,
              "porcelain=%r parents=%d markers=%s" % (porcelain[:40], len(parents) - 1, marked))

        # 6. the OFF twin: no .gitattributes -> the ledger itself conflicts
        r5 = os.path.join(tmp, "off")
        mk_repo(r5)
        run_init(r5)
        os.remove(os.path.join(r5, ".gitattributes"))
        c1, c2 = two_worktrees_with_rows(tmp, r5, decisions, "off")
        m = git(c1, "merge", "--no-edit", "off-b2", check=False)
        unmerged = git(c1, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        check("off-twin-ledger-conflicts-without-the-attribute", m.returncode != 0 and LEDGER in unmerged and has_markers(os.path.join(c1, LEDGER)),
              "rc=%d unmerged=%s" % (m.returncode, unmerged))
        git(c1, "merge", "--abort", check=False)

        # 7. the row lint and the writer's guards
        r6 = os.path.join(tmp, "lint")
        mk_repo(r6)
        run_init(r6)
        write(os.path.join(r6, LEDGER), '{"kind": "commitment", "id": "two-lines", "date": "2026-09-13",\n "text": "spans two lines"}\n')
        p = py([CHECK, "--root", r6])
        check("row-lint-names-a-row-spanning-lines", p.returncode == 1 and "LEDGER-ROW-MALFORMED\t1\t" in p.stdout, p.stdout.strip().replace("\n", " | ")[:120])
        write(os.path.join(r6, LEDGER), json.dumps({"kind": "commitment", "id": "no-newline", "date": "2026-09-13", "text": "x"}))
        decisions.append_line(r6, {"kind": "commitment", "id": "after-repair", "date": "2026-09-13", "text": "y"})
        txt = read(os.path.join(r6, LEDGER))
        rows6 = [json.loads(l) for l in txt.split("\n") if l.strip()]
        check("append-line-repairs-missing-final-newline", len(rows6) == 2 and [r["id"] for r in rows6] == ["no-newline", "after-repair"] and txt.endswith("\n"),
              repr(txt[-30:]))
        refused = False
        try:
            decisions.append_line(r6, {"kind": "commitment", "id": "undated", "text": "z"})
        except SystemExit as exc:
            refused = "no date" in str(exc)
        check("append-line-refuses-a-row-without-date", refused and len([l for l in read(os.path.join(r6, LEDGER)).split("\n") if l.strip()]) == 2)
        p = py([CHECK, "--root", r6])
        check("row-lint-clean-after-repair", p.returncode == 0, p.stdout.strip()[-80:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(results)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
