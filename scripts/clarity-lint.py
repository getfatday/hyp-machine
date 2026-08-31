#!/usr/bin/env python3
"""clarity-lint-v2.py — the communication contract's term-choice and register rules
(addendum L12-L15), extending the committed L1-L11 lint (scripts/clarity-lint.py).

STAGED apply-now artifact (artifact-language program, 2026-08-29). At land these modes
merge into scripts/clarity-lint.py in one commit; until then this file delegates the
committed card/report modes and implements only the NEW modes. Contract addendum:
apply-now/communication-contract-addendum.md. Vocabulary: house-vocabulary v2
(apply-now/house-vocabulary-v2.json schema; v1 files stay loadable — absent fields
default to known_equivalent null, anchor null, status house-only).

Usage:
  clarity-lint-v2.py vocab  <vocabulary.json>                      # L12b birth certificates
  clarity-lint-v2.py gloss  <vocabulary.json> [--term <headword>]  # gloss rules only
  clarity-lint-v2.py spec   <spec.md>   [options]                  # L13/L15 + L12a/c on hypothesis specs
  clarity-lint-v2.py scan   <file.md>   [options] [--kind KIND]    # L12a/c on any artifact class
  clarity-lint-v2.py card   <card.md>   [--v1 <clarity-lint.py>]   # delegated to committed L1-L11 lint
  clarity-lint-v2.py report <report.md> [--v1 <clarity-lint.py>]   # delegated to committed L1-L11 lint

Options: --vocab <json> (default: house-vocabulary-v2.json beside this script, else
scripts/house-vocabulary.json under --repo), --repo <dir> (default: cwd; supplies
scripts/grounding-wordlist.txt and operating-model/*/GLOSSARY.md), --enforce <rules>
(comma list; promotes report-only rules, e.g. --enforce L12a — the per-class
enforcement flip a counted keep licenses), --kind generic|fragment|ruling|
coordination|research|waveplan (scan classes).

Exit 0 = no hard findings. Exit 1 = hard findings. Exit 2 = usage/malformed input.
Hard findings:  CLARITY-LINT\t<file>\t<rule>\t<detail>
Report-only:    CLARITY-LINT-REPORT\t<file>\t<rule>\t<detail>   (exit-neutral until flipped)

Rules implemented here (see the addendum for prose):
  L12a NEW-TERM     unregistered coinage (report-only at birth; hard in titles via L15)
  L12b VOCAB        glossary birth certificate: schema, gloss lint, statuses, anchors,
                    variant uniqueness — hard; wired into harden-check on vocabulary diffs
  L12c DEPRECATED   a deprecated-alias term in a new artifact — hard; names the canonical
                    headword (mechanically substitutable outside quoted spans; --fix lands
                    with the merge, not in this staging)
  L12d/L15 TITLE    title register: <= 25 words, max 1 house-only term, zero unregistered
                    coinages, zero score shorthand — hard
  L13  HEADER       plain-language header per artifact class (spec "## In plain terms",
                    fragment "**In plain terms:**" first line, ruling above-the-fold,
                    coordination above-the-fold) — hard on NEW artifacts (pre-land gate;
                    the migration plan stages the existing corpus)
  L14  AUDIENCE     research pages declare their register (audience tag first line;
                    reader pages open "**In one sentence:**") — hard
  FROZEN-GRAMMAR    spec headings never rename or reorder (the eight preflight headings)

Documented first-cut choices (the term-lint hypothesis hardens these):
  * "no other house terms in a gloss" is enforced against HOUSE-ONLY forms only:
    preferred entries are, by status semantics, known or plain names.
  * Pattern keys (H-NNN, DEC-NNN, N-id, score notation) are exempt from the gloss
    id/score scans — they define those patterns — matching the committed lint.
  * The coinage dictionary is the union of /usr/share/dict/words (when present),
    scripts/grounding-wordlist.txt, registered vocabulary forms, model-glossary
    headwords, and TECH_COMMON (the contract reader's assumed general-software
    vocabulary, frozen below). Tokens containing digits are id grammar (H-NNN,
    0.4.1, L12a, shas) and never fire. The land version ships a pinned wordlist
    so findings are byte-identical across machines.
  * A hyphen/slash compound fires only when a part is unresolvable or single-letter
    (K-strikes) and the whole is unregistered: transparent compounds of known words
    (write-once, byte-compare) are ISO-704-transparent and exempt.
  * Coinage threshold: >= 2 uses in the artifact, or >= 1 use in its title.
Stdlib only; read-only; never writes.
"""
import json
import os
import re
import subprocess
import sys

PATTERN_KEYS = {"H-NNN", "DEC-NNN", "N-id", "score notation"}
SPEC_HEADINGS = ["## Status", "## Hypothesis", "## Variable under test", "## Baseline",
                 "## Method", "## Binary assertions", "## Verdict rule", "## Runs"]
STATUS_ENUM = {"preferred", "house-only"}
ID_RE = re.compile(r"\b(?:H-\d{2,4}|DEC-\d{2,4}|N\d{1,2}|\d+\.\d+\.\d+"
                   r"|(?:fragment|row)\s+\d{1,4})\b")
HASH_RE = re.compile(r"\b(?=[0-9a-f]{7,40}\b)\d*[a-f][0-9a-f]*\b")
SCORE_RE = re.compile(r"\b\d+\s*[x×]\s*\d+/\d+\b|\b\d{1,2}/\d{1,2}\b(?!\d)")
URL_RE = re.compile(r"https?://\S+")
PATH_RE = re.compile(r"(?:~?/|\.{1,2}/)?(?:[\w.@-]+/)+[\w.@*-]+"
                     r"|\b[\w-]+\.(?:py|md|json|jsonl|sh|html|yml|yaml|png|txt|tsv|log|patch)\b")
CAMEL_RE = re.compile(r"[a-z][A-Z]")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’/-]*[A-Za-z]|[A-Za-z]")

TECH_COMMON = frozenset("""
admin ai api apis ascii auth bash bool boolean backlog byte bytes cert certs changelog
cli config configs commit commits csv curl cwd dashboard dataset datasets dir dirs
email emails filename filenames frontmatter git github glob grep html http https id ids
iso json jsonl kanban llm markdown metadata regex readme repo repos rfc runbook runtime
sandbox semver sha shas sop stderr stdin stdlib stdout timestamp timestamps toml
tooltip tooltips tripwire tsv unicode url urls utf wiki wikipedia workflow workflows
workspace workspaces worktree worktrees yaml zsh
""".split())
DRAFT_ID_RE = re.compile(r"\b[A-Z]+-DRAFT-[\w-]+")
SUFFIXES = ("ings", "ing", "ers", "ors", "ies", "es", "ed", "er", "or", "ly", "s")
PREFIXES = ("counter", "anti", "micro", "multi", "super", "inter", "over", "post",
            "pre", "under", "non", "mis", "sub", "co", "re", "un", "de")


def words(text):
    return [w for w in re.split(r"\s+", text) if re.search(r"[A-Za-z0-9]", w)]


def sentence_count(text):
    t = text.replace("...", " ").replace("…", " ")
    return len(re.findall(r"[.!?](?:\s|$)", t))


class Vocab:
    """v2 loader with v1 defaults; matching machinery shared by all modes."""

    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path, encoding="utf-8"))
        self.terms = self.data.get("terms", {})
        self.forms = {}            # lowercased form -> headword
        self.house_only = []       # (headword, [forms])
        self.deprecated = []       # (headword, [forms], canonical)
        for head, e in self.terms.items():
            status = e.get("status", "house-only")
            fs = [head] + list(e.get("variants", []))
            for f in fs:
                self.forms.setdefault(f.lower(), head)
            if head in PATTERN_KEYS:
                continue
            if status == "house-only":
                self.house_only.append((head, fs))
            elif status.startswith("deprecated-alias-of:"):
                self.deprecated.append((head, fs, status.split(":", 1)[1]))
        self.word_forms = {w.lower() for f in self.forms for w in re.split(r"[\s/-]+", f) if w}

    @staticmethod
    def _phrase_re(forms):
        return re.compile(r"\b(?:" + "|".join(
            re.escape(f).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]+")
            for f in sorted(forms, key=len, reverse=True)) + r")\b", re.I)

    def house_only_hits(self, text):
        hits = []
        for head, fs in self.house_only:
            if self._phrase_re(fs).search(text):
                hits.append(head)
        return hits

    def deprecated_hits(self, text):
        hits = []
        for head, fs, canonical in self.deprecated:
            m = self._phrase_re(fs).search(text)
            if m:
                hits.append((m.group(0), canonical))
        return hits


class Dictionary:
    def __init__(self, repo, vocab, model_glossaries):
        self.known = set(TECH_COMMON)
        sysdict = "/usr/share/dict/words"
        if os.path.isfile(sysdict):
            self.known.update(w.strip().lower() for w in open(sysdict, errors="ignore"))
        gw = os.path.join(repo, "scripts", "grounding-wordlist.txt")
        if os.path.isfile(gw):
            self.known.update(w.strip().lower() for w in open(gw, errors="ignore"))
        self.known.update(vocab.word_forms)
        for path in model_glossaries:
            for line in open(path, errors="ignore"):
                m = re.match(r"^\|\s*([^|]+?)\s*\|", line)
                if m and m.group(1).lower() not in ("term", "---", ""):
                    self.known.update(w.lower() for w in re.split(r"[\s/-]+", m.group(1)) if w)

    def _lookup(self, w):
        if len(w) <= 1:
            return False
        if w in self.known:
            return True
        for suf in SUFFIXES:
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                base = w[: -len(suf)]
                if base in self.known or base + "e" in self.known:
                    return True
                if len(base) >= 3 and base[-1] == base[-2] and base[:-1] in self.known:
                    return True
        return False

    def resolves(self, w):
        w = re.sub(r"['’]s$", "", w.strip("'’")).lower()
        if not w or len(w) == 1 or any(c.isdigit() for c in w):
            return True
        if self._lookup(w):
            return True
        for p in PREFIXES:
            if w.startswith(p) and len(w) - len(p) >= 3 and self._lookup(w[len(p):]):
                return True
        return False


def strip_excluded(text, drop_comments=True):
    """Remove spans the term scans never read: HTML comments, code fences, inline
    code, double-quoted spans (straight and curly), URLs, paths."""
    if drop_comments:
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"“[^”\n]{0,300}”", " ", text)
    text = re.sub(r'"[^"\n]{0,300}"', " ", text)
    text = URL_RE.sub(" ", text)
    text = PATH_RE.sub(" ", text)
    return text


def coinage_candidates(prose, vocab, dic):
    """L12a: token -> count over excluded-stripped prose (id grammar pre-stripped)."""
    prose = DRAFT_ID_RE.sub(" ", ID_RE.sub(" ", prose))
    counts = {}
    for tok in TOKEN_RE.findall(prose):
        if len(tok) == 1 or any(c.isdigit() for c in tok):
            continue
        low = tok.lower()
        if low in vocab.forms:
            continue
        parts = [p for p in re.split(r"[/-]", tok) if p]
        fires = False
        if len(parts) > 1:
            fires = any(len(p) == 1 for p in parts) or \
                not all(dic.resolves(p) or p.lower() in vocab.forms for p in parts)
        elif CAMEL_RE.search(tok):
            camel = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", tok)
            fires = not all(dic.resolves(p) for p in camel)
        else:
            fires = not dic.resolves(tok)
        if fires:
            counts[low] = counts.get(low, 0) + 1
    return counts


def lint_gloss(head, entry, vocab, fail):
    gloss = entry.get("gloss", "")
    if not gloss:
        fail("L12b", "%s: empty gloss" % head)
        return
    n = len(words(gloss))
    if n > 25:
        fail("L12b", "%s: gloss is %d words (max 25)" % (head, n))
    sc = sentence_count(gloss)
    if sc > 1 or (sc == 1 and not re.search(r"[.!?]\s*$", gloss)):
        fail("L12b", "%s: gloss is not one clause" % head)
    if head not in PATTERN_KEYS:
        if ID_RE.search(gloss) or HASH_RE.search(gloss):
            fail("L12b", "%s: gloss contains an id token" % head)
        if SCORE_RE.search(gloss):
            fail("L12b", "%s: gloss contains score shorthand" % head)
    for other, fs in vocab.house_only:
        if other == head:
            continue
        if Vocab._phrase_re(fs).search(gloss):
            fail("L12b", "%s: gloss leans on house-only term %r" % (head, other))


def mode_vocab(path, vocab, fail, report, gloss_only=False, only_term=None):
    seen_forms = {}
    heads = set(vocab.terms)
    for head, e in vocab.terms.items():
        if only_term and head != only_term:
            continue
        lint_gloss(head, e, vocab, fail)
        if gloss_only:
            continue
        status = e.get("status")
        if status is None:
            fail("L12b", "%s: missing status (v2 entries declare one)" % head)
        elif status not in STATUS_ENUM and not status.startswith("deprecated-alias-of:"):
            fail("L12b", "%s: illegal status %r" % (head, status))
        elif status.startswith("deprecated-alias-of:"):
            target = status.split(":", 1)[1]
            if target not in heads:
                fail("L12b", "%s: deprecated target %r is not a headword" % (head, target))
            elif str(vocab.terms[target].get("status", "")).startswith("deprecated"):
                fail("L12b", "%s: deprecated target %r is itself deprecated" % (head, target))
        anchor = e.get("anchor")
        if status == "house-only":
            if not e.get("known_equivalent"):
                fail("L12b", "%s: house-only without known_equivalent" % head)
            if not anchor and not e.get("anchor_waiver"):
                fail("L12b", "%s: house-only without anchor or anchor_waiver" % head)
        if anchor:
            if not str(anchor).startswith("https://"):
                fail("L12b", "%s: anchor is not a stable https URL" % head)
            elif "?" in str(anchor):
                report("L12b", "%s: anchor carries a query string (cool-URI: prefer a "
                               "bare stable path)" % head)
        for f in [head] + list(e.get("variants", [])):
            fl = f.lower()
            owner = seen_forms.get(fl)
            if owner and owner != head:
                fail("L12b", "form %r owned by both %r and %r" % (f, owner, head))
            seen_forms[fl] = head
            if fl in (h.lower() for h in heads if h != head):
                fail("L12b", "variant %r of %r shadows another headword" % (f, head))


def title_register(title_prose, vocab, dic, fail, where="title"):
    n = len(words(title_prose))
    if n > 25:
        fail("L15", "%s prose is %d words (max 25)" % (where, n))
    if SCORE_RE.search(title_prose):
        fail("L15", "score shorthand in %s: %r"
             % (where, SCORE_RE.search(title_prose).group(0)))
    hits = vocab.house_only_hits(title_prose)
    if len(hits) > 1:
        fail("L15", "%d house-only terms in %s (max 1): %s"
             % (len(hits), where, ", ".join(hits)))
    for tok, cnt in sorted(coinage_candidates(strip_excluded(title_prose), vocab, dic).items()):
        fail("L15", "unregistered coinage %r in %s (L12d: titles carry zero; register "
                    "a glossary entry in this commit or reword)" % (tok, where))


def scan_common(prose, title, vocab, dic, fail, report):
    for alias, canonical in vocab.deprecated_hits(prose):
        fail("L12c", "deprecated term %r: write %r (mechanically substitutable outside "
                     "quoted spans)" % (alias, canonical))
    counts = coinage_candidates(prose, vocab, dic)
    title_counts = coinage_candidates(strip_excluded(title or ""), vocab, dic)
    for tok in sorted(set(counts) | set(title_counts)):
        n = counts.get(tok, 0) + title_counts.get(tok, 0)
        if tok in title_counts or n >= 2:
            report("L12a", "NEW-TERM %r (%dx%s): register a glossary entry in this "
                           "commit or reword to a known term"
                           % (tok, n, ", title" if tok in title_counts else ""))


def mode_spec(path, raw, vocab, dic, fail, report):
    m = re.search(r"^#\s+([^\n]+)$", raw, re.M)
    title_line = m.group(1) if m else ""
    title_prose = title_line.split(":", 1)[1].strip() if ":" in title_line else title_line
    title_register(title_prose, vocab, dic, fail)

    present = [h for h in SPEC_HEADINGS if re.search(r"^" + re.escape(h) + r"\s*$", raw, re.M)]
    if present != SPEC_HEADINGS:
        fail("FROZEN-GRAMMAR", "spec headings missing or reordered: "
             + (", ".join(h for h in SPEC_HEADINGS if h not in present) or "order drift"))
    order = {"status": raw.find("\n## Status"), "plain": raw.find("\n## In plain terms"),
             "hyp": raw.find("\n## Hypothesis")}
    if order["plain"] < 0:
        fail("L13", "no '## In plain terms' section (place it after ## Status, "
                    "before ## Hypothesis)")
    elif not (order["status"] < order["plain"] < order["hyp"]):
        fail("L13", "'## In plain terms' is not between ## Status and ## Hypothesis")
    section = ""
    if order["plain"] >= 0:
        section = raw[order["plain"]:order["hyp"] if order["hyp"] > order["plain"] else None]
        section = re.sub(r"<!--.*?-->", " ", section, flags=re.S)
    bullets = dict(re.findall(r"^-\s+\*\*(What we're testing|What \"keep\" means|Terms):?\*\*:?\s*(.+)$",
                              section, re.M))
    for need in ("What we're testing", 'What "keep" means'):
        if need not in bullets:
            if order["plain"] >= 0:
                fail("L13", "missing '- **%s:**' bullet" % need)
    for label, text in bullets.items():
        n = len(words(text))
        if n > 25:
            fail("L13", "'%s' line is %d words (max 25)" % (label, n))
        if label != "Terms" and sentence_count(text) > 1:
            fail("L13", "'%s' line is more than one sentence" % label)
    hyp = ""
    if order["hyp"] >= 0:
        hyp = raw[order["hyp"]:]
        nxt = hyp.find("\n## ", 4)
        hyp = re.sub(r"<!--.*?-->", " ", hyp[:nxt if nxt > 0 else None], flags=re.S)
    need_gloss = sorted(set(vocab.house_only_hits(title_prose) + vocab.house_only_hits(hyp)))
    if need_gloss:
        terms_line = bullets.get("Terms", "")
        missing = [t for t in need_gloss if not Vocab._phrase_re(
            [t] + list(vocab.terms[t].get("variants", []))).search(terms_line)]
        if missing:
            fail("L13", "house-only term(s) in title/hypothesis not glossed on the "
                        "Terms line: " + ", ".join(missing))
    scan_common(strip_excluded(raw), title_prose, vocab, dic, fail, report)


def mode_scan(path, raw, kind, vocab, dic, fail, report):
    body = raw
    title = ""
    if kind == "fragment":
        parts = re.split(r"^---\s*$", raw, 2, re.M)
        body = parts[2] if len(parts) >= 3 else raw
        first = next((ln.strip() for ln in body.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")), "")
        m = re.match(r"^\*\*In plain terms:\*\*\s*(.+)$", first)
        if not m:
            fail("L13", "fragment does not open '**In plain terms:** <one sentence>' "
                        "(binds NEW fragments; write-once artifacts are never edited)")
        elif len(words(m.group(1))) > 25:
            fail("L13", "'In plain terms' sentence is %d words (max 25)"
                 % len(words(m.group(1))))
    elif kind == "ruling":
        first = raw.splitlines()[0] if raw.splitlines() else ""
        m = re.match(r"^#\s*RULING:\s*(.+)$", first)
        if not m:
            fail("L13", "ruling does not open '# RULING: <one plain sentence>'")
        else:
            title = m.group(1)
            title_register(title, vocab, dic, fail, "RULING line")
        fold = raw.split("\n---", 1)[0]
        if not re.search(r"^BINDS:", fold, re.M):
            fail("L13", "no 'BINDS:' line above the fold")
        mm = re.search(r"^IN PLAIN TERMS:\s*(.+(?:\n(?!BINDS|---|#).+)*)", fold, re.M)
        if not mm:
            fail("L13", "no 'IN PLAIN TERMS:' block above the fold")
        elif sentence_count(mm.group(1)) > 3:
            fail("L13", "IN PLAIN TERMS is %d sentences (max 3)"
                 % sentence_count(mm.group(1)))
        body = fold  # ceilings and hard scans stop at the fold; below is exempt
        scan_common(strip_excluded(raw), title, vocab, dic, lambda r, d: None, report)
    elif kind == "coordination":
        fold = raw.split("\n---", 1)[0]
        for needed in ("STATE:", "NEXT ACTION", "IF THIS LANE BREAKS"):
            if needed not in fold:
                fail("L13", "no '%s' line above the fold" % needed)
        for m in re.finditer(r"\bH-\d{2,4}\b", strip_excluded(fold)):
            tail = strip_excluded(fold)[m.end():m.end() + 80]
            if not re.match(r"^[\"'’)\]]*[\s,:—-]{0,3}\(", tail):
                fail("L13", "bare id %s above the fold without a plain-noun gloss (L2)"
                     % m.group(0))
        body = fold
    elif kind == "research":
        first = raw.splitlines()[0] if raw.splitlines() else ""
        m = re.match(r"^<!--\s*audience:\s*(reader|agent)\s*-->$", first.strip())
        if not m:
            fail("L14", "first line is not '<!-- audience: reader -->' or "
                        "'<!-- audience: agent -->'")
            return
        if m.group(1) == "agent":
            return  # register-exempt but tagged
        if not re.search(r"^\*\*In one sentence:\*\*", raw, re.M):
            fail("L14", "reader page lacks the '**In one sentence:** ...' opener")
    elif kind == "waveplan":
        trails = re.findall(r"\(was H-[^)]*\)", raw)
        if len(trails) >= 2:
            report("L12a", "%d inline rename trails; move them to one '## Lineage' "
                           "table (old id, current id, date) and use current ids inline"
                   % len(trails))
    if kind != "ruling":
        m = re.search(r"^#\s+([^\n]+)$", body, re.M)
        title = m.group(1) if m else ""
        scan_common(strip_excluded(body), title, vocab, dic, fail, report)


def main(argv):
    modes = ("vocab", "gloss", "spec", "scan", "card", "report")
    if len(argv) < 2 or argv[0] not in modes:
        print(__doc__.strip().splitlines()[0])
        print("usage: clarity-lint-v2.py %s <file> [--vocab V] [--repo DIR] "
              "[--kind K] [--enforce RULES] [--term T] [--v1 PATH]" % "|".join(modes))
        return 2
    mode, path = argv[0], argv[1]
    opt = {a: argv[i + 1] for i, a in enumerate(argv)
           if a.startswith("--") and i + 1 < len(argv)}
    here = os.path.dirname(os.path.abspath(__file__))
    repo = opt.get("--repo", os.getcwd())
    if mode in ("card", "report"):
        v1 = opt.get("--v1", os.path.join(repo, "scripts", "clarity-lint.py"))
        if not os.path.isfile(v1):
            print("CLARITY-LINT\t%s\tERROR\tcommitted L1-L11 lint not found at %s "
                  "(pass --v1)" % (path, v1))
            return 2
        args = [sys.executable, v1, mode, path]
        if "--vocab" in opt:
            args += ["--vocab", opt["--vocab"]]
        return subprocess.call(args)
    if not os.path.isfile(path):
        print("CLARITY-LINT\t%s\tERROR\tno such file" % path)
        return 2
    vocab_path = opt.get("--vocab") or next(
        (p for p in (os.path.join(here, "house-vocabulary-v2.json"),
                     os.path.join(repo, "scripts", "house-vocabulary.json"))
         if os.path.isfile(p)), None)
    if not vocab_path:
        print("CLARITY-LINT\t%s\tERROR\tno vocabulary found (pass --vocab)" % path)
        return 2
    enforce = {r.strip() for r in opt.get("--enforce", "").split(",") if r.strip()}
    findings, reports = [], []
    name = os.path.basename(path)

    def fail(rule, detail):
        findings.append((rule, detail))

    def report(rule, detail):
        (findings if rule in enforce else reports).append((rule, detail))

    try:
        vocab = Vocab(vocab_path)
    except (ValueError, OSError) as e:
        print("CLARITY-LINT\t%s\tL12b\tvocabulary unreadable: %s" % (name, e))
        return 1
    glossaries = []
    om = os.path.join(repo, "operating-model")
    if os.path.isdir(om):
        glossaries = [os.path.join(om, d, "GLOSSARY.md") for d in os.listdir(om)
                      if os.path.isfile(os.path.join(om, d, "GLOSSARY.md"))]
    dic = Dictionary(repo, vocab, glossaries)

    if mode in ("vocab", "gloss"):
        mode_vocab(path, Vocab(path), fail, report,
                   gloss_only=(mode == "gloss"), only_term=opt.get("--term"))
    elif mode == "spec":
        mode_spec(path, open(path, encoding="utf-8").read(), vocab, dic, fail, report)
    elif mode == "scan":
        mode_scan(path, open(path, encoding="utf-8").read(),
                  opt.get("--kind", "generic"), vocab, dic, fail, report)
    for rule, detail in findings:
        print("CLARITY-LINT\t%s\t%s\t%s" % (name, rule, detail))
    for rule, detail in reports:
        print("CLARITY-LINT-REPORT\t%s\t%s\t%s" % (name, rule, detail))
    print("clarity-lint-v2: %s %s — %d hard, %d report-only"
          % (mode, name, len(findings), len(reports)))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
