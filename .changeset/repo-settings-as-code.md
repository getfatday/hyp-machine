---
bump: none
---
Repository settings as code: `.github/rulesets/main-required-checks.json` (main requires `changeset-check`, bypass for the Actions app and admins) and `scripts/repo-settings.sh`, an idempotent apply script that also turns on auto-merge and branch deletion on merge. Ruling 2026-09-07; no shipped bytes change.
