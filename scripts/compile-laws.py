#!/usr/bin/env python3
"""compile-laws.py -- H-247 deliverable: typed rules registry -> compiled LAWS
blocks, with a completeness lint, a round-trip comparator, and an advisory
drift checker.

Landed from the KEPT reference implementation (H-247 rules-registry-compiled-laws,
2x5/5 2026-09-02) experiments/runs/H-247/fixture/compile-laws.py — compile,
round-trip, and drift logic unchanged. Adaptations at land time: the two inert
calibration-variant anchor comments removed, and lint-registry requires assembly
fields only on rows that compile into a templated carrier (the live registry also
holds census rule-sites — memory, CLAUDE.md layers, advisories — that no carrier
template renders; the fixture corpus was templated-carrier rows only, so the
requirement was implicitly scoped there).

The live registry is ledger/rules-registry.jsonl. Expired empirical rows are
rule-lint.py's RULE-EXPIRED class (H-248), which feeds the rule-retest decision
flow (H-249, decisions.py --class rule-retest).

Directive-8 discipline: this tool reads ONLY the registry file, carrier files,
and (for license resolution in lint-registry) the repo's git object store at
the pin recorded in the registry meta row. It never reads harness material
(seed manifest, expected-findings key) and never writes anything.

Subcommands (all exit 0 on completion; findings are ADVISORY lines):
  lint-registry --registry F --repo R
      Field-completeness + license-resolution lint over every rule row.
      Output: one line per row --
        REGISTRY-LINT\tOK\t<id>
        REGISTRY-LINT\tDEFECT\t<id>\t<what>          (one line per defect)
      then REGISTRY-LINT\tSUMMARY\trows=..\tok=..\tdefects=..\tpointers=..\tresolved=..
  compile --registry F --carrier-id ID
      Deterministically render the carrier's rule block (one text line per
      carrier line, template order) to stdout.
  roundtrip --registry F --carrier FILE --carrier-id ID
      Compare compiled block vs the carrier's live block under the FROZEN
      normalization (clause-set equality over whitespace-normalized clause
      text, segment split frozen below). Output one ROUNDTRIP summary line
      plus ROUNDTRIP-DIFF detail lines when unequal.
  check --registry F --carrier FILE --carrier-id ID
      Advisory drift check. One finding per line, exactly three fields after
      the tag (frozen at registration):
        LAWS-DRIFT\t<class>\t<carrier-path-as-given>\t<clause-id>
      class in {silent-drop, reword, orphan, carrier-unparsed};
      orphan clause id is the reserved token (unregistered). Exit 0 always.

Frozen normalization (registration-frozen, recorded in fixture.lock):
  norm(s)   = collapse whitespace runs to single spaces, strip, then strip
              trailing '.' characters.
  segments  = split the line body (prefix/suffix removed) on '; ' then each
              piece on '. '; strip; drop empties. Clause-set equality is
              multiset equality of norm(segment) per carrier line.
  reword pairing: a missing expected segment pairs with the most-similar
              unmatched extra segment when difflib.SequenceMatcher
              (autojunk=False, lowercased norms).ratio() >= 0.5 (frozen;
              measured corpus margins recorded in fixture.lock).
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from difflib import SequenceMatcher

REWORD_PAIR_MIN = 0.5  # frozen at registration
LINE_ORDER = {"LAWS": 0, "D": 1}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_FIELDS = ("kind", "id", "text", "carriers", "licensed_by",
                   "scope_licensed", "scope_written", "class", "cost_class",
                   "status")


def norm(s):
    return " ".join(s.split()).rstrip(".")


def split_line_body(body):
    segs = []
    for part in body.split("; "):
        segs.extend(part.split(". "))
    return [s.strip() for s in segs if s.strip()]


def ratio(a, b):
    return SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


def load_registry(path):
    meta, rows = None, []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            if d.get("kind") == "meta":
                meta = d
            else:
                rows.append(d)
    if meta is None:
        raise SystemExit("REGISTRY-ERROR: no meta row in %s" % path)
    return meta, rows


def rows_for_carrier(rows, carrier_id):
    out = [r for r in rows
           if r.get("status") == "active"
           and carrier_id in (r.get("carriers") or [])]
    out.sort(key=lambda r: (LINE_ORDER.get(r["assembly"]["line"], 99),
                            r["assembly"]["group"], r["assembly"]["seq"]))
    return out


def compile_lines(meta, rows, carrier_id):
    tmpl = meta["carrier_templates"][carrier_id]["lines"]
    per_line = {}
    for r in rows_for_carrier(rows, carrier_id):
        a = r["assembly"]
        per_line.setdefault(a["line"], []).append(
            a.get("pre", "") + r["text"] + a.get("post", ""))
    out = {}
    for line_name in sorted(tmpl, key=lambda k: LINE_ORDER.get(k, 99)):
        spec = tmpl[line_name]
        out[line_name] = (spec["prefix"] + "".join(per_line.get(line_name, []))
                          + spec["suffix"])
    return out


def extract_carrier_lines(meta, carrier_path, carrier_id):
    """Frozen extraction: the carrier is a workflow script holding each rule
    line as a one-line JS double-quoted string literal `const <VAR> = "..."`;
    the literal body is JSON-decodable by construction."""
    src = open(carrier_path, encoding="utf-8").read()
    tmpl = meta["carrier_templates"][carrier_id]
    res = {}
    for line_name, var in tmpl["line_vars"].items():
        m = re.search(r'^const %s = "(.*)"$' % re.escape(var), src, re.M)
        if not m:
            return None
        try:
            res[line_name] = json.loads('"' + m.group(1) + '"')
        except ValueError:
            return None
    return res


def body_of(line_text, spec):
    b = line_text
    if b.startswith(spec["prefix"]):
        b = b[len(spec["prefix"]):]
    if spec["suffix"] and b.endswith(spec["suffix"]):
        b = b[:-len(spec["suffix"])]
    return b


def multiset_diff(exp_segs, act_segs):
    """Order-preserving multiset subtraction over normalized segments."""
    miss, extra = [], []
    pool = Counter(norm(s) for s in act_segs)
    for s in exp_segs:
        n = norm(s)
        if pool[n] > 0:
            pool[n] -= 1
        else:
            miss.append(n)
    pool = Counter(norm(s) for s in exp_segs)
    for s in act_segs:
        n = norm(s)
        if pool[n] > 0:
            pool[n] -= 1
        else:
            extra.append(n)
    return miss, extra


def cmd_lint(args):
    meta, rows = load_registry(args["registry"])
    repo = args["repo"]
    pin = meta["repo_pin"]
    templated = set(meta.get("carrier_templates") or {})
    n_ok = n_def = n_ptr = n_res = 0
    seen_ids = set()
    for r in rows:
        defects = []
        rid = r.get("id", "(missing-id)")
        if rid in seen_ids:
            defects.append("duplicate-id")
        seen_ids.add(rid)
        for f in REQUIRED_FIELDS:
            v = r.get(f)
            if v is None or v == "" or v == []:
                defects.append("field:%s:missing" % f)
        # assembly is a compile concern: required only when the row renders
        # into a templated carrier (non-compiled rule-sites carry none)
        if templated & set(r.get("carriers") or []):
            a = r.get("assembly") or {}
            for f in ("line", "group", "seq"):
                if f not in a:
                    defects.append("assembly:%s:missing" % f)
        klass = r.get("class")
        if klass not in ("permanent", "empirical"):
            defects.append("class:invalid:%s" % klass)
        elif klass == "empirical":
            if not (isinstance(r.get("retest_by"), str)
                    and DATE_RE.match(r["retest_by"])):
                defects.append("retest_by:missing-or-not-a-date")
            if not r.get("retest_method"):
                defects.append("retest_method:missing")
        else:  # permanent: exempt from retest_by, must say why
            if not r.get("retest_exempt_reason"):
                defects.append("retest_exempt_reason:missing")
        for ptr in (r.get("licensed_by") or []):
            n_ptr += 1
            if ptr.startswith("path:"):
                rc = subprocess.run(
                    ["git", "-C", repo, "cat-file", "-e",
                     "%s:%s" % (pin, ptr[5:])],
                    capture_output=True).returncode
            elif ptr.startswith("commit:"):
                rc = subprocess.run(
                    ["git", "-C", repo, "cat-file", "-e",
                     "%s^{commit}" % ptr[7:]],
                    capture_output=True).returncode
            else:
                rc = 1
                defects.append("license-grammar:%s" % ptr)
                continue
            if rc == 0:
                n_res += 1
            else:
                defects.append("license-unresolved:%s" % ptr)
        if defects:
            n_def += 1
            for d in defects:
                print("REGISTRY-LINT\tDEFECT\t%s\t%s" % (rid, d))
        else:
            n_ok += 1
            print("REGISTRY-LINT\tOK\t%s" % rid)
    print("REGISTRY-LINT\tSUMMARY\trows=%d\tok=%d\tdefects=%d\tpointers=%d"
          "\tresolved=%d" % (len(rows), n_ok, n_def, n_ptr, n_res))
    return 0


def cmd_compile(args):
    meta, rows = load_registry(args["registry"])
    lines = compile_lines(meta, rows, args["carrier-id"])
    for name in sorted(lines, key=lambda k: LINE_ORDER.get(k, 99)):
        print(lines[name])
    return 0


def cmd_roundtrip(args):
    meta, rows = load_registry(args["registry"])
    cid = args["carrier-id"]
    compiled = compile_lines(meta, rows, cid)
    actual = extract_carrier_lines(meta, args["carrier"], cid)
    if actual is None:
        print("ROUNDTRIP\tparse-error\t%s" % args["carrier"])
        return 0
    tmpl = meta["carrier_templates"][cid]["lines"]
    segments_equal = True
    diffs = []
    for name in sorted(tmpl, key=lambda k: LINE_ORDER.get(k, 99)):
        exp_segs = split_line_body(body_of(compiled[name], tmpl[name]))
        act_segs = split_line_body(body_of(actual[name], tmpl[name]))
        miss, extra = multiset_diff(exp_segs, act_segs)
        if miss or extra:
            segments_equal = False
            for m in miss:
                diffs.append((name, "missing", m))
            for x in extra:
                diffs.append((name, "extra", x))
    lrows = rows_for_carrier(rows, cid)
    covered = 0
    for r in lrows:
        line_name = r["assembly"]["line"]
        if norm(r["text"]) in norm(body_of(actual[line_name],
                                           tmpl[line_name])):
            covered += 1
    byte_equal = all(compiled[n] == actual[n] for n in tmpl)
    print("ROUNDTRIP\tsegments_equal=%s\trows_covered=%d/%d\tbyte_equal=%s"
          % ("yes" if segments_equal else "no", covered, len(lrows),
             "yes" if byte_equal else "no"))
    for name, kind, seg in diffs:
        print("ROUNDTRIP-DIFF\t%s\t%s\t%s" % (name, kind, seg))
    return 0


def cmd_check(args):
    meta, rows = load_registry(args["registry"])
    cid = args["carrier-id"]
    carrier_arg = args["carrier"]
    compiled = compile_lines(meta, rows, cid)
    actual = extract_carrier_lines(meta, carrier_arg, cid)
    if actual is None:
        print("LAWS-DRIFT\tcarrier-unparsed\t%s\t(all)" % carrier_arg)
        return 0
    tmpl = meta["carrier_templates"][cid]["lines"]
    findings = []
    orphans = []
    for name in sorted(tmpl, key=lambda k: LINE_ORDER.get(k, 99)):
        exp_segs = split_line_body(body_of(compiled[name], tmpl[name]))
        act_body = body_of(actual[name], tmpl[name])
        act_segs = split_line_body(act_body)
        miss, extra = multiset_diff(exp_segs, act_segs)
        # pair rewords: each missing expected segment takes the single
        # most-similar unmatched extra segment at ratio >= REWORD_PAIR_MIN
        paired_extra = set()
        paired_miss = set()
        for i, mseg in enumerate(miss):
            best = None
            for j, xseg in enumerate(extra):
                if j in paired_extra:
                    continue
                rr = ratio(mseg, xseg)
                if best is None or rr > best[0] + 1e-12:
                    best = (rr, j)
            if best is not None and best[0] >= REWORD_PAIR_MIN:
                paired_extra.add(best[1])
                paired_miss.add(i)
        line_rows = [r for r in rows_for_carrier(rows, cid)
                     if r["assembly"]["line"] == name]
        act_line_norm = norm(act_body)
        for r in line_rows:
            t = norm(r["text"])
            if t in act_line_norm:
                continue  # clause present somewhere in the live line
            row_miss_idx = [i for i, mseg in enumerate(miss)
                            if t in mseg or mseg in t]
            if any(i in paired_miss for i in row_miss_idx):
                findings.append(("reword", r["id"]))
            else:
                findings.append(("silent-drop", r["id"]))
        for j, xseg in enumerate(extra):
            if j in paired_extra:
                continue
            covered = any(norm(rr["text"]) and norm(rr["text"]) in xseg
                          for rr in line_rows)
            if not covered:
                orphans.append(xseg)
    for klass, rid in findings:
        print("LAWS-DRIFT\t%s\t%s\t%s" % (klass, carrier_arg, rid))
    for _seg in orphans:
        print("LAWS-DRIFT\torphan\t%s\t(unregistered)" % carrier_arg)
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    args = {}
    i = 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            args[rest[i][2:]] = rest[i + 1]
            i += 2
        else:
            print("bad argument: %s" % rest[i])
            return 2
    need = {"lint-registry": ("registry", "repo"),
            "compile": ("registry", "carrier-id"),
            "roundtrip": ("registry", "carrier", "carrier-id"),
            "check": ("registry", "carrier", "carrier-id")}
    if cmd not in need:
        print("unknown subcommand: %s" % cmd)
        return 2
    for k in need[cmd]:
        if k not in args:
            print("missing --%s" % k)
            return 2
    fn = {"lint-registry": cmd_lint, "compile": cmd_compile,
          "roundtrip": cmd_roundtrip, "check": cmd_check}[cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
