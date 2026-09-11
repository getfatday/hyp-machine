#!/usr/bin/env python3
"""lineage-stopping.py -- the lineage stopping rule (R0-R4): a lineage stops on the kept sequential
instrument or on its spend budget, never on a launch count.

PROVENANCE -- port of the lab keep H-DRAFT-5810517d-verdict-lineage-stopping (cause-n-effect, kept
2026-09-11, fragment 0490). The frozen SPRT policy `{"kind": "sprt", "alpha": 0.05, "beta": 0.1,
"p0": 0.5, "p1": 0.1, "max_looks": 13}` (rules/lineage-sprt.json; sha256 of those bytes terminated by
a newline: 8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1) is read over a lineage's
POOLED counted looks through the kept, unchanged `scripts/stopping-rule.py`; typed voids re-take a look
and never burn one; a spend budget derived from the spec's Budget-per-run line is the only non-evidence
exit. Ported by anchor from the sealed fixture: the R2 typing rule, its six output classes and the walk
(`fixture/lineage.py`, sha 919c89f5..., every constant and all 33 selftest cases byte-for-byte); the R0
header, R1 refusal, R2/R3 bookkeeping and the pending-look settle (`fixture/build.py` init_lineage /
spend_state / r1_check / lineage_bookkeeping / lineage_evaluate / cmd_settle_look); the in-progress
prefix invariants (`fixture/sprt_check.py`); Wald's truncation derivation (`fixture/key.py`). Evidence:
on 14,000 seeded lineages the rule promoted 42 / 38 / 25 of 1000 coin-flip mechanisms (bound alpha 0.05;
the template rule with its 4-launch cap 484), closed 0 on bookkeeping and 0 over budget, and the lane's own
lineage read `evidence-sufficient promote` at look 5 with 17,646.4 s of its 23,400 s unspent.

The rule as a decision procedure (spec Method R0-R4):
  R0  Freeze once at the lineage root: the policy bytes -> <lineage>/frozen/rule.json by the instrument's
      `--freeze` (byte-identical to rules/frozen/lineage-sprt.json, refused otherwise); open stream.jsonl,
      looks.jsonl, spend.jsonl; derive the spend budget = the spec's Budget-per-run caps (wall s, US$) x the
      frozen rule's truncation length (recomputed from the four policy numbers; refused unless it equals
      max_looks) and record both numbers with their derivation in spend.jsonl's header row.
  R1  Before every launch: refuse (exit 3, `budget-exhausted`, a row in refusals.jsonl) when the cumulative
      spend plus one per-run cap would exceed either budget component; also refuse while a look is pending
      its root cause and once the lineage has read a terminal.
  R2  After every launch: charge the run's recorded cost_usd and wall_s -- counted or void -- then type it:
      (i) VOID ambiguous: a run-validity assertion fails or is unresolved, a substantive result is
      unresolved, the record is crash-lost or budget-exceeded, or a failing substantive assertion's recorded
      root cause is harness-side; (ii) VOID annulled: a known-answer control outside its band, a treatment
      not delivered, a contested key; (iii) COUNTED look refusal 1: validity and controls hold and a failing
      substantive assertion is variable-side; (iv) VOID annulled: every failing substantive assertion is
      fixture/manifest/contract-side or instrument-side; (v) COUNTED look refusal 0: everything holds. A
      failing substantive assertion the record leaves unclassed is never typed by guess: `record` parks the
      look as PENDING until `settle` supplies a root-cause class for EVERY pending id (`--root-cause
      A#=<class>`); a `settle` that leaves an id unclassed refuses (exit 2, the look stays pending). That is
      the typing law of the lab fixture's table (cause-n-effect
      experiments/runs/H-DRAFT-5810517d-verdict-lineage-stopping/fixture/lineage.py, R2 (iv): "a failing
      assertion the record leaves unclassed ... never typed by guess"): an unclassed root cause is parked
      pending, never typed by guess -- and never defaulted to variable-side. Every (spec, run) is charged
      and typed once: `record`, `append-look` and `void` refuse a run already in looks.jsonl or voids.jsonl
      (`record` also one parked in pending.jsonl) with exit 3 `already-recorded <run>` and one refusals.jsonl
      row, so a driver retry after a partial failure appends nothing. A void appends no look and is re-taken
      at the next launch.
  R3  After every counted look: append the row to stream.jsonl, evaluate with `--evaluate --looks all`
      into state.jsonl, assert the prefix invariants on an in-progress file and run `--check` only on a
      terminated one. `evidence-sufficient promote` -> the lineage's current spec is KEPT;
      `evidence-sufficient hold` -> DISCARDED with its exclusion banked; `evidence-insufficient max-looks`
      -> closed without a verdict (indifference zone; pooled counts banked). Otherwise continue at R1.
  R4  A refine successor (new id) inherits R0's artifacts unchanged -- `init <successor> --inherit <root>`
      writes one pointer; every verb on the successor resolves to the root's frozen copy, stream, looks,
      ledger and budget. Nothing resets; no count exists to reset.

Verbs (paths resolve through <root>/.claude/hyp.json like the other plugin scripts -- runs_dir,
hypotheses_dir, ledger_file -- with the lab layout as default; <lineage> = <runs_dir>/<lane>/lineage/):
  init <lane> --budget-s S --budget-usd U [--inherit ROOT] [--policy P] [--spec-id ID]
  paths <lane>
  may-launch <lane>                              exit 0 `launch` | exit 3 `budget-exhausted` / `look-pending` / `terminal:<kind>`
  type <run-dir> [--run-validity A5,..] [--root-cause A#=<class> ...]   -> counted|ambiguous|annulled|budget-exceeded
  record <lane> <run-dir> [--run N] [--run-validity ..] [--root-cause ..]  R2 charge + type + look/void/pending, then R3
  charge <lane> --wall-s W --cost-usd C --class CLS [--run N] [--run-record P]
  append-look <lane> <refusal 0|1> [--run N] [--clause iii|v] [--run-record P]
  void <lane> --class ambiguous|annulled [--clause i|ii|iv] [--run N] [--run-record P]
  settle <lane> <run> --root-cause A#=<class> [...]   one class per pending id; an id left unclassed -> exit 2, the look stays pending
  evaluate <lane>                                R3 alone: re-evaluate the stream into state.jsonl
  state <lane>                                   -> promote|hold|insufficient|max-looks|spend-exhausted
  walk <launches.jsonl> --model A|B [--ratios r,..] [--budget-caps N] --out <stream.jsonl>
  --selftest [--into DIR]                        26 typing + 7 walk/settle cases (lineage.py) + spend / refusal / R3 / R4 / settle-refusal / already-recorded cases
Every verb takes --root <repo-root> (default: the nearest ancestor of the cwd carrying .claude/hyp.json or
.git) and --json (before or after the verb). Exit codes: 0 ok; 1 an invariant or check violated (named);
2 usage, malformed input (a settle that leaves a pending id unclassed), or a lineage not initialised; 3 refused
(R1, a pending look, a terminated lineage, an R0 byte or truncation mismatch, a run already recorded). The words
keep and discard never enter a state line (the instrument's
contract); they appear only in this script's `instruction` field, which belongs to the hypothesis loop.
Stdlib only, Python 3.9; the work ledger (ledger_file) is resolved and reported by `paths`, never written.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

# ---------------------------------------------------------------- constants carried from the sealed fixture
PLUGIN = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
INSTRUMENT_REL = os.path.join("scripts", "stopping-rule.py")
POLICY_REL = "rules/lineage-sprt.json"                      # the policy document (the bytes the spec quotes)
FROZEN_REF_REL = "rules/frozen/lineage-sprt.json"           # the frozen copy `--freeze` writes from the plugin root
POLICY_SHA256 = "8052bda9c051a90c762d3af9b84317941a5a5e9d44db5db90122a61a13aeeae1"
FROZEN_REF_SHA256 = "ce73163aeda7147edfc0d6fab1fbca9e5dac6ad4bb37ff9ca1c86cf64f85b027"
POLICY = {"kind": "sprt", "alpha": 0.05, "beta": 0.1, "p0": 0.5, "p1": 0.1, "max_looks": 13}  # documentation; the bytes are read

# R2 typing (fixture/lineage.py, byte-for-byte)
VARIABLE_SIDE = "variable-side"
FIXTURE_SIDE = "fixture/manifest/contract-side"
HARNESS_SIDE = "harness/run-validity"
INSTRUMENT_SIDE = "instrument"
ROOT_CAUSE_CLASSES = (VARIABLE_SIDE, FIXTURE_SIDE, HARNESS_SIDE, INSTRUMENT_SIDE)

BUDGET_CAPS_DEFAULT = 13  # the frozen rule's truncation length (recomputed at init; a different value refuses)
CHARGE_A = 1.0            # charge model A: every launch charges its full per-run cap
STREAM_CLASS = "lineage"

AMBIGUOUS = {"clause": "i", "type": "ambiguous", "refusal": None}
ANNULLED_II = {"clause": "ii", "type": "annulled", "refusal": None}
COUNTED_1 = {"clause": "iii", "type": "counted", "refusal": 1}
ANNULLED_IV = {"clause": "iv", "type": "annulled", "refusal": None}
COUNTED_0 = {"clause": "v", "type": "counted", "refusal": 0}
NOT_LAUNCHED = {"clause": "nl", "type": "not-launched", "refusal": None}

# R1 (fixture/build.py r1_check) and R3 (fixture/sprt_check.py prefix_invariants) tolerances
R1_TOL = 1e-9
PREFIX_TOL = 1e-9

# the run-validity labels the spec's R2 (i) names (the SUBSTANTIVE-ASSERTIONS row), for documentation
RUN_VALIDITY_LABELS = ("determinism", "containment", "isolation", "two-pass", "byte-identical", "selftest",
                       "zero writes", "reproducibility", "pin", "cost-record")
VOID_CLASSES = ("ambiguous", "annulled")

# R3 instruction text (fixture/build.py lineage_evaluate, byte-for-byte)
INSTRUCTIONS = {"promote": "KEEP -- evidence-sufficient promote",
                "hold": "DISCARD -- evidence-sufficient hold (exclusion banked)",
                "max-looks": "closed without a verdict -- evidence-insufficient max-looks (indifference zone; pooled counts banked)"}
CONTINUE = "continue at R1"
RE_TAKE = "the next launch re-takes this look while the budget allows (R2)"

# the lab lineage's recorded bytes (five refusal-0 looks over this policy) -- the selftest's parity target
LAB_STATE_SHA256 = "a56f0dcfd837b5a2fc33c8880744fb65f5d3b8e6d82d869169b09e24050f31be"
LAB_STREAM_SHA256 = "b17fd8a3ce45cb427ee6633f407e99bc348f82c3318264ca2d35b3e647300027"
LAB_LLR = [0.5877866649021191, 1.1755733298042381, 1.7633599947063572, 2.3511466596084762, 2.9389333245105953]

# consumer layout (<root>/.claude/hyp.json; the lab layout as default -- the same keys preflight-rigor.py resolves)
HYP_JSON_REL = os.path.join(".claude", "hyp.json")
LAYOUT_DEFAULTS = {"hypotheses_dir": "hypotheses", "runs_dir": "experiments/runs", "ledger_file": "ledger/ledger.jsonl"}
LINEAGE_FILES = (("frozen", "frozen/rule.json"), ("stream", "stream.jsonl"), ("looks", "looks.jsonl"), ("spend", "spend.jsonl"),
                 ("state", "state.jsonl"), ("voids", "voids.jsonl"), ("pending", "pending.jsonl"), ("refusals", "refusals.jsonl"),
                 ("inherits", "inherits.json"))
RECORD_FILES = ("grade.json", "run.json", "RUN-RECORD.json", "run-record.json", "cost-record.json", "VERDICT.json")

EXIT_OK, EXIT_VIOLATION, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3


class Refuse(Exception):
    """Nothing launched, nothing appended; exit 3 with the reason (fixture/build.py Refuse)."""


class Usage(Exception):
    """Exit 2."""


# ---------------------------------------------------------------- io
def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(path):
    with open(path, "rb") as fh:
        return sha_bytes(fh.read())


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_row(path, row):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(canon(row) + "\n")


def write_rows(path, rows):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(canon(r) + "\n")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- the consumer layout
def find_root(start=None):
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, HYP_JSON_REL)) or os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start or os.getcwd())
        cur = parent


class Layout(object):
    """<root>/.claude/hyp.json keys with lab defaults (the same resolution preflight-rigor.py uses)."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.cfg = dict(LAYOUT_DEFAULTS)
        j = read_json(os.path.join(self.root, HYP_JSON_REL))
        if isinstance(j, dict):
            for k in LAYOUT_DEFAULTS:
                v = j.get(k)
                if isinstance(v, str) and v.strip():
                    self.cfg[k] = v.strip().strip("/")

    def rel(self, key, *parts):
        return os.path.join(self.root, *(self.cfg[key].split("/") + list(parts)))

    def rundir(self, spec):
        return self.rel("runs_dir", spec)

    def lineage_dir(self, lane):
        return os.path.join(self.rundir(lane), "lineage")

    def lineage_paths(self, lane):
        d = self.lineage_dir(lane)
        return {k: os.path.join(d, *v.split("/")) for k, v in LINEAGE_FILES}

    def spec_path(self, lane):
        return self.rel("hypotheses_dir", lane + ".md")

    def runs_rel(self, path):
        try:
            return os.path.relpath(os.path.abspath(path), self.rel("runs_dir")).replace(os.sep, "/")
        except ValueError:
            return os.path.abspath(path)


def check_lane(lane):
    if not lane or "/" in lane or os.sep in lane or lane in (".", ".."):
        raise Usage("a lane is a spec id (the run directory's name under runs_dir), not a path: %r" % lane)
    return lane


def resolve_lineage(layout, lane):
    """R4: follow `inherits.json` pointers to the lineage root. -> (root_lane, paths, hops)."""
    check_lane(lane)
    hops = [lane]
    cur = lane
    while True:
        P = layout.lineage_paths(cur)
        inh = read_json(P["inherits"]) if os.path.exists(P["inherits"]) else None
        if not isinstance(inh, dict) or not inh.get("lineage_root"):
            return cur, P, hops
        nxt = check_lane(str(inh["lineage_root"]))
        if nxt in hops or len(hops) > 16:
            raise Usage("R4 inheritance cycle or chain too long: %s" % " -> ".join(hops + [nxt]))
        hops.append(nxt)
        cur = nxt


def require_header(P):
    rows = read_jsonl(P["spend"])
    header = rows[0] if rows and rows[0].get("header") else None
    if header is None or not os.path.exists(P["frozen"]):
        raise Usage("lineage not initialised at %s (run `init <lane> --budget-s S --budget-usd U`)" % os.path.dirname(P["spend"]))
    return header, rows


def frozen_doc(P):
    doc = read_json(P["frozen"])
    if not isinstance(doc, dict) or not isinstance(doc.get("rule_text"), str) or doc.get("frozen") != "stopping-rule":
        raise Usage("frozen copy unreadable: %s" % P["frozen"])
    if sha_bytes(doc["rule_text"].encode("utf-8")) != doc.get("sha256"):
        raise Refuse("frozen-rule-tampered: %s" % P["frozen"])
    return doc


def instrument_path(explicit=None):
    p = explicit or os.path.join(PLUGIN, INSTRUMENT_REL)
    if not os.path.isfile(p):
        raise Usage("the kept instrument is not at %s" % p)
    return p


def run_instrument(args, cwd=None, instrument=None):
    return subprocess.run([sys.executable, instrument_path(instrument)] + list(args), cwd=cwd, capture_output=True, text=True)


# ---------------------------------------------------------------- R2: the typing rule (fixture/lineage.py, byte-for-byte)
def _question_unclear(r):
    return r.get("control_held") is False or r.get("treatment_delivered") is False or r.get("key_contested") is True


def type_record(r):
    """R2 as a procedure. Returns a fresh dict (clause, type, refusal)."""
    if r.get("launched") is not True:
        return dict(ANNULLED_II) if _question_unclear(r) else dict(NOT_LAUNCHED)
    subst = r.get("substantive") or []
    rv = r.get("run_validity") or []
    if r.get("resolvable") is False:
        return dict(AMBIGUOUS)
    if any(a.get("pass") is False for a in rv) or any(a.get("pass") is None for a in rv):
        return dict(AMBIGUOUS)
    if any(a.get("pass") is None for a in subst):
        return dict(AMBIGUOUS)
    failing = [a for a in subst if a.get("pass") is False]
    if any(a.get("root_cause_class") == HARNESS_SIDE for a in failing):
        return dict(AMBIGUOUS)
    if _question_unclear(r):
        return dict(ANNULLED_II)
    if not failing:
        return dict(COUNTED_0)
    if any(a.get("root_cause_class") == VARIABLE_SIDE for a in failing):
        return dict(COUNTED_1)
    if any(a.get("root_cause_class") not in (FIXTURE_SIDE, INSTRUMENT_SIDE) for a in failing):
        return dict(AMBIGUOUS)
    return dict(ANNULLED_IV)


def pending_ids(rec, typed):
    """The failing substantive assertions whose ONLY missing datum is the root cause: re-typed as variable-side the run
    would be counted. `record` parks such a run as pending instead of typing it by guess (fixture/build.py)."""
    if typed["type"] != "ambiguous":
        return []
    unclassed = [a["id"] for a in rec.get("substantive") or [] if a.get("pass") is False and a.get("root_cause_class") is None]
    if not unclassed:
        return []
    trial = json.loads(json.dumps(rec))
    for a in trial["substantive"]:
        if a["id"] in unclassed:
            a["root_cause_class"] = VARIABLE_SIDE
    return unclassed if type_record(trial)["type"] == "counted" else []


# ---------------------------------------------------------------- R2: reading a run directory into the record shape
def _norm_pass(v):
    """-> True | False | None | "deferred" from the shapes shipped graders write (bool, {pass: ..}, PASS/FAIL strings)."""
    if v is True or v is False or v is None:
        return v
    if isinstance(v, dict):
        for k in ("pass", "passed", "ok"):
            if k in v:
                return _norm_pass(v[k])
        for k in ("status", "result", "verdict"):
            if k in v:
                return _norm_pass(v[k])
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("pass", "passed", "ok", "true", "yes"):
            return True
        if s in ("fail", "failed", "false", "no"):
            return False
        if s == "deferred":
            return "deferred"
        return None
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def _first(files, key, names=RECORD_FILES):
    for name in names:
        j = files.get(name)
        if isinstance(j, dict) and j.get(key) is not None:
            return j[key], name
    return None, None


def _nested(files, path_options):
    for opts in path_options:
        for name in RECORD_FILES:
            cur = files.get(name)
            ok = isinstance(cur, dict)
            for k in opts:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, bool):
                return cur
    return None


def load_run_dir(run_dir):
    """{name: parsed json | None (unparseable)} for every record file present."""
    files = {}
    for name in RECORD_FILES:
        p = os.path.join(run_dir, name)
        if os.path.isfile(p):
            files[name] = read_json(p)
    return files


def run_record_from_dir(run_dir, run_validity_ids=(), root_causes=None, run_id=None):
    """The flat record R2 types, read lane-independently from a run directory:
      assertions        grade.json (then run.json, VERDICT.json) `assertions`: {id: bool | {pass: ..} | "PASS"/"FAIL"} or a list
      run-validity ids  --run-validity, or an entry marked kind/class "run-validity" or run_validity true, or a top-level
                        `run_validity` id list (the spec's SUBSTANTIVE-ASSERTIONS declaration is the driver's source)
      root causes       --root-cause A#=<class>, else the entry's `root_cause_class` (one of ROOT_CAUSE_CLASSES)
      control_held      `control_held` | known_answer_control.held | control.held; a grader-named `void_triggers.annulled`
                        entry or `void_class: annulled` reads as the control failing (the question unclear)
      treatment         manipulation_check.treatment_delivered | treatment_delivered | treatment.delivered
      key_contested     `key_contested`
      resolvable        false when no assertions can be read, a record file is unparseable, the record is crash-lost or
                        budget-exceeded, or the grader typed the void `ambiguous` (void_class / void_triggers / class)
      charge            cost_usd, wall_s, cap_s from the first record file carrying each
    -> (record, meta) where meta carries budget_exceeded, grader_void_class, files_read, unparseable."""
    root_causes = dict(root_causes or {})
    files = load_run_dir(run_dir)
    unparseable = sorted(n for n, j in files.items() if j is None)
    items, src = [], None
    for name in ("grade.json", "run.json", "VERDICT.json"):
        j = files.get(name)
        a = j.get("assertions") if isinstance(j, dict) else None
        if isinstance(a, dict):
            items = [(str(k), v) for k, v in a.items()]
            src = name
            break
        if isinstance(a, list):
            items = [((str(v.get("id")) if isinstance(v, dict) and v.get("id") else "A%d" % i), v) for i, v in enumerate(a, 1)]
            src = name
            break
    declared_rv = set(str(x) for x in (run_validity_ids or []))
    for name in ("grade.json", "run.json"):
        j = files.get(name)
        if isinstance(j, dict) and isinstance(j.get("run_validity"), list):
            declared_rv |= set(str(x) for x in j["run_validity"] if isinstance(x, (str, int)))
    subst, rv = [], []
    for aid, v in items:
        p = _norm_pass(v)
        marked_rv = isinstance(v, dict) and (v.get("kind") == "run-validity" or v.get("class") == "run-validity" or v.get("run_validity") is True)
        rcc = root_causes.get(aid)
        if rcc is None and isinstance(v, dict):
            for k in ("root_cause_class", "root_cause"):
                if v.get(k) in ROOT_CAUSE_CLASSES:
                    rcc = v[k]
                    break
        label = v.get("label") if isinstance(v, dict) and isinstance(v.get("label"), str) else None
        if aid in declared_rv or marked_rv:
            rv.append({"id": aid, "pass": p, "label": label or "run-validity"})
        else:
            subst.append({"id": aid, "pass": p, "root_cause_class": rcc})
    # grader-typed voids and the budget terminal
    grader_void = None
    budget_exceeded = False
    for name in RECORD_FILES:
        j = files.get(name)
        if not isinstance(j, dict):
            continue
        if j.get("budget_exceeded") is True or j.get("terminal") == "budget-exceeded" or j.get("class") == "budget-exceeded":
            budget_exceeded = True
        vc = j.get("void_class")
        if vc in VOID_CLASSES and grader_void is None:
            grader_void = vc
        for key in ("class", "terminal"):
            s = j.get(key)
            if isinstance(s, str) and s.startswith("void:") and s[5:] in VOID_CLASSES and grader_void is None:
                grader_void = s[5:]
        vt = j.get("void_triggers")
        if isinstance(vt, dict):
            if vt.get("budget-exceeded"):
                budget_exceeded = True
            if vt.get("ambiguous") and grader_void is None:
                grader_void = "ambiguous"
            if vt.get("annulled") and grader_void is None:
                grader_void = "annulled"
    control = _nested(files, (("control_held",), ("known_answer_control", "held"), ("control", "held")))
    if control is None and grader_void == "annulled":
        control = False
    treatment = _nested(files, (("manipulation_check", "treatment_delivered"), ("treatment_delivered",), ("treatment", "delivered")))
    if treatment is None:
        t, _ = _first(files, "treatment")
        if isinstance(t, bool):
            treatment = t
    key_contested = _nested(files, (("key_contested",),))
    resolvable = bool(items) and not unparseable and not budget_exceeded and grader_void != "ambiguous"
    cost, _ = _first(files, "cost_usd")
    wall, _ = _first(files, "wall_s")
    cap, _ = _first(files, "cap_s")
    rec = {"id": run_id or os.path.basename(os.path.abspath(run_dir)), "launched": True, "resolvable": resolvable,
           "substantive": subst, "run_validity": rv, "control_held": control, "treatment_delivered": treatment,
           "key_contested": key_contested,
           "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
           "wall_s": float(wall) if isinstance(wall, (int, float)) else None,
           "cap_s": float(cap) if isinstance(cap, (int, float)) else None}
    meta = {"assertions_from": src, "files_read": sorted(files), "unparseable": unparseable, "budget_exceeded": budget_exceeded,
            "grader_void_class": grader_void, "run_validity_ids": sorted(a["id"] for a in rv)}
    return rec, meta


def type_run_dir(run_dir, run_validity_ids=(), root_causes=None):
    """-> (word, typed, rec, meta): word is counted|ambiguous|annulled|budget-exceeded (a budget-exceeded run is the
    `ambiguous` void the spec names -- reality unclear -- reported by its own word)."""
    if not os.path.isdir(run_dir):
        raise Usage("run directory not found: %s" % run_dir)
    rec, meta = run_record_from_dir(run_dir, run_validity_ids, root_causes)
    typed = type_record(rec)
    word = "budget-exceeded" if meta["budget_exceeded"] else typed["type"]
    return word, typed, rec, meta


# ---------------------------------------------------------------- the walk (R1 + the look/void split) and settle (fixture/lineage.py)
def charge_model_A():
    return lambda j: CHARGE_A


def charge_model_B(ratios):
    ratios = [float(x) for x in ratios]
    if not ratios:
        raise ValueError("charge model B needs at least one recorded ratio")
    return lambda j: ratios[(j - 1) % len(ratios)]


def walk(launches, charge_fn, budget_caps=BUDGET_CAPS_DEFAULT):
    """Walk the launches in order under R1 and the R2 look/void split. Every launch is charged, counted or void; the
    walk refuses (stops) at the first launch whose charge, taken as one full cap, would carry the cumulative charge
    over the budget: `cum + 1 > budget_caps`. Returns the counted stream rows, the look -> launch map, the cumulative
    charge after each launch, the void prefix counts and the refused launch index (None when the launch file ended
    before R1 refused -- a fixture defect the grader names)."""
    cum = 0.0
    rows, look_launch, charges, void_prefix = [], [], [], [0]
    refused_at = None
    for L in launches:
        j = int(L["launch"])
        if cum + 1.0 > budget_caps:
            refused_at = j
            break
        cum += charge_fn(j)
        charges.append(cum)
        if int(L["void"]):
            void_prefix.append(void_prefix[-1] + 1)
            continue
        void_prefix.append(void_prefix[-1])
        rows.append({"look": len(rows) + 1, "class": STREAM_CLASS, "refusal": 1 - int(L["pass"])})
        look_launch.append(j)
    return {"rows": rows, "look_launch": look_launch, "charges": charges, "void_prefix": void_prefix,
            "refused_at": refused_at, "n_launched": len(charges), "voids": void_prefix[-1],
            "last_launch_void": bool(launches and len(charges) and int(launches[len(charges) - 1]["void"]))}


def terminal_kind(state):
    if state == "evidence-sufficient promote":
        return "promote"
    if state == "evidence-sufficient hold":
        return "hold"
    if state.startswith("evidence-insufficient max-looks"):
        return "max-looks"
    return None


def settle_walk(w, state_lines, budget_caps=BUDGET_CAPS_DEFAULT):
    """Read the instrument's terminal back. When a state line is terminal the lineage stopped at that look's launch
    (never launched again -- R3); otherwise it closed `budget-exhausted` when R1 refused the next launch, and
    `launch-file-short` (a fixture defect, never a rule outcome) when the launch file ended first."""
    term = None
    for ln in state_lines:
        if terminal_kind(ln["state"]):
            term = ln
            break
    if term is not None:
        k = int(term["look"])
        j = w["look_launch"][k - 1]
        return {"t": terminal_kind(term["state"]), "look": k, "launch": j, "charge": w["charges"][j - 1],
                "voids": w["void_prefix"][j], "launches": j, "looks": k, "over_by_one_cap": None}
    n = w["n_launched"]
    t = "budget-exhausted" if w["refused_at"] is not None else "launch-file-short"
    cum = w["charges"][-1] if w["charges"] else 0.0
    return {"t": t, "look": None, "launch": None, "charge": cum, "voids": w["voids"], "launches": n,
            "looks": len(w["rows"]), "over_by_one_cap": (cum + 1.0 > budget_caps)}


# ---------------------------------------------------------------- the SPRT: constants, prefix invariants (sprt_check.py), truncation (key.py)
INSUFFICIENT = "evidence-insufficient n=%d/%d"


def sprt_constants(rule):
    a, b, p0, p1 = float(rule["alpha"]), float(rule["beta"]), float(rule["p0"]), float(rule["p1"])
    return {"up": math.log((1.0 - b) / a), "lo": math.log(b / (1.0 - a)),
            "inc1": math.log(p1 / p0), "inc0": math.log((1.0 - p1) / (1.0 - p0))}


def is_terminal(state):
    return state.startswith("evidence-sufficient ") or state.startswith("evidence-insufficient max-looks")


def classify(state_lines):
    """'terminated' when the last line is a terminal, else 'in-progress' (an empty file is in-progress: no look)."""
    if state_lines and is_terminal(str(state_lines[-1].get("state", ""))):
        return "terminated"
    return "in-progress"


def prefix_invariants(state_lines, rows, rule, sha, tol=PREFIX_TOL):
    """Invariants on an IN-PROGRESS state file (the instrument's --check is for terminated files only):
    line-count, looks-consecutive, rule-sha-mismatch, vocabulary, terminal-mid-file, state-text, llr-recompute (within
    tol), llr-boundary-uncrossed -> (ok, violation-name or None, detail)."""
    c = sprt_constants(rule)
    ml = int(rule["max_looks"])
    if len(state_lines) != len(rows):
        return False, "line-count", "%d lines for %d rows" % (len(state_lines), len(rows))
    llr = 0.0
    sid = state_lines[0].get("stream") if state_lines else None
    for k, (ln, row) in enumerate(zip(state_lines, rows), 1):
        text = json.dumps(ln)
        if " keep" in text or " discard" in text or '"keep' in text or '"discard' in text:
            return False, "vocabulary", "look %d" % k
        if ln.get("look") != k or ln.get("rule") != "sprt" or ln.get("stream") != sid or ln.get("n_min") != ml:
            return False, "looks-consecutive", "look %d" % k
        if ln.get("rule_sha") != sha:
            return False, "rule-sha-mismatch", "look %d" % k
        st = str(ln.get("state", ""))
        if is_terminal(st):
            return False, "terminal-mid-file", "look %d: %s" % (k, st)
        if st != INSUFFICIENT % (k, ml):
            return False, "state-text", "look %d: %s" % (k, st)
        llr += c["inc1"] if int(row["refusal"]) else c["inc0"]
        got = ln.get("llr")
        if not isinstance(got, (int, float)) or abs(float(got) - llr) > tol:
            return False, "llr-recompute", "look %d: %r vs %r" % (k, got, llr)
        if llr >= c["up"] - tol or llr <= c["lo"] + tol:
            return False, "llr-boundary-uncrossed", "look %d: llr %r at a boundary" % (k, llr)
    return True, None, "%d lines" % len(state_lines)


def _llr_of(c, n_pass, n_ref):
    return n_pass * c["inc0"] + n_ref * c["inc1"]


def term_dist(c, p, max_looks):
    """Per-look promote / hold probabilities of the SPRT over a mechanism with per-look PASS probability p, and the
    probability of no terminal by max_looks (fixture/key.py)."""
    prob = {(0, 0): 1.0}
    prom = [0.0] * (max_looks + 1)
    hold = [0.0] * (max_looks + 1)
    for k in range(1, max_looks + 1):
        nxt = {}
        for (np_, nr), pr in prob.items():
            for passed, q in ((1, p), (0, 1.0 - p)):
                if q == 0.0:
                    continue
                np2, nr2 = np_ + passed, nr + (1 - passed)
                llr = _llr_of(c, np2, nr2)
                if llr >= c["up"]:
                    prom[k] += pr * q
                elif llr <= c["lo"]:
                    hold[k] += pr * q
                else:
                    nxt[(np2, nr2)] = nxt.get((np2, nr2), 0.0) + pr * q
        prob = nxt
    return prom, hold, sum(prob.values())


def truncation(rule, upto=20):
    """Wald's truncation: the smallest n with P_p0(T > n) <= alpha and P_p1(T > n) <= beta (p0 = the null REFUSAL rate,
    so the pass rate is 1 - p0; p1 the effect refusal rate). -> (smallest_n or None, table)."""
    c = sprt_constants(rule)
    rows = []
    smallest = None
    for n in range(1, max(upto, int(rule["max_looks"]) + 3) + 1):
        _, _, nt0 = term_dist(c, 1.0 - float(rule["p0"]), n)
        _, _, nt1 = term_dist(c, 1.0 - float(rule["p1"]), n)
        ok = nt0 <= float(rule["alpha"]) and nt1 <= float(rule["beta"])
        rows.append({"n": n, "P_p0_T_gt_n": nt0, "P_p1_T_gt_n": nt1, "qualifies": ok})
        if ok and smallest is None:
            smallest = n
    return smallest, rows


# ---------------------------------------------------------------- R0
def wall_text(s):
    return "%d min" % int(round(s / 60.0)) if s >= 60 and abs(s / 60.0 - round(s / 60.0)) < 1e-9 else "%g s" % s


def cmd_init(layout, lane, budget_s, budget_usd, inherit=None, policy=None, spec_id=None, instrument=None):
    check_lane(lane)
    P = layout.lineage_paths(lane)
    if inherit:
        # R4: the successor points at the root and inherits every artifact unchanged
        root_lane, RP, hops = resolve_lineage(layout, check_lane(inherit))
        header, _ = require_header(RP)
        if os.path.exists(P["frozen"]) or os.path.exists(P["spend"]):
            raise Refuse("R4: %s already carries its own lineage files; a successor inherits, it never re-freezes" % lane)
        if os.path.exists(P["inherits"]):
            cur = read_json(P["inherits"]) or {}
            if cur.get("lineage_root") == root_lane:
                return {"initialised": False, "inherits": root_lane, "hops": hops}
            raise Refuse("R4: %s already inherits from %s" % (lane, cur.get("lineage_root")))
        doc = {"lineage_root": root_lane, "via": hops if len(hops) > 1 else None, "frozen_rule_sha256": header.get("frozen_rule_sha256"),
               "recorded": now(), "r4": "a refine successor inherits R0's artifacts unchanged -- the frozen copy, the stream, the looks, the ledger, the spend budget; nothing resets, no count exists to reset"}
        os.makedirs(os.path.dirname(P["inherits"]), exist_ok=True)
        with open(P["inherits"], "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        return {"initialised": True, "inherits": root_lane, "budget": header["budget"], "pointer": P["inherits"]}
    if os.path.exists(P["inherits"]):
        raise Refuse("R4: %s inherits from %s; a successor never re-freezes or re-opens its own ledgers" % (lane, (read_json(P["inherits"]) or {}).get("lineage_root")))
    if budget_s is None or budget_usd is None or budget_s <= 0 or budget_usd < 0:
        raise Usage("init needs --budget-s <per-run wall cap, seconds> and --budget-usd <per-run US$ cap> from the spec's Budget-per-run line")
    if os.path.exists(P["frozen"]) and os.path.exists(P["spend"]):
        header, _ = require_header(P)
        return {"initialised": False, "frozen_copy_sha256": sha_file(P["frozen"]), "budget": header["budget"]}
    # freeze: from the plugin root so the frozen copy is the reference copy byte for byte (its `source` is the relative path)
    policy_arg, cwd = (policy, None) if policy else (POLICY_REL, PLUGIN)
    policy_abs = os.path.abspath(policy) if policy else os.path.join(PLUGIN, *POLICY_REL.split("/"))
    if not os.path.isfile(policy_abs):
        raise Usage("policy document not found: %s" % policy_abs)
    with open(policy_abs, "rb") as fh:
        policy_bytes = fh.read()
    rule = json.loads(policy_bytes.decode("utf-8"))
    if rule.get("kind") != "sprt":
        raise Refuse("the lineage rule is the SPRT read over pooled counted looks; a %r policy is not the kept rule" % rule.get("kind"))
    smallest, table = truncation(rule)
    if smallest != int(rule["max_looks"]):
        raise Refuse("R0: max_looks %s is not Wald's truncation for these policy numbers (derived %s); the truncation is never chosen" % (rule["max_looks"], smallest))
    os.makedirs(os.path.dirname(P["frozen"]), exist_ok=True)
    r = run_instrument(["--freeze", policy_arg, "--into", P["frozen"]], cwd=cwd, instrument=instrument)
    if r.returncode != 0:
        raise Refuse("R0 freeze failed: %s" % (r.stderr.strip() or r.stdout.strip()))
    doc = frozen_doc(P)
    if doc["sha256"] != sha_bytes(policy_bytes):
        raise Refuse("R0: the frozen copy's inner rule bytes do not hash to the policy document")
    if not policy:
        ref = os.path.join(PLUGIN, *FROZEN_REF_REL.split("/"))
        if os.path.isfile(ref) and open(ref, "rb").read() != open(P["frozen"], "rb").read():
            raise Refuse("R0: the lineage's frozen copy is not a byte copy of %s" % FROZEN_REF_REL)
    ml = int(rule["max_looks"])
    budget = {"wall_s": float(budget_s) * ml, "usd": round(float(budget_usd) * ml, 6)}
    header = {"header": True, "lineage": lane, "frozen_rule_sha256": doc["sha256"], "frozen_copy": "lineage/frozen/rule.json",
              "per_run_cap": {"wall_s": float(budget_s), "usd": float(budget_usd)}, "truncation_length": ml, "budget": budget,
              "derivation": "R0: the spec's Budget-per-run caps (%s wall-clock, US$%.2f) x the frozen rule's truncation length (%d, recomputed at init) = %.1f wall-hours and US$%.2f across every launch of this lineage, counted or void, root and successors alike" % (
                  wall_text(float(budget_s)), float(budget_usd), ml, budget["wall_s"] / 3600.0, budget["usd"]),
              "opened": now()}
    if spec_id:
        header["spec"] = spec_id
    with open(P["spend"], "w", encoding="utf-8") as fh:
        fh.write(canon(header) + "\n")
    for k in ("stream", "looks"):
        with open(P[k], "w", encoding="utf-8"):
            pass
    out = {"initialised": True, "frozen_copy_sha256": sha_file(P["frozen"]), "frozen_rule_sha256": doc["sha256"], "budget": budget,
           "truncation_length": ml, "truncation_check": {"P_p0_T_gt_n": table[ml - 1]["P_p0_T_gt_n"], "P_p1_T_gt_n": table[ml - 1]["P_p1_T_gt_n"]},
           "lineage_dir": os.path.dirname(P["spend"])}
    spec = layout.spec_path(lane)
    if not os.path.isfile(spec):
        out["note"] = "no spec at %s (the lineage files were still opened)" % spec
    return out


# ---------------------------------------------------------------- R1
def spend_state(P):
    header, rows = require_header(P)
    cum_usd = sum(float(r.get("cost_usd") or 0.0) for r in rows[1:])
    cum_wall = sum(float(r.get("wall_s") or 0.0) for r in rows[1:])
    return header, cum_usd, cum_wall, len(rows) - 1


def open_pending(P):
    return [p for p in read_jsonl(P["pending"]) if not p.get("settled")]


def last_terminal(P):
    lines = read_jsonl(P["state"])
    return (terminal_kind(str(lines[-1].get("state", ""))) if lines else None), lines


def r1_predicate(header, cum_usd, cum_wall):
    b, cap = header["budget"], header["per_run_cap"]
    return not (cum_usd + cap["usd"] > b["usd"] + R1_TOL or cum_wall + cap["wall_s"] > b["wall_s"] + R1_TOL)


def may_launch(P, record_refusal=True):
    """-> (ok, word, detail). Words: launch | budget-exhausted | look-pending | terminal:<kind>."""
    header, cum_usd, cum_wall, n = spend_state(P)
    b, cap = header["budget"], header["per_run_cap"]
    detail = {"cum_usd": cum_usd, "cum_wall_s": round(cum_wall, 1), "launches_so_far": n, "budget": b, "per_run_cap": cap,
              "remaining": {"usd": round(b["usd"] - cum_usd, 6), "wall_s": round(b["wall_s"] - cum_wall, 1)}}
    tk, _ = last_terminal(P)
    if tk:
        detail["terminal"] = tk
        detail["reason"] = "%s -- a terminated lineage never launches again (R3)" % INSTRUCTIONS[tk]
        return False, "terminal:%s" % tk, detail
    if not r1_predicate(header, cum_usd, cum_wall):
        if record_refusal:
            append_row(P["refusals"], {"at": now(), "terminal": "budget-exhausted", "cum_usd": cum_usd, "cum_wall_s": cum_wall, "budget": b, "launches_so_far": n})
        detail["reason"] = "cumulative US$%.4f + US$%.2f or %.0f s + %.0f s would exceed the lineage budget %s (R1)" % (cum_usd, cap["usd"], cum_wall, cap["wall_s"], canon(b))
        return False, "budget-exhausted", detail
    pend = open_pending(P)
    if pend:
        detail["pending"] = [{"spec": p.get("spec"), "run": p.get("run"), "failing": p.get("failing")} for p in pend]
        detail["reason"] = "a look is pending its root-cause record (%s); settle it before launching again" % ", ".join("%s run-%s" % (p.get("spec"), p.get("run")) for p in pend)
        return False, "look-pending", detail
    return True, "launch", detail


# ---------------------------------------------------------------- R2 + R3 bookkeeping
def charge(P, spec, run_n, wall_s, cost_usd, cls, run_record):
    header, cum_usd, cum_wall, n = spend_state(P)
    wall_s = float(wall_s or 0.0)
    cost_usd = float(cost_usd or 0.0)
    row = {"spec": spec, "run": run_n, "run_record": run_record, "cost_usd": cost_usd, "wall_s": round(wall_s, 1),
           "cum_usd": round(cum_usd + cost_usd, 6), "cum_wall_s": round(cum_wall + wall_s, 1), "charged": now(), "class": cls}
    append_row(P["spend"], row)
    return row


def evaluate(P, instrument=None):
    """R3: evaluate the lineage stream against the frozen copy; prefix invariants on an in-progress file, --check at a terminal."""
    doc = frozen_doc(P)
    rows = read_jsonl(P["stream"])
    if not rows:
        write_rows(P["state"], [])
        return {"looks": 0, "state": None, "llr": None, "terminal": None, "check": {"kind": "none"}, "evaluate_rc": None, "instruction": CONTINUE}
    r = run_instrument(["--evaluate", P["stream"], "--rule", P["frozen"], "--looks", "all", "--json"], instrument=instrument)
    lines = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    write_rows(P["state"], lines)
    if r.returncode != 0 or not lines:
        return {"looks": len(rows), "state": lines[-1]["state"] if lines else None, "llr": None, "terminal": None,
                "check": {"kind": "none"}, "evaluate_rc": r.returncode, "error": (r.stderr or r.stdout).strip(), "instruction": "evaluator exited non-zero: a void of the harness, not a look"}
    rule = json.loads(doc["rule_text"])
    if classify(lines) == "terminated":
        cr = run_instrument(["--check", P["state"], "--rule", P["frozen"]], instrument=instrument)
        check = {"kind": "instrument-check", "rc": cr.returncode, "detail": (cr.stdout + cr.stderr).strip()}
    else:
        ok, inv, detail = prefix_invariants(lines, rows, rule, doc["sha256"])
        check = {"kind": "prefix-invariants", "rc": 0 if ok else 1, "invariant": inv, "detail": detail}
    tk = terminal_kind(str(lines[-1]["state"]))
    return {"looks": len(rows), "state": lines[-1]["state"], "llr": lines[-1].get("llr"), "terminal": tk, "check": check,
            "evaluate_rc": r.returncode, "instruction": INSTRUCTIONS.get(tk, CONTINUE)}


def recorded_in(P, spec, run_n, include_pending=False):
    """The lineage file a (spec, run) already occupies -- looks | voids | pending -- or None. A run with no number
    (run_n None) cannot be identified and is never guarded."""
    if run_n is None:
        return None
    key = (str(spec), str(run_n))
    for name in (("looks", "voids", "pending") if include_pending else ("looks", "voids")):
        for r in read_jsonl(P[name]):
            if (str(r.get("spec")), str(r.get("run"))) == key:
                return name
    return None


def refuse_if_recorded(P, spec, run_n, verb, include_pending=False):
    """Every (spec, run) is charged and typed once: a run already in looks.jsonl or voids.jsonl (for `record` also
    pending.jsonl) is refused -- exit 3 `already-recorded`, one refusals.jsonl row -- so a driver retry appends nothing."""
    where = recorded_in(P, spec, run_n, include_pending)
    if where:
        append_row(P["refusals"], {"at": now(), "reason": "already-recorded", "verb": verb, "spec": spec, "run": run_n, "found_in": "%s.jsonl" % where})
        raise Refuse("already-recorded %s run-%s -- a row in %s.jsonl; every run is charged and typed once, so this %s appends nothing" % (spec, run_n, where, verb))


def append_look(P, spec, run_n, refusal, clause, run_record, root_causes=None, instrument=None):
    refuse_if_recorded(P, spec, run_n, "append-look")
    tk, _ = last_terminal(P)
    if tk:
        raise Refuse("terminal:%s -- a terminated lineage never launches again, so no look can be appended (R3)" % tk)
    k = len(read_jsonl(P["stream"])) + 1
    append_row(P["stream"], {"look": k, "class": STREAM_CLASS, "refusal": int(refusal)})
    row = {"look": k, "spec": spec, "run": run_n, "run_record": run_record, "refusal": int(refusal), "clause": clause, "appended": now()}
    if root_causes:
        row["root_causes"] = root_causes
    append_row(P["looks"], row)
    ev = evaluate(P, instrument=instrument)
    return k, ev


def append_void(P, spec, run_n, cls, clause, run_record, extra=None):
    refuse_if_recorded(P, spec, run_n, "void")
    row = {"spec": spec, "run": run_n, "class": cls, "clause": clause, "run_record": run_record, "recorded": now(), "re_take": RE_TAKE}
    if extra:
        row.update(extra)
    append_row(P["voids"], row)
    return row


def _run_record_rel(layout, run_dir):
    files = load_run_dir(run_dir)
    rec_file = next((n for n in ("RUN-RECORD.json", "run.json", "run-record.json", "grade.json") if n in files), None)
    return layout.runs_rel(os.path.join(run_dir, rec_file)) if rec_file else layout.runs_rel(run_dir)


def record_run(layout, lane, run_dir, run_n=None, run_validity_ids=(), root_causes=None, instrument=None):
    """R2 + R3 for the run just graded: charge, type, append the look (or record the void / the pending look)."""
    root_lane, P, hops = resolve_lineage(layout, lane)
    require_header(P)
    run_dir = os.path.abspath(run_dir)
    if run_n is None:
        base = os.path.basename(run_dir)
        run_n = int(base.split("-")[-1]) if base.startswith("run-") and base.split("-")[-1].isdigit() else None
    refuse_if_recorded(P, lane, run_n, "record", include_pending=True)
    run_record = _run_record_rel(layout, run_dir)
    word, typed, rec, meta = type_run_dir(run_dir, run_validity_ids, root_causes)
    pend = pending_ids(rec, typed)
    outcome = {"lineage": root_lane, "spec": lane, "run": run_n, "run_dir": layout.runs_rel(run_dir), "type": word, "typed": typed, "record": rec, "meta": meta}
    if pend:
        cls = "pending-root-cause"
        row = {"spec": lane, "run": run_n, "run_dir": layout.runs_rel(run_dir), "failing": pend, "settled": False, "recorded": now(),
               "run_validity_ids": sorted(set(str(x) for x in run_validity_ids or []) | set(meta["run_validity_ids"])),
               "how": "lineage-stopping.py settle %s %s --root-cause %s -- one class per failing id, from %s; an id left unclassed refuses (exit 2) and the look stays pending, never typed by guess" % (
                   lane, run_n, " ".join("%s=<class>" % a for a in pend), " | ".join(ROOT_CAUSE_CLASSES))}
        append_row(P["pending"], row)
        outcome["pending"] = pend
    elif typed["type"] == "counted":
        cls = "counted"
    else:
        cls = "void:%s" % typed["type"]
    outcome["spend_row"] = charge(P, lane, run_n, rec.get("wall_s"), rec.get("cost_usd"), cls, run_record)
    if cls == "counted":
        k, ev = append_look(P, lane, run_n, typed["refusal"], typed["clause"], run_record, instrument=instrument)
        outcome["look"] = k
        outcome["evaluation"] = ev
    elif cls.startswith("void:"):
        extra = {"terminal": "budget-exceeded"} if word == "budget-exceeded" else None
        outcome["void_row"] = append_void(P, lane, run_n, typed["type"], typed["clause"], run_record, extra)
        outcome["evaluation"] = {"looks": len(read_jsonl(P["stream"])), "instruction": "continue at R1 (no look appended)"}
    else:
        outcome["evaluation"] = {"looks": len(read_jsonl(P["stream"])), "instruction": "settle the pending look before launching again (R1 refuses while it is pending)"}
    return outcome


def settle(layout, lane, run_n, root_causes, instrument=None):
    root_lane, P, hops = resolve_lineage(layout, lane)
    require_header(P)
    pend = read_jsonl(P["pending"])
    mine = [p for p in pend if p.get("spec") == lane and str(p.get("run")) == str(run_n) and not p.get("settled")]
    if not mine:
        raise Refuse("no pending look for %s run-%s" % (lane, run_n))
    p0 = mine[0]
    rc = dict(root_causes or {})
    bad = sorted("%s=%s" % (a, c) for a, c in rc.items() if c not in ROOT_CAUSE_CLASSES)
    if bad:
        raise Usage("settle %s run-%s: unknown root-cause class %s; one of %s" % (lane, run_n, ", ".join(bad), " | ".join(ROOT_CAUSE_CLASSES)))
    unclassed = [a for a in p0["failing"] if a not in rc]
    if unclassed:
        # the fixture's typing law (lineage.py, R2 (iv)): an unclassed root cause is parked pending, never typed by guess
        raise Usage("settle %s run-%s: no root-cause class for %s -- the look stays pending; pass --root-cause %s with a class from %s (an unclassed root cause is parked pending, never typed by guess)" % (
            lane, run_n, ", ".join(unclassed), " ".join("%s=<class>" % a for a in unclassed), " | ".join(ROOT_CAUSE_CLASSES)))
    run_dir = os.path.join(layout.rel("runs_dir"), *p0["run_dir"].split("/"))
    word, typed, rec, meta = type_run_dir(run_dir, p0.get("run_validity_ids") or (), rc)
    run_record = _run_record_rel(layout, run_dir)
    outcome = {"lineage": root_lane, "spec": lane, "run": int(run_n), "type": word, "typed": typed, "root_causes": rc}
    if typed["type"] == "counted":
        k, ev = append_look(P, lane, int(run_n), typed["refusal"], typed["clause"], run_record, root_causes=rc, instrument=instrument)
        outcome["look"] = k
        outcome["evaluation"] = ev
    else:
        outcome["void_row"] = append_void(P, lane, int(run_n), typed["type"], typed["clause"], run_record, {"root_causes": rc})
        outcome["evaluation"] = {"looks": len(read_jsonl(P["stream"])), "instruction": "continue at R1 (no look appended)"}
    rows = [dict(p, settled=True, typed=typed, root_causes=rc) if (p is p0) else p for p in pend]
    write_rows(P["pending"], rows)
    return outcome


def state(layout, lane):
    """-> (word, detail): promote | hold | max-looks | insufficient | spend-exhausted."""
    root_lane, P, hops = resolve_lineage(layout, lane)
    header, cum_usd, cum_wall, n = spend_state(P)
    tk, lines = last_terminal(P)
    rows = read_jsonl(P["stream"])
    b = header["budget"]
    detail = {"lineage": root_lane, "spec": lane, "looks": len(rows), "state": lines[-1]["state"] if lines else None,
              "llr": lines[-1].get("llr") if lines else None, "terminal": tk, "state_lines": len(lines),
              "stale": len(lines) != len(rows) and not (lines and tk),
              "spend": {"cum_usd": round(cum_usd, 6), "cum_wall_s": round(cum_wall, 1), "launches": n, "budget": b,
                        "remaining": {"usd": round(b["usd"] - cum_usd, 6), "wall_s": round(b["wall_s"] - cum_wall, 1)}},
              "voids": len(read_jsonl(P["voids"])), "pending": [{"spec": p.get("spec"), "run": p.get("run"), "failing": p.get("failing")} for p in open_pending(P)],
              "refusals": len(read_jsonl(P["refusals"])), "frozen_rule_sha256": header.get("frozen_rule_sha256"), "truncation_length": header.get("truncation_length")}
    if tk:
        word = tk
        detail["instruction"] = INSTRUCTIONS[tk]
        detail["next"] = "closed"
    elif not r1_predicate(header, cum_usd, cum_wall):
        word = "spend-exhausted"
        detail["instruction"] = "closed without a verdict on the recorded evidence -- R1 refuses the next launch; raising the budget is a disclosed resource amendment, never a decision card"
        detail["next"] = "closed"
    else:
        word = "insufficient"
        detail["instruction"] = CONTINUE if not detail["pending"] else "settle the pending look, then continue at R1"
        detail["next"] = "settle" if detail["pending"] else "launch"
    return word, detail


# ---------------------------------------------------------------- selftest
def _rec(**kw):
    base = {"launched": True, "resolvable": True, "substantive": [{"id": "A1", "pass": True, "root_cause_class": None}],
            "run_validity": [{"id": "A5", "pass": True, "label": "determinism"}], "control_held": True,
            "treatment_delivered": True, "key_contested": False, "cost_usd": 0.0, "wall_s": 1.0, "cap_s": 10.0}
    base.update(kw)
    return base


SELFTEST_TYPING = [
    ("v: everything holds", _rec(), COUNTED_0),
    ("v: a deferred assertion is neither a failure nor unresolved", _rec(substantive=[{"id": "A1", "pass": "deferred", "root_cause_class": None}]), COUNTED_0),
    ("i: a run-validity assertion fails", _rec(run_validity=[{"id": "A5", "pass": False, "label": "containment"}]), AMBIGUOUS),
    ("i: run-validity failure precedes a variable-side substantive failure", _rec(run_validity=[{"id": "A5", "pass": False, "label": "two-pass"}], substantive=[{"id": "A1", "pass": False, "root_cause_class": VARIABLE_SIDE}]), AMBIGUOUS),
    ("i: run-validity failure precedes a control outside its band", _rec(run_validity=[{"id": "A5", "pass": False, "label": "two-pass"}], control_held=False), AMBIGUOUS),
    ("i: a substantive result unresolved (null)", _rec(substantive=[{"id": "A1", "pass": None, "root_cause_class": None}]), AMBIGUOUS),
    ("i: a run-validity result unresolved (null)", _rec(run_validity=[{"id": "A5", "pass": None, "label": "two-pass"}]), AMBIGUOUS),
    ("i: resolvable false (crash-lost record)", _rec(resolvable=False), AMBIGUOUS),
    ("i: a failing substantive assertion whose cause is harness/run-validity", _rec(substantive=[{"id": "A2", "pass": False, "root_cause_class": HARNESS_SIDE}]), AMBIGUOUS),
    ("i: an unclassed failing assertion with no variable-side entry", _rec(substantive=[{"id": "A1", "pass": False, "root_cause_class": None}]), AMBIGUOUS),
    ("i: unclassed beside a fixture-side entry", _rec(substantive=[{"id": "A1", "pass": False, "root_cause_class": FIXTURE_SIDE}, {"id": "A2", "pass": False, "root_cause_class": None}]), AMBIGUOUS),
    ("ii: control outside its band", _rec(control_held=False), ANNULLED_II),
    ("ii: treatment not delivered", _rec(treatment_delivered=False), ANNULLED_II),
    ("ii: key contested", _rec(key_contested=True), ANNULLED_II),
    ("ii: control false precedes a variable-side failure", _rec(control_held=False, substantive=[{"id": "A1", "pass": False, "root_cause_class": VARIABLE_SIDE}]), ANNULLED_II),
    ("iii: a variable-side failure", _rec(substantive=[{"id": "A2", "pass": False, "root_cause_class": VARIABLE_SIDE}]), COUNTED_1),
    ("iii: variable-side beside a contract-side failure (another assertion's ambiguity does not void)", _rec(substantive=[{"id": "A1", "pass": False, "root_cause_class": FIXTURE_SIDE}, {"id": "A2", "pass": False, "root_cause_class": VARIABLE_SIDE}]), COUNTED_1),
    ("iii: variable-side beside an unclassed failure", _rec(substantive=[{"id": "A1", "pass": False, "root_cause_class": None}, {"id": "A2", "pass": False, "root_cause_class": VARIABLE_SIDE}]), COUNTED_1),
    ("iii: null control/treatment are not recorded, not failures", _rec(control_held=None, treatment_delivered=None, key_contested=None, substantive=[{"id": "A1", "pass": False, "root_cause_class": VARIABLE_SIDE}]), COUNTED_1),
    ("iv: one fixture/manifest/contract-side failure", _rec(substantive=[{"id": "A1", "pass": False, "root_cause_class": FIXTURE_SIDE}]), ANNULLED_IV),
    ("iv: one instrument-side failure", _rec(substantive=[{"id": "A5", "pass": False, "root_cause_class": INSTRUMENT_SIDE}]), ANNULLED_IV),
    ("iv: manifest-side and instrument-side together", _rec(substantive=[{"id": "A1", "pass": False, "root_cause_class": FIXTURE_SIDE}, {"id": "A5", "pass": False, "root_cause_class": INSTRUMENT_SIDE}]), ANNULLED_IV),
    ("nl: a spec-level close with nothing recorded", _rec(launched=False, substantive=[], run_validity=[], control_held=None, treatment_delivered=None, key_contested=None), NOT_LAUNCHED),
    ("nl: a close whose control held", _rec(launched=False, substantive=[], run_validity=[], control_held=True, treatment_delivered=None, key_contested=None), NOT_LAUNCHED),
    ("ii: a close with a contested key", _rec(launched=False, substantive=[], run_validity=[], control_held=None, treatment_delivered=None, key_contested=True), ANNULLED_II),
    ("ii: a close with a control outside its band", _rec(launched=False, substantive=[], run_validity=[], control_held=False, treatment_delivered=None, key_contested=None), ANNULLED_II),
]


def _launches(bits):
    """bits: string over V (void) P (pass) F (fail)."""
    return [{"launch": j, "void": 1 if b == "V" else 0, "pass": 1 if b == "P" else 0} for j, b in enumerate(bits, 1)]


def _mk_run(layout, spec, n, assertions, wall_s=100.0, cost_usd=0.0, cap_s=1800.0, extra_grade=None, extra_record=None):
    rd = os.path.join(layout.rundir(spec), "run-%d" % n)
    os.makedirs(rd, exist_ok=True)
    g = {"assertions": assertions, "passed": sum(1 for v in assertions.values() if _norm_pass(v) is True), "of": len(assertions)}
    g.update(extra_grade or {})
    with open(os.path.join(rd, "grade.json"), "w", encoding="utf-8") as fh:
        json.dump(g, fh, indent=1, sort_keys=True)
    r = {"run_n": n, "wall_s": wall_s, "cost_usd": cost_usd, "cap_s": cap_s, "terminal": "ok", "budget_exceeded": False}
    r.update(extra_record or {})
    with open(os.path.join(rd, "RUN-RECORD.json"), "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=1, sort_keys=True)
    return rd


def selftest(into=None):
    ok = total = 0

    def check(name, good, detail=""):
        nonlocal ok, total
        total += 1
        ok += bool(good)
        print("%s %s%s" % ("PASS" if good else "FAIL", name, (" -- " + str(detail)) if (detail and not good) else ""))

    # 1. the typing table (26 cases, fixture/lineage.py)
    for name, rec, want in SELFTEST_TYPING:
        got = type_record(rec)
        check("typing %s" % name, got == want, json.dumps(got, sort_keys=True))
    # 2. the walk and settle (7 cases, fixture/lineage.py)
    ratios = [317.3 / 1800, 307.7 / 1800, 290.3 / 1800, 277.8 / 1800, 190.0 / 900, 134.3 / 900, 1222 / 9000]
    wA = walk(_launches("P" * 20), charge_model_A(), 13)
    check("walk A funds exactly 13 launches and refuses the 14th", wA["n_launched"] == 13 and wA["refused_at"] == 14 and len(wA["rows"]) == 13)
    wB = walk(_launches("P" * 100), charge_model_B(ratios), 13)
    check("walk B funds 73 launches at the seven recorded ratios and refuses the 74th", wB["n_launched"] == 73 and wB["refused_at"] == 74 and abs(wB["charges"][-1] - 12.0979) < 1e-3)
    wV = walk(_launches("VVPVF" + "P" * 20), charge_model_A(), 13)
    check("walk: voids charge and append no look; look k maps to its launch", len(wV["rows"]) == 10 and wV["look_launch"][:2] == [3, 5] and wV["voids"] == 3 and wV["rows"][1]["refusal"] == 1)
    st = settle_walk(wV, [{"look": 1, "state": "evidence-insufficient n=1/13"}, {"look": 2, "state": "evidence-sufficient hold"}], 13)
    check("settle: a terminal at look 2 fell at launch 5 with charge 5.0 and 3 voids before it", st["t"] == "hold" and st["look"] == 2 and st["launch"] == 5 and st["charge"] == 5.0 and st["voids"] == 3)
    st2 = settle_walk(wA, [{"look": k, "state": "evidence-insufficient n=%d/13" % k} for k in range(1, 14)], 13)
    check("settle: no terminal after R1 refused -> budget-exhausted, charge 13 plus one cap over budget", st2["t"] == "budget-exhausted" and st2["charge"] == 13.0 and st2["over_by_one_cap"] is True and st2["launches"] == 13)
    wS = walk(_launches("PPP"), charge_model_A(), 13)
    st3 = settle_walk(wS, [{"look": k, "state": "evidence-insufficient n=%d/13" % k} for k in range(1, 4)], 13)
    check("settle: a launch file that ends before R1 refuses is launch-file-short (a fixture defect)", st3["t"] == "launch-file-short" and wS["refused_at"] is None)
    wE = walk(_launches("V" * 13 + "P" * 5), charge_model_A(), 13)
    check("walk: thirteen voids leave an empty stream and the budget spent", len(wE["rows"]) == 0 and wE["n_launched"] == 13 and wE["refused_at"] == 14 and wE["last_launch_void"])

    # 3. the policy bytes, the frozen reference and the truncation derivation
    pol = os.path.join(PLUGIN, *POLICY_REL.split("/"))
    ref = os.path.join(PLUGIN, *FROZEN_REF_REL.split("/"))
    check("policy bytes rules/lineage-sprt.json hash to %s" % POLICY_SHA256[:8], os.path.isfile(pol) and sha_file(pol) == POLICY_SHA256)
    check("frozen reference rules/frozen/lineage-sprt.json hashes to %s and its inner rule_text to the policy sha" % FROZEN_REF_SHA256[:8],
          os.path.isfile(ref) and sha_file(ref) == FROZEN_REF_SHA256 and sha_bytes((read_json(ref) or {}).get("rule_text", "").encode("utf-8")) == POLICY_SHA256)
    rule = json.loads(open(pol, "rb").read().decode("utf-8")) if os.path.isfile(pol) else POLICY
    c = sprt_constants(rule)
    check("SPRT constants: inc0 log(0.9/0.5), inc1 log(0.1/0.5), promote at log(18), hold at log(0.1/0.95)",
          abs(c["inc0"] - 0.5877866649021191) < 1e-15 and abs(c["inc1"] - -1.6094379124341003) < 1e-15 and abs(c["up"] - 2.8903717578961645) < 1e-15 and abs(c["lo"] - -2.251291798606495) < 1e-15)
    smallest, table = truncation(rule)
    check("truncation derived, never chosen: smallest n = 13 with P_p0(T>13) = 0.0439 and P_p1(T>13) = 0.0595; n = 12 does not qualify",
          smallest == 13 and round(table[12]["P_p0_T_gt_n"], 4) == 0.0439 and round(table[12]["P_p1_T_gt_n"], 4) == 0.0595 and not table[11]["qualifies"])

    # 4. the lineage bookkeeping end to end in a throwaway consumer root
    scratch = into or tempfile.mkdtemp(prefix="lineage-stopping-selftest-")
    root = os.path.join(scratch, "consumer")
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    with open(os.path.join(root, ".claude", "hyp.json"), "w", encoding="utf-8") as fh:
        json.dump({"profile": "experiments", "runs_dir": "experiments/runs", "ledger_file": "ledger/work-ledger.jsonl"}, fh)
    layout = Layout(root)
    lane = "H-DRAFT-selftest-lineage"
    try:
        out = cmd_init(layout, lane, 1800.0, 0.10)
        P = layout.lineage_paths(lane)
        header = read_jsonl(P["spend"])[0]
        check("R0 init: budget derived 13 x (1800 s, US$0.10) = 23400 s / US$1.30, truncation 13, frozen rule sha = policy sha",
              out["initialised"] and header["budget"] == {"wall_s": 23400.0, "usd": 1.3} and header["truncation_length"] == 13
              and header["frozen_rule_sha256"] == POLICY_SHA256 and header["per_run_cap"] == {"wall_s": 1800.0, "usd": 0.1}, canon(header))
        check("R0 header carries the lab's keys and the derivation prose (30 min wall-clock, US$0.10) x 13 = 6.5 wall-hours and US$1.30",
              set(header) == {"budget", "derivation", "frozen_copy", "frozen_rule_sha256", "header", "lineage", "opened", "per_run_cap", "truncation_length"}
              and "(30 min wall-clock, US$0.10) x the frozen rule's truncation length (13, recomputed at init) = 6.5 wall-hours and US$1.30" in header["derivation"], header["derivation"])
        check("R0 frozen copy is the reference copy byte for byte (%s)" % FROZEN_REF_SHA256[:8], sha_file(P["frozen"]) == FROZEN_REF_SHA256)
        before = sha_file(P["spend"])
        out2 = cmd_init(layout, lane, 1800.0, 0.10)
        check("R0 init is idempotent (a second init changes no byte and reports initialised false)", out2["initialised"] is False and sha_file(P["spend"]) == before)
        ok1, w1, d1 = may_launch(P)
        check("R1 permits the first launch (0 + 1800 <= 23400 and 0 + 0.10 <= 1.30)", ok1 and w1 == "launch" and d1["launches_so_far"] == 0)
        wp, dp = state(layout, lane)
        check("state before any look reads insufficient (n = 0), next = launch", wp == "insufficient" and dp["looks"] == 0 and dp["next"] == "launch")
        looks = []
        for k in range(1, 6):
            kk, ev = append_look(P, lane, k + 1, 0, "v", "%s/run-%d/RUN-RECORD.json" % (lane, k + 1))
            looks.append((kk, ev))
        llrs = [read_jsonl(P["state"])[i]["llr"] for i in range(5)]
        check("R3 five refusal-0 looks: evidence-insufficient n=1..4/13 then evidence-sufficient promote at look 5",
              [ev["state"] for _, ev in looks] == ["evidence-insufficient n=%d/13" % k for k in range(1, 5)] + ["evidence-sufficient promote"] and looks[-1][1]["terminal"] == "promote")
        check("R3 llr trajectory equals the lab's to the last bit (0.5878 -> 1.1756 -> 1.7634 -> 2.3511 -> 2.9389)", llrs == LAB_LLR, llrs)
        check("R3 state.jsonl is byte-identical to the lab lineage's (sha %s)" % LAB_STATE_SHA256[:8], sha_file(P["state"]) == LAB_STATE_SHA256, sha_file(P["state"]))
        check("R3 stream.jsonl is byte-identical to the lab lineage's (sha %s)" % LAB_STREAM_SHA256[:8], sha_file(P["stream"]) == LAB_STREAM_SHA256)
        check("R3 the in-progress looks passed the prefix invariants and the terminated file passed the instrument's --check ok",
              all(ev["check"]["kind"] == "prefix-invariants" and ev["check"]["rc"] == 0 for _, ev in looks[:4]) and looks[-1][1]["check"] == {"kind": "instrument-check", "rc": 0, "detail": "ok"})
        check("R3 instruction at promote is the lab's text", looks[-1][1]["instruction"] == "KEEP -- evidence-sufficient promote")
        wd, dd = state(layout, lane)
        check("state reads promote after the terminal; next = closed", wd == "promote" and dd["next"] == "closed" and dd["looks"] == 5)
        okt, wt, dt = may_launch(P)
        check("R1 refuses a launch after a terminal (terminal:promote) without a refusals row", not okt and wt == "terminal:promote" and not os.path.exists(P["refusals"]))
        try:
            append_look(P, lane, 9, 0, "v", "x")
            check("R3 append-look after a terminal is refused", False)
        except Refuse as e:
            check("R3 append-look after a terminal is refused", "terminal:promote" in str(e))
        # the spend arithmetic
        lane2 = "H-DRAFT-selftest-spend"
        cmd_init(layout, lane2, 1800.0, 0.10)
        P2 = layout.lineage_paths(lane2)
        for i in range(1, 13):
            charge(P2, lane2, i, 1800.0, 0.0, "void:ambiguous", "%s/run-%d/RUN-RECORD.json" % (lane2, i))
        ok13, w13, d13 = may_launch(P2)
        check("R1 permits launch 13 at cumulative 21600 s (21600 + 1800 = 23400 <= 23400: the boundary is inside)", ok13 and w13 == "launch" and d13["launches_so_far"] == 12)
        charge(P2, lane2, 13, 1800.0, 0.0, "void:ambiguous", "%s/run-13/RUN-RECORD.json" % lane2)
        ok14, w14, d14 = may_launch(P2)
        refusals = read_jsonl(P2["refusals"])
        check("R1 refuses launch 14 at cumulative 23400 s: budget-exhausted, one refusals.jsonl row with terminal budget-exhausted",
              not ok14 and w14 == "budget-exhausted" and len(refusals) == 1 and refusals[0]["terminal"] == "budget-exhausted" and refusals[0]["launches_so_far"] == 13)
        ws, ds = state(layout, lane2)
        check("state reads spend-exhausted with 0 looks after 13 full-cap voids (the spend budget is the only bound on re-takes)", ws == "spend-exhausted" and ds["looks"] == 0)
        rows2 = read_jsonl(P2["spend"])
        check("spend rows carry the lab's keys (charged, class, cost_usd, cum_usd, cum_wall_s, run, run_record, spec, wall_s) and cumulative 23400.0",
              all(set(r) == {"charged", "class", "cost_usd", "cum_usd", "cum_wall_s", "run", "run_record", "spec", "wall_s"} for r in rows2[1:]) and rows2[-1]["cum_wall_s"] == 23400.0)
        lane3 = "H-DRAFT-selftest-usd"
        cmd_init(layout, lane3, 1800.0, 0.10)
        P3 = layout.lineage_paths(lane3)
        charge(P3, lane3, 1, 1.0, 1.21, "counted", "x")
        ok3, w3, _ = may_launch(P3)
        check("R1 refuses on the US$ component alone (1.21 + 0.10 > 1.30 while 23399 s of wall remain)", not ok3 and w3 == "budget-exhausted")
        # hold: two refusal-1 looks
        lane4 = "H-DRAFT-selftest-hold"
        cmd_init(layout, lane4, 1800.0, 0.10)
        P4 = layout.lineage_paths(lane4)
        append_look(P4, lane4, 1, 1, "iii", "x")
        k4, ev4 = append_look(P4, lane4, 2, 1, "iii", "x")
        check("R3 two refusal-1 looks read evidence-sufficient hold at look 2 (llr -3.2189 <= -2.2513); instruction DISCARD with the exclusion banked",
              ev4["terminal"] == "hold" and k4 == 2 and abs(ev4["llr"] - 2 * c["inc1"]) < 1e-12 and ev4["instruction"] == INSTRUCTIONS["hold"] and ev4["check"]["rc"] == 0)
        # max-looks: thirteen looks that never cross (PPPF PPPF PPPF P)
        lane5 = "H-DRAFT-selftest-maxlooks"
        cmd_init(layout, lane5, 1800.0, 0.10)
        P5 = layout.lineage_paths(lane5)
        ev5 = None
        for i, b in enumerate("PPPFPPPFPPPFP", 1):
            _, ev5 = append_look(P5, lane5, i, 0 if b == "P" else 1, "v" if b == "P" else "iii", "x")
        check("R3 thirteen looks that cross no boundary read evidence-insufficient max-looks n=13 (closed without a verdict, never a promote)",
              ev5 is not None and ev5["terminal"] == "max-looks" and ev5["state"] == "evidence-insufficient max-looks n=13" and ev5["check"]["rc"] == 0 and state(layout, lane5)[0] == "max-looks")
        # prefix invariants: a tampered in-progress line
        rows_pi = [{"look": k, "class": STREAM_CLASS, "refusal": 0} for k in (1, 2)]
        lines_pi = [{"llr": c["inc0"], "look": 1, "n_min": 13, "rule": "sprt", "rule_sha": POLICY_SHA256, "state": "evidence-insufficient n=1/13", "stream": "stream"},
                    {"llr": 2 * c["inc0"] + 1e-6, "look": 2, "n_min": 13, "rule": "sprt", "rule_sha": POLICY_SHA256, "state": "evidence-insufficient n=2/13", "stream": "stream"}]
        okpi, invpi, _ = prefix_invariants(lines_pi, rows_pi, rule, POLICY_SHA256)
        lines_ok = [dict(lines_pi[0]), dict(lines_pi[1], llr=2 * c["inc0"])]
        okpi2, _, _ = prefix_invariants(lines_ok, rows_pi, rule, POLICY_SHA256)
        check("R3 prefix invariants name llr-recompute on a tampered in-progress line and accept the clean one", (not okpi and invpi == "llr-recompute") and okpi2)
        # R2 record: the lab's run-1 (A5 false) and run-2 (5/5)
        lane6 = "H-DRAFT-selftest-record"
        cmd_init(layout, lane6, 1800.0, 0.10)
        P6 = layout.lineage_paths(lane6)
        rd1 = _mk_run(layout, lane6, 1, {"A1": {"pass": True}, "A2": {"pass": True}, "A3": {"pass": True}, "A4": {"pass": True}, "A5": {"pass": False}}, wall_s=950.1,
                      extra_grade={"class": "void:ambiguous", "void_triggers": {"ambiguous": ["containment difference"], "annulled": [], "budget-exceeded": []}, "manipulation_check": {"treatment_delivered": True}})
        o1 = record_run(layout, lane6, rd1, run_validity_ids=["A5"])
        check("R2 record: a run-validity failure is void ambiguous (i): charged 950.1 s, a voids.jsonl row, no look (the lab's run-1)",
              o1["type"] == "ambiguous" and o1["typed"] == AMBIGUOUS and o1["spend_row"]["class"] == "void:ambiguous" and o1["spend_row"]["wall_s"] == 950.1
              and len(read_jsonl(P6["voids"])) == 1 and len(read_jsonl(P6["stream"])) == 0 and read_jsonl(P6["voids"])[0]["re_take"] == RE_TAKE, canon(o1["typed"]))
        rd2 = _mk_run(layout, lane6, 2, {"A1": {"pass": True}, "A2": {"pass": True}, "A3": {"pass": True}, "A4": {"pass": True}, "A5": {"pass": True}}, wall_s=963.3,
                      extra_grade={"class": "counted", "void_triggers": {"ambiguous": [], "annulled": [], "budget-exceeded": []}, "manipulation_check": {"treatment_delivered": True}})
        o2 = record_run(layout, lane6, rd2, run_validity_ids=["A5"])
        check("R2 record: everything holds -> counted look 1 refusal 0 (v), cumulative 1913.4 s, state evidence-insufficient n=1/13",
              o2["type"] == "counted" and o2["look"] == 1 and o2["typed"] == COUNTED_0 and o2["spend_row"]["cum_wall_s"] == 1913.4 and o2["evaluation"]["state"] == "evidence-insufficient n=1/13"
              and read_jsonl(P6["looks"])[0]["clause"] == "v" and o2["record"]["treatment_delivered"] is True, canon(o2["typed"]))
        rd3 = _mk_run(layout, lane6, 3, {"A1": {"pass": True}, "A2": {"pass": True}}, extra_record={"terminal": "budget-exceeded", "budget_exceeded": True}, wall_s=1800.0)
        w3, t3, r3, m3 = type_run_dir(rd3, ["A5"])
        o3 = record_run(layout, lane6, rd3, run_validity_ids=["A5"])
        check("R2 type: a budget-exceeded run reads budget-exceeded (the ambiguous void, reality unclear): charged, no look, voids row carries terminal budget-exceeded",
              w3 == "budget-exceeded" and t3 == AMBIGUOUS and o3["type"] == "budget-exceeded" and o3["spend_row"]["class"] == "void:ambiguous" and o3["void_row"].get("terminal") == "budget-exceeded" and len(read_jsonl(P6["stream"])) == 1)
        # pending and settle
        rd4 = _mk_run(layout, lane6, 4, {"A1": {"pass": True}, "A2": {"pass": False}, "A3": {"pass": True}, "A5": {"pass": True}}, wall_s=100.0, extra_grade={"manipulation_check": {"treatment_delivered": True}, "control_held": True})
        w4, t4, r4, m4 = type_run_dir(rd4, ["A5"])
        o4 = record_run(layout, lane6, rd4, run_validity_ids=["A5"])
        okp4, wp4, _ = may_launch(P6)
        check("R2 record: a substantive failure with no recorded root cause is PENDING (type alone reads ambiguous: never typed by guess); spend charged as pending-root-cause; R1 refuses look-pending",
              w4 == "ambiguous" and o4.get("pending") == ["A2"] and o4["spend_row"]["class"] == "pending-root-cause" and not okp4 and wp4 == "look-pending" and state(layout, lane6)[0] == "insufficient" and state(layout, lane6)[1]["next"] == "settle")
        s4 = settle(layout, lane6, 4, {"A2": FIXTURE_SIDE})
        check("settle fixture/manifest/contract-side -> void annulled (iv): no look, the pending row settled, R1 permits again",
              s4["typed"] == ANNULLED_IV and "void_row" in s4 and len(read_jsonl(P6["stream"])) == 1 and not open_pending(P6) and may_launch(P6)[1] == "launch")
        rd5 = _mk_run(layout, lane6, 5, {"A1": {"pass": True}, "A2": {"pass": False}, "A5": {"pass": True}}, wall_s=100.0)
        o5 = record_run(layout, lane6, rd5, run_validity_ids=["A5"])
        try:
            settle(layout, lane6, 5, {})
            e5 = None
        except Usage as e:
            e5 = str(e)
        check("settle with no root-cause class REFUSES (exit 2, one line naming the pending look and the four classes) and leaves the look pending -- never defaulted to variable-side, never typed by guess",
              o5.get("pending") == ["A2"] and e5 is not None and "run-5" in e5 and "A2" in e5 and all(cls in e5 for cls in ROOT_CAUSE_CLASSES)
              and [p["run"] for p in open_pending(P6)] == [5] and may_launch(P6)[1] == "look-pending" and len(read_jsonl(P6["stream"])) == 1, e5)
        s5 = settle(layout, lane6, 5, {"A2": VARIABLE_SIDE})
        check("settle --root-cause A2=variable-side -> counted look 2 refusal 1 (iii), llr falls, root_causes recorded on the look, nothing pending",
              s5["typed"] == COUNTED_1 and s5["look"] == 2 and s5["evaluation"]["state"] == "evidence-insufficient n=2/13" and abs(s5["evaluation"]["llr"] - (c["inc0"] + c["inc1"])) < 1e-12
              and read_jsonl(P6["looks"])[1].get("root_causes") == {"A2": VARIABLE_SIDE} and not open_pending(P6))
        # type verb: root cause flags and the three assertion shapes; grader-typed voids
        rd6 = _mk_run(layout, lane6, 6, {"A1": {"pass": False}, "A2": {"pass": False}, "A5": {"pass": True}})
        w6, t6, _, _ = type_run_dir(rd6, ["A5"], {"A1": FIXTURE_SIDE, "A2": VARIABLE_SIDE})
        check("type --root-cause: variable-side A2 beside contract-side A1 reads counted refusal 1 (iii) -- another assertion's ambiguity does not void", w6 == "counted" and t6 == COUNTED_1)
        rd7 = _mk_run(layout, lane6, 7, {"A1": "PASS", "A2": "PASS", "A5": "PASS"})
        rd8 = _mk_run(layout, lane6, 8, {"A1": True, "A2": True, "A5": True})
        rd9 = _mk_run(layout, lane6, 9, {"A1": {"pass": True, "clauses": []}, "A2": {"pass": "deferred"}, "A5": {"pass": True, "kind": "run-validity"}})
        check("type reads assertions as PASS/FAIL strings, booleans and {pass} objects (deferred is neither a failure nor unresolved); a kind: run-validity entry needs no flag",
              type_run_dir(rd7, ["A5"])[0] == "counted" and type_run_dir(rd8, ["A5"])[0] == "counted" and type_run_dir(rd9)[0] == "counted" and type_run_dir(rd9)[3]["run_validity_ids"] == ["A5"])
        rd10 = _mk_run(layout, lane6, 10, {"A1": "FAIL", "A5": "PASS"}, extra_grade={"void_class": "annulled"})
        rd11 = _mk_run(layout, lane6, 11, {"A1": "PASS", "A5": "PASS"}, extra_grade={"void_triggers": {"ambiguous": ["evaluator exited non-zero"], "annulled": []}})
        rd12 = _mk_run(layout, lane6, 12, {"A1": "PASS", "A5": "PASS"}, extra_grade={"manipulation_check": {"treatment_delivered": False}})
        check("type honours grader-typed voids: void_class annulled -> annulled (ii); void_triggers.ambiguous -> ambiguous (i); treatment not delivered -> annulled (ii)",
              type_run_dir(rd10, ["A5"])[1] == ANNULLED_II and type_run_dir(rd11, ["A5"])[1] == AMBIGUOUS and type_run_dir(rd12, ["A5"])[1] == ANNULLED_II)
        rd13 = os.path.join(layout.rundir(lane6), "run-13")
        os.makedirs(rd13, exist_ok=True)
        w13, t13, r13, m13 = type_run_dir(rd13, ["A5"])
        check("type on a run directory with no record is ambiguous (a crash-lost record, resolvable false)", w13 == "ambiguous" and r13["resolvable"] is False and t13 == AMBIGUOUS)
        rd14 = _mk_run(layout, lane6, 14, {"A1": "PASS", "A5": "FAIL"}, extra_grade={"void_class": "annulled"})
        check("type keeps R2's order: a run-validity failure is ambiguous (i) even when the grader also named an annulment", type_run_dir(rd14, ["A5"])[1] == AMBIGUOUS)
        # R4 inherit
        succ = "H-DRAFT-selftest-record-v2"
        oi = cmd_init(layout, succ, None, None, inherit=lane6)
        rl, PS, hops = resolve_lineage(layout, succ)
        rdv2 = _mk_run(layout, succ, 1, {"A1": {"pass": True}, "A2": {"pass": True}, "A5": {"pass": True}}, wall_s=50.0)
        ov2 = record_run(layout, succ, rdv2, run_validity_ids=["A5"])
        spend6 = read_jsonl(P6["spend"])
        check("R4 a refine successor inherits the root's frozen copy, stream, looks, ledger and budget: its counted run is look 3 of the ROOT stream, its spend row carries the successor's spec id, nothing reset",
              oi["initialised"] and oi["inherits"] == lane6 and rl == lane6 and PS["spend"] == P6["spend"] and hops == [succ, lane6]
              and ov2["look"] == 3 and ov2["lineage"] == lane6 and spend6[-1]["spec"] == succ and spend6[-1]["run"] == 1 and state(layout, succ)[1]["looks"] == 3 and state(layout, succ)[0] == "insufficient")
        try:
            cmd_init(layout, succ, 1800.0, 0.10)
            refroze = True
        except Refuse:
            refroze = False
        oi2 = cmd_init(layout, succ, None, None, inherit=lane6)
        check("R4 init on a successor is idempotent and the successor may not re-freeze its own copy (a refine never resets)",
              oi2["initialised"] is False and refroze is False and (read_json(layout.lineage_paths(succ)["inherits"]) or {}).get("lineage_root") == lane6 and not os.path.exists(layout.lineage_paths(succ)["spend"]))
        # init refusals: a policy whose max_looks is not the derived truncation; a non-sprt policy
        bad = os.path.join(scratch, "bad-policy.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"kind": "sprt", "alpha": 0.05, "beta": 0.1, "p0": 0.5, "p1": 0.1, "max_looks": 12}\n')
        try:
            cmd_init(layout, "H-DRAFT-selftest-bad", 1800.0, 0.10, policy=bad)
            r_bad = None
        except Refuse as e:
            r_bad = str(e)
        fixed = os.path.join(scratch, "fixed-policy.json")
        with open(fixed, "w", encoding="utf-8") as fh:
            fh.write('{"kind": "fixed-n", "n_min": 60, "max_looks": 120, "threshold": {"lineage": 2}}\n')
        try:
            cmd_init(layout, "H-DRAFT-selftest-fixed", 1800.0, 0.10, policy=fixed)
            r_fixed = None
        except Refuse as e:
            r_fixed = str(e)
        check("R0 refuses a policy whose max_looks is not Wald's truncation (12 vs derived 13) and a non-sprt policy (exit 3, nothing opened)",
              r_bad is not None and "derived 13" in r_bad and r_fixed is not None and not os.path.exists(layout.lineage_paths("H-DRAFT-selftest-bad")["spend"]))
        # evaluate alone and the stale flag
        P7 = layout.lineage_paths("H-DRAFT-selftest-stale")
        cmd_init(layout, "H-DRAFT-selftest-stale", 1800.0, 0.10)
        append_row(P7["stream"], {"look": 1, "class": STREAM_CLASS, "refusal": 0})
        st_w, st_d = state(layout, "H-DRAFT-selftest-stale")
        ev7 = evaluate(P7)
        st_w2, st_d2 = state(layout, "H-DRAFT-selftest-stale")
        check("state flags a stream ahead of its state file as stale; `evaluate` re-derives state.jsonl from the pooled stream", st_d["stale"] is True and ev7["looks"] == 1 and st_d2["stale"] is False and st_w2 == "insufficient")
        # the CLI surface end to end (subprocess): init / may-launch / type / record / state / paths, --json after the verb
        me = os.path.realpath(__file__)
        lane8 = "H-DRAFT-selftest-cli"
        def cli(*args):
            return subprocess.run([sys.executable, me, "--root", root] + list(args), capture_output=True, text=True)
        r_i = cli("init", lane8, "--budget-s", "1800", "--budget-usd", "0.10")
        r_m = cli("may-launch", lane8)
        rd8c = _mk_run(layout, lane8, 1, {"A1": "PASS", "A2": "PASS", "A5": "PASS"}, wall_s=12.5)
        r_t = cli("type", rd8c, "--run-validity", "A5")
        r_r = cli("record", lane8, rd8c, "--run-validity", "A5", "--json")
        r_s = cli("state", lane8)
        r_p = cli("paths", lane8, "--json")
        r_u = cli("state", "H-DRAFT-selftest-never-initialised")
        rj = json.loads(r_r.stdout) if r_r.returncode == 0 else {}
        check("CLI: init -> may-launch exit 0 `launch` -> type `counted` -> record --json (look 1, spend 12.5 s) -> state `insufficient` -> paths --json names the lineage files; an uninitialised lane exits 2",
              r_i.returncode == 0 and r_m.returncode == 0 and r_m.stdout.strip() == "launch" and r_t.returncode == 0 and r_t.stdout.strip() == "counted"
              and r_r.returncode == 0 and rj.get("look") == 1 and rj.get("spend_row", {}).get("wall_s") == 12.5 and r_s.returncode == 0 and r_s.stdout.splitlines()[0] == "insufficient"
              and r_p.returncode == 0 and json.loads(r_p.stdout)["files"]["spend"].endswith("lineage/spend.jsonl") and r_u.returncode == 2,
              "init %d / may-launch %d %r / type %d %r / record %d / state %d %r / paths %d / uninit %d %s" % (r_i.returncode, r_m.returncode, r_m.stdout.strip(), r_t.returncode, r_t.stdout.strip(), r_r.returncode, r_s.returncode, r_s.stdout.strip(), r_p.returncode, r_u.returncode, r_u.stderr.strip()[:80]))
        # settle: each accepted class types as before; an id left unclassed refuses and the look stays pending (A2 of the ship refute)
        lane9 = "H-DRAFT-selftest-settle"
        cmd_init(layout, lane9, 1800.0, 0.10)
        P9 = layout.lineage_paths(lane9)
        settled = {}
        for n, cls in ((1, HARNESS_SIDE), (2, INSTRUMENT_SIDE), (3, FIXTURE_SIDE), (4, VARIABLE_SIDE)):
            rd = _mk_run(layout, lane9, n, {"A1": {"pass": True}, "A2": {"pass": False}, "A5": {"pass": True}})
            o = record_run(layout, lane9, rd, run_validity_ids=["A5"])
            settled[cls] = (o.get("pending"), settle(layout, lane9, n, {"A2": cls}))
        check("settle types each accepted class as before: harness/run-validity -> void ambiguous (i); instrument -> void annulled (iv); fixture/manifest/contract-side -> void annulled (iv); variable-side -> counted refusal 1 (iii)",
              all(v[0] == ["A2"] for v in settled.values()) and settled[HARNESS_SIDE][1]["typed"] == AMBIGUOUS and settled[INSTRUMENT_SIDE][1]["typed"] == ANNULLED_IV
              and settled[FIXTURE_SIDE][1]["typed"] == ANNULLED_IV and settled[VARIABLE_SIDE][1]["typed"] == COUNTED_1 and settled[VARIABLE_SIDE][1]["look"] == 1
              and len(read_jsonl(P9["voids"])) == 3 and len(read_jsonl(P9["stream"])) == 1 and not open_pending(P9),
              json.dumps({k: v[1]["typed"] for k, v in settled.items()}, sort_keys=True))
        rd95 = _mk_run(layout, lane9, 5, {"A1": {"pass": True}, "A2": {"pass": False}, "A3": {"pass": False}, "A5": {"pass": True}})
        o95 = record_run(layout, lane9, rd95, run_validity_ids=["A5"])
        try:
            settle(layout, lane9, 5, {"A2": VARIABLE_SIDE})
            e95 = None
        except Usage as e:
            e95 = str(e)
        try:
            settle(layout, lane9, 5, {"A2": VARIABLE_SIDE, "A3": "spec-side"})
            e95b = None
        except Usage as e:
            e95b = str(e)
        s95 = settle(layout, lane9, 5, {"A2": VARIABLE_SIDE, "A3": FIXTURE_SIDE})
        check("settle refuses while ANY pending id is unclassed (A3 named, A2 not) and refuses an unknown class; with both classed, variable-side beside contract-side is counted refusal 1 (iii)",
              o95.get("pending") == ["A2", "A3"] and e95 is not None and "A3" in e95 and "A2=" not in e95 and e95b is not None and "spec-side" in e95b
              and s95["typed"] == COUNTED_1 and s95["look"] == 2 and not open_pending(P9), (e95, e95b))
        rd96 = _mk_run(layout, lane9, 6, {"A1": {"pass": True}, "A2": {"pass": False}, "A5": {"pass": True}})
        record_run(layout, lane9, rd96, run_validity_ids=["A5"])
        r_s96 = cli("settle", lane9, "6")
        still_pending = [p["run"] for p in open_pending(P9)]
        r_s96b = cli("settle", lane9, "6", "--root-cause", "A2=instrument")
        check("CLI: settle without --root-cause exits 2 with one usage line naming the pending look and the accepted classes, the look still pending; settle --root-cause A2=instrument exits 0 `annulled`",
              r_s96.returncode == 2 and r_s96.stdout == "" and len(r_s96.stderr.strip().splitlines()) == 1 and "run-6" in r_s96.stderr and all(cls in r_s96.stderr for cls in ROOT_CAUSE_CLASSES)
              and still_pending == [6] and r_s96b.returncode == 0 and r_s96b.stdout.splitlines()[0] == "annulled" and not open_pending(P9),
              "rc %d stderr %r / pending %r / rc %d stdout %r" % (r_s96.returncode, r_s96.stderr.strip()[:160], still_pending, r_s96b.returncode, r_s96b.stdout.strip()[:80]))
        # idempotency: every (spec, run) is charged and typed once (A3 of the ship refute)
        lane10 = "H-DRAFT-selftest-idempotent"
        cmd_init(layout, lane10, 1800.0, 0.10)
        P10 = layout.lineage_paths(lane10)
        rd101 = _mk_run(layout, lane10, 1, {"A1": "PASS", "A2": "PASS", "A5": "PASS"}, wall_s=10.0)
        o101 = record_run(layout, lane10, rd101, run_validity_ids=["A5"])
        try:
            record_run(layout, lane10, rd101, run_validity_ids=["A5"])
            dup1 = None
        except Refuse as e:
            dup1 = str(e)
        ref10 = read_jsonl(P10["refusals"])
        check("record twice appends ONE look: the second record is refused `already-recorded <lane> run-1` before charging -- one stream row, one looks row, one spend row, one refusals.jsonl row with reason already-recorded",
              o101["look"] == 1 and dup1 is not None and dup1.startswith("already-recorded %s run-1" % lane10) and len(read_jsonl(P10["stream"])) == 1 and len(read_jsonl(P10["looks"])) == 1
              and len(read_jsonl(P10["spend"])) == 2 and len(ref10) == 1 and ref10[0]["reason"] == "already-recorded" and ref10[0]["run"] == 1 and ref10[0]["found_in"] == "looks.jsonl" and ref10[0]["verb"] == "record", dup1)
        dups = {}
        for verb, fn in (("append-look", lambda: append_look(P10, lane10, 1, 0, "v", "x")), ("void", lambda: append_void(P10, lane10, 1, "ambiguous", "i", "x"))):
            try:
                fn()
                dups[verb] = None
            except Refuse as e:
                dups[verb] = str(e)
        append_void(P10, lane10, 2, "ambiguous", "i", "x")
        for verb, fn in (("void-again", lambda: append_void(P10, lane10, 2, "ambiguous", "i", "x")), ("look-after-void", lambda: append_look(P10, lane10, 2, 0, "v", "x"))):
            try:
                fn()
                dups[verb] = None
            except Refuse as e:
                dups[verb] = str(e)
        check("append-look and void refuse a run already in looks.jsonl (run 1) or voids.jsonl (run 2) as already-recorded; stream and voids unchanged; each refusal wrote its row",
              all(v is not None and v.startswith("already-recorded") for v in dups.values()) and len(read_jsonl(P10["stream"])) == 1 and len(read_jsonl(P10["voids"])) == 1
              and len(read_jsonl(P10["refusals"])) == 5 and sorted(r["found_in"] for r in read_jsonl(P10["refusals"])) == ["looks.jsonl"] * 3 + ["voids.jsonl"] * 2, json.dumps(dups))
        rd103 = _mk_run(layout, lane10, 3, {"A1": "PASS", "A2": "FAIL", "A5": "PASS"})
        o103 = record_run(layout, lane10, rd103, run_validity_ids=["A5"])
        try:
            record_run(layout, lane10, rd103, run_validity_ids=["A5"])
            dup3 = None
        except Refuse as e:
            dup3 = str(e)
        check("record refuses a run parked pending (pending.jsonl) as already-recorded too: one pending row, no second charge; settle is the only way forward",
              o103.get("pending") == ["A2"] and dup3 is not None and "pending.jsonl" in dup3 and len(open_pending(P10)) == 1 and len(read_jsonl(P10["spend"])) == 3, dup3)
        k_a, _ = append_look(P10, lane10, None, 0, "v", "x")
        k_b, _ = append_look(P10, lane10, None, 0, "v", "x")
        check("a look with no run number (run None) is unidentified and never guarded: two such looks append two rows", k_a == 2 and k_b == 3 and len(read_jsonl(P10["stream"])) == 3)
        r_d1 = cli("record", lane10, rd101, "--run-validity", "A5")
        r_d2 = cli("append-look", lane10, "0", "--run", "1")
        r_d3 = cli("void", lane10, "--class", "ambiguous", "--run", "2")
        check("CLI: record / append-look / void on an already-recorded run exit 3 with `refused: already-recorded <lane> run-N`",
              r_d1.returncode == 3 and r_d1.stdout.startswith("refused: already-recorded %s run-1" % lane10) and r_d2.returncode == 3 and "already-recorded" in r_d2.stdout
              and r_d3.returncode == 3 and "already-recorded" in r_d3.stdout, "%d %r / %d / %d" % (r_d1.returncode, r_d1.stdout.strip()[:100], r_d2.returncode, r_d3.returncode))
    finally:
        if into is None:
            shutil.rmtree(scratch, ignore_errors=True)
    print("selftest: %d/%d" % (ok, total))
    return 0 if ok == total else 1


# ---------------------------------------------------------------- CLI
def parse_root_causes(items):
    rc = {}
    for x in items or []:
        if "=" not in x:
            raise Usage("--root-cause takes A#=<class>, got %r" % x)
        a, cls = x.split("=", 1)
        if cls not in ROOT_CAUSE_CLASSES:
            raise Usage("unknown root cause class %r; one of %s" % (cls, ", ".join(ROOT_CAUSE_CLASSES)))
        rc[a.strip()] = cls
    return rc


def parse_ids(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def emit(word, detail, as_json, line2=None):
    if as_json:
        out = dict(detail or {})
        out["word"] = word
        sys.stdout.write(json.dumps(out, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write("%s\n" % word)
        if line2:
            sys.stdout.write("%s\n" % line2)


def _rc_of(ev):
    ev = ev or {}
    return EXIT_OK if (ev.get("evaluate_rc") in (None, 0) and (ev.get("check") or {}).get("rc", 0) == 0) else EXIT_VIOLATION


def main(argv=None):
    ap = argparse.ArgumentParser(description="the lineage stopping rule R0-R4 as a CLI a lane driver calls")
    ap.add_argument("--root", help="consumer repository root (default: nearest ancestor with .claude/hyp.json or .git)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--instrument", help="path to the kept stopping-rule.py (default: this plugin's scripts/stopping-rule.py)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--into", help="--selftest: keep the scratch tree here")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", dest="json_sub", action="store_true")
        p.add_argument("--root", dest="root_sub")
        return p
    p = add("init", "R0: freeze once, open the ledgers, derive the spend budget")
    p.add_argument("lane")
    p.add_argument("--budget-s", type=float, help="the spec's Budget-per-run wall-clock cap, seconds")
    p.add_argument("--budget-usd", type=float, help="the spec's Budget-per-run US$ cap")
    p.add_argument("--inherit", metavar="ROOT", help="R4: this lane is a refine successor of ROOT")
    p.add_argument("--policy", help="a policy document other than rules/lineage-sprt.json (a lab decision; must be sprt with Wald's truncation)")
    p.add_argument("--spec-id", help="record this id in the header (default: the lane)")
    p = add("paths", "the resolved layout and lineage files")
    p.add_argument("lane")
    p = add("may-launch", "R1: exit 0 launch, exit 3 refused")
    p.add_argument("lane")
    p = add("type", "R2 typing of a run directory: counted|ambiguous|annulled|budget-exceeded")
    p.add_argument("run_dir")
    p.add_argument("--run-validity", help="comma-separated ids of the run-validity assertions (the spec's SUBSTANTIVE-ASSERTIONS declaration)")
    p.add_argument("--root-cause", action="append", default=[], help="A#=<class>, one of %s" % ", ".join(ROOT_CAUSE_CLASSES))
    p = add("record", "R2 charge + type + look/void/pending for a graded run, then R3")
    p.add_argument("lane")
    p.add_argument("run_dir")
    p.add_argument("--run", type=int)
    p.add_argument("--run-validity")
    p.add_argument("--root-cause", action="append", default=[])
    p = add("charge", "R2: one spend row")
    p.add_argument("lane")
    p.add_argument("--wall-s", type=float, required=True)
    p.add_argument("--cost-usd", type=float, required=True)
    p.add_argument("--class", dest="cls", required=True, help="counted | void:ambiguous | void:annulled | pending-root-cause")
    p.add_argument("--run", type=int)
    p.add_argument("--run-record", default=None)
    p = add("append-look", "R2 look + R3 evaluation")
    p.add_argument("lane")
    p.add_argument("refusal", type=int, choices=(0, 1))
    p.add_argument("--run", type=int)
    p.add_argument("--clause", choices=("iii", "v"))
    p.add_argument("--run-record", default=None)
    p = add("void", "R2: one voids.jsonl row (no look)")
    p.add_argument("lane")
    p.add_argument("--class", dest="cls", required=True, choices=VOID_CLASSES)
    p.add_argument("--clause", choices=("i", "ii", "iv"))
    p.add_argument("--run", type=int)
    p.add_argument("--run-record", default=None)
    p = add("settle", "R2 (iii)/(iv) for a pending look")
    p.add_argument("lane")
    p.add_argument("run", type=int)
    p.add_argument("--root-cause", action="append", default=[])
    p = add("evaluate", "R3 alone: re-evaluate the stream into state.jsonl")
    p.add_argument("lane")
    p = add("state", "promote|hold|insufficient|max-looks|spend-exhausted")
    p.add_argument("lane")
    p = add("walk", "the seeded-lineage walk (R1 + the look/void split)")
    p.add_argument("launches")
    p.add_argument("--model", choices=("A", "B"), required=True)
    p.add_argument("--ratios")
    p.add_argument("--budget-caps", type=float, default=BUDGET_CAPS_DEFAULT)
    p.add_argument("--out", required=True)
    o = ap.parse_args(argv)
    if o.selftest:
        return selftest(o.into)
    if not o.cmd:
        ap.print_usage(sys.stderr)
        return EXIT_USAGE
    as_json = bool(o.json or getattr(o, "json_sub", False))
    root = o.root or getattr(o, "root_sub", None)
    try:
        if o.cmd == "walk":
            fn = charge_model_A() if o.model == "A" else charge_model_B([float(x) for x in (o.ratios or "").split(",") if x])
            w = walk(read_jsonl(o.launches), fn, o.budget_caps)
            write_rows(o.out, w["rows"])
            sys.stdout.write(canon({"rows": len(w["rows"]), "n_launched": w["n_launched"], "refused_at": w["refused_at"], "voids": w["voids"],
                                    "charge": w["charges"][-1] if w["charges"] else 0.0}) + "\n")
            return EXIT_OK
        if o.cmd == "type":
            word, typed, rec, meta = type_run_dir(o.run_dir, parse_ids(o.run_validity), parse_root_causes(o.root_cause))
            pend = pending_ids(rec, typed)
            detail = {"typed": typed, "record": rec, "meta": meta, "pending_root_cause": pend,
                      "void_class": ("ambiguous" if word == "budget-exceeded" else (typed["type"] if typed["type"] in VOID_CLASSES else None))}
            emit(word, detail, as_json)
            return EXIT_OK
        layout = Layout(root or find_root())
        if o.cmd == "init":
            out = cmd_init(layout, o.lane, o.budget_s, o.budget_usd, inherit=o.inherit, policy=o.policy, spec_id=o.spec_id, instrument=o.instrument)
            sys.stdout.write(json.dumps(out, indent=1, sort_keys=True) + "\n")
            return EXIT_OK
        if o.cmd == "paths":
            root_lane, P, hops = resolve_lineage(layout, o.lane)
            out = {"root": layout.root, "layout": layout.cfg, "lane": o.lane, "lineage_root": root_lane, "hops": hops,
                   "lineage_dir": os.path.dirname(P["spend"]), "files": P, "spec": layout.spec_path(o.lane),
                   "ledger_file": layout.rel("ledger_file"), "instrument": instrument_path(o.instrument), "policy": os.path.join(PLUGIN, *POLICY_REL.split("/")),
                   "initialised": os.path.exists(P["spend"]) and os.path.exists(P["frozen"])}
            sys.stdout.write(json.dumps(out, indent=1, sort_keys=True) + "\n")
            return EXIT_OK
        root_lane, P, hops = resolve_lineage(layout, o.lane)
        if o.cmd == "may-launch":
            ok, word, detail = may_launch(P)
            emit(word, detail, as_json, None if ok else detail.get("reason"))
            return EXIT_OK if ok else EXIT_REFUSED
        if o.cmd == "state":
            word, detail = state(layout, o.lane)
            emit(word, detail, as_json, detail["instruction"])
            return EXIT_OK
        if o.cmd == "evaluate":
            require_header(P)
            ev = evaluate(P, instrument=o.instrument)
            emit(ev["state"] or "no looks", ev, as_json, ev["instruction"])
            return _rc_of(ev)
        if o.cmd == "record":
            out = record_run(layout, o.lane, o.run_dir, o.run, parse_ids(o.run_validity), parse_root_causes(o.root_cause), instrument=o.instrument)
            emit("pending" if out.get("pending") else out["type"], out, as_json, out["evaluation"]["instruction"])
            return _rc_of(out.get("evaluation"))
        if o.cmd == "charge":
            require_header(P)
            row = charge(P, o.lane, o.run, o.wall_s, o.cost_usd, o.cls, o.run_record or ("%s/run-%s" % (o.lane, o.run) if o.run else None))
            sys.stdout.write(canon(row) + "\n")
            return EXIT_OK
        if o.cmd == "append-look":
            require_header(P)
            clause = o.clause or ("v" if o.refusal == 0 else "iii")
            k, ev = append_look(P, o.lane, o.run, o.refusal, clause, o.run_record or ("%s/run-%s" % (o.lane, o.run) if o.run else None), instrument=o.instrument)
            emit(ev["state"], dict(ev, look=k), as_json, ev["instruction"])
            return _rc_of(ev)
        if o.cmd == "void":
            require_header(P)
            clause = o.clause or ("i" if o.cls == "ambiguous" else "ii")
            row = append_void(P, o.lane, o.run, o.cls, clause, o.run_record or ("%s/run-%s" % (o.lane, o.run) if o.run else None))
            sys.stdout.write(canon(row) + "\n")
            return EXIT_OK
        if o.cmd == "settle":
            out = settle(layout, o.lane, o.run, parse_root_causes(o.root_cause), instrument=o.instrument)
            emit(out["type"], out, as_json, out["evaluation"]["instruction"])
            return _rc_of(out.get("evaluation"))
    except Refuse as e:
        sys.stdout.write("refused: %s\n" % e)
        return EXIT_REFUSED
    except Usage as e:
        sys.stderr.write("usage: %s\n" % e)
        return EXIT_USAGE
    ap.print_usage(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
