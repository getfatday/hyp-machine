#!/usr/bin/env python3
"""selftest-decision-briefs.py -- regression test for the decision-brief gate (the plain-English brief every card carries).

Runs the INSTALLED plugin's brief selftests from the tree this file lives in, forwards their per-check lines, then
prints one PASS/FAIL line per stage and a RESULT line:

  decision_card_lint.py --selftest     the lint's synthetic-card checks: the door matrix unchanged, and rules B0-B11
                                       once each (B0 PRESENT / B1 CARD-SHA refuse; B2-B11 are prose findings; B6, B9
                                       and B10 report-only until admitted through .claude/hyp.json)
  decision_brief_render.py --selftest  the render module's state table (valid / findings / legacy-missing /
                                       legacy-resolved / missing / stale), the card, record, NOT READY and marker
                                       lines, the html payload
  decisions.py --selftest              the kit's brief scenarios: a candidate without a brief refused with B0 and the
                                       three recipe lines byte-equal, nothing appended; a prose finding appended with
                                       exit 1 and exactly one BRIEF-FINDINGS marker on show; a valid brief rendered
                                       brief-first; append_line refusing a stamp-less row above the boundary; a
                                       side-door row NOT READY everywhere with check exit 1; the silence policy's
                                       resolve refused while unreadable and accepted after the retrofit brief; a stale
                                       brief NOT READY; an orphan sidecar exit-neutral; the legacy marker line
                                       byte-equal to the frozen string; brief-skeleton deterministic
  decision_door_check.py --selftest    the evaluator's seat: W1 refuses an unbriefed candidate with B0 (G1 -- every
                                       writer refuses what add refuses) and the door matrix is unchanged on briefed cards
  admission                            the admission-tiered rules over the kit selftest's compliant briefs: a rule that
                                       printed no ADD-REPORT line is silent; with --live, the root's
                                       decision_brief_admitted_rules may list only silent rules
  live (--live <root>)                 the marker rendering over a real ledger: every open or commented legacy card
                                       renders today's grammar plus exactly one BRIEF-MISSING marker on `show`, in the
                                       compiler's section 1 and in the html payload; every resolved legacy row renders
                                       byte-identically with no marker; `check` names each open legacy card once with the
                                       fixed detail; the resolver prints DECISION-BRIEFS before any DECISION-LEDGER line
                                       (its line count is reported against the hook's head -40 cut, never asserted)

Usage: python3 scripts/selftest-decision-briefs.py [--live <repo-root>]    exit 0 = PASS, 1 = FAIL
       --live defaults to $CLAUDE_PROJECT_DIR when that directory carries .claude/hyp.json; the stage is SKIPPED when the
       root has no decision rows.
Provenance: cause-n-effect H-DRAFT-1c840b86-decision-brief-gate (kept 2026-09-12 by the lineage rule: five counted
looks, 131/131 mutants caught, 11/11 compliant briefs admitted, legacy surfaces byte-identical; fragment 0500). Standard
library, Python 3.9; writes only under the temp dirs the component selftests create and remove themselves; the live
stage writes nothing (it renders in memory).
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
MARKER = "  brief: BRIEF-MISSING "
HOOK_HEAD_CUT = 40   # experiments/deploy/hyp-machine/hooks/hooks.json: the resolver is piped through head -40

STAGES = (
    ("lint-selftest", "decision_card_lint.py", "lint-selftest: 0 failure(s)", "LINT-SELFTEST-",
     ("b0-brief-missing", "b1-flipped-sha", "b2-26-words", "b3-six-sentences", "b4-121-words", "b5-unglossed-house-only",
      "b6-unadmitted-stays-report", "b7-undated", "b8-none-without-cannot-be-undone", "b9-two-token-author-string",
      "b10-admitted-blocks", "b11-sha-unresolved")),
    ("render-selftest", "decision_brief_render.py", "render-selftest: 0 failure(s)", "RENDER-SELFTEST-", ()),
    ("decisions-selftest", "decisions.py", "selftest: 0 failure(s)", "SELFTEST-",
     ("brief-missing-refused", "brief-refusal-recipe-byte-equal", "brief-refusal-appends-nothing", "brief-finding-exit-1",
      "brief-stamp-on-row", "brief-show-findings-marker", "brief-show-raw-is-todays-grammar", "brief-valid-exit-0",
      "brief-show-brief-first", "append-line-refuses-stamp-less-row", "side-door-not-ready-block",
      "check-brief-missing-gated", "resolve-suspended", "brief-sidecar-appended", "brief-sidecar-shape",
      "brief-sidecar-renders-brief-first", "check-exit-0-after-repair", "resolve-accepted-after-brief", "stale-not-ready",
      "check-stale-gated", "check-brief-orphan-exit-neutral", "list-carries-brief-kinds-silently",
      "legacy-marker-line-frozen", "legacy-check-exit-neutral", "boundary-moves-gating", "brief-skeleton-deterministic",
      "brief-skeleton-refused-until-filled", "door-side-door-row-retrofit-brief", "door-side-door-row-readable-after-retrofit")),
    ("door-selftest", "decision_door_check.py", "door-selftest: 0 failure(s)", "DOOR-SELFTEST-",
     ("w1-brief-missing-refused", "clean-zero-information-records")),
)
TIERED = ("B6", "B9", "B10")


def run_stage(name, script, sentinel, prefix, wanted):
    proc = subprocess.run([sys.executable, os.path.join(HERE, script), "--selftest"], capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    lines = proc.stdout.splitlines()
    checks = [l for l in lines if l.startswith(prefix + "PASS ") or l.startswith(prefix + "FAIL ")]
    failed = [l for l in lines if l.startswith(prefix + "FAIL ")]
    present = [c for c in wanted if any(l.startswith(prefix + "PASS " + c) for l in lines)]
    ok = proc.returncode == 0 and sentinel in lines and bool(checks) and not failed and len(present) == len(wanted)
    print("%s %s -- exit %d, %d check(s), %d failed, brief checks %d/%d"
          % ("PASS" if ok else "FAIL", name, proc.returncode, len(checks), len(failed), len(present), len(wanted)))
    if not ok and proc.stderr.strip():
        sys.stdout.write(proc.stderr[-2000:])
    return ok, proc.stdout


def admission_stage(decisions_stdout, root):
    """ADD-REPORT lines over the kit selftest's compliant briefs, per admission-tiered rule."""
    fired = {r: 0 for r in TIERED}
    for l in decisions_stdout.splitlines():
        m = re.match(r"^ADD-REPORT\t[^\t]*\t(B6|B9|B10)\t", l)
        if m:
            fired[m.group(1)] += 1
    silent = [r for r in TIERED if fired[r] == 0]
    for r in TIERED:
        print("ADMISSION\t%s\t%s" % (r, "silent" if fired[r] == 0 else "fired %d" % fired[r]))
    print("ADMITTED\t%s" % ",".join(silent))
    ok = True
    detail = "silent: %s" % (",".join(silent) or "none")
    if root:
        try:
            with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
                listed = json.load(fh).get("decision_brief_admitted_rules")
        except (OSError, ValueError):
            listed = None
        if isinstance(listed, list):
            bad = [r for r in listed if r not in silent]
            ok = not bad
            detail += "; %s lists %s%s" % (os.path.join(".claude", "hyp.json"), ",".join(listed) or "none",
                                            (" -- NOT silent: %s" % ",".join(bad)) if bad else "")
        else:
            detail += "; no decision_brief_admitted_rules key at the root (nothing to compare)"
    print("%s admission -- %s" % ("PASS" if ok else "FAIL", detail))
    return ok


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def live_stage(root):
    """The marker rendering over the root's own ledger, rendered in memory (nothing is written)."""
    root = os.path.abspath(root)
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("LIVE-PASS" if cond else "LIVE-FAIL", name, (" -- " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    render = load_module("decision_brief_render", os.path.join(HERE, "decision_brief_render.py"))
    cfg = render.load_config(root)
    ledger_rel = cfg.get("ledger_file") if isinstance(cfg.get("ledger_file"), str) else "ledger/ledger.jsonl"
    ledger = os.path.join(root, ledger_rel)
    if not os.path.isfile(ledger):
        print("SKIP live -- no ledger at %s" % ledger_rel)
        return True
    rows = []
    with open(ledger, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    rows.append(rec)
    decisions = [r for r in rows if r.get("kind") == "decision"]
    if not decisions:
        print("SKIP live -- no decision rows in %s" % ledger_rel)
        return True
    chains = {}
    for r in rows:
        if r.get("kind") == "decision-resolution":
            chains.setdefault(r.get("id"), []).append(r.get("disposition"))
    briefs = render.group_by_id([r for r in rows if r.get("kind") == "decision-brief"])
    tests = render.group_by_id([r for r in rows if r.get("kind") == "decision-brief-test"])
    legacy = render.legacy_ids(decisions, cfg)

    def status(i):
        ch = chains.get(i, [])
        closing = [d for d in ch if d in ("accepted", "denied")]
        return closing[-1] if closing else ("commented" if ch else "open")

    states = {}
    for d in decisions:
        st, _b = render.resolve_brief(d, briefs.get(d["id"], []), tests.get(d["id"], []), legacy=d["id"] in legacy, status=status(d["id"]))
        states[d["id"]] = st
    legacy_open = [d["id"] for d in decisions if states[d["id"]] == "legacy-missing"]
    legacy_resolved = [d["id"] for d in decisions if states[d["id"]] == "legacy-resolved"]
    print("LIVE\t%s: %d decision row(s), %d legacy (boundary %s), %d open legacy without a brief, %d resolved legacy, states %s"
          % (ledger_rel, len(decisions), len(legacy), cfg.get(render.LEGACY_KEY, "shape-and-order"), len(legacy_open), len(legacy_resolved),
             json.dumps(sorted(set(states.values())))))

    def cli(*a):
        return subprocess.run([sys.executable, os.path.join(HERE, "decisions.py"), "--root", root] + list(a), capture_output=True, text=True)

    bad_open, bad_resolved = [], []
    for i in legacy_open:
        a = cli("show", i).stdout.splitlines()
        b = cli("show", i, "--raw").stdout.splitlines()
        if not (sum(1 for l in a if l.startswith(MARKER)) == 1 and [l for l in a if not l.startswith(MARKER)] == b):
            bad_open.append(i)
    ok("show-open-legacy-one-marker-rest-byte-identical", not bad_open, "%d card(s) checked%s" % (len(legacy_open), ("; bad: %s" % bad_open) if bad_open else ""))
    for i in legacy_resolved:
        a = cli("show", i).stdout
        b = cli("show", i, "--raw").stdout
        if a != b or MARKER in a:
            bad_resolved.append(i)
    ok("show-resolved-legacy-byte-identical-no-marker", not bad_resolved, "%d row(s) checked%s" % (len(legacy_resolved), ("; bad: %s" % bad_resolved) if bad_resolved else ""))
    chk = cli("check")
    missing = [l.split("\t")[2] for l in chk.stdout.splitlines() if l.startswith("DECISIONS-CHECK\tBRIEF-MISSING\t") and l.endswith("\tlegacy card, no brief on file")]
    ok("check-names-each-open-legacy-card-once", sorted(missing) == sorted(legacy_open), "%d line(s), exit %d" % (len(missing), chk.returncode))
    compiler = load_module("compile_dashboard_live", os.path.join(HERE, "compile-dashboard.py"))
    try:
        text, html = compiler.compile_text(root)
    except Exception as exc:  # the compiler never raises in its own CLI; here it is a stage failure
        text, html = "", None
        ok("compiler-renders", False, repr(exc)[:160])
    if text:
        sec1 = text.split("\n## 1b.")[0].split("## 1. DECISIONS WAITING", 1)[-1]
        ok("section-1-one-marker-per-open-legacy-card", sec1.count(MARKER.strip()) == len(legacy_open)
           and text.count(MARKER.strip()) == len(legacy_open), "%d marker(s) in section 1, %d elsewhere" % (sec1.count(MARKER.strip()), text.count(MARKER.strip()) - sec1.count(MARKER.strip())))
        if html is not None:
            ok("html-payload-one-brief-marker-per-open-legacy-card", html.count('"brief_marker"') == len(legacy_open), "%d brief_marker key(s)" % html.count('"brief_marker"'))
        else:
            print("LIVE\tno decisions.html template at the root -- html payload not checked")
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_ROOT, "hooks", "scripts", "session_resolver.py"), root], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    rlines = res.stdout.splitlines()
    first_brief = next((n for n, l in enumerate(rlines) if l.startswith("DECISION-BRIEFS\t")), None)
    first_ledger = next((n for n, l in enumerate(rlines) if l.startswith("DECISION-LEDGER\t")), None)
    summary = rlines[first_brief] if first_brief is not None else ""
    m = re.search(r"missing=(\d+)", summary)
    ok("resolver-summary-before-any-decision-line", first_brief is not None and (first_ledger is None or first_brief < first_ledger)
       and m is not None and int(m.group(1)) == len([i for i in decisions if states[i["id"]] in ("legacy-missing", "missing")]),
       "%s at line %s of %d; first DECISION-LEDGER at line %s" % (summary or "no DECISION-BRIEFS line", (first_brief + 1) if first_brief is not None else "-", len(rlines), (first_ledger + 1) if first_ledger is not None else "-"))
    inside = first_brief is not None and first_brief + 1 + len([l for l in rlines if l.startswith(("BRIEF-", "DEFAULT-SUSPENDED\t"))]) <= HOOK_HEAD_CUT
    print("LIVE\tresolver: %d line(s); the brief block %s the hook's head -%d cut" % (len(rlines), "sits inside" if inside else "OVERRUNS", HOOK_HEAD_CUT))
    print("%s live -- %d check(s), %d failed" % ("PASS" if not fails else "FAIL", 6 if text and html is not None else 5, len(fails)))
    return not fails


def main(argv):
    root = None
    if "--live" in argv:
        i = argv.index("--live")
        root = argv[i + 1] if i + 1 < len(argv) else os.getcwd()
    else:
        env_root = os.environ.get("CLAUDE_PROJECT_DIR")
        if env_root and os.path.isfile(os.path.join(env_root, ".claude", "hyp.json")):
            root = env_root
    failures = 0
    decisions_out = ""
    for name, script, sentinel, prefix, wanted in STAGES:
        ok, out = run_stage(name, script, sentinel, prefix, wanted)
        if script == "decisions.py":
            decisions_out = out
        if not ok:
            failures += 1
    if not admission_stage(decisions_out, root):
        failures += 1
    stages = len(STAGES) + 1
    if root:
        stages += 1
        if not live_stage(root):
            failures += 1
    print("RESULT %s selftest-decision-briefs: %d of %d stage(s) failed" % ("PASS" if failures == 0 else "FAIL", failures, stages))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
