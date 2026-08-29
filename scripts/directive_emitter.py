#!/usr/bin/env python3
"""Directive On-close commitment emitter (counted under
H-199-directive-intake-v2 in the source lab, kept 2026-08-28, two consecutive
counted 5/5 live sessions; shipped as counted -- only provenance framing and
consumer-repo path resolution (`.claude/hyp.json` overlay for the directives
dir) differ from the counted fixture copy; usage guide:
docs/directive-intake.md in this plugin).

A sibling of the kept On-keep commitment emitter lineage, with exactly four
substitutions (frozen with the counted design):

  | On-keep emitter                       | directive_emitter (this file)              |
  |---------------------------------------|--------------------------------------------|
  | scans hypotheses/H-*.md               | scans <directives_dir>/D-*.md (TEMPLATE.md |
  |                                       | never matches)                             |
  | gate: Status first word `kept`        | gate: Status first word `executed` OR      |
  |                                       | `closed`                                   |
  | block heading `## On keep`            | block heading `## On close`                |
  | instance-of: hypothesis/H-NNN,        | instance-of: directive/D-NNN,              |
  | caused-by: hypothesis-kept:H-NNN,     | caused-by: directive-executed:D-NNN,       |
  | backstop slug H-NNN-missing-onkeep    | backstop slug D-NNN-missing-onclose        |

Usage:
    directive_emitter.py <repo-root> <ledger-path> <fixed-date>

The carried-over contract:

  * each well-formed On-close list line (valid closes-when bracket) becomes one
    kind:commitment ledger record: hit = the full line text INCLUDING its bracket,
    slug = slugify(hit). Emitted records are ordinary kind:commitment rows: they resolve
    through the EXISTING resolver / commitment-lint machinery with zero new predicate
    work.
  * a line inside the block WITHOUT a valid bracket emits nothing for that line; the rest
    of the block's well-formed lines still emit normally.
  * a block whose only content is the literal item "- none" (case-insensitive) emits ZERO
    records and is not a finding -- the explicit "no follow-ups" declaration.
  * a MISSING "## On close" heading, or one present but with zero list-items after
    stripping its instructional HTML comment, emits exactly ONE backstop record:
    slug "<D-NNN>-missing-onclose", kind:commitment, a hit with no closes-when bracket
    (it resolves via the session resolver's ordinary "malformed/missing bracket ->
    unresolved" rule, surfacing every session until the block is written).
  * EMIT-ONCE by slug: every slug already present in <ledger-path> (any kind, read
    best-effort) is skipped; re-running over an unchanged repo/ledger appends nothing.
  * never raises; always exits 0, except a plain usage error (wrong argument count), which
    prints to stderr and exits 1. Stdlib only, no network.
  * prints one line per newly appended record, in emission order (informational only):
        EMITTED\t<slug>\t<hit>
"""
import json
import os
import re
import sys

DEFAULT_DIRECTIVES_DIR = 'directives'

DNUM_RE = re.compile(r'^(D-\d+)-')
STATUS_HEADING_RE = re.compile(r'^##\s+Status\s*$')
ON_CLOSE_HEADING_RE = re.compile(r'^##\s+On close\s*$')
ANY_HEADING_RE = re.compile(r'^##\s+\S')
LIST_ITEM_RE = re.compile(r'^-\s+(.*\S)\s*$')
EXECUTED_OR_CLOSED_RE = re.compile(r'^\s*(executed|closed)\b', re.IGNORECASE)
CLOSES_WHEN_RE = re.compile(
    r'\[closes-when:\s*(path-exists|commit-grep|hypothesis-kept|maintainer-ruling)=([^\]]+)\]'
)
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)


def directives_dir(repo_root):
    """The configured directives dir from `.claude/hyp.json`, best-effort."""
    try:
        with open(os.path.join(repo_root, ".claude", "hyp.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        val = data.get("directives_dir")
        if isinstance(val, str) and val:
            return val
    except Exception:
        pass
    return DEFAULT_DIRECTIVES_DIR


def slugify(text):
    """Reused, not reinvented (the counted ledger lineage), so a commitment's slug is
    derived by the exact same algorithm already used everywhere else in this ledger's
    lineage. 'INTENT:' never appears on an On-close line, so that strip is a no-op here;
    kept anyway for byte-for-byte fidelity to the original function."""
    text = re.sub(r'^\s*INTENT:\s*', '', text.strip(), flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text[:60] if text else 'untitled'


def extract_section(lines, heading_re):
    """Lines strictly between the first line matching heading_re and the next '## ' heading
    (or EOF). None if heading_re never matches anywhere in lines. Carried over verbatim."""
    start = None
    for i, line in enumerate(lines):
        if heading_re.match(line):
            start = i + 1
            break
    if start is None:
        return None
    end = start
    while end < len(lines) and not ANY_HEADING_RE.match(lines[end]):
        end += 1
    return lines[start:end]


def status_is_executed_or_closed(text):
    """Whether this D-doc's '## Status' section's first non-comment word is 'executed' or
    'closed' (case-insensitive) -- substitution 2; the comment-stripping and first-word
    mechanics carried over verbatim so the whole join agrees on what a doc's status word
    is."""
    section = extract_section(text.splitlines(), STATUS_HEADING_RE)
    if section is None:
        return False
    block_text = HTML_COMMENT_RE.sub(' ', '\n'.join(section))
    stripped = block_text.strip()
    if not stripped:
        return False
    first_word = stripped.split()[0]
    return bool(EXECUTED_OR_CLOSED_RE.match(first_word))


def on_close_block_state(text):
    """Returns ('missing', None) | ('none', None) | ('items', [line_text, ...]).

    'missing' covers BOTH a genuinely absent '## On close' heading and one present but
    carrying zero list-items once its instructional HTML comment is stripped -- both are
    "no carrier", the same backstop case, by design. Carried over verbatim (substitution 3
    changes only the heading)."""
    section = extract_section(text.splitlines(), ON_CLOSE_HEADING_RE)
    if section is None:
        return 'missing', None

    block_text = HTML_COMMENT_RE.sub('', '\n'.join(section))
    items = []
    for line in block_text.splitlines():
        m = LIST_ITEM_RE.match(line)
        if m:
            items.append(m.group(1).strip())

    if not items:
        return 'missing', None
    if len(items) == 1 and items[0].lower() == 'none':
        return 'none', None
    return 'items', items


def has_valid_closes_when(text):
    """Whether `text` carries a syntactically valid closes-when bracket -- a known predicate
    name AND a non-empty argument after stripping whitespace. Carried over verbatim so emit
    time and resolve time can never split-brain on bracket validity."""
    m = CLOSES_WHEN_RE.search(text)
    return bool(m and m.group(2).strip())


def build_record(fixed_date, slug, hit, dnum):
    return {
        "date": fixed_date,
        "slug": slug,
        "hit": hit,
        "kind": "commitment",
        "instance-of": "directive/{}".format(dnum),
        "caused-by": "directive-executed:{}".format(dnum),
    }


def read_existing_slugs(ledger_path):
    slugs = set()
    try:
        with open(ledger_path, 'r', encoding='utf-8') as f:
            for raw in f:
                text = raw.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except ValueError:
                    continue
                if isinstance(record, dict) and 'slug' in record:
                    slugs.add(record['slug'])
    except OSError:
        pass
    return slugs


def collect_new_records(dir_path, fixed_date, existing_slugs):
    """Returns (new_records, emitted) where emitted is [(slug, hit), ...] in emission order.
    existing_slugs is mutated in place so a later duplicate slug within the SAME pass is also
    caught. Carried over verbatim modulo the four substitutions."""
    new_records = []
    emitted = []
    try:
        fnames = sorted(os.listdir(dir_path))
    except OSError:
        return new_records, emitted

    for fname in fnames:
        if not (fname.startswith('D-') and fname.endswith('.md')):
            continue  # TEMPLATE.md never matches (substitution 1)
        m = DNUM_RE.match(fname)
        if not m:
            continue
        dnum = m.group(1)

        try:
            with open(os.path.join(dir_path, fname), 'r', encoding='utf-8',
                      errors='ignore') as f:
                text = f.read()
        except OSError:
            continue

        try:
            if not status_is_executed_or_closed(text):
                continue
            state, items = on_close_block_state(text)
        except Exception:
            continue  # one malformed D-doc never blocks emission for the others

        if state == 'missing':
            slug = '{}-missing-onclose'.format(dnum)
            if slug not in existing_slugs:
                hit = ("UNJOINABLE: {} is executed with no machine-readable "
                       "'## On close' block".format(dnum))
                new_records.append(build_record(fixed_date, slug, hit, dnum))
                emitted.append((slug, hit))
                existing_slugs.add(slug)
            continue

        if state == 'none':
            continue  # "- none": zero records, zero findings, by design

        for hit in items:
            if not has_valid_closes_when(hit):
                continue  # a line without a valid bracket emits nothing for that line
            slug = slugify(hit)
            if slug in existing_slugs:
                continue  # emit-once
            new_records.append(build_record(fixed_date, slug, hit, dnum))
            emitted.append((slug, hit))
            existing_slugs.add(slug)

    return new_records, emitted


def append_records(ledger_path, records):
    if not records:
        return
    parent = os.path.dirname(ledger_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(ledger_path, 'a', encoding='utf-8') as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=True) + '\n')


def main(argv):
    if len(argv) != 4:
        sys.stderr.write('usage: directive_emitter.py <repo-root> <ledger-path> '
                         '<fixed-date>\n')
        return 1

    repo_root, ledger_path, fixed_date = argv[1], argv[2], argv[3]
    dir_path = os.path.join(repo_root, directives_dir(repo_root))

    try:
        existing_slugs = read_existing_slugs(ledger_path)
        new_records, emitted = collect_new_records(dir_path, fixed_date, existing_slugs)
        append_records(ledger_path, new_records)
    except Exception:
        # A follow-up silently NOT emitted is exactly the failure mode this mechanism exists
        # to prevent -- but a hard crash on a hook path is worse. Never raise.
        return 0

    for slug, hit in emitted:
        print('EMITTED\t{}\t{}'.format(slug, hit))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
