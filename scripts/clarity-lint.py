#!/usr/bin/env python3
"""clarity-lint.py — the communication contract's lintable rules (L1-L11).

PROVENANCE — MEASURED, byte-preserving port of the source lab's clarity lint
(scripts/clarity-lint.py there; landed 2026-08-28 with the clarity canon —
measured firing 104 findings on the status-quo corpus and silent on the
contract-conformant corpus; the naive-reader eval behind the contract measured
comprehension 78.6%->100% at -35% reader effort. Counted hardening specs
H-207..H-211 are registered in the source lab; this lint ships on the advisory
pattern pending those verdicts). Only this provenance framing and the contract
pointer (docs/communication-contract.md) differ from the lab copy.

Usage:
  clarity-lint.py card   <card.md>   [--vocab house-vocabulary.json]
  clarity-lint.py report <report.md> [--vocab house-vocabulary.json]

Exit 0 = compliant (zero findings). Exit 1 = findings. Exit 2 = usage/malformed input.
One line per finding: CLARITY-LINT\t<file>\t<rule>\t<detail>.

Contract: docs/communication-contract.md (decision-clarity program, 2026-08-28).
Vocabulary: house-vocabulary.json (rule L3's gloss list; default: beside this script).

First-cut choices, documented (the H-DRAFT-clarity-lint experiment hardens these):
- HTML comments and the `# ...` title heading are stripped before any check.
- "Body" = HEADLINE..WHY ONLY YOU (cards) / WHAT CHANGED..NEXT (reports); section
  labels, option labels, `(recommended ...)` tags, and machine lines (evidence:/answer:)
  are excluded from word counts per L8.
- L2/L3 gloss = a parenthetical within 80 chars of first use, OR (L3 only, the
  "replaced by the gloss" path) >= 2 significant gloss-word stems present in the
  artifact, never counting words that overlap the term itself.
- L4 unknown first tokens are reported as ESCALATE findings (judge territory), which
  still exit 1 — the contract routes them to a judge, not past the gate.
- Vocabulary entries that are id/score patterns (H-NNN, DEC-NNN, N-id, score notation)
  are enforced by L2/L9, not scanned as L3 terms.
Stdlib only; read-only; never writes.
"""
import json
import os
import re
import sys

CARD_SECTIONS = ["HEADLINE", "CONTEXT", "ASK", "OPTIONS", "IF YOU DO NOTHING",
                 "WHY ONLY YOU"]
REPORT_SECTIONS = ["WHAT CHANGED", "DONE", "NEEDS YOU", "NEXT — NO ACTION NEEDED"]
PATTERN_VOCAB_KEYS = {"H-NNN", "DEC-NNN", "N-id", "score notation"}

PROCESS_OPENERS = ("during ", "while ", "following ", "as part of ", "per ",
                   "in the course of ", "the lane", "the run ", "the sweep",
                   "the wave ")
RELATIVE_TIME = ("yesterday", "today", "tomorrow", "tonight", "this morning",
                 "this afternoon", "this evening", "this week", "last week",
                 "last night", "recently", "soon")
VERB_LEXICON = {"accept", "add", "adopt", "answer", "approve", "call", "choose",
                "close", "confirm", "convert", "decline", "defer", "deny", "deploy",
                "drop", "enable", "file", "flip", "give", "hold", "ignore", "keep",
                "land", "launch", "leave", "let", "merge", "name", "open", "park",
                "pick", "publish", "redesign", "register", "reject", "retry",
                "review", "run", "schedule", "send", "ship", "sign", "sit", "skip",
                "start", "stay", "stop", "take", "type", "unlock", "veto", "wait"}
STOPWORDS = {"the", "a", "an", "its", "it", "is", "are", "was", "one", "and", "or",
             "of", "to", "in", "on", "by", "for", "with", "not", "no", "that",
             "this", "those", "these", "as", "at", "be", "from", "has", "have",
             "had", "can", "may", "like", "so", "do", "does", "did", "against",
             "their", "them", "they", "you", "your", "before", "after", "into",
             "over", "under", "out", "up", "down", "than", "then", "when", "where",
             "who", "whom", "how", "what", "why", "will", "would", "should",
             "could", "any", "all", "each", "every", "per", "via", "vs", "etc",
             "toward", "whose", "say", "says", "which"}

ID_RE = re.compile(r"\b(?:H-\d{2,4}|DEC-\d{2,4}|N\d{1,2}|\d+\.\d+\.\d+"
                   r"|(?:fragment|row)\s+\d{1,4})\b")
HASH_RE = re.compile(r"\b(?=[0-9a-f]{7,40}\b)\d*[a-f][0-9a-f]*\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SCORE_RE = re.compile(r"\b\d+\s*[x×]\s*\d+/\d+\b|\b\d{1,2}/\d{1,2}\b(?!\d)")
PATHISH_RE = re.compile(r"(?:^|[\s(·])(?:~?/|\.{1,2}/)?(?:[\w.@-]+/)+[\w.@*-]+"
                        r"|\b[\w-]+\.(?:py|md|json|jsonl|sh|html|yml|yaml|png|txt"
                        r"|lock|log)\b|\bpython3\s|\bscripts/")
SENT_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")


def words(text):
    return [w for w in re.split(r"\s+", text) if re.search(r"[A-Za-z0-9]", w)]


def sentences(text):
    return [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]


def strip_md(text):
    return text.replace("**", "").replace("`", "")


class Artifact:
    """Parsed card or report: sections, options, bullets, machine lines."""

    def __init__(self, raw, kind):
        self.kind = kind
        text = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
        self.section_names = CARD_SECTIONS if kind == "card" else REPORT_SECTIONS
        self.sections = {}          # name -> text (labels stripped)
        self.section_order = []     # names in appearance order
        self.options = []           # dicts: label, tag, consequence, rawline
        self.bullets = []           # (section, text)
        self.machine = {}           # evidence/answer -> text
        self.legacy_options = []    # old "[ ] label — desc" shapes
        current = None
        sec_re = re.compile(r"^\*{0,2}(" + "|".join(re.escape(s) for s in
                            self.section_names) + r")\*{0,2}\s*(?:—|:)?\s*(.*)$")
        for line in text.splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            if line.lstrip().startswith("#"):
                continue                      # title heading: excluded
            m = re.match(r"^(evidence|answer):\s*(.*)$", line.strip(), re.I)
            if m and not line.startswith(" " * 8):
                self.machine.setdefault(m.group(1).lower(), m.group(2))
                current = "machine:" + m.group(1).lower()
                continue
            if current and current.startswith("machine:"):
                if re.match(r"^\s{4,}\S", line):   # wrapped machine line
                    self.machine[current[8:]] += " " + line.strip()
                    continue
            m = sec_re.match(line.strip())
            if m:
                name = m.group(1)
                self.section_order.append(name)
                self.sections[name] = m.group(2).strip()
                current = name
                continue
            m = re.match(r"^-\s+\*\*(.+?)\*\*\s*(\([^)]*\))?\s*(?:→|->)?\s*(.*)$",
                         line.strip())
            if m and current in ("OPTIONS", "OPTION-ITEM"):
                self.options.append({"label": m.group(1).strip(),
                                     "tag": (m.group(2) or "").strip(),
                                     "consequence": m.group(3).strip(),
                                     "rawline": line.strip()})
                current = "OPTION-ITEM"
                continue
            m = re.match(r"^\[\s?\]\s+(\S+)\s+—\s+(.*)$", line.strip())
            if m:
                self.legacy_options.append({"label": m.group(1),
                                            "consequence": m.group(2),
                                            "rawline": line.strip()})
                continue
            m = re.match(r"^-\s+(.*)$", line.strip())
            if m and current in self.section_names:
                self.bullets.append((current, m.group(1).strip()))
                continue
            if current == "OPTION-ITEM" and self.options:
                self.options[-1]["consequence"] += " " + line.strip()
            elif current in self.section_names:
                self.sections[current] = (self.sections.get(current, "") + " "
                                          + line.strip()).strip()
            elif current is None:
                # preamble prose before any section (status-quo shapes)
                self.sections.setdefault("_preamble", "")
                self.sections["_preamble"] += " " + line.strip()
        self.full_text = strip_md(text)

    def body_text(self):
        """Prose the ceilings and scans apply to: section text + option
        consequences + bullets (+ any unstructured preamble). Labels, tags,
        machine lines excluded."""
        parts = [self.sections.get(s, "") for s in self.section_names]
        parts.append(self.sections.get("_preamble", ""))
        parts += [o["consequence"] for o in self.options]
        parts += [o["consequence"] for o in self.legacy_options]
        parts += [b for _s, b in self.bullets]
        return strip_md(" ".join(p for p in parts if p))

    def body_units(self):
        """(unit-name, text) pairs for sentence/bullet ceiling checks."""
        units = [(s, strip_md(self.sections.get(s, "")))
                 for s in self.section_names if self.sections.get(s)]
        units += [("option '%s'" % o["label"], strip_md(o["consequence"]))
                  for o in self.options]
        units += [("bullet (%s)" % s, strip_md(b)) for s, b in self.bullets]
        if self.sections.get("_preamble"):
            units.append(("preamble", strip_md(self.sections["_preamble"])))
        return units


def load_vocab(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    entries = []
    for term, spec in data.get("terms", {}).items():
        if term in PATTERN_VOCAB_KEYS:
            continue
        forms = [term] + list(spec.get("variants", []))
        entries.append({"term": term, "forms": forms,
                        "gloss": spec.get("gloss", ""),
                        "own": {w.lower() for f in forms
                                for w in re.split(r"[\s-]+", f)}})
    return entries


def stem_match(a, b):
    a, b = a.lower().strip(".,;:!?\"'()"), b.lower()
    if len(a) < 3 or len(b) < 3:
        return False
    return a.startswith(b[:max(3, min(len(a), len(b)))]) or \
        b.startswith(a[:max(3, min(len(a), len(b)))])


def lint(path, kind, vocab_path):
    findings = []

    def fail(rule, detail):
        findings.append((rule, detail))

    raw = open(path, encoding="utf-8").read()
    art = Artifact(raw, kind)
    body = art.body_text()
    body_words = words(body)

    # ---- L10 anatomy + pointer placement -------------------------------
    missing = [s for s in art.section_names if s not in art.sections]
    if missing:
        fail("L10", "missing section(s): " + ", ".join(missing))
    else:
        order = [s for s in art.section_order if s in art.section_names]
        if order != art.section_names:
            fail("L10", "sections out of order: " + " > ".join(order))
    if "evidence" not in art.machine:
        fail("L10", "no trailing evidence: line")
    if kind == "card" and "answer" not in art.machine:
        fail("L10", "no trailing answer: line")
    for hit in PATHISH_RE.finditer(body):
        fail("L10", "path/command in body prose: %r" % hit.group(0).strip())

    # ---- L1 impact first ------------------------------------------------
    first_sec = "HEADLINE" if kind == "card" else "WHAT CHANGED"
    first = strip_md(art.sections.get(first_sec, "") or
                     art.sections.get("_preamble", "") or body)
    first_sent = (sentences(first) or [first])[0]
    n = len(words(first_sent))
    if n > 25:
        fail("L1", "first sentence is %d words (max 25)" % n)
    low = first_sent.lower()
    for opener in PROCESS_OPENERS:
        if low.startswith(opener.strip() + " ") or low.startswith(opener):
            fail("L1", "first sentence opens with process marker %r"
                 % opener.strip())
            break
    ids_first = ID_RE.findall(first_sent)
    if len(ids_first) > 1:
        fail("L1", "first sentence carries %d id tokens (max 1): %s"
             % (len(ids_first), ", ".join(ids_first)))

    # ---- L2 id appositions ---------------------------------------------
    seen = []
    for m in ID_RE.finditer(body):
        tok = re.sub(r"\s+", " ", m.group(0))
        if tok in seen:
            continue
        seen.append(tok)
        tail = body[m.end():m.end() + 80]
        if not re.match(r"^[\"'’)\]]*[\s,:—-]{0,3}\(", tail):
            fail("L2", "id %s lacks a plain-noun gloss on first use" % tok)
    if len(seen) > 3:
        fail("L2", "%d distinct ids in body (max 3): %s"
             % (len(seen), ", ".join(seen)))
    for m in HASH_RE.finditer(body):
        fail("L2", "commit hash in body prose: %s (evidence lines only)"
             % m.group(0))

    # ---- L3 house vocabulary --------------------------------------------
    for entry in load_vocab(vocab_path):
        pat = re.compile(r"\b(?:" + "|".join(
            re.escape(f).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]+")
            for f in sorted(entry["forms"], key=len, reverse=True)) + r")\b",
            re.I)
        m = pat.search(body)
        if not m:
            continue
        tail = body[m.end():m.end() + 80]
        if re.match(r"^[\"'’)\]]*[\s,:—-]{0,3}\(", tail):
            continue
        gloss_words = [w for w in words(entry["gloss"])
                       if w.lower().strip(".,()") not in STOPWORDS
                       and len(w) >= 3
                       and not any(stem_match(w, own) for own in entry["own"])]
        share = {g for g in gloss_words
                 for w in words(art.full_text) if stem_match(w, g)}
        if len(share) < 2:
            fail("L3", "house term %r unglossed on first use (gloss: %s)"
                 % (m.group(0), entry["gloss"][:60]))

    # ---- L4/L5/L7: options ----------------------------------------------
    if kind == "card":
        opts = art.options + art.legacy_options
        if not 2 <= len(opts) <= 3:
            fail("L7", "%d options (need 2-3)" % len(opts))
        rec = len(re.findall(r"\(recommended", art.full_text, re.I))
        if rec != 1:
            fail("L7", "%d '(recommended' tags (need exactly 1)" % rec)
        qmarks = body.count("?")
        if qmarks > 1:
            fail("L7", "%d question marks in card (max 1)" % qmarks)
        for o in opts:
            label = o["label"]
            lw = words(label)
            if len(lw) > 4:
                fail("L4", "option label %r is %d words (max 4)"
                     % (label, len(lw)))
            first_tok = re.sub(r"^re-", "", lw[0].lower()) if lw else ""
            if lw and lw[0].lower() not in VERB_LEXICON \
                    and first_tok not in VERB_LEXICON:
                fail("L4", "option label %r does not open with a known "
                     "imperative verb (ESCALATE to judge)" % label)
            if "→" not in o["rawline"] and "->" not in o["rawline"]:
                fail("L5", "option %r has no '→ consequence'" % label)
            tags = len(re.findall(r"\b(?:Not\s+reversible|Reversible):",
                                  o["consequence"]))
            if tags != 1:
                fail("L5", "option %r carries %d reversibility tags "
                     "(need exactly 1)" % (label, tags))

        # ---- L6 default on silence -------------------------------------
        nothing = art.sections.get("IF YOU DO NOTHING", "")
        if not nothing:
            fail("L6", "no IF YOU DO NOTHING line")
        elif not (DATE_RE.search(nothing)
                  or "nothing changes" in nothing.lower()):
            fail("L6", "IF YOU DO NOTHING names neither an absolute date "
                 "(YYYY-MM-DD) nor 'nothing changes'")

        # ---- L11 sidecar -------------------------------------------------
        key_path = os.path.splitext(path)[0] + ".key.json"
        if not os.path.isfile(key_path):
            fail("L11", "answer-key sidecar missing: %s"
                 % os.path.basename(key_path))
        else:
            try:
                key = json.load(open(key_path, encoding="utf-8"))
                for field in ("intended_ask", "option_consequences",
                              "default_on_silence"):
                    if not key.get(field):
                        fail("L11", "sidecar field %r empty" % field)
            except ValueError:
                fail("L11", "sidecar is not valid JSON")

    # ---- L8 ceilings ------------------------------------------------------
    cap = 120 if kind == "card" else 200
    if len(body_words) > cap:
        fail("L8", "body is %d words (max %d)" % (len(body_words), cap))
    if kind == "card":
        h = strip_md(art.sections.get("HEADLINE", ""))
        if h and len(sentences(h)) > 1:
            fail("L8", "HEADLINE is %d sentences (max 1)" % len(sentences(h)))
        if len(words(h)) > 25:
            fail("L8", "HEADLINE is %d words (max 25)" % len(words(h)))
        c = strip_md(art.sections.get("CONTEXT", ""))
        if len(sentences(c)) > 2:
            fail("L8", "CONTEXT is %d sentences (max 2)" % len(sentences(c)))
        if len(words(c)) > 40:
            fail("L8", "CONTEXT is %d words (max 40)" % len(words(c)))
        for o in art.options + art.legacy_options:
            n = len(words(o["consequence"]))
            if n > 30:
                fail("L8", "option %r consequence is %d words (max 30)"
                     % (o["label"], n))
    else:
        done = [b for s, b in art.bullets if s == "DONE"]
        nxt = [b for s, b in art.bullets if s.startswith("NEXT")]
        if len(done) > 5:
            fail("L8", "DONE has %d bullets (max 5)" % len(done))
        if len(nxt) > 3:
            fail("L8", "NEXT has %d bullets (max 3)" % len(nxt))
    for name, text in art.body_units():
        for b in ([text] if not text.startswith("-") else []):
            pass
        for sent in sentences(text):
            n = len(words(sent))
            if n > 25:
                fail("L8", "sentence in %s is %d words (max 25): %r"
                     % (name, n, sent[:60]))
    for _s, b in art.bullets:
        if len(words(b)) > 30:
            fail("L8", "bullet is %d words (max 30): %r" % (len(words(b)), b[:60]))
    ev = art.machine.get("evidence", "")
    n_ptr = len([p for p in re.split(r"\s+[··]\s+|\s·\s", ev) if p.strip()])
    if n_ptr > 3:
        fail("L8", "%d evidence pointers (max 3)" % n_ptr)

    # ---- L9 absolute time, expanded scores --------------------------------
    lowbody = body.lower()
    for tok in RELATIVE_TIME:
        if re.search(r"\b" + re.escape(tok) + r"\b", lowbody):
            fail("L9", "relative time %r in body (use an absolute date)" % tok)
    for m in SCORE_RE.finditer(body):
        fail("L9", "score shorthand %r in body prose without expansion"
             % m.group(0))

    return findings


def main(argv):
    if len(argv) < 2 or argv[0] not in ("card", "report"):
        print(__doc__.strip().splitlines()[0])
        print("usage: clarity-lint.py card|report <file.md> "
              "[--vocab house-vocabulary.json]")
        return 2
    kind, path = argv[0], argv[1]
    vocab = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "house-vocabulary.json")
    if "--vocab" in argv:
        vocab = argv[argv.index("--vocab") + 1]
    if not os.path.isfile(path):
        print("CLARITY-LINT\t%s\tERROR\tno such file" % path)
        return 2
    if not os.path.isfile(vocab):
        print("CLARITY-LINT\t%s\tERROR\tno vocabulary at %s" % (path, vocab))
        return 2
    findings = lint(path, kind, vocab)
    name = os.path.basename(path)
    for rule, detail in findings:
        print("CLARITY-LINT\t%s\t%s\t%s" % (name, rule, detail))
    print("clarity-lint: %s %s — %d finding(s)"
          % (kind, name, len(findings)))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
