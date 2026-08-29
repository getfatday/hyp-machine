#!/usr/bin/env python3
"""IssueOps teardown (counted under H-136-issueops-live-tier1 in the source
lab, kept 2026-08-27, two consecutive counted 5/5 on live GitHub with full
post-teardown restoration verified; shipped as counted from the fixture copy
`teardown_issues.py` — only provenance framing, the script name, and the
audited-helper import differ; usage guide: docs/issueops.md in this plugin).

THE TEARDOWN CONTRACT (frozen with the counted fixture):
  1. Operates ONLY on the issue numbers listed in the given RUN-MANIFEST.json --
     never on any other issue (a repo's pre-existing issues, or anything
     another lane seeds, are untouchable by construction).
  2. Allowed operations, per manifest issue, in order:
       a. remove each harness-applied label (the taxonomy label recorded in
          the manifest), op-class label-remove;
       b. exactly one close-with-pointer: `gh issue close N --comment <pointer>`,
          where <pointer> names the durable run record -- the single comment this
          harness ever writes, carried by the allowlisted close operation itself.
  3. Never: delete, reopen, lock, edit a title or body, comment outside the close,
     or touch repository-level configuration (labels stay defined on the repo; only
     their attachment to the seeded issues is removed).
  4. Best-effort completion: a failure on one issue is recorded and the remaining
     issues are still torn down; exit is nonzero if anything failed, and every
     invocation -- success or failure -- is in the audit log.
  5. Restoration record: after teardown, each seeded issue's state/labels/comments
     are fetched (read op) into post-teardown-issues.json -- the artifact a
     "post-teardown listing" assertion grades (closed, pointer present, harness
     labels gone).

Manifest shape: {"repo": "owner/repo", "taxonomy_label": "<label>",
"issues": {"<role>": {"number": N}, ...}}. Requires HYP_GH_ACCOUNT (the
per-invocation account pin — see scripts/issueops_gh.py).

Usage:
    issueops-teardown.py --manifest RUN-MANIFEST.json --pointer TEXT
                         --audit-log LOG --out post-teardown-issues.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import issueops_gh as ghops  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--pointer", required=True, help="run-record pointer for the close comment")
    ap.add_argument("--audit-log", required=True)
    ap.add_argument("--out", required=True)
    o = ap.parse_args(argv[1:])

    manifest = json.loads(Path(o.manifest).read_text(encoding="utf-8"))
    repo = manifest["repo"]
    label = manifest["taxonomy_label"]
    failures = []
    listing = {"pointer": o.pointer, "torn_down": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "issues": {}}

    for role in sorted(manifest["issues"]):
        n = manifest["issues"][role]["number"]
        # 2a. remove the harness-applied taxonomy label (skip cleanly if a halted run
        # never applied it -- removal of an absent label is not an outward write we
        # want to fail teardown over, but the attempt is still audited).
        try:
            view = ghops.gh(["issue", "view", str(n), "--repo", repo, "--json", "labels"],
                            op="issue-view", audit_log=o.audit_log, issue=n)
            labels_now = [l["name"] for l in json.loads(view.stdout).get("labels", [])]
            if label in labels_now:
                ghops.gh(["issue", "edit", str(n), "--repo", repo, "--remove-label", label],
                         op="label-remove", audit_log=o.audit_log, issue=n)
        except Exception as e:  # noqa: BLE001 -- best-effort contract clause 4
            failures.append("issue %d label-remove: %s" % (n, e))
        # 2b. the close-with-pointer.
        try:
            ghops.gh(["issue", "close", str(n), "--repo", repo, "--comment", o.pointer],
                     op="close-with-pointer", audit_log=o.audit_log, issue=n)
        except Exception as e:  # noqa: BLE001
            failures.append("issue %d close-with-pointer: %s" % (n, e))
        # 5. restoration record.
        try:
            post = ghops.gh(
                ["issue", "view", str(n), "--repo", repo,
                 "--json", "number,state,labels,comments,title"],
                op="issue-view", audit_log=o.audit_log, issue=n)
            listing["issues"][role] = json.loads(post.stdout)
        except Exception as e:  # noqa: BLE001
            failures.append("issue %d post-listing: %s" % (n, e))
            listing["issues"][role] = {"number": n, "error": str(e)}

    listing["failures"] = failures
    Path(o.out).parent.mkdir(parents=True, exist_ok=True)
    Path(o.out).write_text(
        json.dumps(listing, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if failures:
        for f in failures:
            print("TEARDOWN FAIL: %s" % f, file=sys.stderr)
        return 1
    print("teardown complete: %d issues closed-with-pointer, label %r removed"
          % (len(manifest["issues"]), label))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
