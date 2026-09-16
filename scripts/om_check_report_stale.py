#!/usr/bin/env python3
"""om_check_report_stale.py -- tier-0 CI glue (this ship's own new bytes, not vendored from a
plugin release; ported from the lab keep getfatday/cause-n-effect H-DRAFT-a28b91c9-om-ci-tier0,
kept 2026-09-15). Prints one decidable line per model tree of the current checkout:

    STALE: True|False|None <model_tree>

computed by the vendored `om-worker.py` beside this file -- its own `_compiled_staleness`, the
same call its `compile-check` verb made one step earlier -- plus one informational line

    LEDGER: <repository-relative path> present|absent

naming the feedback ledger om-worker.py resolved through `.claude/hyp.json` `om_feedback_file`.
Never mutates anything; never commits.

Why the verdict is computed here and not read back from the ledger (ship fix round 5, B1):
rounds 1-4 read the last N rows of a hardcoded `ledger/om-feedback.jsonl` on the assumption
that they were the N rows the compile-check step had just appended. Both halves of that
assumption fail on documented om-worker.py behaviour. (1) It resolves the ledger through
`om_feedback_file` (`ledger_rel`), so a consumer that set the key had om-worker.py write one
file and this script read another -- `STALE: unknown`, then `COMMIT: no (nothing stale)`,
green on a stale tree (the round-5 refuter's measurement). (2) Its `append_to_path` dedupes by
exact bytes against the whole file and returns `duplicate` silently, so when a byte-identical
`model-evaluated` row already sits in the committed ledger (a session hook evaluated the same
commit locally, session rows followed, the ledger was committed) nothing is appended and the
tail is whatever the ledger ended with -- no `STALE:` line at all, the regenerate step never
runs, green on a stale tree. Asking om-worker.py's own functions on the checkout has neither
failure and needs no inline copy of its path rules: `_model_trees` and `ledger_rel` are
called, not mirrored, and the two files are emitted and sha-pinned together by
`om-ci.py emit ci-tier0` (`MANIFEST.json`), so the private name is pinned with its caller.

Exit status (A1): 0 when every model tree got a verdict (`True`, `False`, or om-worker.py's
own `None` for "a date is unknown", printed as-is); 1 when the vendored om-worker.py beside
this file cannot be loaded -- the one shape in which no verdict can be produced -- so an
unreadable verdict goes red, never green. A checkout with no model tree exits 0 (nothing to
report; the trigger's `paths:` list should never have fired).

Stdlib only, Python 3.9.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_om_worker():
    """The `om-worker.py` vendored beside this file (same `emit`, same `MANIFEST.json`), so the
    functions this script calls are the byte-pinned copy the compile-check step itself ran."""
    spec = importlib.util.spec_from_file_location("om_worker_vendored",
                                                  os.path.join(HERE, "om-worker.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    root = os.path.abspath(".")
    try:
        omw = load_om_worker()
    except Exception as exc:  # noqa: BLE001 -- the one shape with no verdict: go red and say why
        print("STALE: unknown (cannot load vendored om-worker.py beside this script: %s: %s)"
              % (type(exc).__name__, exc))
        return 1
    trees = omw._model_trees(root)
    if not trees:
        print("STALE: unknown (no model trees)")
        return 0
    ledger_rel = omw.ledger_rel(root)
    ledger_present = os.path.isfile(os.path.join(root, *ledger_rel.split("/")))
    print("LEDGER: %s %s" % (ledger_rel, "present" if ledger_present else "absent"))
    for tree in trees:
        rel = os.path.relpath(tree, root).replace(os.sep, "/")
        verdict = omw._compiled_staleness(root, tree)
        print("STALE: %r %s" % (verdict.get("stale"), rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
