#!/usr/bin/env python3
"""decisions.py — the decision-ledger CLI (the hyp decision kit).

PROVENANCE — port of the source lab's scripts/decisions.py (decision kit, landed
there 2026-08-28 under the consolidated decision-making directive; --selftest
runs the full add -> surface-once -> resolve -> check -> attribution loop in a
throwaway git repo and is the port's own proof). Differences from the lab copy:
the ledger and raw-capture paths resolve through .claude/hyp.json (ledger_file,
default ledger/ledger.jsonl; raw_dir, default research/raw), and the proactive
opener / dashboard compiler are found in the consumer repo's scripts/ first,
then beside this script (plugin home). Command surface and store semantics are
identical.

Store contract: docs/decisions.md (shipped with the kit). One store — the
configured work ledger, append-only. A decision is one kind:"decision" row; its
status is DERIVED by joining kind:"decision-resolution" rows on id at read time.
decided_by / decided_at / resolution_commit are NEVER stored: they derive from
the git commit that introduced the resolution line (the lab's H-084 keep + its
name-neutrality ruling: attribution resolves through git, never stored names).

Commands
  add       append one validated decision row (id race-checked: max-on-file+1), then run
            scripts/proactive-open.sh (recompile + open-once + notify). The compiler NEVER
            calls the opener; only add/surface do.
  list      one line per decision with derived status (join, no git).
  show ID   the full card, with git-derived resolution provenance.
  resolve   append the decision-resolution row and commit JUST that line under the
            invoker's git identity, message: decision: <id> <disposition> —
            decision-resolved=<id>. When the decision `shadows` legacy maintainer-ruling
            brackets, ALSO emits the research/raw/<date>-<arg>-ruling.md capture (its own
            follow-up commit, so the resolution commit stays single-line). Recompiles the
            dashboard (DASHBOARD.md + decisions.html ride the session's next attributed
            commit, the standing dashboard-commit-policy). Opens NOTHING.
            Compat shim: `resolve --legacy <arg>` answers a legacy maintainer-ruling
            bracket that has no decision row — emits + commits the ruling capture only.
  check     validate every decision/resolution row against the schema; report open/closed;
            exit 1 on violations (land gate + selftest "check closes" assertion). Also prints
            two EXIT-NEUTRAL report classes that never count as findings (decision-retest-when
            lane): RETEST-DUE for an accepted/denied decision whose `retest_when` evidence
            predicate holds at committed HEAD (one line, with the evidence pointer), and
            REVISIT-UNARMED for a decision whose scanned text says revisit/later while the row
            carries no `retest_when` (the wait lives only in prose that memory must re-find).
  surface   print the open-decision surface (per-row lines + count/oldest-age summary,
            resolver grammar) and run proactive-open.sh (once-per-id guard inside).
  open      open decisions.html front-and-center (--all also opens DASHBOARD.md).
  migrate   compat shim — delegates to scripts/migrate-decisions.py (a lab-side
            migration tool, not shipped with the plugin) when your repo carries one.

--selftest runs the end-to-end loop in a throwaway git repo + ledger: add -> list ->
surface-once guard -> resolve -> check closes -> attribution from git -> the retest_when
scenario (an unknown predicate fails validation with one typed error; an armed row fires
RETEST-DUE only after its evidence COMMIT, never on an uncommitted append; a "later" option
with no trigger is REVISIT-UNARMED and exit-neutral). Writes nothing outside its tempdir;
exits 0 only if every assertion passes.

retest_when (optional field on kind:"decision" rows): `<predicate>=<argument>` in the shared
retest-when grammar of scripts/closes_when.py (event-count | metric-crosses |
evidence-received; that module is the ONLY parser -- nothing here re-implements it). Evidence,
never a date: the row is re-presented when committed evidence satisfies the predicate.

door fields (decision-card-door-fields lane): every decision candidate carries six structured
fields -- per-option --undo, and --staged-artifact --evidence --externality --recommended
--default-on-silence (plus --amount-usd when --class spend) -- linted at add time by
scripts/decision_card_lint.py (rules D0-D9) under preflight's exit contract: 0 PASS (appended),
1 ESCALATE (appended with the finding under door.findings; renders as today), 2 MALFORMED
(nothing appended; every missing or invalid field listed). Legacy rows -- cards appended before the
fields existed -- are exempt and never re-validated; the boundary is the consumer's (.claude/hyp.json
decision_door_legacy_max_id, an int; absent: shape + order -- every row before the first row carrying
a door object). `check` re-validates only the fields' shape on the other rows. Every writer of a
kind:"decision" row passes the lint (door_lint_row) and append_line refuses a row without its door stamp.

door evaluator (decision-door-evaluator lane): after the lint, scripts/decision_door_check.py evaluates
the candidate between validate and append -- W1-W2, H1, T1-T3, STREAK and the routing -- and either RECORDS
it (door.outcome RECORD: the row lands with one kind:"decision-resolution" row, basis two-way-door, a
veto window and a one-line undo; no proactive open; the lane proceeds) or lets it render as a CARD carrying
the findings (door.outcome CARD; proactive-open runs as today). Anything unverifiable, a stall or a crash
renders (fail-closed). `resolve --deny` on a recorded id is the veto: the deny wins the join and the
record's undo line runs as an attributed follow-up. `check` reports DOOR-UNAUDITED (exit-neutral) for a
non-legacy decision row that carries no door outcome (a side-door row). Every writer passes the evaluator
too (door_evaluate_row after door_lint_row; on RECORD it appends door_record_row and opens nothing).

Stdlib only. Never touches anything outside the ledger append and the write-once
ruling capture ADDITIONS under the configured raw dir that `shadows` requires
(create-only, never edit).
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

DEFAULT_LEDGER_REL = os.path.join("ledger", "ledger.jsonl")
DEFAULT_RAW_DIR = os.path.join("research", "raw")


def _hyp_config(root):
    """ledger_file + raw_dir from <root>/.claude/hyp.json; hyp defaults on any
    failure. Never raises."""
    cfg = {"ledger_file": DEFAULT_LEDGER_REL.replace(os.sep, "/"),
           "raw_dir": DEFAULT_RAW_DIR.replace(os.sep, "/")}
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in cfg:
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    cfg[key] = val.strip().strip("/")
    except (OSError, ValueError):
        pass
    return cfg


def ledger_rel_for(root):
    return _hyp_config(root)["ledger_file"].replace("/", os.sep)


def raw_dir_for(root):
    return _hyp_config(root)["raw_dir"].replace("/", os.sep)
LEGACY_KINDS = ("intent", "amendment", "commitment", "directive")
URGENCY = ("high", "normal", "low")
# rule-retest (H-249 keep): filed when rule-lint.py reports RULE-EXPIRED on a
# ledger/rules-registry.jsonl row — the retest runs as a counted lane and its
# verdict flips the registry by appended row (KEEP-RULE new retest_by / RETIRE-RULE
# status:retired); dedup on rule id, one open row per expired rule.
CLASSES = ("publish", "spend", "schema", "live-surface", "plan", "hygiene",
           "rule-retest")
DISPOSITIONS = ("accepted", "denied", "commented")
FORBIDDEN_RESOLUTION_FIELDS = ("decided_by", "decided_at", "resolution_commit")
ID_RE = re.compile(r"^DEC-(\d{3,})$")
GIT_TIMEOUT = 20
# retest_when (decision-retest-when lane): the optional evidence trigger on decision rows and
# the revisit-prose lint. The grammar and the HEAD evaluation live in scripts/closes_when.py
# (the shared close-condition module, sibling of this file); imported lazily so a checker
# without that module still validates every row that carries no retest_when.
RETEST_WHEN_FIELD = "retest_when"
REVISIT_RE = re.compile(r"\b(revisit|later)\b", re.IGNORECASE)
RETEST_DUE_CLASS = "RETEST-DUE"
REVISIT_UNARMED_CLASS = "REVISIT-UNARMED"
_RW_POINTER_RE = re.compile(r"^(.+)@([0-9a-f]{40})#L(\d+)-L(\d+)$")
_CLOSES_WHEN = None
# door fields (decision-card-door-fields lane): the six structured fields on every candidate are linted
# by scripts/decision_card_lint.py (rules D0-D9). Legacy rows -- appended before the fields existed -- are
# exempt from check's re-validation. The boundary is the consumer's: .claude/hyp.json
# decision_door_legacy_max_id (an int; rows with a numeric id at or below it are legacy) or, when the key
# is absent, shape + order (the rows before the first row that carries a door object). The lint carries
# no id literal, so the boundary lives here.
DOOR_LEGACY_KEY = "decision_door_legacy_max_id"
DOOR_GIT_TIMEOUT = 20
DOOR_SELFTEST_ARGS = ["--undo", "git-revert", "--undo", "ledger-row", "--staged-artifact", "README.md",
                      "--evidence", "none-exists", "--externality", "none", "--recommended", "none",
                      "--default-on-silence", "nothing-changes"]
_DOOR_LINT = None
DOOR_RECORD_BASIS = "two-way-door"
_DOOR_EVAL = None


def _door_evaluator():
    """scripts/decision_door_check.py imported from beside this file (the door evaluator: RECORD or CARD)."""
    global _DOOR_EVAL
    if _DOOR_EVAL is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            import decision_door_check  # noqa: the door evaluator
        except ImportError:
            raise SystemExit("FATAL: scripts/decision_door_check.py (the door evaluator) is not beside decisions.py")
        _DOOR_EVAL = decision_door_check
    return _DOOR_EVAL


def door_evaluate_row(rec, root, ledger=None, git_timeout=DOOR_GIT_TIMEOUT, inject_fault=False):
    """The door evaluator over one lint-stamped candidate -- the second gate every writer of a kind:"decision"
    row passes after door_lint_row: `add`, and the callers that append through append_line (dispatch-gate.py,
    reflex-surface). Routes RECORD or CARD, fail-closed; no bypass flag exists. On exit_code 0 the row's door
    object gains outcome, evaluator, head, clauses (hard + corroborated_by on a hard card) and the evaluator's
    findings after the lint's; on exit_code 2 (MALFORMED | MALFORMED-BATCH) nothing is stamped and the caller
    appends nothing. -> the evaluator's Result (.outcome, .exit_code, .stdout_lines, .record, .defects). On
    RECORD the caller appends door_record_row(result) right after the row and opens nothing."""
    door = _door_evaluator().run(rec, root, ledger or os.path.join(root, ledger_rel_for(root)),
                                 git_timeout=git_timeout, inject_fault=inject_fault)
    if door.exit_code == 2:
        return door
    obj = door.door_object()
    if not isinstance(rec.get("door"), dict):
        rec["door"] = {}
    merged = list(rec["door"].get("findings", [])) + list(obj.pop("findings", []))
    rec["door"].update(obj)
    if merged:
        rec["door"]["findings"] = merged
    else:
        rec["door"].pop("findings", None)
    return door


def door_record_row(door):
    """The kind:"decision-resolution" row a RECORD lands beside its decision row (disposition accepted,
    basis two-way-door, the veto window, the one-line undo); `date` is stamped here -- the evaluator reads
    no clock."""
    row = dict(door.record)
    row["date"] = today_str()
    return row


def _door_record_row(chain):
    """The latest closing resolution when it is an accepted two-way-door record (a veto target), else None."""
    closing = [r for r in sorted(chain, key=lambda r: r["order"]) if r["disposition"] in ("accepted", "denied")]
    if closing and closing[-1]["disposition"] == "accepted" and closing[-1]["rec"].get("basis") == DOOR_RECORD_BASIS:
        return closing[-1]["rec"]
    return None


def _door_execute_undo(root, dec_id, record_row, committing):
    """The veto's second half: the deny row has won the join; the record's undo line runs as an attributed
    follow-up (a ledger-row record's undo IS the superseding deny row, so nothing else runs)."""
    undo = str(record_row.get("undo") or "")
    if not undo:
        print("VETO\t%s\tno undo line on the record" % dec_id)
        return
    if undo.startswith("python3 scripts/decisions.py resolve"):
        print("VETO\t%s\tundo is this superseding row (ledger-row); nothing else to execute" % dec_id)
        return
    if not committing:
        print("VETO\t%s\tundo not executed (--no-commit): %s" % (dec_id, undo))
        return
    try:
        proc = subprocess.run(["sh", "-c", undo], cwd=root, capture_output=True, text=True, timeout=120)
        rc, tail = proc.returncode, (proc.stderr or proc.stdout).strip()[-300:]
    except (OSError, subprocess.SubprocessError) as exc:
        rc, tail = -1, str(exc)
    print("VETO\t%s\tundo executed rc=%s\t%s" % (dec_id, rc, undo))
    if rc != 0:
        print("VETO-UNDO-FAILED\t%s\t%s" % (dec_id, tail))


def _door_lint():
    """scripts/decision_card_lint.py imported from beside this file (the ON arm requires it)."""
    global _DOOR_LINT
    if _DOOR_LINT is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            import decision_card_lint  # noqa: the door-field lint (rules D0-D9)
        except ImportError:
            raise SystemExit("FATAL: scripts/decision_card_lint.py (the door-field lint) is not beside decisions.py")
        _DOOR_LINT = decision_card_lint
    return _DOOR_LINT


def door_legacy_max_id(root):
    """The explicit legacy boundary from <root>/.claude/hyp.json decision_door_legacy_max_id (an int, or a
    string of digits), else None: the boundary is then read from the ledger's shape and order."""
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    val = data.get(DOOR_LEGACY_KEY) if isinstance(data, dict) else None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return None


def legacy_decision_ids(parsed, root):
    """The decision ids `check` never re-validates for door fields. Explicit boundary (hyp.json
    decision_door_legacy_max_id): every numeric id at or below it. Otherwise shape + order: every row
    appended before the first row that carries a door object (those rows carry none by construction); a
    door-less row appended after that first row is gated. A consumer with any number of pre-upgrade cards
    upgrades cleanly either way; `add` exempts nothing."""
    max_id = door_legacy_max_id(root)
    legacy = set()
    if max_id is not None:
        for dec in parsed["decisions"]:
            m = ID_RE.match(str(dec["rec"].get("id", "")))
            if m and int(m.group(1)) <= max_id:
                legacy.add(dec["id"])
        return legacy
    for dec in sorted(parsed["decisions"], key=lambda d: d["order"]):
        if isinstance(dec["rec"].get("door"), dict):
            break
        legacy.add(dec["id"])
    return legacy


def door_shape_errors(rec):
    """The exit-2 class over the six fields (presence, vocabulary, form; no git) -- what `check` re-validates
    on non-legacy rows."""
    return ["%s %s" % (rule, detail) for rule, detail in _door_lint().shape_errors(rec)]


def _parse_amount(text):
    for cast in (int, float):
        try:
            return cast(text)
        except (TypeError, ValueError):
            pass
    return text


def door_fields_from_args(args, rec):
    """Copies the six door flags onto the candidate row. --undo pairs positionally with --option ('' = none
    given); --staged-artifact: one value 'none' -> none, one empty value -> an empty list, one value with
    whitespace -> a command line, otherwise each value is a repo-relative path; --recommended repeated ->
    a list (malformed by construction; exactly one label is expected)."""
    undos = args.undo or []
    for i, opt in enumerate(rec["ask"]["options"]):
        if i < len(undos) and undos[i] != "":
            opt["undo"] = undos[i]
    sa = args.staged_artifact
    if sa is not None:
        if len(sa) == 1 and sa[0] == "none":
            rec["staged_artifact"] = "none"
        elif len(sa) == 1 and sa[0] == "":
            rec["staged_artifact"] = []
        elif len(sa) == 1 and re.search(r"\s", sa[0]):
            rec["staged_artifact"] = sa[0]
        else:
            rec["staged_artifact"] = list(sa)
    if args.evidence is not None:
        rec["evidence"] = args.evidence
    if args.externality is not None:
        rec["externality"] = args.externality
    if args.recommended is not None:
        rec["recommended"] = args.recommended[0] if len(args.recommended) == 1 else list(args.recommended)
    if args.default_on_silence is not None:
        rec["default_on_silence"] = args.default_on_silence
    if args.amount_usd is not None:
        rec["amount_usd"] = _parse_amount(args.amount_usd)


def door_lint_row(rec, root, ledger=None, git_timeout=DOOR_GIT_TIMEOUT):
    """The door lint over one shape-valid candidate -- the one gate every writer of a kind:"decision" row
    passes: `add`, and the callers that append through append_line (dispatch-gate.py, reflex-surface). On
    PASS or ESCALATE the row is stamped with its door object (fields_sha; findings when any); on MALFORMED
    (exit_code 2) nothing is stamped and the caller appends nothing. -> the lint's result (.exit_code
    0|1|2, .malformed, .findings, .timeouts); door_audit_lines renders it as `add` prints it."""
    lint = _door_lint()
    result = lint.lint(rec, root, ledger or os.path.join(root, ledger_rel_for(root)), git_timeout=git_timeout)
    if result.exit_code != 2:
        rec["door"] = {"fields_sha": lint.fields_sha(rec)}
        if result.findings:
            rec["door"]["findings"] = ["%s:%s" % (rule, detail) for rule, detail in result.findings]
    return result


def door_audit_lines(rec, result):
    """The audit lines `add` prints for a lint result, in order: ADD-TIMEOUT per stalled rule, then either
    the ADD-REFUSED lines with the two refusal sentences (exit 2) or one ADD-FINDING line per finding."""
    lines = ["ADD-TIMEOUT\t%s" % rule for rule in result.timeouts]
    if result.exit_code == 2:
        for rule, detail in result.malformed:
            lines.append("ADD-REFUSED\t%s\t%s\t%s" % ("MALFORMED-BATCH" if rule == "D7" else "MALFORMED",
                                                     rule, detail))
        lines.append("Nothing was appended. A decision candidate carries per-option --undo, and --staged-artifact "
                     "--evidence --externality --recommended --default-on-silence (spend: --amount-usd).")
        lines.append("A card exists only for what depends on a preference or policy only the maintainer holds "
                     "(research/raw/2026-08-18-decisions-are-two-way-doors-grant.md:18-20,26-27). Everything "
                     "reversible proceeds without a card.")
        return lines
    for rule, detail in result.findings:
        lines.append("ADD-FINDING\t%s\t%s\t%s" % (rec.get("id"), rule, detail))
    return lines



def today_str():
    return os.environ.get("DECISIONS_TODAY") or datetime.date.today().isoformat()


# ---------- v3 three-shape normalizer (shared grammar; docs/decisions.md §5) ----------

def parse_ledger_v3(text):
    """-> dict(rows, decisions, resolutions, malformed). rows = legacy+v2 normalized
    ({date,slug,hit,kind,order}); decisions/resolutions keep the raw line text for the
    git introducer search. malformed counts only truly-bad lines."""
    rows, decisions, resolutions, malformed = [], [], [], 0
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(rec, dict):
            malformed += 1
            continue
        kind = rec.get("kind", "intent")
        try:
            if kind == "decision":
                decisions.append({"rec": rec, "raw": line, "order": lineno,
                                  "id": rec["id"]})
            elif kind == "decision-resolution":
                resolutions.append({"rec": rec, "raw": line, "order": lineno,
                                    "id": rec["id"],
                                    "disposition": rec["disposition"]})
            elif "slug" in rec and "hit" in rec:            # legacy shape
                if kind not in LEGACY_KINDS:
                    raise ValueError("unsupported kind")
                rows.append({"date": rec["date"], "slug": rec["slug"],
                             "hit": rec["hit"], "kind": kind, "order": lineno})
            elif "id" in rec and "text" in rec:             # v2 shape
                if kind not in LEGACY_KINDS:
                    raise ValueError("unsupported kind")
                hit = rec["text"]
                if rec.get("closes_when"):
                    hit += " [closes-when: " + rec["closes_when"] + "]"
                rows.append({"date": rec["date"], "slug": rec["id"], "hit": hit,
                             "kind": kind, "order": lineno})
            else:
                raise ValueError("no known shape")
        except (KeyError, TypeError, ValueError):
            malformed += 1
    return {"rows": rows, "decisions": decisions, "resolutions": resolutions,
            "malformed": malformed}


def read_ledger(root, ledger=None):
    path = ledger or os.path.join(root, ledger_rel_for(root))
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def join_status(decisions, resolutions):
    """-> {id: (status, [resolution dicts in file order])}."""
    by_id = {}
    for res in resolutions:
        by_id.setdefault(res["id"], []).append(res)
    joined = {}
    for dec in decisions:
        chain = sorted(by_id.get(dec["id"], []), key=lambda r: r["order"])
        closing = [r for r in chain if r["disposition"] in ("accepted", "denied")]
        status = closing[-1]["disposition"] if closing else (
            "commented" if chain else "open")
        joined[dec["id"]] = (status, chain)
    return joined


def open_decisions(parsed):
    joined = join_status(parsed["decisions"], parsed["resolutions"])
    out = []
    for dec in parsed["decisions"]:
        status, chain = joined[dec["id"]]
        if status in ("open", "commented"):
            out.append((dec, status, chain))
    return out


def age_days(row_rec, today):
    try:
        then = datetime.date.fromisoformat(
            str(row_rec.get("requested_at") or row_rec.get("date"))[:10])
        return max(0, (datetime.date.fromisoformat(today) - then).days)
    except (ValueError, TypeError):
        return 0


def sort_key(dec_status, today):
    dec, _status, _chain = dec_status
    order = {"high": 0, "normal": 1, "low": 2}
    return (order.get(dec["rec"].get("urgency"), 1),
            -age_days(dec["rec"], today), dec["id"])


# ---------- git ----------

def git(root, args, check=False):
    try:
        proc = subprocess.run(["git", "-C", root] + list(args),
                              capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        if check:
            raise SystemExit("FATAL: git %s failed: %s" % (args[:1], exc))
        return 1, "", str(exc)
    if check and proc.returncode != 0:
        raise SystemExit("FATAL: git %s failed: %s" % (" ".join(args[:2]),
                                                       proc.stderr.strip()))
    return proc.returncode, proc.stdout, proc.stderr


def derive_attribution(root, ledger_rel, resolutions):
    """Attach decided_by/decided_at/resolution_commit (or staged=True). Append-only store
    => presence is monotone => binary search over ledger-touching commits."""
    code, out, _ = git(root, ["log", "--reverse", "--format=%H\x1f%an\x1f%aI",
                              "--", ledger_rel])
    commits = ([tuple(l.split("\x1f")) for l in out.splitlines()
                if l.count("\x1f") == 2] if code == 0 else [])
    cache = {}

    def blob(sha):
        if sha not in cache:
            c, b, _ = git(root, ["show", "%s:%s" % (sha, ledger_rel)])
            cache[sha] = b if c == 0 else ""
        return cache[sha]

    for res in resolutions:
        res["staged"] = True
        res["decided_by"] = res["decided_at"] = res["resolution_commit"] = None
        if not commits or res["raw"] not in blob(commits[-1][0]):
            continue
        lo, hi = 0, len(commits) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if res["raw"] in blob(commits[mid][0]):
                hi = mid
            else:
                lo = mid + 1
        sha, author, when = commits[lo]
        res.update({"staged": False, "decided_by": author, "decided_at": when,
                    "resolution_commit": sha[:7]})


# ---------- retest_when: evidence trigger + revisit lint (decision-retest-when lane) ----------

def _shared_parser():
    """scripts/closes_when.py imported from beside this file; None when it is absent."""
    global _CLOSES_WHEN
    if _CLOSES_WHEN is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            import closes_when  # noqa: the shared grammar module
            _CLOSES_WHEN = closes_when
        except ImportError:
            _CLOSES_WHEN = False
    return _CLOSES_WHEN or None


def validate_retest_when(value):
    """-> [] for a well-formed `<predicate>=<argument>`, else EXACTLY ONE error string that
    names retest_when (and the offending predicate when it is unknown)."""
    if not isinstance(value, str) or not value.strip():
        return ["retest_when must be a non-empty '<predicate>=<argument>' string, got %r"
                % (value,)]
    cw = _shared_parser()
    if cw is None:
        return ["retest_when %r cannot be validated: scripts/closes_when.py (the shared "
                "retest-when parser) is not beside decisions.py" % value]
    predicate, _sep, argument = value.strip().partition("=")
    if predicate not in cw.RETEST_WHEN_PREDICATES:
        return ["retest_when %r: unknown predicate %r (known: %s)"
                % (value, predicate, "|".join(cw.RETEST_WHEN_PREDICATES))]
    if not argument.strip():
        return ["retest_when %r: empty argument for %s" % (value, predicate)]
    if cw.parse_retest_when_field(value) is None:
        return ["retest_when %r: argument %r fails the %s grammar (scripts/closes_when.py)"
                % (value, argument, predicate)]
    return []


def closing_resolution(chain):
    closing = [r for r in chain if r.get("disposition") in ("accepted", "denied")]
    return closing[-1] if closing else None


def revisit_scan_fields(rec, status, closing):
    """The lint's field scope: while OPEN (open/commented) -> title, ask.question, every
    option's label and description; once CLOSED (accepted/denied) -> only the chosen_options
    (each chosen text resolved to its option's label + description when it names an option,
    else the chosen text itself) plus the closing resolution's comment. -> [(path, text)]."""
    fields = []
    ask = rec.get("ask") if isinstance(rec.get("ask"), dict) else {}
    opts = ask.get("options") if isinstance(ask.get("options"), list) else []
    if status in ("open", "commented") or closing is None:
        fields.append(("title", rec.get("title")))
        fields.append(("ask.question", ask.get("question")))
        for i, opt in enumerate(opts):
            if isinstance(opt, dict):
                fields.append(("ask.options[%d].label" % i, opt.get("label")))
                fields.append(("ask.options[%d].description" % i, opt.get("description")))
    else:
        res = closing["rec"]
        for j, chosen in enumerate(res.get("chosen_options") or []):
            idx = next((i for i, o in enumerate(opts)
                        if isinstance(o, dict) and o.get("label") == chosen), None)
            if idx is None:
                fields.append(("resolution.chosen_options[%d]" % j, chosen))
            else:
                fields.append(("ask.options[%d].label" % idx, opts[idx].get("label")))
                fields.append(("ask.options[%d].description" % idx,
                               opts[idx].get("description")))
        fields.append(("resolution.comment", res.get("comment")))
    return [(path, text) for path, text in fields if isinstance(text, str)]


def revisit_unarmed_fields(rec, status, chain):
    """Comma-joined, order-preserving matching field paths, or '' when the row is armed
    (carries retest_when) or nothing matches."""
    if RETEST_WHEN_FIELD in rec:
        return ""
    hits = [path for path, text in revisit_scan_fields(rec, status, closing_resolution(chain))
            if REVISIT_RE.search(text)]
    return ",".join(hits)


def retest_pointer(pointers):
    """The shared module returns `<path>@<sha40>#L<a>-L<b>` spans; the report line carries ONE
    line pointer, the last line of the last span (the row that completed the evidence)."""
    if not pointers:
        return "-"
    m = _RW_POINTER_RE.match(pointers[-1])
    if not m:
        return pointers[-1]
    return "%s@%s#L%s" % (m.group(1), m.group(2), m.group(4))


def retest_due(root, rec, status):
    """-> '<pointer>\t<predicate>=<argument>' when this accepted/denied decision's
    retest_when holds at committed HEAD of root, else None. Read-only."""
    if status not in ("accepted", "denied"):
        return None
    value = rec.get(RETEST_WHEN_FIELD)
    cw = _shared_parser()
    if not isinstance(value, str) or cw is None:
        return None
    parsed = cw.parse_retest_when_field(value)
    if parsed is None:
        return None
    holds, pointers = cw.retest_when_evidence(parsed[0], parsed[1], root)
    if not holds:
        return None
    return "%s\t%s=%s" % (retest_pointer(pointers), parsed[0], parsed[1])


# ---------- validation (check + add both use it) ----------

def validate_decision(rec):
    errs = []
    rid = rec.get("id", "")
    if not ID_RE.match(str(rid)):
        errs.append("id %r is not DEC-NNN" % (rid,))
    for field in ("date", "title", "requested_by", "why_only_you"):
        if not str(rec.get(field) or "").strip():
            errs.append("missing %s" % field)
    if rec.get("urgency") not in URGENCY:
        errs.append("urgency %r not in %s" % (rec.get("urgency"), "|".join(URGENCY)))
    if rec.get("class") not in CLASSES:
        errs.append("class %r not in %s" % (rec.get("class"), "|".join(CLASSES)))
    ask = rec.get("ask")
    if not isinstance(ask, dict):
        errs.append("ask missing")
    else:
        if not str(ask.get("question") or "").strip():
            errs.append("ask.question missing")
        header = str(ask.get("header") or "")
        if not header or len(header) > 12:
            errs.append("ask.header %r must be 1-12 chars" % header)
        if not isinstance(ask.get("multiSelect"), bool):
            errs.append("ask.multiSelect must be a bool")
        opts = ask.get("options")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
            errs.append("ask.options must hold 2-4 options")
        else:
            for i, opt in enumerate(opts):
                if not isinstance(opt, dict) or not str(opt.get("label") or "").strip() \
                        or not str(opt.get("description") or "").strip():
                    errs.append("option %d needs label + description" % (i + 1))
    if RETEST_WHEN_FIELD in rec:
        errs.extend(validate_retest_when(rec[RETEST_WHEN_FIELD]))
    for field in FORBIDDEN_RESOLUTION_FIELDS:
        if field in rec:
            errs.append("%s must never be stored (derived from git, H-084)" % field)
    return errs


def validate_resolution(rec, known_ids):
    errs = []
    if rec.get("id") not in known_ids:
        errs.append("resolution for unknown decision %r" % rec.get("id"))
    if rec.get("disposition") not in DISPOSITIONS:
        errs.append("disposition %r not in %s" % (rec.get("disposition"),
                                                  "|".join(DISPOSITIONS)))
    if rec.get("disposition") == "accepted" and not rec.get("chosen_options") \
            and not str(rec.get("comment") or "").strip():
        errs.append("accepted with neither chosen_options nor comment text")
    for field in FORBIDDEN_RESOLUTION_FIELDS:
        if field in rec:
            errs.append("%s must never be stored (derived from git, H-084)" % field)
    return errs


# ---------- append (race-checked) ----------

def next_free_id(parsed):
    mx = 0
    for dec in parsed["decisions"]:
        m = ID_RE.match(str(dec["id"]))
        if m:
            mx = max(mx, int(m.group(1)))
    return "DEC-%03d" % (mx + 1)


def append_line(root, rec, ledger=None):
    """Re-reads the file immediately before the append (race check on id collision for
    decision rows)."""
    path = ledger or os.path.join(root, ledger_rel_for(root))
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    if rec.get("kind") == "decision":
        if not isinstance(rec.get("door"), dict):
            raise SystemExit("FATAL: decision row %s carries no door object -- every writer runs door_lint_row() "
                             "before appending (add does; a MALFORMED card is never appended)" % rec.get("id"))
        if any(d["id"] == rec["id"] for d in parsed["decisions"]):
            raise SystemExit("FATAL: id %s already on file (race check)" % rec["id"])
    line = json.dumps(rec, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line


# ---------- proactive opener (add/surface ONLY; the compiler never calls this) ----------

def run_proactive(root):
    script = os.path.join(root, "scripts", "proactive-open.sh")
    if not os.path.isfile(script):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "proactive-open.sh")
    if not os.path.isfile(script):
        print("note: proactive-open.sh not present — surface recorded, "
              "nothing opened")
        return
    env = dict(os.environ)
    env.setdefault("DECISIONS_ROOT", root)
    env.setdefault("DECISIONS_LEDGER", os.path.join(root, ledger_rel_for(root)))
    try:
        subprocess.run(["sh", script], env=env, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print("note: proactive-open failed (%s) — non-fatal" % exc)


# ---------- ruling capture (shadows / --legacy) ----------

def ruling_capture_path(root, arg, date):
    return os.path.join(root, raw_dir_for(root), "%s-%s-ruling.md" % (date, arg))


def committed_ruling_exists(root, arg):
    code, out, _ = git(root, ["ls-tree", "-r", "--name-only", "HEAD",
                              "--", raw_dir_for(root).replace(os.sep, "/")])
    if code != 0:
        return False
    needle = arg.lower()
    return any(needle in os.path.basename(p).lower()
               and "ruling" in os.path.basename(p).lower()
               for p in out.splitlines())


def emit_ruling_capture(root, arg, dec_rec, disposition, chosen, comment, res_sha, date):
    """Write-once research/raw capture generated from the resolution (never edits an
    existing file). Returns the repo-relative path, or None when one already exists."""
    if committed_ruling_exists(root, arg):
        return None
    path = ruling_capture_path(root, arg, date)
    if os.path.exists(path):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src = ("decision %s (%s)" % (dec_rec["id"], dec_rec.get("title", ""))
           if dec_rec else "legacy bracket (no decision row; resolve --legacy)")
    lines = [
        "# %s ruling — %s (filed through the consolidated decision surface)" % (arg, date),
        "",
        "STATUS: %s by the maintainer via %s." % (disposition.upper(), src),
        "",
        "Chosen: %s" % (", ".join(chosen) if chosen else "(none — %s)" % disposition),
        "Comment: %s" % (comment or "(none)"),
        "",
        "Record: %s kind:\"decision-resolution\" id %s%s. decided-by,"
        % (ledger_rel_for(root).replace(os.sep, "/"),
           dec_rec["id"] if dec_rec else arg,
           (", resolution commit %s" % res_sha) if res_sha else " (resolution staged)"),
        "decided-at, and the commit derive from the git commit that landed the row (H-084;",
        "name-neutrality ruling 2026-08-17) — no personal names are stored here.",
        "",
        "This capture closes the legacy bracket [closes-when: maintainer-ruling=%s]" % arg,
        "without dual bookkeeping (docs/decisions.md, `shadows`).",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return os.path.relpath(path, root)


# ---------- dashboard recompile ----------

def recompile_dashboard(root):
    compiler = os.path.join(root, "scripts", "compile-dashboard.py")
    if not os.path.isfile(compiler):
        compiler = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "compile-dashboard.py")
    if not os.path.isfile(compiler):
        print("note: compile-dashboard.py not present — recompile skipped")
        return
    try:
        subprocess.run([sys.executable, compiler, root, "--quiet"], timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print("note: dashboard recompile failed (%s) — non-fatal" % exc)


# ---------- commands ----------

def cmd_add(args, root, ledger):
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    rec = {
        "kind": "decision",
        "id": args.id or next_free_id(parsed),
        "date": args.date or today_str(),
        "requested_at": args.requested_at or args.date or today_str(),
        "requested_by": args.requested_by,
        "title": args.title,
        "ask": {
            "question": args.question,
            "header": args.header,
            "multiSelect": bool(args.multi),
            "options": [],
        },
        "context_pointers": args.pointer or [],
        "blocks": [b.strip() for b in (args.blocks or "").split(",") if b.strip()],
        "urgency": args.urgency,
        "class": getattr(args, "cls"),
        "why_only_you": args.why_only_you,
    }
    for spec in args.option or []:
        label, _, desc = spec.partition(":")
        rec["ask"]["options"].append({"label": label.strip(),
                                      "description": desc.strip()})
    if args.shadows:
        rec["shadows"] = args.shadows
    if args.note:
        rec["note"] = args.note
    if getattr(args, "retest_when", None):
        rec[RETEST_WHEN_FIELD] = args.retest_when
    door_fields_from_args(args, rec)
    errs = validate_decision(rec)
    if errs:
        for e in errs:
            print("ADD-INVALID\t%s" % e)
        return 1
    result = door_lint_row(rec, root, ledger, git_timeout=getattr(args, "door_git_timeout", DOOR_GIT_TIMEOUT))
    for line in door_audit_lines(rec, result):
        print(line)
    if result.exit_code == 2:
        return 2
    # the door evaluator sits between validate and append: RECORD or CARD, fail-closed (no bypass flag exists)
    door = door_evaluate_row(rec, root, ledger, git_timeout=getattr(args, "door_git_timeout", DOOR_GIT_TIMEOUT),
                             inject_fault=bool(getattr(args, "door_inject_fault", False)))
    for line in door.stdout_lines:
        print(line)
    if door.exit_code == 2:
        return 2
    ledger_rel = os.path.relpath(ledger or os.path.join(root, ledger_rel_for(root)), root)
    append_line(root, rec, ledger)
    if door.outcome == "RECORD":
        append_line(root, door_record_row(door), ledger)
        print("recorded %s: %s (accepted=%s basis=%s) — two JSONL lines appended to %s; no card opens"
              % (rec["id"], rec["title"], rec.get("recommended"), DOOR_RECORD_BASIS, ledger_rel))
        return 1 if result.findings else 0
    print("added %s: %s (urgency %s, class %s) — one JSONL line appended to %s"
          % (rec["id"], rec["title"], rec["urgency"], rec["class"], ledger_rel))
    print("commit it with the asking lane's next attributed commit; the surface opens now")
    if not args.no_open:
        run_proactive(root)
    return 1 if result.findings else 0


def cmd_list(args, root, ledger):
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    joined = join_status(parsed["decisions"], parsed["resolutions"])
    if args.json:
        out = []
        for dec in parsed["decisions"]:
            status, chain = joined[dec["id"]]
            rec = dict(dec["rec"])
            rec["status"] = status
            out.append(rec)
        print(json.dumps({"decisions": out, "malformed": parsed["malformed"]},
                         ensure_ascii=False, indent=1))
        return 0
    today = today_str()
    if not parsed["decisions"]:
        print("(no decision rows on file — add one with: decisions.py add, or run "
              "the migration: python3 scripts/migrate-decisions.py)")
        return 0
    for dec in parsed["decisions"]:
        status, chain = joined[dec["id"]]
        rec = dec["rec"]
        age = age_days(rec, today)
        print("%-8s %-10s %-6s %3dd  %s" % (dec["id"], status,
                                            rec.get("urgency", "?"), age,
                                            rec.get("title", "")))
    opens = [d for d in parsed["decisions"]
             if joined[d["id"]][0] in ("open", "commented")]
    print("-- %d decision(s): %d open/commented, %d closed" %
          (len(parsed["decisions"]), len(opens),
           len(parsed["decisions"]) - len(opens)))
    return 0


def cmd_show(args, root, ledger):
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    dec = next((d for d in parsed["decisions"] if d["id"] == args.id), None)
    if dec is None:
        print("no decision row with id %s" % args.id)
        return 1
    ledger_rel = os.path.relpath(ledger or os.path.join(root, ledger_rel_for(root)), root)
    chain = [r for r in parsed["resolutions"] if r["id"] == args.id]
    if not ledger_rel.startswith(".."):
        derive_attribution(root, ledger_rel.replace(os.sep, "/"), chain)
    status, _ = join_status([dec], chain)[args.id]
    rec = dec["rec"]
    ask = rec.get("ask", {})
    print("[%s | %s | %s | asked-by %s | class %s]%s"
          % (dec["id"], rec.get("urgency", "?"),
             "status " + status, rec.get("requested_by", "?"),
             rec.get("class", "?"),
             " | pick many" if ask.get("multiSelect") else ""))
    print("  ask: %s" % ask.get("question", rec.get("title", "")))
    for opt in ask.get("options", []):
        print("  [ ] %s — %s" % (opt.get("label", "?"), opt.get("description", "")))
    print("  why-only-you: %s" % rec.get("why_only_you", ""))
    if rec.get("context_pointers"):
        print("  evidence: %s" % " · ".join(rec["context_pointers"]))
    if rec.get("blocks"):
        print("  blocks: %s" % ", ".join(rec["blocks"]))
    if rec.get("note"):
        print("  note: %s" % rec["note"])
    if isinstance(rec.get("door"), dict):
        print("  door: externality=%s | recommended=%s | default-on-silence=%s | undo %s"
              % (rec.get("externality"), rec.get("recommended"), rec.get("default_on_silence"),
                 ", ".join("%s:%s" % (o.get("label", "?"), o.get("undo", "?")) for o in ask.get("options", []))))
        print("  door-evidence: %s | staged: %s" % (rec.get("evidence"), json.dumps(rec.get("staged_artifact"), ensure_ascii=False)))
        if rec["door"].get("outcome"):
            print("  door-outcome: %s | evaluator %s | head %s%s"
                  % (rec["door"]["outcome"], rec["door"].get("evaluator", "?"), rec["door"].get("head", "?"),
                     (" | hard %s" % rec["door"]["hard"]) if rec["door"].get("hard") else ""))
        for finding in rec["door"].get("findings", []):
            print("  door-finding: %s" % finding)
    for res in sorted(chain, key=lambda r: r["order"]):
        r = res["rec"]
        if res.get("staged", True):
            prov = "staged (provenance pending its commit)"
        else:
            prov = "decided %s by %s · %s" % (res["decided_at"][:10],
                                              res["decided_by"],
                                              res["resolution_commit"])
        print("  resolution: %s %s — %s%s" % (r["disposition"],
                                              json.dumps(r.get("chosen_options", [])),
                                              prov,
                                              (" — \"%s\"" % r["comment"])
                                              if r.get("comment") else ""))
    if status in ("open", "commented"):
        first = (ask.get("options") or [{}])[0].get("label", "<label>")
        print("  answer: python3 scripts/decisions.py resolve %s --accept \"%s\" "
              "[--comment \"...\"]" % (dec["id"], first))
        print("          deny: python3 scripts/decisions.py resolve %s --deny · comment: "
              "python3 scripts/decisions.py resolve %s --comment \"...\""
              % (dec["id"], dec["id"]))
    return 0


def _ledger_pre_dirty(root, ledger_rel):
    code, out, _ = git(root, ["status", "--porcelain", "--", ledger_rel])
    return code == 0 and bool(out.strip())


def cmd_resolve(args, root, ledger):
    today = today_str()
    ledger_path = ledger or os.path.join(root, ledger_rel_for(root))
    ledger_rel = os.path.relpath(ledger_path, root).replace(os.sep, "/")
    inside = not ledger_rel.startswith("..")

    if args.legacy:
        # Compat shim: a legacy maintainer-ruling bracket with no decision row.
        if not (args.accept or args.deny):
            print("RESOLVE-INVALID\t--legacy needs --accept \"<word>\" or --deny")
            return 1
        disposition = "denied" if args.deny else "accepted"
        rel = emit_ruling_capture(root, args.legacy, None, disposition,
                                  args.accept or [], args.comment or "", None, today)
        if rel is None:
            print("legacy %s: a ruling capture already exists — nothing to do"
                  % args.legacy)
            return 0
        print("emitted %s" % rel)
        if not args.no_commit and inside:
            git(root, ["add", "--", rel], check=True)
            git(root, ["commit",
                       "-m", "decision: legacy-%s %s — maintainer-ruling=%s"
                       % (args.legacy, disposition, args.legacy),
                       "--", rel], check=True)
            print("committed the ruling capture — the legacy bracket closes at HEAD")
        if not args.no_recompile:
            recompile_dashboard(root)
        return 0

    if not args.id:
        print("RESOLVE-INVALID\tan id is required (or --legacy <arg>)")
        return 1
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    dec = next((d for d in parsed["decisions"] if d["id"] == args.id), None)
    if dec is None:
        print("RESOLVE-INVALID\tno decision row with id %s" % args.id)
        return 1
    status, _chain = join_status([dec], [r for r in parsed["resolutions"]
                                         if r["id"] == args.id])[args.id]
    record_row = _door_record_row(_chain) if args.deny else None   # a veto: --deny on a two-way-door record
    if status in ("accepted", "denied") and not args.reopen and record_row is None:
        print("RESOLVE-INVALID\t%s is already %s (latest accepted/denied wins; pass "
              "--reopen to append another closing row anyway)" % (args.id, status))
        return 1
    if args.deny:
        disposition = "denied"
        chosen = []
    elif args.accept:
        disposition = "accepted"
        chosen = args.accept
        if len(chosen) > 1 and not dec["rec"].get("ask", {}).get("multiSelect"):
            print("RESOLVE-INVALID\t%s is single-select; pass ONE --accept" % args.id)
            return 1
    elif args.comment:
        disposition = "commented"   # stays open
        chosen = []
    else:
        print("RESOLVE-INVALID\tneed --accept \"<label-or-free-text>\" (repeatable when "
              "multiSelect), --deny, or --comment \"...\"")
        return 1

    rec = {"kind": "decision-resolution", "id": args.id, "date": today,
           "disposition": disposition}
    if chosen:
        rec["chosen_options"] = chosen
    if args.comment:
        rec["comment"] = args.comment
    errs = validate_resolution(rec, {d["id"] for d in parsed["decisions"]})
    if errs:
        for e in errs:
            print("RESOLVE-INVALID\t%s" % e)
        return 1

    committing = inside and not args.no_commit
    if committing and _ledger_pre_dirty(root, ledger_rel):
        print("RESOLVE-BLOCKED\t%s already has uncommitted changes — the resolution "
              "commit must contain JUST the resolution line. Commit or stash the pending "
              "ledger changes first (or pass --no-commit to stage the row uncommitted)."
              % ledger_rel)
        return 1
    append_line(root, rec, ledger)
    print("appended %s %s to %s" % (args.id, disposition, ledger_rel))

    res_sha = None
    if committing:
        msg = "decision: %s %s — decision-resolved=%s" % (args.id, disposition, args.id)
        git(root, ["commit", "-m", msg, "--", ledger_rel], check=True)
        code, out, _ = git(root, ["rev-parse", "--short", "HEAD"])
        res_sha = out.strip() if code == 0 else None
        print("committed JUST that line: %s (%s) — decided-by/at derive from this commit"
              % (msg, res_sha or "?"))
    else:
        print("resolution left uncommitted — it renders as staged until its commit")
    if record_row is not None:
        _door_execute_undo(root, args.id, record_row, committing)

    if disposition in ("accepted", "denied"):
        for shadow in dec["rec"].get("shadows", []):
            arg = shadow.split("=", 1)[-1]
            rel = emit_ruling_capture(root, arg, dec, disposition, chosen,
                                      args.comment or "", res_sha, today)
            if rel:
                print("emitted %s (closes the legacy bracket maintainer-ruling=%s)"
                      % (rel, arg))
                if committing:
                    git(root, ["add", "--", rel], check=True)
                    git(root, ["commit",
                               "-m", "capture: %s ruling emitted by %s resolution — "
                               "maintainer-ruling=%s closes" % (arg, args.id, arg),
                               "--", rel], check=True)
                    print("committed the ruling capture (its own commit; the resolution "
                          "commit stays single-line)")
    if not args.no_recompile:
        recompile_dashboard(root)
    print("resolve opens nothing — the surface refreshes in place")
    return 0


def cmd_check(args, root, ledger):
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    findings = []
    seen = set()
    legacy = legacy_decision_ids(parsed, root)
    for dec in parsed["decisions"]:
        if dec["id"] in seen:
            findings.append("duplicate decision row for %s (one decision row per id)"
                            % dec["id"])
        seen.add(dec["id"])
        for e in validate_decision(dec["rec"]):
            findings.append("%s: %s" % (dec["id"], e))
        if dec["id"] not in legacy:
            for e in door_shape_errors(dec["rec"]):
                findings.append("%s: %s" % (dec["id"], e))
    known = {d["id"] for d in parsed["decisions"]}
    for res in parsed["resolutions"]:
        for e in validate_resolution(res["rec"], known):
            findings.append("resolution@line%d: %s" % (res["order"], e))
    joined = join_status(parsed["decisions"], parsed["resolutions"])
    opens = [i for i, (s, _c) in joined.items() if s in ("open", "commented")]
    closed = [i for i, (s, _c) in joined.items() if s in ("accepted", "denied")]
    # shadowed brackets: a CLOSED decision that shadows a bracket should have its ruling
    # capture committed (otherwise the legacy bracket stays open with no card anywhere)
    for dec in parsed["decisions"]:
        st, _c = joined[dec["id"]]
        if st in ("accepted", "denied"):
            for shadow in dec["rec"].get("shadows", []):
                arg = shadow.split("=", 1)[-1]
                if not committed_ruling_exists(root, arg):
                    findings.append("%s closed but its shadowed bracket %s has no "
                                    "committed ruling capture (resolve normally emits+"
                                    "commits it)" % (dec["id"], shadow))
    # exit-neutral report classes (decision-retest-when): never counted as findings, so the
    # land gate (exit 1 on violations) is unchanged by them.
    due_lines, unarmed_lines = [], []
    for dec in parsed["decisions"]:
        st, chain = joined[dec["id"]]
        due = retest_due(root, dec["rec"], st)
        if due is not None:
            due_lines.append("%s\t%s" % (dec["id"], due))
        fields = revisit_unarmed_fields(dec["rec"], st, chain)
        if fields:
            unarmed_lines.append("%s\t%s" % (dec["id"], fields))
    # exit-neutral (decision-door-evaluator): a non-legacy decision row with no door outcome was filed
    # outside add (or before the evaluator) -- surfaced, never enforced
    unaudited = [dec["id"] for dec in parsed["decisions"] if dec["id"] not in legacy
                 and not (isinstance(dec["rec"].get("door"), dict) and dec["rec"]["door"].get("outcome"))]
    for line in findings:
        print("DECISIONS-CHECK\tFAIL\t%s" % line)
    for did in unaudited:
        print("DECISIONS-CHECK\tDOOR-UNAUDITED\t%s\tdecision row carries no door outcome (filed outside add, or before the evaluator)" % did)
    for line in due_lines:
        print("DECISIONS-CHECK\t%s\t%s" % (RETEST_DUE_CLASS, line))
    for line in unarmed_lines:
        print("DECISIONS-CHECK\t%s\t%s" % (REVISIT_UNARMED_CLASS, line))
    print("decisions-check: %d decision(s) (%d open, %d closed), %d resolution(s), "
          "%d truly-malformed ledger line(s), %d finding(s)"
          % (len(parsed["decisions"]), len(opens), len(closed),
             len(parsed["resolutions"]), parsed["malformed"], len(findings)))
    return 1 if findings else 0


def cmd_surface(args, root, ledger):
    parsed = parse_ledger_v3(read_ledger(root, ledger))
    today = today_str()
    opens = sorted(open_decisions(parsed), key=lambda t: sort_key(t, today))
    for dec, _status, _chain in opens:
        rec = dec["rec"]
        print("DECISION-LEDGER\t%s\t%s\t%s\t%s"
              % (dec["id"], rec.get("urgency", "normal"), rec.get("title", ""),
                 ", ".join(rec.get("blocks") or []) or "-"))
    if opens:
        oldest = max(opens, key=lambda t: age_days(t[0]["rec"], today))
        print("DECISIONS-OPEN\t%d\toldest %s %dd"
              % (len(opens), oldest[0]["id"], age_days(oldest[0]["rec"], today)))
        if not args.no_open:
            run_proactive(root)
    else:
        print("DECISIONS-OPEN\t0\tnothing is waiting")
    return 0


def cmd_open(args, root, ledger):
    targets = [os.path.join(root, "decisions.html")]
    if args.all:
        targets.append(os.path.join(root, "DASHBOARD.md"))
    opener = os.environ.get("DECISIONS_OPEN_CMD") or \
        ("open" if sys.platform == "darwin" else "xdg-open")
    rc = 0
    for t in targets:
        if not os.path.exists(t):
            print("missing: %s (run scripts/compile-dashboard.py first)" % t)
            rc = 1
            continue
        try:
            subprocess.run(opener.split() + [t], timeout=30)
            print("opened %s" % t)
        except (OSError, subprocess.SubprocessError) as exc:
            print("could not open %s (%s) — open it yourself" % (t, exc))
            rc = 1
    return rc


def cmd_migrate(argv_rest, root):
    script = os.path.join(root, "scripts", "migrate-decisions.py")
    if not os.path.isfile(script):
        print("FATAL: scripts/migrate-decisions.py not found")
        return 2
    return subprocess.call([sys.executable, script, "--root", root] + argv_rest)


# ---------- selftest ----------

def selftest():
    failures = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("SELFTEST-PASS" if cond else "SELFTEST-FAIL", name,
                           (" — " + detail) if detail else ""))
        if not cond:
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="decisions-selftest-")
    try:
        root = tmp
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name",
                        "Selftest Runner"], check=True)
        subprocess.run(["git", "-C", root, "config", "user.email",
                        "selftest@example.invalid"], check=True)
        subprocess.run(["git", "-C", root, "config", "commit.gpgsign", "false"],
                       check=True)
        os.makedirs(os.path.join(root, "ledger"))
        os.makedirs(os.path.join(root, "scripts"))
        # a legacy + a v2 row prove the normalizer skips neither
        with open(os.path.join(root, DEFAULT_LEDGER_REL), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"date": "2026-08-01", "slug": "legacy-row",
                                 "hit": "legacy [closes-when: commit-grep=never]",
                                 "kind": "commitment"}) + "\n")
            fh.write(json.dumps({"kind": "commitment", "id": "v2-row",
                                 "date": "2026-08-02", "text": "v2 text",
                                 "closes_when": "commit-grep=never2"}) + "\n")
            fh.write("this line is not JSON\n")
        with open(os.path.join(root, "README.md"), "w") as fh:
            fh.write("selftest repo\n")
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "init"], check=True)

        parsed = parse_ledger_v3(read_ledger(root))
        ok("normalizer-three-shapes",
           len(parsed["rows"]) == 2 and parsed["malformed"] == 1,
           "rows=%d malformed=%d" % (len(parsed["rows"]), parsed["malformed"]))

        env = dict(os.environ, DECISIONS_TODAY="2026-08-28")
        me = os.path.abspath(__file__)

        def cli(*a, **kw):
            return subprocess.run([sys.executable, me, "--root", root] + list(a),
                                  capture_output=True, text=True, env=env, **kw)

        # add
        r = cli("add", "--title", "Selftest gate", "--question",
                "Ship the selftest gate?", "--header", "Gate",
                "--option", "go:ship it", "--option", "hold:wait a wave",
                "--requested-by", "lane SELFTEST", "--urgency", "high",
                "--class", "plan", "--why-only-you", "only you hold the key",
                "--pointer", "README.md", "--no-open", *DOOR_SELFTEST_ARGS)
        ok("add", r.returncode == 0 and "added DEC-001" in r.stdout,
           r.stdout.strip().splitlines()[0] if r.stdout else r.stderr[:120])
        r = cli("add", "--id", "DEC-001", "--title", "dup", "--question", "dup?",
                "--header", "Dup", "--option", "a:b", "--option", "c:d",
                "--requested-by", "x", "--urgency", "low", "--class", "plan",
                "--why-only-you", "y", "--no-open", *DOOR_SELFTEST_ARGS)
        ok("add-id-race-check", r.returncode != 0 and "already on file" in
           (r.stdout + r.stderr))
        # list
        r = cli("list")
        ok("list", r.returncode == 0 and "DEC-001" in r.stdout
           and "open" in r.stdout)
        # surface + once-guard through the real proactive script
        proactive_src = os.path.join(os.path.dirname(me), "proactive-open.sh")
        opens_log = os.path.join(root, "opens.log")
        if os.path.isfile(proactive_src):
            shutil.copy(proactive_src, os.path.join(root, "scripts",
                                                    "proactive-open.sh"))
            with open(os.path.join(root, "decisions.html"), "w") as fh:
                fh.write("<html>stub</html>")
            rec_sh = os.path.join(root, "recorder.sh")
            with open(rec_sh, "w") as fh:
                fh.write("#!/bin/sh\necho \"$@\" >> %s\n" % opens_log)
            os.chmod(rec_sh, 0o755)
            env2 = dict(env, COMPILE_CMD=":", OPEN_CMD=rec_sh, NOTIFY_CMD=":")
            r1 = subprocess.run([sys.executable, me, "--root", root, "surface"],
                                capture_output=True, text=True, env=env2)
            r2 = subprocess.run([sys.executable, me, "--root", root, "surface"],
                                capture_output=True, text=True, env=env2)
            n_opens = (len(open(opens_log).readlines())
                       if os.path.exists(opens_log) else 0)
            ok("surface-lines", "DECISION-LEDGER\tDEC-001\thigh" in r1.stdout
               and "DECISIONS-OPEN\t1" in r1.stdout, r1.stdout.strip()[:100])
            ok("surface-once-guard", n_opens == 1,
               "opened %d time(s) across two surfaces" % n_opens)
            state = os.path.join(root, ".claude", "decision-surface-state.json")
            ok("surface-state-file", os.path.isfile(state)
               and "DEC-001" in open(state).read())
            ok("surface-second-silent", r2.returncode == 0)
        else:
            ok("surface-once-guard", False, "proactive-open.sh not staged beside me")

        # resolve blocked while ledger dirty
        r = cli("resolve", "DEC-001", "--accept", "go", "--no-recompile")
        ok("resolve-scoop-guard", r.returncode != 0
           and "RESOLVE-BLOCKED" in r.stdout, r.stdout.strip()[:100])
        subprocess.run(["git", "-C", root, "add", "--", DEFAULT_LEDGER_REL],
                       check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm",
                        "ledger: DEC-001 lands (selftest migration stand-in)"],
                       check=True)
        # resolve
        r = cli("resolve", "DEC-001", "--accept", "go", "--comment",
                "selftest comment", "--no-recompile")
        ok("resolve", r.returncode == 0 and "committed JUST that line" in r.stdout,
           r.stdout.strip()[:140])
        log = subprocess.run(["git", "-C", root, "log", "-1",
                              "--format=%s%x1f%an", "--name-only"],
                             capture_output=True, text=True).stdout
        subject = log.split("\x1f")[0]
        ok("resolve-commit-message",
           subject == "decision: DEC-001 accepted — decision-resolved=DEC-001", subject)
        touched = [l for l in log.splitlines()[1:] if l.strip()]
        ok("resolve-commit-single-path",
           touched == [DEFAULT_LEDGER_REL.replace(os.sep, "/")], str(touched))
        shown = subprocess.run(["git", "-C", root, "show", "--stat", "HEAD",
                                "--format="], capture_output=True, text=True).stdout
        ok("resolve-commit-one-line", "1 insertion" in shown, shown.strip()[:80])
        # check closes
        r = cli("check")
        ok("check-closes", r.returncode == 0 and "1 open" not in r.stdout
           and "0 open, 1 closed" in r.stdout, r.stdout.strip()[:140])
        # attribution derives from git
        r = cli("show", "DEC-001")
        ok("attribution-from-git", "decided 2026-08-28 by Selftest Runner" in r.stdout
           or "by Selftest Runner" in r.stdout, r.stdout.strip()[-160:])
        # shadows -> ruling capture emitted + committed
        r = cli("add", "--title", "Shadow test", "--question", "Close the shadow?",
                "--header", "Shadow", "--option", "yes:close it",
                "--option", "no:keep it", "--requested-by", "lane SELFTEST",
                "--urgency", "normal", "--class", "hygiene",
                "--why-only-you", "one word", "--shadows",
                "maintainer-ruling=selftest-shadow", "--no-open", *DOOR_SELFTEST_ARGS)
        ok("add-shadowed", r.returncode == 0 and "DEC-002" in r.stdout)
        subprocess.run(["git", "-C", root, "commit", "-qm", "ledger: DEC-002",
                        "--", DEFAULT_LEDGER_REL], check=True)
        r = cli("resolve", "DEC-002", "--deny", "--comment", "not needed",
                "--no-recompile")
        cap = [n for n in os.listdir(os.path.join(root, "research", "raw"))
               if "selftest-shadow" in n and "ruling" in n] \
            if os.path.isdir(os.path.join(root, "research", "raw")) else []
        ok("shadow-capture-emitted", r.returncode == 0 and len(cap) == 1,
           str(cap))
        ok("shadow-capture-committed", committed_ruling_exists(root,
                                                               "selftest-shadow"))
        # legacy compat shim
        r = cli("resolve", "--legacy", "selftest-legacy", "--accept", "done",
                "--no-recompile")
        ok("legacy-shim", r.returncode == 0
           and committed_ruling_exists(root, "selftest-legacy"),
           r.stdout.strip()[:100])
        r = cli("check")
        ok("check-final", r.returncode == 0, r.stdout.strip()[:140])

        # ---- retest_when scenario (decision-retest-when): the seeded violations must bite ----
        r = cli("add", "--title", "Armed with an unknown predicate", "--question",
                "Does the unknown predicate fail?", "--header", "Unknown",
                "--option", "a:first", "--option", "b:second", "--requested-by", "x",
                "--urgency", "low", "--class", "plan", "--why-only-you", "y",
                "--retest-when", "on-full-moon=phase>=1", "--no-open", *DOOR_SELFTEST_ARGS)
        bad = [l for l in r.stdout.splitlines() if l.startswith("ADD-INVALID\t")]
        ok("retest-when-unknown-predicate-bites", r.returncode != 0 and len(bad) == 1
           and "retest_when" in bad[0] and "on-full-moon" in bad[0],
           " | ".join(bad)[:160])
        r = cli("add", "--title", "Wait for two compiled checkpoints", "--question",
                "Act now or wait for the evidence?", "--header", "Wait",
                "--option", "act-now:do it now",
                "--option", "wait-for-evidence:the row re-presents itself once two "
                "checkpoints have compiled", "--requested-by", "lane SELFTEST",
                "--urgency", "low", "--class", "plan", "--why-only-you", "z",
                "--retest-when", "event-count=event/selftest-compiled>=2", "--no-open", *DOOR_SELFTEST_ARGS)
        ok("retest-when-armed-add", r.returncode == 0 and "added DEC-003" in r.stdout,
           r.stdout.strip()[:120])
        r = cli("add", "--title", "Unarmed wait", "--question", "Now or not now?",
                "--header", "Unarmed", "--option", "now:do it",
                "--option", "later:the card waits", "--requested-by", "lane SELFTEST",
                "--urgency", "low", "--class", "plan", "--why-only-you", "z", "--no-open", *DOOR_SELFTEST_ARGS)
        ok("revisit-unarmed-add", r.returncode == 0 and "added DEC-004" in r.stdout)
        subprocess.run(["git", "-C", root, "commit", "-qm",
                        "ledger: DEC-003 armed + DEC-004 unarmed", "--", DEFAULT_LEDGER_REL],
                       check=True)
        r = cli("resolve", "DEC-003", "--accept", "wait-for-evidence", "--no-recompile")
        ok("retest-when-armed-resolve", r.returncode == 0, r.stdout.strip()[:120])

        def due_lines(out):
            return [l for l in out.splitlines()
                    if l.startswith("DECISIONS-CHECK\tRETEST-DUE\t")]

        def unarmed_lines(out):
            return [l for l in out.splitlines()
                    if l.startswith("DECISIONS-CHECK\tREVISIT-UNARMED\t")]
        r = cli("check")
        ok("retest-due-silent-before-evidence", r.returncode == 0
           and due_lines(r.stdout) == [], r.stdout.strip()[:140])
        ok("revisit-unarmed-reported-exit-neutral", r.returncode == 0
           and "0 finding(s)" in r.stdout and unarmed_lines(r.stdout) ==
           ["DECISIONS-CHECK\tREVISIT-UNARMED\tDEC-004\task.options[1].label"],
           " | ".join(unarmed_lines(r.stdout))[:160])
        events = os.path.join(root, "ledger", "events.jsonl")
        with open(events, "w", encoding="utf-8") as fh:
            for i in range(2):
                fh.write(json.dumps({"schema": "v1", "instance-of": "event/selftest-compiled",
                                     "caused-by": "selftest-%d" % i, "date": "2026-08-28",
                                     "subject": "lane/selftest-%d" % i, "payload": {}},
                                    sort_keys=True) + "\n")
        r = cli("check")
        ok("retest-due-silent-on-uncommitted-evidence", r.returncode == 0
           and due_lines(r.stdout) == [], r.stdout.strip()[:140])
        subprocess.run(["git", "-C", root, "add", "--", "ledger/events.jsonl"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm",
                        "events: two selftest-compiled rows"], check=True)
        head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        r = cli("check")
        due = due_lines(r.stdout)
        ok("retest-due-fires-once-after-evidence-commit", r.returncode == 0
           and "0 finding(s)" in r.stdout and due ==
           ["DECISIONS-CHECK\tRETEST-DUE\tDEC-003\tledger/events.jsonl@%s#L2\t"
            "event-count=event/selftest-compiled>=2" % head], " | ".join(due)[:200])
        # ---- door fields (decision-card-door-fields): the lint is wired into add ----
        r = cli("add", "--title", "Door: missing fields", "--question", "Refused?", "--header", "Door",
                "--option", "a:first", "--option", "b:second", "--requested-by", "x", "--urgency", "low",
                "--class", "plan", "--why-only-you", "w", "--no-open")
        refused = [l for l in r.stdout.splitlines() if l.startswith("ADD-REFUSED\tMALFORMED\t")]
        ok("door-missing-fields-refused", r.returncode == 2 and len(refused) >= 6
           and sorted({l.split("\t")[2] for l in refused}) == ["D0", "D1", "D3", "D4", "D5", "D6"],
           "%d refusal line(s), exit %d" % (len(refused), r.returncode))
        r = cli("add", "--title", "Door: self-declared two-way", "--question", "Escalates?", "--header", "Door2",
                "--option", "a:first", "--option", "b:second", "--requested-by", "x", "--urgency", "low",
                "--class", "plan", "--why-only-you", "the lane proceeds under the standing grant", "--no-open",
                *DOOR_SELFTEST_ARGS)
        fnd = [l for l in r.stdout.splitlines() if l.startswith("ADD-FINDING\t")]
        ok("door-self-declared-escalates", r.returncode == 1 and len(fnd) == 1 and "\tD8\t" in fnd[0]
           and "added " in r.stdout, r.stdout.strip()[:160])
        last = json.loads(read_ledger(root).strip().splitlines()[-1])
        ok("door-object-on-row", isinstance(last.get("door"), dict) and len(last["door"].get("fields_sha", "")) == 64
           and last["door"].get("findings") and last["door"]["findings"][0].startswith("D8:"))
        r = cli("show", last["id"])
        ok("door-show-renders", r.returncode == 0 and "  door: externality=none" in r.stdout and "  door-finding: D8:" in r.stdout)
        r = cli("add", "--title", "Door: stall", "--question", "Files?", "--header", "Stall",
                "--option", "a:first", "--option", "b:second", "--requested-by", "x", "--urgency", "low",
                "--class", "plan", "--why-only-you", "stall", "--no-open", "--door-git-timeout", "0",
                "--undo", "ledger-row", "--undo", "ledger-row", "--staged-artifact", "none", "--evidence", "none-exists",
                "--externality", "none", "--recommended", "none", "--default-on-silence", "nothing-changes")
        ok("door-stall-exit-neutral", r.returncode == 0 and r.stdout.splitlines()[0] == "ADD-TIMEOUT\tD7"
           and r.stdout.count("ADD-TIMEOUT") == 1, r.stdout.strip()[:120])
        r = cli("check")
        ok("door-check-exempts-legacy-ids", r.returncode == 0, r.stdout.strip()[-140:])
        # ---- door evaluator (decision-door-evaluator): a two-way card is RECORDED and opens nothing; the veto
        #      executes the record's undo; a side-door row is DOOR-UNAUDITED; the crash seed renders a card ----
        subprocess.run(["git", "-C", root, "commit", "-qm", "ledger: door cards land", "--", DEFAULT_LEDGER_REL], check=True)
        env_rec = env2 if os.path.isfile(proactive_src) else env
        r = subprocess.run([sys.executable, me, "--root", root, "add", "--title", "Door: two-way record", "--question",
                            "Adopt the default?", "--header", "Record", "--option", "adopt:flip the README line (one commit)",
                            "--option", "hold:leave it", "--requested-by", "lane SELFTEST", "--urgency", "low", "--class", "plan",
                            "--why-only-you", "nothing here is only yours",
                            "--undo", "git-revert", "--undo", "ledger-row", "--staged-artifact", "README.md",
                            "--evidence", "none-exists", "--externality", "none", "--recommended", "adopt",
                            "--default-on-silence", "adopt"], capture_output=True, text=True, env=env_rec)
        first = r.stdout.splitlines()[0] if r.stdout else ""
        ok("door-evaluator-records-two-way", r.returncode == 0 and first.startswith("DECISION-DOOR\tDEC-007\tRECORD\t")
           and "recorded DEC-007:" in r.stdout and "added DEC-007" not in r.stdout
           and "Proceed now. Do not wait on this row, and never gate a driver on it." in r.stdout,
           (r.stdout + r.stderr).strip()[:220])
        rows = [json.loads(l) for l in read_ledger(root).splitlines() if l.strip().startswith("{")]
        dec_row = next((x for x in rows if x.get("kind") == "decision" and x.get("id") == "DEC-007"), {})
        res_row = next((x for x in rows if x.get("kind") == "decision-resolution" and x.get("id") == "DEC-007"), {})
        ok("door-record-pair-on-file", (dec_row.get("door") or {}).get("outcome") == "RECORD"
           and len((dec_row.get("door") or {}).get("evaluator", "")) == 7
           and res_row.get("disposition") == "accepted" and res_row.get("basis") == DOOR_RECORD_BASIS
           and res_row.get("chosen_options") == ["adopt"] and res_row.get("veto_open_until") == "2026-09-04"
           and res_row.get("date") == "2026-08-28"
           and str(res_row.get("undo", "")).startswith("git revert --no-edit $(git log -1"),
           json.dumps(res_row, ensure_ascii=False)[:240])
        state_path = os.path.join(root, ".claude", "decision-surface-state.json")
        r = subprocess.run([sys.executable, me, "--root", root, "surface"], capture_output=True, text=True, env=env_rec)
        seen_now = []
        try:
            seen_now = json.load(open(state_path, encoding="utf-8")).get("seen_ids", [])
        except (OSError, ValueError):
            pass
        ok("door-record-never-opens", r.returncode == 0 and "DEC-007" not in seen_now
           and "DECISION-LEDGER\tDEC-007" not in r.stdout and "DECISION-LEDGER\tDEC-004" in r.stdout,
           "seen=%s" % seen_now)
        # the veto: the ledger rows land, the effect lands as a self-declaring landing commit, resolve --deny
        # wins the join and runs the record's undo (the revert of that landing commit)
        subprocess.run(["git", "-C", root, "commit", "-qm", "ledger: DEC-007 recorded", "--", DEFAULT_LEDGER_REL], check=True)
        readme = os.path.join(root, "README.md")
        readme_before = open(readme, encoding="utf-8").read()
        with open(readme, "a", encoding="utf-8") as fh:
            fh.write("adopted by DEC-007\n")
        subprocess.run(["git", "-C", root, "commit", "-qm", "landing: decision-record=DEC-007 adopt the default",
                        "--", "README.md"], check=True)
        r = cli("resolve", "DEC-007", "--deny", "--comment", "veto", "--no-recompile")
        ok("door-veto-executes-undo", r.returncode == 0 and "appended DEC-007 denied" in r.stdout
           and "VETO\tDEC-007\tundo executed rc=0\tgit revert --no-edit" in r.stdout
           and open(readme, encoding="utf-8").read() == readme_before, (r.stdout + r.stderr).strip()[-260:])
        subj = subprocess.run(["git", "-C", root, "log", "-1", "--format=%s"], capture_output=True, text=True).stdout.strip()
        r = cli("list")
        ok("door-veto-deny-wins-join", r.returncode == 0 and subj.startswith('Revert "landing: decision-record=DEC-007')
           and any(l.startswith("DEC-007 ") and " denied " in l for l in r.stdout.splitlines()),
           subj + " | " + r.stdout.strip()[:120])
        r = cli("show", "DEC-007")
        ok("door-show-renders-outcome", r.returncode == 0 and "  door-outcome: RECORD | evaluator " in r.stdout
           and "  resolution: denied [] " in r.stdout, r.stdout.strip()[-200:])
        # a side-door row: lint-stamped, appended outside add -> DOOR-UNAUDITED, exit-neutral
        side = {"kind": "decision", "id": "DEC-008", "date": "2026-08-28", "requested_at": "2026-08-28",
                "requested_by": "lane SIDE-DOOR", "title": "side-door row (no evaluator)",
                "ask": {"question": "q?", "header": "Side", "multiSelect": False,
                        "options": [{"label": "a", "description": "first", "undo": "ledger-row"},
                                    {"label": "b", "description": "second", "undo": "ledger-row"}]},
                "context_pointers": [], "blocks": [], "urgency": "low", "class": "plan", "why_only_you": "side door",
                "staged_artifact": "none", "evidence": "none-exists", "externality": "none", "recommended": "none",
                "default_on_silence": "nothing-changes", "door": {"fields_sha": "0" * 64}}
        append_line(root, side)
        r = cli("check")
        unaud = [l for l in r.stdout.splitlines() if l.startswith("DECISIONS-CHECK\tDOOR-UNAUDITED\t")]
        ok("door-check-reports-side-door-row", r.returncode == 0 and len(unaud) == 1
           and unaud[0].startswith("DECISIONS-CHECK\tDOOR-UNAUDITED\tDEC-008\t") and "0 finding(s)" in r.stdout,
           " | ".join(unaud)[:200] + " " + r.stdout.strip()[-120:])
        # the crash seed: an exception inside the evaluator renders a card (fail-closed), never a record
        r = cli("add", "--title", "Door: crash seed", "--question", "Renders?", "--header", "Crash",
                "--option", "adopt:flip", "--option", "hold:leave", "--requested-by", "x", "--urgency", "low",
                "--class", "plan", "--why-only-you", "the crash seed", "--no-open", "--door-inject-fault",
                "--undo", "git-revert", "--undo", "ledger-row", "--staged-artifact", "README.md", "--evidence", "none-exists",
                "--externality", "none", "--recommended", "adopt", "--default-on-silence", "adopt")
        last = json.loads(read_ledger(root).strip().splitlines()[-1])
        first = r.stdout.splitlines()[0] if r.stdout else ""
        ok("door-crash-seed-renders-a-card", r.returncode == 0 and first.startswith("DECISION-DOOR\tDEC-009\tCARD\tfail-closed\t")
           and "added DEC-009:" in r.stdout and last.get("id") == "DEC-009" and last.get("kind") == "decision"
           and last["door"].get("outcome") == "CARD"
           and any(f.startswith("EVALUATOR-FAIL-CLOSED") for f in last["door"].get("findings", [])),
           (r.stdout + r.stderr).strip()[:220])
        # ---- the legacy boundary in both modes, and the writer gate (a second scratch repository) ----
        root2 = tempfile.mkdtemp(prefix="decisions-selftest-legacy-")
        try:
            subprocess.run(["git", "init", "-q", root2], check=True)
            for k, v in (("user.name", "Selftest Runner"), ("user.email", "selftest@example.invalid"),
                         ("commit.gpgsign", "false")):
                subprocess.run(["git", "-C", root2, "config", k, v], check=True)
            os.makedirs(os.path.join(root2, "ledger"))
            with open(os.path.join(root2, "README.md"), "w") as fh:
                fh.write("legacy boundary selftest\n")

            def legacy_row(n):
                return {"kind": "decision", "id": "DEC-%03d" % n, "date": "2026-08-01", "requested_at": "2026-08-01",
                        "requested_by": "lane LEGACY", "title": "legacy card %d" % n,
                        "ask": {"question": "q?", "header": "Legacy", "multiSelect": False,
                                "options": [{"label": "a", "description": "first"}, {"label": "b", "description": "second"}]},
                        "context_pointers": [], "blocks": [], "urgency": "low", "class": "plan", "why_only_you": "legacy %d" % n}

            def cli2(*a):
                return subprocess.run([sys.executable, me, "--root", root2] + list(a), capture_output=True, text=True, env=env)

            def flagged(r):
                return sorted({l.split("\t")[2].split(":")[0] for l in r.stdout.splitlines()
                               if l.startswith("DECISIONS-CHECK\tFAIL\t")})

            with open(os.path.join(root2, DEFAULT_LEDGER_REL), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(legacy_row(1)) + "\n" + json.dumps(legacy_row(2)) + "\n")
            subprocess.run(["git", "-C", root2, "add", "-A"], check=True)
            subprocess.run(["git", "-C", root2, "commit", "-qm", "two pre-upgrade cards"], check=True)
            r = cli2("add", "--title", "First door card", "--question", "Upgraded?", "--header", "Door", "--option", "a:first",
                     "--option", "b:second", "--requested-by", "x", "--urgency", "low", "--class", "plan",
                     "--why-only-you", "post-upgrade", "--no-open", *DOOR_SELFTEST_ARGS)
            ok("door-legacy-first-door-row-added", r.returncode == 0 and "added DEC-003" in r.stdout, r.stdout.strip()[:120])
            with open(os.path.join(root2, DEFAULT_LEDGER_REL), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(legacy_row(4)) + "\n")
            r = cli2("check")
            ok("door-legacy-shape-order-gates-only-post-upgrade-rows", r.returncode == 1 and flagged(r) == ["DEC-004"],
               "flagged=%s exit %d" % (flagged(r), r.returncode))
            os.makedirs(os.path.join(root2, ".claude"))
            with open(os.path.join(root2, ".claude", "hyp.json"), "w") as fh:
                json.dump({DOOR_LEGACY_KEY: 4}, fh)
            r = cli2("check")
            ok("door-legacy-explicit-boundary-exempts-at-or-below", r.returncode == 0 and flagged(r) == [], "flagged=%s" % flagged(r))
            with open(os.path.join(root2, ".claude", "hyp.json"), "w") as fh:
                json.dump({DOOR_LEGACY_KEY: 1}, fh)
            r = cli2("check")
            ok("door-legacy-explicit-boundary-gates-above", r.returncode == 1 and flagged(r) == ["DEC-002", "DEC-004"],
               "flagged=%s" % flagged(r))
            # the writer gate: append_line refuses a decision row that never passed door_lint_row
            before = read_ledger(root2)
            try:
                append_line(root2, legacy_row(5))
                refused = False
            except SystemExit as exc:
                refused = "no door object" in str(exc)
            ok("door-append-line-refuses-unstamped-decision-row", refused and read_ledger(root2) == before)
            caller_row = legacy_row(5)
            caller_row.update({"staged_artifact": "none", "evidence": "none-exists", "externality": "none",
                               "recommended": "none", "default_on_silence": "nothing-changes"})
            for opt in caller_row["ask"]["options"]:
                opt["undo"] = "ledger-row"
            res = door_lint_row(caller_row, root2)
            ok("door-lint-row-stamps-a-caller-row", res.exit_code == 0 and len(caller_row.get("door", {}).get("fields_sha", "")) == 64
               and door_audit_lines(caller_row, res) == [], str(res.findings)[:160])
            append_line(root2, caller_row)
            ok("door-lint-row-then-append-line-lands", json.loads(read_ledger(root2).strip().splitlines()[-1])["id"] == "DEC-005")
            bad = legacy_row(6)
            res = door_lint_row(bad, root2)
            lines = door_audit_lines(bad, res)
            ok("door-lint-row-malformed-stamps-nothing", res.exit_code == 2 and "door" not in bad
               and sum(1 for l in lines if l.startswith("ADD-REFUSED\tMALFORMED\t")) >= 6, "%d line(s)" % len(lines))
        finally:
            shutil.rmtree(root2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("selftest: %d failure(s)" % len(failures))
    return 1 if failures else 0


# ---------- entry ----------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    ap = argparse.ArgumentParser(prog="decisions.py",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--ledger", help="ledger path override (tests)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("add", help="append one validated decision row")
    p.add_argument("--id"), p.add_argument("--date"), p.add_argument("--requested-at")
    p.add_argument("--title", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--header", required=True)
    p.add_argument("--option", action="append", metavar="LABEL:DESC")
    p.add_argument("--multi", action="store_true")
    p.add_argument("--requested-by", required=True)
    p.add_argument("--urgency", default="normal", choices=URGENCY)
    p.add_argument("--class", dest="cls", required=True, choices=CLASSES)
    p.add_argument("--why-only-you", required=True)
    p.add_argument("--pointer", action="append")
    p.add_argument("--blocks", default="")
    p.add_argument("--shadows", action="append")
    p.add_argument("--note")
    p.add_argument("--retest-when", dest="retest_when", metavar="PREDICATE=ARGUMENT",
                   help="evidence trigger (shared retest-when grammar): the decision is "
                        "re-presented as RETEST-DUE once committed evidence satisfies it")
    p.add_argument("--no-open", action="store_true",
                   help="skip the proactive open (tests)")
    p.add_argument("--undo", action="append", metavar="UNDO",
                   help="per option, positional to --option: git-revert|flag|amendment|ledger-row|none")
    p.add_argument("--staged-artifact", dest="staged_artifact", action="append", metavar="PATH|COMMAND|none",
                   help="what the recommended option changes (repeat per path) or runs (one command line), or none")
    p.add_argument("--evidence", metavar="PATH@SHA40#La-Lb|none-exists")
    p.add_argument("--externality", metavar="CLASS",
                   help="none|other-humans|external-publication|spend-beyond-granted-budget|physical-act|"
                        "classifier-flagged|reserved-in-his-words")
    p.add_argument("--recommended", action="append", metavar="LABEL|none")
    p.add_argument("--default-on-silence", dest="default_on_silence", metavar="LABEL|nothing-changes")
    p.add_argument("--amount-usd", dest="amount_usd", metavar="NUMBER", help="required when --class spend")
    p.add_argument("--door-git-timeout", dest="door_git_timeout", type=int, default=DOOR_GIT_TIMEOUT,
                   help="seconds per git read in the door lint (tests: 0 seeds a stall)")
    p.add_argument("--door-inject-fault", dest="door_inject_fault", action="store_true",
                   help="harness fault injection inside the door evaluator (tests: the crash seed must render a card)")

    p = sub.add_parser("list", help="all decisions with derived status")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("show", help="one full card with git-derived provenance")
    p.add_argument("id")

    p = sub.add_parser("resolve", help="accept/deny/comment; commits JUST the row")
    p.add_argument("id", nargs="?")
    p.add_argument("--accept", action="append", metavar="LABEL_OR_TEXT")
    p.add_argument("--deny", action="store_true")
    p.add_argument("--comment")
    p.add_argument("--legacy", metavar="ARG",
                   help="compat shim: answer a legacy maintainer-ruling bracket")
    p.add_argument("--no-commit", action="store_true")
    p.add_argument("--no-recompile", action="store_true")
    p.add_argument("--reopen", action="store_true",
                   help="append another closing row over an already-closed id")

    sub.add_parser("check", help="schema + join validation; exit 1 on findings")

    p = sub.add_parser("surface", help="print open-decision lines; proactive open")
    p.add_argument("--no-open", action="store_true")

    p = sub.add_parser("open", help="open decisions.html front-and-center")
    p.add_argument("--all", action="store_true", help="also open DASHBOARD.md")

    sub.add_parser("migrate", help="shim: delegates to scripts/migrate-decisions.py")

    if argv and argv[0] == "migrate":
        # passthrough shim keeps migrate's own flags intact
        root_idx = None
        rest = argv[1:]
        root = "."
        if "--root" in rest:
            i = rest.index("--root")
            root = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        return cmd_migrate(rest, os.path.abspath(root))
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    root = os.path.abspath(args.root)
    ledger = os.path.abspath(args.ledger) if args.ledger else None
    if args.cmd == "migrate":
        return cmd_migrate([], root)
    return {"add": cmd_add, "list": cmd_list, "show": cmd_show,
            "resolve": cmd_resolve, "check": cmd_check, "surface": cmd_surface,
            "open": cmd_open}[args.cmd](args, root, ledger)


if __name__ == "__main__":
    sys.exit(main())
