#!/usr/bin/env python3
"""id-rectify.py — hypothesis-id collision lint + rectifier (identity contract §3;
built under lab H-147, extended and kept under lab H-148 2x5/5; PORTED to the shipped
hyp plugin tree by H-259 — shipping home: scripts/id-rectify.py).

Mechanism (byte-faithful to the kept H-148 tool):
  (a) collision renumber: a head-added spec binding an id already bound at base with a
      different blob renumbers to max+1 (incoming side only); tokens rewrite on
      head-authored lines only; base-slug tokens never rewrite.
  (b) draft-spec allocation (draft-then-allocate): head-added
      <hypotheses>/H-DRAFT-<hash8>-<slug>.md renames to H-NNN-<slug>.md at land —
      rectification IS the allocation; H-DRAFT tokens rewrite on head-authored lines.
  (c) draft dedupe: a draft spec byte-identical to a base spec is the same spec arriving
      twice — incoming copy removed, alias recorded, no allocation.
  (d) fragment-id land-time allocation: incoming fragments (blob path absent at base)
      whose id collides with a base id or an earlier incoming one renumber to next-free
      (filename prefix + `id:` line, filename-sort order).
Refusals (scanned before any write): the directives file (program.md) in the diff; a
collision id or draft handle cited on head-added lines of a LANDED fragment or the frozen
journal file; mixed-binding lines. Exit codes: 0 repaired or no-op, 1 lint-blocked,
2 usage/tool error, 3 refusal (tree untouched).

PORT ADAPTATIONS (H-259; each conditional keeps lab behavior when the surface exists):
  P1 paths come from .claude/hyp.json when present (hypotheses_dir, journal_dir,
     journal_file, runs_dir, compiled_file); defaults = the lab layout.
  P2 spec ids accept 3-4 digits (H-NNN / H-NNNN); allocation zero-pads to 3.
  P3 optional repo surfaces: the id-alias ledger (ledger/work-ledger.jsonl), the H-104
     fidelity manifest (scripts/fidelity-manifest.py + manifests/), and the derived-view
     regens (scripts/compile-journal.py, scripts/compile-dashboard.py) run only when the
     surface exists in the consumer repository; every skip is recorded in the JSON output
     and the rectification report. Renames without a manifest tool execute as plain
     byte-preserving renames.
  P4 dates: git commit dates honor GIT_AUTHOR_DATE/GIT_COMMITTER_DATE when the caller
     sets them (unchanged), otherwise git's own now; alias rows carry today's UTC date
     instead of the lab's frozen fixture date.

    id-rectify.py --repo <clone> --base <ref> --head <ref>          # repair mode
    id-rectify.py --repo <clone> --base <ref> --head <ref> --lint   # lint mode (no writes)

P8 (v2 lane run-1 refine, 2026-09-04): a draft handle in the FILENAME of a head-added
non-spec file (fragment, note) is rewritten to the allocated id at land, composed with any
fragment renumber, so no handle survives in a filename (lint: DRAFT-HANDLE-SURVIVES).

P6 (H-272 run-1 refine, 2026-09-04): an INCOMING fragment (blob not at base) whose filename
has no integer prefix or whose frontmatter has no integer `id:` line — one weak-tier registrar
named its fragment `DRAFT-<hash8>-...md` — is allocated the next free integer at land:
renamed to `NNNN-<rest>.md` (a leading `[H-]DRAFT-<hash8>-` is dropped from <rest>), its
`id:` line set or inserted, alias-free (no old id existed) but reported. Lint mode gains the
finding class FRAGMENT-WITHOUT-INTEGER-ID for the same surface; otherwise lint is unchanged.

P7 (durability lane run-1 refine, 2026-09-04): a head-added `hypotheses/H-DRAFT-*.md` whose
name lacks a well-formed hash8 is still a draft: the whole stem is its handle, it is allocated
and renamed at land like any draft, and lint reports MALFORMED-DRAFT-HANDLE on the unrepaired
branch (before P7 such a file landed on canon untouched and unflagged).

Repair mode requires the working tree checked out clean at <head>. Derives everything
from git — it never reads any harness manifest. Stdout: one JSON object.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

# ---- P1: config-derived surfaces (defaults = lab layout) ---------------------------
_DEFAULTS = {
    "hypotheses_dir": "hypotheses",
    "journal_dir": "experiments/journal-fragments",
    "journal_file": "experiments/journal.md",
    "runs_dir": "experiments/runs",
    "compiled_file": "experiments/journal-compiled.md",
}


def load_cfg(repo):
    cfg = dict(_DEFAULTS)
    path = os.path.join(repo, ".claude", "hyp.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg
    for key in _DEFAULTS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            cfg[key] = val.strip().strip("/")
    return cfg


class Surfaces(object):
    def __init__(self, repo):
        cfg = load_cfg(repo)
        hyp = cfg["hypotheses_dir"]
        self.HYP_DIR = hyp
        self.FRAG_PREFIX = cfg["journal_dir"].rstrip("/") + "/"
        self.RUNS_PREFIX = cfg["runs_dir"].rstrip("/") + "/"
        self.JOURNAL = cfg["journal_file"]
        self.COMPILED = cfg["compiled_file"]
        esc = re.escape(hyp)
        # P2: 3-4 digit ids
        self.SPEC_RE = re.compile(
            r"^%s/H-(\d{3,4})-([A-Za-z0-9._][A-Za-z0-9._-]*)\.md$" % esc)
        self.DSPEC_RE = re.compile(
            r"^%s/(H-DRAFT-[0-9a-f]{8})-([A-Za-z0-9._][A-Za-z0-9._-]*)\.md$" % esc)
        self.MALFORMED_DSPEC_RE = re.compile(
            r"^%s/(H-DRAFT-[A-Za-z0-9._-]*?)\.md$" % esc)
        self.FRAG_FILE_RE = re.compile(
            r"^%s(\d+)-[^/]+\.md$" % re.escape(self.FRAG_PREFIX))


DRAFT_RE = re.compile(r"H-DRAFT-[0-9a-f]{8}")
FRAG_ID_LINE_RE = re.compile(r"^id:\s*(\d+)\s*$")
PROGRAM = "program.md"
LEDGER = "ledger/work-ledger.jsonl"


class ToolError(Exception):
    pass


class Refusal(Exception):
    def __init__(self, reasons):
        super(Refusal, self).__init__("refusal")
        self.reasons = reasons


def git(repo, *args, ok=(0,)):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)
    if proc.returncode not in ok:
        raise ToolError("git %s -> %d: %s" % (" ".join(args[:2]), proc.returncode,
                                              proc.stderr.strip()))
    return proc.stdout


def specs_at(S, repo, ref):
    """{id: {path, sha, slug}} for <hyp>/H-NNN-<slug>.md at ref; duplicates under dups."""
    out, dups = {}, {}
    for line in git(repo, "ls-tree", "-r", ref, "--", S.HYP_DIR).splitlines():
        meta, path = line.split("\t", 1)
        sha = meta.split()[2]
        m = S.SPEC_RE.match(path)
        if not m:
            continue
        hid = "H-" + m.group(1)
        entry = {"path": path, "sha": sha, "slug": m.group(2)}
        if hid in out:
            dups.setdefault(hid, [out[hid]["path"]]).append(path)
        else:
            out[hid] = entry
    return out, dups


def frag_ids_at(S, repo, ref):
    """Fragment ids taken at ref, by filename prefix (the landed convention)."""
    ids = set()
    for line in git(repo, "ls-tree", "-r", "--name-only", ref, "--",
                    S.FRAG_PREFIX.rstrip("/")).splitlines():
        m = S.FRAG_FILE_RE.match(line)
        if m:
            ids.add(int(m.group(1)))
    return ids


def diff_entries(repo, base, head):
    """[(status, path)] with R parsed to its new path (status R treated as add of new)."""
    entries = []
    raw = git(repo, "diff", "--name-status", "-M", base, head)
    for line in raw.splitlines():
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) == 3:
            entries.append(("R", parts[2]))
        elif status in ("A", "M", "D") and len(parts) == 2:
            entries.append((status, parts[1]))
        else:
            entries.append((status[:1], parts[-1]))
    return entries


def added_line_numbers(repo, base, head, path):
    """Head-side line numbers added/changed for path (git diff -U0)."""
    raw = git(repo, "diff", "-U0", base, head, "--", path, ok=(0, 1))
    lines = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw, re.M):
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        lines.update(range(start, start + count))
    return lines


def is_landed_fragment(S, repo, base, path):
    if not path.startswith(S.FRAG_PREFIX):
        return False
    proc = subprocess.run(["git", "-C", repo, "cat-file", "-e", "%s:%s" % (base, path)],
                          capture_output=True, text=True)
    return proc.returncode == 0


def scan_line(line, old, base_slug, head_slug):
    """Classify old-id tokens on one line -> (rewrite_spans, base_tokens)."""
    rewrite, base_tok = [], 0
    for m in re.finditer(r"\b%s\b" % re.escape(old), line):
        rest = line[m.end():]
        if base_slug and rest.startswith("-" + base_slug):
            base_tok += 1
        else:
            rewrite.append((m.start(), m.end()))
    return rewrite, base_tok


def detect(S, repo, base, head):
    base_specs, base_dups = specs_at(S, repo, base)
    head_specs, head_dups = specs_at(S, repo, head)
    entries = diff_entries(repo, base, head)
    added_specs = []
    draft_specs = []
    added_frags = []
    frag_allocs_raw = []
    for status, path in entries:
        if status not in ("A", "R"):
            continue
        m = S.SPEC_RE.match(path)
        if m:
            sha = git(repo, "rev-parse", "%s:%s" % (head, path)).strip()
            added_specs.append({"id": "H-" + m.group(1), "path": path,
                                "sha": sha, "slug": m.group(2)})
            continue
        dm = S.DSPEC_RE.match(path)
        if dm:
            sha = git(repo, "rev-parse", "%s:%s" % (head, path)).strip()
            draft_specs.append({"handle": dm.group(1), "path": path,
                                "sha": sha, "slug": dm.group(2)})
            continue
        mm = S.MALFORMED_DSPEC_RE.match(path)
        if mm:
            # P7: `H-DRAFT-` without a well-formed hash8 (an executor skipped the hash step).
            # The whole stem is the handle; the slug is the stem minus the H-DRAFT- prefix.
            sha = git(repo, "rev-parse", "%s:%s" % (head, path)).strip()
            stem = mm.group(1)
            slug = re.sub(r"^H-DRAFT-", "", stem) or "draft"
            draft_specs.append({"handle": stem, "path": path, "sha": sha, "slug": slug,
                                "malformed": True})
            continue
        if (path.startswith(S.FRAG_PREFIX) and path.endswith(".md")
                and "/" not in path[len(S.FRAG_PREFIX):]
                and not is_landed_fragment(S, repo, base, path)):
            blob = git(repo, "show", "%s:%s" % (head, path))
            fid, fid_line, any_id_line = None, None, None
            for i, line in enumerate(blob.splitlines(), 1):
                fm = FRAG_ID_LINE_RE.match(line)
                if fm:
                    fid, fid_line = int(fm.group(1)), i
                    break
                if any_id_line is None and re.match(r"^id:", line):
                    any_id_line = i
            if S.FRAG_FILE_RE.match(path) and fid is not None:
                added_frags.append({"path": path, "id": fid, "id_line": fid_line})
            else:
                # P6: incoming fragment without an integer id (name and/or frontmatter)
                first = blob.splitlines()[0] if blob.splitlines() else ""
                frag_allocs_raw.append({"path": path, "id_line": any_id_line,
                                        "has_frontmatter": first.strip() == "---",
                                        "name_ok": bool(S.FRAG_FILE_RE.match(path)),
                                        "frontmatter_id": fid})
    collisions, dedupes = [], []
    for spec in sorted(added_specs, key=lambda s: s["path"]):
        hid = spec["id"]
        if hid in base_specs and base_specs[hid]["path"] != spec["path"]:
            if base_specs[hid]["sha"] == spec["sha"]:
                dedupes.append(spec)
            else:
                collisions.append({**spec, "base_path": base_specs[hid]["path"],
                                   "base_slug": base_specs[hid]["slug"]})
    base_blob_to_id = {e["sha"]: hid for hid, e in base_specs.items()}
    draft_dedupes, draft_allocs = [], []
    for spec in sorted(draft_specs, key=lambda s: s["path"]):
        if spec["sha"] in base_blob_to_id:
            draft_dedupes.append({**spec, "target": base_blob_to_id[spec["sha"]],
                                  "base_path": base_specs[base_blob_to_id[spec["sha"]]]["path"]})
        else:
            draft_allocs.append(spec)
    all_ids = [int(i.split("-")[1]) for i in
               list(base_specs.keys()) + list(head_specs.keys())]
    head_names = git(repo, "ls-tree", "-r", "--name-only", head)
    drafts = sorted(set(DRAFT_RE.findall(head_names)))
    malformed = sorted(d["handle"] for d in draft_specs if d.get("malformed"))
    base_drafts = sorted(set(DRAFT_RE.findall(
        git(repo, "ls-tree", "-r", "--name-only", base))))
    if base_drafts:
        raise ToolError("draft handle present at base (canon must never carry drafts): %s"
                        % ", ".join(base_drafts))
    frag_taken = frag_ids_at(S, repo, base)
    frag_renumbers = []
    for frag in sorted(added_frags, key=lambda f: f["path"]):
        if frag["id"] in frag_taken:
            new_id = max(frag_taken) + 1
            frag_renumbers.append({**frag, "new_id": new_id})
            frag_taken.add(new_id)
        else:
            frag_taken.add(frag["id"])
    frag_allocs = []
    for frag in sorted(frag_allocs_raw, key=lambda f: f["path"]):
        new_id = (max(frag_taken) + 1) if frag_taken else 1
        frag_allocs.append({**frag, "new_id": new_id})
        frag_taken.add(new_id)
    return {"frag_allocs": frag_allocs, "malformed_drafts": malformed,"base_specs": base_specs, "head_specs": head_specs,
            "head_dups": head_dups, "entries": entries, "collisions": collisions,
            "dedupes": dedupes, "drafts": drafts,
            "draft_allocs": draft_allocs, "draft_dedupes": draft_dedupes,
            "frag_renumbers": frag_renumbers,
            "max_id": max(all_ids) if all_ids else 0}


def candidate_lines(repo, base, head, det):
    """{path: sorted [line numbers]} of head-authored lines to scan, per diff status."""
    cands = {}
    for status, path in det["entries"]:
        if status in ("A", "R"):
            cands[path] = None  # None = every line
        elif status == "M":
            nums = added_line_numbers(repo, base, head, path)
            if nums:
                cands[path] = sorted(nums)
    return cands


def allocate(det):
    """One deterministic allocation pass, spec-path sort across collisions + drafts."""
    id_map, draft_map = {}, {}
    nxt = det["max_id"] + 1
    for item in sorted(det["collisions"] + det["draft_allocs"],
                       key=lambda s: s["path"]):
        new = "H-%03d" % nxt
        nxt += 1
        if "handle" in item:
            draft_map[item["handle"]] = new
        else:
            id_map[item["id"]] = new
    return id_map, draft_map


def plan_rewrites(S, repo, base, head, det, cands, id_map, draft_map):
    """-> (rewrites, refusal_reasons). rewrites: {head_path: [(lineno, pattern, repl)]}."""
    handle_map = dict(draft_map)
    for d in det["draft_dedupes"]:
        handle_map[d["handle"]] = d["target"]
    rewrites, reasons = {}, []
    if any(p == PROGRAM for _, p in det["entries"]):
        reasons.append("program.md appears in the diff (C7: human-only surface)")
    dedupe_paths = {d["path"] for d in det["draft_dedupes"]}
    for path, linenos in sorted(cands.items()):
        try:
            blob = git(repo, "show", "%s:%s" % (head, path))
        except ToolError:
            continue
        lines = blob.splitlines()
        targets = range(1, len(lines) + 1) if linenos is None else linenos
        for ln in targets:
            if ln > len(lines):
                continue
            text = lines[ln - 1]
            for c in det["collisions"]:
                spans, base_tok = scan_line(text, c["id"], c["base_slug"], c["slug"])
                if not spans:
                    continue
                if base_tok:
                    reasons.append("ambiguous binding at %s:%d — line carries both the "
                                   "base slug and a rewrite candidate for %s"
                                   % (path, ln, c["id"]))
                    continue
                if path == S.JOURNAL or path == PROGRAM:
                    reasons.append("collision id %s cited in frozen %s:%d"
                                   % (c["id"], path, ln))
                    continue
                if is_landed_fragment(S, repo, base, path):
                    reasons.append("collision id %s cited on a head-added line of "
                                   "LANDED fragment %s:%d (write-once; refuse, don't "
                                   "mangle)" % (c["id"], path, ln))
                    continue
                rewrites.setdefault(path, []).append(
                    (ln, r"\b%s\b" % re.escape(c["id"]), id_map[c["id"]]))
            for handle, new in sorted(handle_map.items()):
                if not re.search(r"\b%s\b" % re.escape(handle), text):
                    continue
                if path == S.JOURNAL or path == PROGRAM:
                    reasons.append("draft handle %s cited in frozen %s:%d"
                                   % (handle, path, ln))
                    continue
                if is_landed_fragment(S, repo, base, path):
                    reasons.append("draft handle %s cited on a head-added line of "
                                   "LANDED fragment %s:%d (write-once; refuse, don't "
                                   "mangle)" % (handle, path, ln))
                    continue
                if path in dedupe_paths:
                    continue  # the file itself is removed at dedupe
                rewrites.setdefault(path, []).append(
                    (ln, r"\b%s\b" % re.escape(handle), new))
    for frag in det["frag_renumbers"]:
        # P5 (H-259): match the id VALUE regardless of zero-padding — a consumer session
        # commonly writes `id: 0004` to mirror the filename prefix, and `^id:\s*4\s*$`
        # would silently no-op on it, leaving the collision unrepaired (compile-journal
        # then fails on duplicate ids). `0*` before the digits absorbs any padding;
        # behavior on unpadded ids is byte-identical (leg-P over H-147 unchanged).
        rewrites.setdefault(frag["path"], []).append(
            (frag["id_line"], r"^id:\s*0*%d\s*$" % frag["id"], "id: %d" % frag["new_id"]))
    for frag in det["frag_allocs"]:
        # P6: set the id line, or insert one after the opening frontmatter fence, or
        # prepend a minimal frontmatter block when the file has none.
        if frag["id_line"] is not None:
            rewrites.setdefault(frag["path"], []).append(
                (frag["id_line"], r"^id:.*$", "id: %d" % frag["new_id"]))
        elif frag["has_frontmatter"]:
            rewrites.setdefault(frag["path"], []).append(
                (1, r"^---\s*$", "---\nid: %d" % frag["new_id"]))
        else:
            rewrites.setdefault(frag["path"], []).append(
                (1, r"^", "---\nid: %d\n---\n" % frag["new_id"]))
    return rewrites, reasons


def plan_moves(S, det, id_map, draft_map):
    moves = {}
    for c in det["collisions"]:
        new = id_map[c["id"]]
        moves[c["path"]] = "%s/%s-%s.md" % (S.HYP_DIR, new, c["slug"])
    for d in det["draft_allocs"]:
        new = draft_map[d["handle"]]
        moves[d["path"]] = "%s/%s-%s.md" % (S.HYP_DIR, new, d["slug"])
    for frag in det["frag_renumbers"]:
        name = frag["path"][len(S.FRAG_PREFIX):]
        m = re.match(r"^(\d+)(-.*)$", name)
        width = max(len(m.group(1)), 4)
        moves[frag["path"]] = S.FRAG_PREFIX + str(frag["new_id"]).zfill(width) + m.group(2)
    for frag in det["frag_allocs"]:
        name = frag["path"][len(S.FRAG_PREFIX):]
        rest = re.sub(r"^(?:H-)?DRAFT-[0-9a-f]{8}-", "", name)
        rest = re.sub(r"^\d+-", "", rest) if frag["name_ok"] else rest
        moves[frag["path"]] = S.FRAG_PREFIX + str(frag["new_id"]).zfill(4) + "-" + rest
    for status, path in det["entries"]:
        if status not in ("A", "R") or not path.startswith(S.RUNS_PREFIX):
            continue
        tail = path[len(S.RUNS_PREFIX):]
        top = tail.split("/", 1)[0]
        rest = tail.split("/", 1)[1] if "/" in tail else ""
        if top in id_map:
            moves[path] = S.RUNS_PREFIX + id_map[top] + "/" + rest
            continue
        dm = DRAFT_RE.search(top)
        if dm and dm.group(0) in draft_map:
            new_top = re.sub(r"\b%s\b" % re.escape(dm.group(0)),
                             draft_map[dm.group(0)], top)
            moves[path] = S.RUNS_PREFIX + new_top + ("/" + rest if rest else "")
    # P8 (v2 lane run-1 refine, 2026-09-04): a draft handle inside the FILENAME of any other
    # head-added file (a fragment named `0004-register-H-DRAFT-<hash8>.md`, a note) is
    # rewritten to the allocated id, composed with any rename already planned for that path.
    for status, path in det["entries"]:
        if status not in ("A", "R") or path.startswith((S.HYP_DIR + "/", S.RUNS_PREFIX)):
            continue
        dest = moves.get(path, path)
        head, _, name = dest.rpartition("/")
        new_name = name
        for handle, new in sorted(draft_map.items()):
            new_name = re.sub(r"\b%s\b" % re.escape(handle), new, new_name)
        if new_name != name:
            moves[path] = (head + "/" if head else "") + new_name
    return moves


def write_plan(repo, moves):
    """plan.tsv: delete rows for moves + keep/- exclusions for undeclared siblings."""
    declared = set(moves)
    dirs = sorted({os.path.dirname(src) for src in moves})
    rows = ["%s\t%s\tdelete" % (src, dest) for src, dest in sorted(moves.items())]
    seen = set(declared)
    for d in dirs:
        for f in git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", d).splitlines():
            if f not in seen:
                rows.append("%s\t-\tkeep" % f)
                seen.add(f)
    fd, path = tempfile.mkstemp(prefix="id-rectify-plan-", suffix=".tsv")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return path


def apply_rewrites(repo, rewrites, moves):
    counts = {}
    for head_path, edits in sorted(rewrites.items()):
        disk = moves.get(head_path, head_path)
        full = os.path.join(repo, disk)
        with open(full, encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        n = 0
        for ln, pattern, new in edits:
            idx = ln - 1
            text = lines[idx]
            newline = ""
            for end in ("\r\n", "\n"):
                if text.endswith(end):
                    text, newline = text[:-len(end)], end
                    break
            n += len(re.findall(pattern, text))
            lines[idx] = re.sub(pattern, new, text) + newline
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("".join(lines))
        counts[disk] = n
    return counts


def sweep(repo, old_ids, expected):
    """Worktree walk: {old: {hits: [...], unexpected: [...]}} (skips .git)."""
    result = {}
    files = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            files.append(os.path.join(dirpath, name))
    texts = {}
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                texts[os.path.relpath(path, repo).replace(os.sep, "/")] = fh.read()
        except OSError:
            continue
    for old in old_ids:
        pat = re.compile(r"\b%s\b" % re.escape(old))
        hits = sorted(p for p, t in texts.items() if pat.search(t))
        allowed = expected[old]
        result[old] = {"hits": hits,
                       "unexpected": [h for h in hits if h not in allowed]}
    return result


def build_report(det, full_map, moves, counts, aliases, sweep_res, manifest_rel,
                 verify_exit, skipped):
    lines = ["# Rectification report — id-rectify",
             "",
             "| old id | new id | head spec |",
             "|---|---|---|"]
    for c in det["collisions"]:
        lines.append("| %s | %s | %s |" % (c["id"], full_map[c["id"]], c["path"]))
    for d in det["draft_allocs"]:
        lines.append("| %s | %s | %s |" % (d["handle"], full_map[d["handle"]], d["path"]))
    for d in det["draft_dedupes"]:
        lines.append("| %s | %s (dedupe — incoming copy removed; identical to %s) | %s |"
                     % (d["handle"], d["target"], d["base_path"], d["path"]))
    if det["frag_renumbers"]:
        lines += ["", "## Fragment ids renumbered at land", ""]
        for frag in det["frag_renumbers"]:
            lines.append("- `%s`: id %d -> %d" % (frag["path"], frag["id"],
                                                  frag["new_id"]))
    if det["frag_allocs"]:
        lines += ["", "## Fragment ids allocated at land (incoming fragment had no integer id)", ""]
        for frag in det["frag_allocs"]:
            lines.append("- `%s`: allocated id %d" % (frag["path"], frag["new_id"]))
    lines += ["", "## Moves (fidelity manifest %s, verify exit %s)"
              % (manifest_rel or "none — %s" % ("no renames in this repair"
                 if not moves else "manifest tool absent in this repository; "
                 "plain byte-preserving renames"),
                 "n/a" if verify_exit is None else verify_exit), ""]
    for src, dest in sorted(moves.items()):
        lines.append("- `%s` -> `%s`" % (src, dest))
    lines += ["", "## Rewrites (id tokens on head-authored lines only)", ""]
    for path, n in sorted(counts.items()):
        lines.append("- `%s`: %d token(s)" % (path, n))
    lines += ["", "## Alias rows", ""]
    for row in aliases:
        lines.append("- `%s` -> `%s`" % (row["was"], row["is"]))
    if skipped:
        lines += ["", "## Optional surfaces skipped (absent in this repository)", ""]
        for s in skipped:
            lines.append("- %s" % s)
    lines += ["", "## Dangling sweep (grep each old id; hits must be base surfaces "
              "or this repair's own records)", ""]
    for old in sorted(sweep_res):
        res = sweep_res[old]
        state = "PASS" if not res["unexpected"] else "FAIL: " + ", ".join(res["unexpected"])
        lines.append("- %s: %d hit file(s) — %s" % (old, len(res["hits"]), state))
    return "\n".join(lines) + "\n"


def run_repair(S, repo, base, head, out):
    head_sha = git(repo, "rev-parse", head).strip()
    if git(repo, "rev-parse", "HEAD").strip() != head_sha:
        raise ToolError("working tree must be checked out at --head")
    if git(repo, "status", "--porcelain").strip():
        raise ToolError("working tree must be clean")
    det = detect(S, repo, base, head)
    out["detected"] = [{"old": c["id"], "head_spec": c["path"]}
                       for c in det["collisions"]]
    out["dedupes"] = [d["path"] for d in det["dedupes"]]
    out["drafts"] = det["drafts"]
    out["draft_allocs"] = [d["path"] for d in det["draft_allocs"]]
    out["draft_dedupes"] = [{"path": d["path"], "target": d["target"]}
                            for d in det["draft_dedupes"]]
    out["frag_renumbers"] = [{"path": f["path"], "old": f["id"], "new": f["new_id"]}
                             for f in det["frag_renumbers"]]
    out["frag_allocs"] = [{"path": f["path"], "new": f["new_id"]}
                          for f in det["frag_allocs"]]
    actionable = (det["collisions"] or det["draft_allocs"] or det["draft_dedupes"]
                  or det["frag_renumbers"] or det["frag_allocs"])
    if not actionable:
        out["no_op"] = True
        return 0
    cands = candidate_lines(repo, base, head, det)
    id_map, draft_map = allocate(det)
    rewrites, reasons = plan_rewrites(S, repo, base, head, det, cands, id_map, draft_map)
    if reasons:
        raise Refusal(reasons)
    moves = plan_moves(S, det, id_map, draft_map)
    full_map = dict(id_map)
    full_map.update(draft_map)
    out["id_map"] = full_map
    skipped = []

    manifest_rel, verify_exit = None, None
    manifest_tool = os.path.join(repo, "scripts", "fidelity-manifest.py")
    if moves and os.path.isfile(manifest_tool):
        plan_path = write_plan(repo, moves)
        slug = "id-rectify-%s" % head_sha[:8]
        declare = subprocess.run(
            ["python3", "scripts/fidelity-manifest.py", ".", "--declare", plan_path,
             "--slug", slug, "--note", "id-rectify renumber of incoming collision ids"],
            capture_output=True, text=True, cwd=repo)
        os.unlink(plan_path)
        if declare.returncode != 0:
            raise ToolError("manifest declare failed: %s%s" % (declare.stdout,
                                                               declare.stderr))
        manifests = sorted(f for f in os.listdir(os.path.join(repo, "manifests"))
                           if f.endswith("-%s.manifest.json" % slug))
        if len(manifests) != 1:
            raise ToolError("expected exactly one manifest for slug %s, saw %r"
                            % (slug, manifests))
        manifest_rel = "manifests/" + manifests[0]
        # H-104 sequence: declare, COMMIT the declaration, execute, verify.
        git(repo, "add", manifest_rel)
        proc = subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", "commit",
                               "-q", "-m",
                               "id-rectify: declare fidelity manifest %s" % slug],
                              capture_output=True, text=True, env=dict(os.environ))
        if proc.returncode != 0:
            raise ToolError("manifest-declaration commit failed: " + proc.stderr)
        for src, dest in sorted(moves.items()):
            s, d = os.path.join(repo, src), os.path.join(repo, dest)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            os.rename(s, d)
        verify = subprocess.run(
            ["python3", "scripts/fidelity-manifest.py", ".", "--verify", manifest_rel],
            capture_output=True, text=True, cwd=repo)
        verify_exit = verify.returncode
        out["manifest"] = manifest_rel
        out["manifest_verify_exit"] = verify_exit
        if verify.returncode != 0:
            raise ToolError("manifest verify failed:\n" + verify.stdout + verify.stderr)
    elif moves:
        skipped.append("fidelity manifest (scripts/fidelity-manifest.py absent): "
                       "renames executed as plain byte-preserving renames")
        out["manifest"] = None
        out["manifest_verify_exit"] = None
        for src, dest in sorted(moves.items()):
            s, d = os.path.join(repo, src), os.path.join(repo, dest)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            os.rename(s, d)

    for d in det["draft_dedupes"]:
        os.remove(os.path.join(repo, d["path"]))

    counts = apply_rewrites(repo, rewrites, moves)
    out["rewritten"] = counts

    aliases = []
    alias_specs = ([(c["id"], full_map[c["id"]],
                     "renumbered to %s by id-rectify (incoming side only)")
                    for c in det["collisions"]] +
                   [(d["handle"], full_map[d["handle"]],
                     "allocated as %s by id-rectify at land (draft-then-allocate)")
                    for d in det["draft_allocs"]] +
                   [(d["handle"], d["target"],
                     "deduped into %s by id-rectify at land (byte-identical to the "
                     "base spec; incoming copy removed)")
                    for d in det["draft_dedupes"]])
    today = os.environ.get("ID_RECTIFY_DATE") or \
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    ledger_present = os.path.isfile(os.path.join(repo, LEDGER))
    for old, new, verb in alias_specs:
        row = {"date": today,
               "slug": "id-alias-%s-to-%s" % (old, new),
               "hit": "id-alias: %s@%s %s" % (old, head_sha, verb % new),
               "kind": "id-alias",
               "was": "%s@%s" % (old, head_sha), "is": new}
        aliases.append(row)
    if ledger_present:
        with open(os.path.join(repo, LEDGER), "a", encoding="utf-8") as fh:
            for row in aliases:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif aliases:
        skipped.append("id-alias ledger (%s absent): alias rows recorded in this "
                       "report and the tool output only" % LEDGER)
    out["aliases"] = aliases
    out["ledger_appended"] = ledger_present

    regen = {}
    for name, script, cmd in [
            ("journal", "scripts/compile-journal.py",
             ["python3", "scripts/compile-journal.py", "."]),
            ("dashboard", "scripts/compile-dashboard.py",
             ["python3", "scripts/compile-dashboard.py", ".", "--quiet"])]:
        if not os.path.isfile(os.path.join(repo, script)):
            regen[name] = None
            skipped.append("derived-view regen (%s absent)" % script)
            continue
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=repo)
        regen[name] = proc.returncode
        if proc.returncode != 0:
            raise ToolError("regenerate %s failed: %s%s" % (name, proc.stdout, proc.stderr))
    out["regenerated"] = regen
    out["skipped_surfaces"] = skipped

    if full_map:
        anchor = min(full_map.values(), key=lambda i: int(i.split("-")[1]))
    elif det["draft_dedupes"]:
        anchor = min(d["target"] for d in det["draft_dedupes"])
    else:
        anchor = "H-%03d" % det["max_id"]
    report_rel = S.RUNS_PREFIX + anchor + "/rectification-report.md"
    old_tokens = list(id_map) + [d["handle"] for d in det["draft_allocs"]] + \
        [d["handle"] for d in det["draft_dedupes"]]
    expected = {}
    for old in old_tokens:
        base_files = [l.split(":", 1)[1] for l in
                      git(repo, "grep", "-l", "-w", old, base, ok=(0, 1)).splitlines()
                      if ":" in l]
        allowed = set(base_files) | {report_rel}
        if ledger_present:
            allowed.add(LEDGER)
        if os.path.isfile(os.path.join(repo, "DASHBOARD.md")):
            allowed.add("DASHBOARD.md")
        if os.path.isfile(os.path.join(repo, S.COMPILED)):
            allowed.add(S.COMPILED)
        if manifest_rel:
            allowed.add(manifest_rel)
        expected[old] = allowed
    sweep_res = sweep(repo, old_tokens, expected)
    out["sweep"] = {old: {"hit_count": len(r["hits"]), "unexpected": r["unexpected"]}
                    for old, r in sweep_res.items()}

    report = build_report(det, full_map, moves, counts, aliases, sweep_res,
                          manifest_rel, verify_exit, skipped)
    report_abs = os.path.join(repo, report_rel)
    os.makedirs(os.path.dirname(report_abs), exist_ok=True)
    with open(report_abs, "w", encoding="utf-8") as fh:
        fh.write(report)
    out["report"] = report_rel

    git(repo, "add", "-A")
    labels = ["%s->%s" % (o, n) for o, n in sorted(full_map.items())] + \
        ["%s=>dedupe:%s" % (d["handle"], d["target"]) for d in det["draft_dedupes"]] + \
        ["frag %d->%d" % (f["id"], f["new_id"]) for f in det["frag_renumbers"]] + \
        ["frag %s=>%d" % (f["path"].rsplit("/", 1)[-1], f["new_id"])
         for f in det["frag_allocs"]]
    msg = "id-rectify: resolve %d incoming id(s): %s" % (len(labels), ", ".join(labels))
    proc = subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false",
                           "commit", "-q", "-m", msg],
                          capture_output=True, text=True, env=dict(os.environ))
    if proc.returncode != 0:
        raise ToolError("commit failed: " + proc.stderr)
    out["commit"] = git(repo, "rev-parse", "HEAD").strip()
    out["no_op"] = False
    return 0


def run_lint(S, repo, base, head, out):
    det = detect(S, repo, base, head)
    findings = []
    for c in det["collisions"]:
        findings.append({"class": "ID-COLLISION", "id": c["id"],
                         "head_spec": c["path"], "base_spec": c["base_path"]})
    all_specs = {}
    for ref in (base, head):
        specs, _ = specs_at(S, repo, ref)
        for hid, entry in specs.items():
            all_specs.setdefault(hid, set()).add(entry["path"])
    for hid, paths in sorted(all_specs.items()):
        if len(paths) > 1:
            findings.append({"class": "EXACTLY-ONE-SPEC-BREAK", "id": hid,
                             "paths": sorted(paths)})
    for hid, paths in sorted(det["head_dups"].items()):
        findings.append({"class": "DUPLICATE-ID-AT-HEAD", "id": hid, "paths": paths})
    for draft in det["drafts"]:
        findings.append({"class": "DRAFT-HANDLE-SURVIVES", "handle": draft})
    for frag in det["frag_allocs"]:
        findings.append({"class": "FRAGMENT-WITHOUT-INTEGER-ID", "path": frag["path"]})
    for stem in det["malformed_drafts"]:
        findings.append({"class": "MALFORMED-DRAFT-HANDLE", "handle": stem,
                         "expect": "hypotheses/H-DRAFT-<8 hex>-<slug>.md"})
    for status, path in det["entries"]:
        if status == "M" and is_landed_fragment(S, repo, base, path):
            findings.append({"class": "LANDED-FRAGMENT-MODIFIED", "path": path})
    out["findings"] = findings
    out["blocked"] = bool(findings)
    return 1 if findings else 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--lint", action="store_true")
    args = ap.parse_args(argv)
    out = {"mode": "lint" if args.lint else "repair",
           "base": None, "head": None}
    try:
        S = Surfaces(args.repo)
        out["base"] = git(args.repo, "rev-parse", args.base).strip()
        out["head"] = git(args.repo, "rev-parse", args.head).strip()
        code = (run_lint if args.lint else run_repair)(
            S, args.repo, out["base"], out["head"], out)
    except Refusal as r:
        out["refusal"] = r.reasons
        code = 3
    except ToolError as e:
        out["error"] = str(e)
        code = 2
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
