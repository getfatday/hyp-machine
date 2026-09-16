#!/usr/bin/env python3
"""Mechanical pre-flight for hypothesis specs (hyp).

Usage: preflight.py <hypotheses/H-NNN-slug.md>
Exit 0 = PASS (spec auto-activates and runs, no human intervention).
Exit 1 = ESCALATE (one-way-door signal or checklist failure; human review required).
Exit 2 = MALFORMED (spec missing required sections; fix and re-run).

Deterministic checks only. Prints one line per check: PASS/FAIL <check>: <detail>.
"""
import re, sys, os

ONE_WAY_SIGNALS = [
    # phrases that indicate external or irreversible effects in Method/Fixture text
    r'--dangerously-skip-permissions',
    r'\bproduction\b', r'\blive (?:system|service|api|deploy)\b',
    r'\bdelete[s]? (?:the )?(?:remote|repository|branch)\b',
    r'\bsend[s]? (?:an? )?(?:email|message|slack)\b', r'\bpost[s]? to\b',
    r'\bpurchas', r'\bpayment\b', r'\bspend[s]? (?:real )?money\b',
]
SANDBOX_SIGNALS = [r'\bsandbox', r'\bworktree\b', r'\bclone\b', r'\btransplant', r'\bisolated\b', r'\bscratch', r'\bfixture\b']

# --- H-262 fixture-fresh check (byte-identical region: lab & shipped preflight) ---
FIXTURE_PIN_RE = re.compile(r'^\s*(?:[-*]\s+)?Fixture-SHA256:\s*([0-9a-fA-F]{64})\s+(\S+)\s*$', re.M)

def fixture_fresh_check(spec_path, text, check):
    """Fail-closed fixture freshness (H-262): re-hash every declared Fixture-SHA256
    pin (repo-root-relative path, root = grandparent of the spec file), FAIL on byte
    drift or missing file, and print the blast radius: every co-pinning spec in the
    same hypotheses directory. Specs with no pin lines get an advisory only (pins
    stay optional in v1)."""
    import hashlib, glob
    spec_abs = os.path.abspath(spec_path)
    specs_dir = os.path.dirname(spec_abs)
    root = os.path.dirname(specs_dir)
    pins = FIXTURE_PIN_RE.findall(text)
    if not pins:
        print("ADVISORY(fixture-fresh): no Fixture-SHA256 pins declared — fixture bytes unverified (pins optional in v1)")
        return
    bad = []
    for declared, rel in pins:
        target = os.path.join(root, rel)
        if not os.path.isfile(target):
            bad.append((rel, declared.lower(), "MISSING"))
            continue
        with open(target, 'rb') as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        if actual != declared.lower():
            bad.append((rel, declared.lower(), actual))
    if not bad:
        check("fixture-fresh", True, "%d pin(s) verified fresh" % len(pins))
        return
    drifted = set(rel for rel, _d, _a in bad)
    co = {}
    for other in sorted(glob.glob(os.path.join(specs_dir, "*.md"))):
        if os.path.abspath(other) == spec_abs:
            continue
        try:
            otext = open(other).read()
        except Exception:
            continue
        for _od, orel in FIXTURE_PIN_RE.findall(otext):
            if orel in drifted:
                co.setdefault(orel, []).append(os.path.basename(other))
    for rel, declared, actual in bad:
        print("FIXTURE-DRIFT %s: declared %s actual %s" % (rel, declared, actual))
        print("BLAST-RADIUS %s: %s" % (rel, ", ".join(co[rel]) if co.get(rel) else "none (no co-pinning specs)"))
    blast = sorted(set(s for lst in co.values() for s in lst))
    check("fixture-fresh", False,
          "%d of %d pin(s) stale or missing — fail closed; blast radius (co-pinning specs): %s"
          % (len(bad), len(pins), ", ".join(blast) if blast else "none"))
# --- end H-262 fixture-fresh check ---

def main(path):
    text = open(path).read()
    ok = True
    def check(name, cond, detail):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + ": " + detail)
        if not cond: ok = False

    required = ['## Status', '## Hypothesis', '## Variable under test', '## Baseline',
                '## Method', '## Binary assertions', '## Verdict rule', '## Runs']
    missing = [h for h in required if h not in text]
    if missing:
        print("MALFORMED missing sections: " + ", ".join(missing)); return 2

    method = text.split('## Method',1)[1].split('## Binary assertions',1)[0]

    hits = [p for p in ONE_WAY_SIGNALS if re.search(p, method, re.I)]
    check("two-way-door", not hits, "no one-way-door signals in Method" if not hits else "signals: " + "; ".join(hits))

    sandboxed = any(re.search(p, method, re.I) for p in SANDBOX_SIGNALS)
    check("sandboxing", sandboxed, "Method references an isolated execution surface" if sandboxed else "no sandbox/isolation reference found")

    gt = bool(re.search(r'(never|not).{0,60}(arm[- ]visible|copied into|shown to arms|see[s]? the manifest)|directive 8|(arm|arms) never sees?', text, re.I))
    check("ground-truth-isolation", gt, "spec states arm-invisible ground truth" if gt else "no arm-invisible ground-truth statement found — add a sentence like: \"the answer key and grader live harness-side and are never shown to arms\" (accepted words: never/not ... arm-visible / shown to arms / arm never sees)")

    frozen = bool(re.search(r'[Ff]rozen at registration|[Ff]rozen \(', text)) or 'FROZEN' in text
    check("frozen-protocol/rubric", frozen, "freeze language present" if frozen else "no freeze statement — add a sentence like: \"Frozen at registration: the corpus, keys, prompts, and grading rubric\" (accepted words: \"frozen at registration\", \"Frozen (\", or FROZEN)")

    budget = bool(re.search(r'Budget per run', text))
    check("budget-declared", budget, "budget line present (no cap applies; cost recorded per run)" if budget else "no budget line")

    # scope exclusions: any 'excludes/exclusion/out of scope/deferred' sentence must carry a mapping
    excl = re.findall(r'[^.\n]*\b(?:out of scope|scope exclusion|excludes?|deferred)\b[^.\n]*\.', text, re.I)
    unmapped = [e.strip()[:90] for e in excl
                if not re.search(r'DoD|definition of done|H-\d{3}|future hypothesis|separate .{0,20}hypothesis|plugin', e, re.I)]
    check("scope-exclusions-mapped", not unmapped,
          f"{len(excl)} exclusion sentence(s), all mapped" if not unmapped else "unmapped: " + " | ".join(unmapped))

    assertions = text.split('## Binary assertions',1)[1].split('## Verdict rule',1)[0]
    n = len(re.findall(r'^\d+\.', assertions, re.M))
    check("binary-assertions", (lambda vb: 1 <= n <= 5 and (n >= 3 or bool(re.search(r'^SUBSTANTIVE-ASSERTIONS:', vb, re.M) and re.search(r'\bvoids?\b', vb, re.I))))(text.split('## Verdict rule',1)[1].split('## Runs',1)[0]), f"{n} decision assertions (fewer than 3 admitted only with a SUBSTANTIVE-ASSERTIONS: line and a typed void in the Verdict rule)")

    verdict = text.split('## Verdict rule',1)[1].split('## Runs',1)[0]
    mech = bool(re.search(r'[Kk]eep if .*assertions pass', verdict)) and bool(re.search(r'refine|discard', verdict))
    check("mechanical-verdict", mech, "keep/refine/discard rule present" if mech else "verdict rule not mechanical")

    # --- claim-type-gate contract v1 ---
    # Claim-type routing: descriptive specs measure what is; normative specs propose
    # what should be. A descriptive spec may not bind a maintainer decision through
    # its On-keep rows (maintainer-ruling= predicates are decision-class); the route
    # is a bridging normative spec. A missing line is advisory only (the corpus
    # predates the field); flipping missing-to-FAIL is a future hypothesis after
    # corpus migration.
    ct = re.search(r'^Claim type:\s*(descriptive|normative)\s*(?:<!--.*)?$', text, re.M)
    if ct:
        onkeep = text.split('## On keep',1)[1].split('## Runs',1)[0] if '## On keep' in text else ''
        drows = [ln.strip()[:90] for ln in onkeep.splitlines()
                 if ln.lstrip().startswith('-') and re.search(r'maintainer-ruling\s*=', ln)]
        if ct.group(1) == 'descriptive':
            check("claim-type", not drows,
                  "descriptive with observation-only On-keep rows" if not drows
                  else "descriptive spec carries decision-class On-keep row(s) (maintainer-ruling=): "
                       "route the decision through a bridging normative spec — register a normative "
                       "successor citing this descriptive finding (maintainer decisions land via "
                       "docs/decisions.md); rows: " + " | ".join(drows))
        else:
            check("claim-type", True, "normative — decision-class On-keep rows permitted")
    else:
        print("ADVISORY(claim-type): no 'Claim type: descriptive | normative' line (corpus predates the field; declare it — v1 enforces declared specs only)")
    # --- end claim-type-gate contract v1 ---

    fixture_fresh_check(path, text, check)

    print("RESULT: " + ("PASS — auto-activate and run" if ok else "ESCALATE — human review required"))
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
