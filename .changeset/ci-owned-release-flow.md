---
bump: minor
---

CI now owns the version and the changelog. A pull request adds a `.changeset/<slug>.md` file (a `bump:` line and a paragraph like this one) and never edits the version in `.claude-plugin/plugin.json` or `CHANGELOG.md`; the required `changeset-check` status fails PRs that break the rule. On every merge to main the release job computes the next version from the highest reachable `v*` tag, writes it into plugin.json, prepends a CHANGELOG.md section, deletes the consumed changesets, tags `v<version>`, and publishes the GitHub release. The README changelog moved to CHANGELOG.md. Contract: `.changeset/README.md`; selftest: `python3 scripts/selftest-release.py`. Lab evidence: H-DRAFT-bfb3323b (cause-n-effect), research/release-automation-prior-art.md.
