#!/usr/bin/env python3
"""changeset-check.py -- pull-request guard for the changeset release flow.

Given a base ref and a head ref (or GITHUB_BASE_REF / GITHUB_SHA in Actions), exits 1
with plain-English reasons when any of these hold, exit 0 otherwise:

  - the PR adds no file under .changeset/ (git diff --diff-filter=A base...head)
  - an added .changeset/*.md has missing or invalid frontmatter, more than the one
    `bump:` key, an unknown bump value, or no body with a bump other than none
  - the PR diff changes the "version" line of .claude-plugin/plugin.json
  - the PR touches CHANGELOG.md

Only scripts/release.py, run by CI on main, writes the version and the changelog.
The script prints what it checked so a failing run is self-explaining.

Usage: python3 scripts/changeset-check.py [BASE HEAD]
       (in Actions: BASE defaults to origin/$GITHUB_BASE_REF, HEAD to $GITHUB_SHA)
Exit 0 pass, 1 guard failure, 2 usage or git error. Python 3.9 compatible.
"""
import os
import re
import subprocess
import sys

BUMPS = ("none", "patch", "minor", "major")
CHANGESET_DIR = ".changeset/"
PLUGIN_JSON = ".claude-plugin/plugin.json"
CHANGELOG = "CHANGELOG.md"
VERSION_LINE_RE = re.compile(r'^[+-]\s*"version"\s*:')


def git(*args):
    res = subprocess.run(["git"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        sys.stderr.write("changeset-check: git %s failed (exit %d):\n%s\n"
                         % (" ".join(args), res.returncode, res.stderr.strip()))
        sys.exit(2)
    return res.stdout


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


def main(argv):
    if len(argv) == 3:
        base, head = argv[1], argv[2]
    elif len(argv) == 1 and os.environ.get("GITHUB_BASE_REF") and os.environ.get("GITHUB_SHA"):
        base = "origin/" + os.environ["GITHUB_BASE_REF"]
        head = os.environ["GITHUB_SHA"]
    else:
        sys.stderr.write("usage: changeset-check.py BASE HEAD  (or set GITHUB_BASE_REF and GITHUB_SHA)\n")
        return 2
    rng = "%s...%s" % (base, head)
    problems = []

    print("changeset-check: comparing %s" % rng)
    added = [p for p in git("diff", "--diff-filter=A", "--name-only", rng).splitlines()
             if p.startswith(CHANGESET_DIR) and p.endswith(".md") and p != CHANGESET_DIR + "README.md"]
    print("checked: added changeset files -> %s" % (", ".join(added) if added else "none"))
    if not added:
        problems.append("no changeset was added. Every pull request adds one file "
                        ".changeset/<slug>.md with frontmatter `bump: patch|minor|major|none` and a "
                        "one-paragraph body (see .changeset/README.md). Use `bump: none` when the "
                        "change has no user-visible effect.")
    for path in added:
        text = git("show", "%s:%s" % (head, path))
        try:
            bump, _ = parse_changeset(text, path)
            print("checked: %s -> bump: %s" % (path, bump))
        except ValueError as exc:
            problems.append(str(exc))

    plugin_diff = git("diff", rng, "--", PLUGIN_JSON)
    version_hunks = [l for l in plugin_diff.splitlines() if VERSION_LINE_RE.match(l)]
    print("checked: %s version line -> %s" % (PLUGIN_JSON, "changed" if version_hunks else "unchanged"))
    if version_hunks:
        problems.append("the pull request changes the \"version\" in %s (%s). CI is the only writer "
                        "of the version; drop that edit and let the changeset's bump decide it."
                        % (PLUGIN_JSON, "; ".join(l.strip() for l in version_hunks)))

    touched = git("diff", "--name-only", rng, "--", CHANGELOG).strip()
    added = git("diff", "--name-only", "--diff-filter=A", rng, "--", CHANGELOG).strip()
    state = "added (allowed once: the bootstrap that creates it)" if added else (
        "modified" if touched else "untouched")
    print("checked: %s -> %s" % (CHANGELOG, state))
    if touched and not added:
        problems.append("the pull request edits %s. CI is the only writer of the changelog; put the "
                        "entry text in the changeset body instead." % CHANGELOG)

    if problems:
        print("changeset-check: FAIL, %d problem(s):" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("changeset-check: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
