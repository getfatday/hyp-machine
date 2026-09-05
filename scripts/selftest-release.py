#!/usr/bin/env python3
"""selftest-release.py -- regression test for the changeset release flow.

Builds throwaway plugin repositories under a temp dir (no dependence on the host, the
caller's cwd, or any remote) and drives the INSTALLED scripts/release.py and
scripts/changeset-check.py from the tree this file lives in through:

  release.py     patch / minor / major bumps from a v0.3.3 baseline (major -> 0.4.0 pre-1.0),
                 major from v1.2.3 -> 2.0.0, highest-bump-wins with mixed files, none-only
                 consumption (commit, no tag), no-op on an empty tree, plugin.json/tag drift
                 -> exit 2, plugin.json formatting preserved (only the version value changes),
                 CHANGELOG ordering (newest first, existing content kept, filename after each
                 body), idempotence (second run is a no-op), annotated tag exists after a run
                 (local tag; --publish is not used, there is no remote)
  --publish      against a local bare "origin": (a) happy path pushes main, then creates
                 and pushes the tag, tag points at origin/main; (b) push race via
                 --test-inject-commit (a second changeset lands on origin between compute
                 and push): first push rejected, retry batches both changesets into ONE
                 release with the higher bump, exactly one tag, on the pushed commit;
                 (c) a push that fails outright (pre-receive hook) leaves no tag anywhere;
                 the job syncs to origin/main first (a stale local checkout is discarded);
                 resume when the release commit is on origin but the tag is missing
  changeset-check  missing changeset -> 1, bad frontmatter -> 1, unknown bump -> 1,
                 version edit -> 1, CHANGELOG edit -> 1, valid -> 0, none without body -> 0

Usage: python3 scripts/selftest-release.py    exit 0 = PASS, 1 = FAIL
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE = os.path.join(PLUGIN, "scripts", "release.py")
CHECK = os.path.join(PLUGIN, "scripts", "changeset-check.py")

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="selftest@example.invalid",
               GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="selftest@example.invalid",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
GIT_ENV.pop("GH_TOKEN", None)
GIT_ENV.pop("GITHUB_TOKEN", None)

PLUGIN_JSON_TEXT = ('{\n  "name": "hyp",\n  "version": "%s",\n'
                    '  "description": "fixture plugin, version 9.9.9 mentioned in prose",\n'
                    '  "keywords": [\n    "hypothesis",\n    "version"\n  ]\n}\n')
EXISTING_CHANGELOG = ("# Changelog\n\nNewest first. Written by CI.\n\n"
                      "## 0.3.3 (2026-09-04)\n\n### Fixed\n\n- older entry kept intact (old.md)\n")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (": " + str(detail).strip() if (detail and not cond) else ""))


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, env=GIT_ENV, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)


def git(repo, *args):
    res = run(["git"] + list(args), repo)
    if res.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), res.stderr))
    return res.stdout


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(repo, rel):
    with open(os.path.join(repo, rel), encoding="utf-8") as fh:
        return fh.read()


def make_repo(root, name, version, changelog=EXISTING_CHANGELOG):
    repo = os.path.join(root, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    write(repo, ".claude-plugin/plugin.json", PLUGIN_JSON_TEXT % version)
    write(repo, ".changeset/README.md", "# Changesets\n\nconvention file, never consumed\n")
    write(repo, "README.md", "# fixture\n")
    if changelog is not None:
        write(repo, "CHANGELOG.md", changelog)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "baseline")
    git(repo, "tag", "-a", "v" + version, "-m", "release: v" + version)
    return repo


def add_changeset(repo, slug, bump, body="", commit=True):
    text = "---\nbump: %s\n---\n%s" % (bump, body + "\n" if body else "")
    write(repo, ".changeset/%s.md" % slug, text)
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "add changeset " + slug)


def release(repo, *extra):
    return run([sys.executable, RELEASE, "--repo", repo] + list(extra), repo)


def plugin_version(repo):
    return json.loads(read(repo, ".claude-plugin/plugin.json"))["version"]


def tags(repo):
    return git(repo, "tag", "--merged", "HEAD").split()


def changesets(repo):
    return sorted(n for n in os.listdir(os.path.join(repo, ".changeset")) if n != "README.md")


# --------------------------------------------------------------------------- release.py

def test_bumps(root):
    for bump, expect in (("patch", "0.3.4"), ("minor", "0.4.0"), ("major", "0.4.0")):
        repo = make_repo(root, "bump-" + bump, "0.3.3")
        add_changeset(repo, "H-1-" + bump, bump, "a %s change." % bump)
        res = release(repo)
        check("%s bump from v0.3.3 exits 0" % bump, res.returncode == 0, res.stderr.strip())
        check("%s bump from v0.3.3 -> %s in plugin.json" % (bump, expect),
              plugin_version(repo) == expect, plugin_version(repo))
        check("%s bump: tag v%s reachable from HEAD" % (bump, expect), "v" + expect in tags(repo), tags(repo))
        check("%s bump: changeset consumed" % bump, changesets(repo) == [], changesets(repo))
        check("%s bump: HEAD commit is 'release: v%s'" % (bump, expect),
              git(repo, "log", "-1", "--format=%s").strip() == "release: v" + expect)
    repo = make_repo(root, "major-post-1", "1.2.3")
    add_changeset(repo, "break", "major", "removed a command.")
    res = release(repo)
    check("major from v1.2.3 -> 2.0.0", res.returncode == 0 and plugin_version(repo) == "2.0.0",
          plugin_version(repo))
    section = read(repo, "CHANGELOG.md")
    check("major entry under '### Breaking'", "### Breaking\n\n- removed a command. (break.md)" in section)


def test_mixed(root):
    repo = make_repo(root, "mixed", "0.3.3")
    add_changeset(repo, "b-fix", "patch", "fixed a thing.")
    add_changeset(repo, "a-feature", "minor", "added a thing.")
    add_changeset(repo, "c-docs", "none")
    res = release(repo)
    check("mixed patch+minor+none exits 0", res.returncode == 0, res.stderr.strip())
    check("highest bump wins (minor) -> 0.4.0", plugin_version(repo) == "0.4.0", plugin_version(repo))
    log = read(repo, "CHANGELOG.md")
    added_at = log.find("### Added")
    fixed_at = log.find("### Fixed\n\n- fixed a thing. (b-fix.md)")
    check("Added section precedes Fixed section", 0 < added_at < fixed_at, (added_at, fixed_at))
    check("none-bump body absent from changelog", "c-docs" not in log)
    check("all three changesets consumed, README kept",
          changesets(repo) == [] and os.path.exists(os.path.join(repo, ".changeset/README.md")))


def test_none_only(root):
    repo = make_repo(root, "none-only", "0.3.3")
    add_changeset(repo, "typo", "none")
    add_changeset(repo, "ci", "none", "optional body on a none changeset.")
    before_log = read(repo, "CHANGELOG.md")
    res = release(repo)
    check("none-only exits 0", res.returncode == 0, res.stderr.strip())
    check("none-only: version unchanged", plugin_version(repo) == "0.3.3")
    check("none-only: no new tag", tags(repo) == ["v0.3.3"], tags(repo))
    check("none-only: files deleted", changesets(repo) == [], changesets(repo))
    check("none-only: commit 'chore: consume no-op changesets'",
          git(repo, "log", "-1", "--format=%s").strip() == "chore: consume no-op changesets")
    check("none-only: CHANGELOG untouched", read(repo, "CHANGELOG.md") == before_log)


def test_noop_and_idempotence(root):
    repo = make_repo(root, "noop", "0.3.3")
    head = git(repo, "rev-parse", "HEAD")
    res = release(repo)
    check("empty tree: exit 0", res.returncode == 0, res.stderr.strip())
    check("empty tree: no commit", git(repo, "rev-parse", "HEAD") == head)
    check("empty tree: says nothing to do", "nothing to do" in res.stdout, res.stdout.strip())
    add_changeset(repo, "H-2-fix", "patch", "one fix.")
    first = release(repo)
    head_after = git(repo, "rev-parse", "HEAD")
    second = release(repo)
    check("idempotence: second run exits 0", first.returncode == 0 and second.returncode == 0,
          second.stderr.strip())
    check("idempotence: second run makes no commit", git(repo, "rev-parse", "HEAD") == head_after)
    check("idempotence: still exactly one v0.3.4 tag", tags(repo).count("v0.3.4") == 1, tags(repo))
    kind = git(repo, "cat-file", "-t", "v0.3.4").strip()
    check("tag v0.3.4 is annotated", kind == "tag", kind)


def test_drift(root):
    repo = make_repo(root, "drift", "0.3.3")
    write(repo, ".claude-plugin/plugin.json", PLUGIN_JSON_TEXT % "0.3.5")
    git(repo, "commit", "-qam", "hand edit of the version")
    add_changeset(repo, "H-3", "patch", "a fix.")
    res = release(repo)
    check("plugin.json/tag drift -> exit 2", res.returncode == 2, res.returncode)
    check("drift message names both values", "0.3.5" in res.stderr and "v0.3.3" in res.stderr, res.stderr.strip())
    check("drift: no release commit, no new tag", tags(repo) == ["v0.3.3"]
          and git(repo, "log", "-1", "--format=%s").strip() == "add changeset H-3")
    # drift also detected with no pending changesets
    repo2 = make_repo(root, "drift-empty", "0.3.3")
    write(repo2, ".claude-plugin/plugin.json", PLUGIN_JSON_TEXT % "0.3.4")
    git(repo2, "commit", "-qam", "hand edit")
    res2 = release(repo2)
    check("drift with nothing pending still -> exit 2", res2.returncode == 2, res2.returncode)
    # invalid changeset on main -> exit 1
    repo3 = make_repo(root, "invalid-main", "0.3.3")
    write(repo3, ".changeset/bad.md", "---\nbump: huge\n---\nbody\n")
    git(repo3, "add", "-A")
    git(repo3, "commit", "-qm", "bad changeset")
    res3 = release(repo3)
    check("invalid changeset on main -> exit 1", res3.returncode == 1, res3.returncode)
    # dirty tree refused
    repo4 = make_repo(root, "dirty", "0.3.3")
    write(repo4, "README.md", "# changed but uncommitted\n")
    res4 = release(repo4)
    check("dirty working tree -> exit 2", res4.returncode == 2, res4.returncode)


def test_formatting_and_changelog(root):
    repo = make_repo(root, "format", "0.3.3")
    before = read(repo, ".claude-plugin/plugin.json")
    add_changeset(repo, "H-4-two-lines", "patch", "line one of the entry\nline two of the entry.")
    res = release(repo)
    after = read(repo, ".claude-plugin/plugin.json")
    check("formatting: exit 0", res.returncode == 0, res.stderr.strip())
    check("formatting: only the version value changed",
          after == before.replace('"version": "0.3.3"', '"version": "0.3.4"'))
    check("formatting: prose '9.9.9' untouched", '"fixture plugin, version 9.9.9' in after)
    check("formatting: still valid JSON", json.loads(after)["version"] == "0.3.4")
    log = read(repo, "CHANGELOG.md")
    check("changelog: header and intro kept", log.startswith("# Changelog\n\nNewest first. Written by CI.\n\n"))
    new_at = log.find("## 0.3.4 (")
    old_at = log.find("## 0.3.3 (2026-09-04)")
    check("changelog: new section before the old one", 0 < new_at < old_at, (new_at, old_at))
    check("changelog: old entry kept verbatim", "- older entry kept intact (old.md)" in log)
    check("changelog: date is UTC ISO", re.search(r"^## 0\.3\.4 \(\d{4}-\d{2}-\d{2}\)$", log, re.M) is not None)
    check("changelog: multi-line body verbatim, filename after last line",
          "- line one of the entry\n  line two of the entry. (H-4-two-lines.md)" in log)
    check("stdout carries the new section", "## 0.3.4 (" in res.stdout and "(H-4-two-lines.md)" in res.stdout)
    # fresh repo without CHANGELOG.md gets one created
    repo2 = make_repo(root, "no-changelog", "0.3.3", changelog=None)
    add_changeset(repo2, "first", "minor", "first entry ever.")
    res2 = release(repo2)
    log2 = read(repo2, "CHANGELOG.md")
    check("changelog created when missing", res2.returncode == 0 and log2.startswith("# Changelog\n\n## 0.4.0 ("), log2[:40])


def test_resume_after_partial_failure(root):
    # release commit landed but the tag was never created: the next run tags, does not re-cut
    repo = make_repo(root, "resume", "0.3.3")
    add_changeset(repo, "H-5", "patch", "a fix.")
    res = release(repo)
    git(repo, "tag", "-d", "v0.3.4")
    res2 = release(repo)
    check("resume: release commit without tag -> exit 0 and tag restored",
          res.returncode == 0 and res2.returncode == 0 and "v0.3.4" in tags(repo), res2.stderr.strip())
    check("resume: version still 0.3.4, no second release", plugin_version(repo) == "0.3.4"
          and git(repo, "log", "--format=%s").count("release:") == 1)


# ------------------------------------------------------------------ --publish + origin

def make_origin(root, name, version="0.3.3"):
    """A working clone `name` with a bare `name-origin.git` as origin; main + tags pushed."""
    repo = make_repo(root, name, version)
    bare = os.path.join(root, name + "-origin.git")
    git(root, "init", "-q", "--bare", "-b", "main", bare)
    git(repo, "remote", "add", "origin", bare)
    git(repo, "push", "-q", "origin", "main", "--tags")
    return repo, bare


def clone(root, bare, name):
    path = os.path.join(root, name)
    git(root, "clone", "-q", bare, path)
    return path


def origin_tags(bare):
    return sorted(git(bare, "tag").split())


def origin_head(bare):
    return git(bare, "rev-parse", "main").strip()


def tag_target(repo, tag):
    """Commit a (possibly annotated) tag points at."""
    return git(repo, "rev-list", "-n", "1", tag).strip()


def test_publish_happy_path(root):
    repo, bare = make_origin(root, "pub-happy")
    add_changeset(repo, "H-7", "patch", "a pushed fix.")
    git(repo, "push", "-q", "origin", "main")
    res = release(repo, "--publish")
    check("publish: happy path exits 0", res.returncode == 0, res.stderr.strip())
    head = git(repo, "rev-parse", "HEAD").strip()
    check("publish: origin/main is the release commit",
          origin_head(bare) == head and git(bare, "log", "-1", "--format=%s", "main").strip() == "release: v0.3.4")
    check("publish: tag v0.3.4 on origin", "v0.3.4" in origin_tags(bare), origin_tags(bare))
    check("publish: origin tag points at origin/main", tag_target(bare, "v0.3.4") == origin_head(bare))
    out = res.stdout
    check("publish: main pushed BEFORE the tag was created",
          0 < out.find("pushed main") < out.find("tagged v0.3.4") < out.find("pushed tag v0.3.4"), out)
    check("publish: sync step printed both shas", "syncing to the current tip of main" in out and "origin/main is" in out, out)
    check("publish: GH_TOKEN absent -> gh step skipped with notice", "GH_TOKEN not set" in out, out)
    check("publish: changeset consumed on origin",
          run(["git", "cat-file", "-e", "main:.changeset/H-7.md"], bare).returncode != 0)
    # idempotent second publish run: nothing to do, no new tag
    res2 = release(repo, "--publish")
    check("publish: second run is a no-op", res2.returncode == 0 and "nothing to do" in res2.stdout
          and origin_tags(bare) == ["v0.3.3", "v0.3.4"], (res2.stdout, origin_tags(bare)))


def test_publish_stale_checkout(root):
    # the job's checkout is behind origin (the triggering sha): it must release the tip
    repo, bare = make_origin(root, "pub-stale")
    other = clone(root, bare, "pub-stale-other")
    add_changeset(other, "H-8-late", "minor", "landed after the trigger.")
    git(other, "push", "-q", "origin", "HEAD:main")
    stale = git(repo, "rev-parse", "HEAD").strip()
    res = release(repo, "--publish")
    check("publish: stale checkout is discarded, tip of main is released",
          res.returncode == 0 and plugin_version(repo) == "0.4.0" and stale != origin_head(bare)
          and "H-8-late.md" in read(repo, "CHANGELOG.md"), (res.returncode, res.stderr.strip(), plugin_version(repo)))
    check("publish: sync message names the stale local sha", stale[:12] in res.stdout, res.stdout)


def test_publish_race(root):
    repo, bare = make_origin(root, "pub-race")
    add_changeset(repo, "H-9-fix", "patch", "the first fix.")
    git(repo, "push", "-q", "origin", "main")
    # a second PR merges to origin while release.py has already computed the patch release
    other = clone(root, bare, "pub-race-other")
    add_changeset(other, "H-10-feature", "minor", "the feature that landed mid-run.")
    inject = "git -C %s push -q origin HEAD:main" % other
    res = release(repo, "--publish", "--test-inject-commit", inject)
    check("race: exits 0", res.returncode == 0, res.stderr.strip())
    out = res.stdout
    check("race: first push rejected, retried", "rejected as non-fast-forward on attempt 1/3" in out, out)
    check("race: inject fired exactly once", out.count("running --test-inject-commit") == 1, out)
    subjects = git(bare, "log", "--format=%s", "main")
    check("race: exactly ONE release commit on origin, version 0.4.0 (higher bump wins)",
          subjects.count("release: v0.4.0") == 1 and "release: v0.3.4" not in subjects
          and plugin_version(repo) == "0.4.0", subjects)
    check("race: exactly one new tag, v0.4.0, no v0.3.4 anywhere",
          origin_tags(bare) == ["v0.3.3", "v0.4.0"] and sorted(git(repo, "tag").split()) == ["v0.3.3", "v0.4.0"],
          (origin_tags(bare), git(repo, "tag").split()))
    check("race: tag points at the pushed commit", tag_target(bare, "v0.4.0") == origin_head(bare)
          == git(repo, "rev-parse", "HEAD").strip())
    log = read(repo, "CHANGELOG.md")
    sec = log[log.find("## 0.4.0 ("):log.find("## 0.3.3")]
    check("race: both changesets batched into the one 0.4.0 section",
          "(H-9-fix.md)" in sec and "(H-10-feature.md)" in sec and "### Added" in sec and "### Fixed" in sec, sec)
    check("race: both changesets consumed", changesets(repo) == [], changesets(repo))
    check("race: no 0.3.4 section in CHANGELOG", "## 0.3.4" not in log)
    check("race: origin/main has exactly one release commit total", subjects.count("release:") == 1, subjects)


def test_publish_failed_push_no_tag(root):
    repo, bare = make_origin(root, "pub-fail")
    add_changeset(repo, "H-11", "patch", "will not land.")
    git(repo, "push", "-q", "origin", "main")
    hook = os.path.join(bare, "hooks", "pre-receive")
    write(bare, "hooks/pre-receive", "#!/bin/sh\necho 'origin closed' >&2\nexit 1\n")
    os.chmod(hook, 0o755)
    before = origin_head(bare)
    res = release(repo, "--publish")
    check("failed push: exit 3", res.returncode == 3, (res.returncode, res.stderr.strip()))
    check("failed push: no tag created locally", sorted(git(repo, "tag").split()) == ["v0.3.3"], git(repo, "tag").split())
    check("failed push: no tag on origin, origin/main unchanged",
          origin_tags(bare) == ["v0.3.3"] and origin_head(bare) == before, origin_tags(bare))
    check("failed push: error names the push", "push origin HEAD:main failed" in res.stderr, res.stderr)
    check("failed push: stdout never says tagged", "tagged" not in res.stdout, res.stdout)


def test_publish_resume_missing_tag(root):
    # release commit already on origin, tag missing (a previous run died after the push)
    repo, bare = make_origin(root, "pub-resume")
    add_changeset(repo, "H-12", "patch", "resume me.")
    git(repo, "push", "-q", "origin", "main")
    res = release(repo, "--publish")
    git(repo, "tag", "-d", "v0.3.4")
    git(bare, "tag", "-d", "v0.3.4")
    res2 = release(repo, "--publish")
    check("publish resume: exit 0 and tag restored on origin",
          res.returncode == 0 and res2.returncode == 0 and origin_tags(bare) == ["v0.3.3", "v0.3.4"],
          (res2.returncode, res2.stderr.strip(), origin_tags(bare)))
    check("publish resume: no second release commit",
          git(bare, "log", "--format=%s", "main").count("release:") == 1)
    check("publish resume: says resuming", "resuming" in res2.stdout, res2.stdout)


# ------------------------------------------------------------------ changeset-check.py

def pr(repo, name, mutate):
    """Create branch <name> off main, apply mutate(repo), commit, return (base, head)."""
    git(repo, "checkout", "-q", "-b", name, "main")
    mutate(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "pr " + name)
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    return "main", head


def guard(repo, base, head):
    return run([sys.executable, CHECK, base, head], repo)


def test_guard(root):
    repo = make_repo(root, "guard", "0.3.3")
    write(repo, "docs.md", "# docs\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs")

    base, head = pr(repo, "no-changeset", lambda r: write(r, "docs.md", "# docs\nmore\n"))
    res = guard(repo, base, head)
    check("guard: missing changeset -> 1", res.returncode == 1, res.returncode)
    check("guard: missing changeset reason is plain English", "no changeset was added" in res.stdout, res.stdout)

    base, head = pr(repo, "bad-frontmatter",
                    lambda r: write(r, ".changeset/bad.md", "bump: patch\n\nno dashes\n"))
    res = guard(repo, base, head)
    check("guard: bad frontmatter -> 1", res.returncode == 1 and "must start with a '---'" in res.stdout, res.stdout)

    base, head = pr(repo, "unknown-bump",
                    lambda r: write(r, ".changeset/odd.md", "---\nbump: huge\n---\nbody\n"))
    res = guard(repo, base, head)
    check("guard: unknown bump -> 1", res.returncode == 1 and "bump must be one of" in res.stdout, res.stdout)

    base, head = pr(repo, "extra-key",
                    lambda r: write(r, ".changeset/two.md", "---\nbump: patch\ntitle: x\n---\nbody\n"))
    res = guard(repo, base, head)
    check("guard: extra frontmatter key -> 1", res.returncode == 1 and "exactly one key" in res.stdout, res.stdout)

    base, head = pr(repo, "empty-body",
                    lambda r: write(r, ".changeset/empty.md", "---\nbump: minor\n---\n"))
    res = guard(repo, base, head)
    check("guard: minor without body -> 1", res.returncode == 1 and "needs a body" in res.stdout, res.stdout)

    def version_edit(r):
        add_changeset(r, "ok", "patch", "fine.", commit=False)
        write(r, ".claude-plugin/plugin.json", PLUGIN_JSON_TEXT % "0.3.4")
    base, head = pr(repo, "version-edit", version_edit)
    res = guard(repo, base, head)
    check("guard: version edit -> 1", res.returncode == 1 and "CI is the only writer of the version" in res.stdout, res.stdout)

    def other_plugin_edit(r):
        add_changeset(r, "ok", "patch", "fine.", commit=False)
        write(r, ".claude-plugin/plugin.json", (PLUGIN_JSON_TEXT % "0.3.3").replace("fixture plugin", "fixture Plugin"))
    base, head = pr(repo, "plugin-other-field", other_plugin_edit)
    res = guard(repo, base, head)
    check("guard: plugin.json edit that leaves the version alone -> 0", res.returncode == 0, res.stdout)

    def changelog_edit(r):
        add_changeset(r, "ok", "patch", "fine.", commit=False)
        write(r, "CHANGELOG.md", EXISTING_CHANGELOG + "\nhand edit\n")
    base, head = pr(repo, "changelog-edit", changelog_edit)
    res = guard(repo, base, head)
    check("guard: CHANGELOG edit -> 1", res.returncode == 1 and "CI is the only writer of the changelog" in res.stdout, res.stdout)

    # the one-shot bootstrap PR may CREATE CHANGELOG.md (diff-filter A); editing it stays refused
    fresh = make_repo(root, "guard-bootstrap", "0.3.3", changelog=None)
    def bootstrap(r):
        add_changeset(r, "H-7-bootstrap", "minor", "adopt the release flow.", commit=False)
        write(r, "CHANGELOG.md", "# Changelog\n\nmoved from README.\n")
    base, head = pr(fresh, "bootstrap", bootstrap)
    res = guard(fresh, base, head)
    check("guard: CHANGELOG.md ADDED by the PR -> 0", res.returncode == 0, res.stdout)
    check("guard: reports the add as allowed once", "added (allowed once" in res.stdout, res.stdout)

    base, head = pr(repo, "valid", lambda r: add_changeset(r, "H-6-valid", "patch", "a valid fix.", commit=False))
    res = guard(repo, base, head)
    check("guard: valid changeset -> 0", res.returncode == 0, res.stdout)
    check("guard: prints what it checked", all(s in res.stdout for s in
          ("checked: added changeset files", "version line -> unchanged", "CHANGELOG.md -> untouched")), res.stdout)

    base, head = pr(repo, "none-no-body", lambda r: add_changeset(r, "typo", "none", commit=False))
    res = guard(repo, base, head)
    check("guard: bump none without body -> 0", res.returncode == 0, res.stdout)

    def modified_not_added(r):
        write(r, ".changeset/README.md", "# Changesets\n\nedited convention text\n")
    base, head = pr(repo, "readme-only", modified_not_added)
    res = guard(repo, base, head)
    check("guard: editing .changeset/README.md does not count as a changeset -> 1", res.returncode == 1, res.stdout)

    res = run([sys.executable, CHECK], repo)
    check("guard: usage error without refs or env -> 2", res.returncode == 2, res.returncode)


def main():
    for path in (RELEASE, CHECK):
        if not os.path.isfile(path):
            print("FAIL missing script: " + path)
            print("RESULT: FAIL (0/1)")
            return 1
    root = tempfile.mkdtemp(prefix="selftest-release-")
    try:
        test_bumps(root)
        test_mixed(root)
        test_none_only(root)
        test_noop_and_idempotence(root)
        test_drift(root)
        test_formatting_and_changelog(root)
        test_resume_after_partial_failure(root)
        test_publish_happy_path(root)
        test_publish_stale_checkout(root)
        test_publish_race(root)
        test_publish_failed_push_no_tag(root)
        test_publish_resume_missing_tag(root)
        test_guard(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    ok = all(RESULTS)
    print("RESULT: %s (%d/%d)" % ("PASS" if ok else "FAIL", sum(RESULTS), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
