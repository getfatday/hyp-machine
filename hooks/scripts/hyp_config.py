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


def resolve_root(payload):
    """Repo root: CLAUDE_PROJECT_DIR, then the hook payload's cwd, then cwd."""
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root and os.path.isdir(root):
        return root
    cwd = (payload or {}).get("cwd")
    if cwd and os.path.isdir(cwd):
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
