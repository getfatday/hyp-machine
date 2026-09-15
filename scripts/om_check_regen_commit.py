#!/usr/bin/env python3
"""om_check_regen_commit.py -- tier-0 CI glue (this ship's own new bytes; ported from the lab
keep getfatday/cause-n-effect H-DRAFT-a28b91c9-om-ci-tier0, kept 2026-09-15). The regenerate
half of the compile-check job: if the last compile-check pass (om_check_report_stale.py's
ledger read) found a stale model tree, runs the checkout's declared `compile_command` (from
`.claude/hyp.json`) to regenerate the compiled artifacts, stages ONLY `compiled/**` (never the
model dir's own `model.md` catalogue -- that stays untracked by design, see
scripts/compile-catalog.py and docs/passive-feedback.md, "The catalogue projection"; `git add`
on a gitignored path would refuse without `-f` anyway, and this script never forces it), and
commits exactly once IF that staged diff is non-empty. Never pushes -- the workflow's own
"push regenerated commit if any" step does that with the checkout-persisted `GITHUB_TOKEN`.
Exits 0 always; failures are reported on the `COMMIT:` line, never raised, so one missing
`compile_command` reads as "no commit", not a crash.

Loop guard (belt and suspenders): even though the workflow's `if: actor != bot` step predicate
is meant to keep this script from running at all when `GITHUB_ACTOR` is the bot identity, this
script re-checks the same env var itself and no-ops if it somehow still ran -- so a future
template change that drops the step-level `if` cannot reopen the loop silently.

Drift from the kept fixture's `om_check_regen_commit.py`: (1) `model_dir` is read from the
consumer's `.claude/hyp.json` instead of a hardcoded `"operating-model"` literal, mirroring
`om_check_report_stale.py`'s own inline copy of the same rule; (2) the fixture's docstring said
it stages "`compiled/**` and the model dir's own `model.md` catalogue paths", but its own code
only ever ran `git add -- compiled` and its own later comment says the opposite ("the
catalogue's `model.md` stays untracked by design ... never stage it") -- this port follows the
code and that second comment, not the stale docstring sentence, since staging an ignored
catalogue file would re-track it and contradict the kept catalogue-projection lane
(H-DRAFT-4e06e157-om-rows-merge-shape) this workflow itself calls into on every run.

Stdlib only, Python 3.9.
"""
import glob
import json
import os
import subprocess
import sys

BOT_IDENTITY = "om-check[bot]"


def _model_dir(root):
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


def model_trees(root, model_dir):
    om_dir = os.path.join(root, model_dir)
    if not os.path.isdir(om_dir):
        return []
    return sorted(d for d in glob.glob(os.path.join(om_dir, "*")) if os.path.isdir(d))


def any_stale(root, model_dir):
    ledger = os.path.join(root, "ledger", "om-feedback.jsonl")
    trees = model_trees(root, model_dir)
    if not os.path.isfile(ledger) or not trees:
        return False
    with open(ledger, "r", encoding="utf-8") as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    tail = lines[-len(trees):] if len(lines) >= len(trees) else lines
    for line in tail:
        row = json.loads(line)
        if row.get("kind") == "model-evaluated" and row.get("compiled", {}).get("stale"):
            return True
    return False


def compile_command(root):
    cfg_path = os.path.join(root, ".claude", "hyp.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None
    cmd = cfg.get("compile_command")
    return cmd if isinstance(cmd, str) and cmd.strip() else None


def run(cmd, cwd=None, check=False, shell=False, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, cwd=cwd, shell=shell, env=full_env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and proc.returncode != 0:
        raise SystemExit("om_check_regen_commit: %r failed: %s" % (cmd, proc.stdout))
    return proc


def bot_git_identity_env():
    """The regenerate commit is authored AS the bot identity (never the human contributor whose
    push triggered it) -- both because it is mechanically true (no human wrote this diff) and
    because it is what makes the loop guard work at all: the commit's own subsequent push must
    read `github.actor == BOT_IDENTITY` for the `if` predicate to skip a second round."""
    return {
        "GIT_AUTHOR_NAME": BOT_IDENTITY, "GIT_AUTHOR_EMAIL": "om-check-bot@om-ci-tier0.invalid",
        "GIT_COMMITTER_NAME": BOT_IDENTITY, "GIT_COMMITTER_EMAIL": "om-check-bot@om-ci-tier0.invalid",
    }


def main():
    root = os.path.abspath(".")
    actor = os.environ.get("GITHUB_ACTOR", "")
    if actor == BOT_IDENTITY:
        print("COMMIT: no (actor is bot identity, belt-and-suspenders no-op)")
        return 0

    model_dir = _model_dir(root)
    if not any_stale(root, model_dir):
        print("COMMIT: no (nothing stale)")
        return 0

    cmd = compile_command(root)
    if cmd is None:
        print("COMMIT: no (no compile_command declared)")
        return 0
    run(cmd, cwd=root, check=False, shell=True)

    # `compiled/**` only -- the catalogue's `model.md` stays untracked by design and a `git add`
    # on an ignored path would refuse without -f anyway; never stage it (see module docstring).
    run(["git", "add", "--", "compiled"], cwd=root)
    staged = run(["git", "diff", "--cached", "--quiet"], cwd=root)
    if staged.returncode == 0:
        print("COMMIT: no (regeneration produced no byte change)")
        return 0
    run(["git", "commit", "-q", "-m", "chore(om-check): regenerate compiled artifacts [om-check]"],
        cwd=root, check=True, env=bot_git_identity_env())
    print("COMMIT: yes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
