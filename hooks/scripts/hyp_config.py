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
import json
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


def resolve_root(payload):
    """Repo root for this hook call: the checkout the session is actually working in.

    Order: CLAUDE_PROJECT_DIR -- except when the payload cwd is inside a linked
    worktree of that same repository, where the worktree's toplevel wins (see
    worktree_root); then the payload cwd; then the process cwd. Never raises."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    cwd = _payload_cwd(payload)
    if env_root and os.path.isdir(env_root):
        if cwd:
            wt = worktree_root(cwd, env_root)
            if wt:
                return wt
        return env_root
    if cwd:
        return cwd
    return os.getcwd()


def load_config(root):
    """DEFAULTS overlaid with the consumer's config file, if any."""
    cfg = dict(DEFAULTS)
    try:
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


def in_dir(rel, directory):
    """True when repo-relative path `rel` sits at or under `directory`."""
    directory = directory.strip("/")
    return rel == directory or rel.startswith(directory + "/")


def render(text, cfg):
    """Deterministic placeholder substitution for the canonical templates."""
    for key in sorted(cfg):
        text = text.replace("{{" + key.upper() + "}}", cfg[key])
    return text
