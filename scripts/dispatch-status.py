#!/usr/bin/env python3
"""dispatch-status.py -- the ranked next-item dispatch surface (see docs/workgraph.md).

Ported for hyp 0.2.0 from the source lab's live install `scripts/wave-status.py
--dispatch` (the joined landing of three counted keeps: H-213 stop-driver-unattended,
kept 2x5/5 2026-08-29; H-215 claim-join-two-writers, kept 2x5/5 2026-08-29; H-217
reboot-relaunch, kept 2x5/5 2026-08-29). The join logic, close rule, liveness law, and
output schema are the lab install's; the ONE consumer adaptation is the eligible-item
source: the lab enumerates its release-train wave plan (lab-only infrastructure), this
port enumerates the consumer's own hypotheses corpus -- every registered spec whose
COMMITTED line-initial `## Status` word is not yet terminal (kept/discarded; refine
reruns). Paths resolve through `.claude/hyp.json` (hypotheses_dir, runs_dir).

The close rule (H-213, verbatim in effect): an item is OPEN until its committed exit
artifact is visible at the requested commit (HEAD by default) -- here the line-initial
spec Status verdict. Detection is COMMITTED-only: the working tree never counts, so no
promise string or uncommitted edit can close an item. This empty-dispatch artifact check
is the shared exit condition for every driver layer (the Stop dispatcher, detached
chains, cold-start re-readers, scheduled resume firings).

The dispatch list is CLAIM-JOINED (H-215): each open item's live claim surface --
<runs_dir>/<id>/LANE-STATE.json, the H-216 liveness contract (heartbeat_unix age against
ttl_s; ttl_s = 1800 pinned by the lab's constants ruling; committed schema fields, never
file mtime, never a process table) -- filters every open item whose claim heartbeat is
fresher than the TTL: fresh-claimed items are skipped (reported under claimed_fresh,
never dispatched), stale-claimed items surface again as actionable, so concurrent
readers are steered to disjoint unclaimed items at the list, not the lock. A claim can
only defer an item within its TTL, never close it (--at replays the exit check only).
Refusal-then-re-dispatch rule (binding, every driver layer): if your claim is refused
with the typed exit 3 (scripts/lane-takeover.py -- the lane is fresh-claimed by another
executor), re-run this dispatch -- the freshly claimed item is now filtered out -- and
claim the NEW top item instead.

The dispatch list is also ORPHAN-JOINED (H-217): for every open item whose LANE-STATE
says state=running, the LIVE PID TABLE on THIS host is probed. A lane whose recorded pid
is DEAD on this host is an ORPHAN -- the post-reboot class -- and stays ACTIONABLE with
a recovery verb: `land-terminal` when the newest chain-terminal.run* holds a nonzero rc
(the chain died before landing), else `relaunch`. The orphan join is TTL-FREE -- a dead
pid trumps a fresh heartbeat -- and pid-local: foreign-host or pid-less running lanes
are never orphaned from here (they fall through to the heartbeat join). A running lane
whose pid is ALIVE with a fresh heartbeat is LIVE and is never dispatched. Detection
here is pid-based and immediate, but ADOPTION still goes through the claim door
(scripts/lane-takeover.py), which grants only on heartbeat expiry.

REFILL (0.3.0; ported from the source lab's throughput-floor patch to wave-status.py,
2026-09-02): "N open" can be a starved frontier -- an open item whose committed
`## Status` BLOCK carries a PARKED / BLOCKED-* / COUNTING marker (the markers live in
the status comment, invisible to the line-initial status word) is open but not
actionable. When fewer than 2 open items are actionable (orphans always count
actionable -- they carry recovery verbs), the dispatch appends a REFILL section
listing the licensed follow-up lanes of the consumer's FOLLOWUPS surface
(`followups_file` in .claude/hyp.json, default hypotheses/FOLLOWUPS.md; grammar in
docs/workgraph.md). Followups are read from the LIVE working tree like the claim join:
they are advisory refill surface, never exit artifacts. REFILL is uncounted
build/register dispatch only -- it never reorders counted runs and never closes an
item. Consumer adaptation from the lab patch: the lab also refills from its release
train's next queued wave; a consumer corpus is flat, so the FOLLOWUPS surface is the
whole refill source.

Usage (repo root: --root, then CLAUDE_PROJECT_DIR, then cwd):
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch-status.py" [--json] [--at <sha>]

Exit 0 with the dispatch (text or --json); exit 3 when the root has no commits.
Read-only over the repo; stdlib only.
"""
import argparse
import glob
import json
import os
import platform
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "hooks", "scripts"))
from hyp_status import TERMINAL, canonical_status  # noqa: E402  (shared status reader)

CONFIG_RELPATH = os.path.join(".claude", "hyp.json")
DEFAULTS = {
    "hypotheses_dir": "hypotheses",
    "runs_dir": "experiments/runs",
    "followups_file": "hypotheses/FOLLOWUPS.md",
}
# TERMINAL (kept, discarded, refined-into) is the shared canonical set from
# hooks/scripts/hyp_status.py; the status word is read through its canonicalizer
# so consumer spellings (keep, discard, refined-into:, refined (into ...), any
# casing) close an item the way the author meant. refine reruns.
# REFILL masking (lab throughput-floor patch, 2026-09-02): open items whose committed
# status BLOCK carries one of these markers are excluded from the actionable frontier
# count (never from the open list itself) -- the markers live in the status comment,
# invisible to the line-initial \w+ status word.
NON_ACTIONABLE_RE = re.compile(r"\b(PARKED|BLOCKED[A-Z-]*|COUNTING)\b")
# heartbeat TTL pin (H-215/H-216 shared constant, ratified by their own counted
# lanes in the source lab, 2026-08-29)
TTL_S = 1800
ID_RE = re.compile(r"^(H-\d+)-.*\.md$")


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


def resolve_root(arg_root):
    if arg_root:
        return os.path.abspath(arg_root)
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root and os.path.isdir(env_root):
        return env_root
    return os.getcwd()


class Ctx(object):
    """Resolved paths for one invocation (the lab's module constants, made
    consumer-configurable)."""

    def __init__(self, root):
        self.root = root
        cfg = load_config(root)
        self.hyp_rel = cfg["hypotheses_dir"]
        self.runs_rel = cfg["runs_dir"]
        self.followups_rel = cfg["followups_file"]


# --- claim join (H-215 kept; logic unchanged from the lab install) -------------------

def claim_state(ctx, lane_id):
    """One item's live-claim disposition -- ('fresh' | 'stale' | None, owner).
    Liveness per the H-216 consult contract (scripts/lane-takeover.py):
    <runs_dir>/<id>/LANE-STATE.json committed fields only -- heartbeat_unix age
    against ttl_s -- never file mtime, never a process table. A missing,
    unreadable, or heartbeat-less claim never hides an item (it reads as
    unclaimed)."""
    cp = os.path.join(ctx.root, ctx.runs_rel, lane_id, "LANE-STATE.json")
    try:
        with open(cp, encoding="utf-8") as f:
            claim = json.load(f)
    except (OSError, ValueError):
        return None, None
    if not isinstance(claim, dict):
        return None, None
    owner = claim.get("executor")
    hb = claim.get("heartbeat_unix")
    if not isinstance(hb, int):
        return None, owner
    ttl = claim.get("ttl_s")
    if not isinstance(ttl, int):
        ttl = TTL_S
    age = int(time.time()) - hb
    return ("fresh" if age <= ttl else "stale"), owner


# --- orphan join (H-217 kept; logic unchanged from the lab install) ------------------

def pid_alive(pid):
    """Fixture-verbatim pid probe: True/False on a decisive answer, None when
    the pid is absent, malformed, or unprobeable."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def halt_terminal(ctx, lane_id):
    """The newest <runs_dir>/<id>/chain-terminal.run* holding a nonzero integer
    rc, if any."""
    hits = []
    lane_dir = os.path.join(ctx.root, ctx.runs_rel, lane_id)
    for p in sorted(glob.glob(os.path.join(lane_dir, "chain-terminal.run*"))):
        m = re.match(r"^chain-terminal\.run(\d+)$", os.path.basename(p))
        if not m:
            continue
        try:
            val = open(p, encoding="utf-8").read().strip()
        except OSError:
            continue
        if re.match(r"^\d+$", val) and val != "0":
            hits.append((int(m.group(1)), os.path.basename(p), val))
    if not hits:
        return None
    hits.sort()
    return hits[-1]


def orphan_state(ctx, lane_id):
    """H-217 orphan join over one open item's LANE-STATE -> a disposition dict
    ({'disposition': 'orphan', 'verb': 'relaunch'|'land-terminal', 'pid': ...,
    'detail': ...} | {'disposition': 'live', 'pid': ..., 'detail': ...}) or
    None where the pid table is not decisive (no/unreadable LANE-STATE, state
    != running, foreign host, unverifiable pid) -- those cases fall through to
    the H-215 heartbeat join, so a foreign-host or pid-less lane is NEVER
    orphaned from here."""
    cp = os.path.join(ctx.root, ctx.runs_rel, lane_id, "LANE-STATE.json")
    try:
        with open(cp, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or st.get("state") != "running":
        return None
    host = st.get("host")
    if host and host != platform.node():
        return None   # pid table not meaningful here (HOLD foreign-host)
    alive = pid_alive(st.get("pid"))
    if alive is False:
        ht = halt_terminal(ctx, lane_id)
        if ht is not None:
            return {"disposition": "orphan", "verb": "land-terminal",
                    "pid": st.get("pid"),
                    "detail": "state=running pid=%s DEAD; halt terminal %s rc "
                              "%s unlanded (killed between terminal write and "
                              "state write)" % (st.get("pid"), ht[1], ht[2])}
        return {"disposition": "orphan", "verb": "relaunch",
                "pid": st.get("pid"),
                "detail": "state=running pid=%s DEAD; run %s in flight when "
                          "the chain died" % (st.get("pid"), st.get("run"))}
    if alive is True:
        return {"disposition": "live", "pid": st.get("pid"),
                "detail": "chain running -- pid %s alive, heartbeat %s; never "
                          "adopt" % (st.get("pid"), st.get("heartbeat_unix"))}
    return None


TAKEOVER = 'python3 "$CLAUDE_PLUGIN_ROOT"/scripts/lane-takeover.py'
REDISPATCH = 'python3 "$CLAUDE_PLUGIN_ROOT"/scripts/dispatch-status.py'


def next_action_text(lane_id, verb):
    """The orphan recovery verbs, consumer-surface commands (lab
    next_action_text; claim/relaunch/land targets mapped to the shipped
    tooling)."""
    if verb == "relaunch":
        return ("claim: %s --lane %s --executor <you> (H-216 door -- a "
                "fresh-heartbeat refusal is typed exit 3; re-claim once the "
                "heartbeat crosses ttl_s), then relaunch the lane's own chain "
                "per its committed launch notes with a fresh heartbeat, then "
                "commit the lane record" % (TAKEOVER, lane_id))
    if verb == "land-terminal":
        return ("claim: %s --lane %s --executor <you> (H-216 door -- a "
                "fresh-heartbeat refusal is typed exit 3; re-claim once the "
                "heartbeat crosses ttl_s), then land the recorded halt "
                "(chain-terminal.run* rc) into LANE-STATE state=halted + the "
                "lane record, then commit" % (TAKEOVER, lane_id))
    return "claim: %s --lane %s --executor <you>, then: %s" % (
        TAKEOVER, lane_id, verb)


def claim_join(ctx, open_items):
    """The H-215 kept filter over the open list, ORPHAN-JOINED (H-217):
    orphans (state=running x pid DEAD on this host) stay actionable with a
    recovery verb, TTL-FREE -- a dead pid trumps a fresh heartbeat; LIVE lanes
    (pid ALIVE x heartbeat fresh) are skipped and never dispatched; everything
    else keeps the H-215 semantics exactly -- fresh-claimed items are SKIPPED
    (moved to claimed_fresh, never dispatched), stale-claimed items stay
    actionable again (annotated with the stale claim; a live pid there is a
    pid_alive double-start caution), unclaimed items pass through."""
    joined, claimed_fresh, live = [], [], []
    for it in open_items:
        orphan = orphan_state(ctx, it["id"])   # H-217: this-host pid table
        if orphan and orphan["disposition"] == "orphan":
            joined.append(dict(it, orphan=orphan))
            continue
        state, owner = claim_state(ctx, it["id"])   # H-215: heartbeat TTL
        if state == "fresh":
            if orphan and orphan["disposition"] == "live":
                live.append(dict(it, owner=owner, pid=orphan["pid"],
                                 detail=orphan["detail"]))
            else:
                claimed_fresh.append(dict(it, owner=owner))
            continue
        if state == "stale":
            ann = {"disposition": "stale", "owner": owner}
            if orphan and orphan["disposition"] == "live":
                ann["pid_alive"] = orphan["pid"]
            it = dict(it, claim=ann)
        joined.append(it)
    return joined, claimed_fresh, live


# --- committed-only exit-artifact detection (H-213 kept; logic unchanged) ------------

def git(ctx, args, ok_missing=False):
    p = subprocess.run(["git", "-C", ctx.root] + args, capture_output=True,
                       text=True, timeout=30)
    if p.returncode != 0:
        if ok_missing:
            return None
        raise RuntimeError("git %s failed: %s" % (args[:2], p.stderr.strip()[:200]))
    return p.stdout


_SHOW_CACHE = {}


def show(ctx, sha, path):
    """git show, memoized per (root, sha, path): the REFILL status_block join
    re-reads exactly the spec bytes committed_spec_status already fetched, so
    the cache keeps the corpus scan at one subprocess per spec."""
    key = (ctx.root, sha, path)
    if key not in _SHOW_CACHE:
        _SHOW_CACHE[key] = git(ctx, ["show", "%s:%s" % (sha, path)],
                               ok_missing=True)
    return _SHOW_CACHE[key]


def committed_spec_status(ctx, sha, hid, spec_paths):
    """(line-initial status, path) of the item's spec at the commit -- committed
    bytes only, the working tree never counts. (None, None) when unregistered."""
    for p in spec_paths:
        base = os.path.basename(p)
        if base.startswith(hid + "-") and base.endswith(".md"):
            m = re.search(r"^## Status\s*\n\s*(\S.*)$", show(ctx, sha, p) or "",
                          re.M)
            return (canonical_status(m.group(1)) if m else None) or "unparsed", p
    return None, None


# --- REFILL (lab throughput-floor patch, 2026-09-02; consumer adaptation named in
# the module docstring) ----------------------------------------------------------------

def status_block(ctx, sha, hid, spec_paths):
    """The committed spec's full '## Status' block (status word + its HTML
    comment, bounded) -- the PARKED/BLOCKED-*/COUNTING markers live in the
    comment, invisible to the \\w+ status word."""
    for p in spec_paths:
        base = os.path.basename(p)
        if base.startswith(hid + "-") and base.endswith(".md"):
            m = re.search(r"^## Status\s*\n(.*?)(?=^## |\Z)",
                          show(ctx, sha, p) or "", re.M | re.S)
            return m.group(1)[:4000] if m else ""
    return ""


def read_followups(ctx):
    """Bullets of the live FOLLOWUPS surface's '## FOLLOWUPS' block:
    [{id, note, source}]. One bullet per licensed lane, `- <lane-id> — <license
    citation + what it is>` (an ASCII ` -- ` separator is accepted). Missing
    file or block reads as no followups. Read from the WORKING TREE (advisory
    refill surface, never an exit artifact)."""
    try:
        text = open(os.path.join(ctx.root, ctx.followups_rel),
                    encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r"^## FOLLOWUPS[^\n]*\n(.*?)(?=^## |\Z)", text,
                  re.M | re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        fid, sep, note = body.partition(" — ")
        if not sep:
            fid, sep, note = body.partition(" -- ")
        out.append({"id": fid.strip(), "note": note.strip(),
                    "source": ctx.followups_rel})
    return out[:10]


def build_refill(ctx, n_actionable, n_open):
    """The refill payload: the reason line plus the licensed FOLLOWUPS lanes.
    Consumer adaptation: no next-queued-wave list (a consumer corpus is flat --
    the open list already enumerates every registered spec)."""
    return {"reason": "%d actionable of %d open in %s "
                      "(PARKED/BLOCKED/COUNTING excluded) -- frontier below 2"
                      % (n_actionable, n_open, ctx.hyp_rel),
            "followups": read_followups(ctx)}


def compute(ctx, sha):
    """Dispatch state at the commit: every registered item (eligible = a spec
    file <hypotheses_dir>/H-NNN-*.md committed at sha; landed = committed
    terminal spec status), claim-joined (H-215) and orphan-joined (H-217):
    {corpus, at, open: [{id, lane, kind[, claim][, orphan]}], claimed_fresh,
    live, orphans, landed}. CONSUMER ADAPTATION (the one enumeration change
    from the lab install): eligible items come from the hypotheses corpus at
    the commit, not from a release-train wave plan."""
    spec_paths = (git(ctx, ["ls-tree", "-r", "--name-only", sha, ctx.hyp_rel],
                      ok_missing=True) or "").splitlines()
    hyp_items = sorted(set(
        m.group(1) for m in (ID_RE.match(os.path.basename(p))
                             for p in spec_paths) if m))
    status = {i: committed_spec_status(ctx, sha, i, spec_paths)
              for i in hyp_items}
    landed = {i: {"class": "spec-status", "status": st, "path": pp}
              for i, (st, pp) in sorted(status.items()) if st in TERMINAL}
    open_items = [dict(id=i, lane="%s/%s" % (ctx.runs_rel, i),
                       kind=status[i][0] or "unregistered")
                  for i in hyp_items if i not in landed]
    open_items, claimed_fresh, live = claim_join(ctx, open_items)
    result = {"corpus": ctx.hyp_rel, "at": sha, "open": open_items,
              "claimed_fresh": claimed_fresh, "live": live,
              "orphans": [i["id"] for i in open_items if i.get("orphan")],
              "landed": landed}
    # REFILL (throughput floor): orphans count actionable (recovery verbs);
    # marker-carrying open items do not
    actionable = [it for it in open_items
                  if it.get("orphan")
                  or not NON_ACTIONABLE_RE.search(
                      status_block(ctx, sha, it["id"], spec_paths))]
    if len(actionable) < 2:
        result["refill"] = build_refill(ctx, len(actionable),
                                        len(open_items))
    return result


def dispatch_main(ctx, o):
    sha = (git(ctx, ["rev-parse", o.at], ok_missing=True) or "").strip()
    if not sha:
        print("dispatch-status: no commits", file=sys.stderr)
        return 3
    st = compute(ctx, sha)
    if o.json:
        print(json.dumps(st, indent=1, sort_keys=True))
        return 0
    print("DISPATCH (%s): %d open" % (st["corpus"], len(st["open"])))
    print("join: committed spec statuses x live claims (LANE-STATE.json "
          "fresh-heartbeat filter, ttl_s = %d) x live pid table (orphan "
          "join, this host)" % TTL_S)
    orphans = st.get("orphans", [])
    print("ORPHANS: %s" % (",".join(orphans) if orphans else "none"))
    for n, it in enumerate(st["open"], 1):
        note = ""
        if it.get("orphan"):
            note = " -- ORPHAN: %s" % it["orphan"].get("detail")
        elif it.get("claim"):
            note = (" -- stale-claim: heartbeat older than TTL (owner=%s) -- "
                    "actionable again" % it["claim"].get("owner"))
            if it["claim"].get("pid_alive"):
                note += (" [caution: pid %s still alive on this host -- a "
                         "relaunch would double-start]"
                         % it["claim"]["pid_alive"])
        print("%d. %s -- %s -- lane %s%s"
              % (n, it["id"], it["kind"], it["lane"], note))
        if it.get("orphan"):
            print("   next=%s -> %s"
                  % (it["orphan"].get("verb"),
                     next_action_text(it["id"], it["orphan"].get("verb"))))
    for it in st.get("live", []):
        print("LIVE %s -- %s" % (it["id"], it.get("detail")))
    for it in st.get("claimed_fresh", []):
        print("SKIP %s -- claimed-fresh: live claim, heartbeat within TTL "
              "(owner=%s)" % (it["id"], it.get("owner")))
    if st.get("claimed_fresh"):
        print("rule: a typed claim refusal (scripts/lane-takeover.py exit 3) "
              "obligates re-dispatch -- re-run %s and claim the NEW top item "
              "instead." % REDISPATCH)
    rf = st.get("refill")
    if rf:
        print("REFILL (throughput floor): %s -- licensed follow-up work is "
              "dispatchable NOW (uncounted; release order gates counted runs "
              "only)" % rf["reason"])
        if rf.get("followups"):
            for f in rf["followups"]:
                print("  FOLLOWUP %s -- %s [%s]"
                      % (f["id"], f["note"], f["source"]))
            print("  rule: claim via %s --lane <id> --executor <you> before "
                  "building; REFILL never reorders counted runs and never "
                  "closes an item." % TAKEOVER)
        else:
            print("  no FOLLOWUPS surface: license lanes under a "
                  "'## FOLLOWUPS' heading in %s (grammar: docs/workgraph.md)"
                  % ctx.followups_rel)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="repo root (default: "
                    "CLAUDE_PROJECT_DIR, then cwd)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--at", default="HEAD")
    o = ap.parse_args()
    ctx = Ctx(resolve_root(o.root))
    if not os.path.isdir(os.path.join(ctx.root, ctx.hyp_rel)):
        # no hypotheses corpus: the dispatch surface is empty by construction
        if o.json:
            print(json.dumps({"corpus": ctx.hyp_rel, "at": None, "open": [],
                              "claimed_fresh": [], "live": [], "orphans": [],
                              "landed": {}}, indent=1, sort_keys=True))
        else:
            print("DISPATCH: no hypotheses directory (%s) -- nothing to "
                  "dispatch" % ctx.hyp_rel)
        return 0
    return dispatch_main(ctx, o)


if __name__ == "__main__":
    sys.exit(main())
