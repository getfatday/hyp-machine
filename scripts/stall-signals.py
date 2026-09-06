#!/usr/bin/env python3
"""stall-signals.py -- read-side stall detector + signal strip.

PROVENANCE — COUNTED, byte-preserving port of the kept H-154 fixture detector
(experiments/runs/H-154/fixture/detector/stall_signals.py in the source lab;
hypothesis H-154-observatory-stall-signals KEPT 2026-08-28, two consecutive
counted 5/5: every planted stall class S1-S5 flagged at its seeded onset with
the correct class and evidence line, silent on all three decoys, snooze
round-trip from tracked file state alone, and byte-identical compiled-dashboard
output with the detector on vs off). Only this provenance framing, the script
name, and the ledger-path resolution (read from .claude/hyp.json ledger_file,
default ledger/ledger.jsonl) differ from the counted fixture copy; every
detection rule, window default, and parsing grammar is untouched.

Computes the user-journey section-3 signals S1-S5 over a lab repo's TRACKED FILES ONLY:
committed last-touch first (git log), working-tree mtime as an overlay badged "uncommitted"
(only where git status reports dirt, so checkout mtimes never masquerade as activity).
Pure projection: reads git + files, writes NOTHING, prints the strip (or --json) to stdout.

Parity provenance: the parsing grammars (Status word/comment, claim blocks, ledger rows,
fragment frontmatter, closes-when brackets) are copied literally from the source lab's
compile-dashboard.py / closes_when.py, so the detector and the compiled dashboard can
never disagree on what a status word or bracket says. The snooze fold reads the outbox:
a processed append whose actions say "snoozed until YYYY-MM-DD" suppresses the chip
through that date and re-arms after it, from file state alone. (The v3 decision-resolved
bracket postdates this detector's freeze: rows carrying it parse as bracketless and
simply stay visible — fail-toward-visible, never silently closed.)

Signals (each: exact source file + freshness rule; a signal is a freshness fact, never a
judgment):
  S1 silent hypothesis     active spec + run dir quiet (gate-aware: "gated on H-X" exempts
                           while H-X is not kept)
  S2 stale claim           open COORDINATION claim, zero run-dir writes since the claim
                           line (the claim's own landing commit is not "work": grace below)
  S3 orphaned sibling      ledger group (instance-of: hypothesis/H-N): one row closed, a
                           sibling open with its predicate's input frozen since the close
  S4 frozen predicate      open closes-when row whose inputs stopped moving (per-predicate;
                           maintainer-ruling routes to AWAITS ME: awaits_maintainer=true)
  S5 claimed-journalless   claimed / artifact-producing lane with zero fragments naming the
                           id since the activity started

Determinism: --now <ISO local> freezes the evaluation instant (default: wall clock).
Timezone: naive timestamps (claim lines, --now, ledger dates) parse in the process's local
zone; harnesses pin TZ. Exit 0 always on a readable repo; 2 on unusable --root.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "hooks", "scripts"))
from hyp_status import canonical_status  # noqa: E402  (shared status reader)

# ---- THE ONE TUNABLE BLOCK (journey section-3 shipped defaults; unit = days) -------------
TUNABLES = {
    "S1_DAYS": 3.0,   # active spec: run dir quiet / no run dir since registration
    "S2_DAYS": 1.0,   # open claim with no writes behind it (24 h)
    "S3_DAYS": 7.0,   # open sibling frozen since its sibling closed
    "S4_DAYS": 7.0,   # closes-when inputs stopped moving
    "S5_DAYS": 2.0,   # claimed/artifact-producing with no fragment naming the id
    # The claim's own landing commit is the claim, not work performed against it: run-dir
    # touches within this many seconds of the claimed: timestamp do not clear S2.
    "S2_CLAIM_LANDING_GRACE_S": 3600,
    "SNOOZE_RE": r"snoozed until (\d{4}-\d{2}-\d{2})",   # outbox processed-append grammar
    "SLUG_PREFIX": "stall:",                             # chip slug = stall:<SIG>:<ID>
    "SUBMISSIONS_REL": "experiments/reviews/dashboard/submissions.jsonl",
}

SIGNAL_NAMES = {
    "S1": "Experiment gone quiet",
    "S2": "Claimed but idle",
    "S3": "Forgotten sibling follow-up",
    "S4": "Close condition stopped moving",
    "S5": "Running with no journal entry",
}
FOOTNOTE = ("session-only agents are invisible here — work that has not yet touched a "
            "tracked file, ledger row, or COORDINATION claim cannot appear on any "
            "file-based surface.")

# ---- parity grammars (copied literally; see header) ---------------------------------------
CLOSES_WHEN_RE = re.compile(
    r"\[closes-when:\s*(path-exists|commit-grep|hypothesis-kept|maintainer-ruling)=([^\]]+)\]")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STATUS_HEADING_RE = re.compile(r"(?m)^##\s*Status\s*$")
NEXT_HEADING_RE = re.compile(r"(?m)^##\s")
HID_FILE_RE = re.compile(r"^(H-\d+)-(.+)\.md$")
CLAIMED_LINE_RE = re.compile(r"(?im)^\s*-\s*(?:\*\*)?claimed(?:\*\*)?\s*:\s*(.+)$")
STATE_LINE_RE = re.compile(r"(?m)^\s*-\s*(?:\*\*)?state(?:\*\*)?\s*:\s*(.*)$")
CLAIM_CLOSE_RE = re.compile(r"(?im)^.*\bCLAIM\s+(?:CLOSED|RELEASED)\b.*$")
STATUS_LINE_RE = re.compile(r"(?im)^\s*-\s*(?:\*\*)?status(?:\*\*)?\s*:\s*(.+)$")
REGISTERED_RE = re.compile(r"registered\s+(\d{4}-\d{2}-\d{2})")
GATED_H_RE = re.compile(r"gated on (?:the )?(H-\d+)", re.IGNORECASE)
DATE_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")
SUPPORTED_KINDS = ("intent", "amendment", "commitment", "directive")
GIT_TIMEOUT = 30


def _config_ledger_rel(root):
    """Consumer-repo ledger path from .claude/hyp.json (ledger_file), default
    ledger/ledger.jsonl — the one lab path that diverges in a hyp install."""
    rel = "ledger/ledger.jsonl"
    try:
        with open(os.path.join(root, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        val = data.get("ledger_file") if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            rel = val.strip().strip("/")
    except (OSError, ValueError):
        pass
    return rel


def _days(seconds):
    return round(seconds / 86400.0, 1)


def git(root, args):
    proc = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True,
                          timeout=GIT_TIMEOUT)
    return proc.returncode, proc.stdout


# ---- git facts -----------------------------------------------------------------------------

def git_history_map(root):
    """ONE git log --name-status walk -> {path: {last_ct, added_ct}}."""
    code, out = git(root, ["log", "--format=%x01%H %ct", "--name-status"])
    facts = {}
    if code != 0:
        return facts
    ct = None
    for line in out.splitlines():
        if line.startswith("\x01"):
            parts = line[1:].split()
            ct = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
            continue
        if ct is None or "\t" not in line:
            continue
        cells = line.split("\t")
        status, path = cells[0], cells[-1]
        f = facts.setdefault(path, {})
        if "last_ct" not in f:
            f["last_ct"] = ct
        if status.startswith("A"):
            f["added_ct"] = ct
    return facts


def last_commit_under(facts, prefix):
    best = None
    bare = prefix.rstrip("/")
    deep = bare + "/"
    for path, f in facts.items():
        if path == bare or path.startswith(deep):
            ct = f.get("last_ct")
            if ct is not None and (best is None or ct > best):
                best = ct
    return best


def dirty_paths(root):
    code, out = git(root, ["status", "--porcelain"])
    paths = set()
    if code != 0:
        return paths
    for line in out.splitlines():
        if len(line) > 3:
            p = line[3:].strip()
            if " -> " in p:
                p = p.split(" -> ", 1)[1]
            paths.add(p.strip('"'))
    return paths


def dirty_under(dirty, prefix):
    bare = prefix.rstrip("/")
    deep = bare + "/"
    return any(p == bare or p.startswith(deep) for p in dirty)


def tree_last_mtime(path):
    if not os.path.exists(path):
        return None
    latest = None
    for dp, dns, fns in os.walk(path):
        dns[:] = [d for d in dns if d != ".git"]
        for name in fns:
            try:
                m = os.stat(os.path.join(dp, name)).st_mtime
            except OSError:
                continue
            if latest is None or m > latest:
                latest = m
    return latest


# ---- parsers (parity) ----------------------------------------------------------------------

def extract_status_word(text):
    m = STATUS_HEADING_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    block = rest[: nxt.start()] if nxt else rest
    block = HTML_COMMENT_RE.sub(" ", block)
    stripped = block.strip()
    if not stripped:
        return None
    # shared canonicalizer: consumer spellings map onto the canonical word
    return canonical_status(stripped) or stripped.split()[0]


def extract_status_comment(text):
    m = STATUS_HEADING_RE.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    block = rest[: nxt.start()] if nxt else rest
    cm = re.search(r"<!--(.*?)-->", block, re.DOTALL)
    return " ".join(cm.group(1).split()) if cm else ""


def parse_runs_count(text):
    m = re.search(r"(?m)^##\s*Runs\s*$", text)
    if not m:
        return 0
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    block = rest[: nxt.start()] if nxt else rest
    n = 0
    for line in block.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 5 and cells[1].isdigit():
            n += 1
    return n


def claim_is_open(text):
    """H-153 grammar first (- status: CLAIMED (open) / CLOSED ...), then the compiler's
    fallbacks: a - state: line or a CLAIM CLOSED/RELEASED marker anywhere."""
    sm = STATUS_LINE_RE.search(text or "")
    if sm:
        return not re.search(r"\b(CLOSED|RELEASED)\b", sm.group(1), re.IGNORECASE)
    st = STATE_LINE_RE.search(text or "")
    if st:
        return not re.search(r"\b(CLOSED|RELEASED)\b", st.group(1), re.IGNORECASE)
    return not CLAIM_CLOSE_RE.search(text or "")


def parse_claimed_ts(text):
    m = CLAIMED_LINE_RE.search(text or "")
    if not m:
        return None, ""
    raw = m.group(1).strip()
    dm = DATE_TIME_RE.search(raw)
    if not dm:
        return None, raw
    try:
        if dm.group(2):
            dt = datetime.strptime(dm.group(1) + " " + dm.group(2), "%Y-%m-%d %H:%M")
        else:
            dt = datetime.strptime(dm.group(1), "%Y-%m-%d")
        return dt.timestamp(), raw
    except ValueError:
        return None, raw


def parse_ledger(text):
    rows = []
    if text is None:
        return rows
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            date, slug, hit = rec["date"], rec["slug"], rec["hit"]
            kind = rec.get("kind", "intent")
            if kind not in SUPPORTED_KINDS:
                raise ValueError("unsupported kind")
        except (ValueError, KeyError, TypeError):
            continue
        rows.append({"date": date, "slug": slug, "hit": hit, "kind": kind,
                     "instance-of": rec.get("instance-of"), "order": lineno})
    return rows


def parse_fragment(name, text):
    stem = name[:-3] if name.endswith(".md") else name
    date = ""
    if text:
        fm = re.match(r"\s*---\s*\n(.*?)\n---", text, re.DOTALL)
        if fm:
            dm = re.search(r"(?m)^date:\s*(\S+)", fm.group(1))
            if dm:
                date = dm.group(1)
    return stem, date


# ---- the detector --------------------------------------------------------------------------

def compute(root, now_ts):
    facts = git_history_map(root)
    dirty = dirty_paths(root)

    def touch_under(rel):
        """Committed last-touch first; working-tree mtime overlay only where dirty."""
        ct = last_commit_under(facts, rel)
        mt = tree_last_mtime(os.path.join(root, rel)) if dirty_under(dirty, rel) else None
        vals = [t for t in (ct, mt) if t is not None]
        return (max(vals) if vals else None), (mt is not None and (ct is None or mt > ct))

    # hypotheses
    hyps = {}
    hyp_dir = os.path.join(root, "hypotheses")
    if os.path.isdir(hyp_dir):
        for name in sorted(os.listdir(hyp_dir)):
            m = HID_FILE_RE.match(name)
            if not m:
                continue
            try:
                with open(os.path.join(hyp_dir, name), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            comment = extract_status_comment(text)
            reg = REGISTERED_RE.search(comment or "")
            gate = GATED_H_RE.search(comment or "")
            hyps[m.group(1)] = {
                "hid": m.group(1), "file": "hypotheses/" + name,
                "status": (extract_status_word(text) or "").lower(),
                "registered": reg.group(1) if reg else None,
                "gate_hid": gate.group(1).upper() if gate else None,
                "gated": bool(re.search(r"gated", comment or "", re.IGNORECASE)),
                "runs_recorded": parse_runs_count(text),
            }

    # claims
    open_claims = []
    runs_root = os.path.join(root, "experiments", "runs")
    if os.path.isdir(runs_root):
        for sub in sorted(os.listdir(runs_root)):
            coord = os.path.join(runs_root, sub, "COORDINATION.md")
            if not os.path.isfile(coord):
                continue
            try:
                with open(coord, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            if not claim_is_open(text):
                continue
            ts, raw = parse_claimed_ts(text)
            open_claims.append({"hid": sub, "file": "experiments/runs/%s/COORDINATION.md" % sub,
                                "claimed_ts": ts, "claimed": raw})
    claims_by_hid = {c["hid"]: c for c in open_claims}

    # ledger + predicate evaluation at HEAD (closes_when parity, bulk reads)
    ledger_rel = _config_ledger_rel(root)
    try:
        with open(os.path.join(root, ledger_rel), "r", encoding="utf-8") as fh:
            ledger_rows = parse_ledger(fh.read())
    except OSError:
        ledger_rows = []
    _, ls_out = git(root, ["ls-tree", "-r", "--name-only", "HEAD"])
    head_paths = set(ls_out.splitlines())
    _, msgs_out = git(root, ["log", "--format=%x01%ct%x02%B"])
    commit_msgs = []
    for chunk in msgs_out.split("\x01"):
        if not chunk.strip():
            continue
        ct_part, _sep, body = chunk.partition("\x02")
        commit_msgs.append((int(ct_part.strip()) if ct_part.strip().isdigit() else None, body))

    def spec_head_status(hid):
        pat = re.compile(r"^hypotheses/%s-[^/]*\.md$" % re.escape(hid))
        matches = [p for p in head_paths if pat.match(p)]
        if len(matches) != 1:
            return None
        code, out = git(root, ["show", "HEAD:" + matches[0]])
        return (extract_status_word(out) or "").lower() if code == 0 else None

    def bracket_closed(pred, arg):
        arg = arg.strip()
        if pred == "path-exists":
            return arg in head_paths or any(
                p.startswith(arg.rstrip("/") + "/") for p in head_paths)
        if pred == "commit-grep":
            return any(arg in body for _ct, body in commit_msgs)
        if pred == "hypothesis-kept":
            return spec_head_status(arg) == "kept"
        if pred == "maintainer-ruling":
            needle = arg.lower()
            return any(p.startswith("research/raw/") and needle in p.rsplit("/", 1)[-1].lower()
                       and "ruling" in p.rsplit("/", 1)[-1].lower() for p in head_paths)
        return False

    def predicate_input_last_ct(pred, arg):
        arg = arg.strip()
        if pred == "path-exists":
            return last_commit_under(facts, os.path.dirname(arg) or arg)
        if pred == "hypothesis-kept":
            spec_ct = None
            for path in facts:
                if re.match(r"^hypotheses/%s-.*\.md$" % re.escape(arg), path):
                    spec_ct = facts[path].get("last_ct")
                    break
            run_ct = last_commit_under(facts, "experiments/runs/%s" % arg)
            vals = [v for v in (spec_ct, run_ct) if v is not None]
            return max(vals) if vals else None
        if pred == "commit-grep":
            return next((ct for ct, body in commit_msgs if ct and arg in body), None)
        if pred == "maintainer-ruling":
            best = None
            needle = arg.lower()
            for path, f in facts.items():
                if path.startswith("research/raw/") and needle in path.rsplit("/", 1)[-1].lower():
                    ct = f.get("last_ct")
                    if ct and (best is None or ct > best):
                        best = ct
            return best
        return None

    open_rows, closed_rows = [], []
    for row in ledger_rows:
        if row["kind"] != "commitment":
            continue
        m = CLOSES_WHEN_RE.search(HTML_COMMENT_RE.sub(" ", row["hit"]))
        row["bracket"] = {"predicate": m.group(1), "arg": m.group(2).strip()} if m else None
        if row["bracket"] and bracket_closed(row["bracket"]["predicate"],
                                             row["bracket"]["arg"]):
            closed_rows.append(row)
        else:
            open_rows.append(row)

    # fragments
    frag_meta = []
    frag_dir = os.path.join(root, "experiments", "journal-fragments")
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if not name.endswith(".md"):
                continue
            try:
                with open(os.path.join(frag_dir, name), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                text = ""
            _stem, date = parse_fragment(name, text)
            frag_meta.append((name, date, text))

    def journaled_since(hid, since_date):
        pat = re.compile(r"\b%s\b" % re.escape(hid))
        for _name, date, text in frag_meta:
            if date and since_date and date < since_date:
                continue
            if pat.search(text):
                return True
        return False

    # snooze fold (outbox, beat 4)
    stall_states = {}
    sub_path = os.path.join(root, TUNABLES["SUBMISSIONS_REL"])
    if os.path.isfile(sub_path):
        by_id, order = {}, []
        try:
            with open(sub_path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    rid = rec.get("id")
                    if isinstance(rec.get("items"), list):
                        if rid not in by_id:
                            order.append(rid)
                        by_id[rid] = dict(rec)
                    elif rid in by_id and isinstance(rec.get("status"), str) and rec["status"]:
                        by_id[rid]["status"] = rec["status"]
                        if rec.get("actions") is not None:
                            by_id[rid]["actions"] = rec["actions"]
        except OSError:
            by_id, order = {}, []
        snooze_re = re.compile(TUNABLES["SNOOZE_RE"])
        for rid in order:
            sub = by_id[rid]
            for it in sub.get("items") or []:
                slug = it.get("slug") if isinstance(it, dict) else None
                if not (isinstance(slug, str)
                        and slug.startswith(TUNABLES["SLUG_PREFIX"])):
                    continue
                entry = {"sub_id": rid, "status": sub.get("status") or "unprocessed",
                         "snoozed_until": None}
                if entry["status"] == "processed" and isinstance(sub.get("actions"), str):
                    sm = snooze_re.search(sub["actions"])
                    if sm:
                        entry["snoozed_until"] = sm.group(1)
                stall_states[slug] = entry

    today = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
    signals, seen_slugs = [], set()

    def add_signal(sig, ident, evidence, source, age_days, extra=None):
        slug = "%s%s:%s" % (TUNABLES["SLUG_PREFIX"], sig, ident)
        n = 2
        while slug in seen_slugs:
            slug = "%s%s:%s-%d" % (TUNABLES["SLUG_PREFIX"], sig, ident, n)
            n += 1
        seen_slugs.add(slug)
        st = stall_states.get(slug) or {}
        row = {"slug": slug, "signal": sig, "signal_name": SIGNAL_NAMES[sig], "id": ident,
               "evidence": evidence, "source_file": source, "age_days": age_days,
               "window_days": TUNABLES[sig + "_DAYS"],
               "snoozed_until": st.get("snoozed_until")}
        row["snoozed"] = bool(row["snoozed_until"] and row["snoozed_until"] >= today)
        if extra:
            row.update(extra)
        signals.append(row)

    # S1 -- registered-active hypothesis gone quiet (gate-aware)
    for hid in sorted(hyps, key=lambda x: -int(x.split("-")[1])):
        h = hyps[hid]
        if h["status"] != "active":
            continue
        if h["gated"]:
            gate = h.get("gate_hid")
            if gate is None or (hyps.get(gate) or {}).get("status") != "kept":
                continue  # gated and the gate is still open: waiting is not stalling
        run_rel = "experiments/runs/%s" % hid
        if os.path.isdir(os.path.join(root, run_rel)):
            touch, uncommitted = touch_under(run_rel)
            if touch is None:
                touch = (facts.get(h["file"]) or {}).get("added_ct")
            if touch is None:
                continue
            age = _days(now_ts - touch)
            if age > TUNABLES["S1_DAYS"]:
                add_signal("S1", hid,
                           "%s is marked active, but nothing in %s/ has changed in %s days"
                           % (hid, run_rel, age),
                           run_rel + "/", age,
                           {"spec_file": h["file"], "uncommitted": uncommitted})
        else:
            reg_ts = None
            if h.get("registered"):
                try:
                    reg_ts = datetime.strptime(h["registered"], "%Y-%m-%d").timestamp()
                except ValueError:
                    reg_ts = None
            if reg_ts is None:
                reg_ts = (facts.get(h["file"]) or {}).get("added_ct")
            if reg_ts is None:
                continue
            age = _days(now_ts - reg_ts)
            if age > TUNABLES["S1_DAYS"]:
                add_signal("S1", hid,
                           "%s was registered %s days ago and no run folder exists yet"
                           % (hid, age),
                           h["file"], age, {"spec_file": h["file"]})

    # S2 -- open claim with no writes in its run dir since the claim line
    for claim in open_claims:
        c_ts = claim.get("claimed_ts")
        if c_ts is None:
            continue
        age = _days(now_ts - c_ts)
        if age <= TUNABLES["S2_DAYS"]:
            continue
        run_rel = os.path.dirname(claim["file"])
        touch, uncommitted = touch_under(run_rel)
        if touch is not None and touch > c_ts + TUNABLES["S2_CLAIM_LANDING_GRACE_S"]:
            continue  # work has landed since the claim: not idle
        add_signal("S2", claim["hid"],
                   "the run was claimed %s (%s days ago) and no file in %s has changed "
                   "since the claim" % (claim.get("claimed") or "(undated)", age, run_rel),
                   claim["file"], age, {"uncommitted": uncommitted})

    # S3 -- one on-keep sibling closed, another frozen since that close
    groups = {}
    for row in open_rows + closed_rows:
        inst = row.get("instance-of") or ""
        if inst.startswith("hypothesis/"):
            groups.setdefault(inst.split("/", 1)[1], {"open": [], "closed": []})
    for row in open_rows:
        inst = row.get("instance-of") or ""
        if inst.startswith("hypothesis/"):
            groups[inst.split("/", 1)[1]]["open"].append(row)
    for row in closed_rows:
        inst = row.get("instance-of") or ""
        if inst.startswith("hypothesis/"):
            groups[inst.split("/", 1)[1]]["closed"].append(row)
    for hid in sorted(groups, key=lambda x: -int(x.split("-")[1])
                      if x.split("-")[-1].isdigit() else 0):
        g = groups[hid]
        if not g["open"] or not g["closed"]:
            continue
        close_ts = []
        for row in g["closed"]:
            b = row.get("bracket")
            if b:
                ct = predicate_input_last_ct(b["predicate"], b["arg"])
                if ct:
                    close_ts.append(ct)
        if not close_ts:
            continue
        since_close = _days(now_ts - max(close_ts))
        if since_close < TUNABLES["S3_DAYS"]:
            continue
        cold = []
        for row in g["open"]:
            b = row.get("bracket")
            input_ct = predicate_input_last_ct(b["predicate"], b["arg"]) if b else None
            if input_ct is None or _days(now_ts - input_ct) > TUNABLES["S3_DAYS"]:
                cold.append(row["slug"])
        if not cold:
            continue
        add_signal("S3", hid,
                   "a follow-up from %s closed %s days ago; %d sibling(s) stay open with "
                   "no commit touching their target paths since: %s"
                   % (hid, since_close, len(cold), ", ".join(cold[:4])),
                   ledger_rel, since_close, {"cold_slugs": cold})

    # S4 -- open close-condition whose inputs stopped moving (per-predicate rules)
    for row in open_rows:
        b = row.get("bracket")
        if not b:
            continue
        pred, arg = b["predicate"], b["arg"]
        try:
            row_ts = datetime.strptime(row.get("date") or "", "%Y-%m-%d").timestamp()
        except ValueError:
            row_ts = None
        row_age = _days(now_ts - row_ts) if row_ts else None
        input_ct = predicate_input_last_ct(pred, arg)
        input_age = _days(now_ts - input_ct) if input_ct else None
        fire, awaits, why = False, False, ""
        if pred == "maintainer-ruling":
            if row_age is not None and row_age > TUNABLES["S4_DAYS"]:
                fire, awaits = True, True
                why = ("this waits on your ruling ('%s') and has waited %s days — the "
                       "next step is yours, not the lab's" % (arg, row_age))
        elif pred == "commit-grep":
            recent = input_age is not None and input_age <= TUNABLES["S4_DAYS"]
            if not recent and row_age is not None and row_age > TUNABLES["S4_DAYS"]:
                fire = True
                why = ("this closes on a commit saying \"%s\", and no commit in the last "
                       "%d days moves toward it" % (arg, int(TUNABLES["S4_DAYS"])))
        else:
            if (input_age is not None and input_age > TUNABLES["S4_DAYS"]
                    and row_age is not None and row_age > TUNABLES["S4_DAYS"]):
                fire = True
                target = ("the folder holding %s" % arg if pred == "path-exists"
                          else "%s's spec and run folder" % arg)
                why = ("this closes when %s is satisfied, and no commit has touched %s "
                       "in %s days" % (arg, target, input_age))
        if fire:
            age = row_age if input_age is None else max(input_age, row_age or 0)
            add_signal("S4", row["slug"], why, ledger_rel, age,
                       {"awaits_maintainer": awaits, "predicate": pred, "arg": arg,
                        "hit": row["hit"][:240]})

    # S5 -- claimed / artifact-producing lane with no fragment naming the id
    lane_hids = set(claims_by_hid)
    for hid, h in hyps.items():
        if h["status"] != "active":
            continue
        run_dir = os.path.join(root, "experiments", "runs", hid)
        if not os.path.isdir(run_dir):
            continue
        for dp, dns, fns in os.walk(run_dir):
            if dp == run_dir and "fixture" in dns:
                dns.remove("fixture")
            dns[:] = [d for d in dns if d != ".git"]
            if fns:
                lane_hids.add(hid)
                break
    for hid in sorted(lane_hids, key=lambda x: -int(x.split("-")[1])
                      if x.split("-")[-1].isdigit() else 0):
        start_ts = None
        claim = claims_by_hid.get(hid)
        if claim and claim.get("claimed_ts"):
            start_ts = claim["claimed_ts"]
        else:
            start_ts = last_commit_under(facts, "experiments/runs/%s" % hid)
        if start_ts is None:
            continue
        age = _days(now_ts - start_ts)
        if age < TUNABLES["S5_DAYS"]:
            continue
        since_date = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d")
        if journaled_since(hid, since_date):
            continue
        source = (claim["file"] if claim
                  else "experiments/runs/%s/" % hid)
        add_signal("S5", hid,
                   "%s has been claimed or producing artifacts for %s days, and no "
                   "journal entry since %s names it — work without a journal entry is "
                   "invisible to whoever picks this up next" % (hid, age, since_date),
                   source, age)

    flags = [s for s in signals if not s["snoozed"]]
    suppressed = [s for s in signals if s["snoozed"]]
    counts = {}
    for s in flags:
        counts[s["signal"]] = counts.get(s["signal"], 0) + 1
    return {"now": datetime.fromtimestamp(now_ts).isoformat(), "root": os.path.abspath(root),
            "windows": {k: TUNABLES[k] for k in
                        ("S1_DAYS", "S2_DAYS", "S3_DAYS", "S4_DAYS", "S5_DAYS")},
            "flags": flags, "suppressed": suppressed, "counts": counts,
            "footnote": FOOTNOTE}


def render_strip(doc):
    out = []
    out.append("STALLS — %d signal(s) at %s (windows d: S1 %.0f · S2 %.0f · S3 %.0f · "
               "S4 %.0f · S5 %.0f)"
               % (len(doc["flags"]), doc["now"], doc["windows"]["S1_DAYS"],
                  doc["windows"]["S2_DAYS"], doc["windows"]["S3_DAYS"],
                  doc["windows"]["S4_DAYS"], doc["windows"]["S5_DAYS"]))
    for s in doc["flags"]:
        marker = " [AWAITS MAINTAINER]" if s.get("awaits_maintainer") else ""
        badge = " [uncommitted]" if s.get("uncommitted") else ""
        out.append("  [%s] %s — %s (age %sd > %sd)%s%s" %
                   (s["signal"], s["slug"], s["signal_name"], s["age_days"],
                    s["window_days"], marker, badge))
        out.append("        evidence: %s — %s" % (s["source_file"], s["evidence"]))
    if doc["suppressed"]:
        out.append("  snoozed (%d):" % len(doc["suppressed"]))
        for s in doc["suppressed"]:
            out.append("    %s — snoozed until %s (re-arms after; suppression lives in the "
                       "tracked outbox append)" % (s["slug"], s["snoozed_until"]))
    out.append("  footnote: %s" % doc["footnote"])
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--now", default=None,
                    help="ISO local instant to evaluate at (default: wall clock)")
    ap.add_argument("--json", action="store_true", help="print the machine projection")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(root, ".git")):
        print("stall-signals: %s is not a git repo root" % root, file=sys.stderr)
        sys.exit(2)
    if args.now:
        now_ts = datetime.fromisoformat(args.now).timestamp()
    else:
        now_ts = datetime.now().timestamp()
    doc = compute(root, now_ts)
    if args.json:
        json.dump(doc, sys.stdout, indent=1, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_strip(doc))


if __name__ == "__main__":
    main()
