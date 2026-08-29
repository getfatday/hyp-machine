#!/usr/bin/env python3
"""compile-dashboard.py -- portable status dashboard for a hyp consumer repo.

Compiles DASHBOARD.md at the repo root from the repository's OWN streams:

  1. OPEN DECISIONS AND COMMITMENTS -- ledger rows (JSONL) not yet resolved
     against committed state: a row closes when the [closes-when: ...] bracket
     in its `hit` text is satisfied at HEAD; a row without a valid bracket
     stays open until the ledger line itself is removed or rewritten.
     Rows carry one optional `assignee` (canonical email) and render in three
     buckets: YOURS (assignee canonicalizes to the current identity), OTHERS'
     (assigned to someone else -- always visible, display name resolved), and
     SHARED (no assignee = everyone sees it; absence of routing fails toward
     visibility). Each row's creator is DERIVED from git blame on its ledger
     line -- never stored; a line with no landing commit yet renders with no
     creator segment at all, never a placeholder.
  2. RECENT ACTIVITY -- journal fragments grouped by day, newest first.

Who-am-I: the header carries an `acting as` line -- `git config user.email`
canonicalized through two optional committed maps, the git-native `.mailmap`
and a root-level `contributors.json` ({"<canonical email>": {"name": ...,
"aka": [...]}}). Display names live only in that map; an unmapped email
renders bare with a hint. Both maps are optional (graceful-missing).

Design laws (the intake discipline applied to status):
  - Status is a projection, never an authored document. This script owns every
    byte of DASHBOARD.md: regenerate it, never edit it.
  - The output is COMMITTED content, so it carries no transient values. Any
    fact that is true only at render time is omitted rather than labelled --
    committing a placeholder would freeze a state that has already moved on.
    A cold reader's checkout must mean what it says.
  - Names appear only where they are RESOLVED from the committed maps at render
    time -- the `acting as` line and an assignee. That is the maps doing their
    job, not authored prose, and it is regenerated every session so it cannot
    drift. Nothing else here names a person.
  - Graceful-missing everywhere. A missing ledger file, journal directory, or
    git binary renders as a visible note inside the affected section, never a
    crash. The script never raises; hook mode always exits 0.
  - Deterministic. No wall clock: the header stamp derives from the HEAD commit
    date. The inputs are the repo's streams plus the rendering identity (git
    config user.email, .mailmap, contributors.json, ledger blame); unchanged
    inputs compile to byte-identical output; the write is atomic and happens
    only when bytes differ.

CLI:
    compile-dashboard.py [repo-root]          render; print one summary line
    compile-dashboard.py [repo-root] --quiet  render silently (hook mode)
    compile-dashboard.py [repo-root] --check  no write; exit 0 fresh / 1 stale

Configuration: .claude/hyp.json at the repo root (the init skill writes
it). Keys read here: raw_dir (default research/raw), journal_dir (default
experiments/journal-fragments), and ledger_file (default ledger/ledger.jsonl;
optional -- a repo without a ledger simply shows an empty decisions section).

closes-when predicates (evaluated read-only against HEAD, never the worktree):
    path-exists=<path>        the path is tracked at HEAD
    commit-grep=<text>        some commit message contains the text
    hypothesis-kept=<id>      exactly one hypotheses/<id>-*.md tracked at HEAD
                              whose "## Status" block starts with "kept"
    maintainer-ruling=<slug>  a tracked file under the configured raw dir whose
                              name contains both <slug> and "ruling"
    decision-resolved=<id>    an accepted|denied kind:"decision-resolution" row
                              for <id> exists in the configured ledger AT HEAD

v3 decision store (ported from the source lab's compile-dashboard v3, decision kit
2026-08-28; contract: docs/decisions.md). EXACTLY these additions, everything above
unchanged:
  - parse_ledger normalizes THREE row shapes: the legacy {date,slug,hit[,kind,assignee]}
    rows unchanged; v2 {kind,id,date,text[,closes_when][,assignee]} rows (slug := id,
    hit := text + " [closes-when: ...]"); and the decision pair (kind:"decision" /
    kind:"decision-resolution", joined on id at compile time). Malformed counts only
    truly-bad lines.
  - "## 1. DECISIONS WAITING" renders FIRST: one AskUserQuestion-grammar card per open
    decision (chip line, ask:, option checkboxes, other:, why-only-you:, evidence:,
    blocks:, answer: — the section-1 text grammar of docs/decisions.md), plus compat
    cards for open legacy maintainer-ruling rows not shadowed by an open decision. The
    pre-existing sections keep their exact bodies, renumbered 2..3.
  - decided_by / decided_at / resolution_commit are NEVER stored: they derive from the
    git commit that introduced the resolution line (one git log + ~log2(N) git show per
    resolution, run ONLY when resolution rows exist).
  - decisions.html: a SECOND written file at the repo root, regenerated whole from the
    decisions-template.html found in the consumer repo's scripts/ or beside this script
    (template missing => emission skipped, noted in section 1; render never fails on it).
  - the optional DECIDERS routing file (JSONL {"match","owner"}) lives beside the
    configured ledger; rows with no route default to owner "you".
  - ages in section 1 derive from the header stamp (HEAD commit date), never the wall
    clock — the determinism law is unchanged.
  - the compiler NEVER opens anything; scripts/proactive-open.sh belongs to
    decisions.py add/surface alone.
  - --check compares DASHBOARD.md bytes only (they cover every ledger change); an
    edit to the decisions-template alone refreshes decisions.html on the next
    render rather than tripping --check.
"""
import json
import os
import re
import subprocess
import sys

GIT_TIMEOUT = 10
CAP_DECISIONS = 25
CAP_ACTIVITY_DAYS = 7
DASHBOARD_NAME = "DASHBOARD.md"
CONFIG_RELPATH = os.path.join(".claude", "hyp.json")

# --- v3 decision store (docs/decisions.md) ---
DECISIONS_HTML_NAME = "decisions.html"
DECISIONS_TEMPLATE_NAME = "decisions-template.html"
RESOLVE_CLI = "python3 scripts/decisions.py resolve"
URGENCY_ORDER = {"high": 0, "normal": 1, "low": 2}
CAP_DECISION_CARDS = 25

DEFAULTS = {
    "raw_dir": "research/raw",
    "journal_dir": "experiments/journal-fragments",
    "ledger_file": "ledger/ledger.jsonl",
}

CLOSES_WHEN_RE = re.compile(
    r"\[closes-when:\s*(path-exists|commit-grep|hypothesis-kept|maintainer-ruling"
    r"|decision-resolved)=([^\]]+)\]")
STATUS_HEADING_RE = re.compile(r"(?m)^##\s*Status\s*$")
NEXT_HEADING_RE = re.compile(r"(?m)^##\s")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def load_config(root):
    """DEFAULTS overlaid with the consumer's config file, if any. Never raises."""
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(root, CONFIG_RELPATH), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in DEFAULTS:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    cfg[key] = value.strip().strip("/")
    except Exception:
        pass
    return cfg


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def git_lines(root, args):
    """stdout lines, or None on ANY failure (no git, not a repo, timeout) --
    callers degrade to the graceful-missing rendering."""
    try:
        proc = subprocess.run(["git", "-C", root] + list(args),
                              capture_output=True, timeout=GIT_TIMEOUT)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").splitlines()


def parse_ledger(text):
    """-> (rows, malformed_count, decisions, resolutions) — the v3 THREE-shape
    normalizer (docs/decisions.md section 5). rows keep file order and hold legacy
    {date,slug,hit[,kind,assignee]} rows PLUS v2 {kind,id,date,text[,closes_when]
    [,assignee]} rows normalized as slug := id, hit := text + " [closes-when: ...]".
    decisions/resolutions carry the decision pair with each raw line kept for the
    git introducer search. Malformed counts only truly-bad lines (unparseable JSON,
    or none of the three shapes). lineno (1-based) feeds the blame-derived creator."""
    rows, malformed, decisions, resolutions = [], 0, [], []
    if text is None:
        return rows, malformed, decisions, resolutions
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
            kind = rec.get("kind", "")
            if kind == "decision":
                decisions.append({"rec": rec, "raw": line, "order": lineno,
                                  "id": rec["id"]})
                continue
            if kind == "decision-resolution":
                resolutions.append({"rec": rec, "raw": line, "order": lineno,
                                    "id": rec["id"],
                                    "disposition": rec["disposition"]})
                continue
            if "slug" in rec and "hit" in rec:            # legacy shape
                date, slug, hit = rec["date"], rec["slug"], rec["hit"]
            elif "id" in rec and "text" in rec:           # v2 shape
                date, slug, hit = rec["date"], rec["id"], rec["text"]
                if rec.get("closes_when"):
                    hit = str(hit) + " [closes-when: " + str(rec["closes_when"]) + "]"
            else:
                raise ValueError("no known shape")
        except Exception:
            malformed += 1
            continue
        assignee = rec.get("assignee")
        rows.append({"date": str(date), "slug": str(slug), "hit": str(hit),
                     "kind": str(kind).strip(),
                     "assignee": (assignee.strip()
                                  if isinstance(assignee, str) else ""),
                     "lineno": lineno})
    return rows, malformed, decisions, resolutions


# --- v3 decision join + attribution (docs/decisions.md sections 2-3, 6) -----------------

def git_text(root, args):
    """Full stdout as one string, or None on any failure (v3 helper; the line-based
    git_lines stays untouched for the pre-existing sections)."""
    try:
        proc = subprocess.run(["git", "-C", root] + list(args),
                              capture_output=True, timeout=GIT_TIMEOUT)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def derive_attribution(root, ledger_rel, resolutions):
    """Attach decided_by/decided_at/resolution_commit to each resolution (or
    staged=True). Append-only store => a line's presence over the commit list is
    monotone => binary search over the ledger-touching commits, ~log2(N) git show
    per resolution. Runs ONLY when resolution rows exist."""
    out = git_text(root, ["log", "--reverse", "--format=%H\x1f%an\x1f%aI",
                          "--", ledger_rel])
    commits = ([tuple(l.split("\x1f")) for l in out.splitlines()
                if l.count("\x1f") == 2] if out is not None else [])
    blob_cache = {}

    def blob(sha):
        if sha not in blob_cache:
            text = git_text(root, ["show", "%s:%s" % (sha, ledger_rel)])
            blob_cache[sha] = text if text is not None else ""
        return blob_cache[sha]

    for res in resolutions:
        res["staged"] = True
        res["decided_by"] = res["decided_at"] = res["resolution_commit"] = None
        if not commits or res["raw"] not in blob(commits[-1][0]):
            continue                                # never committed -> staged
        lo, hi = 0, len(commits) - 1                # first commit containing the line
        while lo < hi:
            mid = (lo + hi) // 2
            if res["raw"] in blob(commits[mid][0]):
                hi = mid
            else:
                lo = mid + 1
        sha, author, when = commits[lo]
        res.update({"staged": False, "decided_by": author, "decided_at": when,
                    "resolution_commit": sha[:7]})


def join_decisions(decisions, resolutions):
    """-> logical rows: the stored decision fields + derived status/resolutions.
    Status: open (no resolution) / commented (latest row is a comment — STAYS OPEN)
    / accepted|denied (latest closing row wins)."""
    by_id = {}
    for res in resolutions:
        by_id.setdefault(res["id"], []).append(res)
    logical = []
    for dec in decisions:
        rec = dict(dec["rec"])
        chain = sorted(by_id.get(dec["id"], []), key=lambda r: r["order"])
        closing = [r for r in chain if r["disposition"] in ("accepted", "denied")]
        rec["status"] = closing[-1]["disposition"] if closing else (
            "commented" if chain else "open")
        rec["resolutions"] = [{
            "disposition": r["disposition"],
            "chosen_options": r["rec"].get("chosen_options", []),
            "comment": r["rec"].get("comment", ""),
            "date": r["rec"].get("date", ""),
            "staged": r.get("staged", True),
            "decided_by": r.get("decided_by"), "decided_at": r.get("decided_at"),
            "resolution_commit": r.get("resolution_commit"),
        } for r in chain]
        logical.append(rec)
    return logical


def parse_deciders(text):
    """Optional DECIDERS routing file beside the ledger (JSONL {"match","owner"})
    -> [(match, owner)]."""
    routes = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            routes.append((rec["match"], rec["owner"]))
        except (ValueError, KeyError, TypeError):
            continue
    return routes


def owner_of(row, routes):
    for match, owner in routes:
        if match == row.get("id") or match == row.get("class"):
            return owner
    return "you"


def decision_age_days(row, stamp):
    """Whole days from the row's original ask to the DERIVED stamp (never the wall
    clock)."""
    try:
        from datetime import datetime
        then = datetime.fromisoformat(str(row.get("requested_at")
                                          or row.get("date"))[:10])
        now = datetime.fromisoformat(str(stamp)[:10])
        return max(0, (now - then).days)
    except (ValueError, TypeError):
        return 0


def sort_open_decisions(rows, routes, stamp):
    """Owner group (yours first), urgency, oldest ask, id."""
    def key(r):
        return (0 if owner_of(r, routes) == "you" else 1,
                URGENCY_ORDER.get(r.get("urgency"), 1),
                -decision_age_days(r, stamp),
                r.get("id", ""))
    return sorted(rows, key=key)


def ellipsize(text, limit):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    sp = cut.rfind(" ")
    if sp > limit * 2 // 3:
        cut = cut[:sp]
    return cut + "…"


def strip_bracket(hit):
    return " ".join(CLOSES_WHEN_RE.sub(" ", hit).split()).strip(" —-")


def sh_quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def decision_answer_commands(row):
    first = (row.get("ask", {}).get("options") or [{}])[0].get("label", "<label>")
    accept = "%s %s --accept %s [--comment \"...\"]" % (RESOLVE_CLI, row["id"],
                                                        sh_quote(first))
    deny = "%s %s --deny [--comment \"...\"]" % (RESOLVE_CLI, row["id"])
    comment = "%s %s --comment \"...\"" % (RESOLVE_CLI, row["id"])
    return accept, deny, comment


def find_decisions_template(root):
    """Template text or None: the consumer repo's scripts/ copy wins, then the copy
    beside this script (plugin home)."""
    for path in (os.path.join(root, "scripts", DECISIONS_TEMPLATE_NAME),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              DECISIONS_TEMPLATE_NAME)):
        text = read_text(path)
        if text is not None:
            return text
    return None


def render_decision_section(stamp, head_short, ledger_rel, ledger_missing,
                            template_present, routes, open_cards, compat):
    """Section 1 — DECISIONS WAITING: one AskUserQuestion-grammar card per open
    decision (the section-1 text grammar of docs/decisions.md), then compat cards for
    open legacy maintainer-ruling rows an open decision does not shadow. Ages derive
    from the header stamp (determinism), never the wall clock."""
    yours = [r for r in open_cards if owner_of(r, routes) == "you"]
    others = [r for r in open_cards if owner_of(r, routes) != "you"]
    n = len(open_cards) + len(compat)
    lines = []
    lines.append("## 1. DECISIONS WAITING (%d open — yours %d | others %d)"
                 % (n, len(yours) + len(compat), len(others)))
    lines.append("")
    store_line = ("store: %s at %s · resolve: accept / deny close, a comment stays "
                  "open · decided-by, decided-at, and the commit derive from git when "
                  "the resolution row lands" % (ledger_rel, head_short or "worktree"))
    if not template_present:
        store_line += (" · decisions.html: template missing (%s) — emission skipped"
                       % DECISIONS_TEMPLATE_NAME)
    lines.append(store_line)
    lines.append("")
    if ledger_missing:
        lines.append("source missing: %s" % ledger_rel)
        lines.append("")
    if not open_cards and not compat and not ledger_missing:
        lines.append("(none — nothing is waiting on you; new decisions appear the "
                     "moment a decision row lands in the ledger)")
        lines.append("")
    card_blocks = []
    for row in open_cards:
        ask = row.get("ask", {}) if isinstance(row.get("ask"), dict) else {}
        age = decision_age_days(row, stamp)
        agestr = "new today" if age == 0 else "%dd old" % age
        chip = [str(row.get("id", "?")), row.get("urgency", "normal"), agestr,
                "asked-by " + str(row.get("requested_by", "?")),
                "class " + str(row.get("class", "?"))]
        if ask.get("multiSelect"):
            chip.append("pick many")
        block = ["- [%s]" % " | ".join(chip)]
        block.append("  ask: %s" % ask.get("question", row.get("title", "")))
        for opt in ask.get("options", []):
            block.append("  [ ] %s — %s" % (opt.get("label", "?"),
                                            opt.get("description", "")))
        for res in row.get("resolutions", []):
            if res["disposition"] == "commented":
                who = res["decided_by"] or "staged"
                block.append("  comment (stays open, %s): \"%s\""
                             % (who, res["comment"]))
        block.append("  other: free text is a first-class answer — accept with text "
                     "and no option makes the text the answer; option + text rides "
                     "as --comment")
        block.append("  why-only-you: %s" % row.get("why_only_you", ""))
        if row.get("context_pointers"):
            block.append("  evidence: %s" % " · ".join(row["context_pointers"]))
        if row.get("blocks"):
            block.append("  blocks: %s" % ", ".join(row["blocks"]))
        if row.get("note"):
            block.append("  note: %s" % row["note"])
        accept, deny, comment = decision_answer_commands(row)
        block.append("  answer: %s" % accept)
        block.append("          deny: %s · comment: %s" % (deny, comment))
        block.append("")
        card_blocks.append(block)
    for row, arg in compat:
        age = decision_age_days(row, stamp)
        block = ["- [legacy %s | %s | asked-by ledger row %s | class ruling-compat]"
                 % (arg, "new today" if age == 0 else "%dd old" % age, row["slug"])]
        block.append("  ask: file ruling %s — the legacy bracket [closes-when: "
                     "maintainer-ruling=%s] is still open" % (arg, arg))
        block.append("  %s" % ellipsize(strip_bracket(row["hit"]), 160))
        block.append("  answer: resolve through decisions.py (emits the raw-dir "
                     "ruling capture): %s --legacy %s --accept \"done\" "
                     "[--comment \"...\"]" % (RESOLVE_CLI, arg))
        block.append("")
        card_blocks.append(block)
    shown = card_blocks[:CAP_DECISION_CARDS]
    for block in shown:
        lines.extend(block)
    if len(card_blocks) > CAP_DECISION_CARDS:
        lines.append("(+%d more)" % (len(card_blocks) - CAP_DECISION_CARDS))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def render_decisions_html(template, stamp, head_short, ledger_rel, open_cards):
    """decisions.html: the template re-emitted whole with SNAPSHOT / REPO / DECISIONS
    / stamp injected. REPO injects empty (repo-relative hrefs: the file lives at the
    repo root, next to DASHBOARD.md). Raises on a malformed template; the caller
    catches (the DASHBOARD render never fails on the html emission)."""
    payload = []
    for row in open_cards:
        item = {k: row[k] for k in ("kind", "id", "date", "requested_at",
                                    "requested_by", "title", "ask",
                                    "context_pointers", "blocks", "urgency",
                                    "class", "why_only_you") if k in row}
        note = row.get("note", "")
        comments = [r for r in row.get("resolutions", [])
                    if r["disposition"] == "commented"]
        if comments:
            quoted = " · ".join("queued comment on record: \u201c%s\u201d"
                                % c["comment"] for c in comments)
            note = (note + " " if note else "") + quoted
        if note:
            item["note"] = note
        payload.append(item)
    data = json.dumps(payload, ensure_ascii=False, indent=2).replace("</", "<\\/")
    out = template
    snapshot_date = str(stamp)[:10]
    out = re.sub(r'const SNAPSHOT = "[^"]*";',
                 'const SNAPSHOT = "%s";' % snapshot_date, out)
    out = re.sub(r'const REPO = "[^"]*";', 'const REPO = "";', out)
    start = out.index("const DECISIONS = [")
    end = out.index("];", start) + 2
    out = out[:start] + "const DECISIONS = " + data + ";" + out[end:]
    stamp_html = ('<p class="stamp">snapshot %s · head %s · store %s ·\n'
                  '    regenerated by scripts/compile-dashboard.py</p>'
                  % (snapshot_date, head_short or "worktree", ledger_rel))
    out = re.sub(r'<p class="stamp">.*?</p>', stamp_html, out, count=1,
                 flags=re.DOTALL)
    return out


def head_resolved_ids(root, ledger_rel):
    """Ids whose accepted|denied resolution row exists in the ledger AT HEAD (the
    decision-resolved predicate reads committed state only, like every other
    predicate). Git unavailable -> empty set (rows stay visibly open)."""
    out = git_text(root, ["show", "HEAD:" + ledger_rel])
    ids = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (isinstance(rec, dict) and rec.get("kind") == "decision-resolution"
                and rec.get("disposition") in ("accepted", "denied")):
            ids.add(rec.get("id"))
    return ids


def load_identity_maps(root):
    """(alias_to_canonical, email_to_display) from the optional committed
    .mailmap and contributors.json at the repo root. Graceful-missing; emails
    compare case-insensitively. Display names live ONLY in these maps."""
    alias, names = {}, {}
    text = read_text(os.path.join(root, ".mailmap"))
    if text:
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            emails = re.findall(r"<([^<>]+)>", line)
            if not emails:
                continue
            display = line.split("<", 1)[0].strip()
            canon = emails[0].strip().lower()
            if display and canon not in names:
                names[canon] = display
            for extra in emails[1:]:
                alias[extra.strip().lower()] = canon
    text = read_text(os.path.join(root, "contributors.json"))
    if text:
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            for canon, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                canon = str(canon).strip().lower()
                name = entry.get("name")
                if isinstance(name, str) and name.strip():
                    names[canon] = name.strip()
                aka = entry.get("aka")
                if isinstance(aka, list):
                    for extra in aka:
                        if isinstance(extra, str) and extra.strip():
                            alias[extra.strip().lower()] = canon
    return alias, names


def canonical_email(email, alias):
    """Follow alias hops (cycle-bounded) to the canonical, lowercased form."""
    e = (email or "").strip().lower()
    seen = set()
    while e in alias and e not in seen:
        seen.add(e)
        e = alias[e]
    return e


def display_identity(email, names):
    """'Display Name <email>' when mapped, else the bare email."""
    name = names.get(email)
    return "%s <%s>" % (name, email) if name else email


def resolve_current_identity(root, alias, names):
    """(acting-as line, canonical email or None) for the rendering user."""
    lines = git_lines(root, ["config", "user.email"])
    email = lines[0].strip() if lines and lines[0].strip() else ""
    if not email:
        return ("acting as (unknown -- git config user.email is unset; "
                "identity resolves through git, never prose)", None)
    canon = canonical_email(email, alias)
    if canon in names:
        return "acting as %s <%s>" % (names[canon], canon), canon
    return ("acting as %s (unmapped -- add a contributors.json entry to attach "
            "a display name)" % canon, canon)


def blame_creators(root, ledger_rel):
    """lineno -> creator email for lines that have a landing commit; lines not
    yet committed are OMITTED from the map, and None means blame is unavailable
    (untracked ledger, no git). Creators are always derived from the landing
    commit, never stored in the row.

    Uncommitted lines are omitted rather than labelled because DASHBOARD.md is
    committed content: a placeholder like "(uncommitted)" is true only at the
    instant it renders, and committing that byte would freeze a transient state
    into history. An absent creator is honest at every later read."""
    lines = git_lines(root, ["blame", "--line-porcelain", "--", ledger_rel])
    if lines is None:
        return None
    creators, cur_line, cur_sha = {}, None, None
    for ln in lines:
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", ln)
        if m:
            cur_sha, cur_line = m.group(1), int(m.group(2))
        elif ln.startswith("author-mail ") and cur_line is not None:
            if cur_sha != "0" * 40:
                mail = ln[len("author-mail "):].strip().strip("<>").lower()
                creators[cur_line] = mail
    return creators


def extract_status_word(text):
    m = STATUS_HEADING_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    block = rest[: nxt.start()] if nxt else rest
    block = HTML_COMMENT_RE.sub(" ", block)
    stripped = block.strip()
    if not stripped:
        return None
    return stripped.split()[0]


def predicate_satisfied(root, pred, arg, cfg, tracked, messages, resolved_ids=None):
    """HEAD-only evaluation; git unavailable means unsatisfied (safe default:
    the row stays visibly open rather than silently closing)."""
    if tracked is None or messages is None:
        return False
    if pred == "decision-resolved":
        return arg in (resolved_ids or set())
    if pred == "path-exists":
        return arg in tracked
    if pred == "commit-grep":
        return any(arg in line for line in messages)
    if pred == "maintainer-ruling":
        needle = arg.lower()
        prefix = cfg["raw_dir"].strip("/") + "/"
        for path in tracked:
            if not path.startswith(prefix):
                continue
            name = path.rsplit("/", 1)[-1].lower()
            if needle in name and "ruling" in name:
                return True
        return False
    if pred == "hypothesis-kept":
        pattern = re.compile(r"^hypotheses/%s-.*\.md$" % re.escape(arg))
        matches = [p for p in tracked if pattern.match(p)]
        if len(matches) != 1:
            return False
        lines = git_lines(root, ["show", "HEAD:" + matches[0]])
        if lines is None:
            return False
        word = extract_status_word("\n".join(lines))
        return word is not None and word.lower() == "kept"
    return False


def parse_fragment(name, text):
    """id/date from frontmatter (filename prefix as id fallback); slug from name."""
    stem = name[:-3] if name.endswith(".md") else name
    prefix, _sep, slug = stem.partition("-")
    frag_id = int(prefix) if prefix.isdigit() else None
    date = ""
    if text:
        fm = re.match(r"\s*---\s*\n(.*?)\n---", text, re.DOTALL)
        if fm:
            im = re.search(r"(?m)^id:\s*(\d+)\s*$", fm.group(1))
            dm = re.search(r"(?m)^date:\s*(\S+)", fm.group(1))
            if im:
                frag_id = int(im.group(1))
            if dm:
                date = dm.group(1)
    return frag_id, slug or stem, date


def compile_text(root):
    cfg = load_config(root)
    tracked_list = git_lines(root, ["ls-tree", "-r", "--name-only", "HEAD"])
    tracked = set(tracked_list) if tracked_list is not None else None
    messages = git_lines(root, ["log", "--format=%B"])
    stamp_lines = git_lines(root, ["log", "-1", "--format=%cI"])
    stamp = stamp_lines[0].strip() if stamp_lines else "unknown"
    head_lines = git_lines(root, ["rev-parse", "--short", "HEAD"])
    head_short = head_lines[0].strip() if head_lines else ""

    out = []
    out.append("<!-- GENERATED by the hyp plugin "
               "(scripts/compile-dashboard.py) -- a compiled projection; "
               "edits are overwritten. stamp: %s -->" % stamp)
    out.append("")
    out.append("# DASHBOARD")
    out.append("")
    out.append("Compiled from this repository's own streams -- status is a "
               "projection, never an authored document.")
    out.append("")
    alias, names = load_identity_maps(root)
    who_line, me = resolve_current_identity(root, alias, names)
    out.append(who_line)
    out.append("")

    # ledger read (shared by v3 section 1 and the pre-existing section 2)
    ledger_rel = cfg["ledger_file"]
    ledger_text = read_text(os.path.join(root, ledger_rel))
    rows, malformed, decision_rows, resolution_rows = parse_ledger(ledger_text)

    # v3: git-derived resolution attribution — extra git calls happen ONLY when
    # resolution rows exist; a resolution-free ledger keeps the original budget.
    if resolution_rows:
        derive_attribution(root, ledger_rel, resolution_rows)
    decisions_logical = join_decisions(decision_rows, resolution_rows)
    resolved_ids = (head_resolved_ids(root, ledger_rel)
                    if resolution_rows else set())

    open_rows = []
    for row in rows:
        m = CLOSES_WHEN_RE.search(row["hit"])
        if m and m.group(2).strip():
            if predicate_satisfied(root, m.group(1), m.group(2).strip(), cfg,
                                   tracked, messages, resolved_ids):
                continue
            row["waits"] = "%s=%s" % (m.group(1), m.group(2).strip())
        else:
            row["waits"] = "no closes-when bracket (open until the row is removed)"
        open_rows.append(row)
    open_rows.sort(key=lambda r: r["date"])  # stable: file order within a date

    # v3 section 1 — DECISIONS WAITING (decision-store cards, docs/decisions.md
    # section-1 grammar). Always FIRST; the pre-existing sections follow,
    # renumbered 2..3 with their bodies unchanged.
    deciders_text = read_text(os.path.join(
        root, os.path.dirname(ledger_rel), "DECIDERS"))
    routes = parse_deciders(deciders_text)
    open_cards = sort_open_decisions(
        [r for r in decisions_logical if r["status"] in ("open", "commented")],
        routes, stamp)
    shadowed = set()
    for card in open_cards:
        for sh in card.get("shadows", []):
            shadowed.add(str(sh).split("=", 1)[-1].strip())
    compat = []
    for row in open_rows:
        m = CLOSES_WHEN_RE.search(row["hit"])
        if m and m.group(1) == "maintainer-ruling" and m.group(2).strip() \
                and m.group(2).strip() not in shadowed:
            compat.append((row, m.group(2).strip()))
    template = find_decisions_template(root)
    out.extend(render_decision_section(stamp, head_short, ledger_rel,
                                       ledger_text is None, template is not None,
                                       routes, open_cards, compat))
    out.append("")

    decisions_html = None
    if template is not None:
        try:
            decisions_html = render_decisions_html(template, stamp, head_short,
                                                   ledger_rel, open_cards)
        except (ValueError, KeyError, TypeError):
            decisions_html = None

    # 2. open decisions and commitments (pre-v3 section, body unchanged)
    out.append("## 2. OPEN DECISIONS AND COMMITMENTS (%d)" % len(open_rows))
    out.append("")
    if ledger_text is None:
        out.append("source missing: %s (no ledger -- nothing to resolve)" % ledger_rel)
    if (tracked is None or messages is None) and rows:
        out.append("note: git unavailable -- bracketed rows render as open "
                   "(safe default)")
    if malformed:
        out.append("note: %d malformed ledger line(s) skipped" % malformed)
    creators = blame_creators(root, ledger_rel) if open_rows else None

    def render_row(row, show_assignee):
        kind = (" %s" % row["kind"]) if row["kind"] else ""
        hit = " ".join(CLOSES_WHEN_RE.sub(" ", row["hit"]).split())
        if len(hit) > 160:
            hit = hit[:159] + "..."
        extra = ""
        if show_assignee and row["canon_assignee"]:
            extra = (" -- assignee: %s"
                     % display_identity(row["canon_assignee"], names))
        # No creator resolved (line not yet committed, or blame unavailable) ->
        # omit the segment. Never emit a render-time-only value: this file is
        # committed, and a frozen placeholder would outlive the state it named.
        creator = creators.get(row["lineno"]) if creators else None
        cred = (" -- creator: %s" % creator) if creator else ""
        return ("- [%s]%s %s: %s%s%s -- waits on: %s"
                % (row["date"], kind, row["slug"], hit, extra, cred,
                   row["waits"]))

    yours, others, shared = [], [], []
    for row in open_rows:
        canon = (canonical_email(row["assignee"], alias)
                 if row["assignee"] else "")
        row["canon_assignee"] = canon
        if not canon:
            shared.append(row)     # unassigned = everyone sees it, never hidden
        elif me is not None and canon == me:
            yours.append(row)
        else:
            others.append(row)

    def render_bucket(heading, bucket, show_assignee):
        out.append("")
        out.append(heading % len(bucket))
        for row in bucket[:CAP_DECISIONS]:
            out.append(render_row(row, show_assignee))
        if len(bucket) > CAP_DECISIONS:
            out.append("(+%d more)" % (len(bucket) - CAP_DECISIONS))
        if not bucket:
            out.append("(none)")

    if open_rows:
        render_bucket("### YOURS (%d) -- assigned to the current identity",
                      yours, False)
        render_bucket("### OTHERS' (%d) -- assigned, always visible to everyone",
                      others, True)
        render_bucket("### SHARED (%d) -- unassigned: visible to every identity",
                      shared, False)
    if not open_rows and ledger_text is not None:
        out.append("(none -- every ledger row resolves against committed state)")

    # 3. recent activity (pre-v3 section 2, body unchanged)
    out.append("")
    frag_rel = cfg["journal_dir"]
    frag_dir = os.path.join(root, frag_rel)
    try:
        names = sorted(n for n in os.listdir(frag_dir) if n.endswith(".md"))
    except OSError:
        names = None
    frags = []
    for name in names or []:
        frag_id, slug, date = parse_fragment(
            name, read_text(os.path.join(frag_dir, name)))
        frags.append({"id": frag_id, "slug": slug, "date": date, "name": name})
    days = {}
    for f in frags:
        days.setdefault(f["date"] or "undated", []).append(f)
    day_keys = sorted(days, reverse=True)[:CAP_ACTIVITY_DAYS]
    out.append("## 3. RECENT ACTIVITY (last %d day(s) with journal fragments)"
               % len(day_keys))
    out.append("")
    if names is None:
        out.append("source missing: %s/" % frag_rel)
    for day in day_keys:
        entries = sorted(days[day],
                         key=lambda f: (-(f["id"] if f["id"] is not None else -1),
                                        f["name"]))
        out.append("%s:" % day)
        for f in entries:
            fid = ("fragment %d" % f["id"]) if f["id"] is not None else f["name"]
            out.append("- %s (%s)" % (f["slug"], fid))
    if not day_keys and names is not None:
        out.append("(none yet -- journal fragments will appear here)")

    out.append("")
    return "\n".join(out) + "\n", decisions_html


def main(argv):
    args = list(argv[1:])
    flags = {a for a in args if a.startswith("--")}
    positional = [a for a in args if not a.startswith("--")]
    if positional:
        root = os.path.abspath(positional[0])
    else:
        env_root = os.environ.get("CLAUDE_PROJECT_DIR")
        root = env_root if env_root and os.path.isdir(env_root) else os.getcwd()
    quiet = "--quiet" in flags
    try:
        text, decisions_html = compile_text(root)
        target = os.path.join(root, DASHBOARD_NAME)
        current = read_text(target)
        if "--check" in flags:
            return 0 if current == text else 1
        if current != text:
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, target)
        # v3: decisions.html rides every render (regenerated whole from the
        # template; a missing or malformed template never breaks the render).
        if decisions_html is not None:
            html_target = os.path.join(root, DECISIONS_HTML_NAME)
            if read_text(html_target) != decisions_html:
                tmp = html_target + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(decisions_html)
                os.replace(tmp, html_target)
        if not quiet:
            sys.stdout.write("%s: %d line(s) compiled%s\n"
                             % (DASHBOARD_NAME, text.count("\n"),
                                " (unchanged)" if current == text else ""))
    except Exception:
        # fail open: a status surface must never break a session or a stop
        if not quiet:
            sys.stdout.write("dashboard compile skipped (unexpected error; "
                             "failing open)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
