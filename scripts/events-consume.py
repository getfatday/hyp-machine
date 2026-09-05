#!/usr/bin/env python3
"""events-consume.py -- the licensed event consumer (H-241), consumer port.

PROVENANCE -- COUNTED port of the kept H-241 fixture consumer
(experiments/runs/H-241/fixture/template/consumer/consumer.py +
consumer/DEGRADE-RULE.md in the source lab; H-241-event-action-licensing KEPT
2026-09-02, 2x5/5: the license-checked arm took exactly the 8 licensed actions
and zero unlicensed ones across a 24-event corpus with 8 bait records, while
the act-on-observation arm swallowed all 8 bait). The license rule and degrade
rule ship exactly as kept:

  A record licenses an ACTION iff a committed policy node's `trigger:` names
  the record's event id AND every command id in its `then:` resolves to a
  committed command node. Anything else degrades deterministically:
    1. payload `severity` in {warn, error}  -> ADVISORY surface
    2. else payload `kind` == "status"      -> READ-MODEL surface
    3. else                                 -> NOTHING (deliberately dropped)
  A command id appearing in a record's body or payload is a MENTION, not a use
  -- it licenses nothing (H-233's mention-vs-use law). A policy whose `then:`
  does not fully resolve licenses nothing: no resolvable then, no action.

Actions are STUB-RECORDED, never executed -- the keep measured routing, not
execution; binding `then:` to real handlers is the lab's registered successor
question and ships only on ITS keep. Named divergences from the counted copy
(consumer resolution only):
  - records are unified-stream v1 rows ({schema, instance-of, ...}); the
    fixture corpus's fields map as event := instance-of, severity :=
    payload.severity, kind := payload.kind, seq := 1-based line number;
  - policy nodes load from the consumer model the way the shipped PreToolUse
    interpreter loads them (<model_dir>/policies/*.md and
    <model_dir>/*/policies/*.md), reading `trigger:`/`then:` frontmatter
    (scalar or [flow list]); command ids resolve to <model_dir>/commands/<slug>.md
    or <model_dir>/*/commands/<slug>.md;
  - the fixture's --check off arm (act-on-observation) is NOT shipped: it was
    the experiment's baseline failure mode, not a capability;
  - default input is the repo's configured event stream; --corpus overrides;
    surfaces land under --out (default runtime .claude/events-consume/, never
    committed);
  - experiments-profile gate (typed refusal, exit 3).

Determinism (kept contract): records in file order, policy files in sorted
order, no timestamps or absolute paths in any surface line; re-running over
identical inputs is byte-identical.
"""
import argparse
import glob as _glob
import json
import os
import re
import sys

FIELD_RE = re.compile(r"^(trigger|then):\s*(.+?)\s*$")


def _cfg(root):
    cfg = {"events_file": "ledger/events.jsonl", "model_dir": "operating-model",
           "profile": "capture"}
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in cfg:
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    cfg[key] = v.strip().strip("/") if key != "profile" else v.strip()
    except Exception:
        pass
    return cfg


def _as_ids(value):
    value = value.strip()
    if value.startswith("["):
        return [x.strip().strip("\"'") for x in value[1:-1].split(",") if x.strip()]
    return [value.strip().strip("\"'")]


def load_policies(root, model_dir):
    """[(policy_id, [trigger event ids], [then command ids])], sorted by file."""
    base = os.path.join(root, model_dir)
    paths = []
    for pattern in ("policies/*.md", os.path.join("*", "policies/*.md")):
        paths.extend(_glob.glob(os.path.join(base, pattern)))
    policies = []
    for path in sorted(paths):
        pid = "policy/" + os.path.basename(path)[:-len(".md")]
        triggers, then = [], []
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        front = text[3:end] if end != -1 else text[3:]
        for line in front.splitlines():
            m = FIELD_RE.match(line.strip())
            if not m:
                continue
            if m.group(1) == "trigger":
                triggers = _as_ids(m.group(2))
            else:
                then = _as_ids(m.group(2))
        if triggers:
            policies.append((pid, triggers, then))
    return policies


def command_resolves(root, model_dir, command_id):
    if not command_id.startswith("command/"):
        return False
    slug = command_id.split("/", 1)[1] + ".md"
    base = os.path.join(root, model_dir)
    if os.path.isfile(os.path.join(base, "commands", slug)):
        return True
    return bool(_glob.glob(os.path.join(base, "*", "commands", slug)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd(),
                    help="consumer repo root (default: cwd)")
    ap.add_argument("--corpus", default=None,
                    help="records file (default: the repo's configured event stream)")
    ap.add_argument("--out", default=None,
                    help="surface dir (default: <root>/.claude/events-consume)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    cfg = _cfg(root)

    if cfg["profile"] not in ("experiments", "modeling"):
        print(json.dumps({"status": "refused",
                          "reason": "event consumption is experiments-profile "
                                    "machinery; profile is %r" % cfg["profile"]}))
        return 3

    corpus = args.corpus or os.path.join(root, *cfg["events_file"].split("/"))
    out_dir = args.out or os.path.join(root, ".claude", "events-consume")
    if not os.path.isfile(corpus):
        print(json.dumps({"status": "no-stream",
                          "reason": "no records at %s" % corpus}))
        return 0

    os.makedirs(out_dir, exist_ok=True)
    surfaces = {}
    for name in ("actions", "advisories", "read-model", "nothing"):
        surfaces[name] = open(os.path.join(out_dir, name + ".log"), "w",
                              encoding="utf-8")

    policies = load_policies(root, cfg["model_dir"])
    by_event = {}
    for pid, triggers, then in policies:
        for trig in triggers:
            by_event.setdefault(trig, []).append((pid, then))

    counts = {"records": 0, "actions": 0, "advisories": 0,
              "read-model": 0, "nothing": 0}

    with open(corpus, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            counts["records"] += 1
            seq = "%03d" % lineno
            event = rec.get("instance-of") or rec.get("event") or "?"
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}

            licensing = []
            for pid, then in by_event.get(event, []):
                if then and all(command_resolves(root, cfg["model_dir"], c)
                                for c in then):
                    licensing.append((pid, then))
            if licensing:
                for pid, then in licensing:
                    for cmd in then:
                        surfaces["actions"].write(
                            "ACTION seq=%s event=%s policy=%s command=%s "
                            "mode=stub-recorded\n" % (seq, event, pid, cmd))
                        counts["actions"] += 1
            else:
                # unlicensed: degrade per the committed rule (kept H-241)
                severity = rec.get("severity") or payload.get("severity")
                kind = rec.get("kind") or payload.get("kind")
                if severity in ("warn", "error"):
                    surfaces["advisories"].write(
                        "ADVISORY seq=%s event=%s reason=unlicensed severity=%s\n"
                        % (seq, event, severity))
                    counts["advisories"] += 1
                elif kind == "status":
                    surfaces["read-model"].write(
                        "READMODEL seq=%s event=%s reason=unlicensed kind=status\n"
                        % (seq, event))
                    counts["read-model"] += 1
                else:
                    surfaces["nothing"].write(
                        "NOTHING seq=%s event=%s reason=unlicensed\n" % (seq, event))
                    counts["nothing"] += 1

    for fh in surfaces.values():
        fh.close()
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"arm": "licensed", "counts": counts}, f, sort_keys=True, indent=1)
        f.write("\n")
    print("consumer done arm=licensed records=%d actions=%d"
          % (counts["records"], counts["actions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
