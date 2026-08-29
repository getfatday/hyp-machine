#!/usr/bin/env python3
"""ADVISORY currency lint (non-certifying: findings inform the model owner; a clean run
certifies nothing by itself). Usage: currency-lint.py <flat-or-model-dir> <repo-root>

Verifies CURRENT-STATE VALUE CLAIMS in node bodies against the cited artifacts themselves
(the stale-echo class: a node asserting a pinned artifact's current
state from a historical report rather than the artifact). Mechanical classes only — no
semantic guessing; a claim is checked only when it pairs with a path citation on the SAME
line (markdown table rows are single lines, so rows are covered):

  (a) emptiness claims: `"key": []` / `"key": {}` / `"key": null` / `"key": ""`, plus the
      narrow textual form (the word 'empty' / 'no entries' next to a backticked `key` with a
      .json citation on the line). Flagged when the cited artifact's field is non-empty.
  (b) value claims: `"key": "value"` / `"key": <number>` / `"key": true|false`. Flagged when
      no occurrence of the field in the cited artifact carries the claimed value.
  (c) vice-versa emptiness: `"key": [x, ...]` (populated single-line list) flagged when every
      occurrence of the field in the cited artifact is an empty list.

Verification: JSON parse when the cited file is .json (the key is searched at any depth; a
claim verifies if ANY occurrence matches — elision-lenient); whitespace-flexible substring
check otherwise (.md/.jsonl/...). A claim pairs with the citations on its line, ignoring
paths inside the claim itself; candidates are ordered .json artifacts first (they are the
artifact of record for a JSON-field claim), nearest first, and THE FIRST CITATION THAT CAN
SPEAK DECIDES — verify silences, mismatch flags. A historical .md/.jsonl co-citation that
still quotes the old value can therefore never launder a claim the cited artifact itself
contradicts. Citations that do not resolve to a file under <repo-root> are skipped silently
(resolution is lint_citations' job), as are claims that are unverifiable mechanically
(elided '...' values, keys absent from the cited JSON). Bare 'stale' prose without a
checkable value form is out of scope. Deliberately NO status: debt escape — a debt marker
does not license a false current-state assertion.

Output: one sorted 'currency-stale\t<node-file>:<line>\tclaims ...; actual: ...' line per
flag; silent when clean. Exit 1 iff flags. Stdlib only."""
import json, os, re, sys

KINDS = ('actors', 'commands', 'events', 'policies', 'readmodels')

CITE = re.compile(
    r'(?<![\w/])((?:~/)?[\w.-]+(?:/[\w.-]+)*\.(?:jsonl|json|tsx|ts|mjs|cjs|js|md|yaml|yml|sh|txt|css|html))'
    r'(?::(\d+)(?:-(\d+))?)?')
# "key": <[] | {} | null | true | false | number | "string" | [flat, non-empty, list]>
CLAIM = re.compile(
    r'"(?P<key>[A-Za-z_$][\w.$-]*)"\s*:\s*'
    r'(?P<val>\[\s*\]|\{\s*\}|null|true|false|-?\d+(?:\.\d+)?(?![\w.])|"(?P<sval>[^"]*)"|\[[^\][]+\])')
TEXTUAL_EMPTY = re.compile(r'\bempty\b|\bno entries\b', re.I)
TICKED_KEY = re.compile(r'`([A-Za-z_$][\w$-]{0,40})`')  # simple identifier only — never a path


def fm_split(text):
    """Return (frontmatter-line-count, body-lines-with-absolute-numbers)."""
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                return [(n + 1, l) for n, l in enumerate(lines) if n > i]
    return [(n + 1, l) for n, l in enumerate(lines)]


class Repo:
    def __init__(self, root):
        self.root = root
        self.cache = {}

    def load(self, relpath):
        """-> ('json', parsed) | ('text', str) | None if unresolvable/unparseable."""
        if relpath in self.cache:
            return self.cache[relpath]
        out = None
        fp = os.path.join(self.root, relpath.lstrip('./'))
        if os.path.isfile(fp):
            try:
                raw = open(fp, encoding='utf-8', errors='ignore').read()
                if relpath.endswith('.json'):
                    try:
                        out = ('json', json.loads(raw))
                    except ValueError:
                        out = None  # malformed pinned JSON is not this lint's job
                else:
                    out = ('text', raw)
            except OSError:
                out = None
        self.cache[relpath] = out
        return out


def occurrences(obj, key, acc):
    """All values held under dict key `key` at any depth, in traversal order."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                acc.append(v)
            occurrences(v, key, acc)
    elif isinstance(obj, list):
        for v in obj:
            occurrences(v, key, acc)
    return acc


def preview(v):
    if isinstance(v, str):
        return '"%s"' % (v if len(v) <= 40 else v[:37] + '...')
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if v is None:
        return 'null'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return 'non-empty list (%d entries)' % len(v) if v else '[]'
    if isinstance(v, dict):
        return 'non-empty object (%d keys)' % len(v) if v else '{}'
    return type(v).__name__


def check_json(doc, key, kind, want):
    """-> ('verify'|'mismatch'|'neutral', actual-summary)."""
    occ = occurrences(doc, key, [])
    if not occ:
        return 'neutral', ''
    if kind == 'empty-list':
        if any(isinstance(v, list) and not v for v in occ):
            return 'verify', ''
        return 'mismatch', '; '.join(dict.fromkeys(preview(v) for v in occ[:3]))
    if kind == 'empty-obj':
        if any(isinstance(v, dict) and not v for v in occ):
            return 'verify', ''
        return 'mismatch', '; '.join(dict.fromkeys(preview(v) for v in occ[:3]))
    if kind == 'null':
        if any(v is None for v in occ):
            return 'verify', ''
        return 'mismatch', '; '.join(dict.fromkeys(preview(v) for v in occ[:3]))
    if kind == 'empty-str':
        if any(v == '' for v in occ):
            return 'verify', ''
        return 'mismatch', '; '.join(dict.fromkeys(preview(v) for v in occ[:3]))
    if kind == 'scalar':
        for v in occ:
            if isinstance(want, bool) or isinstance(v, bool):
                if v is want:
                    return 'verify', ''
            elif isinstance(want, (int, float)) and isinstance(v, (int, float)):
                if v == want:
                    return 'verify', ''
            elif v == want:
                return 'verify', ''
        vals = ', '.join(dict.fromkeys(preview(v) for v in occ[:4]))
        return 'mismatch', 'field holds %s' % vals
    if kind == 'nonempty-list':
        lists = [v for v in occ if isinstance(v, list)]
        if not lists:
            return 'neutral', ''
        if any(lists_v for lists_v in lists):
            return 'verify', ''
        return 'mismatch', 'empty ([] at every occurrence)'
    if kind == 'textual-empty':
        if any((isinstance(v, (list, dict, str)) and not v) or v is None for v in occ):
            return 'verify', ''
        return 'mismatch', '; '.join(dict.fromkeys(preview(v) for v in occ[:3]))
    return 'neutral', ''


def check_text(raw, key, kind, val_literal):
    """Whitespace-flexible substring check for non-JSON files -> same triple states."""
    if kind == 'nonempty-list':
        return 'neutral', ''  # bracketed-list text matching is too brittle outside JSON
    if kind == 'textual-empty':
        return 'neutral', ''
    body = {'empty-list': r'\[\s*\]', 'empty-obj': r'\{\s*\}', 'null': r'null(?!\w)',
            'empty-str': r'""'}.get(kind)
    if body is None:  # scalar
        if val_literal.startswith('"'):
            body = re.escape(val_literal)
        elif val_literal in ('true', 'false'):
            body = val_literal + r'(?!\w)'
        else:
            body = re.escape(val_literal) + r'(?![\d.])'
    pat = re.compile(re.escape('"%s"' % key) + r'\s*:\s*' + body)
    if pat.search(raw):
        return 'verify', ''
    return 'mismatch', 'no `"%s": %s` in cited file' % (key, val_literal)


def classify(m):
    """CLAIM match -> (kind, want, literal) or None when unverifiable."""
    val = m.group('val')
    if re.fullmatch(r'\[\s*\]', val):
        return 'empty-list', None, '[]'
    if re.fullmatch(r'\{\s*\}', val):
        return 'empty-obj', None, '{}'
    if val == 'null':
        return 'null', None, 'null'
    if val in ('true', 'false'):
        return 'scalar', val == 'true', val
    if val.startswith('"'):
        sval = m.group('sval')
        if '...' in sval or '…' in sval:
            return None  # elided quote — not mechanically checkable
        if sval == '':
            return 'empty-str', None, '""'
        return 'scalar', sval, val
    if val.startswith('['):
        return 'nonempty-list', None, val if len(val) <= 40 else val[:37] + '...'
    try:
        num = json.loads(val)
    except ValueError:
        return None
    return 'scalar', num, val


def line_citations(line):
    return [(c.start(1), c.end(0), c.group(1)) for c in CITE.finditer(line)]


def evaluate(claims, cites, repo):
    """claims: [(span, key, kind, want, literal)] -> flag summaries."""
    out = []
    for (a, b), key, kind, want, literal in claims:
        cands = [(0 if path.endswith('.json') else 1, max(s - b, a - e, 0), s, path)
                 for s, e, path in cites
                 if not (s < b and e > a)]  # a path inside the claim is its value, not a citation
        cands.sort()
        verdict = None
        for _, _, _, path in cands:
            loaded = repo.load(path)
            if loaded is None:
                continue  # unresolvable citation: lint_citations' job, skip silently
            state, actual = (check_json(loaded[1], key, kind, want) if loaded[0] == 'json'
                             else check_text(loaded[1], key, kind, literal))
            if state == 'verify':
                break
            if state == 'mismatch':
                verdict = (path, actual)
                break  # the nearest citation that can speak decides
        if verdict:
            path, actual = verdict
            if kind == 'textual-empty':
                claim_s = '`%s` empty at %s' % (key, path)
            else:
                claim_s = '"%s": %s at %s' % (key, literal, path)
            out.append('claims %s; actual: %s' % (claim_s, actual or 'differs'))
    return out


def scan_line(line, repo):
    cites = line_citations(line)
    if not cites:
        return []
    cite_spans = [(s, e) for s, e, _ in cites]
    claims = []
    for m in CLAIM.finditer(line):
        if any(s <= m.start() and m.end() <= e for s, e in cite_spans):
            continue  # inside a citation token
        c = classify(m)
        if c:
            claims.append(((m.start(), m.end()), m.group('key'), c[0], c[1], c[2]))
    # narrow textual-emptiness form: `key` within 60 chars of 'empty'/'no entries', .json cited
    if any(p.endswith('.json') for _, _, p in cites):
        for w in TEXTUAL_EMPTY.finditer(line):
            for t in TICKED_KEY.finditer(line):
                gap = max(t.start() - w.end(), w.start() - t.end(), 0)
                if gap <= 60:
                    claims.append(((min(w.start(), t.start()), max(w.end(), t.end())),
                                   t.group(1), 'textual-empty', None, 'empty'))
    json_only = [c for c in cites if c[2].endswith('.json')]
    flags = []
    for claim in claims:
        use = json_only if claim[2] == 'textual-empty' else cites
        flags.extend(evaluate([claim], use, repo))
    return flags


def main(root, repo_root):
    repo = Repo(repo_root)
    flags = set()
    for dirpath, _, fns in os.walk(root):
        if os.path.basename(dirpath) not in KINDS:
            continue
        for fn in sorted(fns):
            if not fn.endswith('.md'):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            for lineno, line in fm_split(open(full, encoding='utf-8').read()):
                for summary in scan_line(line, repo):
                    flags.add('currency-stale\t%s:%d\t%s' % (rel, lineno, summary))
    for f in sorted(flags):
        print(f)
    return 1 if flags else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
