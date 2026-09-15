#!/usr/bin/env python3
# agent tier: build / sonnet / high
"""
routing_lib.py -- the one library imported by routing-guard.py, scripts/routing.py, and
hooks/scripts/routing-ledger.py.

Ported byte-for-byte (find_matching_paren, extract_agent_calls core loop) from
experiments/runs/DESIGN-model-routing/evidence/workflow-model-census.py so the guard
sees exactly the call sites the census counted. Extended here with: line-number
tracking, a cannot-parse classification distinct from payload/IO failure, non-literal
option detection (spread/variable/template), a meta.phases[] model reader, and the
agentType-vs-label-head check (invariant 5).

This is a bracket-matching regex scanner, not a JavaScript parser (DESIGN.md 4.3);
that limitation is deliberate and disclosed -- it is why "cannot-parse" and "alias"
are their own finding classes rather than silently missed defects.

Below `scan_script` this module also carries the `agent-route/v1` schema (source lab
H-DRAFT-38f86fad-routing-ledger-row, VERDICT.json evidence-sufficient promote): the pure
helpers `hooks/scripts/routing-ledger.py`'s Stop/SubagentStop writer uses to read a
workflow run directory in the shape Claude Code writes it and build one covariate row per
finished agent. That schema does not resolve or enforce a routing table -- it only records
what a run's own metadata already carries, and separately carries a frozen label-head ->
class table (`CLASS_BY_HEAD`) kept deliberately distinct from `roles` above: the ledger's
class is an observability label with its own fixture-pinned history, never a live read of
the guard's `rules/routing-default.json`, so a later edit to the guard's table cannot
silently redefine what an already-written row's `class` meant.
"""
import bisect
import hashlib
import json
import os
import re
from datetime import datetime, timezone

AGENT_CALL_RE = re.compile(r"agent\(", re.MULTILINE)
BARE_AGENT_RE = re.compile(r"\bagent\b")

ROUTING_OPTION_KEYS = ("label", "phase", "agentType", "model", "effort")


class ParseError(Exception):
    def __init__(self, msg, offset):
        super().__init__(msg)
        self.offset = offset


def canonical_json_bytes(obj):
    """Deterministic JSON bytes: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    """sha256 hex digest of a file's bytes, or None on any read error (missing file,
    permissions, a directory) -- fail-open. Widened from a raising form for the ledger
    writer (H-DRAFT-38f86fad): every caller in this module already has its own fallback
    for "no usable hash", so a raised exception was strictly worse than returning None,
    and this is the only definition (no other module in this plugin calls it), so the
    widening changes no other caller's behaviour."""
    try:
        with open(path, "rb") as f:
            return sha256_bytes(f.read())
    except OSError:
        return None


def default_sha(default_table_obj):
    return sha256_bytes(canonical_json_bytes(default_table_obj))


def merge_table(default_table_obj, override_table_obj):
    """Effective table = default merged with override ('roles' and 'classes' shallow-
    merged, override wins per key); table_sha = sha256(default_bytes || override_bytes)
    per the spec's Variable-under-test paragraph."""
    default_bytes = canonical_json_bytes(default_table_obj)
    override_bytes = canonical_json_bytes(override_table_obj or {})
    table_sha = sha256_bytes(default_bytes + override_bytes)

    effective = {
        "classes": dict(default_table_obj.get("classes", {})),
        "roles": dict(default_table_obj.get("roles", {})),
        "frontier": list(default_table_obj.get("frontier", [])),
        "frontier_on_execute_requires": list(default_table_obj.get("frontier_on_execute_requires", [])),
        "overrides": {},
    }
    ov = override_table_obj or {}
    effective["classes"].update(ov.get("classes", {}))
    effective["roles"].update(ov.get("roles", {}))
    effective["overrides"] = ov.get("overrides", {})
    effective["table_sha"] = table_sha
    effective["default_sha"] = sha256_bytes(default_bytes)
    return effective


def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def _newline_offsets(text):
    """Sorted offsets of every '\\n' in `text`, computed once. Round-2 finding
    4's actual bottleneck: `line_of`'s `text.count("\\n", 0, offset)` rescans
    from byte 0 every call; called once per agent() call (extract_agent_calls)
    and once per bare-`agent` hit (find_bare_agent_non_call), that is O(n)
    per lookup and O(n x calls) overall -- profiling the 2 MB A4 synthetic
    (15,197 calls) attributed 10.1 of 11.7 s total to this alone. `_line_at`
    below turns repeated lookups into one bisect (O(log n)) against this
    precomputed list; `line_of` itself is kept for the rare single-shot
    caller (a whole-script cannot-parse finding)."""
    offsets = []
    i = text.find("\n")
    while i != -1:
        offsets.append(i)
        i = text.find("\n", i + 1)
    return offsets


def _line_at(offset, newline_offsets):
    return bisect.bisect_left(newline_offsets, offset) + 1


def _skip_quoted(text, i):
    """text[i] is an opening ' or " ; return the index just past the matching
    close (or len(text) if unterminated -- the caller's own depth check turns
    that into a cannot-parse, never a silent misparse)."""
    quote = text[i]
    n = len(text)
    i += 1
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def _skip_template_literal(text, i):
    """text[i] is an opening backtick; return the index just past the matching
    close, recursing into every `${...}` interpolation -- which may itself
    contain a quoted string or ANOTHER template literal (real, valid JS: a
    ternary branch inside an interpolation, for instance). Fixture round 1
    finding 2: the naive single-character in_str tracking treated a nested
    backtick as closing the OUTER template, desynchronizing every paren count
    that followed and raising a spurious cannot-parse on a real corpus script.
    A genuinely unterminated backtick (no matching close before EOF, e.g. the
    seeded M-scanner-crash mutant) still runs to len(text) here, so the
    caller's depth check still raises ParseError -- that class is unchanged."""
    n = len(text)
    i += 1
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and i + 1 < n and text[i + 1] == "{":
            i += 2
            depth = 1
            while i < n and depth > 0:
                c2 = text[i]
                if c2 == "\\":
                    i += 2
                    continue
                if c2 in ("'", '"'):
                    i = _skip_quoted(text, i)
                    continue
                if c2 == "`":
                    i = _skip_template_literal(text, i)
                    continue
                if c2 in "({[":
                    depth += 1
                elif c2 in ")}]":
                    depth -= 1
                i += 1
            continue
        i += 1
    return n


def find_matching_paren(text, open_idx):
    """Given index of '(' just after 'agent', return index of the matching ')'.
    Originally ported byte-for-byte from workflow-model-census.py:find_matching_paren;
    the string/template handling was rewritten in fixture round 1 (finding 2) to
    use _skip_quoted/_skip_template_literal so a nested template literal inside a
    `${...}` interpolation can no longer desynchronize the paren count -- every
    other byte of the original algorithm (paren depth, EOF-unterminated ->
    ParseError) is unchanged."""
    depth = 1
    i = open_idx + 1
    n = len(text)
    while i < n and depth > 0:
        c = text[i]
        if c in ("'", '"'):
            i = _skip_quoted(text, i)
            continue
        if c == "`":
            i = _skip_template_literal(text, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return i - 1, depth


def extract_agent_calls(text):
    """Return list of {start, open_idx, close_idx, arg_text, line} for every
    agent(...) call in text. Raises ParseError if a call's parens never balance
    before EOF -- the scanner's own cannot-parse class, distinct from a payload/IO
    error (DESIGN.md 4.3)."""
    calls = []
    newline_offsets = _newline_offsets(text)  # finding 4: one bisect per call, not one O(n) count()
    for m in AGENT_CALL_RE.finditer(text):
        open_idx = m.end() - 1
        close_idx, depth = find_matching_paren(text, open_idx)
        if depth > 0:
            raise ParseError("unterminated agent( call (unbalanced parens/strings)", m.start())
        if close_idx > open_idx:
            calls.append({
                "start": m.start(),
                "open_idx": open_idx,
                "close_idx": close_idx,
                "arg_text": text[open_idx + 1:close_idx],
                "line": _line_at(m.start(), newline_offsets),
            })
    return calls


def _lexical_spans(text):
    """Single left-to-right pass classifying every `// ... \n` line comment and
    every quoted-string/template-literal span (nested-`${}`-aware via
    _skip_quoted/_skip_template_literal). Returns (comment_spans, string_spans).

    Fixture round 1 finding 8's root cause: computing comments and strings as
    two INDEPENDENT scans (as this module originally did) lets a `//` inside a
    string be missed by the comment scan, AND -- the actual defect found here --
    lets an apostrophe inside a `//` comment (real corpus prose like "...three
    platforms' verified findings...") be treated as OPENING a bogus quoted
    string by the string scan, which then swallows everything up to the next
    unrelated quote character and desynchronizes every span after it. One pass,
    one shared cursor, both classes recognized together, closes that gap."""
    comment_spans = []
    string_spans = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ("'", '"'):
            j = _skip_quoted(text, i)
            string_spans.append((i, j))
            i = j
            continue
        if c == "`":
            j = _skip_template_literal(text, i)
            string_spans.append((i, j))
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            start = i
            end = text.find("\n", i)
            end = end if end != -1 else n
            comment_spans.append((start, end))
            i = end
            continue
        i += 1
    return comment_spans, string_spans


def _line_comment_spans(text):
    """Offsets of every `// ... \n` line-comment body -- see _lexical_spans."""
    return _lexical_spans(text)[0]


def _string_template_spans(text):
    """Every closed quoted-string or template-literal span -- see
    _lexical_spans. Fixture round 1 finding 8: a bare `agent` mention inside a
    PROMPT STRING (English prose, e.g. a template literal that reads "...a
    build agent...") is not code and must never be treated as an alias -- 257
    of 733 corpus scripts were false-denied before this fix."""
    return _lexical_spans(text)[1]


def _in_spans(offset, spans, starts=None):
    """True iff `offset` falls in one of the sorted, non-overlapping `spans`.
    Round-2 finding 4: the original linear scan (with its early `break` once
    a span's start exceeds `offset`) still costs O(len(spans)) for any offset
    near the END of the text, and find_bare_agent_non_call calls this once
    per `agent` match -- on the 1 MB synthetic (~7,599 calls, ~30,000+
    comment/string spans) that is close to O(hits x spans), which is what
    pushed the scan past the 10 s row timeout under load. `starts` (the
    spans' own start offsets, already sorted since `spans` is sorted by
    start) turns the lookup into one bisect: O(log spans) per call."""
    if not spans:
        return False
    if starts is None:
        starts = [a for a, _ in spans]
    idx = bisect.bisect_right(starts, offset) - 1
    if idx < 0:
        return False
    a, b = spans[idx]
    return a <= offset < b


def find_bare_agent_non_call(text):
    """Find every whole-word occurrence of the identifier `agent` that is not
    immediately followed by '(' (skipping whitespace), not a property access
    (`.agent`), and not inside a `//` comment or a quoted/template-literal span
    (prose in a prompt string) -- DESIGN.md 4.3's alias-closing invariant.
    Returns a list of (offset, line)."""
    hits = []
    comment_spans, string_spans = _lexical_spans(text)
    skip_spans = sorted(comment_spans + string_spans)
    skip_starts = [a for a, _ in skip_spans]  # finding 4: bisect, not a linear scan per hit
    newline_offsets = _newline_offsets(text)  # finding 4: one bisect per hit, not one O(n) count()
    for m in BARE_AGENT_RE.finditer(text):
        start = m.start()
        if _in_spans(start, skip_spans, skip_starts):
            continue  # a // comment or a quoted/template string mentioning "agent" in prose, not code
        if start > 0 and text[start - 1] == ".":
            continue  # property access, e.g. someObj.agent
        j = m.end()
        while j < len(text) and text[j] in " \t":
            j += 1
        if j < len(text) and text[j] == "(":
            continue  # a direct call site, already handled by extract_agent_calls
        hits.append((start, _line_at(start, newline_offsets)))
    return hits


def _value_kind(raw):
    """Classify the raw text immediately after 'key:' up to the next top-level
    comma/brace/bracket: literal (quoted string), spread, template, or variable."""
    raw = raw.lstrip()
    if not raw:
        return "missing", None
    if raw[0] in ("'", '"'):
        quote = raw[0]
        end = 1
        while end < len(raw) and raw[end] != quote:
            if raw[end] == "\\":
                end += 2
                continue
            end += 1
        return "literal", raw[1:end]
    if raw[0] == "`":
        return "template", None
    if raw.startswith("..."):
        return "spread", None
    return "variable", None


def _slice_value(call_text, key):
    """Return the raw text of `key: <value>` up to the next top-level , or } at
    depth 0 relative to the option value's own start (handles nested [] {} in the
    value, e.g. a template literal or an array)."""
    m = re.search(r"\b" + re.escape(key) + r"\s*:\s*", call_text)
    if not m:
        return None
    i = m.end()
    depth = 0
    j = i
    n = len(call_text)
    while j < n:
        c = call_text[j]
        if c in ("'", '"'):
            j = _skip_quoted(call_text, j)
            continue
        if c == "`":
            j = _skip_template_literal(call_text, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        j += 1
    return call_text[i:j]


def find_options_object_offset(call_text):
    """Real calls in this corpus are not always agent({...}); many are
    agent(promptTemplateLiteral, {...}) -- a leading prompt argument followed
    by the options object. Returns the character offset of the options
    object's own '{' within call_text (top-level-comma-aware, string/template-
    aware), or 0 when call_text itself is the options object (the common case,
    and every call in this lane's own compliant.workflow.js/mutants). Never
    raises; a call whose last top-level argument is not an object literal
    (`{`-leading) is treated the same way as before (offset 0) rather than
    silently misparsed -- the resulting finding is still 'no-model' or
    'unknown-role' on the whole call_text, never a wrong bracket-insertion
    point (the corruption a naive text.find('{') produced on this corpus's
    ${...} template interpolations)."""
    depth = 0
    split_points = []
    i = 0
    n = len(call_text)
    while i < n:
        c = call_text[i]
        if c in ("'", '"'):
            i = _skip_quoted(call_text, i)
            continue
        if c == "`":
            i = _skip_template_literal(call_text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            split_points.append(i)
        i += 1
    start = (split_points[-1] + 1) if split_points else 0
    j = start
    while j < n and call_text[j] in " \t\n\r":
        j += 1
    if j < n and call_text[j] == "{":
        return j
    return 0


def parse_call_options(call_text):
    """Extract label/phase/agentType/model/effort as (kind, value) pairs. kind is
    one of literal|spread|template|variable|missing. Only the options-object
    argument (find_options_object_offset) is searched, never a leading prompt
    string that may itself contain lookalike text."""
    offset = find_options_object_offset(call_text)
    opts_text = call_text[offset:]
    out = {}
    for key in ROUTING_OPTION_KEYS:
        raw = _slice_value(opts_text, key)
        if raw is None:
            out[key] = ("missing", None)
        else:
            out[key] = _value_kind(raw)
    has_spread_anywhere = bool(re.search(r"\.\.\.\s*[A-Za-z_$]", opts_text))
    return out, has_spread_anywhere


def label_head(label):
    if not label:
        return ""
    return label.split(":", 1)[0].strip()


def role_class(table, role):
    return table.get("roles", {}).get(role)


def find_phases_models(text, with_lines=False):
    """Bracket-match a `phases:` array (as in `meta.phases: [...]`) and return a
    dict phase-label -> literal model, best-effort. Not a JS parser; a phases block
    the scanner cannot bracket-match is simply not indexed (no phase-mismatch
    finding is produced for it) -- disclosed scope limit, never a fabricated match.
    With `with_lines`, also returns phase-label -> the 1-based line of that entry's
    `model` literal, so a phase-mismatch finding can name the meta.phases[] line
    beside the call line (fixture round-6 advisory: two readers of "the mutated
    line" are both satisfied)."""
    out = {}
    lines = {}
    for m in re.finditer(r"phases\s*:\s*\[", text):
        open_idx = m.end() - 1
        depth = 1
        i = open_idx + 1
        n = len(text)
        while i < n and depth > 0:
            c = text[i]
            if c in ("'", '"'):
                i = _skip_quoted(text, i)
                continue
            if c == "`":
                i = _skip_template_literal(text, i)
                continue
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            i += 1
        block = text[open_idx + 1:i - 1]
        for entry_m in re.finditer(r"\{[^{}]*\}", block):
            entry = entry_m.group(0)
            label_m = re.search(r"(?:label|name)\s*:\s*['\"]([^'\"]*)['\"]", entry)
            model_m = re.search(r"model\s*:\s*['\"]([^'\"]*)['\"]", entry)
            if label_m and model_m:
                out[label_m.group(1)] = model_m.group(1)
                lines[label_m.group(1)] = text.count("\n", 0, open_idx + 1 + entry_m.start() + model_m.start()) + 1
    return (out, lines) if with_lines else out


FINDING_FIX = {
    "no-model": "add a literal model/effort to this agent() call: python3 scripts/routing.py resolve <role>",
    "unknown-role": "use a role from the table's roles map, or add it via .claude/routing.json",
    "value-mismatch": "set model/effort to the table's row for this role, or add a reasoned override",
    "frontier-on-execute": "an execute role on a frontier model needs a repository override with reason+evidence",
    "non-literal": "routing options (label/model/effort/agentType) must be string literals, not spread/variable/template",
    "phase-mismatch": "this call's phase entry in meta.phases[] names a different model than the call itself",
    "agent-type-relabel": "agentType disagrees with the label head; they must name the same role",
    "alias": "call agent(...) directly; the identifier `agent` cannot be assigned, passed, or aliased",
    "no-effort": "add a literal effort to this agent() call: python3 scripts/routing.py resolve <role>",
    "cannot-parse": "the scanner cannot balance this agent( call's parens/strings; fix the syntax",
    "too-large": "split this script; it exceeds the guard's timeout-safe byte bound",
    "default-sha-mismatch": "the installed plugin default disagrees with .claude/routing.json's pinned default_sha",
    "subagent-model-env": "CLAUDE_CODE_SUBAGENT_MODEL is set in the invoking environment; unset it or route explicitly",
}


def scan_script(text, table):
    """Return (findings, calls) where findings is a list of dicts:
    {class, line, detail} and calls is the list of successfully parsed call sites
    (each with its extracted options), for a script's full text. Raises ParseError
    only when the scanner itself cannot balance a call -- callers must catch it and
    record a single cannot-parse finding for the whole script (the spec: "a parse
    failure is a finding, not a fail-open")."""
    findings = []
    calls = extract_agent_calls(text)  # may raise ParseError
    for hit in find_bare_agent_non_call(text):
        findings.append({"class": "alias", "line": hit[1], "detail": "identifier `agent` used in a non-call position"})
    phase_models, phase_lines = find_phases_models(text, with_lines=True)
    parsed_calls = []
    for call in calls:
        opts, has_spread = parse_call_options(call["arg_text"])
        label_kind, label_val = opts["label"]
        model_kind, model_val = opts["model"]
        effort_kind, effort_val = opts["effort"]
        agent_type_kind, agent_type_val = opts["agentType"]
        phase_kind, phase_val = opts["phase"]
        head = label_head(label_val) if label_kind == "literal" else ""
        role_cls = role_class(table, head) if head else None
        parsed_calls.append({
            "line": call["line"], "label": label_val if label_kind == "literal" else None,
            "model": model_val if model_kind == "literal" else None,
            "effort": effort_val if effort_kind == "literal" else None,
            "agentType": agent_type_val if agent_type_kind == "literal" else None,
            "phase": phase_val if phase_kind == "literal" else None,
            "role": head, "class": role_cls,
        })
        # Every applicable class is reported for a call -- these are NOT mutually
        # exclusive. A call can be both non-literal (e.g. a templated label) and
        # missing its model in the same object; a grader (this lane's own A2
        # recall check, or the ledger's finding-class counts, section 4.4) that
        # only sees the first-found class under-counts real defects, so nothing
        # here short-circuits on a different class already having fired.
        non_literal_keys = [k for k, (kind, _) in opts.items() if kind in ("spread", "template", "variable")]
        if non_literal_keys or has_spread:
            findings.append({"class": "non-literal", "line": call["line"],
                              "detail": "non-literal option(s): " + ",".join(sorted(set(non_literal_keys))) if non_literal_keys else "spread in options"})
        # Split into two distinct classes (fixture round 1 finding 6/advisory):
        # "no-model" fires only when the model itself is missing -- matching the
        # census's has_model semantic exactly, 2,822 of 2,928 calls -- and
        # "no-effort" fires independently when only effort is missing, so a
        # grader keying on "no-model" is never inflated by an effort-only gap.
        if model_kind == "missing":
            findings.append({"class": "no-model", "line": call["line"], "detail": "missing literal model"})
        if effort_kind == "missing":
            findings.append({"class": "no-effort", "line": call["line"], "detail": "missing literal effort"})
        if head and role_cls is None:
            findings.append({"class": "unknown-role", "line": call["line"],
                              "detail": "label head %r not in roles map" % (head,)})
        elif not head:
            findings.append({"class": "unknown-role", "line": call["line"], "detail": "no literal label"})
        if head and role_cls is not None and model_kind == "literal" and effort_kind == "literal":
            row = table["classes"].get(role_cls, {})
            if model_val != row.get("model") or effort_val != row.get("effort"):
                findings.append({"class": "value-mismatch", "line": call["line"],
                                  "detail": "model/effort %r/%r disagrees with table row %r/%r" % (
                                      model_val, effort_val, row.get("model"), row.get("effort"))})
            if role_cls == "execute" and model_val in table.get("frontier", []):
                findings.append({"class": "frontier-on-execute", "line": call["line"],
                                  "detail": "execute role on frontier model %r without an override" % (model_val,)})
        if head and agent_type_kind == "literal" and agent_type_val != ("hyp:" + head):
            findings.append({"class": "agent-type-relabel", "line": call["line"],
                              "detail": "agentType %r disagrees with label head %r" % (agent_type_val, head)})
        if phase_kind == "literal" and phase_val in phase_models and model_kind == "literal":
            if phase_models[phase_val] != model_val:
                findings.append({"class": "phase-mismatch", "line": call["line"],
                                  "detail": "meta.phases[] entry at line %s names model %r, call at line %d names %r" % (
                                      phase_lines.get(phase_val, "?"), phase_models[phase_val], call["line"], model_val)})
    return findings, parsed_calls



# ---------------------------------------------------------------------------------------
# agent-route/v1 (source lab H-DRAFT-38f86fad-routing-ledger-row): pure helpers for
# hooks/scripts/routing-ledger.py's Stop/SubagentStop writer. Every public function here
# fails closed on bad input by returning None / a typed value rather than raising, because
# the caller is a hook and a crashing hook is worse than a missed row (hyp_config's own
# fail-open contract).
#
# The run-directory shape read is the one Claude Code persists under
# `<project dir>/<session id>/subagents/workflows/wf_*/`:
#
#   journal.jsonl            {"type": "started", "key", "agentId", ["label", "phase"]}
#                            {"type": "result",  "key", "agentId", "result": <object | string>}
#                            {"type": "failed",  "key", "agentId"}
#                            {"type": "launched"}
#   agent-<id>.meta.json     {"agentType", "spawnDepth", ["model", "description", ...]}
#   agent-<id>.jsonl         the agent transcript; every assistant line carries `timestamp`
#                            and `message.model` + `message.usage`
#
# Nothing else is read: no `usage.json`, no per-agent key inside the meta file (the agent
# id is the file name), no workflow `args` record (the journal has none -- the
# experiment-level pointer is the consumer-side opt-in file
# `.claude/routing-outcomes/<wf>.json`, see the writer).
# ---------------------------------------------------------------------------------------

SCHEMA = "agent-route/v1"

# Transcripts over this many bytes are head+tail sampled, never fully read, and the row is
# flagged `transcript_truncated`.
MAX_READ_BYTES = 8 * 1024 * 1024
HEAD_TAIL_BYTES = 64 * 1024

_TIER_PREFIXES = (
    ("claude-fable", "fable"),
    ("claude-opus", "opus"),
    ("claude-sonnet", "sonnet"),
    ("claude-haiku", "haiku"),
)
TIERS = ("fable", "opus", "sonnet", "haiku")

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

_META_RE = re.compile(r"^agent-(.+)\.meta\.json$")

# label head -> class: a FROZEN copy of the lane's fixture-pinned
# experiments/runs/H-DRAFT-38f86fad-routing-ledger-row/fixture/class_by_head.json
# (four classes: mechanical, execute, think, adversarial). Deliberately not derived from
# `rules/routing-default.json`'s `roles` map above -- see the module docstring. A head not
# in this map classes "unknown", never an error.
CLASS_BY_HEAD = {
    "amend": "mechanical", "apply": "execute", "author": "execute", "build": "execute",
    "census": "mechanical", "close": "mechanical", "cold": "adversarial", "corpus": "mechanical",
    "critic": "adversarial", "critique": "adversarial", "design": "think", "diagnose": "think",
    "draft": "execute", "executor": "mechanical", "extract": "mechanical", "fix": "execute",
    "gate": "mechanical", "grade": "mechanical", "judge": "think", "land": "execute",
    "look": "mechanical", "migrate": "execute", "port": "execute", "publish": "execute",
    "recon": "mechanical", "reconcile": "think", "record": "mechanical", "refute": "adversarial",
    "render": "execute", "research": "think", "review": "adversarial", "rule": "think",
    "run": "mechanical", "seal": "mechanical", "settle": "mechanical", "ship": "execute",
    "state": "mechanical", "survey": "think", "sweep": "mechanical", "sync": "execute",
    "synthesize": "think", "verify": "adversarial",
}


def tier_for_model(model_id):
    """Served tier bucket for a model id string; 'unknown' for anything unrecognised, None
    for no model at all."""
    if not model_id:
        return None
    low = str(model_id).lower()
    for prefix, tier in _TIER_PREFIXES:
        if prefix in low:
            return tier
    return "unknown"


def declared_tier(declared_model):
    """The tier a `declared.model` literal names: the meta file's `model` is a tier word
    ("sonnet" / "fable" / "haiku" on this host), but a full model id is admitted too and
    bucketed through `tier_for_model` so a literal id never reads as a false mismatch."""
    if not declared_model:
        return None
    low = str(declared_model).strip().lower()
    if low.startswith("claude-"):
        return tier_for_model(low)
    return low


def role_and_class(label):
    """(role, class) from a label's head token (`role:slug` grammar); ("unknown", "unknown")
    for an empty label."""
    head = (label.split(":", 1)[0].strip().split()[0] if label else "").lower()
    head = "".join(ch for ch in head if ch.isalnum() or ch == "-")
    if not head:
        return "unknown", "unknown"
    return head, CLASS_BY_HEAD.get(head, "unknown")


def agent_id_from_meta_path(path):
    """The agent id is the meta file's name: `agent-<id>.meta.json` -> `<id>`; None otherwise."""
    m = _META_RE.match(os.path.basename(path or ""))
    return m.group(1) if m else None


def canonical_bytes(obj):
    """Deterministic JSON bytes for hashing / byte-identity comparisons (sorted keys, no
    incidental whitespace). Distinct name from `canonical_json_bytes` above (identical
    behaviour, ASCII-widened): both are kept so neither caller's import needs to change."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_capped(path, cap=MAX_READ_BYTES, head_tail=HEAD_TAIL_BYTES):
    """(text, truncated). Reads the whole file under `cap` bytes; above it, reads only the
    head and tail `head_tail` bytes each (never the middle) and marks truncated=True."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, False
    try:
        if size <= cap:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read(), False
        with open(path, "rb") as fh:
            head = fh.read(head_tail)
            fh.seek(max(0, size - head_tail))
            tail = fh.read(head_tail)
        text = head.decode("utf-8", "replace") + "\n...[truncated]...\n" + tail.decode("utf-8", "replace")
        return text, True
    except OSError:
        return None, False


def read_jsonl_capped(path, cap=MAX_READ_BYTES, head_tail=HEAD_TAIL_BYTES):
    """([row, ...], truncated) -- one JSON object per non-blank line; malformed lines are
    skipped, never guessed."""
    text, truncated = read_capped(path, cap, head_tail)
    if text is None:
        return [], False
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("...["):
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, truncated


def journal_index(journal_rows):
    """{agentId: {"started": rec|None, "result": rec|None, "failed": rec|None}} over the real
    journal record types. A `result` record is what makes an agent FINISHED for this writer
    (one row per agent with a `result` record); `failed` and started-only agents are counted
    by the caller's summary and never given a row (never imputed -- a later sweep records
    them if a result ever lands; the (wf, agent) key keeps that safe). The first record of
    each kind is the referent; a later one never overwrites it."""
    idx = {}
    for rec in journal_rows or []:
        if not isinstance(rec, dict):
            continue
        agent = rec.get("agentId")
        kind = rec.get("type")
        if not agent or kind not in ("started", "result", "failed"):
            continue
        slot = idx.setdefault(agent, {"started": None, "result": None, "failed": None})
        if slot[kind] is None:
            slot[kind] = rec
    return idx


VERDICT_MAX_CHARS = 64


def bound_verdict(value):
    """A row's `outcome.verdict` is a verdict TOKEN (keep / discard / refine / a short label);
    an agent whose structured output puts a paragraph in `verdict` gets the first 64
    characters with a `...` marker -- the row records a verdict, never a report. None stays
    None; a non-string is stringified first. Idempotent."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    if len(text) > VERDICT_MAX_CHARS:
        return text[:VERDICT_MAX_CHARS - 3].rstrip() + "..."
    return text


def outcome_from_result(result_rec):
    """`outcome {schema_valid, verdict, refuted}` from a journal `result` record, or None when
    there is none. `result` is the agent's structured output when the call declared a schema
    (an object) or its final text otherwise (a string): `verdict` / `refuted` are read from an
    object's own keys and are None otherwise -- never guessed. `schema_valid` is false only on
    an explicit marker: the record's `schema_valid: false` / `schemaValid: false`, or the
    object's `schema_retry_exhausted: true`."""
    if not isinstance(result_rec, dict):
        return None
    result = result_rec.get("result")
    marker_false = (result_rec.get("schema_valid") is False or result_rec.get("schemaValid") is False
                    or (isinstance(result, dict) and result.get("schema_retry_exhausted") is True))
    verdict = bound_verdict(result.get("verdict")) if isinstance(result, dict) else None
    refuted = result.get("refuted") if isinstance(result, dict) else None
    if refuted is not None and not isinstance(refuted, bool):
        refuted = bool(refuted)
    return {"schema_valid": not marker_false, "verdict": verdict, "refuted": refuted}


def parse_ts(value):
    """datetime for an ISO-8601 timestamp string (the transcript's `timestamp`), or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def summarise_transcript_rows(rows):
    """Over parsed transcript lines: per-model usage buckets (a line counts iff `message`
    carries both `model` and `usage`), the dominant model (most messages; ties broken by
    model id so two readers agree), the four counters summed over EVERY model seen,
    `messages`, and the first/last timestamp of those lines (`ts` = the last one, `wall_s` =
    last - first). Pure, deterministic."""
    models = {}
    first = last = None
    first_raw = last_raw = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        msg = row.get("message")
        if not (isinstance(msg, dict) and "model" in msg and "usage" in msg):
            continue
        usage = msg.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        bucket = models.setdefault(msg.get("model"), {k: 0 for k in USAGE_KEYS + ("n_messages",)})
        for k in USAGE_KEYS:
            v = usage.get(k, 0) or 0
            bucket[k] += int(v) if isinstance(v, (int, float)) else 0
        bucket["n_messages"] += 1
        ts = parse_ts(row.get("timestamp"))
        if ts is not None:
            if first is None or ts < first:
                first, first_raw = ts, row.get("timestamp")
            if last is None or ts > last:
                last, last_raw = ts, row.get("timestamp")
    dominant = None
    if models:
        dominant = sorted(models.items(), key=lambda kv: (-kv[1]["n_messages"], str(kv[0])))[0][0]
    totals = {k: sum(b[k] for b in models.values()) for k in USAGE_KEYS}
    tokens = {
        "in": totals["input_tokens"], "out": totals["output_tokens"],
        "cache_create": totals["cache_creation_input_tokens"],
        "cache_read": totals["cache_read_input_tokens"],
        "messages": sum(b["n_messages"] for b in models.values()),
    }
    wall_s = round((last - first).total_seconds(), 3) if (first is not None and last is not None) else None
    return {"dominant_model": dominant, "models": models, "tokens": tokens,
            "ts": last_raw, "first_ts": first_raw, "wall_s": wall_s}


def scan_transcript(path, cap=MAX_READ_BYTES, head_tail=HEAD_TAIL_BYTES):
    """`summarise_transcript_rows` over `agent-<id>.jsonl` read under the disk rule; the
    returned dict also carries `truncated` and `readable`."""
    rows, truncated = read_jsonl_capped(path, cap, head_tail)
    out = summarise_transcript_rows(rows)
    out["truncated"] = bool(truncated)
    out["readable"] = os.path.isfile(path)
    return out


def cost_usd(tokens, observed_model, prices_table):
    """tokens x the pinned per-model rate from the SHIPPED `rules/model-prices.json` (a list
    of `{"prefix", "in", "out", "cache_read", "cache_create"}` rows, one per model family,
    prices per million tokens directly -- not the lane's placeholder tier+percentage
    schema, which this ships-with-drift resolution replaces: `rules/model-prices.json`
    already exists in this plugin as the guard's own price table, so the writer matches the
    OBSERVED MODEL ID against each row's `prefix` (longest match wins; deterministic on a
    tie by prefix text) rather than resolving a tier first. 0.0 for no match, no tokens, or
    a missing table -- never raises."""
    entries = (prices_table or {}).get("prices") or []
    if not isinstance(entries, list) or not observed_model:
        return 0.0
    low = str(observed_model).lower()
    matches = [e for e in entries if isinstance(e, dict) and e.get("prefix")
               and low.startswith(str(e["prefix"]).lower())]
    if not matches:
        return 0.0
    entry = sorted(matches, key=lambda e: (-len(e["prefix"]), e["prefix"]))[0]
    tokens = tokens or {}
    inp = float(tokens.get("in", 0) or 0)
    out = float(tokens.get("out", 0) or 0)
    cache_read = float(tokens.get("cache_read", 0) or 0)
    cache_create = float(tokens.get("cache_create", 0) or 0)
    total = (
        inp / 1e6 * entry.get("in", 0)
        + out / 1e6 * entry.get("out", 0)
        + cache_read / 1e6 * entry.get("cache_read", 0)
        + cache_create / 1e6 * entry.get("cache_create", 0)
    )
    return round(total, 6)


def build_row(*, ts, repo, session, wf, agent, label, role, klass, declared, observed,
              tokens, wall_s, cost, prices_sha, table_sha, default_sha, override_sha,
              lane, run, outcome_ref, outcome, override, host_load_1m, guard_ran=None,
              transcript_truncated=False):
    """The agent-route/v1 row. `mismatch` and `void` are DERIVED here, never passed in, so a
    caller cannot forget to set them: the declared literal is bucketed by `declared_tier`
    and compared with the served tier."""
    d_tier = declared_tier((declared or {}).get("model"))
    o_tier = (observed or {}).get("tier")
    mismatch = bool(d_tier and o_tier and d_tier != o_tier)
    row = {
        "kind": "agent-route",
        "v": 1,
        "ts": ts,
        "repo": repo,
        "session": session,
        "wf": wf,
        "agent": agent,
        "label": label,
        "role": role,
        "class": klass,
        "declared": declared,
        "observed": observed,
        "tokens": tokens,
        "wall_s": wall_s,
        "cost_usd": cost,
        "prices_sha": prices_sha,
        "table_sha": table_sha,
        "default_sha": default_sha,
        "override_sha": override_sha,
        "lane": lane,
        "run": run,
        "outcome_ref": outcome_ref,
        "outcome": outcome,
        "override": override,
        "host_load_1m": host_load_1m,
        "mismatch": mismatch,
        "transcript_truncated": bool(transcript_truncated),
    }
    if mismatch:
        row["void"] = "annulled"
    if guard_ran is not None:
        row["guard_ran"] = bool(guard_ran)
    return row


def row_key(row):
    return (row.get("wf"), row.get("agent"))


def load_existing_rows(ledger_path):
    """{(wf, agent): row} for every well-formed line already on disk; malformed lines are
    counted separately so a splice is visible, never silently dropped."""
    rows, malformed = {}, 0
    if not os.path.isfile(ledger_path):
        return rows, malformed
    try:
        with open(ledger_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                rows[row_key(obj)] = obj
    except OSError:
        pass
    return rows, malformed


def append_rows(ledger_path, rows):
    """Appends every row as one canonical JSON line terminated by exactly one newline (a
    writer that omits the final newline lets `merge=union` splice two rows into one on the
    next append)."""
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(canonical_bytes(row).decode("utf-8"))
            fh.write("\n")


def _identity_bytes(row):
    """Canonical bytes of a row with `host_load_1m` removed -- the live 1-minute load average
    resamples on every sweep, so two otherwise-identical observations of the same agent taken
    seconds apart (or by two worktrees of one session) would disagree on that one field and be
    typed a `conflict` rather than a `duplicate`. `host_load_1m` is a covariate, never load-
    bearing identity (ship amendment to H-DRAFT-38f86fad-routing-ledger-row's byte-identity law;
    matches the lane's own `grade_a5` determinism compare, which drops the same field first)."""
    trimmed = dict(row)
    trimmed.pop("host_load_1m", None)
    return canonical_bytes(trimmed)


def classify_new_rows(candidates, existing):
    """(to_write, duplicates, conflicts) -- `to_write` are keys not seen yet; a candidate whose
    key was already seen (on disk from a prior sweep, OR earlier in this same batch) and whose
    identity bytes (canonical bytes with `host_load_1m` removed -- see `_identity_bytes`) match
    is a `duplicate` (collapsed silently, never a finding); one whose identity bytes differ is a
    `conflict` (refused, one error-log line, never written). Sequential: a batch's own first
    occurrence of a key becomes the referent its second occurrence is checked against."""
    seen = dict(existing)
    to_write, duplicates, conflicts = [], [], []
    for row in candidates:
        key = row_key(row)
        prior = seen.get(key)
        if prior is None:
            to_write.append(row)
            seen[key] = row
        elif _identity_bytes(prior) == _identity_bytes(row):
            duplicates.append(row)
        else:
            conflicts.append(row)
    return to_write, duplicates, conflicts


def join_outcome(run_record):
    """The pass share of a sealed run's own `assertions` dict (real RUN-RECORD.json shape:
    `{"A1": {"pass": true, ...}, ...}`), to 4 decimals, or None when the record carries no
    usable table. A row's `outcome_ref` names one run's RUN-RECORD.json directly; the join
    computes that run's own pass share, never a lane's pooled VERDICT.json share -- the run
    record is the artifact the pointer actually resolves to."""
    if not isinstance(run_record, dict):
        return None
    table = run_record.get("assertions")
    if not isinstance(table, dict) or not table:
        return None
    total = passed = 0
    for entry in table.values():
        total += 1
        if isinstance(entry, dict) and entry.get("pass") is True:
            passed += 1
    if total == 0:
        return None
    return round(passed / total, 4)


def read_watermark(path):
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def write_watermark(path, state):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
    os.replace(tmp, path)
