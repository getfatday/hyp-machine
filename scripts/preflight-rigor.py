#!/usr/bin/env python3
"""The ethics extension to preflight — REPORT-ONLY (counted under
H-132-ethics-gate in the source lab, kept 2026-08-27, two consecutive counted
4/4: every seeded human-subject defect tripped exactly its intended check,
zero fires across the frozen must-silent corpus, retrofit specs passed
unchanged, full-corpus double pass byte-identical. Shipped as counted from
the fixture copy `impl/ethics_checks.py` — only provenance framing and the
script name differ; usage guide: docs/preflight-rigor.md in this plugin).

Report-only law (the counted admission discipline): findings are printed
rows, the exit code is untouched — this script gates NOTHING. The gated
integration (`## Ethical assumptions` into preflight's required sections,
ethics FAIL -> ESCALATE) happens only on an explicit maintainer ruling; see
docs/preflight-rigor.md. Do not wire this into a blocking path yourself.

Calibrated report-only semantics, frozen with the counted fixture (the two
decisions the contract text forces):

1. ethics-section-present fires (FAIL row) only when SUBJECT_SIGNALS hit in
   Hypothesis+Method AND the section is absent. A sectionless spec with NO
   signal hits reports PASS — pre-existing specs are never re-gated.
2. When the section is absent, checks 2-6 report SKIP ("absence owned by
   ethics-section-present") so that defect class trips exactly one check
   (zero cross-fires).

Check names (6 rows per spec; ethics-tier-mismatch is check 4's named
cross-check, emitted as its own row):
  ethics-section-present, ethics-declared, ethics-nonempty,
  ethics-consent-artifact, ethics-tier-mismatch, ethics-sim-dignity

CLI: preflight-rigor.py <repo-root> <spec.md> [...]   # report-only; always exit 0
"""
import os
import re

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
    """Run the six report rows over one spec text.

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


if __name__ == '__main__':
    import sys
    import json
    if len(sys.argv) < 3:
        print("usage: preflight-rigor.py <repo-root> <spec.md> [...]  # report-only; always exit 0",
              file=sys.stderr)
        sys.exit(2)
    root = sys.argv[1]
    for p in sys.argv[2:]:
        rows, meta = evaluate(open(p).read(), root)
        for ln in report_lines(os.path.relpath(p, root), rows):
            print(ln)
        print("META\t%s\t%s" % (os.path.relpath(p, root), json.dumps(meta, sort_keys=True)))
    sys.exit(0)  # report-only: exit code untouched by findings
