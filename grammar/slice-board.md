# Slice-board layout + conformance — the EM board grammar

Companion to `em-metamodel.md` (K/E/C/M ids refer there). This is the layout the diagram
lane renders and the contract `scripts/em-slice-lint.py` checks mechanically.

## 1. The bands

Three horizontal bands, timeline flowing left → right ("events are arranged horizontally
along a timeline; left-to-right axis = chronological order",
https://eventmodeling.org/posts/what-is-event-modeling/):

```
┌────────────────────────────────────────────────────────────────────────┐
│ BAND 1 — TRIGGERS / UI: interface cards (image = wireframe + audience  │
│ chips; job = automation gear; blank = dashed placeholder)              │
├────────────────────────────────────────────────────────────────────────┤
│ BAND 2 — TIMELINE: commands (blue) + events (yellow, stream-tagged),   │
│ chronological left → right                                             │
├────────────────────────────────────────────────────────────────────────┤
│ BAND 3 — VIEWS: read models (green)                                    │
├────────────────────────────────────────────────────────────────────────┤
│ FRAGMENTS PARKING LOT (declared open-world layer — §3)                 │
└────────────────────────────────────────────────────────────────────────┘
```

Band 1 (wireframes/triggers top) and Band 2 (commands + events middle) follow the founding
document; Band 3 (views bottom) follows the explicit lane list in
https://www.pradhan.is/blogs/event-modelling-best-practices ("UX top, Logic middle, Data
lower"). Orientation is presentation-only — the metamodel is orientation-agnostic.

**Cards and colors.** White image/blank/job interfaces; blue commands; yellow events; green
views. Collision rule: anything else a board styles yellow restyles, so yellow means only
EVENT. Actors surface as small neutral-gray **audience chips** on image interfaces (K6),
never as their own cards. Constraint decorations become **GWT chips** on the slice header
(§2). Job interfaces: white card + gear glyph. Blank interfaces: dashed outline, label
"wireframe: unmodeled — reconcile?" — an empty slot is rendered, never hidden.

**Extension chips** (keys the base SCHEMA carries that EM has no slot for): `invoked-on` →
a small footer chip on the command card (`runs-on: <target>` or `runs-on: — unmodeled`);
`cast-as` → an executor-casting chip; debt/hotspot → their existing marks.

**Stream rendering (C6).** Every event card carries a stream tag chip; a stream legend sits
in the board header.

## 2. Slices as vertical strips

A slice is a vertical strip crossing all three bands — one instantiation of one pattern.
Serialization: reified slice objects (`em-metamodel.md` §6) over placements sharing
`placement/index` columns (C7 — same-index co-location is the co-slice signal).

**Strip header row** (above Band 1): slice id + pattern chip + GWT chip:

| Chip | Pattern | Strip contents top→bottom | Accent |
|---|---|---|---|
| `CMD` | P-C | Band 1: image/blank interface (+audience chips); Band 2: command, then its emitted event(s); Band 3: the views feeding the trigger (E4 arrows rising) | blue |
| `VIEW` | P-V | Band 2: source event placements; Band 3: the view (E3 arrows descending) | green |
| `AUTO` | P-A | Band 2: source events; Band 3: todo-list view; Band 1: job interface (gear); Band 2: issued command + its events | lilac |
| `TRANS` | P-T | As AUTO, plus the source-system/stream badge on the read side; write side unrestricted | pink |

Rules:

- **One strip per slice; entities recur across strips as placements** (K8) — repeat by
  design, no cross-strip dedup lines. Cross-slice chaining is expressed by the shared
  entity id, visible via identical card labels + stream chips.
- **Strip order is deterministic**: P-C strips first in catalog order of their commands,
  then P-V in catalog order of views, then P-A/P-T in catalog order of their issuing
  automations. Two renders over the same tree must be byte-identical.
- **Intra-band stacking within a strip**: alphabetical by node id.
- **Migration blanks**: a P-C strip whose command has no modeled trigger renders a blank
  interface placeholder; a strip is never silently narrowed to hide an empty band slot.
- **GWT chip states**: `GWT: n` (n cases attached), `GWT: 0 — unspecified` (renders as a
  hotspot-tinted chip; M5 makes this definitionally incomplete).

## 3. The fragments parking lot — the open-world layer

A tinted/framed band below the grid holds *operational fragments*: mentions cited in
evidence that name something real but attach to no slice yet, each carrying a `RECONCILE?`
chip (and optionally a target hint: `→ P-A?`, `→ GWT-rejection?`, `→ needs projection?`).
Being outside all slices is LEGAL when declared — the lint treats the parking lot as
declared, not defective. This is the layer EM lacks: EM models a finished system; a model
under reconciliation renders its incompleteness instead of hiding it.

## 4. Conformance rules (what `em-slice-lint.py` checks)

Mechanical, each naming its defect; metamodel ids in parentheses. Severity: **fail** unless
noted; import shims and one-sided frontmatter mirrors are **warn**.

| Rule | Check | Defect name |
|---|---|---|
| **EM-L1** | every command has ≥1 incoming E1 trigger edge from an interface; E5 shim edges satisfy it at warn severity (C3) | triggerless command |
| **EM-L2** | every event names exactly one stream; all its placements agree (C6) | streamless / ambiguous-stream event |
| **EM-L3** | every view has ≥1 E3 projection from an event (M2) | view from nowhere |
| **EM-L4** | every job interface has an upstream feed view (itself EM-L3-clean) and ≥1 outgoing E1 to a command — the todo-list loop closed end to end (P-A) | open automation loop |
| **EM-L5** | in every TRANS slice, all events projected into the read-side view share one source system/stream; no restriction on the write side (C5) | mixed-source translation |
| **EM-L6** | every slice's members chain via E1–E4 into exactly one of the four pattern sequences end to end (M4); every placement in the slice is on the chain | malformed slice |
| **EM-L7** | every slice carries ≥1 GWT case; each case's When is the slice's command (P-C/A/T) or absent (P-V); Then events ⊆ the slice's drawn output events, or a named rejection the slice declares (M5) | unspecified / mismatched slice |
| **EM-L8** | where payloads exist (optional): every image-interface field traces to a view field, every view field to an event field, every event to a triggering command (M1/M3; skipped, not passed, where payloads are absent) | orphan field |
| **EM-L9** | every flow endpoint resolves to a placement; every placement resolves to an entity; every edge's type matches its endpoint-kind pair per the E-table; every non-parking-lot placement belongs to ≥1 slice | dangling placement / illegal edge |
| **EM-L10** | every event with zero consuming edges carries a debt/hotspot mark (M6 marked-ellipsis rule) | unmarked ellipsis |

Legal distractor constructs a lint must NOT flag: parking-lot fragments (declared), blank
interfaces (declared migration state), marked ellipsis events, multi-stream views on
non-TRANS slices (C5 allows), multi-event emissions (C1), one job interface issuing
different commands from different placements (C8), entities recurring across many slices
(C7).

## 5. Board JSON (what the diagram lane emits and consumes)

`scripts/model-to-board.py` projects model nodes into this serialization; `render_flow.py`
and `flow_composer.py` render flow views over it. Top-level shape:

- `placements`: id → `{index, entity-ref}` (exactly one of event/command/read-model/
  interface)
- `flows`: edge id → `{from, to}` over placement ids (types derived per the E-table)
- `slices`: slice objects per `em-metamodel.md` §6
- `streams`: the stream legend
- fragments parking lot: declared unplaced mentions (§3)

Two renders over the same tree are byte-identical; renderers are stdlib and headless.
