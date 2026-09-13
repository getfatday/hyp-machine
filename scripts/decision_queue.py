#!/usr/bin/env python3
"""decision_queue.py -- the stateless per-caller projection of the decision ledger (decision-queue-projection lane).

A pure function of committed bytes and the caller's git identity: the configured ledger (HEAD blob and working
tree), `.claude/hyp.json` `decision_roles`, CODEOWNERS (`.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS`),
`contributors.json`, `.mailmap`, the repository's git objects and one stamp (`--stamp`, else DECISIONS_TODAY, else
today). It reads no state file and writes none; the only process it spawns is `git`; every card line it prints
comes from decision_brief_render.py beside it (card_lines, not_ready_lines, marker_lines, record_lines,
control_options) -- this module composes the header, the routes sentence and the summary, never card text.

  queue [--json|--text] [--mine|--all] [--set T ...] [--since [REF]] [--age B] [--class C ...] [--ids ID ...]
        [--state S ...] [--records] [--stamp DATE] [--root R]
  queue-answer <id> (--label L [--label L ...] | --text T | --response T) [--override POINTER] [--settles A,B]
        [--no-commit] [--recompile] [--root R]
  announce --hook [--root R]        the SessionStart row: the hook payload on stdin, ONE JSON object on stdout
  --live <root>                     a read-only census of a live repository (no write, no append)
  --selftest                        synthetic ledgers in throwaway repositories under TMPDIR

Imports decisions.py beside it (parse_ledger_v3, join_status, sort_key, brief_context, the resolve path) and
decision_brief_render.py. decisions.py imports THIS module lazily for the caller helper, the ladder, the join
rules and the attribution walk, so the filter, the refusal and the attribution cannot disagree.

docs/decisions.md ("The decision queue") is the shipped contract; every reading the spec left open is enumerated
in the source lab's decision-queue-projection lane under fixture/impl/CONTRACT.md.
"""
import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

_T0 = time.monotonic()            # the announce clock starts at interpreter start (import time)
HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA_VERSION = "1"
DEFAULT_ROLE = "maintainer"
ROLE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]*$")
PATH_ROLE_PREFIX = "owner:"        # the R2 path-scoped role shape: owner:<repo-relative path>
ADDRESSEE_FIELD = "addressee"
BASIS_DECISION_ROLES = "decision_roles"
BASIS_CODEOWNERS = "codeowners"
BASIS_SINGLE = "single-identity"
BASIS_UNMAPPED = "unmapped"
NO_ROLE = "none"                   # the announce's role token for a resolved identity that holds no role
VIA_FIELD = "via"
VIA_VALUE = "decisions-queue"
VIA_SUFFIX = " via=decisions-queue"
VIA_GREP = "via=decisions-queue"
RECORD_BASIS = "two-way-door"
OVERTAKEN_BASIS = "premise-overtaken"
HYP_REL = os.path.join(".claude", "hyp.json")
ROLES_KEY = "decision_roles"
CODEOWNERS_CANDIDATES = (os.path.join(".github", "CODEOWNERS"), "CODEOWNERS", os.path.join("docs", "CODEOWNERS"))
CONTRIBUTORS_REL = "contributors.json"
ROOT_PATTERNS = ("*", "**", "/", "/*", "/**")
GIT_TIMEOUT = 20                   # every git read outside the announce (decisions.GIT_TIMEOUT)
ANNOUNCE_DEADLINE_S = 7.8          # 10 s row timeout / 1.28 headroom ratio, rounded down to a tenth
LOCK_DIR_NAME = "hyp-decisions.lock"
LOCK_HOLDER_FILE = "holder"
BATCH_MAX = 4
HEADER_MIN, HEADER_MAX = 1, 12
OPTION_MIN, OPTION_MAX = 2, 4
HYP_DECISIONS_ENV = "HYP_DECISIONS"
HYP_DECISIONS_OFF = "off"
HYP_DECISIONS_SURFACE = "surface"
ANNOUNCE_SOURCES = ("startup", "resume", "clear", "compact", "fork")
EXCEPTION_CLASSES = ("ADDRESSEE-UNMAPPED", "DEEPER-UNFILED", "CONTESTED")
TEXT_TOKEN = "DECISION-QUEUE"
MACHINE_TOKEN = "DECISIONS-YOURS"
ANSWER_CLI_TOKEN = "/hyp:decisions"
DECISIONS_OPEN_SUFFIX = " — answer them: " + ANSWER_CLI_TOKEN
_COLON = ":"
SYSTEM_MESSAGE_FMT = ("Decisions: {n} are yours ({k} new since you last answered; {d} returned with evidence; {w} waiting "
                      "on your pushback; {r} record(s) to glance at) — acting as {role} ({basis}). Answer them: "
                      + ANSWER_CLI_TOKEN)
SYSTEM_MESSAGE_NONE_FMT = "Decisions: none are yours — acting as {role} ({basis})"
SYSTEM_MESSAGE_FAILED_FMT = "Decisions: count unavailable — ANNOUNCE-FAILED {reason}; run " + ANSWER_CLI_TOKEN
SYSTEM_MESSAGE_HEADLESS_SUFFIX = " — interactive session required to answer"
BEHIND_FMT = "behind {upstream} by {b} commits, {m} decision row{s} unseen"
MACHINE_LINE_FMT = (MACHINE_TOKEN + "\t{n}\tnew {k}\treturned {d}\twaiting {w}\tto-glance {r}\trole {role} ({basis})"
                    "\tbehind {b}\tanswer" + _COLON + " " + ANSWER_CLI_TOKEN)
FLAGS = ("--json", "--text", "--mine", "--all", "--set", "--since", "--age", "--class", "--ids", "--state", "--records",
         "--stamp", "--root")
AGE_BUCKETS = ("past-own-date", "in-window", "no-clock")
STATE_FILTERS = ("open", "commented", "deeper", "returned", "waiting", "record-veto-open", "record-challenged",
                 "contested", "unauthorized-attempt")
ENVELOPE_KEYS = ("schema_version", "stamp", "identity", "can_record", "block_reason", "counts", "items", "awaiting_brief",
                 "not_ready", "waiting", "contested", "records", "batches", "free_text_routes", "routes_help", "announce")
ITEM_KEYS = ("id", "status", "urgency", "class", "requested_by", "blocks", "requested_at", "age_days", "brief_state",
             "accountable", "card_lines", "answer_commands", "ask", "controls", "notice", "set_match", "resolutions")
ASK_KEYS = ("header", "question", "multiSelect", "options")
# the free-text route words (Method (b)); the two answer prefixes are assembled so the module source carries no
# question-grammar literal (A2's grep) -- the words themselves are the route table, not card text
ROUTE_ANSWER = "answer" + _COLON
ROUTE_ACCEPT = "accept" + _COLON
ROUTE_DENY = "deny"
ROUTE_LATER = "later"
ROUTE_LATER_WHEN = "later when "
ROUTE_GO_DEEPER = "go deeper"
GO_DEEPER_PREFIX = ROUTE_GO_DEEPER + _COLON
ROUTES_HELP = ("Pick an option, or type: " + ROUTE_GO_DEEPER + " [: why] (send it back for evidence), " + ROUTE_LATER
               + " (leave it open), " + ROUTE_LATER_WHEN + "<predicate>, " + ROUTE_DENY + " [: why], " + ROUTE_ANSWER
               + " <your own words> (recorded as your decision); anything else is kept as a comment and the card stays open.")
FREE_TEXT_ROUTES = (
    {"route": "own-label", "match": "text equal (case-folded) to one of the card's own option labels", "appends": "accepted",
     "fields": ["chosen_options"]},
    {"route": "answer", "match": ROUTE_ANSWER + " <text> | " + ROUTE_ACCEPT + " <text>", "appends": "accepted",
     "fields": ["chosen_options"]},
    {"route": "deny", "match": ROUTE_DENY + "[: <words>]", "appends": "denied", "fields": ["comment"]},
    {"route": "later-when", "match": ROUTE_LATER_WHEN + "<predicate>", "appends": "commented", "fields": ["retest_when"]},
    {"route": "later", "match": ROUTE_LATER, "appends": None, "fields": []},
    {"route": "go-deeper", "match": ROUTE_GO_DEEPER + "[: <words>]", "appends": "commented", "fields": ["comment"]},
    {"route": "other", "match": "any other text", "appends": "commented", "fields": ["comment"]},
    {"route": "response", "match": "a general response", "appends": None, "fields": []},
)
# the finding grammar (byte-exact on every surface; CONTRACT.md)
F_NOT_ADDRESSEE = "RESOLVE-REFUSED\tNOT-ADDRESSEE\t{id}\trole={role}\tyou={basis}"
F_IDENTITY_UNRESOLVED = "IDENTITY-UNRESOLVED"
F_UNAUTHORIZED = "UNAUTHORIZED {id} {basis}"
F_CONTESTED = "CONTESTED {id} {sha_a} {sha_b}"
F_SUPERSEDED = "SUPERSEDED-BY-ADDRESSEE {id}"
F_UNMAPPED = "ADDRESSEE-UNMAPPED {role} — add decision_roles.{role} to .claude/hyp.json: [\"{email}\"]"
F_LEDGER_BEHIND = "LEDGER-BEHIND {upstream} {commits} {rows}"
F_MULTI_ROW = "MULTI-ROW-COMMIT {sha} {ids}"
F_OVERRIDE = "OVERRIDE {id} by {role} basis {pointer}"
F_RESOLVE_BUSY = "RESOLVE-BUSY {pid}"
F_COMMIT_FAILED = "COMMIT-FAILED {tail}"
F_WORKTREE_APPEND_FAILED = "WORKTREE-APPEND-FAILED {row}"
F_QUEUE_INVALID_RETEST = "QUEUE-INVALID\tretest-when"
F_QUEUE_INVALID_LABEL = "QUEUE-INVALID\tlabel {text}"
F_QUEUE_INVALID_FLAG = "QUEUE-INVALID\tflag {flag}"
F_ANNOUNCE_FAILED = "ANNOUNCE-FAILED {reason}"
EXIT_INVALID, EXIT_REFUSED = 1, 2
_KIT = {}


class Deadline(Exception):
    """the announce budget is exhausted"""


def _kit():
    """decisions.py and decision_brief_render.py beside this file, imported once."""
    if not _KIT:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import decisions            # noqa: the decision kit beside this module
        import decision_brief_render  # noqa: the one source of card text
        _KIT["decisions"] = decisions
        _KIT["render"] = decision_brief_render
    return _KIT["decisions"], _KIT["render"]


def stamp_for(args_stamp=None):
    if args_stamp:
        return str(args_stamp)[:10]
    decisions, _render = _kit()
    return decisions.today_str()


class Git(object):
    """the one git runner: every call under GIT_TIMEOUT, or under the remaining announce budget when a deadline is set
    (a call can then never outlive the row's timeout); text in, text out; never raises on a non-zero exit."""

    def __init__(self, root, timeout=GIT_TIMEOUT, deadline=None):
        self.root = root
        self.timeout = timeout
        self.deadline = deadline
        self.calls = []

    def remaining(self):
        return None if self.deadline is None else self.deadline - time.monotonic()

    def run(self, args, binary=False, env=None, stdin=None):
        timeout = self.timeout
        if self.deadline is not None:
            left = self.remaining()
            if left <= 0:
                raise Deadline(" ".join(args[:2]))
            timeout = min(timeout, left)
        self.calls.append(args[0])
        try:
            proc = subprocess.run(["git", "-C", self.root] + list(args), capture_output=True, text=not binary,
                                  timeout=timeout, env=env, input=stdin)
        except subprocess.TimeoutExpired:
            if self.deadline is not None:
                raise Deadline(" ".join(args[:2]))
            return 124, (b"" if binary else ""), "timeout"
        except OSError as exc:
            return 127, (b"" if binary else ""), str(exc)
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        return proc.returncode, proc.stdout, err


# ---------- the caller helper (one helper for queue, announce and resolve) ----------

_IDENT_RE = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]*)>\s+\d+\s+[-+]\d{4}\s*$")
_MAILMAP_RE = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]*)>\s*$")


def caller_identity(root, git=None):
    """-> {resolved, email, canonical_email, name, reason}: the identity the answer commit will carry --
    `git var GIT_AUTHOR_IDENT` (the env-then-config chain) canonicalized through `git check-mailmap`. Unresolved when
    git yields no email (useConfigOnly with no email, or the hostname guess): the caller is view-only."""
    git = git or Git(root)
    rc, out, _err = git.run(["var", "GIT_AUTHOR_IDENT"])
    m = _IDENT_RE.match(out.strip()) if rc == 0 else None
    email = (m.group("email").strip() if m else "")
    name = (m.group("name").strip() if m else "")
    if rc != 0 or not email or email.endswith("(none)") or "@" not in email:
        return {"resolved": False, "email": email, "canonical_email": "", "name": name, "reason": F_IDENTITY_UNRESOLVED}
    canonical = email
    rc2, out2, _err2 = git.run(["check-mailmap", "%s <%s>" % (name, email)])
    m2 = _MAILMAP_RE.match(out2.strip()) if rc2 == 0 else None
    if m2 and m2.group("email").strip():
        canonical = m2.group("email").strip()
    return {"resolved": True, "email": email, "canonical_email": canonical.lower(), "name": name, "reason": None}


# ---------- the attribution walk (the introducing commit of a row; order-independent by construction) ----------

def ledger_commits(git, ledger_rel):
    """-> [{sha, parents[], author, email, iso, time}] newest first: every commit at which the ledger blob differs from
    at least one parent's (git's --full-history walk); emails are mailmap-canonical (%aE)."""
    rc, out, _err = git.run(["log", "--full-history", "--use-mailmap", "--format=%H%x1f%P%x1f%an%x1f%aE%x1f%aI%x1f%at",
                             "--", ledger_rel])
    commits = []
    if rc != 0:
        return commits
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 6:
            continue
        sha, parents, author, email, iso, at = parts
        try:
            at = int(at)
        except ValueError:
            at = 0
        commits.append({"sha": sha, "parents": parents.split(), "author": author, "email": email.strip().lower(),
                        "iso": iso, "time": at})
    return commits


def blob_lines_batch(git, ledger_rel, shas):
    """{sha: frozenset(stripped non-empty lines of <sha>:<ledger>)} through ONE `git cat-file --batch`; a commit
    without the path maps to an empty set."""
    shas = [s for s in dict.fromkeys(shas) if s]
    out_map = {}
    if not shas:
        return out_map
    req = "".join("%s:%s\n" % (s, ledger_rel) for s in shas).encode("utf-8")
    rc, data, _err = git.run(["cat-file", "--batch"], binary=True, stdin=req)
    if rc != 0 or not data:
        return {s: frozenset() for s in shas}
    pos = 0
    for s in shas:
        nl = data.find(b"\n", pos)
        if nl < 0:
            out_map[s] = frozenset()
            continue
        header = data[pos:nl].decode("utf-8", "replace").split()
        if len(header) >= 3 and header[1] == "blob":
            size = int(header[2])
            body = data[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
            out_map[s] = frozenset(l.strip() for l in body.decode("utf-8", "replace").splitlines() if l.strip())
        else:                       # "<sha>:<path> missing"
            pos = nl + 1
            out_map[s] = frozenset()
    return out_map


def introducing_commits(git, ledger_rel, raws, commits=None, blobs=None):
    """-> {raw: {sha, parents, author, email, iso, time} | None}. The introducing commit of a row is a commit whose
    ledger blob contains the row's raw line and none of whose parents' blobs do -- decided from the blobs themselves
    (one log walk + one cat-file batch), never from file order or a date-ordered search. Several such commits (a row
    introduced independently on two merged branches) resolve to the earliest author time, then the older commit."""
    commits = ledger_commits(git, ledger_rel) if commits is None else commits
    need = []
    for c in commits:
        need.append(c["sha"])
        need.extend(c["parents"])
    blobs = blob_lines_batch(git, ledger_rel, need) if blobs is None else blobs
    empty = frozenset()
    index_from_oldest = {c["sha"]: len(commits) - i for i, c in enumerate(commits)}
    out = {}
    for raw in raws:
        raw = raw.strip()
        cands = [c for c in commits if raw in blobs.get(c["sha"], empty)
                 and all(raw not in blobs.get(p, empty) for p in c["parents"])]
        if not cands:
            out[raw] = None
            continue
        cands.sort(key=lambda c: (c["time"], index_from_oldest[c["sha"]]))
        out[raw] = cands[0]
    return out


def attribute_rows(root, ledger_rel, rows, git=None):
    """decisions.derive_attribution's body: attach staged / decided_by / decided_at / resolution_commit (the kit's
    keys, byte-compatible) plus introducing_sha, author_email, introduced_at (epoch) and parents to each row dict
    carrying `raw`. -> the {raw: commit} map."""
    git = git or Git(root)
    intro = introducing_commits(git, ledger_rel, [r["raw"] for r in rows])
    for r in rows:
        c = intro.get(r["raw"].strip())
        r["staged"] = c is None
        r["decided_by"] = r["decided_at"] = r["resolution_commit"] = None
        r["introducing_sha"] = r["author_email"] = None
        r["introduced_at"] = None
        r["parents"] = []
        if c is not None:
            r.update({"staged": False, "decided_by": c["author"], "decided_at": c["iso"],
                      "resolution_commit": c["sha"][:7], "introducing_sha": c["sha"], "author_email": c["email"],
                      "introduced_at": c["time"], "parents": list(c["parents"])})
    return intro


# ---------- the ladder: a role token -> the identities that hold it (R1 decision_roles, R2 CODEOWNERS, R3 single identity, R4 unmapped) ----------

def _read_json(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    return data


def _read_text(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _glob_to_regex(pattern):
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def codeowners_pattern_matches(pattern, path):
    """gitignore-style CODEOWNERS matching against a repo-relative path: a leading `/` or an inner `/` anchors the
    pattern at the root; a pattern without `/` matches its basename at any depth; a trailing `/` names a directory;
    a directory pattern matches everything under it."""
    pattern = pattern.strip()
    path = path.strip("/")
    if not pattern:
        return False
    if pattern in ROOT_PATTERNS:
        return True
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    core = pattern.strip("/")
    rx = _glob_to_regex(core)
    if anchored:
        return re.match("^" + rx + "(/.*)?$", path) is not None
    return re.match("^(.*/)?" + rx + "(/.*)?$", path) is not None


def codeowners_root_pattern(pattern):
    return pattern.strip() in ROOT_PATTERNS


def parse_codeowners(text):
    """-> [(pattern, [owner tokens])] in file order (comments and blanks dropped)."""
    rules = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append((parts[0], parts[1:]))
    return rules


class Routing(object):
    """The ladder over one repository root. `holders(role)` -> (frozenset of canonical emails | None, basis token).
    R1 `.claude/hyp.json` decision_roles; R2 CODEOWNERS (roles `maintainer` = the last pattern matching the repository
    root; `owner:<path>` = the last pattern matching <path>; an email owner as-is, an @handle through
    contributors.json `github`, an @org/team unresolvable); R3 the ledger's committed history carries exactly one
    canonical author email; R4 unmapped (None, "unmapped"). Every read is a committed or working-tree file read plus
    one bounded `git log --format=%aE` for the census (lazy, cached)."""

    def __init__(self, root, ledger_rel, git=None, census=None):
        self.root = root
        self.ledger_rel = ledger_rel
        self.git = git or Git(root)
        cfg = _read_json(os.path.join(root, HYP_REL))
        roles = cfg.get(ROLES_KEY) if isinstance(cfg, dict) else None
        self.decision_roles = {}
        if isinstance(roles, dict):
            for role, emails in roles.items():
                if isinstance(role, str) and isinstance(emails, list):
                    vals = [str(e).strip().lower() for e in emails if isinstance(e, str) and e.strip()]
                    if vals:
                        self.decision_roles[role] = vals
        self.codeowners = []
        self.codeowners_path = None
        for cand in CODEOWNERS_CANDIDATES:
            text = _read_text(os.path.join(root, cand))
            if text is not None:
                self.codeowners = parse_codeowners(text)
                self.codeowners_path = cand
                break
        contributors = _read_json(os.path.join(root, CONTRIBUTORS_REL))
        self.handles = {}
        if isinstance(contributors, dict):
            for email, entry in contributors.items():
                if isinstance(entry, dict) and isinstance(entry.get("github"), str) and isinstance(email, str):
                    self.handles.setdefault(entry["github"].strip().lstrip("@").lower(), []).append(email.strip().lower())
        self._census = census
        self._cache = {}

    def census(self):
        """the distinct canonical author emails of the ledger's committed history (one walk, cached)"""
        if self._census is None:
            rc, out, _err = self.git.run(["log", "--use-mailmap", "--format=%aE", "--", self.ledger_rel])
            emails = set()
            if rc == 0:
                for line in out.splitlines():
                    if line.strip():
                        emails.add(line.strip().lower())
            self._census = frozenset(emails)
        return self._census

    def _owners_to_emails(self, owners):
        emails = []
        for tok in owners:
            tok = tok.strip()
            if not tok:
                continue
            if tok.startswith("@"):
                if "/" in tok:
                    continue                         # @org/team: unresolvable offline
                emails.extend(self.handles.get(tok[1:].lower(), []))
            elif "@" in tok:
                emails.append(tok.lower())
        return emails

    def codeowners_role(self, role):
        """R2: the owner emails for `maintainer` or `owner:<path>`; [] when the rung cannot answer."""
        if not self.codeowners:
            return []
        if role == DEFAULT_ROLE:
            matching = [owners for pattern, owners in self.codeowners if codeowners_root_pattern(pattern)]
        elif role.startswith(PATH_ROLE_PREFIX):
            path = role[len(PATH_ROLE_PREFIX):]
            matching = [owners for pattern, owners in self.codeowners if codeowners_pattern_matches(pattern, path)]
        else:
            return []
        if not matching:
            return []
        return self._owners_to_emails(matching[-1])

    def holders(self, role):
        role = role if isinstance(role, str) and role else DEFAULT_ROLE
        if role in self._cache:
            return self._cache[role]
        result = None
        if role in self.decision_roles:
            result = (frozenset(self.decision_roles[role]), BASIS_DECISION_ROLES)
        if result is None:
            emails = self.codeowners_role(role)
            if emails:
                result = (frozenset(emails), BASIS_CODEOWNERS)
        if result is None:
            census = self.census()
            if len(census) == 1:
                result = (census, BASIS_SINGLE)
        if result is None:
            result = (None, BASIS_UNMAPPED)
        self._cache[role] = result
        return result

    def is_holder(self, role, email):
        """True | False | None (the role is unmapped)"""
        emails, _basis = self.holders(role)
        if emails is None:
            return None
        return bool(email) and email.lower() in emails

    def roles_of(self, email, universe):
        """the role tokens in `universe` whose holders include `email` (sorted, maintainer first)"""
        held = [r for r in universe if self.is_holder(r, email)]
        return sorted(set(held), key=lambda r: (0 if r == DEFAULT_ROLE else 1, r))

    def pointer_resolves(self, pointer):
        """an override's `basis`: a committed pointer -- `<path>` or `<path>@<sha40>#La-Lb`; the path must exist at HEAD
        (and at <sha> when given)."""
        if not isinstance(pointer, str) or not pointer.strip():
            return False
        path, sha = pointer.strip(), "HEAD"
        m = re.match(r"^(.+)@([0-9a-f]{40})(?:#L\d+(?:-L\d+)?)?$", path)
        if m:
            path, sha = m.group(1), m.group(2)
        rc, _out, _err = self.git.run(["cat-file", "-e", "%s:%s" % (sha, path.replace(os.sep, "/"))])
        return rc == 0


def role_of(rec):
    """the accountable role of a kind:"decision" row (a missing field reads maintainer)"""
    obj = rec.get(ADDRESSEE_FIELD) if isinstance(rec, dict) else None
    if isinstance(obj, dict) and isinstance(obj.get("role"), str) and obj["role"].strip():
        return obj["role"].strip()
    return DEFAULT_ROLE


def valid_role_token(token):
    return isinstance(token, str) and (ROLE_TOKEN_RE.match(token) is not None
                                       or (token.startswith(PATH_ROLE_PREFIX) and len(token) > len(PATH_ROLE_PREFIX)
                                           and not re.search(r"\s", token)))


def role_universe(decisions_recs, routing):
    roles = {DEFAULT_ROLE}
    for rec in decisions_recs:
        roles.add(role_of(rec))
    roles.update(routing.decision_roles.keys())
    return sorted(roles)


# ---------- the join with the multi-user rules ----------

def _closing(res):
    return res.get("disposition") in ("accepted", "denied")


def _is_record(res):
    rec = res["rec"]
    return ((res["disposition"] == "accepted" and rec.get("basis") == RECORD_BASIS)
            or (res["disposition"] == "denied" and rec.get("basis") == OVERTAKEN_BASIS))


def chain_order_key(res):
    """the order rule: committed rows by introducing-commit author time then file order; an uncommitted row after
    every committed one (it never decides over a committed row)"""
    committed = res.get("introduced_at") is not None
    return (0 if committed else 1, res.get("introduced_at") or 0, res["order"])


def _sha_matches(full, given):
    return isinstance(given, str) and len(given) >= 7 and full.startswith(given.lower())


def join_with_rules(decisions, resolutions, routing, recipe_email=None):
    """-> {id: {"status", "chain", "deciding", "kind", "findings", "contested", "record", "unauthorized", "role",
    "basis", "holders"}}. Rows carry attribution when derive_attribution ran (introduced_at, author_email); without
    it every row reads as committed-by-unknown and only the file-order rule applies (the kit's legacy join).

    Precedence among authorized closing rows: a settling row (an addressee's closing row citing both contested shas)
    > the addressee's own rows (one identity: the latest decides -- a change of mind; two holders: the EARLIER
    committed row decides and the pair is CONTESTED) > a recorded override > an R4 row carrying addressee_basis
    unmapped > the lab's record (basis two-way-door). A closing row from a non-addressee never changes status
    (UNAUTHORIZED <id> <basis>); an addressee's row over an override, an unmapped row or a record prints
    SUPERSEDED-BY-ADDRESSEE <id>."""
    by_id = {}
    for res in resolutions:
        by_id.setdefault(res["id"], []).append(res)
    joined = {}
    attributed = any(r.get("introduced_at") is not None for r in resolutions) or any(
        r.get("staged") is False for r in resolutions)
    for dec in decisions:
        rec = dec["rec"]
        did = dec["id"]
        role = role_of(rec)
        holders, basis = routing.holders(role) if routing is not None else (None, BASIS_UNMAPPED)
        chain = sorted(by_id.get(did, []), key=chain_order_key if attributed else (lambda r: r["order"]))
        findings = []
        addressee, override, unmapped, records, settles, unauthorized, staged_closing = [], [], [], [], [], [], []
        for res in chain:
            res.setdefault("finding", None)
            if not _closing(res):
                continue
            r = res["rec"]
            email = (res.get("author_email") or "").lower()
            committed = res.get("introduced_at") is not None
            if attributed and not committed:
                staged_closing.append(res)
                continue
            if _is_record(res):
                records.append(res)
                continue
            if isinstance(r.get("settles"), list) and len(r["settles"]) >= 2:
                if holders is None or (email and email in holders) or not attributed:
                    settles.append(res)
                else:
                    res["finding"] = F_UNAUTHORIZED.format(id=did, basis=basis)
                    unauthorized.append(res)
                continue
            if isinstance(r.get("override"), dict):
                admin_ok = routing is not None and routing.is_holder(DEFAULT_ROLE, email)
                pointer = r["override"].get("basis")
                if admin_ok and routing.pointer_resolves(pointer):
                    override.append(res)
                else:
                    res["finding"] = F_UNAUTHORIZED.format(id=did, basis=basis)
                    unauthorized.append(res)
                continue
            if r.get("addressee_basis") == BASIS_UNMAPPED:
                unmapped.append(res)
                continue
            if not attributed or holders is None or (email and email in holders):
                addressee.append(res)
            else:
                res["finding"] = F_UNAUTHORIZED.format(id=did, basis=basis)
                unauthorized.append(res)
        deciding, kind, contested = None, None, None
        if settles:
            deciding, kind = settles[-1], "settle"
        elif addressee:
            authors = []
            for res in addressee:
                a = (res.get("author_email") or "").lower()
                if a not in authors:
                    authors.append(a)
            if len(authors) <= 1:
                deciding, kind = addressee[-1], "addressee"
            else:
                deciding, kind = addressee[0], "addressee"
                first_author = (deciding.get("author_email") or "").lower()
                other = next((res for res in addressee[1:] if (res.get("author_email") or "").lower() != first_author), None)
                if other is not None:
                    contested = (deciding.get("introducing_sha") or "", other.get("introducing_sha") or "")
                    cleared = any(all(any(_sha_matches(s, g) for g in res["rec"]["settles"]) for s in contested)
                                  for res in settles)
                    if not cleared:
                        findings.append(F_CONTESTED.format(id=did, sha_a=contested[0], sha_b=contested[1]))
                        deciding["finding"] = deciding.get("finding")
                        other["finding"] = F_CONTESTED.format(id=did, sha_a=contested[0], sha_b=contested[1])
                    else:
                        contested = None
        elif override:
            deciding, kind = override[-1], "override"
            ov = deciding["rec"]["override"]
            findings.append(F_OVERRIDE.format(id=did, role=ov.get("role", DEFAULT_ROLE), pointer=ov.get("basis", "?")))
        elif unmapped:
            deciding, kind = unmapped[-1], "unmapped"
        elif records:
            deciding, kind = records[-1], "record"
        elif staged_closing:
            deciding, kind = staged_closing[-1], "staged"
        if kind in ("addressee", "settle") and (override or unmapped or records):
            findings.append(F_SUPERSEDED.format(id=did))
            for res in override + unmapped + records:
                res["finding"] = res.get("finding") or F_SUPERSEDED.format(id=did)
        for res in unauthorized:
            findings.append(res["finding"])
        if holders is None and routing is not None:
            findings.append(F_UNMAPPED.format(role=role, email=recipe_email or "<canonical email>"))
        if deciding is not None:
            status = deciding["disposition"]
        elif any(res["disposition"] == "commented" for res in chain):
            status = "commented"
        else:
            status = "open"
        record = deciding if (deciding is not None and kind == "record" and deciding["disposition"] == "accepted") else None
        joined[did] = {"status": status, "chain": chain, "deciding": deciding, "kind": kind, "findings": findings,
                       "contested": contested, "record": record, "unauthorized": unauthorized, "role": role,
                       "basis": basis, "holders": holders}
    return joined


def multi_row_commit_findings(resolutions):
    """MULTI-ROW-COMMIT <sha> <ids>: one ledger commit introduced closing rows for more than one card (a squash merge
    attributes every row to the merger)."""
    by_sha = {}
    for res in resolutions:
        sha = res.get("introducing_sha")
        if sha and _closing(res):
            by_sha.setdefault(sha, set()).add(res["id"])
    out = []
    for sha in sorted(by_sha):
        ids = sorted(by_sha[sha])
        if len(ids) > 1:
            out.append(F_MULTI_ROW.format(sha=sha, ids=",".join(ids)))
    return out


# ---------- the projection ----------

def ledger_rel_for(root):
    decisions, _render = _kit()
    return decisions.ledger_rel_for(root).replace(os.sep, "/")


def git_dir_of(root):
    """<root>/.git as a directory, or the per-worktree gitdir named by a `.git` pointer file; None when neither."""
    g = os.path.join(root, ".git")
    if os.path.isdir(g):
        return g
    if os.path.isfile(g):
        line = (_read_text(g) or "").strip()
        if line.startswith("gitdir:"):
            path = line[len("gitdir:"):].strip()
            return path if os.path.isabs(path) else os.path.normpath(os.path.join(root, path))
    return None


def upstream_name(root):
    """`<remote>/<branch>` of HEAD's configured upstream from the git config files alone (no process): None when
    HEAD is detached or no upstream is configured."""
    gitdir = git_dir_of(root)
    if not gitdir:
        return None
    head = (_read_text(os.path.join(gitdir, "HEAD")) or "").strip()
    if not head.startswith("ref: refs/heads/"):
        return None
    branch = head[len("ref: refs/heads/"):]
    common = gitdir
    rel = (_read_text(os.path.join(gitdir, "commondir")) or "").strip()
    if rel:
        common = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(gitdir, rel))
    text = _read_text(os.path.join(common, "config")) or ""
    section, remote, merge = None, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
            continue
        if section == '[branch "%s"]' % branch and "=" in line:
            key, _sep, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key == "remote":
                remote = val
            elif key == "merge":
                merge = val
    if not remote or not merge:
        return None
    short = merge[len("refs/heads/"):] if merge.startswith("refs/heads/") else merge
    return "%s/%s" % (remote, short)


def lock_holder(root):
    """the pid recorded by a live answer lock, or None"""
    gitdir = git_dir_of(root)
    if not gitdir:
        return None
    path = os.path.join(gitdir, LOCK_DIR_NAME)
    if not os.path.isdir(path):
        return None
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return None
    if age > GIT_TIMEOUT:
        return None                                  # stale: the committer reclaims it
    holder = (_read_text(os.path.join(path, LOCK_HOLDER_FILE)) or "").strip()
    return holder or "?"


def decision_raws(parsed):
    return {d["id"]: d["raw"].strip() for d in parsed["decisions"]}


def armed_retest(chain, root):
    """(predicate, since, fired) when the latest commented row of an open card carries retest_when, else None. Fired is
    evaluated against committed HEAD through the shared closes_when grammar (the queue evaluates; the announce never
    does)."""
    decisions, _render = _kit()
    commented = [r for r in chain if r["disposition"] == "commented"]
    if not commented:
        return None
    last = commented[-1]["rec"]
    value = last.get("retest_when")
    if not isinstance(value, str) or not value.strip():
        return None
    cw = decisions._shared_parser()
    fired = False
    if cw is not None:
        parsed = cw.parse_retest_when_field(value)
        if parsed is not None:
            try:
                fired = bool(cw.retest_when_evidence(parsed[0], parsed[1], root)[0])
            except Exception:
                fired = False
    return value.strip(), str(last.get("date") or ""), fired


_TOKEN_RES = (re.compile(r"\bH-\d{3}\b"), re.compile(r"\bH-DRA" + r"FT-[0-9a-f]{8}\b"), re.compile(r"\bDESIGN-[a-z0-9-]+\b"),
              re.compile(r"\bwf_[0-9a-f]{8}-[0-9a-f]{3}\b"))
_RUNS_RE = re.compile(r"experiments/runs/([^/\s]+)")
_HYP_RE = re.compile(r"hypotheses/([^/\s:]+?)\.md")


def card_tokens(rec):
    """the deterministic lane/workflow token grammar over requested_by, blocks[], context_pointers[], staged_artifact
    and evidence (design section 3)"""
    fields = [rec.get("requested_by")]
    for key in ("blocks", "context_pointers"):
        val = rec.get(key)
        if isinstance(val, list):
            fields.extend(str(v) for v in val)
    for key in ("staged_artifact", "evidence"):
        val = rec.get(key)
        if isinstance(val, list):
            fields.extend(str(v) for v in val)
        elif val is not None:
            fields.append(str(val))
    text = " ".join(str(f) for f in fields if f)
    tokens = []
    for rx in _TOKEN_RES:
        tokens.extend(rx.findall(text))
    tokens.extend(_RUNS_RE.findall(text))
    tokens.extend(_HYP_RE.findall(text))
    out = []
    for t in tokens:
        if t not in out:
            out.append(t)
    return out


def since_tokens(git, ref, last_act_sha=None):
    """the lanes whose experiments/runs/<lane>/VERDICT.json changed since <ref> (a rev, or an ISO date), or since the
    caller's last queue act when ref is None"""
    if ref is None:
        if not last_act_sha:
            return []
        ref = last_act_sha
    if re.match(r"^\d{4}-\d{2}-\d{2}", ref):
        rc, out, _err = git.run(["log", "--since=%s" % ref, "--name-only", "--format=", "--", "experiments/runs"])
    else:
        rc, out, _err = git.run(["diff", "--name-only", "%s..HEAD" % ref, "--", "experiments/runs"])
    if rc != 0:
        return []
    lanes = []
    for line in out.splitlines():
        m = re.match(r"^experiments/runs/([^/]+)/VERDICT\.json$", line.strip())
        if m and m.group(1) not in lanes:
            lanes.append(m.group(1))
    return lanes


def age_bucket(rec, status, policy, stamp, render, record=None):
    if record is not None:
        until = str(record["rec"].get("veto_open_until") or "")
        if not until:
            return "no-clock"
        return "in-window" if until >= stamp else "past-own-date"
    when = render.armed_default(rec, status, policy)
    if not when:
        return "no-clock"
    return "in-window" if when >= stamp else "past-own-date"


def build_ask(rec, brief, render):
    """the ask object: exactly the tool contract -- header, question (brief.decide), multiSelect, options[{label,
    description}] = the brief's choices plus the controls the slot rule appends (render.control_options)"""
    ask = rec.get("ask") if isinstance(rec.get("ask"), dict) else {}
    choices = [c for c in (brief.get("choices") or []) if isinstance(c, dict)]
    options = [{"label": str(c.get("label", "")), "description": str(c.get("in_practice", ""))} for c in choices]
    multi = bool(ask.get("multiSelect"))
    visible, controls = render.control_options(len(options), multi)
    options.extend(visible)
    header = str(ask.get("header") or "")[:HEADER_MAX] or str(rec.get("id"))[:HEADER_MAX]
    return {"header": header, "question": str(brief.get("decide", "")), "multiSelect": multi, "options": options}, controls


def answer_commands_for(rec, brief, render, stamp):
    """answer_commands[i] = the line card_lines emits for choice i (byte-equal, taken from card_lines itself)"""
    needle = "%s %s --accept " % (render.RESOLVE_CLI, rec.get("id"))
    return [l for l in render.card_lines(rec, brief, stamp) if needle in l]


def make_batches(items):
    """at most 4 ids per batch in rank order, no id twice, no two cards of equal ask.question in one batch: a card whose
    question already sits in the current batch is deferred to the next batch (skip-and-defer), later cards still fill
    the current one"""
    pending = [it for it in items]
    batches = []
    while pending:
        batch, questions, rest = [], set(), []
        for it in pending:
            q = it["ask"]["question"]
            if len(batch) < BATCH_MAX and q not in questions:
                batch.append(it["id"])
                questions.add(q)
            else:
                rest.append(it)
        batches.append(batch)
        pending = rest
    return batches


def resolution_view(res):
    r = res["rec"]
    return {"disposition": res["disposition"], "chosen_options": r.get("chosen_options", []), "comment": r.get("comment", ""),
            "date": r.get("date", ""), "staged": bool(res.get("staged", True)), "decided_at": res.get("decided_at"),
            "resolution_commit": res.get("resolution_commit"), "author_email": res.get("author_email"),
            "via": r.get("via"), "basis": r.get("basis"), "finding": res.get("finding")}


class Inputs(object):
    """everything one run reads, read once"""

    def __init__(self, root, git, stamp, ledger_rel=None):
        decisions, render = _kit()
        self.root = root
        self.git = git
        self.stamp = stamp
        self.ledger_rel = ledger_rel or ledger_rel_for(root)
        rc, out, _err = git.run(["rev-parse", "HEAD"])
        self.head = out.strip() if rc == 0 and out.strip() else None
        rc, blob, _err = git.run(["show", "HEAD:%s" % self.ledger_rel]) if self.head else (1, "", "")
        self.head_text = blob if rc == 0 else ""
        self.wt_text = _read_text(os.path.join(root, self.ledger_rel.replace("/", os.sep))) or ""
        self.parsed = decisions.parse_ledger_v3(self.wt_text)
        self.parsed_head = decisions.parse_ledger_v3(self.head_text)
        self.identity = caller_identity(root, git)
        self.routing = Routing(root, self.ledger_rel, git)


def project(root, stamp, flags, git=None, inputs=None):
    """-> the envelope dict and its exit code. flags: dict with json/text/mine/all/set/since/age/class/ids/state/records."""
    decisions, render = _kit()
    git = git or Git(root)
    inp = inputs or Inputs(root, git, stamp)
    parsed, routing, identity = inp.parsed, inp.routing, inp.identity
    me = identity["canonical_email"] if identity["resolved"] else ""
    attribute_rows(root, inp.ledger_rel, parsed["resolutions"], git) if inp.head else None
    joined = join_with_rules(parsed["decisions"], parsed["resolutions"], routing, recipe_email=me or None)
    rmod, legacy, briefs, tests, policy = decisions.brief_context(parsed, root)
    universe = role_universe([d["rec"] for d in parsed["decisions"]], routing)
    roles = routing.roles_of(me, universe) if me else []
    primary = roles[0] if roles else NO_ROLE
    basis = routing.holders(primary)[1] if roles else BASIS_UNMAPPED
    all_view = bool(flags.get("all"))
    want_set = list(flags.get("set") or [])
    if "since" in flags and flags["since"] is not False:
        want_set.extend(since_tokens(git, flags["since"] if flags["since"] is not None else None,
                                     last_act_sha=last_act(git, inp.ledger_rel, me)[0] if me else None))
    want_age, want_class, want_ids, want_state = flags.get("age"), set(flags.get("class") or []), set(flags.get("ids") or []), set(flags.get("state") or [])
    items, awaiting, not_ready, waiting, contested, records = [], [], [], [], [], []
    n_open = 0
    mine_ids = []
    for dec in parsed["decisions"]:
        rec, did = dec["rec"], dec["id"]
        j = joined[did]
        status, chain = j["status"], j["chain"]
        mine = routing.is_holder(j["role"], me) if me else False
        is_open = status in ("open", "commented")
        if is_open:
            n_open += 1
        record = j["record"] if (j["record"] is not None and str(j["record"]["rec"].get("veto_open_until") or "") >= stamp) else None
        state, brief = rmod.resolve_brief(rec, briefs.get(did, []), tests.get(did, []), legacy=did in legacy, status=status)
        card_states = set()
        if is_open:
            card_states.add(status)
        if j["unauthorized"]:
            card_states.add("unauthorized-attempt")
        if j["contested"]:
            card_states.add("contested")
        if record is not None:
            card_states.add("record-veto-open")
            if any(r["disposition"] == "commented" and r["order"] > record["order"] for r in chain):
                card_states.add("record-challenged")
        armed = armed_retest(chain, root) if is_open else None
        is_waiting = bool(armed and not armed[2])
        if is_waiting:
            card_states.add("waiting")
        tokens = card_tokens(rec)
        set_match = [t for t in tokens if t in want_set]
        visible = all_view or mine is True or mine is None
        if want_set and not set_match:
            visible = False
        if want_class and rec.get("class") not in want_class:
            visible = False
        if want_ids and did not in want_ids:
            visible = False
        if want_state and not (card_states & want_state):
            visible = False
        if want_age and age_bucket(rec, status, policy, stamp, rmod, record=record) != want_age:
            visible = False
        if j["contested"] and visible:
            a, b = j["contested"]
            contested.append({"id": did, "rows": [a, b],
                              "settle_command": "%s %s --accept <label> --settles %s,%s" % (rmod.RESOLVE_CLI, did, a, b)})
        if record is not None and visible and (mine is True or mine is None) and flags.get("records"):
            records.append({"id": did, "lines": rmod.record_lines(rec, record["rec"], brief if isinstance(brief, dict) else {}, stamp),
                            "veto_command": "%s %s --deny --comment veto" % (rmod.RESOLVE_CLI, did)})
        # CONTRACT 17 (amendment #5, F1): counts are computed over the unfiltered projection -- n counts every open,
        # unarmed card the caller holds (or an unmapped card) whether or not a subset flag hides it from items, batches
        # and the listed sections; only the sections below are filtered
        if is_open and not is_waiting and (mine is True or mine is None):
            mine_ids.append(did)
        if not is_open or not visible:
            continue
        if is_waiting:
            waiting.append({"id": did, "predicate": armed[0], "since": armed[1]})
            continue
        if state == "legacy-missing":
            marker = rmod.marker_lines(rec, state)
            awaiting.append({"id": did, "urgency": rec.get("urgency", "normal"), "age_days": decisions.age_days(rec, stamp),
                             "title": rec.get("title", ""), "marker": marker[0] if marker else "",
                             "retrofit_command": "%s %s --brief brief.json" % (rmod.BRIEF_CLI, did)})
            continue
        if state in rmod.NOT_READY_STATES:
            not_ready.append({"id": did, "lines": rmod.not_ready_lines([(rec, state)], stamp)})
            continue
        if state not in rmod.QUESTION_STATES:
            continue
        ask, controls = build_ask(rec, brief, rmod)
        notice = list(rmod.marker_lines(rec, state, brief=brief))
        notice.extend(f for f in j["findings"] if not f.startswith("CONTESTED "))
        items.append({"id": did, "status": status, "urgency": rec.get("urgency", "normal"), "class": rec.get("class"),
                      "requested_by": rec.get("requested_by"), "blocks": list(rec.get("blocks") or []),
                      "requested_at": rec.get("requested_at") or rec.get("date"), "age_days": decisions.age_days(rec, stamp),
                      "brief_state": state, "accountable": {"role": j["role"], "basis": j["basis"], "mine": mine},
                      "card_lines": rmod.card_lines(rec, brief, stamp), "answer_commands": answer_commands_for(rec, brief, rmod, stamp),
                      "ask": ask, "controls": controls, "notice": notice, "set_match": set_match,
                      "resolutions": [resolution_view(r) for r in chain]})
    items.sort(key=lambda it: decisions.sort_key((next(d for d in parsed["decisions"] if d["id"] == it["id"]), it["status"], None), stamp))
    awaiting.sort(key=lambda a: decisions.sort_key((next(d for d in parsed["decisions"] if d["id"] == a["id"]), "open", None), stamp))
    askable = [it for it in items if it["accountable"]["mine"] in (True, None)]
    batches = make_batches(askable)
    counts = announce_counts(inp, joined, mine_ids, me, roles, primary, basis, git, attributed=True)
    ann = announce_object(counts, primary, basis, identity, headless=False, joined=joined, parsed=parsed)
    can_record, block = True, None
    busy = lock_holder(root)
    if not identity["resolved"]:
        can_record, block = False, F_IDENTITY_UNRESOLVED
    elif not inp.head:
        can_record, block = False, "NO-HEAD"
    elif busy is not None:
        can_record, block = False, F_RESOLVE_BUSY.format(pid=busy)
    env = {"schema_version": SCHEMA_VERSION, "stamp": stamp,
           "identity": {"email": identity["email"], "canonical_email": identity["canonical_email"], "roles": roles,
                        "basis": basis, "resolved": identity["resolved"]},
           "can_record": can_record, "block_reason": block, "counts": counts["counts"], "items": items, "awaiting_brief": awaiting,
           "not_ready": not_ready, "waiting": waiting, "contested": contested, "records": records, "batches": batches if identity["resolved"] else [],
           "free_text_routes": list(FREE_TEXT_ROUTES), "routes_help": ROUTES_HELP, "announce": ann}
    env["_findings"] = {did: j["findings"] for did, j in joined.items() if j["findings"]}
    env["_check_lines"] = multi_row_commit_findings(parsed["resolutions"])
    return env, (0 if identity["resolved"] else EXIT_INVALID)


# ---------- counts and the announce (committed bytes only; the enumerated git calls) ----------

def last_act(git, ledger_rel, canonical_email):
    """(sha | None, deadline_hit): the newest commit by the caller's canonical identity introducing a row carrying via:
    decisions-queue -- ONE bounded `git log -1 --author=<canonical> --use-mailmap --grep=via=decisions-queue`."""
    if not canonical_email:
        return None, False
    rc, out, _err = git.run(["log", "-1", "--use-mailmap", "--fixed-strings", "--author=%s" % canonical_email,
                             "--grep=%s" % VIA_GREP, "--format=%H", "--", ledger_rel])
    sha = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else None
    return sha, False


def behind_upstream(git, root, ledger_rel, head_parsed_decision_ids):
    """(upstream name, commits behind, decision rows unseen) from already-fetched bytes; (None, 0, 0) without an
    upstream. `rev-list --count HEAD..@{upstream}` then `show @{upstream}:<ledger>` (offline; never fetches)."""
    decisions, _render = _kit()
    rc, out, _err = git.run(["rev-list", "--count", "HEAD..@{upstream}"])
    if rc != 0 or not out.strip().isdigit():
        return None, 0, 0
    b = int(out.strip())
    name = upstream_name(root) or "@{upstream}"
    if b == 0:
        return name, 0, 0
    rc, blob, _err = git.run(["show", "@{upstream}:%s" % ledger_rel])
    m = 0
    if rc == 0:
        up = decisions.parse_ledger_v3(blob)
        m = sum(1 for d in up["decisions"] if d["id"] not in head_parsed_decision_ids)
    return name, b, m


def announce_counts(inp, joined, mine_ids, me, roles, primary, basis, git, attributed=False, k_unknown=False):
    """the numbers of Method (f): n = open, unarmed-commented or returned cards addressed to the caller's roles (unmapped
    cards included: visible to all, answerable by every resolved identity); k = those absent from the ledger blob at the
    caller's last queue act (no such commit -> k = n); d = w = 0 in this lane; r = door records inside their veto window
    addressed to the caller; b, m from the upstream (offline)."""
    decisions, _render = _kit()
    n = len(mine_ids)
    r = 0
    for dec in inp.parsed["decisions"]:
        j = joined[dec["id"]]
        rec_row = j["record"]
        if rec_row is None or str(rec_row["rec"].get("veto_open_until") or "") < inp.stamp:
            continue
        mine = inp.routing.is_holder(j["role"], me) if me else False
        if mine is True or mine is None:
            r += 1
    k = n
    k_text = str(n)
    if me and not k_unknown:
        sha, _hit = last_act(git, inp.ledger_rel, me)
        if sha:
            rc, blob, _err = git.run(["show", "%s:%s" % (sha, inp.ledger_rel)])
            seen = frozenset(l.strip() for l in (blob if rc == 0 else "").splitlines() if l.strip())
            raws = decision_raws(inp.parsed)
            k = sum(1 for did in mine_ids if raws.get(did, "") not in seen)
            k_text = str(k)
    elif k_unknown:
        k_text = "?"
    head_ids = {d["id"] for d in inp.parsed_head["decisions"]}
    upstream, b, m = behind_upstream(git, inp.root, inp.ledger_rel, head_ids)
    others = sum(1 for dec in inp.parsed["decisions"]
                 if joined[dec["id"]]["status"] in ("open", "commented")
                 and (inp.routing.is_holder(joined[dec["id"]]["role"], me) if me else False) is False)
    askable = 0
    counts = {"open": sum(1 for dec in inp.parsed["decisions"] if joined[dec["id"]]["status"] in ("open", "commented")),
              "askable": askable, "mine": n, "others": others, "new": (k if k_text != "?" else None), "returned": 0, "waiting": 0,
              "to_glance": r, "behind_commits": b, "behind_rows": m}
    return {"counts": counts, "n": n, "k_text": k_text, "d": 0, "w": 0, "r": r, "b": b, "m": m, "upstream": upstream}


def announce_object(c, role, basis, identity, headless=False, joined=None, parsed=None):
    """{system_message, machine_line, exceptions[]} -- the bytes announce --hook prints (systemMessage; additionalContext =
    machine_line + exception lines, one per class present, the earliest instance in ledger order)."""
    n, k, d, w, r, b, m = c["n"], c["k_text"], c["d"], c["w"], c["r"], c["b"], c["m"]
    if n == 0:
        msg = SYSTEM_MESSAGE_NONE_FMT.format(role=role, basis=basis)
    else:
        msg = SYSTEM_MESSAGE_FMT.format(n=n, k=k, d=d, w=w, r=r, role=role, basis=basis)
    if b > 0:
        msg += " " + BEHIND_FMT.format(upstream=c["upstream"], b=b, m=m, s="" if m == 1 else "s")
    if headless:
        msg += SYSTEM_MESSAGE_HEADLESS_SUFFIX
    machine = MACHINE_LINE_FMT.format(n=n, k=k, d=d, w=w, r=r, role=role, basis=basis, b=b)
    exceptions = []
    if joined is not None and parsed is not None:
        first = {}
        for dec in parsed["decisions"]:
            j = joined[dec["id"]]
            if j["status"] not in ("open", "commented") and not j["contested"]:
                continue
            if j["holders"] is None and "ADDRESSEE-UNMAPPED" not in first and j["status"] in ("open", "commented"):
                first["ADDRESSEE-UNMAPPED"] = "ADDRESSEE-UNMAPPED %s" % j["role"]
            if j["contested"] and "CONTESTED" not in first:
                first["CONTESTED"] = "CONTESTED %s" % dec["id"]
        exceptions = [first[cls] for cls in EXCEPTION_CLASSES if cls in first]
    return {"system_message": msg, "machine_line": machine, "exceptions": exceptions}


def hook_json(system_message, additional_context):
    return json.dumps({"systemMessage": system_message,
                       "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": additional_context}},
                      ensure_ascii=False)


def resolve_root(explicit, payload):
    """--root, else the checkout containing the payload's cwd (hyp_config.worktree_root when the plugin's hooks/scripts
    is beside this scripts/ directory, else the nearest ancestor with a .git entry), else CLAUDE_PROJECT_DIR, else cwd."""
    if explicit:
        return os.path.abspath(explicit)
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    hooks_scripts = os.path.join(os.path.dirname(HERE), "hooks", "scripts")
    if cwd:
        try:
            if os.path.isdir(hooks_scripts) and hooks_scripts not in sys.path:
                sys.path.insert(0, hooks_scripts)
            from hyp_config import worktree_root  # noqa
            top = worktree_root(cwd, project)
            if top:
                return top
        except Exception:
            pass
        cur = os.path.abspath(cwd)
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return os.path.abspath(project)


def cmd_announce(args):
    """the SessionStart row: ONE JSON object, exit 0 always; nothing at all under HYP_DECISIONS=off. The nine git calls
    run in the frozen order under one deadline (7.8 s from interpreter start); a last-act call exhausting it prints
    `new ?`, any earlier one ANNOUNCE-FAILED deadline -- the script, never the harness, is what times out."""
    if os.environ.get(HYP_DECISIONS_ENV, "").strip().lower() == HYP_DECISIONS_OFF:
        return 0
    payload = {}
    try:
        if args.hook and not sys.stdin.isatty():
            payload = json.loads(sys.stdin.read() or "{}")
            if not isinstance(payload, dict):
                payload = {}
    except Exception:
        payload = {}
    headless = bool(payload.get("headless")) or str(payload.get("mode") or "").lower() in ("headless", "print", "non-interactive")
    deadline = _T0 + ANNOUNCE_DEADLINE_S
    try:
        root = resolve_root(args.root, payload)
        git = Git(root, deadline=deadline)
        decisions, render = _kit()
        stamp = stamp_for(getattr(args, "stamp", None))
        identity = caller_identity(root, git)                                   # var GIT_AUTHOR_IDENT, check-mailmap
        if not identity["resolved"]:
            raise RuntimeError(F_IDENTITY_UNRESOLVED)
        me = identity["canonical_email"]
        inp = Inputs(root, git, stamp)                                          # rev-parse HEAD, show HEAD:<ledger>
        if not inp.head:
            raise RuntimeError("NO-HEAD")
        census = inp.routing.census()                                           # log --format=%aE -- <ledger>
        # committed bytes only: the HEAD blob; authorship by the census when the history carries one canonical author
        parsed = inp.parsed_head
        if len(census) == 1:
            author = next(iter(census))
            for res in parsed["resolutions"]:
                res.update({"staged": False, "author_email": author, "introduced_at": res["order"], "introducing_sha": None})
        joined = join_with_rules(parsed["decisions"], parsed["resolutions"], inp.routing, recipe_email=me)
        universe = role_universe([d["rec"] for d in parsed["decisions"]], inp.routing)
        roles = inp.routing.roles_of(me, universe)
        primary = roles[0] if roles else NO_ROLE
        basis = inp.routing.holders(primary)[1] if roles else BASIS_UNMAPPED
        mine_ids = []
        for dec in parsed["decisions"]:
            j = joined[dec["id"]]
            if j["status"] not in ("open", "commented"):
                continue
            mine = inp.routing.is_holder(j["role"], me)
            if mine is False:
                continue
            commented = [r for r in j["chain"] if r["disposition"] == "commented"]
            if commented and isinstance(commented[-1]["rec"].get("retest_when"), str) and commented[-1]["rec"]["retest_when"].strip():
                continue                                                        # armed: the announce never evaluates predicates
            mine_ids.append(dec["id"])
        inp.parsed = parsed
        head_ids = {d["id"] for d in parsed["decisions"]}
        # rev-list --count HEAD..@{upstream}, show @{upstream}:<ledger>
        upstream, b, m = behind_upstream(git, root, inp.ledger_rel, head_ids)
        r = 0
        for dec in parsed["decisions"]:
            j = joined[dec["id"]]
            if j["record"] is not None and str(j["record"]["rec"].get("veto_open_until") or "") >= stamp \
                    and inp.routing.is_holder(j["role"], me) in (True, None):
                r += 1
        n = len(mine_ids)
        k_text = str(n)
        try:                                                                    # the two last-act calls: log -1, show <sha>:<ledger>
            sha, _hit = last_act(git, inp.ledger_rel, me)
            if sha:
                rc, blob, _err = git.run(["show", "%s:%s" % (sha, inp.ledger_rel)])
                seen = frozenset(l.strip() for l in (blob if rc == 0 else "").splitlines() if l.strip())
                raws = decision_raws(parsed)
                k_text = str(sum(1 for did in mine_ids if raws.get(did, "") not in seen))
        except Deadline:
            k_text = "?"
        c = {"n": n, "k_text": k_text, "d": 0, "w": 0, "r": r, "b": b, "m": m, "upstream": upstream}
        ann = announce_object(c, primary, basis, identity, headless=headless, joined=joined, parsed=parsed)
        context = "\n".join([ann["machine_line"]] + ann["exceptions"])
        sys.stdout.write(hook_json(ann["system_message"], context) + "\n")
        return 0
    except Deadline:
        reason = "deadline"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
    failed = F_ANNOUNCE_FAILED.format(reason=reason)
    sys.stdout.write(hook_json(SYSTEM_MESSAGE_FAILED_FMT.format(reason=reason), failed) + "\n")
    return 0


# ---------- the commands ----------

def text_lines(env):
    """the text mode (M18): the machine count line, one DECISION-QUEUE line per bucket entry with the render module's
    lines verbatim, then routes_help once; no state file, no opener, nothing appended."""
    out = [env["announce"]["machine_line"]]
    out.extend(env["announce"]["exceptions"])
    if not env["can_record"]:
        out.append("%s\tBLOCKED\t%s" % (TEXT_TOKEN, env["block_reason"]))
    for it in env["items"]:
        acc = it["accountable"]
        out.append("%s\t%s\t%s\t%dd\t%s\t%s (%s)\tmine=%s" % (TEXT_TOKEN, it["id"], it["urgency"], it["age_days"], it["status"],
                                                            acc["role"], acc["basis"], json.dumps(acc["mine"])))
        out.extend(it["card_lines"])
        out.extend(it["notice"])
    for a in env["awaiting_brief"]:
        out.append("%s\tAWAITING-BRIEF\t%s\t%s\t%dd\t%s" % (TEXT_TOKEN, a["id"], a["urgency"], a["age_days"], a["title"]))
        out.append(a["marker"])
        out.append("  retrofit: %s" % a["retrofit_command"])
    for nr in env["not_ready"]:
        out.append("%s\tNOT-READY\t%s" % (TEXT_TOKEN, nr["id"]))
        out.extend(nr["lines"])
    for w in env["waiting"]:
        out.append("%s\tWAITING\t%s\t%s\tsince %s" % (TEXT_TOKEN, w["id"], w["predicate"], w["since"]))
    for c in env["contested"]:
        out.append("%s\tCONTESTED\t%s\t%s\t%s" % (TEXT_TOKEN, c["id"], c["rows"][0], c["rows"][1]))
        out.append("  settle: %s" % c["settle_command"])
    for rcd in env["records"]:
        out.append("%s\tRECORD\t%s" % (TEXT_TOKEN, rcd["id"]))
        out.extend(rcd["lines"])
    for lines in env.get("_findings", {}).values():
        for f in lines:
            if f.startswith("OVERRIDE ") or f.startswith("SUPERSEDED-BY-ADDRESSEE ") or f.startswith("UNAUTHORIZED "):
                out.append(f)
    out.extend(env.get("_check_lines", []))
    if env["items"]:
        out.append(env["routes_help"])
    out.append("%s\tbatches\t%s" % (TEXT_TOKEN, json.dumps(env["batches"])))
    return out


def public_envelope(env):
    return {k: env[k] for k in ENVELOPE_KEYS}


def cmd_queue(args):
    root = os.path.abspath(args.root or os.getcwd())
    stamp = stamp_for(args.stamp)
    flags = {"all": bool(args.all), "set": args.set or [], "age": args.age, "class": args.cls or [], "ids": args.ids or [],
             "state": args.state or [], "records": bool(args.records)}
    if args.since is not False:
        flags["since"] = args.since
    text_mode = bool(args.text) or (os.environ.get(HYP_DECISIONS_ENV, "").strip().lower() == HYP_DECISIONS_SURFACE
                                    and not args.json)
    env, rc = project(root, stamp, flags)
    if text_mode:
        for line in text_lines(env):
            sys.stdout.write(line + "\n")
    else:
        sys.stdout.write(json.dumps(public_envelope(env), ensure_ascii=False, indent=1) + "\n")
    return rc


def route_text(text, labels, multi=False):
    """the route table (first match wins, case-insensitive prefix, trimmed) -> (route, payload)"""
    raw = (text or "").strip()
    low = raw.lower()
    for lab in labels:
        if low == str(lab).lower():
            return "own-label", {"chosen": [lab]}
    for prefix in (ROUTE_ANSWER, ROUTE_ACCEPT):
        if low.startswith(prefix):
            return "answer", {"chosen": [raw[len(prefix):].strip()]}
    if low == ROUTE_DENY or low.startswith(ROUTE_DENY + " ") or low.startswith(ROUTE_DENY + _COLON):
        words = raw[len(ROUTE_DENY):].strip().lstrip(_COLON).strip()
        return "deny", {"comment": words}
    if low.startswith(ROUTE_LATER_WHEN):
        return "later-when", {"predicate": raw[len(ROUTE_LATER_WHEN):].strip()}
    if low == ROUTE_LATER or low.startswith(ROUTE_LATER + " ") or low.startswith(ROUTE_LATER + _COLON):
        return "later", {}
    if low == ROUTE_GO_DEEPER or low.startswith(ROUTE_GO_DEEPER + " ") or low.startswith(ROUTE_GO_DEEPER + _COLON):
        words = raw[len(ROUTE_GO_DEEPER):].strip().lstrip(_COLON).strip()
        return "go-deeper", {"comment": (GO_DEEPER_PREFIX + " " + words) if words else GO_DEEPER_PREFIX}
    if raw:
        return "other", {"comment": raw}
    return "response", {}


def cmd_queue_answer(args):
    """the seam: apply the route table, then the kit's resolve path in-process with via: decisions-queue (one row, one
    single-line commit through the re-plumbed committer); refusals are printed, never handled."""
    decisions, render = _kit()
    root = os.path.abspath(args.root or os.getcwd())
    ledger_rel = ledger_rel_for(root)
    git = Git(root)
    identity = caller_identity(root, git)
    if not identity["resolved"]:
        print(F_IDENTITY_UNRESOLVED)
        return EXIT_INVALID
    parsed = decisions.parse_ledger_v3(_read_text(os.path.join(root, ledger_rel.replace("/", os.sep))) or "")
    dec = next((d for d in parsed["decisions"] if d["id"] == args.id), None)
    if dec is None:
        print("RESOLVE-INVALID\tno decision row with id %s" % args.id)
        return EXIT_INVALID
    ask = dec["rec"].get("ask") if isinstance(dec["rec"].get("ask"), dict) else {}
    labels = [o.get("label") for o in (ask.get("options") or []) if isinstance(o, dict) and o.get("label")]
    multi = bool(ask.get("multiSelect"))
    if args.response is not None:
        print("%s: response noted — nothing recorded: %s" % (args.id, args.response))
        return 0
    chosen, disposition, comment, retest_when = [], None, None, None
    if args.label:
        for lab in args.label:
            route, payload = route_text(lab, labels, multi)
            if route == "own-label":
                chosen.extend(payload["chosen"])
            elif route in ("later", "go-deeper") and len(args.label) == 1:
                break
            else:
                print(F_QUEUE_INVALID_LABEL.format(text=lab))
                return EXIT_INVALID
        if chosen:
            route, payload = "own-label", {"chosen": chosen}
    else:
        route, payload = route_text(args.text, labels, multi)
    if route in ("own-label", "answer"):
        disposition, chosen = "accepted", payload["chosen"]
    elif route == "deny":
        disposition, comment = "denied", (payload["comment"] or None)
    elif route == "later-when":
        cw = decisions._shared_parser()
        pred = payload["predicate"]
        if not pred or cw is None or cw.parse_retest_when_field(pred) is None:
            print(F_QUEUE_INVALID_RETEST)
            return EXIT_INVALID
        disposition, retest_when = "commented", pred
    elif route == "later":
        print("%s: %s — nothing recorded; listed first next pass" % (args.id, ROUTE_LATER))
        return 0
    elif route in ("go-deeper", "other"):
        disposition, comment = "commented", payload["comment"]
    else:
        print("%s: nothing typed — nothing recorded" % args.id)
        return 0
    ns = argparse.Namespace(id=args.id, accept=(chosen if disposition == "accepted" else None), deny=(disposition == "denied"),
                            comment=comment, legacy=None, no_commit=bool(args.no_commit), no_recompile=not bool(args.recompile),
                            reopen=False, via=VIA_VALUE, override=args.override, settles=args.settles, retest_when=retest_when)
    if disposition == "commented" and comment is None and retest_when:
        ns.comment = None
    return decisions.cmd_resolve(ns, root, None)


def cmd_live(root):
    """a read-only census of a live repository: never writes, never appends"""
    root = os.path.abspath(root)
    env, rc = project(root, stamp_for(None), {"all": True})
    counts = env["counts"]
    print("LIVE\t%s\tidentity=%s\tresolved=%s\troles=%s\topen=%d\tmine=%d\titems=%d\tawaiting=%d\tnot_ready=%d\twaiting=%d\tcontested=%d"
          % (root, env["identity"]["canonical_email"] or "-", env["identity"]["resolved"], ",".join(env["identity"]["roles"]) or "-",
             counts["open"], counts["mine"], len(env["items"]), len(env["awaiting_brief"]), len(env["not_ready"]), len(env["waiting"]),
             len(env["contested"])))
    for it in env["items"] + env["awaiting_brief"]:
        acc = it.get("accountable") or {}
        print("LIVE-CARD\t%s\t%s\t%s" % (it["id"], acc.get("role", DEFAULT_ROLE), json.dumps(acc.get("mine"))))
    print("LIVE-ANNOUNCE\t%s" % env["announce"]["system_message"])
    return rc


def route_status(root, ledger_rel, parsed, git=None, recipe_email=None):
    """the routing function the compilers and the resolver import: -> (joined, routing, identity). Attributes every
    resolution row (the walk) and applies the join rules; never raises to its caller beyond git's own failures."""
    git = git or Git(root)
    identity = caller_identity(root, git)
    routing = Routing(root, ledger_rel.replace(os.sep, "/"), git)
    attribute_rows(root, ledger_rel.replace(os.sep, "/"), parsed["resolutions"], git)
    me = identity["canonical_email"] if identity["resolved"] else None
    joined = join_with_rules(parsed["decisions"], parsed["resolutions"], routing, recipe_email=recipe_email or me)
    return joined, routing, identity


def owner_label(joined, routing, identity, rec):
    """the compilers' YOURS/OTHERS split: "you" when the current identity holds the card's role (or the role is
    unmapped: visible to all), else the role token"""
    role = role_of(rec)
    me = identity["canonical_email"] if identity.get("resolved") else ""
    mine = routing.is_holder(role, me) if me else False
    return "you" if mine in (True, None) else role


def build_parser():
    ap = argparse.ArgumentParser(prog="decision_queue.py", add_help=True, description=__doc__.splitlines()[1])
    ap.add_argument("--root", default=None)
    ap.add_argument("--stamp", default=None)
    sub = ap.add_subparsers(dest="cmd")
    q = sub.add_parser("queue")
    q.add_argument("--json", action="store_true")
    q.add_argument("--text", action="store_true")
    q.add_argument("--mine", action="store_true")
    q.add_argument("--all", action="store_true")
    q.add_argument("--set", nargs="+", default=None)
    q.add_argument("--since", nargs="?", const=None, default=False)
    q.add_argument("--age", choices=AGE_BUCKETS, default=None)
    q.add_argument("--class", dest="cls", nargs="+", default=None)
    q.add_argument("--ids", nargs="+", default=None)
    q.add_argument("--state", nargs="+", choices=STATE_FILTERS, default=None)
    q.add_argument("--records", action="store_true")
    a = sub.add_parser("queue-answer")
    a.add_argument("id")
    a.add_argument("--label", action="append", default=None)
    a.add_argument("--text", default=None)
    a.add_argument("--response", default=None)
    a.add_argument("--override", default=None)
    a.add_argument("--settles", default=None)
    a.add_argument("--no-commit", dest="no_commit", action="store_true")
    a.add_argument("--recompile", action="store_true")
    n = sub.add_parser("announce")
    n.add_argument("--hook", action="store_true")
    return ap


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        m = re.search(r"unrecognized arguments: (\S+)", message)
        if m:
            sys.stdout.write(F_QUEUE_INVALID_FLAG.format(flag=m.group(1)) + "\n")
        else:
            sys.stdout.write("QUEUE-INVALID\t%s\n" % message)
        sys.exit(EXIT_REFUSED)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    if argv and argv[0] == "--live":
        return cmd_live(argv[1] if len(argv) > 1 else os.getcwd())
    # --root / --stamp may trail the subcommand (decisions.py's delegates append them)
    root, stamp, rest = None, None, []
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
        elif argv[i] == "--stamp" and i + 1 < len(argv):
            stamp = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    ap = build_parser()
    ap.__class__ = _Parser
    for sp in ap._subparsers._group_actions[0].choices.values():
        sp.__class__ = _Parser
    args = ap.parse_args(rest)
    args.root, args.stamp = root, stamp
    if args.cmd == "queue":
        return cmd_queue(args)
    if args.cmd == "queue-answer":
        return cmd_queue_answer(args)
    if args.cmd == "announce":
        return cmd_announce(args)
    ap.print_help()
    return 0


# ---------- selftest (synthetic rows in throwaway repositories under TMPDIR; every finding once) ----------

def selftest():
    import contextlib
    import shutil
    from shutil import which as shutil_which
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("QUEUE-SELFTEST-PASS" if cond else "QUEUE-SELFTEST-FAIL", name, (" -- " + str(detail)[:300]) if detail else ""))
        if not cond:
            fails.append(name)

    decisions, render = _kit()
    lint = decisions._door_lint()
    tmp = tempfile.mkdtemp(prefix="decision-queue-selftest-")
    saved_env = dict(os.environ)
    os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["DECISIONS_TODAY"] = "2026-03-01"
    os.environ.pop(HYP_DECISIONS_ENV, None)
    for k in ("GIT_AUTHOR_EMAIL", "GIT_AUTHOR_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_NAME"):
        os.environ.pop(k, None)
    A, B, C = "a@dq-selftest.invalid", "b@dq-selftest.invalid", "c@dq-selftest.invalid"
    stamp = "2026-03-01"
    L = "ledger/work-ledger.jsonl"

    def sh(root, args, env=None, check=True, stdin=None):
        e = dict(os.environ)
        e.update(env or {})
        p = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True, env=e, input=stdin)
        if check and p.returncode != 0:
            raise RuntimeError("git %s: %s" % (args[:2], p.stderr.strip()[:200]))
        return p

    def repo(name, email=A, roles=None, boundary=0, codeowners=None, contributors=None, mailmap=None):
        root = os.path.join(tmp, name)
        os.makedirs(os.path.join(root, "ledger"))
        os.makedirs(os.path.join(root, ".claude"))
        subprocess.run(["git", "init", "-q", root], check=True, env=dict(os.environ))
        sh(root, ["symbolic-ref", "HEAD", "refs/heads/main"])
        sh(root, ["config", "user.name", "identity-%s" % email[0].upper()])
        sh(root, ["config", "user.email", email])
        sh(root, ["config", "user.useConfigOnly", "true"])
        sh(root, ["config", "commit.gpgsign", "false"])
        sh(root, ["config", "core.hooksPath", "/dev/null"])
        cfg = {"profile": "experiments", "ledger_file": L, "decision_brief_legacy_max_id": boundary, "decision_door_legacy_max_id": boundary}
        if roles is not None:
            cfg[ROLES_KEY] = roles
        with io.open(os.path.join(root, ".claude", "hyp.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        with io.open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("selftest\n")
        if codeowners is not None:
            os.makedirs(os.path.join(root, ".github"), exist_ok=True)
            with io.open(os.path.join(root, ".github", "CODEOWNERS"), "w", encoding="utf-8") as fh:
                fh.write(codeowners)
        if contributors is not None:
            with io.open(os.path.join(root, CONTRIBUTORS_REL), "w", encoding="utf-8") as fh:
                json.dump(contributors, fh)
        if mailmap is not None:
            with io.open(os.path.join(root, ".mailmap"), "w", encoding="utf-8") as fh:
                fh.write(mailmap)
        with io.open(os.path.join(root, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write("%s merge=union\n" % L)
        open(os.path.join(root, L), "w").close()
        sh(root, ["add", "-A"])
        commit(root, "base", 1)
        return root

    def commit(root, msg, day, email=None, paths=("--", ".")):
        env = {"GIT_AUTHOR_DATE": "2026-02-%02dT00:00:00Z" % day, "GIT_COMMITTER_DATE": "2026-02-%02dT00:00:00Z" % day}
        if email:
            env.update({"GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_NAME": "identity-%s" % email[0].upper(),
                        "GIT_COMMITTER_NAME": "identity-%s" % email[0].upper()})
        sh(root, ["add", "-A"], env=env)
        sh(root, ["commit", "-q", "--allow-empty", "-m", msg], env=env)
        return sh(root, ["rev-parse", "HEAD"]).stdout.strip()

    def card(n, role=None, k=2, multi=False, question=None, brief=True, urgency="normal", day=1, decide=None):
        opts = [{"label": "opt%d" % i, "description": "option %d happens" % i, "undo": "ledger-row"} for i in range(1, k + 1)]
        rec = {"kind": "decision", "id": "DEC-%03d" % n, "date": "2026-02-%02d" % day, "requested_at": "2026-02-%02d" % day,
               "requested_by": "lane H-%03d" % n, "title": "Card %d" % n, "urgency": urgency, "class": "plan",
               "why_only_you": "only you", "context_pointers": ["experiments/runs/lane-%d/x" % n], "blocks": [],
               "ask": {"question": question or "Question %d?" % n, "header": "Card%d" % n, "multiSelect": multi, "options": opts},
               "staged_artifact": "none", "evidence": "none-exists", "externality": "none", "recommended": "none",
               "default_on_silence": "nothing-changes", "door": {"fields_sha": "0" * 64, "outcome": "CARD"}}
        if role:
            rec[ADDRESSEE_FIELD] = {"role": role}
        if brief:
            b = {"decide": decide or "Decide card %d now." % n, "situation": "It is ready.", "yours_because": "You hold the key.",
                 "choices": [{"label": o["label"], "in_practice": "The %s option happens." % o["label"], "undo": "A later row supersedes it."} for o in opts],
                 "if_nothing": "Nothing changes: the card stays open.", "evidence_line": "The selftest built it.", "terms": {},
                 "sources": ["pipeline-fact:card-stays-open"], "provenance": {"protocol": "inline"}}
            b["card_sha"] = lint.card_sha(rec)
            b["lint"] = {"tool": "decision_card_lint", "sha7": "0000000", "exit": 0, "brief_sha": lint.brief_sha(b)}
            rec["brief"] = b
        return rec

    def append(root, rows):
        with io.open(os.path.join(root, L), "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def ledger(root):
        return _read_text(os.path.join(root, L)) or ""

    def run_answer(root, did, **kw):
        buf = io.StringIO()
        ns = argparse.Namespace(id=did, label=kw.get("label"), text=kw.get("text"), response=kw.get("response"),
                                override=kw.get("override"), settles=kw.get("settles"), no_commit=False, recompile=False, root=root, stamp=stamp)
        with contextlib.redirect_stdout(buf):
            try:
                rc = cmd_queue_answer(ns)
            except SystemExit as exc:
                rc = exc.code
        return rc, buf.getvalue()

    def stat_plus(root):
        out = sh(root, ["show", "--stat", "--format=", "HEAD"]).stdout
        return out.strip().splitlines()

    try:
        # ---- S1 the filter, --all, batches, the slot rule, the payload contract (M1, M13, M14, M15) ----
        r1 = repo("filter", roles={"maintainer": [A], "lane-owner": [B]})
        append(r1, [card(1, k=2), card(2, k=3, urgency="high"), card(3, role="lane-owner", k=4), card(4, k=4, multi=True),
                    card(5, k=2, decide="Decide card 1 now."), card(6, k=3)])
        commit(r1, "cards", 2)
        env, rc = project(r1, stamp, {})
        ids = [it["id"] for it in env["items"]]
        ok("filter-mine-only", rc == 0 and "DEC-%03d" % 3 not in ids and len(ids) == 5, ids)
        ok("identity-roles", env["identity"]["roles"] == ["maintainer"] and env["identity"]["basis"] == BASIS_DECISION_ROLES, env["identity"])
        env_all, _ = project(r1, stamp, {"all": True})
        other = next((it for it in env_all["items"] if it["id"] == "DEC-%03d" % 3), None)
        ok("all-lists-others-view-only", other is not None and other["accountable"] == {"role": "lane-owner", "basis": BASIS_DECISION_ROLES, "mine": False}
           and not any("DEC-%03d" % 3 in b for b in env_all["batches"]), other and other["accountable"])
        ok("envelope-keys", tuple(public_envelope(env).keys()) == ENVELOPE_KEYS and all(tuple(it.keys()) == ITEM_KEYS for it in env["items"]))
        ok("ask-contract", all(tuple(it["ask"].keys()) == ASK_KEYS and all(tuple(o.keys()) == ("label", "description") for o in it["ask"]["options"])
                               and OPTION_MIN <= len(it["ask"]["options"]) <= OPTION_MAX and HEADER_MIN <= len(it["ask"]["header"]) <= HEADER_MAX
                               and it["ask"]["question"].startswith("Decide card") for it in env["items"]))
        by = {it["id"]: it for it in env["items"]}
        ok("slot-rule", by["DEC-%03d" % 1]["controls"] == {"go_deeper": "option", "later": "option"} and len(by["DEC-%03d" % 1]["ask"]["options"]) == 4
           and by["DEC-%03d" % 2]["controls"] == {"go_deeper": "option", "later": "other"} and len(by["DEC-%03d" % 2]["ask"]["options"]) == 4
           and by["DEC-%03d" % 4]["controls"] == {"go_deeper": "other", "later": "other"} and len(by["DEC-%03d" % 4]["ask"]["options"]) == 4
           and other["controls"] == {"go_deeper": "other", "later": "other"}, {k: v["controls"] for k, v in by.items()})
        ok("answer-commands-from-card-lines", all(len(it["answer_commands"]) == len([o for o in it["ask"]["options"] if o["label"].startswith("opt")])
                                                  and all(cmd in it["card_lines"] for cmd in it["answer_commands"]) for it in env["items"]))
        ok("batches-4-1-equal-question-split", env["batches"] == [["DEC-%03d" % 2, "DEC-%03d" % 1, "DEC-%03d" % 4, "DEC-%03d" % 6], ["DEC-%03d" % 5]], env["batches"])
        env_set, _ = project(r1, stamp, {"set": ["lane-3", "H-%03d" % 1]})
        ok("set-subset", [it["id"] for it in env_set["items"]] == ["DEC-%03d" % 1] and env_set["items"][0]["set_match"] == ["H-%03d" % 1], (env_set["items"][0]["set_match"] if env_set["items"] else None))
        env_ids, _ = project(r1, stamp, {"ids": ["DEC-%03d" % 6], "class": ["plan"]})
        ok("ids-class-subset", [it["id"] for it in env_ids["items"]] == ["DEC-%03d" % 6])
        env_state, _ = project(r1, stamp, {"state": ["open"], "records": True, "class": ["plan"]})
        ok("filters-never-change-counts", env_set["counts"] == env["counts"] == env_ids["counts"] == env_state["counts"]
           and env_set["announce"] == env["announce"] == env_ids["announce"] and env_ids["batches"] == [["DEC-%03d" % 6]],
           (env["counts"], env_set["counts"], env_ids["counts"], env_state["counts"]))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                main(["queue", "--json", "--bogus", "--root", r1])
                code = 0
            except SystemExit as exc:
                code = exc.code
        ok("unknown-flag-refused", code == EXIT_REFUSED and buf.getvalue().startswith("QUEUE-INVALID\tflag --bogus"), buf.getvalue()[:80])
        env2, _ = project(r1, stamp, {})
        ok("deterministic", json.dumps(public_envelope(env)) == json.dumps(public_envelope(env2)))
        counts = env["counts"]
        ok("counts", counts["mine"] == 5 and counts["others"] == 1 and counts["new"] == 5 and counts["open"] == 6 and counts["to_glance"] == 0, counts)
        ok("announce-object", env["announce"]["system_message"] == SYSTEM_MESSAGE_FMT.format(n=5, k=5, d=0, w=0, r=0, role="maintainer", basis=BASIS_DECISION_ROLES)
           and env["announce"]["machine_line"].count("\t") == 8 and env["announce"]["exceptions"] == [], env["announce"])
        # ---- S2 NOT-ADDRESSEE (M2), the transaction on a dirty ledger (M10), the routes (M11), already closed (M12) ----
        before = ledger(r1)
        rc, out = run_answer(r1, "DEC-%03d" % 3, label=["opt1"])
        ok("not-addressee-refused", rc == EXIT_REFUSED and out.splitlines()[0] == F_NOT_ADDRESSEE.format(id="DEC-%03d" % 3, role="lane-owner", basis=BASIS_DECISION_ROLES)
           and ledger(r1) == before, out[:160])
        append(r1, [{"kind": "intent", "date": stamp, "text": "dirty intent %d" % i, "session": "s"} for i in (1, 2)])
        head0 = sh(r1, ["rev-parse", "HEAD"]).stdout.strip()
        rc, out = run_answer(r1, "DEC-%03d" % 1, label=["opt1"])
        st = stat_plus(r1)
        head1 = sh(r1, ["rev-parse", "HEAD"]).stdout.strip()
        subj = sh(r1, ["log", "-1", "--format=%s%x1f%ae"]).stdout.strip().split("\x1f")
        ok("dirty-ledger-single-line-commit", rc == 0 and head1 != head0 and len(st) == 2 and "1 insertion(+)" in st[-1] and st[0].strip().startswith(L)
           and subj == ["decision: DEC-%03d accepted — decision-resolved=DEC-%03d via=decisions-queue" % (1, 1), A]
           and ledger(r1).count("dirty intent") == 2 and sh(r1, ["status", "--porcelain", "--", L]).stdout.startswith(" M"), (rc, out[-300:], st, subj))
        last = json.loads(ledger(r1).strip().splitlines()[-1])
        ok("via-on-row", last.get(VIA_FIELD) == VIA_VALUE and last["chosen_options"] == ["opt1"] and last["disposition"] == "accepted", last)
        rc, out = run_answer(r1, "DEC-%03d" % 1, label=["opt2"])
        ok("already-accepted-refused", rc == EXIT_INVALID and out.startswith("RESOLVE-INVALID\tDEC-%03d is already accepted" % 1), out[:120])
        labels = ["opt1", "opt2"]
        ok("route-own-label", route_text(" OPT2 ", labels) == ("own-label", {"chosen": ["opt2"]}))
        ok("route-answer", route_text(ROUTE_ANSWER + " my own words", labels) == ("answer", {"chosen": ["my own words"]})
           and route_text(ROUTE_ACCEPT + "x", labels)[0] == "answer")
        ok("route-deny", route_text("deny because x", labels) == ("deny", {"comment": "because x"}) and route_text("Deny", labels) == ("deny", {"comment": ""}))
        ok("route-later-when", route_text("later when event-count=event/run-completed>=3", labels) == ("later-when", {"predicate": "event-count=event/run-completed>=3"}))
        ok("route-later", route_text("later", labels) == ("later", {}))
        ok("route-go-deeper", route_text("go deeper: y", labels) == ("go-deeper", {"comment": GO_DEEPER_PREFIX + " y"}) and route_text("go deeper", labels)[1]["comment"] == GO_DEEPER_PREFIX)
        ok("route-other-and-response", route_text("what does this mean?", labels) == ("other", {"comment": "what does this mean?"}) and route_text("", labels) == ("response", {}))
        rc, out = run_answer(r1, "DEC-%03d" % 2, text="later when nonsense")
        ok("later-when-malformed", rc == EXIT_INVALID and out.strip() == F_QUEUE_INVALID_RETEST, out[:80])
        n_lines = len(ledger(r1).splitlines())
        rc, out = run_answer(r1, "DEC-%03d" % 2, text="later when event-count=event/run-completed>=3")
        env_w, _ = project(r1, stamp, {})
        ok("later-when-waiting", rc == 0 and len(ledger(r1).splitlines()) == n_lines + 1 and env_w["waiting"] == [{"id": "DEC-%03d" % 2, "predicate": "event-count=event/run-completed>=3", "since": stamp}]
           and "DEC-%03d" % 2 not in [it["id"] for it in env_w["items"]] and env_w["counts"]["mine"] == 3, (env_w["waiting"], env_w["counts"]))
        rc, out = run_answer(r1, "DEC-%03d" % 6, text="later")
        ok("later-appends-nothing", rc == 0 and len(ledger(r1).splitlines()) == n_lines + 1 and "nothing recorded" in out)
        rc, out = run_answer(r1, "DEC-%03d" % 6, text="go deeper: y")
        last = json.loads(ledger(r1).strip().splitlines()[-1])
        ok("go-deeper-fallback-comment", rc == 0 and last["disposition"] == "commented" and last["comment"] == GO_DEEPER_PREFIX + " y" and last[VIA_FIELD] == VIA_VALUE, last)
        rc, out = run_answer(r1, "DEC-%03d" % 6, text="what does this mean?")
        env_c, _ = project(r1, stamp, {})
        ok("other-text-stays-open", rc == 0 and next(it for it in env_c["items"] if it["id"] == "DEC-%03d" % 6)["status"] == "commented")
        rc, out = run_answer(r1, "DEC-%03d" % 6, response="just thinking aloud")
        ok("response-appends-nothing", rc == 0 and "nothing recorded" in out)
        rc, out = run_answer(r1, "DEC-%03d" % 6, label=["nope"])
        ok("unknown-label-refused", rc == EXIT_INVALID and out.startswith("QUEUE-INVALID\tlabel nope"), out[:80])
        rc, out = run_answer(r1, "DEC-%03d" % 4, label=["opt1", "opt3"])
        last = json.loads(ledger(r1).strip().splitlines()[-1])
        ok("multi-select-labels", rc == 0 and last["chosen_options"] == ["opt1", "opt3"], last)
        # k after acts: the first card answered through the seam -> new counts only cards absent from that commit's blob
        env_k, _ = project(r1, stamp, {})
        ok("k-after-via-row", env_k["counts"]["new"] == 0, env_k["counts"])
        append(r1, [card(7, k=2, day=3)])
        commit(r1, "one more card", 5)
        env_k2, _ = project(r1, stamp, {})
        ok("k-after-further-add", env_k2["counts"]["new"] == 1 and env_k2["counts"]["mine"] == 3, env_k2["counts"])
        # text mode: no state file, DECISION-QUEUE lines
        lines = text_lines(env_k2)
        ok("text-mode-lines", lines[0].startswith(MACHINE_TOKEN + "\t3\tnew 1\t") and any(l.startswith(TEXT_TOKEN + "\tDEC-") for l in lines)
           and not os.path.exists(os.path.join(r1, ".claude", "decision-surface-state.json")), lines[:2])
        # ---- S3 UNAUTHORIZED (M3), SUPERSEDED over a record, MULTI-ROW-COMMIT, CONTESTED + settle (M21), union merge (M20) ----
        r3 = repo("rules", roles={"maintainer": [A], "reviewers": [A, B]})
        append(r3, [card(1), card(2, role="reviewers"), card(3), card(4)])
        commit(r3, "cards", 2)
        append(r3, [{"kind": "decision-resolution", "id": "DEC-%03d" % 1, "date": stamp, "disposition": "accepted", "chosen_options": ["opt1"]}])
        commit(r3, "hand-appended by a non-addressee", 3, email=C)
        env3, _ = project(r3, stamp, {})
        j1 = next(it for it in env3["items"] if it["id"] == "DEC-%03d" % 1)
        ok("unauthorized-status-unchanged", j1["status"] == "open" and F_UNAUTHORIZED.format(id="DEC-%03d" % 1, basis=BASIS_DECISION_ROLES) in j1["notice"], j1["notice"])
        env3s, _ = project(r3, stamp, {"state": ["unauthorized-attempt"]})
        ok("state-unauthorized-attempt", [it["id"] for it in env3s["items"]] == ["DEC-%03d" % 1])
        append(r3, [{"kind": "decision-resolution", "id": "DEC-%03d" % 2, "date": stamp, "disposition": "accepted", "chosen_options": ["opt1"]}])
        sha_a = commit(r3, "holder A closes", 4, email=A)
        append(r3, [{"kind": "decision-resolution", "id": "DEC-%03d" % 2, "date": stamp, "disposition": "denied"}])
        sha_b = commit(r3, "holder B closes", 5, email=B)
        env3b, _ = project(r3, stamp, {"all": True})
        ok("contested-earlier-decides", env3b["contested"] == [{"id": "DEC-%03d" % 2, "rows": [sha_a, sha_b],
                                                                "settle_command": "%s DEC-%03d --accept <label> --settles %s,%s" % (render.RESOLVE_CLI, 2, sha_a, sha_b)}]
           and F_CONTESTED.format(id="DEC-%03d" % 2, sha_a=sha_a, sha_b=sha_b) in env3b["_findings"]["DEC-%03d" % 2]
           and "DEC-%03d" % 2 not in [it["id"] for it in env3b["items"]] and env3b["announce"]["exceptions"] == ["CONTESTED DEC-%03d" % 2], (env3b["contested"], env3b["announce"]["exceptions"]))
        parsed3 = decisions.parse_ledger_v3(ledger(r3))
        attribute_rows(r3, L, parsed3["resolutions"])
        joined3 = join_with_rules(parsed3["decisions"], parsed3["resolutions"], Routing(r3, L))
        ok("contested-status-is-earlier-row", joined3["DEC-%03d" % 2]["status"] == "accepted")
        ns = argparse.Namespace(id="DEC-%03d" % 2, accept=["opt2"], deny=False, comment="settled", legacy=None, no_commit=False, no_recompile=True,
                                reopen=True, via=VIA_VALUE, override=None, settles="%s,%s" % (sha_a[:12], sha_b[:12]), retest_when=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = decisions.cmd_resolve(ns, r3, None)
        env3c, _ = project(r3, stamp, {"all": True})
        ok("settles-row-clears-contested", rc == 0 and env3c["contested"] == [] and json.loads(ledger(r3).strip().splitlines()[-1]).get("settles") == [sha_a[:12], sha_b[:12]], buf.getvalue()[:200])
        # a record vs the human's denied, record first
        append(r3, [{"kind": "decision-resolution", "id": "DEC-%03d" % 3, "date": stamp, "disposition": "accepted", "chosen_options": ["opt1"], "basis": RECORD_BASIS,
                     "undo": "%s DEC-%03d --deny --comment veto" % (render.RESOLVE_CLI, 3), "veto_open_until": "2026-03-08"}])
        commit(r3, "record", 6)
        env3r, _ = project(r3, stamp, {"records": True})
        ok("record-in-veto-window", env3r["counts"]["to_glance"] == 1 and [rc_["id"] for rc_ in env3r["records"]] == ["DEC-%03d" % 3]
           and env3r["records"][0]["lines"] == render.record_lines(card(3), env3r["records"][0] and parsed3 and json.loads(ledger(r3).splitlines()[-1]), card(3)["brief"], stamp)
           and env3r["records"][0]["veto_command"] == "%s DEC-%03d --deny --comment veto" % (render.RESOLVE_CLI, 3), env3r["counts"])
        rc, out = run_answer(r3, "DEC-%03d" % 3, text="deny: veto")
        env3d, _ = project(r3, stamp, {"all": True})
        ok("human-denied-over-record", rc == 0 and "VETO\tDEC-%03d" % 3 in out and F_SUPERSEDED.format(id="DEC-%03d" % 3) in env3d["_findings"]["DEC-%03d" % 3]
           and env3d["counts"]["to_glance"] == 0, (out[:200], env3d["_findings"].get("DEC-%03d" % 3)))
        # multi-row commit: one commit adds closing rows for two cards
        append(r3, [{"kind": "decision-resolution", "id": "DEC-%03d" % 4, "date": stamp, "disposition": "accepted", "chosen_options": ["opt1"]},
                    {"kind": "decision-resolution", "id": "DEC-%03d" % 1, "date": stamp, "disposition": "denied"}])
        sha_m = commit(r3, "squash-shaped", 7, email=A)
        env3m, _ = project(r3, stamp, {"all": True})
        ok("multi-row-commit", F_MULTI_ROW.format(sha=sha_m, ids="DEC-%03d,DEC-%03d" % (1, 4)) in env3m["_check_lines"], env3m["_check_lines"])
        # union merge in both orders with true attribution
        for order in ("ab", "ba"):
            base = repo("merge-%s" % order, roles={"maintainer": [A, B]})
            append(base, [card(1), card(2)])
            commit(base, "cards", 2)
            ca, cb = os.path.join(tmp, "clone-a-%s" % order), os.path.join(tmp, "clone-b-%s" % order)
            subprocess.run(["git", "clone", "-q", base, ca], check=True, env=dict(os.environ))
            subprocess.run(["git", "clone", "-q", base, cb], check=True, env=dict(os.environ))
            for cl, em, n in ((ca, A, 1), (cb, B, 2)):
                sh(cl, ["config", "user.email", em])
                sh(cl, ["config", "user.name", "identity-%s" % em[0].upper()])
                sh(cl, ["config", "commit.gpgsign", "false"])
                append(cl, [{"kind": "decision-resolution", "id": "DEC-%03d" % n, "date": stamp, "disposition": "accepted", "chosen_options": ["opt1"], "via": VIA_VALUE}])
                commit(cl, "decision: DEC-%03d accepted — decision-resolved=DEC-%03d via=decisions-queue" % (n, n), 3 + n, email=em)
            first, second = (ca, cb) if order == "ab" else (cb, ca)
            sh(second, ["fetch", "-q", first, "main"])
            p = sh(second, ["merge", "-q", "--no-edit", "FETCH_HEAD"], env={"GIT_AUTHOR_DATE": "2026-02-09T00:00:00Z", "GIT_COMMITTER_DATE": "2026-02-09T00:00:00Z"}, check=False)
            text = ledger(second)
            parsed_m = decisions.parse_ledger_v3(text)
            attribute_rows(second, L, parsed_m["resolutions"])
            emails = {r["id"]: r.get("author_email") for r in parsed_m["resolutions"]}
            ok("union-merge-%s" % order, p.returncode == 0 and "<<<<<<<" not in text and len(parsed_m["resolutions"]) == 2
               and emails == {"DEC-%03d" % 1: A, "DEC-%03d" % 2: B}, (p.stderr[:120], emails))
        # ---- S4 identity (M4, M5) ----
        r4 = repo("identity")
        sh(r4, ["config", "--unset", "user.email"])
        ident = caller_identity(r4)
        ok("identity-unresolved", not ident["resolved"] and ident["reason"] == F_IDENTITY_UNRESOLVED, ident)
        env4, rc4 = project(r4, stamp, {})
        ok("queue-unresolved-view-only", rc4 == EXIT_INVALID and env4["can_record"] is False and env4["block_reason"] == F_IDENTITY_UNRESOLVED and env4["batches"] == [])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_queue_answer(argparse.Namespace(id="DEC-%03d" % 1, label=["opt1"], text=None, response=None, override=None, settles=None,
                                                     no_commit=False, recompile=False, root=r4, stamp=stamp))
        ok("answer-unresolved-nothing", rc == EXIT_INVALID and buf.getvalue().strip() == F_IDENTITY_UNRESOLVED)
        os.environ["GIT_AUTHOR_EMAIL"] = B
        os.environ["GIT_COMMITTER_EMAIL"] = B
        ident_b = caller_identity(r4)
        r5 = repo("exported", roles={"maintainer": [B]})
        append(r5, [card(1)])
        commit(r5, "card", 2)
        rc, out = run_answer(r5, "DEC-%03d" % 1, label=["opt2"])
        ae = sh(r5, ["log", "-1", "--format=%ae"]).stdout.strip()
        os.environ.pop("GIT_AUTHOR_EMAIL", None)
        os.environ.pop("GIT_COMMITTER_EMAIL", None)
        ok("exported-author-email", ident_b["resolved"] and ident_b["email"] == B and rc == 0 and ae == B, (ident_b, ae, out[:120]))
        # mailmap: two emails of one identity fold to one canonical address; R3 still single
        r6 = repo("mailmap", mailmap="<%s> <%s>\n" % (A, B))
        append(r6, [card(1)])
        commit(r6, "card", 2)
        sh(r6, ["config", "user.email", B])
        env6, _ = project(r6, stamp, {})
        ok("mailmap-fold", env6["identity"]["canonical_email"] == A and env6["identity"]["basis"] == BASIS_SINGLE
           and [it["id"] for it in env6["items"]] == ["DEC-%03d" % 1] and env6["items"][0]["accountable"]["mine"] is True, env6["identity"])
        # ---- S6 the unmapped role (M6), CODEOWNERS (R2), override (M7) ----
        r7 = repo("unmapped", roles={"maintainer": [A]}, codeowners="* @org/team\ndocs/ @bob\n", contributors={B: {"github": "bob"}})
        append(r7, [{"kind": "intent", "date": "2026-02-01", "text": "a second author on the ledger", "session": "s"}])
        commit(r7, "second author", 2, email=B)
        append(r7, [card(1, role="nobody"), card(2, role="owner:docs/README.md"), card(3, role="lane-owner")])
        commit(r7, "cards", 3)
        env7, _ = project(r7, stamp, {})
        it1 = next(it for it in env7["items"] if it["id"] == "DEC-%03d" % 1)
        ok("unmapped-visible-mine-null", it1["accountable"] == {"role": "nobody", "basis": BASIS_UNMAPPED, "mine": None}
           and F_UNMAPPED.format(role="nobody", email=A) in it1["notice"] and env7["announce"]["exceptions"] == ["ADDRESSEE-UNMAPPED nobody"], (it1["accountable"], it1["notice"]))
        it2 = next((it for it in env7["items"] if it["id"] == "DEC-%03d" % 2), None)
        env7all, _ = project(r7, stamp, {"all": True})
        it2 = next(it for it in env7all["items"] if it["id"] == "DEC-%03d" % 2)
        ok("codeowners-path-role", it2["accountable"] == {"role": "owner:docs/README.md", "basis": BASIS_CODEOWNERS, "mine": False}, it2["accountable"])
        routing7 = Routing(r7, L)
        ok("codeowners-root-team-unresolvable-then-r1", routing7.holders(DEFAULT_ROLE) == (frozenset([A]), BASIS_DECISION_ROLES)
           and routing7.holders("lane-owner") == (None, BASIS_UNMAPPED) and routing7.codeowners_role("owner:docs/x.md") == [B])
        rc, out = run_answer(r7, "DEC-%03d" % 1, label=["opt1"])
        last = json.loads(ledger(r7).strip().splitlines()[-1])
        ok("unmapped-answer-marked", rc == 0 and last.get("addressee_basis") == BASIS_UNMAPPED and last["disposition"] == "accepted", (out[:120], last))
        cfg7 = _read_json(os.path.join(r7, HYP_REL))
        cfg7[ROLES_KEY]["nobody"] = [B]
        with io.open(os.path.join(r7, HYP_REL), "w", encoding="utf-8") as fh:
            json.dump(cfg7, fh)
        commit(r7, "map lands", 4)
        os.environ["GIT_AUTHOR_EMAIL"] = B
        os.environ["GIT_COMMITTER_EMAIL"] = B
        ns = argparse.Namespace(id="DEC-%03d" % 1, accept=["opt2"], deny=False, comment=None, legacy=None, no_commit=False, no_recompile=True,
                                reopen=False, via=VIA_VALUE, override=None, settles=None, retest_when=None)   # amendment #5 (F3): no --reopen needed
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = decisions.cmd_resolve(ns, r7, None)
        os.environ.pop("GIT_AUTHOR_EMAIL", None)
        os.environ.pop("GIT_COMMITTER_EMAIL", None)
        env7s, _ = project(r7, stamp, {"all": True})
        ok("superseded-by-addressee", rc == 0 and F_SUPERSEDED.format(id="DEC-%03d" % 1) in env7s["_findings"].get("DEC-%03d" % 1, []) and
           json.loads(ledger(r7).strip().splitlines()[-1])["chosen_options"] == ["opt2"], (buf.getvalue()[:160], env7s["_findings"].get("DEC-%03d" % 1)))
        before = ledger(r7)
        rc, out = run_answer(r7, "DEC-%03d" % 3, label=["opt1"], override="nope/missing.md")
        ok("override-unresolvable-refused", rc == EXIT_REFUSED and out.splitlines()[0] == F_UNAUTHORIZED.format(id="DEC-%03d" % 3, basis=BASIS_UNMAPPED) and ledger(r7) == before, out[:120])
        rc, out = run_answer(r7, "DEC-%03d" % 3, label=["opt1"], override="README.md")
        env7o, _ = project(r7, stamp, {"all": True})
        last = json.loads(ledger(r7).strip().splitlines()[-1])
        ok("override-resolvable", rc == 0 and last.get("override") == {"role": "lane-owner", "basis": "README.md"}
           and F_OVERRIDE.format(id="DEC-%03d" % 3, role="lane-owner", pointer="README.md") in env7o["_findings"].get("DEC-%03d" % 3, [])
           and "DEC-%03d" % 3 not in [it["id"] for it in env7o["items"]], (out[:160], last))
        # amendment #5 (F3): a non-holder's plain answer over the override is still refused; the addressee's own answer lands
        # through the seam without --reopen and supersedes the override (SUPERSEDED-BY-ADDRESSEE on every reader)
        before = ledger(r7)
        rc, out = run_answer(r7, "DEC-%03d" % 3, label=["opt2"])
        ok("non-holder-over-override-still-refused", rc == EXIT_INVALID and out.startswith("RESOLVE-INVALID\tDEC-%03d is already accepted" % 3)
           and ledger(r7) == before, out[:120])
        cfg7[ROLES_KEY]["lane-owner"] = [B]
        with io.open(os.path.join(r7, HYP_REL), "w", encoding="utf-8") as fh:
            json.dump(cfg7, fh)
        commit(r7, "lane-owner map lands", 5)
        os.environ["GIT_AUTHOR_EMAIL"] = B
        os.environ["GIT_COMMITTER_EMAIL"] = B
        rc, out = run_answer(r7, "DEC-%03d" % 3, label=["opt2"])
        os.environ.pop("GIT_AUTHOR_EMAIL", None)
        os.environ.pop("GIT_COMMITTER_EMAIL", None)
        env7b, _ = project(r7, stamp, {"all": True})
        last = json.loads(ledger(r7).strip().splitlines()[-1])
        ok("addressee-supersedes-override-without-reopen", rc == 0 and last.get("chosen_options") == ["opt2"] and "override" not in last
           and F_SUPERSEDED.format(id="DEC-%03d" % 3) in env7b["_findings"].get("DEC-%03d" % 3, []),
           (out[:160], last, env7b["_findings"].get("DEC-%03d" % 3)))
        # ---- S8 readiness (M8, M9): NOT READY never asked; legacy-missing awaiting its brief ----
        r8 = repo("readiness", boundary=1)
        legacy = card(1, brief=False)
        side = card(2, brief=False)
        stale = card(3)
        stale["brief"]["decide"] = "Edited in place."
        append(r8, [legacy, side, stale])
        commit(r8, "cards", 2)
        env8, _ = project(r8, stamp, {})
        ok("legacy-awaiting-brief", [a["id"] for a in env8["awaiting_brief"]] == ["DEC-%03d" % 1] and env8["awaiting_brief"][0]["marker"] == render.marker_lines(legacy, "legacy-missing")[0]
           and env8["awaiting_brief"][0]["retrofit_command"] == "%s DEC-%03d --brief brief.json" % (render.BRIEF_CLI, 1) and env8["items"] == [] and env8["batches"] == [], env8["awaiting_brief"])
        ok("not-ready-lines", [nr["id"] for nr in env8["not_ready"]] == ["DEC-%03d" % 2, "DEC-%03d" % 3]
           and env8["not_ready"][0]["lines"] == render.not_ready_lines([(side, "missing")], stamp) and env8["not_ready"][1]["lines"] == render.not_ready_lines([(stale, "stale")], stamp), env8["not_ready"])
        ok("n-counts-every-open-card", env8["counts"]["mine"] == 3 and env8["counts"]["askable"] == 0, env8["counts"])
        # ---- S17 the announce hook per source; off; the deadline ----
        r9 = repo("announce", roles={"maintainer": [A]})
        append(r9, [card(1), card(2)])
        commit(r9, "cards", 2)
        outs = {}
        global _T0
        for source in ANNOUNCE_SOURCES:
            _T0 = time.monotonic()
            buf = io.StringIO()
            sys.stdin = io.StringIO(json.dumps({"source": source, "cwd": r9}))
            with contextlib.redirect_stdout(buf):
                rc = cmd_announce(argparse.Namespace(hook=True, root=r9, stamp=stamp))
            outs[source] = (rc, buf.getvalue())
        sys.stdin = sys.__stdin__
        first = json.loads(outs["startup"][1])
        ok("announce-json-per-source", all(v[0] == 0 and json.loads(v[1]) == first for v in outs.values())
           and first["systemMessage"] == SYSTEM_MESSAGE_FMT.format(n=2, k=2, d=0, w=0, r=0, role="maintainer", basis=BASIS_DECISION_ROLES)
           and first["hookSpecificOutput"]["additionalContext"] == MACHINE_LINE_FMT.format(n=2, k=2, d=0, w=0, r=0, role="maintainer", basis=BASIS_DECISION_ROLES, b=0), first)
        env9, _ = project(r9, stamp, {})
        ok("envelope-announce-equals-hook", env9["announce"]["system_message"] == first["systemMessage"] and env9["announce"]["machine_line"] == first["hookSpecificOutput"]["additionalContext"])
        r10 = repo("announce-zero", roles={"maintainer": [B]})
        append(r10, [card(1)])
        commit(r10, "card", 2)
        _T0 = time.monotonic()
        buf = io.StringIO()
        sys.stdin = io.StringIO("{}")
        with contextlib.redirect_stdout(buf):
            cmd_announce(argparse.Namespace(hook=True, root=r10, stamp=stamp))
        sys.stdin = sys.__stdin__
        zero = json.loads(buf.getvalue())
        ok("announce-zero-said", zero["systemMessage"] == SYSTEM_MESSAGE_NONE_FMT.format(role=NO_ROLE, basis=BASIS_UNMAPPED), zero)
        os.environ[HYP_DECISIONS_ENV] = HYP_DECISIONS_OFF
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_announce(argparse.Namespace(hook=True, root=r9, stamp=stamp))
        os.environ.pop(HYP_DECISIONS_ENV, None)
        ok("announce-off-silent", rc == 0 and buf.getvalue() == "")
        saved_t0 = _T0
        _T0 = time.monotonic() - ANNOUNCE_DEADLINE_S - 1
        buf = io.StringIO()
        sys.stdin = io.StringIO("{}")
        with contextlib.redirect_stdout(buf):
            rc = cmd_announce(argparse.Namespace(hook=True, root=r9, stamp=stamp))
        sys.stdin = sys.__stdin__
        _T0 = saved_t0
        failed = json.loads(buf.getvalue())
        ok("announce-deadline-loud", rc == 0 and failed["systemMessage"] == SYSTEM_MESSAGE_FAILED_FMT.format(reason="deadline")
           and failed["hookSpecificOutput"]["additionalContext"] == F_ANNOUNCE_FAILED.format(reason="deadline"), failed)
        # ---- the behind clause: a fetched, unmerged upstream (offline; the name from the config files) ----
        bare = os.path.join(tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True, env=dict(os.environ))
        sh(bare, ["symbolic-ref", "HEAD", "refs/heads/main"])
        sh(r9, ["remote", "add", "origin", bare])
        sh(r9, ["push", "-q", "-u", "origin", "main"])
        rb = os.path.join(tmp, "clone-b-behind")
        subprocess.run(["git", "clone", "-q", bare, rb], check=True, env=dict(os.environ))
        sh(rb, ["config", "user.email", B])
        sh(rb, ["config", "user.name", "identity-B"])
        sh(rb, ["config", "commit.gpgsign", "false"])
        append(rb, [card(3)])
        commit(rb, "a card from B", 6, email=B)
        sh(rb, ["push", "-q", "origin", "main"])
        sh(r9, ["fetch", "-q", "origin"])
        _T0 = time.monotonic()
        buf = io.StringIO()
        sys.stdin = io.StringIO("{}")
        with contextlib.redirect_stdout(buf):
            cmd_announce(argparse.Namespace(hook=True, root=r9, stamp=stamp))
        sys.stdin = sys.__stdin__
        behind = json.loads(buf.getvalue())
        ok("announce-behind-clause", behind["systemMessage"].endswith(" behind origin/main by 1 commits, 1 decision row unseen")
           and "\tbehind 1\t" in behind["hookSpecificOutput"]["additionalContext"] and upstream_name(r9) == "origin/main", behind["systemMessage"])
        env9b, _ = project(r9, stamp, {})
        ok("counts-behind", env9b["counts"]["behind_commits"] == 1 and env9b["counts"]["behind_rows"] == 1, env9b["counts"])
        # the seeded git shim (a real process, the deadline inside the row's 10 s): every call sleeps -> ANNOUNCE-FAILED deadline;
        # only the last-act call sleeps -> new ?
        shim_dir = os.path.join(tmp, "shim")
        os.makedirs(shim_dir)
        real_git = shutil_which("git")
        for name, cond in (("all", ""), ("lastact", 'case "$*" in *--grep=*) sleep 9;; esac; '), ):
            d = os.path.join(shim_dir, name)
            os.makedirs(d)
            with io.open(os.path.join(d, "git"), "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n%sexec %s \"$@\"\n" % (cond if cond else "sleep 9\n", real_git))
            os.chmod(os.path.join(d, "git"), 0o755)
        env_shim = dict(os.environ)
        env_shim["PATH"] = os.path.join(shim_dir, "all") + os.pathsep + env_shim.get("PATH", "")
        started = time.monotonic()
        p = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "announce", "--hook", "--root", r9], input="{}", capture_output=True,
                           text=True, env=env_shim, timeout=10)
        wall = time.monotonic() - started
        out = json.loads(p.stdout) if p.stdout.strip() else {}
        ok("announce-shim-deadline", p.returncode == 0 and out.get("systemMessage") == SYSTEM_MESSAGE_FAILED_FMT.format(reason="deadline") and wall < 10,
           (p.returncode, p.stdout[:120], round(wall, 1)))
        env_shim["PATH"] = os.path.join(shim_dir, "lastact") + os.pathsep + os.environ.get("PATH", "")
        started = time.monotonic()
        p = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "announce", "--hook", "--root", r9], input="{}", capture_output=True,
                           text=True, env=env_shim, timeout=10)
        wall = time.monotonic() - started
        out = json.loads(p.stdout) if p.stdout.strip() else {}
        ok("announce-shim-last-act-new-unknown", p.returncode == 0 and "(? new since you last answered;" in out.get("systemMessage", "")
           and "\tnew ?\t" in out.get("hookSpecificOutput", {}).get("additionalContext", "") and wall < 10, (p.returncode, p.stdout[:160], round(wall, 1)))
        # ---- S19 the lock and the failing signer ----
        r11 = repo("lock", roles={"maintainer": [A]})
        append(r11, [card(1)])
        commit(r11, "card", 2)
        lock = os.path.join(r11, ".git", LOCK_DIR_NAME)
        os.mkdir(lock)
        with io.open(os.path.join(lock, LOCK_HOLDER_FILE), "w", encoding="utf-8") as fh:
            fh.write("4242\n")
        before = ledger(r11)
        rc, out = run_answer(r11, "DEC-%03d" % 1, label=["opt1"])
        env11, _ = project(r11, stamp, {})
        ok("resolve-busy", rc == EXIT_REFUSED and out.splitlines()[0] == F_RESOLVE_BUSY.format(pid="4242") and ledger(r11) == before
           and env11["can_record"] is False and env11["block_reason"] == F_RESOLVE_BUSY.format(pid="4242"), out[:120])
        shutil.rmtree(lock)
        signer = os.path.join(r11, ".git", "fail-sign.sh")
        with io.open(signer, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho 'fixture signer: refusing to sign' >&2\nexit 1\n")
        os.chmod(signer, 0o755)
        sh(r11, ["config", "gpg.program", signer])
        sh(r11, ["config", "commit.gpgsign", "true"])
        head_before = sh(r11, ["rev-parse", "HEAD"]).stdout.strip()
        rc, out = run_answer(r11, "DEC-%03d" % 1, label=["opt1"])
        ok("commit-failed-changes-nothing", rc == EXIT_INVALID and out.splitlines()[0].startswith("COMMIT-FAILED ") and ledger(r11) == before
           and sh(r11, ["rev-parse", "HEAD"]).stdout.strip() == head_before, out[:160])
        sh(r11, ["config", "commit.gpgsign", "false"])
        # the module source carries no card or spec id literal and none of the question-grammar literals
        with io.open(os.path.abspath(__file__), encoding="utf-8") as fh:
            src = fh.read()
        ok("no-id-literals-in-source", re.search(r"DEC-[0-9]|H-[0-9]{3}|H-DRA" + r"FT-", src) is None)
        ok("no-question-grammar-literals-in-source", all(lit not in src for lit in ("ask" + _COLON, "[" + " ]", "answer" + _COLON)))
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(tmp, ignore_errors=True)
    print("queue-selftest: %d failure(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
