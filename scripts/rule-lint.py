#!/usr/bin/env python3
"""rule-lint.py -- H-248 four-class rule-currency lint (the lane's deliverable).

Deterministic, offline, advisory. Reads ONLY corpus files under the corpus
root passed as argv[1]; never the repo, never a clock, never the network.

Corpus contract (the registry-schema lane's corpus shape, H-248 Method,
frozen at registration):
  as-of-date.txt            pinned ISO date; "past" means < this date
  rules-registry.jsonl      one typed row per rule:
                            kind,id,text,carriers,licensed_by,scope_licensed,
                            scope_written,class(permanent|empirical),
                            cost_class,retest_by,retest_method,status
                            (permanent rows carry exemption_reason, no
                            retest_by)
  retest-intents.jsonl      rows {kind:"retest-intent",rule_id,status,...};
                            an OPEN intent suppresses RULE-EXPIRED (the
                            retest is already in flight)
  re-earn-evidence.jsonl    rows {kind:"re-earn",rule_id,evidence,...}; a
                            re-earn row suppresses SCOPE-EXCESS
  carriers/**               compiled rule carriers; clauses live between
                            RULES-BEGIN / RULES-END marker lines, one clause
                            per line, each tagged [R-NNN] with its registry
                            row id
  pinned-tree/**            committed-artifact stand-ins the licensed_by
                            pointers must resolve into

The four detection contracts (spec Method, frozen):
  RULE-EXPIRED    class==empirical AND (retest_by missing/null OR
                  retest_by < as-of) AND no open retest intent row.
                  Permanent-class rows NEVER fire this class.
  RULE-UNLICENSED licensed_by empty, or NO entry resolves to a committed
                  artifact in the pinned tree (an entry with a #Lnn anchor
                  must hit an existing line that is not question-classified,
                  i.e. does not end with "?").
  RULE-ORPHAN     a carrier clause line with no registry row: untagged, or
                  tagged with an id absent from the registry.
  SCOPE-EXCESS    scope_written.breadth strictly broader than
                  scope_licensed.breadth (instance < class < universal) with
                  no re-earn evidence row for the rule.

Output grammar (frozen): one finding per line,
  CLASS<TAB>ID<TAB>CARRIER-PATH<TAB>REFERENT
commentary lines start with "# "; findings sorted by (class,id,carrier,
referent); NO absolute paths, NO timestamps; exit 0 ALWAYS (advisory
contract -- assertion 4).

Landed from the KEPT reference implementation (H-248 rule-expiry-lint, 2x5/5
2026-09-02) experiments/runs/H-248/fixture/rule-lint.py — the four detection
contracts unchanged. One adaptation at land time: resolve_license understands
the canonical registry's licensed_by grammar (H-247 schema): a "path:" prefix
resolves under the pinned tree, and "commit:<sha>" entries are non-resolving
here (offline lint; compile-laws.py lint-registry checks them at the pin — a
row passes when any path: entry resolves). Live corpus root is assembled by
scripts/harden-check.sh ADVISORY-28 (as-of-date + registry + pinned-tree link);
RULE-EXPIRED findings feed the rule-retest decision flow (H-249,
decisions.py --class rule-retest).
"""
import json
import os
import re
import sys

# retest-when awareness (lane retest-when-predicates keep): an empirical row may carry a
# well-formed `retest_when` evidence predicate instead of (or beside) a `retest_by` date.
# RULE-EXPIRED still fires on a past date; it no longer fires on `not retest_by` when the row
# carries a well-formed retest_when (the evidence trigger, scripts/retest-trigger.py, owns that
# row). The grammar lives in the sibling shared module; absent module -> no row is treated as
# evidence-armed (fail-closed toward the date path, exactly the pre-keep behaviour).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from closes_when import parse_retest_when_field as _parse_retest_when_field
except Exception:  # pragma: no cover - the lint stays standalone without the module
    _parse_retest_when_field = None

BREADTH_RANK = {"instance": 0, "class": 1, "universal": 2}
CLAUSE_PREFIX = re.compile(r"^\s*(?:#\s*)?(?:[-*]\s+|\d+\.\s+)?(.*)$")
TAG = re.compile(r"^\[(R-\d{3})\]\s*(.*)$")


def read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def resolve_license(entry, tree):
    """Return (ok, failure_label). ok=True iff the entry resolves to a
    committed artifact in the pinned tree whose anchored line (if any) is
    not question-classified."""
    if not isinstance(entry, str) or not entry.strip():
        return False, "(empty-entry)"
    if entry.startswith("commit:"):
        return False, "(commit-entry-offline)"
    if entry.startswith("path:"):
        entry = entry[len("path:"):]
    path, anchor = entry, None
    if "#L" in entry:
        path, _, tail = entry.partition("#L")
        if not tail.isdigit():
            return False, "(bad-anchor)"
        anchor = int(tail)
    full = os.path.normpath(os.path.join(tree, path))
    if not (full == tree or full.startswith(tree + os.sep)):
        return False, "(outside-pinned-tree)"
    if not os.path.isfile(full):
        return False, "(missing)"
    if anchor is not None:
        with open(full, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        if anchor < 1 or anchor > len(lines):
            return False, "(bad-anchor)"
        if lines[anchor - 1].strip().endswith("?"):
            return False, "(question-classified)"
    return True, ""


def clause_lines(path):
    """Yield (cleaned_clause_text) for every non-blank line between the
    RULES-BEGIN and RULES-END marker lines."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    inside = False
    for raw in text.splitlines():
        if "RULES-BEGIN" in raw:
            inside = True
            continue
        if "RULES-END" in raw:
            inside = False
            continue
        if not inside or not raw.strip():
            continue
        m = CLAUSE_PREFIX.match(raw)
        yield (m.group(1) if m else raw).strip()


def lint(corpus):
    findings = []
    asof_p = os.path.join(corpus, "as-of-date.txt")
    with open(asof_p, encoding="utf-8") as fh:
        as_of = fh.read().strip()

    rows = read_jsonl(os.path.join(corpus, "rules-registry.jsonl"))
    intents = read_jsonl(os.path.join(corpus, "retest-intents.jsonl"))
    reearn = read_jsonl(os.path.join(corpus, "re-earn-evidence.jsonl"))
    open_intent_ids = set(r.get("rule_id") for r in intents
                          if r.get("status") == "open")
    reearn_ids = set(r.get("rule_id") for r in reearn)
    tree = os.path.normpath(os.path.join(corpus, "pinned-tree"))

    by_id = {}
    for row in rows:
        if row.get("kind") == "meta":
            continue  # canonical-registry header row (compile-laws contract)
        rid = row.get("id")
        if not isinstance(rid, str) or rid in by_id:
            print("# WARN malformed or duplicate registry row id=%r" % (rid,))
            continue
        by_id[rid] = row

    for rid in sorted(by_id):
        row = by_id[rid]
        carriers = row.get("carriers") or []
        carrier = carriers[0] if carriers else "rules-registry.jsonl"

        # RULE-EXPIRED (empirical rows only; permanent never fires)
        if row.get("class") == "empirical":
            retest_by = row.get("retest_by")
            evidence_armed = bool(
                _parse_retest_when_field is not None and "retest_when" in row
                and _parse_retest_when_field(row.get("retest_when")) is not None)
            if retest_by:
                expired = str(retest_by) < as_of
            else:
                expired = not evidence_armed  # date-less AND predicate-less: expired at every session
            if expired and rid not in open_intent_ids:
                findings.append(("RULE-EXPIRED", rid, carrier,
                                 "retest_by=%s" % (retest_by or "none")))

        # RULE-UNLICENSED
        lic = row.get("licensed_by")
        if not lic:
            findings.append(("RULE-UNLICENSED", rid, carrier,
                             "licensed_by=[]"))
        else:
            fails = []
            any_ok = False
            for entry in lic:
                ok, label = resolve_license(entry, tree)
                if ok:
                    any_ok = True
                else:
                    fails.append("%s%s" % (entry, label))
            if not any_ok:
                findings.append(("RULE-UNLICENSED", rid, carrier,
                                 ";".join(fails)[:160]))

        # SCOPE-EXCESS
        lb = (row.get("scope_licensed") or {}).get("breadth")
        wb = (row.get("scope_written") or {}).get("breadth")
        if lb in BREADTH_RANK and wb in BREADTH_RANK:
            if BREADTH_RANK[wb] > BREADTH_RANK[lb] and rid not in reearn_ids:
                findings.append(("SCOPE-EXCESS", rid, carrier,
                                 "scope_written=%s>scope_licensed=%s"
                                 % (wb, lb)))
        else:
            print("# WARN row %s has unknown scope breadth (%r/%r)"
                  % (rid, lb, wb))

    # RULE-ORPHAN over the carrier set (union of rows' carriers, deduped)
    carrier_set = sorted(set(c for row in by_id.values()
                             for c in (row.get("carriers") or [])))
    n_clauses = 0
    for rel in carrier_set:
        full = os.path.normpath(os.path.join(corpus, rel))
        if not os.path.isfile(full):
            print("# WARN carrier file missing: %s" % rel)
            continue
        for clause in clause_lines(full):
            n_clauses += 1
            m = TAG.match(clause)
            if m is None:
                findings.append(("RULE-ORPHAN", "-", rel, clause[:80]))
            elif m.group(1) not in by_id:
                findings.append(("RULE-ORPHAN", m.group(1), rel,
                                 m.group(2)[:80]))

    findings.sort()
    print("# rule-lint as-of %s: rows=%d carriers=%d clauses=%d findings=%d"
          % (as_of, len(by_id), len(carrier_set), n_clauses, len(findings)))
    for f in findings:
        print("\t".join(f))
    return 0  # advisory contract: exit 0 always


def _selftest():
    """Seeded-violation selftest (lane retest-when-predicates keep): a throwaway corpus with
    one EVIDENCE-ONLY row (well-formed retest_when, no retest_by) and one DATE-LESS,
    PREDICATE-LESS row; exit 1 unless exactly the second is RULE-EXPIRED. Also seeds a past-date
    row (must fire) and a malformed-predicate, date-less row (must fire: a malformed predicate
    arms nothing)."""
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout

    tmp = tempfile.mkdtemp(prefix="rule-lint-selftest-")
    try:
        os.makedirs(os.path.join(tmp, "pinned-tree"))
        with open(os.path.join(tmp, "pinned-tree", "seed.md"), "w") as fh:
            fh.write("seed\n")
        with open(os.path.join(tmp, "as-of-date.txt"), "w") as fh:
            fh.write("2026-09-05\n")

        def row(rid, **extra):
            base = {"kind": "rule", "id": rid, "text": "t", "carriers": [],
                    "licensed_by": ["path:seed.md"], "class": "empirical", "cost_class": "low",
                    "retest_method": "counted lane", "status": "active",
                    "scope_licensed": {"breadth": "class"}, "scope_written": {"breadth": "class"}}
            base.update(extra)
            return base
        rows = [row("R-901", retest_when="evidence-received=H-901"),
                row("R-902"),
                row("R-903", retest_by="2026-01-01"),
                row("R-904", retest_when="metric-crosses=metric/x<0.1@last=3d")]
        with open(os.path.join(tmp, "rules-registry.jsonl"), "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            lint(tmp)
        expired = sorted(ln.split("\t")[1] for ln in buf.getvalue().splitlines()
                         if ln.startswith("RULE-EXPIRED\t"))
        want = ["R-902", "R-903", "R-904"]
        ok = expired == want
        print("%s rule-lint --selftest: RULE-EXPIRED %s (want %s; the evidence-only row R-901 must not fire)"
              % ("ok  " if ok else "FAIL", expired, want))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    if argv == ["--selftest"]:
        return _selftest()
    if len(argv) != 1 or not os.path.isdir(argv[0]):
        print("# usage: rule-lint.py <corpus-root> | --selftest")
        return 0  # advisory contract holds even on misuse
    return lint(os.path.abspath(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
