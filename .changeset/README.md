# Changesets

CI is the only writer of the version and the changelog. A pull request never edits the
`"version"` in `.claude-plugin/plugin.json` and never edits `CHANGELOG.md`; the PR check
(`scripts/changeset-check.py`) fails when it does.

## The rule

Every pull request adds at least one file `.changeset/<slug>.md`. The slug is free-form
and unique on your branch by convention, for example `<issue-or-hypothesis-id>-<topic>`
(`issue-12-stop-driver-timeout.md`, `H-291-async-hooks.md`). The file is YAML
frontmatter with exactly one key, `bump`, followed by a markdown body of one paragraph
that becomes the changelog entry verbatim. Cite the PR, issue, or lab evidence in the
body if you have it.

```markdown
---
bump: patch
---
The Stop driver no longer grades a failed dispatch read as an allow; the first failure
blocks the stop once with a visible retry reason, the second allows under the typed reason
`dispatch-error-open`. Fixes #8 (lab H-280, kept twice at 5/5).
```

`bump` is one of:

| bump    | meaning                                   | changelog heading |
|---------|-------------------------------------------|-------------------|
| `major` | a breaking change for consumers           | `### Breaking`    |
| `minor` | a new capability, backwards compatible    | `### Added`       |
| `patch` | a fix or hardening, no new surface        | `### Fixed`       |
| `none`  | no user-visible effect (docs typo, CI)    | not listed        |

## Opt-out

`bump: none` is the tree-visible opt-out. It satisfies the PR check, is deleted by the
release job without cutting a release, and needs no body. No labels are used anywhere in
this flow; the tree is the only signal.

## Pre-1.0 rule

While the current version is `0.y.z`, a `major` changeset bumps the minor (0.3.3 with a
`major` pending becomes 0.4.0). From 1.0.0 on, `major` bumps the major (1.2.3 becomes
2.0.0).

## What happens on merge

On every push to main the release job (`.github/workflows/release.yml`, one run at a
time) runs `scripts/release.py`:

1. baseline = highest `v*` tag reachable from HEAD, cross-checked against
   `plugin.json`; a mismatch stops the job with an explanation
2. no pending changesets: nothing happens
3. only `bump: none` files: they are deleted in a `chore: consume no-op changesets` commit
4. otherwise the highest bump present decides the next version; the job writes it into
   `plugin.json`, prepends a `## <version> (<date>)` section to `CHANGELOG.md` with the
   bodies grouped by heading (each followed by its changeset filename), deletes the
   consumed files, commits `release: v<version>`, pushes, creates the annotated tag and
   the GitHub release

The job always works on the current tip of main, and changesets merged while a release
run is in flight are batched into that release: a push rejected as non-fast-forward makes
the job discard its local release commit, re-read main, and recompute (the tag is only
ever created after main was pushed). Re-running on a tree with nothing pending is a
no-op, and a tag is never created twice.
