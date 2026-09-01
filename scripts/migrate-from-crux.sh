#!/usr/bin/env bash
# migrate-from-crux.sh — the guided crux -> hyp migration for one repository.
#
# crux is this plugin's prior name. The raw one-liner
#   claude plugin marketplace add ... && claude plugin install ... && claude plugin uninstall crux...
# exits nonzero whenever any step finds its work already done (the H-221 case:
# uninstall fails because crux was enabled by a committed settings flag with no
# project-scope install record) even though the migrated end state is correct.
#
# This script runs the same three steps TOLERANTLY — every step's exit code is
# recorded and printed, none is fatal mid-way — and then accepts or rejects on
# the END-STATE ARTIFACTS alone (house law: artifact-based acceptance, never a
# bare rc), one plain verdict line per check:
#
#   check 1/3  hyp resolvable + enabled at project scope   (claude plugin list --json)
#   check 2/3  crux absent from project settings           (.claude/settings.json bytes)
#   check 3/3  .claude/hyp.json present                    (seeded from .claude/crux.json
#                                                           when /hyp:init has not run yet)
#
# Exit 0 iff all three checks verify. Two bounded, printed repairs may run
# before verification, both confined to the repository being migrated:
#   - a stale crux enable flag in project settings is removed ONLY when hyp
#     already verifies at project scope and no project-scope crux install
#     record exists (the flag-only state the CLI refuses to uninstall);
#   - .claude/hyp.json is seeded from .claude/crux.json via the plugin's own
#     config reader when init has not run.
# .claude/crux.json is never modified or deleted and the crux marketplace entry
# is left known, so an installed crux elsewhere keeps working and rollback
# stays a two-way door.
#
# Usage:
#   migrate-from-crux.sh [--repo DIR] [--marketplace REF]
#                        [--hyp PLUGIN@MKT] [--crux PLUGIN@MKT] [--verify-only]
#
# Defaults: --repo .  --marketplace getfatday/hyp-machine
#           --hyp hyp@hyp-machine  --crux crux@getfatday-skills
# --verify-only runs no lifecycle step and no repair: read-only end-state checks.
#
# Requirements: bash 3.2+, python3 (stdlib only), the claude CLI on PATH.

set -u

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)
PLUGIN_ROOT=$(dirname -- "$SCRIPT_DIR")

REPO="."
MARKETPLACE="getfatday/hyp-machine"
HYP_ID="hyp@hyp-machine"
CRUX_ID="crux@getfatday-skills"
VERIFY_ONLY="no"

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)        REPO="${2:?--repo needs a directory}"; shift 2 ;;
    --marketplace) MARKETPLACE="${2:?--marketplace needs a ref}"; shift 2 ;;
    --hyp)         HYP_ID="${2:?--hyp needs plugin@marketplace}"; shift 2 ;;
    --crux)        CRUX_ID="${2:?--crux needs plugin@marketplace}"; shift 2 ;;
    --verify-only) VERIFY_ONLY="yes"; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) printf 'migrate-from-crux: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

CRUX_NAME=${CRUX_ID%%@*}

cd -- "$REPO" || { printf 'migrate-from-crux: cannot cd to %s\n' "$REPO" >&2; exit 2; }
REPO_ROOT=$(pwd -P)

command -v python3 >/dev/null 2>&1 || {
  printf 'migrate-from-crux: python3 is required\n' >&2; exit 2; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/migrate-from-crux.XXXXXX") || exit 2
trap 'rm -rf "$WORK"' EXIT

HELPER="$WORK/helper.py"
cat > "$HELPER" <<'PY'
"""Artifact inspections for migrate-from-crux.sh. Stdlib only.

Every subcommand prints one human line and exits 0 (verified / done) or
1 (not verified), so the shell stays a thin sequencer.
"""
import json
import os
import sys


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None
    except ValueError:
        return "UNPARSEABLE"


def inspect_list(list_path, hyp_id):
    data = load(list_path)
    if data is None or data == "UNPARSEABLE" or not isinstance(data, list):
        print("plugin list output missing or not JSON (%s)" % list_path)
        return 1
    for entry in data:
        if isinstance(entry, dict) and entry.get("id") == hyp_id:
            enabled = entry.get("enabled") is True
            scope = entry.get("scope")
            if enabled and scope == "project":
                print("%s enabled=true scope=project version=%s"
                      % (hyp_id, entry.get("version", "?")))
                return 0
            print("%s found but enabled=%s scope=%s (want enabled=true "
                  "scope=project)" % (hyp_id, entry.get("enabled"), scope))
            return 1
    print("%s not resolvable: no such entry in claude plugin list" % hyp_id)
    return 1


def crux_flags(name, paths):
    prefix = name + "@"
    found = []
    for path in paths:
        data = load(path)
        if data is None:
            continue
        if data == "UNPARSEABLE" or not isinstance(data, dict):
            print("%s exists but is not parseable JSON — cannot verify" % path)
            return 1
        enabled = data.get("enabledPlugins")
        if isinstance(enabled, dict):
            for key in sorted(enabled):
                if key.startswith(prefix):
                    found.append("%s: %r" % (path, key))
    if found:
        print("; ".join(found))
        return 1
    print("no %s* key in project settings" % prefix)
    return 0


def crux_record(list_path, name):
    data = load(list_path)
    prefix = name + "@"
    if isinstance(data, list):
        for entry in data:
            if (isinstance(entry, dict)
                    and str(entry.get("id", "")).startswith(prefix)
                    and entry.get("scope") == "project"):
                print("project-scope install record exists: %s" % entry.get("id"))
                return 0
    print("no project-scope %s* install record" % prefix)
    return 1


def strip_flags(name, path):
    data = load(path)
    if data is None or data == "UNPARSEABLE" or not isinstance(data, dict):
        print("cannot rewrite %s" % path)
        return 1
    enabled = data.get("enabledPlugins")
    if not isinstance(enabled, dict):
        print("no enabledPlugins map in %s" % path)
        return 1
    removed = [k for k in sorted(enabled) if k.startswith(name + "@")]
    if not removed:
        print("no %s@* flag in %s" % (name, path))
        return 1
    for key in removed:
        del enabled[key]
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, sort_keys=False) + "\n")
    print("removed %s from %s" % (", ".join(removed), path))
    return 0


def seed_hyp(plugin_root, repo_root):
    sys.path.insert(0, os.path.join(plugin_root, "hooks", "scripts"))
    from hyp_config import CONFIG_RELPATH, LEGACY_CONFIG_RELPATH, load_config
    hyp_path = os.path.join(repo_root, CONFIG_RELPATH)
    if os.path.exists(hyp_path):
        print("already present")
        return 0
    if not os.path.exists(os.path.join(repo_root, LEGACY_CONFIG_RELPATH)):
        print("absent, and no %s to seed from — run /hyp:init"
              % LEGACY_CONFIG_RELPATH)
        return 1
    cfg = load_config(repo_root)  # reads crux.json when hyp.json is absent
    os.makedirs(os.path.dirname(hyp_path), exist_ok=True)
    with open(hyp_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    print("seeded from %s (profile: %s)" % (LEGACY_CONFIG_RELPATH, cfg["profile"]))
    return 0


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "inspect-list":
        return inspect_list(args[0], args[1])
    if cmd == "crux-flags":
        return crux_flags(args[0], args[1:])
    if cmd == "crux-record":
        return crux_record(args[0], args[1])
    if cmd == "strip-flags":
        return strip_flags(args[0], args[1])
    if cmd == "seed-hyp":
        return seed_hyp(args[0], args[1])
    print("unknown helper command: %s" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
PY

RC_ADD="skipped"
RC_INSTALL="skipped"
RC_UNINSTALL="skipped"

run_step() {
  # $1 step label; remaining args: the command. Tolerant: rc recorded, printed,
  # never fatal — acceptance is decided by the end-state checks alone.
  step_label="$1"; shift
  printf 'step %s: %s\n' "$step_label" "$*"
  "$@"
  step_rc=$?
  printf 'step %s rc=%d (recorded, not a verdict)\n' "$step_label" "$step_rc"
  return 0
}

if [ "$VERIFY_ONLY" = "no" ]; then
  run_step "1/3 marketplace add" claude plugin marketplace add "$MARKETPLACE"
  RC_ADD=$step_rc
  run_step "2/3 install hyp   " claude plugin install "$HYP_ID" --scope project
  RC_INSTALL=$step_rc
  run_step "3/3 uninstall crux" claude plugin uninstall "$CRUX_ID" --scope project
  RC_UNINSTALL=$step_rc
else
  printf 'verify-only: skipping the three lifecycle steps and all repairs\n'
fi

# --- end-state inspection ----------------------------------------------------

LIST_JSON="$WORK/plugin-list.json"
if ! claude plugin list --json > "$LIST_JSON" 2> "$WORK/plugin-list.err"; then
  : > "$LIST_JSON"   # checks below report the artifact as missing
  printf 'note: claude plugin list --json failed: %s\n' "$(cat "$WORK/plugin-list.err")"
fi

PROJ_SETTINGS="$REPO_ROOT/.claude/settings.json"
PROJ_SETTINGS_LOCAL="$REPO_ROOT/.claude/settings.local.json"

# Check 1 is computed up front: it also gates the flag repair below, so a
# migration that is failing anyway never edits the repository's settings.
C1_OK=no
if c1=$(python3 "$HELPER" inspect-list "$LIST_JSON" "$HYP_ID"); then C1_OK=yes; fi

if [ "$VERIFY_ONLY" = "no" ]; then
  # Repair a stale enable flag: only when hyp is already healthy at project
  # scope AND the CLI holds NO project-scope crux install record (the
  # flag-only state uninstall refuses to touch). Scoped to this repository's
  # settings files; the crux marketplace entry stays known.
  if [ "$C1_OK" = "yes" ] && ! python3 "$HELPER" crux-flags "$CRUX_NAME" \
        "$PROJ_SETTINGS" "$PROJ_SETTINGS_LOCAL" >/dev/null 2>&1; then
    if ! python3 "$HELPER" crux-record "$LIST_JSON" "$CRUX_NAME" >/dev/null 2>&1; then
      for f in "$PROJ_SETTINGS" "$PROJ_SETTINGS_LOCAL"; do
        [ -f "$f" ] || continue
        repair_out=$(python3 "$HELPER" strip-flags "$CRUX_NAME" "$f") \
          && printf 'repair: %s (stale enable flag; no project-scope install record)\n' "$repair_out"
      done
    fi
  fi
  seed_out=$(python3 "$HELPER" seed-hyp "$PLUGIN_ROOT" "$REPO_ROOT") \
    && case "$seed_out" in seeded*) printf 'repair: .claude/hyp.json %s\n' "$seed_out" ;; esac
fi

# --- verdicts (one line per check; the only thing that decides the exit) -----

FAILURES=0

if [ "$C1_OK" = "yes" ]; then
  printf 'check 1/3: hyp resolvable + enabled at project scope: PASS — %s\n' "$c1"
else
  printf 'check 1/3: hyp resolvable + enabled at project scope: FAIL — %s\n' "$c1"
  FAILURES=$((FAILURES + 1))
fi

if c2=$(python3 "$HELPER" crux-flags "$CRUX_NAME" "$PROJ_SETTINGS" "$PROJ_SETTINGS_LOCAL"); then
  printf 'check 2/3: crux absent from project settings: PASS — %s\n' "$c2"
else
  printf 'check 2/3: crux absent from project settings: FAIL — %s\n' "$c2"
  FAILURES=$((FAILURES + 1))
fi

if [ -f "$REPO_ROOT/.claude/hyp.json" ]; then
  c3="present"
  case "${seed_out:-}" in seeded*) c3="$seed_out" ;; esac
  printf 'check 3/3: .claude/hyp.json present: PASS — %s\n' "$c3"
else
  printf 'check 3/3: .claude/hyp.json present: FAIL — %s\n' \
    "${seed_out:-absent — run /hyp:init}"
  FAILURES=$((FAILURES + 1))
fi

if [ "$FAILURES" -eq 0 ]; then
  printf 'migrate-from-crux: end state VERIFIED — exit 0 (step rc recorded: add=%s install=%s uninstall=%s; .claude/crux.json left in place — crux keeps working)\n' \
    "$RC_ADD" "$RC_INSTALL" "$RC_UNINSTALL"
  exit 0
fi
printf 'migrate-from-crux: end state NOT verified — %d check(s) failed, exit 1 (step rc recorded: add=%s install=%s uninstall=%s)\n' \
  "$FAILURES" "$RC_ADD" "$RC_INSTALL" "$RC_UNINSTALL"
exit 1
