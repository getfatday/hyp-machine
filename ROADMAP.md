# Hyp Machine — Roadmap

Everything here is **ordered, not dated** — a release ships when its payload is done, never on a calendar, and queue position is not a promise.

This file is **compiled** from the plugin lab's machine-readable release plan plus each feature's spec status. Do not edit it by hand — it is regenerated whenever the plan moves, and hand edits are overwritten.

Every feature ships only after a spec with binary pass/fail assertions survives its validation runs in the lab that builds this plugin. Ids like `H-262` name those specs; they appear here so every claim is traceable — you never need them to use the plugin.

Feature states: `Shipped` · `Done — awaiting release` · `In progress` · `Paused (reason)` — paused is explicit so a stalled item never masquerades as moving · `Planned` · `Dropped` · `Superseded`.

## Shipped

Marketplace releases, oldest first — what each carried, and where it came from in the plan.

### v0.1.0 — first public release

[Release notes](https://github.com/getfatday/hyp-machine/releases/tag/v0.1.0)

The hypothesis-lab plugin under its public name: spec-first hypotheses with binary assertions, budgeted validation runs, mechanical keep/refine/discard verdicts, and write-once journal capture. This release folded in everything the plugin's pre-rename train had already proven — environment health (an auth doctor plus release-integrity instruments), consumer channels and feedback intake, CI scaffolding, and the observatory/metrics layer.

**From the plan — Environment health: incident handling, an auth doctor, and release-integrity instruments:**

| Feature | Status |
|---|---|
| dangling end pickup v3 (H-188) | Shipped |
| doctor flap detection (H-182) | Shipped |
| doctor guided remediation (H-183) | Shipped |
| crux lab parity (H-181) | Shipped |
| citation archival v2 (H-189) | Shipped |

*…plus 8 internal plan rows not individually tracked here.*

**From the plan — Consumer channels, feedback intake, CI scaffolding, and governance gates:**

| Feature | Status |
|---|---|
| channel round trip (H-125) | Shipped |
| issueops live tier1 (H-136) | Shipped |
| ci scaffold v2 (H-198) | Shipped |
| ethics gate (H-132) | Shipped |
| directive intake v2 (H-199) | Shipped |

**From the plan — Observatory and metrics: stall signals, session identity, case studies, and metric lints:**

| Feature | Status |
|---|---|
| observatory stall signals (H-154) | Shipped |
| observatory identity attribution (H-156) | Shipped |
| case study v2 (H-201) | Shipped |
| metric coverage lint (H-128) | Shipped |
| autonomy trend (H-129) | Shipped |
| gwt accretion loop (H-118) | Shipped |
| event first extraction ordering (H-180) | Shipped |

### v0.2.0 — work-graph dispatch and session durability

[Release notes](https://github.com/getfatday/hyp-machine/releases/tag/v0.2.0)

Ranked next-item dispatch at session boundaries, claim takeover with attributed record-before-write, two-writer claim joins, strike-based quarantine, reboot-surviving scheduled resume, and the compression-surviving re-hydration protocol — plus the plain-language vocabulary, clarity lint, and case-study rendering.

> Note: The note shown on this tag in the marketplace currently describes the environment-health payload that actually shipped in v0.1.0 — a numbering mix-up between internal plan groupings and plugin versions. The tagged payload is the dispatch and durability kit above (see the tag's release commit); a corrected note is pending.

**From the plan — Work-graph part 1: session durability, ranked dispatch, and boundary gates:**

| Feature | Status |
|---|---|
| workgraph compression survival v2 (H-231) | Shipped |
| workgraph live dispatch install (H-175) | Shipped |
| boundary ranked dispatch v2 (H-230) | Shipped |

*…plus 12 internal plan rows not individually tracked here.*

### v0.3.0 — flow governance and the teaching preflight

[Release notes](https://github.com/getfatday/hyp-machine/releases/tag/v0.3.0)

Model-compiled orchestration (H-177): the repository's operating model compiles into runnable lane orchestration. Plus the flow-governance layer — flow-leak meter, rules registry, and the breach-to-autopsy reflex chain — dispatch refill so the work queue never reads empty while work exists, and the teaching preflight: the spec gate now prints accepted example phrasings in its own failure details (fixes issue #5).

**From the plan — Work-graph part 2: model-compiled orchestration, instance layer, telemetry, and compiled-flow refinements:**

| Feature | Status |
|---|---|
| instance graph orchestrator compilation (H-177) | Shipped |

*…plus 1 internal plan row not individually tracked here.*

*Only part of this plan wave rode this release — the remainder appears under Upcoming.*

**From the plan — Consumer-reported hardening: fixes routed from filed issues:**

| Feature | Status |
|---|---|
| preflight phrase transparency (H-257) | Dropped (follow-up lane registered) |

*Only part of this plan wave rode this release — the remainder appears under Upcoming.*

### v0.3.1 — consumer-hardening patch

[Release notes](https://github.com/getfatday/hyp-machine/releases/tag/v0.3.1)

Two consumer-reported gaps closed the day they were verified: the fixture-freshness gate (H-262) — the preflight re-hashes declared fixture pins and fails closed on drift (fixes issue #3) — and the claim-type gate (H-258) — specs declare descriptive or normative, and a descriptive spec carrying decision rows fails with a bridging route (fixes issue #2). Legacy specs advise-only; zero verdict flips across the live spec corpus.

**From the plan — Consumer-reported hardening: fixes routed from filed issues:**

| Feature | Status |
|---|---|
| fixture freshness gate (H-262) | Shipped |
| claim type gate (H-258) | Shipped |

*Only part of this plan wave rode this release — the remainder appears under Upcoming.*

### v0.3.2 — worktree-aware hook root

[Release notes](https://github.com/getfatday/hyp-machine/releases/tag/v0.3.2)

Hooks now grade the tree the session works in, not the checkout that launched it (fixes issue #6). Validated by a dedicated lane (H-278): four clean validation runs by cold-context executors, an adversarial amendment round, and re-verification across 47 real git layouts.

*This release carried work that arrived after the current plan was drawn — no plan rows map to it.*

## Upcoming

Ordered by plan position, not by date. **Next** holds the open milestones plus the remainder of plan waves a release has already partly carried; **Later** holds queued waves no release has carried yet.

### Next

Open milestones on the plugin repo — verified reports route here, and each closes when the release carrying its fix ships:

- [0.3.x — hardening patches](https://github.com/getfatday/hyp-machine/milestone/1) — verified consumer-reported gaps whose fixes ship as patches; the atomic id/run allocation report (issue #4) is the open item currently routed here
- [0.5.0 — dispatch tranche](https://github.com/getfatday/hyp-machine/milestone/2) — the ranked-dispatch redesign; the stop-dispatch relevance and snooze request (issue #1) routes here

**Work-graph part 2: model-compiled orchestration, instance layer, telemetry, and compiled-flow refinements** *(remainder of a partly shipped plan wave)*:

| Feature | Status |
|---|---|
| model step telemetry (H-164) | Paused (blocked-environmental, parked) |
| cross session correlation (H-165) | In progress |
| fusion caused by parity (H-173) | Done — awaiting release |
| then operator parity (H-174) | Done — awaiting release |
| compiled policy gate (H-144) | Dropped |

*…plus 7 internal plan rows not individually tracked here.*

**Consumer-reported hardening: fixes routed from filed issues** *(remainder of a partly shipped plan wave)*:

| Feature | Status |
|---|---|
| shipped atomic allocation (H-259) | Dropped (follow-up lane registered) |
| dispatch relevance affordance (H-260) | Planned (spec registered) |
| compiled roadmap milestones (H-261) | Done — awaiting release |

### Later

**Trial management: scheduling and freeze law, idle refill, concurrency covariates, phase gates, and a runs census:**

| Feature | Status |
|---|---|
| concurrency inflation covariates (H-271) | Done — awaiting release |
| checkpointed phase gates (H-266) | Planned (spec registered) |
| rung promotion collapse (H-270) | Planned (spec registered) |
| runs census board (H-269) | Planned (spec registered) |
| sched freeze law (H-268) | Done — awaiting release |
| idle refill floor (H-267) | Done — awaiting release |

**Agentic OS: a generic event interface and operating-model router:**

| Feature | Status |
|---|---|
| ingress envelope generality (H-288) | Planned (spec registered) |
| node field routing (H-287) | Planned (spec registered) |
| router direct invoker (H-286) | Planned (spec registered) |
| event dead letter card (H-285) | Planned (spec registered) |
| mail monitor pipeline (H-289) | Planned (spec registered) |

## Feedback

This roadmap is compiled — comments on the file itself are not read. File gaps, requests, and questions at: https://github.com/getfatday/hyp-machine/issues

<!-- compiled by hyp_roadmap.py public; inputs-fingerprint: 4cf79ffbe636fb47 -->
