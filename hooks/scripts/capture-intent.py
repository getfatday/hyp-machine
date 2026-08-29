#!/usr/bin/env python3
"""UserPromptSubmit capture-intent detector (hyp).

Deterministic, zero-network, stdlib-only: reads the prompt (hook JSON or raw
text on stdin), emits at most one line of additionalContext when the user is
asking to save/note/record knowledge, and always exits 0.

Precision-first: a missed nudge costs nothing (the intake skill's own
description still triggers the skill), while a false one adds noise — so
discourse and operational phrasings are explicitly excluded. This nudge is an
interactive-session reinforcement only; the durable enforcement lives in the
PreToolUse guard and the consumer's settings deny rules.
"""
import json
import re
import sys

# Positive forms: an imperative ask to persist knowledge.
_POSITIVE = re.compile(
    r"\b(?:note|jot|write)\s+(?:this|that|it)\s+down\b"
    r"|\bnote\s+this\b"
    r"|\b(?:save|capture)\s+(?:this|that)\b"
    r"|\bremember\s+(?:this|that)\b"
    r"|\b(?:make|take|keep)\s+a\s+note\b"
    r"|\bfile\s+(?:this|that)\s+away\b"
    r"|\bfor\s+future\s+reference\b"
    r"|\bdon'?t\s+lose\s+(?:this|that)\b",
    re.IGNORECASE,
)

# Traps that share surface vocabulary but carry no capture ask:
#   - operational saves ("save that file", "capture that screenshot")
#   - recollection idioms ("remember that time ...")
#   - statements after "remember this/that" ("remember that this is flaky")
_TRAPS = re.compile(
    r"\b(?:save|capture)\s+(?:this|that)\s+"
    r"(?:file|image|screenshot|output|log|buffer|draft|change|commit|branch)s?\b"
    r"|\bremember\s+that\s+time\b"
    r"|\bremember\s+(?:this|that)\s+"
    r"(?:is|was|has|had|will|would|can|could|should|takes?|needs?)\b",
    re.IGNORECASE,
)

_CONTEXT = (
    "The user is asking to save or note knowledge. Route it through the "
    "hyp plugin's `intake` skill: raw-first for verbatim input "
    "(write-once, provenance header), classify to one right home, keep it "
    "minimal, link it and add an index line, and record one write-once "
    "journal fragment."
)


def read_prompt():
    data = sys.stdin.read()
    try:
        payload = json.loads(data)
    except Exception:
        return data
    if isinstance(payload, dict):
        for key in ("prompt", "user_prompt"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return ""
    return data


def main():
    prompt = read_prompt()
    if not prompt or not _POSITIVE.search(prompt):
        return
    # Trap guard: if removing the trap phrasings removes every positive hit,
    # the prompt only *looked* like a capture ask — stay silent.
    if not _POSITIVE.search(_TRAPS.sub(" ", prompt)):
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _CONTEXT,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
