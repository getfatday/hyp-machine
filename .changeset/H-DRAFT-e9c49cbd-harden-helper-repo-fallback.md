---
bump: patch
---
`scripts/harden-check.sh` resolves every helper it calls through one resolver: the plugin root first, else the repository's own `scripts/<helper>`, else the block is skipped as before. A repository that keeps helpers the plugin does not ship (the lab's twelve: amendment-detector, check-governance-drift, check-submission-connectivity, claim-lint, coincidence-check, commitment-lint, corpus-lint, dashboard-features.json, journal-freeze.sha, plugin-parity-check, repo-coverage-lint, wave-status) gets their advisory lines back from the plugin-rooted SessionStart fire — 12 of 26 lines had gone dark there — while a consumer without such helpers prints byte-identical output. Lab H-DRAFT-e9c49cbd-harden-helper-repo-fallback (consumer gap G12), kept twice at 5/5.
