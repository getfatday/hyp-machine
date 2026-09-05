---
name: intake
description: Ingest any new knowledge into this repository through its capture process (adapted from the ingest flow in Karpathy's LLM Wiki pattern) — findings, learnings, notes, decisions, or files Claude creates for its own reference. Use this skill whenever you are about to save, write down, record, capture, or file away information that is not an edit to existing content — even if the user just says "note this", "save that", "remember this", or you are creating a self-reference file. Every new knowledge artifact enters through this process.
---

# Capture knowledge into the repository

This repository runs on a capture convention adapted from the ingest flow in Karpathy's LLM Wiki
pattern (which contributes the linked-wiki, index, and append-only log ideas but is explicitly
abstract and optional): every piece of knowledge lives in exactly one right place, is as small as
it can be, is linked so it can be found, and is journaled so its arrival is traceable. Unfiled or
duplicated knowledge silently rots — nothing bypasses this process, including files you create
for your own reference.

## Paths

Defaults below; if `.claude/hyp.json` exists at the repo root, its values override
them. Run `/hyp:init` once per repository to scaffold everything.

| Purpose | Default |
|---|---|
| Raw verbatim sources (write-once) | `research/raw/` |
| Distilled pages / studies | the directory holding the index file (default `research/`) |
| Self-reference notes | `research/notes/` |
| Wiki index (one line per page) | `research/index.md` |
| Journal fragments (write-once) | `experiments/journal-fragments/` |

## Process

0. **Raw first** — if the input is something substantial a human said or provided verbatim
   (brain dump, pasted doc, transcript, key external source), file it untouched in
   `<raw dir>/<YYYY-MM-DD>-<slug>.md` *before* distilling, opening with a provenance header.
   Create the new file with a Bash `tee` heredoc — the sanctioned creation mechanism for
   write-once files (the settings deny protects existing records; creation flows through
   `tee`):

   ```
   tee "<raw dir>/<YYYY-MM-DD>-<slug>.md" << 'EOF'
   ---
   source: <URL, meeting, or document — or the literal word `author` when the capturer is the origin>
   date: <YYYY-MM-DD the material was produced or received>
   context: <one line — why this is landing in the repository>
   captured-by: <git author email — filled mechanically from `git config user.email`, never typed prose>
   ---

   <the verbatim material, untouched, below the header>
   EOF
   ```

   Raw files are never edited after creation (a PreToolUse hook denies it); distillations link
   to them, and disagreements resolve in favor of raw.

   **Identity discipline.** `source:` carries *external* provenance only — first-person
   dictation is `source: author`. Fill `captured-by:` mechanically from
   `git config user.email`; it must equal the capture commit's author email. Third-party
   people who are *spoken about* are pseudonymized at capture ([P2], [P3], ...): display
   names live only in a committed `contributors.json` at the repo root.

   **The raw body is verbatim by design — do not scrub names out of it.** If the material
   names its own speaker ("I, <name>, verified ..."), file it exactly as given. The name is
   part of the record you were asked to preserve, identity still resolves mechanically
   through `captured-by` plus the landing commit, and raw files are write-once so an
   "improved" body could never be corrected later. Verbatim fidelity wins here; the
   name-freedom rule binds the *derived* surfaces instead (steps 2-5).

   **Derived surfaces carry no display names.** Everything you author rather than transcribe
   — distilled notes and studies, the index line, the journal fragment, the dashboard, and
   your commit subject and body — refers to people by canonical email or not at all. You
   write those surfaces, so nothing forces a name into them: if a person must be referenced,
   use the canonical email; if the point survives without the reference, drop it. Do not
   look a display name up out of `contributors.json` in order to put it somewhere else —
   that map is the one place it belongs, and the author field already carries identity.
1. **Classify** what you're capturing — one right home:
   - A study or an external finding → a page in the directory holding the index file
     (default `research/<topic>.md`).
   - A testable idea about a way of working — a hunch an experiment could confirm or refute →
     if the experiments profile is active in this repository (`.claude/hyp.json`
     `profile` is `experiments` or `modeling`), register it through the `hypothesis` skill as
     a spec instead of filing a note; otherwise file
     it under the notes directory with the word `testable` on its first line, so it can
     graduate to a spec later.
   - Self-reference material (conventions, lookups, notes-to-future-Claude) →
     `<notes dir>/<slug>.md`.
   - A decision about how the repository works → if the repo keeps a human-edited directives
     file, never edit it: propose the change to the user and journal the proposal.
   - An observation about how work happened → usually a journal fragment alone (step 4), not a
     new page.
2. **Minimize** — write the smallest document that fully carries the knowledge. Strip anything
   speculative or generic. Rule of thumb: if the knowledge is a single sentence or directly
   extends an existing doc's scope, add it there surgically; a separate file is warranted only
   when the content is operational detail that would bloat its would-be host.
3. **Link** — every new file must be referenced from at least one existing document a reader
   would plausibly start from, and added to the index file as one line under the right section:
   `- [<title>](<relative path>) — <one-line summary>`. A file nothing points to is lost.
4. **Journal** — record the arrival as one write-once fragment
   `<journal dir>/<id>-<slug>.md`. The id follows the draft-then-allocate contract (lab
   H-148): landing immediately on the default branch, take the next free integer re-checked
   right before the write; on any other branch write the same next free integer — it is a
   draft claim, and the lander's id gate (`scripts/id-rectify.py`) renumbers colliding
   incoming fragment ids at land. A fragment id is always an integer; never put a hash or a
   draft handle in a fragment filename (a hypothesis's `H-DRAFT-<hash8>` handle belongs in
   the fragment's text, where it cites the spec). Create it via
   `tee` heredoc like raw files (the settings deny protects existing records; creation flows
   through `tee`):

   ```
   tee "<journal dir>/<id>-<slug>.md" << 'EOF'
   ---
   id: <next integer>
   date: <YYYY-MM-DD>
   type: capture
   ---

   <one short paragraph: what was captured, where it landed, and why>
   EOF
   ```

   Fragments are write-once — create the file once and never modify it (the hook denies
   edits); no author names in the text (git blame is attribution). Refresh the compiled view
   with `python3 scripts/compile-journal.py` when useful.
5. **Commit** — a capture is not complete until its commit lands. Land ONE commit per capture
   containing exactly this capture's files (the raw file and/or note, its index line, its
   journal fragment, and the refreshed `DASHBOARD.md` if the capture changed what it
   projects), authored by the capturer. The (capture-commit sha, git author email)
   pair IS the capture's unique id — the only mechanical join a cold reader has. It resolves
   permanently via `git log --diff-filter=A --follow --format=%H -- <path>`; the sha is never
   written into the file itself (raw files are write-once, so it could never be back-filled).
   Until this commit lands, the capture is one Bash command from unrecoverable — the
   session-start warning will name any capture file left uncommitted.

   **The commit message carries no display names.** Say what landed and why; refer to people
   by canonical email or not at all. The author field already records who did this, and
   unlike an artifact a commit message cannot be rewritten once it is pushed.

   **`DASHBOARD.md` is committed, not ignored.** It is a compiled projection this plugin
   owns, and a stale or untracked copy is the failure mode. You do not need to run the
   compiler: the plugin's own SessionStart and Stop hooks refresh it for you — so `git add`
   the refreshed `DASHBOARD.md` along with the rest of the capture and let it ride the same
   commit, and a cold reader's checkout shows the status you saw. Never hand-edit it, and
   never copy the compiler into this repository — it lives in the plugin, and a second copy
   here is exactly the drift the portability guard exists to prevent. It never contains a
   value that was only true at render time: a ledger row whose line is not yet committed
   renders with no creator at all rather than a placeholder, so committing the dashboard
   cannot freeze a transient state.

## Recovery doctrine

Raw files and journal fragments are write-once, and committed history is the durability
floor. Destruction of a committed file — by any command, mistake or otherwise — is
recoverable: `git checkout <sha> -- <path>` restores it byte-identical and is the sanctioned
response to any wipe. An uncommitted capture has no floor: nobody's capture is safe until it
is committed. Where the repo has a remote, push early — protected branches and commit signing
harden history and attribution server-side (guidance only; nothing here depends on a remote).

## Rules

- One fact, one home. If knowledge would live in two places, pick one and link from the other.
- Sources stay attached: external claims keep their URLs.
- Never edit raw files, past journal fragments, or a human-only directives file.
- Names are git's job: the capturer is `captured-by` + the capture commit; spoken third
  parties are pseudonymized ([P2], [P3], ...) with display names only in `contributors.json`.
- The name rule is scoped. Raw bodies stay verbatim even when they name their own speaker.
  Everything you author — notes, index, fragments, dashboard, commit subject and body —
  uses canonical emails or no reference at all.
- If it's unclear where something belongs, say what's ambiguous and ask rather than guessing.
