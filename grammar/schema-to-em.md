# SCHEMA → EM mapping: the node schema onto the Event Modeling metamodel

Companion to `em-metamodel.md` (K/E/C/M ids refer there). Maps every node type and
relational key of `kernel/operating-model/SCHEMA.md` onto its Event Modeling equivalent,
names what has NO EM equivalent (and stays), and names what EM adds (as additive fields —
nothing here rewrites an existing SCHEMA key).

## 1. Node-type map

| SCHEMA type | EM equivalent | Mapping notes |
|---|---|---|
| `command` | **Command (K2)** — 1:1 | Same concept: "an intention to change the state of the system" vs the schema's "a request an actor issues; can be refused". Refusability = decide's error path, which becomes a named GWT rejection (M5). Gains optional `payload:` |
| `event` | **Event (K1)** — 1:1, plus a required `stream:` key (C6) | The schema's `representation:` (journal-entry/file/commit) has NO EM counterpart and STAYS — EM assumes an event store exists; where none does, the physical-realization honesty rule ("every event must declare its physical representation, or it doesn't exist") remains load-bearing. `stream:` (logical partition) and `representation:` (physical storage) are orthogonal |
| `read-model` | **View (K3)** — 1:1 | "A projection shaped for a decision" ≙ "a query that reads, interprets and curates previously produced data". The schema's `maintainer:` has no EM slot; it becomes derivable when projections are complete and is kept as an optional key meanwhile |
| `policy` | **Splits two ways — policy is NOT an EM node type.** (i) Reactive policies (resolvable `trigger:` events + `then:` commands) → **Automation slices (P-A)**: the policy becomes a job-type Interface (K4) plus its todo-list View; `trigger:` events → the View's projections (E3); `then:` → the job→command trigger edges (E1). (ii) Constraint policies (cited under `invariants-enforced/-requested`, no `then:`) → **GWT rejection paths (M5)** on the constrained command's slice | The processor is "a todo list for some processor in our system" (canon); `react(foreignEvent) → commands` is the code-level twin. The split gives every floating policy exactly two concrete reconciliation targets |
| `actor` | **Audience (K6)** — a persona tag on interface placements, NOT a swimlane container | Actor nodes stay as files (executor-neutral roles with `authority:`); on boards they surface as audience chips on the wireframes that trigger commands |
| `external` | **Foreign stream (K5) + Translation slices (P-T)** | An external system = a stream of foreign events entering via P-T, subject to the one-system read-side rule (C5) |
| `aggregate` | **Stream (K5)** | The same partition concept; `owned-path:`/`guarded-by:` carry over onto the stream node |

## 2. Edge-key map

| SCHEMA key | EM equivalent | Notes |
|---|---|---|
| `command.emits:` | **E2 emission** (command→event) | = decide's `'e list` output. Cardinality 1..n (C1) |
| `command.reads:` | **E4 feed/display + E1**: the views wired `view → (this command's trigger interface) → command` | EM commands never read views directly — the *trigger* consults the view and decides. Algebra: `reads` ≙ decide's folded-state parameter |
| `command.issued-by:` (actor) | **audience on the triggering image-interface** (K4/K6) | The trigger edge carries flow; `issued-by` stays as the decision-attribution key |
| `command.issued-by:` (policy) | **job-interface trigger** (E1 from the automation) | Policy-issued commands get job interfaces from their issuing policies |
| `command.cast-as:` | **No EM equivalent — stays** | Executor-casting (who *performs* vs who *decides*) is a schema extension no EM source models |
| `command.invoked-on:` | **No EM equivalent — stays** | EM has no executor/mediator slot; the empty-slot doctrine renders it as `runs-on: — unmodeled` |
| `command.handler:` / `executor:` / `freedom:` | **No EM equivalent — stay** | The human/agent swap-slot machinery is schema vocabulary, not EM's |
| `command.invariants-enforced/-requested:` | **GWT rejection cases (M5)** on the command's slice: `Then(Throws<NamedRejection>)` | The enforced/requested honesty split is preserved as an attribute on each GWT rejection case (enforced = hook-guaranteed, requested = prose) |
| `policy.trigger:` | **E3 projections into the automation's todo-View** | P-A read side |
| `policy.then:` | **E1 trigger edges, job-interface → command(s)** | "No implicit cascading reaction" is exactly canon's insistence on the mediating View+Trigger — reinforced by E5 (direct event→command edges are import-shims, never strict-legal) |
| `policy.enforcement:` (`hook∣procedural∣saga∣advisory`) + `mechanism:` | **No EM equivalent — stays** on the job-interface node | The guaranteed-vs-requested-vs-advisory honesty axis must not be flattened |
| `readmodel.projects-from:` | **E3 projection** (event→view) | P-V |
| `readmodel.consumed-by:` (command) | **E4 feed** into that command's trigger | See `command.reads:` |
| `readmodel.consumed-by:` (actor) | **E4 display** into an image-interface tagged with that audience | The projection leg event → view → UI → person: direct event→actor consumption gets a real home, the wireframe |
| `event.emitted-by:` / `event.consumed-by:` | Reverse indexes of E2/E3 | One-sided declarations become impossible in the board serialization: a flow either exists (one reified edge) or doesn't. Frontmatter mirrors stay lint-checked |
| `status: hotspot∣debt` + marks | **No EM equivalent — stays** | EM's completeness rules only say *incomplete*; the marks say *known and accepted-for-now* — the open-world layer |

## 3. What has NO EM equivalent — stays, by doctrine

EM models a finished system; this grammar also models systems **under reconciliation**. The
following machinery survives the mapping untouched because deleting it would delete the
honesty layer:

1. **The fragments parking lot** (`slice-board.md` §3) — incompleteness is rendered, not
   declared away.
2. **Debt/hotspot marks and `reason:` strings** (marked ellipsis beats a hard fail).
3. **`event.representation:`** — the no-native-event-bus honesty rule.
4. **`enforcement:`/`mechanism:` classes** including the advisory vocabulary.
5. **`issued-by`/`cast-as`/`invoked-on`** — decision, casting, and execution attribution.
6. **Empty-slot placeholders** (unmodeled system, zero-input commands).

## 4. What EM adds — additive field proposals (each a two-way door)

| # | Addition | Shape |
|---|---|---|
| N1 | **Node type `interface`** — `<model_dir>/<context>/interfaces/<slug>.md` | `type: interface`; `interface-type: image ∣ job ∣ blank`; `audience: [actor/<slug>]` (image only); `asset: {url, width, height}` (image, optional); job nodes absorb the automation half of reactive policies (§1) |
| N2 | **Node type `stream`** + required `event.stream:` | `type: stream`; `name`; carry-overs `owned-path:`/`guarded-by:` from aggregate; every event names exactly one stream (C6) |
| N3 | **Reified `slice` objects** in the board serialization | Shape per `em-metamodel.md` §6 |
| N4 | **GWT specs per slice** | Given events / When command / Then events XOR named rejection; view slices: Given/Then. A GWT case IS a binary assertion |
| N5 | **Optional `payload:`** on event/command entities | Field list or JSON-schema ref; prerequisite for lintable M1/M3 field tracing |
| N6 | **Placement layer** in the board serialization | `placement/{id, index, entity-ref}`; flows reference placement ids |

## 5. Migration recipe (any existing SCHEMA-shaped model → slices)

1. One **P-C slice per command**; commands with actor issuers get image/blank trigger
   interfaces (blank until a screen or prompt surface is designed) tagged with their
   audiences; policy-issued commands get job interfaces from their issuing policies.
2. One **P-V slice per read model** with resolvable `projects-from`; views without
   projections enter the reconciliation queue: gain projections or reclassify as reference
   data.
3. **Reactive policies** (trigger + then resolvable) become P-A slices — todo-View +
   job-interface + issued command(s). Floating policies get two named targets each: become
   a P-A slice, or become GWT rejection cases on the command they constrain.
4. **Externals** become foreign streams entering through P-T read sides (one system per
   read side, C5).
5. Every event gets a `stream:` assignment (C6); constraint decorations become GWT
   rejection-path candidates (N4); unattached mentions go to the parking lot, declared.
6. One-sided edge declarations dissolve into reified flows; frontmatter mirrors stay
   lint-checked.
