#!/usr/bin/env python3
"""om_check_report_stale.py -- tier-0 CI glue (this ship's own new bytes, not vendored from a
plugin release; ported from the lab keep getfatday/cause-n-effect H-DRAFT-a28b91c9-om-ci-tier0,
kept 2026-09-15). Reads the LAST `model-evaluated` row `om-worker.py compile-check` just
appended to the feedback ledger in the current checkout and prints one decidable line per model
tree:

    STALE: True|False|None <model_tree>

in the same order om-worker.py appended them, so the last N rows -- N = number of model trees --
are exactly the rows this compile-check pass wrote. Never mutates anything; never commits.

Drift from the kept fixture's `om_check_report_stale.py`: `model_dir` is read from the
consumer's `.claude/hyp.json` (default `operating-model`) instead of a hardcoded literal, via
its own inline copy of the same safe-default rule `hooks/scripts/hyp_config.safe_rel_path`
states (this file is vendored standalone into `.github/om-scripts/` on a runner with no plugin
install, so it cannot import that module -- it re-implements the one rule it needs, as
`scripts/om-worker.py` already does for the same reason).

Stdlib only, Python 3.9.
"""
import glob
import json
import os
import sys


def _model_dir(root):
    """The consumer's configured `model_dir` (`.claude/hyp.json`), or the plugin default
    `operating-model` when the file, the key, or the value is absent/malformed/unsafe (never
    absolute, never a `..` escape) -- the one rule `hyp_config.safe_rel_path` states, inlined."""
    cfg_path = os.path.join(root, ".claude", "hyp.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    v = cfg.get("model_dir") if isinstance(cfg, dict) else None
    if isinstance(v, str) and v.strip() and not os.path.isabs(v.strip()) and ".." not in v.strip("/").split("/"):
        return v.strip().strip("/")
    return "operating-model"


def model_trees(root):
    om_dir = os.path.join(root, _model_dir(root))
    if not os.path.isdir(om_dir):
        return []
    return sorted(d for d in glob.glob(os.path.join(om_dir, "*")) if os.path.isdir(d))


def main():
    root = os.path.abspath(".")
    ledger = os.path.join(root, "ledger", "om-feedback.jsonl")
    trees = model_trees(root)
    n = len(trees)
    if not os.path.isfile(ledger) or n == 0:
        print("STALE: unknown (no ledger or no model trees)")
        return 0
    with open(ledger, "r", encoding="utf-8") as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    tail = lines[-n:] if len(lines) >= n else lines
    for line in tail:
        row = json.loads(line)
        if row.get("kind") != "model-evaluated":
            continue
        stale = row.get("compiled", {}).get("stale")
        print("STALE: %r %s" % (stale, row.get("model_tree")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
