#!/usr/bin/env python3
"""compile-run-checkpoint.py — compile one counted run into a single-file run-checkpoint.html.

A checkpoint (NAMING.md) is a single-file HTML PROJECTION of committed run bytes: the run's
results.json, grade.txt, verdict.json and the lane's hypothesis spec. The compiler copies
byte slices only and never computes a number; every numeric token and verdict word in the
rendered text must be a byte substring of results.json, grade.txt, or the spec copy, and a
render-time self-check enforces that before a single byte is written. No clock is read, no
environment is consulted, no external resource is referenced: the same sources compile to
the same bytes, so a checkpoint is regenerable after deletion and `--check`-able by digest.

Usage
    compile-run-checkpoint.py <run-dir> [--spec PATH] [--out PATH]
    compile-run-checkpoint.py <run-dir> --check [--out PATH]
    compile-run-checkpoint.py --selftest

    <run-dir>   a counted run directory (…/experiments/runs/<LANE>/run-<k>) holding
                results.json, grade.txt, verdict.json. The spec resolves by default to
                <root>/hypotheses/<LANE>-*.md where <root> is the nearest ancestor holding
                a hypotheses/ directory; --spec overrides.
    --out       write (or --check) a different path than <run-dir>/run-checkpoint.html.
                Links stay relative to <run-dir>, so the bytes do not depend on --out.
    --check     no render: recompute the source digests recorded in the checkpoint header
                and compare; exit 0 fresh, 14 stale (or header unreadable / file missing).
    --selftest  seed each corruption class in a temporary directory, observe the exit code,
                compare to the pinned table below; prints a JSON report; exit 0 iff all match.

Typed exit codes (pinned; the refusal contract)
    0   emitted                      checkpoint written (or --check fresh)
    1   internal-error               unreadable JSON, unexpected exception, bad arguments
    10  assertion-count-mismatch     spec numbered assertions != grade.txt `^(PASS|FAIL) A\\d+ ` lines
    11  verdict-tally-contradiction  those lines' PASS count / line count != RESULT line `<n>/<m>`
    12  budget-line-missing          the spec copy has no `- Budget per run:` line
    13  dangling-evidence-pointer    a file the checkpoint links to is absent (results.json,
                                     grade.txt, verdict.json, spec copy)
    14  source-digest-stale          (--check) a recorded source digest no longer matches
    15  untraceable-numeric          a RESULT-line numeric token is not a byte substring of
                                     results.json or the spec copy, or the render self-check
                                     found a rendered numeric token / verdict word that is
                                     not a byte substring of any of the three sources
Check order: 13 (all four link targets exist), 10, 11, 12, render, 15. Lines labeled
other than `A<n>` (e.g. `PASS WA …`) render in an extra row and never count.

On a successful in-place compile the compiler rewrites one declare row (schema
checkpoint-manifest/v1: path, sha256, compiler, source digests) in the lane directory's
CHECKPOINTS.json keyed by path, so recompiles stay idempotent (H-104 declare shape).
Stdlib only. Python 3.9+.
"""
import argparse
import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

COMPILER_PATH = "scripts/compile-run-checkpoint.py"
OUTPUT_NAME = "run-checkpoint.html"
MANIFEST_NAME = "CHECKPOINTS.json"
MANIFEST_SCHEMA = "checkpoint-manifest/v1"

EXIT = {
    "emitted": 0,
    "internal-error": 1,
    "assertion-count-mismatch": 10,
    "verdict-tally-contradiction": 11,
    "budget-line-missing": 12,
    "dangling-evidence-pointer": 13,
    "source-digest-stale": 14,
    "untraceable-numeric": 15,
}

# The frozen fidelity token grammar (spec Method): numeric tokens + the closed verdict-word set.
TOKEN_RE = re.compile(r"\d+(?:[.,/:%-]\d+)*%?")
VERDICT_WORDS = ("PASS", "FAIL", "keep-eligible", "keep", "kept", "discard", "discarded", "refine")
VERDICT_RE = re.compile(r"(?<![A-Za-z-])(PASS|FAIL|keep-eligible|keep|kept|discard|discarded|refine)(?![A-Za-z-])")
A_LINE_RE = re.compile(r"^(PASS|FAIL) (A\d+) (.*)$")
OTHER_LINE_RE = re.compile(r"^(PASS|FAIL) (\S+) (.*)$")
RESULT_TALLY_RE = re.compile(r"(\d+)/(\d+)")
ASSERTION_ITEM_RE = re.compile(r"^(\d+)\. (.*)$")
BUDGET_LINE_RE = re.compile(r"^\s*- Budget per run:")
DATE_KEY_RE = re.compile(r"(utc|date|time|started|when|clock)", re.I)
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HEADER_SOURCE_RE = re.compile(r"^\s*(source|spec): (\S+) sha256:([0-9a-f]{64})\s*$")

SOURCE_NAMES = ("results.json", "grade.txt", "verdict.json")


class Refusal(Exception):
    def __init__(self, cls, detail):
        Exception.__init__(self, detail)
        self.cls = cls
        self.code = EXIT[cls]
        self.detail = detail


# ----------------------------------------------------------------------------- helpers

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def find_root(run_dir):
    """Nearest ancestor of run_dir that holds a hypotheses/ directory, else None."""
    cur = os.path.abspath(run_dir)
    while True:
        if os.path.isdir(os.path.join(cur, "hypotheses")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def find_source_pin(run_dir):
    """Nearest ancestor SOURCE-PIN.json (fixture copies pin their source commit there)."""
    cur = os.path.abspath(run_dir)
    while True:
        p = os.path.join(cur, "SOURCE-PIN.json")
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def resolve_spec(run_dir, spec_arg):
    if spec_arg:
        return os.path.abspath(spec_arg)
    root = find_root(run_dir)
    if root is None:
        raise Refusal("dangling-evidence-pointer",
                      "spec copy: no hypotheses/ directory above %s" % run_dir)
    lane = os.path.basename(os.path.dirname(os.path.abspath(run_dir)))
    cands = sorted(glob.glob(os.path.join(root, "hypotheses", lane + "-*.md")))
    exact = os.path.join(root, "hypotheses", lane + ".md")
    if os.path.isfile(exact):
        cands.insert(0, exact)
    if len(cands) != 1:
        raise Refusal("dangling-evidence-pointer",
                      "spec copy: expected exactly one hypotheses/%s-*.md under %s, found %d"
                      % (lane, root, len(cands)))
    return cands[0]


def source_commit(run_dir, spec_path):
    """The commit the run bytes come from: SOURCE-PIN.json if a fixture copy, else the last
    commit touching the sources; 'uncommitted' / 'unknown' otherwise. No clock, no network."""
    pin = find_source_pin(run_dir)
    if pin:
        try:
            with open(pin, encoding="utf-8") as f:
                d = json.load(f)
            v = d.get("source_commit")
            if isinstance(v, str) and v:
                return v
        except Exception:
            pass
    try:
        top = subprocess.run(["git", "-C", run_dir, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if top.returncode != 0:
            return "unknown"
        paths = [os.path.join(run_dir, n) for n in SOURCE_NAMES] + [spec_path]
        log = subprocess.run(["git", "-C", run_dir, "log", "-n", "1", "--format=%H", "--"] + paths,
                             capture_output=True, text=True, timeout=10)
        sha = log.stdout.strip()
        return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else "uncommitted"
    except Exception:
        return "unknown"


# ----------------------------------------------------------------------------- spec parsing

def spec_sections(text):
    """{'<title>': first line, '<heading>': body text (lines between ## headings)}."""
    lines = text.split("\n")
    out = {}
    title = lines[0] if lines and lines[0].startswith("# ") else ""
    out["<title>"] = title[2:].strip() if title else ""
    cur = None
    buf = []
    for ln in lines[1:]:
        m = re.match(r"^## (.+?)\s*$", ln)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip("\n")
            cur = m.group(1)
            buf = []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        out[cur] = "\n".join(buf).strip("\n")
    return out


def strip_html_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip("\n")


def status_first_line(sections):
    body = strip_html_comments(sections.get("Status", ""))
    for ln in body.split("\n"):
        if ln.strip():
            return ln.strip()
    return ""


def budget_bullet(text):
    """The `- Budget per run:` bullet with its indented continuation lines, or None."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if BUDGET_LINE_RE.match(ln):
            out = [ln]
            for nxt in lines[i + 1:]:
                if nxt.startswith("  ") and not re.match(r"^\s*- ", nxt) and nxt.strip():
                    out.append(nxt)
                else:
                    break
            return "\n".join(out)
    return None


def numbered_assertions(section_text):
    """[(n, text-with-continuations)] from the Binary assertions section."""
    items = []
    for ln in section_text.split("\n"):
        m = ASSERTION_ITEM_RE.match(ln)
        if m:
            items.append([m.group(1), m.group(2)])
        elif items and (ln.startswith("  ") or ln.startswith("\t")) and ln.strip():
            items[-1][1] += "\n" + ln
        elif items and not ln.strip():
            continue
        elif items:
            break
    return [(n, t) for n, t in items]


# ----------------------------------------------------------------------------- grade parsing

def parse_grade(text):
    a_lines = []
    other = []
    result = None
    for ln in text.split("\n"):
        m = A_LINE_RE.match(ln)
        if m:
            a_lines.append({"word": m.group(1), "label": m.group(2), "rest": m.group(3), "line": ln})
            continue
        m2 = OTHER_LINE_RE.match(ln)
        if m2:
            other.append({"word": m2.group(1), "label": m2.group(2), "rest": m2.group(3), "line": ln})
            continue
        if result is None and ln.startswith("RESULT:"):
            result = ln
    return a_lines, other, result


# ----------------------------------------------------------------------------- results.json

def walk_scalars(obj, path=""):
    """Yield (dotted path, leaf key, scalar value) for every scalar under dicts/lists."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (path, k) if path else str(k)
            if isinstance(v, (dict, list)):
                for item in walk_scalars(v, p):
                    yield item
            else:
                yield (p, str(k), v)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                for item in walk_scalars(v, path + "[]"):
                    yield item


def consumed_fields(results):
    """State-panel rows from results.json: `reps`, wall-ish top-level keys and the scalar
    members of a `budget` dict; never any key that names a date/time."""
    rows = []
    if isinstance(results, dict):
        for k, v in results.items():
            if DATE_KEY_RE.search(str(k)):
                continue
            if k == "reps" and not isinstance(v, (dict, list)):
                rows.append((k, v))
            elif "wall" in str(k).lower() and not isinstance(v, (dict, list)):
                rows.append((k, v))
        b = results.get("budget")
        if isinstance(b, dict):
            for k, v in b.items():
                if DATE_KEY_RE.search(str(k)) or isinstance(v, (dict, list)):
                    continue
                rows.append(("budget.%s" % k, v))
    return rows


def key_paths_named(results, grade_line):
    """results.json key paths whose leaf key appears as a whole word on the grade line."""
    words = set(WORD_RE.findall(grade_line))
    seen = set()
    out = []
    for path, leaf, _v in walk_scalars(results):
        if leaf in words and path not in seen:
            seen.add(path)
            out.append(path)
    # dict-valued keys named on the line (their subtree, not a scalar) also count
    if isinstance(results, dict):
        for k in results:
            if k in words and isinstance(results[k], (dict, list)) and k not in seen:
                seen.add(k)
                out.append(k)
    return sorted(out)


# ----------------------------------------------------------------------------- fidelity

def rendered_text(html_text):
    """The text a reader sees: comments, <script>, <style>, tags stripped, entities decoded."""
    t = re.sub(r"<!--.*?-->", " ", html_text, flags=re.S)
    t = re.sub(r"<script\b.*?</script>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<style\b.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(t)


def fidelity_misses(text, source_blobs):
    """Numeric tokens and verdict words in `text` that are a byte substring of none of
    source_blobs (a list of bytes). Sorted, unique."""
    misses = set()
    for tok in TOKEN_RE.findall(text):
        b = tok.encode("utf-8")
        if not any(b in blob for blob in source_blobs):
            misses.add(tok)
    for m in VERDICT_RE.finditer(text):
        b = m.group(1).encode("utf-8")
        if not any(b in blob for blob in source_blobs):
            misses.add(m.group(1))
    return sorted(misses)


# ----------------------------------------------------------------------------- render

CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1c;--muted:#5a5a5a;--line:#d8d8d8;--card:#f5f5f5;--accent:#2a5db0;--ok:#1d7a3a;--bad:#a83232;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#e6e6e6;--muted:#a0a0a0;--line:#333;--card:#1e1e1e;--accent:#7fa7e6;--ok:#6cc784;--bad:#e07a7a}}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:64rem;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.4rem;margin:0 0 .25rem}h2{font-size:1.05rem;margin:1.6rem 0 .5rem;border-bottom:1px solid var(--line);padding-bottom:.25rem}
.sub{color:var(--muted);margin:0 0 .25rem}
blockquote{margin:.5rem 0;padding:.5rem .9rem;border-left:3px solid var(--accent);background:var(--card);white-space:pre-wrap;font-family:inherit}
pre{margin:.25rem 0;padding:.5rem .7rem;background:var(--card);border:1px solid var(--line);border-radius:4px;white-space:pre-wrap;word-break:break-word;font:13px/1.4 var(--mono)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:.8rem}
.card{border:1px solid var(--line);border-radius:6px;padding:.6rem .8rem;background:var(--card)}
.card h3{margin:0 0 .4rem;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:.92rem}th,td{border:1px solid var(--line);padding:.4rem .5rem;vertical-align:top;text-align:left}
th{background:var(--card)}td.mono,span.mono{font-family:var(--mono);font-size:.85rem}
.w-PASS{color:var(--ok);font-weight:600}.w-FAIL{color:var(--bad);font-weight:600}
.kv{margin:0;padding:0;list-style:none}.kv li{display:flex;gap:.6rem;padding:.15rem 0;border-bottom:1px dotted var(--line)}.kv li span:first-child{color:var(--muted);min-width:9rem}
nav.tabs{display:flex;flex-wrap:wrap;gap:.4rem;margin:.5rem 0}nav.tabs a{padding:.3rem .7rem;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:var(--fg);background:var(--card)}
nav.tabs a.active{border-color:var(--accent);color:var(--accent);font-weight:600}
section.tab{border:1px solid var(--line);border-radius:6px;padding:.6rem .9rem;margin:.6rem 0}
html.js section.tab{display:none}html.js section.tab.active{display:block}
a{color:var(--accent)}ul.src{padding-left:1.2rem}details summary{cursor:pointer;color:var(--muted)}
footer{margin-top:2rem;color:var(--muted);font-size:.85rem}
""".strip("\n")

JS = """
(function(){
  var root=document.documentElement;root.className+=' js';
  var want=new URLSearchParams(location.search).get('tab');
  var tabs=document.querySelectorAll('section.tab');
  var links=document.querySelectorAll('nav.tabs a');
  if(!tabs.length){return;}
  var have=false;
  tabs.forEach(function(t){if(t.getAttribute('data-tab')===want){have=true;}});
  if(!have){want=tabs[0].getAttribute('data-tab');}
  tabs.forEach(function(t){t.classList.toggle('active',t.getAttribute('data-tab')===want);});
  links.forEach(function(a){a.classList.toggle('active',a.getAttribute('data-tab')===want);});
})();
""".strip("\n")


def esc(s):
    return html.escape(s, quote=True)


def word_span(word):
    return '<span class="w-%s">%s</span>' % (esc(word), esc(word))


def build_html(ctx):
    """ctx: dict of byte slices and structure; returns the full HTML text."""
    o = []
    o.append("<!DOCTYPE html>")
    o.append("<!-- GENERATED: %s — DO NOT EDIT (checkpoint: a projection of committed run bytes;"
             " regenerate, never hand-edit)" % COMPILER_PATH)
    o.append("     run: %s" % ctx["run_label"])
    for name in SOURCE_NAMES:
        o.append("     source: %s sha256:%s" % (name, ctx["digests"][name]))
    o.append("     spec: %s sha256:%s" % (ctx["spec_rel"], ctx["digests"]["spec"]))
    o.append("     source-commit: %s" % ctx["source_commit"])
    o.append("     freshness check: python3 %s <run-dir> --check -->" % COMPILER_PATH)
    o.append('<html lang="en"><head><meta charset="utf-8">')
    o.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    o.append("<title>%s</title>" % esc(ctx["title_text"]))
    o.append("<style>%s</style></head><body><main>" % CSS)

    # --- header: lane/run label + spec title + status line
    o.append("<h1>%s</h1>" % esc(ctx["title_text"]))
    o.append('<p class="sub">%s</p>' % esc(ctx["spec_title"]))
    if ctx["status_line"]:
        o.append('<p class="sub">Spec status: <span class="mono">%s</span></p>' % esc(ctx["status_line"]))

    # --- hypothesis + variable at the top
    o.append("<h2>Hypothesis</h2>")
    o.append("<blockquote>%s</blockquote>" % esc(ctx["hypothesis"]))
    o.append("<h2>Variable under test</h2>")
    o.append("<blockquote>%s</blockquote>" % esc(ctx["variable"]))

    # --- state panel
    o.append("<h2>State</h2>")
    o.append('<div class="grid">')
    o.append('<div class="card"><h3>Budget per run (spec)</h3><pre>%s</pre></div>' % esc(ctx["budget"]))
    o.append('<div class="card"><h3>Consumed (results.json)</h3>')
    if ctx["consumed"]:
        o.append('<ul class="kv">')
        for k, v in ctx["consumed"]:
            o.append('<li><span class="mono">%s</span><span class="mono">%s</span></li>' % (esc(k), esc(v)))
        o.append("</ul>")
    else:
        o.append('<p class="sub">no reps or wall field present</p>')
    o.append("</div>")
    o.append('<div class="card"><h3>Result line (grade.txt)</h3><pre>%s</pre></div>' % esc(ctx["result_line"]))
    o.append('<div class="card"><h3>Tally of assertion lines</h3>')
    o.append('<p><span class="mono">%s</span> — ' % esc(ctx["tally"]))
    o.append(" · ".join('<span class="mono">%s</span> %s' % (esc(a["label"]), word_span(a["word"]))
                        for a in ctx["a_lines"]))
    o.append("</p></div>")
    o.append('<div class="card"><h3>Verdict rule (spec)</h3><blockquote>%s</blockquote></div>' % esc(ctx["verdict_rule"]))
    o.append("</div>")

    # --- assertion table
    o.append("<h2>Assertions</h2>")
    o.append("<table><thead><tr><th>Label</th><th>Spec assertion</th><th>Grade line (grade.txt)</th><th>Evidence</th></tr></thead><tbody>")
    for row in ctx["rows"]:
        o.append("<tr>")
        o.append('<td class="mono">%s</td>' % esc(row["label"]))
        o.append("<td>%s</td>" % (esc(row["spec_text"]) if row["spec_text"] is not None
                                  else '<span class="sub">not a spec-numbered assertion; rendered, never counted</span>'))
        if row["grade"] is not None:
            o.append('<td class="mono">%s %s %s</td>' % (word_span(row["grade"]["word"]), esc(row["grade"]["label"]), esc(row["grade"]["rest"])))
        else:
            o.append('<td class="sub">no grade line</td>')
        if row["spec_text"] is not None:
            o.append('<td><a href="?tab=%s" data-tab="%s">evidence</a></td>' % (esc(row["label"]), esc(row["label"])))
        else:
            o.append("<td></td>")
        o.append("</tr>")
    o.append("</tbody></table>")

    # --- evidence tabs
    o.append("<h2>Evidence</h2>")
    o.append('<nav class="tabs">')
    for row in ctx["rows"]:
        if row["spec_text"] is None:
            continue
        o.append('<a href="?tab=%s" data-tab="%s">%s</a>' % (esc(row["label"]), esc(row["label"]), esc(row["label"])))
    o.append("</nav>")
    for row in ctx["rows"]:
        if row["spec_text"] is None:
            continue
        o.append('<section class="tab" data-tab="%s" id="tab-%s">' % (esc(row["label"]), esc(row["label"])))
        o.append("<h3>%s</h3>" % esc(row["label"]))
        o.append("<p>Spec assertion</p><blockquote>%s</blockquote>" % esc(row["spec_text"]))
        o.append('<p>Exact grade line (<a href="%s">grade.txt</a>)</p>' % esc(ctx["links"]["grade.txt"]))
        if row["grade"] is not None:
            o.append("<pre>%s</pre>" % esc(row["grade"]["line"]))
        else:
            o.append('<p class="sub">no grade line</p>')
        o.append('<p>results.json key paths whose leaf key is named on this line (<a href="%s">results.json</a>)</p>' % esc(ctx["links"]["results.json"]))
        if row["key_paths"]:
            o.append('<details><summary>key paths (expand)</summary><ul class="kv">')
            for p in row["key_paths"]:
                o.append('<li><span class="mono">%s</span></li>' % esc(p))
            o.append("</ul></details>")
        else:
            o.append('<p class="sub">none named</p>')
        o.append("</section>")

    # --- sources
    o.append("<h2>Sources</h2>")
    o.append('<ul class="src">')
    for name in SOURCE_NAMES:
        o.append('<li><a href="%s">%s</a></li>' % (esc(ctx["links"][name]), esc(name)))
    o.append('<li><a href="%s">spec copy</a></li>' % esc(ctx["links"]["spec"]))
    o.append("</ul>")
    o.append("<footer>Every number and verdict word on this page is a byte slice of the sources above;"
             " the header comment carries their digests. Regenerate with the compiler, never edit.</footer>")
    o.append("</main><script>%s</script></body></html>" % JS)
    return "\n".join(o) + "\n"


# ----------------------------------------------------------------------------- compile

def compile_run(run_dir, spec_arg=None, out_arg=None):
    run_dir = os.path.abspath(run_dir)
    if not os.path.isdir(run_dir):
        raise Refusal("dangling-evidence-pointer", "run directory missing: %s" % run_dir)
    spec_path = resolve_spec(run_dir, spec_arg)

    # 13: every file the checkpoint links to must exist
    paths = {n: os.path.join(run_dir, n) for n in SOURCE_NAMES}
    paths["spec"] = spec_path
    for name, p in paths.items():
        if not os.path.isfile(p):
            raise Refusal("dangling-evidence-pointer", "%s missing: %s" % (name, p))

    blobs = {n: read_bytes(p) for n, p in paths.items()}
    digests = {n: sha256_bytes(b) for n, b in blobs.items()}
    try:
        results = json.loads(blobs["results.json"].decode("utf-8"))
    except Exception as e:
        raise RuntimeError("results.json unreadable: %s" % e)
    spec_text = blobs["spec"].decode("utf-8")
    grade_text = blobs["grade.txt"].decode("utf-8")

    sections = spec_sections(spec_text)
    assertions = numbered_assertions(sections.get("Binary assertions", ""))
    a_lines, other_lines, result_line = parse_grade(grade_text)

    # 10: assertion count (and label set) must agree
    labels = [a["label"] for a in a_lines]
    expected_labels = ["A%s" % n for n, _t in assertions]
    if len(a_lines) != len(assertions) or sorted(labels) != sorted(expected_labels):
        raise Refusal("assertion-count-mismatch",
                      "spec declares %d numbered assertions %s; grade.txt has %d A-labeled lines %s"
                      % (len(assertions), expected_labels, len(a_lines), labels))

    # 11: RESULT tally must equal the A-line PASS count / line count
    if result_line is None:
        raise Refusal("verdict-tally-contradiction", "grade.txt has no RESULT: line")
    m = RESULT_TALLY_RE.search(result_line)
    if not m:
        raise Refusal("verdict-tally-contradiction", "RESULT line carries no <n>/<m> tally: %r" % result_line)
    n_pass = sum(1 for a in a_lines if a["word"] == "PASS")
    if int(m.group(1)) != n_pass or int(m.group(2)) != len(a_lines):
        raise Refusal("verdict-tally-contradiction",
                      "RESULT says %s/%s but A-labeled lines are %d PASS of %d"
                      % (m.group(1), m.group(2), n_pass, len(a_lines)))
    tally = m.group(0)

    # 12: the spec's budget line must exist
    budget = budget_bullet(spec_text)
    if budget is None:
        raise Refusal("budget-line-missing", "spec copy has no `- Budget per run:` line: %s" % spec_path)

    # 15a: RESULT-line numeric tokens trace to results.json or the spec copy
    misses = fidelity_misses(result_line, [blobs["results.json"], blobs["spec"]])
    misses = [t for t in misses if TOKEN_RE.fullmatch(t)]
    if misses:
        raise Refusal("untraceable-numeric",
                      "RESULT-line tokens absent from results.json and the spec copy: %s" % misses)

    # --- assemble byte slices
    lane = os.path.basename(os.path.dirname(run_dir))
    run_name = os.path.basename(run_dir)
    run_label = "%s/%s" % (lane, run_name)
    root = find_root(run_dir)
    if root and os.path.commonpath([root, run_dir]) == root:
        run_label = os.path.relpath(run_dir, root)
    spec_rel = os.path.relpath(spec_path, run_dir)
    links = {n: n for n in SOURCE_NAMES}
    links["spec"] = spec_rel

    rows = []
    by_label = {a["label"]: a for a in a_lines}
    for n, text in assertions:
        label = "A%s" % n
        g = by_label.get(label)
        rows.append({"label": label, "spec_text": text, "grade": g,
                     "key_paths": key_paths_named(results, g["line"]) if g else []})
    for x in other_lines:
        rows.append({"label": x["label"], "spec_text": None, "grade": x, "key_paths": []})

    consumed = [(k, json.dumps(v)) for k, v in consumed_fields(results)]

    ctx = {
        "run_label": run_label,
        "title_text": "%s %s checkpoint" % (lane, run_name),
        "spec_title": sections.get("<title>", ""),
        "status_line": status_first_line(sections),
        "hypothesis": strip_html_comments(sections.get("Hypothesis", "")),
        "variable": strip_html_comments(sections.get("Variable under test", "")),
        "budget": budget,
        "consumed": consumed,
        "result_line": result_line,
        "tally": tally,
        "a_lines": a_lines,
        "verdict_rule": strip_html_comments(sections.get("Verdict rule", "")),
        "rows": rows,
        "links": links,
        "digests": digests,
        "spec_rel": spec_rel,
        "source_commit": source_commit(run_dir, spec_path),
    }
    out_text = build_html(ctx)

    # 15b: render-time self-check over the rendered text against the three sources
    misses = fidelity_misses(rendered_text(out_text),
                             [blobs["results.json"], blobs["grade.txt"], blobs["spec"]])
    if misses:
        raise Refusal("untraceable-numeric",
                      "render self-check: rendered tokens absent from every source: %s" % misses)

    out_path = os.path.abspath(out_arg) if out_arg else os.path.join(run_dir, OUTPUT_NAME)
    out_bytes = out_text.encode("utf-8")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ckpt-", dir=os.path.dirname(out_path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(out_bytes)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    if not out_arg:
        write_manifest_row(run_dir, out_path, out_bytes, digests, spec_rel, ctx["source_commit"])
    return out_path


def write_manifest_row(run_dir, out_path, out_bytes, digests, spec_rel, commit):
    """Idempotent declare row in the lane's CHECKPOINTS.json (keyed by path). Advisory: a
    failure here never changes the compile's exit."""
    try:
        lane_dir = os.path.dirname(run_dir)
        mp = os.path.join(lane_dir, MANIFEST_NAME)
        data = {"schema": MANIFEST_SCHEMA, "rows": {}}
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and loaded.get("schema") == MANIFEST_SCHEMA \
                    and isinstance(loaded.get("rows"), dict):
                data = loaded
        key = os.path.relpath(out_path, lane_dir)
        data["rows"][key] = {
            "path": key,
            "sha256": sha256_bytes(out_bytes),
            "compiler": COMPILER_PATH,
            "sources": {n: digests[n] for n in SOURCE_NAMES},
            "spec": spec_rel,
            "spec_sha256": digests["spec"],
            "source_commit": commit,
        }
        data["rows"] = dict(sorted(data["rows"].items()))
        tmp = mp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, mp)
    except Exception as e:
        sys.stderr.write("checkpoint-manifest advisory: %s\n" % e)


# ----------------------------------------------------------------------------- check

def check_run(run_dir, out_arg=None):
    run_dir = os.path.abspath(run_dir)
    path = os.path.abspath(out_arg) if out_arg else os.path.join(run_dir, OUTPUT_NAME)
    if not os.path.isfile(path):
        raise Refusal("source-digest-stale", "no checkpoint at %s" % path)
    data = read_bytes(path)
    head = data[:4096].decode("utf-8", "replace")
    if "-->" not in head:
        raise Refusal("source-digest-stale", "header comment truncated or missing in %s" % path)
    header = head.split("-->", 1)[0]
    recorded = {}
    for ln in header.split("\n"):
        m = HEADER_SOURCE_RE.match(ln)
        if m:
            recorded[m.group(2)] = m.group(3)
    if not all(n in recorded for n in SOURCE_NAMES) or len(recorded) != len(SOURCE_NAMES) + 1:
        raise Refusal("source-digest-stale", "header carries an incomplete digest block: %s" % sorted(recorded))
    stale = []
    for name, sha in sorted(recorded.items()):
        p = os.path.join(run_dir, name)
        if not os.path.isfile(p):
            stale.append("%s missing" % name)
            continue
        cur = sha256_bytes(read_bytes(p))
        if cur != sha:
            stale.append("%s recorded %s now %s" % (name, sha[:12], cur[:12]))
    if stale:
        raise Refusal("source-digest-stale", "; ".join(stale))
    return path


# ----------------------------------------------------------------------------- selftest

SELFTEST_SPEC = """# H-999-selftest-lane: a synthetic spec for the compiler's seeded-violation self-test

## Status
draft (synthetic; exists only inside --selftest)
Claim type: normative

## Hypothesis
A synthetic hypothesis sentence for the self-test lane.

## Variable under test
Exactly one: the synthetic toggle.

## Method
1. Step one.
- Budget per run: <= 5 min wall-clock, $0, zero headless sessions; halt on breach.
- Repetitions per arm: 3.

## Binary assertions
1. First synthetic assertion.
2. Second synthetic assertion.
3. Third synthetic assertion.
4. Fourth synthetic assertion.
5. Fifth synthetic assertion.

## Verdict rule
Keep if 5/5 assertions pass in 2 consecutive counted runs; otherwise discard after 3 failed runs.

## Runs
| # | Date | Assertions passed | Journal entry |
|---|------|-------------------|---------------|
"""

SELFTEST_GRADE = """PASS A1 first-check: synthetic evidence, 3/3 reps
PASS A2 second-check: synthetic evidence, 3/3 reps
PASS A3 third-check: synthetic evidence, wall 12.5s
PASS A4 fourth-check: synthetic evidence
PASS A5 fifth-check: synthetic evidence
PASS WA extra-leg: an extra-labeled line that renders but never counts
RESULT: 5/5 keep-eligible (wall 12.5s, reps 3)
"""

SELFTEST_RESULTS = {"reps": 3, "wall_s": 12.5, "results": {"baseline": {"rc": 0}, "treatment": {"rc": 2}}}
SELFTEST_VERDICT = {"passed": 5, "of": 5, "keep_eligible": True}

# The pinned corruption table the self-test seeds (mirrors fixture/corruptions/EXPECTED.json).
SELFTEST_CLASSES = [
    ("c1", "assertion-count-mismatch", "compile"),
    ("c2", "verdict-tally-contradiction", "compile"),
    ("c3", "budget-line-missing", "compile"),
    ("c4", "dangling-evidence-pointer", "compile"),
    ("c5", "source-digest-stale", "check"),
    ("c6", "untraceable-numeric", "compile"),
]


def _selftest_tree(base):
    spec_dir = os.path.join(base, "hypotheses")
    run_dir = os.path.join(base, "experiments", "runs", "H-999", "run-1")
    os.makedirs(spec_dir)
    os.makedirs(run_dir)
    with open(os.path.join(spec_dir, "H-999-selftest-lane.md"), "w", encoding="utf-8") as f:
        f.write(SELFTEST_SPEC)
    with open(os.path.join(run_dir, "grade.txt"), "w", encoding="utf-8") as f:
        f.write(SELFTEST_GRADE)
    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(SELFTEST_RESULTS, f, indent=1)
        f.write("\n")
    with open(os.path.join(run_dir, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(SELFTEST_VERDICT, f, indent=1)
        f.write("\n")
    return run_dir


def _seed(cls, run_dir):
    spec = os.path.join(find_root(run_dir), "hypotheses", "H-999-selftest-lane.md")
    gpath = os.path.join(run_dir, "grade.txt")
    if cls == "c1":
        lines = open(gpath, encoding="utf-8").read().split("\n")
        lines = [l for l in lines if not l.startswith("PASS A4 ")]
        open(gpath, "w", encoding="utf-8").write("\n".join(lines))
    elif cls == "c2":
        t = open(gpath, encoding="utf-8").read().replace("PASS A3 ", "FAIL A3 ", 1)
        open(gpath, "w", encoding="utf-8").write(t)
    elif cls == "c3":
        lines = open(spec, encoding="utf-8").read().split("\n")
        lines = [l for l in lines if not BUDGET_LINE_RE.match(l)]
        open(spec, "w", encoding="utf-8").write("\n".join(lines))
    elif cls == "c4":
        os.unlink(os.path.join(run_dir, "verdict.json"))
    elif cls == "c5":
        t = open(spec, encoding="utf-8").read().replace("draft (synthetic", "KEPT (synthetic", 1)
        open(spec, "w", encoding="utf-8").write(t)
    elif cls == "c6":
        t = open(gpath, encoding="utf-8").read().replace("(wall 12.5s, reps 3)", "(wall 12.5s, probe 77777, reps 3)", 1)
        open(gpath, "w", encoding="utf-8").write(t)


def _observe(fn, *args, **kw):
    try:
        fn(*args, **kw)
        return 0, ""
    except Refusal as r:
        return r.code, r.detail
    except Exception as e:
        return EXIT["internal-error"], "%s: %s" % (type(e).__name__, e)


def selftest():
    base = tempfile.mkdtemp(prefix="ckpt-selftest-")
    report = {"classes": {}, "clean": {}, "ok": True}
    try:
        clean = _selftest_tree(os.path.join(base, "clean"))
        rc, detail = _observe(compile_run, clean)
        rc2, detail2 = _observe(check_run, clean) if rc == 0 else (None, "skipped")
        first = read_bytes(os.path.join(clean, OUTPUT_NAME)) if rc == 0 else b""
        rc3, detail3 = _observe(compile_run, clean, None, os.path.join(base, "clean-again.html")) if rc == 0 else (None, "skipped")
        again = read_bytes(os.path.join(base, "clean-again.html")) if rc3 == 0 else b""
        report["clean"] = {"compile": rc, "check": rc2, "recompile": rc3,
                           "byte_identical": bool(first) and first == again,
                           "detail": detail or detail2 or detail3}
        if rc != 0 or rc2 != 0 or rc3 != 0 or first != again:
            report["ok"] = False
        for cls, name, stage in SELFTEST_CLASSES:
            rd = _selftest_tree(os.path.join(base, cls))
            expected = EXIT[name]
            if stage == "compile":
                _seed(cls, rd)
                observed, detail = _observe(compile_run, rd)
                html_written = os.path.exists(os.path.join(rd, OUTPUT_NAME))
                ok = observed == expected and not html_written
            else:
                pre, _d = _observe(compile_run, rd)
                _seed(cls, rd)
                observed, detail = _observe(check_run, rd)
                html_written = os.path.exists(os.path.join(rd, OUTPUT_NAME))
                ok = pre == 0 and observed == expected
            report["classes"][cls] = {"class": name, "stage": stage, "expected": expected,
                                      "observed": observed, "html_written": html_written,
                                      "ok": ok, "detail": detail}
            if not ok:
                report["ok"] = False
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["ok"] else 1


# ----------------------------------------------------------------------------- main

def main(argv):
    ap = argparse.ArgumentParser(description="compile one counted run into run-checkpoint.html")
    ap.add_argument("run_dir", nargs="?", help="counted run directory")
    ap.add_argument("--spec", default=None, help="spec copy path (default: <root>/hypotheses/<LANE>-*.md)")
    ap.add_argument("--out", default=None, help="alternate output path (links stay run-dir relative)")
    ap.add_argument("--check", action="store_true", help="digest freshness check; exit 0 fresh / 14 stale")
    ap.add_argument("--selftest", action="store_true", help="seed the six corruption classes; exit 0 iff codes match")
    o = ap.parse_args(argv)
    if o.selftest:
        return selftest()
    if not o.run_dir:
        ap.print_usage(sys.stderr)
        return EXIT["internal-error"]
    try:
        if o.check:
            p = check_run(o.run_dir, o.out)
            print("fresh %s" % p)
            return 0
        p = compile_run(o.run_dir, o.spec, o.out)
        print("emitted %s" % p)
        return 0
    except Refusal as r:
        sys.stderr.write("REFUSED %d %s: %s\n" % (r.code, r.cls, r.detail))
        return r.code
    except Exception as e:
        sys.stderr.write("INTERNAL-ERROR %s: %s\n" % (type(e).__name__, e))
        return EXIT["internal-error"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
