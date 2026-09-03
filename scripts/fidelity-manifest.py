#!/usr/bin/env python3
"""fidelity_manifest.py -- declare-then-verify move/merge/consolidate manifests.

Contract (H-104-fidelity-manifest / experiments/runs/H-104/fixture/fidelity-manifest-design.md):

  A move-class change is DECLAREd before it executes: a manifest.json (schema
  move-manifest/v1) records every source file's sha256, its intended
  destination, and a disposition (delete/symlink/keep). After the change
  executes, --verify re-checks the manifest against the current tree and
  reports defects in the house TAB finding contract:

    CLASS<TAB><pointer path><TAB><detail>

  Four defect classes (see check_* functions below for the algorithms):
    OMITTED               -- dest missing (never landed)
    DIVERGED              -- dest present but wrong bytes
    DROPPED-ASSET         -- an undeclared sibling of a moved file
    UNDECLARED-LEFTOVER   -- the source was not cleaned up as declared

  Plus one operational class surfaced by --open:
    UNVERIFIED-MANIFEST   -- a declared manifest with no PASS/ABANDONED record

  CLI:
    fidelity_manifest.py <repo-root> --declare <plan.tsv> --slug <slug>
        [--src-root <path>] [--note <text>]
    fidelity_manifest.py <repo-root> --verify <manifest-path>
    fidelity_manifest.py <repo-root> --open
    fidelity_manifest.py <repo-root> --abandon <manifest-path> --reason <text>

  Exit codes: 0 clean, 1 findings printed, 2 usage/precondition error.

  This script is stdlib-only. It calls `git` read-only (rev-parse, ls-files,
  ls-tree, show, status) and never mutates git state -- the caller commits.

  Determinism: findings are sorted (CLASS, pointer, detail) ascending;
  8-hex sha prefixes in details; no timestamps in stdout beyond a manifest's
  own fixed `declared` field, echoed verbatim in UNVERIFIED-MANIFEST lines.
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys
from collections import namedtuple

SCHEMA = "move-manifest/v1"
DISPOSITIONS = ("delete", "symlink", "keep")

Finding = namedtuple("Finding", ["cls", "pointer", "detail"])


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------

def h8(full_hex):
    """First 8 hex chars of a sha256/sha1 hex digest, for display."""
    return full_hex[:8]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def to_posix(rel):
    return rel.replace(os.sep, "/")


def resolve_path(base_dir, p):
    """Resolve a CLI-supplied path: absolute paths pass through unchanged;
    relative paths resolve against base_dir (normally repo-root), which
    decouples correctness from the caller's cwd."""
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(base_dir, p))


def repo_rel(repo_root, abs_path):
    return to_posix(os.path.relpath(abs_path, repo_root))


def finding_line(f):
    return "%s\t%s\t%s" % (f.cls, f.pointer, f.detail)


def sort_findings(findings):
    return sorted(findings, key=lambda f: (f.cls, f.pointer, f.detail))


def print_findings(findings, stream=sys.stdout):
    for f in sort_findings(findings):
        print(finding_line(f), file=stream)


def error_line(pointer, reason):
    return Finding("ERROR", pointer, reason)


# --------------------------------------------------------------------------
# git helpers (read-only)
# --------------------------------------------------------------------------

def _run_git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True
    )


def git_rev_parse_head(repo_dir):
    r = _run_git(["rev-parse", "HEAD"], repo_dir)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "surrogateescape").strip()


def is_git_repo(path):
    r = _run_git(["rev-parse", "--is-inside-work-tree"], path)
    return r.returncode == 0 and r.stdout.decode().strip() == "true"


def git_ls_files(src_root_fs, pathspec):
    """Tracked files under pathspec, src_root-relative POSIX. None on git error."""
    r = _run_git(["ls-files", "--", pathspec], src_root_fs)
    if r.returncode != 0:
        return None
    out = r.stdout.decode("utf-8", "surrogateescape")
    return sorted(l for l in out.splitlines() if l)


def git_ls_tree_at(repo_root, base_sha, pathspec):
    """Tracked files under pathspec AT base_sha, repo-relative POSIX."""
    r = _run_git(["ls-tree", "-r", base_sha, "--name-only", "--", pathspec], repo_root)
    if r.returncode != 0:
        return []
    out = r.stdout.decode("utf-8", "surrogateescape")
    return [l for l in out.splitlines() if l]


def git_show(repo_root, relpath_at_head):
    """Bytes of relpath_at_head as committed at HEAD, or None if absent there."""
    r = _run_git(["show", "HEAD:%s" % relpath_at_head], repo_root)
    if r.returncode != 0:
        return None
    return r.stdout


def git_has_uncommitted(repo_root, relpath):
    r = _run_git(["status", "--porcelain", "--", relpath], repo_root)
    if r.returncode != 0:
        return False
    return bool(r.stdout.decode("utf-8", "surrogateescape").strip())


def find_files_sorted(root_dir, rel_dir):
    """Fallback directory expansion when src_root is not a git repo: every
    file beneath rel_dir, root_dir-relative POSIX, sorted."""
    abs_dir = os.path.join(root_dir, rel_dir)
    results = []
    for dirpath, dirnames, filenames in os.walk(abs_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            results.append(to_posix(os.path.relpath(full, root_dir)))
    return sorted(results)


# --------------------------------------------------------------------------
# plan parsing (--declare)
# --------------------------------------------------------------------------

class PlanFatal(Exception):
    """Carries a list of (lineno_or_None, reason) plan errors."""
    def __init__(self, errors):
        super().__init__("plan invalid")
        self.errors = errors


def read_plan_lines(plan_path):
    """Yields (lineno, raw_line) for non-blank, non-comment lines."""
    with open(plan_path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if stripped == "" or stripped.startswith("#"):
                continue
            yield lineno, line


def parse_plan(plan_path, src_root_fs, src_root_is_git, is_external):
    """Parse + validate + expand the plan TSV into row dicts (pre-hash):
    {src, dest, disposition, expect, lineno}. Raises PlanFatal on any
    malformed-plan condition, collecting every problem found."""
    errors = []
    raw_rows = []  # (lineno, src, dest, disposition, expect)

    for lineno, line in read_plan_lines(plan_path):
        fields = line.split("\t")
        if len(fields) not in (3, 4):
            errors.append((lineno, "malformed line: expected 3 or 4 TAB-separated fields, got %d" % len(fields)))
            continue
        src, dest, disposition = fields[0], fields[1], fields[2]
        expect = None
        if len(fields) == 4:
            if not fields[3].startswith("expect="):
                errors.append((lineno, "malformed 4th field %r: expected expect=<path>" % fields[3]))
                continue
            expect = fields[3][len("expect="):]
            if not expect:
                errors.append((lineno, "expect= requires a path"))
                continue

        if disposition not in DISPOSITIONS:
            errors.append((lineno, "unknown disposition %r: expected one of %s" % (disposition, DISPOSITIONS)))
            continue
        if dest == "-" and disposition != "keep":
            errors.append((lineno, "dest '-' (declared exclusion) requires disposition=keep"))
            continue
        if is_external and disposition != "keep":
            errors.append((lineno, "non-keep disposition %r with external --src-root: external trees only support keep" % disposition))
            continue

        raw_rows.append((lineno, src, dest, disposition, expect))

    if errors:
        raise PlanFatal(errors)

    # classify + expand (needs filesystem)
    expanded = []  # dicts: src, dest, disposition, expect, lineno
    for lineno, src, dest, disposition, expect in raw_rows:
        src_norm = src.rstrip("/")
        abs_src = os.path.join(src_root_fs, src_norm) if src_norm else src_root_fs
        is_dir = os.path.isdir(abs_src)
        is_file = os.path.isfile(abs_src) or os.path.islink(abs_src)

        if not is_dir and not is_file:
            errors.append((lineno, "src %r missing from the src_root tree" % src))
            continue

        if is_dir:
            if expect is not None:
                errors.append((lineno, "expect= not allowed on a directory row (%r)" % src))
                continue
            if src_root_is_git:
                files = git_ls_files(src_root_fs, src_norm if src_norm else ".")
                if files is None:
                    files = find_files_sorted(src_root_fs, src_norm)
            else:
                files = find_files_sorted(src_root_fs, src_norm)
            dest_prefix = dest.rstrip("/") + "/"
            src_prefix = src_norm + "/" if src_norm else ""
            for file_rel in files:
                if src_prefix and not file_rel.startswith(src_prefix):
                    continue
                tail = file_rel[len(src_prefix):] if src_prefix else file_rel
                expanded.append({
                    "src": file_rel,
                    "dest": dest_prefix + tail,
                    "disposition": disposition,
                    "expect": None,
                    "lineno": lineno,
                })
        else:
            expanded.append({
                "src": src_norm,
                "dest": dest,
                "disposition": disposition,
                "expect": expect,
                "lineno": lineno,
            })

    if errors:
        raise PlanFatal(errors)

    # duplicate-src-after-expansion check
    seen = {}
    for row in expanded:
        s = row["src"]
        if s in seen:
            errors.append((row["lineno"], "duplicate src %r after expansion (first declared at line %d)" % (s, seen[s])))
        else:
            seen[s] = row["lineno"]
    if errors:
        raise PlanFatal(errors)

    return expanded


# --------------------------------------------------------------------------
# manifest read/write
# --------------------------------------------------------------------------

def manifests_dir(repo_root):
    return os.path.join(repo_root, "manifests")


def list_manifest_files(repo_root):
    d = manifests_dir(repo_root)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".manifest.json"))


def next_manifest_number(repo_root):
    max_n = 0
    for fname in list_manifest_files(repo_root):
        prefix = fname.split("-", 1)[0]
        if prefix.isdigit():
            max_n = max(max_n, int(prefix))
    return max_n + 1


def find_manifest_for_slug(repo_root, slug):
    for fname in list_manifest_files(repo_root):
        stem = fname[: -len(".manifest.json")]
        parts = stem.split("-", 1)
        if len(parts) == 2 and parts[1] == slug:
            return fname
    return None


def write_manifest(repo_root, filename, manifest):
    d = manifests_dir(repo_root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, indent=2))
        fh.write("\n")
    return path


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def manifest_row_dict(row):
    """Fixed field order per design doc SS1.2."""
    return {
        "i": row["i"],
        "src": row["src"],
        "dest": row["dest"],
        "disposition": row["disposition"],
        "sha256": row["sha256"],
        "expect_from": row.get("expect_from"),
        "expect_sha256": row.get("expect_sha256"),
    }


def non_verification_fields(manifest):
    return {k: v for k, v in manifest.items() if k != "verifications"}


def has_pass_or_abandoned(manifest):
    return any(v.get("result") in ("PASS", "ABANDONED") for v in manifest.get("verifications", []))


def has_pass(manifest):
    return any(v.get("result") == "PASS" for v in manifest.get("verifications", []))


# --------------------------------------------------------------------------
# --declare
# --------------------------------------------------------------------------

def cmd_declare(repo_root, plan_arg, slug, src_root_arg, note):
    plan_path = resolve_path(repo_root, plan_arg)
    if not os.path.isfile(plan_path):
        print_findings([error_line(plan_arg, "plan file not found")])
        return 2

    src_root_display = src_root_arg if src_root_arg else "."
    is_external = src_root_display != "."
    src_root_fs = repo_root if not is_external else resolve_path(repo_root, src_root_display)

    if is_external and not os.path.isdir(src_root_fs):
        print_findings([error_line(src_root_display, "--src-root does not exist")])
        return 2

    src_root_is_git = is_git_repo(src_root_fs)

    try:
        expanded = parse_plan(plan_path, src_root_fs, src_root_is_git, is_external)
    except PlanFatal as e:
        for lineno, reason in sorted(e.errors, key=lambda t: (t[0] is None, t[0])):
            print(finding_line(error_line("%s:%s" % (plan_arg, lineno), reason)))
        return 2

    if not expanded:
        print_findings([error_line(plan_arg, "plan produced zero rows")])
        return 2

    base = git_rev_parse_head(repo_root)
    if not base:
        print_findings([error_line(repo_root, "repo-root is not a git repository with a HEAD commit")])
        return 2

    src_root_head = None
    if is_external and src_root_is_git:
        src_root_head = git_rev_parse_head(src_root_fs)

    # hash + sort + index
    expanded.sort(key=lambda r: r["src"])
    for idx, row in enumerate(expanded, start=1):
        row["i"] = idx
        abs_src = os.path.join(src_root_fs, row["src"])
        row["sha256"] = sha256_file(abs_src)
        if row["expect"]:
            abs_expect = resolve_path(repo_root, row["expect"])
            row["expect_from"] = row["expect"]
            row["expect_sha256"] = sha256_file(abs_expect)
        else:
            row["expect_from"] = None
            row["expect_sha256"] = None

    # uncommitted-source warning (advisory only, in-repo rows)
    if not is_external:
        for row in expanded:
            if git_has_uncommitted(repo_root, row["src"]):
                print("WARNING: %s has uncommitted changes -- base will not reproduce this hash" % row["src"], file=sys.stderr)

    manifest = {
        "schema": SCHEMA,
        "slug": slug,
        "declared": datetime.date.today().isoformat(),
        "base": base,
        "src_root": src_root_display,
        "src_root_head": src_root_head,
        "note": note or "",
        "rows": [manifest_row_dict(r) for r in expanded],
        "verifications": [],
    }

    existing = find_manifest_for_slug(repo_root, slug)
    if existing:
        existing_rel = "manifests/%s" % existing
        if git_show(repo_root, existing_rel) is not None:
            print(finding_line(error_line(existing_rel, "supersede via a new slug; --abandon the old one")))
            return 2
        filename = existing
    else:
        filename = "%04d-%s.manifest.json" % (next_manifest_number(repo_root), slug)

    write_manifest(repo_root, filename, manifest)
    rel = "manifests/%s" % filename

    if src_root_display == ".":
        src_root_field = "."
    elif src_root_head:
        src_root_field = "%s@%s" % (src_root_display, h8(src_root_head))
    else:
        src_root_field = src_root_display

    print("DECLARED\t%s\trows=%d base=%s src_root=%s" % (rel, len(expanded), h8(base), src_root_field))

    sweep = dropped_asset_sweep(repo_root, base, manifest["rows"], rel)
    if sweep:
        print_findings(sweep)
        return 1
    return 0


# --------------------------------------------------------------------------
# defect-class checks (--verify)
# --------------------------------------------------------------------------

def dropped_asset_sweep(repo_root, base_sha, rows, manifest_rel):
    """DROPPED-ASSET: undeclared siblings of every delete/symlink row's src,
    swept at the manifest's base sha (in-repo rows only, by construction --
    external src_root forces disposition=keep at declare time)."""
    in_repo_rows = [r for r in rows if r["disposition"] in ("delete", "symlink")]
    if not in_repo_rows:
        return []

    dirs = sorted({os.path.dirname(r["src"]) or "." for r in in_repo_rows})
    declared_srcs = {r["src"] for r in rows}

    file_to_dirs = {}
    for d in dirs:
        for f in git_ls_tree_at(repo_root, base_sha, d):
            file_to_dirs.setdefault(f, []).append(d)

    uncovered = sorted(f for f in file_to_dirs if f not in declared_srcs)

    findings = []
    for f in uncovered:
        best_dir = max(file_to_dirs[f], key=lambda d: (len(d), d))
        exists_now = os.path.lexists(os.path.join(repo_root, f))
        state = "left-behind" if exists_now else "vanished"
        detail = ("manifest:%s undeclared sibling under %s — %s; declare a row (move it, keep it, or exclude it)"
                   % (manifest_rel, best_dir, state))
        findings.append(Finding("DROPPED-ASSET", f, detail))
    return findings


def check_omitted_diverged(repo_root, rows, manifest_rel):
    findings = []
    for row in rows:
        if row["dest"] == "-":
            continue
        dest_abs = os.path.join(repo_root, row["dest"])
        if not os.path.isfile(dest_abs):
            detail = "manifest:%s#row%d src=%s sha=%s — dest absent" % (
                manifest_rel, row["i"], row["src"], h8(row["sha256"]))
            findings.append(Finding("OMITTED", row["dest"], detail))
            continue
        expected = row["expect_sha256"] or row["sha256"]
        actual = sha256_file(dest_abs)
        if actual != expected:
            detail = "manifest:%s#row%d expected=%s actual=%s" % (
                manifest_rel, row["i"], h8(expected), h8(actual))
            if row.get("expect_from"):
                detail += " (expect_from=%s)" % row["expect_from"]
            findings.append(Finding("DIVERGED", row["dest"], detail))
    return findings


def check_undeclared_leftover(repo_root, rows, manifest_rel):
    findings = []
    for row in rows:
        disposition = row["disposition"]
        if disposition == "keep":
            continue
        src_abs = os.path.join(repo_root, row["src"])

        if disposition == "delete":
            if os.path.lexists(src_abs):
                detail = "manifest:%s#row%d disposition=delete but still-present" % (manifest_rel, row["i"])
                findings.append(Finding("UNDECLARED-LEFTOVER", row["src"], detail))
            continue

        # disposition == "symlink"
        dest_abs = os.path.join(repo_root, row["dest"])
        src_real = os.path.realpath(src_abs)
        dest_real = os.path.realpath(dest_abs)
        if src_real == dest_real:
            continue  # single-home satisfied, even if neither side exists yet (OMITTED covers that)
        if not os.path.lexists(src_abs):
            state = "single-home-link-missing"
        elif os.path.islink(src_abs):
            state = "symlink-elsewhere"
        else:
            state = "still-regular-file"
        detail = "manifest:%s#row%d disposition=symlink but %s" % (manifest_rel, row["i"], state)
        findings.append(Finding("UNDECLARED-LEFTOVER", row["src"], detail))
    return findings


def run_all_checks(repo_root, manifest, manifest_rel):
    findings = []
    findings.extend(check_omitted_diverged(repo_root, manifest["rows"], manifest_rel))
    findings.extend(check_undeclared_leftover(repo_root, manifest["rows"], manifest_rel))
    findings.extend(dropped_asset_sweep(repo_root, manifest["base"], manifest["rows"], manifest_rel))
    return sort_findings(findings)


# --------------------------------------------------------------------------
# committed-declaration precondition (shared by --verify and --abandon)
# --------------------------------------------------------------------------

def load_committed_manifest(repo_root, manifest_abs, manifest_rel):
    """Returns (manifest_dict, None) on success, or (None, error_reason)."""
    if not os.path.isfile(manifest_abs):
        return None, "manifest file not found"
    try:
        working = load_manifest(manifest_abs)
    except (OSError, ValueError) as e:
        return None, "manifest file unreadable/invalid JSON: %s" % e

    committed_bytes = git_show(repo_root, manifest_rel)
    if committed_bytes is None:
        return None, ("declaration not committed / diverged from committed copy -- "
                       "declare, commit, execute, then verify")
    try:
        committed = json.loads(committed_bytes.decode("utf-8"))
    except ValueError as e:
        return None, "committed manifest is not valid JSON: %s" % e

    if non_verification_fields(committed) != non_verification_fields(working):
        return None, ("declaration not committed / diverged from committed copy -- "
                       "declare, commit, execute, then verify")
    return working, None


# --------------------------------------------------------------------------
# --verify
# --------------------------------------------------------------------------

def cmd_verify(repo_root, manifest_arg):
    manifest_abs = resolve_path(repo_root, manifest_arg)
    manifest_rel = repo_rel(repo_root, manifest_abs)

    manifest, err = load_committed_manifest(repo_root, manifest_abs, manifest_rel)
    if err:
        print(finding_line(error_line(manifest_rel, err)))
        return 2

    findings = run_all_checks(repo_root, manifest, manifest_rel)

    if findings:
        print_findings(findings)
        return 1

    if not has_pass(manifest):
        head = git_rev_parse_head(repo_root)
        manifest.setdefault("verifications", []).append({
            "date": datetime.date.today().isoformat(),
            "head": head,
            "result": "PASS",
            "rows": len(manifest["rows"]),
        })
        with open(manifest_abs, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=2))
            fh.write("\n")

    head = git_rev_parse_head(repo_root)
    print("VERIFIED\t%s\trows=%d clean head=%s" % (manifest_rel, len(manifest["rows"]), h8(head)))
    return 0


# --------------------------------------------------------------------------
# --open
# --------------------------------------------------------------------------

def cmd_open(repo_root):
    findings = []
    for fname in list_manifest_files(repo_root):
        path = os.path.join(manifests_dir(repo_root), fname)
        try:
            manifest = load_manifest(path)
        except (OSError, ValueError):
            continue
        if has_pass_or_abandoned(manifest):
            continue
        rel = "manifests/%s" % fname
        detail = ("declared %s slug=%s rows=%d — verify after executing, or --abandon with a reason"
                   % (manifest.get("declared", "?"), manifest.get("slug", "?"), len(manifest.get("rows", []))))
        findings.append(Finding("UNVERIFIED-MANIFEST", rel, detail))

    if findings:
        print_findings(findings)
        return 1
    return 0


# --------------------------------------------------------------------------
# --abandon
# --------------------------------------------------------------------------

def cmd_abandon(repo_root, manifest_arg, reason):
    manifest_abs = resolve_path(repo_root, manifest_arg)
    manifest_rel = repo_rel(repo_root, manifest_abs)

    manifest, err = load_committed_manifest(repo_root, manifest_abs, manifest_rel)
    if err:
        print(finding_line(error_line(manifest_rel, err)))
        return 2

    head = git_rev_parse_head(repo_root)
    manifest.setdefault("verifications", []).append({
        "date": datetime.date.today().isoformat(),
        "head": head,
        "result": "ABANDONED",
        "rows": len(manifest["rows"]),
        "reason": reason,
    })
    with open(manifest_abs, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, indent=2))
        fh.write("\n")

    print("ABANDONED\t%s\t%s" % (manifest_rel, reason))
    return 0


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------

class UsageError(Exception):
    pass


def parse_kv_flags(tokens, known):
    """known: dict flag -> required(bool). Returns dict flag->value."""
    result = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok not in known:
            raise UsageError("unknown flag %r" % tok)
        if i + 1 >= len(tokens):
            raise UsageError("flag %s requires a value" % tok)
        result[tok] = tokens[i + 1]
        i += 2
    missing = [f for f, required in known.items() if required and f not in result]
    if missing:
        raise UsageError("missing required flag(s): %s" % ", ".join(missing))
    return result


def main(argv):
    if not argv:
        print(finding_line(error_line("-", "usage: fidelity_manifest.py <repo-root> --declare|--verify|--open|--abandon ...")))
        return 2

    repo_root = os.path.abspath(argv[0])
    if not os.path.isdir(repo_root):
        print(finding_line(error_line(argv[0], "repo-root is not a directory")))
        return 2

    rest = argv[1:]
    if not rest:
        print(finding_line(error_line("-", "missing mode: --declare|--verify|--open|--abandon")))
        return 2

    mode = rest[0]
    mode_args = rest[1:]

    try:
        if mode == "--declare":
            if not mode_args:
                raise UsageError("--declare requires a plan.tsv path")
            plan_path = mode_args[0]
            flags = parse_kv_flags(mode_args[1:], {"--slug": True, "--src-root": False, "--note": False})
            return cmd_declare(repo_root, plan_path, flags["--slug"], flags.get("--src-root", "."), flags.get("--note", ""))

        elif mode == "--verify":
            if not mode_args:
                raise UsageError("--verify requires a manifest path")
            return cmd_verify(repo_root, mode_args[0])

        elif mode == "--open":
            return cmd_open(repo_root)

        elif mode == "--abandon":
            if not mode_args:
                raise UsageError("--abandon requires a manifest path")
            manifest_path = mode_args[0]
            flags = parse_kv_flags(mode_args[1:], {"--reason": True})
            return cmd_abandon(repo_root, manifest_path, flags["--reason"])

        else:
            raise UsageError("unknown mode %r: expected --declare|--verify|--open|--abandon" % mode)

    except UsageError as e:
        print(finding_line(error_line("-", str(e))))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
