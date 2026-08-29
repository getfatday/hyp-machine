# Channel consent: the feedback-to-issue discipline

> **NOT LIVE-WIRED.** Everything below is proven in sandbox only. Live wiring of the
> consumer channels — enabling Discussions, porting the issue form onto a real repository,
> letting a session actually file to GitHub — **stays gated on the maintainer channel-deploy
> ruling** (`closes-when: maintainer-ruling=channel-deploy`, the source lab's needs-Ian
> item 10). This document and the templates under `templates/channel/` ship so the counted
> discipline is durable and reviewable; nothing in this plugin files anything anywhere.

Two consumer channels, one discipline: a consumer question becomes a structured Discussion
post; in-session feedback becomes a consent-gated, dedup-checked, form-valid issue payload;
and the lab-side router translates both into typed finding rows. The load-bearing rule
everywhere: **the tool PREPARES, the human FILES** — the shape every fetched precedent
converges on (`go bug` opens a prefilled page and nothing exists until the user submits;
VS Code bundles metadata and live-searches similar issues before submission; Claude Code's
own `/bug` confirms on a consent screen and degrades to a local bundle).

## The consent lines (binding on anything a consumer session files)

1. The consumer sees the **exact sanitized payload that would transmit**, plus any dedup
   matches, BEFORE anything leaves the session; the filing command runs only on an explicit
   yes (`issued-by: actor/plugin-consumer`, never a policy — no auto-file path exists).
2. The consent screen is the fixed template (`templates/channel/consent-screen.txt`),
   rendered with the payload and matches — never improvised prose:

   > I can file this as a GitHub issue on {repo} under your GitHub account. Here is the
   > exact payload. Found {n} similar issues — comment there instead?
   > [file / comment-on-#{dupe} / save locally / drop]

3. Declined, offline, or no gh/auth: a **local bundle** is persisted (field-aligned with the
   issue-form schema) or a prefilled-URL handoff is offered — the signal is never dropped
   and never sent silently.
4. Simulated-consumer sessions in sandbox runs obey the same lines: the harness plays the
   consenting human explicitly, so "an unconsented detection never files" stays checkable.

## The five decided rules of the detection-to-issue slice

1. **No auto-file** — the only code path that transmits is the explicit
   `consent <id> file` answer; re-detecting an already-drafted detection throws
   `AlreadyDrafted` (the double-fire guard).
2. **Metadata that ships** — plugin version, skill involved, failing invocation + output
   excerpt, repro context, mapped onto the issue-form fields (`plugin_version`,
   `failing_assertion`, `journal_excerpt`, `repro_steps`, `expected_vs_actual`) so intake
   arrives pre-triaged.
3. **Sanitization before display** — credentials and private URLs are redacted BEFORE the
   consent screen renders; the draft the consumer sees is exactly what would transmit.
   The counted detection patterns (frozen in the fixture):
   - `ghp_[A-Za-z0-9]+` → `[REDACTED-CREDENTIAL]`
   - `(?i)\b(token|api[_-]?key|secret|password)\s*[=:]\s*\S+` → `[REDACTED-CREDENTIAL]`
   - `https?://[^\s"']*(?:internal|intranet|corp)[^\s"']*` → `[REDACTED-PRIVATE-URL]`
4. **Dedup is explicit and advisory** — a deterministic similarity search (Jaccard over
   lowercased alphanumeric token sets of title + failing assertion, threshold 0.40) runs
   BEFORE the consent screen; matches are offered ("comment there instead?"), never
   auto-merged.
5. **Degradation rule** — declined → local bundle under `feedback/`; no gh or no auth →
   prefilled `issues/new?template=` URL handoff. Never silent, never dropped.

## The reply discipline

Router replies are deterministic templates, never free agent prose
(`templates/channel/reply-ack.json.tmpl`, `templates/channel/reply-reject.json.tmpl`):
a valid payload gets one `finding-recorded` ack carrying its resolvable
`caused_by=<surface>:<id>`; a payload missing a required field gets exactly one
field-pointer reject naming the missing field, and NO finding row (reject terminates at the
rim). A question is a finding — the router lands typed finding rows
(definition-gap / mislead / affordance-gap / procedure-gap), consumed like lint findings,
not chat.

## What ships now vs. at the channel-deploy ruling

| Piece | Status |
|---|---|
| Consent-screen + reply templates (`templates/channel/`) | Ships in 0.3.0, inert — canonical copies of the counted fixture templates (the consent screen's target repo generalized to `{repo}`; the counted fixture pinned the source lab's repo) |
| This document (the consent lines + detection patterns) | Ships in 0.3.0 |
| The detection/consent/dedup scripts, the Q&A Discussion form (`DISCUSSION_TEMPLATE/q-and-a.yml`), Discussions enablement, ISSUE_TEMPLATE port, `config.yml` deflection, SUPPORT.md | **Held at the channel-deploy gate** — the real-repo deploy bundle enters the maintainer queue and lands only on that ruling |

## Evidence

**H-125-channel-round-trip** (source lab, kept 2026-08-27, two consecutive counted 5/5 with
peer-verified byte-reproduction): on scratch clean-install clones, the ask-for-help payload
validated against the Q&A form and landed exactly one typed finding row; the detection
produced a sanitized draft, a consent exchange preceded any filing, the dedup step surfaced
the seeded near-duplicate as an offer, and the declining arm yielded a local bundle and
filed nothing; the malformed payload got exactly one field-pointer reject; the no-consent
detection never produced a filed payload; zero writes reached either live repository. All
GitHub legs ran sandboxed (file-based transport per the kept H-106 precedent); the
live-sandbox leg registers as its own hypothesis, and live wiring waits on the
channel-deploy ruling above.
