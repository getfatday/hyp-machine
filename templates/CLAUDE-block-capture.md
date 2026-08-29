<!-- BEGIN hyp rules (installed by /hyp:init; do not hand-edit — drift-checked against the plugin's canonical template) -->

## Knowledge intake

This repository captures knowledge through the hyp plugin's `intake` skill (adapted
from the ingest flow in Karpathy's LLM Wiki pattern): every piece of knowledge lives in exactly
one right place, is as small as it can be, is linked so it can be found, and is journaled so its
arrival is traceable.

| Path | Purpose |
|---|---|
| `{{RAW_DIR}}/` | Immutable verbatim sources. Write once, never edit; distill into notes |
| `{{NOTES_DIR}}/` | Distilled self-reference notes; studies live next to the index |
| `{{INDEX_FILE}}` | Catalog of the wiki layer — one line per page, updated on every capture |
| `{{JOURNAL_DIR}}/` | Append-only journal: one write-once fragment file per entry; compiled view via `scripts/compile-journal.py` |

### Capture rule

Any new knowledge or agent self-reference file enters through the `intake` skill's process:

0. **Raw first** — verbatim human or external input lands untouched in
   `{{RAW_DIR}}/<date>-<slug>.md` with a provenance header before distilling. Raw files are
   never edited after creation.
1. **Right place** — put it in the directory that owns that kind of content.
2. **Minimal** — smallest useful content; no speculative sections.
3. **Linked** — reference it from a relevant existing doc and add one line to `{{INDEX_FILE}}`.
4. **Journaled** — add one write-once fragment under `{{JOURNAL_DIR}}/` (frontmatter `id:`
   monotonic, `date:`; no author names — git blame is attribution) noting what was added and why.
5. **Committed** — one commit per capture, authored by the capturer, containing exactly that
   capture's files plus the refreshed `DASHBOARD.md` when the capture changed what it
   projects. The (commit sha, author email) pair is the capture's id; until the commit lands
   the capture is one Bash command from unrecoverable.

New write-once files (raw files, journal fragments) are created via a Bash `tee` heredoc: the
settings deny protects existing records; creation flows through `tee`.

### Identity is git's job, and the name rule is scoped

Raw bodies stay **verbatim** even when the material names its own speaker — the name is part
of the record, and identity resolves through the header's `captured-by:` plus the landing
commit. Everything you *author* rather than transcribe — distilled notes, the index line, the
journal fragment, `DASHBOARD.md`, and your commit subject and body — carries no display names:
refer to people by canonical email or not at all. Display names live only in a committed
`contributors.json` at the repo root; do not copy one out of it into anything else.

<!-- END hyp rules -->
