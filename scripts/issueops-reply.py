#!/usr/bin/env python3
"""The resolution reply templater (counted twice in the source lab: under
H-106-issueops-roundtrip, kept 2026-08-15 two consecutive 5/5 on the
file-based transport, and byte-unchanged under H-136-issueops-live-tier1,
kept 2026-08-27 two consecutive counted 5/5 on live GitHub; shipped as
counted from the fixture copy `reply_templater.py` — only provenance framing
and the script name differ; usage guide: docs/issueops.md in this plugin).

The counted outward-comms doctrine this script enforces: replies to a
reporter are ASSEMBLED FROM STRUCTURED FIELDS INTO FIXED TEMPLATES, never
free LLM generation posted to a public surface. Stdlib only (json, re, sys,
pathlib), offline, no free generation. Every field written into a reply JSON
is either passed on argv (itself sourced from a prior deterministic script's
own stdout/exit code -- the converter's REJECT line, preflight's FAIL line)
or read verbatim from an artifact the same pipeline already produced (a
draft's own "## Status" line, a run's own results.json). This script never
invents prose; the calling pipeline supplies any fixed narrative string as a
plain argv value. Emitted replies are STAGED RUN ARTIFACTS — posting one to
a real issue is ladder tier 3, human-gated (docs/issueops.md).

Usage:
    issueops-reply.py resolved  <issue_id> <draft_path> <run2_results_path> <summary> <out_path>
    issueops-reply.py rejected  <issue_id> <field_pointer> <reason> <out_path>
    issueops-reply.py escalated <issue_id> <preflight_check> <detail> <queue_ref> <out_path>

Output is written with sort_keys=True + fixed separators, so byte-identical
inputs (across two independently bootstrapped clones) always produce
byte-identical reply JSON, regardless of any incidental dict/argv ordering.
"""
import json
import re
import sys
from pathlib import Path


def read_status(draft_path):
    text = Path(draft_path).read_text(encoding="utf-8")
    m = re.search(r"^## Status\n(.+)$", text, re.M)
    if not m:
        raise RuntimeError(f"no '## Status' line found in {draft_path}")
    # Status lines in hyp-convention drafts may carry a trailing HTML comment
    # (e.g. "kept <!-- ... -->"); the token itself is the first word.
    return m.group(1).strip().split()[0]


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def cmd_resolved(argv):
    issue_id, draft_path, run2_results_path, summary, out_path = argv
    status = read_status(draft_path)
    if status != "kept":
        raise RuntimeError(f"issueops-reply resolved: draft Status is {status!r}, not 'kept' -- refusing to template a resolved reply")
    results = json.loads(Path(run2_results_path).read_text(encoding="utf-8"))
    assertions_passed = results["assertions_passed"]
    hypothesis_slug = Path(draft_path).stem
    payload = {
        "issue_id": issue_id,
        "status": "resolved",
        "verdict": "kept",
        "hypothesis": hypothesis_slug,
        "evidence": {
            "run_2_results": run2_results_path,
            "assertions_passed": assertions_passed,
        },
        "summary": summary,
    }
    write_json(out_path, payload)


def cmd_rejected(argv):
    issue_id, field_pointer, reason, out_path = argv
    payload = {
        "issue_id": issue_id,
        "status": "rejected",
        "pointer": field_pointer,
        "reason": reason,
    }
    write_json(out_path, payload)


def cmd_escalated(argv):
    issue_id, preflight_check, detail, queue_ref, out_path = argv
    payload = {
        "issue_id": issue_id,
        "status": "escalated",
        "preflight_check": preflight_check,
        "detail": detail,
        "queue_ref": queue_ref,
    }
    write_json(out_path, payload)


COMMANDS = {
    "resolved": (cmd_resolved, 5),
    "rejected": (cmd_rejected, 4),
    "escalated": (cmd_escalated, 5),
}


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(f"usage: {argv[0]} {{resolved|rejected|escalated}} ...", file=sys.stderr)
        return 2
    fn, nargs = COMMANDS[argv[1]]
    rest = argv[2:]
    if len(rest) != nargs:
        print(f"usage error: {argv[1]} takes {nargs} args, got {len(rest)}", file=sys.stderr)
        return 2
    fn(rest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
