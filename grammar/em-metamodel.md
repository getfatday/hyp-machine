# Event Modeling metamodel — reconciled

The Event Modeling layer this plugin's board serialization, lints, and compiler share.
Reconciled from public sources, anchored on a real serialization rather than prose:
**(1)** the Evident Design JSON exercise exports of
https://github.com/test-driven-development/learn-practical-event-modeling (final model:
12 commands, 7 read-models, 8 interfaces, 2 streams, 62 placements, 52 flows),
**(2)** eventmodeling.org canon (https://eventmodeling.org/posts/what-is-event-modeling/ +
https://eventmodeling.org/posts/event-modeling-cheatsheet/), **(3)** semantics sources
(https://thinkbeforecoding.com/post/2021/12/17/functional-event-sourcing-decider,
https://event-driven.io/en/testing_event_sourcing/,
https://www.pradhan.is/blogs/event-modelling-best-practices,
https://developer.confluent.io/courses/event-modeling/domain-functions/). Where sources
disagreed, the adopted position is stated inline as a rule — this document is the plugin's
normative grammar, not a survey.

Provenance tags: `[EVIDENT]` = observed in the exercise JSON; `[CANON]` = eventmodeling.org
text; `[ALGEBRA]` = decide/evolve semantics; `[GWT]` = executable-specification sources;
`[SCHEMA]` = this plugin's node schema (`kernel/operating-model/SCHEMA.md`).

## 0. Reading rules

- **Entity vs placement.** A named entity (event, command, read-model, interface) is
  distinct from its positioned *occurrences* on the board (`placement/id` +
  `placement/index` + exactly one entity ref). One entity, many placements — high
  cardinality shows up as MORE PLACEMENTS of the same node, not higher-degree single nodes
  `[EVIDENT]`.
- **Model vs board.** The *model* stays one-node-per-file YAML frontmatter (the SCHEMA
  shape); *placements, flows, and slices* are board-serialization objects.

## 1. Node kinds

| # | EM kind | Serialization | SCHEMA name | Definition |
|---|---|---|---|---|
| K1 | **Event** | `event/id`, `event/name` | `type: event` | "Describes a business fact that mutated the state of the system and was saved to disk." `[CANON]` Non-state-changing occurrences are explicitly NOT events |
| K2 | **Command** | `command/id`, `command/name` | `type: command` | "An intention to change the state of the system." `[CANON]` |
| K3 | **View** (read model) | `read-model/id`, `read-model/name` | `type: read-model` | "A query that reads, interprets and curates previously produced data and provides it for a specific user interface." `[CANON]` Passive: cannot reject stored events |
| K4 | **Interface** | `interface/id`, `interface/name`, `interface/type` ∈ {image, job, blank} | `type: interface` (additive) | The one polymorphic entity: image = wireframe (+`interface/audience`), job = automation/processor with no human UI, blank = placeholder `[EVIDENT]` |
| K5 | **Stream** | list of stream names; binding via the event's `stream:` key | `type: stream` (additive) | The logical timeline/partition an event belongs to `[EVIDENT]` |
| K6 | **Audience** | `audience/id`; attaches ONLY to interface placements | `type: actor` (mapped) | A persona tag on UI placements `[EVIDENT]` — never a structural swimlane container |
| K7 | **Slice** | reified object (§6) | additive | "A slice is the smallest possible work that can be handed over to a developer for implementation." `[CANON]` Reified here because GWT specs, pattern conformance, and lint verdicts need an addressable unit — neither the anchor serialization nor the base SCHEMA has one |
| K8 | **Placement** | `placement/id`, `placement/index`, one entity ref | board-only | The positioned occurrence of an entity on the timeline `[EVIDENT]` |
| K9 | **Flow** (edge) | dict keyed by edge id; `{flow/from, flow/to}` over PLACEMENT ids | board-only | Reified, addressable edges `[EVIDENT]`; typing per §3 |

Not node kinds: **Trigger** and **Automated Trigger** are *roles* an Interface plays (image/API
= Trigger, job = Automated Trigger). **Translation** is a pattern name, not a box. **State**
is never drawn — it is the hidden accumulator behind Command and View boxes `[ALGEBRA]`.

## 2. The four patterns (the closed slice grammar)

| Pattern | Sequence | Constraint |
|---|---|---|
| **P-C Command** | Trigger(interface: image/API) → Command → Event(s) | every command has a trigger (C3) |
| **P-V View** | Event(s) → View | "only information that already exists can be interpreted and presented in the view." `[CANON]` |
| **P-A Automation** | Event(s) → View → Automated Trigger(interface: job) → Command → Event(s) | the View is "a simple todo list"; it exists to prevent the same automated command firing twice `[CANON]` |
| **P-T Translation** | Event(s) [source system] → View → Automated Trigger(job) → Command → Event(s) | "On the read side of the pattern you can only read events from one system. The write side has no limitation." `[CANON]` |

Algebra bindings `[ALGEBRA]`: `decide: 'c -> 's -> 'e list` implements P-C;
`evolve: 's -> 'e -> 's` implements P-V (a View is a Decider with the command half
amputated, and the replay law holds — two views over the same events always agree);
`react: foreignEvent -> commands` implements P-A/P-T. State-fold law:
`state_n = fold evolve initialState [e_1; ...; e_n]` — replay = truth.

## 3. Legal edges (typed; the type is DERIVABLE from the endpoint-kind pair)

| # | Edge | From → To | Reading |
|---|---|---|---|
| E1 | `trigger` | interface(image or job) → command | selects which `decide` to invoke |
| E2 | `emission` | command → event | `decide` output (`'e list`) |
| E3 | `projection` | event → view | one `evolve` application |
| E4 | `display` / `feed` | view → interface | `display` when the interface is an image; `feed` when it is a job (the todo-list feeding the automation) |
| E5 | `trigger-elided` (import shim, auto-flagged) | event → command | NOT a pattern leg in canon; import-legal as a flagged shim, strict-illegal — the mediator (View + job interface) must be reified or the elision recorded as debt |

Verification recipe for any import: resolve every flow endpoint to its placement's owning
collection, then check the (from-kind, to-kind) pair lands in E1–E4; pairs landing in E5 get
the shim flag; any other pair is an illegal edge.

## 4. Cardinality constraints

| # | Constraint | Rule |
|---|---|---|
| C1 | Command → Event emission | **1..n** per command (canon's pattern text is plural; a 1:1 restriction is rejected — multi-outcome commands, e.g. accepted vs refused, are legal) |
| C2 | Event → Command (upstream) | **1..n commands may emit the same event** (each emission edge belongs to exactly one slice) |
| C3 | Trigger fan-in at a command | **≥1 trigger edge required** (image, job, or an E5-flagged shim) — a command with no trigger is a defect, not a style choice |
| C4 | Event → View projection | **0..n views per event; 1..n events per view** (a view with 0 projected events is incomplete, M2) |
| C5 | View → streams | multi-stream reads LEGAL in general; **exactly 1 source system on a Translation slice's read side** `[CANON]` |
| C6 | Event → Stream | **exactly 1, stable** — entity-level `stream:` key; an import whose placements disagree on one event's stream is rejected |
| C7 | Entity → placements | **1..n**; same-index co-location is the raw co-slice signal |
| C8 | Automation branching | one job interface MAY issue different commands from different placements |

## 5. Completeness rules

| # | Rule | Source |
|---|---|---|
| M1 | **Every field accounted for.** "All information has to have an origin and a destination." | `[CANON]` |
| M2 | **Views show only existing information** — every displayed field traces back to a prior event | `[CANON]` |
| M3 | **Field-level tracing both directions**: every UI field traces back to a source Event, and every Event connects to the Command that triggered it; nothing implicit | `[GWT]` |
| M4 | **Slice completeness** = one full pattern sequence present end to end (§2) | `[CANON]` |
| M5 | **Executable slice validity = a passing GWT case** (§6): a slice with no passing GWT case, or whose Then matches nothing drawn, is definitionally invalid/incomplete | `[GWT]` |
| M6 | **Well-typed events are emitted AND folded** — softened here to a MARK: an event with no consumer parses as a deliberate ellipsis iff it carries an explicit debt/hotspot mark | `[ALGEBRA]`, softened per the open-world doctrine (§7) |
| M7 | **Narratability** — validate by narrating the blueprint aloud end to end (advisory, not mechanical) | `[GWT]` |

## 6. Slice object + GWT (the executable validity hook)

Reified slice, serialized in the board JSON:

```yaml
slice/id: <uuid or slug>
slice/pattern: command | view | automation | translation     # exactly one of §2
slice/members: [<placement ids>]        # every placement in the strip; must chain via E1-E4
slice/index-range: [lo, hi]             # the placement/index columns the strip spans
slice/gwt: []                           # >=1 case required for completeness (M5)
slice/status: current | hotspot | debt  # open-world marks carry over
```

GWT case shape `[GWT]`:

```
Spec.Given(<event_1>, ..., <event_n>)   # prior events; folds via evolve to the state decide sees
    .When(<command>)                    # the single command of this slice (P-C/P-A/P-T)
    .Then(<expected_event_1>, ...)      # OR Then(Throws<NamedRejection>) — documented unhappy path
```

Validity conditions: (1) Given must reduce via evolve to a *reachable* state, not an
arbitrary fixture; (2) When applies exactly the slice's drawn Command and only it; (3) Then
deep-equals either the slice's drawn output Event(s) or a *named* rejection the slice also
declares; (4) deterministic and side-effect-free — same Given/When always yields the same
Then. For **P-V slices** the When is omitted: Given(events) → Then(view-state assertion).
GWT lives on the slice, not on command or view entities — commands and views recur across
slices via placements (C7), so per-entity storage would smear one slice's Given/Then into
another's.

## 7. Adopted positions (where sources disagree, the rule that governs here)

1. **Trigger-mandatory**: every command has ≥1 incoming E1 (or an E5-flagged shim) — C3.
2. **The cheat sheet is the normative four-pattern grammar** (the founding post is an
   informal precursor).
3. **The processor is a first-class node**: `interface` with `type: job` IS the processor;
   the P-A pattern remains the wiring rule.
4. **Slices are reified** (§6), with emergent grouping (shared `placement/index` + E1–E4
   chaining) retained as the IMPORT INFERENCE rule — imports group placements, then
   materialize slice objects; dropping slice objects recovers a valid anchor-shaped export.
5. **The entity/placement split is adopted**; placements live in board JSON, entities stay
   one-file-per-node frontmatter.
6. **Edges are typed**, with the type mechanically derivable from the endpoint-kind pair —
   imports infer types with zero prose, and a declared type disagreeing with its endpoints
   is a lint failure.
7. **Event↔stream binding is entity-level** (`stream:`, exactly one — C6).
8. **Unconsumed events are legal iff marked** (debt/hotspot) — the open-world doctrine:
   absence of a relation is itself a fact worth rendering, not the absence of a fact.
9. **Colors**: white interfaces / blue commands / yellow events / green views (the one
   extractable public legend); anything else on a board restyles so yellow means only EVENT.
10. **GWT specs live per slice** (§6).
11. **Payload schemas are a named optional slot** (`payload:` on event/command entities) —
    field-level completeness (M1/M3) is checkable only where payloads exist; requiring them
    everywhere at once is not a one-variable migration.
12. **Audience is a persona tag on interface placements**, never a swimlane container; the
    SCHEMA's decision-attribution keys (`issued-by`, `cast-as`) stay as accountability
    extensions (see `schema-to-em.md`).
