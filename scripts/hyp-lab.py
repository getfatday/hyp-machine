#!/usr/bin/env python3
"""hyp-lab.py -- one named entry point for the read-only lab instruments this plugin ships.

A consumer that installed the plugin gets seven subcommands, one per `make lab-*` target of
the reference consumer layer (product-portfolio), without locating the plugin cache by glob
or keeping a lab.py of its own:

  subcommand   replaces (consumer Makefile)   runs
  status       make lab-status               scripts/dispatch-status.py (+ one DRAFTS line)
  stalls       make lab-stalls               scripts/stall-signals.py (root = main checkout)
  preflight    make lab-preflight SPEC=      the repo's preflight_file, else scripts/preflight.py
  recent       make lab-recent               built-in: newest N journal fragments with titles
  prior        make lab-prior [TOPIC=|ID=]   built-in: spec list, topic search, id lineage, --sweep
  mine         make lab-mine [ALL=1]         built-in: open specs by git authorship
  next-id      make lab-next-id              built-in: H-148 draft handle (never a numeric next-free)

Root: --root, else CLAUDE_PROJECT_DIR, else the working directory. Paths come from the
repository's hyp config file through hooks/scripts/hyp_config.py (defaults when absent);
status words are read through hooks/scripts/hyp_status.py, the plugin's one status canon
(H-304), so `open` means canonical draft | active | refine.

Writes nothing under the repository: temp files live under tempfile.mkdtemp and are removed
on exit; `next-id` never fetches. Every dispatched script's wall is reported on stderr as
`hyp-lab: <script> rc=<n> wall=<s>s`. Exit code = the dispatched script's; 2 on usage.
Stdlib only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "scripts"))
import hyp_config  # noqa: E402
from hyp_config import load_config  # noqa: E402
from hyp_status import canonical_status, status_line  # noqa: E402

OPEN = ("draft", "active", "refine")
SPEC_RE = re.compile(r"^(H-(?:DRAFT-[0-9a-f]{8}|\d+))-(.+)\.md$")
NUM_RE = re.compile(r"^H-(\d+)-")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

MAPPING = [
    ("status", "make lab-status", "scripts/dispatch-status.py"),
    ("stalls", "make lab-stalls", "scripts/stall-signals.py"),
    ("preflight", "make lab-preflight SPEC=<spec>", "preflight_file, else scripts/preflight.py"),
    ("recent", "make lab-recent", "built-in"),
    ("prior", "make lab-prior [TOPIC=|ID=]", "built-in"),
    ("mine", "make lab-mine [ALL=1]", "built-in"),
    ("next-id", "make lab-next-id", "built-in"),
]

_TMP = []


def tmpdir():
    d = tempfile.mkdtemp(prefix="hyp-lab-")
    _TMP.append(d)
    return d


def cleanup():
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def git(root, *args, timeout=60):
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                              check=False, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def dispatch(label, argv, cwd):
    """Run a shipped script, stream its stdout through, report rc and wall on stderr."""
    t0 = time.monotonic()
    p = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=cwd)
    wall = time.monotonic() - t0
    sys.stderr.write(p.stderr)
    sys.stderr.write("hyp-lab: %s rc=%d wall=%.3fs\n" % (label, p.returncode, wall))
    return p


class Ctx:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.cfg = load_config(self.root)
        self.hyp = os.path.join(self.root, self.cfg["hypotheses_dir"])
        self.runs = os.path.join(self.root, self.cfg["runs_dir"])
        self.frags = os.path.join(self.root, self.cfg["journal_dir"])
        self.research = os.path.dirname(os.path.join(self.root, self.cfg["notes_dir"]))

    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def draft_count(self):
        try:
            return sum(1 for f in os.listdir(self.hyp) if f.startswith("H-DRAFT-") and f.endswith(".md"))
        except OSError:
            return 0

    def specs(self):
        out = []
        try:
            names = sorted(os.listdir(self.hyp))
        except OSError:
            return out
        for fn in names:
            m = SPEC_RE.match(fn)
            if not m:
                continue
            text = read(os.path.join(self.hyp, fn))
            title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), fn)
            line = status_line(text)
            canon = canonical_status(line) if line else None
            num = NUM_RE.match(fn)
            out.append({"id": m.group(1), "num": int(num.group(1)) if num else 10 ** 9, "slug": m.group(2),
                        "file": fn, "rel": self.cfg["hypotheses_dir"] + "/" + fn,
                        "status": canon or "(none)", "title": title, "text": text})
        out.sort(key=lambda s: (s["num"], s["id"], s["slug"]))
        return out


def is_open(s):
    return s["status"] in OPEN


def id_counts(specs):
    counts = {}
    for s in specs:
        counts[s["id"]] = counts.get(s["id"], 0) + 1
    return counts


def run_dirs_for(ctx, s, shared):
    if not os.path.isdir(ctx.runs):
        return [], []
    exact, ambiguous = [], []
    for d in sorted(os.listdir(ctx.runs)):
        if d == "%s-%s" % (s["id"], s["slug"]):
            exact.append(d)
        elif d == s["id"]:
            (ambiguous if shared else exact).append(d)
    return exact, ambiguous


# ---- dispatching subcommands ---------------------------------------------------------------

def cmd_status(ctx, a):
    argv = [sys.executable, os.path.join(PLUGIN, "scripts", "dispatch-status.py"), "--root", ctx.root]
    if a.json:
        argv.append("--json")
    if a.at:
        argv += ["--at", a.at]
    p = dispatch("scripts/dispatch-status.py", argv, ctx.root)
    n = ctx.draft_count()
    if a.json and p.returncode == 0:
        try:
            doc = json.loads(p.stdout)
            doc["drafts"] = n
            print(json.dumps(doc, indent=1, sort_keys=True))
            return p.returncode
        except ValueError:
            pass
    sys.stdout.write(p.stdout)
    if not a.json:
        print("DRAFTS: %d draft-handle spec(s) awaiting land (H-DRAFT-*.md are not listed by dispatch "
              "until scripts/id-rectify.py allocates their id)" % n)
    return p.returncode


def main_checkout(root):
    """The main checkout of the repository containing root (stall-signals refuses a linked
    worktree, whose .git is a file): the Makefile's `git rev-parse --git-common-dir` rule,
    read the way hyp_config reads it -- the .git pointer file and its `commondir`, no git
    subprocess (a `git rev-parse` measured 0.2-0.8 s on a loaded host); git is the fallback."""
    common = hyp_config._common_dir(os.path.realpath(root))
    if not common:
        common = git(root, "rev-parse", "--git-common-dir").strip()
        if not common:
            return root
        if not os.path.isabs(common):
            common = os.path.join(root, common)
        common = os.path.normpath(common)
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    return root


def cmd_stalls(ctx, a):
    argv = [sys.executable, os.path.join(PLUGIN, "scripts", "stall-signals.py"), "--root", main_checkout(ctx.root)]
    if a.json:
        argv.append("--json")
    if a.now:
        argv += ["--now", a.now]
    p = dispatch("scripts/stall-signals.py", argv, ctx.root)
    sys.stdout.write(p.stdout)
    return p.returncode


def cmd_preflight(ctx, a):
    repo_pf = os.path.join(ctx.root, ctx.cfg["preflight_file"])
    if not a.shipped and os.path.isfile(repo_pf):
        which, script = "repository " + ctx.cfg["preflight_file"], repo_pf
    else:
        which, script = "shipped scripts/preflight.py", os.path.join(PLUGIN, "scripts", "preflight.py")
    sys.stderr.write("hyp-lab: preflight via %s\n" % which)
    p = dispatch(which.split()[-1], [sys.executable, script, a.spec], ctx.root)
    sys.stdout.write(p.stdout)
    return p.returncode


# ---- built-in subcommands ------------------------------------------------------------------

def version_key(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def cmd_recent(ctx, a):
    try:
        names = [f for f in os.listdir(ctx.frags) if f.endswith(".md")]
    except OSError:
        print("recent: no journal directory at %s" % ctx.rel(ctx.frags))
        return 1
    for fn in sorted(names, key=version_key)[-a.n:]:
        text = read(os.path.join(ctx.frags, fn))
        title = next((ln[2:] for ln in text.splitlines() if ln.startswith("# ")), "")
        print("%s  %s" % (fn, title))
    return 0


def prior_list(ctx, specs):
    counts = id_counts(specs)
    for s in specs:
        flag = "  shared-id" if counts[s["id"]] > 1 else ""
        print("%-7s %-14s %s%s" % (s["id"], s["status"], s["slug"], flag))
    shared = sum(1 for v in counts.values() if v > 1)
    print("\n%d specs, %d open, %d ids shared by more than one file"
          % (len(specs), sum(1 for s in specs if is_open(s)), shared))
    return 0


def md_files(ctx):
    for base in (ctx.hyp, ctx.research, ctx.runs):
        for dirpath, dns, files in os.walk(base):
            dns[:] = [d for d in dns if d != ".git"]
            for fn in files:
                if fn.endswith(".md"):
                    yield os.path.join(dirpath, fn)


def prior_topic(ctx, specs, topic):
    terms = [t.lower() for t in topic.split() if t.strip()]
    if not terms:
        print("usage: hyp-lab.py prior --topic \"word word\"")
        return 2
    by_rel = {s["rel"]: s for s in specs}
    hits = {"all": [], "any": []}
    for path in md_files(ctx):
        low = read(path).lower()
        found = [t for t in terms if t in low]
        if len(found) == len(terms):
            hits["all"].append(ctx.rel(path))
        elif found:
            hits["any"].append(ctx.rel(path))
    mode, rows = ("every term", hits["all"]) if hits["all"] else ("some terms", hits["any"])
    print("prior work for: %s  (matched %s)\n" % (" ".join(terms), mode))
    groups = {"Specs": [], "Notes, pages, raw": [], "Run-dir documents": []}
    research_rel = ctx.rel(ctx.research) + "/"
    for rel in sorted(rows):
        if rel in by_rel:
            s = by_rel[rel]
            groups["Specs"].append("  %-7s %-14s %s" % (s["id"], s["status"], rel))
        elif rel.startswith(research_rel):
            groups["Notes, pages, raw"].append("  " + rel)
        else:
            groups["Run-dir documents"].append("  " + rel)
    for name, lines in groups.items():
        if lines:
            print("%s (%d)" % (name, len(lines)))
            print("\n".join(lines))
            print()
    if not rows:
        print("nothing found. Try fewer or different terms, or `hyp-lab.py prior` for the full list.")
    return 0


def prior_id(ctx, specs, wanted):
    wanted = wanted.upper() if wanted.upper().startswith("H-") else "H-" + wanted
    mine = [s for s in specs if s["id"] == wanted]
    if not mine:
        print("%s: no spec file. `hyp-lab.py prior` lists every id." % wanted)
        return 1
    shared = len(mine) > 1
    ref_re = re.compile(r"\b%s\b" % re.escape(wanted))
    for s in mine:
        print("%s  %s  %s\n  %s" % (s["id"], s["status"], s["rel"], s["title"]))
        exact, ambiguous = run_dirs_for(ctx, s, shared)
        runs_rel = ctx.cfg["runs_dir"] + "/"
        print("  run dirs: %s" % (", ".join(runs_rel + d for d in exact) if exact else "none attributable"))
        for d in ambiguous:
            print("  shared-id run dir, not attributable to one spec: %s%s" % (runs_rel, d))
        keep = []
        if "## On keep" in s["text"]:
            body = s["text"].split("## On keep", 1)[-1].split("\n## ", 1)[0]
            keep = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("-")]
        if keep:
            print("  on keep:")
            for ln in keep[:6]:
                print("    %s" % ln[:110])
    if shared:
        print("\nnote: %d spec files share %s; referrers and fragments below match the id, not one spec"
              % (len(mine), wanted))
    refs = [o for o in specs if o["id"] != wanted and ref_re.search(o["text"])]
    print("\nspecs that reference %s (%d)" % (wanted, len(refs)))
    for o in refs:
        ctxline = next((ln.strip() for ln in o["text"].splitlines() if ref_re.search(ln)), "")
        print("  %-7s %-14s %s" % (o["id"], o["status"], ctxline[:95]))
    frags = []
    if os.path.isdir(ctx.frags):
        frags = sorted((fn for fn in os.listdir(ctx.frags)
                        if fn.endswith(".md") and ref_re.search(read(os.path.join(ctx.frags, fn)))),
                       key=version_key)
    frag_rel = ctx.cfg["journal_dir"] + "/"
    print("\njournal fragments mentioning %s (%d)" % (wanted, len(frags)))
    for fn in frags[:15]:
        print("  %s%s" % (frag_rel, fn))
    if len(frags) > 15:
        print("  ... %d more" % (len(frags) - 15))
    return 0


def prior_sweep(ctx, draft):
    index = os.path.join(ctx.root, "research", "findings-index.md")
    if not os.path.isfile(index):
        index = os.path.join(tmpdir(), "findings-index.md")
        p = dispatch("scripts/compile-findings-index.py",
                     [sys.executable, os.path.join(PLUGIN, "scripts", "compile-findings-index.py"),
                      "--repo", ctx.root, "--out", index], ctx.root)
        if p.returncode != 0:
            sys.stdout.write(p.stdout)
            return p.returncode
    p = dispatch("scripts/prior-art-sweep.py",
                 [sys.executable, os.path.join(PLUGIN, "scripts", "prior-art-sweep.py"), draft,
                  "--repo", ctx.root, "--index", index, "--threshold", "0.25"], ctx.root)
    sys.stdout.write(p.stdout)
    return p.returncode


def cmd_prior(ctx, a):
    specs = ctx.specs()
    if a.sweep:
        return prior_sweep(ctx, a.sweep)
    if a.id:
        return prior_id(ctx, specs, a.id)
    if a.topic:
        return prior_topic(ctx, specs, a.topic)
    return prior_list(ctx, specs)


def author_map(ctx, extra=()):
    """path -> author emails (newest first) from one git log pass over the two lab dirs."""
    out, current = {}, None
    log = git(ctx.root, "log", *extra, "--name-only", "--format=%x00%ae", "--",
              ctx.cfg["hypotheses_dir"] + "/", ctx.cfg["runs_dir"] + "/", timeout=300)
    for ln in log.splitlines():
        if ln.startswith("\x00"):
            current = ln[1:].strip()
        elif ln.strip() and current:
            out.setdefault(ln.strip(), []).append(current)
    return out


def cmd_mine(ctx, a):
    specs = ctx.specs()
    me = git(ctx.root, "config", "user.email").strip()
    touched = author_map(ctx)
    added = author_map(ctx, ("--diff-filter=A",))
    counts = id_counts(specs)
    rows = []
    for s in specs:
        if not is_open(s):
            continue
        authors = set(touched.get(s["rel"], []))
        exact, _ = run_dirs_for(ctx, s, counts[s["id"]] > 1)
        for d in exact:
            prefix = "%s/%s/" % (ctx.cfg["runs_dir"], d)
            for path, auths in touched.items():
                if path.startswith(prefix):
                    authors.update(auths)
        registrar = (added.get(s["rel"]) or [None])[-1]
        role = "registered" if registrar == me else ("contributed" if me in authors else "")
        rows.append((s, role, len(authors)))
    shown = rows if a.all else [r for r in rows if r[1]]
    header = "every open spec" if a.all else "open specs you registered or contributed to"
    print("%s (ownership from git authors on the spec file and its attributable run dirs)\n" % header)
    for s, role, n in shown:
        print("%-7s %-10s %-11s %d contributor%s  %s" % (s["id"], s["status"], role, n, "" if n == 1 else "s", s["slug"]))
    if not shown:
        print("none. See everything with: hyp-lab.py mine --all")
    print("\n%d open specs in total" % len(rows))
    return 0


def landed_ref(ctx):
    for ref in ("origin/HEAD", "main", "HEAD"):
        if git(ctx.root, "rev-parse", "--verify", "-q", ref + "^{commit}").strip():
            return ref
    return None


def cmd_next_id(ctx, a):
    if not SLUG_RE.match(a.slug or ""):
        print("usage: hyp-lab.py next-id --slug <lowercase-slug> [--body <file>]", file=sys.stderr)
        return 2
    body = b""
    if a.body:
        try:
            with open(a.body, "rb") as fh:
                body = fh.read()
        except OSError as e:
            print("next-id: cannot read body: %s" % e, file=sys.stderr)
            return 2
    try:
        branch = subprocess.run(["git", "-C", ctx.root, "branch", "--show-current"], capture_output=True,
                                check=False, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        branch = b""
    minute = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    # the hypothesis skill's shell recipe, byte for byte:
    #   { cat <body>; git branch --show-current; date -u +%Y-%m-%dT%H:%M; } | shasum -a 256 | cut -c1-8
    hash8 = hashlib.sha256(body + branch + (minute + "\n").encode()).hexdigest()[:8]
    print("H-DRAFT-%s-%s" % (hash8, a.slug))
    print("branch: %s" % (branch.decode(errors="replace").strip() or "(detached HEAD; empty branch line)"))
    print("minute: %s" % minute)
    ref = landed_ref(ctx)
    highest = 0
    if ref:
        tree = git(ctx.root, "ls-tree", "-r", "--name-only", ref, "--", ctx.cfg["hypotheses_dir"] + "/")
        nums = [int(m.group(1)) for m in re.finditer(r"/H-(\d+)-", tree)]
        highest = max(nums, default=0)
    print("rule: draft handle (H-148 draft-then-allocate): register under this name; scripts/id-rectify.py "
          "allocates the canonical numeric id at land. Information only: highest landed id on %s is H-%03d "
          "(ref order origin/HEAD, main, HEAD; no fetch was run)." % (ref or "(no ref)", highest))
    return 0


# ---- CLI -----------------------------------------------------------------------------------

def build_parser():
    epilog = ["subcommand -> consumer Makefile target it replaces -> runs"]
    for sub, target, runs in MAPPING:
        epilog.append("  %-10s %-32s %s" % (sub, target, runs))
    ap = argparse.ArgumentParser(prog="hyp-lab.py", description=__doc__.splitlines()[0],
                                 epilog="\n".join(epilog), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="repository root (default: CLAUDE_PROJECT_DIR, else cwd)")
    sp = ap.add_subparsers(dest="cmd", metavar="<subcommand>")
    p = sp.add_parser("status", help="dispatch surface (scripts/dispatch-status.py) + DRAFTS line; replaces make lab-status")
    p.add_argument("--json", action="store_true")
    p.add_argument("--at", default=None)
    p = sp.add_parser("stalls", help="stall signals (scripts/stall-signals.py); replaces make lab-stalls")
    p.add_argument("--json", action="store_true")
    p.add_argument("--now", default=None)
    p = sp.add_parser("preflight", help="spec preflight (repository preflight_file, else scripts/preflight.py); replaces make lab-preflight SPEC=")
    p.add_argument("spec")
    p.add_argument("--shipped", action="store_true", help="force the plugin's scripts/preflight.py")
    p = sp.add_parser("recent", help="newest journal fragments with titles (built-in); replaces make lab-recent")
    p.add_argument("-n", type=int, default=15)
    p = sp.add_parser("prior", help="spec list | --topic | --id | --sweep (built-in); replaces make lab-prior [TOPIC=|ID=]")
    p.add_argument("--topic", default=None)
    p.add_argument("--id", default=None)
    p.add_argument("--sweep", default=None, metavar="DRAFT_SPEC",
                   help="compile-findings-index.py + prior-art-sweep.py --threshold 0.25 over a draft spec")
    p = sp.add_parser("mine", help="open specs you registered or contributed to (built-in); replaces make lab-mine [ALL=1]")
    p.add_argument("--all", action="store_true")
    p = sp.add_parser("next-id", help="H-148 draft handle from the hypothesis skill's recipe (built-in); replaces make lab-next-id")
    p.add_argument("--slug", required=True)
    p.add_argument("--body", default=None)
    return ap


def main(argv):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    root = a.root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not os.path.isdir(root):
        print("hyp-lab: root %s is not a directory" % root, file=sys.stderr)
        return 2
    ctx = Ctx(root)
    fn = {"status": cmd_status, "stalls": cmd_stalls, "preflight": cmd_preflight, "recent": cmd_recent,
          "prior": cmd_prior, "mine": cmd_mine, "next-id": cmd_next_id}[a.cmd]
    try:
        return fn(ctx, a)
    finally:
        cleanup()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
