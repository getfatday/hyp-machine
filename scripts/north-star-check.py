#!/usr/bin/env python3
"""north-star-check.py -- the north-star file reader/linter (derived-condition-status lane).

A north-star file (ledger/north-stars/<slug>.md, template templates/north-star.md, convention
ledger/north-stars/README.md) lists the conditions that must be true to reach one declared
destination. It stores NO status. This reader derives every condition's status at read time
from the committed resolver state at one commit (HEAD by default, `--at <sha>` for replay):

  hypothesis  hypothesis-kept=H-NNN      done iff the spec's line-initial Status word is kept
              hypothesis-verdict=H-NNN   done iff kept OR discarded (the question is answered
                                         either way; PROPOSED additive predicate, evaluated
                                         here only -- scripts/closes_when.py gains it on keep)
              a `refined-into: H-MMM` Status is followed to the EFFECTIVE resolver H-MMM
  decision    decision-resolved=DEC-NNN  done iff an accepted|denied decision-resolution row
                                         for the id exists in ledger/work-ledger.jsonl
  capture     path-exists=research/raw/<file>            done iff committed at the commit
  probe       path-exists=experiments/runs/<lane>/VERDICT.json   done iff committed

Outcome (for outcome-conditioned `needs` tokens `C-NN:yes` / `C-NN:no`): hypothesis kept=yes,
discarded=no; decision accepted=yes, denied=no; probe VERDICT.json verdict pass|keep=yes,
fail|discard=no; capture=yes.

Derived-status vocabulary (never stored):
  done            the bound predicate is satisfied at the commit
  open            bound, not yet satisfied (whether or not its needs are met)
  retired:C-NN    an outcome-conditioned prerequisite C-NN resolved the other way, or a
                  prerequisite is itself retired (root id carried); retired precedes done
  unbound         the closes-when cell is `-` (counts in distance, never in the frontier)

frontier      open conditions whose every `needs` token is satisfied, in C-NN order, each
              with a resolver verb: register (hypothesis spec absent) / run (spec present) /
              add (decision row absent) / resolve (decision row open) / capture / probe
claimed_fresh frontier members filtered by a fresh experiments/runs/<lane>/LANE-STATE.json
              heartbeat (H-215/H-216: heartbeat_unix age <= ttl_s, default 1800); lane = the
              effective hypothesis id or the probe lane; working-tree overlay, never under --at
retired       the retired ids in order
distance      max over reached-when conditions of the count of open-or-unbound conditions
              on any `needs` path into it (longest-path DP, weight 1 per open/unbound node)
reached       every reached-when condition is done or retired

Lint classes (hard; `--strict` exits 1 when any fires): SCHEMA, DANGLING-REF, CYCLE,
STATUS-STORED. Advisory (never affects exit): HORIZON-AGED (a `## Horizon` line older than
%d days without a `-> C-NN` graduation).

Usage
  north-star-check.py [--repo R] [--at SHA] [--slug S | --file PATH] [--json] [--strict]
                      [--today YYYY-MM-DD] [--ttl-s N]
  north-star-check.py --selftest [--selftest-key KEY.json]

`--json` emits sorted keys (byte-stable across invocations at the same commit). `--file`
lints a working-tree file and derives against the repo at `--at`; without `--file`/`--slug`
every ledger/north-stars/*.md (README.md excluded) committed at the commit is read.
`--selftest` builds a throwaway git repo, plays the nine seeded verdict events, and asserts
vector, replay, zero-edit, lineage, retire, no-over-retire, claim-join, and SCHEMA-lint
checks against the embedded key (or `--selftest-key`, a harness override); exits 0 only when
every check passes. Stdlib + git only; no network; writes nothing outside the selftest tempdir.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HORIZON_AGE_DAYS = 60
DEFAULT_TTL_S = 1800
NORTH_STARS_DIR = "ledger/north-stars"
RESOLVER_KINDS = ("hypothesis", "decision", "capture", "probe")
VERB_BY_KIND = {"capture": "capture", "probe": "probe"}
COLUMNS = ["id", "condition", "resolver", "bound", "closes-when", "needs"]
GIT_TIMEOUT = 30

CID_RE = re.compile(r"^C-\d{2,}$")
NEED_RE = re.compile(r"^(C-\d{2,})(?::(yes|no))?$")
HID_RE = re.compile(r"^H-\d{3,}$")
DEC_RE = re.compile(r"^DEC-\d{3,}$")
LANE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
PRED_RE = re.compile(
    r"^(path-exists|commit-grep|hypothesis-kept|hypothesis-verdict|maintainer-ruling"
    r"|decision-resolved)=(.+)$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STATUS_HEADING_RE = re.compile(r"(?m)^##\s*Status\s*$")
NEXT_HEADING_RE = re.compile(r"(?m)^##\s")
HORIZON_RE = re.compile(r"^-\s*(Z-\d{2,})\s*\((\d{4}-\d{2}-\d{2})\):\s*(.+?)\s*$")
GRADUATE_RE = re.compile(r"->\s*(C-\d{2,})\s*$")
EXCLUDED_RE = re.compile(r"^-\s*(X-\d{2,}):\s*(.+?)\s+[-—]+\s*banks:\s*(\S.*?)\s*$")

__doc__ = __doc__ % HORIZON_AGE_DAYS


# ---------------------------------------------------------------- git plumbing -------------

class Repo(object):
    """Read-only, per-sha cached git reads."""

    def __init__(self, root):
        self.root = root
        self._cache = {}

    def git(self, *args):
        try:
            p = subprocess.run(["git", "-C", self.root] + list(args), capture_output=True,
                               text=True, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return 1, ""
        return p.returncode, p.stdout

    def resolve(self, ref):
        code, out = self.git("rev-parse", "--verify", "-q", ref + "^{commit}")
        return out.strip() if code == 0 else None

    def show(self, sha, path):
        key = ("show", sha, path)
        if key not in self._cache:
            code, out = self.git("show", "%s:%s" % (sha, path))
            self._cache[key] = out if code == 0 else None
        return self._cache[key]

    def exists(self, sha, path):
        key = ("exists", sha, path)
        if key not in self._cache:
            code, _ = self.git("cat-file", "-e", "%s:%s" % (sha, path))
            self._cache[key] = (code == 0)
        return self._cache[key]

    def ls(self, sha, prefix):
        key = ("ls", sha, prefix)
        if key not in self._cache:
            code, out = self.git("ls-tree", "-r", "--name-only", sha, "--", prefix)
            self._cache[key] = out.splitlines() if code == 0 else []
        return self._cache[key]

    def blob_sha(self, sha, path):
        code, out = self.git("rev-parse", "%s:%s" % (sha, path))
        return out.strip() if code == 0 else None

    def log_bodies(self, sha):
        key = ("log", sha)
        if key not in self._cache:
            code, out = self.git("log", "--format=%B%x00", sha)
            self._cache[key] = out if code == 0 else ""
        return self._cache[key]


# ---------------------------------------------------------------- parsing ------------------

def status_block(text):
    """Comment-stripped text under '## Status' (closes_when.extract_status_word lineage)."""
    m = STATUS_HEADING_RE.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    block = rest[:nxt.start()] if nxt else rest
    return HTML_COMMENT_RE.sub(" ", block).strip()


def status_word(text):
    block = status_block(text)
    return block.split()[0] if block else None


def refine_target(text):
    """H-MMM named by a refined / refined-into Status, else None."""
    block = status_block(text)
    if not block:
        return None
    first = block.split()[0].lower()
    if not first.startswith("refined"):
        return None
    m = re.search(r"\bH-\d{3,}\b", block)
    return m.group(0) if m else None


def split_row(line):
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return None
    return [c.strip() for c in s[1:-1].split("|")]


def parse_north_star(text, path):
    """-> dict(destination, reached_when, conditions[list of dict], horizon, excluded,
    findings[list of dict(class,line,message)])."""
    findings = []
    lines = text.splitlines()
    doc = {"path": path, "destination": None, "reached_when": [], "conditions": [],
           "horizon": [], "excluded": [], "findings": findings}

    def find(cls, lineno, msg):
        findings.append({"class": cls, "line": lineno, "message": msg})

    section = None
    header_seen = False
    header_cols = None
    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if line.startswith("destination:") and section is None:
            doc["destination"] = line[len("destination:"):].strip()
            continue
        if line.startswith("reached-when:") and section is None:
            toks = [t.strip() for t in line[len("reached-when:"):].split(",") if t.strip()]
            doc["reached_when"] = toks
            for t in toks:
                if not CID_RE.match(t):
                    find("SCHEMA", i, "reached-when token %r is not C-NN" % t)
            continue
        if section == "conditions":
            cells = split_row(line)
            if cells is None:
                continue
            if not header_seen:
                header_seen = True
                header_cols = [c.lower() for c in cells]
                if "status" in header_cols:
                    find("STATUS-STORED", i,
                         "an authored `status` column; status is derived, never stored")
                    header_cols = [c for c in header_cols if c != "status"]
                    doc["_status_col"] = [c.lower() for c in cells].index("status")
                if header_cols != COLUMNS:
                    find("SCHEMA", i, "header columns %r != %r" % (header_cols, COLUMNS))
                continue
            if all(set(c) <= set("-: ") for c in cells):
                continue  # separator row
            if "_status_col" in doc:
                cells = [c for k, c in enumerate(cells) if k != doc["_status_col"]]
            if len(cells) != len(COLUMNS):
                find("SCHEMA", i, "row has %d cells, expected %d" % (len(cells), len(COLUMNS)))
                continue
            cid, ctext, kind, bound, pred, needs = cells
            cond = {"id": cid, "text": ctext, "resolver": kind, "bound": bound,
                    "ref": pred, "needs": [], "line": i}
            if not CID_RE.match(cid):
                find("SCHEMA", i, "condition id %r is not C-NN" % cid)
            if any(c["id"] == cid for c in doc["conditions"]):
                find("SCHEMA", i, "duplicate condition id %s" % cid)
            if kind not in RESOLVER_KINDS:
                find("SCHEMA", i, "resolver kind %r not in %r" % (kind, RESOLVER_KINDS))
            else:
                ok = {"hypothesis": bool(HID_RE.match(bound)),
                      "decision": bool(DEC_RE.match(bound)),
                      "capture": bound.startswith("research/raw/") and len(bound) > 13,
                      "probe": bool(LANE_RE.match(bound))}[kind]
                if not ok:
                    find("SCHEMA", i, "bound %r malformed for resolver %s" % (bound, kind))
            if pred == "-":
                cond["ref"] = None
            else:
                m = PRED_RE.match(pred)
                if not m or not m.group(2).strip():
                    find("SCHEMA", i, "closes-when %r unparseable" % pred)
                elif kind in RESOLVER_KINDS:
                    pname, parg = m.group(1), m.group(2).strip()
                    want = {"hypothesis": (("hypothesis-kept", "hypothesis-verdict"), bound),
                            "decision": (("decision-resolved",), bound),
                            "capture": (("path-exists",), bound),
                            "probe": (("path-exists",),
                                      "experiments/runs/%s/VERDICT.json" % bound)}[kind]
                    if pname not in want[0] or parg != want[1]:
                        find("SCHEMA", i, "closes-when %r does not bind resolver %s %s"
                             % (pred, kind, bound))
            if needs != "-":
                for tok in [t.strip() for t in needs.split(",") if t.strip()]:
                    m = NEED_RE.match(tok)
                    if not m:
                        find("SCHEMA", i, "needs token %r malformed (C-NN or C-NN:yes|no)"
                             % tok)
                        continue
                    cond["needs"].append({"id": m.group(1), "outcome": m.group(2)})
            doc["conditions"].append(cond)
        elif section == "horizon":
            if not line.strip() or not line.startswith("-"):
                continue
            m = HORIZON_RE.match(line)
            if not m:
                find("SCHEMA", i, "horizon line malformed: '- Z-NN (YYYY-MM-DD): text'")
                continue
            entry = {"id": m.group(1), "date": m.group(2), "text": m.group(3),
                     "graduated_to": None, "line": i}
            g = GRADUATE_RE.search(m.group(3))
            if g:
                entry["graduated_to"] = g.group(1)
            doc["horizon"].append(entry)
        elif section == "excluded":
            if not line.strip() or not line.startswith("-"):
                continue
            m = EXCLUDED_RE.match(line)
            if not m:
                find("SCHEMA", i, "excluded line malformed: '- X-NN: text -- banks: <ref>'")
                continue
            doc["excluded"].append({"id": m.group(1), "text": m.group(2),
                                    "banks": m.group(3), "line": i})
    doc.pop("_status_col", None)

    if doc["destination"] is None:
        find("SCHEMA", 0, "missing `destination:` line")
    elif len(doc["destination"].split()) > 25:
        find("SCHEMA", 0, "destination exceeds 25 words")
    if not header_seen:
        find("SCHEMA", 0, "missing `## Conditions` table")
    if not doc["reached_when"]:
        find("SCHEMA", 0, "missing or empty `reached-when:` line")

    ids = set(c["id"] for c in doc["conditions"])
    for t in doc["reached_when"]:
        if CID_RE.match(t) and t not in ids:
            find("DANGLING-REF", 0, "reached-when names unknown condition %s" % t)
    for c in doc["conditions"]:
        for n in c["needs"]:
            if n["id"] not in ids:
                find("DANGLING-REF", c["line"], "%s needs unknown condition %s"
                     % (c["id"], n["id"]))
    for h in doc["horizon"]:
        if h["graduated_to"] and h["graduated_to"] not in ids:
            find("DANGLING-REF", h["line"], "%s graduates to unknown condition %s"
                 % (h["id"], h["graduated_to"]))

    # cycle detection over needs edges (only among known ids)
    graph = {c["id"]: [n["id"] for n in c["needs"] if n["id"] in ids]
             for c in doc["conditions"]}
    state = {}
    stack = []

    def visit(v):
        state[v] = 1
        stack.append(v)
        for w in graph.get(v, []):
            if state.get(w) == 1:
                cyc = stack[stack.index(w):] + [w]
                return cyc
            if state.get(w) is None:
                r = visit(w)
                if r:
                    return r
        stack.pop()
        state[v] = 2
        return None

    for v in sorted(graph):
        if state.get(v) is None:
            cyc = visit(v)
            if cyc:
                find("CYCLE", 0, "needs cycle: %s" % " -> ".join(cyc))
                break
    return doc


# ---------------------------------------------------------------- resolvers ----------------

def hypothesis_state(repo, sha, hid, seen=None):
    """-> dict(effective, present, word, resolved_kept, resolved_verdict, outcome)."""
    seen = seen or []
    if hid in seen:  # lineage loop guard
        return {"effective": hid, "present": False, "word": None}
    seen = seen + [hid]
    pat = re.compile(r"^hypotheses/%s-.*\.md$" % re.escape(hid))
    matches = [p for p in repo.ls(sha, "hypotheses") if pat.match(p)]
    if len(matches) != 1:
        return {"effective": hid, "present": False, "word": None}
    text = repo.show(sha, matches[0]) or ""
    word = status_word(text)
    tgt = refine_target(text)
    if tgt:
        return hypothesis_state(repo, sha, tgt, seen)
    return {"effective": hid, "present": True, "word": (word or "").lower()}


def decision_state(repo, sha, dec):
    """-> dict(present, disposition) from ledger/work-ledger.jsonl at sha."""
    raw = repo.show(sha, "ledger/work-ledger.jsonl") or ""
    present, disposition = False, None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("id") != dec:
            continue
        if rec.get("kind") == "decision":
            present = True
        elif rec.get("kind") == "decision-resolution":
            present = True
            if rec.get("disposition") in ("accepted", "denied"):
                disposition = rec.get("disposition")
    return {"present": present, "disposition": disposition}


def probe_outcome(repo, sha, lane):
    raw = repo.show(sha, "experiments/runs/%s/VERDICT.json" % lane)
    if raw is None:
        return None
    try:
        v = str(json.loads(raw).get("verdict", "")).lower()
    except (ValueError, AttributeError):
        return "yes"
    if v in ("fail", "failed", "discard", "no"):
        return "no"
    return "yes"


def evaluate_condition(repo, sha, cond):
    """-> dict(resolved, outcome, effective, verb, lane) for one bound condition."""
    kind, bound, ref = cond["resolver"], cond["bound"], cond["ref"]
    out = {"resolved": False, "outcome": None, "effective": bound, "verb": None,
           "lane": None}
    if ref is None:
        return out
    pname, parg = ref.split("=", 1)
    if kind == "hypothesis":
        st = hypothesis_state(repo, sha, bound)
        out["effective"] = st["effective"]
        out["lane"] = st["effective"]
        word = st.get("word") or ""
        kept = word == "kept"
        discarded = word.startswith("discarded")
        if pname == "hypothesis-kept":
            out["resolved"] = kept
        else:
            out["resolved"] = kept or discarded
        if kept:
            out["outcome"] = "yes"
        elif discarded:
            out["outcome"] = "no"
        out["verb"] = "run" if st["present"] else "register"
    elif kind == "decision":
        st = decision_state(repo, sha, bound)
        out["resolved"] = st["disposition"] is not None
        out["outcome"] = {"accepted": "yes", "denied": "no", None: None}[st["disposition"]]
        out["verb"] = "resolve" if st["present"] else "add"
    elif kind == "capture":
        out["resolved"] = repo.exists(sha, parg)
        out["outcome"] = "yes" if out["resolved"] else None
        out["verb"] = "capture"
    elif kind == "probe":
        out["resolved"] = repo.exists(sha, parg)
        out["outcome"] = probe_outcome(repo, sha, bound) if out["resolved"] else None
        out["verb"] = "probe"
        out["lane"] = bound
    return out


def claim_state(repo_root, lane, ttl_default, now):
    """H-215/H-216 claim join over the WORKING TREE: 'fresh' | 'stale' | None."""
    if not lane:
        return None
    p = os.path.join(repo_root, "experiments", "runs", lane, "LANE-STATE.json")
    try:
        with open(p, encoding="utf-8") as fh:
            claim = json.load(fh)
    except (OSError, ValueError):
        return None
    hb = claim.get("heartbeat_unix") if isinstance(claim, dict) else None
    if not isinstance(hb, (int, float)):
        return None
    ttl = claim.get("ttl_s") if isinstance(claim.get("ttl_s"), (int, float)) else ttl_default
    return "fresh" if (now - hb) <= ttl else "stale"


# ---------------------------------------------------------------- derivation ---------------

def derive(doc, repo, sha, live_root=None, ttl_s=DEFAULT_TTL_S, now=None, today=None):
    """Attach derived fields to doc (mutates and returns it). live_root enables the
    working-tree claim overlay (never under --at)."""
    hard = [f for f in doc["findings"] if f["class"] != "HORIZON-AGED"]
    doc["advisories"] = []
    if today is None:
        today = datetime.date.today()
    for h in doc["horizon"]:
        try:
            d = datetime.date.fromisoformat(h["date"])
        except ValueError:
            continue
        if not h["graduated_to"] and (today - d).days > HORIZON_AGE_DAYS:
            doc["advisories"].append({"class": "HORIZON-AGED", "line": h["line"],
                                      "message": "%s has aged %d days without graduation"
                                      % (h["id"], (today - d).days)})
    doc["derived"] = not hard
    if hard:
        doc["frontier"], doc["claimed_fresh"], doc["retired"] = [], [], []
        doc["distance"], doc["reached"] = None, None
        return doc

    by_id = {c["id"]: c for c in doc["conditions"]}
    for c in doc["conditions"]:
        c.update(evaluate_condition(repo, sha, c))

    # retire: outcome-conditioned prerequisite resolved the other way, or a retired prerequisite
    retired = {}
    changed = True
    while changed:
        changed = False
        for c in doc["conditions"]:
            if c["id"] in retired:
                continue
            for n in c["needs"]:
                pre = by_id[n["id"]]
                root = None
                if n["id"] in retired:
                    root = retired[n["id"]]
                elif n["outcome"] and pre["resolved"] and pre["outcome"] and \
                        pre["outcome"] != n["outcome"]:
                    root = n["id"]
                if root:
                    retired[c["id"]] = root
                    changed = True
                    break

    for c in doc["conditions"]:
        if c["id"] in retired:
            c["status"] = "retired:%s" % retired[c["id"]]
        elif c["ref"] is None:
            c["status"] = "unbound"
        elif c["resolved"]:
            c["status"] = "done"
        else:
            c["status"] = "open"

    def need_met(n):
        pre = by_id[n["id"]]
        if pre["status"] != "done":
            return False
        return n["outcome"] is None or pre["outcome"] == n["outcome"]

    now = time.time() if now is None else now
    frontier, claimed = [], []
    for c in doc["conditions"]:
        c["needs_met"] = all(need_met(n) for n in c["needs"])
        c["in_frontier"] = False
        c["claimed_fresh"] = False
        if c["status"] != "open" or not c["needs_met"]:
            c["verb"] = c["verb"] if c["status"] == "open" else None
            continue
        if live_root and claim_state(live_root, c.get("lane"), ttl_s, now) == "fresh":
            c["claimed_fresh"] = True
            claimed.append({"id": c["id"], "lane": c["lane"]})
        else:
            c["in_frontier"] = True
            frontier.append({"id": c["id"], "verb": c["verb"], "resolver": c["resolver"],
                             "bound": c["bound"], "effective": c["effective"]})
    doc["frontier"] = frontier
    doc["claimed_fresh"] = claimed
    doc["retired"] = [c["id"] for c in doc["conditions"] if c["id"] in retired]

    # distance: longest weighted needs path into any reached-when condition
    memo = {}

    def longest(cid):
        if cid in memo:
            return memo[cid]
        c = by_id[cid]
        w = 1 if c["status"] in ("open", "unbound") else 0
        best = 0
        for n in c["needs"]:
            best = max(best, longest(n["id"]))
        memo[cid] = w + best
        return memo[cid]

    doc["distance"] = max([longest(t) for t in doc["reached_when"]] or [0])
    doc["reached"] = all(by_id[t]["status"] == "done" or by_id[t]["status"].startswith(
        "retired:") for t in doc["reached_when"])
    return doc


def slug_of(path):
    return os.path.splitext(os.path.basename(path))[0]


def north_star_paths(repo, sha):
    return sorted(p for p in repo.ls(sha, NORTH_STARS_DIR)
                  if p.endswith(".md") and os.path.basename(p) != "README.md")


def check(repo, sha, targets, live_root=None, ttl_s=DEFAULT_TTL_S, now=None, today=None):
    """targets: list of (path, text). -> report dict."""
    stars = []
    for path, text in targets:
        doc = parse_north_star(text, path)
        derive(doc, repo, sha, live_root=live_root, ttl_s=ttl_s, now=now, today=today)
        doc["slug"] = slug_of(path)
        stars.append(doc)
    findings = []
    for s in stars:
        for f in s["findings"]:
            findings.append(dict(f, north_star=s["slug"]))
    return {"at": sha, "north_stars": stars, "findings": findings,
            "exit_strict": 1 if findings else 0}


def render_json(report):
    clean = json.loads(json.dumps(report))
    for s in clean["north_stars"]:
        for c in s["conditions"]:
            c.pop("line", None)
        for h in s["horizon"]:
            h.pop("line", None)
        for x in s["excluded"]:
            x.pop("line", None)
    return json.dumps(clean, indent=1, sort_keys=True) + "\n"


def render_text(report):
    out = []
    for s in report["north_stars"]:
        out.append("north-star %s @ %s" % (s["slug"], report["at"][:12]))
        out.append("  destination: %s" % (s["destination"] or "<missing>"))
        for f in s["findings"]:
            out.append("  %s L%s: %s" % (f["class"], f["line"], f["message"]))
        for a in s.get("advisories", []):
            out.append("  advisory %s L%s: %s" % (a["class"], a["line"], a["message"]))
        if not s.get("derived"):
            out.append("  (not derived: hard findings)")
            continue
        for c in s["conditions"]:
            eff = "" if c["effective"] == c["bound"] else " -> %s" % c["effective"]
            out.append("  %s %-12s %s %s%s" % (c["id"], c["status"], c["resolver"],
                                                c["bound"], eff))
        out.append("  frontier: %s" % ", ".join("%s(%s)" % (f["id"], f["verb"])
                                                  for f in s["frontier"]) or "  frontier: -")
        if s["claimed_fresh"]:
            out.append("  claimed_fresh: %s" % ", ".join(
                "%s@%s" % (c["id"], c["lane"]) for c in s["claimed_fresh"]))
        out.append("  retired: %s" % (", ".join(s["retired"]) or "-"))
        out.append("  distance: %s  reached: %s" % (s["distance"], s["reached"]))
    if not report["north_stars"]:
        out.append("no north-star files under %s at %s" % (NORTH_STARS_DIR, report["at"][:12]))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- selftest scenario --------

SCENARIO_DATE = "2026-09-04"
SCENARIO_IDENT = {"GIT_AUTHOR_NAME": "ncs-minilab", "GIT_AUTHOR_EMAIL": "lab@ncs.local",
                  "GIT_COMMITTER_NAME": "ncs-minilab", "GIT_COMMITTER_EMAIL": "lab@ncs.local"}


def _spec(hid, slug, title, status):
    return ("# %s-%s: %s\n\n## Status\n%s\n\n## Hypothesis\nSeeded stub for the "
            "derived-condition-status mini-lab; the resolver state is the whole point.\n"
            % (hid, slug, title, status))


NORTH_STAR_FIXTURE = """# North star: fixture-destination

destination: Every condition's state reads from committed verdicts; nobody hand-edits a status column anywhere in the lab.
reached-when: C-02, C-06, C-08, C-09, C-10, C-11, C-12

## Conditions

| id | condition | resolver | bound | closes-when | needs |
|---|---|---|---|---|---|
| C-01 | alpha channel keeps | hypothesis | H-901 | hypothesis-kept=H-901 | - |
| C-02 | beta cache keeps (lineage follows a refine) | hypothesis | H-902 | hypothesis-kept=H-902 | - |
| C-03 | gamma sweep question answered either way | hypothesis | H-903 | hypothesis-verdict=H-903 | - |
| C-04 | gamma follow-up captured | capture | research/raw/2026-09-04-fixture-capture.md | path-exists=research/raw/2026-09-04-fixture-capture.md | C-03:yes |
| C-05 | gamma rollout decided | decision | DEC-902 | decision-resolved=DEC-902 | C-04 |
| C-06 | gamma rollout probed | probe | P-902 | path-exists=experiments/runs/P-902/VERDICT.json | C-05 |
| C-07 | delta resolver question answered either way | hypothesis | H-904 | hypothesis-verdict=H-904 | - |
| C-08 | delta successor registered and kept | hypothesis | H-907 | hypothesis-kept=H-907 | C-07:yes |
| C-09 | delta ship decided | decision | DEC-903 | decision-resolved=DEC-903 | C-07:yes |
| C-10 | delta follow-up captured | capture | research/raw/2026-09-04-h907-followup.md | path-exists=research/raw/2026-09-04-h907-followup.md | C-07:yes |
| C-11 | alpha publish decided | decision | DEC-901 | decision-resolved=DEC-901 | C-01 |
| C-12 | alpha probe verdict recorded | probe | P-901 | path-exists=experiments/runs/P-901/VERDICT.json | - |

## Horizon
- Z-01 (2026-09-04): whether the frontier joins the dispatch surface

## Excluded
- X-01: compiled per-decision prose records — banks: H-242
- X-02: per-scope vision files — banks: H-244
- X-03: north-star prose as a steering surface — banks: H-245
"""

MINILAB_README = """# mini-lab (derived-condition-status fixture spine)

A scratch research mini-lab: hypothesis stubs under `hypotheses/` (line-initial `## Status`
word is the verdict: draft / kept / discarded / refined-into: H-NNN), decision rows and their
resolution rows in `ledger/work-ledger.jsonl` (a decision is resolved by an accepted|denied
`decision-resolution` row; `commented` leaves it open), research captures under
`research/raw/`, probe lanes under `experiments/runs/<lane>/` (a probe lands its
`VERDICT.json`), and one north-star file under `ledger/north-stars/` -- see
`ledger/north-stars/README.md` for the condition grammar.
"""

NORTH_STARS_README_LAB = """# ledger/north-stars/ (mini-lab copy of the convention)

One file per destination. `destination:` (<= 25 words), `reached-when:` (condition ids that
must all be done or retired), then a `## Conditions` table with the columns
`id | condition | resolver | bound | closes-when | needs`:

- resolver: hypothesis (bound H-NNN; closes-when hypothesis-kept=H-NNN or
  hypothesis-verdict=H-NNN, the latter satisfied by kept OR discarded), decision (bound
  DEC-NNN; decision-resolved=DEC-NNN, satisfied by accepted OR denied), capture (bound
  research/raw/<file>; path-exists=<that path>), probe (bound <lane>;
  path-exists=experiments/runs/<lane>/VERDICT.json).
- needs: `-`, or comma-separated `C-NN` (prerequisite must be done) and outcome-conditioned
  `C-NN:yes` / `C-NN:no` (prerequisite must resolve that way; if it resolves the other way
  this condition is retired -- moot -- along with everything that needs it).
- A hypothesis whose Status reads `refined-into: H-MMM` hands its condition to H-MMM.

`## Horizon` lines `- Z-NN (date): text` hold questions not yet sharp enough to be a
condition; `## Excluded` lines `- X-NN: text — banks: <ref>` are terminal exclusions.
"""

WORK_LEDGER_SPINE = "\n".join(json.dumps(r, sort_keys=True) for r in [
    {"kind": "decision", "id": "DEC-901", "date": SCENARIO_DATE,
     "title": "Publish the alpha channel", "class": "publish", "urgency": "normal"},
    {"kind": "decision", "id": "DEC-902", "date": SCENARIO_DATE,
     "title": "Roll gamma out", "class": "plan", "urgency": "normal"},
    {"kind": "decision", "id": "DEC-903", "date": SCENARIO_DATE,
     "title": "Ship delta", "class": "publish", "urgency": "normal"},
]) + "\n"


def _resolution(dec, disposition, comment):
    return json.dumps({"kind": "decision-resolution", "id": dec, "date": SCENARIO_DATE,
                       "disposition": disposition, "comment": comment}, sort_keys=True) + "\n"


SCENARIO = {
    "name": "derived-condition-status/fixture-destination",
    "north_star": "ledger/north-stars/fixture-destination.md",
    "spine_date": SCENARIO_DATE + "T00:00:00Z",
    "spine_message": "spine: seeded mini-lab (derived-condition-status)",
    "spine": {
        "README.md": MINILAB_README,
        ".gitignore": ".ncs-runtime/\n",
        "ledger/north-stars/README.md": NORTH_STARS_README_LAB,
        "ledger/north-stars/fixture-destination.md": NORTH_STAR_FIXTURE,
        "ledger/work-ledger.jsonl": WORK_LEDGER_SPINE,
        "hypotheses/H-901-alpha-channel.md": _spec("H-901", "alpha-channel",
                                                   "alpha channel", "draft"),
        "hypotheses/H-902-beta-cache.md": _spec("H-902", "beta-cache", "beta cache", "draft"),
        "hypotheses/H-903-gamma-sweep.md": _spec("H-903", "gamma-sweep", "gamma sweep",
                                                 "draft"),
        "hypotheses/H-904-delta-resolver.md": _spec("H-904", "delta-resolver",
                                                    "delta resolver", "draft"),
        "research/raw/.gitkeep": "",
        "experiments/runs/.gitkeep": "",
    },
    "events": [
        {"n": 1, "label": "H-901 kept",
         "message": "H-901 KEPT (2x 5/5)",
         "write": {"hypotheses/H-901-alpha-channel.md": _spec(
             "H-901", "alpha-channel", "alpha channel",
             "kept <!-- 2026-09-04: run-1 5/5, run-2 5/5 -->")}},
        {"n": 2, "label": "H-903 discarded",
         "message": "H-903 DISCARDED (3 failed counted runs)",
         "write": {"hypotheses/H-903-gamma-sweep.md": _spec(
             "H-903", "gamma-sweep", "gamma sweep",
             "discarded <!-- 2026-09-04: 3/3 failed at 2/5 -->")}},
        {"n": 3, "label": "H-902 refined into H-905",
         "message": "H-902 refined into H-905 (spec bug in the grader contract)",
         "write": {"hypotheses/H-902-beta-cache.md": _spec(
             "H-902", "beta-cache", "beta cache",
             "refined-into: H-905 <!-- 2026-09-04: run-1 4/5, grader contract bug -->"),
             "hypotheses/H-905-beta-cache-v2.md": _spec(
             "H-905", "beta-cache-v2", "beta cache v2", "draft")}},
        {"n": 4, "label": "DEC-901 accepted",
         "message": "decision: DEC-901 accepted — decision-resolved=DEC-901",
         "append": {"ledger/work-ledger.jsonl": _resolution("DEC-901", "accepted",
                                                            "publish it")}},
        {"n": 5, "label": "DEC-902 denied",
         "message": "decision: DEC-902 denied — decision-resolved=DEC-902",
         "append": {"ledger/work-ledger.jsonl": _resolution("DEC-902", "denied",
                                                            "gamma fell with H-903")}},
        {"n": 6, "label": "DEC-903 commented",
         "message": "decision: DEC-903 commented",
         "append": {"ledger/work-ledger.jsonl": _resolution("DEC-903", "commented",
                                                            "wait for H-904")}},
        {"n": 7, "label": "research capture",
         "message": "capture: research/raw/2026-09-04-fixture-capture.md",
         "write": {"research/raw/2026-09-04-fixture-capture.md":
                   "# Fixture capture (verbatim source)\n\nGamma follow-up notes.\n"}},
        {"n": 8, "label": "probe P-901 verdict",
         "message": "probe P-901: VERDICT.json lands",
         "write": {"experiments/runs/P-901/VERDICT.json": json.dumps(
             {"lane": "P-901", "verdict": "pass", "date": SCENARIO_DATE},
             sort_keys=True) + "\n"}},
        {"n": 9, "label": "H-904 kept",
         "message": "H-904 KEPT (2x 5/5)",
         "write": {"hypotheses/H-904-delta-resolver.md": _spec(
             "H-904", "delta-resolver", "delta resolver",
             "kept <!-- 2026-09-04: run-1 5/5, run-2 5/5 -->")}},
    ],
}

# Seeded malformed files (each violates exactly one rule).
MALFORMED = {
    "bad-outcome-token": ("SCHEMA", NORTH_STAR_FIXTURE.replace("| C-03:yes |", "| C-03:maybe |")),
    "dangling-ref": ("DANGLING-REF", NORTH_STAR_FIXTURE.replace("| C-03:yes |", "| C-99:yes |")),
    "needs-cycle": ("CYCLE", NORTH_STAR_FIXTURE.replace(
        "| hypothesis-kept=H-901 | - |", "| hypothesis-kept=H-901 | C-11 |")),
}


def _add_status_column(text):
    out = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("## Conditions"):
            in_table = True
        elif line.startswith("## "):
            in_table = False
        if in_table and line.startswith("|"):
            if line.startswith("| id |"):
                line = line + " status |"
            elif set(line) <= set("|-: "):
                line = line + "---|"
            else:
                line = line + " open |"
        out.append(line)
    return "\n".join(out) + "\n"


MALFORMED["status-stored"] = ("STATUS-STORED", _add_status_column(NORTH_STAR_FIXTURE))


def scenario_env(date_iso):
    env = dict(os.environ)
    env.update(SCENARIO_IDENT)
    env["GIT_AUTHOR_DATE"] = date_iso
    env["GIT_COMMITTER_DATE"] = date_iso
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
    env["GIT_CONFIG_VALUE_0"] = "false"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def _run_git(dest, args, env):
    p = subprocess.run(["git", "-C", dest] + args, capture_output=True, text=True,
                       timeout=60, env=env)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (args[:2], p.stderr.strip()[:300]))
    return p.stdout


def _write(dest, rel, content, append=False):
    p = os.path.join(dest, rel)
    os.makedirs(os.path.dirname(p) or dest, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)


def scenario_build(dest, scenario=SCENARIO, north_star_override=None):
    """Build the spine commit at dest (an empty or absent directory). Returns spine sha.
    north_star_override replaces the north-star file bytes (the OFF arm's status column)."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    env = scenario_env(scenario["spine_date"])
    subprocess.run(["git", "init", "-q", "-b", "main", dest], capture_output=True, text=True,
                   timeout=60, env=env, check=True)
    for k, v in (("user.name", SCENARIO_IDENT["GIT_AUTHOR_NAME"]),
                 ("user.email", SCENARIO_IDENT["GIT_AUTHOR_EMAIL"]),
                 ("commit.gpgsign", "false"), ("core.hooksPath", ".git/no-git-hooks")):
        _run_git(dest, ["config", k, v], env)
    for rel, content in scenario["spine"].items():
        if rel == scenario["north_star"] and north_star_override is not None:
            content = north_star_override
        _write(dest, rel, content)
    _run_git(dest, ["add", "-A"], env)
    _run_git(dest, ["commit", "-q", "--no-verify", "-m", scenario["spine_message"]], env)
    return _run_git(dest, ["rev-parse", "HEAD"], env).strip()


def scenario_play(dest, n, scenario=SCENARIO):
    """Commit event n (1-based) at dest. Returns the commit sha."""
    ev = scenario["events"][n - 1]
    date_iso = "%sT00:%02d:00Z" % (SCENARIO_DATE, n)
    env = scenario_env(date_iso)
    for rel, content in ev.get("write", {}).items():
        _write(dest, rel, content)
    for rel, content in ev.get("append", {}).items():
        _write(dest, rel, content, append=True)
    _run_git(dest, ["add", "-A"], env)
    _run_git(dest, ["commit", "-q", "--no-verify", "-m", ev["message"]], env)
    return _run_git(dest, ["rev-parse", "HEAD"], env).strip()


# Embedded answer key for the selftest (hand-derived from SCENARIO; see fixture/keys/key.json
# for the harness copy with per-event shas).
def _vec(**over):
    v = {"C-%02d" % i: "open" for i in range(1, 13)}
    v.update(over)
    return v


_R = {"C-04": "retired:C-03", "C-05": "retired:C-03", "C-06": "retired:C-03"}
SELFTEST_KEY = {
    "north_star": SCENARIO["north_star"],
    "spine": {"status": _vec(), "frontier": [["C-01", "run"], ["C-02", "run"], ["C-03", "run"],
                                            ["C-07", "run"], ["C-12", "probe"]],
              "retired": [], "distance": 4, "reached": False},
    "events": [
        {"n": 1, "status": _vec(**{"C-01": "done"}),
         "frontier": [["C-02", "run"], ["C-03", "run"], ["C-07", "run"], ["C-11", "resolve"],
                      ["C-12", "probe"]], "retired": [], "distance": 4, "reached": False},
        {"n": 2, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"], ["C-11", "resolve"], ["C-12", "probe"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False},
        {"n": 3, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"], ["C-11", "resolve"], ["C-12", "probe"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False,
         "effective": {"C-02": "H-905"}},
        {"n": 4, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done", "C-11": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"], ["C-12", "probe"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False},
        {"n": 5, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done", "C-11": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"], ["C-12", "probe"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False,
         "resolved": {"C-05": True}, "outcome": {"C-05": "no"}},
        {"n": 6, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done", "C-11": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"], ["C-12", "probe"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False,
         "resolved": {"C-09": False}},
        {"n": 7, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done", "C-11": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"], ["C-12", "probe"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False,
         "resolved": {"C-04": True}},
        {"n": 8, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done", "C-11": "done",
                                             "C-12": "done"})),
         "frontier": [["C-02", "run"], ["C-07", "run"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 2, "reached": False,
         "outcome": {"C-12": "yes"}},
        {"n": 9, "status": _vec(**dict(_R, **{"C-01": "done", "C-03": "done", "C-07": "done",
                                             "C-11": "done", "C-12": "done"})),
         "frontier": [["C-02", "run"], ["C-08", "register"], ["C-09", "resolve"],
                      ["C-10", "capture"]],
         "retired": ["C-04", "C-05", "C-06"], "distance": 1, "reached": False,
         "outcome": {"C-07": "yes"}},
    ],
    "discard_event": 2, "distance_drop_at_discard": 2,
    "refine_event": 3, "refine_effective": {"C-02": "H-905"}, "refine_verb": {"C-02": "run"},
    "second_keep_event": 9, "second_keep_entrants": ["C-08", "C-09", "C-10"],
    "claim": {"lane": "H-905", "condition": "C-02"},
}


def compare_to_key(star, expected):
    """-> list of mismatch strings between one derived star and one key entry."""
    bad = []
    got_status = {c["id"]: c["status"] for c in star["conditions"]}
    if got_status != expected["status"]:
        bad.append("status vector: got %s" % json.dumps(got_status, sort_keys=True))
    got_frontier = [[f["id"], f["verb"]] for f in star["frontier"]]
    if got_frontier != expected["frontier"]:
        bad.append("frontier: got %s" % got_frontier)
    if star["retired"] != expected["retired"]:
        bad.append("retired: got %s" % star["retired"])
    if star["distance"] != expected["distance"]:
        bad.append("distance: got %s" % star["distance"])
    if star["reached"] != expected["reached"]:
        bad.append("reached: got %s" % star["reached"])
    by_id = {c["id"]: c for c in star["conditions"]}
    for cid, eff in expected.get("effective", {}).items():
        if by_id[cid]["effective"] != eff:
            bad.append("effective %s: got %s" % (cid, by_id[cid]["effective"]))
    for cid, val in expected.get("resolved", {}).items():
        if by_id[cid]["resolved"] != val:
            bad.append("resolved %s: got %s" % (cid, by_id[cid]["resolved"]))
    for cid, val in expected.get("outcome", {}).items():
        if by_id[cid]["outcome"] != val:
            bad.append("outcome %s: got %s" % (cid, by_id[cid]["outcome"]))
    return bad


def selftest(key_path=None):
    key = SELFTEST_KEY
    if key_path:
        with open(key_path, encoding="utf-8") as fh:
            key = json.load(fh)
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="north-star-selftest-")
    try:
        lab = os.path.join(tmp, "lab")
        spine = scenario_build(lab)
        repo = Repo(lab)
        ns = SCENARIO["north_star"]
        today = datetime.date.fromisoformat(SCENARIO_DATE)
        shas = [spine]
        renders = {}

        def read(sha, live=False):
            text = repo.show(sha, ns)
            return check(repo, sha, [(ns, text)], live_root=lab if live else None,
                         today=today)

        rep = read(spine)
        renders[spine] = render_json(rep)
        star = rep["north_stars"][0]
        ok("spine lints clean", not star["findings"], json.dumps(star["findings"]))
        bad = compare_to_key(star, key["spine"])
        ok("spine parity", not bad, "; ".join(bad))
        if "sha" in key["spine"]:
            ok("spine sha", key["spine"]["sha"] == spine, spine)
        blob0 = repo.blob_sha(spine, ns)
        prev_distance = star["distance"]
        for ev in SCENARIO["events"]:
            n = ev["n"]
            sha = scenario_play(lab, n)
            shas.append(sha)
            rep = read(sha)
            r1 = render_json(rep)
            r2 = render_json(read(sha))
            renders[sha] = r1
            star = rep["north_stars"][0]
            exp = key["events"][n - 1]
            bad = compare_to_key(star, exp)
            ok("event %d parity (%s)" % (n, ev["label"]), not bad, "; ".join(bad))
            ok("event %d replay byte-identical" % n, r1 == r2)
            if "sha" in exp:
                ok("event %d sha" % n, exp["sha"] == sha, sha)
            ok("event %d zero-edit blob" % n, repo.blob_sha(sha, ns) == blob0)
            if n == key["discard_event"]:
                drop = prev_distance - star["distance"]
                ok("discard distance drop == %s (>=1)" % key["distance_drop_at_discard"],
                   drop == key["distance_drop_at_discard"] and drop >= 1, str(drop))
            if n >= key["discard_event"]:
                ok("event %d retired exact" % n, star["retired"] == exp["retired"],
                   str(star["retired"]))
                over = [c["id"] for c in star["conditions"]
                        if c["status"].startswith("retired:") and c["id"] not in exp["retired"]]
                ok("event %d no over-retire" % n, not over, str(over))
            if n == key["refine_event"]:
                by_id = {c["id"]: c for c in star["conditions"]}
                for cid, eff in key["refine_effective"].items():
                    c = by_id[cid]
                    ok("refine lineage %s -> %s open" % (cid, eff),
                       c["effective"] == eff and c["status"] == "open"
                       and c["in_frontier"] and c["verb"] == key["refine_verb"][cid],
                       "%s %s %s" % (c["effective"], c["status"], c["verb"]))
            if n == key["second_keep_event"]:
                fids = [f["id"] for f in star["frontier"]]
                by_id = {c["id"]: c for c in star["conditions"]}
                ok("second keep entrants open + frontier",
                   all(by_id[c]["status"] == "open" and c in fids
                       for c in key["second_keep_entrants"]), str(fids))
            prev_distance = star["distance"]
        # replay: every earlier sha renders identically after later commits
        for sha in shas:
            ok("replay --at %s after tip" % sha[:8],
               render_json(read(sha)) == renders[sha])
        # zero edits: git log --follow on the path between spine and tip
        code, out = repo.git("log", "--follow", "--format=%H", "%s..%s" % (spine, shas[-1]),
                             "--", ns)
        ok("zero commits touch the north-star file", code == 0 and not out.strip(), out)
        # claim join over the working tree (never under --at replay)
        claim = key["claim"]
        lane_dir = os.path.join(lab, "experiments", "runs", claim["lane"])
        os.makedirs(lane_dir, exist_ok=True)
        with open(os.path.join(lane_dir, "LANE-STATE.json"), "w", encoding="utf-8") as fh:
            json.dump({"lane": claim["lane"], "state": "running",
                       "heartbeat_unix": time.time(), "ttl_s": DEFAULT_TTL_S}, fh)
        live = read(shas[-1], live=True)["north_stars"][0]
        ok("fresh claim moves %s to claimed_fresh" % claim["condition"],
           claim["condition"] in [c["id"] for c in live["claimed_fresh"]]
           and claim["condition"] not in [f["id"] for f in live["frontier"]]
           and live["distance"] == key["events"][-1]["distance"],
           json.dumps(live["claimed_fresh"]))
        replay = read(shas[-1])["north_stars"][0]
        ok("--at replay ignores the claim overlay", not replay["claimed_fresh"])
        with open(os.path.join(lane_dir, "LANE-STATE.json"), "w", encoding="utf-8") as fh:
            json.dump({"lane": claim["lane"], "heartbeat_unix": time.time() - 10 * DEFAULT_TTL_S,
                       "ttl_s": DEFAULT_TTL_S}, fh)
        stale = read(shas[-1], live=True)["north_stars"][0]
        ok("stale claim returns %s to the frontier" % claim["condition"],
           claim["condition"] in [f["id"] for f in stale["frontier"]])
        # SCHEMA lints: seeded malformed files, exactly one finding of the expected class
        for name, (cls, text) in sorted(MALFORMED.items()):
            doc = parse_north_star(text, "malformed/%s.md" % name)
            classes = [f["class"] for f in doc["findings"]]
            ok("malformed %s -> exactly one %s" % (name, cls), classes == [cls], str(classes))
        unbound = NORTH_STAR_FIXTURE.replace(
            "| probe | P-901 | path-exists=experiments/runs/P-901/VERDICT.json | - |",
            "| probe | P-901 | - | - |")
        doc = derive(parse_north_star(unbound, ns), repo, spine, today=today)
        ok("unbound row lints clean, counts in distance, never frontier",
           not doc["findings"] and doc["conditions"][11]["status"] == "unbound"
           and doc["distance"] == 4 and "C-12" not in [f["id"] for f in doc["frontier"]])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c[1]]
    for name, passed, detail in checks:
        print("%s %s%s" % ("ok  " if passed else "FAIL", name,
                           ("  [%s]" % detail) if (detail and not passed) else ""))
    print("selftest: %d checks, %d failed" % (len(checks), len(failed)))
    return 0 if not failed else 1


# ---------------------------------------------------------------- main ---------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", help="repository root (default: the repo containing cwd)")
    ap.add_argument("--at", default="HEAD", help="commit to read (default HEAD)")
    ap.add_argument("--slug", help="one north-star slug under ledger/north-stars/")
    ap.add_argument("--file", help="lint this working-tree file (derives against --repo/--at)")
    ap.add_argument("--json", action="store_true", help="machine output, sorted keys")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any hard finding")
    ap.add_argument("--today", help="YYYY-MM-DD for horizon aging (default: today)")
    ap.add_argument("--ttl-s", type=int, default=DEFAULT_TTL_S, help="claim TTL default")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--selftest-key", help="harness override: key.json for --selftest")
    o = ap.parse_args(argv)

    if o.selftest:
        return selftest(o.selftest_key)

    root = o.repo
    if not root:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, timeout=GIT_TIMEOUT)
        if p.returncode != 0:
            print("north-star-check: not inside a git repository (use --repo)",
                  file=sys.stderr)
            return 2
        root = p.stdout.strip()
    root = os.path.realpath(root)
    repo = Repo(root)
    sha = repo.resolve(o.at)
    if sha is None:
        print("north-star-check: cannot resolve --at %r in %s" % (o.at, root), file=sys.stderr)
        return 2
    today = datetime.date.fromisoformat(o.today) if o.today else None
    live_root = None if o.at != "HEAD" else root

    targets = []
    if o.file:
        try:
            with open(o.file, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            print("north-star-check: %s" % e, file=sys.stderr)
            return 2
        targets.append((os.path.relpath(os.path.realpath(o.file), root)
                        if os.path.realpath(o.file).startswith(root) else o.file, text))
    else:
        paths = north_star_paths(repo, sha)
        if o.slug:
            paths = [p for p in paths if slug_of(p) == o.slug]
            if not paths:
                print("north-star-check: no %s/%s.md at %s" % (NORTH_STARS_DIR, o.slug,
                                                                 sha[:12]), file=sys.stderr)
                return 2
        for p in paths:
            targets.append((p, repo.show(sha, p) or ""))

    report = check(repo, sha, targets, live_root=live_root, ttl_s=o.ttl_s, today=today)
    sys.stdout.write(render_json(report) if o.json else render_text(report))
    return report["exit_strict"] if o.strict else 0


if __name__ == "__main__":
    sys.exit(main())
