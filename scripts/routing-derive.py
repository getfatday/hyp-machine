#!/usr/bin/env python3
# agent tier: build / sonnet / high
"""routing-derive.py -- reads this repository's own routing ledger
(ledger/routing-ledger.jsonl, hooks/scripts/routing-ledger.py's agent-route/v1 rows) and the
frozen rule bytes (rules/routing-derive.json) and proposes at most one bounded, rule-conformant
routing change per run, never a table edit by hand and never applied automatically -- a
candidate is written ONLY as a draft hypothesis spec (Method: "as a draft hypothesis spec, and
never as a table edit").

  routing-derive.py report   --ledger <ledger.jsonl> --table <state.json> --rule <rule.json>
                              --out <routing-report.md> [--root DIR] [--workdir DIR]
  routing-derive.py propose  --ledger <ledger.jsonl> --table <state.json> --rule <rule.json>
                              --out-dir <dir> [--template <path>] [--prices <path>]
                              [--root DIR] [--workdir DIR] [--budget-usd F]
  routing-derive.py apply-rollbacks
                              --ledger <ledger.jsonl> --table <state.json> --rule <rule.json>
                              --out-table <state.json> --out-derive-row <derive-row.json>
                              [--root DIR] [--workdir DIR]
  routing-derive.py default-bump --table <state.json> --new-default-sha <sha> --out-table <state.json>
  routing-derive.py --check  --ledger <ledger.jsonl> --table <state.json> --rule <rule.json>
  routing-derive.py --selftest [--json]

Source: lab hypothesis H-DRAFT-d5a8d9b6-routing-derive (VERDICT.json evidence-sufficient
promote, five counted looks 5/5). Ported by intent onto this plugin's live tip, not re-pinned
to the lab fixture's placeholder tables -- see "Reconciling with the live tables" in
docs/model-routing.md for every drift this port resolved and why. In short:

  * UNIT OF CHANGE is this repository's CLASS (mechanical/execute/think/adversarial/unknown --
    hooks/scripts/routing_lib.py's CLASS_BY_HEAD, the ledger's own classing of a call), not an
    arbitrary "role" string: a class, not an individual role, is what actually carries a model
    tier in the live table (`.claude/routing.json` merged over `rules/routing-default.json`;
    every role in a class shares that class's tier). The lab fixture's placeholder table keyed
    per-role because its placeholder schema had no live analog for "the tier-carrying unit";
    this port uses the one the live table actually has. Every "role" name and dict key below
    (`candidate["class"]`, `table["classes"]`) reflects that.
  * LEDGER ROW SHAPE: the real agent-route/v1 row nests the served tier under
    `observed.tier` (never a bare top-level `tier`), carries no `seq` field at all (the
    ledger is an append-only, dedup-on-(wf,agent) file, so file order already IS temporal
    order), and never yet writes a `kind: "matched-pair"` row (that pairing mechanism does not
    exist in this plugin yet). This port reads the real shape: `_row_tier` reads
    `observed.tier`, `load_ledger` assigns each kept row a 1-based `seq` from its file
    position, and `rollback_check`'s matched-pair branch is live-safe-but-dormant: with no
    matched-pair rows it can only ever raise a `rollback-advisory` covariate from the
    observational stream, never apply an actual rollback, until a future lane ships that
    pairing. That degrades gracefully -- see the same doc section.
  * GRADER-STABILITY was `{"refute": ["build", ...]}` (role -> role) in the fixture; every
    such pairing in this repository's own `rules/routing-default.json` `grades_edges` sits
    entirely between the `adversarial` class (the graders) and the `execute` class (the
    graded), so this port states the SAME relationship at class granularity:
    `rule["grades"] = {"adversarial": ["execute"]}` -- a restatement, not a new policy.
  * PRICES: `rules/model-prices.json` here is a list of `{"prefix": "claude-<tier>", "in",
    "out", ...}` rows (dollars per million tokens directly, prefix-matched against the
    observed model id -- this plugin's own `routing_lib.cost_usd` shape), not the fixture's
    tier-keyed placeholder dict. `prices_by_tier` adapts the live list into the tier-keyed
    dict `saving()` already expects (`saving()` itself is unchanged). Under the live prices,
    `opus` and `fable` are priced identically (both 15/75 per million); a one-step-down
    candidate from `fable` to `opus` therefore always measures a saving of exactly 0.0 and
    never opens (needs > `--budget-usd`) -- disclosed, not a defect: `tier_order` stays the
    fixed canonical rank `[haiku, sonnet, opus, fable]` and the rule goes quiet at a price
    plateau rather than being re-tuned to today's numbers.
  * STATE FILE is new: this repository has no prior per-class routing-derive bookkeeping
    (basis/tier/since_row/license_id/retest_by/bank), so `--table` names a small new JSON
    file this script owns (suggested path `ledger/routing-derive-state.json`, mirroring
    `ledger/routing-ledger.jsonl`) -- see `load_state_table`/`seed_missing_classes`. A missing
    or empty file is a normal cold start, never an error: `report` and `propose` read it,
    seed any class the ledger has seen but the state file has not (basis `prior`, tier = that
    class's CURRENT live model from `.claude/routing.json` merged over
    `rules/routing-default.json`) purely in memory, and never write it back -- only
    `apply-rollbacks` and `default-bump` persist a table, and only for an EXISTING file the
    caller names explicitly.

Reads every stream ONLY through scripts/stopping-rule.py (the frozen policy
rules/lineage-sprt.json, pinned by sha in this rule's own `lineage_policy` field) -- no second
statistic enters this script.

Stdlib only. python3 -B, no bytecode. Deterministic: sums in sorted key order, no clock, no
random source.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT_DEFAULT = os.path.dirname(HERE)  # .../scripts/.. == the plugin root
sys.path.insert(0, os.path.join(PLUGIN_ROOT_DEFAULT, "hooks", "scripts"))
import routing_lib  # noqa: E402  (unmodified: the shipped baseline library)

DERIVE_SCHEMA = "agent-route-derive/v1"
STATE_SCHEMA = "routing-derive-state/v1"

# R0-style freeze (the mechanism `rules/lineage-sprt.json` already uses via
# scripts/lineage-stopping.py's FROZEN_REF_REL/FROZEN_REF_SHA256 constants): a sibling byte
# copy at rules/frozen/routing-derive.json plus this hardcoded sha. A rule edit that does not
# also refresh both is refused, never silently read.
FROZEN_REF_REL = "rules/frozen/routing-derive.json"
FROZEN_REF_SHA256 = "4fc896c20f4722e124c2656a05aa786fffef23efdf7cc043fe3f45a5220fcc0d"


def _plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT", PLUGIN_ROOT_DEFAULT)


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canon_pretty(obj):
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def die(code, word, detail=""):
    sys.stderr.write("%s%s\n" % (word, (": " + detail) if detail else ""))
    sys.exit(code)


def load_rule(path):
    """Loads rules/routing-derive.json and refuses (exit 13, rule-tampered) unless its bytes
    match the frozen reference copy this script pins by sha -- the same tamper check
    scripts/lineage-stopping.py runs on rules/lineage-sprt.json against
    rules/frozen/lineage-sprt.json."""
    raw = open(path, "rb").read()
    rule = json.loads(raw.decode("utf-8"))
    ref_path = os.path.join(_plugin_root(), *FROZEN_REF_REL.split("/"))
    if os.path.isfile(ref_path):
        ref_bytes = open(ref_path, "rb").read()
        if sha256_bytes(ref_bytes) != FROZEN_REF_SHA256:
            die(13, "rule-tampered", "%s does not hash to this script's pinned sha" % ref_path)
        if ref_bytes != raw:
            die(13, "rule-tampered", "%s is not a byte copy of %s" % (path, ref_path))
    return rule


# ---------------------------------------------------------------------- ledger reading
def load_ledger(path):
    """-> list of row dicts, in file order, each carrying a synthesized 1-based `seq` (the
    real agent-route/v1 row has no `seq` field; the ledger is append-only and dedup'd on
    (wf, agent), so file order already IS temporal order -- pre-mortem item (ix) equivalent).
    Raises ValueError on a malformed input (duplicate (wf, agent) key whose two copies
    differ)."""
    rows = []
    seen = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise ValueError("line %d: not JSON: %s" % (lineno, e))
            if not isinstance(row, dict) or "kind" not in row:
                raise ValueError("line %d: not an object with a 'kind' field" % lineno)
            if row.get("kind") not in ("agent-route", "matched-pair"):
                continue  # a `derive` row on the ledger is excluded from the prefix, never read
            key = (row.get("wf"), row.get("agent"))
            if row.get("kind") == "agent-route" and key != (None, None):
                prior = seen.get(key)
                cur_bytes = canon(row)
                if prior is not None:
                    if prior == cur_bytes:
                        continue  # byte-identical duplicate: collapse silently
                    raise ValueError("line %d: duplicate (wf, agent) key %r with differing bytes" % (lineno, key))
                seen[key] = cur_bytes
            row["seq"] = len(rows) + 1
            rows.append(row)
    return rows


def ledger_prefix_sha256(rows):
    """sha256 over the agent-route rows only, canonical bytes, newline-joined, in seq order."""
    ar = [r for r in rows if r.get("kind") == "agent-route"]
    blob = "\n".join(canon(r) for r in ar).encode("utf-8")
    return sha256_bytes(blob)


def _row_tier(r):
    return (r.get("observed") or {}).get("tier") or r.get("tier")


def _row_outcome(row):
    """Maps a ledger row's `outcome` field to 'pass', 'fail', or None (ungraded). The real
    agent-route/v1 row's `outcome` is the object {schema_valid, verdict, refuted} written by
    hooks/scripts/routing_lib.py's outcome_from_result, or None when the workflow never
    resolved an outcome pointer -- never the bare 'pass'/'fail' string this loop graded
    before this fix. Mapping (stated rule, never guessed):
      - outcome already the string 'pass' or 'fail' -> itself (back-compat with literal rows)
      - outcome not a dict and not one of those strings -> None (ungraded)
      - outcome is a dict:
          schema_valid is False -> 'fail' (the agent's own structured result never validated)
          refuted is True       -> 'fail' (an explicit refutation)
          schema_valid is True and refuted is False -> 'pass'
          anything else (no refuted signal, verdict-only, etc.) -> None (ungraded)
    """
    outcome = row.get("outcome")
    if outcome in ("pass", "fail"):
        return outcome
    if not isinstance(outcome, dict):
        return None
    if outcome.get("schema_valid") is False:
        return "fail"
    if outcome.get("refuted") is True:
        return "fail"
    if outcome.get("schema_valid") is True and outcome.get("refuted") is False:
        return "pass"
    return None


def rows_for_class(rows, cls, kind="agent-route", void_ok=False):
    out = []
    for r in rows:
        if r.get("kind") != kind:
            continue
        if r.get("class") != cls:
            continue
        if not void_ok and r.get("void"):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------- live table (for seeding only)
def _effective_classes_table(root, plugin_root):
    """The SAME merge the guard and the ledger writer already use
    (routing_lib.merge_table over rules/routing-default.json + <root>/.claude/routing.json).
    Read-only, used only to seed a class this state file has not seen yet; never re-derives a
    second notion of the effective table."""
    default_path = os.path.join(plugin_root, "rules", "routing-default.json")
    override_path = os.path.join(root, ".claude", "routing.json")
    default_obj = routing_lib.read_json(default_path) or {}
    override_obj = routing_lib.read_json(override_path)
    if not isinstance(override_obj, dict):
        override_obj = {}
    table = routing_lib.merge_table(default_obj, override_obj)
    return table.get("classes", {}), table.get("default_sha")


def load_state_table(path):
    if path and os.path.isfile(path):
        doc = json.load(open(path, encoding="utf-8"))
        doc.setdefault("classes", {})
        doc.setdefault("bank", {})
        return doc
    return {"schema": STATE_SCHEMA, "classes": {}, "bank": {}, "default_sha": None}


def seed_missing_classes(table, rows, root, plugin_root):
    """-> a COPY of `table` with one entry per class the ledger has seen that `table` does
    not carry yet, basis `prior`, tier = that class's CURRENT live model. Never written back
    by report/propose -- only apply-rollbacks/default-bump persist a table, and only the file
    the caller already named."""
    seen = sorted(set(r.get("class") for r in rows
                       if r.get("kind") == "agent-route" and r.get("class")))
    live_classes, live_default_sha = _effective_classes_table(root, plugin_root)
    out = json.loads(canon(table))
    out.setdefault("classes", {})
    if out.get("default_sha") is None:
        out["default_sha"] = live_default_sha
    for cls in seen:
        if cls in out["classes"]:
            continue
        live = live_classes.get(cls, {})
        out["classes"][cls] = {
            "basis": "prior", "tier": live.get("model"), "since_row": 0,
            "reason": "auto-seeded from the live routing table's %s class" % cls,
        }
    return out


# ---------------------------------------------------------------------- stream reading (frozen instrument only)
def stopping_rule_bin():
    env = os.environ.get("ROUTING_DERIVE_STOPPING_RULE", "")
    if env and os.path.isfile(env):
        return env
    c = os.path.join(_plugin_root(), "scripts", "stopping-rule.py")
    if os.path.isfile(c):
        return c
    die(3, "stopping-rule-missing", "set $ROUTING_DERIVE_STOPPING_RULE to scripts/stopping-rule.py")


_STOPPING_RULE_MODULE = None


def _stopping_rule_module():
    global _STOPPING_RULE_MODULE
    if _STOPPING_RULE_MODULE is None:
        import importlib.util
        path = stopping_rule_bin()
        spec = importlib.util.spec_from_file_location("_stopping_rule_impl", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _STOPPING_RULE_MODULE = mod
    return _STOPPING_RULE_MODULE


_FROZEN_CACHE = {}


def _frozen_rule_for(lineage_policy_path, workdir):
    cached = _FROZEN_CACHE.get(lineage_policy_path)
    if cached is not None:
        return cached
    mod = _stopping_rule_module()
    frozen_copy = os.path.join(workdir, "frozen-rule.json")
    if not os.path.isfile(frozen_copy):
        mod.freeze(lineage_policy_path, frozen_copy)
    try:
        rule, sha = mod.load_frozen(frozen_copy)
    except SystemExit:
        # The cached copy exists but fails load_frozen (corrupt/tampered on disk, not the
        # source rule). Re-freeze it from the source rule -- mod.freeze() writes atomically
        # via tmp+rename -- and retry once. A second failure means the SOURCE rule itself is
        # bad and propagates (caught fail-open by the cadence hook's own main(), edit 3).
        mod.freeze(lineage_policy_path, frozen_copy)
        rule, sha = mod.load_frozen(frozen_copy)
    _FROZEN_CACHE[lineage_policy_path] = (rule, sha)
    return rule, sha


def read_stream(outcome_rows, lineage_policy_path, workdir):
    """outcome_rows: list of 'pass'/'fail' outcome strings, in order. -> a stopping-rule
    state string, via scripts/stopping-rule.py's own evaluate_stream (in-process; see the
    lab fixture's docstring on why in-process is still "reading the stream only through
    scripts/stopping-rule.py")."""
    if not outcome_rows:
        return "evidence-insufficient n=0"
    mod = _stopping_rule_module()
    rule, sha = _frozen_rule_for(lineage_policy_path, workdir)
    rows = [{"look": i, "class": "stream", "refusal": 1 if outcome == "fail" else 0}
            for i, outcome in enumerate(outcome_rows, 1)]
    lines = mod.evaluate_stream(rows, rule, sha, "stream")
    if not lines:
        return "evidence-insufficient n=%d" % len(outcome_rows)
    return lines[-1].get("state", "evidence-insufficient n=%d" % len(outcome_rows))


def _resolve_lineage_policy(rule):
    lp = rule.get("lineage_policy", {})
    path = lp.get("path", "rules/lineage-sprt.json")
    env = os.environ.get("ROUTING_DERIVE_LINEAGE_POLICY", "")
    candidates = [env] if env else []
    candidates += [path, os.path.join(_plugin_root(), path)]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            actual_sha = sha256_bytes(open(candidate, "rb").read())
            if actual_sha != lp.get("sha256"):
                die(13, "lineage-policy-tampered", "%s sha %s != rule's pinned %s" % (candidate, actual_sha, lp.get("sha256")))
            return candidate
    die(12, "lineage-policy-missing", path)


# ---------------------------------------------------------------------- report
def build_report(rows, table, rule, workdir):
    classes = sorted(set(r.get("class") for r in rows if r.get("kind") == "agent-route" and not r.get("void")))
    lines = ["# routing-report", ""]
    per_class = {}
    lineage_policy_path = _resolve_lineage_policy(rule)
    for cls in classes:
        cls_rows = rows_for_class(rows, cls)
        tiers = sorted(set(_row_tier(r) for r in cls_rows if _row_tier(r)))
        n = len(cls_rows)
        cost = sum(float(r.get("cost_usd") or 0.0) for r in cls_rows)
        wall = sum(float(r.get("wall_s", 0.0) or 0.0) for r in cls_rows)
        tok_in = sum(int((r.get("tokens") or {}).get("in", 0) or 0) for r in cls_rows)
        tok_out = sum(int((r.get("tokens") or {}).get("out", 0) or 0) for r in cls_rows)
        passes = sum(1 for r in cls_rows if _row_outcome(r) == "pass")
        graded = sum(1 for r in cls_rows if _row_outcome(r) in ("pass", "fail"))
        pass_share = (passes / graded) if graded else None
        outcomes = [_row_outcome(r) for r in cls_rows if _row_outcome(r) in ("pass", "fail")]
        state = read_stream(outcomes, lineage_policy_path, workdir)
        per_class[cls] = {"n": n, "cost_usd": round(cost, 6), "wall_s": round(wall, 3),
                           "tokens_in": tok_in, "tokens_out": tok_out,
                           "pass_share": (round(pass_share, 4) if pass_share is not None else None),
                           "tiers": tiers, "stream_state": state}
        lines.append("## %s" % cls)
        lines.append("- n=%d cost_usd=%.6f wall_s=%.3f tokens_in=%d tokens_out=%d pass_share=%s tiers=%s" % (
            n, cost, wall, tok_in, tok_out, ("%.4f" % pass_share if pass_share is not None else "n/a"), ",".join(tiers)))
        lines.append("- stream_state: %s" % state)
        lines.append("")
    lines.append("## hull")
    for cls in classes:
        by_tier = {}
        for r in rows_for_class(rows, cls):
            t = _row_tier(r)
            by_tier.setdefault(t, {"cost": 0.0, "pass": 0, "graded": 0})
            by_tier[t]["cost"] += float(r.get("cost_usd") or 0.0)
            if _row_outcome(r) in ("pass", "fail"):
                by_tier[t]["graded"] += 1
                if _row_outcome(r) == "pass":
                    by_tier[t]["pass"] += 1
        points = []
        for t in sorted(by_tier):
            d = by_tier[t]
            share = (d["pass"] / d["graded"]) if d["graded"] else 0.0
            points.append((d["cost"], share, t))
        points.sort()
        hull = []
        best_share = -1.0
        for cost_v, share, t in points:
            if share >= best_share:
                hull.append(t)
                best_share = share
        lines.append("- %s: hull=%s" % (cls, ",".join(hull)))
    lines.append("")
    return "\n".join(lines) + "\n", per_class


# ---------------------------------------------------------------------- candidate proposal
def saving(rows_since, from_tier, to_tier, prices):
    def price(tier, key):
        return prices.get(tier, prices.get("unknown", {})).get(key, 0)
    total = 0.0
    for r in rows_since:
        tin = (r.get("tokens") or {}).get("in", 0) or 0
        tout = (r.get("tokens") or {}).get("out", 0) or 0
        total += tin * (price(from_tier, "in") - price(to_tier, "in")) / 1e6
        total += tout * (price(from_tier, "out") - price(to_tier, "out")) / 1e6
    return total


def prices_by_tier(prices_doc, tier_order):
    """Adapts rules/model-prices.json's live shape (a list of
    {"prefix": "claude-<tier>", "in", "out", ...} rows, dollars per million tokens directly)
    into the tier-keyed {"in", "out"} dict `saving()` reads. One exact-prefix match per tier;
    a tier with no matching row is simply absent (saving()'s price() then reads 0 via its own
    "unknown" fallback -- never raises, never guesses a price)."""
    entries = (prices_doc or {}).get("prices") or []
    out = {}
    for t in tier_order:
        pfx = "claude-%s" % t
        matches = [e for e in entries if isinstance(e, dict) and e.get("prefix") == pfx]
        if matches:
            out[t] = {"in": matches[0].get("in", 0), "out": matches[0].get("out", 0)}
    return out


def next_cheaper_tier(tier_order, current, step=1):
    """One-step law (rule invariant: `one_step: true`, `candidate_step`): a candidate never
    proposes more than `step` tier(s) down."""
    if current not in tier_order:
        return None
    idx = tier_order.index(current)
    dest = idx - step
    return tier_order[dest] if dest >= 0 else None


def open_lineage_classes(table):
    return set(c for c, row in table.get("classes", {}).items() if row.get("open_lineage"))


def propose_candidate(rows, table, rule, prices, workdir, budget_usd):
    """-> candidate dict {"class", "from", "to", "saving_usd", "basis_was"} or None. At most
    one: the (class, to) with the largest declared saving, subject to every gating clause
    below. Deterministic tie-break: sorted class name."""
    lineage_policy_path = _resolve_lineage_policy(rule)
    tier_order = rule.get("tier_order", ["haiku", "sonnet", "opus", "fable"])
    grades = rule.get("grades", {})
    class_asym = set(rule.get("class_asymmetry", []))
    licenses = set(rule.get("think_drop_licenses", []))
    candidate_step = int(rule.get("candidate_step", 1))
    open_classes = open_lineage_classes(table)
    best = None
    for cls in sorted(table.get("classes", {})):
        row = table["classes"][cls]
        if row.get("basis") == "pin":
            continue  # pins never move
        if cls in grades:  # grader-stability: a grading class never opens while a graded class has an open lineage
            if any(g in open_classes for g in grades.get(cls, [])):
                continue
        current_tier = row.get("tier")
        to_tier = next_cheaper_tier(tier_order, current_tier, candidate_step)
        if to_tier is None:
            continue
        bank_key = "%s|%s" % (cls, to_tier)
        if bank_key in table.get("bank", {}):
            continue  # the pair is already banked -- do not reopen it
        cls_rows = rows_for_class(rows, cls)
        since = row.get("since_row", 0)
        window = [r for r in cls_rows if r.get("seq", 0) >= since]
        outcomes = [_row_outcome(r) for r in window if _row_outcome(r) in ("pass", "fail")]
        state = read_stream(outcomes, lineage_policy_path, workdir)
        if state != "evidence-sufficient promote":
            continue
        if cls in class_asym:
            lic = row.get("license_id")
            if lic is None or lic not in licenses:
                continue  # class asymmetry: no candidate on think/adversarial without a license
        sv = saving(window, current_tier, to_tier, prices)
        if sv <= budget_usd:
            continue
        cand = {"class": cls, "from": current_tier, "to": to_tier, "saving_usd": round(sv, 6),
                "basis_was": row.get("basis")}
        if best is None or cand["saving_usd"] > best["saving_usd"] or \
                (cand["saving_usd"] == best["saving_usd"] and cls < best["class"]):
            best = cand
    return best


def candidate_spec_body(candidate, template_text):
    body = template_text
    body = body.replace("{{CLASS}}", candidate["class"])
    body = body.replace("{{FROM_TIER}}", candidate["from"])
    body = body.replace("{{TO_TIER}}", candidate["to"])
    body = body.replace("{{SAVING_USD}}", "%.6f" % candidate["saving_usd"])
    return body


# ---------------------------------------------------------------------- rollback
def rollback_check(rows, table, rule, workdir):
    """-> list of actions: {"kind": "rollback", ...} (never applied by this loop today -- see
    the module docstring: no matched-pair row exists yet, so this branch is live-safe-but-
    dormant) or {"kind": "routing-ceiling-advisory"|"rollback-advisory", ...}."""
    lineage_policy_path = _resolve_lineage_policy(rule)
    tier_order = rule.get("tier_order", ["haiku", "sonnet", "opus", "fable"])
    rollback_step = int(rule.get("rollback_step", 1))
    actions = []
    for cls in sorted(table.get("classes", {})):
        row = table["classes"][cls]
        if row.get("basis") != "evidence":
            continue  # rollback applies to evidence-basis rows only
        current_tier = row.get("tier")
        pair_rows = [r for r in rows_for_class(rows, cls, kind="matched-pair")
                     if r.get("on_tier") == current_tier]
        pair_outcomes = [_row_outcome(r) for r in pair_rows if _row_outcome(r) in ("pass", "fail")]
        idx = tier_order.index(current_tier) if current_tier in tier_order else -1
        is_top = idx == len(tier_order) - 1
        if pair_outcomes:
            state = read_stream(pair_outcomes, lineage_policy_path, workdir)
            if state == "evidence-sufficient hold":
                if is_top:
                    actions.append({"kind": "routing-ceiling-advisory", "class": cls})
                else:
                    dest = min(idx + rollback_step, len(tier_order) - 1)
                    to_tier = tier_order[dest]
                    actions.append({"kind": "rollback", "class": cls, "from": current_tier, "to": to_tier,
                                     "pair_ids": sorted(set(r.get("pair_id") for r in pair_rows))})
        elif not is_top:
            obs_outcomes = [_row_outcome(r) for r in rows_for_class(rows, cls)
                             if _row_outcome(r) in ("pass", "fail")]
            if obs_outcomes:
                obs_state = read_stream(obs_outcomes, lineage_policy_path, workdir)
                if obs_state == "evidence-sufficient hold":
                    actions.append({"kind": "rollback-advisory", "class": cls, "basis": "observational-only"})
    return actions


def pin_advisories(table):
    out = []
    for cls, row in sorted(table.get("classes", {}).items()):
        if row.get("basis") == "pin" and row.get("retest_by"):
            if row.get("retest_by") < table.get("as_of", "9999-99-99"):
                out.append({"kind": "expired-pin-advisory", "class": cls, "retest_by": row["retest_by"]})
    return out


def apply_default_bump(table, new_default_sha):
    new_table = json.loads(canon(table))
    old_default = table.get("default_sha")
    for cls, row in new_table.get("classes", {}).items():
        if row.get("basis") == "prior":
            row["reason"] = "prior from routing-default %s" % (new_default_sha or "")[:12]
            row["reseeded_from"] = old_default
    kept_bank = {}
    for key, bank in new_table.get("bank", {}).items():
        if bank.get("expires_when_default_sha_not") == new_default_sha:
            kept_bank[key] = bank
    new_table["bank"] = kept_bank
    new_table["default_sha"] = new_default_sha
    return new_table


# ---------------------------------------------------------------------- CLI verbs
def _load_table_seeded(args, rows):
    table = load_state_table(args.table)
    root = getattr(args, "root", None) or os.getcwd()
    return seed_missing_classes(table, rows, root, _plugin_root())


def cmd_report(args):
    rows = load_ledger(args.ledger)
    table = _load_table_seeded(args, rows)
    rule = load_rule(args.rule)
    workdir = args.workdir or os.path.dirname(os.path.abspath(args.out))
    text, per_class = build_report(rows, table, rule, workdir)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    if args.rows_out:
        with open(args.rows_out, "w", encoding="utf-8") as fh:
            fh.write(canon_pretty(per_class))
    return 0


def cmd_propose(args):
    rows = load_ledger(args.ledger)
    table = _load_table_seeded(args, rows)
    rule = load_rule(args.rule)
    prices_doc = json.load(open(args.prices)) if args.prices else {}
    prices = prices_by_tier(prices_doc, rule.get("tier_order", ["haiku", "sonnet", "opus", "fable"]))
    workdir = args.workdir or args.out_dir
    os.makedirs(args.out_dir, exist_ok=True)
    cand = propose_candidate(rows, table, rule, prices, workdir, args.budget_usd)
    result = {"candidate": cand}
    with open(os.path.join(args.out_dir, "candidate.json"), "w", encoding="utf-8") as fh:
        fh.write(canon_pretty(result))
    if cand and args.template:
        template_text = open(args.template, encoding="utf-8").read()
        body = candidate_spec_body(cand, template_text)
        with open(os.path.join(args.out_dir, "candidate-spec.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
    rb = rollback_check(rows, table, rule, workdir)
    pins = pin_advisories(table)
    with open(os.path.join(args.out_dir, "actions.json"), "w", encoding="utf-8") as fh:
        fh.write(canon_pretty({"rollbacks": rb, "pin_advisories": pins}))
    return 0


def cmd_apply_rollbacks(args):
    rows = load_ledger(args.ledger)
    table = _load_table_seeded(args, rows)
    rule = load_rule(args.rule)
    workdir = args.workdir or os.path.dirname(os.path.abspath(args.out_table))
    actions = rollback_check(rows, table, rule, workdir)
    applied = [a for a in actions if a["kind"] == "rollback"]
    new_table = json.loads(canon(table))
    derive_row = {"kind": "derive", "schema": DERIVE_SCHEMA, "rollbacks_applied": [],
                  "ledger_prefix_sha256": ledger_prefix_sha256(rows)}
    for a in applied:
        cls = a["class"]
        new_table["classes"][cls]["tier"] = a["to"]
        bank_key = "%s|%s" % (cls, a["to"])
        new_table.setdefault("bank", {})[bank_key] = {
            "pair_stream_ids": a["pair_ids"],
            "expires_when_default_sha_not": table.get("default_sha"),
        }
        derive_row["rollbacks_applied"].append({"class": cls, "to": a["to"]})
    with open(args.out_table, "w", encoding="utf-8") as fh:
        fh.write(canon_pretty(new_table))
    with open(args.out_derive_row, "w", encoding="utf-8") as fh:
        fh.write(canon(derive_row) + "\n")
    return 0


def cmd_default_bump(args):
    table = load_state_table(args.table)
    new_table = apply_default_bump(table, args.new_default_sha)
    with open(args.out_table, "w", encoding="utf-8") as fh:
        fh.write(canon_pretty(new_table))
    return 0


def cmd_check(args):
    rows = load_ledger(args.ledger)
    table = _load_table_seeded(args, rows)
    rule = load_rule(args.rule)
    workdir = args.workdir or (os.path.dirname(os.path.abspath(args.table)) if os.path.isfile(args.table) else os.getcwd())
    actions = rollback_check(rows, table, rule, workdir)
    applied = sorted(a["class"] + "->" + a["to"] for a in actions if a["kind"] == "rollback")
    if applied:
        print("drift: rollback(s) not yet applied to committed table: %s" % ",".join(applied))
        return 1
    print("no drift")
    return 0


def cmd_selftest(args):
    ok = upper_bound_selftest()
    if args.json:
        print(canon({"selftest": "routing-derive", "ok": ok}))
    else:
        print("selftest %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def upper_bound_selftest():
    try:
        prices = {"fable": {"in": 10, "out": 50}, "sonnet": {"in": 2, "out": 10}}
        window = [{"tokens": {"in": 1000000, "out": 1000000}, "seq": 1}]
        sv = saving(window, "fable", "sonnet", prices)
        return abs(sv - 48.0) < 1e-9
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="routing-derive.py")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--table", default=None)
    ap.add_argument("--rule", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--workdir", default=None)
    sub = ap.add_subparsers(dest="verb")

    r = sub.add_parser("report")
    r.add_argument("--ledger", required=True)
    r.add_argument("--table", required=True)
    r.add_argument("--rule", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--rows-out", default=None)
    r.add_argument("--root", default=None)
    r.add_argument("--workdir", default=None)

    p = sub.add_parser("propose")
    p.add_argument("--ledger", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--rule", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--template", default=None)
    p.add_argument("--prices", default=None)
    p.add_argument("--budget-usd", type=float, default=1.3)
    p.add_argument("--root", default=None)
    p.add_argument("--workdir", default=None)

    ar = sub.add_parser("apply-rollbacks")
    ar.add_argument("--ledger", required=True)
    ar.add_argument("--table", required=True)
    ar.add_argument("--rule", required=True)
    ar.add_argument("--out-table", required=True)
    ar.add_argument("--out-derive-row", required=True)
    ar.add_argument("--root", default=None)
    ar.add_argument("--workdir", default=None)

    ck = sub.add_parser("check-table")
    ck.add_argument("--ledger", required=True)
    ck.add_argument("--table", required=True)
    ck.add_argument("--rule", required=True)
    ck.add_argument("--root", default=None)
    ck.add_argument("--workdir", default=None)

    db = sub.add_parser("default-bump")
    db.add_argument("--table", required=True)
    db.add_argument("--new-default-sha", required=True)
    db.add_argument("--out-table", required=True)

    args = ap.parse_args(argv)
    if args.selftest:
        return cmd_selftest(args)
    if args.check and args.verb is None:
        if not (args.ledger and args.table and args.rule):
            ap.error("--check requires --ledger, --table and --rule")
        try:
            return cmd_check(args)
        except ValueError as e:
            sys.stderr.write("ledger-malformed: %s\n" % e)
            return 2
    dispatch = {"report": cmd_report, "propose": cmd_propose,
                "apply-rollbacks": cmd_apply_rollbacks, "check-table": cmd_check,
                "default-bump": cmd_default_bump}
    fn = dispatch.get(args.verb)
    if fn is None:
        ap.print_usage(sys.stderr)
        return 2
    try:
        return fn(args)
    except ValueError as e:
        sys.stderr.write("ledger-malformed: %s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
