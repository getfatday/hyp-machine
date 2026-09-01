#!/usr/bin/env python3
"""Deterministic scaffold for the hyp plugin (run by /hyp:init).

Usage: init-scaffold.py [repo-root] [--profile capture|experiments|modeling]
                        [--context NAME]
                        [--raw-dir P] [--notes-dir P] [--index-file P]
                        [--journal-dir P] [--journal-file P] [--compiled-file P]
                        [--hypotheses-dir P] [--runs-dir P]
                        [--template-file P] [--preflight-file P]

Profile-gated activation inside one install: `capture` (default) scaffolds the
knowledge-intake layer; `experiments` adds the hypothesis loop; `modeling` adds
the operating-model lifecycle. Each profile includes everything below it, and
re-running with a higher profile upgrades in place.

Idempotent and re-runnable: creates what is missing, repairs the plugin-owned
canonical artifacts (the config file, the CLAUDE.md marker block, the settings
deny rules, the installed scripts), and never overwrites consumer-owned content
(the index, notes, raw files, fragments, registered specs, an edited template,
model nodes, or an existing GOVERNANCE.md). Re-running with the same inputs is
a byte-level no-op. Prints one line per artifact: created / updated /
unchanged / kept / migrated.

Migration: a repository initialized by the retired predecessor plugins is
adopted in the same pass — legacy config keys merge into `.claude/hyp.json`
and legacy CLAUDE.md rules blocks are replaced by the hyp block. A
`.claude/crux.json` written by crux (this plugin's prior name) seeds the
profile and path overrides the same way when `.claude/hyp.json` is absent;
an explicit --profile flag still wins, and the crux file is left in place.

Everything written is rendered from the plugin's templates with the chosen
paths. Output contains no timestamps or randomness, so re-running with the
same inputs is byte-stable. Stdlib only.
"""
import argparse
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "hooks", "scripts"))
from hyp_config import (CONFIG_RELPATH, DEFAULTS, LEGACY_CONFIG_RELPATH,  # noqa: E402
                        PROFILES, render)

PATH_KEYS = [k for k in DEFAULTS if k not in ("profile", "context", "model_dir")]

# LEGACY-MIGRATION-BEGIN (data: the retired predecessor plugins' artifact names;
# these literals exist only so init can adopt repositories they initialized)
LEGACY_CONFIGS = [
    (os.path.join(".claude", "lab-intake.json"),
     ("raw_dir", "notes_dir", "index_file",
      "journal_dir", "journal_file", "compiled_file")),
    (os.path.join(".claude", "lab-loop.json"),
     ("hypotheses_dir", "runs_dir", "template_file", "preflight_file")),
]
LEGACY_BLOCK_MARKERS = [
    ("<!-- BEGIN lab-intake rules", "<!-- END lab-intake rules -->"),
    ("<!-- BEGIN lab-loop rules", "<!-- END lab-loop rules -->"),
]
# LEGACY-MIGRATION-END


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def template(name):
    text = read(os.path.join(PLUGIN_ROOT, "templates", name))
    if text is None:
        sys.stderr.write("init-scaffold: missing plugin template %s\n" % name)
        sys.exit(1)
    return text


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def ensure_file(root, relpath, text, label, overwrite=False):
    """created / updated / unchanged / kept, honoring consumer ownership."""
    path = os.path.join(root, relpath)
    current = read(path)
    if current is None:
        write(path, text)
        print("created   %s  (%s)" % (relpath, label))
    elif current == text:
        print("unchanged %s  (%s)" % (relpath, label))
    elif overwrite:
        write(path, text)
        print("updated   %s  (%s — restored to plugin canonical)" % (relpath, label))
    else:
        print("kept      %s  (%s — differs from the plugin canonical; the drift "
              "check will report it)" % (relpath, label))


def ensure_dir(root, reldir, label):
    path = os.path.join(root, reldir)
    keep = os.path.join(path, ".gitkeep")
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        write(keep, "")
        print("created   %s/  (%s)" % (reldir, label))
    else:
        if not os.listdir(path):
            write(keep, "")
        print("unchanged %s/  (%s)" % (reldir, label))


def migrate_legacy_config(root, cfg):
    """Fold retired-plugin config values into cfg (files are left in place;
    removing them is the consumer's call). Returns the migrated key names."""
    migrated = []
    for relpath, keys in LEGACY_CONFIGS:
        text = read(os.path.join(root, relpath))
        if text is None:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                cfg[key] = value.strip().strip("/")
                migrated.append(key)
        print("migrated  %s  (legacy config folded into %s)"
              % (relpath, CONFIG_RELPATH))
    return migrated


def migrate_crux_config(root, cfg, explicit_profile):
    """Seed cfg from `.claude/crux.json` (crux is this plugin's prior name) so
    the rename never drops the consumer's profile or path overrides. Called
    only when `.claude/hyp.json` is absent; the crux file is left in place so
    an installed crux keeps working. Explicit path flags are re-applied by the
    caller; the profile is adopted only when no --profile flag was given."""
    text = read(os.path.join(root, LEGACY_CONFIG_RELPATH))
    if text is None:
        return
    try:
        data = json.loads(text)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    seeded = []
    for key in PATH_KEYS + ["model_dir", "context"]:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            cfg[key] = value.strip().strip("/") if key != "context" else value.strip()
            seeded.append(key)
    profile = data.get("profile")
    if explicit_profile is None and profile in PROFILES and profile != cfg["profile"]:
        cfg["profile"] = profile
        seeded.insert(0, "profile")
    if seeded:
        print("migrated settings from %s  (%s seed %s; the crux file is left "
              "in place — crux keeps working)"
              % (LEGACY_CONFIG_RELPATH, ", ".join(seeded), CONFIG_RELPATH))


def strip_legacy_blocks(current):
    """Remove retired-plugin rules blocks from CLAUDE.md text; count removals."""
    removed = 0
    for begin, end in LEGACY_BLOCK_MARKERS:
        start = current.find(begin)
        stop = current.find(end, start) if start != -1 else -1
        if start != -1 and stop != -1:
            current = current[:start] + current[stop + len(end):]
            removed += 1
    if removed:
        current = re.sub(r"\n{3,}", "\n\n", current)
    return current, removed


def install_claude_block(root, block):
    path = os.path.join(root, "CLAUDE.md")
    lines = block.strip().splitlines()
    begin, end = lines[0], lines[-1]
    canonical = block.strip() + "\n"
    current = read(path)
    if current is None:
        write(path, "# CLAUDE.md\n\nThis file provides guidance to Claude Code "
                    "when working in this repository.\n\n" + canonical)
        print("created   CLAUDE.md  (with the hyp rules block)")
        return
    current, removed = strip_legacy_blocks(current)
    if removed:
        print("migrated  CLAUDE.md  (%d legacy rules block(s) replaced by the "
              "hyp block)" % removed)
    start = current.find(begin)
    stop = current.find(end, start) if start != -1 else -1
    if start != -1 and stop != -1:
        replaced = current[:start] + canonical.strip() + current[stop + len(end):]
        if replaced == current and not removed:
            print("unchanged CLAUDE.md  (rules block already canonical)")
        else:
            write(path, replaced)
            if not removed:
                print("updated   CLAUDE.md  (rules block restored to plugin "
                      "canonical)")
    else:
        write(path, current.rstrip("\n") + "\n\n" + canonical)
        print("updated   CLAUDE.md  (rules block appended)")


def merge_settings(root, deny_rules):
    relpath = os.path.join(".claude", "settings.json")
    path = os.path.join(root, relpath)
    current = read(path)
    if current is None:
        settings = {}
    else:
        try:
            settings = json.loads(current)
        except ValueError:
            print("kept      %s  (could not parse as JSON — add these deny rules "
                  "by hand: %s)" % (relpath, ", ".join(deny_rules)))
            return
        if not isinstance(settings, dict):
            print("kept      %s  (unexpected shape — add the deny rules by hand)"
                  % relpath)
            return
    permissions = settings.setdefault("permissions", {})
    deny = permissions.setdefault("deny", [])
    missing = [rule for rule in deny_rules if rule not in deny]
    if not missing and current is not None:
        print("unchanged %s  (deny rules present)" % relpath)
        return
    deny.extend(missing)
    write(path, json.dumps(settings, indent=2, sort_keys=False) + "\n")
    print(("created   %s  (deny rules installed)" if current is None else
           "updated   %s  (deny rules added: " + ", ".join(missing) + ")")
          % relpath)


def install_script(root, relpath, src_relparts, label):
    src = read(os.path.join(PLUGIN_ROOT, *src_relparts))
    if src is not None:
        ensure_file(root, relpath, src, label, overwrite=True)


def slugify(name):
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug or "main"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--profile", choices=PROFILES, default=None)
    parser.add_argument("--context", dest="context")
    for key in PATH_KEYS:
        parser.add_argument("--" + key.replace("_", "-"), dest=key)
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    cfg = dict(DEFAULTS)
    cfg["profile"] = args.profile or "capture"
    for key in PATH_KEYS:
        value = getattr(args, key)
        if value:
            cfg[key] = value.strip().strip("/")

    # 0. Legacy adoption: fold retired-plugin configs in BEFORE choosing paths,
    #    so an already-initialized repository keeps its layout. A crux config
    #    (the prior name's `.claude/crux.json`) seeds profile + paths when
    #    `.claude/hyp.json` is absent. Explicit flags still win (re-applied
    #    after the merge).
    existing = read(os.path.join(root, CONFIG_RELPATH))
    migrate_legacy_config(root, cfg)
    if existing is None:
        migrate_crux_config(root, cfg, args.profile)
    for key in PATH_KEYS:
        value = getattr(args, key)
        if value:
            cfg[key] = value.strip().strip("/")
    if existing is not None:
        try:
            prior = json.loads(existing)
        except ValueError:
            prior = None
        if isinstance(prior, dict):
            # never silently DOWNGRADE the profile on a repair re-run
            prior_profile = prior.get("profile")
            if (prior_profile in PROFILES and args.profile in (None, "capture")
                    and PROFILES.index(prior_profile) > 0):
                cfg["profile"] = prior_profile
            for key in PATH_KEYS + ["context"]:
                value = prior.get(key)
                if isinstance(value, str) and value.strip() and not getattr(args, key, None):
                    cfg[key] = value.strip().strip("/") if key != "context" else value.strip()
    if args.context:
        cfg["context"] = slugify(args.context)
    if not cfg["context"]:
        cfg["context"] = slugify(os.path.basename(root))
    profile = cfg["profile"]
    at_least = lambda wanted: PROFILES.index(profile) >= PROFILES.index(wanted)

    # 1. Config file (plugin-owned; canonical for the chosen profile + paths).
    ensure_file(root, CONFIG_RELPATH,
                json.dumps(cfg, indent=2, sort_keys=True) + "\n",
                "profile + path configuration", overwrite=True)

    # 2. Capture layer (every profile).
    ensure_dir(root, cfg["raw_dir"], "raw verbatim sources, write-once")
    ensure_dir(root, cfg["notes_dir"], "distilled notes")
    ensure_dir(root, cfg["journal_dir"], "write-once journal fragments")
    ensure_file(root, cfg["index_file"], template("index.md"), "wiki index seed")
    ensure_file(root, "GOVERNANCE.md", template("GOVERNANCE.md"),
                "behavioral invariants")
    install_script(root, os.path.join("scripts", "compile-journal.py"),
                   ("scripts", "compile-journal.py"), "journal compiler")

    # 3. Experiments layer.
    if at_least("experiments"):
        ensure_dir(root, cfg["hypotheses_dir"], "hypothesis specs, one file each")
        ensure_dir(root, cfg["runs_dir"],
                   "run artifacts: <id>/fixture/ shared inputs, <id>/run-<k>/ outputs")
        ensure_file(root, cfg["template_file"],
                    render(template("HYPOTHESIS-TEMPLATE.md"), cfg), "spec template")
        install_script(root, cfg["preflight_file"], ("scripts", "preflight.py"),
                       "deterministic spec pre-flight")

    # 4. Modeling layer.
    if at_least("modeling"):
        model_dir = cfg["model_dir"]
        ctx_dir = "%s/%s" % (model_dir, cfg["context"])
        schema_src = read(os.path.join(PLUGIN_ROOT, "kernel", "operating-model",
                                       "SCHEMA.md"))
        if schema_src is not None:
            ensure_file(root, "%s/SCHEMA.md" % model_dir, schema_src,
                        "operating-model node grammar", overwrite=True)
        ensure_file(root, "%s/model.md" % ctx_dir,
                    render(template("model.md"), cfg), "model catalog stub")
        ensure_file(root, "%s/GLOSSARY.md" % ctx_dir,
                    render(template("GLOSSARY.md"), cfg), "glossary stub")
        ensure_file(root, "%s/sources.yaml" % ctx_dir, template("sources.yaml"),
                    "evidence-source manifest (empty seam)")
        for sub in ("actors", "commands", "events", "policies", "readmodels"):
            ensure_dir(root, "%s/%s" % (ctx_dir, sub), "model nodes: " + sub)
        # Interpreter hook wiring is plugin-side (hooks/hooks.json runs
        # hooks/scripts/interpreter.py over <model_dir>/*/policies/*.md); the
        # policies directory above is its glob target, and the rules block
        # below records the wiring in the durable layer.

    # 5. CLAUDE.md marker block (per profile) + settings deny rules.
    block_template = {"capture": "CLAUDE-block-capture.md",
                      "experiments": "CLAUDE-block-experiments.md",
                      "modeling": "CLAUDE-block-modeling.md"}[profile]
    install_claude_block(root, render(template(block_template), cfg))
    deny_rules = json.loads(render(template("settings-deny.json"),
                                   cfg))["permissions"]["deny"]
    merge_settings(root, deny_rules)

    print("done (profile: %s). Review with git status / git diff, then commit "
          "the scaffold as one attributed commit." % profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
