#!/usr/bin/env python3
# Mechanical EM-slice lint over the slice-board serialization (verified against seeded
# defect suites in two consecutive runs before shipping; changes require re-validation).
"""em_slice_lint.py <slice-board.json> — EM-slice lint, rules EM-L1..EM-L10.

Implements the slice-board conformance rules over the board JSON serialization
(grammar/slice-board.md §4; grammar/em-metamodel.md K1-K9/§6 wire keys).

Output: sorted finding lines "<EM-Lx>\\t<node-or-edge-id>\\t<path-or-detail>"
(corpus-lint finding-line contract); warn-severity findings prefixed "WARN-".
Exit 0 = no fail-severity findings (warns allowed); exit 1 = any fail finding;
exit 2 = unusable input. Deterministic: byte-identical across runs on the same input.

Severity: every rule is FAIL except (a) EM-L1 satisfied only via a D-E5
trigger-elided shim edge -> WARN-EM-L1 (contract §4: "D-E5 shim edges satisfy it at
warn severity"), and (b) one-sided frontmatter mirrors -> WARN-EM-L9 (contract §4
preamble: "import shims and one-sided frontmatter mirrors are warn").

Legal distractors NEVER flagged (contract §4 bottom): parking-lot fragments (declared),
blank interfaces (declared migration state), marked ellipsis events (D12), multi-stream
views on non-TRANS slices (C5), multi-event emissions (C1), one job interface issuing
different commands from different placements (C8), entities recurring across slices (C7).

AMBIGUITY (edge cases the docs leave open; each resolved via the metamodel's D-rows,
never guessed silently):
 A1. GWT case wire shape: em-metamodel.md §6 gives the Given/When/Then template but no
     JSON keys. Accepted keys per case: gwt/given|given, gwt/when|when, gwt/then|then;
     rejection as gwt/throws|throws|then/throws, a Then dict {"throws": name}, or a Then
     string "Throws<Name>". A case that is not a dict counts toward the >=1-case
     requirement but cannot be content-checked (skipped, not failed).
 A2. "a named rejection the slice also declares" (§6 validity 3): a non-empty rejection
     name satisfies the check; if the slice carries a slice/rejections|rejections list,
     membership in it is additionally required. No declaration slot is defined in §6's
     slice shape, so name-presence is the minimal reading.
 A3. Multi-command slices: es-to-em-mapping.md §5 reifies P-A slices as "issued
     command(s)" (plural, C8), so EM-L7's "When is the slice's command" is read as
     "When resolves to one of the slice's member commands (by id or name)".
 A4. Shim edges in EM-L6 chains: D-E5 makes event->command shims import-legal and
     EM-L1-warned; for P-C chaining a shim counts as the (degraded) trigger leg so a
     shim-triggered command slice lints via WARN-EM-L1, not a spurious EM-L6 fail.
     P-A/P-T still require the reified View + job-interface loop (their sequence IS the
     mediator D-E5 says must be reified).
 A5. Blank interfaces play the image role: E1 lists interface(image|job)->command and E4
     display targets "image", but §1/§2 draw blank placeholders in the trigger slot and
     the distractor list protects them, so blank->command derives "trigger" and
     view->blank derives "display".
 A6. P-C trigger interface type is not constrained to image/blank: es-to-em-mapping.md
     §5 gives the 4 policy-issued commands job interfaces as their P-C triggers.
 A7. D12 mark wire key is unspecified; accepted on the event entity: event/status|
     status|event/mark|mark|event/marks|marks containing "debt" or "hotspot".
 A8. Frontmatter mirrors (event/emitted-by, event/consumed-by) are not part of the board
     serialization contract; they are checked only when present on an event entity, and
     one-sidedness (mirror entry without reified flow, or reified flow missing from a
     present mirror list) reports WARN-EM-L9 (nearest rule: edge/reference integrity).
 A9. EM-L2 unknown-stream membership is checked only when event-model/streams is
     non-empty (an absent stream census cannot condemn every event). The entity must
     carry event/stream (D11: entity-level); placement-level stream keys, when present
     (Evident imports / the split-placements defect), must agree with it and each other.
 A10. EM-L8 runs per leg and is "skipped, not passed, where payloads absent": a field is
     checked only if at least one upstream entity on that leg declares a payload.
     Legs: interface-field -> view-field, view-field -> event-field, event-field ->
     command-field (M1 origin, checked when both sides declare payloads), and
     event-with-payload -> has a triggering command (M3). TRANS read-side source events
     are exempt from the last leg (es-to-em-mapping.md §1: foreign-stream events enter
     via P-T with no local emitting command). A payload that is a bare string (schema
     ref) cannot be field-traced and is treated as absent.
 A11. A flow missing flow/type entirely is EM-L9 fail: the serialization contract
     mandates the key and D6 requires declared==derived; absence cannot match.
 A12. "every non-parking-lot placement belongs to >=1 slice" (EM-L9): a placement is
     parking-lot-exempt if its placement id or its entity id appears among the
     parking-lot cards' fragment/id values; fragments themselves are never linted.
 A13. EM-L5 reads canon's "read side ... one system" across the union of the TRANS
     slice's feed views (views feeding a member job interface; fallback: all view
     members); source events are any events projecting into those views, member or not
     (an injected event must be caught even if not spliced into slice/members).
 A14. EM-L1/L3/L4/L10 evaluate at ENTITY level (flows gathered across all of an
     entity's placements): C7 makes entities recur by placement, and per-slice
     completeness is EM-L6's job. Chain semantics in EM-L6/L4 use endpoint-KIND-derived
     edge meaning, not the declared flow/type (a declared/derived mismatch is already
     EM-L9 per D6; double-counting it would cascade).

Self-test: `em_slice_lint.py --self-test` runs inline synthetic boards written from the
docs (not the frozen fixture). Stdlib only; python3.
"""

import json
import re
import sys

EVENT, COMMAND, VIEW, IFACE = "event", "command", "read-model", "interface"

COLLECTIONS = (
    ("event-model/events", EVENT, ("event/id", "id")),
    ("event-model/commands", COMMAND, ("command/id", "id")),
    ("event-model/read-models", VIEW, ("read-model/id", "id")),
    ("event-model/interfaces", IFACE, ("interface/id", "id")),
)
NAME_KEYS = {
    EVENT: ("event/name", "name"),
    COMMAND: ("command/name", "name"),
    VIEW: ("read-model/name", "name"),
    IFACE: ("interface/name", "name"),
}
REF_KEYS = (("event/id", EVENT), ("command/id", COMMAND),
            ("read-model/id", VIEW), ("interface/id", IFACE))
MARK_KEYS = ("event/status", "status", "event/mark", "mark", "event/marks", "marks")
MARKS = ("debt", "hotspot")
PATTERNS = ("command", "view", "automation", "translation")


def first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def norm_prefixed(value, *prefixes):
    if not isinstance(value, str):
        return ""
    v = value.strip()
    for p in prefixes:
        if v.startswith(p):
            v = v[len(p):]
    return v


def norm_iface_type(entity):
    return norm_prefixed(first(entity, "interface/type", "type") or "", "interface.type/")


def norm_flow_type(v):
    return norm_prefixed(v or "", "flow.type/")


def norm_pattern(v):
    return norm_prefixed(v or "", "slice.pattern/")


def norm_stream(v):
    return norm_prefixed(v or "", "stream/")


def payload_fields(entity, kind):
    raw = first(entity, kind + "/payload", "payload", kind + "/fields", "fields")
    if isinstance(raw, list):
        return sorted({str(x) for x in raw if isinstance(x, str) and x.strip()})
    if isinstance(raw, dict):
        return sorted({str(k) for k in raw.keys()})
    return []  # absent, or a bare schema ref string (A10): not field-traceable


def has_mark(entity):
    for k in MARK_KEYS:
        v = entity.get(k)
        vals = v if isinstance(v, list) else [v]
        for x in vals:
            if isinstance(x, str) and norm_prefixed(
                    x, "slice.status/", "event.status/", "status/").lower() in MARKS:
                return True
    return False


class Linter:
    def __init__(self, data):
        self.data = data if isinstance(data, dict) else {}
        self.findings = set()
        self._load()
        self._resolve()

    # -- emission ---------------------------------------------------------
    def add(self, rule, anchor, path, msg, warn=False):
        tag = ("WARN-" if warn else "") + rule
        self.findings.add("%s\t%s\t%s: %s" % (tag, anchor, path, msg))

    def lines(self):
        return sorted(self.findings)

    # -- loading ----------------------------------------------------------
    def _load(self):
        d = self.data
        self.entities = {}   # kind -> {eid: entity}
        self.names = {}      # kind -> {eid: name}
        for key, kind, idks in COLLECTIONS:
            self.entities[kind] = {}
            self.names[kind] = {}
            for ent in d.get(key) or []:
                if not isinstance(ent, dict):
                    continue
                eid = first(ent, *idks)
                if not isinstance(eid, str):
                    continue
                self.entities[kind][eid] = ent
                nm = first(ent, *NAME_KEYS[kind])
                if isinstance(nm, str):
                    self.names[kind][eid] = nm
        self.streams = set()
        for s in d.get("event-model/streams") or []:
            if isinstance(s, str):
                self.streams.add(norm_stream(s))
            elif isinstance(s, dict):
                sid = first(s, "stream/id", "stream/name", "id", "name")
                if isinstance(sid, str):
                    self.streams.add(norm_stream(sid))
        self.streams.discard("")

        self.placements = {}   # pid -> placement dict
        self.pl_ref = {}       # pid -> (kind, eid) | None
        for i, pl in enumerate(d.get("placements") or []):
            if not isinstance(pl, dict):
                continue
            pid = first(pl, "placement/id")
            if not isinstance(pid, str):
                self.add("EM-L9", "placements[%d]" % i, "placements[%d]" % i,
                         "dangling placement: placement lacks placement/id")
                continue
            self.placements[pid] = pl
            refs = [(k, kind) for k, kind in REF_KEYS if pl.get(k) is not None]
            if len(refs) != 1:
                self.add("EM-L9", pid, self._ppath(pid),
                         "dangling placement: expected exactly one entity ref "
                         "(event/id|command/id|read-model/id|interface/id), found %d"
                         % len(refs))
                self.pl_ref[pid] = None
                continue
            key, kind = refs[0]
            eid = pl[key]
            if eid not in self.entities[kind]:
                self.add("EM-L9", pid, self._ppath(pid),
                         "dangling placement: %s=%s resolves to no entity" % (key, eid))
                self.pl_ref[pid] = None
            else:
                self.pl_ref[pid] = (kind, eid)

        flows = d.get("flows")
        if isinstance(flows, list):  # lenient adapter; contract shape is a dict
            flows = {first(f, "flow/id", "id") or "flows[%d]" % i: f
                     for i, f in enumerate(flows) if isinstance(f, dict)}
        self.flows = flows if isinstance(flows, dict) else {}
        self.slices = [s for s in (d.get("slices") or []) if isinstance(s, dict)]
        self.parking = [c for c in (d.get("parking-lot") or []) if isinstance(c, dict)]

    def _ppath(self, pid):
        return "placements[placement/id=%s]" % pid

    def _epath(self, kind, eid):
        coll = {EVENT: "events", COMMAND: "commands",
                VIEW: "read-models", IFACE: "interfaces"}[kind]
        return "event-model/%s[%s/id=%s]" % (coll, kind, eid)

    def _spath(self, sid):
        return "slices[slice/id=%s]" % sid

    # -- flow resolution + derivation (EM-L9 groundwork) -------------------
    def _derive(self, fk, tk, to_entity):
        """Expected flow/type(s) for an endpoint-kind pair; None = illegal pair."""
        if fk == IFACE and tk == COMMAND:
            return ("trigger",)
        if fk == COMMAND and tk == EVENT:
            return ("emission",)
        if fk == EVENT and tk == VIEW:
            return ("projection",)
        if fk == VIEW and tk == IFACE:
            it = norm_iface_type(to_entity)
            if it == "job":
                return ("feed",)
            if it in ("image", "blank"):   # A5
                return ("display",)
            return ("display", "feed")     # unknown type: cannot disprove either
        if fk == EVENT and tk == COMMAND:
            return ("trigger-elided",)     # D-E5 shim: legal edge TYPE (import-legal)
        return None

    def _resolve(self):
        self.rflows = []  # resolved flows usable by the semantic rules
        for fid in sorted(self.flows):
            fl = self.flows[fid]
            if not isinstance(fl, dict):
                self.add("EM-L9", fid, "flows/%s" % fid, "illegal edge: not an object")
                continue
            fpid, tpid = first(fl, "flow/from", "from"), first(fl, "flow/to", "to")
            ok = True
            for label, pid in (("flow/from", fpid), ("flow/to", tpid)):
                if not isinstance(pid, str) or pid not in self.placements:
                    self.add("EM-L9", fid, "flows/%s/%s" % (fid, label),
                             "dangling placement: %s=%r resolves to no placement"
                             % (label, pid))
                    ok = False
                elif self.pl_ref.get(pid) is None:
                    ok = False  # endpoint placement itself already EM-L9-flagged
            if not ok:
                continue
            fk, feid = self.pl_ref[fpid]
            tk, teid = self.pl_ref[tpid]
            declared = norm_flow_type(first(fl, "flow/type", "type"))
            expected = self._derive(fk, tk, self.entities[tk][teid])
            if expected is None:
                self.add("EM-L9", fid, "flows/%s" % fid,
                         "illegal edge: %s->%s pair is outside the E1-E5 table"
                         % (fk, tk))
                continue
            if not declared:
                self.add("EM-L9", fid, "flows/%s/flow/type" % fid,
                         "illegal edge: missing flow/type (derivable as %s)"
                         % "|".join(expected))
            elif declared not in expected:
                self.add("EM-L9", fid, "flows/%s/flow/type" % fid,
                         "illegal edge: declared type %r disagrees with "
                         "endpoint-derived %s (D6)" % (declared, "|".join(expected)))
            self.rflows.append({"fid": fid, "fpid": fpid, "tpid": tpid,
                                "fk": fk, "feid": feid, "tk": tk, "teid": teid,
                                "declared": declared})

    # -- shared indexes -----------------------------------------------------
    def _in_ent(self, kind, eid):
        return [rf for rf in self.rflows if rf["tk"] == kind and rf["teid"] == eid]

    def _out_ent(self, kind, eid):
        return [rf for rf in self.rflows if rf["fk"] == kind and rf["feid"] == eid]

    def ent_placements(self, kind, eid):
        return sorted(p for p, ref in self.pl_ref.items() if ref == (kind, eid))

    # -- EM-L1 triggerless command -----------------------------------------
    def rule_l1(self):
        for cid in sorted(self.entities[COMMAND]):
            ins = self._in_ent(COMMAND, cid)
            trig = [rf for rf in ins if rf["fk"] == IFACE]
            shim = [rf for rf in ins if rf["fk"] == EVENT]
            path = self._epath(COMMAND, cid)
            if trig:
                continue
            if shim:
                self.add("EM-L1", cid, path,
                         "triggerless command: trigger mandate satisfied only by "
                         "D-E5 trigger-elided shim edge(s) %s -- reify the View + "
                         "trigger mediator or record the elision as debt"
                         % ",".join(sorted(rf["fid"] for rf in shim)), warn=True)
            else:
                self.add("EM-L1", cid, path,
                         "triggerless command: no incoming E1 trigger edge from an "
                         "interface on any placement (C3/D1)")

    # -- EM-L2 streamless / ambiguous-stream event ---------------------------
    def rule_l2(self):
        for eid in sorted(self.entities[EVENT]):
            ent = self.entities[EVENT][eid]
            raw = first(ent, "event/stream", "stream")
            named = []
            for v in (raw if isinstance(raw, list) else [raw]):
                nv = norm_stream(v) if isinstance(v, str) else ""
                if nv:
                    named.append(nv)
            path = self._epath(EVENT, eid) + "/event/stream"
            if not named:
                self.add("EM-L2", eid, path,
                         "streamless event: entity names no stream (C6/D11)")
            elif len(set(named)) > 1:
                self.add("EM-L2", eid, path,
                         "ambiguous-stream event: entity names %d streams %s"
                         % (len(set(named)), sorted(set(named))))
            pl_named = set()
            for pid in self.ent_placements(EVENT, eid):
                pv = first(self.placements[pid], "event/stream", "stream")
                if isinstance(pv, str) and norm_stream(pv):
                    pl_named.add(norm_stream(pv))
            allv = set(named) | pl_named
            if len(allv) > 1:
                self.add("EM-L2", eid, path,
                         "ambiguous-stream event: placements disagree on stream %s "
                         "(C6 import rule)" % sorted(allv))
            if len(set(named)) == 1 and self.streams and named[0] not in self.streams:
                self.add("EM-L2", eid, path,
                         "streamless event: names unknown stream %r (not in "
                         "event-model/streams)" % named[0])

    # -- EM-L3 view from nowhere ---------------------------------------------
    def _view_clean(self, vid):
        return any(rf["fk"] == EVENT for rf in self._in_ent(VIEW, vid))

    def rule_l3(self):
        for vid in sorted(self.entities[VIEW]):
            if not self._view_clean(vid):
                self.add("EM-L3", vid, self._epath(VIEW, vid),
                         "view from nowhere: no incoming E3 projection from any "
                         "event on any placement (M2)")

    # -- EM-L4 open automation loop -------------------------------------------
    def rule_l4(self):
        for iid in sorted(self.entities[IFACE]):
            if norm_iface_type(self.entities[IFACE][iid]) != "job":
                continue
            feeds = [rf for rf in self._in_ent(IFACE, iid) if rf["fk"] == VIEW]
            trigs = [rf for rf in self._out_ent(IFACE, iid) if rf["tk"] == COMMAND]
            problems = []
            if not feeds:
                problems.append("no upstream feed view (no view->job E4 edge)")
            else:
                dirty = sorted({rf["feid"] for rf in feeds
                                if not self._view_clean(rf["feid"])})
                if len(dirty) == len({rf["feid"] for rf in feeds}):
                    problems.append("feed view(s) %s are not EM-L3-clean "
                                    "(todo list projected from nothing)" % dirty)
            if not trigs:
                problems.append("no outgoing E1 trigger edge to a command")
            if problems:
                self.add("EM-L4", iid, self._epath(IFACE, iid),
                         "open automation loop: " + "; ".join(problems))

    # -- EM-L5 mixed-source translation ----------------------------------------
    def _slice_id(self, sl, i):
        sid = first(sl, "slice/id", "id")
        return sid if isinstance(sid, str) else "slices[%d]" % i

    def _members(self, sl):
        raw = first(sl, "slice/members", "members")
        return [m for m in raw if isinstance(m, str)] if isinstance(raw, list) else []

    def _readside_view_pids(self, mem_pids):
        views = [p for p in mem_pids if self.pl_ref.get(p) and
                 self.pl_ref[p][0] == VIEW]
        jobs = {p for p in mem_pids if self.pl_ref.get(p) and
                self.pl_ref[p][0] == IFACE and
                norm_iface_type(self.entities[IFACE][self.pl_ref[p][1]]) == "job"}
        feeding = [v for v in views if any(
            rf["fpid"] == v and rf["tpid"] in jobs for rf in self.rflows)]
        return sorted(feeding or views)  # A13

    def _event_stream_of(self, eid, pid):
        ent = self.entities[EVENT].get(eid, {})
        raw = first(ent, "event/stream", "stream")
        vals = [norm_stream(v) for v in (raw if isinstance(raw, list) else [raw])
                if isinstance(v, str) and norm_stream(v)]
        if len(set(vals)) == 1:
            return vals[0]
        pv = first(self.placements.get(pid, {}), "event/stream", "stream")
        if isinstance(pv, str) and norm_stream(pv):
            return norm_stream(pv)
        return "(streamless)"

    def rule_l5(self):
        for i, sl in enumerate(self.slices):
            if norm_pattern(first(sl, "slice/pattern", "pattern")) != "translation":
                continue
            sid = self._slice_id(sl, i)
            mem = set(self._members(sl))
            readside = self._readside_view_pids(mem)
            if not readside:
                continue  # EM-L6 reports the missing view leg
            streams = set()
            for rf in self.rflows:
                if rf["tpid"] in readside and rf["fk"] == EVENT:
                    streams.add(self._event_stream_of(rf["feid"], rf["fpid"]))
            if len(streams) > 1:
                self.add("EM-L5", sid, self._spath(sid),
                         "mixed-source translation: read-side view(s) %s are "
                         "projected from %d source streams %s (C5: read side reads "
                         "exactly one system)" % (readside, len(streams),
                                                  sorted(streams)))

    # -- EM-L6 malformed slice ---------------------------------------------
    SEM = {(IFACE, COMMAND): "trigger", (EVENT, COMMAND): "shim",
           (COMMAND, EVENT): "emission", (EVENT, VIEW): "projection",
           (VIEW, IFACE): "e4"}

    def rule_l6(self):
        for i, sl in enumerate(self.slices):
            sid = self._slice_id(sl, i)
            spath = self._spath(sid)
            pat = norm_pattern(first(sl, "slice/pattern", "pattern"))
            if pat not in PATTERNS:
                self.add("EM-L6", sid, spath + "/slice/pattern",
                         "malformed slice: unknown slice/pattern %r (must be one of "
                         "%s)" % (pat, "|".join(PATTERNS)))
                continue
            members = sorted(set(self._members(sl)))
            if not members:
                self.add("EM-L6", sid, spath + "/slice/members",
                         "malformed slice: no members")
                continue
            mem, bad = [], []
            for m in members:
                if m in self.placements and self.pl_ref.get(m):
                    mem.append(m)
                else:
                    bad.append(m)
            for m in bad:
                self.add("EM-L6", sid, spath + "/slice/members",
                         "malformed slice: member %s does not resolve to a placed "
                         "entity" % m)
            memset = set(mem)
            ind = [rf for rf in self.rflows
                   if rf["fpid"] in memset and rf["tpid"] in memset
                   and (rf["fk"], rf["tk"]) in self.SEM]
            sem = lambda rf: self.SEM[(rf["fk"], rf["tk"])]
            ins = {p: [rf for rf in ind if rf["tpid"] == p] for p in mem}
            outs = {p: [rf for rf in ind if rf["fpid"] == p] for p in mem}
            kinds = {p: self.pl_ref[p][0] for p in mem}
            by_kind = {k: [p for p in mem if kinds[p] == k]
                       for k in (EVENT, COMMAND, VIEW, IFACE)}
            job_pids = {p for p in by_kind[IFACE] if norm_iface_type(
                self.entities[IFACE][self.pl_ref[p][1]]) == "job"}

            def off(p, why):
                self.add("EM-L6", sid, spath + "/slice/members",
                         "malformed slice: member %s (%s %s) %s"
                         % (p, kinds[p], self.pl_ref[p][1], why))

            # required kinds per declared pattern (M4 full-sequence presence)
            need = {"command": (COMMAND, EVENT), "view": (VIEW, EVENT),
                    "automation": (VIEW, IFACE, COMMAND, EVENT),
                    "translation": (VIEW, IFACE, COMMAND, EVENT)}[pat]
            missing_kind = False
            for k in need:
                if not by_kind[k]:
                    missing_kind = True
                    self.add("EM-L6", sid, spath,
                             "malformed slice: %s pattern requires >=1 %s member, "
                             "found none (M4)" % (pat, k))
            if pat in ("automation", "translation") and by_kind[IFACE] \
                    and not job_pids:
                missing_kind = True
                self.add("EM-L6", sid, spath,
                         "malformed slice: %s pattern requires a job-type interface "
                         "member (the Automated Trigger, D3), found none" % pat)

            # per-member on-chain role checks ("every placement ... on the chain")
            for p in mem:
                k = kinds[p]
                sem_in = {sem(rf) for rf in ins[p]}
                sem_out = {sem(rf) for rf in outs[p]}
                if k == COMMAND:
                    if pat == "view":
                        off(p, "is a command member in a view slice (P-V is "
                               "Event(s)->View only)")
                        continue
                    if not ({"trigger", "shim"} & sem_in):
                        off(p, "has no trigger within the slice (no member "
                               "interface E1, no shim)")
                    if "emission" not in sem_out:
                        off(p, "emits no event within the slice (pattern output "
                               "leg missing, C1)")
                elif k == EVENT:
                    if not ({"emission"} & sem_in or
                            {"projection", "shim"} & sem_out):
                        off(p, "is off-chain: neither emitted by, projecting to, "
                               "nor shim-triggering any member")
                    elif pat == "view" and "projection" not in sem_out:
                        off(p, "does not project into a member view (P-V source "
                               "leg)")
                elif k == VIEW:
                    if pat == "view":
                        if "projection" not in sem_in:
                            off(p, "receives no projection within the slice (M2)")
                    elif not ({"projection"} & sem_in or {"e4"} & sem_out):
                        off(p, "is off-chain: no projection in and no display/feed "
                               "out within the slice")
                elif k == IFACE:
                    if not ({"trigger"} & sem_out or {"e4"} & sem_in):
                        off(p, "is off-chain: triggers no member command and "
                               "receives no member view display/feed")

            # end-to-end sequence check for the automation/translation loop
            if pat in ("automation", "translation") and not missing_kind:
                closed = False
                for rf1 in ind:
                    if sem(rf1) != "projection":
                        continue
                    v = rf1["tpid"]
                    for rf2 in ind:
                        if sem(rf2) != "e4" or rf2["fpid"] != v \
                                or rf2["tpid"] not in job_pids:
                            continue
                        i_p = rf2["tpid"]
                        for rf3 in ind:
                            if sem(rf3) != "trigger" or rf3["fpid"] != i_p:
                                continue
                            c = rf3["tpid"]
                            if any(sem(rf4) == "emission" and rf4["fpid"] == c
                                   for rf4 in ind):
                                closed = True
                if not closed:
                    self.add("EM-L6", sid, spath,
                             "malformed slice: todo-list loop not closed end to end "
                             "within members (need event->view->job->command->event, "
                             "P-A/P-T)")

    # -- EM-L7 unspecified / mismatched slice --------------------------------
    def rule_l7(self):
        for i, sl in enumerate(self.slices):
            sid = self._slice_id(sl, i)
            spath = self._spath(sid)
            pat = norm_pattern(first(sl, "slice/pattern", "pattern"))
            cases = first(sl, "slice/gwt", "gwt")
            if not isinstance(cases, list) or not cases:
                self.add("EM-L7", sid, spath + "/slice/gwt",
                         "unspecified slice: no GWT case (M5: definitionally "
                         "invalid/incomplete)")
                continue
            memset = set(self._members(sl))
            cmd_ids = {self.pl_ref[p][1] for p in memset
                       if self.pl_ref.get(p) and self.pl_ref[p][0] == COMMAND}
            cmd_names = {self.names[COMMAND][c] for c in cmd_ids
                         if c in self.names[COMMAND]}
            out_ids = {rf["teid"] for rf in self.rflows
                       if rf["fpid"] in memset and rf["tpid"] in memset
                       and rf["fk"] == COMMAND and rf["tk"] == EVENT}
            out_names = {self.names[EVENT][e] for e in out_ids
                         if e in self.names[EVENT]}
            declared_rej = first(sl, "slice/rejections", "rejections")
            for ci, case in enumerate(cases):
                cpath = "%s/slice/gwt[%d]" % (spath, ci)
                if not isinstance(case, dict):
                    continue  # A1: counts as a case, content not checkable
                when = first(case, "gwt/when", "when")
                then = first(case, "gwt/then", "then")
                throws = first(case, "gwt/throws", "throws", "then/throws")
                if throws is None and isinstance(then, dict):
                    throws = first(then, "throws", "gwt/throws", "then/throws",
                                   "rejection")
                if throws is None and isinstance(then, str):
                    m = re.match(r"^\s*Throws\s*<(.*)>\s*$", then)
                    if m:
                        throws = m.group(1)
                if pat == "view":
                    if when not in (None, ""):
                        self.add("EM-L7", sid, cpath,
                                 "mismatched slice: view slice GWT carries a When "
                                 "(%r); P-V cases are Given/Then only" % when)
                    continue  # Then is a view-state assertion: not event-checked
                # command / automation / translation (and unknown patterns lint
                # only the >=1-case requirement above when pattern is invalid)
                if pat not in PATTERNS:
                    continue
                if when in (None, ""):
                    self.add("EM-L7", sid, cpath,
                             "mismatched slice: GWT case has no When (must be the "
                             "slice's command)")
                elif not isinstance(when, str) or \
                        (when not in cmd_ids and when not in cmd_names):
                    self.add("EM-L7", sid, cpath,
                             "mismatched slice: When %r is not a command of this "
                             "slice %s" % (when, sorted(cmd_ids)))
                if throws is not None:
                    name = str(throws).strip()
                    name = re.sub(r"^Throws\s*<(.*)>$", r"\1", name).strip()
                    if not name:
                        self.add("EM-L7", sid, cpath,
                                 "mismatched slice: rejection is unnamed (M5 "
                                 "requires a NAMED rejection)")
                    elif isinstance(declared_rej, list) and \
                            name not in [str(x) for x in declared_rej]:
                        self.add("EM-L7", sid, cpath,
                                 "mismatched slice: rejection %r is not declared "
                                 "by the slice (slice/rejections)" % name)
                    continue
                if then is None:
                    self.add("EM-L7", sid, cpath,
                             "mismatched slice: GWT case has no Then (expected "
                             "event(s) or a named rejection)")
                    continue
                then_events = then if isinstance(then, list) else [then]
                for t in then_events:
                    if isinstance(t, str) and t not in out_ids \
                            and t not in out_names:
                        self.add("EM-L7", sid, cpath,
                                 "mismatched slice: Then references undrawn event "
                                 "%r (drawn outputs: %s)" % (t, sorted(out_ids)))

    # -- EM-L8 orphan field (skipped, not passed, where payloads absent) -------
    def _trans_readside_source_events(self):
        exempt = set()
        for i, sl in enumerate(self.slices):
            if norm_pattern(first(sl, "slice/pattern", "pattern")) != "translation":
                continue
            readside = self._readside_view_pids(set(self._members(sl)))
            for rf in self.rflows:
                if rf["tpid"] in set(readside) and rf["fk"] == EVENT:
                    exempt.add(rf["feid"])
        return exempt

    def _trace_leg(self, kind, eid, fields, upstream_kind, upstream_ids, leg):
        ups = [(u, payload_fields(self.entities[upstream_kind][u], upstream_kind))
               for u in sorted(upstream_ids) if u in self.entities[upstream_kind]]
        ups = [(u, f) for u, f in ups if f]
        if not ups:
            return  # A10: payloads absent on the upstream side -> leg skipped
        pool = set()
        for _, f in ups:
            pool.update(f)
        for field in fields:
            if field not in pool:
                self.add("EM-L8", eid,
                         "%s/payload[%s]" % (self._epath(kind, eid), field),
                         "orphan field: %s (%s; upstream %s payloads %s carry no "
                         "such field, M1/M3)" % (field, leg, upstream_kind,
                                                 sorted(u for u, _ in ups)))

    def rule_l8(self):
        foreign = self._trans_readside_source_events()
        for iid in sorted(self.entities[IFACE]):
            ent = self.entities[IFACE][iid]
            if norm_iface_type(ent) != "image":
                continue
            fields = payload_fields(ent, IFACE)
            if fields:
                views = {rf["feid"] for rf in self._in_ent(IFACE, iid)
                         if rf["fk"] == VIEW}
                self._trace_leg(IFACE, iid, fields, VIEW, views,
                                "image-interface field -> view field")
        for vid in sorted(self.entities[VIEW]):
            fields = payload_fields(self.entities[VIEW][vid], VIEW)
            if fields:
                events = {rf["feid"] for rf in self._in_ent(VIEW, vid)
                          if rf["fk"] == EVENT}
                self._trace_leg(VIEW, vid, fields, EVENT, events,
                                "view field -> event field")
        for eid in sorted(self.entities[EVENT]):
            fields = payload_fields(self.entities[EVENT][eid], EVENT)
            if not fields:
                continue
            cmds = {rf["feid"] for rf in self._in_ent(EVENT, eid)
                    if rf["fk"] == COMMAND}
            if not cmds:
                if eid not in foreign:  # A10: P-T read-side sources are foreign
                    self.add("EM-L8", eid, self._epath(EVENT, eid) + "/payload",
                             "orphan field: event carries a payload but no "
                             "triggering command emits it (M3)")
                continue
            self._trace_leg(EVENT, eid, fields, COMMAND, cmds,
                            "event field -> command field")

    # -- EM-L9 remainder: every non-parking-lot placement belongs to a slice ---
    def rule_l9_membership(self):
        frag_ids = set()
        for card in self.parking:
            fid = first(card, "fragment/id", "id")
            if isinstance(fid, str):
                frag_ids.add(fid)
        in_slices = set()
        for sl in self.slices:
            in_slices.update(self._members(sl))
        for pid in sorted(self.placements):
            if pid in in_slices or pid in frag_ids:
                continue
            ref = self.pl_ref.get(pid)
            if ref and ref[1] in frag_ids:  # A12
                continue
            self.add("EM-L9", pid, self._ppath(pid),
                     "dangling placement: belongs to no slice and is not a "
                     "parking-lot fragment")

    # -- EM-L10 unmarked ellipsis ----------------------------------------------
    def rule_l10(self):
        for eid in sorted(self.entities[EVENT]):
            if self._out_ent(EVENT, eid):
                continue  # consumed downstream (E3 projection or E-anything)
            if not has_mark(self.entities[EVENT][eid]):
                self.add("EM-L10", eid, self._epath(EVENT, eid),
                         "unmarked ellipsis: event has zero consuming edges and no "
                         "debt/hotspot mark (M6/D12)")

    # -- one-sided frontmatter mirrors (warn; A8) --------------------------------
    def rule_mirrors(self):
        for eid in sorted(self.entities[EVENT]):
            ent = self.entities[EVENT][eid]
            actual_emit = {rf["feid"] for rf in self._in_ent(EVENT, eid)
                           if rf["fk"] == COMMAND}
            actual_cons = {rf["teid"] for rf in self._out_ent(EVENT, eid)}
            for key, actual, label in (
                    (("event/emitted-by", "emitted-by"), actual_emit, "emitted-by"),
                    (("event/consumed-by", "consumed-by"), actual_cons,
                     "consumed-by")):
                raw = first(ent, *key)
                if not isinstance(raw, list):
                    continue  # mirror layer absent: nothing to cross-check
                mirror = {str(x) for x in raw if isinstance(x, str)}
                path = "%s/event/%s" % (self._epath(EVENT, eid), label)
                for x in sorted(mirror - actual):
                    self.add("EM-L9", eid, path,
                             "one-sided frontmatter mirror: %s lists %s but no "
                             "reified flow exists" % (label, x), warn=True)
                for x in sorted(actual - mirror):
                    self.add("EM-L9", eid, path,
                             "one-sided frontmatter mirror: reified flow to/from %s "
                             "is missing from %s" % (x, label), warn=True)

    def run(self):
        self.rule_l1()
        self.rule_l2()
        self.rule_l3()
        self.rule_l4()
        self.rule_l5()
        self.rule_l6()
        self.rule_l7()
        self.rule_l8()
        self.rule_l9_membership()
        self.rule_l10()
        self.rule_mirrors()
        return self.lines()


def lint(data):
    """Returns (sorted finding lines, exit code)."""
    lines = Linter(data).run()
    code = 1 if any(not ln.startswith("WARN-") for ln in lines) else 0
    return lines, code


def main(argv):
    if len(argv) == 1 and argv[0] == "--self-test":
        return self_test()
    if len(argv) != 1:
        sys.stderr.write("usage: em_slice_lint.py <slice-board.json>\n")
        return 2
    try:
        with open(argv[0], "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("em_slice_lint: unusable input: %s\n" % exc)
        return 2
    if not isinstance(data, dict):
        sys.stderr.write("em_slice_lint: top level must be a JSON object\n")
        return 2
    lines, code = lint(data)
    for ln in lines:
        sys.stdout.write(ln + "\n")
    return code

# ---------------------------------------------------------------------------
# Self-test: tiny synthetic boards written from the docs (NOT the frozen fixture).
# ---------------------------------------------------------------------------

def _ev(i, s, mark=None):
    e = {"event/id": "event/" + i, "event/name": i.title(), "event/stream": s}
    if mark:
        e["event/status"] = mark
    return e


def _pl(pid, idx, key, eid):
    return {"placement/id": "placement/" + pid, "placement/index": idx, key: eid}


def _fl(fpid, tpid, t):
    return {"flow/from": "placement/" + fpid, "flow/to": "placement/" + tpid,
            "flow/type": t}


def _sl(sid, pat, members, gwt, status="current"):
    return {"slice/id": "slice/" + sid, "slice/pattern": pat,
            "slice/members": ["placement/" + m for m in members],
            "slice/index-range": [1, 1], "slice/gwt": gwt,
            "slice/status": status}


def build_clean_board():
    """Clean board covering all four patterns + every §4 legal distractor."""
    return {
        "event-model/events": [
            _ev("alpha", "research"),
            _ev("beta", "research", "debt"),      # marked ellipsis distractor
            _ev("delta", "research", "debt"),
            _ev("epsilon", "research", "debt"),
            _ev("eta", "research", "debt"),       # multi-event emission distractor
            _ev("gamma", "harness"),              # foreign P-T source
            _ev("theta", "research", "debt"),
            _ev("zeta", "research", "debt"),
        ],
        "event-model/commands": [
            {"command/id": "command/auto-beta", "command/name": "Auto Beta"},
            {"command/id": "command/auto-six", "command/name": "Auto Six"},
            {"command/id": "command/blank-cmd", "command/name": "Blank Cmd"},
            {"command/id": "command/shim-cmd", "command/name": "Shim Cmd"},
            {"command/id": "command/submit-alpha", "command/name": "Submit Alpha"},
            {"command/id": "command/translate-delta",
             "command/name": "Translate Delta"},
        ],
        "event-model/read-models": [
            {"read-model/id": "read-model/alpha-list",
             "read-model/name": "Alpha List"},
            {"read-model/id": "read-model/auto-todo",
             "read-model/name": "Auto Todo"},
            {"read-model/id": "read-model/foreign-todo",
             "read-model/name": "Foreign Todo"},
        ],
        "event-model/interfaces": [
            {"interface/id": "interface/alpha-form", "interface/name": "Alpha Form",
             "interface/type": "interface.type/image",
             "interface/audience": "audience/researcher"},
            {"interface/id": "interface/auto-runner",
             "interface/name": "Auto Runner",
             "interface/type": "interface.type/job"},   # C8: issues two commands
            {"interface/id": "interface/blank-form", "interface/name": "Blank Form",
             "interface/type": "interface.type/blank"},  # blank distractor
            {"interface/id": "interface/foreign-runner",
             "interface/name": "Foreign Runner",
             "interface/type": "interface.type/job"},
        ],
        "event-model/audiences": [
            {"audience/id": "audience/researcher", "audience/name": "Researcher"},
        ],
        "event-model/streams": ["research", "harness"],
        "placements": [
            _pl("alpha-form-1", 1, "interface/id", "interface/alpha-form"),
            _pl("submit-alpha-1", 1, "command/id", "command/submit-alpha"),
            _pl("alpha-1", 1, "event/id", "event/alpha"),
            _pl("eta-1", 1, "event/id", "event/eta"),
            _pl("alpha-2", 2, "event/id", "event/alpha"),   # C7: entity recurs
            _pl("alpha-list-1", 2, "read-model/id", "read-model/alpha-list"),
            _pl("alpha-3", 3, "event/id", "event/alpha"),
            _pl("auto-todo-1", 3, "read-model/id", "read-model/auto-todo"),
            _pl("auto-runner-1", 3, "interface/id", "interface/auto-runner"),
            _pl("auto-beta-1", 3, "command/id", "command/auto-beta"),
            _pl("beta-1", 3, "event/id", "event/beta"),
            _pl("gamma-1", 4, "event/id", "event/gamma"),
            _pl("foreign-todo-1", 4, "read-model/id", "read-model/foreign-todo"),
            _pl("foreign-runner-1", 4, "interface/id", "interface/foreign-runner"),
            _pl("translate-delta-1", 4, "command/id", "command/translate-delta"),
            _pl("delta-1", 4, "event/id", "event/delta"),
            _pl("alpha-4", 5, "event/id", "event/alpha"),
            _pl("shim-cmd-1", 5, "command/id", "command/shim-cmd"),
            _pl("epsilon-1", 5, "event/id", "event/epsilon"),
            _pl("blank-form-1", 6, "interface/id", "interface/blank-form"),
            _pl("blank-cmd-1", 6, "command/id", "command/blank-cmd"),
            _pl("zeta-1", 6, "event/id", "event/zeta"),
            _pl("alpha-5", 7, "event/id", "event/alpha"),
            _pl("auto-todo-2", 7, "read-model/id", "read-model/auto-todo"),
            _pl("auto-runner-2", 7, "interface/id", "interface/auto-runner"),
            _pl("auto-six-1", 7, "command/id", "command/auto-six"),
            _pl("theta-1", 7, "event/id", "event/theta"),
        ],
        "flows": {
            "flow/f01": _fl("alpha-form-1", "submit-alpha-1", "trigger"),
            "flow/f02": _fl("submit-alpha-1", "alpha-1", "emission"),
            "flow/f03": _fl("alpha-2", "alpha-list-1", "projection"),
            "flow/f04": _fl("alpha-3", "auto-todo-1", "projection"),
            "flow/f05": _fl("auto-todo-1", "auto-runner-1", "feed"),
            "flow/f06": _fl("auto-runner-1", "auto-beta-1", "trigger"),
            "flow/f07": _fl("auto-beta-1", "beta-1", "emission"),
            "flow/f08": _fl("gamma-1", "foreign-todo-1", "projection"),
            "flow/f09": _fl("foreign-todo-1", "foreign-runner-1", "feed"),
            "flow/f10": _fl("foreign-runner-1", "translate-delta-1", "trigger"),
            "flow/f11": _fl("translate-delta-1", "delta-1", "emission"),
            "flow/f12": _fl("alpha-4", "shim-cmd-1", "trigger-elided"),  # D-E5
            "flow/f13": _fl("shim-cmd-1", "epsilon-1", "emission"),
            "flow/f14": _fl("blank-form-1", "blank-cmd-1", "trigger"),
            "flow/f15": _fl("blank-cmd-1", "zeta-1", "emission"),
            "flow/f16": _fl("submit-alpha-1", "eta-1", "emission"),  # C1: 2 events
            "flow/f17": _fl("alpha-5", "auto-todo-2", "projection"),
            "flow/f18": _fl("auto-todo-2", "auto-runner-2", "feed"),
            "flow/f19": _fl("auto-runner-2", "auto-six-1", "trigger"),
            "flow/f20": _fl("auto-six-1", "theta-1", "emission"),
        },
        "slices": [
            _sl("cmd-alpha", "command",
                ["alpha-form-1", "submit-alpha-1", "alpha-1", "eta-1"],
                [{"given": [], "when": "command/submit-alpha",
                  "then": ["event/alpha"]}]),
            _sl("view-alpha", "view", ["alpha-2", "alpha-list-1"],
                [{"given": ["event/alpha"], "then": "list shows alpha"}]),
            _sl("auto-beta", "automation",
                ["alpha-3", "auto-todo-1", "auto-runner-1", "auto-beta-1",
                 "beta-1"],
                [{"given": ["event/alpha"], "when": "command/auto-beta",
                  "then": ["event/beta"]}]),
            _sl("trans-delta", "translation",
                ["gamma-1", "foreign-todo-1", "foreign-runner-1",
                 "translate-delta-1", "delta-1"],
                [{"given": ["event/gamma"], "when": "command/translate-delta",
                  "then": ["event/delta"]}]),
            _sl("cmd-shim", "command", ["alpha-4", "shim-cmd-1", "epsilon-1"],
                [{"given": ["event/alpha"], "when": "command/shim-cmd",
                  "then": ["event/epsilon"]}], status="debt"),
            _sl("cmd-blank", "command",
                ["blank-form-1", "blank-cmd-1", "zeta-1"],
                [{"given": [], "when": "command/blank-cmd",
                  "then": ["event/zeta"]}]),
            _sl("auto-six", "automation",
                ["alpha-5", "auto-todo-2", "auto-runner-2", "auto-six-1",
                 "theta-1"],
                [{"given": ["event/alpha"], "when": "command/auto-six",
                  "then": ["event/theta"]}]),
        ],
        "parking-lot": [
            {"fragment/id": "fragment/loose-idea", "fragment/name": "Loose Idea",
             "fragment/kind": "policy", "reconcile": True},
        ],
    }


def self_test():
    import copy
    failures = []

    def check(name, cond, extra=""):
        if cond:
            print("PASS %s" % name)
        else:
            failures.append(name)
            print("FAIL %s %s" % (name, extra))

    def has(lines, rule, anchor):
        return any(ln.split("\t")[0] == rule and ln.split("\t")[1] == anchor
                   for ln in lines)

    def mutate(fn):
        b = copy.deepcopy(build_clean_board())
        fn(b)
        return lint(b)

    # clean board: exactly one WARN (the D-E5 shim on command/shim-cmd), exit 0
    clean = build_clean_board()
    lines, code = lint(clean)
    check("clean.exit0", code == 0, repr(lines))
    check("clean.only-shim-warn",
          lines == [ln for ln in lines
                    if ln.startswith("WARN-EM-L1\tcommand/shim-cmd\t")]
          and len(lines) == 1, repr(lines))
    lines2, _ = lint(build_clean_board())
    check("determinism", "\n".join(lines) == "\n".join(lines2))

    # EM-L1: strip the trigger edge
    ln, c = mutate(lambda b: b["flows"].pop("flow/f01"))
    check("L1.triggerless", has(ln, "EM-L1", "command/submit-alpha") and c == 1)

    # EM-L2: blank stream / placement split / unknown stream
    def l2a(b):
        b["event-model/events"][0]["event/stream"] = ""
    ln, c = mutate(l2a)
    check("L2.streamless", has(ln, "EM-L2", "event/alpha") and c == 1)

    def l2b(b):
        b["placements"][4]["event/stream"] = "harness"  # alpha-2
    ln, c = mutate(l2b)
    check("L2.split-placements", has(ln, "EM-L2", "event/alpha") and c == 1)

    def l2c(b):
        b["event-model/events"][0]["event/stream"] = "nonexistent"
    ln, c = mutate(l2c)
    check("L2.unknown-stream", has(ln, "EM-L2", "event/alpha") and c == 1)

    # EM-L3: drop the view's projection
    ln, c = mutate(lambda b: b["flows"].pop("flow/f03"))
    check("L3.view-from-nowhere",
          has(ln, "EM-L3", "read-model/alpha-list") and c == 1)

    # EM-L4: sever the job's feed view
    def l4(b):
        b["flows"].pop("flow/f05")
        b["flows"].pop("flow/f18")  # sever both placements' feeds (entity-level)
    ln, c = mutate(l4)
    check("L4.open-loop", has(ln, "EM-L4", "interface/auto-runner") and c == 1)

    # EM-L5: inject a second-system event into the TRANS read side
    def l5(b):
        b["event-model/events"].append(_ev("omega", "research"))
        b["placements"].append(_pl("omega-1", 4, "event/id", "event/omega"))
        b["flows"]["flow/f21"] = _fl("omega-1", "foreign-todo-1", "projection")
        b["slices"][3]["slice/members"].append("placement/omega-1")
    ln, c = mutate(l5)
    check("L5.mixed-source", has(ln, "EM-L5", "slice/trans-delta") and c == 1)

    # EM-L5 must catch the injection even when NOT spliced into members (A13)
    def l5b(b):
        l5(b)
        b["slices"][3]["slice/members"].remove("placement/omega-1")
    ln, c = mutate(l5b)
    check("L5.uninvited-injection",
          has(ln, "EM-L5", "slice/trans-delta") and c == 1)

    # EM-L6: splice a foreign placement into a slice chain
    def l6(b):
        b["slices"][1]["slice/members"].append("placement/beta-1")
    ln, c = mutate(l6)
    check("L6.spliced-foreign", has(ln, "EM-L6", "slice/view-alpha") and c == 1)

    # EM-L7: no case / undrawn Then / When on a view slice / wrong When
    def l7a(b):
        b["slices"][0]["slice/gwt"] = []
    ln, c = mutate(l7a)
    check("L7.no-case", has(ln, "EM-L7", "slice/cmd-alpha") and c == 1)

    def l7b(b):
        b["slices"][0]["slice/gwt"][0]["then"] = ["event/undrawn"]
    ln, c = mutate(l7b)
    check("L7.undrawn-then", has(ln, "EM-L7", "slice/cmd-alpha") and c == 1)

    def l7c(b):
        b["slices"][1]["slice/gwt"][0]["when"] = "command/submit-alpha"
    ln, c = mutate(l7c)
    check("L7.when-on-view", has(ln, "EM-L7", "slice/view-alpha") and c == 1)

    def l7d(b):
        b["slices"][0]["slice/gwt"][0]["when"] = "command/auto-beta"
    ln, c = mutate(l7d)
    check("L7.foreign-when", has(ln, "EM-L7", "slice/cmd-alpha") and c == 1)

    # EM-L8: orphan field on each leg + skip-when-absent + foreign exemption
    def l8a(b):
        b["event-model/commands"][4]["payload"] = ["title"]      # submit-alpha
        b["event-model/events"][0]["payload"] = ["title", "extra"]  # alpha
    ln, c = mutate(l8a)
    check("L8.event-field-orphan", has(ln, "EM-L8", "event/alpha") and c == 1)

    def l8b(b):
        b["event-model/events"][0]["payload"] = ["title"]
        b["event-model/read-models"][0]["payload"] = ["title", "ghost"]
    ln, c = mutate(l8b)
    check("L8.view-field-orphan",
          has(ln, "EM-L8", "read-model/alpha-list") and c == 1)

    def l8c(b):
        b["event-model/read-models"][0]["payload"] = ["title", "ghost"]
    ln, c = mutate(l8c)  # no event payloads anywhere -> leg skipped
    check("L8.skip-when-absent", not has(ln, "EM-L8", "read-model/alpha-list"))

    def l8d(b):
        b["event-model/events"][5]["payload"] = ["raw"]  # gamma: foreign source
    ln, c = mutate(l8d)
    check("L8.foreign-exempt", not has(ln, "EM-L8", "event/gamma") and c == 0)

    def l8e(b):
        b["event-model/events"][3]["payload"] = ["p"]  # epsilon: emitted, ok
        b["event-model/events"][1]["payload"] = ["q"]  # beta: emitted, ok
        b["flows"].pop("flow/f07")                     # unemit beta
        b["slices"][2]["slice/members"].remove("placement/beta-1")
        b["slices"][2]["slice/gwt"][0]["then"] = []
    ln, c = mutate(l8e)
    check("L8.unemitted-payload", has(ln, "EM-L8", "event/beta") and c == 1)

    # EM-L9: dangling endpoint / type mismatch / no-slice placement / bad ref
    def l9a(b):
        b["flows"]["flow/f02"]["flow/to"] = "placement/nope"
    ln, c = mutate(l9a)
    check("L9.dangling-endpoint", has(ln, "EM-L9", "flow/f02") and c == 1)

    def l9b(b):
        b["flows"]["flow/f03"]["flow/type"] = "display"
    ln, c = mutate(l9b)
    check("L9.type-mismatch", has(ln, "EM-L9", "flow/f03") and c == 1)

    def l9c(b):
        b["placements"].append(_pl("stray-1", 9, "event/id", "event/alpha"))
    ln, c = mutate(l9c)
    check("L9.no-slice-placement",
          has(ln, "EM-L9", "placement/stray-1") and c == 1)

    def l9d(b):
        b["placements"][2]["command/id"] = "command/submit-alpha"  # 2nd ref
    ln, c = mutate(l9d)
    check("L9.double-ref", has(ln, "EM-L9", "placement/alpha-1") and c == 1)

    # EM-L10: strip the mark from an unconsumed event
    def l10(b):
        del b["event-model/events"][1]["event/status"]  # beta
    ln, c = mutate(l10)
    check("L10.unmarked-ellipsis", has(ln, "EM-L10", "event/beta") and c == 1)

    # mirror warn (A8): partial consumed-by list -> one-sided, warn-only
    def mir(b):
        b["event-model/events"][0]["event/consumed-by"] = \
            ["read-model/alpha-list"]
    ln, c = mutate(mir)
    check("mirror.one-sided-warn",
          any(x.startswith("WARN-EM-L9\tevent/alpha\t") for x in ln) and c == 0)

    # finding-line shape: 3 tab-separated fields on every line
    ln, _ = mutate(lambda b: b["flows"].pop("flow/f01"))
    check("contract.three-fields", all(len(x.split("\t")) == 3 for x in ln))

    print("%d failures" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
