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


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    print(__doc__.strip().splitlines()[0])
    print("usage: closes_when.py --selftest   (the module is otherwise imported, not run)")
    sys.exit(2)
