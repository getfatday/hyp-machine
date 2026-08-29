# Operating-model meta-schema (v0)

A synthesis of an event-storming-derived node map with Anthropic's authoring format rules.
Instances live at `operating-model/<context>/model.md` in the consumer repository; the
Event Modeling layer over this schema is documented in `grammar/` (see `SCHEMA-DELTA.md`).

## Shape

One node per file, markdown + YAML frontmatter. Frontmatter is the machine surface (greppable,
lintable, compilable); the body holds only distinctions and invariants a reader needs — kept tiny.

```
operating-model/
├── SCHEMA.md                      # this file
└── <context>/                     # one directory per bounded context
    ├── model.md                   # the catalog: every node, one line each (the SKILL.md role)
    ├── actors/<slug>.md
    ├── commands/<slug>.md
    ├── events/<slug>.md
    ├── policies/<slug>.md
    └── readmodels/<slug>.md
```

## Progressive disclosure contract (answers "where does information go")

| Level | What | Standing cost |
|---|---|---|
| 1 | One CLAUDE.md row pointing at `operating-model/` | one line, every session |
| 2 | `<context>/model.md` — catalog with one summary line per node + grep recipes | read on demand, <100 lines |
| 3 | Node files — full frontmatter + body | zero until read; found via catalog links or `grep` over frontmatter |

Rules inherited from Anthropic's guidance: references one level deep (catalog → node, never
node → node → node; nodes may *name* other node ids, resolution goes back through the catalog);
descriptive slugs; consistent terminology; catalog gets a TOC if it passes 100 lines. No RAG —
`grep` over frontmatter keys is the query language (`grep -rl "enforcement: hook" policies/`).

## Frontmatter: common keys (all node types)

```yaml
id: command/register-hypothesis   # <type>/<slug>, unique within the context
type: actor | command | event | policy | read-model | external | aggregate
context: acme-billing
summary: One third-person line; compiled into the catalog.   # ≤140 chars
status: current | hotspot | debt   # hotspot = known pain; debt = known gap, accepted
```

## Type-specific keys

**command** — a request an actor issues; can be refused. The executor field is the swap slot.
```yaml
issued-by: actor/researcher
executor: human | agent | either        # current casting
handler: skill/experiment | script/<path> | manual   # what carries it out
freedom: low | medium | high            # degrees-of-freedom / determinism axis
reads: [read-model/hypothesis-specs]
emits: [event/hypothesis-registered]
invariants-enforced: [policy/one-variable]    # mechanically guaranteed (hook/guard)
invariants-requested: [policy/raw-first]      # prose-only; honest about the difference
```

**event** — an immutable past-tense fact. Claude has no native event bus, so every event must
declare its physical representation, or it doesn't exist.
```yaml
representation: journal-entry(type=capture) | file(events/<type>/*.json) | commit
emitted-by: [command/register-hypothesis]
consumed-by: [policy/journal-append-only, actor/researcher]
```

**policy** — whenever X, then Y; acts on the actor from outside.
```yaml
trigger: event/journal-entry-appended        # domain trigger
enforcement: hook | procedural | saga        # hook = guaranteed; procedural = requested; saga = needs external scheduler (debt)
mechanism: settings.json deny Edit/Write     # how, when enforcement: hook
```

**read-model** — a projection shaped for a decision; must name its maintainer or it rots.
```yaml
implementation: file(hypotheses/) | mcp:<server> | api:<name> | manual
maintainer: command/register-hypothesis      # the command that keeps it current
consumed-by: [command/run-experiment]
```

**actor** — a role, executor-neutral; casting lives on commands, not here.
```yaml
kind: human | main-agent | subagent | role   # today's typical filler, not a binding
authority: reviews specs; sole editor of program.md
```

**external / aggregate** — per the deep map: externals carry `implementation:` + a translation
note; aggregates are owned state directories (`owned-path:`, `guarded-by:`) — model only when one
truly exists.

## Lint rules (deferred until the corpus is bigger than one context)

Orphan read model (consumed, no maintainer); event without representation; command emitting an
undeclared event; hook-claimed policy with no mechanism. All greppable; a `lint.py` is warranted
once there are enough nodes for hand-checking to fail — not before.


## Relational keys (ratified through two converging blind-graded extraction runs)

The relationship IS the model (Brandolini's event-storming rule).
These keys close the disassociated-inventory gap:

- **policy.then** — `then: [command/<slug>, ...]`: the command(s) a policy issues when its
  trigger fires. A policy with neither a resolvable `then:` nor `status: debt` + reason is
  invalid ("no implicit cascading reaction").
- **policy.trigger** — must resolve to event id(s); tool-layer/time triggers become first-class
  events (`representation: tool-event`), never prose.
- **command.issued-by** — widened: legally targets an actor OR a
  policy (time-triggered/policy-cascade issuers like scheduled bots have no deciding human).
- **command.cast-as** — `cast-as: [actor/<slug>, ...]`: the role(s) the executor is cast into
  (distinct from `issued-by`, the decision-maker).
- **command.invoked-on** — `invoked-on: [actor/<slug>|external/<slug>, ...]`: the
  system/aggregate/agent mediating command → event (subagent spawning lives here).
- **readmodel.projects-from** — `projects-from: [event/<slug>, ...]`: the event(s) a read model
  projects.
- **readmodel.consumed-by** — widened to admit actors (read model → informs → person).

Every actor must be reachable from ≥1 command via issued-by/cast-as/invoked-on, else it carries
`status: debt|hotspot` with the gap stated.