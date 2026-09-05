#!/usr/bin/env python3
"""bootstrap-changelog.py -- one-shot: move the README's "## Changelog" section into CHANGELOG.md.

Run once when adopting the changeset release flow. It:

  - takes everything from the "## Changelog" heading in README.md to the next "## " heading
    (or end of file)
  - writes CHANGELOG.md, newest first, keeping the existing order: "### x.y.z ..." headings
    become "## x.y.z ..." (the level release.py prepends at); the older top-level
    "- x.y.z ..." bullet entries become "## x.y.z" headings with the bullet text as the
    paragraph beneath, and the crux-era bullets that follow the hyp 0.2.0 entry are marked
    "(crux)" so the non-monotonic versions read correctly
  - replaces the README section with a one-line pointer to CHANGELOG.md

Refuses to run when CHANGELOG.md already exists or README.md has no "## Changelog" heading.

Usage: python3 scripts/bootstrap-changelog.py [--repo PATH]
Exit 0 done, 2 refused. Python 3.9 compatible.
"""
import argparse
import os
import re
import sys

BULLET_RE = re.compile(r"^- (\d+\.\d+\.\d+)(.*)$")
HEADER = ("# Changelog\n\n"
          "Newest first. This file is written by `scripts/release.py` from the pending\n"
          "`.changeset/*.md` files on every push to main; do not edit it by hand (see\n"
          "`.changeset/README.md`).\n\n")
POINTER = "## Changelog\n\nSee [CHANGELOG.md](CHANGELOG.md).\n"


def die(msg):
    sys.stderr.write("bootstrap-changelog: " + msg + "\n")
    sys.exit(2)


def split_readme(text):
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "## Changelog":
            start = i
            break
    if start is None:
        die("README.md has no '## Changelog' heading; nothing to move")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "".join(lines[:start]), lines[start + 1:end], "".join(lines[end:])


def close_aside(text, dash):
    """Turn the first ') — x' into '. X' so a parenthetical aside ends as a sentence."""
    return re.sub(r"\)" + re.escape(dash) + r"(\w)", lambda m: ". " + m.group(1).upper(), text, count=1)


def convert(section_lines):
    out = []
    seen_hyp_bullet = False
    in_bullet = False
    fix_paren = False
    dash = " — "  # the README's existing dash separator
    for raw in section_lines:
        line = raw.rstrip("\n")
        if line.startswith("### "):
            in_bullet = False
            out.append("## " + line[4:])
            continue
        m = BULLET_RE.match(line)
        if m:
            in_bullet = True
            version, rest = m.group(1), m.group(2)
            heading = version
            if rest.startswith(" (hyp"):
                seen_hyp_bullet = True
                heading += " (hyp)"
                if rest.startswith(" (hyp)"):
                    rest = rest[len(" (hyp)"):]
                elif rest.startswith(" (hyp" + dash):
                    # "(hyp — aside) — body" becomes "Aside. Body"; the closing
                    # paren may sit on a continuation line, so fix_paren carries over
                    rest = dash + rest[len(" (hyp" + dash):]
                    if ")" + dash in rest:
                        rest = close_aside(rest, dash)
                    else:
                        fix_paren = True
            elif seen_hyp_bullet:
                heading += " (crux)"
            if rest.startswith(dash):
                rest = rest[len(dash):]
            body = rest.strip()
            if body:
                body = body[0].upper() + body[1:]
            if out and out[-1] != "":
                out.append("")
            out.append("## " + heading)
            out.append("")
            out.append(body)
            continue
        if in_bullet and line.startswith("  "):
            cont = line[2:]
            if fix_paren and ")" + dash in cont:
                cont = close_aside(cont, dash)
                fix_paren = False
            out.append(cont)
            continue
        if in_bullet and line.strip() == "":
            in_bullet = False
        out.append(line)
    text = "\n".join(out).strip("\n") + "\n"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    readme = os.path.join(repo, "README.md")
    changelog = os.path.join(repo, "CHANGELOG.md")
    if os.path.exists(changelog):
        die("CHANGELOG.md already exists; this script runs once")
    if not os.path.isfile(readme):
        die("README.md not found under " + repo)
    with open(readme, encoding="utf-8") as fh:
        text = fh.read()
    before, section, after = split_readme(text)
    body = convert(section)
    with open(changelog, "w", encoding="utf-8") as fh:
        fh.write(HEADER + body)
    new_readme = before + POINTER + ("\n" + after if after else "")
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write(new_readme)
    n_entries = sum(1 for l in body.splitlines() if l.startswith("## "))
    print("bootstrap-changelog: moved %d entries (%d lines) into CHANGELOG.md; README.md now points to it"
          % (n_entries, len(section)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
