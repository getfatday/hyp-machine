#!/usr/bin/env python3
"""eval-delta.py: materialize a session's DELTA tree for `eval-grade.py`.

  eval-delta.py <clone> <base-commit> <out-dir>      write <out-dir>/ (the tree) and <out-dir>.json
  eval-delta.py --selftest                           build a throwaway repo and check the rule

The delta tree of a session is every file the session ADDED or MODIFIED in its working tree
relative to <base-commit>, copied out with paths preserved, so a case file's graders read only
what the session changed (a copied modified file carries its whole content; a grader over
`research/index.md` therefore sees the pre-existing lines too, which is why an index-line grader
against a lab clone must be written as "the file is in the delta", not "the file has a link").

Rule (frozen with lane H-DRAFT-6ec1691d-lab-plugin-skill-swap, spec Method "Terms"):
  * `git diff --name-status <base>` against the WORKING TREE (committed or not) plus
    `git ls-files --others --exclude-standard` (untracked, not ignored);
  * each path is classified by `git cat-file -e <base>:<path>`: exists at base -> `modified`
    (or `deleted` when absent from the working tree), else `added`;
  * `deleted` paths are LISTED in the json, never copied; ignored-by-git runtime files never
    enter (they are neither tracked nor `--others --exclude-standard`);
  * a rename shows as its two halves (deleted + added) because `--no-renames` is passed;
  * symlinks are copied as the link (never followed); directories never appear (git has none).

The json beside the tree: {"clone", "base", "head", "paths": [{"path", "status"}...],
"counts": {"added", "modified", "deleted"}, "copied": n, "bytes": n}. Stdlib + git only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile


def git(cwd, *args, check=True):
    p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("git %s rc=%d: %s" % (" ".join(args), p.returncode, p.stderr.strip()[:300]))
    return p.stdout


def exists_at(cwd, base, path):
    return subprocess.run(["git", "-C", cwd, "cat-file", "-e", "%s:%s" % (base, path)],
                          capture_output=True).returncode == 0


def delta_paths(clone, base):
    seen = {}
    out = git(clone, "diff", "--name-status", "--no-renames", "-z", base)
    parts = out.split("\0")
    i = 0
    while i + 1 < len(parts):
        status, path = parts[i], parts[i + 1]
        i += 2
        if not path:
            continue
        seen[path] = status[:1]
    for path in git(clone, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path:
            seen.setdefault(path, "??")
    rows = []
    for path in sorted(seen):
        fp = os.path.join(clone, path)
        present = os.path.lexists(fp)
        at_base = exists_at(clone, base, path)
        if at_base and not present:
            st = "deleted"
        elif at_base:
            st = "modified"
        else:
            st = "added" if present else "deleted"
        rows.append({"path": path, "status": st})
    return rows


def materialize(clone, base, out_dir):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    rows = delta_paths(clone, base)
    copied = nbytes = 0
    for r in rows:
        if r["status"] == "deleted":
            continue
        src = os.path.join(clone, r["path"])
        dst = os.path.join(out_dir, r["path"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copyfile(src, dst)
            nbytes += os.path.getsize(dst)
        copied += 1
    counts = {"added": 0, "modified": 0, "deleted": 0}
    for r in rows:
        counts[r["status"]] += 1
    rec = {"clone": clone, "base": base, "head": git(clone, "rev-parse", "HEAD").strip(),
           "rule": "git diff --name-status --no-renames <base> (working tree) + ls-files --others --exclude-standard; "
                   "cat-file -e <base>:<path> classifies; deleted listed never copied; ignored never enter",
           "paths": rows, "counts": counts, "copied": copied, "bytes": nbytes}
    with open(out_dir.rstrip("/") + ".json", "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")
    return rec


def selftest():
    tmp = tempfile.mkdtemp(prefix="eval-delta-selftest-")
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "a"))
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        subprocess.run(["git", "init", "-q", repo], check=True)
        for rel, txt in (("a/keep.md", "keep\n"), ("a/mod.md", "v1\n"), ("a/gone.md", "bye\n"), (".gitignore", "*.log\n")):
            with open(os.path.join(repo, rel), "w") as f:
                f.write(txt)
        os.symlink("keep.md", os.path.join(repo, "a", "link"))
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"], check=True, env=env)
        base = git(repo, "rev-parse", "HEAD").strip()
        with open(os.path.join(repo, "a", "mod.md"), "w") as f:
            f.write("v2\n")
        os.remove(os.path.join(repo, "a", "gone.md"))
        os.makedirs(os.path.join(repo, "b"))
        with open(os.path.join(repo, "b", "new.md"), "w") as f:
            f.write("new\n")
        with open(os.path.join(repo, "runtime.log"), "w") as f:
            f.write("ignored\n")
        os.remove(os.path.join(repo, "a", "link"))
        os.symlink("mod.md", os.path.join(repo, "a", "link"))
        # one committed change after base too: committed-or-not must not matter
        with open(os.path.join(repo, "a", "committed.md"), "w") as f:
            f.write("c\n")
        subprocess.run(["git", "-C", repo, "add", "a/committed.md"], check=True, env=env)
        subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "after"], check=True, env=env)
        out = os.path.join(tmp, "delta")
        rec = materialize(repo, base, out)
        got = {r["path"]: r["status"] for r in rec["paths"]}
        want = {"a/mod.md": "modified", "a/gone.md": "deleted", "b/new.md": "added", "a/link": "modified",
                "a/committed.md": "added"}
        ok = got == want
        ok = ok and not os.path.exists(os.path.join(out, "a", "gone.md"))
        ok = ok and not os.path.exists(os.path.join(out, "runtime.log"))
        ok = ok and not os.path.exists(os.path.join(out, "a", "keep.md"))
        ok = ok and open(os.path.join(out, "a", "mod.md")).read() == "v2\n"
        ok = ok and os.path.islink(os.path.join(out, "a", "link")) and os.readlink(os.path.join(out, "a", "link")) == "mod.md"
        ok = ok and rec["counts"] == {"added": 2, "modified": 2, "deleted": 1} and rec["copied"] == 4
        ok = ok and os.path.isfile(out + ".json")
        print("paths:", got)
        print("SELFTEST %s" % ("OK" if ok else "FAIL"))
        return 0 if ok else 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    if len(argv) == 2 and argv[1] == "--selftest":
        return selftest()
    if len(argv) != 4:
        sys.stderr.write("usage: eval-delta.py <clone> <base-commit> <out-dir> | eval-delta.py --selftest\n")
        return 64
    clone, base, out_dir = os.path.abspath(argv[1]), argv[2], os.path.abspath(argv[3])
    if not os.path.isdir(os.path.join(clone, ".git")) and not os.path.isfile(os.path.join(clone, ".git")):
        sys.stderr.write("eval-delta: not a git work tree: %s\n" % clone)
        return 2
    rec = materialize(clone, base, out_dir)
    print("delta %s: %d added, %d modified, %d deleted (listed); %d file(s) copied, %d bytes"
          % (out_dir, rec["counts"]["added"], rec["counts"]["modified"], rec["counts"]["deleted"], rec["copied"], rec["bytes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
