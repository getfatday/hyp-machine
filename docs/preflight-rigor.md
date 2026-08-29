# Preflight rigor: the ethics extension (report-only)

> **REPORT-ONLY BY DESIGN.** `scripts/preflight-rigor.py` prints findings and always exits
> 0 — it gates nothing. The enforcement flip (making `## Ethical assumptions` a required
> spec section in `scripts/preflight.py` and turning ethics FAILs into ESCALATE) is
> **maintainer-gated**: it lands only on an explicit maintainer ruling, applies to specs
> registered after the flip commit only, and pre-existing specs are never re-gated. Do not
> wire this script into a blocking path.

The gap it closes: a lab that runs simulated humans and prepares real-person invites with
no named ethical-assumption check anywhere in its preflight. The extension adds five
contract checks (rendered as six report rows — the tier cross-check is its own row) over
each hypothesis spec:

| Row | Fires when |
|---|---|
| `ethics-section-present` | Subject signals hit in Hypothesis+Method AND `## Ethical assumptions` is absent. A sectionless spec with no signal hits PASSes — pre-existing specs are never re-gated. When the section is absent, the other five rows report SKIP so this class trips exactly one check. |
| `ethics-declared` | Signals hit but the `subjects:` line is missing, placeholder, or a bare `none` — the calibrated over-trigger escape is one clause: `none — <reason>`. |
| `ethics-nonempty` | Subjects are declared but any of the `consent` / `data` / `withdrawal` / `deception` keyed lines is missing or empty after comment strip. |
| `ethics-consent-artifact` | A real-human subject is named and the `consent:` line resolves no committed artifact path. |
| `ethics-tier-mismatch` | Method names a real-human interaction (invite / second-human / real human) while `subjects:` declares neither `real-human` nor `none — <reason>`. |
| `ethics-sim-dignity` | A sim-persona subject lacks a `sim-dignity:` line, or a transcript-grounded persona's consent line resolves no committed path (grounded cards inherit the source humans' consent surface). |

## Usage

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preflight-rigor.py" <repo-root> hypotheses/H-*.md
```

Output: byte-stable `STATUS<TAB>check<TAB>relpath<TAB>detail` rows (PASS/FAIL/SKIP) plus one
`META` line per spec (signal hits, section presence, routing). Exit 0 always; findings never
change the exit code. Run it alongside — not inside — the shipped `scripts/preflight.py`
(the 8-check deterministic gate), whose PASS/ESCALATE/MALFORMED semantics are untouched.

## The spec section it reads

```
## Ethical assumptions

subjects: <who is affected — or `none — <reason>` when subject-signal words over-trigger>
consent: <the committed consent artifact path, when a real human participates>
data: <what is collected/retained>
withdrawal: <how a subject exits>
deception: <any, and its debrief>
sim-dignity: <for sim-persona subjects: the dignity constraints the simulation honors>
```

## Evidence

**H-132-ethics-gate** (source lab, kept 2026-08-27, two consecutive counted 4/4,
scripts-only, ~$0): every seeded human-subject defect spec tripped exactly its intended
check with zero seeds missed and zero cross-fires; zero ethics FAILs across the frozen
must-silent corpus classification (the over-trigger cost capped at the one `none — <reason>`
clause); the four retrofitted active specs passed all checks with diffs confined to the
inserted section; the full-corpus double pass was byte-identical.

The shipped script is the counted fixture implementation as counted; only provenance
framing and the script name differ. The enforcement flip and any retrofit of a consumer's
pre-existing specs remain maintainer decisions, out of this plugin's hands.
