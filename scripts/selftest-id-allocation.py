#!/usr/bin/env python3
"""selftest-id-allocation.py -- regression test for the draft-then-allocate id gate.

Builds its own throwaway consumer repositories under a temp dir (no dependence on the
host, the caller's cwd, or any lab path) and drives the INSTALLED gate
(scripts/id-rectify.py, the tree this file lives in) through:

  two-branch concurrent land       distinct draft handles land in turn through the gate:
                                    canonical H-004 then H-005 allocated, the colliding
                                    incoming fragment id 4 renumbers to 5, zero draft tokens
                                    survive outside a rectification report or a manifest,
                                    every cited id resolves, lint 0, both lands fast-forward
  byte-identical dedupe            exit 0, empty id_map, incoming copy removed
  landed-fragment citation         refusal, exit 3, tree (head sha) unchanged
  usage error                      exit 2
  P6 fragment without integer id   lint FRAGMENT-WITHOUT-INTEGER-ID, both the id-line and
                                    no-id-line variants; repair then lands clean
  P7 hash-less draft handle        lint MALFORMED-DRAFT-HANDLE; repair then lands clean
  P8 handle inside a filename      lint DRAFT-HANDLE-SURVIVES, composed with a renumber
                                    forced by a sibling land; repair then lands clean

Usage: python3 scripts/selftest-id-allocation.py    exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-293 (v3 patch, kept 5/5: 16/16 concurrent registrations landed,
OFF collided 4/4) and H-295 (kept 5/5 run 2: cold second sessions honored the handle 3/3,
offline, OFF collided 3/3); the P6/P7/P8 cases are adapted from the lab's own gate selftest.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(PLUGIN, "scripts", "id-rectify.py")

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="selftest@example.invalid",
              GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="selftest@example.invalid",
              GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
              HOME=os.environ.get("HOME", "/"), PATH=os.environ.get("PATH", "/usr/bin:/bin"))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(cond)
    print(("PASS " if cond else "FAIL ") + name + (": " + str(detail) if detail else ""))


def git(repo, *args, check_rc=True):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                       env=GIT_ENV)
    if check_rc and p.returncode != 0:
        raise SystemExit("git %s failed (rc=%d): %s%s" % (" ".join(args[:3]), p.returncode,
                                                          p.stdout, p.stderr))
    return p.stdout


def gate(repo, base, head, lint=False):
    cmd = [sys.executable, GATE, "--repo", repo, "--base", base, "--head", head]
    if lint:
        cmd.append("--lint")
    p = subprocess.run(cmd, capture_output=True, text=True, env=GIT_ENV)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {}
    return p.returncode, out


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


SPEC = ("# %s: selftest spec\n\n## Status\n%s\n\n## Hypothesis\nx\n\n## Method\nx\n\n"
       "## Binary assertions\n1. x\n\n## Verdict rule\nx\n\n## Runs\n")
FRAG = "---\nid: %d\ndate: 2026-09-04\ntype: capture\n---\n\nseed\n"


def build_repo(tmp, name):
    """A minimal consumer repository, seeded with three landed specs and fragments
    (H-001..H-003, fragment ids 1..3), so drafts allocate H-004 onward."""
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    write(repo, ".claude/hyp.json", json.dumps({"profile": "experiments", "context": name}))
    for i, slug in ((1, "alpha"), (2, "beta"), (3, "gamma")):
        write(repo, "hypotheses/H-%03d-%s.md" % (i, slug), SPEC % ("H-%03d-%s" % (i, slug), "kept"))
        write(repo, "experiments/journal-fragments/%04d-seed-%s.md" % (i, slug), FRAG % i)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "scaffold + seeds")
    return repo


def all_ids_resolve(repo, ref):
    """Every H-NNN token cited anywhere in the tree at ref binds exactly one spec path."""
    names = git(repo, "ls-tree", "-r", "--name-only", ref).split()
    specs = {m.group(1) for m in (re.match(r"^hypotheses/(H-\d{3,4})-[^/]+\.md$", n)
                                  for n in names) if m}
    cited = set()
    for n in names:
        body = git(repo, "show", "%s:%s" % (ref, n))
        cited |= set(re.findall(r"\bH-\d{3,4}\b", body))
    unresolved = cited - specs
    return (not unresolved), unresolved


def draft_tokens_outside_reports(repo, ref):
    """H-DRAFT tokens (in path or content) surviving outside a rectification report or a
    manifest are a leak; anywhere else they must be gone by the time a branch lands."""
    names = git(repo, "ls-tree", "-r", "--name-only", ref).split()
    survivors = []
    for n in names:
        if n.endswith("rectification-report.md") or n.startswith("manifests/"):
            continue
        if "DRAFT" in n:
            survivors.append(n)
            continue
        body = git(repo, "show", "%s:%s" % (ref, n))
        if re.search(r"H-DRAFT-[0-9a-f]{8}", body):
            survivors.append(n)
    return survivors


# ---------------------------------------------------------------------------------------
# two-branch concurrent land: distinct draft handles, colliding fragment id, landed in turn
# ---------------------------------------------------------------------------------------
def test_two_branch(tmp):
    repo = build_repo(tmp, "two-branch")

    git(repo, "checkout", "-q", "-b", "reg-a")
    write(repo, "hypotheses/H-DRAFT-aaaaaaaa-feature-a.md",
         "# H-DRAFT-aaaaaaaa-feature-a: feature a\n\n## Status\ndraft\n\n## Hypothesis\nx\n")
    write(repo, "experiments/journal-fragments/0004-register-a.md",
         "---\nid: 4\ndate: 2026-09-04\ntype: capture\n---\n\nRegistered H-DRAFT-aaaaaaaa.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "register a")

    git(repo, "checkout", "-q", "-b", "reg-b", "main")
    write(repo, "hypotheses/H-DRAFT-bbbbbbbb-feature-b.md",
         "# H-DRAFT-bbbbbbbb-feature-b: feature b\n\n## Status\ndraft\n\n## Hypothesis\nx\n")
    write(repo, "experiments/journal-fragments/0004-register-b.md",
         "---\nid: 4\ndate: 2026-09-04\ntype: capture\n---\n\nRegistered H-DRAFT-bbbbbbbb.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "register b")

    git(repo, "checkout", "-q", "reg-a")
    rc, out = gate(repo, "main", "reg-a")
    check("two-branch: reg-a repair exit 0", rc == 0, out.get("error") or out.get("refusal"))
    check("two-branch: reg-a allocates H-004", out.get("id_map", {}).get("H-DRAFT-aaaaaaaa") == "H-004",
         out.get("id_map"))
    lrc, lout = gate(repo, "main", "reg-a", lint=True)
    check("two-branch: reg-a lint exit 0", lrc == 0, lout.get("findings"))
    git(repo, "checkout", "-q", "main")
    ff = subprocess.run(["git", "-C", repo, "merge", "-q", "--ff-only", "reg-a"],
                        capture_output=True, text=True, env=GIT_ENV)
    check("two-branch: reg-a fast-forwards", ff.returncode == 0, ff.stderr[-200:])

    git(repo, "checkout", "-q", "reg-b")
    git(repo, "merge", "-q", "main", "-m", "bring main in")
    rc, out = gate(repo, "main", "reg-b")
    check("two-branch: reg-b repair exit 0", rc == 0, out.get("error") or out.get("refusal"))
    check("two-branch: reg-b allocates H-005", out.get("id_map", {}).get("H-DRAFT-bbbbbbbb") == "H-005",
         out.get("id_map"))
    check("two-branch: colliding fragment id 4 renumbers to 5",
         out.get("frag_renumbers") == [{"path": "experiments/journal-fragments/0004-register-b.md",
                                        "old": 4, "new": 5}],
         out.get("frag_renumbers"))
    lrc, lout = gate(repo, "main", "reg-b", lint=True)
    check("two-branch: reg-b lint exit 0", lrc == 0, lout.get("findings"))
    git(repo, "checkout", "-q", "main")
    ff = subprocess.run(["git", "-C", repo, "merge", "-q", "--ff-only", "reg-b"],
                        capture_output=True, text=True, env=GIT_ENV)
    check("two-branch: reg-b fast-forwards", ff.returncode == 0, ff.stderr[-200:])

    survivors = draft_tokens_outside_reports(repo, "main")
    check("two-branch: zero draft tokens survive outside reports/manifests", not survivors, survivors)
    ok, unresolved = all_ids_resolve(repo, "main")
    check("two-branch: every cited id resolves", ok, unresolved)


# ---------------------------------------------------------------------------------------
# byte-identical dedupe
# ---------------------------------------------------------------------------------------
def test_dedupe(tmp):
    repo = build_repo(tmp, "dedupe")
    git(repo, "checkout", "-q", "-b", "reg-1")
    write(repo, "hypotheses/H-DRAFT-cccccccc-feature-c.md",
         "# H-DRAFT-cccccccc-feature-c: feature c\n\n## Status\ndraft\n\n## Hypothesis\nx\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "register c")
    rc, out = gate(repo, "main", "reg-1")
    check("dedupe: original registration repair exit 0", rc == 0, out.get("error"))
    landed_path = "hypotheses/H-004-feature-c.md"
    git(repo, "checkout", "-q", "main")
    subprocess.run(["git", "-C", repo, "merge", "-q", "--ff-only", "reg-1"], env=GIT_ENV, check=True)
    landed_bytes = git(repo, "show", "main:%s" % landed_path)

    git(repo, "checkout", "-q", "-b", "reg-dup", "main")
    write(repo, "hypotheses/H-DRAFT-dddddddd-feature-c-again.md", landed_bytes)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "re-register c byte-identically")
    rc, out = gate(repo, "main", "reg-dup")
    check("dedupe: exit 0", rc == 0, out.get("error") or out.get("refusal"))
    check("dedupe: empty id_map (no allocation for a byte-identical arrival)",
         out.get("id_map") == {}, out.get("id_map"))
    check("dedupe: draft recorded as a dedupe alias, not a fresh allocation",
         [d["path"] for d in out.get("draft_dedupes", [])] ==
         ["hypotheses/H-DRAFT-dddddddd-feature-c-again.md"], out.get("draft_dedupes"))


# ---------------------------------------------------------------------------------------
# landed-fragment citation refusal
# ---------------------------------------------------------------------------------------
def test_refusal(tmp):
    repo = build_repo(tmp, "refusal")
    before_sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "-b", "reg-1")
    # a colliding spec (same id as a landed one, different content)...
    write(repo, "hypotheses/H-001-impostor.md", SPEC % ("H-001-impostor", "draft"))
    # ...cited on a newly-added line of an already-LANDED fragment: refuse, don't mangle.
    with open(os.path.join(repo, "experiments/journal-fragments/0001-seed-alpha.md"), "a",
             encoding="utf-8") as fh:
        fh.write("\nSee also H-001.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "collide and cite on a landed fragment")
    head_sha = git(repo, "rev-parse", "reg-1").strip()
    rc, out = gate(repo, "main", "reg-1")
    check("refusal: exit 3", rc == 3, out)
    check("refusal: names the landed fragment", bool(out.get("refusal")), out.get("refusal"))
    after_sha = git(repo, "rev-parse", "reg-1").strip()
    check("refusal: tree (head sha) unchanged", after_sha == head_sha, (after_sha, head_sha))
    check("refusal: main unmoved", git(repo, "rev-parse", "main").strip() == before_sha, "")


# ---------------------------------------------------------------------------------------
# usage error
# ---------------------------------------------------------------------------------------
def test_usage_error(tmp):
    repo = build_repo(tmp, "usage-error")
    rc, out = gate(repo, "no-such-ref", "main")
    check("usage error: exit 2", rc == 2, out)
    check("usage error: reports a tool error", bool(out.get("error")), out.get("error"))


# ---------------------------------------------------------------------------------------
# P6: an incoming fragment with no integer id (name and/or frontmatter)
# ---------------------------------------------------------------------------------------
def test_p6(tmp):
    for variant, frag_body in (
            ("p6-id-line-is-handle",
             "---\nid: {H}\ndate: 2026-09-04\ntype: capture\n---\n\nRegistered {H}.\n"),
            ("p6-no-id-line",
             "---\ndate: 2026-09-04\ntype: capture\n---\n\nRegistered {H}.\n")):
        repo = build_repo(tmp, variant)
        git(repo, "checkout", "-q", "-b", "reg-1")
        handle = "H-DRAFT-0d53e5e3"
        write(repo, "hypotheses/%s-snapshot-errors.md" % handle,
             "# %s-snapshot-errors: snapshot errors\n\n## Status\ndraft\n\n## Hypothesis\nx\n" % handle)
        frag_path = "experiments/journal-fragments/DRAFT-0d53e5e3-b14-registration.md"
        write(repo, frag_path, frag_body.replace("{H}", handle))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "register %s" % handle)

        lrc, lout = gate(repo, "main", "reg-1", lint=True)
        check("%s: lint reports FRAGMENT-WITHOUT-INTEGER-ID" % variant,
             lrc == 1 and any(f.get("class") == "FRAGMENT-WITHOUT-INTEGER-ID"
                              for f in lout.get("findings", [])), lout.get("findings"))

        rc, out = gate(repo, "main", "reg-1")
        check("%s: repair exit 0" % variant, rc == 0, out.get("error") or out.get("refusal"))
        check("%s: spec allocated H-004" % variant, out.get("id_map", {}).get(handle) == "H-004",
             out.get("id_map"))
        check("%s: fragment allocated id 4" % variant,
             out.get("frag_allocs") == [{"path": frag_path, "new": 4}], out.get("frag_allocs"))
        ls = git(repo, "ls-tree", "-r", "--name-only", "reg-1", "--",
                 "experiments/journal-fragments").split()
        check("%s: fragment renamed, no DRAFT survives in its name" % variant,
             "experiments/journal-fragments/0004-b14-registration.md" in ls
             and not any("DRAFT" in p for p in ls), ls)

        lrc, lout = gate(repo, "main", "reg-1", lint=True)
        check("%s: lint after repair exit 0" % variant, lrc == 0, lout.get("findings"))
        git(repo, "checkout", "-q", "main")
        ff = subprocess.run(["git", "-C", repo, "merge", "-q", "--ff-only", "reg-1"],
                            capture_output=True, text=True, env=GIT_ENV)
        check("%s: lands clean (fast-forward)" % variant, ff.returncode == 0, ff.stderr[-200:])


# ---------------------------------------------------------------------------------------
# P7: a draft handle with no well-formed hash8
# ---------------------------------------------------------------------------------------
def test_p7(tmp):
    repo = build_repo(tmp, "p7")
    git(repo, "checkout", "-q", "-b", "reg-1")
    write(repo, "hypotheses/H-DRAFT-schema-two-reviewer.md",
         "# H-DRAFT-schema-two-reviewer: two reviewers\n\n## Status\ndraft\n\n"
         "## Hypothesis\nTwo reviewers cut schema regressions.\n")
    write(repo, "experiments/journal-fragments/0004-register-schema.md",
         "---\nid: 4\ndate: 2026-09-04\ntype: capture\n---\n\nRegistered H-DRAFT-schema-two-reviewer.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "register malformed handle")

    lrc, lout = gate(repo, "main", "reg-1", lint=True)
    check("p7: lint reports MALFORMED-DRAFT-HANDLE",
         lrc == 1 and any(f.get("class") == "MALFORMED-DRAFT-HANDLE"
                          for f in lout.get("findings", [])), lout.get("findings"))

    rc, out = gate(repo, "main", "reg-1")
    check("p7: repair exit 0", rc == 0, out.get("error") or out.get("refusal"))
    check("p7: allocated H-004", out.get("id_map", {}).get("H-DRAFT-schema-two-reviewer") == "H-004",
         out.get("id_map"))
    ls = git(repo, "ls-tree", "-r", "--name-only", "reg-1", "--", "hypotheses").split()
    check("p7: spec renamed H-004-schema-two-reviewer.md, no DRAFT survives",
         "hypotheses/H-004-schema-two-reviewer.md" in ls and not any("DRAFT" in p for p in ls), ls)
    body = git(repo, "show", "reg-1:experiments/journal-fragments/0004-register-schema.md")
    check("p7: fragment citation rewritten to H-004", "H-004" in body and "H-DRAFT-schema" not in body,
         body[-80:])

    lrc, lout = gate(repo, "main", "reg-1", lint=True)
    check("p7: lint after repair exit 0", lrc == 0, lout.get("findings"))
    git(repo, "checkout", "-q", "main")
    ff = subprocess.run(["git", "-C", repo, "merge", "-q", "--ff-only", "reg-1"],
                        capture_output=True, text=True, env=GIT_ENV)
    check("p7: lands clean (fast-forward)", ff.returncode == 0, ff.stderr[-200:])


# ---------------------------------------------------------------------------------------
# P8: a draft handle inside a fragment FILENAME, composed with a sibling-forced renumber
# ---------------------------------------------------------------------------------------
def test_p8(tmp):
    repo = build_repo(tmp, "p8")
    git(repo, "checkout", "-q", "-b", "reg-1")
    handle = "H-DRAFT-27f22cc8"
    write(repo, "hypotheses/%s-two-reviewer.md" % handle,
         "# %s-two-reviewer: two reviewers\n\n## Status\ndraft\n\n## Hypothesis\nx\n" % handle)
    write(repo, "experiments/journal-fragments/0004-register-%s.md" % handle,
         "---\nid: 4\ndate: 2026-09-04\ntype: capture\n---\n\nRegistered %s.\n" % handle)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "register with handle in fragment filename")

    # sibling lands fragment id 4 on main first, forcing a renumber on top of the rewrite
    git(repo, "checkout", "-q", "main")
    write(repo, "experiments/journal-fragments/0004-sibling.md",
         "---\nid: 4\ndate: 2026-09-04\ntype: capture\n---\n\nsibling\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "sibling fragment 4")
    git(repo, "checkout", "-q", "reg-1")
    git(repo, "merge", "-q", "main", "-m", "bring main in")

    lrc, lout = gate(repo, "main", "reg-1", lint=True)
    check("p8: lint reports DRAFT-HANDLE-SURVIVES",
         lrc == 1 and any(f.get("class") == "DRAFT-HANDLE-SURVIVES" for f in lout.get("findings", [])),
         lout.get("findings"))

    rc, out = gate(repo, "main", "reg-1")
    check("p8: repair exit 0", rc == 0, out.get("error") or out.get("refusal"))
    ls = git(repo, "ls-tree", "-r", "--name-only", "reg-1", "--",
             "experiments/journal-fragments").split()
    check("p8: fragment renamed 0005-register-H-004.md (renumber + handle rewrite composed)",
         "experiments/journal-fragments/0005-register-H-004.md" in ls
         and not any("DRAFT" in p for p in ls), ls)

    lrc, lout = gate(repo, "main", "reg-1", lint=True)
    check("p8: lint after repair exit 0", lrc == 0, lout.get("findings"))
    git(repo, "checkout", "-q", "main")
    ff = subprocess.run(["git", "-C", repo, "merge", "-q", "--ff-only", "reg-1"],
                        capture_output=True, text=True, env=GIT_ENV)
    check("p8: lands clean (fast-forward)", ff.returncode == 0, ff.stderr[-200:])


def main():
    tmp = tempfile.mkdtemp(prefix="hyp-id-allocation-selftest-")
    try:
        test_two_branch(tmp)
        test_dedupe(tmp)
        test_refusal(tmp)
        test_usage_error(tmp)
        test_p6(tmp)
        test_p7(tmp)
        test_p8(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(RESULTS)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(RESULTS), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
