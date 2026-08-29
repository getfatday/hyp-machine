#!/usr/bin/env python3
"""The verdict-forcing review cadence (counted under
H-188-dangling-end-pickup-v3 in the source lab, kept 2026-08-26, two
consecutive 5/5; lineage H-163 -> H-187 -> H-188. It carries H-187 correction
i's vocabulary harmonization: closed-with-cause on an AGED row is legal
exactly when the act is evidenced -- and the H-188 interface repair: the
appender accepts REPEATED --evidence flags, so a verdict can declare EVERY
artifact the act created (each provided path validated to exist, all recorded
in the one verdict row), and the RULES doc, the rendered surface, and the
refusal texts all teach that same record-every-artifact rule. Shipped with
the appender exactly as counted; only provenance framing, invocation paths,
and consumer-repo-root resolution differ from the counted fixture copy.
Usage guide: docs/review-cadence.md in this plugin.) ONE bundled mechanism,
deliberately (spec: obligation + ranking + closure semantics are one
cadence):

  * ranked re-presentation -- open rows render as REVIEW DEBT ranked aged-first
    then age-descending, never as an oldest-first uniform wall;
  * the verdict obligation -- every open row leaves review with exactly one
    recorded verdict: act-now, next-touch <date>, parked-because, or
    closed-with-cause (including explicit supersession);
  * closure semantics -- commit-grep closure carries a BORN-AFTER anchor (a
    match dated on/before the row's own date never closes it), and
    supersession is an explicit verdict (superseded-by:<newer-slug>), never
    silence.

Verdicts are APPEND-ONLY rows in ledger/review-ledger.jsonl (kind: "review");
readers join the LATEST verdict per slug. This script never edits
ledger/work-ledger.jsonl, never rewrites history, and never touches any
other surface -- it is purely additive (H-188 assertion 5).

Usage (repo root = --repo, else CLAUDE_PROJECT_DIR, else the cwd):
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py"            # render the review surface
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py" verdict --slug S --class C [args]
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py" emit-doc   # print the cadence rules doc

Stdlib + git only. Deterministic given repo state and the local date.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

AGED_DAYS = 7
CLASSES = ("act-now", "next-touch", "parked-because", "closed-with-cause")
BRACKET_RE = re.compile(
    r"\[closes-when:\s*(path-exists|commit-grep|hypothesis-kept|"
    r"maintainer-ruling)=([^\]]+)\]")

RULES = """THE REVIEW CADENCE (verdict-forcing review)

Every OPEN row listed under REVIEW DEBT must leave this session with exactly
ONE recorded verdict. "Seen, no action, still open" is not a state -- record
it as a verdict. The four verdict classes:

  act-now         the row's work is executable in this repo, now: do it, then
                  record EVERY artifact the act created as evidence (repeat
                  --evidence <repo-relative-path> once per artifact; every
                  path must exist).
  next-touch      schedule it: --next-touch YYYY-MM-DD (a strictly future
                  date), optional --reason. Allowed ONLY for rows younger
                  than 7 days -- the appender refuses it for AGED rows.
  parked-because  the row itself names a blocker outside this repo: record
                  --reason quoting that blocker. Parking without a named
                  reason is refused.
  closed-with-cause  ONLY when (a) the row's closes-when predicate is
                  satisfied by evidence born strictly AFTER the row's date
                  (the born-after anchor: a pre-existing match never closes
                  a row), or (b) a strictly NEWER ledger row absorbs this
                  row's scope: --superseded-by <newer-slug>. On an AGED row,
                  closed-with-cause ADDITIONALLY requires the act evidenced
                  on disk: record EVERY artifact the act created (repeat
                  --evidence <repo-relative-path> once per artifact; every
                  path must exist). Aged work closes only when it is
                  evidenced.

AGED rows (>= 7 days old, marked AGED) MUST leave this session acted or
parked: act-now recording every created artifact as evidence, parked-because
with the named blocker, or closed-with-cause with that same act evidence --
never next-touch.

Record a verdict (--evidence repeats, once per artifact the act created):
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py" verdict \\
      --slug <slug> --class <class> \\
      [--evidence <path> [--evidence <path> ...]] \\
      [--next-touch YYYY-MM-DD] [--reason <text>] [--superseded-by <slug>]

Verdicts are append-only rows in ledger/review-ledger.jsonl; never edit that
file or ledger/work-ledger.jsonl by hand. Re-render this surface any time:
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/review-cadence.py"
"""


def repo_root(cli_repo=None):
    """Consumer repo root: --repo, then CLAUDE_PROJECT_DIR, then the cwd.
    (Plugin convention -- this script ships inside the plugin, so __file__
    never locates the consumer repository.)"""
    if cli_repo:
        return os.path.abspath(cli_repo)
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root and os.path.isdir(env_root):
        return env_root
    return os.getcwd()


def _git(root, args):
    try:
        p = subprocess.run(["git", "-C", root] + list(args),
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return 1, ""
    return p.returncode, p.stdout


def today_str():
    return time.strftime("%Y-%m-%d", time.localtime())


def parse_date(s):
    try:
        return time.strptime(str(s), "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def age_days(row_date, today):
    a, b = parse_date(row_date), parse_date(today)
    if a is None or b is None:
        return 0
    import calendar
    return int((calendar.timegm(b) - calendar.timegm(a)) // 86400)


def load_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    rows.append(d)
    except OSError:
        pass
    return rows


def load_work_rows(root):
    rows = []
    for i, d in enumerate(load_jsonl(
            os.path.join(root, "ledger", "work-ledger.jsonl"))):
        if d.get("kind", "intent") == "review":
            continue
        if not (d.get("slug") and d.get("date") is not None):
            continue
        d["_order"] = i
        rows.append(d)
    return rows


def latest_verdicts(root):
    latest = {}
    for d in load_jsonl(os.path.join(root, "ledger", "review-ledger.jsonl")):
        if d.get("kind") == "review" and d.get("slug") \
                and d.get("verdict") in CLASSES:
            latest[d["slug"]] = d
    return latest


def evidence_record(paths):
    """The recorded shape (H-188 interface repair): the single path itself
    when the act created one artifact (byte-compatible with h187-v1 verdict
    rows), the full list when it created several -- every provided path
    lands in the ONE verdict row."""
    return paths[0] if len(paths) == 1 else list(paths)


def evidence_display(v):
    ev = v.get("evidence", "")
    if isinstance(ev, list):
        return ", ".join(str(p) for p in ev)
    return ev


def closes_when_status(root, row):
    """(state, line) where state in {'none','open','satisfied-born-after',
    'anchored-open'} -- the born-after anchor applied to commit-grep."""
    m = BRACKET_RE.search(str(row.get("hit", "")))
    if not m:
        return "none", "closes-when: (none declared)"
    pred, arg = m.group(1), m.group(2).strip()
    if pred == "path-exists":
        code, _ = _git(root, ["cat-file", "-e", "HEAD:" + arg])
        if code == 0:
            return ("satisfied-born-after",
                    "closes-when path-exists=%s: SATISFIED at HEAD -- record "
                    "closed-with-cause" % arg)
        return "open", "closes-when path-exists=%s: not satisfied" % arg
    if pred == "commit-grep":
        code, out = _git(root, ["log", "--fixed-strings", "--grep=" + arg,
                                "--date=format-local:%Y-%m-%d",
                                "--format=%cd"])
        dates = sorted(set(out.split())) if code == 0 else []
        if not dates:
            return "open", "closes-when commit-grep='%s': no matching commit" % arg
        row_date = str(row.get("date", ""))
        born_after = [d for d in dates if d > row_date]
        if born_after:
            return ("satisfied-born-after",
                    "closes-when commit-grep='%s': SATISFIED born-after "
                    "(commit dated %s > row date %s) -- record "
                    "closed-with-cause" % (arg, born_after[0], row_date))
        return ("anchored-open",
                "closes-when commit-grep='%s': matching commit(s) exist dated "
                "%s -- none born after this row (row date %s); the born-after "
                "anchor holds: NOT closable on that evidence, the row is "
                "still open" % (arg, ",".join(dates), row_date))
    return "open", "closes-when %s=%s: not evaluated by this surface" % (pred, arg)


def classify(root, rows, verdicts, today):
    debt, scheduled, parked, acted, closed = [], [], [], [], []
    for row in rows:
        v = verdicts.get(row["slug"])
        if v is None:
            debt.append((row, None))
        elif v["verdict"] == "closed-with-cause":
            closed.append((row, v))
        elif v["verdict"] == "parked-because":
            parked.append((row, v))
        elif v["verdict"] == "act-now":
            acted.append((row, v))
        elif v["verdict"] == "next-touch":
            nt = str(v.get("next_touch", ""))
            if parse_date(nt) and nt > today:
                scheduled.append((row, v))
            else:
                debt.append((row, v))
    return debt, scheduled, parked, acted, closed


def render(root):
    today = today_str()
    rows = load_work_rows(root)
    verdicts = latest_verdicts(root)
    debt, scheduled, parked, acted, closed = classify(
        root, rows, verdicts, today)
    debt.sort(key=lambda rv: (
        0 if age_days(rv[0].get("date"), today) >= AGED_DAYS else 1,
        -age_days(rv[0].get("date"), today),
        rv[0]["_order"]))
    out = [RULES.rstrip(), "",
           "REVIEW DEBT (%d row(s); ranked aged-first, then age-descending) "
           "-- rendered %s" % (len(debt), today)]
    if not debt:
        out.append("  (none -- every open row carries a current verdict)")
    for i, (row, v) in enumerate(debt, 1):
        age = age_days(row.get("date"), today)
        badge = "AGED %dd" % age if age >= AGED_DAYS else "%dd" % age
        expired = " [next-touch %s EXPIRED -- verdict again]" % \
            v.get("next_touch") if v else ""
        _, cw_line = closes_when_status(root, row)
        out.append("RANK %-2d %-8s %-10s %s%s"
                   % (i, badge, row.get("kind", "intent"), row["slug"],
                      expired))
        out.append("        %s" % row.get("hit", ""))
        out.append("        %s" % cw_line)
    out.append("")
    out.append("SCHEDULED (next-touch in the future; suppressed until due): "
               "%d" % len(scheduled))
    for row, v in scheduled:
        out.append("  %s -> %s" % (row["slug"], v.get("next_touch")))
    out.append("PARKED (parked-because; re-opens only by a new verdict): %d"
               % len(parked))
    for row, v in parked:
        out.append("  %s -- %s" % (row["slug"], v.get("reason", "")))
    out.append("ACTED this cadence (act-now, every created artifact "
               "recorded as evidence): %d" % len(acted))
    for row, v in acted:
        out.append("  %s -> %s" % (row["slug"], evidence_display(v)))
    out.append("CLOSED (closed-with-cause, incl. supersession): %d"
               % len(closed))
    for row, v in closed:
        sup = v.get("superseded_by")
        out.append("  %s -- %s" % (row["slug"],
                                   "superseded-by:%s" % sup if sup
                                   else v.get("reason", "closed")))
    print("\n".join(out))
    return 0


def verdict(root, o):
    today = today_str()
    rows = {r["slug"]: r for r in load_work_rows(root)}
    row = rows.get(o.slug)
    if row is None:
        print("REFUSED: slug %r is not a row in ledger/work-ledger.jsonl"
              % o.slug)
        return 1
    if o.klass not in CLASSES:
        print("REFUSED: --class must be one of %s" % (CLASSES,))
        return 1
    age = age_days(row.get("date"), today)
    rec = {"kind": "review", "date": today, "slug": o.slug,
           "verdict": o.klass}
    sess = os.environ.get("H188_ARM_SESSION", "")
    if sess:
        rec["session"] = sess
    if o.klass == "act-now":
        if not o.evidence:
            print("REFUSED: act-now requires --evidence "
                  "<repo-relative-path>, repeated once per artifact -- "
                  "record EVERY artifact the act created as evidence")
            return 1
        missing = [p for p in o.evidence
                   if not os.path.exists(os.path.join(root, p))]
        if missing:
            print("REFUSED: act-now evidence path(s) %s do not exist -- do "
                  "the work first, then record EVERY artifact the act "
                  "created as evidence"
                  % ", ".join(repr(p) for p in missing))
            return 1
        rec["evidence"] = evidence_record(o.evidence)
        if o.reason:
            rec["reason"] = o.reason
    elif o.klass == "next-touch":
        if age >= AGED_DAYS:
            print("REFUSED: %s is AGED (%dd >= %dd) -- aged rows leave review "
                  "as act-now, parked-because, or closed-with-cause with act "
                  "evidence, never next-touch"
                  % (o.slug, age, AGED_DAYS))
            return 1
        if not (o.next_touch and parse_date(o.next_touch)
                and o.next_touch > today):
            print("REFUSED: next-touch requires --next-touch YYYY-MM-DD "
                  "strictly after today (%s)" % today)
            return 1
        rec["next_touch"] = o.next_touch
        if o.reason:
            rec["reason"] = o.reason
    elif o.klass == "parked-because":
        if not (o.reason and o.reason.strip()):
            print("REFUSED: parked-because requires --reason naming the "
                  "blocker the row itself carries")
            return 1
        rec["reason"] = o.reason
    elif o.klass == "closed-with-cause":
        state, cw_line = closes_when_status(root, row)
        # Vocabulary harmonization (H-187 correction i): closed-with-cause on
        # an AGED row is legal exactly when the act is evidenced on disk --
        # enforced here on the closed path, whichever closure route applies.
        if age >= AGED_DAYS and not o.evidence:
            print("REFUSED: %s is AGED (%dd >= %dd) -- closed-with-cause "
                  "on an aged row additionally requires --evidence "
                  "<repo-relative-path>, repeated once per artifact, "
                  "recording EVERY artifact the act created (do the work "
                  "first, then record the verdict)"
                  % (o.slug, age, AGED_DAYS))
            return 1
        missing = [p for p in (o.evidence or [])
                   if not os.path.exists(os.path.join(root, p))]
        if missing:
            print("REFUSED: closed-with-cause evidence path(s) %s do not "
                  "exist -- do the work first, then record EVERY artifact "
                  "the act created as evidence"
                  % ", ".join(repr(p) for p in missing))
            return 1
        if o.superseded_by:
            sup = rows.get(o.superseded_by)
            if sup is None:
                print("REFUSED: --superseded-by %r is not a ledger row"
                      % o.superseded_by)
                return 1
            if not (str(sup.get("date", "")) > str(row.get("date", ""))):
                print("REFUSED: superseding row %s (date %s) is not strictly "
                      "newer than %s (date %s)"
                      % (o.superseded_by, sup.get("date"), o.slug,
                         row.get("date")))
                return 1
            rec["superseded_by"] = o.superseded_by
            if o.reason:
                rec["reason"] = o.reason
        elif state == "satisfied-born-after":
            rec["reason"] = o.reason or cw_line
        else:
            print("REFUSED: closed-with-cause needs either a closes-when "
                  "predicate satisfied born-after (%s) or --superseded-by "
                  "<strictly newer row>" % cw_line)
            return 1
        if o.evidence:
            rec["evidence"] = evidence_record(o.evidence)
    path = os.path.join(root, "ledger", "review-ledger.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print("RECORDED %s" % json.dumps(rec, sort_keys=True))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="render",
                    choices=["render", "verdict", "emit-doc"])
    ap.add_argument("--slug")
    ap.add_argument("--class", dest="klass")
    ap.add_argument("--evidence", action="append")
    ap.add_argument("--next-touch", dest="next_touch")
    ap.add_argument("--reason")
    ap.add_argument("--superseded-by", dest="superseded_by")
    ap.add_argument("--repo", default=None,
                    help="consumer repo root (default: CLAUDE_PROJECT_DIR, "
                         "then the cwd)")
    o = ap.parse_args()
    root = repo_root(o.repo)
    if o.mode == "emit-doc":
        print("# The review cadence\n")
        print(RULES)
        return 0
    if o.mode == "verdict":
        return verdict(root, o)
    return render(root)


if __name__ == "__main__":
    sys.exit(main())
