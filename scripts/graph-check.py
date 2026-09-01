#!/usr/bin/env python3
"""graph-check.py -- the work-graph checker: lint + derived dispatch state for
every `ledger/graphs/*.md` effort graph (the durability model's §2 checker; see
ledger/graphs/README.md lab-side, docs/workgraph.md plugin-side).

Provenance (H-231 workgraph-compression-survival-v2, kept 2x 5/5 2026-08-30):
the row grammar, the frontier rule, and the false-done class are ported from
the counted grader (experiments/runs/H-231/fixture/grade_h231.py,
parse_graph_rows/graph_frontier) and the frozen graph mandate
(fixture/prompts/prompt-a-on-extra.txt), generalized from the fixture's
`s[0-9]+` ids to the schema's id grammar. The schema extensions accepted here
beyond the counted core (claim column; parked/stale/superseded-by statuses)
are the durability model's documented §2 fields
(experiments/runs/DESIGN-session-durability/fixture/durability-model.md).

REPORT-ONLY: findings never fail a pipeline -- exit 0 always (argparse usage
errors excepted). Same posture as the H-154 detector: registered expectation,
never inference. Read-only over the repo; stdlib only; deterministic (sorted
output; `--today` pins the clock the claim-expiry rule reads).

What it derives per graph, from disk alone:
  frontier        non-done steps (parked/superseded excluded) whose step-needs
                  are all done -- the graph's remaining ready work. Computed
                  from recorded status exactly as the counted grader computed
                  it; false-dones are reported separately, not auto-reopened.
  false-dones     done steps whose produces path is missing on disk or whose
                  sha256 no longer matches the recorded evidence (open graphs
                  only -- a closed graph's gitignored outputs are legitimately
                  gone, so closed graphs get structural checks only)
  next-dispatch   the stamped pointer re-checked against the recomputed
                  frontier ("a cache, never trusted without recomputation")

Findings, one per line, `GRAPH-CHECK<TAB><file><TAB><RULE><TAB><detail>`:
  SCHEMA              frontmatter/table/row-grammar violation (missing effort/
                      state/invariants, unknown state, bad header, row width,
                      bad or duplicate id, unknown status, malformed evidence
                      or claim, missing next-dispatch line)
  DANGLING-REF        a needs/blocked:/superseded-by: token that looks like a
                      step id but matches no row (blocks the frontier -- an
                      unknown dependency is never satisfied)
  CYCLE               a needs cycle among step edges (the frontier can never
                      drain)
  DONE-UNEVIDENCED    status done with evidence "-" or no produces path
                      ("done is evidenced, never declared")
  FALSE-DONE          done, but produces is missing on disk or sha-mismatched
                      (the seeded defect class the H-231 protocol flags before
                      dispatch)
  STALE-DOWNSTREAM    a done step downstream of a FALSE-DONE (the update-leg
                      invalidation set: re-run candidates)
  NEXT-DISPATCH-STALE the stamped pointer disagrees with the recomputed
                      frontier
  CLAIM-EXPIRED       a non-done step's claim date is older than the TTL
                      window (--claim-ttl-days, default 3 -- the §3 S6 default)
  CLOSED-WITH-OPEN    state closed but open/blocked/stale rows remain

Usage (repo root: --root, then CLAUDE_PROJECT_DIR, then cwd):
    python3 scripts/graph-check.py [graph.md ...] [--root DIR] [--json]
                                   [--claim-ttl-days N] [--today YYYY-MM-DD]
With no path arguments it sweeps `<root>/ledger/graphs/*.md`. produces paths
resolve against the tree the graph lives in (the segment before
/ledger/graphs/ when present, else the repo root).
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sys

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# evidence: "-", a bare sha256, or the §2 example's <path@sha> pin form
EVIDENCE_AT_RE = re.compile(r"^\S+@([0-9a-f]{64})$")
CLAIM_RE = re.compile(r"^(\S+)@(\d{4}-\d{2}-\d{2})$")
NEXT_DISPATCH_RE = re.compile(r"^next-dispatch:\s*(.*)$")
HEADER_CORE = ["id", "task", "needs", "produces", "status", "evidence"]
STATE_OPEN = ("open",)
STATE_CLOSED = ("closed", "done")   # run-1's counted graph closed with "done"
STATUS_SIMPLE = ("open", "done", "stale")
STATUS_PREFIXED = ("blocked:", "parked:", "superseded-by:")


def resolve_root(cli_root):
    if cli_root:
        return os.path.abspath(cli_root)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.getcwd()


def tree_root_for(graph_path, fallback_root):
    """The tree a graph's produces paths resolve against: the segment before
    /ledger/graphs/ when the graph lives at the conventional home."""
    parts = os.path.abspath(graph_path).replace(os.sep, "/").rsplit(
        "/ledger/graphs/", 1)
    if len(parts) == 2:
        return parts[0]
    return fallback_root


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def split_frontmatter(lines):
    """-> (frontmatter dict-ish, invariants count, body lines, findings)."""
    finds = []
    if not lines or lines[0].strip() != "---":
        finds.append(("SCHEMA", "no frontmatter block (expected leading ---)"))
        return {}, 0, lines, finds
    fm, invariants, close = {}, 0, None
    for i in range(1, len(lines)):
        s = lines[i].strip()
        if s == "---":
            close = i
            break
        if s.startswith("- "):
            invariants += 1
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", s)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    if close is None:
        finds.append(("SCHEMA", "frontmatter never closed (no second ---)"))
        return fm, invariants, [], finds
    return fm, invariants, lines[close + 1:], finds


def parse_table(body):
    """-> (header cells or None, rows list of dicts, findings)."""
    finds, header, rows = [], None, []
    for ln in body:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r"[-: ]*", c) for c in cells):
            continue                       # the |----| separator line
        if header is None:
            header = cells
            words = [c.split()[0].lower() if c.split() else "" for c in cells]
            if words[:6] != HEADER_CORE or len(cells) > 7 or (
                    len(cells) == 7 and words[6] != "claim"):
                finds.append(("SCHEMA",
                              "header is | %s | -- expected | id | task | "
                              "needs | produces | status | evidence | with "
                              "optional 7th claim" % " | ".join(words)))
            continue
        if len(cells) < 6:
            finds.append(("SCHEMA", "row too narrow (%d cells): %s"
                          % (len(cells), s[:80])))
            continue
        if header is not None and len(cells) != len(header) \
                and len(cells) in (6, 7):
            finds.append(("SCHEMA", "row width %d != header width %d: %s"
                          % (len(cells), len(header), cells[0])))
        rows.append({"id": cells[0], "task": cells[1], "needs": cells[2],
                     "produces": cells[3], "status": cells[4],
                     "evidence": cells[5],
                     "claim": cells[6] if len(cells) >= 7 else "-"})
    if header is None:
        finds.append(("SCHEMA", "no step table found"))
    return header, rows, finds


def status_kind(status):
    if status in STATUS_SIMPLE:
        return status
    for p in STATUS_PREFIXED:
        if status.startswith(p) and len(status) > len(p):
            return p.rstrip(":")
    return None


def need_tokens(needs):
    if needs.strip() in ("", "-"):
        return []
    return [t.strip() for t in needs.split(",") if t.strip()]


def is_id_like(token):
    return bool(ID_RE.match(token))


def step_needs(row, known_ids):
    """The tokens that participate in edges: known ids, plus id-like unknowns
    (which block the frontier, exactly as the counted grader's unknown-sN
    tokens did). Path-like tokens are external inputs -- no edge. A
    blocked:<ids> annotation contributes its refs as edges too (redundant with
    needs in every counted graph; binding when someone writes it alone)."""
    out = []
    for t in need_tokens(row["needs"]):
        if t in known_ids or is_id_like(t):
            out.append(t)
    if row["status"].startswith("blocked:"):
        for t in row["status"].split(":", 1)[1].split(","):
            t = t.strip()
            if t and (t in known_ids or is_id_like(t)) and t not in out:
                out.append(t)
    return out


def frontier_of(rows_by_id):
    done = {i for i, r in rows_by_id.items() if r["status"] == "done"}
    front = []
    for i, r in sorted(rows_by_id.items()):
        kind = status_kind(r["status"])
        if kind in ("done", "parked", "superseded-by"):
            continue
        if all(n in done for n in step_needs(r, set(rows_by_id))):
            front.append(i)
    return front


def find_cycles(rows_by_id):
    """Ids on some needs cycle, via iterative DFS (sorted, deterministic)."""
    known = set(rows_by_id)
    edges = {i: [n for n in step_needs(r, known) if n in known]
             for i, r in rows_by_id.items()}
    state, on_cycle = {}, set()
    for start in sorted(edges):
        if state.get(start):
            continue
        stack = [(start, iter(edges[start]))]
        state[start] = "in"
        path = [start]
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = "out"
                stack.pop()
                path.pop()
                continue
            if state.get(nxt) == "in":
                on_cycle.update(path[path.index(nxt):])
            elif state.get(nxt) is None:
                state[nxt] = "in"
                stack.append((nxt, iter(edges[nxt])))
                path.append(nxt)
    return sorted(on_cycle)


def descendants_of(seed_ids, rows_by_id):
    """Ids reachable via reverse needs edges from any seed (seeds excluded)."""
    known = set(rows_by_id)
    consumers = {i: set() for i in known}
    for i, r in rows_by_id.items():
        for n in step_needs(r, known):
            if n in known:
                consumers[n].add(i)
    seen, work = set(), sorted(set(seed_ids))
    while work:
        cur = work.pop()
        for c in sorted(consumers.get(cur, ())):
            if c not in seen and c not in seed_ids:
                seen.add(c)
                work.append(c)
    return seen


def evidence_sha(evidence):
    if SHA_RE.match(evidence):
        return evidence
    m = EVIDENCE_AT_RE.match(evidence)
    return m.group(1) if m else None


def parse_next_dispatch(body):
    """The LAST next-dispatch line -> (present, pointer-or-None-for-empty)."""
    val = None
    for ln in body:
        m = NEXT_DISPATCH_RE.match(ln.strip())
        if m:
            val = m.group(1).strip()
    if val is None:
        return False, None
    first = val.split()[0].rstrip(".,;") if val.split() else ""
    if first in ("", "-", "none", "None"):
        return True, None
    return True, first


def check_graph(path, root, ttl_days, today):
    finds = []          # (RULE, detail)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return {"file": path, "error": str(e)}, [("SCHEMA",
                                                  "unreadable: %s" % e)]
    fm, invariants, body, f0 = split_frontmatter(lines)
    finds.extend(f0)
    for key in ("effort", "state"):
        if not fm.get(key):
            finds.append(("SCHEMA", "frontmatter missing %s:" % key))
    if invariants == 0:
        finds.append(("SCHEMA", "frontmatter has no invariants list"))
    state = fm.get("state", "")
    is_open = state in STATE_OPEN
    if state and state not in STATE_OPEN + STATE_CLOSED:
        finds.append(("SCHEMA", "unknown state '%s' (open | closed)" % state))

    header, rows, f1 = parse_table(body)
    finds.extend(f1)
    rows_by_id = {}
    for r in rows:
        if not ID_RE.match(r["id"]):
            finds.append(("SCHEMA", "bad step id '%s'" % r["id"][:40]))
            continue
        if r["id"] in rows_by_id:
            finds.append(("SCHEMA", "duplicate step id '%s'" % r["id"]))
            continue
        rows_by_id[r["id"]] = r
    known = set(rows_by_id)

    for i, r in sorted(rows_by_id.items()):
        kind = status_kind(r["status"])
        if kind is None:
            finds.append(("SCHEMA", "%s: unknown status '%s' (open | done | "
                          "blocked:<ids> | parked:<reason> | stale | "
                          "superseded-by:<id>)" % (i, r["status"][:60])))
        if r["evidence"] != "-" and evidence_sha(r["evidence"]) is None:
            finds.append(("SCHEMA", "%s: evidence is neither '-', a sha256, "
                          "nor <path>@<sha256>" % i))
        if r["claim"] != "-" and not CLAIM_RE.match(r["claim"]):
            finds.append(("SCHEMA", "%s: claim is neither '-' nor "
                          "<executor>@<YYYY-MM-DD>" % i))
        # edge references
        refs = [t for t in need_tokens(r["needs"])
                if is_id_like(t) and t not in known]
        if kind == "blocked":
            refs += [t.strip() for t in r["status"].split(":", 1)[1].split(",")
                     if t.strip() and is_id_like(t.strip())
                     and t.strip() not in known]
        if kind == "superseded-by":
            t = r["status"].split(":", 1)[1].strip()
            if is_id_like(t) and t not in known:
                refs.append(t)
        for t in sorted(set(refs)):
            finds.append(("DANGLING-REF", "%s -> '%s' matches no step"
                          % (i, t)))
        # done is evidenced, never declared
        if r["status"] == "done":
            if r["evidence"] == "-" or r["produces"] in ("", "-"):
                finds.append(("DONE-UNEVIDENCED", "%s: done with %s" %
                              (i, "no evidence sha" if r["evidence"] == "-"
                               else "no produces path")))

    for i in find_cycles(rows_by_id):
        finds.append(("CYCLE", "%s is on a needs cycle" % i))

    # disk diff -- open graphs only (a closed graph's gitignored outputs are
    # legitimately gone)
    false_dones = []
    if is_open:
        tree = tree_root_for(path, root)
        for i, r in sorted(rows_by_id.items()):
            if r["status"] != "done" or r["produces"] in ("", "-"):
                continue
            sha = evidence_sha(r["evidence"])
            if sha is None:
                continue
            p = os.path.join(tree, r["produces"])
            if not os.path.isfile(p):
                false_dones.append(i)
                finds.append(("FALSE-DONE", "%s: produces %s missing on disk"
                              % (i, r["produces"])))
            elif sha256_file(p) != sha:
                false_dones.append(i)
                finds.append(("FALSE-DONE", "%s: produces %s sha256 != "
                              "recorded evidence" % (i, r["produces"])))
        stale_from = {}
        for seed in false_dones:
            for i in descendants_of({seed}, rows_by_id):
                if rows_by_id[i]["status"] == "done":
                    stale_from.setdefault(i, set()).add(seed)
        for i in sorted(stale_from):
            finds.append(("STALE-DOWNSTREAM", "%s: done downstream of "
                          "false-done(s) %s -- re-run candidate"
                          % (i, ",".join(sorted(stale_from[i])))))

    frontier = frontier_of(rows_by_id)
    nd_present, nd_ptr = parse_next_dispatch(body)
    if rows_by_id and not nd_present:
        finds.append(("SCHEMA", "missing next-dispatch: line"))
    if is_open and nd_present:
        if nd_ptr is None and frontier:
            finds.append(("NEXT-DISPATCH-STALE", "stamped none/-, recomputed "
                          "frontier is %s" % ",".join(frontier)))
        elif nd_ptr is not None and nd_ptr not in frontier:
            finds.append(("NEXT-DISPATCH-STALE", "stamped %s, recomputed "
                          "frontier is %s" % (nd_ptr,
                                              ",".join(frontier) or "empty")))

    if is_open:
        for i, r in sorted(rows_by_id.items()):
            m = CLAIM_RE.match(r["claim"])
            if not m or status_kind(r["status"]) in ("done", "parked",
                                                     "superseded-by"):
                continue
            try:
                claimed = datetime.date.fromisoformat(m.group(2))
            except ValueError:
                finds.append(("SCHEMA", "%s: claim date unparseable" % i))
                continue
            age = (today - claimed).days
            if age > ttl_days:
                finds.append(("CLAIM-EXPIRED", "%s: claimed by %s %dd ago "
                              "(window %dd)" % (i, m.group(1), age,
                                                ttl_days)))
    if state in STATE_CLOSED:
        leftover = sorted(i for i, r in rows_by_id.items()
                          if status_kind(r["status"]) in ("open", "blocked",
                                                          "stale"))
        if leftover:
            finds.append(("CLOSED-WITH-OPEN", "state %s but %s not done"
                          % (state, ",".join(leftover))))

    done_n = sum(1 for r in rows_by_id.values() if r["status"] == "done")
    summary = {"file": path, "effort": fm.get("effort", ""), "state": state,
               "steps": len(rows_by_id), "done": done_n,
               "invariants": invariants, "frontier": frontier,
               "false_dones": sorted(set(false_dones)),
               "next_dispatch": nd_ptr if nd_present else None,
               "findings": len(finds)}
    return summary, finds


def main():
    ap = argparse.ArgumentParser(
        description="work-graph checker (report-only; exit 0 always)")
    ap.add_argument("paths", nargs="*",
                    help="graph files (default: <root>/ledger/graphs/*.md)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--claim-ttl-days", type=int, default=3)
    ap.add_argument("--today", default=None, metavar="YYYY-MM-DD")
    o = ap.parse_args()
    root = resolve_root(o.root)
    today = (datetime.date.fromisoformat(o.today) if o.today
             else datetime.date.today())
    paths = o.paths or sorted(
        glob.glob(os.path.join(root, "ledger", "graphs", "*.md")))
    paths = [p for p in paths
             if os.path.basename(p).lower() != "readme.md"]
    if not paths:
        print("GRAPH-CHECK: no work-graphs under %s (convention: "
              "ledger/graphs/<effort-slug>.md)" % os.path.join(
                  root, "ledger", "graphs"))
        return 0
    out = {"graphs": [], "findings": []}
    for p in paths:
        summary, finds = check_graph(p, root, o.claim_ttl_days, today)
        rel = os.path.relpath(p, root)
        rel = p if rel.startswith("..") else rel
        summary["file"] = rel
        out["graphs"].append(summary)
        for rule, detail in finds:
            out["findings"].append({"file": rel, "rule": rule,
                                    "detail": detail})
    if o.json:
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    for g in out["graphs"]:
        print(g["file"])
        if "error" in g:
            continue
        print("  effort: %s  state: %s  steps: %d (done %d)  invariants: %d"
              % (g["effort"] or "?", g["state"] or "?", g["steps"],
                 g["done"], g["invariants"]))
        print("  frontier: %s" % (", ".join(g["frontier"]) or "(empty)"))
        print("  next-dispatch (stamped): %s"
              % (g["next_dispatch"] if g["next_dispatch"] is not None
                 else "-"))
    for f in out["findings"]:
        print("GRAPH-CHECK\t%s\t%s\t%s" % (f["file"], f["rule"],
                                           f["detail"]))
    print("GRAPH-CHECK: %d graph(s), %d finding(s)"
          % (len(out["graphs"]), len(out["findings"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
