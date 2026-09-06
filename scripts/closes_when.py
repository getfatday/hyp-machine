#!/usr/bin/env python3
"""closes_when.py -- shared closes-when predicate parser + evaluator.

Design: experiments/runs/H-100/fixture/prevention-design.md (S2, "the closes-when
convention"). Spec: hypotheses/H-100-commitment-detector.md. This module is shared by
commitment_lint.py (the sweep) and, later, the H-101 ledger resolver: one predicate
evaluator, two consumers, so the sweep and the ledger can never disagree on open/closed
(the split-brain guard, design S4.2).

Grammar (at most one bracket per line, appended to the committing line):

    [closes-when: <predicate>=<argument>]

parsed by:

    \\[closes-when:\\s*(path-exists|commit-grep|hypothesis-kept|hypothesis-verdict|maintainer-ruling|decision-resolved)=([^\\]]+)\\]

Unknown predicate or empty argument = malformed. The design treats "malformed" and
"bracket absent" identically -- both are "no valid closes-when" -- so parse_bracket()
returns None for both.

All six predicates are evaluated read-only, against committed (HEAD) state ONLY, never
the working tree: "committed at HEAD" (path-exists), "some commit message" (commit-grep),
"landed as kept ... at HEAD" (hypothesis-kept), "landed as kept OR discarded at HEAD"
(hypothesis-verdict: the question is answered either way -- a discard closes a condition
whose question a null settles; added with the destination-map ship, lab lane
H-DRAFT-2cae0933-derived-condition-status kept 2x 5/5), "some committed filename"
(maintainer-ruling), "an accepted|denied decision-resolution row in the ledger at HEAD"
(decision-resolved, decisions-schema.md section 4) -- the Durability invariant
("uncommitted work effectively does not exist").

Stdlib + git only. No network. No writes. `python3 closes_when.py --selftest` builds a
throwaway repository and proves the two hypothesis predicates on kept / discarded / draft
specs (exit 0 iff every check passes).
"""
import json
import re
import subprocess
from pathlib import Path

# ---------- bracket grammar ----------

CLOSES_WHEN_RE = re.compile(
    r"\[closes-when:\s*(path-exists|commit-grep|hypothesis-kept|hypothesis-verdict"
    r"|maintainer-ruling|decision-resolved|frontmatter-status)=([^\]]+)\]"
)

# Shared with commitment_lint.py's On-keep-block gating: both need "what word does this
# spec's Status carry" and must agree, or the sweep and the hypothesis-kept predicate could
# split-brain on the same file.
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STATUS_HEADING_RE = re.compile(r"(?m)^##\s*Status\s*$")
NEXT_HEADING_RE = re.compile(r"(?m)^##\s")

GIT_TIMEOUT = 5  # seconds per git call; defensive only -- local repo reads are near-instant.


def parse_bracket(line):
    """Parse the closes-when bracket out of one line of text.

    Returns (predicate, arg) for a well-formed bracket: one of the four known predicate
    names, '=', then a non-empty (after stripping) argument, closed by ']'.

    Returns None for every other case -- the design's single "no valid closes-when" class:
      - no bracket present on the line at all;
      - an unknown predicate name (the regex's alternation only matches the four known
        names, so anything else simply fails to match at that position);
      - a present-but-empty or whitespace-only argument.

    Callers that must honor "visible, not an HTML comment" (design S2: a bracket hidden
    inside an HTML comment must not count, because invisible carriers are exactly what
    leaked in the census) pass a comment-masked line in, rather than the raw line -- this
    function itself is a pure, context-free regex parse of whatever text it is given.
    """
    m = CLOSES_WHEN_RE.search(line)
    if not m:
        return None
    predicate, arg = m.group(1), m.group(2).strip()
    if not arg:
        return None
    return predicate, arg


def extract_status_word(text):
    """First non-comment word under a '## Status' heading in hypothesis-spec text.

    None if the heading is absent, or its content -- after stripping HTML comments, which
    is where most specs carry their status rationale (e.g. "kept <!-- 2026-08-14: ... -->")
    -- is empty. Shared by _check_hypothesis_kept below and commitment_lint.py's On-keep-
    block gating, so both agree on what a spec's status "word" is.
    """
    m = STATUS_HEADING_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    block = rest[:nxt.start()] if nxt else rest
    block = HTML_COMMENT_RE.sub(" ", block)
    stripped = block.strip()
    if not stripped:
        return None
    return stripped.split()[0]


# ---------- git plumbing ----------

def _git(repo_root, args):
    """Run git in repo_root. Never raises: returns (returncode, stdout); on any failure to
    even launch git, or on timeout, returns (1, "")."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root)] + list(args),
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


# ---------- the predicates (v1: four; v2 adds decision-resolved; v3 hypothesis-verdict) ----------

def _check_path_exists(arg, repo_root):
    """path-exists=<repo-relative-path> -- committed at HEAD."""
    code, _ = _git(repo_root, ["cat-file", "-e", "HEAD:" + arg])
    return code == 0


_LOG_BODY_CACHE = {}


def _all_commit_bodies(repo_root):
    """One full-history message pass per (process, repo) — the compile-dashboard
    single-pass law, ported here after the per-row form scaled to ~57 s at 188
    open rows and crossed the CLI's SessionStart hook timeout (2026-09-01)."""
    text = _LOG_BODY_CACHE.get(repo_root)
    if text is None:
        code, out = _git(repo_root, ["log", "--format=%B%x00"])
        text = out if code == 0 else ""
        _LOG_BODY_CACHE[repo_root] = text
    return text


def _check_commit_grep(arg, repo_root):
    """commit-grep=<needle> -- some commit message contains the literal needle."""
    return arg in _all_commit_bodies(repo_root)


def _spec_status_word(arg, repo_root):
    """The Status word of the single committed hypotheses/<arg>-*.md at HEAD, lower-cased;
    None when zero or more-than-one spec matches (ambiguity never closes a commitment --
    the same safe-default the ledger resolver uses for a missing operating_model_dir) or
    when the spec has no Status word. Shared by hypothesis-kept and hypothesis-verdict so
    the two predicates can never read the same spec differently."""
    code, out = _git(repo_root, ["ls-tree", "-r", "--name-only", "HEAD", "--", "hypotheses"])
    if code != 0:
        return None
    pattern = re.compile(r"^hypotheses/%s-.*\.md$" % re.escape(arg))
    matches = [p for p in out.splitlines() if pattern.match(p)]
    if len(matches) != 1:
        return None
    code, content = _git(repo_root, ["show", "HEAD:" + matches[0]])
    if code != 0:
        return None
    word = extract_status_word(content)
    return word.lower() if word is not None else None


def _check_hypothesis_kept(arg, repo_root):
    """hypothesis-kept=H-NNN -- exactly one committed hypotheses/H-NNN-*.md at HEAD whose
    first non-comment word under '## Status' is 'kept' (case-insensitive: this repo's real
    specs use both 'kept' and 'KEPT')."""
    return _spec_status_word(arg, repo_root) == "kept"


def _check_hypothesis_verdict(arg, repo_root):
    """hypothesis-verdict=H-NNN -- the same single-spec read, satisfied when the Status word
    is 'kept' OR 'discarded': the hypothesis has a terminal verdict either way. 'refine',
    'draft', 'running' and a refined-into pointer leave it open (a refine hands the question
    to a successor; this predicate does not follow lineage -- north-star-check.py does)."""
    return _spec_status_word(arg, repo_root) in ("kept", "discarded")


def _check_maintainer_ruling(arg, repo_root):
    """maintainer-ruling=<slug> -- some committed filename under research/raw/ contains
    both <slug> and 'ruling', case-insensitive (matches existing practice, e.g.
    raw/2026-08-15-m7-extraction-bar-ruling.md)."""
    code, out = _git(repo_root, ["ls-tree", "-r", "--name-only", "HEAD", "--", "research/raw"])
    if code != 0:
        return False
    needle = arg.lower()
    for path in out.splitlines():
        name = Path(path).name.lower()
        if needle in name and "ruling" in name:
            return True
    return False


def _check_decision_resolved(arg, repo_root):
    """decision-resolved=<DEC-id> -- a kind:"decision-resolution" row for <id> with
    disposition accepted|denied exists in ledger/work-ledger.jsonl AT HEAD (decisions-
    schema.md section 4; committed state only, like every other predicate -- a staged,
    uncommitted resolution does not close anything)."""
    code, out = _git(repo_root, ["show", "HEAD:ledger/work-ledger.jsonl"])
    if code != 0:
        return False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (isinstance(rec, dict) and rec.get("kind") == "decision-resolution"
                and rec.get("id") == arg
                and rec.get("disposition") in ("accepted", "denied")):
            return True
    return False


def _frontmatter_status_word(text):
    """The text after `status:` on the first such line between the opening `---` (first
    line) and the next `---`, whitespace and one pair of matching quotes stripped; None when
    the document has no frontmatter block or no `status:` line inside it."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("status:"):
            value = line[len("status:"):].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


def _check_frontmatter_status(arg, repo_root):
    """frontmatter-status=<repo path>:<done-values>[!<no-values>] -- the typed document
    committed at <repo path> AT HEAD carries a frontmatter `status:` whose value is one of the
    comma-separated done-values (exact compare). The path is everything before the LAST colon
    of the argument, so real paths containing `: ` parse; the no-values only matter to the
    north-star reader (outcome `no`), never to closure. Absent path, no frontmatter, no
    `status:` line, an uncommitted edit, or a malformed argument -> False (document-resolver
    lane). Note the bracket grammar stops at the first `]`, so a path whose segments carry
    `[...]` cannot ride in an On-keep bracket; the north-star closes-when cell has no such
    limit."""
    if ":" not in arg:
        return False
    path, values = arg.rsplit(":", 1)
    path = path.strip()
    done = [v.strip() for v in values.partition("!")[0].split(",") if v.strip()]
    if not path or not done:
        return False
    # Presence is decided by cat-file -e, never by `show`'s exit code: `git show HEAD:<path>`
    # returns 0 with empty output for some deleted paths (document-resolver lane, Amendment 1).
    present, _ = _git(repo_root, ["cat-file", "-e", "HEAD:" + path])
    if present != 0:
        return False
    code, text = _git(repo_root, ["show", "HEAD:" + path])
    if code != 0:
        return False
    word = _frontmatter_status_word(text)
    return word is not None and word in done


_CHECKERS = {
    "path-exists": _check_path_exists,
    "commit-grep": _check_commit_grep,
    "hypothesis-kept": _check_hypothesis_kept,
    "hypothesis-verdict": _check_hypothesis_verdict,
    "maintainer-ruling": _check_maintainer_ruling,
    "decision-resolved": _check_decision_resolved,
    "frontmatter-status": _check_frontmatter_status,
}


def check(predicate, arg, repo_root):
    """True iff <predicate>=<arg> is satisfied in the repo at repo_root, evaluated against
    committed (HEAD) state. Unknown predicate -> False (defensive; commitment_lint.py only
    ever calls check() with a predicate parse_bracket already validated as one of the
    five known names)."""
    fn = _CHECKERS.get(predicate)
    return bool(fn and fn(arg, repo_root))


# ---------- selftest ----------

def _selftest():
    """Throwaway-repo proof of the two hypothesis predicates. Exit 0 iff every check passes."""
    import os
    import shutil
    import tempfile

    def spec(hid, word):
        return "# %s\n\n## Status\n%s <!-- rationale -->\n\n## Method\nbody\n" % (hid, word)

    tmp = tempfile.mkdtemp(prefix="closes-when-selftest-")
    checks = []
    try:
        subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
        os.makedirs(os.path.join(tmp, "hypotheses"))
        files = {
            "H-901-kept.md": spec("H-901", "kept"),
            "H-902-discarded.md": spec("H-902", "DISCARDED"),
            "H-903-draft.md": spec("H-903", "draft"),
            "H-904-refined.md": spec("H-904", "refined-into: H-905"),
            "H-906-a.md": spec("H-906", "kept"),
            "H-906-b.md": spec("H-906", "kept"),
        }
        for name, text in files.items():
            with open(os.path.join(tmp, "hypotheses", name), "w", encoding="utf-8") as f:
                f.write(text)

        # typed documents for frontmatter-status (a `: ` inside a directory name on purpose)
        def doc(status_line):
            body = "---\ntype: milestone\ntitle: \"t\"\n"
            if status_line is not None:
                body += status_line + "\n"
            return body + "date: 2026-09-05\n---\n\n# t\n"
        docs = {
            "docs/Area 1: alpha/done.md": doc("status: \"completed\""),
            "docs/Area 1: alpha/no.md": doc("status: cancelled"),
            "docs/Area 1: alpha/other.md": doc("status: in-progress"),
            "docs/Area 1: alpha/nostatus.md": doc(None),
        }
        for rel, text in docs.items():
            os.makedirs(os.path.join(tmp, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
                f.write(text)
        env = dict(os.environ, GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="s@t",
                   GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="s@t")
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "-c", "commit.gpgsign=false", "commit", "-q",
                        "-m", "seed"], check=True, capture_output=True, env=env)
        # An uncommitted kept spec must not count (committed state only).
        with open(os.path.join(tmp, "hypotheses", "H-907-staged.md"), "w",
                  encoding="utf-8") as f:
            f.write(spec("H-907", "kept"))
        # An uncommitted status flip must not count either (committed state only).
        with open(os.path.join(tmp, "docs/Area 1: alpha/other.md"), "w", encoding="utf-8") as f:
            f.write(doc("status: completed"))
        fs_values = ":completed!cancelled,rejected"
        table = [
            ("frontmatter-status done value satisfies",
             check("frontmatter-status", "docs/Area 1: alpha/done.md" + fs_values, tmp), True),
            ("frontmatter-status no value fails",
             check("frontmatter-status", "docs/Area 1: alpha/no.md" + fs_values, tmp), False),
            ("frontmatter-status other value fails (uncommitted flip ignored)",
             check("frontmatter-status", "docs/Area 1: alpha/other.md" + fs_values, tmp), False),
            ("frontmatter-status missing status line fails",
             check("frontmatter-status", "docs/Area 1: alpha/nostatus.md" + fs_values, tmp), False),
            ("frontmatter-status absent path fails",
             check("frontmatter-status", "docs/Area 1: alpha/absent.md" + fs_values, tmp), False),
            ("frontmatter-status malformed argument (no values) fails",
             check("frontmatter-status", "docs/Area 1: alpha/done.md", tmp), False),
            ("bracket parses frontmatter-status",
             parse_bracket("x [closes-when: frontmatter-status=docs/a.md:completed!cancelled]"),
             ("frontmatter-status", "docs/a.md:completed!cancelled")),
            ("kept spec satisfies hypothesis-kept", check("hypothesis-kept", "H-901", tmp), True),
            ("kept spec satisfies hypothesis-verdict", check("hypothesis-verdict", "H-901", tmp), True),
            ("discarded spec fails hypothesis-kept", check("hypothesis-kept", "H-902", tmp), False),
            ("discarded spec satisfies hypothesis-verdict", check("hypothesis-verdict", "H-902", tmp), True),
            ("draft spec fails hypothesis-kept", check("hypothesis-kept", "H-903", tmp), False),
            ("draft spec fails hypothesis-verdict", check("hypothesis-verdict", "H-903", tmp), False),
            ("refined-into spec fails hypothesis-verdict", check("hypothesis-verdict", "H-904", tmp), False),
            ("ambiguous id (two specs) fails hypothesis-verdict", check("hypothesis-verdict", "H-906", tmp), False),
            ("absent id fails hypothesis-verdict", check("hypothesis-verdict", "H-999", tmp), False),
            ("uncommitted kept spec fails hypothesis-verdict", check("hypothesis-verdict", "H-907", tmp), False),
            ("bracket parses hypothesis-verdict",
             parse_bracket("x [closes-when: hypothesis-verdict=H-902]"), ("hypothesis-verdict", "H-902")),
            ("unknown predicate still parses as None",
             parse_bracket("x [closes-when: hypothesis-settled=H-902]"), None),
        ]
        for name, got, want in table:
            checks.append((name, got == want, "got %r want %r" % (got, want)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  [%s]" % detail))
    print("selftest: %d checks, %d failed" % (len(checks), len(failed)))
    return 0 if not failed else 1


# ---------- retest-when family (lane retest-when-predicates; additive, closes-when untouched) ----------
#
# A sibling bracket grammar for EMPIRICAL RULES: "retest me when this evidence exists". It never
# closes anything -- parse_bracket, CLOSES_WHEN_RE, check() and every closes-when checker above
# are byte-unchanged -- and it is evaluated with the same read-only committed-HEAD discipline:
# an appended-but-uncommitted stream row or packet is invisible until it is committed.
#
#     [retest-when: <predicate>=<argument>]
#
#   event-count=event/<node>[:<subject-prefix>]>=N
#       rows of HEAD:ledger/events.jsonl whose `instance-of` equals event/<node> and whose
#       `subject` starts with <subject-prefix> (when given) number at least N, counted DISTINCT
#       by `caused-by` (replaying one install's rows cannot move the count). N is a positive
#       integer.
#   metric-crosses=metric/<id><op><T>@last=K          <op> in  <  <=  >  >=
#       the last K committed derivation rows whose `metric` equals metric/<id> in
#       HEAD:ledger/metrics-timeseries.jsonl ALL satisfy `value <op> T` (the Prometheus `for`
#       hold expressed in rows). K counts derivation rows, never days: `@last=3d` or any other
#       unit suffix is malformed. Fewer than K committed rows -> False.
#   evidence-received=<target>                        <target> = H-NNN | DEC-NNN | <rule-id>
#       a committed file under research/raw/ at HEAD whose basename matches
#       *-evidence-packet-<target>-*.json (the evidence-packet roundtrip's landing shape).
#
# Malformed = "no valid retest-when", exactly as parse_bracket treats a malformed closes-when:
# unknown predicate, empty argument, or an argument that fails its predicate's grammar all
# parse to None (callers report the field once and never file on it).
#
# STABLE API (consumers: scripts/retest-trigger.py, scripts/rule-lint.py, the decision-retest-when
# and evidence-packet-roundtrip lanes):
#   RETEST_WHEN_RE                                  the bracket regex; group 1 predicate, group 2 argument
#   RETEST_WHEN_PREDICATES                          ("event-count", "metric-crosses", "evidence-received")
#   parse_retest_when(line)            -> (predicate, arg) | None      bracket form, one per line
#   parse_retest_when_field(value)     -> (predicate, arg) | None      registry-field form: the bare
#                                         "<predicate>=<argument>" (like the ledger's closes_when
#                                         field) or the bracketed form; same return contract
#   retest_when_evidence(predicate, arg, repo_root) -> (holds, pointers)
#                                         holds: bool; pointers: ["<path>@<sha40>#L<a>-L<b>", ...]
#                                         maximal contiguous spans into the committed stream at
#                                         HEAD whose every line satisfies the predicate's row
#                                         filter (event node + subject prefix; metric id; the
#                                         whole packet file). Empty when holds is False.
#   check_retest_when(predicate, arg, repo_root)    -> bool   (holds only)

RETEST_WHEN_PREDICATES = ("event-count", "metric-crosses", "evidence-received")

RETEST_WHEN_RE = re.compile(
    r"\[retest-when:\s*(event-count|metric-crosses|evidence-received)=([^\]]+)\]"
)

_RW_EVENT_COUNT_ARG_RE = re.compile(
    r"^event/([a-z0-9][a-z0-9-]*)(?::([A-Za-z0-9][A-Za-z0-9._/-]*))?>=([1-9][0-9]*)$"
)
_RW_METRIC_ARG_RE = re.compile(
    r"^(metric/[a-z0-9][a-z0-9._-]*)(<=|>=|<|>)(-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+))@last=([1-9][0-9]*)$"
)
_RW_TARGET_ARG_RE = re.compile(r"^(?:H-[0-9]+|DEC-[0-9]+|[A-Za-z0-9][A-Za-z0-9._-]*)$")
_RW_PACKET_GLOB = "*-evidence-packet-%s-*.json"
_RW_EVENTS_PATH = "ledger/events.jsonl"
_RW_METRICS_PATH = "ledger/metrics-timeseries.jsonl"
_RW_RAW_DIR = "research/raw"


def _retest_when_arg_ok(predicate, arg):
    if predicate == "event-count":
        return _RW_EVENT_COUNT_ARG_RE.match(arg) is not None
    if predicate == "metric-crosses":
        return _RW_METRIC_ARG_RE.match(arg) is not None
    if predicate == "evidence-received":
        return _RW_TARGET_ARG_RE.match(arg) is not None
    return False


def parse_retest_when(line):
    """(predicate, arg) for a well-formed retest-when bracket on the line; None otherwise
    (absent, unknown predicate, empty argument, or an argument its predicate's grammar
    rejects -- e.g. `@last=3d`). Pure regex parse of the text given, like parse_bracket."""
    m = RETEST_WHEN_RE.search(line)
    if not m:
        return None
    predicate, arg = m.group(1), m.group(2).strip()
    if not arg or not _retest_when_arg_ok(predicate, arg):
        return None
    return predicate, arg


def parse_retest_when_field(value):
    """The registry-field form: a `retest_when` value written bare as `<predicate>=<argument>`
    (the shape the work ledger already uses for its `closes_when` field) or already
    bracketed. Non-string, empty, or malformed -> None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if not text.startswith("["):
        text = "[retest-when: " + text + "]"
    return parse_retest_when(text)


def _rw_head_sha(repo_root):
    code, out = _git(repo_root, ["rev-parse", "HEAD"])
    sha = out.strip()
    return sha if code == 0 and re.match(r"^[0-9a-f]{40}$", sha) else None


_RW_BLOB_CACHE = {}  # (repo_root, head sha, path) -> lines | None; keyed by the sha, so a moved HEAD never reads stale


def _rw_head_lines(repo_root, path, sha=None):
    """Lines of <path> at HEAD, or None when the path is not committed (cat-file -e decides
    presence, never `show`'s exit code -- the document-resolver lesson). Cached per
    (repo, HEAD sha, path): one trigger invocation evaluates many rules over the same two
    streams, and every cache key names the exact commit it was read at."""
    key = (str(repo_root), sha, path)
    if sha is not None and key in _RW_BLOB_CACHE:
        return _RW_BLOB_CACHE[key]
    present, _ = _git(repo_root, ["cat-file", "-e", "HEAD:" + path])
    if present != 0:
        return None  # absence (or a failed read) is never cached: the next call asks git again
    code, out = _git(repo_root, ["show", "HEAD:" + path])
    if code != 0:
        return None
    lines = out.splitlines()
    if sha is not None:
        _RW_BLOB_CACHE[key] = lines
    return lines


def _rw_spans(line_numbers):
    """Sorted 1-based line numbers -> maximal contiguous (a, b) runs."""
    spans = []
    for n in sorted(set(line_numbers)):
        if spans and spans[-1][1] == n - 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return [(a, b) for a, b in spans]


def _rw_pointers(path, sha, line_numbers):
    return ["%s@%s#L%d-L%d" % (path, sha, a, b) for a, b in _rw_spans(line_numbers)]


def _rw_json_rows(lines):
    """(lineno, dict) for every parseable object line; other lines are skipped."""
    for i, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield i, rec


def _rw_event_count(arg, repo_root):
    m = _RW_EVENT_COUNT_ARG_RE.match(arg)
    if not m:
        return False, []
    node, prefix, n = "event/" + m.group(1), m.group(2), int(m.group(3))
    sha = _rw_head_sha(repo_root)
    lines = _rw_head_lines(repo_root, _RW_EVENTS_PATH, sha) if sha else None
    if sha is None or lines is None:
        return False, []
    matched, causes = [], set()
    for lineno, rec in _rw_json_rows(lines):
        if rec.get("instance-of") != node:
            continue
        subject = rec.get("subject")
        if prefix is not None and not (isinstance(subject, str) and subject.startswith(prefix)):
            continue
        matched.append(lineno)
        cause = rec.get("caused-by")
        if isinstance(cause, str) and cause.strip():
            causes.add(cause.strip())
    if len(causes) < n:
        return False, []
    return True, _rw_pointers(_RW_EVENTS_PATH, sha, matched)


_RW_OPS = {
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
}


def _rw_metric_crosses(arg, repo_root):
    m = _RW_METRIC_ARG_RE.match(arg)
    if not m:
        return False, []
    metric, op, threshold, k = m.group(1), m.group(2), float(m.group(3)), int(m.group(4))
    sha = _rw_head_sha(repo_root)
    lines = _rw_head_lines(repo_root, _RW_METRICS_PATH, sha) if sha else None
    if sha is None or lines is None:
        return False, []
    rows = []
    for lineno, rec in _rw_json_rows(lines):
        if rec.get("metric") != metric:
            continue
        value = rec.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        rows.append((lineno, float(value)))
    if len(rows) < k:
        return False, []
    last = rows[-k:]
    test = _RW_OPS[op]
    if not all(test(v, threshold) for _ln, v in last):
        return False, []
    return True, _rw_pointers(_RW_METRICS_PATH, sha, [ln for ln, _v in last])


def _rw_evidence_received(arg, repo_root):
    import fnmatch
    if not _RW_TARGET_ARG_RE.match(arg):
        return False, []
    sha = _rw_head_sha(repo_root)
    if sha is None:
        return False, []
    code, out = _git(repo_root, ["ls-tree", "-r", "--name-only", "HEAD", "--", _RW_RAW_DIR])
    if code != 0:
        return False, []
    pattern = _RW_PACKET_GLOB % arg
    pointers = []
    for path in sorted(out.splitlines()):
        if not fnmatch.fnmatchcase(Path(path).name, pattern):
            continue
        lines = _rw_head_lines(repo_root, path, sha)
        if lines is None:
            continue
        pointers.append("%s@%s#L1-L%d" % (path, sha, max(1, len(lines))))
    return (len(pointers) > 0), pointers


_RETEST_WHEN_CHECKERS = {
    "event-count": _rw_event_count,
    "metric-crosses": _rw_metric_crosses,
    "evidence-received": _rw_evidence_received,
}


def retest_when_evidence(predicate, arg, repo_root):
    """(holds, pointers) for <predicate>=<arg> against committed HEAD of repo_root. Unknown
    predicate or malformed argument -> (False, []). Read-only; never touches the working tree."""
    fn = _RETEST_WHEN_CHECKERS.get(predicate)
    if fn is None or not _retest_when_arg_ok(predicate, arg):
        return False, []
    holds, pointers = fn(arg, repo_root)
    return bool(holds), list(pointers)


def check_retest_when(predicate, arg, repo_root):
    """True iff the retest-when predicate holds at HEAD (retest_when_evidence's first value)."""
    return retest_when_evidence(predicate, arg, repo_root)[0]


def _retest_when_selftest():
    """Throwaway-repo proof of the retest-when family: grammar closure, distinct-by-caused-by,
    the @last=K hold on a cross-then-revert series, HEAD-only (an uncommitted packet is
    invisible), and parse_bracket neutrality. Returns [(name, ok, detail)]; the module's
    --selftest prints and tallies it alongside the closes-when checks."""
    import os
    import shutil
    import tempfile

    def row(node, cause, subject):
        return json.dumps({"schema": "v1", "instance-of": node, "caused-by": cause,
                           "date": "2026-09-05", "subject": subject, "payload": {}},
                          sort_keys=True, separators=(",", ":"))

    def mrow(metric, value):
        return json.dumps({"schema": "metric-point/v1", "metric": metric, "value": value},
                          sort_keys=True, separators=(",", ":"))

    tmp = tempfile.mkdtemp(prefix="retest-when-selftest-")
    checks = []
    try:
        subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
        os.makedirs(os.path.join(tmp, "ledger"))
        os.makedirs(os.path.join(tmp, "research", "raw"))
        events = [row("event/verdict-flipped", "c%07d" % i, "lane/H-%d" % i) for i in range(5)]
        events += [row("event/verdict-flipped", "c%07d" % i, "lane/replay-%d" % i) for i in range(5)]
        events += [row("event/advisory-surfaced", "d%07d" % i, "lane/beta-%d" % i) for i in range(3)]
        events += [row("event/advisory-surfaced", "e%07d" % i, "lane/alpha-%d" % i) for i in range(2)]
        with open(os.path.join(tmp, "ledger", "events.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(events) + "\n")
        series = [mrow("metric/x", v) for v in (0.20, 0.20, 0.20, 0.09, 0.08, 0.12)]
        series += [mrow("metric/y", v) for v in (5, 5, 6, 7)]
        with open(os.path.join(tmp, "ledger", "metrics-timeseries.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(series) + "\n")
        with open(os.path.join(tmp, "research", "raw", "repo-evidence-packet-H-900-abc1234.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"target": "H-900"}\n')
        env = dict(os.environ, GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="s@t",
                   GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="s@t")
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "-c", "commit.gpgsign=false", "commit", "-q",
                        "-m", "seed"], check=True, capture_output=True, env=env)
        # uncommitted packet: invisible until committed
        with open(os.path.join(tmp, "research", "raw", "repo-evidence-packet-H-901-abc1234.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"target": "H-901"}\n')
        ev = retest_when_evidence
        held, ptrs = ev("event-count", "event/verdict-flipped>=5", tmp)
        table = [
            ("bracket parses event-count",
             parse_retest_when("x [retest-when: event-count=event/verdict-flipped>=5]"),
             ("event-count", "event/verdict-flipped>=5")),
            ("bracket parses metric-crosses",
             parse_retest_when("[retest-when: metric-crosses=metric/ask-rate<0.20@last=3]"),
             ("metric-crosses", "metric/ask-rate<0.20@last=3")),
            ("bracket parses evidence-received",
             parse_retest_when("[retest-when: evidence-received=DEC-016]"),
             ("evidence-received", "DEC-016")),
            ("field form parses bare value",
             parse_retest_when_field("event-count=event/verdict-flipped:lane/>=2"),
             ("event-count", "event/verdict-flipped:lane/>=2")),
            ("@last with a unit suffix is malformed",
             parse_retest_when("[retest-when: metric-crosses=metric/ask-rate<0.20@last=3d]"), None),
            ("unknown predicate is malformed",
             parse_retest_when("[retest-when: file-count=ledger/x>=3]"), None),
            ("empty argument is malformed", parse_retest_when("[retest-when: evidence-received= ]"), None),
            ("event-count without >=N is malformed",
             parse_retest_when("[retest-when: event-count=event/verdict-flipped]"), None),
            ("parse_bracket returns None for a retest-when bracket",
             parse_bracket("[retest-when: event-count=event/verdict-flipped>=5]"), None),
            ("closes-when bracket is not a retest-when",
             parse_retest_when("[closes-when: path-exists=a.md]"), None),
            ("event-count holds at 5 distinct caused-by (10 rows)", held, True),
            ("event-count pointers cover the matching rows",
             ptrs, ["ledger/events.jsonl@%s#L1-L10" % _rw_head_sha(tmp)]),
            ("event-count 6 fails: replays do not move the distinct count",
             check_retest_when("event-count", "event/verdict-flipped>=6", tmp), False),
            ("event-count subject prefix counts only the prefix",
             check_retest_when("event-count", "event/advisory-surfaced:lane/beta>=3", tmp), True),
            ("event-count subject prefix above count fails",
             check_retest_when("event-count", "event/advisory-surfaced:lane/beta>=4", tmp), False),
            ("metric hold @last=3 fails on cross-then-revert",
             check_retest_when("metric-crosses", "metric/x<0.10@last=3", tmp), False),
            ("metric hold @last=1 sees the revert (0.12 not < 0.10)",
             check_retest_when("metric-crosses", "metric/x<0.10@last=1", tmp), False),
            ("metric hold >=5 @last=4 holds", check_retest_when("metric-crosses", "metric/y>=5@last=4", tmp), True),
            ("metric hold fewer rows than K fails",
             check_retest_when("metric-crosses", "metric/y>=5@last=5", tmp), False),
            ("metric pointers are the last K rows",
             ev("metric-crosses", "metric/y>5@last=2", tmp)[1],
             ["ledger/metrics-timeseries.jsonl@%s#L9-L10" % _rw_head_sha(tmp)]),
            ("committed packet satisfies evidence-received",
             check_retest_when("evidence-received", "H-900", tmp), True),
            ("uncommitted packet does not (HEAD-only)",
             check_retest_when("evidence-received", "H-901", tmp), False),
            ("absent packet fails", check_retest_when("evidence-received", "DEC-777", tmp), False),
            ("unknown predicate never holds", check_retest_when("row-count", "x", tmp), False),
        ]
        for name, got, want in table:
            checks.append((name, got == want, "got %r want %r" % (got, want)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return checks


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv[1:]:
        rc = _selftest()
        fam = _retest_when_selftest()
        for name, ok, detail in fam:
            print("%s retest-when: %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  [%s]" % detail))
        fam_failed = sum(1 for c in fam if not c[1])
        print("retest-when selftest: %d checks, %d failed" % (len(fam), fam_failed))
        sys.exit(rc or (1 if fam_failed else 0))
    print(__doc__.strip().splitlines()[0])
    print("usage: closes_when.py --selftest   (the module is otherwise imported, not run)")
    sys.exit(2)
