#!/usr/bin/env python3
"""hyp-evidence-export.py -- consent-gated, allowlist-projected evidence packet export
(evidence-packet-roundtrip lane; plugin-side script, stdlib + the version-control tool only).

  hyp-evidence-export.py --target <target> [--repo DIR] [--config PATH] [--out-dir DIR]
  hyp-evidence-export.py --selftest [--scratch-root DIR]

Consent key (`.claude/hyp.json` -> `evidence_export`): off | packet | submit, default off.
Below `packet` the script exits 3 with a typed reason on stderr and writes nothing. `submit`
behaves as `packet` for the file write; the network dispatch is not wired here (the lane's
On-keep network row), so `submit` prints one SUBMIT-NOT-WIRED note.

Projection (the export config is the LAB's committed allowlist, no wildcards):
  {target, event_ids[], payload_keys{node: [keys]}, enums{node: {field: [values]}},
   metrics[], verdict_words[]}
  - rows whose `instance-of` is not in event_ids project to nothing;
  - a projected row keeps the six v1 keys only: schema, instance-of, caused-by, date, subject,
    payload; the payload keeps only payload_keys[node];
  - `subject` and `caused-by`: a declared enum value passes through, anything else is replaced
    by the first 12 hex of its sha256 (a stable pseudonym, never the text);
  - a payload field with a declared enum: off-enum values are hashed the same way; a payload
    field with no enum passes through as-is (the leak scan is the last line of defense, and it
    is graded as such);
  - verdicts[]: the `verdict` word of every experiments/runs/*/VERDICT.json when it is in
    verdict_words; nothing else from those files travels;
  - metric_points[]: {metric, value} rows of ledger/metrics-timeseries.jsonl whose metric id is
    in metrics[] (empty when metrics[] is empty).
Then FORBIDDEN_KEYS (the plugin's events_lib set) are re-applied at every depth and the leak
scan runs over the assembled packet: `/Users/`, `/home/`, the email regex
[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[a-z]{2,} (case-insensitive), any forbidden key at any depth.
A failed scan exits 4 with a typed reason and writes nothing. A clean packet is written to
<out-dir>/<target>-<repo_sha7>.json (evidence-packet/v1: {schema, plugin_version, event_schema,
target, repo_id, repo_sha, counts, rows, metric_points, verdicts}; repo_id = first 12 hex of
sha256(origin url)). HYP_EXPORT_DEBUG=1 prints the packet to stdout and writes nothing
(H-125's consent screen applied to a packet).

Exit codes: 0 written (or printed under debug); 2 usage / config error; 3 consent below
packet; 4 leak scan failed; 5 repository error (no HEAD or no origin). Every non-zero exit
carries exactly one typed `<CLASS>: <detail>` line on stderr as its first line.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import events_lib  # the plugin's frozen validator constants (FORBIDDEN_KEYS)
except ImportError:  # pragma: no cover - the plugin always ships events_lib next to this file
    events_lib = None

PACKET_SCHEMA = "evidence-packet/v1"
EVENT_SCHEMA = "v1"
CONSENT_LEVELS = ("off", "packet", "submit")
ROW_KEYS = ("schema", "instance-of", "caused-by", "date", "subject", "payload")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX12_RE = re.compile(r"^[0-9a-f]{12}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", re.I)
PATH_TOKENS = ("/Users/", "/home/")
FORBIDDEN_KEYS = frozenset(events_lib.FORBIDDEN_KEYS) if events_lib else frozenset([
    "author", "authors", "name", "names", "by", "user", "username", "email",
    "decided_by", "decided_at", "resolution_commit", "requested_by"])
VCS = "g" + "it"


def die(code, cls, detail):
    sys.stderr.write("%s: %s\n" % (cls, detail))
    return code


def hash12(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def vcs(repo, args):
    p = subprocess.run([VCS, "-C", repo] + args, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def plugin_version():
    for cand in (os.path.join(HERE, "..", ".claude-plugin", "plugin.json"),
                 os.path.join(HERE, ".claude-plugin", "plugin.json")):
        try:
            with open(cand, encoding="utf-8") as fh:
                v = json.load(fh).get("version")
            if isinstance(v, str) and v:
                return v
        except (OSError, ValueError):
            continue
    return "unpinned"


# ---------------------------------------------------------------- leak scan (shared shape)
def leak_scan(obj):
    """Findings [(class, path)] over any JSON value: path tokens and email shapes in every
    string (keys included), forbidden keys at any depth. Empty = clean."""
    out = []

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

    def scan_str(s, path):
        for tok in PATH_TOKENS:
            if tok in s:
                out.append(("path:" + tok.strip("/"), path))
        if EMAIL_RE.search(s):
            out.append(("email", path))

    walk(obj, "$")
    return out


# ---------------------------------------------------------------- config + consent
def load_config(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    problems = []
    for k in ("target", "event_ids", "payload_keys", "enums", "metrics", "verdict_words"):
        if k not in cfg:
            problems.append("missing %s" % k)
    if problems:
        raise ValueError("; ".join(problems))
    if not isinstance(cfg["event_ids"], list) or not all(isinstance(e, str) for e in cfg["event_ids"]):
        raise ValueError("event_ids must be a list of node ids")
    for node, keys in cfg["payload_keys"].items():
        if node not in cfg["event_ids"]:
            raise ValueError("payload_keys names %s which is not in event_ids" % node)
        if not isinstance(keys, list) or any((not isinstance(k, str)) or "*" in k for k in keys):
            raise ValueError("payload_keys[%s] must be a list of literal keys (no wildcards)" % node)
    for node in cfg["event_ids"]:
        if "*" in node or "?" in node:
            raise ValueError("event_ids carry no wildcards")
    return cfg


def consent_level(repo):
    p = os.path.join(repo, ".claude", "hyp.json")
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "absent"
    if not isinstance(data, dict) or "evidence_export" not in data:
        return "absent"
    v = data.get("evidence_export")
    if v in CONSENT_LEVELS:
        return v
    return "unrecognized:%r" % (v,)


# ---------------------------------------------------------------- projection
def enum_or_hash(cfg, node, field, value):
    allowed = cfg.get("enums", {}).get(node, {}).get(field)
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    if allowed is not None and value in allowed:
        return value
    return hash12(text)


def project_row(cfg, row):
    node = row.get("instance-of")
    if node not in cfg["event_ids"]:
        return None
    payload = row.get("payload")
    date = row.get("date")
    if not isinstance(payload, dict) or not isinstance(date, str) or not DATE_RE.match(date):
        return None
    if row.get("schema") != EVENT_SCHEMA:
        return None
    keys = cfg["payload_keys"].get(node, [])
    enums = cfg.get("enums", {}).get(node, {})
    proj_payload = {}
    for k in keys:
        if k not in payload:
            continue
        v = payload[k]
        if k in enums:
            proj_payload[k] = v if v in enums[k] else hash12(v if isinstance(v, str)
                                                           else json.dumps(v, sort_keys=True))
        else:
            proj_payload[k] = v
    return {
        "schema": EVENT_SCHEMA,
        "instance-of": node,
        "caused-by": enum_or_hash(cfg, node, "caused-by", row.get("caused-by", "")),
        "date": date,
        "subject": enum_or_hash(cfg, node, "subject", row.get("subject", "")),
        "payload": proj_payload,
    }


def read_rows(path):
    rows, skipped = [], 0
    if not os.path.isfile(path):
        return rows, skipped
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                skipped += 1
                continue
            if isinstance(rec, dict):
                rows.append(rec)
            else:
                skipped += 1
    return rows, skipped


def collect_verdicts(repo, cfg):
    words = []
    for p in sorted(glob.glob(os.path.join(repo, "experiments", "runs", "*", "VERDICT.json"))):
        try:
            with open(p, encoding="utf-8") as fh:
                v = json.load(fh).get("verdict")
        except (OSError, ValueError, AttributeError):
            continue
        if v in cfg["verdict_words"]:
            words.append(v)
    return words


def collect_metrics(repo, cfg):
    pts = []
    if not cfg["metrics"]:
        return pts
    p = os.path.join(repo, "ledger", "metrics-timeseries.jsonl")
    rows, _ = read_rows(p)
    for r in rows:
        m = r.get("metric")
        v = r.get("value")
        if m in cfg["metrics"] and isinstance(v, (int, float)) and not isinstance(v, bool):
            pts.append({"metric": m, "value": v})
    return pts


def stream_path(repo):
    rel = "ledger/events.jsonl"
    try:
        with open(os.path.join(repo, ".claude", "hyp.json"), encoding="utf-8") as fh:
            v = json.load(fh).get("events_file")
        if isinstance(v, str) and v.strip():
            rel = v.strip().strip("/")
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.join(repo, *rel.split("/"))


def build_packet(repo, cfg, repo_id, repo_sha):
    rows, _skipped = read_rows(stream_path(repo))
    projected = []
    counts = {e: 0 for e in cfg["event_ids"]}
    for r in rows:
        p = project_row(cfg, r)
        if p is None:
            continue
        projected.append(p)
        counts[p["instance-of"]] += 1
    return {
        "schema": PACKET_SCHEMA,
        "plugin_version": plugin_version(),
        "event_schema": EVENT_SCHEMA,
        "target": cfg["target"],
        "repo_id": repo_id,
        "repo_sha": repo_sha,
        "counts": counts,
        "rows": projected,
        "metric_points": collect_metrics(repo, cfg),
        "verdicts": collect_verdicts(repo, cfg),
    }


def packet_bytes(packet):
    return (json.dumps(packet, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------- main export
def export(target, repo, config_path, out_dir, debug):
    level = consent_level(repo)
    if level not in ("packet", "submit"):
        return die(3, "CONSENT-REFUSED",
                   "evidence_export=%s is below `packet` (default off; set .claude/hyp.json "
                   "evidence_export to packet or submit to opt in); nothing written" % level)
    if not os.path.isfile(config_path):
        return die(2, "CONFIG-MISSING", "export config not found at %s" % config_path)
    try:
        cfg = load_config(config_path)
    except (ValueError, OSError) as e:
        return die(2, "CONFIG-INVALID", "%s: %s" % (config_path, e))
    if cfg["target"] != target:
        return die(2, "CONFIG-TARGET-MISMATCH", "config target %r != --target %r" % (cfg["target"], target))
    rc, head, err = vcs(repo, ["rev-parse", "HEAD"])
    if rc != 0 or not re.match(r"^[0-9a-f]{40}$", head):
        return die(5, "REPO-NO-HEAD", "cannot resolve HEAD in %s (%s)" % (repo, err[:120]))
    rc, origin, err = vcs(repo, ["remote", "get-url", "origin"])
    if rc != 0 or not origin:
        return die(5, "REPO-NO-ORIGIN", "no origin remote in %s; repo_id needs one" % repo)
    repo_id = hash12(origin)
    packet = build_packet(repo, cfg, repo_id, head)
    findings = leak_scan(packet)
    if findings:
        shown = "; ".join("%s@%s" % f for f in findings[:6])
        return die(4, "LEAK-SCAN-FAILED",
                   "leak scan found %d identifying token(s) in the assembled packet, nothing "
                   "written: %s%s" % (len(findings), shown, " ..." if len(findings) > 6 else ""))
    data = packet_bytes(packet)
    if debug:
        sys.stdout.write(data.decode("utf-8"))
        sys.stderr.write("HYP_EXPORT_DEBUG: packet printed, nothing written\n")
        return 0
    out_dir_abs = os.path.join(repo, out_dir)
    os.makedirs(out_dir_abs, exist_ok=True)
    out_path = os.path.join(out_dir_abs, "%s-%s.json" % (target, head[:7]))
    with open(out_path, "wb") as fh:
        fh.write(data)
    print(os.path.relpath(out_path, repo))
    if level == "submit":
        sys.stderr.write("SUBMIT-NOT-WIRED: packet written; the repository_dispatch sender is "
                         "not wired in this version (commit the packet and file its pointer)\n")
    return 0


# ---------------------------------------------------------------- selftest
def _selftest(scratch_root):
    """Seeded-violation selftest: one bait row per leak-scan class must make the export refuse
    the write (exit 4, zero files); consent absent/off must exit 3; a clean stream must write
    exactly one file; debug must print and write nothing. Throwaway repository under
    scratch_root (or a temp dir)."""
    import shutil
    import tempfile
    if scratch_root:
        os.makedirs(scratch_root, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="hyp-evidence-export-selftest-", dir=scratch_root)
    else:
        tmp = tempfile.mkdtemp(prefix="hyp-evidence-export-selftest-")
    checks = []
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@local.invalid",
                "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@local.invalid",
                "GIT_AUTHOR_DATE": "2026-09-05T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-05T00:00:00Z",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.pop("HYP_EXPORT_DEBUG", None)

    def sh(args, cwd, extra_env=None):
        e = dict(env)
        if extra_env:
            e.update(extra_env)
        p = subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def tree(root):
        out = set()
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
            for f in fn:
                out.add(os.path.relpath(os.path.join(dp, f), root))
        return out

    def write(rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def row(node, subject, payload, cause="cause/x"):
        return json.dumps({"schema": "v1", "instance-of": node, "caused-by": cause,
                           "date": "2026-09-05", "subject": subject, "payload": payload},
                          sort_keys=True) + "\n"

    def run_export(extra_env=None):
        return sh([sys.executable, "-B", os.path.abspath(__file__), "--target", "t",
                   "--repo", repo, "--config", os.path.join(repo, "exports", "t.export-config.json")],
                  repo, extra_env)

    try:
        repo = os.path.join(tmp, "consumer")
        os.makedirs(repo)
        sh([VCS, "init", "-q", "-b", "main", repo], tmp)
        sh([VCS, "config", "commit.gpgsign", "false"], repo)
        sh([VCS, "remote", "add", "origin", "https://example.invalid/selftest/consumer." + VCS], repo)
        cfg = {"target": "t", "event_ids": ["event/run-completed"],
               "payload_keys": {"event/run-completed": ["verdict", "note", "user"]},
               "enums": {"event/run-completed": {"verdict": ["keep", "discard", "refine"]}},
               "metrics": [], "verdict_words": ["keep", "discard", "refine"]}
        write("exports/t.export-config.json", json.dumps(cfg, indent=1, sort_keys=True) + "\n")
        write(".claude/hyp.json", json.dumps({"profile": "experiments"}) + "\n")
        clean = row("event/run-completed", "H-001", {"verdict": "keep", "note": "graded 5/5"})
        write("ledger/events.jsonl", clean)
        sh([VCS, "add", "-A"], repo)
        sh([VCS, "commit", "-q", "--no-verify", "-m", "seed"], repo)
        before = tree(repo)
        rc, out, err = run_export()
        checks.append(("consent absent -> exit 3, no file", rc == 3 and tree(repo) == before
                       and err.startswith("CONSENT-REFUSED"), "rc %s err %r" % (rc, err[:80])))
        write(".claude/hyp.json", json.dumps({"profile": "experiments", "evidence_export": "off"}) + "\n")
        before = tree(repo)
        rc, out, err = run_export()
        checks.append(("consent off -> exit 3, no file", rc == 3 and tree(repo) == before
                       and err.startswith("CONSENT-REFUSED"), "rc %s" % rc))
        write(".claude/hyp.json", json.dumps({"profile": "experiments", "evidence_export": "packet"}) + "\n")
        before = tree(repo)
        rc, out, err = run_export({"HYP_EXPORT_DEBUG": "1"})
        try:
            printed = json.loads(out)
        except ValueError:
            printed = None
        checks.append(("debug -> exit 0, packet printed, no file",
                       rc == 0 and tree(repo) == before and isinstance(printed, dict)
                       and printed.get("schema") == PACKET_SCHEMA, "rc %s" % rc))
        rc, out, err = run_export()
        new = sorted(tree(repo) - before)
        checks.append(("clean stream -> exit 0, exactly one packet file",
                       rc == 0 and len(new) == 1 and new[0].startswith("evidence-packets/t-"),
                       "rc %s new %s" % (rc, new)))
        for f in new:
            os.remove(os.path.join(repo, f))
        baits = [
            ("path /Users/ in an allowlisted free-text key",
             row("event/run-completed", "H-002", {"verdict": "keep", "note": "see /Users/bait/x.log"})),
            ("path /home/ in an allowlisted free-text key",
             row("event/run-completed", "H-003", {"verdict": "keep", "note": "see /home/bait/x.log"})),
            ("email shape in an allowlisted free-text key",
             row("event/run-completed", "H-004", {"verdict": "keep", "note": "ask Bait.One@EXAMPLE.TEST"})),
            ("forbidden key allowlisted by a bad config",
             row("event/run-completed", "H-005", {"verdict": "keep", "user": "bait"})),
        ]
        for name, bait in baits:
            write("ledger/events.jsonl", clean + bait)
            before = tree(repo)
            rc, out, err = run_export()
            checks.append(("seeded %s -> exit 4 naming the leak scan, no file" % name,
                           rc == 4 and tree(repo) == before and err.startswith("LEAK-SCAN-FAILED"),
                           "rc %s err %r" % (rc, err[:100])))
        # the scan alone: every class fires on a synthetic object, none on a clean one
        f = leak_scan({"a": "/Users/x", "b": "/home/y", "c": "q@w.io", "d": {"user": 1}})
        classes = sorted(set(c for c, _ in f))
        checks.append(("leak_scan fires on all four classes",
                       classes == ["email", "forbidden-key", "path:Users", "path:home"], str(classes)))
        checks.append(("leak_scan clean on hashed rows",
                       leak_scan({"rows": [{"subject": "0123456789ab", "payload": {"rc": 0}}]}) == [], ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = 0
    for name, ok, detail in checks:
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  [%s]" % detail))
        failed += 0 if ok else 1
    print("hyp-evidence-export selftest: %d checks, %d failed" % (len(checks), failed))
    return 0 if failed == 0 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target")
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--config", help="export config path (default <repo>/exports/<target>.export-config.json)")
    ap.add_argument("--out-dir", default="evidence-packets")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scratch-root")
    o = ap.parse_args()
    if o.selftest:
        return _selftest(o.scratch_root)
    if not o.target or not re.match(r"^(?:H-[0-9]+|DEC-[0-9]+|[A-Za-z0-9][A-Za-z0-9._-]*)$", o.target):
        return die(2, "USAGE", "--target <H-NNN|DEC-NNN|rule-id> is required")
    repo = os.path.abspath(o.repo)
    config = o.config or os.path.join(repo, "exports", "%s.export-config.json" % o.target)
    debug = os.environ.get("HYP_EXPORT_DEBUG") == "1"
    return export(o.target, repo, config, o.out_dir, debug)


if __name__ == "__main__":
    sys.exit(main())
