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
deny rules, the installed scripts), appends any missing merge-shape row to
.gitattributes (templates/gitattributes: the work ledger and the passive feedback
ledger merge by union, the compiled projections regenerate; never removes a consumer line), and never overwrites consumer-owned content
(the index, notes, raw files, fragments, the ledger, registered specs, an edited
template, model nodes, or an existing GOVERNANCE.md). Re-running with the same
inputs is a byte-level no-op. Prints one line per artifact: created / updated /
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
                        PROFILES, render, safe_rel_path)

PATH_KEYS = [k for k in DEFAULTS if k not in ("profile", "context", "model_dir")]
# The work ledger: the append-only JSONL store that scripts/decisions.py, the
# session resolver, and dashboard sections 1-2 all read at this default path.
LEDGER_RELPATH = os.path.join("ledger", "ledger.jsonl")
# The passive feedback ledger: the append-only JSONL store scripts/om-worker.py writes one
# session-observed / model-evaluated row into (.claude/hyp.json om_feedback_file overrides it;
# lab H-DRAFT-35397146-om-worker-deterministic). Created by the worker on first write, not here.
OM_FEEDBACK_RELPATH = os.path.join("ledger", "om-feedback.jsonl")
# The substrate probe ledger: one substrate-discovered row per handle per probe, written by
# scripts/om-integrate.py (.claude/hyp.json `om_substrates_file` overrides it;
# lab H-DRAFT-e2a5e911-om-integrate-probe). Created by the integrator on first write, not here.
OM_SUBSTRATES_RELPATH = os.path.join("ledger", "om-substrates.jsonl")
# The routing ledger: one agent-route/v1 row per finished workflow agent, written by
# hooks/scripts/routing-ledger.py (.claude/hyp.json `routing_ledger_file` overrides it;
# lab H-DRAFT-38f86fad-routing-ledger-row). Created by the writer on first write, not here.
ROUTING_LEDGER_RELPATH = os.path.join("ledger", "routing-ledger.jsonl")

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


def ensure_ledger(root, relpath, label):
    """created / unchanged: the ledger is append-only consumer data, so an
    existing file is never compared against a canonical or rewritten."""
    path = os.path.join(root, relpath)
    if read(path) is None:
        write(path, "")
        print("created   %s  (%s)" % (relpath, label))
    else:
        print("unchanged %s  (%s — append-only, never rewritten)" % (relpath, label))


def _attr_rows(text):
    """[(pattern, set(attrs))] for every non-comment line of a .gitattributes text."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rows.append((parts[0], set(parts[1:])))
    return rows


def ensure_gitattributes(root, canonical, label):
    """created / updated / unchanged: the merge shapes of the files the plugin's hooks and
    writers touch (templates/gitattributes). A row is present when some line names the same
    pattern with every canonical attribute; missing rows are APPENDED (a later line overrides
    an earlier one for the same attribute, so the plugin's shape wins without deleting the
    consumer's lines). Consumer-owned lines are never removed or rewritten; re-running with
    the same inputs is a byte-level no-op. Lab H-DRAFT-b9e771b2-hook-writes-worktree: a
    worktree that appended a ledger row and recompiled the dashboard could not merge into
    another without a manual edit until these rows existed."""
    relpath = ".gitattributes"
    path = os.path.join(root, relpath)
    current = read(path)
    if current is None:
        write(path, canonical)
        print("created   %s  (%s)" % (relpath, label))
        return
    have = _attr_rows(current)
    missing = []
    for raw in canonical.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, attrs = line.split()[0], set(line.split()[1:])
        if not any(p == pattern and attrs <= a for p, a in have):
            missing.append(line)
    if not missing:
        print("unchanged %s  (%s)" % (relpath, label))
        return
    block = "\n".join(missing) + "\n"
    header = "# hyp: merge shapes for the files the plugin's hooks and writers touch"
    if header not in current:
        block = header + " (added by /hyp:init; see the plugin's templates/gitattributes for the why)\n" + block
    sep = "" if current.endswith("\n") else "\n"
    write(path, current + sep + block)
    print("updated   %s  (%s — %d row(s) appended: %s)"
          % (relpath, label, len(missing), ", ".join(m.split()[0] for m in missing)))


def _ignore_rows(text):
    """The set of non-comment, non-blank lines of a .gitignore text, verbatim."""
    return {ln.strip() for ln in (text or "").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def ensure_gitignore(root, canonical, label):
    """created / updated / unchanged: rows the plugin needs ignored (templates/gitignore).
    A row is present when some existing non-comment line matches it verbatim, or matches its
    un-anchored form (a consumer already carrying `operating-model/*/model.md` is not handed
    `/operating-model/*/model.md` as a second row: the un-anchored pattern ignores a superset);
    missing rows are APPENDED under a header comment; a consumer's own lines are never removed or rewritten;
    re-running with the same inputs is a byte-level no-op. Mirrors ensure_gitattributes's
    contract (H-DRAFT-b9e771b2), for a plain ignore file with no per-line attributes.
    Lab H-DRAFT-4e06e157-om-rows-merge-shape: two adopters editing model.md by hand on separate
    branches could not merge without a manual step until this row existed."""
    relpath = ".gitignore"
    path = os.path.join(root, relpath)
    current = read(path)
    if current is None:
        write(path, canonical)
        print("created   %s  (%s)" % (relpath, label))
        return
    have = _ignore_rows(current)
    canon_rows = [ln.strip() for ln in canonical.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    missing = [ln for ln in canon_rows if ln not in have and ln.lstrip("/") not in have]
    if not missing:
        print("unchanged %s  (%s)" % (relpath, label))
        return
    block = "\n".join(missing) + "\n"
    header = "# hyp: the operating-model catalogue is a regenerated projection"
    if header not in current:
        block = header + " (added by /hyp:init; see templates/gitignore for the why)\n" + block
    sep = "" if current.endswith("\n") else "\n"
    write(path, current + sep + block)
    print("updated   %s  (%s -- %d row(s) appended: %s)"
          % (relpath, label, len(missing), ", ".join(missing)))


def retire_tracked_model_md(root, relpath, label):
    """One-time retire: if git already tracks relpath (an upgrade from the old, hand-maintained
    shape), `git rm --cached -q` it -- index only, the work-tree file is left in place -- and
    print one `retired` line naming the retire -- only when git's exit status is 0. When git
    declines (a staged model.md edit differing from both HEAD and the work tree makes
    `git rm --cached` exit 1 and leave the path tracked) print one
    `retire-refused <relpath>: <git's first stderr line>` line instead, never `retired`, so init
    never reports a retire that did not happen (cold-refuter finding, ship round 4). A no-op
    (silent) when the path is untracked already (a fresh scaffold, or a repository already
    retired): re-running is idempotent. Requires no git repository; failures are swallowed
    (fail-open, like every other init-scaffold step) because a consumer scaffolding outside a
    repository has nothing to retire."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--", relpath],
                              capture_output=True, text=True, timeout=30)
    except OSError:
        return
    if out.returncode != 0 or not out.stdout.strip():
        return
    try:
        rm = subprocess.run(["git", "-C", root, "rm", "--cached", "-q", "--", relpath],
                            capture_output=True, text=True, timeout=30, check=False)
    except OSError:
        return
    if rm.returncode != 0:
        reason = (rm.stderr or rm.stdout or "").strip().splitlines()
        print("retire-refused %s: %s" % (relpath, reason[0] if reason else "git rm --cached exit %d" % rm.returncode))
        return
    print("retired   %s  (%s -- git rm --cached; work-tree file kept)" % (relpath, label))


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
    # Keep-if-customized (2026-09-01, H-221's migration lane): an unconditional
    # overwrite destroyed a consumer's amended preflight during migration
    # rehearsal — the same silent-data-loss class as the profile drop. Install
    # when absent; overwrite only a byte-identical-to-shipped copy (a no-op);
    # a customized copy is KEPT with a printed notice so nothing is lost
    # silently and the consumer can diff at leisure.
    src = read(os.path.join(PLUGIN_ROOT, *src_relparts))
    if src is None:
        return
    existing = read(os.path.join(root, relpath))
    if existing is not None and existing != src:
        print("kept your customized %s (%s differs from the shipped copy; "
              "compare against the plugin's %s)" % (relpath, label,
                                                    os.path.join(*src_relparts)))
        return
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
    prior = None
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
    # Consumer-owned keys ride along (om-worker ship fix round 1, refuter B1): the config rewrite
    # below is canonical for DEFAULTS only, so every prior key outside DEFAULTS -- ledger_file,
    # om_feedback_file, compile_command, the decision-brief settings, anything a later release or
    # the consumer added -- is carried verbatim (any JSON value). Before this, the documented
    # "re-run /hyp:init once" dropped the override on every re-init, and the worker fell back to
    # the default ledger path with no union row behind it. Only string values reach render().
    carried = {}
    if isinstance(prior, dict):
        for key, value in prior.items():
            if key not in DEFAULTS:
                carried[key] = value
    config_out = dict(cfg)
    config_out.update(carried)

    # 1. Config file (plugin-owned; canonical for the chosen profile + paths; consumer keys kept).
    ensure_file(root, CONFIG_RELPATH,
                json.dumps(config_out, indent=2, sort_keys=True) + "\n",
                "profile + path configuration", overwrite=True)

    # 2. Capture layer (every profile).
    ensure_dir(root, cfg["raw_dir"], "raw verbatim sources, write-once")
    ensure_dir(root, cfg["notes_dir"], "distilled notes")
    ensure_dir(root, cfg["journal_dir"], "write-once journal fragments")
    ensure_ledger(root, LEDGER_RELPATH, "work ledger: decisions, commitments, claims")
    # merge shapes (H-DRAFT-b9e771b2-hook-writes-worktree): the ledger the plugin's writer
    # appends to (the configured ledger_file when the consumer overrides it) and the
    # leak-meter fires log merge by union; the compiled projections are derived, regenerated
    # after a merge and never merged by lines. A .gitattributes travels with the repository,
    # unlike a merge driver in git config.
    # One validator for both overrides (hyp_config.safe_rel_path, the worker's own rule): a value
    # the worker refuses (absolute, or a `..` hop) renders the DEFAULT row, never a mangled one.
    ledger_rel = safe_rel_path(carried.get("ledger_file"), LEDGER_RELPATH.replace(os.sep, "/"))
    om_feedback_rel = safe_rel_path(carried.get("om_feedback_file"),
                                    OM_FEEDBACK_RELPATH.replace(os.sep, "/"))
    om_substrates_rel = safe_rel_path(carried.get("om_substrates_file"),
                                      OM_SUBSTRATES_RELPATH.replace(os.sep, "/"))
    routing_ledger_rel = safe_rel_path(carried.get("routing_ledger_file"),
                                       ROUTING_LEDGER_RELPATH.replace(os.sep, "/"))
    ensure_gitattributes(root, render(template("gitattributes"),
                                      dict(cfg, ledger_file=ledger_rel, om_feedback_file=om_feedback_rel,
                                           om_substrates_file=om_substrates_rel,
                                           routing_ledger_file=routing_ledger_rel)),
                         "merge shapes: ledger rows merge by union, projections regenerate")
    ensure_file(root, cfg["index_file"], template("index.md"), "wiki index seed")
    ensure_file(root, "GOVERNANCE.md", template("GOVERNANCE.md"),
                "behavioral invariants")
    install_script(root, os.path.join("scripts", "compile-journal.py"),
                   ("scripts", "compile-journal.py"), "journal compiler")
    # Model-routing guard (H-DRAFT-314c8d17-routing-guard, VERDICT.json evidence-sufficient
    # promote): every profile, since a Workflow-tool call can happen regardless of profile.
    # ensure_file's default overwrite=False -- a consumer's edited table is never rewritten;
    # the generic "kept ..." line ensure_file prints is the only report (hooks/scripts/
    # drift-check.py compares the CLAUDE.md block and its template set and does not read
    # .claude/routing.json), the same as the hypotheses template and GOVERNANCE.md.
    ensure_file(root, os.path.join(".claude", "routing.json"), template("routing.json"),
                "model-routing override table (advise until routing.enforce is set to deny "
                "in .claude/hyp.json)")

    # 3. Experiments layer.
    if at_least("experiments"):
        ensure_dir(root, cfg["hypotheses_dir"], "hypothesis specs, one file each")
        ensure_dir(root, cfg["runs_dir"],
                   "run artifacts: <id>/fixture/ shared inputs, <id>/run-<k>/ outputs")
        ensure_file(root, cfg["template_file"],
                    render(template("HYPOTHESIS-TEMPLATE.md"), cfg), "spec template")
        install_script(root, cfg["preflight_file"], ("scripts", "preflight.py"),
                       "deterministic spec pre-flight")
        # Destination-map program (lab H-DRAFT-2cae0933): the north-star convention (one tracked file per
        # destination under ledger/north-stars/, status derived at read time by the
        # plugin's scripts/north-star-check.py). Only the README is scaffolded --
        # consumer-owned once created, no .gitkeep, no example file.
        ensure_file(root, "ledger/north-stars/README.md",
                    template("north-stars-README.md"),
                    "north-star file convention (ledger/north-stars/<slug>.md)")
        # H-254 keep: the reflex install self-test runs as a required install/update
        # step — a seeded synthetic slowdown must fire the detector and land an
        # autopsy record, end to end, before the install reports healthy. Report-only
        # at init time (a failed drill prints loudly but never blocks scaffolding).
        selftest = os.path.join(PLUGIN_ROOT, "scripts", "reflex-selftest")
        if os.path.exists(selftest):
            import subprocess
            r = subprocess.run([sys.executable, selftest, "--root", root],
                               capture_output=True, text=True, timeout=120)
            tail = (r.stdout or r.stderr or "").strip().splitlines()
            print("reflex-selftest: %s" % (tail[-1] if tail else "rc=%d" % r.returncode))

    # 4. Modeling layer.
    if at_least("modeling"):
        model_dir = cfg["model_dir"]
        ctx_dir = "%s/%s" % (model_dir, cfg["context"])
        schema_src = read(os.path.join(PLUGIN_ROOT, "kernel", "operating-model",
                                       "SCHEMA.md"))
        if schema_src is not None:
            ensure_file(root, "%s/SCHEMA.md" % model_dir, schema_src,
                        "operating-model node grammar", overwrite=True)
        # H-DRAFT-4e06e157-om-rows-merge-shape: the catalogue is an untracked projection,
        # regenerated by scripts/compile-catalog.py wherever the model is read, never a
        # hand-maintained file two adopters both edit. The ignore row and the one-time retire
        # of any previously-tracked copy land before the working-tree file is (re)written.
        ensure_gitignore(root, render(template("gitignore"), cfg),
                         "model catalogue: untracked, regenerated projection")
        retire_tracked_model_md(root, "%s/model.md" % ctx_dir,
                                "model catalogue: retiring the tracked copy")
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
