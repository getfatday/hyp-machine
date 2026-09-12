#!/usr/bin/env python3
"""decision_door_check.py -- the door evaluator: between validate and append, every decision candidate is either
RECORDED as the lab's own two-way decision (with a veto window and a one-line undo) or RENDERED as a card
carrying the findings. Fail-closed: anything the evaluator cannot verify renders.

Clauses, evaluated top to bottom, stopping at the first routing clause that fires
(R = the validated candidate row with the six door fields; HEAD = the committed tree; TAIL = the uncommitted
ledger rows):
  W1 FIELDS     every option has an undo in vocabulary; staged_artifact, evidence, externality, recommended,
                default_on_silence present and in vocabulary; class spend => amount_usd; recommended != none => an
                option label.                                    no -> MALFORMED (every defect listed; exit 2)
  W2 DEDUP-WHY  >= 2 decision rows in TAIL share R.why_only_you byte-identically -> MALFORMED-BATCH (exit 2)
  H1 HARD       externality != none: corroboration POSITIVE -> CARD (hard=<class>); UNVERIFIABLE -> CARD;
                NEGATIVE -> finding DECLARED-HARD-UNCORROBORATED:<class>, continue
  T1 UNDO       every option has undo != none and the recommended option's undo is corroborated (git-revert | flag |
                amendment => every staged path inside the worktree; ledger-row => the effect is an append to the
                ledger); undo none on tracked paths -> finding UNDO-MISDECLARED
  T2 EVIDENCE   ordered: (a) a pointer that fails to resolve at HEAD -> UNVERIFIABLE -> CARD; (b) recommended != none
                and default_on_silence == recommended -> yes (zero-information); (c) recommended != none and the
                pointer resolves inside EVIDENCE-CLASSES -> yes; (d) otherwise no (NOT-EVIDENCE-DECIDED);
                AMENDMENT GUARD after a yes: a staged hypotheses/*.md whose frozen span (from the Binary-assertions
                heading line through the Verdict-rule heading line, newline inclusive) differs from HEAD -> no
                (FROZEN-SPAN-TOUCHED)
  T3 EXTERNALITY every corroboration in the table NEGATIVE (a declared class already NEGATIVE at H1 stays NEGATIVE);
                any POSITIVE -> no (EXTERNALITY-UNDECLARED:<class>); any UNVERIFIABLE -> CARD
  ROUTING       T1 and T2 and T3 -> RECORD; otherwise CARD with every finding; an exception or a git stall past the
                timeout -> CARD (EVALUATOR-FAIL-CLOSED)
  STREAK        the last 10 door outcomes on file all RECORD with zero vetoes and zero cards -> this candidate renders
                as CARD (EVALUATOR-STREAK)

The corroboration table is the door-field lint's (scripts/decision_card_lint.py beside this file: the evaluator and the
lint read ONE table), with two readings this spec fixes: the hook denial record is `.claude/hook-denials.jsonl` in the
worktree (one JSON row per line {ts, command, rule}; POSITIVE iff a row's command equals the staged command
byte-for-byte; no file or no row -> NEGATIVE; a row that does not parse -> UNVERIFIABLE), and a staged path under
.github/ or .changeset/ is an external-publication target unless a SHIP.md is staged beside it.

Allowed reads: the candidate row, the repository's git objects, the ledger, research/raw/ and program.md at HEAD, the
staged hypotheses/*.md worktree bytes (the amendment guard), the hook denial record. No clock beyond requested_at, no
environment, no network, no process beyond git. Standard library only; byte-deterministic. No card or spec id literal.

CLI (the stdin/argv contract): the candidate row as JSON on stdin; --root, --ledger, --git-timeout, --inject-fault
(the harness's fault-injection hook, the crash seed); stdout: the one DECISION-DOOR audit line (or ADD-REFUSED lines),
the plain-English block, then one `DOOR-JSON<TAB>{...}` line; exit 0 (CARD | RECORD) or 2 (MALFORMED). decisions.py
add imports run() in-process and stamps the resolution row's date itself.

Plugin port (hyp-machine): the ledger is the consumer's -- `.claude/hyp.json` `ledger_file`, default `ledger/ledger.jsonl`,
resolved through the lint's ledger_rel_for when no ledger path is handed in -- and its repo-relative path is what a
`ledger-row` undo must stage (T1) and D3's decision-resolution authority (T2). Every other byte is the lab's sealed
evaluator (the source lab's decision-door-evaluator lane; its fixture.lock evaluator.sha256 b5640819...).
"""
import datetime
import hashlib
import json
import os
import re
import sys

LINT_MODULE = "decision_card_lint"
GIT_TIMEOUT_DEFAULT = 20
VETO_WINDOW_DAYS = 7
STREAK_LEN = 10
RECORD_BASIS = "two-way-door"
DENIAL_RECORD_REL = ".claude/hook-denials.jsonl"
REVERTIBLE = ("git-revert", "flag", "amendment")
LEDGER_UNDO = "ledger-row"
CONSUMER_VISIBLE_PREFIXES = (".github/", ".changeset/")
SHIP_GATE_BASENAME = "SHIP.md"
FROZEN_START = "## Binary assertions"
FROZEN_END = "## Verdict rule"
HYP_SPEC_RE = re.compile(r"^hypotheses/[^/]+\.md$")
GRANT_CITE = "research/raw/2026-08-18-decisions-are-two-way-doors-grant.md"
CLARITY_CITE = "research/raw/2026-08-28-decision-clarity-directive.md"
FRAGMENT_CITE = "experiments/journal-fragments/0479-dec-018-withdrawn-caps-from-rules-not-humans.md:34"
POSITIVE, NEGATIVE, UNVERIFIABLE = "POSITIVE", "NEGATIVE", "UNVERIFIABLE"
_LINT = None


def _lint():
    """scripts/decision_card_lint.py imported from beside this file (the shared corroboration table)."""
    global _LINT
    if _LINT is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            import decision_card_lint  # noqa
        except ImportError:
            raise SystemExit("FATAL: scripts/decision_card_lint.py (the door-field lint, the corroboration table) is not beside decision_door_check.py")
        _LINT = decision_card_lint
    return _LINT


def ledger_rel_of(root, ledger_path):
    """The ledger's repo-relative posix path (the consumer's: .claude/hyp.json ledger_file through the lint's
    ledger_rel_for when none is handed in) -- what a ledger-row undo must stage and D3's resolution authority."""
    return os.path.relpath(os.path.abspath(ledger_path), root).replace(os.sep, "/")


def self_sha7():
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:7]


class Route(Exception):
    """A routing clause fired: outcome CARD with the findings so far."""

    def __init__(self, reason, clause=None, value=None):
        Exception.__init__(self, reason)
        self.reason, self.clause, self.value = reason, clause, value


class Result(object):
    def __init__(self, rec):
        self.id = str(rec.get("id", "?"))
        self.outcome = None
        self.clauses = {}
        self.findings = []
        self.defects = []          # [(clause, detail)] for MALFORMED
        self.hard = None
        self.corroborated_by = None
        self.head = None
        self.evaluator = None
        self.evidence_ref = None
        self.undo_class = None
        self.ledger_rel = None
        self.stdout_lines = []
        self.record = None         # the resolution-row fields for RECORD (the caller stamps `date`)

    @property
    def exit_code(self):
        return 2 if self.outcome in ("MALFORMED", "MALFORMED-BATCH") else 0

    def door_object(self):
        d = {"outcome": self.outcome, "evaluator": self.evaluator, "head": self.head, "clauses": dict(self.clauses),
             "findings": list(self.findings)}
        if self.hard:
            d["hard"] = self.hard
            d["corroborated_by"] = self.corroborated_by
        return d


# ---------- corroboration (one table: the lint's observables + this spec's two readings) ----------

def _staged_paths(rec):
    sa = rec.get("staged_artifact")
    return list(sa) if isinstance(sa, list) else []


def obs_classifier_flagged(rec, root):
    """-> (state, why). The hook denial record `.claude/hook-denials.jsonl` in the worktree."""
    lint = _lint()
    path = os.path.join(root, DENIAL_RECORD_REL)
    if not os.path.isfile(path):
        return NEGATIVE, "no denial on record (no hook denial record in the worktree)"
    with open(path, "rb") as fh:
        raw = fh.read().decode("utf-8", "replace")
    rows = []
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return UNVERIFIABLE, "hook denial record row %d does not parse as JSON" % n
        rows.append((n, row))
    if lint.staged_kind(rec) != "command":
        return NEGATIVE, "no command staged (no denial on record)"
    cmd = rec["staged_artifact"]
    for n, row in rows:
        if isinstance(row, dict) and row.get("command") == cmd:
            return POSITIVE, "a harness denial for the staged command is on record (%s row %d, rule %s)" % (DENIAL_RECORD_REL, n, row.get("rule", "?"))
    return NEGATIVE, "no denial on record for the staged command"


def obs_consumer_visible_target(rec):
    paths = _staged_paths(rec)
    hits = [p for p in paths if p.startswith(CONSUMER_VISIBLE_PREFIXES)]
    if hits and not any(os.path.basename(p) == SHIP_GATE_BASENAME for p in paths):
        return POSITIVE, "staged path %s is a consumer-visible workflow target with no %s staged beside it" % (json.dumps(hits[0]), SHIP_GATE_BASENAME)
    return NEGATIVE, "no consumer-visible workflow target staged"


def corroborate(cls, rec, git, root):
    """-> (POSITIVE | NEGATIVE | UNVERIFIABLE, why) for one class of the table."""
    lint = _lint()
    if cls == "classifier-flagged":
        return obs_classifier_flagged(rec, root)
    fn = dict(lint.OBSERVABLES)[cls]
    positive, why = fn(rec, git)
    if positive:
        return POSITIVE, why
    if cls == "external-publication":
        state, why2 = obs_consumer_visible_target(rec)
        if state == POSITIVE:
            return POSITIVE, why2
    return NEGATIVE, why


TABLE_CLASSES = ("other-humans", "external-publication", "spend-beyond-granted-budget", "physical-act",
                 "classifier-flagged", "reserved-in-his-words")


# ---------- the clauses ----------

def clause_w1(rec, res):
    defects = _lint().shape_errors(rec)
    if defects:
        res.defects = [("W1", "%s %s" % (rule, detail)) for rule, detail in defects]
        res.clauses["W1"] = "no(%d defects)" % len(defects)
        res.outcome = "MALFORMED"
        return False
    res.clauses["W1"] = "yes"
    return True


def clause_w2(rec, res, git, root, ledger_path):
    lint = _lint()
    why = rec.get("why_only_you")
    twins = [r for r in lint.tail_decision_rows(git, root, ledger_path) if r.get("why_only_you") == why]
    if len(twins) >= 2:
        ids = ", ".join(str(r.get("id", "?")) for r in twins)
        res.defects = [("W2", "why_only_you is byte-identical to %d uncommitted decision rows (%s): one policy question filed %d times: file one"
                        % (len(twins), ids, len(twins) + 1))]
        res.clauses["W2"] = "yes(%d twins)" % len(twins)
        res.outcome = "MALFORMED-BATCH"
        return False
    res.clauses["W2"] = "no"
    return True


def clause_h1(rec, res, git, root):
    ext = rec.get("externality")
    if ext == "none":
        res.clauses["H1"] = "no"
        return
    state, why = corroborate(ext, rec, git, root)
    if state == POSITIVE:
        res.hard, res.corroborated_by = ext, why
        res.clauses["H1"] = "yes(%s: %s)" % (ext, why)
        raise Route("hard", "H1", "yes")
    if state == UNVERIFIABLE:
        res.findings.append("CORROBORATION-UNVERIFIABLE:%s (%s)" % (ext, why))
        res.clauses["H1"] = "unverifiable(%s: %s)" % (ext, why)
        raise Route("fail-closed", "H1", "unverifiable")
    res.findings.append("DECLARED-HARD-UNCORROBORATED:%s (%s)" % (ext, why))
    res.clauses["H1"] = "no(declared %s uncorroborated: %s)" % (ext, why)


def clause_t1(rec, res, git):
    lint = _lint()
    opts = lint._options(rec)
    labels = [str(o.get("label", "")) for o in opts]
    undos = [o.get("undo") for o in opts]
    recommended = rec.get("recommended")
    kind = lint.staged_kind(rec)
    paths = _staged_paths(rec)
    rec_idx = labels.index(recommended) if recommended in labels else None
    rec_undo = undos[rec_idx] if rec_idx is not None else None
    tracked, tracked_dirs = git.tracked()
    findings = []
    if rec_undo == "none" and kind == "paths" and paths and all(os.path.normpath(p) in tracked for p in paths):
        findings.append("UNDO-MISDECLARED: option %s declares none while every staged path is a tracked repository path" % json.dumps(recommended))
    for label, undo in zip(labels, undos):
        if undo == "none" and not (label == recommended and findings):
            findings.append("UNDO-NONE: option %s declares no undo" % json.dumps(label))
    if findings:
        res.findings.extend(findings)
        res.clauses["T1"] = "no(%s)" % "; ".join(f.split(":")[0] for f in findings)
        return False
    if rec_idx is None:
        res.clauses["T1"] = "yes(undo:all-revertible; no recommended option to corroborate)"
        return True
    if rec_undo in REVERTIBLE:
        if kind == "paths":
            outside = [p for p in paths if not lint._inside_worktree(p, tracked, tracked_dirs)]
            if outside:
                res.findings.append("UNDO-UNCORROBORATED: option %s declares %s while staged path %s lies outside the repository worktree"
                                    % (json.dumps(recommended), rec_undo, json.dumps(outside[0])))
                res.clauses["T1"] = "no(UNDO-UNCORROBORATED)"
                return False
            res.undo_class = rec_undo
            res.clauses["T1"] = "yes(undo:%s)" % rec_undo
            return True
        res.findings.append("UNDO-UNVERIFIABLE: option %s declares %s but the staged artifact is %s, which cannot be corroborated as revertible"
                            % (json.dumps(recommended), rec_undo, "a command line" if kind == "command" else json.dumps(rec.get("staged_artifact"))))
        res.clauses["T1"] = "no(UNDO-UNVERIFIABLE: %s on %s)" % (rec_undo, kind)
        return False
    if rec_undo == LEDGER_UNDO:
        if kind == "paths" and [os.path.normpath(p) for p in paths] == [res.ledger_rel]:
            res.undo_class = LEDGER_UNDO
            res.clauses["T1"] = "yes(undo:ledger-row)"
            return True
        res.findings.append("UNDO-UNCORROBORATED: option %s declares ledger-row while the staged artifact is not an append to %s" % (json.dumps(recommended), res.ledger_rel))
        res.clauses["T1"] = "no(UNDO-UNCORROBORATED)"
        return False
    res.findings.append("UNDO-UNVERIFIABLE: option %s declares %s" % (json.dumps(recommended), json.dumps(rec_undo)))
    res.clauses["T1"] = "no(UNDO-UNVERIFIABLE)"
    return False


def frozen_span(text):
    """The bytes from the start of the Binary-assertions heading line through the Verdict-rule heading line, newline
    inclusive; None when either heading is absent."""
    if text is None:
        return None
    lines = text.splitlines(keepends=True)
    a = v = None
    for i, line in enumerate(lines):
        s = line.rstrip("\r\n")
        if a is None and s == FROZEN_START:
            a = i
        elif a is not None and s == FROZEN_END:
            v = i
            break
    if a is None or v is None:
        return None
    return "".join(lines[a:v + 1])


def amendment_guard(rec, res, git, root):
    """-> True when every staged hypotheses/*.md keeps its frozen span byte-identical to HEAD."""
    for p in _staged_paths(rec):
        if not HYP_SPEC_RE.match(p):
            continue
        head_text = git.show("HEAD:%s" % p)
        wt_path = os.path.join(root, p)
        if not os.path.isfile(wt_path):
            if head_text is None:
                continue                      # a new spec not yet written: nothing frozen to touch
            res.findings.append("AMENDMENT-UNVERIFIABLE: staged spec %s is absent from the worktree" % json.dumps(p))
            res.clauses["T2"] = "unverifiable(amendment guard: %s absent)" % p
            raise Route("fail-closed", "T2", "unverifiable")
        with open(wt_path, "rb") as fh:
            wt_text = fh.read().decode("utf-8", "replace")
        if head_text is None:
            continue                          # a new spec: no frozen span at HEAD
        span_head, span_wt = frozen_span(head_text), frozen_span(wt_text)
        if span_head is None and span_wt is None:
            continue
        h1 = hashlib.sha256((span_head or "").encode("utf-8")).hexdigest()
        h2 = hashlib.sha256((span_wt or "").encode("utf-8")).hexdigest()
        if h1 != h2:
            res.findings.append("FROZEN-SPAN-TOUCHED:%s (the Binary-assertions..Verdict-rule span differs from HEAD)" % p)
            return False
    return True


def clause_t2(rec, res, git, root):
    lint = _lint()
    ev = rec.get("evidence")
    recommended = rec.get("recommended")
    default = rec.get("default_on_silence")
    resolves, reason = False, None
    if ev != lint.NONE_EXISTS:
        m = lint.POINTER_RE.match(ev) if isinstance(ev, str) else None
        if not m:
            res.findings.append("EVIDENCE-UNRESOLVED: %s is not a pointer" % json.dumps(ev))
            res.clauses["T2"] = "unverifiable(evidence form)"
            raise Route("fail-closed", "T2", "unverifiable")
        path, sha, a, b = m.group("path"), m.group("sha"), int(m.group("a")), int(m.group("b"))
        content = git.show("%s:%s" % (sha, path))
        if content is None or b > len(content.splitlines()):
            res.findings.append("EVIDENCE-UNRESOLVED: %s does not resolve at HEAD (%s)" % (
                json.dumps(ev), "git show %s:%s failed" % (sha[:12], path) if content is None else "the cited lines do not exist"))
            res.clauses["T2"] = "unverifiable(EVIDENCE-UNRESOLVED)"
            raise Route("fail-closed", "T2", "unverifiable")
        resolves = True
        reason = lint.evidence_class(path, sha, a, b, content, res.ledger_rel)
    if recommended != "none" and default == recommended:
        res.evidence_ref = "zero-information: the default on silence already is the recommendation"
        res.clauses["T2"] = "yes(zero-information)"
    elif recommended != "none" and resolves and reason is None:
        res.evidence_ref = ev
        res.clauses["T2"] = "yes(evidence:%s)" % ev
    else:
        if recommended == "none":
            why = "recommended none"
        elif not resolves:
            why = "evidence none-exists with default_on_silence != recommended"
        else:
            why = "the pointer resolves but is %s" % reason
        res.findings.append("NOT-EVIDENCE-DECIDED: %s" % why)
        res.clauses["T2"] = "no(NOT-EVIDENCE-DECIDED: %s)" % why
        return False
    if not amendment_guard(rec, res, git, root):
        res.clauses["T2"] = "no(FROZEN-SPAN-TOUCHED)"
        return False
    return True


def clause_t3(rec, res, git, root):
    positives, unverifiable = [], []
    for cls in TABLE_CLASSES:
        state, why = corroborate(cls, rec, git, root)
        if state == POSITIVE:
            positives.append((cls, why))
        elif state == UNVERIFIABLE:
            unverifiable.append((cls, why))
    if unverifiable:
        for cls, why in unverifiable:
            res.findings.append("CORROBORATION-UNVERIFIABLE:%s (%s)" % (cls, why))
        res.clauses["T3"] = "unverifiable(%s)" % ", ".join(c for c, _ in unverifiable)
        raise Route("fail-closed", "T3", "unverifiable")
    if positives:
        for cls, why in positives:
            res.findings.append("EXTERNALITY-UNDECLARED:%s (%s)" % (cls, why))
        res.clauses["T3"] = "no(%s)" % ", ".join("EXTERNALITY-UNDECLARED:%s" % c for c, _ in positives)
        return False
    res.clauses["T3"] = "yes"
    return True


def streak_on_file(ledger_path):
    """-> True when the last 10 door outcomes on file are all RECORD with zero vetoes."""
    outcomes, vetoed = [], set()
    try:
        with open(ledger_path, "rb") as fh:
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "decision" and isinstance(row.get("door"), dict) and row["door"].get("outcome") in ("RECORD", "CARD"):
            outcomes.append((row.get("id"), row["door"]["outcome"]))
        elif row.get("kind") == "decision-resolution" and row.get("disposition") == "denied":
            vetoed.add(row.get("id"))
    last = outcomes[-STREAK_LEN:]
    return len(last) == STREAK_LEN and all(o == "RECORD" for _, o in last) and not any(i in vetoed for i, _ in last)


# ---------- the record and the texts ----------

def veto_open_until(rec):
    day = str(rec.get("requested_at") or rec.get("date") or "")[:10]
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        return None
    return (d + datetime.timedelta(days=VETO_WINDOW_DAYS)).isoformat()


def undo_line(rec_id, undo_class):
    if undo_class == LEDGER_UNDO:
        return "python3 scripts/decisions.py resolve %s --deny --comment 'superseding row: undo of %s (ledger-row)'" % (rec_id, rec_id)
    return "git revert --no-edit $(git log -1 --format=%%H --grep='^landing: decision-record=%s ')" % rec_id


def build_record(rec, res):
    rid = res.id
    line = undo_line(rid, res.undo_class)
    until = veto_open_until(rec)
    veto = "python3 scripts/decisions.py resolve %s --deny --comment veto" % rid
    res.record = {"kind": "decision-resolution", "id": rid, "disposition": "accepted", "chosen_options": [rec.get("recommended")],
                  "basis": RECORD_BASIS, "undo": line, "veto_open_until": until,
                  "comment": "decided by policy/no-card-for-two-way-doors on %s; undo: %s; veto: %s" % (res.evidence_ref, line, veto)}
    return line, until, veto


def audit_and_block(rec, res):
    rid = res.id
    c = res.clauses
    if res.outcome == "MALFORMED":
        res.stdout_lines = ["ADD-REFUSED\tMALFORMED\t%s\t%s" % (cl, d) for cl, d in res.defects]
        res.stdout_lines.append("Nothing was appended. A decision candidate carries per-option --undo, and --staged-artifact --evidence "
                                "--externality --recommended --default-on-silence (spend: --amount-usd).")
        res.stdout_lines.append("A card exists only for what depends on a preference or policy only the maintainer holds (%s:18-20,26-27). "
                                "Everything reversible proceeds without a card." % GRANT_CITE)
        return
    if res.outcome == "MALFORMED-BATCH":
        res.stdout_lines = ["ADD-REFUSED\tMALFORMED-BATCH\t%s\t%s" % (cl, d) for cl, d in res.defects]
        res.stdout_lines.append("File ONE candidate for the policy question; carry the per-row data in its note or a run artifact. Nothing was appended.")
        return
    tail = "\t".join("%s=%s" % (k, c.get(k, "-")) for k in ("T1", "T2", "T3", "H1"))
    if res.outcome == "RECORD":
        line, until, veto = build_record(rec, res)
        res.stdout_lines = [
            "DECISION-DOOR\t%s\tRECORD\t%s" % (rid, tail),
            "NOT A CARD. Every option is revertible, the recommendation follows from committed evidence or is already the default on silence, "
            "and nothing leaves this repository: a two-way door, and by the standing grant the lab's own decision (%s:18-20; %s:24-25)." % (GRANT_CITE, CLARITY_CITE),
            "Recorded: %s accepted=%s basis=%s; veto open until %s; undo: %s (available after the window too)." % (rid, rec.get("recommended"), RECORD_BASIS, until, line),
            "The maintainer sees it under DECIDED FOR YOU; one word vetoes it: %s (%s)." % (veto, FRAGMENT_CITE),
            "Proceed now. Do not wait on this row, and never gate a driver on it.",
        ]
        return
    # CARD
    if res.hard:
        res.stdout_lines = [
            "DECISION-DOOR\t%s\tCARD\tH1=yes(%s: %s)" % (rid, res.hard, res.corroborated_by),
            "Card rendered: the answer depends on something only the maintainer holds (%s, %s:26-27). decisions.html opens; the row surfaces every session until resolved." % (res.hard, GRANT_CITE),
        ]
        return
    streak = any(f.startswith("EVALUATOR-STREAK") for f in res.findings)
    fail_closed = any(f.startswith(("EVALUATOR-FAIL-CLOSED", "EVIDENCE-UNRESOLVED", "CORROBORATION-UNVERIFIABLE", "UNDO-UNVERIFIABLE", "AMENDMENT-UNVERIFIABLE")) for f in res.findings)
    head = "EVALUATOR-STREAK(%d consecutive records on file, zero vetoes)" % STREAK_LEN if streak else ("fail-closed" if fail_closed else "not-proven")
    res.stdout_lines = [
        "DECISION-DOOR\t%s\tCARD\t%s\t%s\t%s" % (rid, head, "; ".join(res.findings) or "-", tail),
        "Card rendered with a finding attached: the evaluator could not prove this two-way (%s). If the evidence exists, commit it and re-file with the pointer; "
        "if it does not exist, this is a hypothesis, not a question -- register it." % ("; ".join(res.findings) or "no finding"),
    ]


# ---------- run ----------

def evaluate(rec, root, ledger_path, git_timeout, inject_fault, res):
    lint = _lint()
    git = lint.Git(root, git_timeout)
    if not clause_w1(rec, res):
        return
    code, out = git.text(["rev-parse", "HEAD"])
    res.head = out.strip()[:7] if code == 0 and out.strip() else None
    if not clause_w2(rec, res, git, root, ledger_path):
        return
    if inject_fault:
        raise RuntimeError("harness fault injection (the crash seed)")
    try:
        clause_h1(rec, res, git, root)
        t1 = clause_t1(rec, res, git)
        t2 = clause_t2(rec, res, git, root)
        t3 = clause_t3(rec, res, git, root)
    except Route:
        res.outcome = "CARD"
        return
    if t1 and t2 and t3:
        if streak_on_file(ledger_path):
            res.findings.append("EVALUATOR-STREAK: the last %d door outcomes on file are all records with zero vetoes; this candidate renders" % STREAK_LEN)
            res.clauses["STREAK"] = "yes"
            res.outcome = "CARD"
            return
        res.clauses["STREAK"] = "no"
        res.outcome = "RECORD"
        return
    res.outcome = "CARD"


def run(rec, root, ledger_path=None, git_timeout=GIT_TIMEOUT_DEFAULT, inject_fault=False):
    """-> Result. Never raises: any exception or git stall routes to CARD with EVALUATOR-FAIL-CLOSED."""
    root = os.path.abspath(root)
    ledger_path = ledger_path or os.path.join(root, _lint().ledger_rel_for(root))
    res = Result(rec)
    res.ledger_rel = ledger_rel_of(root, ledger_path)
    res.evaluator = self_sha7()
    try:
        evaluate(rec, root, ledger_path, git_timeout, inject_fault, res)
    except Exception as exc:  # fail closed: a stall, a crash, an unreadable file
        lint = _LINT
        kind = "git read stalled past the %ss timeout" % git_timeout if (lint is not None and isinstance(exc, lint.GitStall)) else "%s: %s" % (type(exc).__name__, exc)
        res.findings.append("EVALUATOR-FAIL-CLOSED: %s" % kind)
        res.clauses["ROUTING"] = "fail-closed(%s)" % type(exc).__name__
        res.outcome = "CARD"
        res.record = None
        res.hard = None
    if res.outcome == "RECORD" and res.record is None:
        build_record(rec, res)
    audit_and_block(rec, res)
    return res


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    import argparse
    ap = argparse.ArgumentParser(prog="decision-door-check.py", description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--ledger")
    ap.add_argument("--git-timeout", dest="git_timeout", type=int, default=GIT_TIMEOUT_DEFAULT)
    ap.add_argument("--inject-fault", dest="inject_fault", action="store_true", help="harness fault injection (the crash seed)")
    args = ap.parse_args(argv)
    rec = json.load(sys.stdin)
    res = run(rec, args.root, args.ledger, git_timeout=args.git_timeout, inject_fault=args.inject_fault)
    for line in res.stdout_lines:
        print(line)
    print("DOOR-JSON\t%s" % json.dumps({"door": res.door_object(), "record": res.record}, sort_keys=True, ensure_ascii=False))
    return res.exit_code


# ---------- selftest: the defect matrix in a throwaway repository ----------

def _selftest():
    import shutil
    import subprocess
    import tempfile
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("DOOR-SELFTEST-PASS" if cond else "DOOR-SELFTEST-FAIL", name, (" -- " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix="door-check-selftest-")
    try:
        def sh(*a):
            return subprocess.run(["git", "-C", tmp] + list(a), capture_output=True, text=True, check=True).stdout
        subprocess.run(["git", "init", "-q", tmp], check=True)
        sh("config", "user.name", "door selftest"); sh("config", "user.email", "door@selftest.invalid"); sh("config", "commit.gpgsign", "false")
        for d in ("ledger", "research/raw", "notes", "experiments/runs/lane-x", "hypotheses", ".claude", ".github/workflows"):
            os.makedirs(os.path.join(tmp, d), exist_ok=True)
        with open(os.path.join(tmp, ".github", "workflows", "keep.yml"), "w") as fh:
            fh.write("name: keep\n")
        with open(os.path.join(tmp, "notes", "a.md"), "w") as fh:
            fh.write("one\ntwo\nthree\n")
        with open(os.path.join(tmp, "research", "raw", "2026-01-01-topic-ruling.md"), "w") as fh:
            fh.write("# Topic ruling (maintainer, verbatim)\n\nline three\nthis row stays reserved to the maintainer\nbudget: US$5 per run\n")
        with open(os.path.join(tmp, "experiments", "runs", "lane-x", "VERIFY.md"), "w") as fh:
            fh.write("# verify\n\nkept 5/5\n")
        spec = "# spec\n\n## Status\nkept\n\n## Binary assertions\n1. one\n2. two\n\n## Verdict rule\nKeep if 2/2.\n\n## Runs\n| # | Date |\n|---|---|\n| 1 | 2026-01-01 |\n"
        with open(os.path.join(tmp, "hypotheses", "spec-x.md"), "w") as fh:
            fh.write(spec)
        with open(os.path.join(tmp, "ledger", "work-ledger.jsonl"), "w") as fh:
            fh.write(json.dumps({"kind": "decision-resolution", "id": "R-1", "date": "2026-01-01", "disposition": "accepted"}) + "\n")
        with open(os.path.join(tmp, ".claude", "hook-denials.jsonl"), "w") as fh:
            fh.write(json.dumps({"ts": "2026-01-01T00:00:00Z", "command": "bash scripts/post.sh", "rule": "security-classifier"}) + "\n")
        with open(os.path.join(tmp, ".gitignore"), "w") as fh:
            fh.write(".claude/\n")
        sh("add", "-A"); sh("commit", "-qm", "init")
        head = sh("rev-parse", "HEAD").strip()
        ledger = os.path.join(tmp, "ledger", "work-ledger.jsonl")

        def card(**kw):
            base = {"kind": "decision", "id": "X-9", "class": "plan", "requested_at": "2026-01-10", "why_only_you": "only you hold it", "blocks": [],
                    "context_pointers": [], "ask": {"options": [{"label": "go", "description": "d", "undo": "git-revert"},
                                                                {"label": "hold", "description": "d", "undo": "ledger-row"}]},
                    "staged_artifact": ["notes/a.md"], "evidence": "none-exists", "externality": "none",
                    "recommended": "go", "default_on_silence": "go"}
            for k, v in kw.items():
                if v is None:
                    base.pop(k, None)
                else:
                    base[k] = v
            return base

        def undo(c, i, v):
            c["ask"]["options"][i]["undo"] = v
            return c

        def briefed(c):
            """decision briefs: the compliant brief W1's seat (decision_card_lint.shape_errors, rules B0/B1) requires on
            every candidate, built from the card's own facts so its card_sha matches whatever the test changed."""
            undo_text = {"git-revert": "Reverting the landing commit restores the previous state.",
                         "ledger-row": "A later resolution row supersedes this one.",
                         "none": "cannot be undone: the effect stays.",
                         "flag": "Turning the flag back restores the previous state.",
                         "amendment": "A later amendment restores the previous state."}
            opts = c.get("ask", {}).get("options", [])
            dflt = c.get("default_on_silence")
            c["brief"] = {"decide": "Decide which of the %d options this card takes." % len(opts),
                          "situation": "A small change is ready and nothing else depends on it.",
                          "yours_because": "You hold the one part of this that no script can do.",
                          "choices": [{"label": o.get("label"), "in_practice": "The %s option happens next." % o.get("label"),
                                       "undo": undo_text.get(o.get("undo"), "A later resolution row supersedes this one.")} for o in opts],
                          "if_nothing": ("Nothing changes: the card stays open until you answer." if dflt == "nothing-changes"
                                         else "Unanswered by 2026-01-24, %s happens." % dflt),
                          "evidence_line": "The selftest built this card and nothing else is on record.",
                          "terms": {}, "sources": ["pipeline-fact:card-stays-open"],
                          "card_sha": _lint().card_sha(c), "provenance": {"protocol": "inline"}}
            return c

        def go(c, timeout=20, fault=False, brief=True):
            return run(briefed(c) if brief else c, tmp, ledger, git_timeout=timeout, inject_fault=fault)

        r = go(card()); ok("clean-zero-information-records", r.outcome == "RECORD" and r.record["basis"] == RECORD_BASIS and r.record["veto_open_until"] == "2026-01-17"
                             and r.stdout_lines[0].startswith("DECISION-DOOR\tX-9\tRECORD\t") and r.stdout_lines[-1] == "Proceed now. Do not wait on this row, and never gate a driver on it.", str(r.clauses))
        ok("record-undo-line-revert", r.record["undo"].startswith("git revert --no-edit $(git log -1 --format=%H --grep='^landing: decision-record=X-9 ')"), r.record["undo"])
        r = go(card(evidence="experiments/runs/lane-x/VERIFY.md@%s#L1-L3" % head, default_on_silence="hold")); ok("evidence-decided-records", r.outcome == "RECORD" and r.clauses["T2"].startswith("yes(evidence:"), str(r.clauses))
        r = go(card(evidence="notes/a.md@%s#L1-L2" % head, default_on_silence="hold")); ok("lab-authority-not-evidence-cards", r.outcome == "CARD" and any(f.startswith("NOT-EVIDENCE-DECIDED") for f in r.findings), str(r.findings))
        r = go(card(recommended="none", staged_artifact="none", default_on_silence="hold")); ok("recommended-none-cards", r.outcome == "CARD" and any("recommended none" in f for f in r.findings))
        r = go(card(evidence="notes/a.md@%s#L1-L2" % ("0" * 40), default_on_silence="go")); ok("dangling-pointer-fails-closed-whatever-the-default", r.outcome == "CARD" and any(f.startswith("EVIDENCE-UNRESOLVED") for f in r.findings), str(r.findings))
        r = go(card(evidence="experiments/runs/lane-x/VERIFY.md@%s#L1-L99" % head)); ok("lines-beyond-eof-fail-closed", r.outcome == "CARD" and any(f.startswith("EVIDENCE-UNRESOLVED") for f in r.findings))
        r = go(card(staged_artifact="gh release create v1 --repo getfatday/hyp-machine", externality="external-publication")); ok("hard-publication-cards", r.outcome == "CARD" and r.hard == "external-publication", str(r.clauses))
        r = go(card(staged_artifact="gpg --sign x", externality="physical-act")); ok("hard-physical-cards", r.outcome == "CARD" and r.hard == "physical-act")
        r = go(card(staged_artifact="bash scripts/post.sh", externality="classifier-flagged")); ok("hard-classifier-cards-from-dot-claude-record", r.outcome == "CARD" and r.hard == "classifier-flagged", str(r.clauses))
        r = go(card(staged_artifact="bash scripts/other.sh", externality="classifier-flagged")); ok("declared-classifier-no-row-uncorroborated-continues", r.outcome == "CARD" and any(f.startswith("DECLARED-HARD-UNCORROBORATED:classifier-flagged") for f in r.findings) and any(f.startswith("UNDO-UNVERIFIABLE") for f in r.findings) and "T3" in r.clauses, str(r.findings))
        r = go(card(externality="reserved-in-his-words", context_pointers=["research/raw/2026-01-01-topic-ruling.md:4"])); ok("hard-reserved-cards", r.outcome == "CARD" and r.hard == "reserved-in-his-words")
        r = go(card(**{"class": "spend", "amount_usd": 6, "externality": "spend-beyond-granted-budget", "context_pointers": ["research/raw/2026-01-01-topic-ruling.md:5"]})); ok("hard-spend-beyond-cards", r.outcome == "CARD" and r.hard == "spend-beyond-granted-budget")
        r = go(card(**{"class": "spend", "amount_usd": 6, "externality": "spend-beyond-granted-budget"})); ok("declared-spend-no-line-records-with-finding", r.outcome == "RECORD" and any(f.startswith("DECLARED-HARD-UNCORROBORATED:spend-beyond-granted-budget") for f in r.findings), str(r.findings))
        r = go(card(staged_artifact="gh issue comment 1 --repo acme/other --body hi", externality="other-humans")); ok("hard-other-humans-cards", r.outcome == "CARD" and r.hard == "other-humans")
        r = go(card(staged_artifact="curl https://api.example.org/ping")); ok("external-host-undeclared-cards", r.outcome == "CARD" and any(f.startswith("EXTERNALITY-UNDECLARED:other-humans") for f in r.findings), str(r.findings))
        r = go(undo(card(), 0, "none")); ok("undo-none-on-tracked-cards-misdeclared", r.outcome == "CARD" and any(f.startswith("UNDO-MISDECLARED") for f in r.findings), str(r.findings))
        r = go(undo(card(), 1, "none")); ok("undo-none-other-option-cards", r.outcome == "CARD" and any(f.startswith("UNDO-NONE") for f in r.findings))
        r = go(card(staged_artifact="make all")); ok("revertible-on-command-fails-closed", r.outcome == "CARD" and any(f.startswith("UNDO-UNVERIFIABLE") for f in r.findings) and r.clauses.get("T3") == "yes", str(r.findings))
        r = go(card(staged_artifact=["../elsewhere/x.md"])); ok("outside-worktree-uncorroborated", r.outcome == "CARD" and any(f.startswith("UNDO-UNCORROBORATED") for f in r.findings))
        r = go(undo(card(staged_artifact=["ledger/work-ledger.jsonl"]), 0, "ledger-row")); ok("ledger-row-records", r.outcome == "RECORD" and r.undo_class == "ledger-row" and r.record["undo"].startswith("python3 scripts/decisions.py resolve X-9 --deny"), str(r.clauses))
        r = go(undo(card(), 0, "ledger-row")); ok("ledger-row-on-other-path-uncorroborated", r.outcome == "CARD" and any(f.startswith("UNDO-UNCORROBORATED") for f in r.findings))
        r = go(card(staged_artifact=[".github/workflows/x.yml"])); ok("github-target-undeclared-publication", r.outcome == "CARD" and any(f.startswith("EXTERNALITY-UNDECLARED:external-publication") for f in r.findings), str(r.findings))
        r = go(card(staged_artifact=[".github/workflows/x.yml", "experiments/runs/lane-x/SHIP.md"])); ok("github-target-under-ship-gate-records", r.outcome == "RECORD", str(r.findings))
        # amendment guard: an edit outside the span records; inside the span cards
        spec_path = os.path.join(tmp, "hypotheses", "spec-x.md")
        with open(spec_path, "w") as fh:
            fh.write(spec.replace("Keep if 2/2.", "Keep if 2/2. Amendment 1: disclosed."))
        r = go(undo(card(staged_artifact=["hypotheses/spec-x.md"]), 0, "amendment")); ok("amendment-outside-span-records", r.outcome == "RECORD" and r.undo_class == "amendment", str(r.findings))
        with open(spec_path, "w") as fh:
            fh.write(spec.replace("1. one", "1. one (touched)"))
        r = go(undo(card(staged_artifact=["hypotheses/spec-x.md"]), 0, "amendment")); ok("span-touched-cards", r.outcome == "CARD" and any(f.startswith("FROZEN-SPAN-TOUCHED") for f in r.findings), str(r.findings))
        with open(spec_path, "w") as fh:
            fh.write(spec)
        r = go(card(staged_artifact=["hypotheses/spec-new.md"])); ok("new-spec-no-frozen-span-records", r.outcome == "RECORD", str(r.findings))
        # W1 / W2
        r = go(card(), brief=False); ok("w1-brief-missing-refused", r.outcome == "MALFORMED" and r.exit_code == 2 and any(d.startswith("B0 ") for _c, d in r.defects) and r.stdout_lines[0].startswith("ADD-REFUSED\tMALFORMED\tW1\tB0 "), str(r.defects))
        r = go(card(evidence=None)); ok("w1-malformed-lists-defects", r.outcome == "MALFORMED" and r.exit_code == 2 and r.stdout_lines[0].startswith("ADD-REFUSED\tMALFORMED\tW1\t"), str(r.defects))
        with open(ledger, "a") as fh:
            for i in (2, 3):
                fh.write(json.dumps({"kind": "decision", "id": "X-%d" % i, "why_only_you": "twin"}) + "\n")
        r = go(card(why_only_you="twin")); ok("w2-third-of-batch-refused", r.outcome == "MALFORMED-BATCH" and r.exit_code == 2 and r.stdout_lines[0].startswith("ADD-REFUSED\tMALFORMED-BATCH\tW2\t"))
        with open(ledger, "a") as fh:
            fh.write(json.dumps({"kind": "decision", "id": "X-4", "why_only_you": "single"}) + "\n")
        r = go(card(why_only_you="single")); ok("w2-second-passes", r.outcome == "RECORD", str(r.clauses))
        sh("checkout", "-q", "--", "ledger/work-ledger.jsonl")
        # fail-closed wrapper
        r = go(card(), timeout=0); ok("stall-fails-closed", r.outcome == "CARD" and any(f.startswith("EVALUATOR-FAIL-CLOSED") and "stalled" in f for f in r.findings), str(r.findings))
        r = go(card(), fault=True); ok("injected-fault-fails-closed", r.outcome == "CARD" and any(f.startswith("EVALUATOR-FAIL-CLOSED") for f in r.findings) and r.record is None, str(r.findings))
        with open(os.path.join(tmp, ".claude", "hook-denials.jsonl"), "a") as fh:
            fh.write("{not json\n")
        r = go(card()); ok("unparseable-denial-row-fails-closed", r.outcome == "CARD" and any(f.startswith("CORROBORATION-UNVERIFIABLE:classifier-flagged") for f in r.findings), str(r.findings))
        with open(os.path.join(tmp, ".claude", "hook-denials.jsonl"), "w") as fh:
            fh.write(json.dumps({"ts": "t", "command": "bash scripts/post.sh", "rule": "security-classifier"}) + "\n")
        # STREAK: ten records on file then the eleventh renders; a veto or a card breaks it
        rows = []
        for i in range(10):
            rows.append(json.dumps({"kind": "decision", "id": "S-%d" % i, "door": {"outcome": "RECORD"}}))
            rows.append(json.dumps({"kind": "decision-resolution", "id": "S-%d" % i, "disposition": "accepted", "basis": RECORD_BASIS}))
        with open(ledger, "a") as fh:
            fh.write("\n".join(rows) + "\n")
        r = go(card(why_only_you="eleventh")); ok("streak-eleventh-cards", r.outcome == "CARD" and any(f.startswith("EVALUATOR-STREAK") for f in r.findings) and r.stdout_lines[0].split("\t")[3].startswith("EVALUATOR-STREAK"), str(r.findings))
        with open(ledger, "a") as fh:
            fh.write(json.dumps({"kind": "decision-resolution", "id": "S-3", "disposition": "denied", "comment": "veto"}) + "\n")
        r = go(card(why_only_you="after-veto")); ok("veto-breaks-streak", r.outcome == "RECORD", str(r.findings))
        sh("checkout", "-q", "--", "ledger/work-ledger.jsonl")
        # determinism + the manipulation stamp
        a, b = go(card()), go(card()); ok("two-runs-identical", (a.outcome, a.clauses, a.findings, a.record, a.stdout_lines) == (b.outcome, b.clauses, b.findings, b.record, b.stdout_lines))
        ok("evaluator-sha7-stamped", len(a.evaluator) == 7 and a.door_object()["evaluator"] == a.evaluator and len(a.head) == 7)
        # plugin port: with no ledger handed in the ledger is the consumer's (.claude/hyp.json ledger_file), and a
        # ledger-row undo corroborates against THAT path -- never the lab's
        os.makedirs(os.path.join(tmp, ".claude"), exist_ok=True)
        with open(os.path.join(tmp, ".claude", "hyp.json"), "w") as fh:
            json.dump({"ledger_file": "ledger/ledger.jsonl"}, fh)
        with open(os.path.join(tmp, "ledger", "ledger.jsonl"), "w") as fh:
            fh.write("")
        sh("add", "-A"); sh("commit", "-qm", "consumer ledger")
        r = run(briefed(undo(card(staged_artifact=["ledger/ledger.jsonl"]), 0, "ledger-row")), tmp); ok("consumer-ledger-row-records-through-hyp-json", r.outcome == "RECORD" and r.ledger_rel == "ledger/ledger.jsonl" and r.undo_class == "ledger-row" and r.record["undo"].startswith("python3 scripts/decisions.py resolve X-9 --deny"), str(r.clauses))
        r = run(briefed(undo(card(staged_artifact=["ledger/work-ledger.jsonl"]), 0, "ledger-row")), tmp); ok("consumer-ledger-row-on-lab-path-uncorroborated", r.outcome == "CARD" and any(f.startswith("UNDO-UNCORROBORATED") for f in r.findings), str(r.findings))
        os.remove(os.path.join(tmp, ".claude", "hyp.json"))
        # the CLI contract
        me = os.path.abspath(__file__)
        p = subprocess.run([sys.executable, "-B", me, "--root", tmp, "--ledger", ledger], input=json.dumps(briefed(card())), capture_output=True, text=True)
        last = p.stdout.strip().splitlines()[-1]
        ok("cli-contract", p.returncode == 0 and p.stdout.startswith("DECISION-DOOR\t") and last.startswith("DOOR-JSON\t") and json.loads(last.split("\t", 1)[1])["door"]["outcome"] == "RECORD", p.stdout[:120] + p.stderr[-200:])
        p = subprocess.run([sys.executable, "-B", me, "--root", tmp, "--ledger", ledger, "--inject-fault"], input=json.dumps(briefed(card())), capture_output=True, text=True)
        ok("cli-inject-fault-cards", p.returncode == 0 and "\tCARD\tfail-closed\t" in p.stdout.splitlines()[0], p.stdout[:160])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("door-selftest: %d failure(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
