#!/usr/bin/env bash
# repo-settings.sh -- apply this repository's GitHub settings as code (idempotent).
#
# What it sets, and why (maintainer ruling 2026-09-07; CLAUDE.md "Shipping a change"):
#   * allow_auto_merge = true            -- owner PRs merge themselves once checks are green
#   * delete_branch_on_merge = true      -- merged feature branches are removed
#   * ruleset "main: required checks"    -- .github/rulesets/main-required-checks.json: main
#     requires the `changeset-check` status. Bypass: the GitHub Actions app (id 15368), so the
#     release job's version commit still lands, and repository admins. Auto-merge is only
#     offered by GitHub when a rule like this exists.
#
# Usage (repository owner, from any checkout of this repo):
#   GH_TOKEN=$(gh auth token --user getfatday) bash scripts/repo-settings.sh [owner/repo]
#
# Re-running is safe: the ruleset is created once and updated in place afterwards.
set -euo pipefail
repo="${1:-getfatday/hyp-machine}"
here="$(cd "$(dirname "$0")/.." && pwd)"
spec="$here/.github/rulesets/main-required-checks.json"
[ -f "$spec" ] || { echo "missing $spec" >&2; exit 2; }

echo "== repository flags"
gh api -X PATCH "repos/$repo" -F allow_auto_merge=true -F delete_branch_on_merge=true \
  --jq '"allow_auto_merge=\(.allow_auto_merge) delete_branch_on_merge=\(.delete_branch_on_merge)"'

echo "== ruleset"
name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$spec")"
id="$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" | head -1)"
if [ -z "$id" ]; then
  gh api -X POST "repos/$repo/rulesets" --input "$spec" --jq '"created id=\(.id) enforcement=\(.enforcement)"'
else
  gh api -X PUT "repos/$repo/rulesets/$id" --input "$spec" --jq '"updated id=\(.id) enforcement=\(.enforcement)"'
fi

echo "== verify"
gh api "repos/$repo" --jq '"allow_auto_merge=\(.allow_auto_merge)"'
gh api "repos/$repo/rulesets" --jq '.[] | "ruleset \(.id) \(.name) \(.enforcement)"'
