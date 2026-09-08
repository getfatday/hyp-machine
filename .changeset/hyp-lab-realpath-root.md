---
bump: patch
---
`scripts/hyp-lab.py` resolves the plugin root through the real path of the file rather than its invoked path, so a repository that links the script into a mirrored plugin tree (the source lab links every shared script into its deploy tree, one source per file) imports `hooks/scripts/hyp_config.py` beside the real file instead of raising ModuleNotFoundError; invoked from the plugin tree itself nothing changes. Found landing lab H-DRAFT-e9c49cbd-consumer-lab-entrypoint-v2 (v0.17.0).
