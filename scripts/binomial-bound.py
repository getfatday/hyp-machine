#!/usr/bin/env python3
# agent tier: build / sonnet / high
"""binomial-bound.py -- one-sided upper confidence bound on a binomial rate, by rule.

  binomial-bound.py --alpha 0.05 --n 1000 --confidence 0.99 [--json]

Computes the largest count k such that P(X <= k | p=alpha, n) < confidence is false for k-1 and
true for k -- i.e. the smallest k with P(X <= k) >= confidence (the one-sided upper CI bound at
the stated confidence on the count, not the rate). Exact binomial CDF via math.comb (stdlib
only, Python 3.8+). Used by routing-derive.py A4 so a pass bar is set by rule with sampling
margin, never at the bare alpha share (H-DRAFT-d5a8d9b6-routing-derive, Binary assertions A4).

Exit codes: 0 ok; 2 usage/argument error.
"""
import argparse
import json
import math
import sys


def binomial_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p). k, n ints; 0 <= p <= 1."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return total


def upper_bound(alpha, n, confidence):
    """Smallest k with P(X <= k | p=alpha, n) >= confidence."""
    k = int(alpha * n)
    while binomial_cdf(k, n, alpha) < confidence:
        k += 1
    # walk back down in case the starting point overshot
    while k > 0 and binomial_cdf(k - 1, n, alpha) >= confidence:
        k -= 1
    return k


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--confidence", type=float, required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not (0.0 < args.alpha < 1.0) or args.n < 1 or not (0.0 < args.confidence < 1.0):
        sys.stderr.write("usage-error: alpha and confidence in (0,1), n >= 1\n")
        return 2
    k = upper_bound(args.alpha, args.n, args.confidence)
    if args.json:
        print(json.dumps({"alpha": args.alpha, "n": args.n, "confidence": args.confidence, "bound": k},
                          sort_keys=True, separators=(",", ":")))
    else:
        print(k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
