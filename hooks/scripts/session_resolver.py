#!/usr/bin/env python3
"""SessionStart ledger resolver (v5: the decision store + the three-shape
normalizer over the v4 four-kind resolver).

PROVENANCE — port of the source lab's hooks/session_resolver.py v5 (kept lineage
H-088 -> H-095 -> H-101 -> H-105, decision store joined 2026-08-28 under the
consolidated decision-making directive). Differences from the lab copy: the
shared closes_when.py loads from the consumer repo's scripts/ first and then
from this plugin's own scripts/ (same evaluator, so the resolver and the
compiled dashboard can never disagree on open/closed), and a one-argument AUTO
mode (`session_resolver.py <repo_root>`) derives the ledger / hypotheses /
operating-model paths from .claude/hyp.json + the hyp defaults for hook
wiring. The 3-, 4-, and 5-argument CLI and all resolution semantics are
identical to the lab copy.

v6: the H-239 events-cursor join (kept 2026-09-02, 2x5/5 -- exactly-once
cross-session surfacing with the resolver contract byte-preserved). The join is
the kept fixture BLOCK + one call line, inserted mechanically by the fixture's
join_lib.make_on at its two anchors (experiments/runs/H-239/fixture/join_lib.py
in the source lab; the H-230 strip-proof pattern): surface_events() runs LAST in
run(), so decisions-first, head-20, and every non-event row stay byte-unchanged
-- the byte-compare contract test ships as scripts/selftest-events.py check 6.
One named divergence from the kept block: the hook payload is read through the
module's _hook_stdin_raw() single-read cache, because AUTO mode's worktree
resolution already consumes stdin and a second read would see EOF and collapse
every session onto one shared cursor. Cursor semantics unchanged: per-session
count at runtime .claude/events-cursor/<session>.cursor, never committed;
silent when nothing is new; a missing ledger/events.jsonl keeps this file's
output byte-identical to v5 (events are experiments-profile machinery -- the
stream only exists where the emitters run).

v5 additions (docs/decisions.md sections 5-6) -- everything below this block is the v4
text, unchanged where it still applies:
  - THREE-shape read: legacy {date,slug,hit,kind} rows unchanged; v2
    {kind,id,date,text,closes_when} rows normalize as slug := id, hit := text +
    " [closes-when: <closes_when>]" (the 12 live v2 rows stop being warned as malformed);
    kind:"decision" / kind:"decision-resolution" rows join the decision store. LEDGER-WARN
    now fires only for truly-bad lines (unparseable JSON, or none of the three shapes).
  - Open-decision surfacing, printed FIRST after the read-pass warnings (the hook pipes
    through `head -20`; decisions are the top surface): one line per open decision --
        DECISION-LEDGER\t<id>\t<urgency>\t<title>\t<blocks>
    ordered urgency (high first) then oldest ask, followed by ONE summary line --
        DECISIONS-OPEN\t<count>\toldest <id> <age>d
    A decision is open iff no accepted|denied resolution row joins its id (a comment row
    stays open). Ages use the wall clock (this is a live session surface, not a compiled
    artifact); pin with DECISIONS_TODAY=YYYY-MM-DD for deterministic tests. No decision
    rows -> not even the summary line prints: a decision-free legacy-only ledger keeps
    v4's byte-identical output (regression lock preserved).
  - decision-resolved=<id> reaches commitment rows through the shared closes_when.py
    (scripts/closes_when.py carries the predicate; this file's commitment machinery is
    unchanged).

Original v4 header:

Built from hypotheses/H-105-directive-net.md and
experiments/runs/H-105/fixture/directive-intake-design.md S3 (resolver v4's `directive` kind
contract) ONLY, extending the KEPT H-101 v3 resolver (hooks/session_resolver.py) verbatim for
all pre-existing behavior -- typed records: intent + amendment + commitment. Per directive 8
(train/test separation), never saw any label set, seeder, or manifest for this or any prior
resolver hypothesis.

Usage (UNCHANGED CLI from v3 -- no new argv position; design doc S3: "No new argv --
directives dir derives from repo_root"):
    session_resolver_v4.py <ledger_path> <hypotheses_dir> [operating_model_dir] [repo_root]

argv[4] (repo_root) is OPTIONAL, exactly as in v3. Calling with the original 2 or 3 arguments
reproduces v3's (and, transitively, H-088's/H-095's) kept behavior byte-for-byte (regression
lock): a ledger with no "kind":"directive" records resolves identically either way, and any
"kind":"directive" record present under a 2-arg or 3-arg call surfaces as unresolved
(repo_root absent -> safe default, the same rule already applied to amendment/commitment).

Ledger records are JSON objects, one per line: {"date", "slug", "hit"} plus an OPTIONAL "kind".
"kind" absent => "intent" (backward compatible with every H-088 ledger). Supported kinds are
now "intent", "amendment", "commitment", "directive"; any other kind value makes the line
malformed.

Resolution predicate, per kind:
    intent      slug substring-matches some H-*.md filename under <hypotheses_dir>. UNCHANGED
                from H-088/H-095/v3.
    amendment   slug substring-matches the CONTENTS of some file found by a recursive walk of
                <operating_model_dir>. UNCHANGED from H-095/v3: absent/unlistable dir -> every
                amendment record unresolved.
    commitment  resolved iff repo_root is given AND the record's "hit" carries a valid
                "[closes-when: <predicate>=<arg>]" bracket AND that predicate is satisfied
                against repo_root. repo_root absent, or the bracket missing/malformed in hit,
                -> unresolved. UNCHANGED from v3 (H-101), including the closes_when.py load
                path and the level-triggered backstop-suppression fix -- see is_resolved_commitment
                and is_stale_backstop below, both untouched.

    directive   NEW in v4 (design doc S3). resolved iff repo_root is given AND
                <repo_root>/directives/ is listable AND the slug substring-matches the
                CONTENTS of some file matching <repo_root>/directives/D-*.md (a flat listdir,
                not a recursive walk -- design doc S3: "recursive walk not needed -- flat
                dir"). Contents-match, not filename-match -- design doc S1/S3: directive slugs
                are long derived spans that would make filenames absurd, the same reason
                amendment resolution is contents-match rather than filename-match. repo_root
                absent, or directives/ missing/unlistable, -> every directive record
                unresolved (the same safe default amendment already uses for a missing model
                dir; never crashes, never guesses).

Prints, oldest first ACROSS ALL FOUR KINDS (stable sort by "date"; ties keep each record's
original position in the ledger file), one line per unresolved, well-formed record:

    INTENT-LEDGER\t<date>\t<slug>\t<hit>          (kind == "intent")
    AMENDMENT-LEDGER\t<date>\t<slug>\t<hit>        (kind == "amendment")
    COMMITMENT-LEDGER\t<date>\t<slug>\t<hit>       (kind == "commitment")
    DIRECTIVE-LEDGER\t<date>\t<slug>\t<hit>        (kind == "directive", NEW in v4)

Resolved entries are silent. Each malformed line (invalid JSON; not a JSON object; missing
"date"/"slug"/"hit"; or a "kind" value that is none of the four supported kinds) is skipped
with exactly one warning, printed immediately during the read pass -- so warnings precede the
(oldest-first) report, in file order:

    LEDGER-WARN\tskipped malformed line <n>

Never raises; always exits 0. An empty or missing ledger prints nothing. Stdlib only (the
closes_when.py load is a plain-file import attempt, still stdlib-only machinery -- importlib).

Regression lock (design doc S3 / H-NET assertion 5): with a ledger containing no "directive"
rows, this file's output is byte-identical to v3's under 2-, 3-, 4-, and 5-arg calls -- the
new load_directive_contents() call happens unconditionally (mirroring how om_contents is
already loaded unconditionally), but its result only ever feeds is_resolved_directive(),
which is only ever consulted for kind == "directive" rows. With none present, that extra load
has zero effect on output.
"""
import datetime
import importlib.util
import json
import os
import sys

SUPPORTED_KINDS = ('intent', 'amendment', 'commitment', 'directive')
DECISION_URGENCY_ORDER = {'high': 0, 'normal': 1, 'low': 2}

_HOOK_STDIN_RAW = None


def _hook_stdin_raw():
    """Port adaptation (single-read law): AUTO mode's worktree resolution
    (_session_repo) and the H-239 events join (_events_session_id) both need the
    hook's stdin payload, and stdin is consumable exactly once -- the first
    reader caches it here. A tty or unreadable stdin caches ''. Never raises,
    never blocks on a tty."""
    global _HOOK_STDIN_RAW
    if _HOOK_STDIN_RAW is None:
        try:
            _HOOK_STDIN_RAW = '' if sys.stdin.isatty() else (sys.stdin.read() or '')
        except Exception:
            _HOOK_STDIN_RAW = ''
    return _HOOK_STDIN_RAW

# closes_when.py candidate locations (port adaptation): the consumer repo's own
# scripts/ copy wins when present, then this plugin's shipped copy
# (hooks/scripts/session_resolver.py -> ../../scripts/closes_when.py). One shared
# evaluator, two consumers — the resolver and the dashboard can never disagree on
# open/closed (the lab's split-brain guard).
def _closes_when_candidates(repo_root):
    cands = []
    if repo_root:
        cands.append(os.path.join(repo_root, 'scripts', 'closes_when.py'))
    cands.append(os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '..', '..', 'scripts', 'closes_when.py')))
    return cands

_closes_when_module = None
_closes_when_load_attempted = False


def _load_closes_when(repo_root=None):
    """Best-effort, memoized load of H-100's shared closes_when.py. UNCHANGED from v3.

    Returns the loaded module if it exists at a candidate path and exposes both
    parse_bracket(line) -> (predicate, arg) | None and
    check(predicate, arg, repo_root) -> bool -- H-100's actual exported names.
    Returns None otherwise (file missing, unreadable, raises on exec, or missing either
    expected callable) -- the degrade path. Attempted at most once per process; never raises.
    """
    global _closes_when_module, _closes_when_load_attempted
    if _closes_when_load_attempted:
        return _closes_when_module
    _closes_when_load_attempted = True
    for path in _closes_when_candidates(repo_root):
        try:
            if not os.path.isfile(path):
                continue
            spec = importlib.util.spec_from_file_location('h101_closes_when', path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not (callable(getattr(module, 'parse_bracket', None)) and
                    callable(getattr(module, 'check', None))):
                continue
            _closes_when_module = module
            break
        except Exception:
            continue
    return _closes_when_module


def load_hypothesis_filenames(hyp_dir):
    """UNCHANGED from H-088/H-095/v3: filenames under <hyp_dir> that look like H-*.md."""
    try:
        names = os.listdir(hyp_dir)
    except OSError:
        return []
    return [n for n in names if n.startswith('H-') and n.endswith('.md')]


def load_operating_model_contents(om_dir):
    """UNCHANGED from v3/H-095. Contents of every file found by a recursive walk of <om_dir>.

    Returns None when om_dir itself was never supplied (argv[3] omitted, or explicitly "") --
    the "absent" case. Returns a (possibly empty) list otherwise; never raises.
    """
    if not om_dir:
        return None
    contents = []
    for root, _dirs, files in os.walk(om_dir):
        for fname in files:
            path = os.path.join(root, fname)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    contents.append(f.read())
            except OSError:
                continue
    return contents


def load_directive_contents(repo_root):
    """NEW in v4 (design doc S3). Contents of every file matching <repo_root>/directives/D-*.md
    -- a flat os.listdir, not a recursive walk (design doc S3: "recursive walk not needed --
    flat dir").

    Returns None when repo_root itself was never supplied (argv[4] omitted, or falsy) -- the
    "absent" case, mirroring load_operating_model_contents()'s posture for a missing
    operating_model_dir. A missing/unlistable directives/ dir also degrades to None. Returns a
    (possibly empty) list otherwise; never raises.
    """
    if not repo_root:
        return None
    directives_dir = os.path.join(repo_root, 'directives')
    try:
        names = os.listdir(directives_dir)
    except OSError:
        return None
    contents = []
    for name in names:
        if not (name.startswith('D-') and name.endswith('.md')):
            continue
        path = os.path.join(directives_dir, name)
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                contents.append(f.read())
        except OSError:
            continue
    return contents


def is_resolved_intent(slug, hyp_filenames):
    """UNCHANGED from H-088/H-095/v3: slug substring-matches some H-*.md filename."""
    return any(slug in name for name in hyp_filenames)


def is_resolved_amendment(slug, om_contents):
    """UNCHANGED from v3/H-095. om_contents is None => operating_model_dir was absent =>
    always unresolved."""
    if om_contents is None:
        return False
    return any(slug in content for content in om_contents)


def is_resolved_directive(slug, directive_contents):
    """NEW in v4 (design doc S3). directive_contents is None => repo_root was absent, or
    directives/ missing/unlistable => always unresolved. Otherwise: slug substring-matches
    the CONTENTS of some directives/D-*.md file (contents-match, not filename-match -- the
    same posture is_resolved_amendment already takes, and for the same reason: directive
    slugs are long derived spans that would make filenames absurd)."""
    if directive_contents is None:
        return False
    return any(slug in content for content in directive_contents)


import re

_BACKSTOP_SLUG_RE = re.compile(r'^(H-\d+)-missing-onkeep$')


def is_stale_backstop(slug, hyp_dir, hyp_filenames):
    """Level-triggered suppression (living-copy fix, 2026-08-15): a '<H-NNN>-missing-onkeep'
    backstop row is satisfied the moment the spec gains a machine-readable '## On keep' block —
    the ledger is append-only, so the row itself never goes away; recompute from spec state
    instead of trusting the historical append. UNCHANGED from v3. Never raises."""
    m = _BACKSTOP_SLUG_RE.match(slug or '')
    if not m:
        return False
    prefix = m.group(1) + '-'
    for name in hyp_filenames:
        if name.startswith(prefix):
            try:
                with open(os.path.join(hyp_dir, name), 'r', encoding='utf-8',
                          errors='ignore') as f:
                    if re.search(r'^##\s+On keep\s*$', f.read(), re.MULTILINE):
                        return True
            except OSError:
                continue
    return False


def is_resolved_commitment(hit, repo_root):
    """UNCHANGED from v3 (H-101). resolved iff repo_root is given, the shared closes_when
    module loaded, hit carries a valid [closes-when: predicate=arg] bracket, AND that
    predicate is satisfied against repo_root. Any failure at any step -> unresolved; never
    raises."""
    if not repo_root:
        return False
    module = _load_closes_when(repo_root)
    if module is None:
        return False
    try:
        parsed = module.parse_bracket(hit)
        if not parsed:
            return False
        predicate, argument = parsed
        return bool(module.check(predicate, argument, repo_root))
    except Exception:
        return False


def is_resolved(kind, slug, hit, hyp_filenames, om_contents, repo_root, hyp_dir=None,
                directive_contents=None):
    if kind == 'amendment':
        return is_resolved_amendment(slug, om_contents)
    if kind == 'commitment':
        if hyp_dir and is_stale_backstop(slug, hyp_dir, hyp_filenames):
            return True
        return is_resolved_commitment(hit, repo_root)
    if kind == 'directive':
        return is_resolved_directive(slug, directive_contents)
    return is_resolved_intent(slug, hyp_filenames)  # 'intent' default


def read_ledger_lines(ledger_path):
    try:
        with open(ledger_path, 'r', encoding='utf-8') as f:
            return f.readlines()
    except OSError:
        return []


def _decision_age_days(rec):
    """Whole days since the row's original ask. Wall clock (live surface), pinnable via
    DECISIONS_TODAY=YYYY-MM-DD for deterministic tests. Never raises."""
    try:
        pin = os.environ.get('DECISIONS_TODAY')
        today = (datetime.date.fromisoformat(pin) if pin else datetime.date.today())
        then = datetime.date.fromisoformat(
            str(rec.get('requested_at') or rec.get('date'))[:10])
        return max(0, (today - then).days)
    except (ValueError, TypeError):
        return 0


def surface_decisions(decisions, resolutions):
    """v5: print the open-decision surface (docs/decisions.md section 6). One
    DECISION-LEDGER line per open decision (urgency then oldest ask then id), then the
    DECISIONS-OPEN count + oldest-age summary. Prints NOTHING when no decision rows
    exist (v4 byte-identical regression lock for decision-free ledgers)."""
    if not decisions:
        return
    closed = set()
    for rec in resolutions:
        if rec.get('disposition') in ('accepted', 'denied'):
            closed.add(rec.get('id'))
    open_rows = [rec for rid, rec in decisions.items() if rid not in closed]
    if not open_rows:
        return
    open_rows.sort(key=lambda r: (DECISION_URGENCY_ORDER.get(r.get('urgency'), 1),
                                  -_decision_age_days(r), str(r.get('id'))))
    for rec in open_rows:
        blocks = rec.get('blocks') or []
        print('DECISION-LEDGER\t{}\t{}\t{}\t{}'.format(
            rec.get('id'), rec.get('urgency', 'normal'), rec.get('title', ''),
            ', '.join(str(b) for b in blocks) if blocks else '-'))
    oldest = max(open_rows, key=_decision_age_days)
    print('DECISIONS-OPEN\t{}\toldest {} {}d'.format(
        len(open_rows), oldest.get('id'), _decision_age_days(oldest)))


# <<<H-239-EVENTS-JOIN block begin>>>
def _events_session_id():
    """H-239: the per-session cursor key. Hook stdin carries the harness's JSON
    payload (session_id et al.); read it once, guarded -- a tty or empty/opaque
    stdin degrades to the CLAUDE_SESSION_ID env var, then to 'default'. Never
    raises, never blocks on a tty; sanitized to [A-Za-z0-9._-], max 80 chars."""
    sid = None
    try:
        raw = _hook_stdin_raw()  # PORT adaptation: shared single-read stdin cache
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                sid = payload.get('session_id')
    except Exception:
        sid = None
    if not sid:
        sid = os.environ.get('CLAUDE_SESSION_ID') or 'default'
    cleaned = []
    for ch in str(sid)[:80]:
        cleaned.append(ch if (ch.isalnum() or ch in '._-') else '-')
    return ''.join(cleaned) or 'default'


def surface_events(repo_root):
    """H-239 events-cursor join (additive; called LAST in run(), so the
    decisions-first head and every non-event row stay byte-unchanged). Surfaces
    ledger/events.jsonl records this session has not yet seen -- exactly once
    per session, silent when nothing is new (zero lines printed, the H-204
    suppression contract extended from decisions to events). The cursor (a
    consumed-line count) lives in runtime .claude/events-cursor/<session>.cursor
    -- per-session, never committed. Never raises; a broken stream never breaks
    a session."""
    if not repo_root:
        return
    events_path = os.path.join(repo_root, 'ledger', 'events.jsonl')
    if not os.path.isfile(events_path):
        return
    try:
        with open(events_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.read().splitlines()
    except OSError:
        return
    sid = _events_session_id()
    cursor_dir = os.path.join(repo_root, '.claude', 'events-cursor')
    cursor_path = os.path.join(cursor_dir, sid + '.cursor')
    seen = 0
    try:
        with open(cursor_path, 'r') as f:
            seen = int(f.read().strip() or '0')
    except (OSError, ValueError):
        seen = 0
    if seen < 0 or seen > len(lines):
        seen = 0  # stream replaced/truncated: resurface rather than lose events
    surfaced = 0
    for raw in lines[seen:]:
        text = raw.strip()
        if not text:
            continue
        try:
            rec = json.loads(text)
        except ValueError:
            continue  # malformed stream line: consumed, never surfaced
        if not isinstance(rec, dict):
            continue
        eid = rec.get('caused-by') or rec.get('instance-of') or '?'
        print('EVENT-STREAM\t{}\t{}\t{}\t{}'.format(
            rec.get('date', ''), rec.get('instance-of', ''), eid,
            rec.get('subject', '')))
        surfaced += 1
    if surfaced:
        print('EVENTS-NEW\t{}\tcursor {}->{}'.format(surfaced, seen, len(lines)))
    if len(lines) != seen:
        try:
            if not os.path.isdir(cursor_dir):
                os.makedirs(cursor_dir)
            tmp_path = cursor_path + '.tmp'
            with open(tmp_path, 'w') as f:
                f.write(str(len(lines)))
            os.replace(tmp_path, cursor_path)
        except OSError:
            pass
# <<<H-239-EVENTS-JOIN block end>>>


def run(ledger_path, hyp_dir, om_dir, repo_root):
    filenames = load_hypothesis_filenames(hyp_dir)
    om_contents = load_operating_model_contents(om_dir)
    directive_contents = load_directive_contents(repo_root)  # NEW in v4
    raw_lines = read_ledger_lines(ledger_path)

    entries = []
    decisions = {}     # v5: id -> decision rec (first occurrence wins)
    resolutions = []   # v5: decision-resolution recs, file order
    for lineno, raw in enumerate(raw_lines, start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
            if not isinstance(record, dict):
                raise ValueError('not an object')
            kind = record.get('kind', 'intent')
            if kind == 'decision':                       # v5: decision store
                if record['id'] not in decisions:
                    decisions[record['id']] = record
                continue
            if kind == 'decision-resolution':            # v5: decision store
                record['id'], record['disposition']      # shape check
                resolutions.append(record)
                continue
            if kind not in SUPPORTED_KINDS:
                raise ValueError('unsupported kind {!r}'.format(kind))
            if 'slug' in record and 'hit' in record:     # legacy shape
                date, slug, hit = record['date'], record['slug'], record['hit']
            elif 'id' in record and 'text' in record:    # v5: v2 shape normalized
                date, slug, hit = record['date'], record['id'], record['text']
                if record.get('closes_when'):
                    hit += ' [closes-when: ' + record['closes_when'] + ']'
            else:
                raise ValueError('no known shape')
        except (ValueError, KeyError, TypeError):
            print('LEDGER-WARN\tskipped malformed line {}'.format(lineno))
            continue
        entries.append((date, slug, hit, kind))

    surface_decisions(decisions, resolutions)  # v5: decisions print FIRST (head -20)

    unresolved = [
        e for e in entries
        if not is_resolved(e[3], e[1], e[2], filenames, om_contents, repo_root,
                           hyp_dir=hyp_dir, directive_contents=directive_contents)
    ]
    unresolved.sort(key=lambda e: e[0])  # oldest first, stable across all four kinds

    for date, slug, hit, kind in unresolved:
        if kind == 'amendment':
            tag = 'AMENDMENT-LEDGER'
        elif kind == 'commitment':
            tag = 'COMMITMENT-LEDGER'
        elif kind == 'directive':
            tag = 'DIRECTIVE-LEDGER'
        else:
            tag = 'INTENT-LEDGER'
        print('{}\t{}\t{}\t{}'.format(tag, date, slug, hit))
    surface_events(repo_root)  # <<<H-239-EVENTS-JOIN call>>>


def _auto_args(repo_root):
    """AUTO mode (port adaptation, hook wiring): derive the four v5 arguments from
    <repo_root>/.claude/hyp.json + the hyp defaults. Never raises."""
    cfg = {'ledger_file': 'ledger/ledger.jsonl', 'hypotheses_dir': 'hypotheses',
           'model_dir': 'operating-model'}
    try:
        with open(os.path.join(repo_root, '.claude', 'hyp.json'),
                  encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in cfg:
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    cfg[key] = val.strip().strip('/')
    except (OSError, ValueError):
        pass
    return (os.path.join(repo_root, cfg['ledger_file']),
            os.path.join(repo_root, cfg['hypotheses_dir']),
            os.path.join(repo_root, cfg['model_dir']),
            repo_root)


def _session_repo(repo):
    """hooks.json passes CLAUDE_PROJECT_DIR; a session resumed inside a linked worktree
    of that repository resolves against the worktree (hyp_config.worktree_root). Any
    failure keeps argv[1]; stdin is read only when it is not a tty."""
    try:
        if sys.stdin.isatty():
            return repo
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from hyp_config import worktree_root
        payload = json.loads(_hook_stdin_raw() or "{}")
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        return worktree_root(cwd, repo) or repo
    except Exception:
        return repo


def main(argv):
    if len(argv) == 2 and os.path.isdir(argv[1]):
        # AUTO mode: session_resolver.py <repo_root>
        ledger, hyp, om_dir, repo_root = _auto_args(os.path.abspath(_session_repo(argv[1])))
        try:
            run(ledger, hyp, om_dir, repo_root)
        except Exception:
            pass
        return 0
    if len(argv) not in (3, 4, 5):
        sys.stderr.write(
            'usage: session_resolver.py <repo_root> | <ledger_path> <hypotheses_dir> '
            '[operating_model_dir] [repo_root]\n'
        )
        return 0  # never crash; exit 0 always
    om_dir = argv[3] if len(argv) >= 4 else None
    repo_root = argv[4] if len(argv) == 5 else None
    try:
        run(argv[1], argv[2], om_dir, repo_root)
    except Exception:
        # Belt-and-suspenders: the spec requires this resolver never crash
        # and always exit 0, no matter what the ledger or dirs hold.
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
