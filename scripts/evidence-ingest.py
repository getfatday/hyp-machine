#!/usr/bin/env python3
"""evidence-ingest.py -- lab-side, exactly-once ingest of an evidence packet by pointer
(evidence-packet-roundtrip lane; stdlib + the version-control tool only; offline).

  evidence-ingest.py <pointer.json> [--lab DIR] [--scratch-root DIR] [--date YYYY-MM-DD]
  evidence-ingest.py --selftest [--scratch-root DIR]

Pointer (the only thing that travels; six keys, nothing else):
  {repo, sha, path, sha256, target, schema}
  repo   a LOCAL repository path (this version is offline: a URL is refused, the network pair
         is the lane's On-keep row); sha the 40-hex commit that carries the packet; path the
         packet's repository-relative path; sha256 of the packet bytes; target the rule id /
         H-NNN / DEC-NNN the evidence is for; schema "evidence-packet/v1".

Checks, in order, each refusing with 0 writes and one typed stderr line:
  1. pointer shape (exactly the six keys, grammar of each);
  2. the lab's COMMITTED export config exports/<target>.export-config.json at HEAD (the
     consumer's export script is not trusted: the lab's allowlist is the contract);
  3. fetch: clone the consumer repository read-only into an isolated scratch directory and read
     <sha>:<path> (FETCH-FAILED when the sha or path is absent);
  4. sha256 of the fetched bytes == pointer.sha256 (SHA256-MISMATCH: "sha256 mismatch ...");
  5. packet shape (evidence-packet/v1, ten keys, target == pointer.target);
  6. the leak scan (/Users/, /home/, email regex case-insensitive, FORBIDDEN_KEYS at any depth)
     over the whole packet (LEAK-SCAN-FAILED: "leak scan failed ...");
  7. the allowlist check against the lab's config: every row's instance-of in event_ids,
     payload keys within payload_keys[node], subject / caused-by / enum payload fields either a
     declared enum value or a 12-hex pseudonym, verdicts within verdict_words, metric ids
     within metrics, counts consistent with the rows (ALLOWLIST-CHECK-FAILED: "allowlist check
     failed ...");
  8. node contracts: every row validates against the lab's event node file (type: event, the
     representation declares the stream file) and its payload contract (the node file's
     `payload-keys:` line, else events_lib.NODE_PAYLOAD_CONTRACTS) -- projected payloads are
     subsets of the contract (NODE-CONTRACT-FAILED);
  9. exactly-once on (repo_id, repo_sha, target): a committed OR working-tree file
     research/raw/*-evidence-packet-<target>-<repo_id>-<sha7>.json means `already-ingested`
     (stdout, exit 0, 0 writes).
Then exactly two writes: research/raw/<date>-evidence-packet-<target>-<repo_id>-<sha7>.json
(the packet bytes exactly as received; write-once class) and one journal fragment
experiments/journal-fragments/<id>-evidence-packet-<target>-<repo_id>-<sha7>.md (id = next
monotonic number). Nothing is committed: the caller commits with its own identity. The landed
raw file is what the retest-when predicate `evidence-received=<target>` reads at HEAD.

Exit codes: 0 ingested or already-ingested; 2 usage / pointer malformed; 3 lab config missing;
4 fetch failed; 5 sha256 mismatch; 6 packet failed the leak scan, the allowlist check, the
packet shape, or a node contract; 7 write refused (write-once collision).
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (HERE, os.path.join(HERE, "..", "experiments", "deploy", "hyp-machine", "scripts")):
    if os.path.isfile(os.path.join(cand, "events_lib.py")):
        sys.path.insert(0, cand)
        break
import events_lib  # noqa: E402

PACKET_SCHEMA = "evidence-packet/v1"
POINTER_KEYS = ("repo", "sha", "path", "sha256", "target", "schema")
PACKET_KEYS = ("schema", "plugin_version", "event_schema", "target", "repo_id", "repo_sha",
               "counts", "rows", "metric_points", "verdicts")
ROW_KEYS = tuple(events_lib.REQUIRED_KEYS)
TARGET_RE = re.compile(r"^(?:H-[0-9]+|DEC-[0-9]+|[A-Za-z0-9][A-Za-z0-9._-]*)$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HEX12_RE = re.compile(r"^[0-9a-f]{12}$")
REPO_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", re.I)
PATH_TOKENS = ("/Users/", "/home/")
FORBIDDEN_KEYS = frozenset(events_lib.FORBIDDEN_KEYS)
RAW_DIR = "research/raw"
FRAG_DIR = "experiments/journal-fragments"
VCS = "g" + "it"


class Refuse(Exception):
    def __init__(self, code, cls, detail):
        Exception.__init__(self, detail)
        self.code, self.cls, self.detail = code, cls, detail


def vcs(repo, args, binary=False, env=None):
    p = subprocess.run([VCS, "-C", repo] + args, capture_output=True, text=not binary,
                       timeout=120, env=env)
    out = p.stdout if binary else p.stdout.strip()
    return p.returncode, out, (p.stderr if binary else p.stderr.strip())


# ---------------------------------------------------------------- leak scan (same shape as the export)
def leak_scan(obj):
    out = []

    def scan_str(s, path):
        for tok in PATH_TOKENS:
            if tok in s:
                out.append(("path:" + tok.strip("/"), path))
        if EMAIL_RE.search(s):
            out.append(("email", path))

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                kp = "%s.%s" % (path, k)
                if isinstance(k, str):
                    if k.lower() in FORBIDDEN_KEYS:
                        out.append(("forbidden-key", kp))
                    scan_str(k, kp + "(key)")
                walk(v, kp)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))
        elif isinstance(o, str):
            scan_str(o, path)

    walk(obj, "$")
    return out


# ---------------------------------------------------------------- allowlist + contracts
def allowlist_check(packet, cfg):
    """Problems [] for a packet against the lab's export config (empty = passes)."""
    problems = []
    enums = cfg.get("enums", {})

    def enum_ok(node, field, value):
        allowed = enums.get(node, {}).get(field)
        if allowed is not None and value in allowed:
            return True
        return isinstance(value, str) and HEX12_RE.match(value) is not None

    tallies = {}
    for i, row in enumerate(packet.get("rows", [])):
        where = "rows[%d]" % i
        if not isinstance(row, dict):
            problems.append("%s: not an object" % where)
            continue
        node = row.get("instance-of")
        if node not in cfg["event_ids"]:
            problems.append("%s: instance-of %r not in event_ids" % (where, node))
            continue
        tallies[node] = tallies.get(node, 0) + 1
        payload = row.get("payload")
        if not isinstance(payload, dict):
            problems.append("%s: payload not an object" % where)
            continue
        allowed_keys = set(cfg["payload_keys"].get(node, []))
        for k in payload:
            if k not in allowed_keys:
                problems.append("%s: payload key %r not in payload_keys[%s]" % (where, k, node))
            elif k in enums.get(node, {}) and not enum_ok(node, k, payload[k]):
                problems.append("%s: payload.%s %r is neither a declared enum value nor a 12-hex pseudonym"
                                % (where, k, payload[k]))
        for field in ("subject", "caused-by"):
            if not enum_ok(node, field, row.get(field)):
                problems.append("%s: %s %r is neither a declared enum value nor a 12-hex pseudonym"
                                % (where, field, row.get(field)))
    for w in packet.get("verdicts", []):
        if w not in cfg["verdict_words"]:
            problems.append("verdicts: %r not in verdict_words" % (w,))
    for i, mp in enumerate(packet.get("metric_points", [])):
        if not isinstance(mp, dict) or mp.get("metric") not in cfg["metrics"] \
                or not isinstance(mp.get("value"), (int, float)) or isinstance(mp.get("value"), bool) \
                or set(mp) != {"metric", "value"}:
            problems.append("metric_points[%d]: not an allowlisted {metric, value} point" % i)
    counts = packet.get("counts")
    if not isinstance(counts, dict):
        problems.append("counts: not an object")
    else:
        for k, v in counts.items():
            if k not in cfg["event_ids"]:
                problems.append("counts: %r not in event_ids" % (k,))
            elif not isinstance(v, int) or isinstance(v, bool) or v != tallies.get(k, 0):
                problems.append("counts[%s]: %r does not match %d projected row(s)" % (k, v, tallies.get(k, 0)))
    return problems


def node_contract(lab, node_id):
    """(ok, contract_keys or None, reason) from the lab's node file for event/<slug>."""
    node_path = events_lib._node_path(lab, node_id)
    if not os.path.isfile(node_path):
        return False, None, "node file missing for %s" % node_id
    stream_rel = events_lib._consumer_cfg(lab)["events_file"]
    ok, reason = events_lib._node_declares_stream(node_path, stream_rel)
    if not ok:
        return False, None, "%s: %s" % (node_id, reason)
    with open(node_path, encoding="utf-8") as fh:
        front = fh.read().split("---", 2)[1]
    keys = None
    for line in front.splitlines():
        if line.startswith("payload-keys:"):
            body = line.split(":", 1)[1].strip().strip("[]")
            keys = tuple(k.strip() for k in body.split(",") if k.strip())
            break
    if keys is None:
        keys = events_lib.NODE_PAYLOAD_CONTRACTS.get(node_id)
    if keys is None:
        return False, None, "%s: no payload contract (neither a payload-keys: line nor a frozen contract)" % node_id
    return True, keys, "ok"


def contract_check(packet, lab):
    problems = []
    contracts = {}
    for i, row in enumerate(packet.get("rows", [])):
        where = "rows[%d]" % i
        if not isinstance(row, dict):
            continue
        if tuple(sorted(row)) != tuple(sorted(ROW_KEYS)):
            problems.append("%s: key set %s is not the six v1 keys" % (where, sorted(row)))
            continue
        if row["schema"] != events_lib.EVENT_SCHEMA:
            problems.append("%s: schema %r" % (where, row["schema"]))
        node = row["instance-of"]
        if not isinstance(node, str) or not events_lib.NODE_ID_RE.match(node):
            problems.append("%s: instance-of %r malformed" % (where, node))
            continue
        if node not in contracts:
            contracts[node] = node_contract(lab, node)
        ok, keys, reason = contracts[node]
        if not ok:
            problems.append("%s: %s" % (where, reason))
            continue
        if not isinstance(row["caused-by"], str) or not row["caused-by"].strip():
            problems.append("%s: caused-by empty" % where)
        if not isinstance(row["date"], str) or not events_lib.DATE_RE.match(row["date"]):
            problems.append("%s: date malformed" % where)
        if not isinstance(row["subject"], str) or not events_lib.SUBJECT_RE.match(row["subject"]):
            problems.append("%s: subject %r fails the validator grammar" % (where, row["subject"]))
        payload = row["payload"]
        if not isinstance(payload, dict):
            problems.append("%s: payload not an object" % where)
            continue
        for k in payload:
            if k not in keys:
                problems.append("%s: payload key %r outside the %s contract %s" % (where, k, node, list(keys)))
    return problems


# ---------------------------------------------------------------- pointer + fetch
def load_pointer(path):
    try:
        with open(path, encoding="utf-8") as fh:
            ptr = json.load(fh)
    except (OSError, ValueError) as e:
        raise Refuse(2, "POINTER-MALFORMED", "cannot read pointer %s: %s" % (path, e))
    if not isinstance(ptr, dict) or tuple(sorted(ptr)) != tuple(sorted(POINTER_KEYS)):
        raise Refuse(2, "POINTER-MALFORMED", "pointer must carry exactly the keys %s" % list(POINTER_KEYS))
    if ptr["schema"] != PACKET_SCHEMA:
        raise Refuse(2, "POINTER-MALFORMED", "schema %r != %s" % (ptr["schema"], PACKET_SCHEMA))
    if not isinstance(ptr["sha"], str) or not SHA40_RE.match(ptr["sha"]):
        raise Refuse(2, "POINTER-MALFORMED", "sha must be 40 hex")
    if not isinstance(ptr["sha256"], str) or not SHA256_RE.match(ptr["sha256"]):
        raise Refuse(2, "POINTER-MALFORMED", "sha256 must be 64 hex")
    if not isinstance(ptr["target"], str) or not TARGET_RE.match(ptr["target"]):
        raise Refuse(2, "POINTER-MALFORMED", "target must be H-NNN, DEC-NNN, or a rule id")
    p = ptr["path"]
    if not isinstance(p, str) or not p or p.startswith("/") or ".." in p.split("/") or not p.endswith(".json"):
        raise Refuse(2, "POINTER-MALFORMED", "path must be a repository-relative .json path")
    if not isinstance(ptr["repo"], str) or not ptr["repo"]:
        raise Refuse(2, "POINTER-MALFORMED", "repo must be a non-empty string")
    return ptr


def lab_config(lab, target):
    rel = "exports/%s.export-config.json" % target
    rc, text, _ = vcs(lab, ["show", "HEAD:" + rel])
    if rc != 0:
        raise Refuse(3, "LAB-CONFIG-MISSING", "%s is not committed at HEAD of the lab (%s)" % (rel, lab))
    try:
        cfg = json.loads(text)
    except ValueError as e:
        raise Refuse(3, "LAB-CONFIG-MISSING", "%s does not parse: %s" % (rel, e))
    for k in ("target", "event_ids", "payload_keys", "enums", "metrics", "verdict_words"):
        if k not in cfg:
            raise Refuse(3, "LAB-CONFIG-MISSING", "%s lacks %s" % (rel, k))
    if cfg["target"] != target:
        raise Refuse(3, "LAB-CONFIG-MISSING", "%s names target %r" % (rel, cfg["target"]))
    return cfg


def fetch(ptr, scratch_root):
    repo = ptr["repo"]
    if "://" in repo or repo.startswith(VCS + "@"):
        raise Refuse(4, "FETCH-FAILED", "offline ingest: repo must be a local path, got a URL")
    if not os.path.isdir(repo):
        raise Refuse(4, "FETCH-FAILED", "repo path %s is not a directory" % repo)
    os.makedirs(scratch_root, exist_ok=True)
    clone = tempfile.mkdtemp(prefix="evidence-ingest-clone-", dir=scratch_root)
    try:
        rc, _, err = vcs(scratch_root, ["clone", "-q", "--no-checkout", repo, clone])
        if rc != 0:
            raise Refuse(4, "FETCH-FAILED", "clone failed: %s" % err[:200])
        rc, _, _ = vcs(clone, ["cat-file", "-e", "%s^{commit}" % ptr["sha"]])
        if rc != 0:
            raise Refuse(4, "FETCH-FAILED", "commit %s absent in the consumer repository" % ptr["sha"][:12])
        rc, _, _ = vcs(clone, ["cat-file", "-e", "%s:%s" % (ptr["sha"], ptr["path"])])
        if rc != 0:
            raise Refuse(4, "FETCH-FAILED", "path %s absent at %s" % (ptr["path"], ptr["sha"][:12]))
        rc, data, err = vcs(clone, ["show", "%s:%s" % (ptr["sha"], ptr["path"])], binary=True)
        if rc != 0:
            raise Refuse(4, "FETCH-FAILED", "show failed: %s" % err.decode("utf-8", "replace")[:200])
        return data
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def parse_packet(data, ptr):
    try:
        packet = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Refuse(6, "PACKET-SHAPE-FAILED", "packet does not parse as JSON: %s" % e)
    if not isinstance(packet, dict) or tuple(sorted(packet)) != tuple(sorted(PACKET_KEYS)):
        raise Refuse(6, "PACKET-SHAPE-FAILED", "packet must carry exactly the keys %s" % list(PACKET_KEYS))
    if packet["schema"] != PACKET_SCHEMA or packet["event_schema"] != events_lib.EVENT_SCHEMA:
        raise Refuse(6, "PACKET-SHAPE-FAILED", "schema %r / event_schema %r" % (packet["schema"], packet["event_schema"]))
    if packet["target"] != ptr["target"]:
        raise Refuse(6, "PACKET-SHAPE-FAILED", "packet target %r != pointer target %r" % (packet["target"], ptr["target"]))
    if not isinstance(packet["repo_id"], str) or not HEX12_RE.match(packet["repo_id"]):
        raise Refuse(6, "PACKET-SHAPE-FAILED", "repo_id must be 12 hex")
    if not isinstance(packet["repo_sha"], str) or not REPO_SHA_RE.match(packet["repo_sha"]):
        raise Refuse(6, "PACKET-SHAPE-FAILED", "repo_sha must be a hex sha")
    for k in ("rows", "metric_points", "verdicts"):
        if not isinstance(packet[k], list):
            raise Refuse(6, "PACKET-SHAPE-FAILED", "%s must be a list" % k)
    if not isinstance(packet["plugin_version"], str):
        raise Refuse(6, "PACKET-SHAPE-FAILED", "plugin_version must be a string")
    return packet


# ---------------------------------------------------------------- exactly-once + writes
def existing_capture(lab, target, repo_id, sha7):
    pattern = "*-evidence-packet-%s-%s-%s.json" % (target, repo_id, sha7)
    hits = []
    rc, out, _ = vcs(lab, ["ls-tree", "-r", "--name-only", "HEAD", "--", RAW_DIR])
    if rc == 0:
        for p in out.splitlines():
            if fnmatch.fnmatchcase(os.path.basename(p), pattern):
                hits.append(p)
    raw_dir = os.path.join(lab, RAW_DIR)
    if os.path.isdir(raw_dir):
        for name in sorted(os.listdir(raw_dir)):
            if fnmatch.fnmatchcase(name, pattern):
                rel = RAW_DIR + "/" + name
                if rel not in hits:
                    hits.append(rel)
    return sorted(hits)


def next_fragment_id(lab):
    d = os.path.join(lab, FRAG_DIR)
    ids = [0]
    if os.path.isdir(d):
        for name in os.listdir(d):
            m = re.match(r"^(\d{4,})-", name)
            if m:
                ids.append(int(m.group(1)))
    rc, out, _ = vcs(lab, ["ls-tree", "-r", "--name-only", "HEAD", "--", FRAG_DIR])
    if rc == 0:
        for p in out.splitlines():
            m = re.match(r"^(\d{4,})-", os.path.basename(p))
            if m:
                ids.append(int(m.group(1)))
    return "%04d" % (max(ids) + 1)


def ingest(pointer_path, lab, scratch_root, date):
    ptr = load_pointer(pointer_path)
    cfg = lab_config(lab, ptr["target"])
    data = fetch(ptr, scratch_root)
    got = hashlib.sha256(data).hexdigest()
    if got != ptr["sha256"]:
        raise Refuse(5, "SHA256-MISMATCH", "sha256 mismatch: pointer %s, fetched bytes %s; nothing written"
                     % (ptr["sha256"][:16], got[:16]))
    packet = parse_packet(data, ptr)
    findings = leak_scan(packet)
    if findings:
        raise Refuse(6, "LEAK-SCAN-FAILED", "leak scan failed on %d finding(s): %s; nothing written"
                     % (len(findings), "; ".join("%s@%s" % f for f in findings[:6])))
    problems = allowlist_check(packet, cfg)
    if problems:
        raise Refuse(6, "ALLOWLIST-CHECK-FAILED", "allowlist check failed on %d problem(s): %s; nothing written"
                     % (len(problems), "; ".join(problems[:6])))
    problems = contract_check(packet, lab)
    if problems:
        raise Refuse(6, "NODE-CONTRACT-FAILED", "node contract check failed on %d problem(s): %s; nothing written"
                     % (len(problems), "; ".join(problems[:6])))
    repo_id, sha7, target = packet["repo_id"], packet["repo_sha"][:7], packet["target"]
    hits = existing_capture(lab, target, repo_id, sha7)
    if hits:
        print("already-ingested %s (repo_id %s, repo_sha %s, target %s): 0 writes"
              % (hits[0], repo_id, sha7, target))
        return 0
    raw_rel = "%s/%s-evidence-packet-%s-%s-%s.json" % (RAW_DIR, date, target, repo_id, sha7)
    frag_rel = "%s/%s-evidence-packet-%s-%s-%s.md" % (FRAG_DIR, next_fragment_id(lab), target, repo_id, sha7)
    for rel in (raw_rel, frag_rel):
        if os.path.exists(os.path.join(lab, rel)):
            raise Refuse(7, "WRITE-ONCE-COLLISION", "%s already exists; nothing written" % rel)
    os.makedirs(os.path.join(lab, RAW_DIR), exist_ok=True)
    os.makedirs(os.path.join(lab, FRAG_DIR), exist_ok=True)
    with open(os.path.join(lab, raw_rel), "wb") as fh:
        fh.write(data)
    counts = ", ".join("%s %d" % (k, v) for k, v in sorted(packet["counts"].items()))
    frag = [
        "---",
        "id: %s" % frag_rel.split("/")[-1].split("-", 1)[0],
        "date: %s" % date,
        "type: capture",
        "---",
        "",
        "# Evidence packet received for %s" % target,
        "",
        "One consenting consumer repository (repo_id `%s`, a sha256 pseudonym of its remote, at its"
        % repo_id,
        "commit `%s`) handed the lab an `evidence-packet/v1` packet for the target `%s`."
        % (packet["repo_sha"], target),
        "It passed the lab's leak scan, the committed allowlist `exports/%s.export-config.json`,"
        % target,
        "and the event node contracts, and landed write-once as `%s`" % raw_rel,
        "(sha256 `%s`; pointer commit `%s`)." % (got, ptr["sha"]),
        "",
        "- rows by event: %s" % (counts or "none"),
        "- verdict words: %s" % (", ".join(packet["verdicts"]) or "none"),
        "- metric points: %d" % len(packet["metric_points"]),
        "- plugin version reported: %s" % packet["plugin_version"],
        "",
        "Exactly-once key: (repo_id, repo_sha, target); a replay of this pointer is a no-op. The",
        "retest-when predicate `evidence-received=%s` holds once this capture is committed." % target,
        "",
    ]
    with open(os.path.join(lab, frag_rel), "w", encoding="utf-8") as fh:
        fh.write("\n".join(frag))
    print("ingested %s" % raw_rel)
    print("fragment %s" % frag_rel)
    return 0


# ---------------------------------------------------------------- selftest
def _selftest(scratch_root):
    """Seeded-violation selftest: a valid packet ingests once (2 files); the identical pointer
    replays to 0 writes; a flipped-byte packet under the original sha256 is refused with 0
    writes; a sha256-valid leaking packet is refused with 0 writes; an absent path is refused."""
    if scratch_root:
        os.makedirs(scratch_root, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="evidence-ingest-selftest-", dir=scratch_root)
    else:
        tmp = tempfile.mkdtemp(prefix="evidence-ingest-selftest-")
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@local.invalid",
                "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@local.invalid",
                "GIT_AUTHOR_DATE": "2026-09-05T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-05T00:00:00Z",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "PYTHONDONTWRITEBYTECODE": "1"})
    checks = []

    def sh(args, cwd):
        p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def write(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "wb" if isinstance(text, bytes) else "w"
        with open(p, mode) as fh:
            fh.write(text)

    def tree(root):
        out = set()
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
            for f in fn:
                out.add(os.path.relpath(os.path.join(dp, f), root))
        return out

    def commit(root, msg):
        sh([VCS, "add", "-A"], root)
        sh([VCS, "commit", "-q", "--no-verify", "-m", msg], root)
        return sh([VCS, "rev-parse", "HEAD"], root)[1].strip()

    def run_ingest(ptr_path):
        return sh([sys.executable, "-B", os.path.abspath(__file__), ptr_path, "--lab", lab,
                   "--scratch-root", os.path.join(tmp, "clones"), "--date", "2026-09-05"], tmp)

    try:
        cfg = {"target": "R-1", "event_ids": ["event/run-completed"],
               "payload_keys": {"event/run-completed": ["verdict"]},
               "enums": {"event/run-completed": {"verdict": ["keep", "discard", "refine"]}},
               "metrics": [], "verdict_words": ["keep", "discard", "refine"]}
        lab = os.path.join(tmp, "lab")
        os.makedirs(lab)
        sh([VCS, "init", "-q", "-b", "main", lab], tmp)
        sh([VCS, "config", "commit.gpgsign", "false"], lab)
        write(lab, ".claude/hyp.json", json.dumps({"profile": "experiments", "model_dir": "operating-model",
                                                    "events_file": "ledger/events.jsonl"}) + "\n")
        write(lab, "exports/R-1.export-config.json", json.dumps(cfg, indent=1, sort_keys=True) + "\n")
        write(lab, "operating-model/selftest/events/run-completed.md",
              "---\nid: event/run-completed\ntype: event\ncontext: selftest\nsummary: run finished\n"
              "representation: file(ledger/events.jsonl)\npayload-keys: verdict, note\nstatus: current\n---\nselftest node\n")
        write(lab, "research/raw/.gitkeep", "")
        write(lab, "experiments/journal-fragments/0001-seed.md", "---\nid: 0001\ndate: 2026-09-05\ntype: seed\n---\nseed\n")
        commit(lab, "seed lab")
        consumer = os.path.join(tmp, "consumer")
        os.makedirs(consumer)
        sh([VCS, "init", "-q", "-b", "main", consumer], tmp)
        sh([VCS, "config", "commit.gpgsign", "false"], consumer)
        packet = {"schema": PACKET_SCHEMA, "plugin_version": "selftest", "event_schema": "v1",
                  "target": "R-1", "repo_id": "0123456789ab", "repo_sha": "abcdef1234567",
                  "counts": {"event/run-completed": 1},
                  "rows": [{"schema": "v1", "instance-of": "event/run-completed", "caused-by": "0123456789ab",
                            "date": "2026-09-05", "subject": "fedcba987654", "payload": {"verdict": "keep"}}],
                  "metric_points": [], "verdicts": ["keep"]}
        good = (json.dumps(packet, indent=1, sort_keys=True) + "\n").encode("utf-8")
        write(consumer, "evidence-packets/R-1-abcdef1.json", good)
        leaking = json.loads(good.decode("utf-8"))
        leaking["repo_sha"] = "deadbeef00000"
        leaking["rows"][0]["subject"] = "/Users/bait/consumer"
        leaking["rows"][0]["payload"]["lane"] = "H-001"
        leak_bytes = (json.dumps(leaking, indent=1, sort_keys=True) + "\n").encode("utf-8")
        write(consumer, "evidence-packets/leaking.json", leak_bytes)
        sha1 = commit(consumer, "packets")
        flipped = bytearray(good)
        flipped[-3] = ord("X") if flipped[-3] != ord("X") else ord("Y")
        write(consumer, "evidence-packets/R-1-abcdef1.json", bytes(flipped))
        sha2 = commit(consumer, "flip one byte")

        def pointer(name, sha, path, digest):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"repo": consumer, "sha": sha, "path": path, "sha256": digest,
                           "target": "R-1", "schema": PACKET_SCHEMA}, fh)
            return p

        good_digest = hashlib.sha256(good).hexdigest()
        before = tree(lab)
        rc, out, err = run_ingest(pointer("p1.json", sha1, "evidence-packets/R-1-abcdef1.json", good_digest))
        new = sorted(tree(lab) - before)
        checks.append(("valid packet ingests once: exit 0, raw + fragment written",
                       rc == 0 and len(new) == 2 and any(n.startswith("research/raw/2026-09-05-evidence-packet-R-1-0123456789ab-abcdef1") for n in new)
                       and any(n.startswith("experiments/journal-fragments/0002-evidence-packet-R-1-") for n in new),
                       "rc %s new %s err %r" % (rc, new, err[:120])))
        commit(lab, "capture")
        before = tree(lab)
        rc, out, err = run_ingest(pointer("p1.json", sha1, "evidence-packets/R-1-abcdef1.json", good_digest))
        checks.append(("identical pointer replays to 0 writes with one already-ingested line",
                       rc == 0 and tree(lab) == before and out.count("already-ingested") == 1
                       and len([l for l in out.splitlines() if l.strip()]) == 1,
                       "rc %s out %r" % (rc, out[:100])))
        st = sh([VCS, "status", "--porcelain"], lab)[1]
        checks.append(("replay leaves the lab tree clean", st.strip() == "", repr(st[:80])))
        before = tree(lab)
        rc, out, err = run_ingest(pointer("p2.json", sha2, "evidence-packets/R-1-abcdef1.json", good_digest))
        checks.append(("flipped byte under the original sha256 is refused with 0 writes",
                       rc == 5 and tree(lab) == before and "sha256 mismatch" in err, "rc %s err %r" % (rc, err[:100])))
        rc, out, err = run_ingest(pointer("p3.json", sha1, "evidence-packets/absent.json", good_digest))
        checks.append(("absent path is refused with 0 writes",
                       rc == 4 and tree(lab) == before and err.startswith("FETCH-FAILED"), "rc %s" % rc))
        rc, out, err = run_ingest(pointer("p4.json", sha1, "evidence-packets/leaking.json",
                                          hashlib.sha256(leak_bytes).hexdigest()))
        checks.append(("sha256-valid leaking packet is refused naming the failed check, 0 writes",
                       rc == 6 and tree(lab) == before and ("leak scan" in err or "allowlist" in err),
                       "rc %s err %r" % (rc, err[:120])))
        st = sh([VCS, "status", "--porcelain"], lab)[1]
        checks.append(("refusals leave the lab tree clean", st.strip() == "", repr(st[:80])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = 0
    for name, ok, detail in checks:
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  [%s]" % detail))
        failed += 0 if ok else 1
    print("evidence-ingest selftest: %d checks, %d failed" % (len(checks), failed))
    return 0 if failed == 0 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pointer", nargs="?")
    ap.add_argument("--lab", help="lab repository root (default: the current repository's top level)")
    ap.add_argument("--scratch-root", help="where the isolated fetch clone lives (removed after use)")
    ap.add_argument("--date", help="capture date YYYY-MM-DD for the raw filename (default today)")
    ap.add_argument("--selftest", action="store_true")
    o = ap.parse_args()
    if o.selftest:
        return _selftest(o.scratch_root)
    if not o.pointer:
        sys.stderr.write("USAGE: evidence-ingest.py <pointer.json> [--lab DIR] [--scratch-root DIR] [--date D]\n")
        return 2
    lab = o.lab
    if not lab:
        rc, top, _ = vcs(os.getcwd(), ["rev-parse", "--show-toplevel"])
        if rc != 0:
            sys.stderr.write("USAGE: --lab is required outside a repository\n")
            return 2
        lab = top
    lab = os.path.abspath(lab)
    date = o.date
    if date is None:
        import datetime
        date = datetime.date.today().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        sys.stderr.write("USAGE: --date must be YYYY-MM-DD\n")
        return 2
    scratch = o.scratch_root or os.path.join(os.path.expanduser("~"), ".claude", "evidence-ingest-scratch")
    try:
        return ingest(os.path.abspath(o.pointer), lab, scratch, date)
    except Refuse as r:
        sys.stderr.write("%s: %s\n" % (r.cls, r.detail))
        return r.code


if __name__ == "__main__":
    sys.exit(main())
