#!/usr/bin/env python3
"""Workflow-facts loop library: canonical serialization + stdlib validators.

PROVENANCE — COUNTED, byte-preserving port of the kept H-118 fixture library
(experiments/runs/H-118/fixture/impl/facts_lib.py in the source lab; hypothesis
H-118-gwt-accretion-loop KEPT 2026-08-28, two consecutive counted 4/4). Shared
by scripts/emit_workflow_fact.py and scripts/harvest_gwt.py. Only this
provenance framing differs from the counted fixture copy.

Built from methodology-integration-contract.md SS2-SS3 ONLY (directive 8: this
module and its siblings never read fixture/keys/*). Stdlib only, no network.

canonical-v1: json.dumps(obj, sort_keys=True, ensure_ascii=False,
separators=(",", ":")) + "\n" -- one line per JSONL record, byte-stable.
"""
import hashlib
import json
import re

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
HYP_RE = re.compile(r"^H-\d+$")
OUTCOMES = ("pass", "fail", "skip")

FACT_SCHEMA = "workflow-fact/v1"
CASE_SCHEMA = "gwt-case/v1"


def canonical(obj):
    """canonical-v1 single-line serialization (byte-stable across replays)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")) + "\n"


def canonical_doc(obj):
    """canonical document form for multi-record files (indent 1, sorted)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _err(errors, path, msg):
    errors.append("%s: %s" % (path, msg))


def validate_fact(rec):
    """workflow-fact/v1 validator (impl/workflow-fact.schema.json in code form).
    Returns a list of error strings; empty = valid. No author names allowed
    (GOVERNANCE: git blame is attribution)."""
    errors = []
    if not isinstance(rec, dict):
        return ["record: not an object"]
    required = ("schema", "id", "ts", "workflow", "kind", "gates",
                "artifacts", "sha", "links")
    for key in required:
        if key not in rec:
            _err(errors, key, "missing required field")
    for key in rec:
        if key not in required:
            _err(errors, key, "unknown field (v1 is closed; author-name keys "
                              "especially are banned)")
    if errors:
        return errors
    if rec["schema"] != FACT_SCHEMA:
        _err(errors, "schema", "must be %r" % FACT_SCHEMA)
    if not isinstance(rec["id"], int) or rec["id"] < 1:
        _err(errors, "id", "must be a positive integer (monotonic at land-time)")
    if not isinstance(rec["ts"], str) or not DATE_RE.match(rec["ts"]):
        _err(errors, "ts", "must be YYYY-MM-DD")
    if not isinstance(rec["workflow"], str) or not SLUG_RE.match(rec["workflow"]):
        _err(errors, "workflow", "must be a slug")
    if rec["kind"] != "workflow-closed":
        _err(errors, "kind", "v1 kind is 'workflow-closed'")
    if not isinstance(rec["gates"], list) or not rec["gates"]:
        _err(errors, "gates", "must be a non-empty list")
    else:
        for i, g in enumerate(rec["gates"]):
            if not isinstance(g, dict) or set(g) != {"gate", "outcome", "detail"}:
                _err(errors, "gates[%d]" % i, "must be {gate, outcome, detail}")
                continue
            if not isinstance(g["gate"], str) or not SLUG_RE.match(g["gate"]):
                _err(errors, "gates[%d].gate" % i, "must be a slug")
            if g["outcome"] not in OUTCOMES:
                _err(errors, "gates[%d].outcome" % i,
                     "must be one of %s" % (OUTCOMES,))
            if not isinstance(g["detail"], str):
                _err(errors, "gates[%d].detail" % i, "must be a string")
    if not isinstance(rec["artifacts"], list) or \
            not all(isinstance(a, str) for a in rec["artifacts"]):
        _err(errors, "artifacts", "must be a list of repo-relative path strings")
    if not isinstance(rec["sha"], str) or not SHA_RE.match(rec["sha"]):
        _err(errors, "sha", "must be a hex sha (HEAD at close)")
    links = rec["links"]
    if not isinstance(links, dict):
        _err(errors, "links", "must be an object")
    else:
        for k, v in links.items():
            if k == "hypothesis":
                if not isinstance(v, str) or not HYP_RE.match(v):
                    _err(errors, "links.hypothesis", "must match H-NNN")
            elif k == "run":
                if not isinstance(v, (int, str)):
                    _err(errors, "links.run", "must be an int or label string")
            else:
                _err(errors, "links.%s" % k, "unknown link key (v1: hypothesis, run)")
    return errors


def _valid_then(then):
    """gwt/then oneOf: [event ids] (id-level lint-compatible form) |
    {events: [ids]} | {throws: name} | {state: assertion} | {graders: [...]}."""
    if isinstance(then, list):
        return all(isinstance(t, str) and t for t in then) and bool(then)
    if isinstance(then, dict):
        keys = set(then)
        if keys == {"events"}:
            return isinstance(then["events"], list) and \
                all(isinstance(t, str) and t for t in then["events"])
        if keys == {"throws"}:
            return isinstance(then["throws"], str) and bool(then["throws"].strip())
        if keys == {"state"}:
            return isinstance(then["state"], str) and bool(then["state"])
        if keys == {"graders"}:
            return isinstance(then["graders"], list) and bool(then["graders"])
        return False
    return False


def validate_case(case):
    """gwt-case/v1 validator (SS2a superset of the kept lint's accepted keys)."""
    errors = []
    if not isinstance(case, dict):
        return ["case: not an object"]
    if case.get("schema_version") != CASE_SCHEMA:
        _err(errors, "schema_version", "must be %r" % CASE_SCHEMA)
    cid = case.get("gwt/id")
    if not isinstance(cid, str) or not cid:
        _err(errors, "gwt/id", "missing addressable case id")
    src = case.get("gwt/source")
    if not isinstance(src, dict):
        _err(errors, "gwt/source", "missing provenance object")
    else:
        keys = set(src)
        if keys == {"hypothesis", "assertion"}:
            if not isinstance(src["hypothesis"], str) or \
                    not HYP_RE.match(src["hypothesis"]):
                _err(errors, "gwt/source.hypothesis", "must match H-NNN")
            if not isinstance(src["assertion"], int) or src["assertion"] < 1:
                _err(errors, "gwt/source.assertion", "must be a positive int")
        elif keys == {"policy"} or keys == {"ruling"}:
            val = list(src.values())[0]
            if not isinstance(val, str) or not val:
                _err(errors, "gwt/source", "policy/ruling id must be a string")
        else:
            _err(errors, "gwt/source",
                 "must be {hypothesis, assertion} | {policy} | {ruling}")
    sl = case.get("gwt/slice")
    if sl is not None and (not isinstance(sl, str) or not sl):
        _err(errors, "gwt/slice", "optional, but must be a slice id string")
    given = case.get("gwt/given")
    if not isinstance(given, list) or \
            not all(isinstance(g, (str, dict)) for g in given):
        _err(errors, "gwt/given",
             "must be a list of event ids (payload dicts optional per D15)")
    when = case.get("gwt/when")
    if isinstance(when, dict):
        if set(when) != {"prompt", "scaffold"}:
            _err(errors, "gwt/when", "agent-tier form is {prompt, scaffold}")
    elif not isinstance(when, str) or not when:
        _err(errors, "gwt/when", "must be a command id (model tier) or "
                                 "{prompt, scaffold} (agent tier)")
    if not _valid_then(case.get("gwt/then")):
        _err(errors, "gwt/then", "must be [event ids] | {events} | {throws} | "
                                 "{state} | {graders}")
    tags = case.get("gwt/tags")
    if tags is not None and (not isinstance(tags, list) or
                             not all(isinstance(t, str) for t in tags)):
        _err(errors, "gwt/tags", "optional, but must be a list of strings")
    trials = case.get("gwt/trials")
    if trials is not None and not isinstance(trials, dict):
        _err(errors, "gwt/trials", "optional, but must be {k, aggregate}")
    state = case.get("gwt/state")
    if state is not None and state not in ("candidate", "accepted"):
        _err(errors, "gwt/state", "must be candidate|accepted (board chip states; "
                                  "harvested records land candidate until reconciled)")
    allowed = {"schema_version", "gwt/id", "gwt/source", "gwt/slice", "gwt/given",
               "gwt/when", "gwt/then", "gwt/tags", "gwt/trials", "gwt/state"}
    for key in case:
        if key not in allowed:
            _err(errors, key, "unknown field for gwt-case/v1")
    return errors


def roundtrip_identical(text):
    """serialize -> parse -> serialize byte identity for a canonical doc."""
    return canonical_doc(json.loads(text)) == text


def load_jsonl(path):
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
