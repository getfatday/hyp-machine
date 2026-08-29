#!/usr/bin/env python3
"""directive-lint -- the level-triggered directive closure join (counted under
H-199-directive-intake-v2 in the source lab, kept 2026-08-28, two consecutive
counted 5/5 live sessions; shipped as counted -- only provenance framing and
consumer-repo path resolution (`.claude/hyp.json` overlay) differ from the
counted fixture copy; usage guide: docs/directive-intake.md in this plugin).

CLI:
    directive-lint.py <repo-root>

Output: `CLASS\t<path>\t<detail>` lines, sorted (the corpus-lint shape);
silent when clean; exit 0 always. Deterministic state lint, re-derived from current state
every run (level-triggered: findings surface every session until the state is fixed, never
edge-fired once and lost). Never raises; any unexpected condition degrades to "no finding
from that probe", never a crash on a hook path.

Finding classes (one owner per join edge -- the ledger<->D-doc edge belongs
to the RESOLVER and On-close completeness to the EMITTER; this lint never duplicates either):

  RAW-UNANSWERED        a committed <raw_dir>/*-directive*.md newer than the install epoch
                        with no <directives_dir>/D-*.md whose `## Ask` section contains its
                        path.
  D-DANGLING-ASK        a D-doc whose `source:` points at a path that does not exist at HEAD.
  D-MALFORMED           missing Ask pointer / Restatement / Affected-nodes section (empty and
                        not `none reachable`), or acceptance-assertion count not in [3,5].
  D-EXECUTED-UNVERIFIED Status executed/closed but verification rows < assertion count, or any
                        non-PASS row without a divergence note.
  D-CLOSED-UNJOURNALED  Status closed but no file in the journal-fragments dir (nor the
                        frozen base journal file, read-only) contains the D-NNN id.

Epoch gating: RAW-UNANSWERED applies only to raw directive files committed after a pinned
epoch sha -- a constant set AT INSTALL to the commit that creates the directives dir, so
pre-existing raw directive files are grandfathered rather than becoming a permanent
unmovable finding floor. EPOCH_SHA None (the shipped default) = no grandfathering: every
committed raw directive file is in scope, which is the correct behavior for a fresh
install. When set to a commit sha, raw files whose ADDING commit is not a descendant of
that epoch commit are exempt.

"Committed" and "at HEAD" are checked through git when the repo-root is a git work tree
(`git ls-tree`-backed tracked-at-HEAD checks), degrading to plain filesystem existence when
git is unavailable -- degrade-open for existence (fewer findings), never a crash.

Concrete parse rules (each pinned with the counted fixture, stated here so any future
re-run sees the same lint):
  * A D-doc is any <directives_dir>/D-*.md file whose name matches D-<digits>-...;
    TEMPLATE.md never matches.
  * Sections are `## <name>` blocks up to the next `## ` heading; HTML comments (including the
    template's own instructional comments) are stripped before content checks, so an untouched
    template section counts as EMPTY, not as content.
  * Ask pointer = a line in `## Ask` matching `- source: <non-space>` (after comment strip).
  * Acceptance assertions = lines in `## Acceptance assertions` matching `^\\s*\\d+[.)]` with
    non-empty text after the number.
  * Verification rows = `|`-delimited table body rows in `## Verification record` (header and
    |---| separator rows excluded). Result cell = column 2; Evidence cell = column 3. A row is
    non-PASS when its Result cell does not start with PASS (case-insensitive); such a row
    "has a divergence note" when its Evidence cell is non-empty after strip.
  * Status word = first non-comment word of `## Status`.

Paths come from `.claude/hyp.json` when present, else the defaults below.
"""
import json
import os
import re
import subprocess
import sys

EPOCH_SHA = None  # set at install to the commit that creates the directives dir

DEFAULTS = {
    "directives_dir": "directives",
    "raw_dir": "research/raw",
    "journal_dir": "experiments/journal-fragments",
    "journal_file": "experiments/journal.md",
}

DNUM_RE = re.compile(r'^(D-\d+)-.*\.md$')
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)
ANY_HEADING_RE = re.compile(r'^##\s+\S')
SOURCE_LINE_RE = re.compile(r'^-\s*source:\s*(\S+)', re.MULTILINE)
ASSERTION_LINE_RE = re.compile(r'^\s*\d+[.)]\s*(\S.*)?$')
NUMBERED_ONLY_RE = re.compile(r'^\s*\d+[.)]\s*$')
RAW_DIRECTIVE_RE = re.compile(r'.*-directive.*\.md$')
NONE_REACHABLE_RE = re.compile(r'none\s+reachable', re.IGNORECASE)


def load_cfg(root):
    """DEFAULTS overlaid with the consumer's `.claude/hyp.json`, best-effort."""
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(root, ".claude", "hyp.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        for key in DEFAULTS:
            if isinstance(data.get(key), str) and data[key]:
                cfg[key] = data[key]
    except Exception:
        pass
    return cfg


def section(text, name):
    """Lines strictly between `## <name>` and the next `## ` heading (or EOF); None when the
    heading is absent. Same extraction shape as the emitter so every tool in this join
    agrees on what a section is."""
    lines = text.splitlines()
    head = re.compile(r'^##\s+' + re.escape(name) + r'\s*$')
    start = None
    for i, line in enumerate(lines):
        if head.match(line):
            start = i + 1
            break
    if start is None:
        return None
    end = start
    while end < len(lines) and not ANY_HEADING_RE.match(lines[end]):
        end += 1
    return '\n'.join(lines[start:end])


def stripped(block):
    """Section content with HTML comments removed; '' for None."""
    if block is None:
        return ''
    return HTML_COMMENT_RE.sub('', block).strip()


def status_word(text):
    block = section(text, 'Status')
    if block is None:
        return ''
    body = HTML_COMMENT_RE.sub(' ', block).strip()
    return body.split()[0].lower() if body.split() else ''


def git_lines(repo, *args):
    """git output lines, or None when git is unavailable / not a work tree. Never raises."""
    try:
        r = subprocess.run(['git', '-C', repo] + list(args), capture_output=True,
                           text=True, timeout=60)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or '').splitlines()


def tracked_at_head(repo, subdir):
    """Relative paths tracked at HEAD under subdir, or None when git can't answer."""
    return git_lines(repo, 'ls-tree', '-r', '--name-only', 'HEAD', '--', subdir)


def exists_at_head(repo, relpath, head_cache):
    """D-DANGLING-ASK: 'does not exist at HEAD'. Uses the tracked-at-HEAD file list
    when git answers; degrades to filesystem existence otherwise (degrade-open)."""
    if head_cache is not None:
        return relpath in head_cache
    return os.path.exists(os.path.join(repo, relpath))


def post_epoch(repo, relpath):
    """Whether relpath's adding commit is inside the epoch window. EPOCH_SHA None -> always
    True (see module docstring). With an epoch set: the file's first adding
    commit must be a descendant of (or equal to) EPOCH_SHA; any git failure -> True
    (fail-toward-surfacing: a raw ask wrongly grandfathered is the silent failure class this
    lint exists to kill)."""
    if not EPOCH_SHA:
        return True
    lines = git_lines(repo, 'log', '--diff-filter=A', '--format=%H', '--', relpath)
    if not lines:
        return True
    adding = lines[-1].strip()
    try:
        r = subprocess.run(['git', '-C', repo, 'merge-base', '--is-ancestor',
                            EPOCH_SHA, adding], capture_output=True, timeout=60)
    except Exception:
        return True
    return r.returncode == 0


def read_file(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except OSError:
        return None


def list_ddocs(repo, cfg):
    ddir = os.path.join(repo, cfg["directives_dir"])
    out = []
    try:
        names = sorted(os.listdir(ddir))
    except OSError:
        return out
    for name in names:
        m = DNUM_RE.match(name)
        if not m:
            continue
        text = read_file(os.path.join(ddir, name))
        if text is None:
            continue
        out.append((cfg["directives_dir"] + '/' + name, m.group(1), text))
    return out


def list_raw_directives(repo, cfg):
    """Committed <raw_dir>/*-directive*.md, relative paths. git-backed when possible;
    filesystem fallback otherwise."""
    tracked = tracked_at_head(repo, cfg["raw_dir"])
    if tracked is not None:
        return sorted(p for p in tracked
                      if RAW_DIRECTIVE_RE.match(os.path.basename(p)))
    raw_dir = os.path.join(repo, cfg["raw_dir"])
    try:
        names = sorted(os.listdir(raw_dir))
    except OSError:
        return []
    return [cfg["raw_dir"] + '/' + n for n in names if RAW_DIRECTIVE_RE.match(n)]


def assertion_items(text):
    block = stripped(section(text, 'Acceptance assertions'))
    items = []
    for line in block.splitlines():
        if NUMBERED_ONLY_RE.match(line):
            continue  # a bare template number with no text is not an assertion
        m = ASSERTION_LINE_RE.match(line)
        if m and m.group(1):
            items.append(m.group(1).strip())
    return items


def verification_rows(text):
    """(result_cell, evidence_cell) per table BODY row in ## Verification record."""
    block = stripped(section(text, 'Verification record'))
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if not cells:
            continue
        first = cells[0]
        if first in ('#',) or set(first) <= set('-: '):
            continue  # header row or |---| separator
        result = cells[1] if len(cells) > 1 else ''
        evidence = cells[2] if len(cells) > 2 else ''
        rows.append((result, evidence))
    return rows


def journal_mentions(repo, dnum, cfg):
    """Whether any journal-fragments file (or the frozen base journal file, read-only)
    contains the D-NNN id."""
    frag_dir = os.path.join(repo, cfg["journal_dir"])
    try:
        names = sorted(os.listdir(frag_dir))
    except OSError:
        names = []
    for name in names:
        text = read_file(os.path.join(frag_dir, name))
        if text and dnum in text:
            return True
    frozen = read_file(os.path.join(repo, cfg["journal_file"]))
    return bool(frozen and dnum in frozen)


def lint(repo):
    findings = []
    cfg = load_cfg(repo)
    ddocs = list_ddocs(repo, cfg)
    head_raw = tracked_at_head(repo, '.')  # full HEAD file list for existence checks

    # RAW-UNANSWERED: raw ask -> D-doc
    ask_sections = [(path, stripped(section(text, 'Ask'))) for path, _d, text in ddocs]
    for rawrel in list_raw_directives(repo, cfg):
        if not post_epoch(repo, rawrel):
            continue
        if not any(rawrel in ask for _p, ask in ask_sections):
            findings.append(('RAW-UNANSWERED', rawrel,
                             'no %s/D-*.md Ask section contains this path'
                             % cfg["directives_dir"]))

    for path, dnum, text in ddocs:
        word = status_word(text)
        n_assert = len(assertion_items(text))

        # D-DANGLING-ASK: D-doc -> raw ask
        for m in SOURCE_LINE_RE.finditer(stripped(section(text, 'Ask'))):
            src = m.group(1).strip()
            if src and not exists_at_head(repo, src, head_raw):
                findings.append(('D-DANGLING-ASK', path,
                                 'source: %s does not exist at HEAD' % src))

        # D-MALFORMED: internal completeness
        ask = stripped(section(text, 'Ask'))
        if not SOURCE_LINE_RE.search(ask):
            findings.append(('D-MALFORMED', path, 'Ask has no `- source:` pointer'))
        if not stripped(section(text, 'Restatement')):
            findings.append(('D-MALFORMED', path, 'Restatement missing or empty'))
        nodes = stripped(section(text, 'Affected nodes'))
        node_rows = [l for l in nodes.splitlines()
                     if l.strip().startswith('|')
                     and not set(l.strip().strip('|').replace('|', '')) <= set('-: ')
                     and 'Reached via' not in l]
        if not node_rows and not NONE_REACHABLE_RE.search(nodes):
            findings.append(('D-MALFORMED', path,
                             'Affected nodes empty and not `none reachable`'))
        if not 3 <= n_assert <= 5:
            findings.append(('D-MALFORMED', path,
                             'acceptance-assertion count %d not in [3,5]' % n_assert))

        # D-EXECUTED-UNVERIFIED: assertions -> verification
        if word in ('executed', 'closed'):
            rows = verification_rows(text)
            if len(rows) < n_assert:
                findings.append(('D-EXECUTED-UNVERIFIED', path,
                                 'verification rows %d < assertion count %d'
                                 % (len(rows), n_assert)))
            for i, (result, evidence) in enumerate(rows, start=1):
                if not result.upper().startswith('PASS') and not evidence:
                    findings.append(('D-EXECUTED-UNVERIFIED', path,
                                     'row %d non-PASS with no divergence note' % i))

        # D-CLOSED-UNJOURNALED: D-doc -> journal fragment
        if word == 'closed' and not journal_mentions(repo, dnum, cfg):
            findings.append(('D-CLOSED-UNJOURNALED', path,
                             'no journal fragment contains %s' % dnum))

    return sorted('%s\t%s\t%s' % f for f in findings)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write('usage: directive-lint.py <repo-root>\n')
        return 0  # exit 0 always
    try:
        for line in lint(os.path.abspath(argv[1])):
            print(line)
    except Exception:
        pass  # a crash on a hook path is worse than a silent probe; exit 0 always
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
