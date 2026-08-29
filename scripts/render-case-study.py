#!/usr/bin/env python3
"""render-case-study.py — the counted per-keep case-study renderer: a pure,
deterministic projection of one pinned kept experiment's artifacts into ONE
plain-language page plus its extraction manifest.

PROVENANCE — COUNTED, byte-preserving port of the kept H-201 fixture renderer
(experiments/runs/H-201/fixture/render_case_study.py in the source lab;
hypothesis H-201-keep-case-study-v2 KEPT 2026-08-28, two consecutive counted
5/5: zero renderer-invented facts against the extraction manifest, the cold
outside reader answered 5/5 discriminating synthesis questions from the page
while the raw-artifacts reader answered strictly fewer, content-law lint clean,
and byte-identical recompilation). Only this provenance framing and the script
name differ from the counted fixture copy.

This file IS the counted reference render: its fact table is written against
one specific pinned keep (the source lab's H-188 review-cadence keep — the
SPEC/F*/RR*/SC* constants below). To render a case study for one of YOUR keeps,
copy this script, repoint those constants at your keep's pinned artifacts, and
keep the Renderer class, the fail-closed self-checks, and the content laws
unchanged — the grammar (scripts/fact_fidelity.py + scripts/content_lint.py +
scripts/jargon.json) is the counted machinery; the constants are the per-keep
configuration.

Contract (spec Hypothesis + Method steps 1/2/6, frozen at fixture build):
  * inputs: ONLY files under --source (the byte-identical pinned copy). No clock, no
    environment, no network — recompiling reproduces the page byte-identically.
  * declared outputs: exactly <out>/case-study.md and <out>/extraction-manifest.json.
    The renderer creates <out> if needed and writes NOTHING else anywhere.
  * every number is extracted from the artifact bytes by anchored regex at render time
    (never hardcoded), and every quote is verified as an exact byte substring of its
    artifact before it is placed — a drifted source fails the render loudly rather than
    rendering an invented fact.
  * every fact line carries a [source: <repo-relative-path>] pointer; the extraction
    manifest records every (kind, value, artifacts) fact placed.
  * self-check: before writing, the renderer runs the SAME frozen fact grammar the
    fidelity leg uses (fact_fidelity.check) plus the content lint (content_lint.lint)
    and refuses to emit a page that fails either — fail-closed at the source.

Content laws carried by the template: content-law voice (plain language), the frozen
jargon list glossed at first use, zero bare slugs, curly double quotes reserved for
artifact quotes, no timestamps, no invented aggregates (per-run figures only — a sum
appears in no artifact of record, so no sum appears here).

CLI: render_case_study.py --source <fixture/source> --out <dir>
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import content_lint  # noqa: E402
import fact_fidelity  # noqa: E402

SPEC = "hypotheses/H-188-dangling-end-pickup-v3.md"
F0196 = "experiments/journal-fragments/0196-h187-refined-h170-judge-parse-defect.md"
F0198 = "experiments/journal-fragments/0198-h188-kept-wave-at-4of7.md"
F0200 = "experiments/journal-fragments/0200-wave-020-complete.md"
F0201 = "experiments/journal-fragments/0201-hyp-0-2-0-published.md"
RR1 = "experiments/runs/H-188/run-1/run-record.json"
SC1 = "experiments/runs/H-188/run-1/h188-score.json"
RR2 = "experiments/runs/H-188/run-2/run-record.json"
SC2 = "experiments/runs/H-188/run-2/h188-score.json"

OUTPUTS = ("case-study.md", "extraction-manifest.json")


class Renderer(object):
    def __init__(self, source_dir):
        self.source_dir = source_dir
        self.bytes = {}
        self.facts = []

    def load(self, rel):
        if rel not in self.bytes:
            with open(os.path.join(self.source_dir, rel), "rb") as f:
                self.bytes[rel] = f.read()
        return self.bytes[rel]

    def _register(self, kind, value, artifacts):
        entry = {"kind": kind, "value": value, "artifacts": sorted(artifacts)}
        if entry not in self.facts:
            self.facts.append(entry)

    def quote(self, rel, text):
        """A verbatim byte-slice of one artifact line, placed in curly quotes."""
        if text.encode("utf-8") not in self.load(rel):
            raise SystemExit("RENDER REFUSE: quote not a byte substring of %s: %r"
                             % (rel, text))
        if "\n" in text:
            raise SystemExit("RENDER REFUSE: quote crosses artifact lines: %r" % text)
        self._register("quote", text, [rel])
        return "“%s”" % text

    def num(self, value, rels):
        """A numeral fact; must byte-appear in every artifact it is attributed to."""
        for rel in rels:
            if value.encode("utf-8") not in self.load(rel):
                raise SystemExit("RENDER REFUSE: number %r not in %s" % (value, rel))
        self._register("number", value, rels)
        return value

    def extract(self, rel, pattern):
        """Anchored regex extraction of a numeral from artifact bytes (group 1)."""
        m = re.search(pattern, self.load(rel).decode("utf-8"))
        if not m:
            raise SystemExit("RENDER REFUSE: pattern %r not found in %s"
                             % (pattern, rel))
        return self.num(m.group(1), [rel])


def ptr(*rels):
    return "[source: %s]" % "; ".join(rels)


def render(source_dir):
    """-> (page_text, manifest_dict). Pure function of the source bytes."""
    r = Renderer(source_dir)

    spend1 = r.extract(RR1, r'"spent_usd_counted": ([0-9.]+)')
    wall1 = r.extract(RR1, r'"wall_clock_s": ([0-9.]+)')
    spend2 = r.extract(RR2, r'"spent_usd_counted": ([0-9.]+)')
    wall2 = r.extract(RR2, r'"wall_clock_s": ([0-9.]+)')
    sessions = r.extract(RR1, r'"counted_sessions": ([0-9]+)')
    r.num(sessions, [RR2])
    cap = r.extract(RR1, r'"cost_cap_usd": ([0-9]+)\.0')
    r.num(cap, [RR2])
    passed = r.extract(SC1, r'"passed": ([0-9]+)')
    r.num(passed, [SC2, SPEC])
    run_date = r.num("2026-08-26", [SPEC])
    ship_date = r.num("2026-08-27", [F0201])

    L = []
    a = L.append
    a("# A kept experiment, explained: the review cadence")
    a("")
    a("This page is a compiled case study of one finished experiment from a research "
      "lab that tests better ways of working. It is written for a cold outside reader. "
      "Every number and quoted phrase on it carries a pointer, in square brackets, to "
      "the tracked file it came from — its artifact of record.")
    a("")
    a("## What was tested")
    a("")
    a("The lab keeps a durable work ledger. Entries were captured reliably but old "
      "ones were rarely picked back up, so the lab built a fix and put it on trial "
      "under the name H-188 (the lab names each experiment H- plus a number; this was "
      "the third revision of its pickup experiment).")
    a("")
    a("The fix on trial is a review cadence: open ledger rows are re-presented in "
      "ranked order, and — quoting the hypothesis file — %s %s."
      % (r.quote(SPEC, "every open row leaves review with exactly one recorded "
                       "verdict"), ptr(SPEC)))
    a("")
    a("A verdict (the decision a row must carry before review ends: act now, set a "
      "next-touch date, park it with a written reason, or close it with a cause) is "
      "the forcing part. The hypothesis file frames the claim as %s %s."
      % (r.quote(SPEC, "the review-cadence mechanism re-dispatches aged open work"),
         ptr(SPEC)))
    a("")
    a("## Against what baseline")
    a("")
    a("The control condition removed only the cadence: %s — meaning the lab's "
      "existing record-only surfacing tools, unmodified %s."
      % (r.quote(SPEC, "The OFF arm: the same seeded aged ledger surfaced only as "
                       "today"), ptr(SPEC)))
    a("")
    a("The house measurement that motivated the trial: %s — captured work was "
      "surfacing but not moving %s."
      % (r.quote(SPEC, "13.7% seven-day pickup across all open rows; 4.8% for rows "
                       ">=7d"), ptr(SPEC)))
    a("")
    a("## How pass or fail was decided")
    a("")
    a("Success was defined before anything ran, as five assertions (yes-or-no checks "
      "written into the hypothesis file up front and graded mechanically, never by "
      "impression): total verdict coverage, aged rows re-dispatched, a measured gap "
      "versus the baseline, no false motion, and no interference beyond the "
      "mechanism's own writes %s." % ptr(SPEC))
    a("")
    a("The frozen decision rule reads %s %s."
      % (r.quote(SPEC, "Keep if 5/5 assertions pass in 2 consecutive runs."),
         ptr(SPEC)))
    a("")
    a("Both counted runs (runs whose results are scored against the declared budget "
      "and rules and count toward the outcome) ran on %s, and each passed %s of %s "
      "assertions %s." % (run_date, passed, passed, ptr(SPEC, SC1, SC2)))
    a("")
    a("The hypothesis file closes its run table with %s %s."
      % (r.quote(SPEC, "Verdict: KEPT (2 consecutive 5/5)."), ptr(SPEC)))
    a("")
    a("## What it cost")
    a("")
    a("Each counted run launched %s scored child sessions under a declared cap of "
      "$%s per run %s." % (sessions, cap, ptr(RR1, RR2)))
    a("")
    a("The first run's recorded spend was $%s in model usage across %s wall-clock "
      "seconds %s." % (spend1, wall1, ptr(RR1)))
    a("")
    a("The second run's recorded spend was $%s across %s wall-clock seconds %s."
      % (spend2, wall2, ptr(RR2)))
    a("")
    a("## What changed because it was kept")
    a("")
    a("The hypothesis file ends with an on-keep (the change pre-declared to ship if "
      "the experiment is kept, written before any run so that a keep has consequences) "
      "line, committing that %s — in the file's own words — %s %s."
      % (r.quote(SPEC, "the verdict-forcing review cadence"),
         r.quote(SPEC, "enters the resolver/dashboard extension"), ptr(SPEC)))
    a("")
    a("The keep entered the lab journal as write-once fragments (each fragment is a "
      "small numbered journal file recording one result); the keep record's headline "
      "reads %s %s."
      % (r.quote(F0198, "the review-cadence mechanism counts clean"), ptr(F0198)))
    a("")
    a("Then it shipped. The release record titled %s notes the plugin release of %s "
      "carrying %s — the kept mechanism, as product %s."
      % (r.quote(F0201, "hyp 0.2.0 published"), ship_date,
         r.quote(F0201, "review-cadence with the multi-evidence law"), ptr(F0201)))
    a("")
    a("## The artifacts of record")
    a("")
    a("Every pointer above names a tracked file in the lab's repository, pinned at "
      "one git commit and copied byte-for-byte into this experiment's fixture (the "
      "frozen, checksum-pinned bundle of files a run executes against). The full set:")
    a("")
    a("- %s — the hypothesis file: claim, baseline, assertions, decision rule, run "
      "table, and the on-keep line." % ptr(SPEC))
    a("- %s — the lineage record: how the previous revision's instrument defect was "
      "found and refined into this experiment." % ptr(F0196))
    a("- %s — the keep record." % ptr(F0198))
    a("- %s — the wave close-out listing this keep among its results." % ptr(F0200))
    a("- %s — the release record: the kept mechanism shipping as product."
      % ptr(F0201))
    a("- %s — the first counted run's results record: budget, spend, sessions, and "
      "the graded assertions." % ptr(RR1, SC1))
    a("- %s — the second counted run's results record." % ptr(RR2, SC2))
    a("")
    a("Compiled by the case-study renderer as a pure projection of the pinned files "
      "above; recompiling from the same pinned files reproduces this page "
      "byte-for-byte.")
    a("")

    page = "\n".join(L)
    manifest = {"page": "case-study.md",
                "grammar": "fact_fidelity.py (frozen fact grammar)",
                "facts": sorted(r.facts,
                                key=lambda f: (f["kind"], f["value"],
                                               f["artifacts"]))}
    return page, manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    o = ap.parse_args()
    source_dir = os.path.abspath(o.source)
    out_dir = os.path.abspath(o.out)

    page, manifest = render(source_dir)

    # fail-closed self-checks: the frozen fidelity grammar + the content lint
    fid = fact_fidelity.check(page, manifest, source_dir)
    if not fid["ok"]:
        raise SystemExit("RENDER REFUSE: self fidelity check failed: %s"
                         % json.dumps(fid["problems"][:5]))
    jargon = json.load(open(os.path.join(HERE, "jargon.json"), encoding="utf-8"))
    lint = content_lint.lint(page, jargon)
    if not lint["clean"]:
        raise SystemExit("RENDER REFUSE: self content lint failed: %s"
                         % json.dumps(lint["findings"][:5]))

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "case-study.md"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(out_dir, "extraction-manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    print("rendered: %d facts (%d quotes, %d numbers), lint clean"
          % (fid["facts_extracted"], fid["quotes_extracted"],
             fid["numbers_extracted"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
