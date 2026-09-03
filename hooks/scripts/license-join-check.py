#!/usr/bin/env python3
"""license-join-check.py -- H-250 rule-write license-join advisory (the deliverable).

A deterministic, stdlib-only, PURE-READER advisory over a single rule-carrier write
event: a write that adds rules must cite a license -- a `licensed_by` pointer that
resolves to (a) a ledger turn row the KEPT H-083/H-105 detector classed DIRECTIVE, or
(b) a committed incident artifact (fragment, raw directive, run record). A pointer to
a hedged/question-classed turn NEVER licenses, regardless of prose. Not a classifier:
turn classes are read from the corpus's pinned `detector_output` fields (pinned at
fixture build by running the kept detector); this tool implements only the JOIN.

Usage:
    license-join-check.py --corpus CORPUS_ROOT --event EVENT.json
    license-join-check.py --corpus CORPUS_ROOT   (event JSON on stdin)

Input event: PreToolUse-shaped JSON ({tool_name, tool_input:{file_path, content|
new_string|edits}, cwd, ...}). Only Write/Edit/MultiEdit events are examined.

Corpus layout:
    CORPUS_ROOT/ledger-extract.jsonl   turn rows: {turn_id, text, detector_output}
    CORPUS_ROOT/artifacts/<repo-relative-path>  committed-artifact stubs; existence
                                                under artifacts/ == committed

Frozen rule-carrier path set (spec Method; component/basename tests on the
cwd-normalized path):
    - basename CLAUDE.md                        (CLAUDE.md layers)
    - basename MEMORY.md, or a `memory` dir     (memory files)
    - a `hooks` dir component, or hooks.json    (hooks)
    - basename settings.json/settings.local.json (settings files)
    - a `workflows` dir component               (workflow LAWS surfaces)
    - basename *registry*.jsonl                 (rules registry; PER-ROW join)

Frozen license grammar: `licensed_by` (also licensed-by) followed by `:` or `=`,
optionally quoted, capturing one token of [A-Za-z0-9._/:@-]+. `turn:<id>` resolves
against the ledger extract; anything else is a repo-relative artifact path.

Decision per write (per row for .jsonl registry carriers), frozen precedence:
    no pointer                              -> missing-license
    any pointer resolves to DIRECTIVE turn
      or committed artifact                 -> silent (licensed)
    else any pointer is a question turn     -> hedged-license
    else                                    -> unresolvable-license

Output grammar (spec Method, frozen): one line per finding
    RULE-LICENSE\t<carrier-path>\t<type>\t<pointer-or-dash>
plus `# ` commentary lines only for skip/malformed cases. ADVISORY CONTRACT: exit 0
always, never blocks a write, writes nothing anywhere (pure reader).
"""
import json
import os
import re
import sys

FINDING_PREFIX = "RULE-LICENSE"

_POINTER_RE = re.compile(
    r"licensed[_-]by[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9._/:@-]+)")

_SETTINGS_BASENAMES = ("settings.json", "settings.local.json")


def normalize_path(file_path, cwd):
    """cwd-relative repo path, deterministic: strip cwd prefix when the absolute
    path sits under it, else strip leading slashes."""
    p = (file_path or "").strip()
    c = (cwd or "").rstrip("/")
    if p.startswith("/"):
        if c and (p == c or p.startswith(c + "/")):
            p = p[len(c):].lstrip("/")
        else:
            p = p.lstrip("/")
    return p


def carrier_surface(rel_path):
    """Return the rule-carrier surface name for `rel_path`, or None."""
    if not rel_path:
        return None
    parts = rel_path.split("/")
    base = parts[-1]
    dirs = parts[:-1]
    if base == "CLAUDE.md":
        return "claude-md"
    if base == "MEMORY.md" or "memory" in dirs:
        return "memory"
    if "hooks" in dirs or base == "hooks.json":
        return "hooks"
    if base in _SETTINGS_BASENAMES:
        return "settings"
    if "workflows" in dirs:
        return "workflow"
    if base.endswith(".jsonl") and "registry" in base:
        return "registry"
    return None


def load_turns(corpus):
    turns = {}
    path = os.path.join(corpus, "ledger-extract.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                tid = row.get("turn_id")
                if isinstance(tid, str):
                    turns[tid] = row
    except OSError:
        pass
    return turns


def resolve_pointer(pointer, corpus, turns):
    """Return one of: directive | artifact | question | silent-turn |
    missing-turn | missing-artifact."""
    if pointer.startswith("turn:"):
        row = turns.get(pointer[len("turn:"):])
        if row is None:
            return "missing-turn"
        out = row.get("detector_output") or ""
        if out.startswith("DIRECTIVE:"):
            return "directive"
        if out.startswith("INTENT:"):
            return "question"
        return "silent-turn"
    rel = pointer.lstrip("/")
    if os.path.isfile(os.path.join(corpus, "artifacts", rel)):
        return "artifact"
    return "missing-artifact"


def judge(pointers, corpus, turns):
    """Apply the frozen precedence to an ordered pointer list. Returns
    (finding_type or None, pointer-or-dash)."""
    if not pointers:
        return "missing-license", "-"
    resolutions = [(p, resolve_pointer(p, corpus, turns)) for p in pointers]
    if any(r in ("directive", "artifact") for _p, r in resolutions):
        return None, "-"
    for p, r in resolutions:
        if r == "question":
            return "hedged-license", p
    return "unresolvable-license", pointers[0]


def extract_pointers(text):
    seen, out = set(), []
    for m in _POINTER_RE.finditer(text or ""):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def event_content(tool_name, tool_input):
    if tool_name == "Write":
        return tool_input.get("content")
    if tool_name == "Edit":
        return tool_input.get("new_string")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return "\n".join(str(ed.get("new_string", "")) for ed in edits
                             if isinstance(ed, dict))
    return None


def check(event, corpus):
    """Return the list of output lines for one event (possibly empty)."""
    if not isinstance(event, dict):
        return ["# license-join-check: skipped (event is not a JSON object)"]
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if tool_name not in ("Write", "Edit", "MultiEdit") \
            or not isinstance(tool_input, dict):
        return []
    rel = normalize_path(tool_input.get("file_path"), event.get("cwd"))
    surface = carrier_surface(rel)
    if surface is None:
        return []
    content = event_content(tool_name, tool_input)
    if not isinstance(content, str):
        return ["# license-join-check: skipped (no textual content in %s event)"
                % tool_name]
    turns = load_turns(corpus)
    lines = []
    if surface == "registry":
        for raw in content.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                row = None
            if isinstance(row, dict):
                lb = row.get("licensed_by")
                pointers = [lb] if isinstance(lb, str) and lb else []
            else:
                pointers = []
            ftype, pointer = judge(pointers, corpus, turns)
            if ftype:
                lines.append("%s\t%s\t%s\t%s"
                             % (FINDING_PREFIX, rel, ftype, pointer))
    else:
        pointers = extract_pointers(content)
        ftype, pointer = judge(pointers, corpus, turns)
        if ftype:
            lines.append("%s\t%s\t%s\t%s" % (FINDING_PREFIX, rel, ftype, pointer))
    return lines


def main(argv):
    corpus, event_path = None, None
    i = 0
    while i < len(argv):
        if argv[i] == "--corpus" and i + 1 < len(argv):
            corpus = argv[i + 1]
            i += 2
        elif argv[i] == "--event" and i + 1 < len(argv):
            event_path = argv[i + 1]
            i += 2
        else:
            i += 1
    if not corpus:
        print("# license-join-check: skipped (no --corpus given)")
        return 0
    raw = None
    if event_path:
        try:
            with open(event_path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as err:
            print("# license-join-check: skipped (event unreadable: %s)" % err)
            return 0
    else:
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        print("# license-join-check: skipped (event is not valid JSON)")
        return 0
    for line in check(event, corpus):
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        # Advisory contract: never a traceback to the hook host, never non-zero.
        try:
            print("# license-join-check: skipped (internal error)")
        except Exception:
            pass
        sys.exit(0)
