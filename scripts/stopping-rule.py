#!/usr/bin/env python3
"""stopping-rule.py -- a frozen stopping rule decides when an observation stream has earned a verdict.

  stopping-rule.py --freeze <rule.json> --into <copy.json>
  stopping-rule.py --evaluate <rows.jsonl> --rule <copy.json> --looks <k|all> [--json]
  stopping-rule.py --check <state.jsonl> --rule <copy.json>
  stopping-rule.py --selftest [--into DIR] [--json]

The rule is FROZEN at the gate: `--freeze` copies the rule file's bytes, their sha256 and the
source path into one JSON document. `--evaluate` reads ONLY that frozen copy (never the source
rule), refuses to run when the copy is absent (exit 12, stderr `frozen-rule-missing`) or when
its bytes disagree with the recorded sha (exit 13, stderr `frozen-rule-tampered`), and emits one
state line per look: `evidence-insufficient n=k/<n_min>` before the rule fires and exactly one
terminal -- `evidence-sufficient promote`, `evidence-sufficient hold`, or
`evidence-insufficient max-looks n=<max_looks>` -- at the look the rule fires. `--looks k`
computes the one line for look k from the first k rows alone; `--looks all` emits the same lines
for looks 1..terminal from one process (a batch of the per-look computation, never a different
one). The verdict vocabulary is evidence-sufficient / evidence-insufficient only; the words keep
and discard belong to the hypothesis loop and never appear in an output line.

Rule kinds (docs/stopping-rules.md):
  fixed-n: {"kind": "fixed-n", "n_min": 60, "max_looks": 120, "threshold": {"<class>": 2}}
           fires at look n_min: promote when the class's refusal count <= threshold, else hold.
  sprt:    {"kind": "sprt", "alpha": 0.05, "beta": 0.05, "p0": 0.15, "p1": 0.01, "max_looks": 120}
           running log-likelihood ratio, +log(p1/p0) on a refusal, +log((1-p1)/(1-p0)) otherwise;
           promote when llr >= log((1-beta)/alpha), hold when llr <= log(beta/(1-alpha)),
           max-looks when neither boundary is crossed by look max_looks.

Rows: one JSON object per line, {"look": k, "class": "<id>", "refusal": 0|1}, looks 1..n in order.
State lines (--json): canonical JSON, sorted keys: look, n_min, rule, rule_sha, state, stream
(+ llr under sprt). Stdlib only; reads no clock and no environment beyond the paths it is given.
Exit codes: 0 ok; 1 a --check invariant violated (named on stderr); 2 usage or malformed rows/rule;
3 --looks k past the stream's terminal (`stream-terminated n=<t>`); 12 frozen-rule-missing;
13 frozen-rule-tampered.
"""
import argparse
import hashlib
import json
import math
import os
import sys

FROZEN_MARK = "stopping-rule"
KINDS = ("fixed-n", "sprt")
FORBIDDEN_WORDS = ("keep", "discard")


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def die(code, word, detail=""):
    sys.stderr.write("%s%s\n" % (word, (": " + detail) if detail else ""))
    sys.exit(code)


# ---------------------------------------------------------------- rules and the frozen copy
def parse_rule(text):
    """-> rule dict or raise ValueError."""
    r = json.loads(text)
    if not isinstance(r, dict) or r.get("kind") not in KINDS:
        raise ValueError("rule kind must be one of %s" % (KINDS,))
    ml = r.get("max_looks")
    if not isinstance(ml, int) or ml < 1:
        raise ValueError("max_looks must be a positive integer")
    if r["kind"] == "fixed-n":
        if not isinstance(r.get("n_min"), int) or r["n_min"] < 1:
            raise ValueError("fixed-n n_min must be a positive integer")
        thr = r.get("threshold")
        if not isinstance(thr, dict) or not thr or any(not isinstance(v, int) or v < 0 for v in thr.values()):
            raise ValueError("fixed-n threshold must map class -> non-negative integer")
    else:
        for key in ("alpha", "beta", "p0", "p1"):
            v = r.get(key)
            if not isinstance(v, (int, float)) or not (0.0 < float(v) < 1.0):
                raise ValueError("sprt %s must be a probability strictly inside (0, 1)" % key)
        if float(r["p0"]) == float(r["p1"]):
            raise ValueError("sprt p0 and p1 must differ")
    return r


def freeze(rule_path, into):
    with open(rule_path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
        parse_rule(text)
    except (UnicodeDecodeError, ValueError) as e:
        die(2, "rule-malformed", str(e))
    doc = {"frozen": FROZEN_MARK, "rule_text": text, "sha256": hashlib.sha256(raw).hexdigest(),
           "source": rule_path}
    parent = os.path.dirname(os.path.abspath(into))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(into, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return doc


def load_frozen(copy_path):
    """-> (rule dict, sha) or exit 12 / 13. Reads ONLY the frozen copy."""
    try:
        with open(copy_path, "rb") as fh:
            raw = fh.read()
    except OSError:
        die(12, "frozen-rule-missing", copy_path)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        die(13, "frozen-rule-tampered", "%s is not a JSON document" % copy_path)
    if not isinstance(doc, dict) or doc.get("frozen") != FROZEN_MARK or not isinstance(doc.get("rule_text"), str) \
            or not isinstance(doc.get("sha256"), str):
        die(13, "frozen-rule-tampered", "%s lacks the frozen-copy fields" % copy_path)
    actual = hashlib.sha256(doc["rule_text"].encode("utf-8")).hexdigest()
    if actual != doc["sha256"]:
        die(13, "frozen-rule-tampered", "recorded sha %s, rule bytes hash %s" % (doc["sha256"][:12], actual[:12]))
    try:
        rule = parse_rule(doc["rule_text"])
    except ValueError as e:
        die(13, "frozen-rule-tampered", "rule block unparseable: %s" % e)
    return rule, doc["sha256"]


def sprt_constants(rule):
    a, b, p0, p1 = float(rule["alpha"]), float(rule["beta"]), float(rule["p0"]), float(rule["p1"])
    return {"up": math.log((1.0 - b) / a), "lo": math.log(b / (1.0 - a)),
            "inc1": math.log(p1 / p0), "inc0": math.log((1.0 - p1) / (1.0 - p0))}


def denominator(rule):
    return rule["n_min"] if rule["kind"] == "fixed-n" else rule["max_looks"]


# ---------------------------------------------------------------- rows
def read_rows(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                die(2, "rows-malformed", "%s line %d is not JSON" % (path, i))
            if not isinstance(r, dict) or r.get("look") != len(rows) + 1 or r.get("refusal") not in (0, 1) \
                    or not isinstance(r.get("class"), str):
                die(2, "rows-malformed", "%s line %d: expected {look: %d, class: str, refusal: 0|1}" % (path, i, len(rows) + 1))
            rows.append(r)
    return rows


def stream_id(path):
    base = os.path.basename(path)
    return base[:-6] if base.endswith(".jsonl") else base


# ---------------------------------------------------------------- the per-look computation
def evaluate_stream(rows, rule, sha, sid, upto=None):
    """Replay looks 1..upto (default: all) and return the state lines up to and including the
    first terminal. Each line is computed from the first k rows alone; the batch is the same
    computation applied once per look."""
    lines = []
    n_min = denominator(rule)
    kind = rule["kind"]
    consts = sprt_constants(rule) if kind == "sprt" else None
    refusals = {}
    llr = 0.0
    last = len(rows) if upto is None else min(upto, len(rows))
    for k in range(1, last + 1):
        row = rows[k - 1]
        cls = row["class"]
        refusals[cls] = refusals.get(cls, 0) + row["refusal"]
        state = None
        line = {"look": k, "n_min": n_min, "rule": kind, "rule_sha": sha, "stream": sid}
        if kind == "fixed-n":
            if k >= rule["n_min"]:
                thr = rule["threshold"].get(cls)
                if thr is None:
                    die(2, "rule-class-unknown", "class %s has no threshold in the frozen rule" % cls)
                state = "evidence-sufficient promote" if refusals[cls] <= thr else "evidence-sufficient hold"
        else:
            llr += consts["inc1"] if row["refusal"] else consts["inc0"]
            line["llr"] = llr
            if llr >= consts["up"]:
                state = "evidence-sufficient promote"
            elif llr <= consts["lo"]:
                state = "evidence-sufficient hold"
        if state is None and k >= rule["max_looks"]:
            state = "evidence-insufficient max-looks n=%d" % rule["max_looks"]
        if state is None:
            state = "evidence-insufficient n=%d/%d" % (k, n_min)
        line["state"] = state
        lines.append(line)
        if is_terminal(state):
            break
    return lines


def is_terminal(state):
    return state.startswith("evidence-sufficient ") or state.startswith("evidence-insufficient max-looks")


def emit(lines, as_json):
    for ln in lines:
        text = canon(ln) if as_json else "look %d: %s" % (ln["look"], ln["state"])
        for w in FORBIDDEN_WORDS:
            if w in ln["state"].split():
                die(2, "vocabulary-breach", w)
        sys.stdout.write(text + "\n")


def cmd_evaluate(o):
    rule, sha = load_frozen(o.rule)
    rows = read_rows(o.evaluate)
    sid = stream_id(o.evaluate)
    if o.looks == "all":
        emit(evaluate_stream(rows, rule, sha, sid), o.json)
        return 0
    try:
        k = int(o.looks)
    except ValueError:
        die(2, "usage", "--looks must be a positive integer or `all`")
    if k < 1 or k > len(rows):
        die(2, "usage", "--looks %d outside 1..%d" % (k, len(rows)))
    lines = evaluate_stream(rows, rule, sha, sid, upto=k)
    if lines[-1]["look"] != k:
        die(3, "stream-terminated", "n=%d" % lines[-1]["look"])
    emit(lines[-1:], o.json)
    return 0


# ---------------------------------------------------------------- the invariant checker
def check_state_file(state_path, copy_path):
    """-> (0, None) or (1, invariant-name). Invariants:
    frozen-copy-absent   the frozen copy the rows cite must exist and verify (else no line is valid)
    rule-sha-mismatch    every row's rule_sha equals the frozen copy's sha
    looks-consecutive    rows are looks 1..n in order under one rule and one stream
    one-terminal         exactly one terminal line and it is the last
    never-early          fixed-n: no terminal before look n_min and every earlier line reads
                         evidence-insufficient n=k/n_min; sprt: the terminal's llr crosses its
                         boundary and no earlier llr does; max-looks only at look max_looks
    never-late           the terminal falls at n_min (fixed-n) / at the first crossing (sprt)
    vocabulary           no line carries the words keep or discard
    """
    try:
        with open(copy_path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return 1, "frozen-copy-absent"
    try:
        doc = json.loads(raw.decode("utf-8"))
        rule = parse_rule(doc["rule_text"])
        sha = doc["sha256"]
        if hashlib.sha256(doc["rule_text"].encode("utf-8")).hexdigest() != sha or doc.get("frozen") != FROZEN_MARK:
            return 1, "frozen-copy-tampered"
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return 1, "frozen-copy-tampered"
    rows = []
    with open(state_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if _forbidden(line):
                return 1, "vocabulary"
            try:
                rows.append(json.loads(line))
            except ValueError:
                return 1, "looks-consecutive"
    if not rows:
        return 1, "looks-consecutive"
    for i, r in enumerate(rows, 1):
        if r.get("rule_sha") != sha:
            return 1, "rule-sha-mismatch"
        if r.get("look") != i or r.get("rule") != rule["kind"] or r.get("stream") != rows[0].get("stream") \
                or r.get("n_min") != denominator(rule):
            return 1, "looks-consecutive"
    terms = [i for i, r in enumerate(rows, 1) if is_terminal(str(r.get("state", "")))]
    if len(terms) != 1 or terms[0] != len(rows):
        return 1, "one-terminal"
    t = terms[0]
    n_min = denominator(rule)
    for i, r in enumerate(rows[:-1], 1):
        if r.get("state") != "evidence-insufficient n=%d/%d" % (i, n_min):
            return 1, "never-early"
    tstate = rows[-1]["state"]
    if rule["kind"] == "fixed-n":
        if tstate.startswith("evidence-sufficient") and t < rule["n_min"]:
            return 1, "never-early"
        if tstate.startswith("evidence-sufficient") and t > rule["n_min"]:
            return 1, "never-late"
        if tstate.startswith("evidence-insufficient max-looks") and t != rule["max_looks"]:
            return 1, "never-early" if t < rule["max_looks"] else "never-late"
    else:
        c = sprt_constants(rule)
        for i, r in enumerate(rows, 1):
            llr = r.get("llr")
            if not isinstance(llr, (int, float)):
                return 1, "looks-consecutive"
            crossed = llr >= c["up"] or llr <= c["lo"]
            if i < t and crossed:
                return 1, "never-late"
            if i == t:
                if tstate.startswith("evidence-sufficient") and not crossed:
                    return 1, "never-early"
                if tstate.startswith("evidence-sufficient promote") and not llr >= c["up"]:
                    return 1, "never-early"
                if tstate.startswith("evidence-sufficient hold") and not llr <= c["lo"]:
                    return 1, "never-early"
                if tstate.startswith("evidence-insufficient max-looks") and (crossed or t != rule["max_looks"]):
                    return 1, "never-early" if t < rule["max_looks"] else "never-late"
    return 0, None


def _forbidden(line):
    import re
    return re.search(r"\b(%s)\b" % "|".join(FORBIDDEN_WORDS), line) is not None


def cmd_check(o):
    rc, inv = check_state_file(o.check, o.rule)
    if rc:
        sys.stderr.write("invariant-violated %s\n" % inv)
    else:
        sys.stdout.write("ok\n")
    return rc


# ---------------------------------------------------------------- selftest (seeded violations)
SELFTEST_FIXED = {"kind": "fixed-n", "n_min": 60, "max_looks": 120, "threshold": {"13": 2}}
SELFTEST_SPRT = {"kind": "sprt", "alpha": 0.05, "beta": 0.05, "p0": 0.15, "p1": 0.01, "max_looks": 120}


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(canon(ln) + "\n")


def cmd_selftest(o):
    import tempfile
    into = o.into or tempfile.mkdtemp(prefix="stopping-rule-selftest-")
    if not os.path.isdir(into):
        os.makedirs(into)
    copies = {}
    for kind, rule in (("fixed-n", SELFTEST_FIXED), ("sprt", SELFTEST_SPRT)):
        rp = os.path.join(into, "rule-%s.json" % kind)
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(rule, fh, sort_keys=True)
            fh.write("\n")
        copies[kind] = freeze(rp, os.path.join(into, "frozen-%s.json" % kind))
    fsha, ssha = copies["fixed-n"]["sha256"], copies["sprt"]["sha256"]
    fcopy, scopy = os.path.join(into, "frozen-fixed-n.json"), os.path.join(into, "frozen-sprt.json")

    def rows_from(bits):
        return [{"look": k, "class": "13", "refusal": b} for k, b in enumerate(bits, 1)]

    cases = []
    # clean 1: fixed-n stream terminating at look 60 (one refusal -> promote)
    bits = [0] * 120
    bits[7] = 1
    clean_fixed = evaluate_stream(rows_from(bits), SELFTEST_FIXED, fsha, "self-fixed", None)
    cases.append(("clean-fixed-n-terminal-60", clean_fixed, fcopy, 0, None))
    # clean 2: sprt alternating one refusal with eighteen non-refusals -> never crosses, max-looks at 120
    bits = [1 if k % 19 == 0 else 0 for k in range(120)]
    clean_sprt = evaluate_stream(rows_from(bits), SELFTEST_SPRT, ssha, "self-sprt", None)
    cases.append(("clean-sprt-max-looks-120", clean_sprt, scopy, 0, None))
    # violation 1: fixed-n terminal at look n_min - 1
    v1 = [dict(ln) for ln in clean_fixed[:59]]
    v1[-1]["state"] = "evidence-sufficient promote"
    cases.append(("violation-fixed-n-terminal-at-59", v1, fcopy, 1, "never-early"))
    # violation 2: sprt terminal one look before the boundary crossing (all non-refusals cross at look 20)
    full = evaluate_stream(rows_from([0] * 120), SELFTEST_SPRT, ssha, "self-sprt-early", None)
    v2 = [dict(ln) for ln in full[:-1]]
    v2[-1]["state"] = "evidence-sufficient promote"
    cases.append(("violation-sprt-terminal-one-look-early", v2, scopy, 1, "never-early"))
    # violation 3: a state row whose rule sha differs from the frozen copy
    v3 = [dict(ln) for ln in clean_fixed]
    v3[30]["rule_sha"] = "0" * 64
    cases.append(("violation-rule-sha-differs", v3, fcopy, 1, "rule-sha-mismatch"))
    # violation 4: evidence-insufficient emitted with the frozen copy absent
    cases.append(("violation-frozen-copy-absent", clean_fixed[:10], os.path.join(into, "frozen-absent.json"), 1, "frozen-copy-absent"))

    results = []
    all_ok = True
    for name, lines, copy_path, want_rc, want_inv in cases:
        sp = os.path.join(into, name + ".jsonl")
        _write_lines(sp, lines)
        rc, inv = check_state_file(sp, copy_path)
        ok = rc == want_rc and inv == want_inv
        all_ok &= ok
        if rc:
            sys.stderr.write("%s invariant-violated %s\n" % (name, inv))
        results.append({"case": name, "state_file": sp, "frozen_copy": copy_path, "rc": rc, "invariant": inv,
                        "expected_rc": want_rc, "expected_invariant": want_inv, "ok": ok, "lines": len(lines),
                        "terminal": lines[-1]["state"] if lines else None})
    if o.json:
        sys.stdout.write(json.dumps({"into": into, "cases": results, "ok": all_ok}, indent=1, sort_keys=True) + "\n")
    else:
        for r in results:
            sys.stdout.write("%s %s rc=%d invariant=%s (expected rc=%d %s)\n" % (
                "PASS" if r["ok"] else "FAIL", r["case"], r["rc"], r["invariant"], r["expected_rc"], r["expected_invariant"]))
        sys.stdout.write("selftest %s (%d/%d cases behaved) files under %s\n" % ("ok" if all_ok else "FAIL",
                                                                                  sum(1 for r in results if r["ok"]), len(results), into))
    return 0 if all_ok else 1


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="frozen stopping rule: freeze at the gate, evaluate per look, check invariants")
    ap.add_argument("--freeze", metavar="RULE_JSON")
    ap.add_argument("--into", metavar="PATH", help="--freeze: the frozen copy to write; --selftest: the directory for seeded files")
    ap.add_argument("--evaluate", metavar="ROWS_JSONL")
    ap.add_argument("--rule", metavar="FROZEN_COPY")
    ap.add_argument("--looks", metavar="K|all")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", metavar="STATE_JSONL")
    ap.add_argument("--selftest", action="store_true")
    o = ap.parse_args()
    verbs = [bool(o.freeze), bool(o.evaluate), bool(o.check), o.selftest]
    if sum(verbs) != 1:
        ap.print_usage(sys.stderr)
        return 2
    if o.freeze:
        if not o.into:
            die(2, "usage", "--freeze needs --into <copy>")
        doc = freeze(o.freeze, o.into)
        sys.stdout.write("frozen %s sha256 %s -> %s\n" % (o.freeze, doc["sha256"], o.into))
        return 0
    if o.evaluate:
        if not o.rule or not o.looks:
            die(2, "usage", "--evaluate needs --rule <frozen copy> and --looks <k|all>")
        return cmd_evaluate(o)
    if o.check:
        if not o.rule:
            die(2, "usage", "--check needs --rule <frozen copy>")
        return cmd_check(o)
    return cmd_selftest(o)


if __name__ == "__main__":
    sys.exit(main())
