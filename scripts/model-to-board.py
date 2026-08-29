#!/usr/bin/env python3
"""model-to-board.py -- adopted-model -> renderer adapter.

Usage: model-to-board.py <operating-model-dir> <out-model.json>

Reads an operating-model directory (operating-model/SCHEMA.md node format: one markdown file
per node, YAML frontmatter as the machine surface) and writes the board model.json shape
the render/parity instruments consume as ground truth (grammar/slice-board.md §5):

  nodes[]  -- {id, type, label, lane, column, status, summary, pivotal?, hotspot?, debt?,
               stack_row?, attaches-to?, attach-role?} plus the SCHEMA.md relational keys
               passed through as resolved id lists (parity_score.py reads source keys like
               issued-by/reads/emits/trigger/then off the nodes themselves).
  edges[]  -- {from, to, kind} in readback-protocol.md sec.4's five reader-facing kinds ONLY
               (emission | issue | trigger | read | attachment); score_cli.py raises on any
               other kind. Canonical directions follow make_synthetic.py:
                 emission:   command -> event (command -> external -> event when mediated)
                 issue:      actor|policy -> command
                 trigger:    event -> policy
                 read:       read-model -> consumer (command|actor); event -> read-model
                             for projections (projects-from / event consumed-by read-model)
                 attachment: actor -> spine node it is generally involved with

Derivation sources (SCHEMA.md common + typed + relational keys, both the ratified relational-key
keys and the pre-ratification keys real adopted models actually carry):
  emits / emitted-by            -> emission (event targets) ; emits naming a read-model marks
                                   the command as that read-model's writer (ordering + tuck)
  issued-by (actor OR policy)   -> issue
  then                          -> issue (policy -> command)
  trigger                       -> trigger, when the value resolves to event id(s); prose
                                   triggers (pre-ratification models) contribute no edge, but
                                   a command id inside trigger prose is used as a layout hint
  reads / consumed-by           -> read (read-model side); an actor inside reads/consumed-by
                                   is an involvement -> attachment
  projects-from / event
    consumed-by read-model      -> read (event -> read-model)
  cast-as / invoked-on (actor)  -> attachment; invoked-on external -> mediated emission

Layout (research/eventstorming/board-layout-and-direction.md + readback-protocol.md):
  - one lane per bounded context (contexts are the immediate subdirectories that contain
    node files); lane order = sorted context names; columns are GLOBAL across lanes.
  - columns come from a topological pass over the spine (command/event/policy/external/
    aggregate) using prioritized ordering constraints (emission > trigger > issue >
    event-consumption > read-model write->read chains > invariant/prose hints); lower-priority
    constraints that would close a cycle are dropped deterministically. A pull-right pass then
    sits each command directly before its emitted event (and each policy before its
    then-command) where its own dependencies permit. Same-layer nodes are serialized into
    consecutive columns (deterministic order), EXCEPT alternate-outcome events sharing an
    emitter and a layer, which stack in one column via stack_row (grammar: happy path on top,
    approximated as most-consumed first). Columns start at 1, never 0 -- the grammar's "never
    anchor the first event at the leftmost edge".
  - actors and read-models never own a column: each gets an explicit attaches-to/attach-role
    (issue-corner / general / read-below) chosen so no (anchor, role) slot is used twice --
    the renderer's cascade offset for shared slots fans cards at 14px and buries labels, so
    slot exclusivity IS the legibility guarantee. A command and its own issue-corner actor
    share one read-below tuck slot (their tuck footprints intersect).
  - anchor preferences are evidence-ordered; anchors chosen with no frontmatter relationship
    (body-text id mention, or pure placement fallback) are listed in meta.fallback_anchored,
    and every rendered adjacency is materialized as an edge so the board and edges[] agree
    (board-only additions listed in meta.board_only_edges).
  - pivotal: frontmatter `pivotal: true`, or an event referenced from another context.
  - status hotspot/debt map to the renderer's hotspot/debt flags (kept as status too).

Deterministic: stdlib only, stable sorts everywhere, no timestamps. Two runs on the same
input tree produce byte-identical JSON.
"""
import json
import os
import re
import sys

ALL_TYPES = ("actor", "command", "event", "policy", "read-model", "external", "aggregate")
ATTACHED_TYPES = {"actor", "read-model"}
# within-layer serialization order: the process grammar's atom order
TYPE_RANK = {"command": 0, "external": 1, "aggregate": 2, "event": 3, "policy": 4}
EDGE_KIND_ORDER = {"emission": 0, "issue": 1, "trigger": 2, "read": 3, "attachment": 4}

ROLE_ISSUE_CORNER = "issue-corner"
ROLE_GENERAL = "general"
ROLE_READ_BELOW = "read-below"

# relational keys whose values are scanned for node-id references
REF_KEYS = (
    "emits", "emitted-by", "issued-by", "reads", "consumed-by", "trigger", "then",
    "cast-as", "invoked-on", "invoked-by", "projects-from", "maintainer",
    "invariants-enforced", "invariants-requested",
)

REF_RE = re.compile(
    r"\b(actor|command|event|policy|read-model|readmodel|read_model|external|aggregate)"
    r"/([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
)


def norm_type(t):
    t = (t or "").strip().lower().replace("_", "-")
    return "read-model" if t == "readmodel" else t


def find_refs(text):
    """All node-id-shaped references in a string, type prefix normalized, order preserved,
    de-duplicated."""
    out, seen = [], set()
    for m in REF_RE.finditer(text or ""):
        rid = f"{norm_type(m.group(1))}/{m.group(2)}"
        if rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def parse_frontmatter(text):
    """Minimal YAML-subset parser: `key: value` lines between the first two `---` fences,
    plus `key:` followed by `- item` block lists. Returns (fm_dict_of_raw_strings, body)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    fm, i, cur_key = {}, 1, None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            i += 1
            break
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m:
            cur_key = m.group(1)
            fm[cur_key] = m.group(2).strip()
        elif cur_key and re.match(r"^\s+-\s+", line):
            fm[cur_key] = (fm[cur_key] + ", " + re.sub(r"^\s+-\s+", "", line).strip()).strip(", ")
        i += 1
    return fm, "\n".join(lines[i:])


def title_case_slug(slug):
    return " ".join(w[:1].upper() + w[1:] for w in slug.split("-") if w)


# ============================================================================ model loading
def load_nodes(model_dir):
    """Discover contexts (immediate subdirectories containing node files) and parse every
    node. Falls back to treating model_dir itself as a single context if no subdirectory
    yields nodes."""

    def nodes_under(root, context):
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                if not fn.endswith(".md") or fn in ("model.md", "SCHEMA.md"):
                    continue
                path = os.path.join(dirpath, fn)
                with open(path, encoding="utf-8") as f:
                    fm, body = parse_frontmatter(f.read())
                if not fm or "id" not in fm or "type" not in fm:
                    continue
                ntype = norm_type(fm["type"])
                if ntype not in ALL_TYPES:
                    continue
                nid = fm["id"].strip()
                found.append({
                    "id": nid,
                    "type": ntype,
                    "context": context,
                    "status": (fm.get("status") or "current").strip().lower(),
                    "summary": fm.get("summary", ""),
                    "pivotal_fm": (fm.get("pivotal", "").strip().lower() == "true"),
                    "reason": fm.get("reason", ""),
                    "fm": fm,
                    "body": body,
                    "path": os.path.relpath(path, model_dir),
                })
        return found

    contexts, nodes = [], []
    for entry in sorted(os.listdir(model_dir)):
        sub = os.path.join(model_dir, entry)
        if os.path.isdir(sub):
            got = nodes_under(sub, entry)
            if got:
                contexts.append(entry)
                nodes.extend(got)
    if not nodes:
        ctx = os.path.basename(os.path.normpath(model_dir))
        nodes = nodes_under(model_dir, ctx)
        contexts = [ctx] if nodes else []
    return contexts, nodes


# ============================================================================ ref resolution
def build_ref_views(nodes):
    """Attach node['refs'][key] = [resolved ids] for every REF_KEY, resolving within the
    node's own context first, then globally when unique. Returns list of unresolved refs."""
    by_ctx_id = {(n["context"], n["id"]): n for n in nodes}
    by_id = {}
    for n in nodes:
        by_id.setdefault(n["id"], []).append(n)
    dupes = sorted(i for i, ns in by_id.items() if len(ns) > 1)
    if dupes:
        raise SystemExit(
            "model-to-board.py: node id(s) collide across contexts, which would make the "
            f"flat renderer model ambiguous: {dupes} -- refusing to guess."
        )

    unresolved = []
    for n in nodes:
        refs = {}
        for key in REF_KEYS:
            if key not in n["fm"]:
                continue
            resolved, missing = [], []
            for rid in find_refs(n["fm"][key]):
                if (n["context"], rid) in by_ctx_id or rid in by_id:
                    resolved.append(rid)
                else:
                    missing.append(rid)
            refs[key] = resolved
            for rid in missing:
                unresolved.append(f"{n['id']}.{key} -> {rid}")
        n["refs"] = refs
    return unresolved


def refs_of(node, key, types=None):
    out = node["refs"].get(key, [])
    if types is None:
        return list(out)
    return [r for r in out if r.split("/", 1)[0] in types]


# ============================================================================ edge derivation
def derive_edges(nodes):
    """The five readback kinds, canonical directions per make_synthetic.py. Returns a list of
    (from, to, kind), de-duplicated, deterministic order."""
    idx = {n["id"]: n for n in nodes}
    edges = set()

    def add(f, t, kind):
        if f != t and f in idx and t in idx:
            edges.add((f, t, kind))

    for n in nodes:
        nid, t = n["id"], n["type"]
        if t == "command":
            for ev in refs_of(n, "emits", {"event"}):
                add(nid, ev, "emission")
            for ext in refs_of(n, "invoked-on", {"external", "aggregate"}):
                add(nid, ext, "emission")
            for issuer in refs_of(n, "issued-by", {"actor", "policy"}):
                add(issuer, nid, "issue")
            for rm in refs_of(n, "reads", {"read-model"}):
                add(rm, nid, "read")
            for a in refs_of(n, "cast-as", {"actor"}) + refs_of(n, "invoked-on", {"actor"}) \
                    + refs_of(n, "reads", {"actor"}):
                add(a, nid, "attachment")
        elif t == "event":
            for src in refs_of(n, "emitted-by", {"command", "external", "aggregate"}):
                add(src, nid, "emission")
            for c in refs_of(n, "consumed-by"):
                ct = c.split("/", 1)[0]
                if ct == "policy":
                    add(nid, c, "trigger")
                elif ct == "read-model":
                    add(nid, c, "read")
                elif ct == "command":
                    pass  # ordering-only dependency, not a readback edge kind
                elif ct == "actor":
                    add(c, nid, "attachment")
        elif t == "policy":
            for ev in refs_of(n, "trigger", {"event"}):
                add(ev, nid, "trigger")
            for cmd in refs_of(n, "then", {"command"}):
                add(nid, cmd, "issue")
        elif t == "read-model":
            for ev in refs_of(n, "projects-from", {"event"}):
                add(ev, nid, "read")
            for c in refs_of(n, "consumed-by", {"command", "actor"}):
                add(nid, c, "read")
        elif t in ("external", "aggregate"):
            for ev in refs_of(n, "emits", {"event"}):
                add(nid, ev, "emission")
            for cmd in refs_of(n, "invoked-by", {"command"}):
                add(cmd, nid, "emission")

    return sorted(edges, key=lambda e: (EDGE_KIND_ORDER[e[2]], e[0], e[1]))


# ============================================================================ spine layout
def readmodel_writers(nodes, idx):
    """read-model id -> [command ids that write it] (maintainer + commands 'emitting' it --
    both spellings occur in adopted models)."""
    writers = {}
    for n in nodes:
        if n["type"] == "read-model":
            writers[n["id"]] = refs_of(n, "maintainer", {"command"})
    for n in nodes:
        if n["type"] == "command":
            for rm in refs_of(n, "emits", {"read-model"}):
                if n["id"] not in writers.setdefault(rm, []):
                    writers[rm].append(n["id"])
    return writers


def readmodel_readers(nodes, idx):
    readers = {}
    for n in nodes:
        if n["type"] == "read-model":
            readers[n["id"]] = refs_of(n, "consumed-by", {"command"})
    for n in nodes:
        if n["type"] == "command":
            for rm in refs_of(n, "reads", {"read-model"}):
                if rm in readers and n["id"] not in readers[rm]:
                    readers[rm].append(n["id"])
                elif rm not in readers:
                    readers[rm] = [n["id"]]
    return readers


def build_ordering_constraints(nodes, edges, idx):
    """Prioritized (from, to) constraint classes over spine nodes only."""
    spine = {n["id"] for n in nodes if n["type"] not in ATTACHED_TYPES}
    classes = [[] for _ in range(6)]  # A..F

    for f, t, kind in edges:
        if f in spine and t in spine:
            if kind == "emission":
                classes[0].append((f, t))
            elif kind == "trigger":
                classes[1].append((f, t))
            elif kind == "issue":  # only policy->command survives the spine filter
                classes[2].append((f, t))

    for n in nodes:
        if n["type"] == "event":
            for cmd in refs_of(n, "consumed-by", {"command"}):
                classes[3].append((n["id"], cmd))

    writers = readmodel_writers(nodes, idx)
    readers = readmodel_readers(nodes, idx)
    projections = {}  # rm -> [event ids projected in]
    for f, t, kind in edges:
        if kind == "read" and f.startswith("event/"):
            projections.setdefault(t, []).append(f)
    for rm in sorted(set(writers) | set(readers)):
        for r in readers.get(rm, []):
            for w in writers.get(rm, []):
                if w != r:
                    classes[4].append((w, r))
            for ev in projections.get(rm, []):
                classes[4].append((ev, r))

    for n in nodes:
        if n["type"] == "command":
            for pol in refs_of(n, "invariants-enforced", {"policy"}) \
                    + refs_of(n, "invariants-requested", {"policy"}):
                classes[5].append((n["id"], pol))
        elif n["type"] == "policy":
            for cmd in refs_of(n, "trigger", {"command"}):
                classes[5].append((cmd, n["id"]))

    return spine, classes


def layer_spine(spine, classes):
    """Build a DAG by adding constraint classes in priority order, skipping any edge that
    would close a cycle; longest-path layer from sources; then a pull-right pass so
    producers sit directly before their products where dependencies permit."""
    adj = {s: set() for s in spine}
    radj = {s: set() for s in spine}

    def reaches(a, b):
        stack, seen = [a], set()
        while stack:
            x = stack.pop()
            if x == b:
                return True
            if x in seen:
                continue
            seen.add(x)
            stack.extend(adj[x])
        return False

    for cls in classes:
        for f, t in sorted(set(cls)):
            if f == t or t in adj[f]:
                continue
            if reaches(t, f):
                continue  # deterministic cycle-skip: lower-priority edge dropped
            adj[f].add(t)
            radj[t].add(f)

    # longest path from sources (Kahn order)
    indeg = {s: len(radj[s]) for s in spine}
    layer = {s: 1 for s in spine}
    queue = sorted(s for s in spine if indeg[s] == 0)
    topo = []
    while queue:
        x = queue.pop(0)
        topo.append(x)
        for y in sorted(adj[x]):
            layer[y] = max(layer[y], layer[x] + 1)
            indeg[y] -= 1
            if indeg[y] == 0:
                queue.append(y)
        queue.sort()
    assert len(topo) == len(spine), "internal: cycle survived cycle-skip"

    for x in reversed(topo):  # pull-right
        if adj[x]:
            layer[x] = max(layer[x], min(layer[y] for y in adj[x]) - 1)
    return layer


def assign_columns(nodes, layer, idx):
    """Serialize spine nodes into consecutive global columns; alternate-outcome events
    (same layer, shared emitter) stack in one column via stack_row."""
    spine_nodes = [n for n in nodes if n["type"] not in ATTACHED_TYPES]

    def emitters(ev_node):
        ems = set(refs_of(ev_node, "emitted-by", {"command", "external", "aggregate"}))
        for m in spine_nodes:
            if m["type"] in ("command", "external", "aggregate") \
                    and ev_node["id"] in refs_of(m, "emits", {"event"}):
                ems.add(m["id"])
        return ems

    def sort_key(n):
        alt = -len(n["refs"].get("consumed-by", [])) if n["type"] == "event" else 0
        return (layer[n["id"]], TYPE_RANK.get(n["type"], 9), alt, n["id"])

    col, placed_events = 0, []  # placed_events: (node, layer, emitters, column)
    columns, stack_rows = {}, {}
    for n in sorted(spine_nodes, key=sort_key):
        if n["type"] == "event":
            ems = emitters(n)
            partner = next((p for p in placed_events
                            if p[1] == layer[n["id"]] and p[2] & ems), None)
            if partner is not None:
                columns[n["id"]] = partner[3]
                stack_rows[n["id"]] = max(
                    stack_rows.get(e["id"], 0) for e in spine_nodes
                    if columns.get(e["id"]) == partner[3]) + 1
                placed_events.append((n, layer[n["id"]], ems, partner[3]))
                continue
            col += 1
            columns[n["id"]] = col
            stack_rows[n["id"]] = 0
            placed_events.append((n, layer[n["id"]], ems, col))
        else:
            col += 1
            columns[n["id"]] = col
            stack_rows[n["id"]] = 0
    return columns, stack_rows


# ============================================================================ attachments
def mention_links(nodes):
    """Bidirectional body-text mention map: node id -> [ids of spine nodes linked by a plain
    or [[wiki]] id mention in either body]. Evidence-weakest anchor source."""
    spine_ids = {n["id"] for n in nodes if n["type"] not in ATTACHED_TYPES}
    links = {n["id"]: [] for n in nodes}
    for n in nodes:
        body_refs = set(find_refs(n["body"]))
        if n["type"] in ATTACHED_TYPES:
            for rid in sorted(body_refs & spine_ids):
                if rid not in links[n["id"]]:
                    links[n["id"]].append(rid)
        else:
            for rid in sorted(body_refs):
                if rid in links and rid != n["id"]:
                    links[rid].append(n["id"])
    return links


def assign_attachments(nodes, edges, columns, stack_rows, idx):
    """Explicit attaches-to/attach-role for every actor and read-model, one occupant per
    (anchor, role) slot. Returns (attach, fallback_anchored, board_only_edges) where attach
    maps node id -> (anchor id, role); the edge list is extended in place so every rendered
    adjacency is also ground truth."""
    corner_slot, general_slot, tuck_slot = {}, {}, {}
    attach = {}
    fallback_anchored, board_only = [], []
    edge_set = set(edges)
    mentions = mention_links(nodes)

    def colkey(nid):
        return (columns.get(nid, 10 ** 6), nid)

    def tuck_key(anchor_id):
        """A command and its issue-corner actor share one tuck footprint (their read-below
        rectangles intersect), so they share one slot key."""
        a = attach.get(anchor_id)
        if a and a[1] == ROLE_ISSUE_CORNER:
            return ("cmd", a[0])  # anchor IS a corner actor: share its command's footprint
        if anchor_id.startswith("command/"):
            return ("cmd", anchor_id)
        return ("self", anchor_id)

    def add_edge(f, t, kind, note):
        if (f, t, kind) not in edge_set:
            edge_set.add((f, t, kind))
            edges.append((f, t, kind))
            board_only.append(f"{f} -[{kind}]-> {t} ({note})")

    spine_nodes = sorted((n for n in nodes if n["type"] not in ATTACHED_TYPES),
                         key=lambda n: colkey(n["id"]))
    commands = [n for n in spine_nodes if n["type"] == "command"]
    stacked = {i for i, sr in stack_rows.items() if sr > 0}
    # mention/fallback anchor pool: a stacked card would put a general actor on top of the
    # primary card above it; an event anchor would put a tuck on top of any stacked
    # alternate below it -- exclude both.
    safe_general_pool = [m for m in spine_nodes if m["id"] not in stacked]
    safe_tuck_pool = [m for m in spine_nodes
                      if m["id"] not in stacked and m["type"] != "event"]

    # ---- actors ----
    # Staged by evidence class so a weakly-anchored actor can never steal a slot that a
    # later-processed actor holds real evidence for: every actor gets its class-a chance
    # before ANY actor falls through to class b, and so on.
    writers_map = readmodel_writers(nodes, idx)

    def actor_candidates(nid, cls):
        issued = sorted((c["id"] for c in commands
                         if nid in refs_of(c, "issued-by", {"actor"})), key=colkey)
        if cls == "a":  # decision corner on a command this actor issues
            return issued
        if cls == "b":  # general involvement stated in frontmatter
            cands = list(issued)  # dual-issued command whose corner is taken
            for c in commands:
                keys = refs_of(c, "cast-as", {"actor"}) + refs_of(c, "invoked-on", {"actor"}) \
                    + refs_of(c, "reads", {"actor"})
                if nid in keys:
                    cands.append(c["id"])
            for ev in (m for m in spine_nodes if m["type"] == "event"):
                # never attach above a stacked alternate card: the actor would land on the
                # primary card above it
                if nid in refs_of(ev, "consumed-by", {"actor"}) \
                        and not stack_rows.get(ev["id"], 0):
                    cands.append(ev["id"])
            return sorted(set(cands), key=colkey)
        if cls == "c":  # consults a read-model -> general on that read-model's writer
            cands = []
            for rm in sorted(writers_map):
                rm_node = idx.get(rm)
                if rm_node and nid in refs_of(rm_node, "consumed-by", {"actor"}):
                    cands.extend(writers_map[rm])
            return sorted(set(cands), key=colkey)
        if cls == "d":  # body-text mention
            return sorted(set(mentions.get(nid, [])) - stacked, key=colkey)
        return [m["id"] for m in commands] + [m["id"] for m in safe_general_pool]  # e

    CLASS_NOTE = {"c": "via read-model consultation", "d": "body mention",
                  "e": "placement fallback"}
    unplaced = [n["id"] for n in sorted((n for n in nodes if n["type"] == "actor"),
                                        key=lambda n: n["id"])]
    for cls in ("a", "b", "c", "d", "e"):
        still = []
        for nid in unplaced:
            placed = False
            for cand in actor_candidates(nid, cls):
                if cls == "a":
                    if cand not in corner_slot:
                        corner_slot[cand] = nid
                        attach[nid] = (cand, ROLE_ISSUE_CORNER)
                        placed = True
                elif cand not in general_slot:
                    general_slot[cand] = nid
                    attach[nid] = (cand, ROLE_GENERAL)
                    # every rendered general adjacency is ground truth
                    add_edge(nid, cand, "attachment", CLASS_NOTE.get(cls, "general adjacency"))
                    if cls in ("d", "e"):
                        fallback_anchored.append(f"{nid} -> {cand} ({CLASS_NOTE[cls]})")
                    placed = True
                if placed:
                    break
            if not placed:
                still.append(nid)
        unplaced = still
    assert not unplaced, f"could not anchor actor(s): {unplaced}"

    # ---- read-models ----
    # Candidates are scored (occlusion_penalty, evidence_class, column, id) and the best free
    # slot wins. Occlusion facts (from render_board.py's fixed geometry, 18px Arial Bold at
    # ~12px/char): a tuck under an ISSUE-CORNER actor covers the left 20px of that actor's
    # command, which clips label glyphs once a label word passes ~8 chars (line wider than
    # ~100px starts inside the strip); a tuck directly under a command covers its bottom 30px,
    # which shaves the 4th label line once a label wraps that far (>=4 words is the proxy).
    # A tuck under a GENERAL actor floats fully clear (GENERAL_GAP is sized for it).
    def corner_sliver_risky(cmd_id):
        # 8-char caps words (~105px at 18px Arial Bold) already start inside the 20px strip
        return any(len(w) >= 8 for w in title_case_slug(cmd_id.split("/", 1)[1]).split())

    def bottom_strip_risky(cmd_id):
        return len(title_case_slug(cmd_id.split("/", 1)[1]).split()) >= 4

    readers = readmodel_readers(nodes, idx)
    safe_ids = {m["id"] for m in safe_tuck_pool}

    def anchor_col(aid):  # actors order by their own anchor's column
        if aid in attach:
            return (columns.get(attach[aid][0], 10 ** 6), aid)
        return colkey(aid)

    def rm_candidates(n):
        """(occlusion_penalty, evidence_class, column_key, anchor_id, note_or_None) list."""
        nid = n["id"]
        cands = []
        for actor_id in refs_of(n, "consumed-by", {"actor"}):
            if actor_id not in attach:
                continue
            anch, role = attach[actor_id]
            if role == ROLE_GENERAL and anch in corner_slot:
                # a tuck under a general actor descends to anchor.y-30 and always intersects
                # a corner actor's card on the same command (x overlap is structural) -- skip
                continue
            pen = 0 if role == ROLE_GENERAL else (1 if corner_sliver_risky(anch) else 0)
            cands.append((pen, 0, anchor_col(actor_id), actor_id, None))
        for cmd in readers.get(nid, []):
            cands.append((1 if bottom_strip_risky(cmd) else 0, 1, colkey(cmd), cmd, None))
        for cmd in writers_map.get(nid, []):
            cands.append((1 if bottom_strip_risky(cmd) else 0, 2, colkey(cmd), cmd,
                          "writer tuck"))
        for cand in set(mentions.get(nid, [])) & safe_ids:
            cands.append((1 if bottom_strip_risky(cand) else 0, 3, colkey(cand), cand,
                          "body mention"))
        for pool_rank, pool in ((4, commands), (5, safe_tuck_pool)):
            for m in pool:
                cands.append((1 if bottom_strip_risky(m["id"]) else 0, pool_rank,
                              colkey(m["id"]), m["id"], "placement fallback"))
        return cands

    # Staged by evidence bucket, like the actors: every read-model gets its evidence-anchor
    # chance (classes 0-2) before any read-model falls to a mention (3) or a pure placement
    # fallback (4-5). WITHIN a bucket the occlusion penalty sorts first, so legibility picks
    # among equally-grounded anchors but never overrides evidence.
    unplaced_rm = [n for n in sorted((n for n in nodes if n["type"] == "read-model"),
                                     key=lambda n: n["id"])]
    for bucket_classes in ((0, 1, 2), (3,), (4, 5)):
        still = []
        for n in unplaced_rm:
            nid = n["id"]
            cands = [c for c in rm_candidates(n) if c[1] in bucket_classes]
            placed = False
            for pen, cls, _ck, cand, note in sorted(cands, key=lambda c: (c[0], c[1], c[2])):
                if tuck_key(cand) in tuck_slot:
                    continue
                tuck_slot[tuck_key(cand)] = nid
                attach[nid] = (cand, ROLE_READ_BELOW)
                if note is not None:
                    add_edge(nid, cand, "read", note)
                    fallback_anchored.append(f"{nid} -> {cand} ({note})")
                placed = True
                break
            if not placed:
                still.append(n)
        unplaced_rm = still
    assert not unplaced_rm, f"could not anchor read-model(s): {[n['id'] for n in unplaced_rm]}"

    return attach, fallback_anchored, board_only


# ============================================================================ assembly
def compile_model(model_dir):
    contexts, nodes = load_nodes(model_dir)
    if not nodes:
        raise SystemExit(f"model-to-board.py: no SCHEMA.md-shaped nodes found under {model_dir}")
    unresolved = build_ref_views(nodes)
    idx = {n["id"]: n for n in nodes}

    edges = derive_edges(nodes)
    spine, classes = build_ordering_constraints(nodes, edges, idx)
    layer = layer_spine(spine, classes)
    columns, stack_rows = assign_columns(nodes, layer, idx)
    attach, fallback_anchored, board_only = assign_attachments(
        nodes, edges, columns, stack_rows, idx)

    # attached nodes inherit their anchor chain's column (never own one)
    def inherited_column(nid, depth=0):
        if nid in columns:
            return columns[nid]
        assert depth < 4 and nid in attach, f"unanchored attached node {nid}"
        return inherited_column(attach[nid][0], depth + 1)

    # pivotal: frontmatter flag, or an event referenced from another context
    pivotal_ids = set()
    for n in nodes:
        if n["type"] == "event" and n["pivotal_fm"]:
            pivotal_ids.add(n["id"])
    for n in nodes:
        for key in REF_KEYS:
            for rid in n["refs"].get(key, []):
                tgt = idx.get(rid)
                if tgt and tgt["type"] == "event" and tgt["context"] != n["context"]:
                    pivotal_ids.add(rid)

    lane_of = {n["id"]: n["context"] for n in nodes}
    lane_order = sorted(set(lane_of.values()))
    lane_rank = {ln: i for i, ln in enumerate(lane_order)}

    out_nodes = []
    for n in nodes:
        nid = n["id"]
        rec = {
            "id": nid,
            "type": n["type"],
            "label": title_case_slug(nid.split("/", 1)[1]),
            "lane": n["context"],
            "column": inherited_column(nid),
            "status": n["status"],
        }
        if n["summary"]:
            rec["summary"] = n["summary"]
        if nid in pivotal_ids:
            rec["pivotal"] = True
        if n["status"] == "hotspot":
            rec["hotspot"] = True
        if n["status"] == "debt":
            rec["debt"] = True
            if n["reason"]:
                rec["reason"] = n["reason"]
        if stack_rows.get(nid, 0):
            rec["stack_row"] = stack_rows[nid]
        if nid in attach:
            rec["attaches-to"] = attach[nid][0]
            rec["attach-role"] = attach[nid][1]
        for key in REF_KEYS:  # relational pass-through: parity harness reads these off nodes
            if key in n["refs"]:
                if n["refs"][key]:
                    rec[key] = n["refs"][key]
                elif n["fm"].get(key, "").strip() not in ("", "[]"):
                    rec[key + "-unresolved"] = n["fm"][key]
        out_nodes.append(rec)

    out_nodes.sort(key=lambda r: (lane_rank[r["lane"]], r["column"],
                                  r.get("stack_row", 0), TYPE_RANK.get(r["type"], 9), r["id"]))

    # ---- self-checks (make_synthetic.py's own invariants, minus its synthetic-only ones)
    ids = {r["id"] for r in out_nodes}
    labels = [r["label"] for r in out_nodes]
    assert len(labels) == len(set(labels)), \
        f"duplicate labels (score_cli.py needs unambiguous labels): {sorted(set(l for l in labels if labels.count(l) > 1))}"
    for f, t, k in edges:
        assert f in ids and t in ids, f"edge endpoint unknown: {(f, t, k)}"
        assert k in EDGE_KIND_ORDER, f"non-readback edge kind: {k}"
    for r in out_nodes:
        assert isinstance(r["column"], int) and r["column"] >= 1, f"bad column on {r['id']}"
        if r["type"] in ATTACHED_TYPES:
            assert r.get("attaches-to") in ids, f"unanchored attached node {r['id']}"

    edges_out = [{"from": f, "to": t, "kind": k}
                 for f, t, k in sorted(edges, key=lambda e: (EDGE_KIND_ORDER[e[2]], e[0], e[1]))]
    by_type, by_kind = {}, {}
    for r in out_nodes:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    for e in edges_out:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1

    return {
        "meta": {
            "generator": "scripts/model-to-board.py",
            "source_dir": os.path.normpath(model_dir),
            "contexts": lane_order,
            "lanes": lane_order,
            "node_count": len(out_nodes),
            "edge_count": len(edges_out),
            "column_count": max(columns.values()) if columns else 0,
            "fallback_anchored": fallback_anchored,
            "board_only_edges": board_only,
            "unresolved_refs": sorted(unresolved),
            "schema_note": (
                "nodes follow operating-model/SCHEMA.md vocabulary; lane/column/attachment "
                "placement and edges[] kinds follow the board readback protocol "
                "(grammar/slice-board.md); renderer input contract per render_flow.py."
            ),
        },
        "nodes": out_nodes,
        "edges": edges_out,
        "pivotal_ids": sorted(pivotal_ids),
        "hotspot_ids": sorted(r["id"] for r in out_nodes if r.get("hotspot")),
        "debt_ids": sorted(r["id"] for r in out_nodes if r.get("debt")),
        "census": {"by_type": by_type, "by_edge_kind": by_kind},
    }


def main(argv):
    if len(argv) != 3:
        print("usage: model-to-board.py <operating-model-dir> <out-model.json>", file=sys.stderr)
        return 2
    model_dir, out_path = argv[1], argv[2]
    if not os.path.isdir(model_dir):
        print(f"model-to-board.py: not a directory: {model_dir}", file=sys.stderr)
        return 2
    model = compile_model(model_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2)
        f.write("\n")
    m = model["meta"]
    print(f"wrote {out_path}: {m['node_count']} nodes ({model['census']['by_type']}), "
          f"{m['edge_count']} edges ({model['census']['by_edge_kind']}), "
          f"{m['column_count']} columns, lanes={m['lanes']}")
    for w in m["unresolved_refs"]:
        print(f"  unresolved ref: {w}", file=sys.stderr)
    for w in m["fallback_anchored"]:
        print(f"  fallback anchor: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
