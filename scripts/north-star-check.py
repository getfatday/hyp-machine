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
  document    frontmatter-status=<repo path>:<done-values>[!<no-values>]
                                         a typed document committed at <repo path> (the row's
                                         `bound` cell is that same path); the path is everything
                                         before the LAST `:` of the argument, values are
                                         comma-separated; the document's status is the text after
                                         `status:` on the first such line between the opening
                                         `---` and the next `---`, whitespace and one pair of
                                         matching quotes stripped, compared exactly; done iff that
                                         value is a done-value; a present file with no `status:`
                                         line is open with no outcome; a bound path ABSENT at the
                                         commit is a DANGLING-REF hard finding (document-resolver
                                         lane; the predicate lands in scripts/closes_when.py too)

Outcome (for outcome-conditioned `needs` tokens `C-NN:yes` / `C-NN:no`): hypothesis kept=yes,
discarded=no; decision accepted=yes, denied=no; probe VERDICT.json verdict pass|keep=yes,
fail|discard=no; capture=yes; document done-value=yes, no-value=no (a no-value SETTLES the
outcome without resolving the row: the row stays open, its `C-NN:yes` dependents retire).

Derived-status vocabulary (never stored):
  done            the bound predicate is satisfied at the commit
  open            bound, not yet satisfied (whether or not its needs are met)
  retired:C-NN    an outcome-conditioned prerequisite C-NN resolved the other way, or a
                  prerequisite is itself retired (root id carried); retired precedes done
  unbound         the closes-when cell is `-` (counts in distance, never in the frontier)

frontier      open conditions whose every `needs` token is satisfied, in C-NN order, each
              with a resolver verb: register (hypothesis spec absent) / run (spec present) /
              add (decision row absent) / resolve (decision row open) / capture / probe /
              sync (document)
claimed_fresh frontier members filtered by a fresh experiments/runs/<lane>/LANE-STATE.json
              heartbeat (H-215/H-216: heartbeat_unix age <= ttl_s, default 1800); lane = the
              effective hypothesis id or the probe lane; working-tree overlay, never under --at
retired       the retired ids in order
distance      max over reached-when conditions of the count of open-or-unbound conditions
              on any `needs` path into it (longest-path DP, weight 1 per open/unbound node)
reached       every reached-when condition is done or retired
set           (--json top-level key; text: a trailing `set:` block) the cross-file reduction
              over every block read: destinations, reached, not_derived, union_frontier (one
              entry per distinct effective lane across all frontiers + claimed_fresh lists,
              with serves/n_serves/min_distance/claimed_fresh, sorted n_serves desc,
              min_distance asc, lane asc), shared_bounds, exit_strict_by_slug -- see
              compute_set(); the flat exit_strict keeps its meaning (any file, any hard finding)

Lint classes (hard; `--strict` exits 1 when any fires): SCHEMA, DANGLING-REF, CYCLE,
STATUS-STORED, DUPLICATE-SLUG (two committed paths under ledger/north-stars/, recursive, share
a basename: the slug is the cross-file resolution key). Advisory (never affects exit):
HORIZON-AGED (a `## Horizon` line older than %d days without a `-> C-NN` graduation).

Cross-file `needs` (north-star-set-cross-file-needs lane): a token `<slug>#C-NN[:yes|no]`
resolves against the sibling north-star file `ledger/north-stars/<slug>.md` committed at the
SAME sha (never the working tree) and takes that condition's derived status and outcome; it
counts in distance exactly as a local token does (continuing into the sibling's own chain),
and retire crosses the boundary with the boundary token itself as the root written into the
referencer (`retired:<slug>#C-NN`, never the sibling's local root). A sibling carrying hard
findings makes the token derive `unbound` in the referencer, which still derives. In `--json`
an unqualified need entry is byte-unchanged; a qualified entry adds `slug` and `status`.
Unknown slug / unknown sibling id -> DANGLING-REF; a cycle over the union of local and
cross-file edges -> CYCLE (once).

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
RESOLVER_KINDS = ("hypothesis", "decision", "capture", "probe", "document")
VERB_BY_KIND = {"capture": "capture", "probe": "probe", "document": "sync"}
COLUMNS = ["id", "condition", "resolver", "bound", "closes-when", "needs"]
GIT_TIMEOUT = 30

CID_RE = re.compile(r"^C-\d{2,}$")
NEED_RE = re.compile(r"^(?:([a-z0-9][a-z0-9._-]*)#)?(C-\d{2,})(?::(yes|no))?$")
# what a qualified needs token answers when its sibling is unknown here, did not derive (hard
# findings) or is mid-derivation (a cross-file cycle): counts in distance, never satisfies a need
UNBOUND_NEED = {"status": "unbound", "outcome": None, "resolved": False, "settled": False}
HID_RE = re.compile(r"^H-\d{3,}$")
DEC_RE = re.compile(r"^DEC-\d{3,}$")
LANE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
PRED_RE = re.compile(
    r"^(path-exists|commit-grep|hypothesis-kept|hypothesis-verdict|maintainer-ruling"
    r"|decision-resolved|frontmatter-status)=(.+)$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STATUS_HEADING_RE = re.compile(r"(?m)^##\s*Status\s*$")
NEXT_HEADING_RE = re.compile(r"(?m)^##\s")
HORIZON_RE = re.compile(r"^-\s*(Z-\d{2,})\s*\((\d{4}-\d{2}-\d{2})\):\s*(.+?)\s*$")
GRADUATE_RE = re.compile(r"->\s*(C-\d{2,})\s*$")
EXCLUDED_RE = re.compile(r"^-\s*(X-\d{2,}):\s*(.+?)\s+[-—]+\s*banks:\s*(\S.*?)\s*$")

__doc__ = __doc__ % HORIZON_AGE_DAYS


# ---------------------------------------------------------------- git plumbing -------------

class Repo(object):
    """Read-only, per-sha cached git reads, batched (north-star-set-batched-reads).

    Per commit sha one whole-tree `ls-tree -r --name-only <sha>` (no pathspec: `path-exists=`
    accepts any repository path) answers every exists() and ls() call; exists() is also true
    for a directory prefix of a listed blob, matching `cat-file -e` on a tree. Blob bodies
    arrive through `cat-file --batch` fed `<sha>:<path>` lines in two rounds triggered inside
    this class: on the first show() miss at a sha, every `ledger/north-stars/**.md` plus
    `ledger/work-ledger.jsonl`; on the next miss, every `hypotheses/*.md` and
    `experiments/runs/*/VERDICT.json` the tree lists. A later miss on any other path is fetched
    singly with `git show`; a `missing` reply maps to None exactly as a failed `git show` does.
    The show / exists / ls signatures and return values are unchanged, so no caller moves."""

    def __init__(self, root):
        self.root = root
        self._cache = {}
        self._trees = {}   # sha -> (ordered paths, blob set, directory-prefix set, ok)
        self._blobs = {}   # (sha, path) -> raw bytes from a batch round (None = missing)
        self._rounds = {}  # sha -> batch rounds spent (0, 1, 2)

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

    @staticmethod
    def _norm(path):
        # `<sha>:<path>` accepts a leading ./ and a trailing /; mirror that against the listing
        while path.startswith("./"):
            path = path[2:]
        return path.rstrip("/")

    @staticmethod
    def _decode(body):
        # the same decoding subprocess.run(text=True) applies: locale encoding, strict errors,
        # universal newlines
        import io
        return io.TextIOWrapper(io.BytesIO(body)).read()

    def _tree(self, sha):
        if sha not in self._trees:
            code, out = self.git("ls-tree", "-r", "--name-only", sha)
            paths = out.splitlines() if code == 0 else []
            dirs = set()
            for p in paths:
                parts = p.split("/")
                for i in range(1, len(parts)):
                    dirs.add("/".join(parts[:i]))
            self._trees[sha] = (paths, set(paths), dirs, code == 0)
        return self._trees[sha]

    def _batch(self, sha, paths):
        """One `cat-file --batch` round; blob bodies land in self._blobs, `missing` as None."""
        if not paths:
            return
        feed = "".join("%s:%s\n" % (sha, p) for p in paths).encode("utf-8")
        try:
            p = subprocess.run(["git", "-C", self.root, "cat-file", "--batch"], input=feed,
                               capture_output=True, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return
        if p.returncode != 0:
            return
        data, pos = p.stdout, 0
        for path in paths:
            nl = data.find(b"\n", pos)
            if nl < 0:
                break
            header = data[pos:nl].decode("utf-8", "replace")
            pos = nl + 1
            if header.endswith(" missing"):
                self._blobs[(sha, path)] = None
                continue
            parts = header.split(" ")
            if len(parts) != 3 or not parts[2].isdigit():
                break
            size = int(parts[2])
            body = data[pos:pos + size]
            pos += size + 1
            if parts[1] == "blob" and len(body) == size:
                self._blobs[(sha, path)] = body

    def _prefetch(self, sha, path):
        paths, _blobs, _dirs, _ok = self._tree(sha)
        n = self._rounds.get(sha, 0)
        if n == 0:
            self._rounds[sha] = 1
            self._batch(sha, [p for p in paths if p.startswith(NORTH_STARS_DIR + "/")
                              and p.endswith(".md")]
                        + [p for p in paths if p == "ledger/work-ledger.jsonl"])
            if (sha, path) in self._blobs:
                return
            n = 1
        if n == 1:
            self._rounds[sha] = 2
            self._batch(sha, [p for p in paths if
                              (p.startswith("hypotheses/") and p.endswith(".md")
                               and p.count("/") == 1)
                              or (p.startswith("experiments/runs/") and p.count("/") == 3
                                  and p.endswith("/VERDICT.json"))])

    def show(self, sha, path):
        key = ("show", sha, path)
        if key not in self._cache:
            if (sha, path) not in self._blobs:
                self._prefetch(sha, path)
            if (sha, path) in self._blobs:
                body = self._blobs[(sha, path)]
                self._cache[key] = self._decode(body) if body is not None else None
            else:
                code, out = self.git("show", "%s:%s" % (sha, path))
                self._cache[key] = out if code == 0 else None
        return self._cache[key]

    def exists(self, sha, path):
        key = ("exists", sha, path)
        if key not in self._cache:
            paths, blobs, dirs, ok = self._tree(sha)
            q = self._norm(path)
            self._cache[key] = ok and (q == "" or q in blobs or q in dirs)
        return self._cache[key]

    def ls(self, sha, prefix):
        key = ("ls", sha, prefix)
        if key not in self._cache:
            paths, _blobs, _dirs, _ok = self._tree(sha)
            q = self._norm(prefix)
            self._cache[key] = list(paths) if q == "" else \
                [p for p in paths if p == q or p.startswith(q + "/")]
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


def parse_frontmatter_pred(arg):
    """`<repo path>:<done-values>[!<no-values>]` -> dict(path, done, no) or None when
    malformed (no colon, empty path, empty done-values). The path is everything before the
    LAST colon, so real paths containing `: ` parse; values are comma-separated."""
    if ":" not in arg:
        return None
    path, values = arg.rsplit(":", 1)
    path = path.strip()
    done_part, _, no_part = values.partition("!")
    done = [v.strip() for v in done_part.split(",") if v.strip()]
    no = [v.strip() for v in no_part.split(",") if v.strip()]
    if not path or path.startswith("/") or not done:
        return None
    return {"path": path, "done": done, "no": no}


def frontmatter_status(text):
    """The document's status word: the text after `status:` on the first such line between
    the opening `---` (first line) and the next `---`, whitespace and one pair of matching
    quotes stripped. None when there is no frontmatter block or no `status:` line in it."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("status:"):
            val = line[len("status:"):].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            return val
    return None


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
                      "probe": bool(LANE_RE.match(bound)),
                      "document": bool(bound) and not bound.startswith("/")}[kind]
                if not ok:
                    find("SCHEMA", i, "bound %r malformed for resolver %s" % (bound, kind))
            if pred == "-":
                cond["ref"] = None
            else:
                m = PRED_RE.match(pred)
                if not m or not m.group(2).strip():
                    find("SCHEMA", i, "closes-when %r unparseable" % pred)
                elif kind == "document":
                    pname, parg = m.group(1), m.group(2).strip()
                    fm = parse_frontmatter_pred(parg) if pname == "frontmatter-status" else None
                    if fm is None or fm["path"] != bound:
                        find("SCHEMA", i, "closes-when %r does not bind resolver %s %s"
                             % (pred, kind, bound))
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
                        find("SCHEMA", i, "needs token %r malformed (C-NN, C-NN:yes|no, or "
                             "<slug>#C-NN[:yes|no])" % tok)
                        continue
                    need = {"id": m.group(2), "outcome": m.group(3)}
                    if m.group(1):
                        need["slug"] = m.group(1)   # qualified: resolves in the sibling file
                        need["status"] = None       # filled at derive time
                    cond["needs"].append(need)
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
            if "slug" not in n and n["id"] not in ids:   # qualified tokens resolve in check()
                find("DANGLING-REF", c["line"], "%s needs unknown condition %s"
                     % (c["id"], n["id"]))
    for h in doc["horizon"]:
        if h["graduated_to"] and h["graduated_to"] not in ids:
            find("DANGLING-REF", h["line"], "%s graduates to unknown condition %s"
                 % (h["id"], h["graduated_to"]))

    # cycle detection over LOCAL needs edges (only among known ids; the union with cross-file
    # edges is lint_cross_file's)
    graph = {c["id"]: [n["id"] for n in c["needs"] if "slug" not in n and n["id"] in ids]
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
    elif kind == "document":
        fm = parse_frontmatter_pred(parg)
        text = repo.show(sha, fm["path"]) if fm else None
        value = frontmatter_status(text) if text is not None else None
        out["document_status"] = value
        out["resolved"] = bool(fm) and value is not None and value in fm["done"]
        if out["resolved"]:
            out["outcome"] = "yes"
        elif fm and value is not None and value in fm["no"]:
            out["outcome"] = "no"
        # a no-value settles the outcome without resolving the row (open, dependents retire)
        out["settled"] = out["outcome"] is not None
        out["verb"] = VERB_BY_KIND["document"]
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

def derive(doc, repo, sha, live_root=None, ttl_s=DEFAULT_TTL_S, now=None, today=None,
           siblings=None):
    """Attach derived fields to doc (mutates and returns it). live_root enables the
    working-tree claim overlay (never under --at). siblings (a Siblings) resolves qualified
    `<slug>#C-NN` needs against the sibling files at the same sha; without it they are unbound."""
    # document rows: a bound path absent at the commit is a DANGLING-REF hard finding
    for c in doc["conditions"]:
        if c["resolver"] == "document" and c["ref"] is not None:
            fm = parse_frontmatter_pred(c["ref"].split("=", 1)[1])
            if fm is not None and not repo.exists(sha, fm["path"]):
                doc["findings"].append({"class": "DANGLING-REF", "line": c["line"],
                                        "message": "%s binds %s, absent at %s"
                                        % (c["id"], fm["path"], sha[:12])})
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

    def pre_of(n):
        """The record a needs token points at: the local condition, or -- qualified token
        `<slug>#C-NN` -- the sibling's DERIVED condition at the same sha (UNBOUND_NEED when the
        sibling is unknown here, did not derive, or is mid-derivation)."""
        if "slug" not in n:
            return by_id[n["id"]]
        if siblings is None:
            return UNBOUND_NEED
        return siblings.condition(n["slug"], n["id"]) or UNBOUND_NEED

    # retire: outcome-conditioned prerequisite resolved the other way, or a retired prerequisite;
    # across a file boundary the root is the boundary token itself, never the sibling's root
    retired = {}
    changed = True
    while changed:
        changed = False
        for c in doc["conditions"]:
            if c["id"] in retired:
                continue
            for n in c["needs"]:
                pre = pre_of(n)
                root = None
                if "slug" in n:
                    if pre["status"].startswith("retired:") or (
                            n["outcome"] and (pre["resolved"] or pre.get("settled"))
                            and pre["outcome"] and pre["outcome"] != n["outcome"]):
                        root = "%s#%s" % (n["slug"], n["id"])
                elif n["id"] in retired:
                    root = retired[n["id"]]
                elif n["outcome"] and (pre["resolved"] or pre.get("settled")) and \
                        pre["outcome"] and pre["outcome"] != n["outcome"]:
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

    for c in doc["conditions"]:
        for n in c["needs"]:
            if "slug" in n:
                n["status"] = pre_of(n)["status"]   # the sibling condition's derived status

    def need_met(n):
        pre = pre_of(n)
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

    def far(slug, cid, path):
        """A sibling node on the path, weighted exactly as a local one (1 if open/unbound, then
        its own needs); an unresolvable token counts one, like an unbound row; a cycle stops."""
        key = (slug, cid)
        if key in path:
            return 0
        pre = pre_of({"slug": slug, "id": cid})
        if "needs" not in pre:
            return 1
        best = 0
        for m in pre["needs"]:
            best = max(best, far(m.get("slug", slug), m["id"], path + (key,)))
        return (1 if pre["status"] in ("open", "unbound") else 0) + best

    def longest(cid):
        if cid in memo:
            return memo[cid]
        c = by_id[cid]
        w = 1 if c["status"] in ("open", "unbound") else 0
        best = 0
        for n in c["needs"]:
            best = max(best, far(n["slug"], n["id"], ()) if "slug" in n else longest(n["id"]))
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


def lane_key(c):
    """The effective lane a condition is keyed by in the set block: the derived `lane`
    (effective hypothesis id or probe lane), else the effective id, else the bound cell."""
    return c.get("lane") or c.get("effective") or c.get("bound")


def compute_set(stars):
    """The cross-file `set` block (north-star-set-union-frontier lane), reduced from the
    per-file blocks alone -- no file gains a field, no authored order is read:
      destinations         number of north-star blocks read
      reached              number of derived blocks with reached true
      not_derived          slugs whose block has derived false (hard findings), file order
      union_frontier       one entry per distinct effective lane among every derived file's
                           frontier + claimed_fresh conditions (open, every need met):
                           {lane, verb (of the first serving pair), serves ["<slug>#C-NN"...
                           sorted], n_serves (distinct binding files), min_distance (smallest
                           per-file distance among them), claimed_fresh (heartbeat fresh in ANY
                           binding file -- a flag, never a reorder)}, sorted by
                           (n_serves desc, min_distance asc, lane asc)
      shared_bounds        lane -> sorted ["<slug>#C-NN"...] for every lane bound (any status)
                           in more than one derived file
      exit_strict_by_slug  slug -> 1 if that block carries any hard finding else 0
    Blocks that did not derive contribute only to not_derived / exit_strict_by_slug."""
    derived = [s for s in stars if s.get("derived")]

    def pair(s, c):
        return "%s#%s" % (s["slug"], c["id"])

    def n_files(pairs):
        return len(set(p.rsplit("#", 1)[0] for p in pairs))

    bounds = {}
    for s in derived:
        for c in s["conditions"]:
            bounds.setdefault(lane_key(c), []).append(pair(s, c))
    shared = {k: sorted(v) for k, v in bounds.items() if n_files(v) > 1}
    entries = {}
    verbs = {}
    for s in derived:
        for c in s["conditions"]:
            if not (c.get("in_frontier") or c.get("claimed_fresh")):
                continue
            k = lane_key(c)
            e = entries.setdefault(k, {"lane": k, "verb": None, "serves": [], "n_serves": 0,
                                       "min_distance": s["distance"], "claimed_fresh": False})
            e["serves"].append(pair(s, c))
            verbs[pair(s, c)] = c.get("verb")
            e["claimed_fresh"] = e["claimed_fresh"] or bool(c.get("claimed_fresh"))
            e["min_distance"] = min(e["min_distance"], s["distance"])
    for e in entries.values():
        e["serves"].sort()
        e["n_serves"] = n_files(e["serves"])
        e["verb"] = verbs[e["serves"][0]]
    union = sorted(entries.values(),
                   key=lambda e: (-e["n_serves"], e["min_distance"], e["lane"]))
    return {"destinations": len(stars),
            "reached": sum(1 for s in derived if s.get("reached")),
            "not_derived": [s["slug"] for s in stars if not s.get("derived")],
            "union_frontier": union,
            "shared_bounds": shared,
            "exit_strict_by_slug": {s["slug"]: (1 if s["findings"] else 0) for s in stars}}


class Siblings(object):
    """Cross-file `needs` resolution (north-star-set-cross-file-needs lane): slug -> the
    north-star record at the same sha -- the targets first (the first path in target order keys
    the slug), then any other committed ledger/north-stars/**.md read on demand. Records derive
    once, lazily; a record mid-derivation (a cross-file cycle) or one carrying hard findings
    answers UNBOUND_NEED."""

    def __init__(self, repo, sha, docs, **derive_kw):
        self.repo, self.sha, self.kw = repo, sha, derive_kw
        self.docs = {}
        for d in docs:
            self.docs.setdefault(d["slug"], d)
        self.committed = None
        self.busy = set()

    def doc(self, slug):
        if slug not in self.docs:
            if self.committed is None:
                self.committed = {}
                for p in north_star_paths(self.repo, self.sha):
                    self.committed.setdefault(slug_of(p), p)
            p = self.committed.get(slug)
            d = None
            if p is not None:
                d = parse_north_star(self.repo.show(self.sha, p) or "", p)
                d["slug"] = slug
            self.docs[slug] = d
        return self.docs[slug]

    def ensure(self, d):
        if "derived" not in d and id(d) not in self.busy:
            self.busy.add(id(d))
            try:
                derive(d, self.repo, self.sha, siblings=self, **self.kw)
            finally:
                self.busy.discard(id(d))
        return d

    def condition(self, slug, cid):
        """-> the sibling's derived condition dict, UNBOUND_NEED (sibling not derived), or None
        (unknown slug or id)."""
        d = self.doc(slug)
        if d is None:
            return None
        c = next((x for x in d["conditions"] if x["id"] == cid), None)
        if c is None:
            return None
        self.ensure(d)
        return c if d.get("derived") else UNBOUND_NEED


def lint_cross_file(docs, siblings):
    """Lint the cross-file resolution key BEFORE derivation (north-star-set-cross-file-needs
    lane): DUPLICATE-SLUG when two paths share a basename (the later path in target order
    carries the finding, naming both); DANGLING-REF for a qualified token naming an unknown
    slug or an unknown sibling condition; CYCLE over the union of local and cross-file needs
    edges, reported once on the first target on the cycle and only when a cross-file edge is on
    it (a purely local cycle is the per-file parser's finding)."""
    first = {}
    for d in docs:
        f = first.setdefault(d["slug"], d)
        if f is not d:
            d["findings"].append({"class": "DUPLICATE-SLUG", "line": 0,
                                  "message": "slug %s is committed at both %s and %s"
                                  % (d["slug"], f["path"], d["path"])})
    for d in docs:
        for c in d["conditions"]:
            for n in c["needs"]:
                if "slug" not in n:
                    continue
                sib = siblings.doc(n["slug"])
                if sib is None:
                    d["findings"].append({"class": "DANGLING-REF", "line": c["line"],
                                          "message": "%s needs unknown north-star %s"
                                          % (c["id"], n["slug"])})
                elif not any(x["id"] == n["id"] for x in sib["conditions"]):
                    d["findings"].append({"class": "DANGLING-REF", "line": c["line"],
                                          "message": "%s needs unknown condition %s#%s"
                                          % (c["id"], n["slug"], n["id"])})
    known = {s: d for s, d in siblings.docs.items() if d is not None}
    graph, cross = {}, set()
    for slug, d in known.items():
        ids = set(c["id"] for c in d["conditions"])
        for c in d["conditions"]:
            edges = []
            for n in c["needs"]:
                if "slug" in n:
                    sib = known.get(n["slug"])
                    if sib and any(x["id"] == n["id"] for x in sib["conditions"]):
                        edges.append((n["slug"], n["id"]))
                        cross.add(((slug, c["id"]), (n["slug"], n["id"])))
                elif n["id"] in ids:
                    edges.append((slug, n["id"]))
            graph[(slug, c["id"])] = edges
    targets = set(id(d) for d in docs)
    state, stack = {}, []

    def visit(v):
        state[v] = 1
        stack.append(v)
        for w in graph.get(v, []):
            if state.get(w) == 1:
                cyc = stack[stack.index(w):] + [w]
                if any((a, b) in cross for a, b in zip(cyc, cyc[1:])):
                    return cyc
            elif state.get(w) is None:
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
                owner = next((known[s] for s, _ in cyc if id(known[s]) in targets), None)
                if owner is not None:
                    owner["findings"].append({"class": "CYCLE", "line": 0,
                                              "message": "needs cycle: %s" % " -> ".join(
                                                  "%s#%s" % n for n in cyc)})
                break


def check(repo, sha, targets, live_root=None, ttl_s=DEFAULT_TTL_S, now=None, today=None):
    """targets: list of (path, text). -> report dict."""
    stars = []
    for path, text in targets:
        doc = parse_north_star(text, path)
        doc["slug"] = slug_of(path)
        stars.append(doc)
    siblings = Siblings(repo, sha, stars, live_root=live_root, ttl_s=ttl_s, now=now, today=today)
    lint_cross_file(stars, siblings)
    for doc in stars:
        siblings.ensure(doc)
    findings = []
    for s in stars:
        for f in s["findings"]:
            findings.append(dict(f, north_star=s["slug"]))
    return {"at": sha, "north_stars": stars, "findings": findings,
            "exit_strict": 1 if findings else 0, "set": compute_set(stars)}


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
    else:
        out.extend(render_set_text(report))
    return "\n".join(out) + "\n"


def render_set_text(report):
    """The trailing `set:` block (after every per-file block; the per-file text is unchanged)."""
    st = report.get("set")
    if not st:
        return []
    out = ["set: %d destinations, %d reached, not derived: %s"
           % (st["destinations"], st["reached"], ", ".join(st["not_derived"]) or "-")]
    out.append("  union_frontier:" if st["union_frontier"] else "  union_frontier: -")
    for e in st["union_frontier"]:
        out.append("    %s(%s) serves %d [%s] min_distance %s%s"
                   % (e["lane"], e["verb"], e["n_serves"], ", ".join(e["serves"]),
                      e["min_distance"], " claimed_fresh" if e["claimed_fresh"] else ""))
    out.append("  shared_bounds: %s" % ("; ".join(
        "%s [%s]" % (k, ", ".join(v)) for k, v in sorted(st["shared_bounds"].items())) or "-"))
    bad = [k for k, v in sorted(st["exit_strict_by_slug"].items()) if v]
    out.append("  exit_strict_by_slug: %s" % (", ".join("%s=1" % k for k in bad) or "all 0"))
    return out


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


# Set scenario (north-star-set-union-frontier lane): three files over the same lab repo -- a
# lane bound in three files (H-901), a lane bound in two (H-902, refined into H-905 at event
# 3), a needs chain in set-b -- plus one malformed sibling (authored status column).
def _set_file(slug, rows):
    out = ["# North star: %s" % slug, "", "destination: set scenario placeholder for %s." % slug,
           "reached-when: " + ", ".join(r[0] for r in rows), "", "## Conditions", "",
           "| id | condition | resolver | bound | closes-when | needs |",
           "|---|---|---|---|---|---|"]
    for cid, hid, pred, needs in rows:
        out.append("| %s | %s row | hypothesis | %s | %s=%s | %s |" % (cid, slug, hid, pred, hid,
                                                                      needs))
    return "\n".join(out) + "\n"


SET_FILES = {
    "set-a": _set_file("set-a", [("C-01", "H-901", "hypothesis-kept", "-"),
                                 ("C-02", "H-902", "hypothesis-kept", "-"),
                                 ("C-03", "H-903", "hypothesis-verdict", "-")]),
    "set-b": _set_file("set-b", [("C-01", "H-901", "hypothesis-kept", "-"),
                                 ("C-02", "H-902", "hypothesis-kept", "-"),
                                 ("C-03", "H-907", "hypothesis-kept", "C-02")]),
    "set-c": _set_file("set-c", [("C-01", "H-901", "hypothesis-kept", "-"),
                                 ("C-02", "H-904", "hypothesis-verdict", "-")]),
}
SET_FILES["set-bad"] = _add_status_column(SET_FILES["set-a"].replace("set-a", "set-bad"))

# union_frontier rows: [lane, verb, n_serves, min_distance, serves]
SET_KEY = {
    "spine": {
        "union_frontier": [["H-901", "run", 3, 1, ["set-a#C-01", "set-b#C-01", "set-c#C-01"]],
                           ["H-902", "run", 2, 1, ["set-a#C-02", "set-b#C-02"]],
                           ["H-903", "run", 1, 1, ["set-a#C-03"]],
                           ["H-904", "run", 1, 1, ["set-c#C-02"]]],
        "shared_bounds": {"H-901": ["set-a#C-01", "set-b#C-01", "set-c#C-01"],
                          "H-902": ["set-a#C-02", "set-b#C-02"]},
        "not_derived": ["set-bad"], "destinations": 4, "reached": 0,
        "exit_strict_by_slug": {"set-a": 0, "set-b": 0, "set-c": 0, "set-bad": 1}},
    "tip": {
        "union_frontier": [["H-905", "run", 2, 1, ["set-a#C-02", "set-b#C-02"]]],
        "shared_bounds": {"H-901": ["set-a#C-01", "set-b#C-01", "set-c#C-01"],
                          "H-905": ["set-a#C-02", "set-b#C-02"]},
        "not_derived": ["set-bad"], "destinations": 4, "reached": 1,
        "exit_strict_by_slug": {"set-a": 0, "set-b": 0, "set-c": 0, "set-bad": 1}},
    "claim_lane": "H-905",
}


def compare_set(got, exp):
    """-> list of mismatch strings between a computed set block and one SET_KEY entry."""
    bad = []
    rows = [[e["lane"], e["verb"], e["n_serves"], e["min_distance"], e["serves"]]
            for e in got["union_frontier"]]
    if rows != exp["union_frontier"]:
        bad.append("union_frontier: got %s" % json.dumps(rows))
    for k in ("shared_bounds", "not_derived", "destinations", "reached", "exit_strict_by_slug"):
        if got[k] != exp[k]:
            bad.append("%s: got %s" % (k, json.dumps(got[k], sort_keys=True)))
    return bad


# Cross-file scenario (north-star-set-cross-file-needs lane) over the same lab repo: xf-a's
# C-02 and C-03 depend on xf-b's conditions through qualified tokens; xf-b binds H-903
# (discarded at event 2) as a premise, so at the tip xf-a's dependents retire with the BOUNDARY
# token as root while xf-b's own C-02 carries its local root C-01.
def _xf_file(slug, rows, reached):
    out = ["# North star: %s" % slug, "", "destination: cross-file scenario placeholder for %s." % slug,
           "reached-when: " + reached, "", "## Conditions", "",
           "| id | condition | resolver | bound | closes-when | needs |",
           "|---|---|---|---|---|---|"]
    for cid, text, hid, pred, needs in rows:
        out.append("| %s | %s | hypothesis | %s | %s=%s | %s |" % (cid, text, hid, pred, hid, needs))
    return "\n".join(out) + "\n"


XF_FILES = {
    "xf-a": _xf_file("xf-a", [
        ("C-01", "xf-a alpha", "H-901", "hypothesis-kept", "-"),
        ("C-02", "xf-a waits on the sibling premise", "H-902", "hypothesis-kept", "xf-b#C-01:yes"),
        ("C-03", "xf-a waits on the sibling follow-up", "H-904", "hypothesis-kept", "C-01, xf-b#C-02")],
        "C-01, C-02, C-03"),
    "xf-b": _xf_file("xf-b", [
        ("C-01", "xf-b premise answered either way", "H-903", "hypothesis-verdict", "-"),
        ("C-02", "xf-b follow-up", "H-907", "hypothesis-kept", "C-01:yes")],
        "C-01, C-02"),
}

XF_KEY = {
    "spine": {
        "xf-a": {"status": {"C-01": "open", "C-02": "open", "C-03": "open"},
                 "frontier": [["C-01", "run"]], "retired": [], "distance": 3, "reached": False},
        "xf-b": {"status": {"C-01": "open", "C-02": "open"}, "frontier": [["C-01", "run"]],
                 "retired": [], "distance": 2, "reached": False},
        "token_status": {"C-02": "open", "C-03": "open"}},
    "tip": {
        "xf-a": {"status": {"C-01": "done", "C-02": "retired:xf-b#C-01",
                            "C-03": "retired:xf-b#C-02"},
                 "frontier": [], "retired": ["C-02", "C-03"], "distance": 0, "reached": True,
                 "effective": {"C-02": "H-905"}},
        "xf-b": {"status": {"C-01": "done", "C-02": "retired:C-01"}, "frontier": [],
                 "retired": ["C-02"], "distance": 0, "reached": True},
        "token_status": {"C-02": "done", "C-03": "retired:C-01"}},
    "sibling_status_stored": {
        "status": {"C-01": "open", "C-02": "open", "C-03": "open"},
        "frontier": [["C-01", "run"]], "retired": [], "distance": 2, "reached": False,
        "token_status": {"C-02": "unbound", "C-03": "unbound"}},
    "violations": {"unknown-slug": ["DANGLING-REF"], "unknown-cid": ["DANGLING-REF"],
                   "cross-cycle": ["CYCLE"], "nested-duplicate": ["DUPLICATE-SLUG"]},
}


# ---------------------------------------------------------------- document case ------------
# A second, separate throwaway repository for the `document` resolver kind (document-resolver
# lane): typed documents whose only committed truth is a frontmatter `status:` line that a
# sync commit rewrites. Kept apart from SCENARIO so the derived-condition-status fixture's
# pinned copy of SCENARIO stays byte-equal to the embedded one.

def _typed_doc(kind, title, status_line):
    fm = "---\ntype: %s\ntitle: \"%s\"\n" % (kind, title)
    if status_line is not None:
        fm += status_line + "\n"
    fm += "date: %s\n---\n\n# %s\n\nSynced stub; the `status:` line is the whole point.\n" % (
        SCENARIO_DATE, title)
    return fm


_DOC_M1 = "docs/Area A: alpha/Milestones/M1: first/index.md"
_DOC_M2 = "docs/Area A: alpha/Milestones/M2: second/index.md"
_DOC_VALUES = "completed!cancelled,rejected"


def _doc_row(cid, text, path, values, needs):
    return "| %s | %s | document | %s | frontmatter-status=%s:%s | %s |" % (
        cid, text, path, path, values, needs)


DOC_NORTH_STAR = "\n".join([
    "# North star: typed-tree", "",
    "destination: Every milestone of the typed tree reads its own status line; a sync commit is the only update.",
    "reached-when: C-05, C-07, C-08", "",
    "## Conditions", "",
    "| id | condition | resolver | bound | closes-when | needs |",
    "|---|---|---|---|---|---|",
    _doc_row("C-01", "first milestone lands (path carries two `: `)", _DOC_M1, _DOC_VALUES, "-"),
    _doc_row("C-02", "second milestone lands (double-quoted status)", _DOC_M2, _DOC_VALUES, "-"),
    _doc_row("C-03", "third milestone lands (cancelled at the spine)", "docs/m3.md", _DOC_VALUES, "-"),
    _doc_row("C-04", "follow-on to the third", "docs/m4.md", _DOC_VALUES, "C-03:yes"),
    _doc_row("C-05", "follow-on to the follow-on", "docs/m5.md", _DOC_VALUES, "C-04"),
    _doc_row("C-06", "document without a status line", "docs/m6.md", _DOC_VALUES, "C-01"),
    _doc_row("C-07", "outcome achieved (single-quoted status)", "docs/o1.md", "achieved!missed", "-"),
    _doc_row("C-08", "outcome still proposed", "docs/o2.md", "achieved!missed", "C-06"),
    "",
]) + "\n"

DOC_SCENARIO = {
    "name": "document-resolver/typed-tree",
    "north_star": "ledger/north-stars/typed-tree.md",
    "spine_date": SCENARIO_DATE + "T00:00:00Z",
    "spine_message": "spine: seeded typed tree (document resolver)",
    "spine": {
        "README.md": "# typed-tree mini-lab\n\nTyped documents with a frontmatter `status:` line.\n",
        "ledger/north-stars/README.md": NORTH_STARS_README_LAB,
        "ledger/north-stars/typed-tree.md": DOC_NORTH_STAR,
        _DOC_M1: _typed_doc("milestone", "M1: first", "status: in-progress"),
        _DOC_M2: _typed_doc("milestone", "M2: second", "status: \"completed\""),
        "docs/m3.md": _typed_doc("milestone", "m3", "status: cancelled"),
        "docs/m4.md": _typed_doc("milestone", "m4", "status: planned"),
        "docs/m5.md": _typed_doc("milestone", "m5", "status: planned"),
        "docs/m6.md": _typed_doc("milestone", "m6", None),
        "docs/o1.md": _typed_doc("outcome", "o1", "status: 'achieved'"),
        "docs/o2.md": _typed_doc("outcome", "o2", "status: proposed"),
    },
    "events": [
        {"n": 1, "label": "sync: M1 in-progress -> completed",
         "message": "jira-sync: M1 in-progress -> completed",
         "write": {_DOC_M1: _typed_doc("milestone", "M1: first", "status: completed")}},
        {"n": 2, "label": "o2 deleted (seeded dangling bound path)",
         "message": "remove docs/o2.md", "delete": ["docs/o2.md"]},
        {"n": 3, "label": "o2 restored",
         "message": "restore docs/o2.md",
         "write": {"docs/o2.md": _typed_doc("outcome", "o2", "status: proposed")}},
        {"n": 4, "label": "sync: m3 cancelled -> completed (retire reversed)",
         "message": "jira-sync: m3 cancelled -> completed",
         "write": {"docs/m3.md": _typed_doc("milestone", "m3", "status: completed")}},
    ],
}

DOC_MALFORMED = {
    "document-bound-mismatch": ("SCHEMA", DOC_NORTH_STAR.replace(
        "| document | docs/m3.md |", "| document | docs/m3-other.md |")),
    "document-empty-done-values": ("SCHEMA", DOC_NORTH_STAR.replace(
        "frontmatter-status=docs/m3.md:completed!cancelled,rejected",
        "frontmatter-status=docs/m3.md:!cancelled")),
    "document-predicate-on-probe": ("SCHEMA", DOC_NORTH_STAR.replace(
        "| document | docs/m3.md | frontmatter-status=docs/m3.md:completed!cancelled,rejected |",
        "| probe | m3 | frontmatter-status=docs/m3.md:completed!cancelled,rejected |")),
}


def _dvec(**over):
    v = {"C-%02d" % i: "open" for i in range(1, 9)}
    v.update({"C-02": "done", "C-04": "retired:C-03", "C-05": "retired:C-03", "C-07": "done"})
    v.update(over)
    return v


DOC_KEY = {
    "north_star": DOC_SCENARIO["north_star"],
    "spine": {"status": _dvec(), "frontier": [["C-01", "sync"], ["C-03", "sync"]],
              "retired": ["C-04", "C-05"], "distance": 3, "reached": False, "derived": True,
              "outcome": {"C-02": "yes", "C-03": "no", "C-06": None, "C-07": "yes",
                          "C-08": None},
              "document_status": {"C-02": "completed", "C-06": None, "C-07": "achieved"}},
    "events": [
        {"n": 1, "status": _dvec(**{"C-01": "done"}),
         "frontier": [["C-03", "sync"], ["C-06", "sync"]], "retired": ["C-04", "C-05"],
         "distance": 2, "reached": False, "derived": True},
        {"n": 2, "derived": False, "findings": ["DANGLING-REF"], "dangling_id": "C-08"},
        {"n": 3, "status": _dvec(**{"C-01": "done"}),
         "frontier": [["C-03", "sync"], ["C-06", "sync"]], "retired": ["C-04", "C-05"],
         "distance": 2, "reached": False, "derived": True},
        {"n": 4, "status": _dvec(**{"C-01": "done", "C-03": "done", "C-04": "open",
                                    "C-05": "open"}),
         "frontier": [["C-04", "sync"], ["C-06", "sync"]], "retired": [],
         "distance": 2, "reached": False, "derived": True},
    ],
}


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
    for rel in ev.get("delete", []):
        os.remove(os.path.join(dest, rel))
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

        # document resolver case: a second throwaway repository of typed documents
        dkey = key.get("document", DOC_KEY)
        dlab = os.path.join(tmp, "typed")
        dspine = scenario_build(dlab, scenario=DOC_SCENARIO)
        drepo = Repo(dlab)
        dns = DOC_SCENARIO["north_star"]

        def dread(sha):
            return check(drepo, sha, [(dns, drepo.show(sha, dns))], today=today)

        def dcheck(label, rep, exp):
            star = rep["north_stars"][0]
            classes = [f["class"] for f in star["findings"]]
            ok("document %s STATUS-STORED silent" % label, "STATUS-STORED" not in classes,
               str(classes))
            if not exp.get("derived", True):
                by_id = {c["id"]: c for c in star["conditions"]}
                ok("document %s not derived, findings %s on %s" % (label, exp["findings"],
                                                                   exp["dangling_id"]),
                   not star["derived"] and classes == exp["findings"]
                   and star["findings"][0]["line"] == by_id[exp["dangling_id"]]["line"]
                   and rep["exit_strict"] == 1 and star["frontier"] == []
                   and star["distance"] is None, "%s derived=%s" % (classes, star["derived"]))
                return star
            bad = compare_to_key(star, exp)
            by_id = {c["id"]: c for c in star["conditions"]}
            for cid, val in exp.get("document_status", {}).items():
                if by_id[cid].get("document_status") != val:
                    bad.append("document_status %s: got %r" % (cid, by_id[cid].get(
                        "document_status")))
            ok("document %s parity" % label, star["derived"] and not bad
               and rep["exit_strict"] == 0, "; ".join(bad))
            return star

        rep = dread(dspine)
        star = dcheck("spine", rep, dkey["spine"])
        c01 = [c for c in star["conditions"] if c["id"] == "C-01"][0]
        ok("document last-colon split keeps `: ` inside the path",
           parse_frontmatter_pred(c01["ref"].split("=", 1)[1])["path"] == _DOC_M1
           and c01["bound"] == _DOC_M1)
        dblob0 = drepo.blob_sha(dspine, dns)
        for ev in DOC_SCENARIO["events"]:
            n = ev["n"]
            sha = scenario_play(dlab, n, scenario=DOC_SCENARIO)
            rep = dread(sha)
            dcheck("event %d (%s)" % (n, ev["label"]), rep, dkey["events"][n - 1])
            ok("document event %d replay byte-identical" % n,
               render_json(rep) == render_json(dread(sha)))
            ok("document event %d zero-edit blob" % n, drepo.blob_sha(sha, dns) == dblob0)
        for name, (cls, text) in sorted(DOC_MALFORMED.items()):
            doc = parse_north_star(text, "malformed/%s.md" % name)
            classes = [f["class"] for f in doc["findings"]]
            ok("malformed %s -> exactly one %s" % (name, cls), classes == [cls], str(classes))

        # set scenario (north-star-set-union-frontier lane): three files + a malformed sibling
        skey = key.get("set", SET_KEY)
        set_targets = [("ledger/north-stars/%s.md" % slug, SET_FILES[slug])
                       for slug in sorted(SET_FILES)]

        def sread(sha, live=False):
            return check(repo, sha, set_targets, live_root=lab if live else None, today=today)

        srep = sread(spine)
        bad = compare_set(srep["set"], skey["spine"])
        ok("set spine: dedup, n_serves, ranking, shared_bounds, isolation", not bad,
           "; ".join(bad))
        ok("set spine: one exit bit per slug, flat exit_strict still 1",
           srep["exit_strict"] == 1 and sum(srep["set"]["exit_strict_by_slug"].values()) == 1)
        by_slug = {s["slug"]: s for s in srep["north_stars"]}
        ok("set spine: per-file blocks unchanged by the set (frontiers intact)",
           [f["id"] for f in by_slug["set-a"]["frontier"]] == ["C-01", "C-02", "C-03"]
           and [f["id"] for f in by_slug["set-b"]["frontier"]] == ["C-01", "C-02"]
           and by_slug["set-bad"]["derived"] is False)
        ok("set spine: --json byte-identical across two reads",
           render_json(srep) == render_json(sread(spine)))
        ok("set spine: text form ends with the set block",
           render_text(srep).rstrip("\n").splitlines()[-1].startswith("  exit_strict_by_slug:")
           and "\nset: 4 destinations, 0 reached, not derived: set-bad\n" in render_text(srep))
        trep = sread(shas[-1])
        bad = compare_set(trep["set"], skey["tip"])
        ok("set tip: lineage keys the lane (H-902 -> H-905), done lanes leave", not bad,
           "; ".join(bad))
        with open(os.path.join(lane_dir, "LANE-STATE.json"), "w", encoding="utf-8") as fh:
            json.dump({"lane": skey["claim_lane"], "state": "running",
                       "heartbeat_unix": time.time(), "ttl_s": DEFAULT_TTL_S}, fh)
        lrep = sread(shas[-1], live=True)
        lset = lrep["set"]
        ok("set claim: fresh heartbeat flags claimed_fresh without removing or reordering",
           [e["lane"] for e in lset["union_frontier"]] == [e["lane"] for e in
                                                             trep["set"]["union_frontier"]]
           and lset["union_frontier"][0]["claimed_fresh"] is True
           and trep["set"]["union_frontier"][0]["claimed_fresh"] is False,
           json.dumps(lset["union_frontier"]))
        # the gate bites: a wrong n_serves in the key must be detected before the key is trusted
        wrong = json.loads(json.dumps(skey["spine"]))
        wrong["union_frontier"][0][2] = 2
        ok("set key comparison detects a wrong n_serves",
           bool(compare_set(srep["set"], wrong)) and not compare_set(srep["set"], skey["spine"]))

        # cross-file scenario (north-star-set-cross-file-needs lane): qualified needs tokens
        xkey = key.get("cross_file", XF_KEY)
        xf_targets = [("ledger/north-stars/%s.md" % s, XF_FILES[s]) for s in sorted(XF_FILES)]

        def xread(sha, targets=None):
            return check(repo, sha, targets or xf_targets, today=today)

        def xstar(rep, slug):
            return [s for s in rep["north_stars"] if s["slug"] == slug][0]

        def token_statuses(star):
            return {c["id"]: n["status"] for c in star["conditions"] for n in c["needs"]
                    if "slug" in n}

        def classes_of(rep, slug):
            return [f["class"] for f in xstar(rep, slug)["findings"]]

        xa_tip = None
        for label, xsha in (("spine", spine), ("tip", shas[-1])):
            rep = xread(xsha)
            xa, xb = xstar(rep, "xf-a"), xstar(rep, "xf-b")
            bad = compare_to_key(xa, xkey[label]["xf-a"]) + compare_to_key(xb, xkey[label]["xf-b"])
            ok("cross-file %s: both files derive to the key (cross-file retire root = boundary "
               "token)" % label, rep["exit_strict"] == 0 and xa["derived"] and xb["derived"]
               and not bad, "; ".join(bad))
            ok("cross-file %s: qualified need entries carry the sibling condition's status"
               % label, token_statuses(xa) == xkey[label]["token_status"],
               json.dumps(token_statuses(xa)))
            ok("cross-file %s: unqualified need entries byte-unchanged (id, outcome only); "
               "qualified add slug, status" % label,
               all(sorted(n) == ["id", "outcome"] for s in (xa, xb) for c in s["conditions"]
                   for n in c["needs"] if "slug" not in n)
               and all(sorted(n) == ["id", "outcome", "slug", "status"] for c in xa["conditions"]
                       for n in c["needs"] if "slug" in n))
            alone = xread(xsha, [xf_targets[1]])["north_stars"][0]
            ok("cross-file %s: the sibling's own entry is unchanged by being referenced" % label,
               json.dumps(alone, sort_keys=True) == json.dumps(xb, sort_keys=True))
            ok("cross-file %s: --json byte-identical across two reads" % label,
               render_json(rep) == render_json(xread(xsha)))
            if label == "tip":
                xa_tip = xa
        # a referenced sibling that is NOT among the targets is read from the commit on demand
        _write(lab, "ledger/north-stars/xf-b.md", XF_FILES["xf-b"])
        xenv = scenario_env(SCENARIO_DATE + "T00:10:00Z")
        _run_git(lab, ["add", "--", "ledger/north-stars/xf-b.md"], xenv)
        _run_git(lab, ["commit", "-q", "--no-verify", "-m", "xf: sibling committed"], xenv)
        xsha = _run_git(lab, ["rev-parse", "HEAD"], xenv).strip()
        rep = xread(xsha, [xf_targets[0]])
        ok("cross-file: a sibling outside the targets resolves from the commit (--slug shape)",
           len(rep["north_stars"]) == 1 and rep["exit_strict"] == 0
           and token_statuses(rep["north_stars"][0]) == xkey["tip"]["token_status"]
           and {c["id"]: c["status"] for c in rep["north_stars"][0]["conditions"]}
           == xkey["tip"]["xf-a"]["status"], json.dumps(rep["findings"]))
        # seeded violations of the resolution key, each exactly one finding of its class
        bad_a = XF_FILES["xf-a"].replace("xf-b#C-01:yes", "nope#C-01:yes")
        rep = xread(spine, [("ledger/north-stars/xf-a.md", bad_a), xf_targets[1]])
        ok("cross-file unknown slug -> exactly one DANGLING-REF; the sibling still derives",
           classes_of(rep, "xf-a") == xkey["violations"]["unknown-slug"]
           and xstar(rep, "xf-b")["derived"] and rep["exit_strict"] == 1,
           str(classes_of(rep, "xf-a")))
        bad_a = XF_FILES["xf-a"].replace("xf-b#C-02", "xf-b#C-09")
        rep = xread(spine, [("ledger/north-stars/xf-a.md", bad_a), xf_targets[1]])
        ok("cross-file unknown sibling id -> exactly one DANGLING-REF",
           classes_of(rep, "xf-a") == xkey["violations"]["unknown-cid"] and rep["exit_strict"] == 1,
           str(classes_of(rep, "xf-a")))
        bad_b = XF_FILES["xf-b"].replace("| hypothesis-verdict=H-903 | - |",
                                         "| hypothesis-verdict=H-903 | xf-a#C-02 |")
        rep = xread(spine, [xf_targets[0], ("ledger/north-stars/xf-b.md", bad_b)])
        ok("cross-file cycle -> exactly one CYCLE over the union of local and cross-file edges",
           [f["class"] for f in rep["findings"]] == xkey["violations"]["cross-cycle"]
           and rep["exit_strict"] == 1, json.dumps(rep["findings"]))
        rep = xread(spine, [xf_targets[0], ("ledger/north-stars/team/xf-a.md", XF_FILES["xf-a"]),
                            xf_targets[1]])
        dups = [f for f in rep["findings"] if f["class"] == "DUPLICATE-SLUG"]
        ok("nested duplicate basename -> exactly one DUPLICATE-SLUG naming both paths",
           [f["class"] for f in rep["findings"]] == xkey["violations"]["nested-duplicate"]
           and "ledger/north-stars/xf-a.md" in dups[0]["message"]
           and "ledger/north-stars/team/xf-a.md" in dups[0]["message"]
           and rep["north_stars"][0]["derived"] and not rep["north_stars"][1]["derived"]
           and rep["exit_strict"] == 1, json.dumps(rep["findings"]))
        rep = xread(spine, [xf_targets[0], ("ledger/north-stars/xf-b.md",
                                            _add_status_column(XF_FILES["xf-b"]))])
        xa = xstar(rep, "xf-a")
        bad = compare_to_key(xa, xkey["sibling_status_stored"])
        ok("sibling with hard findings -> token unbound, the referencer still derives",
           classes_of(rep, "xf-b") == ["STATUS-STORED"] and xa["derived"] and not bad
           and token_statuses(xa) == xkey["sibling_status_stored"]["token_status"]
           and rep["exit_strict"] == 1 and rep["findings"][0]["north_star"] == "xf-b",
           "; ".join(bad) + " " + json.dumps(token_statuses(xa)))
        ok("needs grammar: every shipped token still parses; qualified tokens parse; junk rejected",
           all(NEED_RE.match(t) for t in ("C-01", "C-01:yes", "C-01:no", "xf-b#C-01",
                                          "xf-b#C-01:no", "a.b_c-1#C-10"))
           and not any(NEED_RE.match(t) for t in ("C-1", "C-01:maybe", "Xf#C-01", "#C-01",
                                                  "xf-b/C-01", "xf-b#C-01:")))
        # the gate bites: a sibling-local root in the key must be detected before it is trusted
        wrong = json.loads(json.dumps(xkey["tip"]["xf-a"]))
        wrong["status"]["C-02"] = "retired:C-01"
        ok("cross-file key comparison detects a sibling-local root",
           bool(compare_to_key(xa_tip, wrong)) and not compare_to_key(xa_tip, xkey["tip"]["xf-a"]))
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
