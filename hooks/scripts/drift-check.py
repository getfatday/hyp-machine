#!/usr/bin/env python3
"""SessionStart standing-rules pointer + drift check (hyp).

Prints a short context block: the standing capture-rules pointer, plus a
byte-compare of the consumer-owned durable artifacts against the plugin's
canonical templates — the CLAUDE.md marker block (per the configured profile),
the settings deny rules, and GOVERNANCE.md (when installed). Also detects
repositories initialized by the retired predecessor plugins and points at the
init migration. Advisory only: always exits 0, and any internal error degrades
to silence rather than blocking session start.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import CONFIG_RELPATH, load_config, render, resolve_root

REPAIR = "re-run /hyp:init to restore the canonical version"

BLOCK_TEMPLATES = {
    "capture": "CLAUDE-block-capture.md",
    "experiments": "CLAUDE-block-experiments.md",
    "modeling": "CLAUDE-block-modeling.md",
}

# LEGACY-MIGRATION-BEGIN (data: the retired predecessor plugins' artifact names;
# these literals exist only so the drift check can recognize repositories they
# initialized and route them to the init migration)
LEGACY_SIGNS = [
    os.path.join(".claude", "lab-intake.json"),
    os.path.join(".claude", "lab-loop.json"),
]
LEGACY_BLOCK_BEGINS = ["<!-- BEGIN lab-intake rules", "<!-- BEGIN lab-loop rules"]
# LEGACY-MIGRATION-END


def plugin_root():
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def extract_block(text, begin, end):
    """The marker-delimited block (inclusive), or None."""
    if text is None:
        return None
    start = text.find(begin)
    if start == -1:
        return None
    stop = text.find(end, start)
    if stop == -1:
        return None
    return text[start:stop + len(end)]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    root = resolve_root(payload)
    proot = plugin_root()
    cfg = load_config(root)

    template = read(os.path.join(proot, "templates",
                                 BLOCK_TEMPLATES[cfg["profile"]]))
    if template is None:
        sys.exit(0)  # plugin tree unreadable; stay silent
    canonical = render(template, cfg).strip()
    lines = canonical.splitlines()
    begin, end = lines[0], lines[-1]

    claude_text = read(os.path.join(root, "CLAUDE.md"))
    installed_block = extract_block(claude_text, begin, end)
    config_installed = os.path.isfile(os.path.join(root, CONFIG_RELPATH))

    if installed_block is None and not config_installed:
        legacy = [p for p in LEGACY_SIGNS if os.path.isfile(os.path.join(root, p))]
        if not legacy and claude_text:
            legacy = [b for b in LEGACY_BLOCK_BEGINS if b in claude_text]
        if legacy:
            print("hyp: this repository was initialized by a retired predecessor "
                  "plugin — run /hyp:init to migrate its config and rules block "
                  "in place (nothing consumer-owned is rewritten).")
        else:
            print("hyp: not initialized in this repository — run /hyp:init to "
                  "scaffold capture (raw/notes/index/journal) and install the "
                  "durable guard rules (add --profile experiments or "
                  "--profile modeling for more).")
        sys.exit(0)

    print("hyp (%s profile): capture rules active — raw write-once at %s/, "
          "notes at %s/, index at %s, journal fragments at %s/. New knowledge "
          "enters via the intake skill."
          % (cfg["profile"], cfg["raw_dir"], cfg["notes_dir"],
             cfg["index_file"], cfg["journal_dir"]))

    drift = []

    if installed_block is None:
        drift.append("CLAUDE.md is missing the hyp rules block")
    elif installed_block.strip() != canonical:
        drift.append("the CLAUDE.md rules block differs from the plugin canonical")

    deny_template = read(os.path.join(proot, "templates", "settings-deny.json"))
    if deny_template is not None:
        try:
            wanted = json.loads(render(deny_template, cfg))["permissions"]["deny"]
        except Exception:
            wanted = []
        try:
            with open(os.path.join(root, ".claude", "settings.json"),
                      encoding="utf-8") as f:
                have = json.load(f).get("permissions", {}).get("deny", [])
        except Exception:
            have = []
        missing = [rule for rule in wanted if rule not in have]
        if missing:
            drift.append(".claude/settings.json is missing deny rules: %s"
                         % ", ".join(missing))

    gov_template = read(os.path.join(proot, "templates", "GOVERNANCE.md"))
    gov_installed = read(os.path.join(root, "GOVERNANCE.md"))
    if gov_template is not None and gov_installed is not None:
        if gov_installed.strip() != gov_template.strip():
            drift.append("GOVERNANCE.md differs from the plugin canonical "
                         "(kept as-is; delete it and re-run init to restore)")

    # LEGACY-MIGRATION-BEGIN (advice only; init performs the migration)
    leftovers = [p for p in LEGACY_SIGNS if os.path.isfile(os.path.join(root, p))]
    if claude_text:
        leftovers += [b for b in LEGACY_BLOCK_BEGINS if b in claude_text]
    if leftovers:
        drift.append("legacy predecessor-plugin artifacts remain (%s) — re-run "
                     "/hyp:init to finish the migration" % ", ".join(leftovers))
    # LEGACY-MIGRATION-END

    if drift:
        for item in drift:
            print("hyp DRIFT: %s — %s." % (item, REPAIR))
    else:
        print("hyp drift check: clean.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
