# CLAUDE.md

Guidance for Claude Code sessions working in this repository (the Hyp Machine plugin).

## Shipping a change

Every pull request that changes what the plugin ships adds one changeset file and nothing else
about versions:

1. Add `.changeset/<slug>.md` (slug: an issue or hypothesis id plus a topic, unique to your branch)
   with this shape:

   ```
   ---
   bump: patch
   ---

   One paragraph that becomes the changelog entry: what changed, why, and the evidence.
   ```

   `bump` is `patch`, `minor`, `major`, or `none` (`none` for changes with no user-visible
   effect, such as CI or comments). Below 1.0, `major` bumps the minor number.
2. Never edit the `version` in `.claude-plugin/plugin.json`, never edit `CHANGELOG.md`, never
   create a tag or a GitHub release. CI is the only writer of all three: on every merge to main
   the release job computes the next version from the highest reachable `v*` tag, writes
   plugin.json and CHANGELOG.md, deletes the consumed changesets, tags, and publishes.
3. The pull request check `changeset-check` fails a PR that lacks a changeset, edits the version
   line, or edits CHANGELOG.md. Its output says what to fix.

Full contract: `.changeset/README.md`. Regression test: `python3 scripts/selftest-release.py`.

## Repository conventions

- Python scripts are standard-library only and run under Python 3.9.
- Shipped regression tests live in `scripts/selftest-*.py` and print one PASS/FAIL line per check.
- No personal names in artifact prose; attribution is git.
