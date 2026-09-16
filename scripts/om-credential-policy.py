#!/usr/bin/env python3
"""om-credential-policy.py -- refuses a shared subscription token for a model-calling tier when
the recorded author census exceeds one, and offers three alternatives.

Verb: check --rows <rows.jsonl> --tier <tier> --class <credential_class>

Reads every `substrate-discovered` row (`schema` 1 -- scripts/om-integrate.py's own row shape,
the same rows `probe --json` appends to `ledger/om-substrates.jsonl`) recorded in the rows file,
takes the `authors_90d` integer those rows share, and refuses a `shared-subscription-token`
credential for the given tier whenever that count is more than 1, printing exactly one
`CREDENTIAL-REFUSED` line and exactly three `OFFER` lines naming the three credential shapes the
platform documents for shared use (`api-key`, `federation`, `platform-identity`, in that order)
and exiting 3. When the count is 1, or the requested class is one of those three accepted
shapes, it prints `CREDENTIAL-OK` and exits 0. When the census itself is undecidable (no schema-1
row carries an integer `authors_90d` >= 1, or the schema-1 rows disagree on it), it prints
`CREDENTIAL-UNDECIDABLE` and exits 2 -- a void, never a substantive answer.

Ported by intent from the lab keep getfatday/cause-n-effect H-DRAFT-744a5773-om-credential-policy
(kept 2026-09-16: five counted looks, A1 pass in every one, cold-verified). This file owns the
refusal rule and nothing else does: `scripts/om-integrate.py`'s `compose` calls `evaluate()` below
as its own imported entry point (one call site, before any model-calling tier is bound) and never
re-implements the threshold or the class table; this file in turn never imports om-integrate.py or
anything else in this plugin -- the dependency runs one way. Zero LLM calls anywhere in this file.
Python 3.9, stdlib only. No credential value is ever read, printed or planted here: `authors_90d`
is an integer covariate and the credential classes are labels.
"""
import argparse
import json
import sys

REFUSED_CLASS = "shared-subscription-token"
ACCEPTED_CLASSES = ("api-key", "federation", "platform-identity")

# Frozen at registration (Method, H-DRAFT-744a5773-om-credential-policy): grammar and order.
OFFER_LINES = (
    "OFFER api-key repository-owned",
    "OFFER federation workload-identity",
    "OFFER platform-identity oidc",
)


def load_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def census(rows):
    """Returns (authors_90d:int, reason:None) on a decidable census, else (None, reason): a
    missing/non-integer/zero authors_90d, a schema/kind other than the substrate-discovered row
    shape, or rows that disagree on the count are all undecidable, never treated as a small
    count."""
    values = set()
    for row in rows:
        if row.get("kind") != "substrate-discovered" or row.get("schema") != 1:
            continue
        v = row.get("authors_90d")
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            continue
        values.add(v)
    if not values:
        return None, "no schema-1 substrate-discovered row carries an integer authors_90d >= 1"
    if len(values) > 1:
        return None, "schema-1 rows disagree on authors_90d: %s" % sorted(values)
    return next(iter(values)), None


def evaluate(rows, tier, credential_class):
    """The one substantive answer: (lines: list[str], exit_code: int). Never prints anything
    itself, so an importer (`om-integrate.py`'s `compose`) can inspect the verdict before it
    reaches a terminal; `cmd_check` below is the only caller that prints."""
    n, reason = census(rows)
    if n is None:
        return (["CREDENTIAL-UNDECIDABLE %s" % reason], 2)
    if credential_class == REFUSED_CLASS and n > 1:
        lines = ["CREDENTIAL-REFUSED tier=%s class=%s authors_90d=%d" % (tier, REFUSED_CLASS, n)]
        lines.extend(OFFER_LINES)
        return (lines, 3)
    return (["CREDENTIAL-OK tier=%s class=%s authors_90d=%d" % (tier, credential_class, n)], 0)


def cmd_check(args):
    try:
        rows = load_rows(args.rows)
    except (OSError, ValueError) as exc:
        print("CREDENTIAL-UNDECIDABLE cannot read rows file %s (%s)" % (args.rows, exc))
        return 2
    lines, code = evaluate(rows, args.tier, args.credential_class)
    for line in lines:
        print(line)
    return code


def _selftest():
    """A quick built-in smoke check for a cold caller with no fixture on hand -- three synthetic
    row sets (authors_90d 1, 2, 5), the class table, the void reading. Not a substitute for
    scripts/selftest-om-credential-policy.py (the full port-fidelity suite): this is the fast
    path `--selftest` gives a caller who wants one answer before trusting the script."""
    results = []

    def row(n):
        return {"kind": "substrate-discovered", "schema": 1, "authors_90d": n}

    cases = [(1, "shared-subscription-token", 0), (2, "shared-subscription-token", 3),
             (5, "shared-subscription-token", 3), (1, "api-key", 0), (2, "api-key", 0),
             (5, "api-key", 0), (2, "federation", 0), (2, "platform-identity", 0)]
    for n, cls, want_exit in cases:
        lines, code = evaluate([row(n)], "selftest-tier", cls)
        refused = sum(1 for l in lines if l.startswith("CREDENTIAL-REFUSED"))
        offers = sum(1 for l in lines if l.startswith("OFFER"))
        want_refused = 1 if want_exit == 3 else 0
        want_offers = 3 if want_exit == 3 else 0
        passed = code == want_exit and refused == want_refused and offers == want_offers
        results.append(passed)
        print(("PASS " if passed else "FAIL ") + "authors_90d=%d class=%s" % (n, cls))
    n, _reason = census([{"kind": "substrate-discovered", "schema": 1}])
    passed = n is None
    results.append(passed)
    print(("PASS " if passed else "FAIL ") + "undecidable-census")
    n_pass = sum(1 for r in results if r)
    print("%d/%d checks passed" % (n_pass, len(results)))
    return 0 if all(results) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="om-credential-policy.py")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in smoke check and exit (ignores every other argument)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check")
    p.add_argument("--rows", required=True, help="substrate-discovered rows file to replay")
    p.add_argument("--tier", required=True)
    p.add_argument("--class", dest="credential_class", required=True)
    p.set_defaults(fn=cmd_check)
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.cmd != "check":
        ap.print_usage(sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
