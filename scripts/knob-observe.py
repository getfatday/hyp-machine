#!/usr/bin/env python3
"""knob-observe.py -- the between-runs evaluator for bounded knob nodes.

Lane: checkpoint-gate-shadow-promotion (decision-durability program). A knob is a bounded
setting a script moves between runs; this evaluator reads the knob node, the committed signal
rows, the licensing policy node, the kill-switch surfaces and the run directories' lane state,
appends ONE state row to ledger/knob-state.jsonl per new signal state, and -- only in
`mode: recommend`, only when the window holds the declared sample size, only when the licensing
node resolves (H-241), and only when no decision for the knob is open -- files exactly one
class-plan decision row through scripts/decisions.py add --no-open. Silence is a recorded
state (`evidence-insufficient n=k/30`), never an absent one (H-154). No wall clock is read
anywhere: DECISIONS_TODAY is passed through to decisions.py as --date and the state row carries
no clock field.

Verbs
  evaluate <knob> [--root DIR] [--at SHA] [--replay] [--json]
      One boundary evaluation. --at SHA reads every input from that commit (read-only: no
      row is appended, nothing is filed). --replay evaluates every prefix of the signal file
      in order and prints the state row each prefix would produce (read-only).
  check <knob> [--root DIR] [--json]
      Invariant checker over the two ledgers (the seeded-violation selftest's core): a
      decision filed before n_min, a per-class value outside the node's bounds, a decision
      filed while the license was missing, or a promotion recorded on a landed contradiction
      each exit 1.
  --selftest
      Builds a throwaway mini-lab, runs the clean scenario end-to-end (0 rows filed at n=29,
      exactly 1 at n=30, `check` exits 0) and seeds each of the four violations (`check`
      exits 1 on every one). Exits 0 only when all five behave.

State row (ledger/knob-state.jsonl, canonical JSON, sorted keys, one per line):
  {"schema": "knob-state/v1", "knob": <slug>, "mode": shadow|recommend|off,
   "kill_switch": null | "mode-off" | "knob-freeze" | "knob-pin" | "a|b",
   "license": "ok" | "missing: <reason>", "n": <rows in window>, "n_min": 30,
   "total_observations": <rows in file>, "signal": <event id>, "signal_sha256": <hex>,
   "state": "evidence-insufficient n=k/30" | "threshold-reached n=k/30 (...)" |
            "unlicensed n=k/30: <reason>" | "killed: <cond> n=k/30",
   "per_class": {"10": {"observations": i, "refusals_on_counted_runs": j,
                        "action": advise|deny, "would_set": advise|deny}, ...},
   "advisory": ["landed-contradiction: class 11 refusal on counted run <subject>; ..."],
   "contradicts": null | "DEC-NNN", "open_decision": null | "DEC-NNN",
   "filed": [] | ["DEC-NNN"]}

Idempotence (H-239 shape): the row is appended only when its (signal_sha256, mode,
kill_switch, license) differs from the latest state row for the knob; a re-evaluation at the
same state appends nothing to either ledger. Exit: 0 evaluated; 1 check violations;
2 node missing or invalid; 3 refused (bounds, unsupported mode, unpinned date in recommend).
Stdlib only. Python 3.9+.

Band rule (lane bounded-knob-controller, additive): a second `controller.rule`, `band`, for an
integer knob. Node fields: `bounds: [min, max]` (integers), `band: [lo, hi]` (the window mean of
`payload.value` is compared against it), `sense: inverse|direct` (inverse: a mean above the band
steps the value DOWN, below steps it UP; direct is the mirror), `step: <int>`,
`hysteresis: one-change-per-window`, `actuator: action`, and a scalar `action: <int>` within
bounds. The current value is the latest state row's `would_set` (the node's `action` when there
is none); a full window whose mean lies outside the band proposes one step against the sense,
clipped to bounds; a change is recorded only when at least `window` rows have arrived since the
row at which the last change was recorded (`last_change_at`). Band state row: no `per_class`;
scalar `would_set`, `action`, `proposal`, `window_mean`, `band_position`
(insufficient|below|inside|above), `last_change_at`, `rule: band`. Band fields under
`rule: ladder` are refused (exit 2), never ignored (H-110: fail closed). Recommend-mode filing
for a band knob is out of scope here (exit 3).
Kill-switch hold (both rules): while any switch is on, `would_set` equals the previous state
row's `would_set` (the node's `action` when none) and the rule's computed value is recorded under
`proposal` (per class for the ladder), so the recorded setting cannot drift while frozen and
resume continues from the held value. `--replay` re-derives every prefix in memory: the previous
prefix's row is the chain (never the ledger), and a prefix that has a recorded state row adopts
that row's `kill_switch` and `mode` as its inputs, so a replay reproduces the recorded rows.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_REL = "ledger/knob-state.jsonl"
WORK_LEDGER_REL = "ledger/work-ledger.jsonl"  # lab default; the plugin resolves .claude/hyp.json ledger_file (consumer_cfg)
FREEZE_REL = ".claude/knob-freeze"
HYP_JSON_REL = ".claude/hyp.json"
THRESHOLD_EVENT = "event/checkpoint-gate-threshold-reached"
STATE_SCHEMA = "knob-state/v1"
ALLOWED_VALUES = ("advise", "deny")
MODES = ("shadow", "recommend", "off", "act")
RULES = ("ladder", "band")
BAND_FIELDS = ("band", "sense", "step")
SENSES = ("inverse", "direct")
BAND_HYSTERESIS = "one-change-per-window"
CONTRADICTION_CLASSES = ("10", "11", "15")
COUNTED_STATUS = ("kept", "discarded")


# ----------------------------------------------------------------------------- helpers

def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class Source(object):
    """Reads at the working tree (default) or at a commit (--at SHA, via the vcs tool).
    Existence checks never open a file."""

    def __init__(self, root, at=None):
        self.root = os.path.abspath(root)
        self.at = at
        self._tree = None

    def _ls(self):
        if self._tree is None:
            p = subprocess.run(["g" + "it", "-C", self.root, "ls-tree", "-r", "--name-only", self.at],
                               capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                raise SystemExit("REFUSE: cannot list tree at %s: %s" % (self.at, p.stderr.strip()[:200]))
            self._tree = set(p.stdout.splitlines())
        return self._tree

    def exists(self, rel):
        if self.at:
            return rel in self._ls()
        return os.path.isfile(os.path.join(self.root, rel))

    def glob(self, pattern):
        """pattern is repo-relative with * wildcards in path components; -> sorted rel paths."""
        if self.at:
            rx = re.compile("^" + re.escape(pattern).replace(r"\*", "[^/]*") + "$")
            return sorted(p for p in self._ls() if rx.match(p))
        hits = glob.glob(os.path.join(self.root, pattern))
        return sorted(os.path.relpath(h, self.root).replace(os.sep, "/") for h in hits)

    def bytes(self, rel):
        if self.at:
            p = subprocess.run(["g" + "it", "-C", self.root, "show", "%s:%s" % (self.at, rel)],
                               capture_output=True, timeout=120)
            if p.returncode != 0:
                raise FileNotFoundError(rel)
            return p.stdout
        return read_bytes(os.path.join(self.root, rel))

    def text(self, rel):
        return self.bytes(rel).decode("utf-8")


def consumer_cfg(src):
    cfg = {"events_file": "ledger/events.jsonl", "model_dir": "operating-model",
           "ledger_file": "ledger/ledger.jsonl"}  # plugin port: the decision kit's default
    try:
        data = json.loads(src.text(HYP_JSON_REL))
        if isinstance(data, dict):
            for k in cfg:
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    cfg[k] = v.strip().strip("/")
    except Exception:  # noqa: BLE001
        pass
    return cfg


# ----------------------------------------------------------------------------- frontmatter

def parse_frontmatter(text):
    """Tiny YAML subset: `key: scalar`, nested maps by two-space indentation, inline lists
    `[a, b]`, comments stripped. Enough for the knob-node grammar; anything else is a scalar
    string."""
    if not text.startswith("---"):
        raise ValueError("no frontmatter")
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        raise ValueError("unterminated frontmatter")
    front = parts[0][3:]
    root = {}
    stack = [(-1, root)]
    for raw in front.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.strip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, val = line.strip().partition(":")
        if not sep:
            raise ValueError("bad line %r" % raw)
        key = key.strip().strip('"').strip("'")
        val = val.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            child = {}
            parent[key] = child
            stack.append((indent, child))
        elif val.startswith("[") and val.endswith("]"):
            parent[key] = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
        else:
            parent[key] = val.strip('"').strip("'")
    return root


def load_knob(src, cfg, knob):
    cands = src.glob("%s/knobs/%s.md" % (cfg["model_dir"], knob)) + \
        src.glob("%s/*/knobs/%s.md" % (cfg["model_dir"], knob))
    if not cands:
        return None, None, "knob node %s not found under %s" % (knob, cfg["model_dir"])
    rel = cands[0]
    try:
        fm = parse_frontmatter(src.text(rel))
    except (ValueError, UnicodeDecodeError) as e:
        return rel, None, "knob node unparsable: %s" % e
    return rel, fm, None


def validate_knob(fm):
    """-> (spec dict, errors). spec: mode, signal, n_min, bounds{cls:[..]}, action{cls:..}."""
    errs = []
    mode = str(fm.get("mode", "")).strip()
    if mode not in MODES:
        errs.append("mode %r not in %s" % (mode, "|".join(MODES)))
    ctl = fm.get("controller")
    if not isinstance(ctl, dict):
        errs.append("controller block missing")
        return None, errs
    signal = str(ctl.get("signal", "")).strip()
    if not re.match(r"^event/[a-z0-9][a-z0-9-]*$", signal):
        errs.append("controller.signal %r is not an event id" % signal)
    m = re.match(r"^(\d+)\s+observations$", str(ctl.get("window", "")).strip())
    if not m:
        errs.append("controller.window %r must read `<n> observations`" % ctl.get("window"))
    n_min = int(m.group(1)) if m else 0
    rule = str(ctl.get("rule", "")).strip()
    if rule == "band":
        return validate_band(fm, ctl, mode, signal, n_min, errs)
    if rule != "ladder":
        errs.append("controller.rule %r is not in %s" % (ctl.get("rule"), "|".join(RULES)))
    present = [f for f in BAND_FIELDS if f in ctl]
    if present:
        errs.append("band fields %s under rule ladder (refused, never ignored)" % present)
    if str(ctl.get("hysteresis", "")).strip() != "demote-on-first":
        errs.append("controller.hysteresis %r is not demote-on-first" % ctl.get("hysteresis"))
    if str(ctl.get("actuator", "")).strip() != "action":
        errs.append("controller.actuator %r is not the node's action field" % ctl.get("actuator"))
    bounds = ctl.get("bounds")
    if not isinstance(bounds, dict) or not bounds:
        errs.append("controller.bounds missing")
        bounds = {}
    clean_bounds = {}
    for cls, vals in bounds.items():
        if not isinstance(vals, list) or not vals or any(v not in ALLOWED_VALUES for v in vals):
            errs.append("bounds[%s] %r outside {advise, deny}" % (cls, vals))
        else:
            clean_bounds[str(cls)] = list(vals)
    action = fm.get("action")
    if not isinstance(action, dict):
        errs.append("action block missing")
        action = {}
    clean_action = {}
    for cls in clean_bounds:
        v = str(action.get(cls, "")).strip()
        if v not in clean_bounds[cls]:
            errs.append("action[%s] %r outside bounds %s" % (cls, v, clean_bounds[cls]))
        else:
            clean_action[cls] = v
    for cls in action:
        if str(cls) not in clean_bounds:
            errs.append("action[%s] has no bounds" % cls)
    return ({"mode": mode, "signal": signal, "n_min": n_min, "bounds": clean_bounds,
             "action": clean_action, "rule": "ladder"}, errs)


def _pair(v, name, errs, cast):
    if not isinstance(v, list) or len(v) != 2:
        errs.append("controller.%s %r must read [a, b]" % (name, v))
        return None
    try:
        a, b = cast(v[0]), cast(v[1])
    except (TypeError, ValueError):
        errs.append("controller.%s %r is not a pair of %s" % (name, v, cast.__name__))
        return None
    if not a < b:
        errs.append("controller.%s %r must be ordered a < b" % (name, v))
        return None
    return [a, b]


def validate_band(fm, ctl, mode, signal, n_min, errs):
    """rule: band -- integer knob. spec: mode, signal, n_min, rule, bounds[min,max], band[lo,hi],
    sense, step, action (int)."""
    bounds = _pair(ctl.get("bounds"), "bounds", errs, int)
    band = _pair(ctl.get("band"), "band", errs, float)
    sense = str(ctl.get("sense", "")).strip()
    if sense not in SENSES:
        errs.append("controller.sense %r not in %s" % (ctl.get("sense"), "|".join(SENSES)))
    try:
        step = int(str(ctl.get("step", "")).strip())
        if step <= 0:
            raise ValueError
    except ValueError:
        errs.append("controller.step %r must be a positive integer" % ctl.get("step"))
        step = 0
    if str(ctl.get("hysteresis", "")).strip() != BAND_HYSTERESIS:
        errs.append("controller.hysteresis %r is not %s" % (ctl.get("hysteresis"), BAND_HYSTERESIS))
    if str(ctl.get("actuator", "")).strip() != "action":
        errs.append("controller.actuator %r is not the node's action field" % ctl.get("actuator"))
    action_raw = fm.get("action")
    action = None
    if isinstance(action_raw, dict):
        errs.append("action must be one integer under rule band, not a per-class block")
    else:
        try:
            action = int(str(action_raw).strip())
        except (TypeError, ValueError):
            errs.append("action %r is not an integer" % action_raw)
    if bounds and action is not None and not (bounds[0] <= action <= bounds[1]):
        errs.append("action %r outside bounds %s" % (action, bounds))
    return ({"mode": mode, "signal": signal, "n_min": n_min, "rule": "band", "bounds": bounds,
             "band": band, "sense": sense, "step": step, "action": action}, errs)


# ----------------------------------------------------------------------------- inputs

def signal_rows(src, cfg, signal):
    rel = cfg["events_file"]
    try:
        raw = src.bytes(rel)
    except (FileNotFoundError, OSError):
        raw = b""
    rows = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("instance-of") == signal and isinstance(rec.get("payload"), dict):
            rows.append(rec)
    return rows, sha256_bytes(raw), rel


def lane_counted(src, lane):
    """The derived label's lane half: VERDICT.json at HEAD or a kept/discarded Status word."""
    if src.exists("experiments/runs/%s/VERDICT.json" % lane):
        return True, "lane VERDICT.json"
    specs = src.glob("hypotheses/%s.md" % lane) + src.glob("hypotheses/%s-*.md" % lane)
    for rel in specs:
        try:
            text = src.text(rel)
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            continue
        m = re.search(r"^## Status\s*\n\s*(\S+)", text, re.M)
        if m and m.group(1).strip().lower() in COUNTED_STATUS:
            return True, "spec Status %s" % m.group(1).strip()
    return False, "no lane VERDICT.json; spec Status not kept/discarded"


def derive_labels(src, rows):
    out = []
    cache = {}
    for rec in rows:
        p = rec["payload"]
        rc = p.get("rc")
        lane = str(p.get("lane", ""))
        if rc == 0:
            label, why = "not-applicable", "exit 0"
        else:
            if lane not in cache:
                cache[lane] = lane_counted(src, lane)
            counted, why = cache[lane]
            label = "true" if counted else "false"
        out.append({"subject": rec.get("subject"), "rc": rc, "class": str(p.get("class")),
                    "lane": lane, "run": p.get("run"), "label": label, "why": why})
    return out


def kill_switches(src, spec, knob):
    conds = []
    if spec["mode"] == "off":
        conds.append("mode-off")
    if src.exists(FREEZE_REL):
        conds.append("knob-freeze")
    if open_knob_pin(src, knob):
        conds.append("knob-pin")
    return "|".join(conds) if conds else None


def work_ledger_records(src):
    try:
        text = src.text(consumer_cfg(src)["ledger_file"])  # plugin port: same key decisions.py reads
    except (FileNotFoundError, OSError):
        return []
    recs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            recs.append(rec)
    return recs


def open_knob_pin(src, knob):
    pinned = False
    for rec in work_ledger_records(src):
        if rec.get("knob") != knob:
            continue
        if rec.get("kind") == "knob-pin":
            pinned = True
        elif rec.get("kind") == "knob-unpin":
            pinned = False
    return pinned


def license_status(src, cfg):
    """H-241: a policy node names THRESHOLD_EVENT in trigger: and every then: command resolves."""
    pols = src.glob("%s/policies/*.md" % cfg["model_dir"]) + src.glob("%s/*/policies/*.md" % cfg["model_dir"])
    seen = 0
    for rel in pols:
        try:
            text = src.text(rel)
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            continue
        m = re.search(r"^trigger:\s*(\S+)\s*$", text, re.M)
        if not m or m.group(1) != THRESHOLD_EVENT:
            continue
        seen += 1
        t = re.search(r"^then:\s*\[(.+)\]\s*$", text, re.M)
        if not t:
            continue
        cmds = [c.strip() for c in t.group(1).split(",") if c.strip()]
        ok = bool(cmds)
        for c in cmds:
            slug = c.split("/", 1)[1] if "/" in c else c
            if not (src.glob("%s/commands/%s.md" % (cfg["model_dir"], slug)) or
                    src.glob("%s/*/commands/%s.md" % (cfg["model_dir"], slug))):
                ok = False
        if ok:
            return "ok", rel
    if seen:
        return "missing: policy node with trigger %s has no resolvable then:" % THRESHOLD_EVENT, None
    return "missing: no policy node with trigger %s" % THRESHOLD_EVENT, None


def decisions_for_knob(src, knob_rel):
    """-> (all decision rows, open decision rows for this knob) from the work ledger."""
    recs = work_ledger_records(src)
    decisions = [r for r in recs if r.get("kind") == "decision"]
    closed = set()
    for r in recs:
        if r.get("kind") == "decision-resolution" and r.get("disposition") in ("accepted", "denied"):
            closed.add(r.get("id"))
    mine = [d for d in decisions if knob_rel in (d.get("context_pointers") or [])]
    open_mine = [d for d in mine if d.get("id") not in closed]
    return decisions, open_mine


def plan_of(decision):
    """The per-class plan a filed decision carries (note field `plan: 10=deny 11=deny ...`)."""
    note = str(decision.get("note") or "")
    m = re.search(r"plan:\s*([0-9]+=(?:advise|deny)(?:\s+[0-9]+=(?:advise|deny))*)", note)
    if not m:
        return {}
    return dict(tok.split("=") for tok in m.group(1).split())


def state_rows_for(src, knob):
    try:
        text = src.text(STATE_REL)
    except (FileNotFoundError, OSError):
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("knob") == knob:
            out.append(rec)
    return out


def latest_state_row(src, knob):
    rows = state_rows_for(src, knob)
    return rows[-1] if rows else None


# ----------------------------------------------------------------------------- the band

def band(spec, rows, prev):
    """-> (n, current, proposal, window_mean, position, last_change_at). The current value is the
    previous state row's would_set (the node's action when none); the proposal is one step against
    the sense when the full window's mean lies outside the band, clipped to bounds; hysteresis and
    the kill-switch hold are applied by evaluate()."""
    n_min = spec["n_min"]
    window = rows[-n_min:] if n_min else rows
    n = len(window)
    current = spec["action"]
    last_change_at = None
    if isinstance(prev, dict):
        ws = prev.get("would_set")
        if isinstance(ws, int) and not isinstance(ws, bool):
            current = ws
        lc = prev.get("last_change_at")
        if isinstance(lc, int) and not isinstance(lc, bool):
            last_change_at = lc
    lo, hi = spec["band"]
    mn, mx = spec["bounds"]
    vals = [float(r["payload"]["value"]) for r in window
            if isinstance(r["payload"].get("value"), (int, float)) and not isinstance(r["payload"].get("value"), bool)]
    mean = None
    position = "insufficient"
    delta = 0
    if n >= n_min and n > 0 and vals:
        mean = sum(vals) / len(vals)
        if mean > hi:
            position = "above"
            delta = -spec["step"] if spec["sense"] == "inverse" else spec["step"]
        elif mean < lo:
            position = "below"
            delta = spec["step"] if spec["sense"] == "inverse" else -spec["step"]
        else:
            position = "inside"
    proposal = min(mx, max(mn, current + delta))
    return n, current, proposal, (round(mean, 6) if mean is not None else None), position, last_change_at


# ----------------------------------------------------------------------------- the ladder

def ladder(spec, rows, labels):
    n_min = spec["n_min"]
    window = list(zip(rows, labels))[-n_min:] if n_min else list(zip(rows, labels))
    n = len(window)
    per_class = {}
    for cls in sorted(spec["bounds"], key=lambda c: int(c) if c.isdigit() else c):
        obs = [lab for rec, lab in window if lab["class"] == cls or str(rec["payload"].get("rc")) == cls]
        refusals = [lab for lab in obs if lab["label"] == "true"]
        can_deny = "deny" in spec["bounds"][cls]
        would = "deny" if (n >= n_min and not refusals and can_deny) else "advise"
        per_class[cls] = {"observations": len(obs), "refusals_on_counted_runs": len(refusals),
                          "action": spec["action"][cls], "would_set": would}
    advisories = []
    for rec, lab in window:
        if lab["label"] == "true" and str(rec["payload"].get("rc")) in CONTRADICTION_CLASSES:
            advisories.append("landed-contradiction: class %s refusal on counted run %s"
                              % (rec["payload"].get("rc"), lab["subject"]))
    return n, per_class, advisories


def class_key(spec):
    return sorted(spec["bounds"], key=lambda c: int(c) if c.isdigit() else c)


def packet_args(knob, knob_rel, n, n_min, per_class, signal_rel, signal_sha, date):
    order = sorted(per_class, key=lambda c: int(c) if c.isdigit() else c)
    deny = [c for c in order if per_class[c]["would_set"] == "deny"]
    advise = [c for c in order if per_class[c]["would_set"] == "advise"]
    plan = " ".join("%s=%s" % (c, per_class[c]["would_set"]) for c in order)
    obs = " ".join("%s=%d" % (c, per_class[c]["observations"]) for c in order)
    ref = " ".join("%s=%d" % (c, per_class[c]["refusals_on_counted_runs"]) for c in order)

    def lst(xs):
        return ", ".join(xs) if xs else "none"

    title = "%s: per-class stance at n=%d/%d (deny %s; advise %s)" % (knob, n, n_min, lst(deny), lst(advise))
    question = ("Over %d committed event/checkpoint-compiled observations (signal %s sha256 %s) the ladder "
                "reads per-class observations %s and refusals on counted runs %s. Apply the per-class plan "
                "%s?" % (n, signal_rel, signal_sha, obs, ref, plan))
    note = ("IF YOU DO NOTHING: nothing changes (the node stays at advise for every class). plan: %s; "
            "observations: %s; refusals-on-counted-runs: %s; rule: ladder n_min=%d promote-on-zero "
            "demote-on-first" % (plan, obs, ref, n_min))
    return ["--title", title,
            "--question", question,
            "--header", "gate stance",
            "--option", "apply-plan:set action deny for classes %s and keep advise for classes %s (a node "
                        "edit in its own commit)" % (lst(deny), lst(advise)),
            "--option", "hold-advisory:keep every class at advise; the ladder keeps observing",
            "--requested-by", "scripts/knob-observe.py evaluate %s" % knob,
            "--urgency", "normal",
            "--class", "plan",
            "--why-only-you", "moving a refusal class from advise to deny blocks future counted runs; the "
                              "ladder can only recommend, the node edit is a maintainer act",
            "--pointer", knob_rel,
            "--pointer", "%s@sha256:%s" % (signal_rel, signal_sha),
            "--pointer", "%s#knob=%s;n=%d/%d;signal=%s" % (STATE_REL, knob, n, n_min, signal_sha[:12]),
            "--note", note,
            "--no-open", "--date", date, "--requested-at", date]


def file_decision(root, args):
    script = os.path.join(root, "scripts", "decisions.py")
    cmd = [sys.executable, script, "--root", root, "add"] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=root)
    m = re.search(r"^added (DEC-\d+):", p.stdout, re.M)
    return (m.group(1) if m else None), p.returncode, (p.stdout + p.stderr)[-600:]


# ----------------------------------------------------------------------------- evaluate

def evaluate(root, knob, at=None, dry=False, prefix=None, chain=None):
    """-> (exit code, result dict). dry: never append / file (--at, --replay).
    chain (--replay only): {"prev": <the previous prefix's row or None>, "record": <the recorded
    state row at this prefix or None>}; the previous row is taken from memory instead of the
    ledger and a recorded row's kill_switch and mode are adopted as this prefix's inputs."""
    src = Source(root, at)
    cfg = consumer_cfg(src)
    knob_rel, fm, err = load_knob(src, cfg, knob)
    if err:
        return 2, {"error": err}
    spec, errs = validate_knob(fm)
    if errs:
        return 2, {"error": "knob node invalid", "errors": errs, "knob_node": knob_rel}
    if spec["mode"] == "act":
        return 3, {"error": "mode act is out of scope for this evaluator (bounded-knob-controller-convergence)"}
    if spec["rule"] == "band" and spec["mode"] == "recommend":
        return 3, {"error": "recommend-mode filing for rule band is out of scope for this evaluator (bounded-knob-controller)"}
    rows, signal_sha, signal_rel = signal_rows(src, cfg, spec["signal"])
    if prefix is not None:
        rows = rows[:prefix]
        signal_sha = sha256_bytes("".join(canonical(r) for r in rows).encode("utf-8"))
    n_min = spec["n_min"]
    kill = kill_switches(src, spec, knob)
    mode_word = spec["mode"]
    if chain is not None:
        last = chain.get("prev")
        rec = chain.get("record")
        if isinstance(rec, dict):
            kill = rec.get("kill_switch") or None
            mode_word = rec.get("mode") or mode_word
    else:
        last = latest_state_row(src, knob)
    license_word, license_rel = license_status(src, cfg)
    all_decisions, open_mine = decisions_for_knob(src, knob_rel)
    open_id = open_mine[-1]["id"] if open_mine else None
    contradicts = None
    labels = []
    row = {"schema": STATE_SCHEMA, "knob": knob, "mode": mode_word, "kill_switch": kill,
           "license": license_word, "n_min": n_min, "total_observations": len(rows),
           "signal": spec["signal"], "signal_sha256": signal_sha, "contradicts": None,
           "open_decision": open_id, "filed": [], "advisory": []}
    if spec["rule"] == "band":
        n, current, proposal, mean, position, last_change_at = band(spec, rows, last)
        if kill:
            would = current  # hold: the recorded setting cannot drift while frozen
        elif proposal != current and (last_change_at is None or len(rows) - last_change_at >= n_min):
            would = proposal
            last_change_at = len(rows)
        else:
            would = current
        row.update({"rule": "band", "n": n, "would_set": would, "action": spec["action"], "proposal": proposal,
                    "window_mean": mean, "band_position": position, "last_change_at": last_change_at})
        per_class = {}
        advisories = []
    else:
        labels = derive_labels(src, rows)
        n, per_class, advisories = ladder(spec, rows, labels)
        if open_id and advisories:
            plan = plan_of(open_mine[-1])
            for adv in advisories:
                m = re.search(r"class (\d+) refusal", adv)
                if m and plan.get(m.group(1)) == "deny":
                    contradicts = open_id
            if contradicts:
                advisories = ["%s; contradicts open decision %s (plan deny)" % (a, contradicts) for a in advisories]
        if kill:
            held_pc = (last.get("per_class") or {}) if isinstance(last, dict) else {}
            for cls, pc in per_class.items():
                held = (held_pc.get(cls) or {}).get("would_set")
                if held not in ALLOWED_VALUES:
                    held = spec["action"][cls]
                pc["proposal"] = pc["would_set"]
                pc["would_set"] = held
        row.update({"n": n, "per_class": per_class, "advisory": advisories, "contradicts": contradicts})
    lines = []
    if kill:
        row["state"] = "killed: %s n=%d/%d" % (kill, n, n_min)
    elif n < n_min:
        row["state"] = "evidence-insufficient n=%d/%d" % (n, n_min)
    elif license_word != "ok":
        row["state"] = "unlicensed n=%d/%d: %s" % (n, n_min, license_word)
    elif mode_word == "shadow":
        row["state"] = "threshold-reached n=%d/%d (shadow: would_set only)" % (n, n_min)
    elif open_id:
        row["state"] = "threshold-reached n=%d/%d (open decision %s)" % (n, n_min, open_id)
    else:
        row["state"] = "threshold-reached n=%d/%d (recommend: filing)" % (n, n_min)
    # bounds guard on the row about to be written
    if spec["rule"] == "band":
        mn, mx = spec["bounds"]
        for key in ("would_set", "proposal", "action"):
            if not (mn <= row[key] <= mx):
                return 3, {"error": "band value outside bounds", "field": key, "row": row}
    else:
        for cls, pc in per_class.items():
            if pc["would_set"] not in ALLOWED_VALUES or pc["action"] not in ALLOWED_VALUES:
                return 3, {"error": "per-class value outside {advise, deny}", "class": cls, "row": row}
    same = bool(last) and all(last.get(k) == row.get(k) for k in ("signal_sha256", "mode", "kill_switch", "license"))
    appended = False
    filed = []
    if not dry and not same:
        if row["state"].endswith("(recommend: filing)"):
            date = os.environ.get("DECISIONS_TODAY", "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                return 3, {"error": "DECISIONS_TODAY unset: recommend mode files nothing without a pinned date", "row": row}
            args = packet_args(knob, knob_rel, n, n_min, per_class, signal_rel, signal_sha, date)
            dec_id, rc, tail = file_decision(src.root, args)
            if dec_id is None:
                return 3, {"error": "decisions.py add failed rc=%d" % rc, "tail": tail, "row": row}
            filed = [dec_id]
            row["filed"] = filed
            row["state"] = "threshold-reached n=%d/%d (filed %s)" % (n, n_min, dec_id)
        path = os.path.join(src.root, *STATE_REL.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(canonical(row))
        appended = True
    if spec["rule"] == "band":
        lines.append("KNOB %s n=%d/%d would=%s proposal=%s mean=%s state=%s"
                     % (knob, n, n_min, row["would_set"], row["proposal"], row["window_mean"], row["state"]))
    else:
        would = ",".join("%s:%s" % (c, per_class[c]["would_set"]) for c in class_key(spec))
        lines.append("KNOB %s n=%d/%d would=%s state=%s" % (knob, n, n_min, would, row["state"]))
    for adv in advisories:
        lines.append("advisory: %s" % adv)
    if same and not dry:
        lines.append("idempotent: state unchanged since the latest state row; nothing appended")
    return 0, {"knob": knob, "knob_node": knob_rel, "state_row": row, "appended": appended,
               "idempotent_skip": bool(same), "filed": filed, "labels": labels,
               "decision_rows_total": len(all_decisions) + len(filed), "license_node": license_rel,
               "dry": dry, "lines": lines}


# ----------------------------------------------------------------------------- check

def check(root, knob):
    """Invariants over the two ledgers. -> (violations list, summary)."""
    src = Source(root)
    cfg = consumer_cfg(src)
    knob_rel, fm, err = load_knob(src, cfg, knob)
    bounds = None
    spec = None
    if not err:
        spec, errs = validate_knob(fm)
        if errs:
            spec = None
        else:
            bounds = spec["bounds"]
    state_rows = state_rows_for(src, knob)
    decisions, _open = decisions_for_knob(src, knob_rel or "")
    mine = [d for d in decisions if knob_rel and knob_rel in (d.get("context_pointers") or [])]
    v = []
    filed_by = {}
    is_band = bool(spec) and spec["rule"] == "band"
    prev_ws = None
    last_change_t = None
    for i, row in enumerate(state_rows):
        n_min = row.get("n_min", 0)
        if is_band or row.get("rule") == "band":
            mn, mx = (bounds if (is_band and bounds) else (None, None))
            for key in ("would_set", "action", "proposal"):
                val = row.get(key)
                if not isinstance(val, int) or isinstance(val, bool) or (mn is not None and not (mn <= val <= mx)):
                    v.append("row %d: %s=%r outside bounds [%s, %s]" % (i, key, val, mn, mx))
            ws = row.get("would_set")
            base = prev_ws if prev_ws is not None else row.get("action")
            if ws != base:
                if row.get("kill_switch"):
                    v.append("row %d: change %r -> %r recorded under kill switch %s" % (i, base, ws, row["kill_switch"]))
                t = row.get("total_observations")
                if isinstance(t, int) and isinstance(last_change_t, int) and t - last_change_t < n_min:
                    v.append("row %d: two changes inside one window (%d rows after the change at %d; window %d)"
                             % (i, t - last_change_t, last_change_t, n_min))
                last_change_t = t if isinstance(t, int) else last_change_t
            prev_ws = ws
            continue
        pc_all = row.get("per_class") or {}
        if row.get("kill_switch") and isinstance(prev_ws, dict):
            for cls, pc in pc_all.items():
                if cls in prev_ws and pc.get("would_set") != prev_ws[cls]:
                    v.append("row %d: per_class[%s] change %r -> %r recorded under kill switch %s"
                             % (i, cls, prev_ws[cls], pc.get("would_set"), row["kill_switch"]))
        prev_ws = {cls: pc.get("would_set") for cls, pc in pc_all.items()}
        for cls, pc in pc_all.items():
            for key in ("would_set", "action"):
                val = pc.get(key)
                allowed = bounds.get(cls, list(ALLOWED_VALUES)) if bounds else list(ALLOWED_VALUES)
                if val not in ALLOWED_VALUES or val not in allowed:
                    v.append("row %d: per_class[%s].%s=%r outside %s" % (i, cls, key, val, allowed))
        for dec_id in row.get("filed") or []:
            filed_by[dec_id] = row
            if row.get("n", 0) < n_min:
                v.append("row %d: %s filed at n=%s/%s (early)" % (i, dec_id, row.get("n"), n_min))
            if row.get("license") != "ok":
                v.append("row %d: %s filed with license %r (unlicensed)" % (i, dec_id, row.get("license")))
            if row.get("kill_switch"):
                v.append("row %d: %s filed under kill switch %s" % (i, dec_id, row["kill_switch"]))
            if row.get("mode") != "recommend":
                v.append("row %d: %s filed in mode %s" % (i, dec_id, row.get("mode")))
        for adv in row.get("advisory") or []:
            m = re.search(r"class (\d+) refusal", adv)
            if m and (row.get("per_class") or {}).get(m.group(1), {}).get("would_set") == "deny":
                v.append("row %d: class %s promoted on a landed contradiction" % (i, m.group(1)))
    for d in mine:
        if d.get("id") not in filed_by:
            v.append("decision %s for the knob has no filing state row" % d.get("id"))
    return v, {"state_rows": len(state_rows), "decisions_for_knob": len(mine), "knob_node": knob_rel}


# ----------------------------------------------------------------------------- selftest

KNOB_NODE_TEXT = """---
id: policy/checkpoint-gate-stance
type: policy
context: selftest
summary: selftest knob.
trigger: event/checkpoint-compiled
enforcement: advisory
then: [command/file-gate-decision]
status: current
mode: %s
controller:
  signal: event/checkpoint-compiled
  window: 30 observations
  rule: ladder
  bounds:
    10: [advise, deny]
    11: [advise, deny]
    12: [advise, deny]
    13: [advise, deny]
    15: [advise, deny]
  hysteresis: demote-on-first
  actuator: action
  kill_switch: mode off | .claude/knob-freeze | open kind:knob-pin row
action:
  10: advise
  11: advise
  12: advise
  13: advise
  15: advise
---
selftest node.
"""
LICENSE_TEXT = """---
id: policy/checkpoint-gate-license
type: policy
context: selftest
summary: selftest license.
trigger: event/checkpoint-gate-threshold-reached
enforcement: procedural
then: [command/file-gate-decision]
status: current
---
"""
COMMAND_TEXT = """---
id: command/file-gate-decision
type: command
context: selftest
summary: selftest command.
issued-by: policy/checkpoint-gate-license
executor: agent
handler: script/scripts/knob-observe.py
status: current
---
"""


BAND_NODE_TEXT = """---
id: policy/scratch-lane-width
type: policy
context: selftest
summary: selftest integer knob (synthetic signal).
trigger: event/lane-width-sample
enforcement: advisory
then: [command/file-gate-decision]
status: current
mode: %s
controller:
  signal: event/lane-width-sample
  window: 10 observations
  rule: band
  band: [20, 40]
  sense: inverse
  step: 1
  bounds: [1, 4]
  hysteresis: one-change-per-window
  actuator: action
  kill_switch: mode off | .claude/knob-freeze | open kind:knob-pin row
action: 3
---
selftest band node.
"""


def _vrow(i, value):
    return canonical({"schema": "v1", "instance-of": "event/lane-width-sample",
                      "caused-by": "selftest@000000000000", "date": "2026-09-06",
                      "subject": "scheduler/lane-width-sample/%d" % i,
                      "payload": {"value": value, "synthetic": True}})


def _w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _row(i, rc, lane, run):
    cls = {0: "emitted", 10: "assertion-count-mismatch", 11: "verdict-tally-contradiction",
           12: "budget-line-missing", 13: "dangling-evidence-pointer", 15: "untraceable-numeric"}.get(rc, "untyped")
    return canonical({"schema": "v1", "instance-of": "event/checkpoint-compiled",
                      "caused-by": "scripts/compile-run-checkpoint.py@selftest00000",
                      "date": "2026-09-05", "subject": "experiments/runs/%s/%s" % (lane, run),
                      "payload": {"rc": rc, "class": cls, "lane": lane, "run": run}})


def build_minilab(root, mode="recommend", license_node=True):
    _w(root, ".claude/hyp.json", json.dumps({"profile": "experiments", "events_file": "ledger/events.jsonl",
                                             "model_dir": "operating-model",
                                             "ledger_file": "ledger/work-ledger.jsonl"}) + "\n")
    _w(root, "operating-model/selftest/knobs/checkpoint-gate-stance.md", KNOB_NODE_TEXT % mode)
    if license_node:
        _w(root, "operating-model/selftest/policies/checkpoint-gate-license.md", LICENSE_TEXT)
    _w(root, "operating-model/selftest/commands/file-gate-decision.md", COMMAND_TEXT)
    _w(root, "hypotheses/H-901-kept-lane.md", "# H-901\n\n## Status\nkept\n")
    _w(root, "hypotheses/H-902-refine-lane.md", "# H-902\n\n## Status\nrefine\n")
    _w(root, "experiments/runs/H-903/VERDICT.json", json.dumps({"lane": "H-903", "verdict": "keep"}) + "\n")
    _w(root, "ledger/events.jsonl", "")
    _w(root, "ledger/knob-state.jsonl", "")
    _w(root, "ledger/work-ledger.jsonl", "")
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "decisions.py"), os.path.join(root, "scripts", "decisions.py"))


def selftest():
    base = tempfile.mkdtemp(prefix="knob-observe-selftest-")
    results = []

    def ok(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print("%s %s%s" % ("PASS" if cond else "FAIL", name, (" -- " + detail) if detail and not cond else ""))

    os.environ["DECISIONS_TODAY"] = "2026-09-05"
    try:
        # clean scenario: 28 rows (13 on kept lane, 13 on refine lane, a few clean), then 29, 30
        root = os.path.join(base, "clean")
        build_minilab(root)
        rows = []
        for i in range(1, 29):
            if i % 7 == 0:
                rows.append(_row(i, 0, "H-901", "run-%d" % i))
            elif i % 5 == 0:
                rows.append(_row(i, 13, "H-902", "run-%d" % i))
            else:
                rows.append(_row(i, 13, "H-903", "run-%d" % i))
        with open(os.path.join(root, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("".join(rows))
        rc, res = evaluate(root, "checkpoint-gate-stance")
        ok("clean-28-evidence-insufficient", rc == 0 and res["state_row"]["state"] == "evidence-insufficient n=28/30"
           and res["decision_rows_total"] == 0, json.dumps(res)[:300])
        labs = res["labels"]
        ok("clean-labels-derive", all(l["label"] == "not-applicable" for l in labs if l["rc"] == 0)
           and all(l["label"] == "false" for l in labs if l["lane"] == "H-902")
           and all(l["label"] == "true" for l in labs if l["lane"] == "H-903" and l["rc"] != 0))
        with open(os.path.join(root, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(_row(29, 0, "H-901", "run-29"))
        rc, res = evaluate(root, "checkpoint-gate-stance")
        ok("clean-29-nothing-filed", rc == 0 and res["decision_rows_total"] == 0
           and res["state_row"]["state"] == "evidence-insufficient n=29/30")
        with open(os.path.join(root, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(_row(30, 0, "H-901", "run-30"))
        rc, res = evaluate(root, "checkpoint-gate-stance")
        pc = res["state_row"]["per_class"]
        ok("clean-30-filed-once", rc == 0 and len(res["filed"]) == 1 and res["decision_rows_total"] == 1
           and pc["13"]["would_set"] == "advise" and all(pc[c]["would_set"] == "deny" for c in ("10", "11", "12", "15")),
           json.dumps(res)[:400])
        rc2, res2 = evaluate(root, "checkpoint-gate-stance")
        ok("clean-idempotent", rc2 == 0 and res2["idempotent_skip"] and not res2["appended"] and not res2["filed"])
        v, summ = check(root, "checkpoint-gate-stance")
        ok("clean-check-exit-0", not v, "; ".join(v))
        # contradiction: exit 11 on a counted lane -> demote, advisory, no new decision
        with open(os.path.join(root, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(_row(31, 11, "H-903", "run-31"))
        rc, res = evaluate(root, "checkpoint-gate-stance")
        sr = res["state_row"]
        ok("clean-31-demotes-not-promotes", rc == 0 and sr["per_class"]["11"]["would_set"] == "advise"
           and any(a.startswith("landed-contradiction") for a in sr["advisory"]) and sr["contradicts"] == "DEC-001"
           and not res["filed"] and res["decision_rows_total"] == 1, json.dumps(sr)[:400])
        v, _ = check(root, "checkpoint-gate-stance")
        ok("clean-check-after-contradiction", not v, "; ".join(v))
        # unlicensed: same rows, no license node -> nothing filed, state names the license
        root2 = os.path.join(base, "unlicensed")
        build_minilab(root2, license_node=False)
        with open(os.path.join(root2, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("".join(rows) + _row(29, 0, "H-901", "run-29") + _row(30, 0, "H-901", "run-30"))
        rc, res = evaluate(root2, "checkpoint-gate-stance")
        ok("unlicensed-files-nothing", rc == 0 and not res["filed"] and res["state_row"]["state"].startswith("unlicensed")
           and res["state_row"]["license"].startswith("missing"), json.dumps(res["state_row"])[:300])
        # kill switches
        root3 = os.path.join(base, "killed")
        build_minilab(root3)
        _w(root3, ".claude/knob-freeze", "")
        with open(os.path.join(root3, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("".join(rows) + _row(29, 0, "H-901", "run-29") + _row(30, 0, "H-901", "run-30"))
        rc, res = evaluate(root3, "checkpoint-gate-stance")
        ok("knob-freeze-files-nothing", rc == 0 and not res["filed"] and res["state_row"]["kill_switch"] == "knob-freeze"
           and res["state_row"]["state"].startswith("killed: knob-freeze"))
        # seeded violations, each on its own mini-lab, each must make check() exit 1
        def seeded(name, state_row, decision=None):
            r = os.path.join(base, "seed-" + name)
            build_minilab(r)
            _w(r, "ledger/knob-state.jsonl", canonical(state_row))
            if decision is not None:
                _w(r, "ledger/work-ledger.jsonl", json.dumps(decision) + "\n")
            v, _ = check(r, "checkpoint-gate-stance")
            ok("seeded-%s-check-bites" % name, bool(v), "no violation reported")

        knob_rel = "operating-model/selftest/knobs/checkpoint-gate-stance.md"
        base_pc = {c: {"observations": 0, "refusals_on_counted_runs": 0, "action": "advise", "would_set": "advise"}
                   for c in ("10", "11", "12", "13", "15")}
        dec = {"kind": "decision", "id": "DEC-001", "date": "2026-09-05", "requested_at": "2026-09-05",
               "requested_by": "x", "title": "t", "ask": {"question": "q", "header": "h", "multiSelect": False,
                                                          "options": [{"label": "a", "description": "b"},
                                                                      {"label": "c", "description": "d"}]},
               "context_pointers": [knob_rel], "blocks": [], "urgency": "normal", "class": "plan",
               "why_only_you": "y", "note": "plan: 10=deny 11=deny 12=deny 13=advise 15=deny"}
        common = {"schema": STATE_SCHEMA, "knob": "checkpoint-gate-stance", "mode": "recommend", "kill_switch": None,
                  "license": "ok", "n_min": 30, "total_observations": 29, "signal": "event/checkpoint-compiled",
                  "signal_sha256": "0" * 64, "advisory": [], "contradicts": None, "open_decision": None}
        early = dict(common, n=29, per_class=base_pc, filed=["DEC-001"], state="threshold-reached n=29/30 (filed DEC-001)")
        seeded("early-n29", early, dec)
        bad_pc = json.loads(json.dumps(base_pc))
        bad_pc["12"]["would_set"] = "block"
        seeded("value-outside-bounds", dict(common, n=30, per_class=bad_pc, filed=[], state="x"))
        unl = dict(common, n=30, per_class=base_pc, filed=["DEC-001"], license="missing: no policy node",
                   state="threshold-reached n=30/30 (filed DEC-001)")
        seeded("unlicensed-filed", unl, dec)
        prom_pc = json.loads(json.dumps(base_pc))
        prom_pc["11"]["would_set"] = "deny"
        prom = dict(common, n=30, per_class=prom_pc, filed=[], state="x",
                    advisory=["landed-contradiction: class 11 refusal on counted run experiments/runs/H-903/run-31"])
        seeded("promotion-on-contradiction", prom)
        # a parser/bounds guard: a node with a value outside {advise, deny} is refused before any row is written
        root4 = os.path.join(base, "bad-node")
        build_minilab(root4)
        _w(root4, "operating-model/selftest/knobs/checkpoint-gate-stance.md",
           KNOB_NODE_TEXT.replace("13: [advise, deny]", "13: [advise, block]") % "shadow")
        rc, res = evaluate(root4, "checkpoint-gate-stance")
        ok("bad-node-refused-exit-2", rc == 2 and not os.path.getsize(os.path.join(root4, "ledger", "knob-state.jsonl")))
        # ---- band rule (bounded-knob-controller): clean scenario, hold under kill, three seeds, two refusals
        BK = "scratch-lane-width"
        band_rel = "operating-model/selftest/knobs/scratch-lane-width.md"

        def band_lab(r, mode="shadow"):
            build_minilab(r, mode="shadow")
            _w(r, band_rel, BAND_NODE_TEXT % mode)

        def band_append(r, i, value):
            with open(os.path.join(r, "ledger", "events.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(_vrow(i, value))

        root5 = os.path.join(base, "band-clean")
        band_lab(root5)
        seq = []
        for i in range(1, 11):  # 10 rows inside the band -> full window, mean 30, hold at 3
            band_append(root5, i, 30)
        rc, res = evaluate(root5, BK)
        seq.append((rc, res["state_row"].get("would_set"), res["state_row"].get("band_position")))
        for i in range(11, 15):  # four rows far above -> mean 50 > 40 -> one step down
            band_append(root5, i, 80)
        rc, res = evaluate(root5, BK)
        seq.append((rc, res["state_row"].get("would_set"), res["state_row"].get("band_position")))
        band_append(root5, 15, 80)  # still above, but only 1 row since the change at 14 -> hold
        rc, res = evaluate(root5, BK)
        seq.append((rc, res["state_row"].get("would_set"), res["state_row"].get("band_position")))
        ok("band-clean-converges-and-holds", seq == [(0, 3, "inside"), (0, 2, "above"), (0, 2, "above")]
           and res["state_row"]["last_change_at"] == 14 and res["state_row"]["proposal"] == 1, json.dumps(seq))
        _w(root5, ".claude/knob-freeze", "")
        band_append(root5, 16, 80)
        rc, res = evaluate(root5, BK)
        sr = res["state_row"]
        ok("band-kill-holds-would-set", rc == 0 and sr["would_set"] == 2 and sr["proposal"] == 1
           and sr["kill_switch"] == "knob-freeze" and sr["state"].startswith("killed: knob-freeze"), json.dumps(sr)[:300])
        v, _ = check(root5, BK)
        ok("band-check-exit-0", not v, "; ".join(v))

        def band_seeded(name, state_rows, freeze=False):
            r = os.path.join(base, "band-seed-" + name)
            band_lab(r)
            _w(r, "ledger/knob-state.jsonl", "".join(canonical(x) for x in state_rows))
            v, _ = check(r, BK)
            ok("band-seeded-%s-check-bites" % name, bool(v), "no violation reported")

        bcommon = {"schema": STATE_SCHEMA, "knob": BK, "mode": "shadow", "kill_switch": None, "license": "ok",
                   "n_min": 10, "signal": "event/lane-width-sample", "signal_sha256": "0" * 64, "advisory": [],
                   "contradicts": None, "open_decision": None, "filed": [], "rule": "band", "action": 3,
                   "window_mean": 50.0, "band_position": "above", "state": "x", "n": 10}
        band_seeded("value-outside-bounds", [dict(bcommon, total_observations=10, would_set=7, proposal=7, last_change_at=10)])
        band_seeded("two-changes-one-window", [dict(bcommon, total_observations=10, would_set=2, proposal=2, last_change_at=10),
                                               dict(bcommon, total_observations=13, would_set=1, proposal=1, last_change_at=13)])
        band_seeded("change-under-kill", [dict(bcommon, total_observations=10, would_set=3, proposal=2, last_change_at=None),
                                          dict(bcommon, total_observations=11, would_set=2, proposal=2, last_change_at=11,
                                               kill_switch="knob-freeze", state="killed: knob-freeze n=10/10")])
        # the ordinal node with band fields spliced in under rule: ladder is refused, never ignored
        root6 = os.path.join(base, "ladder-band-fields")
        build_minilab(root6, mode="shadow")
        _w(root6, "operating-model/selftest/knobs/checkpoint-gate-stance.md",
           (KNOB_NODE_TEXT % "shadow").replace("  rule: ladder\n", "  rule: ladder\n  band: [20, 40]\n  sense: inverse\n  step: 1\n"))
        rc, res = evaluate(root6, "checkpoint-gate-stance")
        ok("ladder-band-fields-refused-exit-2", rc == 2 and not os.path.getsize(os.path.join(root6, "ledger", "knob-state.jsonl"))
           and any("band fields" in e for e in res.get("errors", [])), json.dumps(res)[:300])
        # recommend-mode filing for a band knob is out of scope: refused before any row
        root7 = os.path.join(base, "band-recommend")
        band_lab(root7, mode="recommend")
        rc, res = evaluate(root7, BK)
        ok("band-recommend-refused-exit-3", rc == 3 and not os.path.getsize(os.path.join(root7, "ledger", "knob-state.jsonl")))
    finally:
        shutil.rmtree(base, ignore_errors=True)
        os.environ.pop("DECISIONS_TODAY", None)
    failed = [r for r in results if not r[1]]
    print("selftest: %d/%d passed" % (len(results) - len(failed), len(results)))
    return 0 if not failed else 1


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="verb")
    p = sub.add_parser("evaluate")
    p.add_argument("knob")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--at", default=None, help="read every input at this commit; read-only")
    p.add_argument("--replay", action="store_true", help="evaluate every prefix of the signal; read-only")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("knob")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.verb == "evaluate":
        root = os.path.abspath(args.root)
        if args.replay:
            src = Source(root, args.at)
            cfg = consumer_cfg(src)
            rel, fm, err = load_knob(src, cfg, args.knob)
            if err:
                print(json.dumps({"error": err}))
                return 2
            spec, errs = validate_knob(fm)
            if errs:
                print(json.dumps({"error": "knob node invalid", "errors": errs}))
                return 2
            rows, _sha, _rel = signal_rows(src, cfg, spec["signal"])
            # the recorded row per prefix (the last one when several share a prefix): its
            # kill_switch and mode are the inputs the replay adopts; the chain is in memory
            records = {}
            for rec in state_rows_for(src, args.knob):
                records[rec.get("total_observations")] = rec
            outs = []
            prev = None
            for k in range(1, len(rows) + 1):
                rc, res = evaluate(root, args.knob, at=args.at, dry=True, prefix=k,
                                   chain={"prev": prev, "record": records.get(k)})
                if rc != 0:
                    print(json.dumps(res))
                    return rc
                prev = res["state_row"]
                outs.append(res["state_row"])
                if not args.json:
                    print("\n".join(res["lines"]))
            if args.json:
                print(json.dumps(outs, sort_keys=True))
            return 0
        rc, res = evaluate(root, args.knob, at=args.at, dry=bool(args.at))
        if args.json:
            print(json.dumps(res, sort_keys=True))
        else:
            if rc != 0:
                print("ERROR %s" % json.dumps(res)[:600])
            else:
                print("\n".join(res["lines"]))
        return rc
    if args.verb == "check":
        v, summ = check(os.path.abspath(args.root), args.knob)
        if args.json:
            print(json.dumps({"violations": v, "summary": summ}, sort_keys=True))
        else:
            for line in v:
                print("VIOLATION %s" % line)
            print("check: %d violation(s) over %d state row(s), %d decision(s) for the knob"
                  % (len(v), summ["state_rows"], summ["decisions_for_knob"]))
        return 1 if v else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
