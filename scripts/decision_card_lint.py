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

Decision briefs (decision-brief-gate lane): the candidate row carries rec["brief"], the plain-English brief
of docs/decision-brief.schema.json, linted by rules B0-B11 seated in shape_errors (B0 PRESENT and B1 CARD-SHA
are the exit-2 class; B2-B11 are prose findings, exit 1, computed under brief_prose=True) with admission
tiering for B6, B9 and B10 read from .claude/hyp.json decision_brief_admitted_rules, report rows R1/R2, and
the git legs of B11 (pointers resolve at HEAD) and B9 (two-token author strings) inside lint(). Reads add
scripts/house-vocabulary.json and .claude/hyp.json under the root; nothing else changes in the read set.
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
        self.reports = []     # [(rule, detail)] -> printed as ADD-REPORT, exit-neutral (admission-tiered rules not admitted; R1/R2)

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


# ---------- decision briefs (decision-brief-gate lane): rules B0-B11 and report rows R1/R2 ----------
#
# The brief is the plain-English object beside a card (docs/decision-brief.schema.json): six authored
# fields -- decide, situation, yours_because, choices[] {label, in_practice, undo}, if_nothing,
# evidence_line -- plus terms{} (glosses), sources[] (path@sha40#La-Lb pointers or pipeline-fact:
# literals), card_sha (sha256 over the canonical JSON of the card's reader-facing fields) and
# provenance{} (carried through; no rule reads it). B0 and B1 are the exit-2 class and sit in
# shape_errors beside D0-D6, so the door evaluator's W1 and every in-process writer refuse exactly what
# `add` refuses (G1). The prose rules B2-B11 exit 1 (the row is appended with brief.lint.findings); they
# are computed by the same function under brief_prose=True, which only lint() turns on, because W1
# consumes shape_errors as the refusal class and must never see a prose finding. Admission (B6, B9,
# B10): a tiered rule blocks only when <root>/.claude/hyp.json decision_brief_admitted_rules names it;
# otherwise its hits print as ADD-REPORT lines (exit unchanged). Git-reading legs (B11 pointers, B9
# author strings) run inside lint() under the one timeout; a stall is an exit-neutral ADD-TIMEOUT.
# Every reading the frozen tables leave open is fixed in CONTRACT.md beside this file.

BRIEF_TEXT_FIELDS = ("decide", "situation", "yours_because", "if_nothing", "evidence_line")
BRIEF_FIELD_ORDER = ("decide", "situation", "yours_because", "choices", "if_nothing", "evidence_line",
                     "terms", "sources", "card_sha", "provenance", "lint")
BRIEF_RULE_EXIT = {"B0": 2, "B1": 2, "B2": 1, "B3": 1, "B4": 1, "B5": 1, "B6": 1, "B7": 1, "B8": 1,
                   "B9": 1, "B10": 1, "B11": 1}
BRIEF_ADMISSION_TIERED = ("B6", "B9", "B10")
BRIEF_ADMITTED_KEY = "decision_brief_admitted_rules"
BRIEF_SENTENCE_MAX_WORDS = 25
BRIEF_FIELD_MAX_SENTENCES = 5
BRIEF_BODY_MAX_WORDS = 120
BRIEF_GLOSS_WINDOW = 80
BRIEF_NEVER_AUTO_CLASSES = ("publish", "spend", "live-surface", "schema")
BRIEF_TONE_BANNED = ("like a", "as if", "imagine", "think of it as", "!", "simply", "just", "easy",
                     "don't worry", "you don't need to")
BRIEF_RELATIVE_TIME = ("yesterday", "today", "tomorrow", "tonight", "this morning", "this afternoon",
                       "this evening", "this week", "last week", "last night", "recently", "soon")
BRIEF_PIPELINE_FACTS = ("no-default-armed", "card-stays-open", "append-only-ledger", "class-never-auto-resolves")
BRIEF_PIPELINE_FACT_RE = re.compile(r"^pipeline-fact:(?:no-default-armed|card-stays-open|append-only-ledger|"
                                    r"class-never-auto-resolves|premise-shipped:[A-Za-z0-9][A-Za-z0-9._-]*)$")
BRIEF_PIPELINE_FACT_PROSE = ("no default is armed", "card stays open")
BRIEF_NOTHING_CHANGES = "nothing changes"
BRIEF_CANNOT_UNDO = "cannot be undone:"
BRIEF_UNKNOWN = "UNKNOWN:"
BRIEF_SENT_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")
BRIEF_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# id tokens: the decision id grammar, the numbered hypothesis id, the draft handle (the literal is split so
# the source carries no id-shaped string: the boundary is a number in .claude/hyp.json, never in the lint)
BRIEF_ID_TOKEN_RE = re.compile(r"\b(?:DEC-\d{3,}|H-" + r"DRAFT-[0-9a-f]{8}|H-\d{2,4})\b")
BRIEF_NAME_TAG_RE = re.compile(r"(?<![A-Za-z0-9-])NEEDS-[A-Z][A-Za-z]+")
BRIEF_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+")
BRIEF_SLASH_TOKEN_RE = re.compile(r"(?<![\w/])(?:~?/|\.{1,2}/)?(?:[\w.@*-]+/)+[\w.@*-]+")
BRIEF_FILE_TOKEN_RE = re.compile(r"\b[\w-]+\.(?:py|md|json|jsonl|sh|html|yml|yaml|png|txt|lock|log)\b")
BRIEF_COMMAND_RE = re.compile(r"\b(?:python3?|git|gh|bash|sh|npm|make|curl|pip3?)\s+(?:-{1,2}[A-Za-z]|[a-z][\w./-]*)"
                              r"|(?<!\w)--[a-z][\w-]*|\$\(")
BRIEF_HASH_RE = re.compile(r"\b(?=[0-9a-f]{7,40}\b)\d*[a-f][0-9a-f]*\b")
BRIEF_SCORE_RE = re.compile(r"\b\d+\s*[x×]\s*\d+/\d+\b|\b\d{1,2}/\d{1,2}\b(?!\d)")
BRIEF_REPO_DIRS = ("scripts/", "hooks/", "ledger/", "research/", "experiments/", "hypotheses/", "operating-model/",
                   "docs/", "kernel/", "skills/", "manifests/", ".claude/", "templates/", "publish/", "exports/")
BRIEF_COINAGE_RE = re.compile(r"\b[a-z]+(?:-[a-z]+)+\b")
BRIEF_COINAGE_ALLOW = frozenset(("two-way", "one-way", "read-only", "write-once", "follow-up", "built-in", "e-mail",
                                 "long-standing", "in-place", "byte-for-byte", "up-to-date", "self-closing",
                                 "one-off", "well-known", "pre-declared", "re-run", "re-sign", "day-to-day",
                                 "so-called", "non-empty", "co-author", "opt-in", "opt-out", "add-on", "check-in",
                                 "set-up", "sign-in", "log-in", "on-line", "off-line", "well-formed", "plain-english",
                                 "machine-checked", "first-class", "single-select", "multi-select", "on-file",
                                 "second-device", "one-time", "one-line", "first-day", "cannot-be-undone"))
VOCAB_BASENAME = "house-vocabulary.json"
_VOCAB_CACHE = {}


def brief_words(text):
    """The contract's word count (scripts/clarity-lint-v1.py words()): whitespace tokens carrying a letter or digit."""
    return [w for w in re.split(r"\s+", str(text)) if re.search(r"[A-Za-z0-9]", w)]


def brief_sentences(text):
    """The contract's sentence split (scripts/clarity-lint-v1.py SENT_SPLIT_RE): a run of . ! ? followed by
    whitespace or the end of the field."""
    return [s.strip() for s in BRIEF_SENT_SPLIT_RE.split(str(text)) if s.strip()]


def _brief_choices(brief):
    ch = brief.get("choices") if isinstance(brief.get("choices"), list) else []
    return [c if isinstance(c, dict) else {} for c in ch]


def brief_text_items(brief):
    """[(path, text)] over the authored text fields in render order: decide, situation, yours_because,
    choices[i].in_practice, choices[i].undo, if_nothing, evidence_line (string values only)."""
    items = []
    for f in ("decide", "situation", "yours_because"):
        if isinstance(brief.get(f), str):
            items.append((f, brief[f]))
    for i, c in enumerate(_brief_choices(brief)):
        for k in ("in_practice", "undo"):
            if isinstance(c.get(k), str):
                items.append(("choices[%d].%s" % (i, k), c[k]))
    for f in ("if_nothing", "evidence_line"):
        if isinstance(brief.get(f), str):
            items.append((f, brief[f]))
    return items


def brief_gloss_items(brief):
    """[(path, gloss)] over terms{} in file order (the glosses count as authored text: B2, B4, B6, B9, B10)."""
    terms = brief.get("terms") if isinstance(brief.get("terms"), dict) else {}
    return [("terms.%s" % k, v) for k, v in terms.items() if isinstance(v, str)]


def brief_sources(brief):
    src = brief.get("sources")
    return src if isinstance(src, list) else []


def _canon_bytes(obj):
    """The fields_sha serialisation: sorted keys, compact separators, UTF-8, non-ASCII kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def reader_fields(rec):
    """The card's reader-facing fields with every absent field present as None (the six_fields convention)."""
    ask = rec.get("ask") if isinstance(rec.get("ask"), dict) else {}
    opts = [{"label": o.get("label"), "description": o.get("description"), "undo": o.get("undo")}
            for o in _options(rec)]
    return (rec.get("title"), ask.get("question"), opts, rec.get("why_only_you"), rec.get("recommended"),
            rec.get("default_on_silence"), rec.get("externality"))


def card_sha_objects(rec):
    """The canonical objects B1 accepts; the FIRST is the one brief-skeleton writes and card_sha() returns. The
    spec names the fields (title, ask.question, ask.options[].label/description/undo, why_only_you, recommended,
    default_on_silence, externality) and the serialisation, not the object's nesting; every transcription a blind
    author would make of that list is accepted (CONTRACT.md reading 2), because a flipped byte matches none of them."""
    title, question, opts, why, recommended, dflt, ext = reader_fields(rec)
    tail = {"why_only_you": why, "recommended": recommended, "default_on_silence": dflt, "externality": ext}
    opts_no_null = [{k: v for k, v in o.items() if v is not None} for o in opts]
    objs = []
    for options in (opts, opts_no_null):
        objs.append(dict({"title": title, "ask.question": question, "ask.options": options}, **tail))
        objs.append(dict({"title": title, "ask": {"question": question, "options": options}}, **tail))
        objs.append(dict({"title": title, "question": question, "options": options}, **tail))
        objs.append(dict({"title": title, "ask.question": question, "ask.options[]": options}, **tail))
    cols = {"ask.options[].label": [o["label"] for o in opts],
            "ask.options[].description": [o["description"] for o in opts],
            "ask.options[].undo": [o["undo"] for o in opts]}
    objs.append(dict({"title": title, "ask.question": question}, **dict(cols, **tail)))
    return objs


def card_sha(rec):
    """sha256 over the canonical JSON of the reader-facing fields (the first accepted object)."""
    return hashlib.sha256(_canon_bytes(card_sha_objects(rec)[0])).hexdigest()


def card_sha_variants(rec):
    return set(hashlib.sha256(_canon_bytes(o)).hexdigest() for o in card_sha_objects(rec))


def brief_sha(brief):
    """sha256 over the canonical JSON of the brief with its lint stamp removed (every other byte counts, so an
    in-place edit of a brief row renders stale)."""
    return hashlib.sha256(_canon_bytes({k: v for k, v in brief.items() if k != "lint"})).hexdigest()


def self_sha7():
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:7]


def admitted_rules(root):
    """The admission-tiered rules that block: <root>/.claude/hyp.json decision_brief_admitted_rules (a list of
    ids among B6, B9, B10). Absent key or file: none admitted, every tiered rule report-only."""
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return frozenset()
    val = data.get(BRIEF_ADMITTED_KEY) if isinstance(data, dict) else None
    if not isinstance(val, list):
        return frozenset()
    return frozenset(str(v) for v in val if str(v) in BRIEF_ADMISSION_TIERED)


def vocab_path_beside():
    """scripts/house-vocabulary.json: beside this file in an install; for a copy of the lint outside scripts/,
    the nearest ancestor's scripts/house-vocabulary.json. None when neither exists."""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, VOCAB_BASENAME)
    if os.path.isfile(cand):
        return cand
    d = here
    for _ in range(8):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
        cand = os.path.join(d, "scripts", VOCAB_BASENAME)
        if os.path.isfile(cand):
            return cand
    return None


def _norm_term(s):
    return re.sub(r"[\s-]+", " ", str(s).strip().lower())


def _term_re(forms):
    """One case-insensitive pattern over a term's forms; a space or hyphen inside a form matches any run of either."""
    alts = []
    for f in sorted(set(forms), key=len, reverse=True):
        parts = [re.escape(p) for p in re.split(r"[\s-]+", f.strip()) if p]
        if parts:
            alts.append(r"[\s-]+".join(parts))
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alts) + r")(?![A-Za-z0-9])", re.I)


def load_vocabulary(path=None):
    """-> {"entries": [(headword, [forms], status, canonical)], "forms": set of normalised forms}. Entries whose
    headword is the NEEDS-<Name> escalation tag are B9's alone and are dropped here; the pattern keys (the id
    and score grammars) are enforced by the id-token leg and B6, not matched as terms. Empty when no file."""
    path = path or vocab_path_beside()
    if path is None:
        return {"entries": [], "forms": set()}
    if path in _VOCAB_CACHE:
        return _VOCAB_CACHE[path]
    out = {"entries": [], "forms": set()}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        terms = data.get("terms", {}) if isinstance(data, dict) else {}
        if isinstance(terms, list):
            terms = dict((t.get("term") or t.get("headword") or "", t) for t in terms if isinstance(t, dict))
        for head, entry in terms.items():
            if not isinstance(entry, dict) or not isinstance(head, str) or not head:
                continue
            if BRIEF_NAME_TAG_RE.match(head) or head in ("H-NNN", "DEC-NNN", "N-id", "score notation"):
                continue
            status = str(entry.get("status") or "")
            canonical = entry.get("deprecated-alias-of")
            if canonical and not status.startswith("deprecated"):
                status = "deprecated-alias-of:%s" % canonical
            elif status.startswith("deprecated-alias-of:") and not canonical:
                canonical = status.split(":", 1)[1]
            forms = [head] + [v for v in (entry.get("variants") or []) if isinstance(v, str) and v]
            out["entries"].append((head, forms, status, canonical))
            out["forms"].update(_norm_term(f) for f in forms)
    except (OSError, ValueError, AttributeError):
        pass
    _VOCAB_CACHE[path] = out
    return out


def brief_presence_errors(rec, brief):
    """B0 PRESENT and B1 CARD-SHA -> [(rule, detail)] (the exit-2 class)."""
    errs = []
    if not isinstance(brief, dict):
        return [("B0", "brief missing: pass --brief <brief.json> carrying the seven fields of docs/decision-brief.schema.json")]
    for f in BRIEF_TEXT_FIELDS:
        if not isinstance(brief.get(f), str) or not brief.get(f).strip():
            errs.append(("B0", "%s empty" % f))
    choices = brief.get("choices")
    if not isinstance(choices, list) or not choices:
        errs.append(("B0", "choices empty (one {label, in_practice, undo} per ask.options[] entry)"))
    else:
        got = [str(c.get("label", "")) if isinstance(c, dict) else "" for c in choices]
        want = _labels(rec)
        if got != want:
            errs.append(("B0", "choices[].label %s do not equal ask.options[].label %s one-to-one and in order"
                         % (json.dumps(got, ensure_ascii=False), json.dumps(want, ensure_ascii=False))))
        for i, c in enumerate(choices):
            for k in ("in_practice", "undo"):
                if not isinstance(c, dict) or not isinstance(c.get(k), str) or not c.get(k).strip():
                    errs.append(("B0", "choices[%d] has no %s" % (i, k)))
    for path, text in brief_text_items(brief) + brief_gloss_items(brief):
        if BRIEF_UNKNOWN in text:
            errs.append(("B0", "%s carries an UNKNOWN: statement (the record does not supply it; a commit must, before the card is filed)" % path))
    src = brief.get("sources")
    if not isinstance(src, list) or not src or any(not isinstance(s, str) or not s.strip() for s in src):
        errs.append(("B0", "sources[] must be a non-empty list of <path>@<sha40>#L<a>-L<b> pointers or pipeline-fact: literals, one per authored statement"))
    sha = brief.get("card_sha")
    if not isinstance(sha, str) or sha not in card_sha_variants(rec):
        errs.append(("B1", "card_sha %s does not equal sha256 over the canonical JSON of the reader-facing fields (%s)"
                     % (json.dumps(sha, ensure_ascii=False), card_sha(rec))))
    return errs


def _b2_b3_b4(brief):
    out = []
    items = brief_text_items(brief)
    glosses = brief_gloss_items(brief)
    for path, text in items + glosses:
        for s in brief_sentences(text):
            n = len(brief_words(s))
            if n > BRIEF_SENTENCE_MAX_WORDS:
                out.append(("B2", "%s: a %d-word sentence (max %d)" % (path, n, BRIEF_SENTENCE_MAX_WORDS)))
    for path, text in items:
        n = len(brief_sentences(text))
        if path == "decide":
            if n != 1:
                out.append(("B3", "decide has %d sentences (exactly 1)" % n))
        elif n > BRIEF_FIELD_MAX_SENTENCES:
            out.append(("B3", "%s has %d sentences (max %d)" % (path, n, BRIEF_FIELD_MAX_SENTENCES)))
    total = sum(len(brief_words(t)) for _p, t in items + glosses)
    if total > BRIEF_BODY_MAX_WORDS:
        out.append(("B4", "the six authored fields run %d words with glosses, labels excluded (max %d)" % (total, BRIEF_BODY_MAX_WORDS)))
    return out


def _glossed(body, end, keys, *names):
    if any(_norm_term(n) in keys for n in names if n):
        return True
    return "(" in body[end:end + BRIEF_GLOSS_WINDOW]


def _b5(brief, vocab):
    out = []
    body = "\n".join(t for _p, t in brief_text_items(brief))
    terms = brief.get("terms") if isinstance(brief.get("terms"), dict) else {}
    keys = set(_norm_term(k) for k in terms)
    for head, forms, status, canonical in vocab.get("entries", []):
        m = _term_re(forms).search(body)
        if not m:
            continue
        if status.startswith("deprecated"):
            out.append(("B5", "deprecated alias %r used; write %r instead" % (m.group(0), canonical or "its canonical headword")))
        elif status == "house-only" and not _glossed(body, m.end(), keys, head, m.group(0)):
            out.append(("B5", "house-only term %r unglossed at first use (no gloss within %d characters and absent from terms{})"
                        % (m.group(0), BRIEF_GLOSS_WINDOW)))
    seen = set()
    for m in BRIEF_ID_TOKEN_RE.finditer(body):
        tok = m.group(0)
        if tok in seen:
            continue
        seen.add(tok)
        if not _glossed(body, m.end(), keys, tok):
            out.append(("B5", "id token %s unglossed at first use (no gloss within %d characters and absent from terms{})"
                        % (tok, BRIEF_GLOSS_WINDOW)))
    return out


def _pathlike(tok):
    if tok.count("/") >= 2 or tok.startswith(BRIEF_REPO_DIRS) or tok.startswith(("/", "~/", "./", "../")):
        return True
    last = tok.rsplit("/", 1)[-1]
    return "." in last.strip(".")


def _b6(items):
    out = []
    for path, text in items:
        hits = []
        m = BRIEF_URL_RE.search(text)
        if m:
            hits.append("URL %r" % m.group(0))
        path_hit = None
        for m in BRIEF_SLASH_TOKEN_RE.finditer(text):
            if _pathlike(m.group(0)):
                path_hit = m.group(0)
                hits.append("repository path %r" % path_hit)
                break
        m = BRIEF_FILE_TOKEN_RE.search(text)
        if m and not (path_hit and m.group(0) in path_hit):
            hits.append("file name %r" % m.group(0))
        m = BRIEF_COMMAND_RE.search(text)
        if m:
            hits.append("shell command %r" % m.group(0))
        for m in BRIEF_HASH_RE.finditer(text):
            window = text[max(0, m.start() - 40):m.end() + 40]
            if re.search(r"(?i)\bcommit", window) or any(_pathlike(x.group(0)) for x in BRIEF_SLASH_TOKEN_RE.finditer(window)):
                hits.append("commit hash %r" % m.group(0))
                break
        m = BRIEF_SCORE_RE.search(text)
        if m:
            hits.append("score shorthand %r" % m.group(0))
        for phrase in BRIEF_RELATIVE_TIME:
            if re.search(r"(?i)(?<![A-Za-z])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![A-Za-z])", text):
                hits.append("relative-time token %r" % phrase)
                break
        for h in hits:
            out.append(("B6", "%s: %s is machine text; it belongs on the machine line" % (path, h)))
    return out


def _b7(rec, brief):
    out = []
    text = brief.get("if_nothing") if isinstance(brief.get("if_nothing"), str) else ""
    low = text.lower()
    has_date = bool(BRIEF_ISO_DATE_RE.search(text))
    has_nothing = BRIEF_NOTHING_CHANGES in low
    has_fact = any(p in low for p in BRIEF_PIPELINE_FACT_PROSE)
    if not (has_date or has_nothing or has_fact):
        out.append(("B7", "if_nothing carries neither an ISO date nor the literal \"nothing changes\" nor the pipeline fact (no default is armed; the card stays open)"))
    dflt = rec.get("default_on_silence")
    labels = _labels(rec)
    armed_label = isinstance(dflt, str) and dflt in labels
    if dflt == "nothing-changes" and not has_nothing:
        out.append(("B7", "if_nothing disagrees with default_on_silence nothing-changes (the literal \"nothing changes\" is absent)"))
    elif armed_label and (has_nothing or not has_date):
        out.append(("B7", "if_nothing disagrees with the armed default %r (an armed default names its ISO date and never says nothing changes)" % dflt))
    automatic = armed_label or (has_date and not has_nothing and not has_fact)
    if automatic and rec.get("class") in BRIEF_NEVER_AUTO_CLASSES:
        out.append(("B7", "class %s never auto-resolves, yet if_nothing names an automatic default (the silence policy's boundary)" % rec.get("class")))
    return out


def _door_stamped(rec):
    opts = _options(rec)
    return bool(opts) and all(o.get("undo") in UNDO_VOCAB for o in opts)


def _has_source_ref(sources):
    for s in sources:
        if isinstance(s, str) and (BRIEF_PIPELINE_FACT_RE.match(s) or POINTER_RE.match(s)):
            return True
    return False


def _b8(rec, brief):
    out = []
    choices = _brief_choices(brief)
    opts = _options(rec)
    if _door_stamped(rec):
        for i, c in enumerate(choices):
            undo_text = str(c.get("undo") or "").strip()
            token = opts[i].get("undo") if i < len(opts) else None
            says_cannot = undo_text.lower().startswith(BRIEF_CANNOT_UNDO.rstrip(":")) 
            if token == "none" and not undo_text.lower().startswith(BRIEF_CANNOT_UNDO):
                out.append(("B8", "choices[%d] %r: the door says undo none but the brief's undo does not begin \"cannot be undone:\"" % (i, c.get("label"))))
            elif token in REVERTIBLE + ("ledger-row",) and says_cannot:
                out.append(("B8", "choices[%d] %r: the door says undo %s (reversible) but the brief says it cannot be undone" % (i, c.get("label"), token)))
    else:
        if choices and not _has_source_ref(brief_sources(brief)):
            out.append(("B8", "legacy card: the choices' undo statements have no resolving sources[] pointer and no pipeline-fact: literal"))
    return out


def _b9_tags(items):
    out = []
    for path, text in items:
        m = BRIEF_NAME_TAG_RE.search(text)
        if m:
            out.append(("B9", "%s: an escalation tag of the form NEEDS-<Name> at offset %d is machine text and may carry a name (git is attribution)" % (path, m.start())))
    return out


def _b10(items):
    out = []
    for path, text in items:
        for phrase in BRIEF_TONE_BANNED:
            if phrase == "!":
                hit = "!" in text
            else:
                pat = r"(?<![A-Za-z])" + re.escape(phrase).replace(r"\ ", r"\s+").replace("'", "['’]") + r"(?![A-Za-z])"
                hit = re.search(pat, text, re.I) is not None
            if hit:
                out.append(("B10", "%s: banned tone token %r" % (path, phrase)))
    return out


def _b11_form(brief):
    out = []
    for i, s in enumerate(brief_sources(brief)):
        if not isinstance(s, str):
            continue
        if s.startswith("pipeline-fact:"):
            if not BRIEF_PIPELINE_FACT_RE.match(s):
                out.append(("B11", "sources[%d] literal %r is outside the fixed list (%s, premise-shipped:<tag>)" % (i, s, "|".join(BRIEF_PIPELINE_FACTS))))
            continue
        m = POINTER_RE.match(s)
        if not m:
            out.append(("B11", "sources[%d] %r is neither <path>@<sha40>#L<a>-L<b> nor a pipeline-fact: literal" % (i, s)))
        elif not (1 <= int(m.group("a")) <= int(m.group("b"))):
            out.append(("B11", "sources[%d] %r has an empty or reversed line range" % (i, s)))
    return out


def brief_prose_errors(rec, brief, vocab=None):
    """B2-B11, the git-free legs -> [(rule, detail)] in rule order. Runs only on a brief that passed B0 and B1."""
    vocab = vocab if vocab is not None else load_vocabulary()
    items = brief_text_items(brief) + brief_gloss_items(brief)
    out = []
    out.extend(_b2_b3_b4(brief))
    out.extend(_b5(brief, vocab))
    out.extend(_b6(items))
    out.extend(_b7(rec, brief))
    out.extend(_b8(rec, brief))
    out.extend(_b9_tags(items))
    out.extend(_b10(items))
    out.extend(_b11_form(brief))
    return out


def brief_shape_errors(rec, brief_prose=False, vocab=None):
    """The brief rules over one candidate row carrying rec["brief"]: B0 and B1 always (exit 2); B2-B11 only under
    brief_prose=True and only when B0 and B1 are silent."""
    brief = rec.get("brief")
    errs = brief_presence_errors(rec, brief)
    if errs or not brief_prose:
        return errs
    return brief_prose_errors(rec, brief, vocab=vocab)


def b11_sources_resolve(brief, git):
    """B11's git leg: every well-formed pointer resolves at HEAD (git cat-file) and its lines exist."""
    out = []
    for i, s in enumerate(brief_sources(brief)):
        m = POINTER_RE.match(s) if isinstance(s, str) else None
        if not m:
            continue
        code, content = git.text(["cat-file", "-p", "%s:%s" % (m.group("sha"), m.group("path"))])
        if code != 0:
            out.append(("B11", "sources[%d] %s does not resolve at HEAD (git cat-file)" % (i, s)))
            continue
        n = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        if int(m.group("b")) > n:
            out.append(("B11", "sources[%d] %s: lines L%s-L%s do not exist (the file has %d lines)" % (i, s, m.group("a"), m.group("b"), n)))
    return out


def b9_author_strings(brief, git):
    """B9's git leg: a full two-token author string from `git log --format=%an` over the clone in any authored
    field (a single token never counts). The detail never echoes the string."""
    code, out_text = git.text(["log", "--format=%an"])
    if code != 0:
        return []
    names = sorted(set(l.strip() for l in out_text.splitlines() if len(l.split()) == 2))
    out = []
    for path, text in brief_text_items(brief) + brief_gloss_items(brief):
        for name in names:
            if re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", text):
                out.append(("B9", "%s: a full two-token git author string appears in the brief (names never appear in a brief; git is attribution)" % path))
                break
    return out


def brief_report_rows(brief, vocab=None):
    """R1 COINAGE and R2 GLOSS-DENSITY: report rows that print and never exit."""
    vocab = vocab if vocab is not None else load_vocabulary()
    items = brief_text_items(brief)
    glosses = brief_gloss_items(brief)
    body = "\n".join(t for _p, t in items)
    forms = vocab.get("forms", set())
    coin = sorted(set(m.group(0) for m in BRIEF_COINAGE_RE.finditer(body.lower())
                      if _norm_term(m.group(0)) not in forms and m.group(0) not in BRIEF_COINAGE_ALLOW))
    out = []
    if coin:
        out.append(("R1", "unregistered coinage candidates (hyphenated compounds outside the vocabulary): %s" % ", ".join(coin)))
    terms = brief.get("terms") if isinstance(brief.get("terms"), dict) else {}
    keys = set(_norm_term(k) for k in terms)
    words = sum(len(brief_words(t)) for _p, t in items + glosses)
    pref_used = pref_glossed = 0
    for head, fs, status, _c in vocab.get("entries", []):
        if status != "preferred":
            continue
        m = _term_re(fs).search(body)
        if m:
            pref_used += 1
            if _glossed(body, m.end(), keys, head, m.group(0)):
                pref_glossed += 1
    per100 = (100.0 * len(glosses) / words) if words else 0.0
    out.append(("R2", "glosses=%d words=%d per100=%.1f preferred-used=%d preferred-glossed=%d"
                % (len(glosses), words, per100, pref_used, pref_glossed)))
    return out


def _rule_sort_key(item):
    rule = item[0]
    m = re.match(r"^([A-Z]+)(\d+)$", rule)
    return ((m.group(1), int(m.group(2))) if m else (rule, 0), item[1])


# ---------- exit-2 class: presence, vocabulary, form (no git) ----------

def shape_errors(rec, brief_prose=False, vocab=None):
    """-> [(rule, detail)] for D0, D1, D3 (form), D4 (form), D5, D6 and the brief's B0 PRESENT / B1 CARD-SHA: the exit-2
    class every writer refuses on (the door evaluator's W1 reads this list as its defects). Under brief_prose=True the
    prose rules B2-B11 (git-free legs) are appended too -- only lint() asks for them. Empty when the six door fields
    and the brief are well-formed."""
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
    errs.extend(brief_shape_errors(rec, brief_prose=brief_prose, vocab=vocab))
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

def lint(rec, root, ledger_path=None, git_timeout=20, brief_only=False, vocab=None):
    """-> LintResult over one validated candidate row (shape-valid in the legacy sense). brief_only=True (the
    `brief` subcommand: a retrofit or correction on a row already on file) runs the brief rules alone."""
    res = LintResult()
    root = os.path.abspath(root)
    ledger_path = ledger_path or os.path.join(root, ledger_rel_for(root))
    ledger_rel = os.path.relpath(os.path.abspath(ledger_path), root).replace(os.sep, "/")
    git = Git(root, git_timeout)
    shape = shape_errors(rec, brief_prose=True, vocab=vocab)
    if brief_only:
        shape = [e for e in shape if e[0] in BRIEF_RULE_EXIT]
    res.malformed.extend(e for e in shape if BRIEF_RULE_EXIT.get(e[0], 2) == 2)
    prose = [e for e in shape if BRIEF_RULE_EXIT.get(e[0], 2) == 1]
    if res.malformed:
        return res
    if not brief_only:
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
    brief = rec.get("brief") if isinstance(rec.get("brief"), dict) else None
    if brief is not None:
        for rule, fn in (("B9", lambda: b9_author_strings(brief, git)), ("B11", lambda: b11_sources_resolve(brief, git))):
            try:
                hits = fn()
            except GitStall:
                res.timeouts.append(rule)
                hits = []
            prose.extend(hits)
        admitted = admitted_rules(root)
        for rule, detail in prose:
            if rule in BRIEF_ADMISSION_TIERED and rule not in admitted:
                res.reports.append((rule, detail))
            else:
                res.findings.append((rule, detail))
        res.reports.extend(brief_report_rows(brief, vocab=vocab))
    res.findings.sort(key=_rule_sort_key)
    res.reports.sort(key=_rule_sort_key)
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

        SYN_VOCAB = {"entries": [("lane", ["lane", "lanes"], "house-only", None),
                                 ("gate", ["gate", "gates"], "preferred", None),
                                 ("old-word", ["old-word"], "deprecated-alias-of:new-word", "new-word")],
                     "forms": {"lane", "lanes", "gate", "gates", "old word"}}

        DEFAULT_UNDO = {"reversible": "Reverting the landing commit restores the previous state.",
                        "ledger-row": "A later resolution supersedes this one.",
                        "none": "cannot be undone: the release is out and the wait is over."}
        DEFAULT_IF_NOTHING = ("Unanswered by 2026-01-15, go happens and the change ships.",
                              "Unanswered by 2026-01-15, hold happens and the change waits.",
                              "Nothing changes: the card stays open until you answer.",
                              "No default is armed; the card stays open and is listed at every session start.")

        def derive_undo(token):
            return DEFAULT_UNDO["none" if token == "none" else ("ledger-row" if token == "ledger-row" else "reversible")]

        def derive_if_nothing(c):
            dflt = c.get("default_on_silence")
            labels = [o.get("label") for o in c["ask"]["options"]]
            if dflt in labels:
                return "Unanswered by 2026-01-15, %s happens and the change %s." % (dflt, "ships" if dflt == labels[0] else "waits")
            if dflt == "nothing-changes":
                return DEFAULT_IF_NOTHING[2]
            return DEFAULT_IF_NOTHING[3]

        def brief_for(c, **over):
            opts = c["ask"]["options"]
            labels = [o.get("label") for o in opts]
            b = {"decide": "Decide whether the change ships now or waits.",
                 "situation": "A small script change is ready. Nothing else depends on it.",
                 "yours_because": "You hold the key that signs the release.",
                 "choices": [{"label": labels[0], "in_practice": "The change lands and the release goes out.",
                              "undo": derive_undo(opts[0].get("undo"))},
                             {"label": labels[1], "in_practice": "The change waits for the next release.",
                              "undo": derive_undo(opts[1].get("undo"))}],
                 "if_nothing": derive_if_nothing(c),
                 "evidence_line": "The change passed every check in both scored runs.",
                 "terms": {}, "sources": ["pipeline-fact:card-stays-open"], "provenance": {"protocol": "inline"}}
            b.update(over)
            b["card_sha"] = card_sha(c)
            return b

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
            if base.get("class") in ("publish", "spend", "live-surface", "schema") and "default_on_silence" not in kw:
                base["default_on_silence"] = "nothing-changes"
            base["brief"] = brief_for(base)
            return base

        def undo(c, i, v):
            if v is None:
                c["ask"]["options"][i].pop("undo", None)
            else:
                c["ask"]["options"][i]["undo"] = v
            return c

        def run(c, timeout=20, resha=True, **kw):
            if resha and isinstance(c.get("brief"), dict):
                opts = c["ask"]["options"]
                for i, ch in enumerate(c["brief"].get("choices") or []):
                    if ch.get("undo") in DEFAULT_UNDO.values() and i < len(opts):
                        ch["undo"] = derive_undo(opts[i].get("undo"))
                if c["brief"].get("if_nothing") in DEFAULT_IF_NOTHING:
                    c["brief"]["if_nothing"] = derive_if_nothing(c)
                c["brief"]["card_sha"] = card_sha(c)
            kw.setdefault("vocab", SYN_VOCAB)
            return lint(c, tmp, os.path.join(tmp, ledger_rel_for(tmp)), git_timeout=timeout, **kw)

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
        ok("stall-exit-neutral", r.exit_code == 0 and r.timeouts == ["D7", "B9"] and not r.findings, "timeouts=%s" % r.timeouts)
        r = run(card(), timeout=0); ok("stall-marks-every-git-rule", r.exit_code == 0 and r.timeouts == ["D7", "D2", "B9"], "timeouts=%s" % r.timeouts)
        # ---- decision briefs (decision-brief-gate): rules B0-B11 once each, synthetic data only ----
        def brules(r):
            return sorted(set([m[0] for m in r.malformed] + [f[0] for f in r.findings]), key=lambda x: (x[0], int(x[1:])))

        def bcard(**over):
            c = card()
            c["brief"] = brief_for(c, **over)
            return c

        def mut(c, **over):
            c["brief"].update(over)
            return c

        r = run(card()); ok("brief-clean", r.exit_code == 0 and not r.findings and [x[0] for x in r.reports] == ["R2"], "%s %s" % (brules(r), r.reports))
        c = card(); c.pop("brief"); r = run(c)
        ok("b0-brief-missing", r.exit_code == 2 and brules(r) == ["B0"] and len(r.malformed) == 1 and r.malformed[0][1].startswith("brief missing"), str(r.malformed))
        ok("b0-empty-field", brules(run(mut(bcard(), situation=""))) == ["B0"])
        c = bcard(); c["brief"]["choices"].reverse(); ok("b0-label-order", brules(run(c)) == ["B0"])
        c = bcard(); c["brief"]["choices"][0].pop("undo"); ok("b0-missing-undo", brules(run(c)) == ["B0"])
        ok("b0-unknown", brules(run(mut(bcard(), evidence_line="UNKNOWN: what the record does not say"))) == ["B0"])
        ok("b0-no-sources", brules(run(mut(bcard(), sources=[]))) == ["B0"])
        c = bcard(); c["brief"]["card_sha"] = ("0" if c["brief"]["card_sha"][0] != "0" else "1") + c["brief"]["card_sha"][1:]
        r = run(c, resha=False); ok("b1-flipped-sha", r.exit_code == 2 and brules(r) == ["B1"], str(brules(r)))
        nested = {"title": c.get("title"), "ask": {"question": c["ask"].get("question"), "options": [{"label": o.get("label"), "description": o.get("description"), "undo": o.get("undo")} for o in c["ask"]["options"]]},
                  "why_only_you": c.get("why_only_you"), "recommended": c.get("recommended"), "default_on_silence": c.get("default_on_silence"), "externality": c.get("externality")}
        c["brief"]["card_sha"] = hashlib.sha256(json.dumps(nested, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        ok("b1-accepts-nested-variant", run(c, resha=False).exit_code == 0)
        long26 = " ".join(["word"] * 26) + "."
        r = run(mut(bcard(), situation=long26)); ok("b2-26-words", r.exit_code == 1 and brules(r) == ["B2"], str(brules(r)))
        ok("b3-six-sentences", brules(run(mut(bcard(), situation="One. Two. Three. Four. Five. Six."))) == ["B3"])
        ok("b3-decide-two-sentences", brules(run(mut(bcard(), decide="Decide now. Or later."))) == ["B3"])
        five = " ".join([" ".join(["word"] * 20) + "."] * 5)
        r = run(mut(bcard(), situation=five)); ok("b4-121-words", brules(r) == ["B4"], str(brules(r)))
        r = run(mut(bcard(), situation="The lane is ready. Nothing else depends on it.")); ok("b5-unglossed-house-only", brules(r) == ["B5"], str(brules(r)))
        r = run(mut(bcard(), situation="The lane (one experiment's pipeline) is ready. Nothing else depends on it.")); ok("b5-paren-gloss-within-80", r.exit_code == 0, str(brules(r)))
        r = run(mut(bcard(), situation="The lane is ready. Nothing else depends on it.", terms={"lane": "one experiment's pipeline"})); ok("b5-terms-gloss", r.exit_code == 0, str(brules(r)))
        r = run(mut(bcard(), situation="The old-word is ready. Nothing else depends on it.")); ok("b5-deprecated-alias", brules(r) == ["B5"] and "deprecated" in r.findings[0][1])
        r = run(mut(bcard(), situation="Spec %s is ready. Nothing else depends on it." % ("H-%03d" % 42))); ok("b5-bare-id-token", brules(r) == ["B5"], str(brules(r)))
        r = run(mut(bcard(), situation="The gate is ready. Nothing else depends on it.")); ok("b5-preferred-report-only", r.exit_code == 0 and any(x[0] == "R2" and "preferred-used=1" in x[1] for x in r.reports), str(r.reports))
        r = run(mut(bcard(), situation="See scripts/decisions.py for the change. Nothing else depends on it."))
        ok("b6-path-report-only-unadmitted", r.exit_code == 0 and [x[0] for x in r.reports if x[0] == "B6"] == ["B6"], str(r.reports))
        for txt, name in (("Run git push origin main to land it.", "command"), ("See https://example.invalid/x for it.", "url"),
                          ("It passed 5/5 in both runs.", "score"), ("It landed yesterday and holds.", "relative-time"),
                          ("Commit 9f8e7d6c5b carried it.", "hash-beside-commit")):
            r = run(mut(bcard(), situation=txt)); ok("b6-%s" % name, [x[0] for x in r.reports if x[0] == "B6"] == ["B6"], str(r.reports))
        r = run(mut(bcard(), situation="Ready and/or waiting. Nothing else depends on it.")); ok("b6-and-or-is-not-a-path", not [x for x in r.reports if x[0] == "B6"], str(r.reports))
        r = run(mut(bcard(), if_nothing="The change waits for you.")); ok("b7-undated", brules(r) == ["B7"], str(brules(r)))
        c = bcard(**{"if_nothing": "Unanswered by 2026-01-15, the change ships."}); c["class"] = "publish"
        r = run(c); ok("b7-publish-automatic-default", brules(r) == ["B7"] and "never auto-resolves" in " ".join(d for _r, d in r.findings), str(r.findings))
        c = card(default_on_silence="nothing-changes"); c["brief"] = brief_for(c, if_nothing="Unanswered by 2026-01-15, hold happens.")
        r = run(c); ok("b7-disagrees-with-nothing-changes", brules(r) == ["B7"], str(brules(r)))
        c = card(default_on_silence="nothing-changes"); c["brief"] = brief_for(c, if_nothing="Nothing changes: the card stays open until you answer.")
        ok("b7-nothing-changes-agrees", run(c).exit_code == 0)
        c = undo(card(), 1, "none"); c["staged_artifact"] = "make all"; c["brief"] = brief_for(c)
        c["brief"]["choices"][1]["undo"] = "The wait ends when the release ships."
        r = run(c); ok("b8-none-without-cannot-be-undone", brules(r) == ["B8"], str(brules(r)))
        c = undo(card(), 1, "none"); c["staged_artifact"] = "make all"; c["brief"] = brief_for(c)
        c["brief"]["choices"][1]["undo"] = "cannot be undone: the wait is over once the release ships."
        r = run(c); ok("b8-none-with-cannot-be-undone", r.exit_code == 0, str(r.findings))
        c = bcard(); c["brief"]["choices"][0]["undo"] = "cannot be undone: the release is out."
        r = run(c); ok("b8-reversible-says-cannot", brules(r) == ["B8"], str(brules(r)))
        leg = card(); leg["ask"]["options"] = [{"label": o["label"], "description": o["description"]} for o in leg["ask"]["options"]]
        for k in ("staged_artifact", "evidence", "externality", "recommended", "default_on_silence"):
            leg.pop(k, None)
        leg["brief"] = brief_for(leg, if_nothing="Nothing changes: the card stays open until you answer.", sources=["see the silence policy"])
        r = run(leg, brief_only=True); ok("b8-legacy-no-source-ref-and-b11-form", brules(r) == ["B8", "B11"] and r.exit_code == 1, str(r.findings))
        leg["brief"]["sources"] = ["pipeline-fact:append-only-ledger"]; leg["brief"]["card_sha"] = card_sha(leg)
        r = run(leg, brief_only=True); ok("brief-only-skips-door-rules", r.exit_code == 0 and not r.malformed, str(r.malformed))
        r = run(mut(bcard(), situation="Tagged NEEDS-Somebody for a look. Nothing else depends on it."))
        ok("b9-tag-report-only-unadmitted", r.exit_code == 0 and [x[0] for x in r.reports if x[0] == "B9"] == ["B9"] and "Somebody" not in r.reports[0][1], str(r.reports))
        r = run(mut(bcard(), situation="Ask lint selftest about it. Nothing else depends on it."))
        ok("b9-two-token-author-string", [x[0] for x in r.reports if x[0] == "B9"] == ["B9"] and "lint selftest" not in " ".join(d for _r, d in r.reports), str(r.reports))
        r = run(mut(bcard(), situation="Think of it as a switch. Nothing else depends on it."))
        ok("b10-tone-report-only-unadmitted", r.exit_code == 0 and [x[0] for x in r.reports if x[0] == "B10"] == ["B10"], str(r.reports))
        ok("b10-just-as-word-not-justify", not [x for x in run(mut(bcard(), situation="It justifies itself. Nothing else depends on it.")).reports if x[0] == "B10"])
        ok("b10-exclamation", [x[0] for x in run(mut(bcard(), situation="Ready! Nothing else depends on it.")).reports if x[0] == "B10"] == ["B10"])
        os.makedirs(os.path.join(tmp, ".claude"), exist_ok=True)
        with open(os.path.join(tmp, ".claude", "hyp.json"), "w") as fh:
            json.dump({BRIEF_ADMITTED_KEY: ["B10", "B9"], "ledger_file": LEDGER_REL}, fh)   # the scratch consumer's ledger rides along
        r = run(mut(bcard(), situation="Think of it as a switch. Nothing else depends on it."))
        ok("b10-admitted-blocks", r.exit_code == 1 and brules(r) == ["B10"], str(brules(r)))
        r = run(mut(bcard(), situation="See scripts/decisions.py for it. Nothing else depends on it."))
        ok("b6-unadmitted-stays-report", r.exit_code == 0 and [x[0] for x in r.reports if x[0] == "B6"] == ["B6"])
        with open(os.path.join(tmp, ".claude", "hyp.json"), "w") as fh:
            fh.write(json.dumps({"ledger_file": LEDGER_REL}) + "\n")   # admission back to none; the ledger key stays
        r = run(mut(bcard(), sources=["pipeline-fact:made-up"])); ok("b11-literal-outside-list", brules(r) == ["B11"] and r.exit_code == 1, str(brules(r)))
        r = run(mut(bcard(), sources=["notes/a.md@%s#L1-L2" % head])); ok("b11-pointer-resolves", r.exit_code == 0, str(r.findings))
        r = run(mut(bcard(), sources=["notes/a.md@%s#L1-L9" % head])); ok("b11-lines-do-not-exist", brules(r) == ["B11"], str(r.findings))
        r = run(mut(bcard(), sources=["notes/a.md@%s#L1-L2" % ("0" * 40)])); ok("b11-sha-unresolved", brules(r) == ["B11"], str(r.findings))
        r = run(mut(bcard(), sources=["notes/a.md#L1-L2"])); ok("b11-pointer-form", brules(r) == ["B11"], str(r.findings))
        r = run(mut(bcard(), sources=["notes/a.md@%s#L1-L2" % head]), timeout=0)
        ok("b11-stall-exit-neutral", "B11" in r.timeouts and "B9" in r.timeouts and not [f for f in r.findings if f[0] in ("B9", "B11")], str(r.timeouts))
        c = mut(bcard(), situation=long26); c["brief"]["card_sha"] = card_sha(c)
        ok("w1-parity-shape-errors-carries-no-prose-rule", not [e for e in shape_errors(c) if e[0] in BRIEF_RULE_EXIT], str(shape_errors(c)))
        ok("shape-errors-prose-switch", [e[0] for e in shape_errors(c, brief_prose=True, vocab=SYN_VOCAB)] == ["B2"])
        b = bcard()["brief"]; b2 = json.loads(json.dumps(b)); b2["lint"] = {"anything": 1}
        ok("brief-sha-ignores-stamp-only", brief_sha(b) == brief_sha(b2) and len(brief_sha(b)) == 64 and brief_sha(dict(b, decide="x")) != brief_sha(b))
        ok("card-sha-variants-contain-canonical", card_sha(card()) in card_sha_variants(card()) and len(card_sha_variants(card())) >= 5)
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            src = fh.read()
        ok("no-id-literals-in-lint-source", re.search(r"DEC-[0-9]|H-[0-9]{3}|H-DRA" + r"FT-", src) is None)
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
