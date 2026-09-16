#!/usr/bin/env python3
"""om-integrate.py -- deterministic host/repo passive-offload integrator.

Verbs: probe --json | compose | emit --agents-dir D | test | report | uninstall

Probes candidate background substrates by RUNNING each handle's recorded command and reading its
real exit code (never sniffing the OS name), composes a plist/workflow from the `usable` rows
alone, drives a synthetic inbox record through the pinned worker under a real external substrate
process (`launchctl`, resolved on PATH -- a stub shim under test/selftest, a real launchd on a
host that ships one) that plays the QueueDirectories-fire / ThrottleInterval-throttle role, and
reports drift + mixed plugin versions across a repository's worktrees. `emit` never activates
anything it writes: a launchd plist is written but never `launchctl load`ed, and the `ci-tier0`
remote handle is delegated whole to `scripts/om-ci.py emit ci-tier0` (the shipped tier-0 CI
emitter) rather than carrying a second copy of that workflow's template. Every probe row carries
a `void` field: `None` when the probe command ran to completion, else the typed void
(`timeout` / `not-found` / `os-error:<T>`) of the subprocess that never answered -- a stalled
`gh` or `launchctl` is never collapsed into a substantive `usable: false`; `compose`, `emit` and
`report` print one `probe-void: <handle>:<void>` line per voided row so a caller can read the
probe as ambiguous instead of grading a refusal.

`probe --json` appends the nine rows to the consumer's `ledger/om-substrates.jsonl`
(`.claude/hyp.json` `om_substrates_file` overrides the path; `merge=union` -- see
`docs/passive-feedback.md`), one canonical JSON object per line.

Zero LLM calls anywhere in this file. Python 3.9, stdlib only.

Ported by intent from the lab keep getfatday/cause-n-effect
H-DRAFT-e2a5e911-om-integrate-probe (kept 2026-09-15: five counted looks, A1-A5 pass in every
one, cold-verified). Drift from the kept fixture bytes, disclosed here rather than hidden in a
diff:

1. The `ci-tier0` remote handle no longer carries its own workflow-emitting string template
   (the fixture's `WORKFLOW_TMPL`, which rendered `workflow_dispatch: {{}}` -- a `str.format`
   escaping defect PyYAML rejects, VERIFY.md finding 1 of that lane). This file instead shells
   out to `scripts/om-ci.py emit ci-tier0`, the tier-0 CI emitter this plugin already ships
   (v0.33.0), which vendors its own dependencies into `.github/om-scripts/` and renders a workflow
   that parses. `uninstall` removes whatever that call reported, plus the vendor tree.
2. The on-device `launchd-queue` plist's `QueueDirectories`/log paths now name the SAME inbox
   directory `scripts/om-worker.py`'s own default (`--inbox`-less) `drain` will actually read
   (`state_root_for_repo`/`state_root`, keyed the same way, duplicated below with attribution)
   -- the fixture's plist pointed `QueueDirectories` at `<root>/.claude/om-state/inbox` while its
   `ProgramArguments` ran `drain` with no `--inbox` override, so a real launchd load would have
   watched one directory while the worker it launched drained a different one. The fixture's own
   `test` verb never caught this because it drives the worker with an explicit `--inbox`
   override (a deliberate, disclosed test-only scratch location, unchanged here).
3. Every probe row gains a `host_key` field (short crc32+adler32 hex of hostname/platform/user,
   the same recipe `hooks/scripts/session-start-budget.py`'s state-directory key uses) beside
   `authors_90d` and `disk`: `usable` is a host-specific fact, and a `ledger/om-substrates.jsonl`
   shared across machines needs a way to tell rows from different hosts apart. Not present in the
   kept fixture bytes.
4. `check` is named `report` (the CONTEXT's verb list), `--root`/`--worker`/`--plugin-scripts`
   default from `.claude/hyp.json` / this file's own directory instead of being required on every
   call (`hyp_config.resolve_root`, the plugin's own path-resolution contract), and `emit` no
   longer takes a `--workflows-dir` (the om-ci.py delegation computes that path itself).
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import zlib

SCHEMA = 1
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "hooks", "scripts"))
from hyp_config import resolve_root as _hyp_resolve_root, safe_rel_path  # noqa: E402

# compose order, frozen at registration (Method): first probe-verified handle wins.
ON_DEVICE_ORDER = ["launchd-queue", "systemd-user", "schtasks-idle", "desktop-task",
                    "cron-anacron", "hook-oneshot", "none"]
REMOTE_ORDER = ["ci-tier0", "ampersand", "routine", "none"]

LOCK_REL = os.path.join(".claude", "om-offload.lock.json")
HYP_JSON_REL = os.path.join(".claude", "hyp.json")
OM_SUBSTRATES_DEFAULT_REL = "ledger/om-substrates.jsonl"
OM_FEEDBACK_DEFAULT_REL = "ledger/om-feedback.jsonl"
WORKFLOW_REL = os.path.join(".github", "workflows", "om-check.yml")


# --------------------------------------------------------------------------- small helpers
def _which(name):
    return shutil.which(name)


def _run(cmd, timeout=8, env=None, cwd=None):
    """Run a probe/utility command, catching every subprocess failure as a typed void instead
    of crashing: every subprocess call in this file runs under a caught timeout that records a
    typed void rather than raising."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        return {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "void": None}
    except subprocess.TimeoutExpired:
        return {"exit": None, "stdout": "", "stderr": "", "void": "timeout"}
    except FileNotFoundError:
        return {"exit": None, "stdout": "", "stderr": "", "void": "not-found"}
    except OSError as exc:
        return {"exit": None, "stdout": "", "stderr": "", "void": "os-error:%s" % type(exc).__name__}


def authors_90d(root):
    """Distinct committer addresses in the last 90 days, `[bot]` / `noreply` addresses filtered."""
    r = _run(["git", "log", "--since=90.days", "--format=%ae"], timeout=10, cwd=root)
    if r["exit"] != 0:
        return 0
    addrs = set()
    for line in r["stdout"].splitlines():
        a = line.strip().lower()
        if not a:
            continue
        if "[bot]" in a or "noreply" in a:
            continue
        addrs.add(a)
    return len(addrs)


def disk_covariate(cache_dir=None):
    cache_dir = cache_dir or os.path.expanduser("~/.claude/plugins/cache")
    try:
        st = os.statvfs(os.path.expanduser("~"))
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = None
    size = 0
    if os.path.isdir(cache_dir):
        for dirpath, _dirnames, filenames in os.walk(cache_dir):
            for fn in filenames:
                try:
                    size += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    return {"free_bytes": free, "plugin_cache_bytes": size}


def _crc_adler_key(raw):
    """The plugin's other state-directory key recipe (`hooks/scripts/session-start-budget.py`
    `state_dir`, `scripts/om-worker.py` `repo_key`/`path_key`), duplicated here for the same
    reason `hyp_config.safe_rel_path` documents duplicating its own rule: every reader of one
    convention shares the SAME formula, not an import of the same module (this file must not
    grow a dependency a consumer's vendored copy would not carry)."""
    b = str(raw).encode("utf-8", "replace")
    return "%08x%08x" % (zlib.crc32(b) & 0xFFFFFFFF, zlib.adler32(b) & 0xFFFFFFFF)


def host_key():
    raw = "%s:%s:%s" % (socket.gethostname(), sys.platform,
                        os.environ.get("USER") or os.environ.get("LOGNAME") or "")
    return _crc_adler_key(raw)


def _git_common_dir(root):
    r = _run(["git", "-C", root, "rev-parse", "--git-common-dir"], timeout=5)
    if r["exit"] != 0:
        return None
    out = r["stdout"].strip()
    if not out:
        return None
    common = out if os.path.isabs(out) else os.path.abspath(os.path.join(root, out))
    return os.path.realpath(common)


def worker_state_root(root):
    """The SAME directory `scripts/om-worker.py`'s own default (`--inbox`-less) `drain` will
    read from for this repository: `state_root_for_repo(common_dir)` when `root` is a git
    checkout git can answer for within the probe timeout, else the single-path `state_root(root)`
    fallback -- both duplicated from `om-worker.py` (`HYP_STATE_DIR` override,
    `~/.hyp-state/om/<key>` base). Used only to pick WHERE to point a plist's `QueueDirectories`
    and log paths; the worker itself remains the single source of truth for where it actually
    drains (`om-worker.py` `state_root_for_repo`/`state_root`)."""
    override = os.environ.get("HYP_STATE_DIR")
    base = override if override else os.path.join(os.path.expanduser("~"), ".hyp-state")
    common_dir = _git_common_dir(root)
    if common_dir:
        return os.path.join(base, "om", _crc_adler_key(common_dir))
    key = hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(base, "om", key)


def _plugin_scripts_dir(val):
    d = val or os.environ.get("HYP_OM_PLUGIN_SCRIPTS") or HERE
    if not os.path.isfile(os.path.join(d, "om-worker.py")) or not os.path.isfile(os.path.join(d, "om-ci.py")):
        raise SystemExit("om-integrate: %s does not hold om-worker.py and om-ci.py (name the "
                         "plugin scripts/ directory with --plugin-scripts or $HYP_OM_PLUGIN_SCRIPTS)" % d)
    return os.path.abspath(d)


def _resolve_root(val):
    return os.path.abspath(val) if val else _hyp_resolve_root()


def _consumer_config(root):
    try:
        with open(os.path.join(root, HYP_JSON_REL), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def om_substrates_rel(root):
    return safe_rel_path(_consumer_config(root).get("om_substrates_file"), OM_SUBSTRATES_DEFAULT_REL)


def om_substrates_path(root):
    return os.path.join(root, *om_substrates_rel(root).split("/"))


def om_feedback_rel(root):
    return safe_rel_path(_consumer_config(root).get("om_feedback_file"), OM_FEEDBACK_DEFAULT_REL)


def om_feedback_path(root):
    return os.path.join(root, *om_feedback_rel(root).split("/"))


# --------------------------------------------------------------------------- handle table
def _probe_launchd_queue():
    """Both booleans are RUN, never sniffed. `advertised` is the recorded `command -v launchctl`
    exit code; `usable` is a real `launchctl print gui/<uid>` exit code (0 iff the per-user
    launchd domain answers)."""
    cmd_adv = "command -v launchctl"
    r_adv = _run(["sh", "-c", cmd_adv], timeout=5)
    advertised = r_adv["exit"] == 0
    cmd_use = "launchctl print gui/%s" % os.getuid()
    r_use = _run(["sh", "-c", cmd_use], timeout=5)
    usable = r_use["exit"] == 0
    return {"advertised": advertised, "usable": usable, "probe_cmd": cmd_use,
            "exit": r_use["exit"] if r_use["exit"] is not None else 1,
            "void": r_use["void"] or r_adv["void"],
            "evidence": "%s rc %s (advertised: %s rc %s)" % (cmd_use, r_use["exit"], cmd_adv, r_adv["exit"])}


def _probe_systemd_user():
    path = _which("systemctl")
    cmd = "systemctl --user is-system-running"
    if not path:
        return {"advertised": False, "usable": False, "probe_cmd": "command -v systemctl",
                "exit": 1, "void": None, "evidence": "systemctl not on PATH"}
    r = _run(["systemctl", "--user", "is-system-running"], timeout=5)
    ok = r["exit"] == 0
    return {"advertised": True, "usable": ok, "probe_cmd": cmd,
            "exit": r["exit"] if r["exit"] is not None else 1, "void": r["void"],
            "evidence": "systemctl --user answered rc %s" % r["exit"]}


def _command_v(name):
    """`command -v <name>` is RUN as a real `sh -c` subprocess and its real exit code recorded
    -- never an exit synthesized from `shutil.which`."""
    cmd = "command -v %s" % name
    r = _run(["sh", "-c", cmd], timeout=5)
    rc = r["exit"] if r["exit"] is not None else 1
    return cmd, rc, r["void"]


def _probe_cron_anacron():
    cmd, rc, void = _command_v("crontab")
    if rc != 0:
        return {"advertised": False, "usable": False, "probe_cmd": cmd, "exit": rc, "void": void,
                "evidence": "crontab not on PATH (%s rc %s)" % (cmd, rc)}
    return {"advertised": True, "usable": True, "probe_cmd": cmd, "exit": 0, "void": void,
            "evidence": "crontab on PATH (%s rc 0); usable for the deterministic tier only (no LLM call may ride this handle)" % cmd}


def _probe_schtasks_idle():
    cmd, rc, void = _command_v("schtasks")
    present = rc == 0
    return {"advertised": present, "usable": present, "probe_cmd": cmd, "exit": rc, "void": void,
            "evidence": "schtasks on PATH (%s rc 0)" % cmd if present
            else "schtasks not on PATH (%s rc %s; Windows-only handle)" % (cmd, rc)}


def _probe_desktop_task():
    # advertised-but-unverified on purpose: osascript can plant a login item, but nothing here
    # has actually verified that a login item survives or fires on schedule, so usable stays
    # false pending a real probe.
    cmd, rc, void = _command_v("osascript")
    return {"advertised": rc == 0, "usable": False, "probe_cmd": cmd, "exit": rc, "void": void,
            "evidence": "osascript %s (%s rc %s) but no scheduled-firing probe exists yet (disclosed gap)"
            % ("present" if rc == 0 else "absent", cmd, rc)}


def _probe_hook_oneshot():
    # per-turn hook wake was tried and abandoned by a separate lab lane (evidence-sufficient hold
    # at low n): this handle reads unusable until a future lane reopens the question.
    return {"advertised": False, "usable": False,
            "probe_cmd": "n/a: per-turn hook wake not usable (lab hold)",
            "exit": 1, "void": None, "evidence": "per-turn hook wake abandoned in the source lab"}


def _probe_ci_tier0(remote_host):
    path = _which("gh")
    cmd = "gh auth status --hostname %s" % remote_host
    if not path:
        return {"advertised": False, "usable": False, "probe_cmd": "command -v gh", "exit": 1,
                "void": None, "evidence": "gh not on PATH"}
    r = _run(["gh", "auth", "status", "--hostname", remote_host], timeout=8)
    ok = r["exit"] == 0
    return {"advertised": True, "usable": ok, "probe_cmd": cmd,
            "exit": r["exit"] if r["exit"] is not None else 1, "void": r["void"],
            "evidence": "gh auth status --hostname %s rc %s" % (remote_host, r["exit"])}


def _probe_ampersand():
    cmd, rc, void = _command_v("ampersand")
    present = rc == 0
    return {"advertised": present, "usable": present, "probe_cmd": cmd, "exit": rc, "void": void,
            "evidence": "ampersand CLI %s (%s rc %s)" % ("present" if present else "absent", cmd, rc)}


def _probe_routine():
    return {"advertised": False, "usable": False,
            "probe_cmd": "n/a: no local capability probe for a cloud routine",
            "exit": 1, "void": None, "evidence": "a routine is a cloud schedule, not a local capability; not attempted"}


def probe_all(root, remote_host="github.com"):
    """Run every handle's probe once; return the 9 rows (schema-shaped, covariates attached)."""
    fns = {
        "launchd-queue": _probe_launchd_queue,
        "systemd-user": _probe_systemd_user,
        "cron-anacron": _probe_cron_anacron,
        "schtasks-idle": _probe_schtasks_idle,
        "desktop-task": _probe_desktop_task,
        "hook-oneshot": _probe_hook_oneshot,
        "ci-tier0": lambda: _probe_ci_tier0(remote_host),
        "ampersand": _probe_ampersand,
        "routine": _probe_routine,
    }
    a90 = authors_90d(root)
    disk = disk_covariate()
    hkey = host_key()
    rows = []
    for handle, fn in fns.items():
        r = fn()
        rows.append({
            "kind": "substrate-discovered", "schema": SCHEMA, "handle": handle,
            "advertised": bool(r["advertised"]), "usable": bool(r["usable"]),
            "probe_cmd": r["probe_cmd"], "exit": int(r["exit"]) if r["exit"] is not None else 1,
            "void": r.get("void"),
            "evidence": r["evidence"], "authors_90d": a90, "disk": disk, "host_key": hkey,
        })
    return rows


def probe_voids(rows):
    """The `<handle>:<void>` list of every row whose probe never answered (timeout / not-found /
    os-error)."""
    return sorted("%s:%s" % (r["handle"], r["void"]) for r in rows if r.get("void"))


def _print_probe_voids(rows):
    for item in probe_voids(rows):
        print("probe-void: %s" % item)


def _canonical_row(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"


def append_substrate_rows(root, rows, dest=None):
    path = dest or om_substrates_path(root)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(_canonical_row(row))
    return path


def cmd_probe(args):
    rows = probe_all(args.root, args.remote_host)
    dest = append_substrate_rows(args.root, rows, args.out)
    if args.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        print("probe: wrote %d row(s) to %s" % (len(rows), dest))
    _print_probe_voids(rows)
    return 0


# --------------------------------------------------------------------------- compose
def compose(rows):
    """Pick exactly one on-device handle and at most one remote handle from the `usable` rows
    ALONE -- covariates (`authors_90d`, `disk`, `host_key`) are never a compose input."""
    usable = {r["handle"]: r["usable"] for r in rows}
    on_device = "none"
    for h in ON_DEVICE_ORDER:
        if h == "none":
            break
        if usable.get(h):
            on_device = h
            break
    remote = "none"
    for h in REMOTE_ORDER:
        if h == "none":
            break
        if usable.get(h):
            remote = h
            break
    return {"on_device": on_device, "remote": remote}


def cmd_compose(args):
    rows = probe_all(args.root, args.remote_host)
    result = compose(rows)
    print(json.dumps(result, sort_keys=True))
    _print_probe_voids(rows)
    if result["on_device"] == "none":
        print("om-integrate: no probe-verified on-device handle; nothing to compose (handle=none)")
    return 0


# --------------------------------------------------------------------------- emit
def _rootkey(root):
    return hashlib.sha1(os.path.abspath(root).encode("utf-8")).hexdigest()[:12]


def _read_plist_template():
    path = os.path.join(PLUGIN_ROOT, "templates", "offload", "om-worker.plist")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _emit_launchd_plist(root, agents_dir, worker_path, plugin_scripts):
    """Writes (never loads) the `launchd-queue` plist. Returns its path. `QueueDirectories` and
    the log paths name `worker_state_root(root)` -- the same directory the worker's own default
    `drain` reads from -- so `ProgramArguments` runs plain `drain --root ... --plugin-scripts ...`
    with no `--inbox` override, matching what a real launchd load would actually fire against."""
    os.makedirs(agents_dir, exist_ok=True)
    state_dir = worker_state_root(root)
    inbox = os.path.join(state_dir, "inbox")
    os.makedirs(state_dir, exist_ok=True)
    label = "com.hyp-machine.om-worker.%s" % _rootkey(root)
    plist_path = os.path.join(agents_dir, label + ".plist")
    body = _read_plist_template().format(
        label=label, python3=os.path.abspath(shutil.which("python3") or "/usr/bin/python3"),
        worker=os.path.abspath(worker_path), root=os.path.abspath(root),
        plugin_scripts=os.path.abspath(plugin_scripts), inbox=os.path.abspath(inbox),
        state_dir=os.path.abspath(state_dir))
    with open(plist_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return plist_path


def _emit_ci_tier0(root, plugin_scripts):
    """Delegates whole to `om-ci.py emit ci-tier0` (the shipped tier-0 CI emitter) -- never a
    second workflow template. Returns the list of repo-relative paths that call reported
    (created/unchanged/updated/kept), for the lock's own uninstall accounting."""
    om_ci = os.path.join(plugin_scripts, "om-ci.py")
    r = _run([sys.executable, om_ci, "emit", "ci-tier0", "--root", root], timeout=60)
    relpaths = []
    for line in (r["stdout"] or "").splitlines():
        parts = line.split()
        if parts:
            relpaths.append(parts[-1])
    if r["exit"] != 0 or r["void"]:
        print("om-integrate: ci-tier0 delegation to om-ci.py did not complete (%s); nothing "
              "emitted for the remote handle" % (r["void"] or ("om-ci.py exit %s" % r["exit"])))
        return []
    return relpaths


def emit(root, agents_dir, worker_path, plugin_scripts, remote_host="github.com"):
    rows = probe_all(root, remote_host)
    decision = compose(rows)
    emitted = []
    ci_relpaths = []
    if decision["on_device"] == "launchd-queue":
        emitted.append(_emit_launchd_plist(root, agents_dir, worker_path, plugin_scripts))
    elif decision["on_device"] == "none":
        print("om-integrate: no probe-verified on-device handle; nothing to compose (handle=none)")
    else:
        # a probe-verified handle with no plist/unit template in this round (disclosed gap):
        # 0 artifacts emitted for it, one explanatory line naming the next probe-verified handle.
        print("om-integrate: no emit template for probe-verified handle %r yet; naming it, "
              "emitting nothing" % decision["on_device"])

    if decision["remote"] == "ci-tier0":
        ci_relpaths = _emit_ci_tier0(root, plugin_scripts)
        emitted.extend(os.path.join(root, p) for p in ci_relpaths)

    lock_path = os.path.join(root, LOCK_REL)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock = {"on_device_handle": decision["on_device"], "remote_handle": decision["remote"],
            "emitted": emitted, "rootkey": _rootkey(root), "probe_voids": probe_voids(rows),
            "ci_tier0_relpaths": ci_relpaths}
    with open(lock_path, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=1, sort_keys=True)

    hyp_json_path = os.path.join(root, HYP_JSON_REL)
    os.makedirs(os.path.dirname(hyp_json_path), exist_ok=True)
    data = {}
    if os.path.isfile(hyp_json_path):
        try:
            data = json.load(open(hyp_json_path))
        except ValueError:
            data = {}
    if decision["on_device"] != "none" or decision["remote"] != "none":
        data["om_offload"] = decision["on_device"] if decision["on_device"] != "none" else decision["remote"]
    with open(hyp_json_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)

    return {"decision": decision, "emitted": emitted, "lock": lock_path,
            "probe_voids": lock["probe_voids"]}


def cmd_emit(args):
    result = emit(args.root, args.agents_dir, args.worker, args.plugin_scripts, args.remote_host)
    print(json.dumps(result, sort_keys=True))
    for item in result["probe_voids"]:
        print("probe-void: %s" % item)
    if result["decision"]["on_device"] == "launchd-queue":
        label = "com.hyp-machine.om-worker.%s" % _rootkey(args.root)
        plist_path = os.path.join(args.agents_dir, label + ".plist")
        print("om-integrate: to activate (a deliberate, separate step -- never run by this "
              "verb): launchctl load %s" % plist_path)
    return 0


# --------------------------------------------------------------------------- test
def _plant_pointer(inbox_dir, transcript_path, session_id):
    os.makedirs(inbox_dir, exist_ok=True)
    p = os.path.join(inbox_dir, session_id + ".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"transcript_path": os.path.abspath(transcript_path), "session_id": session_id}, fh)
    return p


def _substrate_report(label):
    """A REAL subprocess call to `launchctl print gui/<uid>/<label>`, resolved on PATH. Under a
    stub substrate shim it answers the fixture-side stand-in report; under a real launchd it
    answers truthfully that nothing by this label is loaded (this verb never loads anything)."""
    r = _run(["launchctl", "print", "gui/%s/%s" % (os.getuid(), label)], timeout=8)
    if r["void"]:
        return "launchctl print gui/%s/%s: void=%s" % (os.getuid(), label, r["void"])
    return (r["stdout"] or r["stderr"] or "").strip() or ("launchctl print: rc %r, no output" % r["exit"])


def _queue_watch(worker_path, plugin_scripts, root, state_dir, deadline_s, throttle_s,
                  launch_log, ledger_path, target_kind, target_count):
    """Ask the substrate (`launchctl watch`, resolved on PATH) to drive the pinned worker over
    the inbox under `state_dir`: fire `drain` whenever the queue directory is non-empty, re-check
    every `throttle_s` like `ThrottleInterval`, log every launch to `launch_log`, and stop at
    `deadline_s` or once `target_count` rows of `target_kind` land in `ledger_path`. This process
    never polls the inbox or runs the worker itself -- the substrate does -- and the caller reads
    the outcome back from disk, never from this call's own bookkeeping. On a real host this named
    `watch` subcommand does not exist on the real `launchctl`; `test` is a controlled exercise of
    the worker integration against a substrate that plays the role (a stub shim placed ahead of
    the real binary on PATH for this verb only -- see `scripts/selftest-om-integrate.py`), not a
    persistence check against the live substrate."""
    r = _run(["launchctl", "watch", "--root", root, "--worker", worker_path,
              "--plugin-scripts", plugin_scripts, "--state-dir", state_dir,
              "--deadline", str(deadline_s), "--throttle", str(throttle_s),
              "--launch-log", launch_log, "--ledger", ledger_path,
              "--target-kind", target_kind, "--target-count", str(target_count)],
             timeout=deadline_s + 20)
    return {"substrate_rc": r["exit"], "substrate_void": r["void"], "substrate_stdout": r["stdout"]}


POISON_MAX_LAUNCHES = 2  # frozen launch-count grammar: at most 2, no spin


def _count_kind(ledger_path, kind):
    if not os.path.isfile(ledger_path):
        return 0
    n = 0
    with open(ledger_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == kind:
                n += 1
    return n


def _count_launches(launch_log):
    if not os.path.isfile(launch_log):
        return 0
    n = 0
    with open(launch_log, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def cmd_test(args):
    root = os.path.abspath(args.root)
    state_dir = os.path.join(root, ".claude", "om-state")
    inbox_dir = os.path.join(state_dir, "inbox")
    ledger_path = om_feedback_path(root)
    launch_log_single = os.path.join(state_dir, "launch-log.single.jsonl")
    launch_log_poison = os.path.join(state_dir, "launch-log.poison.jsonl")
    os.makedirs(inbox_dir, exist_ok=True)
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    label = "com.hyp-machine.om-worker.%s" % _rootkey(root)
    print(_substrate_report(label))

    # `test` REPORTS PASS/FAIL, computed from what actually landed on disk (the ledger row
    # counts, the inbox listing and the substrate's launch log), and exits 1 on FAIL.
    failures = []
    before_single = _count_kind(ledger_path, "session-observed")
    before_quarantine = _count_kind(ledger_path, "quarantine")

    # phase 1: one synthetic pointer, expect one landed row within the window.
    _plant_pointer(inbox_dir, args.transcript, "scn-session-0")
    wr1 = _queue_watch(args.worker, args.plugin_scripts, root, state_dir, args.window,
                        args.throttle, launch_log_single, ledger_path, "session-observed", 1)
    print(json.dumps({"phase": "single", "substrate": wr1}, sort_keys=True))
    landed_single = _count_kind(ledger_path, "session-observed") - before_single
    if landed_single != 1:
        failures.append("single: %d session-observed rows landed, want 1" % landed_single)
    if wr1.get("substrate_void"):
        failures.append("single: substrate void %s" % wr1["substrate_void"])

    # phase 2: two poison seeds -- (1) a pointer file that is NOT JSON (the pointer-parse-failure
    # path, the QueueDirectories spin risk is written against); (2) a pointer to a format-shifted
    # transcript whose assistant row lacks `usage` and renames `tool_use` to `tool_call`.
    poison_not_json = os.path.join(inbox_dir, "poison-not-json.json")
    with open(poison_not_json, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    shifted = os.path.join(state_dir, "poison-transcript.jsonl")
    with open(shifted, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                              "content": [{"type": "tool_call", "name": "Bash"}]}}) + "\n")
    poison_shifted = os.path.join(inbox_dir, "poison-shifted.json")
    with open(poison_shifted, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"session_id": "poison-shifted", "transcript_path": shifted}))
    wr2 = _queue_watch(args.worker, args.plugin_scripts, root, state_dir, args.poison_window,
                        args.throttle, launch_log_poison, ledger_path, "quarantine", 2)
    print(json.dumps({"phase": "poison", "substrate": wr2}, sort_keys=True))
    landed_quarantine = _count_kind(ledger_path, "quarantine") - before_quarantine
    if landed_quarantine != 2:
        failures.append("poison: %d quarantine rows landed, want 2" % landed_quarantine)
    remaining = sorted(f for f in os.listdir(inbox_dir) if f.endswith(".json")) \
        if os.path.isdir(inbox_dir) else []
    if remaining:
        failures.append("poison: inbox not empty: %r" % remaining)
    poison_launches = _count_launches(launch_log_poison)
    if poison_launches > POISON_MAX_LAUNCHES:
        failures.append("poison: %d launches logged, at most %d allowed (spin)" %
                        (poison_launches, POISON_MAX_LAUNCHES))
    if wr2.get("substrate_void"):
        failures.append("poison: substrate void %s" % wr2["substrate_void"])

    if failures:
        print("test: FAIL (%s)" % "; ".join(failures))
        return 1
    print("test: PASS (1 session-observed row, 2 quarantine rows, inbox empty, %d poison launch(es))"
          % poison_launches)
    return 0


# --------------------------------------------------------------------------- report
def _worktrees_sharing(root):
    common = _git_common_dir(root)
    if not common:
        return []
    r = _run(["git", "worktree", "list", "--porcelain"], timeout=5, cwd=root)
    if r["exit"] != 0:
        return []
    trees = []
    for line in r["stdout"].splitlines():
        if line.startswith("worktree "):
            trees.append(line[len("worktree "):].strip())
    return trees


def _mixed_installs_line(root, installed_plugins):
    """The `mixed-installs: <v1>,<v2> (<checkout>=<v> ...)` line when the checkouts sharing this
    root's git common dir carry more than one plugin version in `installed_plugins`, else None."""
    if not installed_plugins or not os.path.isfile(installed_plugins):
        return None
    data = json.load(open(installed_plugins))
    trees = _worktrees_sharing(root)
    versions = {}
    for wt in trees:
        v = data.get(wt) or data.get(os.path.realpath(wt))
        if v:
            versions[wt] = v
    distinct = sorted(set(versions.values()))
    if len(distinct) > 1:
        named = " ".join("%s=%s" % (wt, versions[wt]) for wt in sorted(versions))
        return "mixed-installs: %s (%s)" % (",".join(distinct), named)
    return None


def cmd_report(args):
    root = os.path.abspath(args.root)
    # the mixed-installs drift report runs FIRST, independent of whether this root carries a
    # lock -- a root with a mixed install and no lock still reports the drift.
    mixed = _mixed_installs_line(root, args.installed_plugins)
    if mixed:
        print(mixed)
    lock_path = os.path.join(root, LOCK_REL)
    if not os.path.isfile(lock_path):
        print("not installed")
        return 0
    lock = json.load(open(lock_path))
    handle = lock.get("on_device_handle") or lock.get("remote_handle")
    rows = probe_all(root, args.remote_host)
    _print_probe_voids(rows)
    usable = {r["handle"]: r["usable"] for r in rows}
    if handle and handle != "none" and usable.get(handle):
        print("holds")
    else:
        decision = compose(rows)
        proposal = decision["on_device"] if decision["on_device"] != "none" else decision["remote"]
        print("no longer holds: %s (propose: %s)" % (handle, proposal))
    return 0


# --------------------------------------------------------------------------- uninstall
def uninstall(root, dry_run=False):
    root = os.path.abspath(root)
    lock_path = os.path.join(root, LOCK_REL)
    reversal = []
    removed = []
    if os.path.isfile(lock_path):
        lock = json.load(open(lock_path))
        for path in lock.get("emitted", []):
            if path.endswith(".plist"):
                reversal.append("launchctl unload %s" % path)
            elif os.path.relpath(path, root) == WORKFLOW_REL:
                reversal.append("git rm -r %s %s  # and push to disable the workflow" %
                                (WORKFLOW_REL, os.path.join(".github", "om-scripts")))
            if not dry_run and os.path.isfile(path):
                os.remove(path)
                removed.append(path)
        if not dry_run:
            os.remove(lock_path)
            # best-effort cleanup of now-empty vendor directories the ci-tier0 delegation made;
            # deepest first, never raises on a non-empty or absent directory.
            vendor_dir = os.path.join(root, ".github", "om-scripts")
            for d in (os.path.join(vendor_dir, "pyyaml", "yaml"),
                      os.path.join(vendor_dir, "pyyaml"), vendor_dir):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
        else:
            removed.append(lock_path)
    hyp_json_path = os.path.join(root, HYP_JSON_REL)
    if os.path.isfile(hyp_json_path):
        data = json.load(open(hyp_json_path))
        if "om_offload" in data:
            reversal.append("(no reversal command needed: dropping the .claude/hyp.json om_offload key)")
            if not dry_run:
                data.pop("om_offload", None)
                with open(hyp_json_path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1, sort_keys=True)
    return {"removed": removed, "reversal": reversal, "dry_run": dry_run}


def cmd_uninstall(args):
    result = uninstall(args.root, args.dry_run)
    print(json.dumps(result, sort_keys=True))
    for line in result["reversal"]:
        print(line)
    return 0


# --------------------------------------------------------------------------- CLI
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="om-integrate.py")
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--root", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--remote-host", default="github.com")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("compose")
    p.add_argument("--root", default=None)
    p.add_argument("--remote-host", default="github.com")
    p.set_defaults(fn=cmd_compose)

    p = sub.add_parser("emit")
    p.add_argument("--root", default=None)
    p.add_argument("--agents-dir", required=True)
    p.add_argument("--worker", default=None)
    p.add_argument("--plugin-scripts", default=None)
    p.add_argument("--remote-host", default="github.com")
    p.set_defaults(fn=cmd_emit)

    p = sub.add_parser("test")
    p.add_argument("--root", default=None)
    p.add_argument("--worker", default=None)
    p.add_argument("--plugin-scripts", default=None)
    p.add_argument("--transcript", required=True)
    p.add_argument("--throttle", type=float, default=10.0)
    p.add_argument("--window", type=float, default=100.0)
    p.add_argument("--poison-window", type=float, default=30.0)
    p.set_defaults(fn=cmd_test)

    p = sub.add_parser("report")
    p.add_argument("--root", default=None)
    p.add_argument("--installed-plugins", default=None)
    p.add_argument("--remote-host", default="github.com")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("uninstall")
    p.add_argument("--root", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_uninstall)

    args = ap.parse_args(argv)
    args.root = _resolve_root(args.root)
    if hasattr(args, "plugin_scripts"):
        args.plugin_scripts = _plugin_scripts_dir(args.plugin_scripts)
        if not getattr(args, "worker", None):
            args.worker = os.path.join(args.plugin_scripts, "om-worker.py")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
