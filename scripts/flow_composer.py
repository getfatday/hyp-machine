#!/usr/bin/env python3
"""flow_composer.py -- extension index, flow validation, and review block for FLOW mode.

Contract: the journey-composer contract -- the
extension relation (EX-1..EX-7), the flow artifact (FA-2/FA-4/FA-7), and the layout
columns (LO-2/LO-3), as amended (AM-1..AM-5, layout rulings applied
without A/B): within a capsule, horizontal position encodes DEPENDENCY ONLY -- role
columns carry the pattern's real sequence and same-role parallels STACK VERTICALLY
at scope (capsule x lane x role), alphabetical within a stack (AM-1/AM-2); lanes and
capsules grow dynamically (AM-3); ghosts are gone from every payload (AM-4); and
flow_geometry() + assert_flow_layout() carry the AM-5 hard collision invariant
(NOTHING inside or behind a capsule's footprint except its own members) -- the layout
generators' zero-overlap asserts extended to capsule footprints vs foreign cards, and
AMENDED per the label-legibility contract §2.2
(the ruled defect fix, variant-independent): every label -- stitch verb+chip, intra
verb, capsule header band, V-C legend -- is a first-class rect in geo["labels"], the
collision assert covers label x label/card/chip/foreign-hull/lane-label-reserve, and
connector_clashes() is promoted to a hard assert. flow_geometry(cfg["label_mode"])
renders the rung-1 variants -- clearance (V-A) / routed (V-B) / minimal (V-C, with
cfg["hover"] for the hover exemplar) -- from one shared substrate.
Pure stdlib, no wall-clock, no RNG: every function is a pure function of its inputs;
two runs over the same bytes serialize byte-identically. Imported by serve_board.py
(--views mode) beside path_tracer.py, which is untouched (contract section 9), and by
render_flow.py, which delegates all pixel geometry here.

Definitions over slice-board.json as served (wire key `flows` = placement-level typed
CONNECTIONS, each intra-slice; slices connect ONLY by sharing entities -- C7):
  outputs(S) = events S emits (emission targets) + read-models S produces (projection
               targets)                                                        [EX-1]
  inputs(S)  = events S projects (projection sources) + events landing directly on
               S's command via an E5 trigger-elided shim (import boards; live 0) +
               read-models S reads (display/feed sources)                      [EX-1]
  extends(A,B): an OUTPUT event of A is an INPUT event of B (A != B)           [EX-2]
  extends via read-model: a read-model A produces is displayed/fed into B      [EX-3]

CLI:
    python3 flow_composer.py --selftest [--board B] [--lanes L]   # EX-6 census asserts
    python3 flow_composer.py --extensions '{"node_id": "event/session-started"}'
    python3 flow_composer.py --resolve '{"seed": "...", "slices": [...], "stitches": [...]}'
Defaults: slice-board.json + lanes-layout.json beside this file.
"""
from __future__ import annotations

import json
from pathlib import Path

ENTITY_PLACEMENT_KEYS = ("command/id", "event/id", "interface/id", "read-model/id")
FLOW_TYPES = ("trigger", "emission", "projection", "feed", "display")
# RE-4 verbs, reused verbatim (path_tracer.VERBS; copied so this module stays
# standalone-importable without loading the tracer)
VERBS = {"trigger": "triggers", "emission": "emits", "projection": "projects to",
         "feed": "feeds", "display": "displays to (actor)"}
PATTERN_CHIP = {"command": "CMD", "view": "VIEW",
                "automation": "AUTO", "translation": "TRANS"}
# D13-adjacent accents: pattern accents exactly as lanes_layout strips carry them
# (verified against lanes-layout.json 2026-08-17); status accents are the CP-3 mockup
# reading -- debt/hotspot warm, current green -- rendered at low alpha (flag F10:
# IM-2(b) owns the final tint call).
PATTERN_ACCENT = {"command": "#5B9BD5", "view": "#9ACD6E",
                  "automation": "#C9A0F0", "translation": "#F4C6DC"}
STATUS_ACCENT = {"current": "#2E7D32", "debt": "#C2185B", "hotspot": "#E65100"}
# em-slice-lint A7 / path_tracer D12 mark keys on the event entity
D12_MARK_KEYS = ("event/status", "status", "event/mark", "mark", "event/marks", "marks")
# EX-4 fixed group order, downstream then upstream (EX-5); labels are UI-visible and
# use the decided vocabulary only
GROUPS_DOWNSTREAM = (("new-view-impacted", "NEW VIEW IMPACTED", ("view",)),
                     ("new-automation", "NEW AUTOMATION", ("automation", "translation")),
                     ("new-command", "NEW COMMAND", ("command",)))
GROUPS_UPSTREAM = (("emitted-by", "EMITTED BY", None),
                   ("produced-by", "PRODUCED BY", None))
# AM-1 grammar roles in E1-E4 order: role columns read left->right; x only ever
# advances along an actual connection, so the rank is a stack-sort key, never an
# x source. The todo read-models (AUTO/TRANS) are both projection targets and feed
# sources -- output wins, matching their grammar position.
ROLE_RANK = {"input-view": 0, "input-event": 1, "interface": 2,
             "command": 3, "output-event": 4, "output-view": 5}

# ---- §2.2 label clearance (label-legibility-contract.md -- the RULED defect
# fix, variant-independent): labels are first-class layout rects, never post-hoc
# paint. Constants adopted into GEO from the ELK vocabulary.
EDGE_LABEL_GAP = 2      # label box <-> its own edge stroke
EDGE_NODE_GAP = 10      # edge stroke / label box <-> any foreign node or hull
LABEL_SIDE = "above"    # preferred caption side (the Miro caption parameterization)
LABEL_STATIONS = (0.7, 0.5, 0.3)          # §1 V-A candidate t-stations, near-arrival first
LABEL_MODES = ("clearance", "routed", "minimal")
# V-C legend block content, drawn verbatim (contract §1 anonymity lint: no variant
# name, concept slug, or treatment word appears in any drawn string)
LEGEND_VERBS = tuple(VERBS[t] for t in FLOW_TYPES)
LEGEND_HINT = "hover an edge for its verb + slice"

# Exact per-char advance widths of the pinned badge font (bold sans TTF at 11 px),
# measured 2026-08-17 on the render host: advances are integer and kerning-free at
# this size, so sums are EXACT for every drawn string. render_flow.py re-measures at
# draw time and refuses (FlowError) to draw text wider than its reserved rect, so a
# font swap fails loud instead of shipping an overlap the geometry lint cannot see.
_BADGE_W = {
    "a": 6, "b": 7, "c": 6, "d": 7, "e": 6, "f": 4, "g": 7, "h": 7, "i": 3, "j": 3,
    "k": 6, "l": 3, "m": 10, "n": 7, "o": 7, "p": 7, "q": 7, "r": 4, "s": 6, "t": 4,
    "u": 7, "v": 6, "w": 9, "x": 6, "y": 6, "z": 6,
    "A": 8, "B": 8, "C": 8, "D": 8, "E": 7, "F": 7, "G": 9, "H": 8, "I": 3, "J": 6,
    "K": 8, "L": 7, "M": 9, "N": 8, "O": 9, "P": 7, "Q": 9, "R": 8, "S": 7, "T": 7,
    "U": 8, "V": 7, "W": 10, "X": 7, "Y": 7, "Z": 7,
    "0": 6, "1": 6, "2": 6, "3": 6, "4": 6, "5": 6, "6": 6, "7": 6, "8": 6, "9": 6,
    " ": 3, "/": 3, "-": 4, "—": 11, "·": 4, ":": 4, ".": 3, ",": 3,
    "(": 4, ")": 4, "[": 4, "]": 4, "+": 6, "&": 8, "%": 10, "|": 3, ">": 6,
    "’": 3,
}


def _text_w(s):
    """Badge-font pixel width of s (exact for the measured alphabet; unknown chars
    take the widest measured advance, so estimates only ever over-reserve)."""
    return int(sum(_BADGE_W.get(c, 11) for c in s))


def _card_role(kind, is_input, is_output):
    """AM-1 role of a capsule member: kind + input/output flavor."""
    if kind == "interface":
        return "interface"
    if kind == "command":
        return "command"
    if kind == "event":
        return "output-event" if is_output else "input-event"
    return "output-view" if is_output else "input-view"


class FlowError(ValueError):
    """A flow spec that cannot be validated against the served board (FA-4 -> 422)."""


# ------------------------------------------------------------------- index (EX-1/EX-7)

def _entity_kind(entity_id):
    """Card kind from the id prefix: event/command/interface/view (D13 vocabulary;
    read-models render as view cards, matching lanes-layout card kinds)."""
    prefix = entity_id.split("/", 1)[0]
    return {"event": "event", "command": "command",
            "interface": "interface", "read-model": "view"}.get(prefix)


def _event_is_marked(entity):
    """D12 mark (path_tracer's reading of em-slice-lint A7): any accepted key whose
    value (string or list) contains 'debt' or 'hotspot'."""
    for key in D12_MARK_KEYS:
        raw = entity.get(key)
        vals = raw if isinstance(raw, list) else [raw]
        for v in vals:
            if isinstance(v, str) and ("debt" in v.lower() or "hotspot" in v.lower()):
                return True
    return False


def build_index(board, lanes_layout=None):
    """Everything the extension relation, flow validation, resolution, and the review
    block need, derived once from the board (+ optional lanes layout for lanes and
    strip accents). Pure; deterministic member/consumer orderings throughout."""
    entities = {}
    for e in board.get("event-model/events", []):
        entities[e["event/id"]] = {"kind": "event",
                                   "label": e.get("event/name") or e["event/id"],
                                   "stream": e.get("event/stream"),
                                   "marked": _event_is_marked(e)}
    for c in board.get("event-model/commands", []):
        entities[c["command/id"]] = {"kind": "command",
                                     "label": c.get("command/name") or c["command/id"],
                                     "stream": None, "marked": False}
    for i in board.get("event-model/interfaces", []):
        entities[i["interface/id"]] = {"kind": "interface",
                                       "label": i.get("interface/name") or i["interface/id"],
                                       "stream": None, "marked": False}
    for r in board.get("event-model/read-models", []):
        entities[r["read-model/id"]] = {"kind": "view",
                                        "label": r.get("read-model/name") or r["read-model/id"],
                                        "stream": None, "marked": False}

    p2e = {p["placement/id"]: p[k] for p in board.get("placements", [])
           for k in ENTITY_PLACEMENT_KEYS if k in p}
    p2s = {m: s["slice/id"] for s in board.get("slices", [])
           for m in s.get("slice/members", [])}

    slices = {}
    ordered = sorted(board.get("slices", []),
                     key=lambda s: (s.get("slice/index-range") or [0])[0])
    accent_by_slice = {}
    gwt_label_by_slice = {}
    for st in (lanes_layout or {}).get("strips") or []:
        if isinstance(st, dict) and st.get("slice_id"):
            if st.get("accent"):
                accent_by_slice[st["slice_id"]] = st["accent"]
            if st.get("gwt_label"):
                gwt_label_by_slice[st["slice_id"]] = st["gwt_label"]
    for idx, s in enumerate(ordered):
        sid = s["slice/id"]
        members = []
        for m in s.get("slice/members", []):
            ent = p2e.get(m)
            if ent and ent not in members:
                members.append(ent)  # placement order kept; one card per entity (C7
                #                      repeats live across slices, not within one)
        gwt = len(s.get("slice/gwt") or [])
        pattern = s.get("slice/pattern") or "command"
        status = s.get("slice/status") or "current"
        slices[sid] = {
            "pattern": pattern, "chip": PATTERN_CHIP.get(pattern, "CMD"),
            "status": status, "order": idx,
            "gwt_count": gwt,
            "gwt_label": gwt_label_by_slice.get(
                sid, ("GWT: %d" % gwt) if gwt else "GWT: 0 — unspecified"),
            "members": members, "cards_total": len(members),
            "pattern_accent": accent_by_slice.get(sid, PATTERN_ACCENT.get(pattern)),
            "status_accent": STATUS_ACCENT.get(status, STATUS_ACCENT["current"]),
        }

    outputs = {sid: {"events": set(), "read_models": set()} for sid in slices}
    inputs = {sid: {"proj_events": set(), "shim_events": set(),
                    "display_rms": set(), "feed_rms": set()} for sid in slices}
    intra = {sid: [] for sid in slices}
    shim_pairs = []
    for fid in sorted(board.get("flows") or {}):
        f = board["flows"][fid]
        fe, te = p2e.get(f.get("flow/from")), p2e.get(f.get("flow/to"))
        ty = f.get("flow/type")
        sl = p2s.get(f.get("flow/from")) or p2s.get(f.get("flow/to"))
        if not fe or not te or ty not in FLOW_TYPES or sl not in slices:
            continue
        intra[sl].append((fe, te, ty))
        if ty == "emission":
            outputs[sl]["events"].add(te)
        elif ty == "projection":
            outputs[sl]["read_models"].add(te)
            inputs[sl]["proj_events"].add(fe)
        elif ty == "display":
            inputs[sl]["display_rms"].add(fe)
        elif ty == "feed":
            inputs[sl]["feed_rms"].add(fe)
        # E5 trigger-elided shim: an event landing directly on a command (import
        # boards only; live census 0) -- detected by SHAPE, whatever the declared type
        if _entity_kind(fe) == "event" and _entity_kind(te) == "command":
            inputs[sl]["shim_events"].add(fe)
            shim_pairs.append((fe, sl))
    for sid in intra:
        intra[sid].sort()

    slice_ids = sorted(slices)
    consumers_event = {}   # event -> [(slice, "projection"|"shim")], slice-sorted
    consumers_rm = {}      # read-model -> [(slice, "display"|"feed")]
    emitters = {}          # event -> [slices]
    producers = {}         # read-model -> [slices]
    for sid in slice_ids:
        for e in inputs[sid]["proj_events"]:
            consumers_event.setdefault(e, []).append((sid, "projection"))
        for e in inputs[sid]["shim_events"]:
            consumers_event.setdefault(e, []).append((sid, "shim"))
        for r in inputs[sid]["display_rms"]:
            consumers_rm.setdefault(r, []).append((sid, "display"))
        for r in inputs[sid]["feed_rms"]:
            consumers_rm.setdefault(r, []).append((sid, "feed"))
        for e in outputs[sid]["events"]:
            emitters.setdefault(e, []).append(sid)
        for r in outputs[sid]["read_models"]:
            producers.setdefault(r, []).append(sid)
    for d in (consumers_event, consumers_rm):
        for k in d:
            d[k].sort()
    for d in (emitters, producers):
        for k in d:
            d[k].sort()

    placed = set(p2e.values())
    zero_consumer_events = sorted(
        eid for eid, meta in entities.items()
        if meta["kind"] == "event" and not consumers_event.get(eid))
    zero_consumer_rms = sorted(
        eid for eid, meta in entities.items()
        if meta["kind"] == "view" and eid in placed and not consumers_rm.get(eid))

    # lanes: layout assignments when given (TR-8: a node never changes lanes),
    # else event streams only (enough for headless census work without the layout)
    lane_of = {eid: meta["stream"] for eid, meta in entities.items()
               if meta["kind"] == "event"}
    lanes = list(board.get("event-model/streams") or [])
    foreign = set(board.get("event-model/foreign-streams") or [])
    lane_labels = {}
    if lanes_layout:
        assignments = lanes_layout.get("lane_assignments")
        if isinstance(assignments, dict) and assignments:
            lane_of = {k: (v.get("lane") if isinstance(v, dict) else v)
                       for k, v in assignments.items()}
        layout_lanes = [ln.get("lane") for ln in (lanes_layout.get("stream_lanes") or [])
                        if isinstance(ln, dict) and ln.get("lane")]
        for ln in lanes_layout.get("stream_lanes") or []:
            if isinstance(ln, dict) and ln.get("lane"):
                lane_labels[ln["lane"]] = ln.get("label") or ln["lane"].upper()
        if layout_lanes:
            lanes = layout_lanes
            foreign = {ln["lane"] for ln in lanes_layout.get("stream_lanes") or []
                       if isinstance(ln, dict) and ln.get("foreign")} or foreign
    lanes = ([l for l in lanes if l not in foreign]
             + [l for l in lanes if l in foreign])  # L3: foreign last
    lane_order = {l: i for i, l in enumerate(lanes)}

    return {"entities": entities, "slices": slices, "slice_ids": slice_ids,
            "outputs": outputs, "inputs": inputs, "intra": intra,
            "consumers_event": consumers_event, "consumers_rm": consumers_rm,
            "emitters": emitters, "producers": producers,
            "zero_consumer_events": zero_consumer_events,
            "zero_consumer_read_models": zero_consumer_rms,
            "shim_pairs": shim_pairs,
            "lane_of": lane_of, "lanes": lanes, "lane_order": lane_order,
            "lane_labels": lane_labels,
            "foreign_lanes": sorted(foreign)}


def slice_outputs(index, sid):
    """outputs(S) as one sorted entity list (events + read-models)."""
    o = index["outputs"][sid]
    return sorted(o["events"] | o["read_models"])


def _event_input_of(index, sid, event_id):
    """The consuming leg type if event_id is an event input of slice sid, else None."""
    i = index["inputs"][sid]
    if event_id in i["proj_events"]:
        return "projection"
    if event_id in i["shim_events"]:
        return "shim"
    return None


def _rm_input_of(index, sid, rm_id):
    """The consuming leg type if rm_id is a read-model input of slice sid, else None.
    display wins the tiebreak (the trigger-interface leg is the directive's 'new
    command that gets kicked off')."""
    i = index["inputs"][sid]
    if rm_id in i["display_rms"]:
        return "display"
    if rm_id in i["feed_rms"]:
        return "feed"
    return None


def is_input_of(index, sid, entity_id):
    """The consuming leg type for any entity input, else None (EX-1 inputs)."""
    if index["entities"][entity_id]["kind"] == "event":
        return _event_input_of(index, sid, entity_id)
    return _rm_input_of(index, sid, entity_id)


def extension_pairs(index):
    """Every event-stitched ordered pair extends(A, B) per EX-2 (A != B). Live census:
    29. Sorted for determinism."""
    pairs = set()
    for a in index["slice_ids"]:
        for e in index["outputs"][a]["events"]:
            for (b, _leg) in index["consumers_event"].get(e, []):
                if b != a:
                    pairs.add((a, b))
    return sorted(pairs)


# ------------------------------------------------------------ the picker payload (EX-4/EX-5)

def _row(index, sid, via, leg, flow_slices):
    """One EX-4 row: {slice, pattern, status, gwt_cases, cards, via, verb, included}
    (+ additive chip/shim; SV-1 all-future-fields-optional law)."""
    meta = index["slices"][sid]
    shim = leg == "shim"
    return {"slice": sid, "pattern": meta["pattern"], "chip": meta["chip"],
            "status": meta["status"], "gwt_cases": meta["gwt_count"],
            "cards": meta["cards_total"], "via": via,
            "verb": VERBS["trigger"] if shim else VERBS[leg],
            "shim": shim, "included": sid in flow_slices}


def _downstream_rows(index, entity_id, exclude_slice, flow_slices):
    """Rows for the slices an entity extends into: event -> EX-2 legs (projection +
    shim), read-model -> EX-3 legs (display/feed). exclude_slice drops the self pair
    (A != B when the entity is asked for as a capsule's own output card)."""
    kind = index["entities"][entity_id]["kind"]
    rows = []
    if kind == "event":
        for (sid, leg) in index["consumers_event"].get(entity_id, []):
            if sid != exclude_slice:
                rows.append(_row(index, sid, entity_id, leg, flow_slices))
    elif kind == "view":
        for (sid, leg) in index["consumers_rm"].get(entity_id, []):
            if sid != exclude_slice:
                rows.append(_row(index, sid, entity_id, leg, flow_slices))
    return rows


def _grouped(rows, groups, by_pattern=True, verb_split=None):
    """Fixed-order groups with heading rows; rows alphabetical by (slice, via, verb)
    within each group (TR-7 tiebreak discipline)."""
    out = []
    for key, label, patterns in groups:
        if by_pattern:
            g = [r for r in rows if r["pattern"] in patterns]
        else:
            g = [r for r in rows if verb_split.get(r["verb"]) == key]
        g.sort(key=lambda r: (r["slice"], r["via"], r["verb"]))
        out.append({"key": key, "label": label, "rows": g})
    return out


def extensions(index, node_id=None, slice_id=None, direction="downstream",
               flow_slices=()):
    """POST /api/extensions payload (EX-4/EX-5): fixed-order groups of rows, byte-
    deterministic. flow_slices = the open flow's membership, marking rows included.
    Raises FlowError on unknown ids / bad arguments (-> 422)."""
    if (node_id is None) == (slice_id is None):
        raise FlowError("exactly one of node_id or slice_id is required")
    if direction not in ("downstream", "upstream"):
        raise FlowError('direction must be "downstream" or "upstream"')
    flow_slices = frozenset(flow_slices)

    if slice_id is not None:
        if slice_id not in index["slices"]:
            raise FlowError("unknown slice: %s" % slice_id)
        if direction == "downstream":
            rows = []
            for ent in slice_outputs(index, slice_id):
                rows.extend(_downstream_rows(index, ent, slice_id, flow_slices))
            return {"slice_id": slice_id, "direction": direction,
                    "groups": _grouped(rows, GROUPS_DOWNSTREAM)}
        # upstream of a slice: who emits its event inputs / produces its rm inputs
        emitted, produced = [], []
        i = index["inputs"][slice_id]
        for e in sorted(i["proj_events"] | i["shim_events"]):
            for a in index["emitters"].get(e, []):
                if a != slice_id:
                    emitted.append(_row(index, a, e, "emission", flow_slices))
        for r in sorted(i["display_rms"] | i["feed_rms"]):
            for a in index["producers"].get(r, []):
                if a != slice_id:
                    produced.append(_row(index, a, r, "projection", flow_slices))
        groups = []
        for (key, label, _p), g in zip(GROUPS_UPSTREAM, (emitted, produced)):
            g.sort(key=lambda r: (r["slice"], r["via"], r["verb"]))
            groups.append({"key": key, "label": label, "rows": g})
        return {"slice_id": slice_id, "direction": direction, "groups": groups}

    if node_id not in index["entities"]:
        raise FlowError("unknown entity: %s" % node_id)
    kind = index["entities"][node_id]["kind"]
    if direction == "downstream":
        rows = _downstream_rows(index, node_id, None, flow_slices)
        return {"node_id": node_id, "direction": direction,
                "groups": _grouped(rows, GROUPS_DOWNSTREAM)}
    # upstream of a node (EX-5): EMITTED BY / PRODUCED BY, foreign-origin note when
    # an event has no emitter and lives in a foreign stream
    emitted, produced = [], []
    if kind == "event":
        for a in index["emitters"].get(node_id, []):
            emitted.append(_row(index, a, node_id, "emission", flow_slices))
    elif kind == "view":
        for a in index["producers"].get(node_id, []):
            produced.append(_row(index, a, node_id, "projection", flow_slices))
    groups = []
    for (key, label, _p), g in zip(GROUPS_UPSTREAM, (emitted, produced)):
        g.sort(key=lambda r: (r["slice"], r["via"], r["verb"]))
        groups.append({"key": key, "label": label, "rows": g})
    payload = {"node_id": node_id, "direction": direction, "groups": groups}
    if kind == "event" and not emitted:
        stream = index["entities"][node_id]["stream"]
        if stream in set(index["foreign_lanes"]):
            payload["foreign_origin"] = stream
    return payload


# --------------------------------------------------------- flow validation (FA-2/FA-4)

def normalize_flow_spec(raw, index):
    """FA-2 membership fields validated per FA-4, defaults filled -> {seed, slices,
    stitches, collapsed}. Raises FlowError (-> 422 on save, loud banner on open).
    Unknown keys are ignored (SV-1 optional-fields law)."""
    if not isinstance(raw, dict):
        raise FlowError("flow spec must be a JSON object")
    seed = raw.get("seed")
    if not isinstance(seed, str) or not seed.strip():
        raise FlowError('flow.seed is required (an entity id or a slice id)')
    seed = seed.strip()
    seed_is_slice = seed in index["slices"]
    if not seed_is_slice and seed not in index["entities"]:
        raise FlowError("unknown seed: %s" % seed)

    slices_raw = raw.get("slices", [])
    if not isinstance(slices_raw, list) or not all(isinstance(s, str) for s in slices_raw):
        raise FlowError("flow.slices must be a list of slice ids")
    slices = [s.strip() for s in slices_raw]
    seen = set()
    for s in slices:
        if s not in index["slices"]:
            raise FlowError("unknown slice: %s" % s)
        if s in seen:
            raise FlowError("duplicate slice: %s" % s)
        seen.add(s)
    if seed_is_slice and seed not in seen:
        raise FlowError("seed slice %s must be listed in flow.slices" % seed)

    stitches_raw = raw.get("stitches", [])
    if not isinstance(stitches_raw, list):
        raise FlowError("flow.stitches must be a list")
    stitches = []
    seen_st = set()
    for st in stitches_raw:
        if not isinstance(st, dict):
            raise FlowError("each stitch must be an object {via, from_slice, to_slice}")
        via = st.get("via")
        frm = st.get("from_slice")
        to = st.get("to_slice")
        if not isinstance(via, str) or via not in index["entities"]:
            raise FlowError("stitch via does not resolve: %r" % (via,))
        if index["entities"][via]["kind"] not in ("event", "view"):
            raise FlowError("stitch via must be an event or a read-model: %s" % via)
        if not isinstance(to, str) or to not in seen:
            raise FlowError("stitch to_slice must be an included slice: %r" % (to,))
        if frm is not None and (not isinstance(frm, str) or frm not in seen):
            raise FlowError("stitch from_slice must be null or an included slice: %r"
                            % (frm,))
        if frm == to:
            raise FlowError("stitch cannot join a slice to itself: %s" % to)
        leg = is_input_of(index, to, via)
        if leg is None:
            raise FlowError("invalid stitch: %s is not an input of %s" % (via, to))
        if frm is not None:
            if via not in set(slice_outputs(index, frm)):
                raise FlowError("invalid stitch: %s is not an output of %s" % (via, frm))
        else:
            # seed-stitch: via = the seed, or an output of no slice (FA-4)
            has_producer = bool(index["emitters"].get(via)
                                or index["producers"].get(via))
            if via != seed and has_producer:
                raise FlowError("seed-stitch via %s must be the seed or an output of "
                                "no slice" % via)
        key = (via, frm, to)
        if key in seen_st:
            raise FlowError("duplicate stitch: %s -> %s via %s" % (frm, to, via))
        seen_st.add(key)
        stitches.append({"via": via, "from_slice": frm, "to_slice": to})

    collapsed_raw = raw.get("collapsed", [])
    if collapsed_raw is None:
        collapsed_raw = []
    if not isinstance(collapsed_raw, list) or not all(isinstance(s, str)
                                                      for s in collapsed_raw):
        raise FlowError("flow.collapsed must be a list of slice ids")
    for s in collapsed_raw:
        if s not in seen:
            raise FlowError("collapsed names a slice not in the flow: %s" % s)

    return {"seed": seed, "slices": slices, "stitches": stitches,
            "collapsed": list(collapsed_raw)}


# -------------------------------------------------- resolution (CP-2..CP-6, LO-2/LO-3)

def _sub_columns(cards, conns):
    """Longest-path depth over the rendered intra DAG, compacted to consecutive
    sub-columns. AM-1: horizontal position encodes DEPENDENCY ONLY -- a card advances
    x only along an actual connection, so unordered same-role siblings share one
    sub-column (the grammar's edges make the depth classes the role columns); bounded
    iteration keeps a pathological cycle deterministic."""
    depth = {c: 0 for c in cards}
    for _ in range(max(1, len(cards))):
        changed = False
        for (a, b, _ty) in conns:
            if a in depth and b in depth and depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                changed = True
        if not changed:
            break
    ranks = {d: i for i, d in enumerate(sorted(set(depth.values())))}
    return {c: ranks[depth[c]] for c in cards}


def _plus_counts(index, entity_id, owner_slice, flow_slices):
    """(plus_new, plus_total) for a canvas copy of an output entity: extending slices
    per EX-2/EX-3, minus the owning capsule (A != B); new = not yet in the flow."""
    kind = index["entities"][entity_id]["kind"]
    if kind == "event":
        consumers = [s for (s, _leg) in index["consumers_event"].get(entity_id, [])]
    elif kind == "view":
        consumers = [s for (s, _leg) in index["consumers_rm"].get(entity_id, [])]
    else:
        return 0, 0
    consumers = [s for s in consumers if s != owner_slice]
    return sum(1 for s in consumers if s not in flow_slices), len(consumers)


def resolve_flow(index, nspec, lint_findings=None):
    """A normalized flow spec -> the re-derived render payload: capsules (full
    membership, CP-4 input coalescing, CP-5 upstream stubs), stitches with verbs and
    first-consuming-card attachment, the CP-6 frontier, and the FA-7 review block.
    Geometry is columns/sub-columns only (LO-2/LO-3); pixels are the renderer's job
    (FA-5: the file stores membership + stitches, everything else is recomputed)."""
    seed = nspec["seed"]
    flow_slices = list(nspec["slices"])
    flow_set = frozenset(flow_slices)
    seed_is_slice = seed in index["slices"]
    lane_of = index["lane_of"]

    # LO-2 columns as amended by AM-8 (bidirectional-contract.md): columns are
    # SIGNED stitch depth, the seed pinned at column 0 forever. A capsule with
    # >=1 already-placed stitch SOURCE keeps the v1 rule (1 + max source column);
    # with NO placed source but >=1 already-placed stitch TARGET it takes
    # min(target columns) - 1 (an upstream reveal -- the canvas grows LEFT); with
    # neither it keeps the v1 fallback 1. Sources/targets counted only when
    # already placed (accretion order resolves cycles -- a later-source stitch
    # renders backward rather than re-columning the board), so accreting upstream
    # provably changes no existing capsule's column and every pre-bidirectional
    # flow resolves to the exact v1 columns (zero-diff at min column 0).
    column = {}
    if seed_is_slice:
        column[seed] = 0
    stitches_to, stitches_from = {}, {}
    for st in nspec["stitches"]:
        stitches_to.setdefault(st["to_slice"], []).append(st)
        if st["from_slice"] is not None:
            stitches_from.setdefault(st["from_slice"], []).append(st)
    for sid in flow_slices:
        if sid in column:
            continue  # the seed slice
        src_cols = []
        for st in stitches_to.get(sid, []):
            frm = st["from_slice"]
            if frm is None:
                src_cols.append(0)
            elif frm in column:
                src_cols.append(column[frm])
        if src_cols:
            column[sid] = 1 + max(src_cols)
        else:
            tgt_cols = [column[st["to_slice"]]
                        for st in stitches_from.get(sid, [])
                        if st["to_slice"] in column]
            column[sid] = (min(tgt_cols) - 1) if tgt_cols else 1

    # coalesced inputs per capsule (CP-4): the via of every stitch into it, unless
    # the capsule also OUTPUTS that entity (repeat-by-design wins; live: n/a)
    coalesced = {sid: set() for sid in flow_slices}
    for st in nspec["stitches"]:
        sid = st["to_slice"]
        if st["via"] in set(index["slices"][sid]["members"]) \
                and st["via"] not in set(slice_outputs(index, sid)):
            coalesced[sid].add(st["via"])

    capsules = []
    card_index = {}   # (slice, entity) -> card dict, for stitch attachment
    for sid in flow_slices:
        meta = index["slices"][sid]
        rendered = [m for m in meta["members"] if m not in coalesced[sid]]
        conns_all = index["intra"][sid]
        subcol = _sub_columns(rendered, [(a, b, t) for (a, b, t) in conns_all
                                         if a in rendered and b in rendered])
        outs = set(slice_outputs(index, sid))
        stitched_in = {st["via"] for st in stitches_to.get(sid, [])}
        cards = []
        for ent in sorted(rendered, key=lambda e: (subcol[e], e)):
            emeta = index["entities"][ent]
            is_output = ent in outs
            plus_new, plus_total = (_plus_counts(index, ent, sid, flow_set)
                                    if is_output else (0, 0))
            leg = is_input_of(index, sid, ent)
            upstream_stub = (leg is not None and not is_output
                             and ent not in stitched_in)
            card = {"entity": ent, "kind": emeta["kind"], "label": emeta["label"],
                    "lane": lane_of.get(ent), "sub_column": subcol[ent],
                    "role": _card_role(emeta["kind"], leg is not None, is_output),
                    "is_output": is_output, "plus_new": plus_new,
                    "plus_total": plus_total, "upstream_stub": upstream_stub}
            cards.append(card)
            card_index[(sid, ent)] = card
        lanes_here = sorted({c["lane"] for c in cards if c["lane"]},
                            key=lambda l: index["lane_order"].get(l, 99))
        fallback_lane = lanes_here[0] if lanes_here else \
            (index["lanes"][0] if index["lanes"] else None)
        # AM-2 vertical stacking at (capsule x lane x role): same-sub cells stack
        # top->bottom, sorted by grammar role rank then entity id (alphabetical
        # within a stack -- LO-2's tiebreak turned vertical)
        cells = {}
        for c in cards:
            cells.setdefault((c["lane"] or fallback_lane, c["sub_column"]),
                             []).append(c)
        for cell in cells.values():
            cell.sort(key=lambda c: (ROLE_RANK.get(c["role"], 9), c["entity"]))
            for r, c in enumerate(cell):
                c["row"] = r
        connections = []
        for (a, b, ty) in conns_all:
            if b not in subcol:
                continue  # target coalesced away (cannot happen for live legs)
            connections.append({"from": a, "to": b, "type": ty, "verb": VERBS[ty],
                                "from_external": a not in subcol})
        capsules.append({
            "slice": sid, "pattern": meta["pattern"], "chip": meta["chip"],
            "status": meta["status"], "gwt_count": meta["gwt_count"],
            "gwt_label": meta["gwt_label"], "cards_total": meta["cards_total"],
            "pattern_accent": meta["pattern_accent"],
            "status_accent": meta["status_accent"],
            "column": column[sid], "lanes": lanes_here,
            "collapsed": sid in set(nspec["collapsed"]),
            "cards": cards, "connections": connections,
            "coalesced_inputs": sorted(coalesced[sid]),
        })

    # seed card (CP-2): a bare entity card in its home lane, column 0
    seed_card = None
    if not seed_is_slice:
        emeta = index["entities"][seed]
        plus_new, plus_total = _plus_counts(index, seed, None, flow_set)
        seed_card = {"entity": seed, "kind": emeta["kind"], "label": emeta["label"],
                     "lane": lane_of.get(seed), "column": 0,
                     "plus_new": plus_new, "plus_total": plus_total}

    # stitches resolved: verb from the consuming leg, attached to the first consuming
    # card of the target capsule (CP-4; RE-4 verbs; shim rows carry the auto-flag)
    resolved_stitches = []
    for st in nspec["stitches"]:
        sid, via = st["to_slice"], st["via"]
        leg = is_input_of(index, sid, via)
        shim = leg == "shim"
        targets = sorted((b for (a, b, _ty) in index["intra"][sid]
                          if a == via and (sid, b) in card_index),
                         key=lambda b: (card_index[(sid, b)]["sub_column"], b))
        resolved_stitches.append({
            "via": via, "via_kind": index["entities"][via]["kind"],
            "from_slice": st["from_slice"], "to_slice": sid,
            "to_card": targets[0] if targets else None,
            "type": leg, "verb": VERBS["trigger"] if shim else VERBS[leg],
            "shim": shim})

    # CP-6 frontier: seed card + every capsule OUTPUT card with >=1 not-yet-included
    # extension -- one stub chip per canvas copy
    frontier = []
    if seed_card and seed_card["plus_new"] >= 1:
        frontier.append({"entity": seed, "kind": seed_card["kind"],
                         "label": seed_card["label"], "capsule": None,
                         "plus_new": seed_card["plus_new"],
                         "plus_total": seed_card["plus_total"]})
    for cap in capsules:
        for card in cap["cards"]:
            if card["is_output"] and card["plus_new"] >= 1:
                frontier.append({"entity": card["entity"], "kind": card["kind"],
                                 "label": card["label"], "capsule": cap["slice"],
                                 "plus_new": card["plus_new"],
                                 "plus_total": card["plus_total"]})

    review = review_block(index, nspec, frontier, lint_findings=lint_findings)
    # AM-8: "columns" stays the slot COUNT; with signed columns that is the span
    # max - min + 1 (column 0 always exists: the seed). min_column == 0 for every
    # pre-bidirectional flow, so their payloads are byte-identical to v1.
    max_column = max([0] + [c["column"] for c in capsules])
    min_column = min([0] + [c["column"] for c in capsules])
    return {"spec": nspec, "seed_card": seed_card, "capsules": capsules,
            "stitches": resolved_stitches, "frontier": frontier,
            "review": review, "columns": max_column - min_column + 1}


# ------------------------------------------------------------------ review block (FA-7)

def review_block(index, nspec, frontier, lint_findings=None):
    """(a) GAPS: flow output entities with ZERO consuming slices board-wide (the
    'missing bits' list, EM-L10/D12-badged); (b) hotspot/debt census (OV-6 at flow
    scope); (c) GWT coverage over included slices (OV-5); (d) frontier size."""
    flow_slices = list(nspec["slices"])

    produced_by = {}
    for sid in flow_slices:
        for ent in slice_outputs(index, sid):
            produced_by.setdefault(ent, []).append(sid)
    gaps = []
    for ent in sorted(produced_by):
        kind = index["entities"][ent]["kind"]
        if kind == "event":
            if index["consumers_event"].get(ent):
                continue
            cls = "d12-marked" if index["entities"][ent]["marked"] else "em-l10"
        else:
            if index["consumers_rm"].get(ent):
                continue
            cls = "unconsumed-read-model"
        gaps.append({"entity": ent, "kind": kind,
                     "label": index["entities"][ent]["label"], "class": cls,
                     "produced_by": produced_by[ent]})

    debt = [s for s in flow_slices if index["slices"][s]["status"] == "debt"]
    hot = [s for s in flow_slices if index["slices"][s]["status"] == "hotspot"]
    on_flow = set()
    for sid in flow_slices:
        on_flow.update(index["slices"][sid]["members"])
    if nspec["seed"] in index["entities"]:
        on_flow.add(nspec["seed"])
    marked = sorted(e for e in on_flow if index["entities"][e]["marked"])
    lint_rows = []
    slice_set = set(flow_slices)
    for f in (lint_findings or []):
        fid = f.get("id") if isinstance(f, dict) else None
        rule = f.get("rule") if isinstance(f, dict) else None
        if fid and rule and (fid in slice_set or fid in on_flow):
            lint_rows.append({"rule": rule, "id": fid})
    lint_rows.sort(key=lambda r: (r["rule"], r["id"]))

    return {
        "gaps": gaps,
        "hotspot_debt": {"count": len(debt) + len(hot),
                         "debt_slices": debt, "hotspot_slices": hot,
                         "d12_marked_events": marked, "lint_findings": lint_rows},
        "gwt_coverage": {"slices_total": len(flow_slices),
                         "slices_with_gwt": sum(
                             1 for s in flow_slices
                             if index["slices"][s]["gwt_count"] > 0)},
        "frontier_size": len(frontier),
    }


# ------------------------------------------------- pixel geometry (AM-1..AM-5, LO-5)
# The single geometry source for headless renders and asserts: a pure function of
# (resolved flow, board index, config) -- render_flow.py draws these rects verbatim.
# Defaults mirror render_flow.py's drawing constants.

GEO = {
    "card_w": 190, "card_h": 76, "sub_gap": 64, "row_gap": 18,
    "caps_pad_x": 16, "caps_hdr": 42, "caps_hdr_minor": 14, "caps_pad_bot": 34,
    "chip_h": 48, "chip_pad": 10, "seed_pad": 26,
    "col_gap": 120, "track_gap": 46, "stack_gap": 30,
    "lane_pad": 48, "lane_min_h": 120, "margin_l": 150, "margin_r": 70,
    "top": 154,
    # §2.2 clearance constants (ELK vocabulary; label-legibility-contract.md)
    "edge_label_gap": EDGE_LABEL_GAP, "edge_node_gap": EDGE_NODE_GAP,
    "label_side": LABEL_SIDE, "label_h": 18, "intra_label_h": 16, "label_pad_x": 5,
    "corridor_nudge": 8,
    # rung-1 variant switch: clearance (V-A) | routed (V-B) | minimal (V-C);
    # hover = entity id for the V-C hover exemplar (minimal mode only)
    "label_mode": "clearance", "hover": None,
}


def _owners(index, res, g):
    """One layout owner per drawable unit -- the seed card, each collapsed chip,
    each expanded capsule (all its per-lane chunks move as one) -- in stable order:
    seed first, then accretion order (LO-3/LO-4)."""
    lanes = index["lanes"]
    lane_idx = {l: i for i, l in enumerate(lanes)}
    fallback = lanes[0] if lanes else ""

    def lidx(lane):
        return lane_idx.get(lane, 0)

    owners = []
    if res["seed_card"]:
        lane = res["seed_card"]["lane"] or fallback
        owners.append({"key": "@", "kind": "seed", "col": 0, "span": g["card_w"],
                       "lo": lidx(lane), "hi": lidx(lane), "single": True,
                       "primary": lane,
                       "heights": {lane: g["card_h"] + g["seed_pad"]}})
    for cap in res["capsules"]:
        cap_lane = cap["lanes"][0] if cap["lanes"] else fallback
        if cap["collapsed"] or not cap["cards"]:
            owners.append({"key": cap["slice"], "kind": "chip",
                           "col": cap["column"], "span": g["card_w"],
                           "lo": lidx(cap_lane), "hi": lidx(cap_lane),
                           "single": True, "primary": cap_lane,
                           "heights": {cap_lane: g["chip_h"] + g["chip_pad"]}})
            continue
        n_sub = max(c["sub_column"] for c in cap["cards"]) + 1
        # §2.2: intra verb labels are LAYOUT INPUTS (dagre makeSpaceForEdgeLabels
        # discipline) -- the sub-column gap widens so every verb box fits between
        # its endpoint cards with EDGE_LABEL_GAP clearance, in every variant
        verbs = sorted({conn["verb"] for conn in cap["connections"]
                        if not conn["from_external"]})
        gap_eff = g["sub_gap"]
        if verbs:
            gap_eff = max(gap_eff, max(_text_w(v) for v in verbs) + 8
                          + 2 * (g["edge_label_gap"] + 2))
        # §2.2: the header run (id chip + status dot/text + GWT chip) reserves an
        # exact-width band -- the hull and its column slot grow to contain it (the
        # maintainer-screenshot bounding-box overflow, fixed in geometry itself)
        header_w = (30 + _text_w(cap["slice"]) + 30
                    + _text_w(cap["status"].upper())
                    + _text_w(cap["gwt_label"]) + 12)
        span = max(2 * g["caps_pad_x"] + n_sub * g["card_w"]
                   + (n_sub - 1) * gap_eff, header_w)
        lanes_used = sorted({c["lane"] or cap_lane for c in cap["cards"]}, key=lidx)
        rows_in = {}
        for c in cap["cards"]:
            L = c["lane"] or cap_lane
            rows_in[L] = max(rows_in.get(L, 0), c["row"] + 1)
        primary = lanes_used[0]
        heights = {}
        for L in lanes_used:
            hdr = g["caps_hdr"] if L == primary else g["caps_hdr_minor"]
            heights[L] = (hdr + rows_in[L] * g["card_h"]
                          + (rows_in[L] - 1) * g["row_gap"] + g["caps_pad_bot"])
        owners.append({"key": cap["slice"], "kind": "capsule",
                       "col": cap["column"], "span": span, "n_sub": n_sub,
                       "gap_eff": gap_eff, "header_w": header_w,
                       "lo": lidx(lanes_used[0]), "hi": lidx(lanes_used[-1]),
                       "single": len(lanes_used) == 1, "primary": primary,
                       "heights": heights})
    return owners


def _owners_conflict(a, b):
    """AM-5 horizontal-track rule: two owners can share a column track only when
    their lane-index ranges cannot collide -- disjoint ranges, or both single-lane
    in the SAME lane (vertical stacking separates those). A multi-lane capsule's
    hull covers every band it spans, so anything intersecting its range gets its
    own track."""
    if a["lo"] > b["hi"] or b["lo"] > a["hi"]:
        return False
    if a["single"] and b["single"] and a["lo"] == b["lo"]:
        return False
    return True


# ---------------------------------------------- §2.2 shared route/label machinery
# (label-legibility-contract.md: offsets + label rects ship in EVERY variant;
# only the treatment above them -- clearance / routed / minimal -- is under test)

def _spread(n, span, step_max):
    """n fixed per-track offsets centered on 0, spread over span (§1: parallel
    stitches get per-track offsets by sorted key, replacing the bundle)."""
    if n <= 1:
        return [0]
    step = min(step_max, span // (n - 1))
    return [int(round((i - (n - 1) / 2.0) * step)) for i in range(n)]


def _dep_arr_offsets(res, g):
    """Departure offsets per source owner and arrival offsets per (target capsule,
    consuming card), assigned in sorted (via, from, to) key order -- deterministic
    per-track separation for parallel runs. Returns (dep, arr, order)."""
    n = len(res["stitches"])

    def skey(i):
        st = res["stitches"][i]
        return (st["via"], st["from_slice"] or "", st["to_slice"])

    order = sorted(range(n), key=skey)
    by_src, by_dst = {}, {}
    for i in order:
        st = res["stitches"][i]
        by_src.setdefault(st["from_slice"] or "@", []).append(i)
        by_dst.setdefault((st["to_slice"], st["to_card"] or ""), []).append(i)
    dep, arr = {}, {}
    span = g["card_h"] - 20
    for key in sorted(by_src):
        idxs = by_src[key]
        offs = _spread(len(idxs), span, 14)
        for j, i in enumerate(idxs):
            dep[i] = offs[j]
    for key in sorted(by_dst):
        idxs = by_dst[key]
        offs = _spread(len(idxs), span, 14)
        for j, i in enumerate(idxs):
            arr[i] = offs[j]
    return dep, arr, order


def _stitch_endpoints(ctx, st, i, dep, arr):
    """(src point, (target rect, arrival point)) for a stitch, offsets applied and
    clamped to the endpoint node's height. Either side may be None (chip targets
    keep working; missing rects skip the route exactly as before)."""
    src = None
    if st["from_slice"] is None:
        r = ctx["cards"].get("@|" + st["via"])
    elif st["from_slice"] in ctx["chips"]:
        r = ctx["chips"][st["from_slice"]]
    else:
        r = ctx["cards"].get(st["from_slice"] + "|" + st["via"])
    if r:
        lim = max(0, r[3] // 2 - 8)
        dy = max(-lim, min(lim, dep.get(i, 0)))
        src = [r[0] + r[2], r[1] + r[3] // 2 + dy]
    tgt = None
    if st["to_slice"] in ctx["chips"]:
        r2 = ctx["chips"][st["to_slice"]]
    elif st["to_card"]:
        r2 = ctx["cards"].get(st["to_slice"] + "|" + st["to_card"])
    else:
        r2 = None
    if r2:
        lim = max(0, r2[3] // 2 - 8)
        dy = max(-lim, min(lim, arr.get(i, 0)))
        tgt = (r2, [r2[0], r2[1] + r2[3] // 2 + dy])
    return src, tgt


def _target_siblings(ctx, caps, st):
    """Rects of the target capsule's OTHER cards (the connector-through-card
    obstacle set the ruled defect names)."""
    cap = caps.get(st["to_slice"])
    if not cap or cap["collapsed"] or not st["to_card"]:
        return []
    return [ctx["cards"][st["to_slice"] + "|" + c["entity"]]
            for c in cap["cards"]
            if c["entity"] != st["to_card"]
            and st["to_slice"] + "|" + c["entity"] in ctx["cards"]]


def _routes_direct(ctx, dep, arr):
    """V-A/V-C stitch routes: the CP-4 regime (straight final approach, sub-column
    corridor entry) extended per §2.2 -- the corridor now ALSO triggers when the
    direct leg would pass through a sibling card of the target capsule (the ruled
    ADVISORY SUITE feeds-clash), entering above or below the hull through a short
    escape leg, so the promoted connector_clashes assert holds by construction."""
    g = ctx["g"]
    res = ctx["res"]
    caps = {c["slice"]: c for c in res["capsules"]}
    routes = []
    for i, st in enumerate(res["stitches"]):
        src, tgt = _stitch_endpoints(ctx, st, i, dep, arr)
        points = None
        if src and tgt:
            r, arrival = tgt
            ty = arrival[1]
            if st["to_slice"] in ctx["chips"]:
                points = [src, [r[0], ty]]
            else:
                meta = ctx["card_meta"].get((st["to_slice"], st["to_card"]))
                hull = ctx["hulls"].get(st["to_slice"])
                o = ctx["by_key"].get(st["to_slice"]) or {}
                corr_x = r[0] - o.get("gap_eff", g["sub_gap"]) // 2
                sibs = _target_siblings(ctx, caps, st)
                blocked = any(_seg_hits_rect(src, (r[0], ty), rr) for rr in sibs)
                if (meta and meta["sub_column"] > 0 and hull and src[0] < corr_x
                        and (src[1] < hull[1] or blocked)):
                    if src[1] <= hull[1] + hull[3] // 2:
                        entry = hull[1] - g["edge_node_gap"]
                    else:
                        entry = hull[1] + hull[3] + g["edge_node_gap"]
                    ex = min(src[0] + 120, corr_x)
                    if ex < corr_x:
                        points = [src, [ex, entry], [corr_x, entry],
                                  [corr_x, ty], [r[0], ty]]
                    else:
                        points = [src, [corr_x, entry], [corr_x, ty], [r[0], ty]]
                else:
                    points = [src, [r[0], ty]]
        routes.append({"via": st["via"], "from_slice": st["from_slice"],
                       "to_slice": st["to_slice"], "points": points})
    return routes


def _routes_orthogonal(ctx, dep, arr, order):
    """V-B: orthogonal corridor routing (the survey's libavoid/LOOM patterns as a
    pure-python port, contract §1): stitches run in the inter-column/inter-track
    gutter CHANNELS and a reserved highway strip at the top of the first lane
    (routed mode grows that lane's pad to hold it); obstacle set = cards + chips +
    hulls buffered by EDGE_NODE_GAP; deterministic track assignment by sorted
    (via, from, to) key with fixed nudge offsets. Targets whose consuming card sits
    behind sibling sub-columns are entered from the sky onto the card's TOP edge,
    to the right of the reserved header band -- never across a sibling card, never
    through the header run."""
    g = ctx["g"]
    res = ctx["res"]
    caps = {c["slice"]: c for c in res["capsules"]}
    spans = []
    for col in sorted(ctx["track_w"]):
        x = ctx["col_x"][col]
        for w in ctx["track_w"][col]:
            spans.append((x, x + w))
            x += w + g["track_gap"]
    spans.sort()
    channels, prev = [], 0
    for (x0, x1) in spans:
        if x0 > prev:
            channels.append((prev, x0))
        prev = max(prev, x1)
    channels.append((prev, ctx["total_w"]))

    def ch_after(o):
        x1 = o["x"] + ctx["track_w"][o["col"]][o["track"]]
        for ci, (a, _b) in enumerate(channels):
            if a >= x1:
                return ci
        return len(channels) - 1

    def ch_before(o):
        best = 0
        for ci, (_a, b) in enumerate(channels):
            if b <= o["x"]:
                best = ci
        return best

    use = {}

    def vx_for(ci, i):
        lst = use.setdefault(ci, [])
        if i not in lst:
            lst.append(i)
        a, b = channels[ci]
        x = a + g["edge_node_gap"] + lst.index(i) * g["corridor_nudge"]
        if x > b - g["edge_node_gap"]:
            raise FlowError("routed-mode corridor overflow in channel %d..%d"
                            % (a, b))
        return x

    obs = []
    for key, r in sorted(ctx["cards"].items()):
        obs.append((key.split("|", 1)[0], r))
    for sid in sorted(ctx["chips"]):
        obs.append((sid, ctx["chips"][sid]))
    for sid in sorted(ctx["hulls"]):
        obs.append((sid, ctx["hulls"][sid]))
    buf = g["edge_node_gap"]

    def reach_blocked(p0, p1, skip):
        for owner, r in obs:
            if owner in skip:
                continue
            rr = (r[0] - buf, r[1] - buf, r[2] + 2 * buf, r[3] + 2 * buf)
            if _seg_hits_rect(p0, p1, rr, inset=0):
                return True
        return False

    hw_rank = {idx: rank for rank, idx in enumerate(order)}
    first_top = ctx["lane_top"][ctx["lanes"][0]] if ctx["lanes"] else g["top"]
    hy0 = first_top + 44

    routes = {}
    for i in order:
        st = res["stitches"][i]
        src, tgt = _stitch_endpoints(ctx, st, i, dep, arr)
        points = None
        if src and tgt:
            r, arrival = tgt
            ty = arrival[1]
            to_o = ctx["by_key"].get(st["to_slice"]) or {}
            skip = {st["from_slice"] or "@", st["to_slice"]}
            ci_t = ch_before(to_o)
            a_t, b_t = channels[ci_t]
            sibs = _target_siblings(ctx, caps, st)
            final_blocked = any(_seg_hits_rect((a_t, ty), (r[0], ty), rr)
                                for rr in sibs)
            hull = ctx["hulls"].get(st["to_slice"])
            src_o = ctx["by_key"].get(st["from_slice"] or "@") or {}
            hy = hy0 + hw_rank[i] * g["corridor_nudge"]
            if final_blocked and hull:
                vx_s = vx_for(ch_after(src_o), i)
                drop = min(max(r[0] + 12,
                               hull[0] + to_o.get("header_w", 0)
                               + g["edge_node_gap"]),
                           r[0] + r[2] - 12)
                points = [src, [vx_s, src[1]], [vx_s, hy], [drop, hy],
                          [drop, r[1]]]
            elif not reach_blocked(src, (b_t - buf, src[1]), skip):
                vx = vx_for(ci_t, i)
                points = [src, [vx, src[1]], [vx, ty], [r[0], ty]]
            else:
                vx_s = vx_for(ch_after(src_o), i)
                vx_t = vx_for(ci_t, i)
                points = [src, [vx_s, src[1]], [vx_s, hy], [vx_t, hy],
                          [vx_t, ty], [r[0], ty]]
        routes[i] = {"via": st["via"], "from_slice": st["from_slice"],
                     "to_slice": st["to_slice"], "points": points}
    return [routes[i] for i in range(len(res["stitches"]))]


def _label_seg(pts, mode):
    """The leg a stitch label describes: routed mode uses the longest horizontal
    segment (the reserved station track); other modes label the final approach."""
    if mode == "routed":
        best, blen = None, -1
        for k in range(len(pts) - 1):
            p0, p1 = pts[k], pts[k + 1]
            if p0[1] == p1[1] and abs(p1[0] - p0[0]) > blen:
                best, blen = ((p0[0], p0[1]), (p1[0], p1[1])), abs(p1[0] - p0[0])
        if best:
            return best
    p0, p1 = pts[-2], pts[-1]
    return ((p0[0], p0[1]), (p1[0], p1[1]))


def _lane_label_reserves(index, lane_geo):
    """One reserve rect per lane label (§2.2: labels -- and V-B corridors -- must
    clear them). Width is 2x the badge estimate: conservatively covers the 20 px
    lane font, and over-reserving only pushes labels further from the margin."""
    out = []
    base = index.get("lane_labels") or {}
    for lg in lane_geo:
        text = base.get(lg["lane"]) or lg["lane"].upper()
        if lg["foreign"]:
            text += "  — we only observe"
        out.append({"lane": lg["lane"],
                    "rect": [10, lg["top"] + 8, 2 * _text_w(text) + 8, 26]})
    return out


def _legend_geometry(g, total_h):
    """V-C legend block (contract §1): the five verb strings with their line
    styles, the four pattern chips, and the hover hint -- a fixed-corner block
    whose rect joins the collision set; the canvas grows a bottom margin to hold
    it, so it can never collide by construction (and the lint re-checks anyway)."""
    chips_w = sum(_text_w(c) + 10 for c in PATTERN_CHIP.values()) + 3 * 6
    body_w = max(max(_text_w(v) for v in LEGEND_VERBS) + 36,
                 _text_w(LEGEND_HINT), chips_w)
    rect = [16, total_h - 8, body_w + 20,
            10 + len(LEGEND_VERBS) * 18 + 26 + 24 + 8]
    return rect, rect[1] + rect[3] + 16


def _place_labels(movable, fixed, ctx, legend_rect, lane_rects, y_bounds):
    """§1 V-A greedy deconfliction, shared by every variant that places labels:
    candidates = t-stations x side above/below on the label's own leg; when none is
    clear the label takes the nearest displaced slot (deterministic outward scan)
    and records leader=True for a thin leader line. Obstacles: cards, chip nodes,
    lane-label reserves, the legend, already-placed labels, and every hull that
    does not own the label's edge. Raises FlowError if a label cannot be placed --
    a collision is never shipped silently."""
    g = ctx["g"]
    obstacles = [ctx["cards"][k] for k in sorted(ctx["cards"])]
    obstacles += [ctx["chips"][k] for k in sorted(ctx["chips"])]
    obstacles += [lr["rect"] for lr in lane_rects]
    if legend_rect:
        obstacles.append(legend_rect)
    placed_rects = [f["rect"] for f in fixed]
    hulls = ctx["hulls"]
    y_min, y_max = y_bounds
    total_w = ctx["total_w"]

    def clear(rect, own):
        if rect[0] < 2 or rect[1] < y_min or rect[0] + rect[2] > total_w - 2 \
                or rect[1] + rect[3] > y_max:
            return False
        for r in obstacles:
            if _rects_hit(rect, r):
                return False
        for r in placed_rects:
            if _rects_hit(rect, r):
                return False
        for sid in sorted(hulls):
            if sid not in own and _rects_hit(rect, hulls[sid]):
                return False
        return True

    sides = ("above", "below") if g["label_side"] == "above" else ("below", "above")
    out = []
    for lab in sorted(movable, key=lambda l: (l["prio"], l["key"])):
        (x0, y0), (x1, y1) = lab["seg"]
        w, h = lab["w"], lab["h"]
        own = set(lab["own_hulls"])
        chosen, leader, base = None, False, None
        for t in LABEL_STATIONS:
            px = x0 + (x1 - x0) * t
            py = y0 + (y1 - y0) * t
            for side in sides:
                if side == "above":
                    ry = int(py) - g["edge_label_gap"] - 2 - h
                else:
                    ry = int(py) + g["edge_label_gap"] + 2
                rect = [int(px - w / 2.0), ry, w, h]
                if base is None:
                    base = rect
                if clear(rect, own):
                    chosen = rect
                    break
            if chosen:
                break
        if chosen is None:
            for dx in (0, -60, 60, -120, 120, -200, 200, -300, 300):
                for k in range(1, 161):
                    for sgn in (-1, 1):
                        rect = [base[0] + dx, base[1] + sgn * 10 * k, w, h]
                        if clear(rect, own):
                            chosen, leader = rect, True
                            break
                    if chosen:
                        break
                if chosen:
                    break
        if chosen is None:
            raise FlowError("label placement overflow: %r" % (lab["key"],))
        t0 = LABEL_STATIONS[0]
        anchor = [int(x0 + (x1 - x0) * t0), int(y0 + (y1 - y0) * t0)]
        placed_rects.append(chosen)
        out.append({"class": lab["class"], "owner": lab["owner"],
                    "own_hulls": lab["own_hulls"], "rect": chosen,
                    "text": lab["text"], "chip": lab.get("chip"),
                    "chip_accent": lab.get("chip_accent"),
                    "anchor": anchor, "leader": leader})
    return out


def _flow_labels(ctx, routes, legend_rect, lane_rects, y_bounds, hover_idx):
    """geo["labels"] for the mode: capsule header bands always (fixed rects inside
    their own reserved hull band); stitch verb+chip and intra verb labels in
    clearance/routed; minimal draws none at rest and only the hover exemplar's
    stitch labels when hover is set; the legend rect joins as its own item."""
    g = ctx["g"]
    res = ctx["res"]
    index = ctx["index"]
    mode = ctx["mode"]
    fixed = []
    for cap in res["capsules"]:
        if cap["collapsed"] or cap["slice"] not in ctx["hulls"]:
            continue
        hull = ctx["hulls"][cap["slice"]]
        o = ctx["by_key"][cap["slice"]]
        fixed.append({"class": "caps-header", "owner": cap["slice"],
                      "own_hulls": [cap["slice"]],
                      "rect": [hull[0] + 10, hull[1] + 8, o["header_w"] - 22, 20],
                      "text": None, "chip": None, "chip_accent": None,
                      "anchor": None, "leader": False})
    movable = []
    show = []
    if mode != "minimal":
        show = list(range(len(res["stitches"])))
    elif hover_idx:
        show = list(hover_idx)
    for i in show:
        st = res["stitches"][i]
        pts = routes[i].get("points")
        if not pts:
            continue
        verb = st["verb"] + (" [shim]" if st["shim"] else "")
        chip = index["slices"][st["to_slice"]]["chip"]
        movable.append({
            "class": "stitch",
            "owner": "%s->%s via %s" % (st["from_slice"] or "@",
                                        st["to_slice"], st["via"]),
            "own_hulls": [s for s in (st["from_slice"], st["to_slice"]) if s],
            "text": verb, "chip": chip,
            "chip_accent": index["slices"][st["to_slice"]]["pattern_accent"],
            "w": 2 * g["label_pad_x"] + _text_w(verb) + 6 + _text_w(chip) + 8,
            "h": g["label_h"],
            "seg": _label_seg(pts, mode), "prio": 1,
            "key": (st["via"], st["from_slice"] or "", st["to_slice"])})
    if mode != "minimal":
        for cap in res["capsules"]:
            if cap["collapsed"]:
                continue
            sid = cap["slice"]
            for conn in cap["connections"]:
                if conn["from_external"]:
                    continue
                r0 = ctx["cards"].get(sid + "|" + conn["from"])
                r1 = ctx["cards"].get(sid + "|" + conn["to"])
                if not r0 or not r1:
                    continue
                p0 = (r0[0] + r0[2], r0[1] + r0[3] // 2)
                p1 = (r1[0], r1[1] + r1[3] // 2)
                movable.append({
                    "class": "intra", "owner": sid, "own_hulls": [sid],
                    "text": conn["verb"], "chip": None, "chip_accent": None,
                    "w": 8 + _text_w(conn["verb"]), "h": g["intra_label_h"],
                    "seg": (p0, p1), "prio": 2,
                    "key": (sid, conn["from"], conn["to"], conn["verb"])})
    labels = fixed + _place_labels(movable, fixed, ctx, legend_rect, lane_rects,
                                   y_bounds)
    if legend_rect:
        labels.append({"class": "legend", "owner": None, "own_hulls": [],
                       "rect": list(legend_rect), "text": None, "chip": None,
                       "chip_accent": None, "anchor": None, "leader": False})
    return labels


def flow_geometry(index, res, cfg=None):
    """Pixel rects for a resolved flow (AM-1..AM-3): x = stitch-depth column slots
    split into collision-free horizontal tracks; y = lane bands grown to content,
    with same-role stacks per (capsule x lane x role). Pure function of
    (resolved flow, index, cfg) -- no wall-clock, no RNG (LO-5). Returns JSON-able
    dict: lanes, columns, cards ("owner|entity" -> [x, y, w, h]; owner "@" = the
    bare seed), chips, hulls, stitch_routes, size -- plus, per the rung-1 label
    contract §2.2 (ships whatever the verdict): "labels" (first-class rects for
    stitch verb+chip, intra verb, capsule header band, V-C legend), "lane_labels"
    (lane-label reserve rects), "legend", "label_mode", "hover".

    cfg["label_mode"] selects the rung-1 treatment over one shared substrate:
    clearance (default; V-A reserve-space + backplates + greedy station
    deconfliction with leader lines), routed (V-B orthogonal corridor routing with
    station-reserved labels), minimal (V-C rest state: clean strokes + legend, no
    edge labels; cfg["hover"]=<entity id> renders the hover exemplar -- the labels
    of the stitches consuming that card)."""
    g = dict(GEO)
    if cfg:
        g.update(cfg)
    mode = g.get("label_mode") or "clearance"
    if mode not in LABEL_MODES:
        raise FlowError("unknown label_mode: %r" % (mode,))
    hover = g.get("hover")
    if hover and mode != "minimal":
        raise FlowError("hover exemplar renders are minimal-mode only")
    lanes = index["lanes"]
    owners = _owners(index, res, g)

    # AM-8 (bidirectional-contract.md): signed columns rebase to 0..n-1 slot
    # indexes for layout -- world x = margin + (col - min_col) * slots, the
    # IDENTITY at min_col = 0, so every pre-bidirectional flow lays out
    # byte-identically; the published "columns"/"tracks" carry the SIGNED value.
    min_col = min([o["col"] for o in owners]) if owners else 0
    for o in owners:
        o["col"] -= min_col

    # tracks per column (first-fit in stable order), then slot widths and x
    by_col = {}
    for o in owners:
        by_col.setdefault(o["col"], []).append(o)
    n_cols = res["columns"]
    track_w = {}
    for col in range(n_cols):
        tracks = []
        for o in by_col.get(col, []):
            t = 0
            while t < len(tracks) and any(_owners_conflict(o, p)
                                          for p in tracks[t]):
                t += 1
            if t == len(tracks):
                tracks.append([])
            tracks[t].append(o)
            o["track"] = t
        track_w[col] = [max(o["span"] for o in t) for t in tracks] or [g["card_w"]]
    col_x, slot_w = {}, {}
    x = g["margin_l"]
    for col in range(n_cols):
        ws = track_w[col]
        slot_w[col] = sum(ws) + g["track_gap"] * (len(ws) - 1)
        col_x[col] = x
        x += slot_w[col] + g["col_gap"]
    total_w = x - g["col_gap"] + g["margin_r"]
    for o in owners:
        off = sum(track_w[o["col"]][:o["track"]]) + g["track_gap"] * o["track"]
        o["x"] = col_x[o["col"]] + off

    # AM-3: lane bands grow to fit their tallest (column x track) stack; routed
    # mode additionally reserves the corridor highway strip in the FIRST lane's
    # top pad (§1 V-B: stitches run in reserved margin + gutter corridors)
    pads = {L: g["lane_pad"] for L in lanes}
    if mode == "routed" and lanes:
        pads[lanes[0]] = max(g["lane_pad"],
                             44 + len(res["stitches"]) * g["corridor_nudge"]
                             + g["edge_node_gap"])
    cells = {}
    for o in owners:
        for L, h in o["heights"].items():
            cells.setdefault((L, o["col"], o["track"]), []).append((o, h))
    lane_geo, lane_top = [], {}
    y = g["top"]
    for L in lanes:
        need = g["lane_min_h"]
        for (cl, _col, _t), occ in cells.items():
            if cl != L:
                continue
            h = (sum(hh for (_o, hh) in occ) + g["stack_gap"] * (len(occ) - 1)
                 + 2 * pads[L])
            need = max(need, h)
        lane_top[L] = y
        lane_geo.append({"lane": L, "top": y, "height": need,
                         "foreign": L in set(index["foreign_lanes"])})
        y += need
    total_h = y + 28
    for key in sorted(cells):
        yy = lane_top[key[0]] + pads[key[0]]
        for (o, hh) in cells[key]:
            o.setdefault("y", {})[key[0]] = yy
            yy += hh + g["stack_gap"]

    # rects
    cards_rect, chips_rect, hulls_rect = {}, {}, {}
    by_key = {o["key"]: o for o in owners}
    card_meta = {}
    if res["seed_card"]:
        o = by_key["@"]
        L = o["primary"]
        cards_rect["@|" + res["seed_card"]["entity"]] = \
            [o["x"], o["y"][L] + 14, g["card_w"], g["card_h"]]
    for cap in res["capsules"]:
        o = by_key.get(cap["slice"])
        if o is None:
            continue
        if o["kind"] == "chip":
            L = o["primary"]
            chips_rect[cap["slice"]] = [o["x"], o["y"][L], g["card_w"], g["chip_h"]]
            continue
        pts = []
        for c in cap["cards"]:
            L = c["lane"] or o["primary"]
            hdr = g["caps_hdr"] if L == o["primary"] else g["caps_hdr_minor"]
            cx = o["x"] + g["caps_pad_x"] \
                + c["sub_column"] * (g["card_w"] + o["gap_eff"])
            cy = o["y"][L] + hdr + c["row"] * (g["card_h"] + g["row_gap"])
            cards_rect[cap["slice"] + "|" + c["entity"]] = \
                [cx, cy, g["card_w"], g["card_h"]]
            card_meta[(cap["slice"], c["entity"])] = c
            pts.append((cx, cy))
        x0 = min(p[0] for p in pts) - g["caps_pad_x"]
        y0 = min(p[1] for p in pts) - g["caps_hdr"]
        # §2.2: the hull box contains its reserved header band -- never narrower
        # than the drawn header run (the screenshot bounding-box overflow, fixed)
        x1 = max(max(p[0] for p in pts) + g["card_w"] + g["caps_pad_x"],
                 x0 + o["header_w"])
        y1 = max(p[1] for p in pts) + g["card_h"] + g["caps_pad_bot"] - 6
        hulls_rect[cap["slice"]] = [x0, y0, x1 - x0, y1 - y0]

    # stitch routes (CP-4 attachment kept, §2.2 offsets + clash-free routing):
    # clearance/minimal keep the straight-approach regime with the sub-column
    # corridor (now also triggered by the ruled through-card clash); routed swaps
    # in the V-B orthogonal corridor discipline. Both share endpoint offsets.
    dep_off, arr_off, order = _dep_arr_offsets(res, g)
    ctx = {"g": g, "index": index, "res": res, "mode": mode, "hover": hover,
           "cards": cards_rect, "chips": chips_rect, "hulls": hulls_rect,
           "by_key": by_key, "card_meta": card_meta,
           "col_x": col_x, "track_w": track_w, "total_w": total_w,
           "lane_top": lane_top, "lanes": lanes, "pads": pads}
    if mode == "routed":
        stitch_routes = _routes_orthogonal(ctx, dep_off, arr_off, order)
    else:
        stitch_routes = _routes_direct(ctx, dep_off, arr_off)

    # §2.2 label rects (ships whatever the verdict): reserves, legend, placements
    lane_label_rects = _lane_label_reserves(index, lane_geo)
    legend = None
    if mode == "minimal":
        legend, total_h = _legend_geometry(g, total_h)
    hover_idx = []
    if hover:
        hover_idx = [i for i, st in enumerate(res["stitches"])
                     if st["to_card"] == hover]
    labels = _flow_labels(ctx, stitch_routes, legend, lane_label_rects,
                          (g["top"] - 6, total_h - 2), hover_idx)

    return {"size": [total_w, total_h], "lanes": lane_geo,
            "columns": [{"col": c + min_col, "x": col_x[c], "width": slot_w[c]}
                        for c in range(n_cols)],
            "tracks": {str(c + min_col): len(track_w[c]) for c in range(n_cols)},
            "cards": cards_rect, "chips": chips_rect, "hulls": hulls_rect,
            "stitch_routes": stitch_routes,
            "labels": labels, "lane_labels": lane_label_rects, "legend": legend,
            "label_mode": mode,
            "hover": ({"entity": hover, "stitch_indexes": hover_idx}
                      if hover else None)}


def _rects_hit(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def assert_flow_layout(geo, res=None):
    """AM-5 hard collision assert (every relayout runs it): no card/card, chip, or
    hull/hull overlap anywhere, and nothing inside or behind a capsule's footprint
    except that capsule's own members. EXTENDED per label-legibility-contract
    §2.2 (the ruled defect fix, variant-independent): geo["labels"] rects join the
    item set -- label x label, label x card, label x chip-node, label x FOREIGN
    hull, and label x lane-label-reserve intersections are failures (a label may
    sit inside a hull that owns its edge -- own-hull containment is by design and
    stays legal). When res is given, connector_clashes() is PROMOTED from a printed
    count to a hard assert. Raises FlowError naming the first clashes."""
    items = []
    for key in sorted(geo["cards"]):
        items.append((key.split("|", 1)[0], key, geo["cards"][key]))
    for sid in sorted(geo["chips"]):
        items.append((sid, "chip:" + sid, geo["chips"][sid]))
    clashes = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _rects_hit(items[i][2], items[j][2]):
                clashes.append((items[i][1], items[j][1]))
    hulls = sorted(geo["hulls"])
    for hi, sid in enumerate(hulls):
        hr = geo["hulls"][sid]
        for owner, key, r in items:
            if owner != sid and _rects_hit(hr, r):
                clashes.append(("hull:" + sid, key))
        for sid2 in hulls[hi + 1:]:
            if _rects_hit(hr, geo["hulls"][sid2]):
                clashes.append(("hull:" + sid, "hull:" + sid2))
    labels = geo.get("labels") or []

    def lid(la):
        return "label[%s]:%s" % (la["class"], la.get("owner") or "-")

    for i in range(len(labels)):
        la, lr = labels[i], labels[i]["rect"]
        for j in range(i + 1, len(labels)):
            if _rects_hit(lr, labels[j]["rect"]):
                clashes.append((lid(la), lid(labels[j])))
        for owner, key, r in items:
            if _rects_hit(lr, r):
                clashes.append((lid(la), key))
        own = set(la.get("own_hulls") or ())
        for sid in hulls:
            if sid not in own and _rects_hit(lr, geo["hulls"][sid]):
                clashes.append((lid(la), "hull:" + sid))
        for reserve in geo.get("lane_labels") or []:
            if _rects_hit(lr, reserve["rect"]):
                clashes.append((lid(la), "lane-label:" + reserve["lane"]))
    if clashes:
        raise FlowError("layout collision (AM-5/§2.2): %s"
                        % "; ".join("%s x %s" % c for c in clashes[:8]))
    if res is not None:
        bad = connector_clashes(geo, res)
        if bad:
            raise FlowError(
                "connector clash (§2.2 promoted assert): %s"
                % "; ".join("%s %s x %s" % c for c in bad[:8]))
    return True


def _seg_hits_rect(p0, p1, rect, inset=1):
    """Liang-Barsky: does the straight leg p0->p1 pass through rect (inset keeps
    edge-touching anchors from counting)?"""
    x, y, w, h = rect
    rx0, ry0, rx1, ry1 = x + inset, y + inset, x + w - inset, y + h - inset
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, p0[0] - rx0), (dx, rx1 - p0[0]),
                 (-dy, p0[1] - ry0), (dy, ry1 - p0[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / float(p)
        if p < 0:
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return False
            if t < t1:
                t1 = t
    return t0 < t1


def connector_clashes(geo, res):
    """AM-2's no-under-box rule, checkable: every intra connection (straight leg,
    right edge -> left edge) and every stitch-route leg vs the owning/target
    capsule's OTHER cards. Returns [] when clean; the selftest asserts it empty on
    the fixture scenes."""
    bad = []
    for cap in res["capsules"]:
        if cap["collapsed"]:
            continue
        sid = cap["slice"]
        for conn in cap["connections"]:
            r0 = geo["cards"].get(sid + "|" + conn["from"])
            r1 = geo["cards"].get(sid + "|" + conn["to"])
            if not r0 or not r1:
                continue
            p0 = (r0[0] + r0[2], r0[1] + r0[3] / 2.0)
            p1 = (r1[0], r1[1] + r1[3] / 2.0)
            for c in cap["cards"]:
                if c["entity"] in (conn["from"], conn["to"]):
                    continue
                rr = geo["cards"].get(sid + "|" + c["entity"])
                if rr and _seg_hits_rect(p0, p1, rr):
                    bad.append((sid, "%s->%s" % (conn["from"], conn["to"]),
                                c["entity"]))
    caps_by_id = {c["slice"]: c for c in res["capsules"]}
    for i, route in enumerate(geo["stitch_routes"]):
        st = res["stitches"][i]
        cap = caps_by_id.get(st["to_slice"])
        pts = route.get("points")
        if not cap or cap["collapsed"] or not pts:
            continue
        for k in range(len(pts) - 1):
            for c in cap["cards"]:
                if c["entity"] == st["to_card"]:
                    continue
                rr = geo["cards"].get(cap["slice"] + "|" + c["entity"])
                if rr and _seg_hits_rect(pts[k], pts[k + 1], rr):
                    bad.append((cap["slice"], "stitch:" + st["via"], c["entity"]))
    return bad


# ---------------------------------------------------------------------------- selftest

FIXTURE_SEED = "event/session-started"
FIXTURE_SLICES = ["slice/pt-advisory-sweep-on-session",
                  "slice/pt-durable-work-committed",
                  "slice/pv-injected-context"]
FIXTURE_STITCHES = [
    {"via": "event/session-started", "from_slice": None,
     "to_slice": "slice/pt-advisory-sweep-on-session"},
    {"via": "event/session-started", "from_slice": None,
     "to_slice": "slice/pt-durable-work-committed"},
    {"via": "event/advisory-surfaced",
     "from_slice": "slice/pt-advisory-sweep-on-session",
     "to_slice": "slice/pv-injected-context"},
    {"via": "event/advisory-surfaced",
     "from_slice": "slice/pt-durable-work-committed",
     "to_slice": "slice/pv-injected-context"}]


def self_test(board, lanes_layout):
    """Hard-assert the EX-6 live census and the section-11 fixture numbers."""
    index = build_index(board, lanes_layout)

    pairs = extension_pairs(index)
    assert len(pairs) == 29, "event-stitched pairs %d != 29" % len(pairs)
    print("SELF-TEST PASS: 29 ordered event-stitched pairs (EX-2 census)")

    ext = extensions(index, node_id="event/session-started")
    by_key = {g["key"]: [r["slice"] for r in g["rows"]] for g in ext["groups"]}
    assert by_key["new-view-impacted"] == ["slice/pv-advisory-suite"], by_key
    assert by_key["new-automation"] == ["slice/pa-open-commitments-resurface",
                                        "slice/pt-advisory-sweep-on-session",
                                        "slice/pt-durable-work-committed"], by_key
    assert by_key["new-command"] == [], by_key
    assert sum(len(v) for v in by_key.values()) == 4, by_key
    print("SELF-TEST PASS: session-started -> 4 extending slices (1 VIEW / 3 AUTO)")

    # EX-4 WIRE SHAPE, pinned: groups is an ORDERED LIST of {key, label, rows}, not a
    # {key: rows} map. The browser picker parses this shape; a silent switch to a map
    # (or a dropped label) is what emptied the picker while badge/frontier/details all
    # counted 4 -- research/raw/2026-08-17-flow-picker-empty-bug.md. Client-side twin:
    # em_board_domshim.js ("normalizeExtPayload keeps every wire row").
    assert isinstance(ext["groups"], list), type(ext["groups"])
    assert [g["key"] for g in ext["groups"]] == [k for k, _l, _p in GROUPS_DOWNSTREAM]
    assert [g["label"] for g in ext["groups"]] == [l for _k, l, _p in GROUPS_DOWNSTREAM]
    assert all(isinstance(g["rows"], list) for g in ext["groups"]), ext["groups"]
    for g in ext["groups"]:
        assert set(g) == {"key", "label", "rows"}, sorted(g)
        for r in g["rows"]:
            assert {"slice", "pattern", "status", "gwt_cases", "cards", "via", "verb",
                    "included"} <= set(r), sorted(r)
    up_shape = extensions(index, node_id="event/advisory-surfaced", direction="upstream")
    assert [g["key"] for g in up_shape["groups"]] == [k for k, _l, _p in GROUPS_UPSTREAM]
    print("SELF-TEST PASS: EX-4 wire shape = ordered list of {key,label,rows} "
          "(downstream + upstream), every row carrying the 8 picker fields")

    ext = extensions(index, node_id="event/advisory-surfaced")
    rows = [r for g in ext["groups"] for r in g["rows"]]
    assert [r["slice"] for r in rows] == ["slice/pv-injected-context"], rows
    assert rows[0]["verb"] == "projects to" and rows[0]["via"] == "event/advisory-surfaced"
    print("SELF-TEST PASS: advisory-surfaced -> 1 (pv-injected-context, projects to)")

    ext = extensions(index, node_id="read-model/injected-context")
    by_key = {g["key"]: [r["slice"] for r in g["rows"]] for g in ext["groups"]}
    assert by_key["new-command"] == ["slice/capture-note",
                                     "slice/register-request",
                                     "slice/run-check"], by_key
    assert by_key["new-view-impacted"] == [] and by_key["new-automation"] == []
    print("SELF-TEST PASS: injected-context -> NEW COMMAND x3 (EX-3 read-model leg)")

    ext = extensions(index, slice_id="slice/pt-advisory-sweep-on-session")
    rows = [r for g in ext["groups"] for r in g["rows"]]
    assert len(rows) == 1 and rows[0]["slice"] == "slice/pv-injected-context" \
        and rows[0]["via"] == "event/advisory-surfaced", rows
    print("SELF-TEST PASS: slice pt-advisory-sweep-on-session -> 1 row via advisory-surfaced")

    assert index["zero_consumer_events"] == ["event/hypothesis-registered",
                                             "event/report-emitted"], \
        index["zero_consumer_events"]
    assert index["zero_consumer_read_models"] == [], index["zero_consumer_read_models"]
    assert index["shim_pairs"] == [], "E5 shim legs %r != 0" % index["shim_pairs"]
    print("SELF-TEST PASS: zero-consumer events = the EM-L10 pair; zero read-model "
          "gaps; 0 live E5 shims")

    up = extensions(index, node_id="event/advisory-surfaced", direction="upstream")
    emitted = next(g for g in up["groups"] if g["key"] == "emitted-by")["rows"]
    assert len(emitted) == 5, "advisory-surfaced emitters %d != 5" % len(emitted)
    up = extensions(index, node_id="event/session-started", direction="upstream")
    assert up.get("foreign_origin") == "harness", up
    print("SELF-TEST PASS: upstream (5 emitters of advisory-surfaced; session-started "
          "foreign_origin=harness)")

    # the section-11 acceptance fixture (the mockup scenario)
    nspec = normalize_flow_spec({"seed": FIXTURE_SEED, "slices": FIXTURE_SLICES,
                                 "stitches": FIXTURE_STITCHES}, index)
    res = resolve_flow(index, nspec)
    assert res["seed_card"]["lane"] == "harness" and res["seed_card"]["column"] == 0
    assert (res["seed_card"]["plus_new"], res["seed_card"]["plus_total"]) == (2, 4), \
        res["seed_card"]
    cols = {c["slice"]: c["column"] for c in res["capsules"]}
    assert cols == {"slice/pt-advisory-sweep-on-session": 1,
                    "slice/pt-durable-work-committed": 1,
                    "slice/pv-injected-context": 2}, cols
    pt = res["capsules"][0]
    pt_cards = [c["entity"] for c in pt["cards"]]
    assert pt_cards == ["read-model/advisory-sweep-on-session-todo",
                        "interface/advisory-sweep-on-session",
                        "command/run-advisory-suite",
                        "event/advisory-surfaced"], pt_cards
    assert pt["coalesced_inputs"] == ["event/session-started"], pt
    assert [c["sub_column"] for c in pt["cards"]] == [0, 1, 2, 3]
    adv = pt["cards"][-1]
    assert (adv["plus_new"], adv["plus_total"]) == (0, 1), adv
    pv = res["capsules"][2]
    assert [c["entity"] for c in pv["cards"]] == ["read-model/injected-context"], pv
    ic = pv["cards"][0]
    assert (ic["plus_new"], ic["plus_total"]) == (3, 3), ic
    conv = [s for s in res["stitches"] if s["to_slice"] == "slice/pv-injected-context"]
    assert len(conv) == 2 and all(s["to_card"] == "read-model/injected-context"
                                  and s["verb"] == "projects to" for s in conv), conv
    rev = res["review"]
    assert rev["gaps"] == [], rev["gaps"]
    assert rev["hotspot_debt"]["count"] == 2 and \
        rev["hotspot_debt"]["debt_slices"] == FIXTURE_SLICES[:2], rev["hotspot_debt"]
    assert (rev["gwt_coverage"]["slices_total"],
            rev["gwt_coverage"]["slices_with_gwt"]) == (3, 0), rev["gwt_coverage"]
    assert rev["frontier_size"] == 2 and len(res["frontier"]) == 2, res["frontier"]
    assert [f["entity"] for f in res["frontier"]] == [
        "event/session-started", "read-model/injected-context"], res["frontier"]
    print("SELF-TEST PASS: mockup fixture (columns 0/1/1/2, coalesced seed, "
          "converging stitches, +2/+3 frontier, review 0 gaps / 2 debt / GWT 0-3)")

    # gap demo (acceptance 7): a flow accreting pc-observe-session -> exactly one
    # GAP row, event/report-emitted, EM-L10 badge
    gspec = normalize_flow_spec({"seed": "slice/pc-observe-session",
                                 "slices": ["slice/pc-observe-session"],
                                 "stitches": []}, index)
    gres = resolve_flow(index, gspec)
    assert [(g["entity"], g["class"]) for g in gres["review"]["gaps"]] == \
        [("event/report-emitted", "em-l10")], gres["review"]["gaps"]
    assert gres["capsules"][0]["column"] == 0  # slice seed sits at column 0
    stubs = [c["entity"] for c in gres["capsules"][0]["cards"] if c["upstream_stub"]]
    assert stubs == ["read-model/command-node", "read-model/session-transcript"], stubs
    print("SELF-TEST PASS: gap demo (pc-observe-session -> 1 GAP row report-emitted "
          "EM-L10; un-stitched read-model inputs carry upstream stubs)")

    # ---- AM-1/AM-2/AM-3 stacking fixture (amendment acceptance 14): composing
    # slice/register-request stacks its three input views on one x and its
    # three emissions on one x, alphabetical within each stack; injected-context
    # stays in its own lane band; the band grows; no connector under a sibling card
    ssid = "slice/register-request"
    sspec = normalize_flow_spec({"seed": ssid, "slices": [ssid], "stitches": []},
                                index)
    sres = resolve_flow(index, sspec)
    scap = sres["capsules"][0]
    by_ent = {c["entity"]: c for c in scap["cards"]}
    views3 = ["read-model/hypothesis-specs", "read-model/program-directives",
              "read-model/worktree"]
    events3 = ["event/changes-staged", "event/hypothesis-registered",
               "event/report-emitted"]
    assert [by_ent[v]["sub_column"] for v in views3] == [0, 0, 0]
    assert [by_ent[v]["row"] for v in views3] == [0, 1, 2]      # alphabetical stack
    assert all(by_ent[v]["role"] == "input-view" for v in views3)
    assert [by_ent[e]["sub_column"] for e in events3] == [3, 3, 3]
    assert [by_ent[e]["row"] for e in events3] == [0, 1, 2]
    assert all(by_ent[e]["role"] == "output-event" for e in events3)
    ic = by_ent["read-model/injected-context"]
    assert (ic["lane"], ic["sub_column"], ic["row"]) == ("ledger", 0, 0), ic
    assert by_ent["interface/register-hypothesis-trigger"]["sub_column"] == 1
    assert by_ent["command/register-hypothesis"]["sub_column"] == 2
    sgeo = flow_geometry(index, sres)
    gc = lambda o, e: sgeo["cards"][o + "|" + e]  # noqa: E731
    assert len({gc(ssid, v)[0] for v in views3}) == 1            # one shared x
    vys = [gc(ssid, v)[1] for v in views3]
    assert vys == sorted(vys) and len(set(vys)) == 3, vys        # stacked ys
    assert len({gc(ssid, e)[0] for e in events3}) == 1
    eys = [gc(ssid, e)[1] for e in events3]
    assert eys == sorted(eys) and len(set(eys)) == 3, eys
    ledger = next(l for l in sgeo["lanes"] if l["lane"] == "ledger")
    icr = gc(ssid, "read-model/injected-context")
    assert ledger["top"] <= icr[1] and \
        icr[1] + icr[3] <= ledger["top"] + ledger["height"]      # own lane band
    research = next(l for l in sgeo["lanes"] if l["lane"] == "research")
    assert research["height"] > GEO["lane_min_h"], research      # AM-3: band grew
    assert_flow_layout(sgeo)
    assert connector_clashes(sgeo, sres) == [], connector_clashes(sgeo, sres)
    print("SELF-TEST PASS: stacking fixture (register-request -- 3 input "
          "views one x/stacked ys, 3 emissions one x/stacked ys, injected-context "
          "in its own grown band, no connector under a sibling card)")

    # ---- AM-4/AM-5 no-ghost/no-overlap fixture (amendment acceptance 15): the
    # injected-context accretion scene -- zero footprint intersections, no ghost
    # nodes anywhere in the payload, + counts hide at zero remaining
    def ispec(slices):
        return normalize_flow_spec({
            "seed": "read-model/injected-context", "slices": slices,
            "stitches": [{"via": "read-model/injected-context",
                          "from_slice": None, "to_slice": s} for s in slices]},
            index)

    ires = resolve_flow(index, ispec(["slice/run-check"]))
    payload = json.dumps(ires, sort_keys=True)
    assert "ghost" not in payload.lower(), "ghost nodes leaked into the payload"
    assert (ires["seed_card"]["plus_new"], ires["seed_card"]["plus_total"]) == (2, 3)
    igeo = flow_geometry(index, ires)
    assert "ghost" not in json.dumps(igeo, sort_keys=True).lower()
    assert_flow_layout(igeo)
    assert connector_clashes(igeo, ires) == [], connector_clashes(igeo, ires)
    ires3 = resolve_flow(index, ispec(["slice/run-check",
                                       "slice/capture-note",
                                       "slice/register-request"]))
    assert ires3["seed_card"]["plus_new"] == 0                   # + hides at zero
    assert all(f["entity"] != "read-model/injected-context"
               for f in ires3["frontier"]), ires3["frontier"]
    igeo3 = flow_geometry(index, ires3)
    assert_flow_layout(igeo3)                                    # zero intersections
    assert connector_clashes(igeo3, ires3) == [], connector_clashes(igeo3, ires3)
    assert igeo3["tracks"]["1"] >= 2, igeo3["tracks"]            # AM-5 track split
    print("SELF-TEST PASS: no-ghost/no-overlap fixture (injected-context accretion "
          "-- zero footprint intersections at 1 and 3 capsules, no ghost nodes in "
          "payload, seed + count 2->0 and hidden at zero)")

    # ---- AM-8 bidirectional fixture (bidirectional-contract.md acceptance 1/3):
    # the maintainer's scene -- seed slice/pv-injected-context, one upstream
    # reveal via event/advisory-surfaced -> SIGNED column -1 left of the seed
    # pinned at 0, the seed's column unchanged across the accrete, the input
    # coalesced (CP-4), the stitch landing on the focal card, geometry rebased
    # (identity at min column 0) and collision-clean
    bseed = "slice/pv-injected-context"
    bres0 = resolve_flow(index, normalize_flow_spec(
        {"seed": bseed, "slices": [bseed], "stitches": []}, index))
    assert bres0["capsules"][0]["column"] == 0 and bres0["columns"] == 1
    bspec = normalize_flow_spec({
        "seed": bseed, "slices": [bseed, "slice/pc-run-advisory-suite"],
        "stitches": [{"via": "event/advisory-surfaced",
                      "from_slice": "slice/pc-run-advisory-suite",
                      "to_slice": bseed}]}, index)
    bres = resolve_flow(index, bspec)
    bcols = {c["slice"]: c["column"] for c in bres["capsules"]}
    assert bcols == {bseed: 0, "slice/pc-run-advisory-suite": -1}, bcols
    assert bres["columns"] == 2, bres["columns"]
    bst = bres["stitches"][0]
    assert (bst["verb"], bst["to_card"]) == \
        ("projects to", "read-model/injected-context"), bst
    bcap = next(c for c in bres["capsules"] if c["slice"] == bseed)
    assert bcap["coalesced_inputs"] == ["event/advisory-surfaced"], bcap
    bgeo = flow_geometry(index, bres)
    assert [c["col"] for c in bgeo["columns"]] == [-1, 0], bgeo["columns"]
    assert_flow_layout(bgeo, bres)
    assert connector_clashes(bgeo, bres) == [], connector_clashes(bgeo, bres)
    print("SELF-TEST PASS: bidirectional fixture (upstream reveal at SIGNED column "
          "-1, seed pinned at 0, input coalesced, stitch lands on the focal card, "
          "geometry rebase collision-clean)")

    # validation negatives (FA-4 loud failures)
    for bad, why in (
            ({"seed": "event/nope", "slices": [], "stitches": []}, "unknown seed"),
            ({"seed": FIXTURE_SEED, "slices": ["slice/nope"], "stitches": []},
             "unknown slice"),
            ({"seed": FIXTURE_SEED, "slices": FIXTURE_SLICES,
              "stitches": [{"via": "event/advisory-surfaced",
                            "from_slice": "slice/pv-injected-context",
                            "to_slice": "slice/pt-advisory-sweep-on-session"}]},
             "via not an input of to_slice"),
            ({"seed": FIXTURE_SEED, "slices": FIXTURE_SLICES,
              "stitches": [{"via": "event/advisory-surfaced", "from_slice": None,
                            "to_slice": "slice/pv-injected-context"}]},
             "seed-stitch via that some slice outputs")):
        try:
            normalize_flow_spec(bad, index)
            raise AssertionError("accepted invalid flow (%s)" % why)
        except FlowError:
            pass
    print("SELF-TEST PASS: FA-4 validation rejects bad seeds/slices/stitches")

    # determinism: independent index builds serialize byte-identically -- payloads
    # AND pixel geometry (LO-5/AM-5: relayout is a pure function)
    for payload in (lambda i: extensions(i, node_id="event/session-started"),
                    lambda i: extensions(i, node_id="event/advisory-surfaced",
                                         direction="upstream"),
                    lambda i: extensions(i, node_id="read-model/injected-context"),
                    lambda i: resolve_flow(i, normalize_flow_spec(
                        {"seed": FIXTURE_SEED, "slices": FIXTURE_SLICES,
                         "stitches": FIXTURE_STITCHES}, i)),
                    lambda i: resolve_flow(i, normalize_flow_spec(
                        {"seed": "slice/pv-injected-context",
                         "slices": ["slice/pv-injected-context",
                                    "slice/pc-run-advisory-suite"],
                         "stitches": [{"via": "event/advisory-surfaced",
                                       "from_slice": "slice/pc-run-advisory-suite",
                                       "to_slice": "slice/pv-injected-context"}]},
                        i)),
                    lambda i: flow_geometry(i, resolve_flow(i, normalize_flow_spec(
                        {"seed": ssid, "slices": [ssid], "stitches": []}, i))),
                    lambda i: flow_geometry(i, resolve_flow(i, normalize_flow_spec(
                        {"seed": "read-model/injected-context",
                         "slices": ["slice/run-check"],
                         "stitches": [{"via": "read-model/injected-context",
                                       "from_slice": None,
                                       "to_slice": "slice/run-check"}]},
                        i)))):
        a = json.dumps(payload(build_index(board, lanes_layout)), sort_keys=True)
        b = json.dumps(payload(build_index(board, lanes_layout)), sort_keys=True)
        assert a == b, "double run not byte-identical"
    print("SELF-TEST PASS: double-run payloads byte-identical (extensions both "
          "directions + resolve incl. bidirectional + geometry)")
    return True


def main(argv=None):
    import argparse
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="EM flow composer (journey-composer contract sections 2-4)")
    ap.add_argument("--board", default=str(here / "slice-board.json"))
    ap.add_argument("--lanes", default=str(here / "lanes-layout.json"))
    ap.add_argument("--extensions", default=None,
                    help='extensions request JSON, e.g. {"node_id": "..."}')
    ap.add_argument("--resolve", default=None,
                    help="flow spec JSON {seed, slices, stitches} to resolve")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    board = json.loads(Path(args.board).read_text(encoding="utf-8"))
    lanes_layout = None
    lanes_path = Path(args.lanes)
    if lanes_path.exists():
        lanes_layout = json.loads(lanes_path.read_text(encoding="utf-8"))
    index = build_index(board, lanes_layout)

    ran = False
    if args.extensions:
        req = json.loads(args.extensions)
        try:
            print(json.dumps(extensions(
                index, node_id=req.get("node_id"), slice_id=req.get("slice_id"),
                direction=req.get("direction", "downstream"),
                flow_slices=req.get("flow_slices") or ()), indent=2))
        except FlowError as e:
            print(json.dumps({"error": str(e)}))
            return 2
        ran = True
    if args.resolve:
        try:
            nspec = normalize_flow_spec(json.loads(args.resolve), index)
            print(json.dumps(resolve_flow(index, nspec), indent=2))
        except FlowError as e:
            print(json.dumps({"error": str(e)}))
            return 2
        ran = True
    if args.selftest or not ran:
        self_test(board, lanes_layout)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
