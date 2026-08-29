#!/usr/bin/env python3
"""doctor-remediate: the frozen remediation ladder (the environment-health
doctor's remediation half; counted under H-183-doctor-guided-remediation in
the source lab, kept 2026-08-26, two consecutive 5/5; shipped byte-preserving
— only this provenance framing differs from the counted fixture copy).
Usage guide: docs/doctor.md in this plugin.

Stdlib only, no imports from this repository, deterministic —
output is a pure function of argv (+ the bytes of a --probe-record file for
`verify`); no clock reads, no environment reads, no network.

Input:  a typed credential state from the H-182 classifier's frozen vocabulary
        (CLEAN | FLAP-DEGRADED | HARD-EXPIRED | INDETERMINATE).
Output: the exact next remediation step from the frozen four-state decision
        table (H-183 spec, Method), on stdout, with a stable typed exit code.

Subcommands
  step   --state <STATE> [--version-fit passed|failed|unknown]
         Emit exactly one next step for the state. FLAP-DEGRADED branches on
         whether the setup-token version-fit probe passed on this CLI;
         version-fit defaults to `unknown`, which takes the conservative
         partial-mitigation branch (the token path is trusted only when
         PROVEN, per fragment 0186's stated condition).
  verify --state <STATE> --probe-record <path>
         Post-remediation heal verification: fail-closed over the re-probe
         record. VERIFIED-CLEAN only when the record shows a passing
         fail-closed probe through the token path; absent or unreadable
         evidence refuses (never verifies).

Frozen decision table (exit codes are the ladder's stable contract):
  CLEAN                          -> no action                        exit 0
  FLAP-DEGRADED + fit passed     -> token path, then re-probe        exit 10
  FLAP-DEGRADED + fit failed/unk -> /login + serialized startups,
                                    then re-probe (partial)          exit 11
  HARD-EXPIRED                   -> re-mint, needs-maintainer        exit 20
  INDETERMINATE                  -> fail-closed probe + re-classify  exit 30
  verify: VERIFIED-CLEAN 0 | NOT-VERIFIED 12 | fail-closed/reject 64

Rejected rungs (frozen OUT of the table; never emitted in any plan):
ANTHROPIC_API_KEY (bills as API usage, not the subscription — issue 43333)
and credential-store manipulation at the OS layer (wrong layer, security
debt). They stay rejected in every branch above.
"""
import argparse
import json
import sys

STATES = ("CLEAN", "FLAP-DEGRADED", "HARD-EXPIRED", "INDETERMINATE")

STEP_CLEAN = (
    "NO-ACTION: state CLEAN — the ladder emits no remediation for a clean "
    "surface. Continue passive monitoring; re-run the doctor on the next "
    "probe records."
)
STEP_FLAP_TOKEN = (
    "STEP token-path: export CLAUDE_CODE_OAUTH_TOKEN from the operator token "
    "store (minted once via `claude setup-token`, 1-year lifetime) into the "
    "invocation environment of every lane child — per-invocation environment "
    "only, never written to disk or artifacts. This bypasses the per-session "
    "OAuth refresh, the single-use-refresh-token race, and the credential-"
    "store dependency entirely. Then re-probe: run one fail-closed haiku "
    "probe through the token path and feed its record to the verify step — "
    "the state is CLEAN only on a passing probe record."
)
STEP_FLAP_PARTIAL = (
    "STEP partial-mitigation: the token path is not version-fit-proven on "
    "this CLI, so fall back: run /login to re-mint the access token (heals "
    "until that token expires), and serialize session startups behind a "
    "single flock so concurrent sessions stop racing the single-use refresh "
    "token. This mitigates the failing-refresh mode; it does not remove it. "
    "Then re-probe: run one fail-closed haiku probe and feed its record to "
    "the verify step — the state is CLEAN only on a passing probe record."
)
STEP_HARD_EXPIRED = (
    "STEP needs-maintainer: access and refresh expiries are both past — "
    "re-mint required. Flag the maintainer to run `claude setup-token` "
    "(browser approval; cannot be automated). No automated rung exists for "
    "this state; the ladder stops here."
)
STEP_INDETERMINATE = (
    "STEP re-classify: the records are insufficient to type the state. Run "
    "one fail-closed haiku probe now, append its record to the stream, and "
    "re-run the classifier — act only on the resulting typed state, never "
    "on a guess."
)

EXIT_BY_STEP = {
    STEP_CLEAN: 0,
    STEP_FLAP_TOKEN: 10,
    STEP_FLAP_PARTIAL: 11,
    STEP_HARD_EXPIRED: 20,
    STEP_INDETERMINATE: 30,
}


def step(state, version_fit):
    if state not in STATES:
        print("REJECT: unknown typed state '%s' — the ladder is frozen to "
              "the four-state vocabulary (CLEAN | FLAP-DEGRADED | "
              "HARD-EXPIRED | INDETERMINATE); failing closed." % state)
        return 64
    if state == "CLEAN":
        text = STEP_CLEAN
    elif state == "FLAP-DEGRADED":
        text = STEP_FLAP_TOKEN if version_fit == "passed" else STEP_FLAP_PARTIAL
    elif state == "HARD-EXPIRED":
        text = STEP_HARD_EXPIRED
    else:
        text = STEP_INDETERMINATE
    print(text)
    return EXIT_BY_STEP[text]


def verify(state, probe_record_path):
    if state not in STATES:
        print("REJECT: unknown typed state '%s' — the ladder is frozen to "
              "the four-state vocabulary (CLEAN | FLAP-DEGRADED | "
              "HARD-EXPIRED | INDETERMINATE); failing closed." % state)
        return 64
    try:
        with open(probe_record_path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        print("VERIFY-FAIL-CLOSED: probe evidence absent or unreadable at "
              "%s; refusing to verify." % probe_record_path)
        return 64
    ok = (isinstance(rec, dict)
          and rec.get("probe") == "fail-closed-haiku"
          and rec.get("authenticated") is True
          and rec.get("credential_source") == "CLAUDE_CODE_OAUTH_TOKEN")
    if ok:
        print("VERIFIED-CLEAN: post-remediation re-probe passed via "
              "CLAUDE_CODE_OAUTH_TOKEN; evidence: %s" % probe_record_path)
        return 0
    print("NOT-VERIFIED: the re-probe record does not show a passing "
          "token-path probe; the state remains degraded. evidence: %s"
          % probe_record_path)
    return 12


def main(argv=None):
    ap = argparse.ArgumentParser(prog="doctor-remediate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_step = sub.add_parser("step")
    ap_step.add_argument("--state", required=True)
    ap_step.add_argument("--version-fit", default="unknown",
                         choices=["passed", "failed", "unknown"])
    ap_verify = sub.add_parser("verify")
    ap_verify.add_argument("--state", required=True)
    ap_verify.add_argument("--probe-record", required=True)
    o = ap.parse_args(argv)
    if o.cmd == "step":
        return step(o.state, o.version_fit)
    return verify(o.state, o.probe_record)


if __name__ == "__main__":
    sys.exit(main())
