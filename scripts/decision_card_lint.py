#!/usr/bin/env python3
"""decision_card_lint.py -- the door-field lint D0-D9 over one decision candidate (the variable under test).

Contract: the frozen rule table and corroboration table of the decision-card-door-fields spec; the
lint-author readings of every point the tables leave open are listed in impl/CONTRACT.md beside this
file. Standard library only; byte-deterministic (findings are emitted one line per rule, in rule order).

Reads: the candidate row, the repository's git objects (git ls-files / show / diff), the work ledger's
uncommitted tail (the consumer's ledger: .claude/hyp.json ledger_file, default ledger/ledger.jsonl --
also D3's decision-resolution authority), research/raw/ and program.md at HEAD, and the hook denial
record ledger/hook-denials.jsonl at HEAD. No clock, no environment, no network, no process beyond git.
Every git read runs under one timeout; a stall yields one exit-neutral timeout mark for that rule
(never a finding). A timeout of 0 means "no time to wait" and stalls by definition (the seeded stall).

Exit contract (experiments/preflight.py's): 0 PASS, 1 ESCALATE (findings attached; the row still
renders), 2 MALFORMED (every missing or invalid field listed in one pass; nothing appended).
"""
import hashlib
import json
import os
import re
import subprocess
import sys

UNDO_VOCAB = ("git-revert", "flag", "amendment", "ledger-row", "none")
REVERTIBLE = ("git-revert", "flag", "amendment")
EXT_VOCAB = ("none", "other-humans", "external-publication", "spend-beyond-granted-budget",
             "physical-act", "classifier-flagged", "reserved-in-his-words")
D8_PHRASES = ("two-way door", "proceeds under the standing grant", "Nothing is blocked",
              "default advisory", "keep-advisory")
NONE_EXISTS = "none-exists"
LAB_OWNED_REPOS = ("getfatday/cause-n-effect", "getfatday/hyp-machine")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
DENIAL_RECORD_REL = "ledger/hook-denials.jsonl"
HYP_JSON_REL = ".claude/hyp.json"
LEDGER_REL_DEFAULT = "ledger/ledger.jsonl"  # the plugin default; .claude/hyp.json ledger_file overrides it (ledger_rel_for)

POINTER_RE = re.compile(r"^(?P<path>[^@\s]+)@(?P<sha>[0-9a-f]{40})#L(?P<a>\d+)-L(?P<b>\d+)$")
PHYSICAL_RE = re.compile(r"(?i)\bdevice[ -]code\b|\bop signin\b|\bsigning[ -]key\b|\bgpg\b|"
                         r"\blaunchctl load\b|\bclick\b|\bkeystroke\b|\byour eyes\b")
EXT_VERB_RE = re.compile(r"\b(invite|email|slack|reply)\b")
URL_RE = re.compile(r"https?://([^/\s'\"]+)(/[^\s'\"]*)?")
REPO_FLAG_RE = re.compile(r"(?:^|\s)(?:-R|--repo)[ =]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
GH_REPO_VERB_RE = re.compile(r"\bgh repo (?:create|rename|edit)\b(?:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?")
GH_RELEASE_RE = re.compile(r"\bgh release\b")
GH_COMMENT_RE = re.compile(r"\bgh (?:issue|pr) comment\b")
GIT_PUSH_RE = re.compile(r"\bgit push\b(?P<rest>[^;&|]*)")
MARKETPLACE_RE = re.compile(r"marketplace\.json")
MAINTAINER_CAPTURE_RE = re.compile(r"(?i)maintainer.*(verbatim|ruling|directive|grant)", re.DOTALL)
MAINTAINER_SUFFIXES = ("-ruling.md", "-directive.md", "-grant.md")
RAW_CITE_RE = re.compile(r"(research/raw/[^\s:,;)\]'\"]+\.md)(?::L?(\d+)(?:-L?(\d+))?)?")
MAINTAINER_ARTIFACT_CITE_RE = re.compile(r"(?:^|[\s(\[])((?:research/raw/[^\s:,;)\]'\"]+\.md)|program\.md)(?::L?(\d+)(?:-L?(\d+))?)?")
RESERVED_RE = re.compile(r"(?i)\breserv(?:e|ed|es|ing)\b")
BUDGET_NUMBER_RE = re.compile(r"(?:US)?\$\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:USD\b|dollars\b)")
HEADING_RE = re.compile(r"^##\s")
EVIDENCE_RUN_BASENAMES = ("VERDICT.json", "VERIFY.md")
EVIDENCE_RUN_PREFIXES = ("grade", "RUN-RECORD")


class GitStall(Exception):
    """A git read exceeded the lint's timeout (or the timeout was 0: no time to wait)."""


class Git(object):
    """One repository, one timeout, memoised reads. Raises GitStall on a stall."""

    def __init__(self, root, timeout):
        self.root = root
        self.timeout = timeout
        self._cache = {}

    def run(self, args):
        key = tuple(args)
        if key in self._cache:
            return self._cache[key]
        if self.timeout is None or self.timeout <= 0:
            raise GitStall(" ".join(args[:2]))
        try:
            proc = subprocess.run(["git", "-C", self.root] + list(args), capture_output=True,
                                  timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise GitStall(" ".join(args[:2]))
        except OSError:
            res = (127, b"")
            self._cache[key] = res
            return res
        res = (proc.returncode, proc.stdout)
        self._cache[key] = res
        return res

    def text(self, args):
        code, out = self.run(args)
        return code, out.decode("utf-8", "replace")

    def show(self, spec):
        code, out = self.text(["show", spec])
        return out if code == 0 else None

    def tracked(self):
        if "tracked" not in self._cache:
            code, out = self.text(["ls-files", "-z"])
            paths = [p for p in out.split("\x00") if p] if code == 0 else []
            dirs = set()
            for p in paths:
                d = os.path.dirname(p)
                while d and d not in dirs:
                    dirs.add(d)
                    d = os.path.dirname(d)
            self._cache["tracked"] = (frozenset(paths), frozenset(dirs))
        return self._cache["tracked"]


class LintResult(object):
    def __init__(self):
        self.malformed = []   # [(rule, detail)] -> exit 2
        self.findings = []    # [(rule, detail)] -> exit 1
        self.timeouts = []    # [rule] -> exit-neutral

    @property
    def exit_code(self):
        if self.malformed:
            return 2
        return 1 if self.findings else 0


def ledger_rel_for(root):
    """The consumer's work ledger, repo-relative: .claude/hyp.json ledger_file when set, else LEDGER_REL_DEFAULT
    (the key and default scripts/decisions.py resolves). D3's decision-resolution authority is this file."""
    try:
        with open(os.path.join(root, *HYP_JSON_REL.split("/")), encoding="utf-8") as fh:
            data = json.load(fh)
        val = data.get("ledger_file") if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip().strip("/")
    except (OSError, ValueError):
        pass
    return LEDGER_REL_DEFAULT


# ---------- field helpers ----------

def _options(rec):
    ask = rec.get("ask") if isinstance(rec.get("ask"), dict) else {}
    opts = ask.get("options") if isinstance(ask.get("options"), list) else []
    return [o for o in opts if isinstance(o, dict)]


def _labels(rec):
    return [str(o.get("label", "")) for o in _options(rec)]


def _is_repo_relative_path(p):
    if not isinstance(p, str) or not p or p != p.strip():
        return False
    if p.startswith("/") or p.startswith("~") or re.search(r"\s", p) or "\x00" in p:
        return False
    return True


def staged_kind(rec):
    """-> 'none' | 'command' | 'paths' | None (absent or malformed)."""
    sa = rec.get("staged_artifact")
    if sa == "none":
        return "none"
    if isinstance(sa, str):
        return "command" if sa.strip() and "\n" not in sa else None
    if isinstance(sa, list):
        if sa and all(_is_repo_relative_path(p) for p in sa):
            return "paths"
        return None
    return None


def staged_text(rec):
    sa = rec.get("staged_artifact")
    if isinstance(sa, list):
        return " ".join(str(p) for p in sa)
    return sa if isinstance(sa, str) else ""


def _is_amount(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def six_fields(rec):
    """The six field values as one canonical object (the manipulation-check sha reads this)."""
    obj = {"undo": [o.get("undo") for o in _options(rec)],
           "staged_artifact": rec.get("staged_artifact"),
           "evidence": rec.get("evidence"),
           "externality": rec.get("externality"),
           "recommended": rec.get("recommended"),
           "default_on_silence": rec.get("default_on_silence")}
    if "amount_usd" in rec:
        obj["amount_usd"] = rec.get("amount_usd")
    return obj


def fields_sha(rec):
    return hashlib.sha256(json.dumps(six_fields(rec), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------- exit-2 class: presence, vocabulary, form (no git) ----------

def shape_errors(rec):
    """-> [(rule, detail)] for D0, D1, D3 (form), D4 (form), D5, D6. Empty when the six fields are well-formed."""
    errs = []
    labels = _labels(rec)
    rec_recommended = rec.get("recommended")
    # D0 STAGED-ARTIFACT
    kind = staged_kind(rec)
    if "staged_artifact" not in rec:
        errs.append(("D0", "staged_artifact missing (a non-empty list of repo-relative paths, one command line, or the literal none)"))
    elif kind is None:
        errs.append(("D0", "staged_artifact %s is neither a non-empty list of repo-relative paths nor one command line nor the literal none"
                     % json.dumps(rec.get("staged_artifact"), ensure_ascii=False)))
    elif kind == "none" and rec_recommended != "none":
        errs.append(("D0", "staged_artifact none while recommended is %s (none is permitted only while recommended is none)"
                     % json.dumps(rec_recommended, ensure_ascii=False)))
    # D1 UNDO-VOCAB
    for opt in _options(rec):
        label = str(opt.get("label", "?"))
        if "undo" not in opt:
            errs.append(("D1", "option %s has no undo (%s)" % (json.dumps(label, ensure_ascii=False), "|".join(UNDO_VOCAB))))
        elif opt.get("undo") not in UNDO_VOCAB:
            errs.append(("D1", "option %s undo %s not in %s" % (json.dumps(label, ensure_ascii=False),
                                                                json.dumps(opt.get("undo"), ensure_ascii=False), "|".join(UNDO_VOCAB))))
    # D3 EVIDENCE-RESOLVES (form)
    ev = rec.get("evidence")
    if "evidence" not in rec:
        errs.append(("D3", "evidence missing (<repo-path>@<sha40>#L<a>-L<b>, or the literal none-exists)"))
    elif ev != NONE_EXISTS:
        m = POINTER_RE.match(ev) if isinstance(ev, str) else None
        if not m:
            errs.append(("D3", "evidence %s is neither <repo-path>@<sha40>#L<a>-L<b> nor the literal none-exists" % json.dumps(ev, ensure_ascii=False)))
        elif not (1 <= int(m.group("a")) <= int(m.group("b"))):
            errs.append(("D3", "evidence %s has an empty or reversed line range" % json.dumps(ev, ensure_ascii=False)))
    # D4 EXTERNALITY-CORROBORATED (form)
    ext = rec.get("externality")
    if "externality" not in rec:
        errs.append(("D4", "externality missing (%s)" % "|".join(EXT_VOCAB)))
    elif ext not in EXT_VOCAB:
        errs.append(("D4", "externality %s not in %s" % (json.dumps(ext, ensure_ascii=False), "|".join(EXT_VOCAB))))
    if rec.get("class") == "spend":
        if "amount_usd" not in rec:
            errs.append(("D4", "class spend requires amount_usd (a number >= 0)"))
        elif not _is_amount(rec.get("amount_usd")):
            errs.append(("D4", "amount_usd %s is not a number >= 0" % json.dumps(rec.get("amount_usd"), ensure_ascii=False)))
    # D5 DEFAULT-STATED
    dflt = rec.get("default_on_silence")
    if "default_on_silence" not in rec:
        errs.append(("D5", "default_on_silence missing (an option label, or the literal nothing-changes)"))
    elif not (dflt == "nothing-changes" or (isinstance(dflt, str) and dflt in labels)):
        errs.append(("D5", "default_on_silence %s is neither an existing option label nor nothing-changes" % json.dumps(dflt, ensure_ascii=False)))
    # D6 RECOMMENDED-ONE
    if "recommended" not in rec:
        errs.append(("D6", "recommended missing (exactly one existing option label, or the literal none)"))
    elif not (rec_recommended == "none" or (isinstance(rec_recommended, str) and rec_recommended in labels)):
        errs.append(("D6", "recommended %s is neither exactly one existing option label nor none" % json.dumps(rec_recommended, ensure_ascii=False)))
    return errs


# ---------- D7 DEDUP-WHY (the uncommitted ledger tail) ----------

def tail_decision_rows(git, root, ledger_path):
    rel = os.path.relpath(os.path.abspath(ledger_path), os.path.abspath(root)).replace(os.sep, "/")
    if rel.startswith(".."):
        return []
    code, out = git.text(["diff", "HEAD", "--", rel])
    rows = []
    if code != 0:
        return rows
    for line in out.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            try:
                rec = json.loads(line[1:])
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("kind") == "decision":
                rows.append(rec)
    return rows


def d7_dedup_why(rec, git, root, ledger_path):
    why = rec.get("why_only_you")
    twins = [r for r in tail_decision_rows(git, root, ledger_path) if r.get("why_only_you") == why]
    if len(twins) >= 2:
        ids = ", ".join(str(r.get("id", "?")) for r in twins)
        return ("D7", "why_only_you is byte-identical to %d uncommitted decision rows (%s): one policy question filed %d times; file ONE candidate"
                % (len(twins), ids, len(twins) + 1))
    return None


# ---------- D2 UNDO-CORROBORATED ----------

def _inside_worktree(path, tracked, tracked_dirs):
    norm = os.path.normpath(path)
    if norm.startswith("..") or norm.startswith("/"):
        return False
    if norm in tracked:
        return True
    d = os.path.dirname(norm)
    return d == "" or d in tracked_dirs


def d2_undo_corroborated(rec, git):
    if staged_kind(rec) != "paths":
        return None
    tracked, tracked_dirs = git.tracked()
    paths = rec["staged_artifact"]
    outside = [p for p in paths if not _inside_worktree(p, tracked, tracked_dirs)]
    all_tracked = all(os.path.normpath(p) in tracked for p in paths)
    parts = []
    for opt in _options(rec):
        label = json.dumps(str(opt.get("label", "?")), ensure_ascii=False)
        undo = opt.get("undo")
        if undo in REVERTIBLE and outside:
            parts.append("UNDO-UNCORROBORATED option %s declares %s while staged path %s lies outside the repository worktree"
                         % (label, undo, json.dumps(outside[0], ensure_ascii=False)))
        elif undo == "none" and all_tracked:
            parts.append("UNDO-MISDECLARED option %s declares none while every staged path is a tracked repository path" % label)
    if parts:
        return ("D2", "; ".join(parts))
    return None


# ---------- D3 EVIDENCE-RESOLVES (resolution + authority class) ----------

def _hyp_authority_lines(text):
    """Line numbers (1-based) inside the ## Runs section, plus ## Status lines carrying 'kept'."""
    ok = set()
    section = None
    for i, line in enumerate(text.splitlines(), 1):
        if HEADING_RE.match(line):
            head = line[2:].strip().lower()
            section = "runs" if head == "runs" else ("status" if head == "status" else None)
            continue
        if section == "runs" and line.strip():
            ok.add(i)
        elif section == "status" and "kept" in line.lower():
            ok.add(i)
    return ok


def evidence_class(path, sha, a, b, content, ledger_rel=LEDGER_REL_DEFAULT):
    """-> None when the target is an EVIDENCE-CLASS artifact, else a short reason."""
    base = os.path.basename(path)
    if path.startswith("experiments/runs/") and (base in EVIDENCE_RUN_BASENAMES or base.startswith(EVIDENCE_RUN_PREFIXES)):
        return None
    if path.startswith("research/raw/"):
        if is_maintainer_capture(path, content):
            return None
        return "a research/raw file that is not a maintainer capture"
    if path == ledger_rel:
        lines = content.splitlines()
        for n in range(a, b + 1):
            try:
                row = json.loads(lines[n - 1])
            except (ValueError, IndexError):
                return "ledger line %d is not a JSON row" % n
            if not (isinstance(row, dict) and row.get("kind") == "decision-resolution"):
                return "ledger line %d is not a decision-resolution row" % n
        return None
    if path.startswith("hypotheses/") and path.endswith(".md"):
        ok = _hyp_authority_lines(content)
        if all(n in ok for n in range(a, b + 1)):
            return None
        return "hypotheses lines outside the Runs table and the Status kept line"
    return "a lab-authored path (never an authority)"


def is_maintainer_capture(path, content):
    if path.endswith(MAINTAINER_SUFFIXES):
        return True
    head = "\n".join(content.splitlines()[:5])
    return bool(MAINTAINER_CAPTURE_RE.search(head))


def d3_evidence_resolves(rec, git, ledger_rel=LEDGER_REL_DEFAULT):
    ev = rec.get("evidence")
    if ev == NONE_EXISTS:
        return None
    m = POINTER_RE.match(ev)
    path, sha, a, b = m.group("path"), m.group("sha"), int(m.group("a")), int(m.group("b"))
    content = git.show("%s:%s" % (sha, path))
    if content is None:
        return ("D3", "EVIDENCE-UNRESOLVED %s does not resolve at committed HEAD (git show %s:%s failed)" % (json.dumps(ev, ensure_ascii=False), sha[:12], path))
    n = len(content.splitlines())
    if b > n:
        return ("D3", "EVIDENCE-UNRESOLVED %s cites lines %d-%d but the file has %d lines at that commit" % (json.dumps(ev, ensure_ascii=False), a, b, n))
    reason = evidence_class(path, sha, a, b, content, ledger_rel)
    if reason:
        return ("D3", "EVIDENCE-NOT-AN-AUTHORITY %s resolves but is %s; evidence is a VERDICT.json, a run grade, a RUN-RECORD, a VERIFY.md, a research/raw maintainer capture, a decision-resolution row or a Runs-table row" % (json.dumps(ev, ensure_ascii=False), reason))
    return None


# ---------- D4 EXTERNALITY-CORROBORATED (the corroboration table) ----------

def _repo_targets(cmd):
    targets = [m.group(1) for m in REPO_FLAG_RE.finditer(cmd)]
    for m in GH_REPO_VERB_RE.finditer(cmd):
        if m.group(1):
            targets.append(m.group(1))
    hosts = []
    for m in URL_RE.finditer(cmd):
        host, tail = m.group(1).lower(), (m.group(2) or "")
        hosts.append(host)
        if host in ("github.com", "www.github.com"):
            segs = [s for s in tail.split("/") if s]
            if len(segs) >= 2:
                targets.append(segs[0] + "/" + segs[1].replace(".git", ""))
    return targets, hosts


def _public_url(cmd):
    for m in URL_RE.finditer(cmd):
        host, tail = m.group(1).lower(), (m.group(2) or "")
        if host in LOCAL_HOSTS:
            continue
        if host in ("github.com", "www.github.com"):
            segs = [s for s in tail.split("/") if s]
            if len(segs) >= 2 and (segs[0] + "/" + segs[1].replace(".git", "")) in LAB_OWNED_REPOS:
                continue
        return m.group(0)
    return None


def obs_other_humans(rec, git):
    if staged_kind(rec) != "command":
        return False, "no command staged"
    cmd = rec["staged_artifact"]
    targets, hosts = _repo_targets(cmd)
    for t in targets:
        if t not in LAB_OWNED_REPOS:
            return True, "target repository %s is outside the lab-owned set" % t
    for h in hosts:
        if h not in LOCAL_HOSTS and h not in ("github.com", "www.github.com"):
            return True, "target host %s is outside the lab-owned set" % h
    m = EXT_VERB_RE.search(cmd)
    if m:
        return True, "external-communication verb %s" % json.dumps(m.group(1))
    return False, "target inside the lab-owned set, no external-communication verb"


def obs_external_publication(rec, git):
    kind = staged_kind(rec)
    text = staged_text(rec)
    if MARKETPLACE_RE.search(text):
        return True, "a marketplace file is staged"
    if kind != "command":
        return False, "no command staged"
    cmd = text
    if GH_RELEASE_RE.search(cmd):
        return True, "gh release"
    m = GH_REPO_VERB_RE.search(cmd)
    if m:
        return True, m.group(0).strip()
    for m in GIT_PUSH_RE.finditer(cmd):
        rest = m.group("rest").split()
        args = [t for t in rest if not t.startswith("-")]
        if len(args) >= 2 or "--tags" in rest:
            branch = args[1].split(":")[-1] if len(args) >= 2 else "--tags"
            code, out = git.text(["symbolic-ref", "--short", "-q", "HEAD"])
            current = out.strip() if code == 0 and out.strip() else None
            if branch != current:
                return True, "git push to %s (not the lane's branch %s)" % (branch, current or "<detached>")
    url = _public_url(cmd)
    if url:
        return True, "public URL %s" % url
    return False, "no publication target staged"


def _cited_maintainer_lines(rec, git, want_program_md):
    """Yields (path, text-of-cited-lines) for every maintainer artifact cited in context_pointers (and, when
    asked, why_only_you) that exists at HEAD and passes the maintainer-capture test."""
    sources = list(rec.get("context_pointers") or [])
    for src in sources:
        if not isinstance(src, str):
            continue
        for m in MAINTAINER_ARTIFACT_CITE_RE.finditer(src):
            path, a, b = m.group(1), m.group(2), m.group(3)
            if path == "program.md" and not want_program_md:
                continue
            content = git.show("HEAD:%s" % path)
            if content is None:
                continue
            if path != "program.md" and not is_maintainer_capture(path, content):
                continue
            lines = content.splitlines()
            if a:
                lo, hi = int(a), int(b) if b else int(a)
                picked = lines[lo - 1:hi]
            else:
                picked = lines
            yield path, "\n".join(picked)


def obs_spend_beyond(rec, git):
    amount = rec.get("amount_usd")
    if not _is_amount(amount):
        return False, "no amount_usd"
    for path, text in _cited_maintainer_lines(rec, git, want_program_md=True):
        for m in BUDGET_NUMBER_RE.finditer(text):
            raw = m.group(1) or m.group(2)
            try:
                budget = float(raw.replace(",", ""))
            except ValueError:
                continue
            if amount > budget:
                return True, "amount_usd %s exceeds the budget %s on a cited line of %s" % (amount, raw, path)
    return False, "no cited maintainer-artifact budget line is exceeded"


def obs_physical_act(rec, git):
    m = PHYSICAL_RE.search(staged_text(rec))
    if m:
        return True, "physical-act token %s" % json.dumps(m.group(0))
    return False, "no physical-act token staged"


def obs_classifier_flagged(rec, git):
    if staged_kind(rec) != "command":
        return False, "no command staged"
    cmd = rec["staged_artifact"].strip()
    content = git.show("HEAD:%s" % DENIAL_RECORD_REL)
    if content is None:
        return False, "no hook denial record at HEAD"
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("command", "")).strip() == cmd:
            return True, "a harness denial for the staged command is on record (%s)" % row.get("source", "hook")
    return False, "no denial for the staged command on record"


def obs_reserved_in_his_words(rec, git):
    sources = [rec.get("why_only_you") or ""] + [s for s in (rec.get("context_pointers") or []) if isinstance(s, str)]
    for src in sources:
        for m in RAW_CITE_RE.finditer(src):
            path, a, b = m.group(1), m.group(2), m.group(3)
            content = git.show("HEAD:%s" % path)
            if content is None or not is_maintainer_capture(path, content):
                continue
            lines = content.splitlines()
            picked = lines[int(a) - 1:int(b) if b else int(a)] if a else lines
            if RESERVED_RE.search("\n".join(picked)):
                return True, "the cited maintainer capture %s%s carries the reserved noun" % (path, (":%s" % a) if a else "")
    return False, "no cited maintainer capture carries the reserved noun"


OBSERVABLES = (("other-humans", obs_other_humans), ("external-publication", obs_external_publication),
               ("spend-beyond-granted-budget", obs_spend_beyond), ("physical-act", obs_physical_act),
               ("classifier-flagged", obs_classifier_flagged), ("reserved-in-his-words", obs_reserved_in_his_words))


def d4_externality_corroborated(rec, git):
    ext = rec.get("externality")
    if ext == "none":
        positives = []
        for name, fn in OBSERVABLES:
            positive, why = fn(rec, git)
            if positive:
                positives.append("%s (%s)" % (name, why))
        if positives:
            return ("D4", "EXTERNALITY-UNDECLARED:" + "; ".join(positives))
        return None
    fn = dict(OBSERVABLES)[ext]
    positive, why = fn(rec, git)
    if not positive:
        return ("D4", "DECLARED-HARD-UNCORROBORATED:%s (%s)" % (ext, why))
    return None


# ---------- D8 / D9 companion heuristics ----------

def d8_self_declared(rec):
    hits = []
    for field in ("why_only_you", "note"):
        text = rec.get(field)
        if not isinstance(text, str):
            continue
        for phrase in D8_PHRASES:
            if phrase in text:
                hits.append("%s carries %s" % (field, json.dumps(phrase)))
    if hits:
        return ("D8", "SELF-DECLARED-TWO-WAY: " + "; ".join(hits) + " -- the card concedes its own door type")
    return None


def d9_blocking_two_way(rec):
    blocks = rec.get("blocks") or []
    if not blocks:
        return None
    undos = [o.get("undo") for o in _options(rec)]
    if undos and all(u != "none" for u in undos) and rec.get("externality") == "none":
        return ("D9", "BLOCKING-TWO-WAY: blocks %s while every option is revertible and externality is none -- never gate a driver on a two-way answer"
                % json.dumps(blocks, ensure_ascii=False))
    return None


# ---------- the lint ----------

def lint(rec, root, ledger_path=None, git_timeout=20):
    """-> LintResult over one validated candidate row (shape-valid in the legacy sense)."""
    res = LintResult()
    root = os.path.abspath(root)
    ledger_path = ledger_path or os.path.join(root, ledger_rel_for(root))
    ledger_rel = os.path.relpath(os.path.abspath(ledger_path), root).replace(os.sep, "/")
    git = Git(root, git_timeout)
    res.malformed.extend(shape_errors(rec))
    if res.malformed:
        return res
    try:
        hit = d7_dedup_why(rec, git, root, ledger_path)
        if hit:
            res.malformed.append(hit)
            return res
    except GitStall:
        res.timeouts.append("D7")
    for rule, fn in (("D2", lambda: d2_undo_corroborated(rec, git)), ("D3", lambda: d3_evidence_resolves(rec, git, ledger_rel)),
                     ("D4", lambda: d4_externality_corroborated(rec, git))):
        try:
            hit = fn()
        except GitStall:
            res.timeouts.append(rule)
            hit = None
        if hit:
            res.findings.append(hit)
    for fn in (d8_self_declared, d9_blocking_two_way):
        hit = fn(rec)
        if hit:
            res.findings.append(hit)
    res.findings.sort(key=lambda t: t[0])
    return res


# ---------- selftest (synthetic cards in a throwaway repository) ----------

def _selftest():
    import shutil
    import tempfile
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("LINT-SELFTEST-PASS" if cond else "LINT-SELFTEST-FAIL", name, (" -- " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix="door-lint-selftest-")
    LEDGER_REL = "ledger/work-ledger.jsonl"  # the scratch consumer's configured ledger (.claude/hyp.json), not the plugin default
    try:
        def sh(*a):
            return subprocess.run(["git", "-C", tmp] + list(a), capture_output=True, text=True, check=True).stdout
        subprocess.run(["git", "init", "-q", tmp], check=True)
        sh("config", "user.name", "lint selftest"); sh("config", "user.email", "lint@selftest.invalid"); sh("config", "commit.gpgsign", "false")
        os.makedirs(os.path.join(tmp, "ledger")); os.makedirs(os.path.join(tmp, "research", "raw")); os.makedirs(os.path.join(tmp, "notes"))
        os.makedirs(os.path.join(tmp, ".claude"))
        with open(os.path.join(tmp, ".claude", "hyp.json"), "w") as fh:
            fh.write(json.dumps({"ledger_file": LEDGER_REL}) + "\n")
        os.makedirs(os.path.join(tmp, "experiments", "runs", "lane-x")); os.makedirs(os.path.join(tmp, "hypotheses"))
        with open(os.path.join(tmp, "notes", "a.md"), "w") as fh:
            fh.write("one\ntwo\nthree\n")
        with open(os.path.join(tmp, "research", "raw", "2026-01-01-topic-ruling.md"), "w") as fh:
            fh.write("# Topic ruling (maintainer, verbatim)\n\nline three\nthis row stays reserved to the maintainer\nbudget: US$5 per run\n")
        with open(os.path.join(tmp, "experiments", "runs", "lane-x", "VERIFY.md"), "w") as fh:
            fh.write("# verify\n\nkept 5/5\n")
        with open(os.path.join(tmp, "hypotheses", "spec-x.md"), "w") as fh:
            fh.write("# spec\n\n## Status\nkept\n\n## Motivation\nprose\n\n## Runs\n| # | Date |\n|---|---|\n| 1 | 2026-01-01 |\n")
        with open(os.path.join(tmp, LEDGER_REL), "w") as fh:
            fh.write(json.dumps({"kind": "decision-resolution", "id": "DEC-%03d" % 1, "date": "2026-01-01", "disposition": "accepted"}) + "\n")
        with open(os.path.join(tmp, DENIAL_RECORD_REL), "w") as fh:
            fh.write(json.dumps({"kind": "hook-denial", "command": "bash scripts/post.sh", "source": "security-classifier"}) + "\n")
        sh("add", "-A"); sh("commit", "-qm", "init")
        head = sh("rev-parse", "HEAD").strip()

        def card(**kw):
            base = {"kind": "decision", "id": "DEC-%03d" % 9, "class": "plan", "why_only_you": "only you hold it", "blocks": [],
                    "context_pointers": [], "ask": {"options": [{"label": "go", "description": "d", "undo": "git-revert"},
                                                                {"label": "hold", "description": "d", "undo": "ledger-row"}]},
                    "staged_artifact": ["notes/a.md"], "evidence": NONE_EXISTS, "externality": "none",
                    "recommended": "go", "default_on_silence": "hold"}
            for k, v in kw.items():
                if v is None:
                    base.pop(k, None)
                else:
                    base[k] = v
            return base

        def undo(c, i, v):
            if v is None:
                c["ask"]["options"][i].pop("undo", None)
            else:
                c["ask"]["options"][i]["undo"] = v
            return c

        def run(c, timeout=20):
            return lint(c, tmp, os.path.join(tmp, ledger_rel_for(tmp)), git_timeout=timeout)

        def rules(r):
            return sorted(set([m[0] for m in r.malformed] + [f[0] for f in r.findings]))

        r = run(card()); ok("clean-pass", r.exit_code == 0 and not r.findings and not r.timeouts, str(rules(r)))
        ok("d0-missing", rules(run(card(staged_artifact=None))) == ["D0"] and run(card(staged_artifact=None)).exit_code == 2)
        ok("d0-empty-list", rules(run(card(staged_artifact=[]))) == ["D0"])
        ok("d0-none-with-recommended", rules(run(card(staged_artifact="none"))) == ["D0"])
        ok("d0-none-ok-when-unrecommended", run(card(staged_artifact="none", recommended="none")).exit_code == 0)
        ok("d0-absolute-path", rules(run(card(staged_artifact=["/etc/hosts"]))) == ["D0"])
        ok("d0-command-ok", run(card(staged_artifact="make all")).exit_code == 0)
        ok("d1-vocab", rules(run(undo(card(), 0, "revert"))) == ["D1"])
        ok("d1-missing", rules(run(undo(card(), 1, None))) == ["D1"])
        r = run(card(staged_artifact=["../elsewhere/x.md"])); ok("d2-outside", rules(r) == ["D2"] and r.exit_code == 1 and "UNDO-UNCORROBORATED" in r.findings[0][1])
        r = run(card(staged_artifact=["brand-new-dir/x.md"])); ok("d2-new-dir-is-outside", rules(r) == ["D2"])
        r = run(card(staged_artifact=["notes/new.md"])); ok("d2-new-file-under-tracked-dir-inside", r.exit_code == 0, str(rules(r)))
        r = run(undo(card(), 1, "none")); ok("d2-none-on-tracked", rules(r) == ["D2"] and "UNDO-MISDECLARED" in r.findings[0][1])
        r = run(undo(card(staged_artifact="make all"), 1, "none")); ok("d2-silent-on-command", r.exit_code == 0, str(rules(r)))
        ok("d3-missing", rules(run(card(evidence=None))) == ["D3"])
        ok("d3-form", rules(run(card(evidence="notes/a.md#L1-L2"))) == ["D3"] and run(card(evidence="notes/a.md#L1-L2")).exit_code == 2)
        ok("d3-reversed", run(card(evidence="notes/a.md@%s#L3-L1" % head)).exit_code == 2)
        r = run(card(evidence="experiments/runs/lane-x/VERIFY.md@%s#L1-L3" % head)); ok("d3-resolves-verify", r.exit_code == 0, str(r.findings))
        r = run(card(evidence="research/raw/2026-01-01-topic-ruling.md@%s#L1-L2" % head)); ok("d3-resolves-capture", r.exit_code == 0, str(r.findings))
        r = run(card(evidence="ledger/work-ledger.jsonl@%s#L1-L1" % head)); ok("d3-resolves-resolution-row", r.exit_code == 0, str(r.findings))
        r = run(card(evidence="hypotheses/spec-x.md@%s#L11-L11" % head)); ok("d3-resolves-runs-row", r.exit_code == 0, str(r.findings))
        r = run(card(evidence="hypotheses/spec-x.md@%s#L4-L4" % head)); ok("d3-resolves-status-kept", r.exit_code == 0, str(r.findings))
        r = run(card(evidence="hypotheses/spec-x.md@%s#L7-L7" % head)); ok("d3-not-authority-prose", rules(r) == ["D3"] and "NOT-AN-AUTHORITY" in r.findings[0][1])
        r = run(card(evidence="notes/a.md@%s#L1-L2" % head)); ok("d3-not-authority-lab-path", rules(r) == ["D3"] and r.exit_code == 1)
        r = run(card(evidence="notes/a.md@%s#L1-L2" % ("0" * 40))); ok("d3-unresolved-sha", rules(r) == ["D3"] and "UNRESOLVED" in r.findings[0][1])
        r = run(card(evidence="experiments/runs/lane-x/VERIFY.md@%s#L1-L99" % head)); ok("d3-unresolved-lines", rules(r) == ["D3"] and "UNRESOLVED" in r.findings[0][1])
        ok("d4-missing", rules(run(card(externality=None))) == ["D4"])
        ok("d4-vocab", rules(run(card(externality="humans"))) == ["D4"])
        ok("d4-spend-no-amount", rules(run(card(**{"class": "spend"}))) == ["D4"])
        ok("d4-spend-bad-amount", rules(run(card(**{"class": "spend", "amount_usd": -1}))) == ["D4"])
        ok("d4-spend-ok", run(card(**{"class": "spend", "amount_usd": 0})).exit_code == 0)
        r = run(card(staged_artifact="gh release create v1 --repo getfatday/hyp-machine")); ok("d4-undeclared-publication", rules(r) == ["D4"] and "external-publication" in r.findings[0][1])
        r = run(card(staged_artifact="git commit -S --gpg-sign -m x")); ok("d4-undeclared-physical", rules(r) == ["D4"] and "physical-act" in r.findings[0][1])
        r = run(card(staged_artifact="gh issue comment 1 --repo acme/other --body hi")); ok("d4-undeclared-other-humans-repo", rules(r) == ["D4"] and "other-humans" in r.findings[0][1])
        r = run(card(staged_artifact="email the admins")); ok("d4-undeclared-other-humans-verb", rules(r) == ["D4"] and "other-humans" in r.findings[0][1])
        r = run(card(staged_artifact="gh issue comment 1 --repo getfatday/hyp-machine --body hi")); ok("d4-lab-owned-comment-is-not-other-humans", r.exit_code == 0, str(r.findings))
        r = run(card(staged_artifact="bash scripts/post.sh")); ok("d4-undeclared-classifier", rules(r) == ["D4"] and "classifier-flagged" in r.findings[0][1])
        r = run(card(staged_artifact="bash scripts/post.sh", externality="classifier-flagged")); ok("d4-declared-classifier-corroborated", r.exit_code == 0, str(r.findings))
        r = run(card(externality="physical-act")); ok("d4-declared-uncorroborated", rules(r) == ["D4"] and "DECLARED-HARD-UNCORROBORATED" in r.findings[0][1])
        r = run(card(externality="external-publication", staged_artifact="gh repo edit getfatday/hyp-machine --description x")); ok("d4-declared-publication-corroborated", r.exit_code == 0, str(r.findings))
        r = run(card(context_pointers=["research/raw/2026-01-01-topic-ruling.md"])); ok("d4-undeclared-reserved-whole-file", rules(r) == ["D4"] and "reserved-in-his-words" in r.findings[0][1])
        r = run(card(context_pointers=["research/raw/2026-01-01-topic-ruling.md:1-3"])); ok("d4-reserved-narrowed-lines-silent", r.exit_code == 0, str(r.findings))
        r = run(card(externality="reserved-in-his-words", context_pointers=["research/raw/2026-01-01-topic-ruling.md:4"])); ok("d4-declared-reserved-corroborated", r.exit_code == 0, str(r.findings))
        r = run(card(**{"class": "spend", "amount_usd": 6, "context_pointers": ["research/raw/2026-01-01-topic-ruling.md:5"]})); ok("d4-undeclared-spend-beyond", rules(r) == ["D4"] and "spend-beyond" in r.findings[0][1])
        r = run(card(**{"class": "spend", "amount_usd": 5, "context_pointers": ["research/raw/2026-01-01-topic-ruling.md:5"]})); ok("d4-spend-at-budget-negative", r.exit_code == 0, str(r.findings))
        r = run(card(**{"class": "spend", "amount_usd": 6, "externality": "spend-beyond-granted-budget"})); ok("d4-declared-spend-no-line-uncorroborated", rules(r) == ["D4"] and "UNCORROBORATED" in r.findings[0][1])
        ok("d5-missing", rules(run(card(default_on_silence=None))) == ["D5"])
        ok("d5-not-label", rules(run(card(default_on_silence="maybe"))) == ["D5"])
        ok("d5-nothing-changes", run(card(default_on_silence="nothing-changes")).exit_code == 0)
        ok("d6-missing", rules(run(card(recommended=None))) == ["D6"])
        ok("d6-two", rules(run(card(recommended=["go", "hold"]))) == ["D6"])
        ok("d6-not-label", rules(run(card(recommended="Go"))) == ["D6"])
        r = run(card(why_only_you="it proceeds under the standing grant")); ok("d8", rules(r) == ["D8"] and r.exit_code == 1)
        r = run(card(note="Nothing is blocked: two-way door")); ok("d8-one-line-many-phrases", rules(r) == ["D8"] and len(r.findings) == 1)
        r = run(card(blocks=["lane x"])); ok("d9", rules(r) == ["D9"] and r.exit_code == 1)
        r = run(card(blocks=["lane x"], externality="physical-act", staged_artifact="gpg --sign")); ok("d9-silent-hard-class", r.exit_code == 0, str(r.findings))
        r = run(undo(card(blocks=["lane x"], staged_artifact="make all"), 1, "none")); ok("d9-silent-undo-none", r.exit_code == 0, str(r.findings))
        # malformed suppresses corroboration: only the field defects print
        r = run(undo(card(blocks=["lane x"]), 0, "revert")); ok("malformed-suppresses-escalate", r.exit_code == 2 and rules(r) == ["D1"], str(rules(r)))
        # D7: two uncommitted twins in the tail
        with open(os.path.join(tmp, LEDGER_REL), "a") as fh:
            for i in (2, 3):
                fh.write(json.dumps({"kind": "decision", "id": "DEC-%03d" % i, "why_only_you": "twin"}) + "\n")
        r = run(card(why_only_you="twin")); ok("d7-third-of-batch", r.exit_code == 2 and rules(r) == ["D7"], str(rules(r)))
        with open(os.path.join(tmp, LEDGER_REL), "a") as fh:
            fh.write(json.dumps({"kind": "decision", "id": "DEC-%03d" % 4, "why_only_you": "single"}) + "\n")
        r = run(card(why_only_you="single")); ok("d7-second-is-silent", r.exit_code == 0, str(rules(r)))
        sh("checkout", "-q", "--", LEDGER_REL)
        # the seeded stall: timeout 0 -> one exit-neutral mark for the only git-reading rule on this card's path
        r = run(card(staged_artifact="none", recommended="none"), timeout=0)
        ok("stall-exit-neutral", r.exit_code == 0 and r.timeouts == ["D7"] and not r.findings, "timeouts=%s" % r.timeouts)
        r = run(card(), timeout=0); ok("stall-marks-every-git-rule", r.exit_code == 0 and r.timeouts == ["D7", "D2"], "timeouts=%s" % r.timeouts)
        # determinism + the manipulation-check sha
        c = card(); ok("fields-sha-stable", fields_sha(c) == fields_sha(json.loads(json.dumps(c))) and len(fields_sha(c)) == 64)
        r1, r2 = run(card()), run(card()); ok("two-pass-identical", (r1.malformed, r1.findings, r1.timeouts) == (r2.malformed, r2.findings, r2.timeouts))
        # the ledger the lint reads (the D7 tail, D3's decision-resolution authority) is the consumer's:
        # .claude/hyp.json ledger_file, the plugin default when the key is absent -- never a fixed path
        ok("ledger-resolves-from-hyp-json", ledger_rel_for(tmp) == LEDGER_REL, ledger_rel_for(tmp))
        ok("ledger-default-without-hyp-json", ledger_rel_for(os.path.join(tmp, "notes")) == LEDGER_REL_DEFAULT == "ledger/ledger.jsonl")
        r = lint(card(evidence="ledger/work-ledger.jsonl@%s#L1-L1" % head), tmp, os.path.join(tmp, "ledger", "custom.jsonl"))
        ok("d3-authority-is-the-configured-ledger", rules(r) == ["D3"] and "NOT-AN-AUTHORITY" in r.findings[0][1], str(r.findings)[:160])
        with open(os.path.join(tmp, "ledger", "custom.jsonl"), "w") as fh:
            fh.write(json.dumps({"kind": "decision-resolution", "id": "DEC-%03d" % 1, "date": "2026-01-02", "disposition": "denied"}) + "\n")
        with open(os.path.join(tmp, ".claude", "hyp.json"), "w") as fh:
            fh.write(json.dumps({"ledger_file": "ledger/custom.jsonl"}) + "\n")
        sh("add", "-A"); sh("commit", "-qm", "custom ledger")
        head2 = sh("rev-parse", "HEAD").strip()
        r = lint(card(evidence="ledger/custom.jsonl@%s#L1-L1" % head2), tmp)
        ok("d3-custom-ledger-resolution-row-resolves", r.exit_code == 0 and not r.findings, str(r.findings)[:160])
        r = lint(card(evidence="ledger/work-ledger.jsonl@%s#L1-L1" % head), tmp)
        ok("d3-former-ledger-path-is-no-authority-once-reconfigured", rules(r) == ["D3"] and "NOT-AN-AUTHORITY" in r.findings[0][1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("lint-selftest: %d failure(s)" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    print(__doc__.strip().splitlines()[0])
    print("usage: decision_card_lint.py --selftest   (the lint is called from decisions.py add)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
