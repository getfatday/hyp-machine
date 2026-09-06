#!/usr/bin/env python3
"""retest-trigger.py -- file one rule-retest decision row when a rule's retest_when holds.

Lane retest-when-predicates (decision-durability program). The kept retest flow (H-249) fires
only on a calendar date (rule-lint.py RULE-EXPIRED on retest_by < as-of). This trigger fires
on EVIDENCE: for every active empirical rule whose `retest_when` predicate
(closes_when.parse_retest_when_field; predicates event-count / metric-crosses /
evidence-received, evaluated against committed HEAD only) holds and which has no open or
resolved class:"rule-retest" decision row for its id, it files exactly one
`decisions.py add --class rule-retest` row whose context_pointers are `<path>@<sha>#L<a>-L<b>`
spans into the committed stream, and prints one RETEST-DUE line. It licenses nothing else
(H-241: the row is the only action). No wall clock is read anywhere: the row's `date` and
`requested_at` are the HEAD commit's author date (the date the evidence was committed), so two
drives over the same commits file byte-identical rows.

Usage
  retest-trigger.py <root> [--registry PATH] [--ledger PATH] [--dry-run] [--date YYYY-MM-DD]
  retest-trigger.py --selftest

  <root> is either
    - a repository root: registry = ledger/rules-registry.jsonl (the live shape) or
      registry/rules.jsonl (the mini-lab shape), decisions store = ledger/work-ledger.jsonl,
      HEAD = that repository; or
    - a rule-lint corpus root (the harden-check.sh ADVISORY-28 assembly): rules-registry.jsonl
      + pinned-tree/ -> the pinned tree is the repository whose HEAD is read and whose
      ledger/work-ledger.jsonl is the decisions store.
  --dry-run prints the findings and files nothing.

Output grammar (frozen; rule-lint.py's): one finding per line, CLASS<TAB>ID<TAB>PATH<TAB>REFERENT
  RETEST-DUE             <rule-id>  <registry path>  <predicate>=<argument> @<sha7>
  RETEST-WHEN-MALFORMED  <rule-id>  <registry path>  <the raw retest_when value>
commentary lines start with "# "; NO absolute paths; NO timestamps; exit 0 ALWAYS (advisory
contract). --selftest is the exception: it exits 1 when a seeded violation files a row.

Registry head-state: the last row per id wins (H-129 append-only supersession); `kind: meta`
rows and id-less rows are skipped. Rule id in the filed row: `blocks: ["rule/<id>"]` and
title `rule-retest: <id>`; dedup matches class + that blocks token across open AND resolved
rows (a resolved retest never re-files: the three predicates are monotone over committed
streams, so re-arming is the re-earned rule's next predicate, not a re-fire).
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import closes_when  # noqa: E402  (the shared module; retest-when family appended there)

GIT_TIMEOUT = 60
# The shared module's 5 s per-call timeout is a hook-time defensive default. Here a slow git
# under machine load must never read as "evidence absent" (a missed fire is a silent false
# negative), so the trigger raises it for its own process only; the module's bytes are untouched.
closes_when.GIT_TIMEOUT = GIT_TIMEOUT
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RULE_RETEST_CLASS = "rule-retest"


def _git(repo, args):
    try:
        proc = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                              text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


def head_sha(repo):
    code, out = _git(repo, ["rev-parse", "HEAD"])
    sha = out.strip()
    return sha if code == 0 and re.match(r"^[0-9a-f]{40}$", sha) else None


def head_author_date(repo):
    """YYYY-MM-DD of the HEAD commit's author date: the date the evidence was committed. The
    only date the trigger ever writes, and it comes from git, never a clock."""
    code, out = _git(repo, ["log", "-1", "--format=%as", "HEAD"])
    d = out.strip()
    return d if code == 0 and DATE_RE.match(d) else None


def read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
    return rows


def registry_head_state(rows):
    """{id: row} -- last row per id wins; meta and id-less rows skipped; seed order kept."""
    heads = {}
    for r in rows:
        if r.get("kind") == "meta":
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        heads[rid] = r
    return heads


def resolve_root(root, registry_opt, ledger_opt):
    """-> (repo, registry_path, ledger_path, registry_display) or None with a reason."""
    root = os.path.abspath(root)
    corpus_reg = os.path.join(root, "rules-registry.jsonl")
    pinned = os.path.join(root, "pinned-tree")
    if os.path.isfile(corpus_reg) and os.path.isdir(pinned):
        repo = os.path.realpath(pinned)
        registry = registry_opt or corpus_reg
        ledger = ledger_opt or os.path.join(repo, "ledger", "work-ledger.jsonl")
        return repo, registry, ledger, "rules-registry.jsonl"
    repo = root
    if registry_opt:
        registry = registry_opt
    elif os.path.isfile(os.path.join(root, "ledger", "rules-registry.jsonl")):
        registry = os.path.join(root, "ledger", "rules-registry.jsonl")
    else:
        registry = os.path.join(root, "registry", "rules.jsonl")
    ledger = ledger_opt or os.path.join(root, "ledger", "work-ledger.jsonl")
    try:
        display = os.path.relpath(registry, root)
    except ValueError:
        display = os.path.basename(registry)
    if display.startswith(".."):
        display = os.path.basename(registry)
    return repo, registry, ledger, display


def filed_rule_ids(ledger_path):
    """Rule ids that already carry a rule-retest decision row (open or resolved)."""
    ids = set()
    for rec in read_jsonl(ledger_path):
        if rec.get("kind") != "decision" or rec.get("class") != RULE_RETEST_CLASS:
            continue
        for tok in rec.get("blocks") or []:
            if isinstance(tok, str) and tok.startswith("rule/"):
                ids.add(tok[len("rule/"):])
    return ids


def file_row(repo, ledger_path, rid, predicate, arg, sha, pointers, date):
    """Append the row through decisions.py (the single validated writer). Returns (rc, out)."""
    decisions = os.path.join(HERE, "decisions.py")
    cmd = [sys.executable, "-B", decisions, "--root", repo, "--ledger", ledger_path, "add",
           "--no-open",
           "--class", RULE_RETEST_CLASS,
           "--date", date, "--requested-at", date,
           "--title", "rule-retest: %s" % rid,
           "--question", "Rule %s is due for a counted retest: its retest_when predicate "
                         "%s=%s holds at HEAD %s. Run the retest lane?" % (rid, predicate, arg, sha[:7]),
           "--header", "Retest",
           "--option", "retest:run the counted retest lane for %s (the H-249 flow: keep re-earns, "
                       "fail retires)" % rid,
           "--option", "retire:retire the rule without a retest",
           "--requested-by", "scripts/retest-trigger.py",
           "--urgency", "normal",
           "--why-only-you", "a counted retest spends lane budget and its verdict can retire a live "
                             "rule; the trigger files this row and licenses nothing else",
           "--blocks", "rule/%s" % rid,
           "--note", "retest_when %s=%s evaluated at %s" % (predicate, arg, sha)]
    for p in pointers:
        cmd += ["--pointer", p]
    env = dict(os.environ, DECISIONS_TODAY=date)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "decisions.py launch failed: %s" % exc
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run(root, registry_opt=None, ledger_opt=None, dry_run=False, date_opt=None):
    resolved = resolve_root(root, registry_opt, ledger_opt)
    repo, registry, ledger, display = resolved
    rows = read_jsonl(registry)
    if not rows:
        print("# retest-trigger: no registry rows at %s" % display)
        return 0
    heads = registry_head_state(rows)
    sha = head_sha(repo)
    if sha is None:
        print("# retest-trigger: no committed HEAD to evaluate against; nothing filed")
        return 0
    date = date_opt or head_author_date(repo)
    if not date:
        print("# retest-trigger: HEAD carries no author date; nothing filed")
        return 0
    already = filed_rule_ids(ledger)
    findings = []
    n_armed = n_due = n_filed = 0
    for rid in sorted(heads):
        row = heads[rid]
        if row.get("class") != "empirical" or row.get("status", "active") != "active":
            continue
        if "retest_when" not in row:
            continue
        raw = row.get("retest_when")
        parsed = closes_when.parse_retest_when_field(raw)
        if parsed is None:
            findings.append(("RETEST-WHEN-MALFORMED", rid, display,
                             str(raw).replace("\t", " ").replace("\n", " ")[:160] or "(empty)"))
            continue
        n_armed += 1
        predicate, arg = parsed
        holds, pointers = closes_when.retest_when_evidence(predicate, arg, repo)
        if not holds:
            continue
        n_due += 1
        if rid in already:
            print("# already-filed %s (rule-retest row on file; exactly-once)" % rid)
            continue
        referent = "%s=%s @%s" % (predicate, arg, sha[:7])
        if dry_run:
            findings.append(("RETEST-DUE", rid, display, referent))
            continue
        rc, out = file_row(repo, ledger, rid, predicate, arg, sha, pointers, date)
        if rc != 0:
            for ln in out.splitlines():
                print("# decisions.py add failed for %s: %s" % (rid, ln.strip()))
            continue
        already.add(rid)
        n_filed += 1
        findings.append(("RETEST-DUE", rid, display, referent))
    findings.sort()
    print("# retest-trigger at %s: rules=%d armed=%d due=%d filed=%d%s"
          % (sha[:7], len(heads), n_armed, n_due, n_filed, " (dry-run)" if dry_run else ""))
    for f in findings:
        print("\t".join(f))
    return 0


# ---------- seeded-violation selftest ----------

def _selftest():
    """Builds a throwaway repository, plants (a) an UNCOMMITTED evidence packet for an armed
    evidence-received rule and (b) a committed cross-then-revert series under a
    metric-crosses @last=3 rule, plus a positive control (an event-count rule that holds) and
    a malformed rule. Exits 1 if either seeded violation files a row, if the control does not
    file exactly once across two invocations, or if the malformed rule is not reported exactly
    once per invocation."""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="retest-trigger-selftest-")
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    try:
        for d in ("ledger", "registry", "scripts", os.path.join("research", "raw")):
            os.makedirs(os.path.join(tmp, d))
        for name in ("closes_when.py", "decisions.py", "retest-trigger.py"):
            shutil.copy2(os.path.join(HERE, name), os.path.join(tmp, "scripts", name))

        def rule(rid, retest_when):
            return {"kind": "rule", "id": rid, "class": "empirical", "status": "active",
                    "text": "seeded %s" % rid, "carriers": [], "licensed_by": ["path:seed.md"],
                    "retest_by": "2027-12-31", "retest_method": "counted lane",
                    "retest_when": retest_when}
        rules = [rule("R-901", "evidence-received=H-901"),
                 rule("R-902", "metric-crosses=metric/x<0.10@last=3"),
                 rule("R-903", "event-count=event/verdict-flipped>=3"),
                 rule("R-904", "metric-crosses=metric/x<0.10@last=3d")]
        with open(os.path.join(tmp, "registry", "rules.jsonl"), "w", encoding="utf-8") as f:
            for r in rules:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        ev = [{"schema": "v1", "instance-of": "event/verdict-flipped", "caused-by": "c%07d" % i,
               "date": "2026-09-05", "subject": "lane/H-%d" % i, "payload": {}} for i in range(3)]
        with open(os.path.join(tmp, "ledger", "events.jsonl"), "w", encoding="utf-8") as f:
            for r in ev:
                f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        series = [{"schema": "metric-point/v1", "metric": "metric/x", "value": v}
                  for v in (0.20, 0.20, 0.20, 0.09, 0.08, 0.12)]
        with open(os.path.join(tmp, "ledger", "metrics-timeseries.jsonl"), "w", encoding="utf-8") as f:
            for r in series:
                f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        open(os.path.join(tmp, "ledger", "work-ledger.jsonl"), "w").close()
        with open(os.path.join(tmp, "seed.md"), "w") as f:
            f.write("seed\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="s@t",
                   GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="s@t",
                   GIT_AUTHOR_DATE="2026-09-05T12:00:00+0000",
                   GIT_COMMITTER_DATE="2026-09-05T12:00:00+0000")
        subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
                       check=True, capture_output=True, env=env)
        # the seeded violation (a): packet present in the working tree, NOT committed
        with open(os.path.join(tmp, "research", "raw", "repo-evidence-packet-H-901-abc1234.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"target": "H-901"}\n')

        def invoke():
            proc = subprocess.run([sys.executable, "-B", os.path.join(tmp, "scripts", "retest-trigger.py"),
                                   tmp], capture_output=True, text=True, timeout=120)
            return proc.returncode, proc.stdout

        rc1, out1 = invoke()
        rc2, out2 = invoke()
        rows = read_jsonl(os.path.join(tmp, "ledger", "work-ledger.jsonl"))
        dec = [r for r in rows if r.get("kind") == "decision"]
        by_rule = {}
        for r in dec:
            for tok in r.get("blocks") or []:
                by_rule[tok] = by_rule.get(tok, 0) + 1
        ok("uncommitted packet files 0 rows (HEAD-only)", by_rule.get("rule/R-901", 0) == 0, str(by_rule))
        ok("cross-then-revert @last=3 files 0 rows (hold)", by_rule.get("rule/R-902", 0) == 0, str(by_rule))
        ok("control files exactly once across two invocations", by_rule.get("rule/R-903", 0) == 1, str(by_rule))
        ok("malformed rule files 0 rows", by_rule.get("rule/R-904", 0) == 0, str(by_rule))
        ok("exactly one RETEST-WHEN-MALFORMED line per invocation",
           out1.count("RETEST-WHEN-MALFORMED\tR-904\t") == 1 and out2.count("RETEST-WHEN-MALFORMED\tR-904\t") == 1,
           out1 + out2)
        ok("RETEST-DUE printed once for the control, first invocation only",
           out1.count("RETEST-DUE\tR-903\t") == 1 and out2.count("RETEST-DUE\tR-903\t") == 0, out1 + out2)
        ok("exit 0 both invocations", rc1 == 0 and rc2 == 0, "%d %d" % (rc1, rc2))
        grammar = re.compile(r"^(RETEST-DUE|RETEST-WHEN-MALFORMED)\t[^\t]+\t[^\t]+\t[^\t]+$")
        ok("stdout grammar holds on every line",
           all(grammar.match(ln) or ln.startswith("# ") for ln in (out1 + out2).splitlines() if ln),
           out1 + out2)
        ptr_re = re.compile(r"^[^@]+@[0-9a-f]{7,40}#L\d+-L\d+$")
        ctrl = [r for r in dec if "rule/R-903" in (r.get("blocks") or [])]
        ok("control row pointers resolve to the stream",
           ctrl and all(ptr_re.match(p) for p in ctrl[0].get("context_pointers") or [])
           and ctrl[0]["context_pointers"] and ctrl[0]["date"] == "2026-09-05", json.dumps(ctrl)[:300])
        date_tokens = set(re.findall(r"\d{4}-\d{2}-\d{2}", json.dumps(ctrl)))
        ok("no date token in the row other than its own date", date_tokens <= {"2026-09-05"}, str(date_tokens))
        # dry-run files nothing even when due
        proc = subprocess.run([sys.executable, "-B", os.path.join(tmp, "scripts", "retest-trigger.py"),
                               tmp, "--dry-run"], capture_output=True, text=True, timeout=120)
        rows_after = read_jsonl(os.path.join(tmp, "ledger", "work-ledger.jsonl"))
        ok("dry-run appends nothing", len(rows_after) == len(rows) and proc.returncode == 0, proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [c for c in checks if not c[1]]
    for name, good, detail in checks:
        print("%s %s%s" % ("ok  " if good else "FAIL", name, "" if good else "  [%s]" % detail[:400]))
    print("selftest: %d checks, %d failed" % (len(checks), len(failed)))
    return 0 if not failed else 1


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    root = None
    registry = ledger = date = None
    dry = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dry-run":
            dry = True
        elif a == "--registry" and i + 1 < len(argv):
            i += 1
            registry = argv[i]
        elif a == "--ledger" and i + 1 < len(argv):
            i += 1
            ledger = argv[i]
        elif a == "--date" and i + 1 < len(argv):
            i += 1
            date = argv[i] if DATE_RE.match(argv[i]) else None
        elif root is None and not a.startswith("--"):
            root = a
        i += 1
    if root is None or not os.path.isdir(root):
        print("# usage: retest-trigger.py <root> [--registry PATH] [--ledger PATH] [--dry-run] "
              "[--date YYYY-MM-DD] | --selftest")
        return 0  # advisory contract holds even on misuse
    return run(root, registry, ledger, dry, date)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
