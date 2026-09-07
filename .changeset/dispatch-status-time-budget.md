---
bump: patch
---
`scripts/dispatch-status.py` reads every committed spec body in `git cat-file --batch` chunks of 64 under one wall ceiling, `DISPATCH_STATUS_MAX` seconds (environment, default 20), instead of one `git show` subprocess per spec: a 180-303 id corpus that read in 38-151 s on a loaded host reads in about one second, so the Stop dispatcher's 45 s inner read finishes. A chunk that times out is discarded whole and its ids are disclosed as UNREAD (open with kind `unread`, never landed, never actionable) through a `partial` object in `--json` or one `DISPATCH-PARTIAL:` line in text; whenever the budget is not hit the output is byte-identical to before. Consumer gap G12 (lab H-DRAFT-45585281).
