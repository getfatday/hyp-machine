#!/usr/bin/env python3
"""preflight-rigor.py -- REPORT-ONLY rigor rows beside preflight: the six ethics rows and the
thirteen spec-rigor rows. Findings are printed rows; the exit code is untouched -- this script
gates NOTHING. Usage guide: docs/preflight-rigor.md in this plugin.

Part 1 -- the ethics extension (counted under H-132-ethics-gate in the source lab, kept
2026-08-27, two consecutive counted 4/4: every seeded human-subject defect tripped exactly its
intended check, zero fires across the frozen must-silent corpus, retrofit specs passed
unchanged, full-corpus double pass byte-identical. Shipped as counted from the fixture copy
`impl/ethics_checks.py` -- only provenance framing and the script name differ). The gated
integration (`## Ethical assumptions` into preflight's required sections, ethics FAIL ->
ESCALATE) happens only on an explicit maintainer ruling. Do not wire this into a blocking path
yourself.

Calibrated report-only semantics of the ethics rows, frozen with the counted fixture:
1. ethics-section-present fires (FAIL row) only when SUBJECT_SIGNALS hit in Hypothesis+Method
   AND the section is absent. A sectionless spec with NO signal hits reports PASS --
   pre-existing specs are never re-gated.
2. When the section is absent, checks 2-6 report SKIP ("absence owned by
   ethics-section-present") so that defect class trips exactly one check (zero cross-fires).
Ethics rows (6 per spec; ethics-tier-mismatch is check 4's named cross-check, its own row):
  ethics-section-present, ethics-declared, ethics-nonempty, ethics-consent-artifact,
  ethics-tier-mismatch, ethics-sim-dignity

Part 2 -- the thirteen spec-rigor rows (counted under H-DRAFT-50b0c1da-spec-rigor-rows-v2 in
the source lab, kept 2026-09-09, 5/5 in two consecutive counted runs, the second by a cold
README-only executor; journal fragments 0465 and 0469): every row reported FAIL on 100% of its
blind-authored single-defect mutants (90 admissible over three clean seeds) while the pinned
preflight could not tell any mutant from its seed, every named historical incident was
re-found at the pinned snapshot, the two hard candidates held under one fire in twenty on the
modern kept population, and two passes were byte-identical. Shipped from the counted fixture
`rigor_rows.py` (sha256 c95de555...): the detectors are the counted bytes; only the harness
sabotage hook is removed and the repository paths resolve through `.claude/hyp.json`.
  DISCARD-BANKS/NULL-CLASS, CONTROL-PRESENT/OFF-FAILS, TREATMENT-DELIVERED, FROZEN-SPAN-SHA,
  DIRECTIVE-INTERPRETATION/SOURCE-LINE, STAGE1-REVIEWED, REFUTE-REVIEW, LINEAGE-CAP,
  VOID-CLASS, WHO-CARES, PREMORTEM-PRESENT, BLIND-SEEDS, SUBSTANTIVE-ASSERTIONS
A slashed row name is ONE row with two legs; the detail names each leg's finding and the row
FAILs when any leg fails. SKIP owns absence (no trigger, no artifact); one FAIL per defect
class. Row 8 (LINEAGE-CAP) reads the kept lineage stopping rule FIRST (lab
H-DRAFT-5810517d-verdict-lineage-stopping, kept 2026-09-11, fragment 0490; docs/lineage-stopping.md
R0-R4): at depth >= 3 a lineage whose root carries a verified frozen copy is reported from its stream
and spend ledger (PASS lineage-rule:<state> looks= voids= spend=), a frozen copy that does not hash to
the policy reads FAIL lineage-rule:tampered, and only a lineage with no lineage directory falls through
to the legacy `lineage-decision:` path, unchanged. Forward-only law (H-132): no row is flipped here; a later flip to ESCALATE is a
maintainer decision per row on the recorded fire rate and applies to specs registered after the
flip commit only. `--census` evaluates the same predicates over every spec for that measurement.

Read surfaces of the rigor rows (root-relative; <id> = `H-NNN` when the spec stem matches
^H-[0-9]+, else the full stem -- the keep-ship-gate id rule; run dir = <runs_dir>/<id>/):
  Status block      text after the `## Status` heading to the next `## ` heading
  Method span       text after the `## Method` heading up to the `## Binary assertions` heading
  assertions span   text after the `## Binary assertions` heading up to the `## Verdict rule` heading
  frozen span       from the start of the `## Binary assertions` heading line through the
                    `## Verdict rule` heading line (incl. newline) -- the span FROZEN-SPAN-SHA hashes
  verdict span      text after the `## Verdict rule` heading to the next `## ` heading
  keep line         the `- **What "keep" means:**` line (straight or curly quotes)
  artifacts         <runs_dir>/<id>/{VERDICT.json,grade.json,run.json,SHIP.md,REFUTE.md,AMENDMENTS.md,
                    frozen/span.sha,fixture/README.md,stage1/}, the work ledger (kind:decision and
                    kind:decision-resolution rows), <model_dir>/*/actors/*.md, <raw_dir>/<file>.md
  lineage files     <runs_dir>/<lineage root>/lineage/{frozen/rule.json,state.jsonl,looks.jsonl,voids.jsonl,
                    spend.jsonl} and a successor's <runs_dir>/<id>/lineage/inherits.json (row 8 under the
                    lineage stopping rule; the root resolves through the pointer chain, then refined-into)
Paths come from <repo-root>/.claude/hyp.json when present (hypotheses_dir, runs_dir, raw_dir,
model_dir, ledger_file) and default to the lab layout (hypotheses, experiments/runs, research/raw,
operating-model). Decision rows are read from the configured `ledger_file` (plugin default
ledger/ledger.jsonl) AND from ledger/work-ledger.jsonl, the counted contract's ledger.

Grammar (byte-stable): per spec the six ethics rows, then the thirteen rigor rows, then one META line
    STATUS<TAB>check<TAB>relpath<TAB>detail            STATUS in PASS / FAIL / SKIP
    META<TAB>relpath<TAB>{json}                         ethics keys as before + "rigor": {...}
The first six rows per spec are byte-identical to the six-row output printed before the rigor rows landed; the rigor rows are appended.

CLI (stdlib only, Python 3.9; exit 0 always in report modes -- findings never change the exit code):
    preflight-rigor.py <repo-root> <spec.md> [...]    rows for each spec
    preflight-rigor.py --census <repo-root>           every <hypotheses_dir>/H-*.md, then one
                                                      CENSUS<TAB>row<TAB>FAIL=<TAB>PASS=<TAB>SKIP=<TAB>n= line per row
    preflight-rigor.py --selftest                     seeded positives and clean negatives per rigor row over
                                                      the counted fixture's three seeds; PASS/FAIL per check;
                                                      exit 0 when every check passed, 1 otherwise
"""
import hashlib
import json
import os
import re
import subprocess
import sys

# ====================================================================== Part 1: the ethics rows (H-132)
CHECKS = [
    "ethics-section-present",
    "ethics-declared",
    "ethics-nonempty",
    "ethics-consent-artifact",
    "ethics-tier-mismatch",
    "ethics-sim-dignity",
]

# Counted contract check 2 — verbatim.
SUBJECT_SIGNALS = [
    r'\bhumans?\b', r'\breaders?\b', r'\bpersonas?\b', r'\bparticipants?\b',
    r'\bsim[- ]users?\b', r'\bsimulated (?:user|human|consumer|student|reader)',
    r'\bscorers?\b', r'\bblind panel', r'\binterview', r'\binvite', r'\bonboard',
    r'\bsecond[- ]human\b', r'\bmaintainer\b',
]
# Counted contract check 4 cross-check — verbatim.
TIER_RE = re.compile(r'\binvite|second[- ]human|real human\b', re.I)
# Counted contract check 4 — verbatim path shape.
PATH_RE = re.compile(r'[\w./-]+\.(?:md|json|ya?ml|txt)')

SECTION_MARKER = '## Ethical assumptions'
KEY_NAMES = ('subjects', 'consent', 'data', 'withdrawal', 'deception', 'sim-dignity')
_KEYLINE = re.compile(r'^\s*-?\s*(subjects|consent|data|withdrawal|deception|sim-dignity)\s*:\s*(.*)$')
_SIGNALS_C = [re.compile(p, re.I) for p in SUBJECT_SIGNALS]


def strip_comments(s):
    return re.sub(r'<!--.*?-->', '', s, flags=re.S)


def slice_after(text, marker, stops):
    """Text after `marker` up to the earliest of `stops` (or EOF). '' if marker absent."""
    if marker not in text:
        return ''
    rest = text.split(marker, 1)[1]
    cut = len(rest)
    for s in stops:
        m = re.search(s, rest)
        if m and m.start() < cut:
            cut = m.start()
    return rest[:cut]


def hyp_method_scan_text(text):
    """Counted contract check 2: 'scan Hypothesis+Method text only (NOT the whole file)'.
    Method slice mirrors preflight.py: split('## Method')[1].split('## Binary assertions')[0]."""
    hyp = slice_after(text, '## Hypothesis', [r'\n## '])
    method = slice_after(text, '## Method', [r'## Binary assertions'])
    return hyp, method


def signal_hits(scan_text):
    return sorted(p.pattern for p in _SIGNALS_C if p.search(scan_text))


def ethics_section(text):
    if SECTION_MARKER not in text:
        return None
    rest = text.split(SECTION_MARKER, 1)[1]
    m = re.search(r'\n## ', rest)
    return rest[:m.start()] if m else rest


def keyed_lines(section_text):
    """Parse the keyed lines (comment-stripped; leading '- ' optional; wrapped
    continuation lines are folded into the current key's value)."""
    out = {}
    cur = None
    for ln in strip_comments(section_text).split('\n'):
        m = _KEYLINE.match(ln)
        if m:
            cur = m.group(1)
            out[cur] = m.group(2).strip()
        elif cur is not None:
            if ln.strip() == '':
                cur = None
            else:
                out[cur] = (out[cur] + ' ' + ln.strip()).strip()
    return out


def _norm_subjects(val):
    return re.sub(r'\s+', ' ', (val or '')).strip().rstrip('.').lower()


def is_bare_none(val):
    return _norm_subjects(val) == 'none'


def is_none_with_reason(val):
    return bool(re.match(r'^\s*none\s*(—|–|--|-)\s*\S', (val or '')))


def is_none_family(val):
    return is_bare_none(val) or is_none_with_reason(val)


def _resolving_paths(line, root):
    """Paths named on `line` (contract regex) that exist under `root` at HEAD.
    Hardening (semantics-neutral for honest repo-relative paths): candidates that
    normalize outside `root` are never resolved."""
    found = []
    root = os.path.abspath(root)
    for m in PATH_RE.finditer(line or ''):
        cand = m.group(0).lstrip('/')
        full = os.path.normpath(os.path.join(root, cand))
        if not (full == root or full.startswith(root + os.sep)):
            continue
        if os.path.exists(full):
            found.append(cand)
    return found


def evaluate(text, root):
    """Run the six ethics report rows over one spec text.

    Returns (rows, meta): rows = [(check, STATUS, detail)] in CHECKS order,
    STATUS in PASS/FAIL/SKIP; meta = dict(signals, section_present, subjects,
    route) — route in {'no-signals','escape','declared','unsectioned-silent',
    'fires'}.
    """
    hyp, method = hyp_method_scan_text(text)
    sigs = signal_hits(hyp + '\n' + method)
    siglist = ', '.join(sigs[:4]) + (', ...' if len(sigs) > 4 else '')
    sec = ethics_section(text)
    rows = []

    if sec is None:
        if sigs:
            rows.append(("ethics-section-present", "FAIL",
                         "subject signals hit (%s) and '## Ethical assumptions' missing "
                         "(would be MALFORMED post-flip; report-only)" % siglist))
        else:
            rows.append(("ethics-section-present", "PASS",
                         "section absent, no subject signals in Hypothesis+Method "
                         "(pre-template spec; pre-existing specs never re-gated)"))
        for c in CHECKS[1:]:
            rows.append((c, "SKIP", "no section; absence owned by ethics-section-present"))
        meta = {"signals": sigs, "section_present": False, "subjects": None,
                "route": "fires" if sigs else "unsectioned-silent"}
        return rows, meta

    rows.append(("ethics-section-present", "PASS", "'## Ethical assumptions' present"))
    kv = keyed_lines(sec)
    subj = kv.get('subjects')  # None when the line is missing entirely

    # 2. ethics-declared
    if not sigs:
        rows.append(("ethics-declared", "PASS", "no subject signals in Hypothesis+Method"))
        declared_fail = False
    elif subj is None or subj == '':
        rows.append(("ethics-declared", "FAIL",
                     "subject signals hit (%s); subjects line missing or placeholder" % siglist))
        declared_fail = True
    elif is_bare_none(subj):
        rows.append(("ethics-declared", "FAIL",
                     "subject signals hit (%s); bare 'none' lacks the '— <reason>' clause" % siglist))
        declared_fail = True
    elif is_none_with_reason(subj):
        rows.append(("ethics-declared", "PASS",
                     "signals over-trigger absorbed by the 'none — <reason>' escape (one clause)"))
        declared_fail = False
    else:
        rows.append(("ethics-declared", "PASS", "subjects declared with non-placeholder content"))
        declared_fail = False

    subject_declared = subj is not None and subj != '' and not is_none_family(subj)

    # 3. ethics-nonempty
    if not subject_declared:
        rows.append(("ethics-nonempty", "PASS",
                     "subjects is none/undeclared; keyed-line completeness not triggered"))
        nonempty_fail = False
    else:
        missing = [k for k in ('consent', 'data', 'withdrawal', 'deception')
                   if not (kv.get(k) or '').strip()]
        if missing:
            rows.append(("ethics-nonempty", "FAIL",
                         "subjects declared; keyed line(s) missing or empty after comment strip: "
                         + ", ".join(missing)))
            nonempty_fail = True
        else:
            rows.append(("ethics-nonempty", "PASS",
                         "consent/data/withdrawal/deception present and non-empty"))
            nonempty_fail = False

    real_human = bool(re.search(r'real[- ]human', subj or '', re.I))

    # 4. ethics-consent-artifact
    if not real_human:
        rows.append(("ethics-consent-artifact", "PASS",
                     "no real-human subject; consent-artifact resolution not triggered"))
        consent_fail = False
    else:
        resolved = _resolving_paths(kv.get('consent', ''), root)
        if resolved:
            rows.append(("ethics-consent-artifact", "PASS",
                         "consent artifact resolves: " + resolved[0]))
            consent_fail = False
        else:
            rows.append(("ethics-consent-artifact", "FAIL",
                         "real human named, no committed consent artifact resolves"))
            consent_fail = True

    # 4b. ethics-tier-mismatch (contract check 4 cross-check; Method slice only)
    if not TIER_RE.search(method):
        rows.append(("ethics-tier-mismatch", "PASS", "no real-human-interaction terms in Method"))
        tier_fail = False
    elif real_human or is_none_with_reason(subj):
        rows.append(("ethics-tier-mismatch", "PASS",
                     "Method names a real-human interaction; subjects declares "
                     + ("real-human" if real_human else "'none — <reason>'")))
        tier_fail = False
    else:
        rows.append(("ethics-tier-mismatch", "FAIL",
                     "Method hits invite|second-human|real-human but subjects declares "
                     "neither 'real-human' nor 'none — <reason>'"))
        tier_fail = True

    # 5. ethics-sim-dignity
    sim_persona = bool(re.search(r'sim[- ]persona', subj or '', re.I))
    if not sim_persona:
        rows.append(("ethics-sim-dignity", "PASS", "no sim-persona subject; not triggered"))
        sim_fail = False
    else:
        sd = (kv.get('sim-dignity') or '').strip()
        grounded = bool(re.search(r'transcript[- ]grounded', subj or '', re.I))
        if not sd:
            rows.append(("ethics-sim-dignity", "FAIL",
                         "sim-persona declared; sim-dignity line missing or empty"))
            sim_fail = True
        elif grounded and not _resolving_paths(kv.get('consent', ''), root):
            rows.append(("ethics-sim-dignity", "FAIL",
                         "transcript-grounded provenance; consent line resolves no committed "
                         "path (grounded cards inherit the source humans' consent surface)"))
            sim_fail = True
        else:
            rows.append(("ethics-sim-dignity", "PASS",
                         "sim-dignity present" + ("; grounded consent surface resolves" if grounded else "")))
            sim_fail = False

    any_fail = declared_fail or nonempty_fail or consent_fail or tier_fail or sim_fail
    if any_fail:
        route = "fires"
    elif not sigs:
        route = "no-signals"
    elif is_none_with_reason(subj):
        route = "escape"
    else:
        route = "declared"
    meta = {"signals": sigs, "section_present": True, "subjects": subj, "route": route}
    return rows, meta


def report_lines(relpath, rows):
    """Byte-stable report rows: STATUS<TAB>check<TAB>relpath<TAB>detail."""
    return ["%s\t%s\t%s\t%s" % (st, ck, relpath, dt) for (ck, st, dt) in rows]


def fails_of(rows):
    return {ck: dt for (ck, st, dt) in rows if st == "FAIL"}


# ====================================================================== Part 2: the thirteen spec-rigor rows
RIGOR_ROWS = [
    "DISCARD-BANKS/NULL-CLASS",
    "CONTROL-PRESENT/OFF-FAILS",
    "TREATMENT-DELIVERED",
    "FROZEN-SPAN-SHA",
    "DIRECTIVE-INTERPRETATION/SOURCE-LINE",
    "STAGE1-REVIEWED",
    "REFUTE-REVIEW",
    "LINEAGE-CAP",
    "VOID-CLASS",
    "WHO-CARES",
    "PREMORTEM-PRESENT",
    "BLIND-SEEDS",
    "SUBSTANTIVE-ASSERTIONS",
]
HARD_CANDIDATES = ("FROZEN-SPAN-SHA", "LINEAGE-CAP")  # proposed for a later ESCALATE flip; bound asserted in the lab

# Row 8 contract cell "SKIP when: depth < 3" (the contract cell is canon; the v2 key lists depth 0/1 as must-SKIP).
LINEAGE_UNDER_CAP_STATUS = "SKIP"

# Row 8 under the lineage stopping rule (docs/lineage-stopping.md R0-R4; lab keep H-DRAFT-5810517d-verdict-lineage-stopping,
# 2026-09-11, fragment 0490): at depth >= 3 the row reads the lineage's stream and spend ledger BEFORE the legacy
# `lineage-decision:` path -- a lineage under the rule is never asked for a count. A frozen copy
# <runs_dir>/<root>/lineage/frozen/rule.json is under the rule only when its `rule_text` bytes hash to the policy sha
# (sha256 of rules/lineage-sprt.json) and its `sha256` field agrees; anything else at that path reads FAIL lineage-rule:tampered.
LINEAGE_POLICY_SHA256 = "8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1"
LINEAGE_R1_TOL = 1e-9          # R1: cum + one per-run cap > budget + tol on either component refuses the next launch
LINEAGE_INHERIT_HOPS = 16      # R4 pointer-chain bound (lineage-stopping.py resolve_lineage)
LINEAGE_TRUNCATION_DEFAULT = 13

NULL_CLASSES = ("refuted-claim", "threshold-miss", "instrument", "ambiguous", "annulled")
VOID_CLASSES = ("ambiguous", "annulled")
PRIOR_RUNS_VALUES = ("re-graded", "voided", "unchanged")
RUN_VALIDITY_LABELS = {
    "determinism", "grading determinism", "containment", "isolation", "isolation and scope",
    "two-pass", "byte-identical", "selftest", "zero writes", "reproducibility",
}
OFF_TOKENS_RE = re.compile(r"\bOFF\b|\bbaseline\b|\bcontrol\b|\barm-B\b|\bunpatched\b|\bshipped\b")
OFF_TOKENS_CI_RE = re.compile(r"\bbaseline\b|\bcontrol\b|\barm-B\b|\bunpatched\b|\bshipped\b", re.I)
COMPARATOR_RE = re.compile(r"<=|>=|=<|=>|<|>|=|≤|≥|\bat most\b|\bat least\b|\bno more than\b|\bfewer than\b|\bmore than\b|\bexactly\b", re.I)
POSITIVE_CONTROL_RE = re.compile(r"\bseeded\b|\bmutants?\b|\bplanted\b|\bsabotaged\b", re.I)
NEGATIVE_CONTROL_RE = re.compile(r"\bclean corpus\b|\bmust-silent\b|\bzero findings\b|\bcontrol range\b", re.I)
TREATMENT_TOKENS_RE = re.compile(r"\bpremise\b|\bpre-check\b|\bmanipulation check\b|\bmarkers?\b|\btreatment delivered\b", re.I)
MANIP_LINE_RE = re.compile(r"(?m)^\s*(?:[-*]\s+|\d+\.\s+)?\**Manipulation check\**(?:\s*\([^)\n]*\))?[^:\n]{0,60}:")
DIRECTIVE_TRIGGER_RE = re.compile(r"research/raw/|\bdirectives?\b|\brulings?\b", re.I)
DESIGN_DOC_RE = re.compile(r"experiments/runs/DESIGN-[\w.-]+")
RAW_CITATION_RE = re.compile(r"research/raw/([\w.\-/]+\.md):(\d+)-(\d+)")
INTERP_BLOCK_RE = re.compile(r"```interpretation-set[^\n]*\n(.*?)\n```", re.S)
# Keyed line (shared definition): the value is the text after the colon on the SAME line; an empty value is an
# absent line -- `:[ \t]*` never swallows the newline (the predecessor's `:\s*` read the next line).
REVIEWED_BY_RE = re.compile(r"(?m)^\s*(?:[-*]\s+)?\**reviewed-by\**:[ \t]*(.+)$")
LINEAGE_DECISION_RE = re.compile(r"(?m)^\s*(?:[-*]\s+)?\**lineage-decision\**:[ \t]*(.+)$")
REFINED_INTO_RE = re.compile(r"refined[- ]into:?\s*\(?\s*(H-[A-Za-z0-9-]+)")
DEC_ID_RE = re.compile(r"\bDEC-\d+\b")
VOID_CLAUSE_RE = re.compile(r"\bvoid\b|\buncounted\b|\bnot counted\b", re.I)
KEEP_LINE_RE = re.compile(r"(?m)^\s*[-*]\s*\**What\s+[\"“”']keep[\"“”']\s+means:?\**:?\s*(.*)$")
MAGNITUDE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|x\b|/\s*\d+|ms\b|s\b)|\btoday\b", re.I)
PREMORTEM_KEY_RE = re.compile(r"(?m)^\s*(?:[-*]\s+)?\**Pre-mortem\b[^\n]*?:\s*$")
PREMORTEM_MAP_RE = re.compile(r"->\s*(A\d+\b|void:[\w-]+|banks\b)")
SEED_MENTION_RE = re.compile(r"\bseeded\b|\bmutants?\b|\bplanted\b", re.I)
KEYED_ID_RE = lambda key: re.compile(r"(?m)^\s*(?:[-*]\s+)?\**" + key + r"\**:[ \t]*(\S.*)$")  # noqa: E731
IDLIST = r"(?:[A#]?\d+)(?:\s*(?:,|and|or|-|–|to|/)\s*[A#]?\d+)*"
DISCARD_SENTENCE_RE = re.compile(
    r"\bdiscards?\s+(?:driven\s+by|on|where|when)\s+assertions?\s+(" + IDLIST + r")\b[^.;]{0,200}?\bbanks\b", re.I)
DISCARD_KEYED_RE = re.compile(r"(?m)^\s*(?:[-*]\s+)?\**Discard banks\**:[ \t]*(.+)$")
ASSERTION_MENTION_RE = re.compile(r"\bassertions?\s+(" + IDLIST + r")|\bA(\d+)\b|#(\d+)\b", re.I)
RANGE_RE = re.compile(r"(\d+)\s*(?:-|–|to)\s*(\d+)")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
HEX64_RE = re.compile(r"[0-9a-fA-F]{64}")

# Repository layout: <repo-root>/.claude/hyp.json keys, lab defaults (the counted fixture's layout).
HYP_JSON_REL = os.path.join(".claude", "hyp.json")
LAYOUT_DEFAULTS = {
    "hypotheses_dir": "hypotheses",
    "runs_dir": "experiments/runs",
    "raw_dir": "research/raw",
    "model_dir": "operating-model",
    "ledger_file": "ledger/ledger.jsonl",  # the decision kit's default (scripts/decisions.py)
}
WORK_LEDGER_REL = "ledger/work-ledger.jsonl"  # the counted contract's ledger (lab layout); always read too
DEPLOY_PREFIX = "experiments/deploy/hyp-machine/"  # keep-ship-gate's plugin-shipped rule (b)/(c)


# ---------------------------------------------------------------- text helpers
def rr_strip_comments(s):
    return COMMENT_RE.sub(" ", s)


def norm_ws(s):
    return re.sub(r"\s+", " ", s)


def heading(text, name):
    """(line_start, body_start, heading_line_end) of the first `## <name>` heading, or None."""
    m = re.compile(r"(?m)^##\s+" + re.escape(name) + r"\b[^\n]*$").search(text)
    if not m:
        return None
    body_start = m.end() + 1 if m.end() < len(text) else m.end()
    return (m.start(), body_start, body_start)


def next_heading_pos(text, frm):
    m = re.compile(r"(?m)^##\s").search(text, frm)
    return m.start() if m else len(text)


def spans(text):
    """Every span the rows read, as (start, end) offsets into `text` (None when the heading is absent)."""
    out = {}
    st = heading(text, "Status")
    out["status"] = (st[1], next_heading_pos(text, st[1])) if st else None
    me = heading(text, "Method")
    ba = heading(text, "Binary assertions")
    vr = heading(text, "Verdict rule")
    out["method"] = (me[1], ba[0] if ba else next_heading_pos(text, me[1])) if me else None
    out["assertions"] = (ba[1], vr[0] if vr else next_heading_pos(text, ba[1])) if ba else None
    out["frozen"] = (ba[0], vr[2]) if (ba and vr) else None
    out["verdict"] = (vr[1], next_heading_pos(text, vr[1])) if vr else None
    return out


def slice_(text, sp):
    return text[sp[0]:sp[1]] if sp else ""


def frozen_span_sha(text):
    sp = spans(text)["frozen"]
    if not sp:
        return None
    return hashlib.sha256(text[sp[0]:sp[1]].encode("utf-8")).hexdigest()


def numbered_items(body):
    """Numbered assertions of a comment-stripped body: [(n, text)] with wrapped lines folded."""
    items = []
    cur = None
    for line in rr_strip_comments(body).split("\n"):
        m = re.match(r"^\s{0,3}(\d+)\.\s+(.*)$", line)
        if m:
            cur = [m.group(1), m.group(2).strip()]
            items.append(cur)
            continue
        if cur is not None:
            if line.strip() == "" or re.match(r"^##\s", line):
                cur = None
            else:
                cur[1] = (cur[1] + " " + line.strip()).strip()
    return [(n, t) for n, t in items]


def spec_id(spec_path):
    stem = os.path.splitext(os.path.basename(spec_path))[0]
    m = re.match(r"^(H-[0-9]+)", stem)
    return m.group(1) if m else stem


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def read_json(path):
    t = read_text(path)
    if t is None:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return {"__unparseable__": True}


def keyed_value(text, key):
    m = KEYED_ID_RE(key).search(text or "")
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- repo-level context (built once per root)
class Root(object):
    """The repository the spec's committed artifacts live in; layout from .claude/hyp.json with lab defaults."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.cfg = dict(LAYOUT_DEFAULTS)
        j = read_json(os.path.join(self.root, HYP_JSON_REL))
        if isinstance(j, dict):
            for k in LAYOUT_DEFAULTS:
                v = j.get(k)
                if isinstance(v, str) and v.strip():
                    self.cfg[k] = v.strip().strip("/")
        self._ledger = None
        self._actors = None
        self._lineage = None

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def rel(self, key, *parts):
        return os.path.join(self.root, *(self.cfg[key].split("/") + list(parts)))

    def rundir(self, sid):
        return self.rel("runs_dir", sid)

    def ledger_files(self):
        out = []
        for relp in (self.cfg["ledger_file"], WORK_LEDGER_REL):
            p = os.path.join(self.root, *relp.split("/"))
            if p not in out:
                out.append(p)
        return out

    def ledger(self):
        """(decision ids, decision-resolution ids) over every configured ledger file that exists."""
        if self._ledger is None:
            dec, res = set(), set()
            for lf in self.ledger_files():
                t = read_text(lf) or ""
                for line in t.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(r, dict):
                        continue
                    if r.get("kind") == "decision" and isinstance(r.get("id"), str):
                        dec.add(r["id"])
                    if r.get("kind") == "decision-resolution" and isinstance(r.get("id"), str):
                        res.add(r["id"])
            self._ledger = (dec, res)
        return self._ledger

    def actors(self):
        if self._actors is None:
            ids = set()
            om = self.rel("model_dir")
            if os.path.isdir(om):
                for ctx in sorted(os.listdir(om)):
                    ad = os.path.join(om, ctx, "actors")
                    if os.path.isdir(ad):
                        for f in sorted(os.listdir(ad)):
                            if f.endswith(".md"):
                                ids.add(f[:-3])
            self._actors = sorted(ids)
        return self._actors

    def specs(self):
        hd = self.rel("hypotheses_dir")
        if not os.path.isdir(hd):
            return []
        return sorted(os.path.join(hd, f) for f in os.listdir(hd) if f.startswith("H-") and f.endswith(".md"))

    def raw_file(self, rel):
        """The cited research/raw/<rel> file: the literal path first, then the configured raw_dir."""
        cands = [self.path("research", "raw", *rel.split("/"))]
        if self.cfg["raw_dir"] != "research/raw":
            cands.append(self.rel("raw_dir", *rel.split("/")))
        for c in cands:
            t = read_text(c)
            if t is not None:
                return t
        return None

    def lineage(self):
        """stem -> list of predecessor stems (specs whose Status says refined-into: <this>)."""
        if self._lineage is None:
            stems = [os.path.splitext(os.path.basename(p))[0] for p in self.specs()]
            stem_set = set(stems)
            preds = {s: [] for s in stems}
            spanshas = {}
            for p, s in zip(self.specs(), stems):
                t = read_text(p) or ""
                spanshas[s] = frozen_span_sha(t)
                sp = spans(t)["status"]
                block = rr_strip_comments(slice_(t, sp)) if sp else ""
                for m in REFINED_INTO_RE.finditer(block):
                    tgt = self._resolve(m.group(1), stem_set)
                    if tgt and tgt != s and s not in preds.setdefault(tgt, []):
                        preds[tgt].append(s)
            self._lineage = (preds, spanshas)
        return self._lineage

    @staticmethod
    def _resolve(ref, stem_set):
        ref = ref.rstrip(".,;:)")
        if ref in stem_set:
            return ref
        cands = sorted(s for s in stem_set if s.startswith(ref + "-"))
        if len(cands) == 1:
            return cands[0]
        m = re.match(r"^(H-[0-9]+)$", ref)
        if m:
            cands = sorted(s for s in stem_set if s == ref or s.startswith(ref + "-"))
            return cands[0] if cands else None
        return cands[0] if cands else None

    def ancestry(self, stem):
        """(depth, root_stem) by the longest predecessor chain (cycle-guarded)."""
        preds, _ = self.lineage()
        best = {}

        def walk(s, seen):
            if s in best:
                return best[s]
            d, r = 0, s
            for p in sorted(preds.get(s, [])):
                if p in seen:
                    continue
                pd, pr = walk(p, seen | {p})
                if pd + 1 > d or (pd + 1 == d and pr < r):
                    d, r = pd + 1, pr
            best[s] = (d, r)
            return best[s]

        return walk(stem, {stem})


# ---------------------------------------------------------------- the thirteen predicates
def expand_ids(idlist_text):
    """'1-3, A5 and 7' -> {'1','2','3','5','7'} (ranges expanded; A/# prefixes dropped)."""
    out = set()
    for a, b in RANGE_RE.findall(idlist_text):
        lo, hi = int(a), int(b)
        if lo <= hi <= lo + 20:
            out.update(str(i) for i in range(lo, hi + 1))
    out.update(re.findall(r"\d+", idlist_text))
    return out


def row_discard_banks(ctx):
    t, sp, rd = ctx["text"], ctx["spans"], ctx["rundir"]
    verdict = rr_strip_comments(slice_(t, sp["verdict"]))
    vn = norm_ws(verdict)
    ids = set(n for n, _ in numbered_items(slice_(t, sp["assertions"])))
    # both branches name an assertion id that is present in the assertions span (ranges expanded)
    sentence = any(expand_ids(m.group(1)) & ids for m in DISCARD_SENTENCE_RE.finditer(vn))
    keyed_ok = False
    for m in DISCARD_KEYED_RE.finditer(verdict):
        for mm in ASSERTION_MENTION_RE.finditer(norm_ws(m.group(1))):
            named = expand_ids(mm.group(1)) if mm.group(1) else {mm.group(2) or mm.group(3)}
            if named & ids:
                keyed_ok = True
    spec_leg = "spec:bank-sentence" if sentence else ("spec:keyed-line" if keyed_ok else "spec:no-bank")
    spec_fail = not (sentence or keyed_ok)
    v = read_json(os.path.join(rd, "VERDICT.json")) if rd else None
    run_fail = False
    if v is None:
        run_leg = "run:skip(no-VERDICT.json)"
    elif not isinstance(v, dict) or v.get("verdict") != "discard":
        run_leg = "run:skip(verdict-not-discard)"
    else:
        nc = v.get("null_class")
        if nc not in NULL_CLASSES:
            run_fail = True
            run_leg = "run:null_class-missing-or-untyped"
        elif nc == "threshold-miss" and not ("observed" in v and "bar" in v):
            run_fail = True
            run_leg = "run:threshold-miss-lacks-observed/bar"
        else:
            run_leg = "run:null_class=%s" % nc
    status = "FAIL" if (spec_fail or run_fail) else "PASS"
    return status, spec_leg + " " + run_leg


def _off_binds(items):
    for _, text in items:
        for clause in re.split(r";|,|\band\b|\bAND\b|\bor\b|\(|\)", text):
            if not (OFF_TOKENS_RE.search(clause) or OFF_TOKENS_CI_RE.search(clause)):
                continue
            if COMPARATOR_RE.search(clause) and re.search(r"\d", clause):
                return True
    return False


def row_control_present(ctx):
    t, sp = ctx["text"], ctx["spans"]
    items = numbered_items(slice_(t, sp["assertions"]))
    binds = _off_binds(items)
    scan = " ".join(x for _, x in items)  # shared definition: numbered assertions only -- Method prose never declares a control
    pos = bool(POSITIVE_CONTROL_RE.search(scan))
    neg = bool(NEGATIVE_CONTROL_RE.search(scan))
    fail = (not binds) and (not pos or not neg)
    detail = "off-fails:%s control-present:%s" % (
        "binds" if binds else "none",
        "pos+neg" if (pos and neg) else ("pos-only" if pos else ("neg-only" if neg else "none")))
    mth = rr_strip_comments(slice_(t, sp["method"]))  # Method mentions are reported for the record only, never counted
    detail += " method-mentions:pos=%d,neg=%d" % (bool(POSITIVE_CONTROL_RE.search(mth)), bool(NEGATIVE_CONTROL_RE.search(mth)))
    return ("FAIL" if fail else "PASS"), detail


def row_treatment_delivered(ctx):
    t, sp, rd = ctx["text"], ctx["spans"], ctx["rundir"]
    method = rr_strip_comments(slice_(t, sp["method"]))
    keyed = bool(MANIP_LINE_RE.search(method))
    items = numbered_items(slice_(t, sp["assertions"]))
    tok = any(TREATMENT_TOKENS_RE.search(x) for _, x in items)
    spec_fail = not (keyed or tok)
    spec_leg = "spec:%s" % ("manipulation-check-line" if keyed else ("assertion-token" if tok else "none"))
    run_fail = False
    if not rd or not os.path.isdir(rd):
        run_leg = "run:skip(no-run-directory)"
    elif not os.path.isfile(os.path.join(rd, "grade.json")):
        run_leg = "run:not-graded"
    else:
        rj = read_json(os.path.join(rd, "run.json"))
        if isinstance(rj, dict) and rj.get("treatment_delivered") is True:
            run_leg = "run:treatment_delivered=true"
        else:
            run_fail = True
            run_leg = "run:graded-without-treatment_delivered"
    return ("FAIL" if (spec_fail or run_fail) else "PASS"), spec_leg + " " + run_leg


def _amendment_complete(am_text):
    if not am_text:
        return False
    parts = re.split(r"(?mi)^.*\bAmendment\s*#?\s*\d+\b.*$", am_text)
    for rec in parts[1:]:
        mot = re.search(r"(?mi)^\s*(?:[-*]\s+)?\**motivation\**:\s*\S", rec)
        ri = re.search(r"(?mi)^\s*(?:[-*]\s+)?\**results-influence\**:\s*\S", rec)
        pr = re.search(r"(?mi)^\s*(?:[-*]\s+)?\**prior-runs\**:[ \t]*(re-graded|voided|unchanged)\b", rec)
        if mot and ri and pr:
            return True
    return False


def row_frozen_span_sha(ctx):
    t, rd = ctx["text"], ctx["rundir"]
    sha_file = os.path.join(rd, "frozen", "span.sha") if rd else None
    recorded = read_text(sha_file) if sha_file else None
    if recorded is None:
        ctx["meta"]["no_frozen_span"] = True
        return "SKIP", "no-frozen-span"
    m = HEX64_RE.search(recorded)
    current = frozen_span_sha(t)
    ctx["meta"]["frozen_span_sha"] = current
    if not m:
        return "FAIL", "span.sha-unreadable"
    rec = m.group(0).lower()
    if current == rec:
        return "PASS", "span-sha-matches %s" % rec[:12]
    if _amendment_complete(read_text(os.path.join(rd, "AMENDMENTS.md"))):
        return "PASS", "span-changed recorded=%s current=%s amended(motivation,results-influence,prior-runs)" % (rec[:12], (current or "")[:12])
    return "FAIL", "span-changed recorded=%s current=%s no-complete-amendment" % (rec[:12], (current or "")[:12])


def _interp_block_ok(text):
    for m in INTERP_BLOCK_RE.finditer(text):
        b = m.group(1)
        if not re.search(r"(?m)^\s*source:\s*\S", b) or not re.search(r"(?m)^\s*verbatim:\s*\S", b):
            continue
        if len(re.findall(r"(?m)^\s*\d+\.\s+\S", b)) < 2:
            continue
        if not re.search(r"(?m)^\s*chosen:\s*\S", b):
            continue
        return True
    return False


def row_directive_interpretation(ctx):
    t, root = ctx["text"], ctx["root"]
    if not DIRECTIVE_TRIGGER_RE.search(t):
        return "SKIP", "no-directive-trigger"
    block = _interp_block_ok(t)
    cited = False
    for m in RAW_CITATION_RE.finditer(t):
        rel, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        raw = root.raw_file(rel)
        if raw is None:
            continue
        n = raw.count("\n") + (0 if raw.endswith("\n") or raw == "" else 1)
        if 1 <= a <= b <= n:
            cited = True
            break
    parts = ["interpretation-set:%s" % ("ok" if block else "missing"), "source-line:%s" % ("ok" if cited else "missing")]
    if not cited and DESIGN_DOC_RE.search(t) and "research/raw/" not in t:
        parts.append("design-doc-only")
    return ("PASS" if (block and cited) else "FAIL"), " ".join(parts)


def row_stage1_reviewed(ctx):
    t, root, rd = ctx["text"], ctx["root"], ctx["rundir"]
    normative = bool(re.search(r"(?m)^Claim type:\s*normative\b", t))
    directive = bool(DIRECTIVE_TRIGGER_RE.search(t))
    if not (normative or directive):
        return "SKIP", "no-trigger"
    trig = "trigger:%s" % ("normative" if normative else "directive")
    m = REVIEWED_BY_RE.search(rr_strip_comments(t))
    if not m:
        return "FAIL", trig + " reviewed-by:absent"
    val = m.group(1).strip()
    dec, res = root.ledger()
    if DEC_ID_RE.fullmatch(val) and val in dec and val in res:  # shared definition: exact id, joined decision + resolution rows
        return "PASS", trig + " reviewed-by:%s(decision+resolution)" % val
    st = os.path.join(rd, "stage1") if rd else None
    if st and os.path.isdir(st):
        files = sorted(f for f in os.listdir(st) if os.path.isfile(os.path.join(st, f)))
        for tok in re.findall(r"[\w./-]+", val):
            base = os.path.basename(tok.rstrip("."))
            if base in files:
                return "PASS", trig + " reviewed-by:stage1/%s" % base
    return "FAIL", trig + " reviewed-by:unresolved(%s)" % val[:40]


def _plugin_shipped(root, v):
    if not isinstance(v, dict):
        return False
    paths = v.get("files_changed_in_on")
    if not isinstance(paths, list):
        return False
    parent = v.get("on_parent") or v.get("off_sha")
    rule_a = False
    if isinstance(parent, str) and re.match(r"^[0-9a-fA-F]{7,40}$", parent) and os.path.isdir(root.path(".git")):
        try:
            rc = subprocess.run(["git", "-C", root.root, "cat-file", "-e", parent + "^{commit}"],
                                capture_output=True, timeout=30).returncode
            rule_a = rc != 0
        except Exception:
            rule_a = False
    for p in paths:
        if not isinstance(p, str):
            continue
        if rule_a or p.startswith(DEPLOY_PREFIX) or os.path.exists(root.path(*(DEPLOY_PREFIX.rstrip("/").split("/") + p.split("/")))):
            return True
    return False


def _recorded_ids(rd):
    ids = set()
    for name in ("SHIP.md", os.path.join("fixture", "README.md")):
        t = read_text(os.path.join(rd, name)) or ""
        for key in ("row-author", "builder-session", "executor-session", "builder", "executor"):
            v = keyed_value(t, key)
            if v:
                ids.add(v.split()[0])
    rj = read_json(os.path.join(rd, "run.json"))
    if isinstance(rj, dict):
        for k in ("builder_session", "executor_session", "row_author", "executor"):
            if isinstance(rj.get(k), str):
                ids.add(rj[k])
    return ids


def row_refute_review(ctx):
    root, rd = ctx["root"], ctx["rundir"]
    ship = bool(rd) and os.path.isfile(os.path.join(rd, "SHIP.md"))
    v = read_json(os.path.join(rd, "VERDICT.json")) if rd else None
    shipped = _plugin_shipped(root, v)
    if not ship and not shipped:
        return "SKIP", "no-SHIP.md no-plugin-shipped-change"
    trig = "SHIP.md" if ship else "plugin-shipped-path"
    ref = read_text(os.path.join(rd, "REFUTE.md"))
    if ref is None:
        return "FAIL", trig + " REFUTE.md:absent"
    if not re.search(r"(?m)^\s*(?:[-*]\s+)?\**blocking-findings\**:\s*0\s*$", ref):
        return "FAIL", trig + " REFUTE.md:blocking-findings!=0"
    refuter = keyed_value(ref, "refuter-session")
    if not refuter:
        return "FAIL", trig + " REFUTE.md:refuter-session-absent"
    if refuter.split()[0] in _recorded_ids(rd):
        return "FAIL", trig + " REFUTE.md:refuter-equals-builder-or-executor"
    return "PASS", trig + " REFUTE.md:blocking-findings=0 refuter-distinct"


def _read_jsonl(path):
    """Dict rows of a JSONL file (blank and unparseable lines skipped); [] when absent."""
    out = []
    for line in (read_text(path) or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or float(x) != int(x):
        return None
    return int(x)


def _fmt(x, places):
    """Canonical decimal for the detail line: trailing zeros dropped, at least one decimal digit kept (0.0, 1.3, 5753.6)."""
    s = ("%." + str(places) + "f") % x
    s = s.rstrip("0").rstrip(".")
    return s if "." in s else s + ".0"


def _stem_id(stem):
    """The run-directory id of a spec stem (spec_id's rule applied to a stem)."""
    m = re.match(r"^(H-[0-9]+)", stem)
    return m.group(1) if m else stem


def _lineage_dir(root, lane):
    return os.path.join(root.rundir(lane), "lineage")


def _follow_inherits(root, lane):
    """R4: follow <runs_dir>/<lane>/lineage/inherits.json `lineage_root` pointers -> (root lane, hops). A missing,
    unreadable, cyclic or over-long pointer ends the walk at the last lane reached (report-only: nothing raises)."""
    hops = [lane]
    cur = lane
    while True:
        inh = read_json(os.path.join(_lineage_dir(root, cur), "inherits.json"))
        nxt = inh.get("lineage_root") if isinstance(inh, dict) else None
        if (not isinstance(nxt, str) or not nxt or "/" in nxt or os.sep in nxt or nxt in (".", "..")
                or nxt in hops or len(hops) > LINEAGE_INHERIT_HOPS):
            return cur, hops
        hops.append(nxt)
        cur = nxt


def _lineage_under_rule(root, sid, rootstem):
    """(lane, lineage dir) of the lineage a depth>=3 spec is under, or (None, None). Resolution order: the successor's
    own inherits.json chain (R4 -- the pointer names the root), then the refined-into root's run directory, then the
    spec's own; the first that carries frozen/rule.json wins."""
    lane, hops = _follow_inherits(root, sid)
    cands = ([lane] if len(hops) > 1 else []) + [_stem_id(rootstem), sid]
    seen = set()
    for lane in cands:
        if lane in seen:
            continue
        seen.add(lane)
        d = _lineage_dir(root, lane)
        if os.path.isfile(os.path.join(d, "frozen", "rule.json")):
            return lane, d
    return None, None


def _frozen_rule_ok(path):
    """(verified, parsed rule): the frozen copy's `rule_text` bytes hash to the policy sha AND its `sha256` field agrees."""
    doc = read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("rule_text"), str):
        return False, None
    if (hashlib.sha256(doc["rule_text"].encode("utf-8")).hexdigest() != LINEAGE_POLICY_SHA256
            or doc.get("sha256") != LINEAGE_POLICY_SHA256):
        return False, None
    try:
        rule = json.loads(doc["rule_text"])
    except ValueError:
        rule = None
    return True, (rule if isinstance(rule, dict) else None)


def _r1_permits(header, cum_usd, cum_wall):
    """lineage-stopping.py r1_predicate: the next launch is permitted while cum + one per-run cap stays within BOTH budget
    components; a header without budget/per_run_cap never refuses."""
    b, cap = header.get("budget"), header.get("per_run_cap")
    if not isinstance(b, dict) or not isinstance(cap, dict):
        return True
    return not (cum_usd + _num(cap.get("usd")) > _num(b.get("usd")) + LINEAGE_R1_TOL
                or cum_wall + _num(cap.get("wall_s")) > _num(b.get("wall_s")) + LINEAGE_R1_TOL)


def _lineage_rule_state(ldir, rule):
    """(state token, looks, voids, cum_usd, cum_wall_s) of a lineage whose frozen copy verified. The token follows
    lineage-stopping.py `state`: the instrument's terminal from the last state.jsonl line (promote | hold | max-looks),
    else spend-exhausted when R1 would refuse the next launch, else no-looks-yet | insufficient n=<looks>/<truncation>."""
    looks = _read_jsonl(os.path.join(ldir, "looks.jsonl"))
    voids = _read_jsonl(os.path.join(ldir, "voids.jsonl"))
    spend = _read_jsonl(os.path.join(ldir, "spend.jsonl"))
    header = spend[0] if spend and spend[0].get("header") is True else {}
    charges = spend[1:] if header else spend
    cum_usd = sum(_num(r.get("cost_usd")) for r in charges)
    cum_wall = sum(_num(r.get("wall_s")) for r in charges)
    states = _read_jsonl(os.path.join(ldir, "state.jsonl"))
    last = str(states[-1].get("state", "")) if states else ""
    if last == "evidence-sufficient promote":
        tok = "promote"
    elif last == "evidence-sufficient hold":
        tok = "hold"
    elif last.startswith("evidence-insufficient max-looks"):
        tok = "max-looks"
    elif header and not _r1_permits(header, cum_usd, cum_wall):
        tok = "spend-exhausted"
    elif not looks:
        tok = "no-looks-yet"
    else:
        n_max = _int_or_none(header.get("truncation_length"))
        if n_max is None and isinstance(rule, dict):
            n_max = _int_or_none(rule.get("max_looks"))
        tok = "insufficient n=%d/%d" % (len(looks), n_max if n_max is not None else LINEAGE_TRUNCATION_DEFAULT)
    return tok, len(looks), len(voids), cum_usd, cum_wall


def row_lineage_cap(ctx):
    t, root = ctx["text"], ctx["root"]
    stem = ctx["stem"]
    depth, rootstem = root.ancestry(stem)
    _, shas = root.lineage()
    cur = frozen_span_sha(t) or ""
    rsha = shas.get(rootstem) or ""
    ctx["meta"]["lineage_depth"] = depth
    ctx["meta"]["lineage_rule"] = None
    base = "depth=%d root=%s root-span-sha=%s current-span-sha=%s" % (depth, rootstem, rsha[:12], cur[:12])
    if depth < 3:
        return LINEAGE_UNDER_CAP_STATUS, base + " cap-not-reached"
    # the lineage stopping rule first (R0-R4): a lineage under the rule is read from its stream and spend ledger and is
    # never asked for a count; only a lineage with no lineage directory falls through to the legacy decision path
    _, ldir = _lineage_under_rule(root, ctx["id"], rootstem)
    if ldir:
        ok, rule = _frozen_rule_ok(os.path.join(ldir, "frozen", "rule.json"))
        if not ok:
            ctx["meta"]["lineage_rule"] = "tampered"
            return "FAIL", base + " lineage-rule:tampered"
        tok, looks, voids, usd, wall = _lineage_rule_state(ldir, rule)
        ctx["meta"]["lineage_rule"] = tok
        return "PASS", base + " lineage-rule:%s looks=%d voids=%d spend=%s/%s" % (tok, looks, voids, _fmt(usd, 6), _fmt(wall, 1))
    sp = ctx["spans"]["status"]
    block = rr_strip_comments(slice_(t, sp)) if sp else ""
    m = LINEAGE_DECISION_RE.search(block)
    if m:
        dec, res = root.ledger()
        val = m.group(1).strip()
        if DEC_ID_RE.fullmatch(val) and val in dec and val in res:  # shared definition: exact id, joined decision + resolution rows
            return "PASS", base + " lineage-decision:%s" % val
        return "FAIL", base + " lineage-decision:unresolved"
    return "FAIL", base + " lineage-decision:absent"


def _is_void_record(j):
    """Shared definition: a top-level VERDICT.json or grade.json carrying any of the five void shapes."""
    return isinstance(j, dict) and (
        j.get("verdict") == "void"
        or (isinstance(j.get("terminal"), str) and j["terminal"].startswith("void"))
        or j.get("voided") is True
        or j.get("void") is True
        or "void_class" in j)


def row_void_class(ctx):
    t, sp, rd = ctx["text"], ctx["spans"], ctx["rundir"]
    verdict = rr_strip_comments(slice_(t, sp["verdict"]))
    clause = bool(VOID_CLAUSE_RE.search(verdict))
    spec_fail = False
    if clause:
        typed = bool(re.search(r"\bambiguous\b", verdict, re.I)) and bool(re.search(r"\bannulled\b", verdict, re.I))
        spec_fail = not typed
        spec_leg = "spec:%s" % ("void-typed(ambiguous+annulled)" if typed else "void-untyped")
    else:
        spec_leg = "spec:no-void-clause"
    void_record = False
    run_fail = False
    run_leg = "run:no-void-record"
    for name in ("VERDICT.json", "grade.json"):
        j = read_json(os.path.join(rd, name)) if rd else None
        if _is_void_record(j):
            void_record = True
            if j.get("void_class") in VOID_CLASSES:
                run_leg = "run:%s void_class=%s" % (name, j["void_class"])
            else:
                run_fail = True
                run_leg = "run:%s void_class-missing-or-untyped" % name
    if not clause and not void_record:
        return "SKIP", spec_leg + " " + run_leg
    return ("FAIL" if (spec_fail or run_fail) else "PASS"), spec_leg + " " + run_leg


def row_who_cares(ctx):
    t, root = ctx["text"], ctx["root"]
    m = KEEP_LINE_RE.search(t)
    if not m:
        return "FAIL", "keep-line:absent"
    line = m.group(1)
    actors = root.actors()
    named = sorted(a for a in actors if re.search(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])", line))
    mag = MAGNITUDE_RE.search(line)
    parts = ["actor:%s" % (",".join(named) if named else "none"), "magnitude:%s" % ("ok" if mag else "none")]
    return ("PASS" if (named and mag) else "FAIL"), " ".join(parts)


def row_premortem_present(ctx):
    t, sp = ctx["text"], ctx["spans"]
    method = rr_strip_comments(slice_(t, sp["method"]))
    m = PREMORTEM_KEY_RE.search(method)
    if not m:
        return "FAIL", "pre-mortem:absent"
    items = []
    cur = None
    for line in method[m.end():].split("\n"):
        if re.match(r"^\s*(?:[-*]|\d+\.)\s+", line):
            cur = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).strip()
            items.append(cur)
        elif line.strip() == "":
            if items:
                break
        elif items:
            items[-1] = items[-1] + " " + line.strip()
    mapped = [bool(PREMORTEM_MAP_RE.search(i)) for i in items]
    ok = len(items) >= 2 and all(mapped)
    return ("PASS" if ok else "FAIL"), "pre-mortem:items=%d mapped=%d" % (len(items), sum(mapped))


def row_blind_seeds(ctx):
    t, sp, rd = ctx["text"], ctx["spans"], ctx["rundir"]
    method = rr_strip_comments(slice_(t, sp["method"]))
    if not SEED_MENTION_RE.search(method):
        return "SKIP", "no-seeded-mutant-mention"
    fx = os.path.join(rd, "fixture") if rd else None
    if not fx or not os.path.isdir(fx):
        return "SKIP", "no-fixture-directory"
    readme = read_text(os.path.join(fx, "README.md"))
    if readme is None:
        return "FAIL", "fixture-present README.md:absent"
    seed = keyed_value(readme, "seed-author")
    row = keyed_value(readme, "row-author")
    if not seed:
        return "FAIL", "fixture-present seed-author:absent"
    if not row:
        return "FAIL", "fixture-present row-author:absent"
    if seed.split()[0] == row.split()[0]:
        return "FAIL", "fixture-present seed-author==row-author"
    return "PASS", "fixture-present seed-author!=row-author"


def _label(text):
    tt = re.sub(r"[*_`]", "", text).strip()
    idx = tt.find(":")
    if 0 < idx <= 45:
        return tt[:idx].strip().lower().rstrip(".;:")
    return None


def is_run_validity(text):
    lab = _label(text)
    if lab:
        if lab in RUN_VALIDITY_LABELS:
            return True
        parts = [p.strip() for p in re.split(r"\s+and\s+|,|/|\s*\+\s*|\s*&\s*", lab) if p.strip()]
        if parts and all(p in RUN_VALIDITY_LABELS for p in parts):
            return True
    whole = re.sub(r"[*_`]", "", text).strip().lower()
    if len(whole) <= 160 and re.search(r"\b(two[- ]pass(?:es)?|byte[- ]identical|zero writes)\b", whole):
        return True
    return False


def row_substantive_assertions(ctx):
    t, sp = ctx["text"], ctx["spans"]
    items = numbered_items(slice_(t, sp["assertions"]))
    if len(items) < 3:
        return "SKIP", "assertions=%d (MALFORMED belongs to preflight)" % len(items)
    rv = [n for n, x in items if is_run_validity(x)]
    subst = len(items) - len(rv)
    detail = "substantive=%d/%d run-validity=[%s]" % (subst, len(items), ",".join(rv))
    return ("PASS" if subst >= 3 else "FAIL"), detail


RIGOR_PREDICATES = [
    row_discard_banks, row_control_present, row_treatment_delivered, row_frozen_span_sha,
    row_directive_interpretation, row_stage1_reviewed, row_refute_review, row_lineage_cap,
    row_void_class, row_who_cares, row_premortem_present, row_blind_seeds, row_substantive_assertions,
]


# ---------------------------------------------------------------- evaluation and emission
def evaluate_rigor(root, spec_path, text=None):
    """The thirteen rigor rows over one spec -> (rows, meta): rows = [(row, STATUS, detail)] in RIGOR_ROWS order."""
    if text is None:
        text = read_text(spec_path)
        if text is None:
            text = ""
    stem = os.path.splitext(os.path.basename(spec_path))[0]
    sid = spec_id(spec_path)
    ctx = {"text": text, "spans": spans(text), "root": root, "stem": stem, "id": sid,
           "rundir": root.rundir(sid), "meta": {}}
    rows = []
    for name, pred in zip(RIGOR_ROWS, RIGOR_PREDICATES):
        st, dt = pred(ctx)
        rows.append((name, st, dt.replace("\t", " ").replace("\n", " ")))
    meta = dict(ctx["meta"])
    meta["fails"] = [r for r, s, _ in rows if s == "FAIL"]
    meta["skips"] = [r for r, s, _ in rows if s == "SKIP"]
    meta["id"] = sid
    return rows, meta


def evaluate_spec(root, spec_path):
    """All rows over one spec: the six ethics rows, then the thirteen rigor rows; one merged meta."""
    text = read_text(spec_path)
    if text is None:
        text = ""
    erows, emeta = evaluate(text, root.root)
    rrows, rmeta = evaluate_rigor(root, spec_path, text)
    meta = dict(emeta)
    meta["rigor"] = rmeta
    return erows + rrows, meta


def emit_spec(root, spec_path, out):
    rows, meta = evaluate_spec(root, spec_path)
    rel = os.path.relpath(os.path.abspath(spec_path), root.root)
    for ln in report_lines(rel, rows):
        out.write(ln + "\n")
    out.write("META\t%s\t%s\n" % (rel, json.dumps(meta, sort_keys=True)))
    return rows


def census(root, out):
    """Every spec under <hypotheses_dir>, then one CENSUS line per row (the flip cards' fire-rate input)."""
    names = CHECKS + RIGOR_ROWS
    counts = {r: {"PASS": 0, "FAIL": 0, "SKIP": 0} for r in names}
    n = 0
    for p in root.specs():
        rows = emit_spec(root, p, out)
        n += 1
        for rw, st, _ in rows:
            counts[rw][st] += 1
    for r in names:
        c = counts[r]
        out.write("CENSUS\t%s\tFAIL=%d\tPASS=%d\tSKIP=%d\tn=%d\n" % (r, c["FAIL"], c["PASS"], c["SKIP"], n))


# ====================================================================== selftest: the counted fixture's seeds
# The three clean seeds, the lineage ancestors, the ledger, the actor nodes, the raw source and the run artifacts
# are the counted fixture's seed root byte for byte (H-DRAFT-50b0c1da-spec-rigor-rows-v2 fixture/seeds/root/).
# Every mutant below mirrors one class of the frozen blind-mutant manifest (98 submitted, 90 admissible) --
# the ids name the manifest entry whose defect class it reproduces.
_SEED_001 = """# H-SEED-001-normative-complete: a report-only rigor row beside the gate names what a discard rules out before the first counted run

## Status
draft <!-- seed spec for the spec-rigor-rows fixture; fourth spec of the H-SEED-101 -> 102 -> 103 lineage -->
lineage-decision: DEC-901
reviewed-by: DEC-902
Claim type: normative

## In plain terms
- **What we're testing:** whether a single report-only row can tell a spec that names its exclusion from one that does not.
- **What "keep" means:** the row ships report-only in the plugin, so each researcher registering a spec sees 1 extra line per spec (0 today).
- **Terms:** "row" = one PASS/FAIL/SKIP line per check per spec; "counted run" = a run entering the verdict tally.

## Hypothesis
A deterministic row over the verdict-rule span reports FAIL on 100% of seeded no-exclusion mutants and PASS on every clean seed, byte-identical across two passes.

## Motivation
The lab enforces the rigor half of its frameworks and none of the question half; a discard that names nothing banks nothing.

Source: research/raw/2026-09-01-seed-directive.md:7-9

```interpretation-set
source: research/raw/2026-09-01-seed-directive.md:7-9
verbatim: "I'd like the checks to say what they rule out when they fail, not only what they prove when they pass. Keep it small: rows beside the gate, nothing that blocks anyone until we have seen the rate."
readings:
  1. One report-only row beside the gate; nothing blocks; the flip waits on a recorded rate.
  2. A blocking check now, with the rate recorded afterwards.
chosen: 1 — "nothing that blocks anyone until we have seen the rate" forbids reading 2; the narrowest reading is the default.
```

## Variable under test
Exactly one: the row present and run over the spec (ON) versus today's gate alone (OFF). Same seeds, same mutants, same commit.

## Baseline
Today's gate prints no line for the exclusion class; it exits 0 on every seed and every mutant.

## Prior work
- Builds on H-SEED-103-lineage-c (refined, 2026-09-01): the same claim with the sentinel repaired — hypotheses/H-SEED-103-lineage-c.md.

## Method
Claim class: deterministic — every counted arm is a script over committed bytes; zero LLM in counted runs. Frozen at registration: the row contract, the three seeds, the seeded mutant manifest (authored by a blind second session that never sees the row's code), the clean corpus, the output grammar and the grading script. The answer key and the grader live harness-side under `fixture/` and are never shown to arms. Everything runs in an isolated scratch snapshot under `/private/tmp/`.
1. Snapshot: copy the seeds and the clean corpus into the scratch.
2. Mutant pass: apply each seeded mutant onto a fresh copy of its seed; run the row; record the finding.
3. Clean pass: run the row over the clean corpus; record zero findings.
4. Manipulation check: `row.py --selftest` fires the row's FAIL path on an in-script sentinel before any outcome row is graded; `run.json` records `treatment_delivered: true` only when it passes.
5. Two passes of steps 2-4, byte-compared.
Pre-mortem, written before the first counted run:
- the row is satisfied by wording alone -> A1 is decided on blind mutants, not on the author's phrasing -> banks
- a mutant is caught by today's gate already -> A2 fails to hold; the mutant is inadmissible -> void:annulled
- the scratch snapshot fails to build -> nothing measured -> void:ambiguous

- Fixture: `experiments/runs/H-SEED-001-normative-complete/fixture/` (seeds, manifest, `README.md` with `row-author:` and `seed-author:` ids)
- Repetitions per arm: the full mutant, clean and two-pass set per counted run; 2 counted runs
- Budget per run: script-only, 0 child sessions, <= $0.10 compute, <= 10 min wall-clock; halt on breach; budget-exceeded recorded

## Binary assertions
1. Detection: the row reports FAIL on 100% of admissible seeded mutants; both runs.
2. Control (assay sensitivity): OFF differing output lines = 0 for every mutant against its seed, OFF exit code = 0 on all 3 seeds, and ON FAIL rows = 0 on all 3 seeds; both runs.
3. False positives: the row fires on <= 5% of the clean corpus (0 of 20 clean specs); both runs.
4. Live echo: the row re-finds the named historical instance at the snapshot; both runs.
5. Determinism: every pass's second execution is byte-identical to its first; both runs.

## Verdict rule
Keep if 5/5 assertions pass in 2 consecutive counted runs, the second by a cold executor from `fixture/README.md` alone. Refine after 1 failed counted run only when a counterfactual regrade of the same inputs flips the failed assertion (a predicate or grader contract defect); any other failure counts toward discard; discard after 3 failed counted runs. Void classes: ambiguous (reality unclear — a host, disk or snapshot fault before the row runs) is recorded, not counted, and re-run unchanged; annulled (question unclear — a mutant today's gate tells from its seed, or a manipulation-check failure) is recorded, never counted as a pass, and forces a refine of the manifest.
Discard banks: a discard driven by assertion 1 banks "the no-exclusion class is not recoverable from committed bytes by a span-scoped predicate"; a discard driven by assertion 3 banks "the row cannot stay under one fire in twenty on clean specs without a template slot".
Exclusion (what a discard rules out): a discard excludes the claim that the exclusion class can be enforced by an artifact-bound report-only row without a template change, and routes that class to a separate hypothesis.

## On keep
- the row lands report-only in the plugin's preflight-rigor script [closes-when: commit-grep=closes: seed-row-ship]
- its flip to ESCALATE is one decision card carrying the recorded fire rate [closes-when: maintainer-ruling=seed-row-flip]

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""

_SEED_002 = """# H-SEED-002-lint-lane: a deterministic currency lint over direction prose catches seeded stale-reference drift with zero false positives

## Status
draft <!-- seed spec for the spec-rigor-rows fixture: a zero-LLM lint lane -->
Claim type: descriptive

## In plain terms
- **What we're testing:** whether a script can catch stale references in direction prose the way a careful re-read would.
- **What "keep" means:** the lint runs as a harden-check advisory for every main-agent session, so stale references surface within 1 session instead of at the next review (10 x sooner).
- **Terms:** "direction prose" = the North Star paragraph and its status pointers; "mutant" = a copy of the clean corpus with one seeded defect.

## Hypothesis
A deterministic lint over the direction-layer surfaces reports >= 1 correctly-classed finding on 100% of seeded drift mutants and zero findings on the clean corpus, byte-identical across two passes.

## Motivation
Surfacing alone does not move work: stale references are re-presented every session until someone notices. A lint that names the stale line converts a recurring read into a one-line fix.

## Variable under test
Exactly one: the lint present and run over the surfaces (ON) versus no lint (OFF). Same clean corpus, same mutants, same commit.

## Baseline
No lint; drift is found by hand during review cadence, at about 1 instance per 10 sessions.

## Prior work
- none surfaced (sweep run 2026-09-01)

## Method
Frozen at registration: the five drift classes and their detection contracts; the seed manifest (>= 2 mutants per class, >= 10 total, injected fresh by a blind second session); the clean corpus definition; the lint's output grammar (one finding per line); the two-pass determinism check. The seed manifest and the expected-findings key live harness-side and are never shown to arms; the lint reads only corpus files.
1. Fixture build (harness-side) in the lane's `fixture/`: a scratch, isolated copy of the direction-layer surfaces with all known drift corrected, forming the clean corpus; pin with SHA256SUMS.
2. Mutant passes: apply the seed manifest one mutant at a time, each onto a fresh copy of the clean corpus; run the lint per mutant; record findings.
3. Clean pass: run the lint over the untouched clean corpus; record findings.
4. Manipulation check: `lint.py --selftest` fires every class on an in-script sentinel before any mutant is graded; a selftest failure voids the run as an instrument fault.
5. Determinism and grade (script): every pass executes twice and byte-compares; the grading script scores the assertions below.
Pre-mortem:
- a drift class shares tokens with another so a single seed cross-fires -> the cross-fire count is recorded -> A1
- the clean corpus still carries an uncorrected instance -> A2 fails; the corpus is a fixture defect -> void:annulled
- the scratch copy cannot be built -> nothing measured -> void:ambiguous

- Fixture: the lane's `fixture/` (clean corpus + snapshot copy + SHA256SUMS; seed manifest held harness-side)
- Repetitions per arm: full mutant + clean + snapshot passes per counted run; 2 counted runs
- Budget per run: script-only, 0 child sessions, <= $0.10 compute, <= 10 min wall-clock; halt on breach; budget-exceeded recorded

## Binary assertions
1. Detection: the lint reports >= 1 correctly-classed finding for 100% of seeded mutants across all five classes; both runs.
2. Zero false positives: the clean-corpus pass reports zero findings; both runs.
3. Determinism: every pass's second execution is byte-identical to its first; both runs.
4. Live echo: on the as-of-registration snapshot, the lint re-finds every census-documented drift instance; both runs.
5. Containment: the tree diff shows zero writes outside the fixture copies and the lane's run directory; both runs.

## Verdict rule
Keep if 5/5 assertions pass in 2 consecutive runs. Refine after 1 failed run only when a counterfactual regrade of the same inputs flips the failed assertion (a fixture, seed-manifest or output-contract defect); otherwise discard after 3 failed runs. Void classes: ambiguous (reality unclear — a host or disk fault before the lint runs) is recorded, not counted, and re-run unchanged; annulled (question unclear — an uncorrected clean-corpus instance or a selftest failure) is recorded, never counted as a pass, and forces a fixture refine. A discard driven by assertion 1 banks "drift of the evaded classes is not mechanically detectable from prose and stays a review-cadence job"; a discard driven by assertion 2 banks "the clean corpus cannot be defined tightly enough for a zero-finding bar".
Exclusion (what a discard rules out): a discard excludes the claim that direction drift is catchable by a deterministic lint, and leaves currency with the review cadence as a separate hypothesis.

## On keep
- the lint ships as a harden-check advisory [closes-when: commit-grep=closes: seed-lint-ship]

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""

_SEED_003 = """# H-SEED-003-behavioral-ab: an asynchronous Stop hook adds under 100 ms per stop where the synchronous hook adds over 300 ms

## Status
draft <!-- seed spec for the spec-rigor-rows fixture: a behavioral A/B lane -->
Claim type: descriptive

## In plain terms
- **What we're testing:** whether running the Stop hook in the background makes each session stop noticeably faster without losing the dashboard refresh.
- **What "keep" means:** the asynchronous hook ships to every plugin-consumer, cutting the per-stop wait from about 300 ms to under 100 ms (3 x faster).
- **Terms:** "Stop hook" = a command run when the session ends its turn; "p50" = the median over the measured stops.

## Hypothesis
An asynchronous Stop hook keeps the added wall time per stop at or under 100 ms at p50 across 20 stops while the synchronous hook (OFF) stays at or over 300 ms, with the dashboard refreshed after every stop.

## Motivation
The synchronous hook makes every turn boundary wait for a dashboard render that nothing consumes at that moment; moving it off the turn keeps the freshness and drops the wait.

## Variable under test
Exactly one: the hook's `async` flag (ON: asynchronous; OFF: synchronous, today's configuration). Same host, same session script, same window.

## Baseline
The synchronous hook as configured today: the stop waits for the render, measured at about 300 ms p50 on this host.

## Prior work
- none surfaced (sweep run 2026-09-01)

## Method
Frozen at registration: the session script (20 stops per arm), the timing harness, the freshness check and the grading script. The timing log and the grader live harness-side and are never shown to arms; the arms run in an isolated scratch clone with the fixture pinned by SHA256SUMS.
1. Pre-check: confirm that no stdout consumer exists for the hook (otherwise the asynchronous form would drop output); record the result before either arm runs.
2. ON arm: 20 stops with the asynchronous hook; record added wall time per stop and the dashboard mtime after each stop.
3. OFF arm: 20 stops with the synchronous hook; record the same.
4. Grade (script): compute p50 per arm, count refreshes, score the assertions below; two passes byte-compared.
Pre-mortem:
- host load inflates both arms equally and the gap survives but the absolute bar does not -> A2 states the absolute bar; load is recorded as a covariate -> A2
- the asynchronous hook loses its tail at session exit so the dashboard is not refreshed -> A3
- the timing harness itself fails to start -> nothing measured -> void:ambiguous

- Fixture: the lane's `fixture/` (session script, timing harness, pinned hook copies, SHA256SUMS)
- Repetitions per arm: 20 stops per arm per counted run; 2 counted runs
- Budget per run: <= 2 headless sessions, <= $0.50, <= 20 min wall-clock; halt on breach; budget-exceeded recorded

## Binary assertions
1. Consumer-less pre-check passes mechanically (no stdout consumer exists) before either arm runs; both runs.
2. ON arm: Stop-event added wall p50 <= 100 ms across >= 20 stops; OFF arm p50 >= 300 ms; both runs.
3. Freshness preserved: the dashboard mtime advances after 20/20 stops in the ON arm; both runs.
4. Zero new error rows in the hook log during the ON window; both runs.
5. Grading determinism: two passes byte-identical; both runs.

## Verdict rule
Keep if 5/5 assertions pass in 2 consecutive counted runs. Refine after 1 failed counted run only when a counterfactual regrade of the same inputs flips the failed assertion (a harness or grader defect, never a judgment about the hook); otherwise discard after 3 failed counted runs. Void classes: ambiguous (reality unclear — a host fault or session flap before the arms run) is recorded, not counted, and re-run unchanged; annulled (question unclear — the pre-check finds a stdout consumer, so the arms measure a different question) is recorded, never counted as a pass, and forces a refine of the fixture. A discard driven by assertion 2 banks "the asynchronous form does not buy 3 x at p50 on this host"; a discard driven by assertion 3 banks "the asynchronous hook loses its tail and cannot carry the dashboard refresh".
Exclusion (what a discard rules out): a discard excludes the claim that the Stop hook's wait can be removed without losing the refresh, and leaves the synchronous hook as the baseline of a separate hypothesis.

## On keep
- the asynchronous flag ships in the plugin's hook configuration [closes-when: commit-grep=closes: seed-async-ship]

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""

_ANCESTOR = """# H-SEED-%(n)s-lineage-%(l)s: %(ord)s spec of the seed lineage

## Status
refined-into: %(into)s <!-- %(note)s -->
Claim type: normative

## Hypothesis
A %(ord)s attempt at the seed claim.

## Method
Scratch fixture; the key is never shown to arms. Frozen at registration.
- Budget per run: 5 min

## Binary assertions
1. Detection: the row fires on every seeded defect.
2. Clean corpus: zero findings on the clean corpus.
3. Determinism: two passes byte-identical.

## Verdict rule
Keep if 3/3 assertions pass in 2 consecutive runs; refine on an instrument defect; discard after 3 failed runs.

## On keep
- none

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""

_SEED_LEDGER = (
    '{"kind": "decision", "id": "DEC-901", "date": "2026-09-02", "title": "Continue the H-SEED lineage past three refinements", "ask": {"question": "The seed lineage H-SEED-101 -> 102 -> 103 -> 001 reaches depth 3. Continue or retire?", "options": [{"label": "continue", "description": "register H-SEED-001 as the fourth spec of the lineage"}, {"label": "retire", "description": "bank the lineage as an instrument-repair chain"}]}, "default": "continue"}\n'
    '{"kind": "decision-resolution", "id": "DEC-901", "date": "2026-09-02", "disposition": "accepted", "chosen_options": ["continue"], "comment": "the three refinements were instrument repairs; the claim is unchanged (Meehl\'s denominator recorded in the card)"}\n'
    '{"kind": "decision", "id": "DEC-902", "date": "2026-09-02", "title": "Stage-1 read of H-SEED-001-normative-complete", "ask": {"question": "Is this the directive\'s question (clause quoted), are the assertions decidable, what would a discard rule out?", "options": [{"label": "approve", "description": "the three fixed questions answered in the card body"}, {"label": "revise", "description": "send back with the unanswered question named"}]}, "default": "approve", "reviewer_session": "55555555-5555-4555-8555-555555555555"}\n'
    '{"kind": "decision-resolution", "id": "DEC-902", "date": "2026-09-02", "disposition": "accepted", "chosen_options": ["approve"], "comment": "clause quoted (raw :7-9); assertions decidable by script; a discard rules out artifact-bound rows without a template change"}\n'
)

_SEED_RAW = """# 2026-09-01 seed directive (stub raw source, verbatim, write-once)

Captured for the seed root of the spec-rigor-rows fixture. This file stands in for a
raw directive transcript so that a seed spec can cite a line range that exists.

---

I'd like every lint we add to be checked by someone who did not write it, and I'd like the
checks to say what they rule out when they fail, not only what they prove when they pass.
Keep it small: rows beside the gate, nothing that blocks anyone until we have seen the rate.

---

Everything else in the conversation was about scheduling and is not part of the directive.
Lines below this point pad the file so that an out-of-range citation is possible.

pad 1
pad 2
pad 3
pad 4
pad 5
"""

_RD1 = "experiments/runs/H-SEED-001-normative-complete/"
_RD2 = "experiments/runs/H-SEED-002-lint-lane/"
_RD3 = "experiments/runs/H-SEED-003-behavioral-ab/"
_SEED_FILES = {
    "hypotheses/H-SEED-001-normative-complete.md": _SEED_001,
    "hypotheses/H-SEED-002-lint-lane.md": _SEED_002,
    "hypotheses/H-SEED-003-behavioral-ab.md": _SEED_003,
    "hypotheses/H-SEED-101-lineage-a.md": _ANCESTOR % {"n": "101", "l": "a", "ord": "first", "into": "H-SEED-102-lineage-b",
                                                        "note": "2026-08-30: run 1 scored 3/5; the grader's span parser mis-read wrapped lines (instrument repair)"},
    "hypotheses/H-SEED-102-lineage-b.md": _ANCESTOR % {"n": "102", "l": "b", "ord": "second", "into": "H-SEED-103-lineage-c",
                                                        "note": "2026-08-31: run 1 scored 4/5; the clean corpus carried one uncorrected instance (instrument repair)"},
    "hypotheses/H-SEED-103-lineage-c.md": _ANCESTOR % {"n": "103", "l": "c", "ord": "third", "into": "H-SEED-001-normative-complete",
                                                        "note": "2026-09-01: run 1 scored 4/5; the sentinel omitted one class (instrument repair)"},
    "ledger/work-ledger.jsonl": _SEED_LEDGER,
    "research/raw/2026-09-01-seed-directive.md": _SEED_RAW,
    "operating-model/seed-lab/actors/main-agent.md": "# main-agent\n\nStub actor node (seed root; filename is the actor id).\n",
    "operating-model/seed-lab/actors/plugin-consumer.md": "# plugin-consumer\n\nStub actor node (seed root; filename is the actor id).\n",
    "operating-model/seed-lab/actors/researcher.md": "# researcher\n\nStub actor node (seed root; filename is the actor id).\n",
    "operating-model/seed-lab/actors/subagent.md": "# subagent\n\nStub actor node (seed root; filename is the actor id).\n",
    "operating-model/seed-lab/actors/workflow-orchestrator.md": "# workflow-orchestrator\n\nStub actor node (seed root; filename is the actor id).\n",
    _RD1 + "fixture/README.md": "# H-SEED-001 fixture (stub)\n\nrow-author: 11111111-1111-4111-8111-111111111111\nseed-author: 22222222-2222-4222-8222-222222222222\n\nThe seeded mutant manifest was authored by the seed-author session, which never saw the row's code.\n",
    _RD1 + "frozen/span.sha": "cb72d8f7b6275d7768c04428bd78b6eb360aa6d0c6a93016c3a514fd3f72846f\n",
    _RD1 + "grade.json": '{\n "run": 2,\n "passed": 5,\n "failed": 0,\n "assertions": {"A1": "PASS", "A2": "PASS", "A3": "PASS", "A4": "PASS", "A5": "PASS"}\n}\n',
    _RD1 + "REFUTE.md": "# REFUTE record: H-SEED-001-normative-complete\n\nrefuter-session: 33333333-3333-4333-8333-333333333333\nblocking-findings: 0\n- finding 1 (non-blocking): the row's detail line truncates the recorded sha to 12 hex characters; the full sha is in META\n- finding 2 (non-blocking): the selftest sentinel does not cover a wrapped bank sentence; covered by mutant m-07\n",
    _RD1 + "run.json": '{\n "run": 2,\n "treatment_delivered": true,\n "selftest": "ok",\n "executor_session": "44444444-4444-4444-8444-444444444444",\n "wall_s": 41.2\n}\n',
    _RD1 + "SHIP.md": "# SHIP record: H-SEED-001-normative-complete\n\n- plugin changeset PR opened from the kept ON patch; changeset-check PASS\n- evidence: VERDICT.json here (kept 5/5 x2), REFUTE.md beside it\nbuilder-session: 11111111-1111-4111-8111-111111111111\nexecutor-session: 44444444-4444-4444-8444-444444444444\npr: 7\n",
    _RD1 + "VERDICT.json": '{\n "lane": "H-SEED-001-normative-complete",\n "verdict": "keep",\n "date": "2026-09-02",\n "tally": "5/5, 5/5 (run-2 by a cold executor from fixture/README.md alone)",\n "pointer": "hypotheses/H-SEED-001-normative-complete.md"\n}\n',
    _RD2 + "fixture/README.md": "# H-SEED-002 fixture (stub)\n\nrow-author: 11111111-1111-4111-8111-111111111111\nseed-author: 22222222-2222-4222-8222-222222222222\n\nClean corpus + seed manifest (harness-side); the manifest was authored by the seed-author session.\n",
}
_SEED_SPECS = ("hypotheses/H-SEED-001-normative-complete.md", "hypotheses/H-SEED-002-lint-lane.md", "hypotheses/H-SEED-003-behavioral-ab.md")

# The lane's recorded ON output on the three clean seeds (run-2 pass-1 seeds-on.tsv): (status, detail) per row.
_LANE_SEED_ROWS = {
    _SEED_SPECS[0]: [
        ("PASS", "spec:bank-sentence run:skip(verdict-not-discard)"),
        ("PASS", "off-fails:binds control-present:pos+neg method-mentions:pos=1,neg=1"),
        ("PASS", "spec:manipulation-check-line run:treatment_delivered=true"),
        ("PASS", "span-sha-matches cb72d8f7b627"),
        ("PASS", "interpretation-set:ok source-line:ok"),
        ("PASS", "trigger:normative reviewed-by:DEC-902(decision+resolution)"),
        ("PASS", "SHIP.md REFUTE.md:blocking-findings=0 refuter-distinct"),
        ("PASS", "depth=3 root=H-SEED-101-lineage-a root-span-sha=937859d8566d current-span-sha=cb72d8f7b627 lineage-decision:DEC-901"),
        ("PASS", "spec:void-typed(ambiguous+annulled) run:no-void-record"),
        ("PASS", "actor:researcher magnitude:ok"),
        ("PASS", "pre-mortem:items=3 mapped=3"),
        ("PASS", "fixture-present seed-author!=row-author"),
        ("PASS", "substantive=4/5 run-validity=[5]"),
    ],
    _SEED_SPECS[1]: [
        ("PASS", "spec:bank-sentence run:skip(no-VERDICT.json)"),
        ("PASS", "off-fails:none control-present:pos+neg method-mentions:pos=1,neg=1"),
        ("PASS", "spec:manipulation-check-line run:not-graded"),
        ("SKIP", "no-frozen-span"),
        ("SKIP", "no-directive-trigger"),
        ("SKIP", "no-trigger"),
        ("SKIP", "no-SHIP.md no-plugin-shipped-change"),
        ("SKIP", "depth=0 root=H-SEED-002-lint-lane root-span-sha=7d5634e0895c current-span-sha=7d5634e0895c cap-not-reached"),
        ("PASS", "spec:void-typed(ambiguous+annulled) run:no-void-record"),
        ("PASS", "actor:main-agent magnitude:ok"),
        ("PASS", "pre-mortem:items=3 mapped=3"),
        ("PASS", "fixture-present seed-author!=row-author"),
        ("PASS", "substantive=3/5 run-validity=[3,5]"),
    ],
    _SEED_SPECS[2]: [
        ("PASS", "spec:bank-sentence run:skip(no-VERDICT.json)"),
        ("PASS", "off-fails:binds control-present:none method-mentions:pos=0,neg=0"),
        ("PASS", "spec:assertion-token run:skip(no-run-directory)"),
        ("SKIP", "no-frozen-span"),
        ("SKIP", "no-directive-trigger"),
        ("SKIP", "no-trigger"),
        ("SKIP", "no-SHIP.md no-plugin-shipped-change"),
        ("SKIP", "depth=0 root=H-SEED-003-behavioral-ab root-span-sha=4ce0e2a1f987 current-span-sha=4ce0e2a1f987 cap-not-reached"),
        ("PASS", "spec:void-typed(ambiguous+annulled) run:no-void-record"),
        ("PASS", "actor:plugin-consumer magnitude:ok"),
        ("PASS", "pre-mortem:items=3 mapped=3"),
        ("SKIP", "no-seeded-mutant-mention"),
        ("PASS", "substantive=4/5 run-validity=[5]"),
    ],
}

# Seeded positives: (mutant id, row, spec rel, path rel, op) -- op is {"edits": [(old, new), ...]} | {"create": text} |
# {"delete": True}. Each mirrors the named class of the frozen blind manifest; `repaired` marks the four detector
# repairs (same-line keyed value, exact-id decision resolution, five-shape void record, assertion-bound control scan).
_S1, _S2, _S3 = _SEED_SPECS
_MUTANTS = [
    # 1 DISCARD-BANKS/NULL-CLASS
    ("DISCARD-BANKS_NULL-CLASS-s2-01 (spec leg: no sentence banks)", "DISCARD-BANKS/NULL-CLASS", _S2, _S2, {"edits": [
        ('driven by assertion 1 banks "drift', 'driven by assertion 1 rules out "drift'),
        ('a discard driven by assertion 2 banks "the clean corpus', 'a discard driven by assertion 2 rules out "the clean corpus')]}),
    ("DISCARD-BANKS_NULL-CLASS-s2-04 (run leg: discard without null_class)", "DISCARD-BANKS/NULL-CLASS", _S1, _RD1 + "VERDICT.json",
     {"edits": [('"verdict": "keep"', '"verdict": "discard"')]}),
    ("DISCARD-BANKS_NULL-CLASS-s2-05 (run leg: null_class outside the enum)", "DISCARD-BANKS/NULL-CLASS", _S2, _RD2 + "VERDICT.json",
     {"create": '{\n "lane": "H-SEED-002-lint-lane",\n "verdict": "discard",\n "null_class": "no-signal"\n}\n'}),
    ("DISCARD-BANKS_NULL-CLASS-s2-06 (run leg: threshold-miss without bar)", "DISCARD-BANKS/NULL-CLASS", _S3, _RD3 + "VERDICT.json",
     {"create": '{\n "lane": "H-SEED-003-behavioral-ab",\n "verdict": "discard",\n "null_class": "threshold-miss",\n "observed": "142ms"\n}\n'}),
    # 2 CONTROL-PRESENT/OFF-FAILS
    ("CONTROL-PRESENT_OFF-FAILS-01 (OFF-FAILS leg: binding removed)", "CONTROL-PRESENT/OFF-FAILS", _S3, _S3, {"edits": [
        ("2. ON arm: Stop-event added wall p50 <= 100 ms across >= 20 stops; OFF arm p50 >= 300 ms; both runs.",
         "2. ON arm: Stop-event added wall p50 <= 100 ms across >= 20 stops while the synchronous hook stays slower; both runs.")]}),
    ("CONTROL-PRESENT_OFF-FAILS-02 (OFF-FAILS leg: token without comparator+number)", "CONTROL-PRESENT/OFF-FAILS", _S3, _S3, {"edits": [
        ("OFF arm p50 >= 300 ms; both runs.", "the OFF arm is slower; both runs.")]}),
    ("CONTROL-PRESENT_OFF-FAILS-04 [repaired] (positive tokens gone from the assertions, kept in Method)", "CONTROL-PRESENT/OFF-FAILS", _S2, _S2, {"edits": [
        ("for 100% of seeded mutants across all five classes", "for 100% of the injected drift cases across all five classes")]}),
    ("CONTROL-PRESENT_OFF-FAILS-05 [repaired] (negative tokens gone from the assertions, kept in Method)", "CONTROL-PRESENT/OFF-FAILS", _S2, _S2, {"edits": [
        ("2. Zero false positives: the clean-corpus pass reports zero findings; both runs.",
         "2. Reference pass: the reference-copy pass reports a flagged instance; both runs.")]}),
    # 3 TREATMENT-DELIVERED
    ("TREATMENT-DELIVERED-01 (spec leg: pre-check token removed)", "TREATMENT-DELIVERED", _S3, _S3, {"edits": [
        ("1. Consumer-less pre-check passes mechanically", "1. Consumer-less confirmation passes mechanically")]}),
    ("TREATMENT-DELIVERED-02 (spec leg: Manipulation check line renamed)", "TREATMENT-DELIVERED", _S1, _S1, {"edits": [
        ("4. Manipulation check: `row.py --selftest`", "4. Instrument check: `row.py --selftest`")]}),
    ("TREATMENT-DELIVERED-04 (run leg: treatment_delivered false)", "TREATMENT-DELIVERED", _S1, _RD1 + "run.json", {"edits": [
        ('"treatment_delivered": true,', '"treatment_delivered": false,')]}),
    ("TREATMENT-DELIVERED-05 (run leg: treatment_delivered key absent)", "TREATMENT-DELIVERED", _S1, _RD1 + "run.json", {"edits": [
        (' "treatment_delivered": true,\n', '')]}),
    # 4 FROZEN-SPAN-SHA
    ("FROZEN-SPAN-SHA-01 (assertion span edited, span.sha untouched)", "FROZEN-SPAN-SHA", _S1, _S1, {"edits": [
        ("1. Detection: the row reports FAIL on 100% of admissible seeded mutants; both runs.",
         "1. Detection: the row reports FAIL on 100% of admissible seeded mutants; both counted runs.")]}),
    ("FROZEN-SPAN-SHA-05 (one added space inside the span)", "FROZEN-SPAN-SHA", _S1, _S1, {"edits": [
        ("4. Live echo: the row re-finds", "4. Live echo:  the row re-finds")]}),
    ("FROZEN-SPAN-SHA-06 (one hex character of span.sha flipped)", "FROZEN-SPAN-SHA", _S1, _RD1 + "frozen/span.sha", {"edits": [
        ("cb72d8f7b6275d7768c04428bd78b6eb360aa6d0c6a93016c3a514fd3f72846f", "ab72d8f7b6275d7768c04428bd78b6eb360aa6d0c6a93016c3a514fd3f72846f")]}),
    ("FROZEN-SPAN-SHA-08 (span.sha created where none existed)", "FROZEN-SPAN-SHA", _S2, _RD2 + "frozen/span.sha",
     {"create": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"}),
    # 5 DIRECTIVE-INTERPRETATION/SOURCE-LINE
    ("DIRECTIVE-INTERPRETATION_SOURCE-LINE-01 (chosen: key renamed)", "DIRECTIVE-INTERPRETATION/SOURCE-LINE", _S1, _S1, {"edits": [
        ("chosen: 1 —", "picked: 1 —")]}),
    ("DIRECTIVE-INTERPRETATION_SOURCE-LINE-02 (one reading only)", "DIRECTIVE-INTERPRETATION/SOURCE-LINE", _S1, _S1, {"edits": [
        ("  2. A blocking check now, with the rate recorded afterwards.\n", "")]}),
    ("DIRECTIVE-INTERPRETATION_SOURCE-LINE-03 (directive trigger with no block)", "DIRECTIVE-INTERPRETATION/SOURCE-LINE", _S2, _S2, {"edits": [
        ("converts a recurring read into a one-line fix.",
         "converts a recurring read into a one-line fix. Source: the 2026-09-01 directive, research/raw/2026-09-01-seed-directive.md:7-9.")]}),
    ("DIRECTIVE-INTERPRETATION_SOURCE-LINE-05 (line range beyond the raw file)", "DIRECTIVE-INTERPRETATION/SOURCE-LINE", _S1, _S1, {"edits": [
        ("Source: research/raw/2026-09-01-seed-directive.md:7-9", "Source: research/raw/2026-09-01-seed-directive.md:25-27"),
        ("source: research/raw/2026-09-01-seed-directive.md:7-9", "source: research/raw/2026-09-01-seed-directive.md:25-27")]}),
    ("DIRECTIVE-INTERPRETATION_SOURCE-LINE-06 (raw file does not exist)", "DIRECTIVE-INTERPRETATION/SOURCE-LINE", _S1, _S1, {"edits": [
        ("Source: research/raw/2026-09-01-seed-directive.md:7-9", "Source: research/raw/2026-09-02-seed-directive.md:7-9"),
        ("source: research/raw/2026-09-01-seed-directive.md:7-9", "source: research/raw/2026-09-02-seed-directive.md:7-9")]}),
    # 6 STAGE1-REVIEWED
    ("STAGE1-REVIEWED-01 (reviewed-by names an id absent from the ledger)", "STAGE1-REVIEWED", _S1, _S1, {"edits": [
        ("reviewed-by: DEC-902", "reviewed-by: DEC-999")]}),
    ("STAGE1-REVIEWED-02 (reviewed-by line removed)", "STAGE1-REVIEWED", _S1, _S1, {"edits": [
        ("reviewed-by: DEC-902\n", "")]}),
    ("STAGE1-REVIEWED-04 (the DEC-902 resolution row no longer joins)", "STAGE1-REVIEWED", _S1, "ledger/work-ledger.jsonl", {"edits": [
        ('{"kind": "decision-resolution", "id": "DEC-902",', '{"kind": "decision-resolution", "id": "DEC-902X",')]}),
    ("STAGE1-REVIEWED [repaired] (reviewed-by value is prose around the id)", "STAGE1-REVIEWED", _S1, _S1, {"edits": [
        ("reviewed-by: DEC-902", "reviewed-by: see DEC-902")]}),
    ("STAGE1-REVIEWED [repaired] (reviewed-by value empty; the next line is not the value)", "STAGE1-REVIEWED", _S1, _S1, {"edits": [
        ("reviewed-by: DEC-902", "reviewed-by:")]}),
    ("STAGE1-REVIEWED-s2-03 (directive trigger on a seed with no reviewed-by)", "STAGE1-REVIEWED", _S2, _S2, {"edits": [
        ("converts a recurring read into a one-line fix.", "converts a recurring read into a one-line fix (the 2026-09-01 directive asked for this).")]}),
    # 7 REFUTE-REVIEW
    ("REFUTE-REVIEW-01 (REFUTE.md deleted beside SHIP.md)", "REFUTE-REVIEW", _S1, _RD1 + "REFUTE.md", {"delete": True}),
    ("REFUTE-REVIEW-02 (blocking-findings nonzero)", "REFUTE-REVIEW", _S1, _RD1 + "REFUTE.md", {"edits": [
        ("blocking-findings: 0", "blocking-findings: 2")]}),
    ("REFUTE-REVIEW-03 (blocking-findings line absent)", "REFUTE-REVIEW", _S1, _RD1 + "REFUTE.md", {"edits": [
        ("blocking-findings: 0\n", "")]}),
    ("REFUTE-REVIEW-04 (refuter equals the builder)", "REFUTE-REVIEW", _S1, _RD1 + "REFUTE.md", {"edits": [
        ("refuter-session: 33333333-3333-4333-8333-333333333333", "refuter-session: 11111111-1111-4111-8111-111111111111")]}),
    ("REFUTE-REVIEW-05 (refuter equals the executor)", "REFUTE-REVIEW", _S1, _RD1 + "REFUTE.md", {"edits": [
        ("refuter-session: 33333333-3333-4333-8333-333333333333", "refuter-session: 44444444-4444-4444-8444-444444444444")]}),
    # 8 LINEAGE-CAP
    ("LINEAGE-CAP-01 (lineage-decision line removed at depth 3)", "LINEAGE-CAP", _S1, _S1, {"edits": [
        ("lineage-decision: DEC-901\n", "")]}),
    ("LINEAGE-CAP-02 (lineage-decision names an id with no ledger row)", "LINEAGE-CAP", _S1, _S1, {"edits": [
        ("lineage-decision: DEC-901", "lineage-decision: DEC-777")]}),
    ("LINEAGE-CAP-05 [repaired] (lineage-decision value empty)", "LINEAGE-CAP", _S1, _S1, {"edits": [
        ("lineage-decision: DEC-901", "lineage-decision:")]}),
    ("LINEAGE-CAP-07 [repaired] (free text around the real id)", "LINEAGE-CAP", _S1, _S1, {"edits": [
        ("lineage-decision: DEC-901", "lineage-decision: see DEC-901")]}),
    ("LINEAGE-CAP-08 [repaired] (the real id suffixed)", "LINEAGE-CAP", _S1, _S1, {"edits": [
        ("lineage-decision: DEC-901", "lineage-decision: DEC-901-draft")]}),
    # 9 VOID-CLASS
    ("VOID-CLASS-01 (void clause loses 'ambiguous')", "VOID-CLASS", _S1, _S1, {"edits": [
        ("Void classes: ambiguous (reality unclear", "Void classes: unclear-reality (reality unclear")]}),
    ("VOID-CLASS-02 (void clause loses 'annulled')", "VOID-CLASS", _S1, _S1, {"edits": [
        ("re-run unchanged; annulled (question unclear", "re-run unchanged; withdrawn (question unclear")]}),
    ("VOID-CLASS-05 [repaired] (grade.json void record with void_class outside the enum)", "VOID-CLASS", _S1, _RD1 + "grade.json", {"edits": [
        ('"passed": 5,\n "failed": 0,\n "assertions":', '"passed": 5,\n "failed": 0,\n "voided": true,\n "void_class": "unclear",\n "assertions":')]}),
    ("VOID-CLASS-06 [repaired] (VERDICT.json voided:true without void_class)", "VOID-CLASS", _S1, _RD1 + "VERDICT.json", {"edits": [
        ('"tally": "5/5, 5/5 (run-2 by a cold executor from fixture/README.md alone)",',
         '"tally": "5/5, 5/5 (run-2 by a cold executor from fixture/README.md alone)",\n "voided": true,')]}),
    ("VOID-CLASS [shared definition] (a void_class key with a null value is an untyped void record)", "VOID-CLASS", _S1, _RD1 + "VERDICT.json", {"edits": [
        ('"verdict": "keep",', '"verdict": "keep",\n "void_class": null,')]}),
    # 10 WHO-CARES
    ("WHO-CARES-01 (keep line absent)", "WHO-CARES", _S1, _S1, {"edits": [
        ('- **What "keep" means:** the row ships report-only in the plugin, so each researcher registering a spec sees 1 extra line per spec (0 today).\n', "")]}),
    ("WHO-CARES-02 (no actor id on the keep line)", "WHO-CARES", _S1, _S1, {"edits": [
        ("so each researcher registering a spec sees", "so each person registering a spec sees")]}),
    ("WHO-CARES-03 (no magnitude token on the keep line)", "WHO-CARES", _S1, _S1, {"edits": [
        ("sees 1 extra line per spec (0 today).", "sees an extra line per spec (rarely).")]}),
    # 11 PREMORTEM-PRESENT
    ("PREMORTEM-PRESENT-01 (Pre-mortem keyword removed)", "PREMORTEM-PRESENT", _S1, _S1, {"edits": [
        ("Pre-mortem, written before the first counted run:", "Risks, written before the first counted run:")]}),
    ("PREMORTEM-PRESENT-02 (one mapped item left)", "PREMORTEM-PRESENT", _S1, _S1, {"edits": [
        ("- a mutant is caught by today's gate already -> A2 fails to hold; the mutant is inadmissible -> void:annulled\n"
         "- the scratch snapshot fails to build -> nothing measured -> void:ambiguous\n", "")]}),
    ("PREMORTEM-PRESENT-03 (mappings stripped from two items)", "PREMORTEM-PRESENT", _S3, _S3, {"edits": [
        ("- host load inflates both arms equally and the gap survives but the absolute bar does not -> A2 states the absolute bar; load is recorded as a covariate -> A2",
         "- host load inflates both arms equally and the gap survives but the absolute bar does not; load is recorded as a covariate"),
        ("- the asynchronous hook loses its tail at session exit so the dashboard is not refreshed -> A3",
         "- the asynchronous hook loses its tail at session exit so the dashboard is not refreshed")]}),
    # 12 BLIND-SEEDS
    ("BLIND-SEEDS-01 (seed-author line removed)", "BLIND-SEEDS", _S1, _RD1 + "fixture/README.md", {"edits": [
        ("seed-author: 22222222-2222-4222-8222-222222222222\n", "")]}),
    ("BLIND-SEEDS-02 (seed-author equals row-author)", "BLIND-SEEDS", _S1, _RD1 + "fixture/README.md", {"edits": [
        ("seed-author: 22222222-2222-4222-8222-222222222222", "seed-author: 11111111-1111-4111-8111-111111111111")]}),
    ("BLIND-SEEDS-05 [repaired] (seed-author value empty; the next line is not the value)", "BLIND-SEEDS", _S2, _RD2 + "fixture/README.md", {"edits": [
        ("seed-author: 22222222-2222-4222-8222-222222222222", "seed-author:")]}),
    # 13 SUBSTANTIVE-ASSERTIONS
    ("SUBSTANTIVE-ASSERTIONS-04 (two substantive assertions relabelled run-validity)", "SUBSTANTIVE-ASSERTIONS", _S1, _S1, {"edits": [
        ("1. Detection: the row reports FAIL", "1. Byte-identical: the row reports FAIL"),
        ("2. Control (assay sensitivity): OFF differing", "2. Isolation: OFF differing")]}),
    ("SUBSTANTIVE-ASSERTIONS-s2-01 (Live echo relabelled Reproducibility)", "SUBSTANTIVE-ASSERTIONS", _S2, _S2, {"edits": [
        ("4. Live echo: on the as-of-registration snapshot", "4. Reproducibility: on the as-of-registration snapshot")]}),
]

# Clean negatives beyond the seeds: alternative PASS paths a mutant class must not confuse with a defect.
_NEGATIVES = [
    ("FROZEN-SPAN-SHA amended (span changed; AMENDMENTS.md carries motivation, results-influence, prior-runs)", "FROZEN-SPAN-SHA", _S1, "PASS", [
        (_S1, {"edits": [("both runs.\n2. Control", "both counted runs.\n2. Control")]}),
        (_RD1 + "AMENDMENTS.md", {"create": "# Amendments\n\n## Amendment 1 (2026-09-03)\n\nmotivation: the wording 'both runs' was ambiguous between counted and dry runs\nresults-influence: none; no counted run had graded assertion 1\nprior-runs: unchanged\n"})]),
    ("STAGE1-REVIEWED via a committed stage1/ file", "STAGE1-REVIEWED", _S1, "PASS", [
        (_S1, {"edits": [("reviewed-by: DEC-902", "reviewed-by: stage1/card.md")]}),
        (_RD1 + "stage1/card.md", {"create": "# Stage-1 card\n\nclause quoted; assertions decidable; a discard rules out artifact-bound rows.\n"})]),
    ("VOID-CLASS typed void record (VERDICT.json verdict void, void_class annulled)", "VOID-CLASS", _S1, "PASS", [
        (_RD1 + "VERDICT.json", {"edits": [('"verdict": "keep",', '"verdict": "void",\n "void_class": "annulled",')]})]),
    ("DISCARD-BANKS/NULL-CLASS typed discard (null_class threshold-miss with observed and bar)", "DISCARD-BANKS/NULL-CLASS", _S1, "PASS", [
        (_RD1 + "VERDICT.json", {"edits": [('"verdict": "keep",', '"verdict": "discard",\n "null_class": "threshold-miss",\n "observed": 3,\n "bar": 5,')]})]),
    ("SUBSTANTIVE-ASSERTIONS with fewer than 3 assertions is SKIP (MALFORMED belongs to preflight)", "SUBSTANTIVE-ASSERTIONS", _S1, "SKIP", [
        (_S1, {"edits": [("3. False positives: the row fires on <= 5% of the clean corpus (0 of 20 clean specs); both runs.\n"
                          "4. Live echo: the row re-finds the named historical instance at the snapshot; both runs.\n"
                          "5. Determinism: every pass's second execution is byte-identical to its first; both runs.\n", "")]})]),
]

# Row 8 under the lineage stopping rule: the seed lineage's root (H-SEED-101-lineage-a) carrying the lineage files in
# lineage-stopping.py's layout. The frozen copy is byte-identical to the plugin's rules/frozen/lineage-sprt.json
# (sha256 ce73163a...; inner rule_text 8052bda9...); the llr trajectories are the policy's increments (docs/lineage-stopping.md).
_LINEAGE_POLICY_TEXT = '{"kind": "sprt", "alpha": 0.05, "beta": 0.1, "p0": 0.5, "p1": 0.1, "max_looks": 13}\n'
_LINEAGE_FROZEN = json.dumps({"frozen": "stopping-rule", "rule_text": _LINEAGE_POLICY_TEXT, "sha256": LINEAGE_POLICY_SHA256,
                              "source": "rules/lineage-sprt.json"}, indent=1, sort_keys=True) + "\n"
_LINEAGE_FROZEN_TAMPERED_RULE = _LINEAGE_FROZEN.replace('\\"p1\\": 0.1', '\\"p1\\": 0.2')      # rule_text no longer hashes to the policy
_LINEAGE_FROZEN_TAMPERED_SHA = _LINEAGE_FROZEN.replace('"sha256": "8052bda9', '"sha256": "0052bda9')  # the field disagrees
_LAB_LLR = [0.5877866649021191, 1.1755733298042381, 1.7633599947063572, 2.3511466596084762, 2.9389333245105953]  # five refusal-0 looks
_HOLD_LLR = [-1.6094379124341003, -3.2188758248682006]  # two refusal-1 looks
_ROOT_LANE = "H-SEED-101-lineage-a"
_BASE_S1 = "depth=3 root=H-SEED-101-lineage-a root-span-sha=937859d8566d current-span-sha=cb72d8f7b627"
_INSUFF_3 = _BASE_S1 + " lineage-rule:insufficient n=3/13 looks=3 voids=0 spend=0.0/300.0"


def _canon_row(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"


def _lineage_ops(lane, looks=3, terminal=None, voids=0, frozen=None, budget_wall=23400.0, cap_wall=1800.0, wall_s=100.0):
    """Ops creating <runs_dir>/<lane>/lineage/: the frozen copy, spend.jsonl (header + one row per launch), stream/looks/
    state.jsonl for `looks` counted looks (refusal 0; refusal 1 when terminal == 'hold'), voids.jsonl for `voids` ambiguous
    voids (charged half a run, no look). terminal in {None, 'promote', 'hold'} names the last state line."""
    rd = "experiments/runs/%s/lineage/" % lane
    header = {"budget": {"usd": 1.3, "wall_s": budget_wall}, "derivation": "R0: the spec's Budget-per-run caps x the frozen rule's truncation length (seed fixture)",
              "frozen_copy": "lineage/frozen/rule.json", "frozen_rule_sha256": LINEAGE_POLICY_SHA256, "header": True, "lineage": lane,
              "opened": "2026-09-02T00:00:00Z", "per_run_cap": {"usd": 0.1, "wall_s": cap_wall}, "truncation_length": 13}
    spend, stream, lk, st, vd = [header], [], [], [], []
    cum, run = 0.0, 0
    for _ in range(voids):
        run += 1
        cum += wall_s / 2
        spend.append({"charged": "2026-09-02T00:00:00Z", "class": "void:ambiguous", "cost_usd": 0.0, "cum_usd": 0.0, "cum_wall_s": cum,
                      "run": run, "run_record": "run-%d/RUN-RECORD.json" % run, "spec": lane, "wall_s": wall_s / 2})
        vd.append({"class": "ambiguous", "clause": "i", "re_take": "the next launch re-takes this look while the budget allows (R2)",
                   "recorded": "2026-09-02T00:00:00Z", "run": run, "run_record": "run-%d/RUN-RECORD.json" % run, "spec": lane})
    refusal = 1 if terminal == "hold" else 0
    llrs = _HOLD_LLR if terminal == "hold" else _LAB_LLR
    for k in range(1, looks + 1):
        run += 1
        cum += wall_s
        spend.append({"charged": "2026-09-02T00:00:00Z", "class": "counted", "cost_usd": 0.0, "cum_usd": 0.0, "cum_wall_s": cum,
                      "run": run, "run_record": "run-%d/RUN-RECORD.json" % run, "spec": lane, "wall_s": wall_s})
        stream.append({"class": "lineage", "look": k, "refusal": refusal})
        lk.append({"appended": "2026-09-02T00:00:00Z", "clause": "iii" if refusal else "v", "look": k, "refusal": refusal, "run": run,
                   "run_record": "run-%d/RUN-RECORD.json" % run, "spec": lane})
        state = ("evidence-sufficient %s" % terminal) if (k == looks and terminal) else "evidence-insufficient n=%d/13" % k
        st.append({"llr": llrs[k - 1], "look": k, "n_min": 13, "rule": "sprt", "rule_sha": LINEAGE_POLICY_SHA256, "state": state, "stream": "stream"})
    return [(rd + "frozen/rule.json", {"create": _LINEAGE_FROZEN if frozen is None else frozen}),
            (rd + "spend.jsonl", {"create": "".join(_canon_row(r) for r in spend)}),
            (rd + "stream.jsonl", {"create": "".join(_canon_row(r) for r in stream)}),
            (rd + "looks.jsonl", {"create": "".join(_canon_row(r) for r in lk)}),
            (rd + "state.jsonl", {"create": "".join(_canon_row(r) for r in st)}),
            (rd + "voids.jsonl", {"create": "".join(_canon_row(r) for r in vd)})]


def _inherits_op(lane, root_lane):
    """R4 pointer on a successor: <runs_dir>/<lane>/lineage/inherits.json -> root_lane."""
    return ("experiments/runs/%s/lineage/inherits.json" % lane,
            {"create": json.dumps({"frozen_rule_sha256": LINEAGE_POLICY_SHA256, "lineage_root": root_lane,
                                   "r4": "a refine successor inherits R0's artifacts unchanged -- the frozen copy, the stream, the looks, the ledger, the spend budget; nothing resets, no count exists to reset",
                                   "recorded": "2026-09-02T00:00:00Z", "via": "init --inherit"}, indent=1, sort_keys=True) + "\n"})


_S1_SUCCESSOR = "H-SEED-001-normative-complete"
_NO_DECISION_LINE = (_S1, {"edits": [("lineage-decision: DEC-901\n", "")]})
# (name, spec rel, expected status, expected detail, expected META lineage_rule, ops)
_LINEAGE_RULE_CASES = [
    ("root lineage dir: clean frozen copy + a 3-look stream", _S1, "PASS", _INSUFF_3, "insufficient n=3/13", _lineage_ops(_ROOT_LANE, looks=3)),
    ("root lineage dir: the lineage-decision line removed -- the rule is read, no count is requested", _S1, "PASS", _INSUFF_3, "insufficient n=3/13",
     [_NO_DECISION_LINE] + _lineage_ops(_ROOT_LANE, looks=3)),
    ("root lineage dir: 3 looks + 1 ambiguous void (charged, no look)", _S1, "PASS",
     _BASE_S1 + " lineage-rule:insufficient n=3/13 looks=3 voids=1 spend=0.0/350.0", "insufficient n=3/13", _lineage_ops(_ROOT_LANE, looks=3, voids=1)),
    ("root lineage dir: promote terminal at look 5", _S1, "PASS", _BASE_S1 + " lineage-rule:promote looks=5 voids=0 spend=0.0/500.0", "promote",
     _lineage_ops(_ROOT_LANE, looks=5, terminal="promote")),
    ("root lineage dir: hold terminal at look 2", _S1, "PASS", _BASE_S1 + " lineage-rule:hold looks=2 voids=0 spend=0.0/200.0", "hold",
     _lineage_ops(_ROOT_LANE, looks=2, terminal="hold")),
    ("root lineage dir: spend exhausted (3 x 100 s charged against a 300 s budget with a 100 s cap; R1 refuses the 4th)", _S1, "PASS",
     _BASE_S1 + " lineage-rule:spend-exhausted looks=3 voids=0 spend=0.0/300.0", "spend-exhausted", _lineage_ops(_ROOT_LANE, looks=3, budget_wall=300.0, cap_wall=100.0)),
    ("root lineage dir: frozen copy, nothing launched", _S1, "PASS", _BASE_S1 + " lineage-rule:no-looks-yet looks=0 voids=0 spend=0.0/0.0", "no-looks-yet",
     _lineage_ops(_ROOT_LANE, looks=0)),
    ("root lineage dir: tampered rule_text (p1 0.1 -> 0.2; sha256 field unchanged)", _S1, "FAIL", _BASE_S1 + " lineage-rule:tampered", "tampered",
     _lineage_ops(_ROOT_LANE, looks=3, frozen=_LINEAGE_FROZEN_TAMPERED_RULE)),
    ("root lineage dir: sha256 field disagrees (rule_text intact)", _S1, "FAIL", _BASE_S1 + " lineage-rule:tampered", "tampered",
     _lineage_ops(_ROOT_LANE, looks=3, frozen=_LINEAGE_FROZEN_TAMPERED_SHA)),
    ("root lineage dir: frozen copy unparseable", _S1, "FAIL", _BASE_S1 + " lineage-rule:tampered", "tampered",
     _lineage_ops(_ROOT_LANE, looks=3, frozen="{not json\n")),
    ("successor inherits.json -> the root (files at the root)", _S1, "PASS", _INSUFF_3, "insufficient n=3/13",
     [_inherits_op(_S1_SUCCESSOR, _ROOT_LANE)] + _lineage_ops(_ROOT_LANE, looks=3)),
    ("successor inherits.json -> a lane the refined-into walk would not pick (files there only)", _S1, "PASS", _INSUFF_3, "insufficient n=3/13",
     [_inherits_op(_S1_SUCCESSOR, "H-SEED-102-lineage-b")] + _lineage_ops("H-SEED-102-lineage-b", looks=3)),
    ("successor inherits.json chain of two hops (001 -> 103 -> 102; files at 102)", _S1, "PASS", _INSUFF_3, "insufficient n=3/13",
     [_inherits_op(_S1_SUCCESSOR, "H-SEED-103-lineage-c"), _inherits_op("H-SEED-103-lineage-c", "H-SEED-102-lineage-b")] + _lineage_ops("H-SEED-102-lineage-b", looks=3)),
    ("the spec's own lineage dir (registered under the rule at depth 3, no pointer)", _S1, "PASS", _INSUFF_3, "insufficient n=3/13",
     _lineage_ops(_S1_SUCCESSOR, looks=3)),
    ("depth 0 with a lineage dir stays SKIP cap-not-reached (unchanged)", _S2, "SKIP",
     "depth=0 root=H-SEED-002-lint-lane root-span-sha=7d5634e0895c current-span-sha=7d5634e0895c cap-not-reached", None, _lineage_ops("H-SEED-002-lint-lane", looks=3)),
    ("legacy: no lineage dir + lineage-decision line resolving to DEC-901 (unchanged)", _S1, "PASS", _BASE_S1 + " lineage-decision:DEC-901", None, []),
    ("legacy: no lineage dir + no line (unchanged)", _S1, "FAIL", _BASE_S1 + " lineage-decision:absent", None, [_NO_DECISION_LINE]),
    ("legacy: no lineage dir + an id with no ledger row (unchanged)", _S1, "FAIL", _BASE_S1 + " lineage-decision:unresolved", None,
     [(_S1, {"edits": [("lineage-decision: DEC-901", "lineage-decision: DEC-777")]})]),
]

# The lane's sentinel: one spec on which every rigor row must FAIL (the manipulation check of the counted fixture).
_SENTINEL_SPEC = """# H-SENTINEL-001-fires-everything: sentinel

## Status
refined-into: H-SENTINEL-004
Claim type: normative

## Hypothesis
A directive-derived claim with every rigor gap.

## Method
Seeded mutants are planted here. Isolated scratch fixture; the key is never shown to arms. Frozen at registration.
- Budget per run: 1 min

## Binary assertions
1. Determinism: two passes byte-identical.
2. Containment: zero writes outside the run directory.
3. Isolation: the scratch is isolated.
4. Two-pass: the grader is re-run.

## Verdict rule
Keep if 4/4 assertions pass; refine on a spec bug; discard after 3 failed runs. A void run is recorded.

## On keep
- none

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""


def _write(root, rel, content):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)


def _seed_root(dest):
    for rel, content in _SEED_FILES.items():
        _write(dest, rel, content)


def _sentinel_root(dest):
    _write(dest, "hypotheses/H-SENTINEL-001-fires-everything.md", _SENTINEL_SPEC)
    _write(dest, "hypotheses/H-SENTINEL-004-anc.md", "# x\n\n## Status\nrefined-into: H-SENTINEL-003\n\n## Binary assertions\n## Verdict rule\n")
    _write(dest, "hypotheses/H-SENTINEL-003-anc.md", "# x\n\n## Status\nrefined-into: H-SENTINEL-002\n\n## Binary assertions\n## Verdict rule\n")
    _write(dest, "hypotheses/H-SENTINEL-002-anc.md", "# x\n\n## Status\nrefined-into: H-SENTINEL-001\n\n## Binary assertions\n## Verdict rule\n")
    rd = "experiments/runs/H-SENTINEL-001-fires-everything/"
    _write(dest, rd + "VERDICT.json", json.dumps({"verdict": "discard"}))
    _write(dest, rd + "grade.json", json.dumps({"graded": True}))
    _write(dest, rd + "run.json", json.dumps({"treatment_delivered": False}))
    _write(dest, rd + "frozen/span.sha", "0" * 64 + "\n")
    _write(dest, rd + "SHIP.md", "pr: 1\n")
    _write(dest, rd + "fixture/README.md", "row-author: same-id\nseed-author: same-id\n")
    _write(dest, "ledger/work-ledger.jsonl", "")
    _write(dest, "operating-model/ctx/actors/researcher.md", "")
    return os.path.join(dest, "hypotheses", "H-SENTINEL-001-fires-everything.md")


def _apply(root, rel, op):
    """Apply one seeded edit (the fixture harness's rule: every `old` occurs exactly once)."""
    p = os.path.join(root, *rel.split("/"))
    if op.get("delete"):
        os.remove(p)
        return
    if "create" in op:
        if os.path.exists(p):
            raise ValueError("create: %s exists" % rel)
        _write(root, rel, op["create"])
        return
    text = read_text(p)
    if text is None:
        raise ValueError("edit: %s absent" % rel)
    for old, new in op["edits"]:
        if text.count(old) != 1:
            raise ValueError("edit: %r occurs %d times in %s" % (old[:50], text.count(old), rel))
        text = text.replace(old, new, 1)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _rigor_map(root, spec_rel):
    rows, _ = evaluate_rigor(root, os.path.join(root.root, *spec_rel.split("/")))
    return {rw: (st, dt) for rw, st, dt in rows}


def selftest(out):
    import shutil
    import tempfile
    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        out.write(("PASS " if cond else "FAIL ") + name + ("" if (cond or not detail) else ": " + str(detail)) + "\n")

    base = tempfile.mkdtemp(prefix="preflight-rigor-selftest-")
    try:
        seed = os.path.join(base, "seed")
        _seed_root(seed)
        root = Root(seed)

        # --- clean negatives: the counted fixture's three seeds reproduce the lane's recorded rows byte for byte
        check("frozen-span sha of seed 1 equals the lane's sealed frozen/span.sha (cb72d8f7...)",
              frozen_span_sha(_SEED_001) == "cb72d8f7b6275d7768c04428bd78b6eb360aa6d0c6a93016c3a514fd3f72846f", frozen_span_sha(_SEED_001))
        seed_rows = {}
        for spec_rel in _SEED_SPECS:
            rows, meta = evaluate_rigor(root, os.path.join(seed, *spec_rel.split("/")))
            seed_rows[spec_rel] = {rw: (st, dt) for rw, st, dt in rows}
            check("clean negative %s: 0 FAIL rows" % os.path.basename(spec_rel), not meta["fails"], meta["fails"])
            for (rw, st, dt), (est, edt) in zip(rows, _LANE_SEED_ROWS[spec_rel]):
                check("clean negative %s %s reads %s as the lane recorded" % (os.path.basename(spec_rel), rw, est),
                      (st, dt) == (est, edt), "got %s %s" % (st, dt))

        # --- seeded positives: one blind-manifest class per mutant, the intended row FAILs; the ethics rows (the
        #     pre-existing report) cannot tell the mutant from its seed -- the assay-sensitivity control
        fired = {r: 0 for r in RIGOR_ROWS}
        for mid, row, spec_rel, path_rel, op in _MUTANTS:
            mroot = os.path.join(base, "m")
            if os.path.isdir(mroot):
                shutil.rmtree(mroot)
            shutil.copytree(seed, mroot)
            try:
                _apply(mroot, path_rel, op)
            except ValueError as exc:
                check("seeded positive %s applies cleanly" % mid, False, exc)
                continue
            mr = Root(mroot)
            got = _rigor_map(mr, spec_rel)
            st, dt = got[row]
            check("seeded positive %s -> %s FAIL" % (mid, row), st == "FAIL", "got %s %s" % (st, dt))
            fired[row] += st == "FAIL"
            seed_text = read_text(os.path.join(seed, *spec_rel.split("/")))
            mut_text = read_text(os.path.join(mroot, *spec_rel.split("/")))
            check("control %s: the six ethics rows are byte-identical mutant vs seed" % mid,
                  evaluate(seed_text, seed)[0] == evaluate(mut_text, mroot)[0])
        for r in RIGOR_ROWS:
            check("row %s has >= 1 seeded positive and it fired" % r, fired[r] >= 1, fired[r])
        repaired = [m for m in _MUTANTS if "[repaired]" in m[0]]
        check("the four repaired detectors each have a seeded positive (BLIND-SEEDS, CONTROL-PRESENT/OFF-FAILS, LINEAGE-CAP, VOID-CLASS)",
              {"BLIND-SEEDS", "CONTROL-PRESENT/OFF-FAILS", "LINEAGE-CAP", "VOID-CLASS"} <= set(m[1] for m in repaired))

        # --- alternative clean paths
        for name, row, spec_rel, expected, ops in _NEGATIVES:
            nroot = os.path.join(base, "n")
            if os.path.isdir(nroot):
                shutil.rmtree(nroot)
            shutil.copytree(seed, nroot)
            for path_rel, op in ops:
                _apply(nroot, path_rel, op)
            st, dt = _rigor_map(Root(nroot), spec_rel)[row]
            check("clean negative %s -> %s" % (name, expected), st == expected, "got %s %s" % (st, dt))

        # --- row 8 under the lineage stopping rule (lab keep H-DRAFT-5810517d, fragment 0490): the rule is read before the
        #     legacy decision path; a tampered frozen copy FAILs; legacy lineages and depth < 3 read exactly as before
        check("the seed lineage's frozen copy hashes to the plugin's frozen reference (ce73163a...) and its rule_text to the policy (8052bda9...)",
              hashlib.sha256(_LINEAGE_FROZEN.encode("utf-8")).hexdigest() == "ce73163aeda7147edfc0d6fab1fbca9e5dac6ad4bb37ff9ca1c86cf64f85b027"
              and hashlib.sha256(_LINEAGE_POLICY_TEXT.encode("utf-8")).hexdigest() == LINEAGE_POLICY_SHA256)
        plugin_frozen = read_text(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules", "frozen", "lineage-sprt.json"))
        check("the plugin's rules/frozen/lineage-sprt.json is present and byte-identical to the seed lineage's frozen copy", plugin_frozen == _LINEAGE_FROZEN)
        check("the two tampered frozen copies each differ from the clean copy in exactly the intended bytes",
              _LINEAGE_FROZEN_TAMPERED_RULE != _LINEAGE_FROZEN and _LINEAGE_FROZEN_TAMPERED_SHA != _LINEAGE_FROZEN
              and _LINEAGE_FROZEN_TAMPERED_RULE.count('0.2') == 1 and _LINEAGE_FROZEN_TAMPERED_SHA.count('0052bda9') == 1)
        for name, spec_rel, est, edt, emeta, ops in _LINEAGE_RULE_CASES:
            lroot = os.path.join(base, "l")
            if os.path.isdir(lroot):
                shutil.rmtree(lroot)
            shutil.copytree(seed, lroot)
            for path_rel, op in ops:
                _apply(lroot, path_rel, op)
            rows, meta = evaluate_rigor(Root(lroot), os.path.join(lroot, *spec_rel.split("/")))
            got = {rw: (st, dt) for rw, st, dt in rows}
            st, dt = got["LINEAGE-CAP"]
            check("lineage rule: %s -> %s %s" % (name, est, edt.split("current-span-sha=", 1)[-1].split(" ", 1)[-1]),
                  (st, dt) == (est, edt), "got %s %s" % (st, dt))
            check("lineage rule: %s -> META lineage_rule=%r" % (name, emeta), meta.get("lineage_rule") == emeta, meta.get("lineage_rule"))
            check("lineage rule: %s -> the other twelve rows are byte-identical to the seed" % name,
                  {rw: v for rw, v in got.items() if rw != "LINEAGE-CAP"} == {rw: v for rw, v in seed_rows[spec_rel].items() if rw != "LINEAGE-CAP"})

        # --- the sentinel fires every row; the exit code never moves; the grammar holds; two passes are byte-identical
        sroot = os.path.join(base, "sentinel")
        sentinel = _sentinel_root(sroot)
        rows, meta = evaluate_rigor(Root(sroot), sentinel)
        check("sentinel: all 13 rigor rows FAIL", all(st == "FAIL" for _, st, _ in rows), [(r, s) for r, s, _ in rows if s != "FAIL"])
        me = os.path.abspath(__file__)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        r_sent = subprocess.run([sys.executable, me, sroot, sentinel], capture_output=True, text=True, env=env)
        check("exit code unchanged: 13 FAIL rows on the sentinel still exit 0 (report-only)", r_sent.returncode == 0, r_sent.returncode)
        sent_lines = r_sent.stdout.splitlines()
        check("grammar: 6 ethics rows + 13 rigor rows + 1 META line per spec",
              len(sent_lines) == 20 and [l.split("\t")[1] for l in sent_lines[:19]] == CHECKS + RIGOR_ROWS and sent_lines[19].startswith("META\t"),
              len(sent_lines))
        check("grammar: 13 FAIL rigor lines on the sentinel", sum(1 for l in sent_lines[6:19] if l.startswith("FAIL\t")) == 13)
        s1 = os.path.join(seed, *_S1.split("/"))
        r_seed = subprocess.run([sys.executable, me, seed, s1], capture_output=True, text=True, env=env)
        check("exit code unchanged: 0 FAIL rows on seed 1 exits 0", r_seed.returncode == 0, r_seed.returncode)
        seed_lines = r_seed.stdout.splitlines()
        eth_rows, _ = evaluate(read_text(s1), seed)
        check("the first six lines per spec are the pre-existing ethics rows, byte-identical to evaluate()",
              seed_lines[:6] == report_lines(_S1, eth_rows))
        check("relpath in every row is root-relative", all(l.split("\t")[2] == _S1 for l in seed_lines[:19]))
        try:
            mj = json.loads(seed_lines[19].split("\t", 2)[2])
        except Exception:
            mj = {}
        check("META carries the ethics keys and a rigor block with fails/skips/id",
              {"signals", "section_present", "subjects", "route", "rigor"} <= set(mj) and {"fails", "skips", "id"} <= set(mj.get("rigor", {})))
        check("META rigor block carries lineage_rule (null on the seed's legacy lineage)",
              "lineage_rule" in mj.get("rigor", {}) and mj["rigor"]["lineage_rule"] is None and mj["rigor"].get("lineage_depth") == 3)
        c1 = subprocess.run([sys.executable, me, "--census", seed], capture_output=True, text=True, env=env)
        c2 = subprocess.run([sys.executable, me, "--census", seed], capture_output=True, text=True, env=env)
        check("--census exits 0", c1.returncode == 0 and c2.returncode == 0, (c1.returncode, c2.returncode))
        check("two-pass: --census output byte-identical across two runs", c1.stdout == c2.stdout and c1.stdout != "")
        cl = [l for l in c1.stdout.splitlines() if l.startswith("CENSUS\t")]
        check("--census prints one CENSUS line per row (6 + 13) over the 6 seed-root specs",
              len(cl) == 19 and all(l.endswith("\tn=6") for l in cl), len(cl))
        r_use = subprocess.run([sys.executable, me], capture_output=True, text=True, env=env)
        check("usage error (no arguments) exits 2 as before", r_use.returncode == 2, r_use.returncode)
        check("exit code unchanged: seed 1 report and sentinel report differ only in findings, both rc 0",
              r_seed.returncode == r_sent.returncode == 0)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    passed, failed = sum(results), len(results) - sum(results)
    out.write("selftest: %d passed, %d failed\n" % (passed, failed))
    return 0 if failed == 0 else 1


# ====================================================================== CLI
def main(argv):
    out = sys.stdout
    if len(argv) >= 2 and argv[1] == "--selftest":
        return selftest(out)
    if len(argv) >= 3 and argv[1] == "--census":
        census(Root(argv[2]), out)
        return 0
    if len(argv) < 3:
        sys.stderr.write("usage: preflight-rigor.py <repo-root> <spec.md> [...] | --census <repo-root> | --selftest"
                         "   # report-only; always exit 0\n")
        return 2
    root = Root(argv[1])
    for p in argv[2:]:
        emit_spec(root, p, out)
    return 0  # report-only: exit code untouched by findings


if __name__ == '__main__':
    sys.exit(main(sys.argv))
