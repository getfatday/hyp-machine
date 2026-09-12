#!/usr/bin/env python3
"""decision_brief_render.py -- one render module behind every decision surface (decision-brief-gate lane).

Pure functions of committed bytes and the header stamp: no clock beyond the stamp a caller passes, no environment,
no network, no process. Imported by `decisions.py show`, both compilers' section 1 and 1b builders, the html card
payload and the session-start resolver, so the presentation of a card cannot drift per surface.

State table (the six states; a `legacy-resolved` row is the disclosed reading that keeps resolved rows byte-identical):
  valid            a brief with lint exit 0 whose card_sha matches the card and whose lint.brief_sha matches its bytes
                   -> brief-first card: the seven labels, one answer: line per choice, the evidence: machine line,
                      details: python3 scripts/decisions.py show <id> --raw
  findings         the same with lint exit 1 -> the same lines plus one BRIEF-FINDINGS <rules> marker line
  legacy-missing   id at or below the boundary, no brief, status open or commented -> today's grammar byte for byte
                   plus exactly one marker line (marker_lines)
  legacy-resolved  id at or below the boundary, resolved (a record or a closed card), no brief -> today's form, no marker
  missing          id above the boundary, no valid brief (a side-door row) -> the NOT READY block (not_ready_lines);
                   no ask:, no [ ] option line, no answer: line -- the AskUserQuestion grammar is not emitted
  stale            any id, a brief present whose card_sha or brief_sha mismatch (or an unstamped brief) -> NOT READY
                   with finding: BRIEF-STALE; a stale brief is never printed as if current
The latest valid brief per id wins (the row's own brief and every kind:"decision-brief" sidecar row, in ledger order,
read from the latest back). The boundary is `.claude/hyp.json` decision_brief_legacy_max_id (absent: the shape-and-
order default -- every decision row appended before the first row carrying a brief.lint stamp is legacy). This
module carries no card id or spec id literal; the sha helpers come from decision_card_lint.py beside it.
"""
import datetime
import json
import os
import re
import sys

LEGACY_KEY = "decision_brief_legacy_max_id"
BRIEF_KIND = "decision-brief"
BRIEF_TEST_KIND = "decision-brief-test"
STATES = ("valid", "findings", "legacy-missing", "legacy-resolved", "missing", "stale")
QUESTION_STATES = ("valid", "findings")
NOT_READY_STATES = ("missing", "stale")
RESOLVE_CLI = "python3 scripts/decisions.py resolve"
SHOW_CLI = "python3 scripts/decisions.py show"
BRIEF_CLI = "python3 scripts/decisions.py brief"
INDENT = "  "
MARKER_MISSING = ("brief: BRIEF-MISSING \u2014 no brief on file; a default never executes against this card while it is "
                  "not readable; retrofit: %s %%s --brief brief.json" % BRIEF_CLI)
MARKER_FINDINGS = ("brief: BRIEF-FINDINGS %%s \u2014 %%d prose finding(s) on the brief; the card renders brief-first; "
                   "fix: %s %%s --brief brief.json" % BRIEF_CLI)
NOT_READY_FINDING = {"missing": "BRIEF-MISSING", "stale": "BRIEF-STALE"}
CARD_LABELS = ("DECIDE", "THE SITUATION", "WHY YOU", "YOUR CHOICES", "IF YOU DO NOTHING", "WHAT WE KNOW", "UNDO")
RECORD_LABELS = ("DECIDED", "THE SITUATION", "WHY THE LAB DID NOT ASK YOU", "WHAT CHANGES", "UNDO")
BRIEF_KEY_ORDER = ("decide", "situation", "yours_because", "choices", "if_nothing", "evidence_line",
                   "terms", "sources", "card_sha", "provenance", "lint")
PAYLOAD_KEYS = ("kind", "id", "date", "requested_at", "requested_by", "title", "ask", "context_pointers", "blocks",
                "urgency", "class", "why_only_you")
NOT_READY_KEYS = ("kind", "id", "date", "requested_at", "requested_by", "title", "urgency", "class")
ARMED_DAYS = 14
# the frozen legacy-armed regex (Method (e)): applied to the silence policy's text with every whitespace run collapsed
ARMED_LINE_RE = re.compile(r"- Armed for (DEC-\d{3}) \([^)]*\): [^*]*?parking backstop \*\*(\d{4}-\d{2}-\d{2})\*\*")
ID_RE = re.compile(r"^DEC-(\d{3,})$")
_LINT = None


def _lint():
    """decision_card_lint.py beside this file: the card_sha variants and brief_sha the state table verifies."""
    global _LINT
    if _LINT is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import decision_card_lint  # noqa
        _LINT = decision_card_lint
    return _LINT


# ---------- boundary and joins ----------

def load_config(root):
    """<root>/.claude/hyp.json as a dict; {} on any failure (never raises)."""
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def explicit_boundary(config):
    val = config.get(LEGACY_KEY) if isinstance(config, dict) else None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return None


def numeric_id(rec_id):
    m = ID_RE.match(str(rec_id or ""))
    return int(m.group(1)) if m else None


def has_stamp(rec):
    brief = rec.get("brief") if isinstance(rec, dict) else None
    return isinstance(brief, dict) and isinstance(brief.get("lint"), dict)


def legacy_ids(decisions, config):
    """The ids at or below the brief boundary. decisions: the kind:"decision" recs in ledger order. Explicit boundary
    N (decision_brief_legacy_max_id): every numeric id at or below N. Absent: shape and order -- every row appended
    before the first row carrying a brief.lint stamp; a stamp-less row after it is gated."""
    n = explicit_boundary(config)
    out = set()
    if n is not None:
        for rec in decisions:
            k = numeric_id(rec.get("id"))
            if k is not None and k <= n:
                out.add(rec.get("id"))
        return out
    for rec in decisions:
        if has_stamp(rec):
            break
        out.add(rec.get("id"))
    return out


def group_by_id(rows):
    """{id: [rec, ...]} in the given (ledger) order over kind:"decision-brief" / "decision-brief-test" recs."""
    out = {}
    for rec in rows:
        if isinstance(rec, dict) and rec.get("id") is not None:
            out.setdefault(rec["id"], []).append(rec)
    return out


# ---------- the state table ----------

def verify_brief(row, brief):
    """-> "valid" | "findings" | "stale" for one brief object against its card: the lint stamp must exist, its
    brief_sha must equal the sha over the brief's own bytes (stamp removed) and card_sha must match the card's
    reader-facing fields (any accepted canonical form); exit 0 is valid, exit 1 findings, anything else stale."""
    if not isinstance(brief, dict):
        return "stale"
    stamp = brief.get("lint")
    if not isinstance(stamp, dict):
        return "stale"
    lint = _lint()
    if stamp.get("brief_sha") != lint.brief_sha(brief):
        return "stale"
    if brief.get("card_sha") not in lint.card_sha_variants(row):
        return "stale"
    if stamp.get("exit") == 0:
        return "valid"
    if stamp.get("exit") == 1:
        return "findings"
    return "stale"


def resolve_brief(row, briefs, tests, legacy=None, status=None):
    """-> (state, brief-or-None). row: the decision rec (a logical row may carry "status" and "_legacy"); briefs: the
    kind:"decision-brief" recs for this id in ledger order; tests: kind:"decision-brief-test" recs (carried, unread
    by this lane). The latest valid brief per id wins; with briefs present but none valid the row is stale."""
    status = status or (row.get("status") if isinstance(row, dict) else None) or "open"
    if legacy is None:
        legacy = bool(row.get("_legacy")) if isinstance(row, dict) else False
    candidates = []
    if isinstance(row.get("brief"), dict):
        candidates.append(row["brief"])
    for rec in briefs or []:
        if isinstance(rec, dict) and isinstance(rec.get("brief"), dict):
            candidates.append(rec["brief"])
    verdicts = [(verify_brief(row, b), b) for b in candidates]
    for verdict, brief in reversed(verdicts):
        if verdict in QUESTION_STATES:
            return verdict, brief
    if verdicts:
        return "stale", verdicts[-1][1]
    if legacy:
        return ("legacy-missing" if status in ("open", "commented") else "legacy-resolved"), None
    return "missing", None


def brief_state(row, briefs, tests, legacy=None, status=None):
    return resolve_brief(row, briefs, tests, legacy=legacy, status=status)[0]


def findings_rules(brief):
    """The comma-joined rule ids of a findings brief's stamp, first-use order ("B2,B5")."""
    stamp = brief.get("lint") if isinstance(brief, dict) else None
    out = []
    for f in (stamp or {}).get("findings") or []:
        rule = str(f).split(":", 1)[0]
        if rule and rule not in out:
            out.append(rule)
    return ",".join(out)


# ---------- lines ----------

def sh_quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def age_days(row, stamp):
    """Whole days from the row's original ask to the header stamp (never the wall clock)."""
    try:
        then = datetime.date.fromisoformat(str(row.get("requested_at") or row.get("date"))[:10])
        now = datetime.date.fromisoformat(str(stamp)[:10])
        return max(0, (now - then).days)
    except (ValueError, TypeError):
        return 0


def age_label(row, stamp):
    n = age_days(row, stamp)
    return "new today" if n == 0 else "%dd old" % n


def marker_lines(row, state, brief=None):
    """Exactly one marker line for a legacy-missing card or a findings card; nothing otherwise."""
    rid = row.get("id")
    if state == "legacy-missing":
        return [INDENT + MARKER_MISSING % rid]
    if state == "findings":
        brief = brief if isinstance(brief, dict) else row.get("brief")
        stamp = brief.get("lint") if isinstance(brief, dict) else {}
        n = len((stamp or {}).get("findings") or [])
        return [INDENT + MARKER_FINDINGS % (findings_rules(brief), n, rid)]
    return []


def marker_text(row, state, brief=None):
    """The marker without its indentation: the value the html card payload carries under brief_marker."""
    lines = marker_lines(row, state, brief=brief)
    return lines[0][len(INDENT):] if lines else None


def not_ready_lines(rows, stamp=None):
    """The NOT READY block addressed to the lab, one per row, blocks separated by one blank line. rows: dicts
    carrying brief_state (missing | stale) or (row, state) pairs. No ask:, no [ ] line, no answer: line."""
    out = []
    for item in rows:
        row, state = (item if isinstance(item, tuple) else (item, item.get("brief_state")))
        finding = NOT_READY_FINDING.get(state, "BRIEF-MISSING")
        if out:
            out.append("")
        out.append("- [%s | %s | %s | NOT READY]" % (row.get("id"), row.get("urgency", "normal"), age_label(row, stamp)))
        out.append(INDENT + "title: %s" % row.get("title", ""))
        out.append(INDENT + "finding: %s" % finding)
        out.append(INDENT + "fix: %s %s --brief brief.json" % (BRIEF_CLI, row.get("id")))
    return out


def _choices(brief):
    ch = brief.get("choices") if isinstance(brief.get("choices"), list) else []
    return [c for c in ch if isinstance(c, dict)]


def _terms_line(brief):
    terms = brief.get("terms") if isinstance(brief.get("terms"), dict) else {}
    return "; ".join("%s = %s" % (k, v) for k, v in terms.items() if isinstance(v, str))


def _evidence_line(row, brief):
    src = [s for s in (brief.get("sources") or []) if isinstance(s, str)] if isinstance(brief, dict) else []
    if not src:
        src = [str(p) for p in (row.get("context_pointers") or [])]
    return " \u00b7 ".join(src) if src else "-"


def card_lines(row, brief, stamp):
    """The brief-first card body (two-space indent; identical on show and section 1): the seven labels in order,
    one answer: line per choice, the terms line when the brief glosses anything, the evidence: machine line, then
    details: <show --raw>. The chip line is the surface's own and precedes these lines."""
    rid = row.get("id")
    lines = [INDENT + "DECIDE: %s" % brief.get("decide", ""),
             INDENT + "THE SITUATION: %s" % brief.get("situation", ""),
             INDENT + "WHY YOU: %s" % brief.get("yours_because", ""),
             INDENT + "YOUR CHOICES:"]
    for c in _choices(brief):
        lines.append(INDENT + "[ ] %s \u2014 %s" % (c.get("label", "?"), c.get("in_practice", "")))
        lines.append(INDENT + "answer: %s %s --accept %s" % (RESOLVE_CLI, rid, sh_quote(c.get("label", ""))))
    lines.append(INDENT + "IF YOU DO NOTHING: %s" % brief.get("if_nothing", ""))
    lines.append(INDENT + "WHAT WE KNOW: %s" % brief.get("evidence_line", ""))
    lines.append(INDENT + "UNDO: %s" % "; ".join("%s \u2014 %s" % (c.get("label", "?"), c.get("undo", "")) for c in _choices(brief)))
    terms = _terms_line(brief)
    if terms:
        lines.append(INDENT + "terms: %s" % terms)
    lines.append(INDENT + "evidence: %s" % _evidence_line(row, brief))
    lines.append(INDENT + "details: %s %s --raw" % (SHOW_CLI, rid))
    return lines


def card_text(row, brief):
    """The reader text of the card as one string, every whitespace run collapsed -- the reference the P12 extractor
    compares across show, section 1 and the html payload (labels, choices, answer lines, evidence, details)."""
    return re.sub(r"\s+", " ", " ".join(card_lines(row, brief, None))).strip()


def record_lines(row, res, brief, stamp):
    """The brief-first record (section 1b) for a two-way decision the door recorded: DECIDED, THE SITUATION, WHY THE
    LAB DID NOT ASK YOU, WHAT CHANGES, UNDO (the resolution row's undo line), the veto line, details."""
    rid = row.get("id")
    res = res if isinstance(res, dict) else {}
    chosen = (res.get("chosen_options") or ["?"])[0]
    choice = next((c for c in _choices(brief) if c.get("label") == chosen), {})
    until = str(res.get("veto_open_until") or "?")
    window = "open" if until >= str(stamp)[:10] else "closed"
    lines = [INDENT + "DECIDED: %s \u2014 %s" % (chosen, brief.get("decide", "")),
             INDENT + "THE SITUATION: %s" % brief.get("situation", ""),
             INDENT + "WHY THE LAB DID NOT ASK YOU: %s" % brief.get("yours_because", ""),
             INDENT + "WHAT CHANGES: %s" % (choice.get("in_practice") or brief.get("if_nothing", "")),
             INDENT + "UNDO: %s  (one command; still works after the window)" % (res.get("undo") or choice.get("undo") or "?"),
             INDENT + "veto: %s %s --deny --comment veto   (veto %s until %s; runs the undo; nothing else changes)"
             % (RESOLVE_CLI, rid, window, until)]
    terms = _terms_line(brief)
    if terms:
        lines.append(INDENT + "terms: %s" % terms)
    lines.append(INDENT + "evidence: %s" % _evidence_line(row, brief))
    lines.append(INDENT + "details: %s %s --raw" % (SHOW_CLI, rid))
    return lines


def brief_public(brief):
    """The brief object in the fixed field order (the html payload's brief; JSON key order is render order)."""
    out = {}
    for k in BRIEF_KEY_ORDER:
        if k in brief:
            out[k] = brief[k]
    for k, v in brief.items():
        if k not in out:
            out[k] = v
    return out


def _payload_note(row, res):
    note = row.get("note", "")
    comments = [r for r in (res or []) if isinstance(r, dict) and r.get("disposition") == "commented"]
    if comments:
        quoted = " \u00b7 ".join("queued comment on record: \u201c%s\u201d" % c.get("comment", "") for c in comments)
        note = (note + " " if note else "") + quoted
    return note


def html_payload(row, brief, res, stamp, state=None):
    """The card object for decisions.html `const DECISIONS`. legacy-missing: today's object with the marker string as
    the value of a brief_marker key inserted FIRST (json.dumps(indent=2) adds exactly one line). valid: today's object
    plus brief (fixed field order) and brief_state. findings: brief_marker first, then the same. missing / stale: the
    NOT READY object -- no ask, no options, no answer command: brief_state and the not_ready lines only."""
    state = state or row.get("brief_state") or "legacy-missing"
    if state in NOT_READY_STATES:
        item = {k: row[k] for k in NOT_READY_KEYS if k in row}
        item["brief_state"] = state
        item["not_ready"] = not_ready_lines([(row, state)], stamp)
        return item
    item = {}
    if state in ("legacy-missing", "findings"):
        item["brief_marker"] = marker_text(row, state, brief=brief)
    for k in PAYLOAD_KEYS:
        if k in row:
            item[k] = row[k]
    note = _payload_note(row, res)
    if note:
        item["note"] = note
    if state in QUESTION_STATES and isinstance(brief, dict):
        item["brief"] = brief_public(brief)
        item["brief_state"] = state
    return item


def vocab_payload(vocab_path):
    """{headword: gloss} from scripts/house-vocabulary.json (every entry carrying a string gloss); {} on any failure.
    Not injected by this lane's compiler (the html VOCAB injection is the sibling lane's); pure, for callers that do."""
    try:
        with open(vocab_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    terms = data.get("terms", {}) if isinstance(data, dict) else {}
    out = {}
    if isinstance(terms, dict):
        for head, entry in terms.items():
            if isinstance(entry, dict) and isinstance(entry.get("gloss"), str):
                out[head] = entry["gloss"]
    return out


# ---------- the armed default (Method (e), two legs) ----------

def armed_default(row, status, policy_text):
    """-> the armed ISO date, or None. Row-armed: default_on_silence equals one of ask.options[].label; the date is
    requested_at (its date part) plus 14 days. Legacy-armed: a row with no default_on_silence is armed iff the
    silence policy's text, whitespace-collapsed, matches the frozen list-item regex with this row's id; the date is
    the regex's second group. Only an open or commented card can be armed."""
    if status not in ("open", "commented"):
        return None
    ask = row.get("ask") if isinstance(row.get("ask"), dict) else {}
    labels = [o.get("label") for o in (ask.get("options") or []) if isinstance(o, dict)]
    dflt = row.get("default_on_silence")
    if isinstance(dflt, str) and dflt in labels:
        try:
            day = datetime.date.fromisoformat(str(row.get("requested_at") or row.get("date"))[:10])
        except (ValueError, TypeError):
            return None
        return (day + datetime.timedelta(days=ARMED_DAYS)).isoformat()
    if "default_on_silence" not in row and policy_text:
        flat = re.sub(r"\s+", " ", policy_text)
        for m in ARMED_LINE_RE.finditer(flat):
            if m.group(1) == row.get("id"):
                return m.group(2)
    return None


# ---------- selftest (synthetic rows only) ----------

def _selftest():
    import hashlib
    import tempfile
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("RENDER-SELFTEST-PASS" if cond else "RENDER-SELFTEST-FAIL", name, (" -- " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    lint = _lint()

    def row(n, **kw):
        r = {"kind": "decision", "id": "DEC-%03d" % n, "date": "2026-01-01", "requested_at": "2026-01-01",
             "requested_by": "lane x", "title": "Title %d" % n, "urgency": "normal", "class": "plan",
             "ask": {"question": "Go or hold?", "header": "Go", "multiSelect": False,
                     "options": [{"label": "go", "description": "ship it", "undo": "git-revert"},
                                 {"label": "hold", "description": "wait", "undo": "ledger-row"}]},
             "context_pointers": ["README.md:1"], "blocks": [], "why_only_you": "only you", "status": "open"}
        r.update(kw)
        return r

    def brief(r, exit_code=0, findings=None, **over):
        b = {"decide": "Decide whether it ships now.", "situation": "It is ready.", "yours_because": "You sign it.",
             "choices": [{"label": "go", "in_practice": "It ships.", "undo": "Revert the landing commit."},
                         {"label": "hold", "in_practice": "It waits.", "undo": "A later row supersedes."}],
             "if_nothing": "Nothing changes: the card stays open.", "evidence_line": "It passed every check.",
             "terms": {}, "sources": ["pipeline-fact:card-stays-open"], "provenance": {"protocol": "inline"}}
        b.update(over)
        b["card_sha"] = lint.card_sha(r)
        stamp = {"tool": "decision_card_lint", "sha7": "0000000", "exit": exit_code, "brief_sha": lint.brief_sha(b)}
        if findings:
            stamp["findings"] = findings
        b["lint"] = stamp
        return b

    stamp = "2026-01-11"
    r1 = row(1)
    r1["brief"] = brief(r1)
    ok("state-valid", brief_state(r1, [], [], legacy=False) == "valid")
    r2 = row(2)
    r2["brief"] = brief(r2, exit_code=1, findings=["B2:long", "B5:term", "B2:again"])
    ok("state-findings", brief_state(r2, [], [], legacy=False) == "findings")
    ok("findings-rules", findings_rules(r2["brief"]) == "B2,B5")
    ok("state-legacy-missing", brief_state(row(3), [], [], legacy=True, status="open") == "legacy-missing")
    ok("state-legacy-commented", brief_state(row(3), [], [], legacy=True, status="commented") == "legacy-missing")
    ok("state-legacy-resolved", brief_state(row(3), [], [], legacy=True, status="accepted") == "legacy-resolved")
    ok("state-missing", brief_state(row(4), [], [], legacy=False) == "missing")
    r5 = row(5)
    stale = brief(r5)
    stale["decide"] = "Edited in place."
    ok("state-stale-edited", brief_state(r5, [{"kind": BRIEF_KIND, "id": r5["id"], "brief": stale}], [], legacy=False) == "stale")
    unstamped = brief(r5)
    unstamped.pop("lint")
    ok("state-stale-unstamped", brief_state(r5, [{"kind": BRIEF_KIND, "id": r5["id"], "brief": unstamped}], [], legacy=True) == "stale")
    r5b = row(5)
    r5b["title"] = "changed"
    ok("state-stale-card-sha", brief_state(r5b, [{"kind": BRIEF_KIND, "id": r5b["id"], "brief": brief(r5)}], [], legacy=False) == "stale")
    repaired = [{"kind": BRIEF_KIND, "id": r5["id"], "brief": stale}, {"kind": BRIEF_KIND, "id": r5["id"], "brief": brief(r5)}]
    st, b = resolve_brief(r5, repaired, [], legacy=False)
    ok("latest-valid-wins", st == "valid" and b is repaired[1]["brief"])
    ok("legacy-retrofit-valid", brief_state(row(3), [{"kind": BRIEF_KIND, "id": row(3)["id"], "brief": brief(row(3))}], [], legacy=True) == "valid")
    ok("row-flags-default", brief_state(dict(row(6), _legacy=True, status="open"), [], []) == "legacy-missing")
    # boundary
    decs = [row(1), row(2), dict(row(3), brief=brief(row(3))), row(4)]
    ok("legacy-ids-explicit", legacy_ids(decs, {LEGACY_KEY: 2}) == {"DEC-%03d" % 1, "DEC-%03d" % 2})
    ok("legacy-ids-explicit-string", legacy_ids(decs, {LEGACY_KEY: "3"}) == {"DEC-%03d" % n for n in (1, 2, 3)})
    ok("legacy-ids-shape-and-order", legacy_ids(decs, {}) == {"DEC-%03d" % 1, "DEC-%03d" % 2})
    ok("legacy-ids-no-stamp-all-legacy", legacy_ids([row(1), row(2)], {}) == {"DEC-%03d" % 1, "DEC-%03d" % 2})
    # marker lines
    m = marker_lines(row(3), "legacy-missing")
    frozen = ("  brief: BRIEF-MISSING \u2014 no brief on file; a default never executes against this card while it is not "
              "readable; retrofit: python3 scripts/decisions.py brief %s --brief brief.json" % row(3)["id"])
    ok("marker-legacy-missing-frozen", m == [frozen], repr(m))
    ok("marker-findings", len(marker_lines(r2, "findings")) == 1 and "BRIEF-FINDINGS B2,B5" in marker_lines(r2, "findings")[0])
    ok("marker-none-for-valid", marker_lines(r1, "valid") == [] and marker_lines(row(3), "legacy-resolved") == [] and marker_lines(row(4), "missing") == [])
    ok("marker-text-strips-indent", marker_text(row(3), "legacy-missing") == frozen.strip())
    # NOT READY block grammar
    nr = not_ready_lines([dict(row(4), brief_state="missing")], stamp)
    ok("not-ready-grammar", nr == ["- [%s | normal | 10d old | NOT READY]" % row(4)["id"], "  title: Title 4", "  finding: BRIEF-MISSING",
                                   "  fix: python3 scripts/decisions.py brief %s --brief brief.json" % row(4)["id"]], repr(nr))
    nr2 = not_ready_lines([(row(5), "stale")], "2026-01-01")
    ok("not-ready-stale-new-today", nr2[0].endswith("| new today | NOT READY]") and nr2[2] == "  finding: BRIEF-STALE")
    ok("not-ready-no-question-grammar", not any(l.lstrip().startswith(("ask:", "[ ]", "answer:")) for l in nr + nr2))
    both = not_ready_lines([(row(4), "missing"), (row(5), "stale")], stamp)
    ok("not-ready-blocks-separated", len(both) == 9 and both[4] == "")
    # card lines
    cl = card_lines(r1, r1["brief"], stamp)
    labels = [l.strip().split(":")[0] for l in cl if l.strip().split(":")[0] in CARD_LABELS]
    ok("card-labels-in-order", labels == list(CARD_LABELS), str(labels))
    ok("card-one-answer-per-choice", sum(1 for l in cl if l.startswith("  answer: ")) == 2)
    ok("card-answer-form", "  answer: python3 scripts/decisions.py resolve %s --accept \"go\"" % r1["id"] in cl)
    ok("card-evidence-and-details", cl[-2] == "  evidence: pipeline-fact:card-stays-open" and cl[-1] == "  details: python3 scripts/decisions.py show %s --raw" % r1["id"])
    ok("card-no-terms-line-when-empty", not any(l.startswith("  terms:") for l in cl))
    r7 = row(7)
    r7["brief"] = brief(r7, terms={"gate": "a check that refuses"})
    ok("card-terms-line", any(l == "  terms: gate = a check that refuses" for l in card_lines(r7, r7["brief"], stamp)))
    ok("card-text-collapses", card_text(r1, r1["brief"]) == re.sub(r"\s+", " ", " ".join(cl)).strip())
    # record lines
    res = {"disposition": "accepted", "chosen_options": ["go"], "basis": "two-way-door", "undo": "git revert X", "veto_open_until": "2026-01-08"}
    rl = record_lines(r1, res, r1["brief"], stamp)
    rlabels = [l.strip().split(":")[0] for l in rl if l.strip().split(":")[0] in RECORD_LABELS]
    ok("record-labels-in-order", rlabels == list(RECORD_LABELS), str(rlabels))
    ok("record-veto-closed", "veto closed until 2026-01-08" in rl[5] and "UNDO: git revert X" in rl[4])
    # html payload
    p = html_payload(row(3), None, [], stamp, state="legacy-missing")
    ok("payload-legacy-marker-first", list(p.keys())[0] == "brief_marker" and p["brief_marker"] == frozen.strip())
    base = {k: row(3)[k] for k in PAYLOAD_KEYS if k in row(3)}
    ok("payload-legacy-adds-exactly-one-key", [k for k in p if k != "brief_marker"] == list(base.keys()) and all(p[k] == base[k] for k in base))
    p1 = html_payload(r1, r1["brief"], [], stamp, state="valid")
    ok("payload-valid-brief-and-state", "brief_marker" not in p1 and p1["brief_state"] == "valid" and list(p1["brief"].keys())[:6] == list(BRIEF_KEY_ORDER[:6]))
    p2 = html_payload(r2, r2["brief"], [], stamp, state="findings")
    ok("payload-findings-marker-first", list(p2.keys())[0] == "brief_marker" and p2["brief_state"] == "findings")
    p4 = html_payload(row(4), None, [], stamp, state="missing")
    ok("payload-not-ready-no-ask", "ask" not in p4 and "why_only_you" not in p4 and p4["brief_state"] == "missing" and p4["not_ready"] == nr)
    pc = html_payload(row(3), None, [{"disposition": "commented", "comment": "hm"}], stamp, state="legacy-missing")
    ok("payload-comment-note", pc["note"] == "queued comment on record: \u201chm\u201d")
    # armed default, both legs
    ok("armed-row-leg", armed_default(dict(row(8), default_on_silence="hold"), "open", "") == "2026-01-15")
    ok("armed-row-leg-not-label", armed_default(dict(row(8), default_on_silence="nothing-changes"), "open", "") is None)
    ok("armed-row-leg-resolved-none", armed_default(dict(row(8), default_on_silence="hold"), "accepted", "") is None)
    policy = ("2. **Armed prompt with parking backstop**\n   - Armed for %s (requested 2026-01-01; re-armed by this policy 2026-01-02): one\n"
              "     prompt at next presence; parking backstop **2026-01-20**.\n" % row(9)["id"])
    ok("armed-legacy-leg", armed_default(row(9), "open", policy) == "2026-01-20")
    ok("armed-legacy-leg-other-id", armed_default(row(8), "open", policy) is None)
    ok("armed-legacy-leg-needs-no-default-field", armed_default(dict(row(9), default_on_silence="nothing-changes"), "open", policy) is None)
    # vocab payload
    with tempfile.TemporaryDirectory(prefix="brief-render-selftest-") as tmp:
        vp = os.path.join(tmp, "v.json")
        with open(vp, "w", encoding="utf-8") as fh:
            json.dump({"terms": {"gate": {"gloss": "a check", "status": "preferred"}, "x": {"status": "house-only"}}}, fh)
        ok("vocab-payload", vocab_payload(vp) == {"gate": "a check"} and vocab_payload(os.path.join(tmp, "none.json")) == {})
        with open(os.path.join(tmp, "hyp.json"), "w") as fh:
            json.dump({LEGACY_KEY: 37}, fh)
        os.makedirs(os.path.join(tmp, ".claude"))
        os.replace(os.path.join(tmp, "hyp.json"), os.path.join(tmp, ".claude", "hyp.json"))
        ok("load-config", explicit_boundary(load_config(tmp)) == 37 and load_config(os.path.join(tmp, "nowhere")) == {})
    # determinism and the no-id-literal rule
    ok("deterministic", card_lines(r1, r1["brief"], stamp) == cl and html_payload(r1, r1["brief"], [], stamp, state="valid") == p1)
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    ok("no-id-literals-in-render-source", re.search(r"DEC-[0-9]|H-[0-9]{3}|H-DRA" + r"FT-", src) is None)
    ok("sha-helpers-shared-with-lint", len(hashlib.sha256(b"x").hexdigest()) == 64 and lint.card_sha(r1) in lint.card_sha_variants(r1))
    print("render-selftest: %d failure(s)" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    print(__doc__.strip().splitlines()[0])
    print("usage: decision_brief_render.py --selftest   (the module is imported by decisions.py, the compilers and the resolver)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
