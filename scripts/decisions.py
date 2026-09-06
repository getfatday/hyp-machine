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
    errs = validate_decision(rec)
    if errs:
        for e in errs:
            print("ADD-INVALID\t%s" % e)
        return 1
    append_line(root, rec, ledger)
    print("added %s: %s (urgency %s, class %s) — one JSONL line appended to %s"
          % (rec["id"], rec["title"], rec["urgency"], rec["class"],
             os.path.relpath(ledger or os.path.join(root, ledger_rel_for(root)), root)))
    print("commit it with the asking lane's next attributed commit; the surface opens now")
    if not args.no_open:
        run_proactive(root)
    return 0


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
    if status in ("accepted", "denied") and not args.reopen:
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
    for dec in parsed["decisions"]:
        if dec["id"] in seen:
            findings.append("duplicate decision row for %s (one decision row per id)"
                            % dec["id"])
        seen.add(dec["id"])
        for e in validate_decision(dec["rec"]):
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
    for line in findings:
        print("DECISIONS-CHECK\tFAIL\t%s" % line)
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
                "--pointer", "README.md", "--no-open")
        ok("add", r.returncode == 0 and "added DEC-001" in r.stdout,
           r.stdout.strip().splitlines()[0] if r.stdout else r.stderr[:120])
        r = cli("add", "--id", "DEC-001", "--title", "dup", "--question", "dup?",
                "--header", "Dup", "--option", "a:b", "--option", "c:d",
                "--requested-by", "x", "--urgency", "low", "--class", "plan",
                "--why-only-you", "y", "--no-open")
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
                "maintainer-ruling=selftest-shadow", "--no-open")
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
                "--retest-when", "on-full-moon=phase>=1", "--no-open")
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
                "--retest-when", "event-count=event/selftest-compiled>=2", "--no-open")
        ok("retest-when-armed-add", r.returncode == 0 and "added DEC-003" in r.stdout,
           r.stdout.strip()[:120])
        r = cli("add", "--title", "Unarmed wait", "--question", "Now or not now?",
                "--header", "Unarmed", "--option", "now:do it",
                "--option", "later:the card waits", "--requested-by", "lane SELFTEST",
                "--urgency", "low", "--class", "plan", "--why-only-you", "z", "--no-open")
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
