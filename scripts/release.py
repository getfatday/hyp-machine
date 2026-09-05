#!/usr/bin/env python3
"""release.py -- the only writer of the plugin version and the changelog.

Runs on every push to main (see .github/workflows/release.yml) and turns pending
.changeset/*.md files into one release:

  1. baseline  = highest v<semver> tag reachable from HEAD, cross-checked against the
                 "version" in .claude-plugin/plugin.json (a mismatch means someone edited
                 the version by hand, or a tag is missing: exit 2 with an explanation)
  2. pending   = every .changeset/*.md except README.md
                 none pending           -> exit 0, nothing to do
                 all `bump: none`       -> delete them, commit "chore: consume no-op changesets"
                 otherwise              -> next = baseline bumped by the highest bump present
                                           (pre-1.0: `major` bumps the MINOR while baseline is 0.y.z)
  3. write the new version into plugin.json (only the version value changes), prepend a
     "## <version> (<UTC date>)" section to CHANGELOG.md grouped Breaking / Added / Fixed,
     delete the consumed files, commit "release: v<version>", tag v<version> (annotated)
  4. with --publish: the job works on the CURRENT tip of main, not the sha that
     triggered it. It first runs `git fetch origin main` + `git reset --hard origin/main`
     (both shas are printed), then computes, commits the release, and pushes
     `HEAD:main`. Only after that push succeeded does it create the annotated tag, push
     the tag, and run `gh release create --verify-tag` with the new section as notes
     (skipped with a notice when no GH_TOKEN / GITHUB_TOKEN is set). A tag is never
     created before the main push succeeded.

     Push race: when `git push origin HEAD:main` is rejected as non-fast-forward
     (another changeset landed while this run was computing), the local release commit
     is discarded (fresh fetch, reset --hard origin/main) and the whole computation is
     retried, at most 3 attempts in total, so changesets merged during the run are
     batched into the one release with the higher bump. Three consecutive rejections
     exit 3 with nothing tagged.

Without --publish (local / selftest mode) nothing talks to a remote: the release is
committed and then tagged, in that order.

Idempotent: a tree with no pending files is a no-op; the tag is never created twice
(an existing v<next> tag aborts before anything is written); a release commit whose tag
or publish step failed is resumed on the next run instead of being re-cut (in publish
mode the release commit is already origin/main, so only the tag and the GitHub release
are created).

Exit codes: 0 ok / no-op, 1 changeset parse failure, 2 version or tag drift, 3 git or gh
command failure (including a main push still rejected after 3 attempts).

Usage: python3 scripts/release.py [--repo PATH] [--publish] [--test-inject-commit CMD]
  --test-inject-commit CMD  selftest only: shell command run once, in the repo, after the
                            release commit exists and before the first `git push`; the
                            selftest uses it to land a commit on origin mid-run and prove
                            the race is batched.
Python 3.9 compatible.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile

BUMPS = ("none", "patch", "minor", "major")
BUMP_RANK = {"none": 0, "patch": 1, "minor": 2, "major": 3}
SECTION_FOR = {"major": "### Breaking", "minor": "### Added", "patch": "### Fixed"}
SECTION_ORDER = ("major", "minor", "patch")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION_LINE_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')
CHANGESET_DIR = ".changeset"
PLUGIN_JSON = os.path.join(".claude-plugin", "plugin.json")
CHANGELOG = "CHANGELOG.md"


def die(code, msg):
    sys.stderr.write("release: " + msg.rstrip() + "\n")
    sys.exit(code)


def git(repo, *args, check=True, capture=True):
    cmd = ["git", "-C", repo] + list(args)
    res = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.PIPE if capture else None, text=True)
    if check and res.returncode != 0:
        die(3, "git %s failed (exit %d):\n%s" % (" ".join(args), res.returncode,
                                               (res.stderr or "").strip()))
    return res


def parse_changeset(text, name):
    """Return (bump, body) or raise ValueError with a plain-English reason."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("%s: the file must start with a '---' frontmatter line" % name)
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("%s: the frontmatter has no closing '---' line" % name)
    keys = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if ":" not in raw:
            raise ValueError("%s: frontmatter line %r is not 'key: value'" % (name, raw))
        key, value = raw.split(":", 1)
        keys[key.strip()] = value.strip().strip("'\"")
    if set(keys) != {"bump"}:
        raise ValueError("%s: frontmatter must contain exactly one key, 'bump' (found: %s)"
                         % (name, ", ".join(sorted(keys)) or "nothing"))
    bump = keys["bump"]
    if bump not in BUMPS:
        raise ValueError("%s: bump must be one of patch, minor, major, none (found %r)"
                         % (name, bump))
    body = "\n".join(lines[end + 1:]).strip()
    if bump != "none" and not body:
        raise ValueError("%s: a changeset with bump %s needs a body; it becomes the "
                         "changelog entry" % (name, bump))
    return bump, body


def semver_key(tag):
    m = TAG_RE.match(tag)
    return tuple(int(x) for x in m.groups())


def baseline_tag(repo):
    out = git(repo, "tag", "--merged", "HEAD").stdout.split()
    tags = [t for t in out if TAG_RE.match(t)]
    if not tags:
        return None
    return max(tags, key=semver_key)


def read_plugin_version(repo):
    path = os.path.join(repo, PLUGIN_JSON)
    if not os.path.isfile(path):
        die(2, "%s not found; run from the plugin repository root or pass --repo" % PLUGIN_JSON)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    matches = VERSION_LINE_RE.findall(text)
    if len(matches) != 1:
        die(2, "%s: expected exactly one \"version\" entry, found %d" % (PLUGIN_JSON, len(matches)))
    return text, matches[0][1]


def bump_version(version, bump):
    major, minor, patch = (int(x) for x in version.split("."))
    if bump == "major" and major == 0:
        bump = "minor"  # pre-1.0 house rule: breaking changes bump the minor
    if bump == "major":
        return "%d.0.0" % (major + 1)
    if bump == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if bump == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise ValueError("no version bump for %r" % bump)


def pending_changesets(repo):
    cdir = os.path.join(repo, CHANGESET_DIR)
    if not os.path.isdir(cdir):
        return []
    names = sorted(n for n in os.listdir(cdir)
                   if n.endswith(".md") and n != "README.md"
                   and os.path.isfile(os.path.join(cdir, n)))
    parsed = []
    errors = []
    for n in names:
        with open(os.path.join(cdir, n), encoding="utf-8") as fh:
            text = fh.read()
        try:
            bump, body = parse_changeset(text, os.path.join(CHANGESET_DIR, n))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        parsed.append((n, bump, body))
    if errors:
        die(1, "cannot release, %d changeset file(s) are invalid:\n  " % len(errors)
            + "\n  ".join(errors))
    return parsed


def render_section(version, date, entries):
    lines = ["## %s (%s)" % (version, date), ""]
    for bump in SECTION_ORDER:
        group = [(n, body) for (n, b, body) in entries if b == bump]
        if not group:
            continue
        lines.append(SECTION_FOR[bump])
        lines.append("")
        for n, body in group:
            body_lines = body.splitlines()
            body_lines[-1] = body_lines[-1] + " (%s)" % n
            lines.append("- " + body_lines[0])
            for extra in body_lines[1:]:
                lines.append("  " + extra)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def prepend_changelog(repo, section):
    path = os.path.join(repo, CHANGELOG)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
    else:
        existing = "# Changelog\n\n"
    lines = existing.splitlines(keepends=True)
    insert_at = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break
    if insert_at is None:
        head = existing.rstrip("\n") + "\n\n" if existing.strip() else existing
        new = head + section
    else:
        new = "".join(lines[:insert_at]) + section + "\n" + "".join(lines[insert_at:])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)


def write_plugin_version(repo, text, new_version):
    new_text = VERSION_LINE_RE.sub(lambda m: m.group(1) + new_version + m.group(3), text, count=1)
    with open(os.path.join(repo, PLUGIN_JSON), "w", encoding="utf-8") as fh:
        fh.write(new_text)


def tag_exists(repo, tag):
    return git(repo, "tag", "-l", tag).stdout.strip() == tag


def remote_has_tag(repo, tag):
    res = git(repo, "ls-remote", "--tags", "origin", "refs/tags/" + tag, check=False)
    return res.returncode == 0 and bool(res.stdout.strip())


def head_release_version(repo):
    """Version named by a 'release: vX.Y.Z' HEAD commit, else None."""
    subject = git(repo, "log", "-1", "--format=%s").stdout.strip()
    m = re.match(r"^release: v(\d+\.\d+\.\d+)$", subject)
    return m.group(1) if m else None


def changelog_section(repo, version):
    path = os.path.join(repo, CHANGELOG)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)
    out = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith("## %s " % version) or line.rstrip() == "## " + version
        if inside:
            out.append(line)
    return "".join(out).rstrip("\n") + "\n" if out else None




MAX_ATTEMPTS = 3
REJECTED_RE = re.compile(r"!\s+\[rejected\]")


class PushRejected(Exception):
    """origin refused HEAD:main as non-fast-forward; the tip moved under us."""


def push_main(repo):
    """Push HEAD to origin main. Raises PushRejected on a non-fast-forward rejection,
    dies (exit 3) on any other failure."""
    res = git(repo, "push", "origin", "HEAD:main", check=False)
    if res.returncode == 0:
        print("release: pushed main (%s)" % git(repo, "rev-parse", "--short", "HEAD").stdout.strip())
        sys.stdout.flush()
        return
    err = (res.stderr or "").strip()
    if REJECTED_RE.search(err) and "remote rejected" not in err:
        raise PushRejected(err)
    die(3, "git push origin HEAD:main failed (exit %d):\n%s" % (res.returncode, err))


def sync_to_origin_main(repo, why):
    """git fetch origin main + git reset --hard origin/main; prints both shas."""
    local = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "fetch", "-q", "origin", "main")
    remote = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    git(repo, "reset", "-q", "--hard", remote)
    print("release: %s: local HEAD was %s, origin/main is %s; working on origin/main"
          % (why, local[:12], remote[:12]))
    sys.stdout.flush()
    return local, remote


def create_tag(repo, version):
    tag = "v" + version
    git(repo, "tag", "-a", tag, "-m", "release: " + tag)
    print("release: tagged %s" % tag)


def publish_tag_and_release(repo, version, notes):
    """Push the (already created) tag and create the GitHub release. Idempotent."""
    tag = "v" + version
    if not remote_has_tag(repo, tag):
        git(repo, "push", "origin", "refs/tags/" + tag)
        print("release: pushed tag %s" % tag)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("release: GH_TOKEN not set, skipping gh release create for %s" % tag)
        return
    view = subprocess.run(["gh", "release", "view", tag], cwd=repo,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if view.returncode == 0:
        print("release: GitHub release %s already exists, nothing to do" % tag)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(notes)
        notes_path = fh.name
    try:
        res = subprocess.run(["gh", "release", "create", tag, "--verify-tag", "--title", tag,
                              "--notes-file", notes_path], cwd=repo, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    finally:
        os.unlink(notes_path)
    if res.returncode != 0:
        die(3, "gh release create %s failed (exit %d):\n%s" % (tag, res.returncode, res.stderr.strip()))
    print("release: created GitHub release %s" % tag)


class Injector(object):
    """--test-inject-commit: run the command once, right before the first push."""

    def __init__(self, cmd):
        self.cmd = cmd
        self.fired = False

    def fire(self, repo):
        if not self.cmd or self.fired:
            return
        self.fired = True
        print("release: [test] running --test-inject-commit before the push")
        sys.stdout.flush()
        res = subprocess.run(self.cmd, shell=True, cwd=repo, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            die(3, "--test-inject-commit command failed (exit %d):\n%s"
                % (res.returncode, (res.stderr or "").strip()))


def attempt_release(repo, publish_mode, injector):
    """One full computation on the current HEAD. Returns the exit code, or raises
    PushRejected when the main push lost a race (the caller resyncs and retries).
    In publish mode no tag exists until push_main() has returned."""
    plugin_text, plugin_version = read_plugin_version(repo)
    base_tag = baseline_tag(repo)
    if base_tag is None:
        die(2, "no v<major>.<minor>.<patch> tag is reachable from HEAD, so there is no baseline "
               "to bump from. Create the baseline tag once by hand (git tag -a v%s -m 'release: "
               "v%s' && git push origin v%s) and re-run." % ((plugin_version,) * 3))
    baseline = base_tag[1:]
    pending = pending_changesets(repo)
    resumed = head_release_version(repo)

    if baseline != plugin_version:
        if resumed == plugin_version and not tag_exists(repo, "v" + plugin_version) \
                and semver_key("v" + plugin_version) > semver_key(base_tag):
            # A release commit landed (in publish mode it is already origin/main) but its
            # tag never did: finish that release without re-cutting it.
            print("release: HEAD is 'release: v%s' without its tag; resuming" % plugin_version)
            create_tag(repo, plugin_version)
            if publish_mode:
                notes = changelog_section(repo, plugin_version) or ("release v" + plugin_version)
                publish_tag_and_release(repo, plugin_version, notes)
            return 0
        die(2, "version drift: %s says \"version\": \"%s\" but the highest release tag reachable "
               "from HEAD is %s. Only scripts/release.py writes the version; a pull request must "
               "not edit it. Fix: revert the hand edit to %s, or if a release tag is genuinely "
               "missing, push it (git tag -a v%s ... && git push origin v%s)."
            % (PLUGIN_JSON, plugin_version, base_tag, PLUGIN_JSON, plugin_version, plugin_version))

    if not pending:
        print("release: no pending changesets under %s/ (baseline %s); nothing to do"
              % (CHANGESET_DIR, base_tag))
        if publish_mode and resumed == baseline and not remote_has_tag(repo, base_tag):
            print("release: tag %s exists locally but not on origin; resuming publish" % base_tag)
            notes = changelog_section(repo, baseline) or ("release " + base_tag)
            publish_tag_and_release(repo, baseline, notes)
        return 0

    print("release: baseline %s, %d pending changeset(s): %s"
          % (base_tag, len(pending), ", ".join("%s=%s" % (n, b) for n, b, _ in pending)))
    highest = max((b for _, b, _ in pending), key=lambda b: BUMP_RANK[b])

    if highest == "none":
        for n, _, _ in pending:
            git(repo, "rm", "-q", os.path.join(CHANGESET_DIR, n))
        git(repo, "commit", "-q", "-m", "chore: consume no-op changesets")
        print("release: consumed %d no-op changeset(s), committed, no tag" % len(pending))
        if publish_mode:
            injector.fire(repo)
            push_main(repo)
        return 0

    next_version = bump_version(baseline, highest)
    next_tag = "v" + next_version
    if tag_exists(repo, next_tag):
        die(2, "tag %s already exists but is not reachable from HEAD (baseline is %s). Refusing "
               "to create a second %s; inspect the tag before re-running." % (next_tag, base_tag, next_tag))
    if publish_mode and remote_has_tag(repo, next_tag):
        die(2, "tag %s already exists on origin; fetch tags (git fetch --tags) and inspect before "
               "re-running." % next_tag)

    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    entries = [(n, b, body) for n, b, body in pending if b != "none"]
    section = render_section(next_version, date, entries)

    write_plugin_version(repo, plugin_text, next_version)
    prepend_changelog(repo, section)
    for n, _, _ in pending:
        git(repo, "rm", "-q", os.path.join(CHANGESET_DIR, n))
    git(repo, "add", PLUGIN_JSON, CHANGELOG)
    git(repo, "commit", "-q", "-m", "release: " + next_tag)
    print("release: committed release: %s (%s bump from %s)" % (next_tag, highest, base_tag))
    if publish_mode:
        injector.fire(repo)
        push_main(repo)  # raises PushRejected on a lost race: no tag has been created
    create_tag(repo, next_version)
    if publish_mode:
        publish_tag_and_release(repo, next_version, section)
    sys.stdout.write(section)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".", help="plugin repository root (default: .)")
    ap.add_argument("--publish", action="store_true",
                    help="work on the current origin/main, push main, then tag and create the "
                         "GitHub release")
    ap.add_argument("--test-inject-commit", metavar="CMD", default=None,
                    help="selftest only: shell command run once after the release commit and "
                         "before the first push (simulates a commit landing on origin mid-run)")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)

    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        die(2, "the working tree has uncommitted changes; release.py only runs on a clean tree")

    injector = Injector(args.test_inject_commit)
    if not args.publish:
        return attempt_release(repo, False, injector)

    sync_to_origin_main(repo, "publish: syncing to the current tip of main")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return attempt_release(repo, True, injector)
        except PushRejected as exc:
            print("release: push of main rejected as non-fast-forward on attempt %d/%d (the tip "
                  "moved while this run computed); discarding the local release commit"
                  % (attempt, MAX_ATTEMPTS))
            sys.stdout.flush()
            sync_to_origin_main(repo, "discarding the local release commit")
            if attempt == MAX_ATTEMPTS:
                die(3, "git push origin HEAD:main was rejected %d times in a row; nothing was "
                       "tagged. Re-run the job (or push to main again) to release the batch.\n%s"
                    % (MAX_ATTEMPTS, str(exc)))
            print("release: recomputing so the changesets that just landed join this release")
    return 3


if __name__ == "__main__":
    sys.exit(main())
