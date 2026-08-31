#!/usr/bin/env python3
"""dispatch-gate.py -- the shared relaunch governor every driver layer consults
(see docs/workgraph.md).

Ported for hyp 0.2.0 from the source lab's live install scripts/dispatch-gate.py --
the On-keep landing of H-218 k-strikes-quarantine (kept 2x5/5 2026-08-29). The
decision logic (terminal census, streak, permit/deny, K-strikes fire condition,
exit codes) is the lab install's, which itself landed the kept reference
implementation with path/config adaptation only. Consumer adaptations here:
repo-root resolution (--root, then CLAUDE_PROJECT_DIR, then cwd), paths through
`.claude/hyp.json` (hypotheses_dir, runs_dir; the ledger through the decision
store's own resolver), the quarantine row type renamed needs-ian ->
needs-maintainer (consumer repos have no Ian), and invocation strings pointing at
the shipped tooling.

Surfaces read (all consumer-repo-relative):
  <hypotheses_dir>/H-NNN-*.md          a lane = a registered id; the spec's
                                       line-initial `## Status` word and its
                                       "Budget per run" dollar figure
  <runs_dir>/<lane>/chain-terminal.run<N>   terminal census ("0" = green)
  <runs_dir>/<lane>/VERDICT.json       completion evidence (or the spec's
                                       terminal status: kept/discarded/refined/
                                       retired families)
  dirname(ledger_file)/cost-records.jsonl   recorded spend (absent-tolerant)
  the decision store (scripts/decisions.py)  quarantine rows: kind:"decision"
                                       rows with type:"needs-maintainer",
                                       reason:"k-strikes-quarantine" --
                                       validated and appended through
                                       decisions.py's own primitives, so the
                                       DASHBOARD "DECISIONS WAITING" surface and
                                       decisions.html render them with no
                                       compiler change

Verbs. The exit codes are the consult contract for every driver layer -- the
Stop-hook dispatcher, detached chains, and the scheduled resume path:
  dispatch          print the dispatch decision JSON: actionable lanes (ordered,
                    failing-streak lanes first), quarantined rows (verbatim open
                    needs-maintainer decision rows), complete lanes
  request <lane>    permit/deny one relaunch request; exit 0 permit, exit 3 deny
  ingest <lane>     post-terminal bookkeeping after a permitted relaunch: the
                    gate recomputes the lane's consecutive non-green streak from
                    its terminal artifacts; at K consecutive non-green terminals
                    it appends + commits ONE quarantine decision row (K=2, the
                    lab's ratified constant)

Decisions are computed from committed artifacts, never from a bare exit code.
A lane with an open quarantine decision row is quarantined: it leaves the
dispatch list and every consult denies it until a human ruling closes the row
(python3 scripts/decisions.py resolve DEC-NNN --accept "relaunch"|--deny). A
"retire" ruling must also land the spec-status change that marks the lane
complete, else the lane re-enters the actionable list. Composes with, never
duplicates, the dispatch lister (scripts/dispatch-status.py): this gate bounds
RELAUNCH at dispatch; completion evidence is read-only here.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decisions  # the decision-store contract: schema, join, race-checked append

CONFIG_RELPATH = os.path.join(".claude", "hyp.json")
DEFAULTS = {
    "hypotheses_dir": "hypotheses",
    "runs_dir": "experiments/runs",
}
TERM_RE = re.compile(r"^chain-terminal\.run(\d+)$")
LANE_RE = re.compile(r"^(H-\d+)-.*\.md$")
COMPLETE_STATUSES = {"kept", "discarded", "refined", "retired"}
# K pin adopted from the lab's ratified constant (H-218's own counted lane,
# 2026-08-29)
K_STRIKES = 2
ROW_TYPE = "needs-maintainer"


def load_config(root):
    """DEFAULTS overlaid with the consumer's config file, if any. Never raises."""
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(root, CONFIG_RELPATH), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in DEFAULTS:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    cfg[key] = value.strip().strip("/")
    except Exception:
        pass
    return cfg


class Ctx(object):
    def __init__(self, root):
        self.root = root
        cfg = load_config(root)
        self.hyp = os.path.join(root, cfg["hypotheses_dir"])
        self.runs = os.path.join(root, cfg["runs_dir"])
        self.runs_rel = cfg["runs_dir"]
        self.ledger_rel = decisions.ledger_rel_for(root)
        self.costs = os.path.join(
            root, os.path.dirname(self.ledger_rel) or ".",
            "cost-records.jsonl")

    def root_ok(self):
        return (os.path.isdir(self.hyp) and os.path.isdir(self.runs)
                and os.path.isfile(os.path.join(self.root, self.ledger_rel)))


def spec_path(ctx, lane):
    for fn in sorted(os.listdir(ctx.hyp)):
        m = LANE_RE.match(fn)
        if m and m.group(1) == lane:
            return os.path.join(ctx.hyp, fn)
    return None


def lanes(ctx):
    out = set()
    for fn in os.listdir(ctx.hyp):
        m = LANE_RE.match(fn)
        if m:
            out.add(m.group(1))
    return sorted(out)


def terminals(ctx, lane):
    """Ordered (n, content) terminal census from artifacts; "0" = green."""
    d = os.path.join(ctx.runs, lane)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            m = TERM_RE.match(fn)
            if not m:
                continue
            try:
                content = open(os.path.join(d, fn),
                               encoding="utf-8").read().strip()
            except OSError:
                content = "unreadable"
            out.append((int(m.group(1)), content))
    out.sort(key=lambda t: t[0])
    return out


def streak(ctx, lane):
    s = 0
    for _n, content in reversed(terminals(ctx, lane)):
        if content == "0":
            break
        s += 1
    return s


def spec_status(ctx, lane):
    """Line-initial status word of the lane's hypothesis spec, or None."""
    p = spec_path(ctx, lane)
    if not p:
        return None
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r"^## Status\s*\n(\w+)", text, re.M)
    return m.group(1) if m else None


def verdict_present(ctx, lane):
    """Completion evidence: a VERDICT.json run artifact or the spec's
    line-initial terminal status."""
    if os.path.isfile(os.path.join(ctx.runs, lane, "VERDICT.json")):
        return True
    s = spec_status(ctx, lane)
    return bool(s) and s.lower() in COMPLETE_STATUSES


def rows(ctx):
    """Open quarantine decision rows -- the live quarantine ledger. A row
    counts while its derived status is open/commented; a closing resolution
    (accepted/denied) is the human ruling that removes it."""
    parsed = decisions.parse_ledger_v3(decisions.read_ledger(ctx.root))
    out = []
    for dec, _status, _chain in decisions.open_decisions(parsed):
        rec = dec["rec"]
        if isinstance(rec, dict) and rec.get("type") == ROW_TYPE:
            out.append(rec)
    return out


def lane_rows(ctx, lane):
    return [r for r in rows(ctx) if r.get("lane") == lane]


def spend(ctx, lane):
    total = 0.0
    if os.path.isfile(ctx.costs):
        for line in open(ctx.costs, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("lane") == lane:
                try:
                    total += float(rec.get("cost_usd") or 0.0)
                except (TypeError, ValueError):
                    pass
    return round(total, 4)


def declared_budget(ctx, lane):
    """The spec's declared per-run dollar budget: the first $N figure within
    the "Budget per run" clause; None when the spec declares no dollar figure
    (time-only or script-only budgets)."""
    p = spec_path(ctx, lane)
    if not p:
        return None
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r"Budget per run[\s\S]{0,200}?\$([0-9]+(?:\.[0-9]+)?)", text)
    return float(m.group(1)) if m else None


def facts(ctx, lane):
    t = terminals(ctx, lane)
    return {"lane": lane, "terminals": len(t), "streak": streak(ctx, lane),
            "next_run": len(t) + 1, "complete": verdict_present(ctx, lane),
            "quarantine_rows": len(lane_rows(ctx, lane)),
            "recorded_spend_usd": spend(ctx, lane),
            "declared_per_run_budget_usd": declared_budget(ctx, lane)}


def emit(obj, rc=0):
    print(json.dumps(obj, indent=1, sort_keys=True))
    return rc


def cmd_dispatch(ctx):
    actionable, complete = [], []
    q = rows(ctx)
    qlanes = set(r.get("lane") for r in q)
    for lane in lanes(ctx):
        if verdict_present(ctx, lane):
            complete.append(lane)
            continue
        if lane in qlanes:
            continue  # a quarantined lane leaves the dispatch list
        actionable.append(facts(ctx, lane))
    actionable.sort(key=lambda f: (-f["streak"], f["lane"]))
    return emit({"schema": 1, "verb": "dispatch", "actionable": actionable,
                 "quarantined": q, "complete": complete})


def cmd_request(ctx, lane):
    if lane not in lanes(ctx):
        return emit({"schema": 1, "verb": "request", "lane": lane,
                     "permit": False, "reason": "unknown lane"}, 3)
    f = facts(ctx, lane)
    f["verb"] = "request"
    lr = lane_rows(ctx, lane)
    if lr:
        f["permit"] = False
        f["reason"] = ("quarantined: open %s decision row present "
                       "(human ruling required before any relaunch: "
                       "scripts/decisions.py resolve %s)"
                       % (ROW_TYPE,
                          ", ".join(str(r.get("id")) for r in lr)))
        f["quarantine_decision_rows"] = lr
        return emit(f, 3)
    if f["complete"]:
        f["permit"] = False
        f["reason"] = "complete: terminal verdict evidence present"
        return emit(f, 3)
    f["permit"] = True
    f["reason"] = "actionable: no verdict evidence, no quarantine row"
    return emit(f, 0)


def cmd_ingest(ctx, lane):
    if lane not in lanes(ctx):
        return emit({"schema": 1, "verb": "ingest", "lane": lane,
                     "error": "unknown lane"}, 2)
    t = terminals(ctx, lane)
    s = streak(ctx, lane)
    out = {"schema": 1, "verb": "ingest", "lane": lane, "terminals": len(t),
           "streak": s, "quarantined_now": False}
    # ---- K-STRIKES RULE (H-218, kept 2x5/5): a lane at K consecutive
    # non-green terminals is quarantined -- one decision row is appended to
    # the work ledger and committed, the lane leaves the dispatch list, and
    # every later consult honors the committed row until a human ruling
    # closes it. ----
    if s >= K_STRIKES and not lane_rows(ctx, lane):
        import subprocess
        state = {}
        sp = os.path.join(ctx.runs, lane, "LANE-STATE.json")
        if os.path.isfile(sp):
            try:
                state = json.load(open(sp, encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
        if not isinstance(state, dict):
            state = {}
        strike_names = ["run%d" % n for n, _c in t[-s:]]
        halt = str(state.get("halt_reason") or "")[:200]
        sp_rel = spec_path(ctx, lane)
        sp_rel = (os.path.relpath(sp_rel, ctx.root).replace(os.sep, "/")
                  if sp_rel else "hypotheses/ (spec file not found)")
        parsed = decisions.parse_ledger_v3(decisions.read_ledger(ctx.root))
        row = {
            "kind": "decision",
            "id": decisions.next_free_id(parsed),
            "date": decisions.today_str(),
            "requested_at": decisions.today_str(),
            "requested_by": ("scripts/dispatch-gate.py ingest %s "
                             "(k-strikes relaunch governor, H-218)" % lane),
            "title": ("QUARANTINED: %s (k-strikes, %d consecutive "
                      "non-green terminals)" % (lane, s)),
            "ask": {
                "question": ("Lane %s hit %d consecutive non-green terminals "
                             "(%s)%s. The dispatch gate now denies every "
                             "relaunch consult for it. Relaunch it after a "
                             "fix, or retire it from the dispatch list?"
                             % (lane, s, ", ".join(strike_names),
                                ("; last halt_reason: %s" % halt) if halt
                                else "")),
                "header": "Quarantine",
                "multiSelect": False,
                "options": [
                    {"label": "relaunch",
                     "description": ("closing this row lifts the quarantine -- "
                                     "the next gate consult permits run %d "
                                     "(land the fix first)" % (len(t) + 1))},
                    {"label": "retire",
                     "description": ("keep it out of dispatch: land the "
                                     "spec-status ruling (discard/refine/"
                                     "supersede) that marks the lane "
                                     "complete, then close this row")},
                ],
            },
            "context_pointers": [
                sp_rel,
                "%s/%s/ (chain-terminal.*, LANE-STATE.json)"
                % (ctx.runs_rel, lane)],
            "blocks": ["lane %s (quarantined from dispatch)" % lane],
            "urgency": "normal",
            "class": "spend",
            "why_only_you": ("K-strikes converts persistent failure into a "
                             "human ruling: whether a %d-times-failed lane "
                             "burns another run-budget is not the machinery's "
                             "call (H-218; K=%d)" % (s, K_STRIKES)),
            # gate join + evidence fields (the kept fixture row schema):
            "type": ROW_TYPE,
            "lane": lane,
            "reason": "k-strikes-quarantine",
            "k": K_STRIKES,
            "strikes": s,
            "strike_terminals": strike_names,
            "halt_reason": halt,
            "declared_per_run_budget_usd": declared_budget(ctx, lane),
            "recorded_spend_usd": spend(ctx, lane),
            "action_required": ("quarantined after %d consecutive non-green "
                                "terminals; human ruling required before "
                                "any relaunch" % s),
        }
        errs = decisions.validate_decision(row)
        if errs:
            out["error"] = ("quarantine row failed decision-schema "
                            "validation: %s" % "; ".join(errs))
            return emit(out, 2)
        decisions.append_line(ctx.root, row)
        env = dict(os.environ)
        env.update({"GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "commit.gpgsign",
                    "GIT_CONFIG_VALUE_0": "false"})
        subprocess.run(["git", "-C", ctx.root, "add", "--", ctx.ledger_rel],
                       check=True, capture_output=True, env=env)
        subprocess.run(["git", "-C", ctx.root, "commit", "-q", "-m",
                        "quarantine: %s (k-strikes, %d consecutive non-green "
                        "terminals) -- %s"
                        % (lane, s, row["id"]),
                        "--", ctx.ledger_rel], check=True,
                       capture_output=True, env=env)
        out["quarantined_now"] = True
        out["quarantine_row"] = row
        # The add-path surface behavior: recompile DASHBOARD/decisions.html
        # and open-once + notify (once-per-id guard inside proactive-open.sh).
        decisions.run_proactive(ctx.root)
    # ---- END K-STRIKES RULE ----
    return emit(out, 0)


def main(argv):
    root = None
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 >= len(argv):
            print(json.dumps({"error": "--root needs a value"}))
            return 2
        root = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if not root:
        env_root = os.environ.get("CLAUDE_PROJECT_DIR")
        root = env_root if env_root and os.path.isdir(env_root) \
            else os.getcwd()
    ctx = Ctx(os.path.abspath(root))
    if not ctx.root_ok():
        print(json.dumps({"error": "not an initialized experiments root "
                                   "(expected %s/, %s/, and the configured "
                                   "ledger under the repo root; run "
                                   "/hyp:init --profile experiments first)"
                                   % (os.path.basename(ctx.hyp),
                                      ctx.runs_rel)}))
        return 2
    if len(argv) >= 1 and argv[0] == "dispatch":
        return cmd_dispatch(ctx)
    if len(argv) >= 2 and argv[0] == "request":
        return cmd_request(ctx, argv[1])
    if len(argv) >= 2 and argv[0] == "ingest":
        return cmd_ingest(ctx, argv[1])
    print(json.dumps({"error": "usage: dispatch-gate.py [--root <repo>] "
                               "dispatch | request <lane> | ingest <lane>"}))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
