#!/usr/bin/env python3
"""The real-GitHub-issues transport adapter (counted under
H-136-issueops-live-tier1 in the source lab, kept 2026-08-27, two consecutive
counted 5/5 on live GitHub; shipped as counted from the fixture copy
`transport_adapter.py` — only provenance framing, the script name, and the
audited-helper import differ; usage guide: docs/issueops.md in this plugin).

The one variable H-136 moved and proved: a file-based channel inbox -> real
GitHub issues fetched by pinned issue number, with the frozen CRLF->LF
normalization. Everything downstream of this adapter (converter, preflight,
sandboxed auto-run, reply templater, queue mechanics) consumes the same JSON
payload shape a file-based seeder would have written into the inbox — the
counted transport-parity assertion was a straight byte comparison of
converter outputs across the two transports.

Both transport directions live in this one file so the contract is
calibratable offline without an LLM (both-direction no-LLM calibration):

    render_body(payload, schema)   the GitHub issue-form renderer emulation a
                                   seeder uses to compose an issue body: one
                                   "### <label>" section per schema field present in
                                   the payload, in schema document order; textarea
                                   fields carrying a `render: X` attribute are fenced
                                   as ```X blocks (issue-form behavior); the final body
                                   is CRLF-terminated line by line, faithful to
                                   web-form-submitted issues.
    parse_body(body, schema)       the fetch-side inverse: normalize CRLF->LF (the
                                   frozen normalization -- strictly \\r\\n -> \\n),
                                   split on ### headings, map heading text back to the
                                   schema field id, unwrap render fences, drop
                                   "_No response_" sections (GitHub's empty-optional
                                   marker), and pass unknown headings through as
                                   slugified keys so the downstream converter -- not this
                                   adapter -- stays the single validation point
                                   (it rejects unknown fields itself).

A section heading absent from the body yields no key, so the converter's own
"missing required field" rejection fires with the right field pointer -- the
malformed class is transported, not simulated.

CLI:
    issueops-fetch.py --schema issue-form.yml --out payload.json \
        (--issue N --repo owner/repo --audit-log LOG [--raw-out body.md]  |  --body-file body.md)

Live mode fetches `gh issue view N --json ...` through issueops_gh.gh
(audited, pinned account — set HYP_GH_ACCOUNT). Offline mode (--body-file)
reads a body from disk -- the calibration path. Output JSON is written with
sort_keys, indent=2, ensure_ascii=False and a trailing newline, so transport
parity stays a straight byte comparison.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

_ITEM_START = re.compile(r'^\s*-\s+type:\s*(\S+)\s*$')
_ID_LINE = re.compile(r'^\s*id:\s*(\S+)\s*$')
_LABEL_LINE = re.compile(r'^\s*label:\s*(.+?)\s*$')
_RENDER_LINE = re.compile(r'^\s*render:\s*(\S+)\s*$')
_REQUIRED_LINE = re.compile(r'^\s*required:\s*(true|false)\s*$', re.I)
_HEADING = re.compile(r'^### (.+?)\s*$')
NO_RESPONSE = "_No response_"


def parse_schema(yaml_path):
    """Hand-rolled parser for a GitHub issue-form yml -- same discipline (and
    the same item/id/required recognizers) as the counted converter's parse_schema,
    extended with the two attributes the transport needs: label and render.

    Returns (fields, order):
        fields: {field_id: {"type": ..., "label": str, "render": str|None, "required": bool}}
        order:  [field_id, ...] in document order
    """
    lines = Path(yaml_path).read_text(encoding="utf-8").splitlines()
    fields, order = {}, []
    cur = None

    def flush():
        if cur and cur.get("type") in ("input", "textarea") and cur.get("id"):
            fid = cur["id"]
            if fid not in fields:
                order.append(fid)
            fields[fid] = {
                "type": cur["type"],
                "label": cur.get("label") or fid,
                "render": cur.get("render"),
                "required": cur.get("required", False),
            }

    for line in lines:
        m = _ITEM_START.match(line)
        if m:
            flush()
            cur = {"type": m.group(1)}
            continue
        if cur is None:
            continue
        m = _ID_LINE.match(line)
        if m:
            cur["id"] = m.group(1).strip('"\'')
            continue
        m = _LABEL_LINE.match(line)
        if m and "label" not in cur:
            cur["label"] = m.group(1).strip('"\'')
            continue
        m = _RENDER_LINE.match(line)
        if m:
            cur["render"] = m.group(1).strip('"\'')
            continue
        m = _REQUIRED_LINE.match(line)
        if m:
            cur["required"] = m.group(1).lower() == "true"
            continue
    flush()
    return fields, order


def slugify_key(s):
    s = re.sub(r'[^a-z0-9]+', '_', s.strip().lower()).strip('_')
    return s or "unknown"


def render_body(payload, schema_path):
    """Compose the issue body exactly as GitHub's issue-form renderer would for this
    schema: sections in schema document order, only for fields present in the payload
    (a tampered/API submission omitting a required field simply has no such section),
    render-fenced where the schema says so, CRLF line endings throughout."""
    fields, order = parse_schema(schema_path)
    sections = []
    for fid in order:
        if fid not in payload or payload[fid] is None:
            continue
        value = payload[fid]
        if fields[fid]["render"]:
            value = "```{}\n{}\n```".format(fields[fid]["render"], value)
        sections.append("### {}\n\n{}".format(fields[fid]["label"], value))
    body_lf = "\n\n".join(sections)
    return body_lf.replace("\n", "\r\n")


def normalize_crlf(text):
    """The frozen normalization: CRLF -> LF, nothing else."""
    return text.replace("\r\n", "\n")


def parse_body(body, schema_path):
    """Fetched issue body -> payload dict (see module docstring)."""
    fields, order = parse_schema(schema_path)
    label_to_id = {fields[fid]["label"]: fid for fid in order}
    text = normalize_crlf(body)

    payload = {}
    current_key = None
    current_lines = []

    def commit():
        if current_key is None:
            return
        value = "\n".join(current_lines).strip("\n").strip()
        if value == NO_RESPONSE or value == "":
            return
        fid = label_to_id.get(current_key)
        if fid is None:
            payload[slugify_key(current_key)] = value
            return
        render = fields[fid]["render"]
        if render:
            fence_open = "```" + render
            lines = value.splitlines()
            if len(lines) >= 2 and lines[0].strip() == fence_open and lines[-1].strip() == "```":
                value = "\n".join(lines[1:-1]).strip("\n")
        payload[fid] = value

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            commit()
            current_key = m.group(1)
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)
    commit()
    return payload


def write_payload(path, payload):
    """Byte-identical dumper to the counted payload convention:
    sort_keys + indent=2 + ensure_ascii=False + '\\n'."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def fetch_issue(issue_number, repo, audit_log):
    import issueops_gh as ghops
    proc = ghops.gh(
        ["issue", "view", str(issue_number), "--repo", repo,
         "--json", "number,title,body,state,labels,url"],
        op="issue-view", audit_log=audit_log, issue=issue_number,
    )
    return json.loads(proc.stdout)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True, help="pinned issue-form.yml path")
    ap.add_argument("--out", required=True, help="payload JSON output path")
    ap.add_argument("--issue", type=int, help="live mode: issue number to fetch")
    ap.add_argument("--repo", help="live mode: owner/repo")
    ap.add_argument("--audit-log", help="live mode: JSONL audit log path")
    ap.add_argument("--raw-out", help="live mode: save the raw fetched body (pre-normalization)")
    ap.add_argument("--body-file", help="offline mode: read the body from this file")
    o = ap.parse_args(argv[1:])

    if o.body_file:
        body = Path(o.body_file).read_text(encoding="utf-8")
        meta = {"mode": "offline", "source": o.body_file}
    elif o.issue is not None and o.repo and o.audit_log:
        data = fetch_issue(o.issue, o.repo, o.audit_log)
        body = data.get("body") or ""
        if o.raw_out:
            Path(o.raw_out).parent.mkdir(parents=True, exist_ok=True)
            Path(o.raw_out).write_bytes(body.encode("utf-8"))  # byte-exact raw body; write_text(newline=) is 3.10+ and consumer PATHs may pin 3.9
        meta = {"mode": "live", "issue": data.get("number"), "state": data.get("state"),
                "url": data.get("url"), "crlf_observed": "\r\n" in body}
    else:
        ap.error("need either --body-file, or --issue + --repo + --audit-log")
        return 2

    payload = parse_body(body, o.schema)
    write_payload(o.out, payload)
    print(json.dumps(meta, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
