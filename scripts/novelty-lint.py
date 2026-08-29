#!/usr/bin/env python3
"""Report-only lint: flags novelty-overclaim phrases ("the only tool", "first-ever",
"unprecedented") in README prose, so marketing language never outruns the evidence.
Common technical uses of first/only (append-only, spec first) are not findings.
Default target: README.md. Exit 0 always."""
import re
import sys

PAT = re.compile(r"\b(the (?:first|only) (?:tool|plugin|system|way|framework)|first[- ]ever|one of a kind|never been (?:done|possible)|invented|unprecedented|revolutionary|groundbreaking|game[- ]chang\w+)\b", re.I)
ALLOW = re.compile(r"(spec first|first defined|first release|first use|feature state|impact first)", re.I)

count = 0
for path in sys.argv[1:] or ["README.md"]:
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            m = PAT.search(line)
            if m and not ALLOW.search(line):
                count += 1
                print(f"{path}:{i}: NOVELTY-OVERCLAIM '{m.group(0)}': {line.strip()[:100]}")
print(f"novelty-lint: {count} finding(s) (report-only)")
