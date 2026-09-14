#!/usr/bin/env python3
"""Shared configuration helpers for the hyp hook scripts.

Consumer repositories may override the default paths by writing
`.claude/hyp.json` at the repo root (the init step does this). The `profile`
key gates capability activation inside the one install: `capture` (default),
`experiments`, `modeling` — each includes everything below it.
All paths are repo-root-relative and use forward slashes. Stdlib only;
`load_config` never raises — hook scripts must fail open, because a crashing
hook is worse than a missed check.
"""
import os

PROFILES = ("capture", "experiments", "modeling")

DEFAULTS = {
    "profile": "capture",
    "raw_dir": "research/raw",
    "notes_dir": "research/notes",
    "index_file": "research/index.md",
    "journal_dir": "experiments/journal-fragments",
    "journal_file": "experiments/journal.md",
    "compiled_file": "experiments/journal-compiled.md",
    "hypotheses_dir": "hypotheses",
    "runs_dir": "experiments/runs",
    "template_file": "hypotheses/TEMPLATE.md",
    "preflight_file": "experiments/preflight.py",
    "model_dir": "operating-model",
    "context": "",
    # Stop-boundary dispatcher scope: "participants" (default -- only sessions that
    # carry HYP_DISPATCH=1 or a per-session marker are re-presented open work) or
    # "all" (every experiments-profile session; the pre-gate behaviour).
    "dispatch": "participants",
}

CONFIG_RELPATH = os.path.join(".claude", "hyp.json")
# Consumers migrating from the crux plugin: their old config is read when the
# new one is absent, so an install swap never silently drops their overrides.
LEGACY_CONFIG_RELPATH = os.path.join(".claude", "crux.json")


def _git_entry(path):
    """The .git entry at `path`: ("dir", <path>/.git) | ("file", <path>/.git) | None."""
    g = os.path.join(path, ".git")
    if os.path.isdir(g):
        return "dir", g
    if os.path.isfile(g):
        return "file", g
    return None


def _toplevel(start):
    """Nearest ancestor of `start` (inclusive) carrying a .git entry. Walks the path as
    given first (so callers' path forms keep matching), then its symlink-resolved form
    (a cwd reached through a link into a checkout's interior); None when neither walk
    finds one."""
    for cur in (os.path.abspath(start), os.path.realpath(start)):
        while True:
            if _git_entry(cur):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


def _read_small(path):
    """First 4096 bytes of a REGULAR file, stripped; None for anything else. A FIFO or
    device node in place of a pointer file would block open() forever inside a hook
    (found by adversarial review of the counted patch), so only S_ISREG files are read."""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(4096).strip()
    except OSError:
        return None


def _common_dir(top):
    """Realpath of the repository's COMMON git dir for the checkout at `top`. A main
    checkout: <top>/.git. A linked worktree: the `gitdir:` pointer file's target, then
    its `commondir` file -- the same two files git itself reads, no subprocess. None
    when unreadable, malformed, or not a linked worktree (a submodule's gitdir carries
    no commondir file, so submodules resolve to None and keep the legacy root)."""
    entry = _git_entry(top)
    if not entry:
        return None
    kind, g = entry
    if kind == "dir":
        return os.path.realpath(g)
    line = _read_small(g)
    if not line or not line.startswith("gitdir:"):
        return None
    gitdir = line[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(top, gitdir)
    gitdir = os.path.realpath(gitdir)
    rel = _read_small(os.path.join(gitdir, "commondir"))
    if not rel:
        return None
    common = rel if os.path.isabs(rel) else os.path.join(gitdir, rel)
    return os.path.realpath(common)


def worktree_root(cwd, project_root):
    """Toplevel of the checkout containing `cwd` when that checkout belongs to the SAME
    repository as `project_root` (one shared common git dir) but is not `project_root`
    itself -- i.e. the session is working in ANOTHER checkout of the repository: a
    linked worktree, or the main checkout when project_root is itself a worktree; else
    None.

    Why: in a worktree-isolated Claude Code session CLAUDE_PROJECT_DIR keeps naming the
    original checkout while the session's cwd -- and every file it stages, commits, or
    registers -- lives in the worktree. A hook that grades CLAUDE_PROJECT_DIR grades the
    wrong tree: the Stop driver reported zero open specs on main while five sat on the
    worktree branch and let the session end (consumer vault, 2026-09-03).

    Pure filesystem reads (a few stats and two tiny files), no git subprocess: hooks
    run under 10 s timeouts on loaded hosts where a single `git rev-parse` was measured
    at 200-500 ms, and a timeout here would silently fall back to the wrong tree.
    Foreign repositories, submodules, non-git directories, and unreadable pointer files
    all return None. Never raises."""
    try:
        if not cwd or not project_root:
            return None
        top = _toplevel(cwd)
        if not top:
            return None
        if os.path.realpath(top) == os.path.realpath(project_root):
            return None
        common_top = _common_dir(top)
        common_root = _common_dir(project_root)
        if not common_top or not common_root or common_top != common_root:
            return None
        return top
    except Exception:
        return None


def _payload_cwd(payload):
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(cwd, str) and cwd and os.path.isdir(cwd):
        return cwd
    return None


def same_repo_root(cwd, project_root):
    """Toplevel of the checkout containing `cwd` when that checkout is `project_root`
    itself or another checkout of the same repository (worktree_root); with no
    `project_root` (no CLAUDE_PROJECT_DIR), any git toplevel containing `cwd`. None for a
    foreign repository, a non-git directory, or an unreadable pointer file. Never raises."""
    try:
        if not cwd:
            return None
        top = _toplevel(cwd)
        if not top:
            return None
        if not project_root:
            return top
        if os.path.realpath(top) == os.path.realpath(project_root):
            return project_root
        return worktree_root(cwd, project_root)
    except Exception:
        return None


def resolve_root(payload=None):
    """Repo root for this hook call: the checkout the session is actually working in.

    ONE contract for every hook writer (lab H-278, extended by H-DRAFT-b9e771b2-hook-writes-worktree):
      1. the payload `cwd`'s toplevel, when that checkout is CLAUDE_PROJECT_DIR itself or
         another checkout of the same repository (a linked worktree, or the main checkout
         when CLAUDE_PROJECT_DIR is a worktree) -- see same_repo_root / worktree_root;
      2. else the process cwd's toplevel, under the same test (a hook run without a
         payload, or by a wrapper that already consumed it);
      3. else CLAUDE_PROJECT_DIR;
      4. else the payload cwd; else the process cwd.
    A foreign repository or a non-git cwd never wins over CLAUDE_PROJECT_DIR. Why the
    process cwd sits before CLAUDE_PROJECT_DIR: in a session that entered a worktree after
    launch, Claude Code keeps CLAUDE_PROJECT_DIR at the launch checkout while every hook's
    process cwd and payload cwd name the worktree (probe-pinned, lab
    H-DRAFT-b9e771b2); a writer that fell straight back to CLAUDE_PROJECT_DIR rewrote the
    main checkout's DASHBOARD.md from a worktree session. Never raises."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root and not os.path.isdir(env_root):
        env_root = None
    cwd = _payload_cwd(payload)
    if cwd:
        top = same_repo_root(cwd, env_root)
        if top:
            return top
    try:
        process_cwd = os.getcwd()
    except OSError:
        process_cwd = None
    if process_cwd:
        top = same_repo_root(process_cwd, env_root)
        if top:
            return top
    if env_root:
        return env_root
    if cwd:
        return cwd
    return process_cwd or "."


def load_config(root):
    """DEFAULTS overlaid with the consumer's config file, if any."""
    cfg = dict(DEFAULTS)
    try:
        import json  # lazy: session-start-budget.py imports this module under -S -E and never needs json
        path = os.path.join(root, CONFIG_RELPATH)
        if not os.path.exists(path):
            legacy = os.path.join(root, LEGACY_CONFIG_RELPATH)
            if os.path.exists(legacy):
                path = legacy
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in DEFAULTS:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    stripped = value.strip().strip("/")
                    cfg[key] = stripped if key != "profile" else value.strip()
    except Exception:
        pass
    if cfg["profile"] not in PROFILES:
        cfg["profile"] = "capture"
    return cfg


def profile_at_least(cfg, wanted):
    """True when the configured profile includes `wanted`'s capabilities."""
    try:
        return PROFILES.index(cfg.get("profile", "capture")) >= PROFILES.index(wanted)
    except ValueError:
        return False


def rel_to_root(fpath, root):
    """Normalized repo-relative path for fpath, or None when outside the repo."""
    try:
        if not os.path.isabs(fpath):
            fpath = os.path.join(root, fpath)
        rel = os.path.relpath(os.path.normpath(fpath), os.path.normpath(root))
        if rel.startswith(".."):
            # The two paths may name one tree through different symlink prefixes
            # (macOS /var -> /private/var; a worktree reached via a link): compare
            # the resolved forms before concluding the file is outside the repo.
            rel = os.path.relpath(os.path.realpath(fpath), os.path.realpath(root))
    except Exception:
        return None
    rel = rel.replace(os.sep, "/")
    if rel.startswith(".."):
        return None
    return rel


def safe_rel_path(value, default):
    """A consumer-declared repository-relative file path, or `default` when the value is not a
    non-empty string, is absolute, or leaves the repository through a `..` segment. THE one rule
    for every reader of a `.claude/hyp.json` path override -- scripts/om-worker.py (its own inline
    copy, stdlib-only bytes), init-scaffold.py's union row and merge-attrs-check.py's expected
    rows -- so a value the worker refuses is never rendered into .gitattributes as if the worker
    used it (om-worker ship fix round 1, refuter A1: "/abs/fb.jsonl" rendered `abs/fb.jsonl
    merge=union` while the rows landed at the default path). Never raises."""
    if not isinstance(value, str) or not value.strip():
        return default
    raw = value.strip()
    rel = raw.strip("/").replace(os.sep, "/")
    if not rel or os.path.isabs(raw) or ".." in rel.split("/"):
        return default
    return rel


def in_dir(rel, directory):
    """True when repo-relative path `rel` sits at or under `directory`."""
    directory = directory.strip("/")
    return rel == directory or rel.startswith(directory + "/")


def render(text, cfg):
    """Deterministic placeholder substitution for the canonical templates."""
    for key in sorted(cfg):
        text = text.replace("{{" + key.upper() + "}}", cfg[key])
    return text
