---
name: decisions
description: Walk the decision queue — the open decision cards addressed to the caller's own role — through the ask-user prompt and record each answer as one committed ledger row. Use this skill whenever the user runs /hyp:decisions, asks what they need to decide, asks for their open decisions or the decision queue, wants to answer the cards, or wants to see all decisions or a subset (a hypothesis set that just ran, an age bucket, a class, ids, a state, the records to glance at).
---

# /hyp:decisions — the decision queue

The queue is a stateless projection of the committed ledger: `decisions.py queue` recomputes it on every run
from the ledger, `.claude/hyp.json` `decision_roles`, CODEOWNERS, `contributors.json`, `.mailmap` and the
caller's git identity. Nothing here decides anything — the filter, the rank, the batch size, a card's
readiness, every refusal, the row shape, the routing of free text and the commit all live in the script.
Your whole contribution: run, parse, print verbatim, call the tool with the objects you were given, run the
answer seam with the user's token, print what it said.

Every script call is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/decisions.py" <command> ...`. The user's typed
flags pass through untouched: `--all`, `--set <token> ...`, `--since [<ref>]`, `--age <bucket>`,
`--class <c> ...`, `--ids <id> ...`, `--state <s> ...`, `--records`, `--text`. The script validates them and
refuses an unknown flag (`QUEUE-INVALID`); never guess a flag.

0. **Mode.** If the ask-user tool is not among this session's tools (a `-p` run, a subagent, a workflow
   child) or `HYP_DECISIONS=surface` is set: run `decisions.py queue --text <flags>`, print its output
   verbatim, stop. Nothing is asked, nothing is appended.
1. **List.** Run `decisions.py queue --json <flags>` and parse the JSON envelope.
2. **Precondition.** If `can_record` is false (`IDENTITY-UNRESOLVED`, `NO-HEAD`, or `RESOLVE-BUSY <pid>`),
   print `block_reason` verbatim and stop. Never `--no-commit`, never `--reopen`, never `--legacy`, never an
   edit to the ledger, never a route around a guard.
3. **Header.** Print `announce.system_message` (the one-line count), then every `awaiting_brief` entry's
   `marker` and `retrofit_command`, every `not_ready` entry's `lines`, every `waiting` entry and every
   `contested` entry with its `settle_command`: the reader learns at once what is asked, what is the lab's
   debt, and what is parked.
4. **Ask a batch.** Take the first entry of `batches` (a list of item ids). For each id, print the item's
   `card_lines` verbatim, then its `notice` lines. Print `routes_help` once. Call the ask-user tool ONCE with
   `questions = [item.ask for each id in the batch]` — the objects exactly as emitted, no field added,
   removed, reworded or reordered. If `batches` is empty, go to step 8.
5. **Record each answer immediately**, in batch order, before the next answer is touched:
   a chosen option label → `decisions.py queue-answer <id> --label "<label>"` (repeat `--label` on a
   multi-select card); free text typed into Other → `decisions.py queue-answer <id> --text "<text>"`;
   a general response → `decisions.py queue-answer <id> --response "<text>"` (nothing is recorded). Print
   the seam's lines. One row and one single-line commit per answer, so a compaction or crash mid-pass loses
   at most the answer in flight.
6. **Refusals are printed, not handled.** `RESOLVE-INVALID ...`, `RESOLVE-REFUSED ... DEFAULT-SUSPENDED`,
   `RESOLVE-REFUSED ... NOT-ADDRESSEE`, `QUEUE-INVALID ...`, `COMMIT-FAILED ...`, `RESOLVE-BUSY ...`: print
   the line, mark the card "not recorded", continue. Never retry with other flags.
7. **Repeat.** Re-run `queue --json <flags>` (status re-derives; a card closed in step 5 or by another user
   is gone; a card parked with the later-when route is now under `waiting`) and go to step 4 until
   `batches` is empty or every card of the last batch was left with the later route.
8. **Summarize.** Print the post-pass `announce.system_message`, the committed shas the seam printed, the
   ids deferred, the ids refused with their lines, and `counts.behind_commits` when non-zero. Then recompile
   once: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-dashboard.py" --quiet`.

The away message of the prompt is STOP: record nothing. A card addressed to another role is visible under
`--all` and cannot be answered — the seam refuses before anything is appended. Records the lab decided for
the user (`--records`) are review items: their `veto_command` is the one-word veto; nothing else is asked.
