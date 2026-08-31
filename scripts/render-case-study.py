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

H-225 CANDIDATE — the optional `--vocab` gloss-lookup join (default OFF; absent
flag = the shipped code path, byte-unchanged):
  * `--vocab <path>` names a house-vocabulary JSON (v2 schema; v1 files load with
    defaults) read as a SUPERSET of the frozen jargon floor by construction: the
    floor keeps its hardcoded page glosses and its unchanged lint; the vocabulary
    only ever ADDS glosses, never edits the floor.
  * trigger: files in the pinned source bundle BEYOND the fact table (extra
    artifacts). For each house-only vocabulary term those extra artifacts use in
    prose, the page's FIRST bare prose use of that term (same span exclusions as
    the content lint) gains the vocabulary gloss byte-for-byte: `term (gloss)`.
    A bundle that is exactly the fact table — the counted reference — is a
    structural no-op: the page reproduces byte-identically with and without the
    flag.
  * unregistered coinages in extra artifacts (tokens resolving through neither
    the pinned wordlist beside the vocabulary nor the vocabulary's own forms,
    used twice or more in one artifact) are REPORTED on stdout
    (VOCAB-REPORT\tunregistered-coinage\t...) and never glossed: no gloss text
    is ever invented — every inserted byte comes from a vocabulary entry.
  * fail-closed: an unreadable vocabulary, a missing pinned wordlist, or a
    triggered gloss that would break the page grammar (digits, parens, brackets,
    quote characters, under ten characters) REFUSES the render loudly. The
    frozen self-checks below run UNCHANGED on the final (joined) page.

CLI: render_case_study.py --source <fixture/source> --out <dir> [--vocab <vocabulary.json>]
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
F0201 = "experiments/journal-fragments/0201-crux-0-2-0-published.md"
RR1 = "experiments/runs/H-188/run-1/run-record.json"
SC1 = "experiments/runs/H-188/run-1/h188-score.json"
RR2 = "experiments/runs/H-188/run-2/run-record.json"
SC2 = "experiments/runs/H-188/run-2/h188-score.json"

OUTPUTS = ("case-study.md", "extraction-manifest.json")

FACT_RELS = (SPEC, F0196, F0198, F0200, F0201, RR1, SC1, RR2, SC2)

# ---------------------------------------------------------------------------
# The --vocab gloss-lookup join (H-225 candidate). Everything above and below
# this block is the shipped renderer byte-unchanged (per-keep constants aside);
# the Renderer class, content laws, and fail-closed self-checks are untouched.
# Term matching, span exclusions, and the coinage scan are vendored VERBATIM
# from the staged clarity-lint-v2 (the artifact-language program's L12
# machinery) and from content_lint's frozen prose-span semantics.
# ---------------------------------------------------------------------------

VOCAB_WORDLIST_NAME = "vocab-wordlist.txt"

_TECH_COMMON = frozenset("""
admin ai api apis ascii auth bash bool boolean backlog byte bytes cert certs changelog
cli config configs commit commits csv curl cwd dashboard dataset datasets dir dirs
email emails filename filenames frontmatter git github glob grep html http https id ids
iso json jsonl kanban llm markdown metadata regex readme repo repos rfc runbook runtime
sandbox semver sha shas sop stderr stdin stdlib stdout timestamp timestamps toml
tooltip tooltips tripwire tsv unicode url urls utf wiki wikipedia workflow workflows
workspace workspaces worktree worktrees yaml zsh
""".split())
_ID_RE = re.compile(r"\b(?:H-\d{2,4}|DEC-\d{2,4}|N\d{1,2}|\d+\.\d+\.\d+"
                    r"|(?:fragment|row)\s+\d{1,4})\b")
_DRAFT_ID_RE = re.compile(r"\b[A-Z]+-DRAFT-[\w-]+")
_URL_RE = re.compile(r"https?://\S+")
_PATH_RE = re.compile(r"(?:~?/|\.{1,2}/)?(?:[\w.@-]+/)+[\w.@*-]+"
                      r"|\b[\w-]+\.(?:py|md|json|jsonl|sh|html|yml|yaml|png|txt|tsv|log|patch)\b")
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019/-]*[A-Za-z]|[A-Za-z]")
_SUFFIXES = ("ings", "ing", "ers", "ors", "ies", "es", "ed", "er", "or", "ly", "s")
_PREFIXES = ("counter", "anti", "micro", "multi", "super", "inter", "over", "post",
             "pre", "under", "non", "mis", "sub", "co", "re", "un", "de")


def _vocab_refuse(msg):
    raise SystemExit("RENDER REFUSE: " + msg)


def _phrase_re(forms):
    return re.compile(r"\b(?:" + "|".join(
        re.escape(f).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]+")
        for f in sorted(forms, key=len, reverse=True)) + r")\b", re.I)


def _strip_excluded(text):
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub("\u201c[^\u201d\n]{0,300}\u201d", " ", text)
    text = re.sub(r'"[^"\n]{0,300}"', " ", text)
    text = _URL_RE.sub(" ", text)
    text = _PATH_RE.sub(" ", text)
    return text


def _lookup(w, known):
    if len(w) <= 1:
        return False
    if w in known:
        return True
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[: -len(suf)]
            if base in known or base + "e" in known:
                return True
            if len(base) >= 3 and base[-1] == base[-2] and base[:-1] in known:
                return True
    return False


def _resolves(w, known):
    w = re.sub(r"['\u2019]s$", "", w.strip("'\u2019")).lower()
    if not w or len(w) == 1 or any(c.isdigit() for c in w):
        return True
    if _lookup(w, known):
        return True
    for p in _PREFIXES:
        if w.startswith(p) and len(w) - len(p) >= 3 and _lookup(w[len(p):], known):
            return True
    return False


def _coinage_counts(prose, forms, known):
    prose = _DRAFT_ID_RE.sub(" ", _ID_RE.sub(" ", prose))
    counts = {}
    for tok in _TOKEN_RE.findall(prose):
        if len(tok) == 1 or any(c.isdigit() for c in tok):
            continue
        low = tok.lower()
        if low in forms:
            continue
        parts = [p for p in re.split(r"[/-]", tok) if p]
        if len(parts) > 1:
            fires = any(len(p) == 1 for p in parts) or \
                not all(_resolves(p, known) or p.lower() in forms for p in parts)
        elif _CAMEL_RE.search(tok):
            camel = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", tok)
            fires = not all(_resolves(p, known) for p in camel)
        else:
            fires = not _resolves(tok, known)
        if fires:
            counts[low] = counts.get(low, 0) + 1
    return counts


def _sorted_walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _first_prose_use(page, forms):
    """First prose use of any form across the page, per the content lint's own
    span exclusions and gloss-adjacency grammar. -> (lineno, bare) or (None, None)."""
    rx = _phrase_re(forms)
    for i, prose in content_lint.prose_lines(page):
        m = rx.search(prose)
        if not m:
            continue
        return i, not re.match(content_lint.GLOSS_PAREN, prose[m.end():])
    return None, None


def _insert_gloss(page, lineno, forms, gloss):
    """Insert ' (gloss)' after the first non-excluded match on the given raw line."""
    rx = _phrase_re(forms)
    lines = page.split("\n")
    raw = lines[lineno - 1]
    excluded = [m.span() for m in content_lint.POINTER_RE.finditer(raw)]
    excluded += [m.span() for m in content_lint.QUOTE_RE.finditer(raw)]
    for m in rx.finditer(raw):
        if any(a <= m.start() < b or a < m.end() <= b for a, b in excluded):
            continue
        lines[lineno - 1] = raw[:m.end()] + " (" + gloss + ")" + raw[m.end():]
        return "\n".join(lines), m.group(0)
    _vocab_refuse("gloss join lost the prose match on page line %d" % lineno)


def vocab_join(page, source_dir, vocab_path):
    """-> (page, report_lines). Pure function of the page bytes, the source tree,
    the vocabulary file, and the pinned wordlist beside it. Fail-closed throughout."""
    try:
        vocab = json.load(open(vocab_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        _vocab_refuse("--vocab file unreadable: %s" % e)
    terms = vocab.get("terms")
    if not isinstance(terms, dict) or not terms:
        _vocab_refuse("--vocab file carries no terms dict")
    wordlist_path = os.path.join(os.path.dirname(os.path.abspath(vocab_path)),
                                 VOCAB_WORDLIST_NAME)
    if not os.path.isfile(wordlist_path):
        _vocab_refuse("pinned wordlist missing beside --vocab: %s" % wordlist_path)
    forms = {}
    for head in sorted(terms):
        for f in [head] + list(terms[head].get("variants") or []):
            forms.setdefault(str(f).lower(), head)
    known = set(_TECH_COMMON)
    with open(wordlist_path, encoding="utf-8", errors="ignore") as f:
        known.update(w.strip().lower() for w in f)
    known.update(w.lower() for form in forms for w in re.split(r"[\s/-]+", form) if w)

    extras = [rel for rel in _sorted_walk(source_dir) if rel not in set(FACT_RELS)]
    report = []
    if not extras:
        return page, report  # the counted reference bundle: structurally a no-op

    triggered = {}
    coinages = {}
    for rel in extras:
        with open(os.path.join(source_dir, rel), "rb") as f:
            raw = f.read().decode("utf-8", errors="replace")
        prose = _strip_excluded(raw)
        for head in sorted(terms):
            if str(terms[head].get("status", "house-only")) != "house-only":
                continue
            fs = [head] + list(terms[head].get("variants") or [])
            if _phrase_re(fs).search(prose):
                triggered.setdefault(head, []).append(rel)
        for tok, n in sorted(_coinage_counts(prose, forms, known).items()):
            if n >= 2:
                coinages[(tok, rel)] = n

    for head in sorted(triggered):
        gloss = str(terms[head].get("gloss") or "")
        fs = [head] + list(terms[head].get("variants") or [])
        lineno, bare = _first_prose_use(page, fs)
        if lineno is None:
            report.append("VOCAB-JOIN\tnot-in-page\t%s" % head)
            continue
        if not bare:
            report.append("VOCAB-JOIN\talready-glossed\t%s" % head)
            continue
        if len(gloss) < 10:
            _vocab_refuse("vocabulary gloss for %r is under ten characters" % head)
        if re.search("[\\d()\\[\\]\"\u201c\u201d\\n]", gloss):
            _vocab_refuse("vocabulary gloss for %r carries digits, parens, brackets, "
                          "or quote characters the page grammar reserves" % head)
        page, matched = _insert_gloss(page, lineno, fs, gloss)
        report.append("VOCAB-JOIN\tglossed\t%s\tline %d\t%s" % (head, lineno, matched))

    for (tok, rel), n in sorted(coinages.items()):
        report.append("VOCAB-REPORT\tunregistered-coinage\t%s\t%dx\t%s\t"
                      "no gloss invented" % (tok, n, rel))
    return page, sorted(report)



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
      % (r.quote(F0201, "crux 0.2.0 published"), ship_date,
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
    ap.add_argument("--vocab", default=None,
                    help="optional house-vocabulary JSON read as a superset of "
                         "the frozen jargon floor (default off = shipped path)")
    o = ap.parse_args()
    source_dir = os.path.abspath(o.source)
    out_dir = os.path.abspath(o.out)

    page, manifest = render(source_dir)
    vocab_report = []
    if o.vocab:
        page, vocab_report = vocab_join(page, source_dir, os.path.abspath(o.vocab))

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
    for line in vocab_report:
        print(line)
    print("rendered: %d facts (%d quotes, %d numbers), lint clean"
          % (fid["facts_extracted"], fid["quotes_extracted"],
             fid["numbers_extracted"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
