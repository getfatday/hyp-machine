#!/usr/bin/env python3
"""events_lib -- the unified event stream (H-238), consumer port.

PROVENANCE -- COUNTED port of the kept H-238 fixture library
(experiments/runs/H-238/fixture/impl/events_lib.py in the source lab;
hypothesis H-238-unified-event-stream KEPT 2026-09-02, consecutive 5/5 pair:
exactly one node-conformant record per firing, zero on replay, byte-identical
re-emission, cold readers answered the frozen provenance set at <=0.5x the
tool calls of the scattered baseline). Record grammar, validator rules,
canonical serialization, idempotence, and the forbidden-key set are the kept
fixture's, unchanged. Named divergences from the counted copy (consumer-repo
resolution only):
  - the stream path reads `.claude/hyp.json` `events_file`
    (default ledger/events.jsonl) instead of the fixture constant;
  - event NODE files resolve under the consumer's `model_dir`
    (default operating-model) at events/<node>.md or */events/<node>.md --
    the same two-level glob the shipped policy interpreter uses -- instead
    of the lab's fixed context directory;
  - a node's representation line must declare the CONFIGURED stream path.

facts_lib-derived (kept H-118 fixture library, byte-identical canonical form):
one JSON object per line, append-only, canonical-v1 serialization. Record
grammar frozen at H-238 registration:

    {"schema": "v1", "instance-of": "event/<node-id>", "caused-by": <str>,
     "date": "YYYY-MM-DD", "subject": <str>, "payload": <object>}

Closed key set. A record validates only against its instance-of node's
DECLARED representation: the node file must exist under the model dir, carry
`type: event`, and declare the stream file in its representation line
(SCHEMA.md law: an event without a declared physical representation doesn't
exist). Canonical node templates ship in `templates/event-nodes/`.

No author names anywhere in a record (GOVERNANCE: git carries attribution;
decisions.py forbidden-fields precedent). Idempotence: emit_event appends only
if the exact canonical line is not already present (H-118 dedupe pattern
generalized to canonical-bytes identity) -- replaying identical inputs appends
zero. Determinism: canonical serialization + caller-pinned dates/shas; this
module never reads a wall clock.
"""
import glob as _glob
import json
import os
import re

EVENT_SCHEMA = "v1"
DEFAULT_STREAM_RELPATH = os.path.join("ledger", "events.jsonl")
DEFAULT_MODEL_DIR = "operating-model"

REQUIRED_KEYS = ("schema", "instance-of", "caused-by", "date", "subject", "payload")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NODE_ID_RE = re.compile(r"^event/[a-z0-9][a-z0-9-]*$")
SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

# Attribution lives in git, never in record fields (decisions.py:6-9 precedent,
# GOVERNANCE Recoverability invariant, H-084/H-156 keeps).
FORBIDDEN_KEYS = frozenset([
    "author", "authors", "name", "names", "by", "user", "username", "email",
    "decided_by", "decided_at", "resolution_commit", "requested_by",
])

# Per-node payload contracts (frozen method constants; the validator IS part of
# the frozen instrument -- H-238 Method line "the validator (facts_lib-derived,
# checks each record against its node's declared representation)").
NODE_PAYLOAD_CONTRACTS = {
    "event/verdict-flipped": ("spec", "from", "to", "evidence"),
    "event/ledger-record-appended": ("kind", "slug"),
    "event/chain-terminal-landed": ("lane", "phase", "rc", "green"),
    "event/advisory-surfaced": ("policy", "message"),
    "event/workflow-closed": ("workflow", "sha", "gates_passed", "gates_total"),
}


def _consumer_cfg(repo_root):
    """{events_file, model_dir} from <repo_root>/.claude/hyp.json + defaults.
    Tiny local reader so this module stays standalone stdlib (the fixture was).
    Never raises."""
    cfg = {"events_file": DEFAULT_STREAM_RELPATH.replace(os.sep, "/"),
           "model_dir": DEFAULT_MODEL_DIR}
    try:
        with open(os.path.join(repo_root, ".claude", "hyp.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in cfg:
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    cfg[key] = val.strip().strip("/")
    except Exception:
        pass
    return cfg


def canonical(obj):
    """canonical-v1 single-line serialization (byte-stable across replays) --
    byte-identical function to facts_lib.canonical (kept H-118)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")) + "\n"


def _err(errors, path, msg):
    errors.append("%s: %s" % (path, msg))


def _scan_forbidden(obj, path, errors):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in FORBIDDEN_KEYS:
                _err(errors, "%s.%s" % (path, k),
                     "forbidden author-attribution key (git carries attribution)")
            _scan_forbidden(v, "%s.%s" % (path, k), errors)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_forbidden(v, "%s[%d]" % (path, i), errors)


def _node_path(repo_root, node_id):
    """First existing node file for event/<slug>: <model_dir>/events/<slug>.md,
    then <model_dir>/*/events/<slug>.md (sorted -- deterministic pick), else the
    flat-layout candidate path (for the error message)."""
    cfg = _consumer_cfg(repo_root)
    slug = node_id.split("/", 1)[1] + ".md"
    model = os.path.join(repo_root, cfg["model_dir"])
    flat = os.path.join(model, "events", slug)
    if os.path.isfile(flat):
        return flat
    hits = sorted(_glob.glob(os.path.join(model, "*", "events", slug)))
    if hits:
        return hits[0]
    return flat


def _node_declares_stream(node_path, stream_rel):
    """Frontmatter must carry type: event and a representation line declaring
    the stream file. Returns (ok, reason)."""
    try:
        with open(node_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, "node file unreadable: %s" % e
    if not text.startswith("---"):
        return False, "node file has no frontmatter"
    fm = text.split("---", 2)
    if len(fm) < 3:
        return False, "node frontmatter unterminated"
    front = fm[1]
    if not re.search(r"^type:\s*event\s*$", front, re.M):
        return False, "node is not type: event"
    rep = None
    for line in front.splitlines():
        if line.startswith("representation:"):
            rep = line
            break
    if rep is None:
        return False, "node declares no representation"
    if stream_rel not in rep:
        return False, ("node representation does not declare "
                       "file(%s); per SCHEMA.md the stream "
                       "record has no declared physical representation" % stream_rel)
    return True, "ok"


def validate_event(rec, repo_root):
    """Full record validation against grammar + the instance-of node's declared
    representation. Returns a list of error strings; empty = valid."""
    errors = []
    if not isinstance(rec, dict):
        return ["record: not an object"]
    for key in REQUIRED_KEYS:
        if key not in rec:
            _err(errors, key, "missing required field")
    for key in rec:
        if key not in REQUIRED_KEYS:
            _err(errors, key, "unknown field (event/v1 is a closed set)")
    if errors:
        return errors
    if rec["schema"] != EVENT_SCHEMA:
        _err(errors, "schema", "must be %r" % EVENT_SCHEMA)
    node_id = rec["instance-of"]
    if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
        _err(errors, "instance-of", "must match event/<node-id>")
        return errors
    if not isinstance(rec["caused-by"], str) or not rec["caused-by"].strip():
        _err(errors, "caused-by", "must be a non-empty string pointer")
    if not isinstance(rec["date"], str) or not DATE_RE.match(rec["date"]):
        _err(errors, "date", "must be YYYY-MM-DD")
    if not isinstance(rec["subject"], str) or not SUBJECT_RE.match(rec["subject"]):
        _err(errors, "subject", "must be a lane/H-id/slug string")
    payload = rec["payload"]
    if not isinstance(payload, dict):
        _err(errors, "payload", "must be an object")
        return errors
    _scan_forbidden(rec, "record", errors)
    stream_rel = _consumer_cfg(repo_root)["events_file"]
    node_path = _node_path(repo_root, node_id)
    if not os.path.isfile(node_path):
        _err(errors, "instance-of",
             "node file missing: %s (an event without a node doesn't exist; "
             "canonical templates: the plugin's templates/event-nodes/)"
             % os.path.relpath(node_path, repo_root))
    else:
        ok, reason = _node_declares_stream(node_path, stream_rel)
        if not ok:
            _err(errors, "instance-of", reason)
    contract = NODE_PAYLOAD_CONTRACTS.get(node_id)
    if contract is None:
        _err(errors, "instance-of",
             "no payload contract for %s (unknown to the frozen validator)" % node_id)
        return errors
    for field in contract:
        if field not in payload:
            _err(errors, "payload.%s" % field, "missing required payload field")
    if node_id == "event/chain-terminal-landed" and "green" in payload:
        if payload["green"] is True and "halt-class" in payload:
            _err(errors, "payload.halt-class", "forbidden on a green terminal")
        if payload["green"] is False and "halt-class" not in payload:
            _err(errors, "payload.halt-class",
                 "required on a non-green terminal (spec assertion 2)")
        if "rc" in payload:
            rc = payload["rc"]
            if not isinstance(rc, int):
                _err(errors, "payload.rc", "must be an integer")
            elif (rc == 0) != bool(payload["green"]):
                _err(errors, "payload.green", "must equal (rc == 0)")
    if node_id == "event/workflow-closed" and "sha" in payload:
        if not isinstance(payload["sha"], str) or not SHA_RE.match(payload["sha"]):
            _err(errors, "payload.sha", "must be a hex sha")
    return errors


def stream_path(repo_root):
    rel = _consumer_cfg(repo_root)["events_file"]
    return os.path.join(repo_root, *rel.split("/"))


def read_stream_lines(repo_root):
    path = stream_path(repo_root)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines(True) if ln.strip()]


def emit_event(repo_root, rec):
    """Validate, canonicalize, dedupe on exact canonical bytes, append.
    Returns {"status": appended|skipped|invalid, "errors": [...], ...}.
    Append-only by construction: the stream is only ever opened with mode 'a'."""
    errors = validate_event(rec, repo_root)
    if errors:
        return {"status": "invalid", "errors": errors}
    line = canonical(rec)
    existing = read_stream_lines(repo_root)
    if line in existing:
        return {"status": "skipped", "errors": [],
                "reason": "identical canonical record already in stream"}
    path = stream_path(repo_root)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    return {"status": "appended", "errors": []}


def make_record(instance_of, caused_by, date, subject, payload):
    return {"schema": EVENT_SCHEMA, "instance-of": instance_of,
            "caused-by": caused_by, "date": date, "subject": subject,
            "payload": payload}
