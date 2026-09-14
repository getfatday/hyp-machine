#!/usr/bin/env python3
"""om-worker.py -- the zero-token passive feedback worker for the operating model.

Reads a finished Claude Code transcript and a modeling-profile checkout and appends canonical
rows to the feedback ledger (`.claude/hyp.json` `om_feedback_file`, default
`ledger/om-feedback.jsonl`): one `session-observed` row per transcript (the arithmetic half of
`observe`: the census the live board's classifier computes -- class counts, leverage,
determinism, handoff share, the top token step, the unmodeled programs) and one
`model-evaluated` row per `operating-model/<context>` tree (the arithmetic half of `evaluate`:
the model lint's findings, plus compile staleness from git dates). No model call, ever; no
prompt text, tool argument, file body, absolute path or identity string leaves the row writer.

Verbs: observe <transcript> --root R | evaluate --root R | compile-check --root R |
       drain [--root R] [--inbox DIR] | status --root R | latest --root R

`status` and `latest` are readers: they tolerate unknown fields and higher `schema` values
(read, reported, never rewritten). `latest` is the latest-wins view -- the highest-`through`
`session-observed` row per session, printed as canonical bytes in session order.

Imports `op_tokens_bash`, `Catalog`, `ratio_block` and `unmodeled_top` from the plugin's
scripts/observatory.py (the same classifier the live board runs) and shells out to the plugin's
scripts/model-lint.py, both located beside this file (override: --plugin-scripts or
$HYP_OM_PLUGIN_SCRIPTS). Python 3.9, stdlib only.

Provenance: lab H-DRAFT-35397146-om-worker-deterministic, kept 2026-09-13 (five counted looks, A1-A5
pass in every one; VERDICT.json beside the lane, journal fragment 0528). The kept bytes are the lane
fixture's impl/om-worker.py; this file is that worker adapted to the plugin layout (the scripts
directory defaults to its own, the ledger path reads the consumer config) with the carried
non-blocking findings resolved: the self-check now runs the fixture grader's A3 nets (absolute
path, relative escape, well-known-root spelling, identity) instead of the two markers alone; a
drain sweeps `processing/` back into the inbox first so a SIGKILL mid-drain strands nothing;
compile staleness reads the NEWEST compiled artifact by commit date.
"""
import errno
import fcntl
import getpass
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

SCHEMA = 1
CANARY_KEYS_FORBIDDEN = ("tool_input", "prompt", "last_assistant_message")
DEFAULT_LEDGER_REL = "ledger/om-feedback.jsonl"
CONFIG_RELPATH = os.path.join(".claude", "hyp.json")
HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- plugin import
def _load_observatory(plugin_scripts):
    if plugin_scripts not in sys.path:
        sys.path.insert(0, plugin_scripts)
    import observatory  # noqa: E402  (path-injected)
    return observatory


def _model_lint_path(plugin_scripts):
    return os.path.join(plugin_scripts, "model-lint.py")


def plugin_scripts_dir(args_val=None):
    """The plugin scripts/ directory: --plugin-scripts, else $HYP_OM_PLUGIN_SCRIPTS, else the
    directory this file lives in (the installed plugin's scripts/)."""
    d = args_val or os.environ.get("HYP_OM_PLUGIN_SCRIPTS") or HERE
    if not os.path.isdir(d) or not os.path.isfile(os.path.join(d, "observatory.py")):
        raise SystemExit("om-worker: %s does not hold observatory.py and model-lint.py (name the "
                         "plugin scripts/ directory with --plugin-scripts or $HYP_OM_PLUGIN_SCRIPTS)" % d)
    return os.path.abspath(d)


# --------------------------------------------------------------------------- root / state
def state_root(root):
    override = os.environ.get("HYP_STATE_DIR")
    base = override if override else os.path.join(os.path.expanduser("~"), ".hyp-state")
    key = hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(base, "om", key)


def _consumer_config(root):
    try:
        with open(os.path.join(root, CONFIG_RELPATH), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def ledger_rel(root):
    """Repository-relative posix path of the feedback ledger: `.claude/hyp.json` `om_feedback_file`
    when it is a relative path inside the repository, else the default. Never absolute."""
    v = _consumer_config(root).get("om_feedback_file")
    if isinstance(v, str) and v.strip():
        rel = v.strip().strip("/").replace(os.sep, "/")
        if rel and not os.path.isabs(v.strip()) and ".." not in rel.split("/"):
            return rel
    return DEFAULT_LEDGER_REL


def ledger_path(root):
    return os.path.join(root, *ledger_rel(root).split("/"))


def free_floor_bytes():
    v = os.environ.get("HYP_OM_FREE_FLOOR_BYTES")
    try:
        return max(int(v), 1 << 30) if v else (1 << 30)
    except (TypeError, ValueError):
        return 1 << 30


def free_bytes(path):
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    st = os.statvfs(probe or ".")
    return st.f_bavail * st.f_frsize


# --------------------------------------------------------------------------- canonical rows
def canonical_bytes(row):
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


CANARY_MARKERS = ("/users/", "$home")   # case-folded substrings; the lane's original two markers
# "canary" itself is deliberately NOT a marker: it is the lane fixture's own ground-truth vocabulary
# word, and a production self-check must not know a test's vocabulary (REFUTE-FIXTURE-1 finding 6).
# Redaction relies on never capturing raw transcript bytes in production (see mutant_mode() /
# _debug_preview) plus the nets below.

# The fixture grader's A3 nets (REFUTE-FIXTURE-6/7/8), now the worker's own self-check -- the carried
# finding "the worker's self-check markers share every grader blind spot" (fixture README rounds 6-9):
#   an absolute-path-shaped token: '/' (or '~/') NOT glued to a preceding path/word character, then
#   segments -- "operating-model/ops" and "policies/gate-broken.md" are relative and never match;
_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.~\-/])(?:/+|~/)[A-Za-z0-9_.~\-]+(?:/[A-Za-z0-9_.~\-]*)*")
#   a relative path that leaves the repository: a '..'-led hop chain, anchored on the '..' itself;
_REL_ESCAPE_RE = re.compile(r"(?<![A-Za-z0-9_.~\-/])\.\.(?:/[A-Za-z0-9_.~\-]*)+")
#   a bare relative spelling of a well-known absolute root (heuristic: a consumer whose tree has a
#   top-level `etc/` or `var/` directory in a row string would need this list revisited);
_WELLKNOWN_ROOTS = ("Users", "home", "private", "tmp", "var", "opt", "etc")
_WELLKNOWN_ROOT_REL_RE = re.compile(r"(?<![A-Za-z0-9_.~\-/])(?:%s)/[A-Za-z0-9_.~\-]+(?:/[A-Za-z0-9_.~\-]*)*"
                                    % "|".join(_WELLKNOWN_ROOTS))
#   the executor's identity: login and host-name spellings, case-insensitive, not glued to a word
#   character; tokens under 4 characters, generic words, or words the worker's own rows legitimately
#   carry (keys, kinds, class names, tool names, suggested nodes) are skipped -- the disclosed blind
#   spot: such a login cannot be seen as an identity leak on that host.
_IDENTITY_GENERIC = {"root", "user", "admin", "localhost", "host", "home", "main", "test", "build", "none",
                     "null", "true", "false", "local", "default"}
_ROW_VOCABULARY = {"kind", "schema", "session", "through", "head", "landed_in", "counts", "leverage",
                   "determinism", "handoff_share", "top_step", "unmodeled_top", "hook_timeouts", "date",
                   "model_tree", "lint", "compiled", "compile_command", "errors", "findings", "parse_skipped",
                   "stale", "compiled_path", "compiled_commit_date", "model_commit_date", "command", "file",
                   "reason", "moved", "recovered", "output_tokens", "suggested_node", "count", "tool",
                   "session-observed", "model-evaluated", "spool-overflow", "quarantine",
                   "modeled_deterministic", "modeled_stochastic", "delegated", "unmodeled",
                   "bash", "edit", "read", "write", "skill", "agent", "task", "glob", "grep", "webfetch",
                   "websearch", "notebookedit", "structuredoutput", "capture-candidate", "harness", "handoff",
                   "python3", "python", "error", "warn", "valueerror", "keyerror", "typeerror", "oserror",
                   "jsondecodeerror", "unicodedecodeerror", "filenotfounderror", "permissionerror"}


def _identity_tokens():
    """{token: kind} for the login and host-name spellings the self-check refuses."""
    cands = []
    for var in ("USER", "LOGNAME"):
        v = os.environ.get(var)
        if v:
            cands.append((v, "login"))
    try:
        cands.append((getpass.getuser(), "login"))
    except Exception:
        pass
    try:
        cands.append((os.path.basename(os.path.abspath(os.path.expanduser("~"))), "login"))
    except Exception:
        pass
    try:
        host = socket.gethostname()
        cands.append((host, "host"))
        cands.append((host.split(".")[0], "host"))
    except Exception:
        pass
    toks = {}
    for tok, kind in cands:
        low = (tok or "").lower()
        if not tok or len(tok) < 4 or low in _IDENTITY_GENERIC or low in _ROW_VOCABULARY:
            continue
        toks.setdefault(tok, kind)
    return toks


def _walk_strings(obj):
    """Every string in a row object -- keys and values, at any depth."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            for s in _walk_strings(v):
                yield s
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            for s in _walk_strings(v):
                yield s


def _forbidden_hit(row):
    """The first reason this row must not be written, or None: a forbidden key, one of the two
    markers, an absolute-path-shaped string, a relative escape, a bare well-known-root spelling, or
    an identity string. The reason names the class, never the offending bytes."""
    blob = json.dumps(row)
    for k in CANARY_KEYS_FORBIDDEN:
        if ('"%s"' % k) in blob:
            return "forbidden key %s" % k
    low = blob.lower()
    for marker in CANARY_MARKERS:
        if marker in low:
            return "marker %s" % marker
    strings = list(_walk_strings(row))
    for s in strings:
        if s.startswith("/") or s.startswith("~/") or _ABS_PATH_RE.search(s):
            return "absolute path"
    for s in strings:
        if s == ".." or s.startswith("../") or _REL_ESCAPE_RE.search(s):
            return "relative path outside the repository"
    for s in strings:
        if any(s.startswith(r + "/") for r in _WELLKNOWN_ROOTS) or _WELLKNOWN_ROOT_REL_RE.search(s):
            return "bare spelling of a well-known absolute root"
    for tok, kind in _identity_tokens().items():
        if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tok), blob, re.IGNORECASE):
            return "identity string (%s)" % kind
    return None


def mutant_mode():
    """None (production) | 'redact-disabled' (the debug-preview transform is skipped, self-check
    intact -- must refuse) | 'blind' (transform AND self-check both skipped -- must leak). Exists
    only so the known-answer control (the fixture's two writer mutants, now the selftest's) has
    something to flip; production runs never set $HYP_OM_MUTANT."""
    return os.environ.get("HYP_OM_MUTANT") or None


def _debug_preview(transcript_path):
    """What an unredacted row writer would have captured verbatim: the raw transcript bytes,
    untouched by the classifier. The transform's job (production) is to never call this; row
    construction only adds it under a seeded mutant, so the selftest can prove the self-check
    catches it (M-redact) or fails to (M-blind)."""
    with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def append_row(root, row, allow_below_floor=False):
    """Append one canonical row under an flock'd O_APPEND descriptor, deduped by exact bytes,
    refusing when the self-check fires or free space is below the floor. Returns 'written' |
    'duplicate' | 'refused-redaction' | 'refused-floor'. A refusal prints exactly one stderr line."""
    selfcheck_enabled = mutant_mode() != "blind"
    bad = _forbidden_hit(row) if selfcheck_enabled else None
    if bad:
        sys.stderr.write("om-worker: refuse to write row: %s present\n" % bad)
        return "refused-redaction"
    path = ledger_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not allow_below_floor and free_bytes(path) < free_floor_bytes():
        sys.stderr.write("om-worker: refusing append, free space below floor\n")
        return "refused-floor"
    line = canonical_bytes(row)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            size = os.fstat(fd).st_size
            existing = set(os.read(fd, size).splitlines(True)) if size else set()
            if line in existing:
                return "duplicate"
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, line)
            return "written"
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- observe
def _tool_use_records(transcript_path, cwd_for_classify, observatory):
    """Yield flat hook-shaped records (hook_event_name/tool_name/op_name/op_names/cwd) from one
    Claude Code transcript JSONL; the generator's return value is (top step, hook-timeout count,
    last timestamp)."""
    top_step = None
    hook_timeouts = 0
    last_ts = None
    with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        ts = rec.get("timestamp")
        if isinstance(ts, str):
            last_ts = ts
        att = rec.get("attachment") if isinstance(rec.get("attachment"), dict) else {}
        if att.get("type") == "hook_cancelled" or att.get("timedOut"):
            hook_timeouts += 1
        if rec.get("type") == "assistant":
            msg = (rec.get("message") or {})
            if "usage" not in msg:
                raise ValueError("assistant row missing usage: (format-shifted transcript)")
            usage = msg.get("usage") or {}
            out_tok = usage.get("output_tokens")
            if isinstance(out_tok, int):
                if top_step is None or out_tok > top_step["output_tokens"]:
                    top_step = {"msg": rec.get("uuid") or rec.get("requestId") or "step", "output_tokens": out_tok}
            for block in (msg.get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool = block.get("name")
                ti = block.get("input") or {}
                op, op_names = _derive_op(tool, ti, cwd_for_classify, observatory)
                yield {"hook_event_name": "PostToolUse", "tool_name": tool, "op_name": op,
                       "op_names": op_names, "cwd": cwd_for_classify}
    return top_step, hook_timeouts, last_ts


def _derive_op(tool, tool_input, cwd, observatory):
    """(op, op_names) exactly as the shipped classifier's chain rule would derive them
    (op_tokens_bash for Bash; the same fields derive_op reads for Skill/Agent/Task)."""
    if tool == "Skill":
        return (tool_input.get("skill") or tool_input.get("skill_name") or tool_input.get("name")), []
    if tool in ("Agent", "Task"):
        return tool_input.get("subagent_type"), []
    if tool == "Bash":
        names = observatory.op_tokens_bash(tool_input.get("command"), cwd=cwd)
        return (names[0] if names else None), names[1:]
    return None, []


def observe(transcript_path, root, plugin_scripts):
    observatory = _load_observatory(plugin_scripts)
    records = []
    top_step, hook_timeouts, last_ts = None, 0, None
    gen = _tool_use_records(transcript_path, root, observatory)
    try:
        while True:
            records.append(next(gen))
    except StopIteration as stop:
        if stop.value:
            top_step, hook_timeouts, last_ts = stop.value
    cat = observatory.Catalog()
    counter = {}
    counts = {"modeled-deterministic": 0, "modeled-stochastic": 0, "delegated": 0, "unmodeled": 0}
    for r in records:
        cls, node = cat.classify(r)
        if cls is None:
            continue
        counts[cls] = counts.get(cls, 0) + 1
        if cls == "unmodeled":
            key = (r.get("op_name"), r.get("tool_name"))
            counter[key] = counter.get(key, 0) + 1
    block = observatory.ratio_block(counts)
    session_id = os.path.splitext(os.path.basename(transcript_path))[0]
    with open(transcript_path, "rb") as fh:
        data = fh.read()
    n_lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    head = hashlib.sha256(data).hexdigest()
    row = {
        "kind": "session-observed",
        "schema": SCHEMA,
        "session": session_id,
        "through": n_lines,
        "head": head,
        "landed_in": "root",
        "counts": {"modeled_deterministic": block["modeled_deterministic"],
                   "modeled_stochastic": block["modeled_stochastic"],
                   "delegated": block["delegated"], "unmodeled": block["unmodeled"]},
        "leverage": block["leverage"],
        "determinism": block["determinism"],
        "handoff_share": block["handoff_share"],
        "top_step": top_step or {"msg": None, "output_tokens": 0},
        "unmodeled_top": observatory.unmodeled_top(counter, catalog=cat.get(root)),
        "hook_timeouts": hook_timeouts,
        "date": last_ts,
    }
    mode = mutant_mode()
    if mode in ("redact-disabled", "blind"):
        row["debug_extra"] = _debug_preview(transcript_path)
    return row


# --------------------------------------------------------------------------- evaluate / compile-check
def _model_dir(root):
    v = _consumer_config(root).get("model_dir")
    if isinstance(v, str) and v.strip() and not os.path.isabs(v.strip()):
        return v.strip().strip("/")
    return "operating-model"


def _model_trees(root):
    om_dir = os.path.join(root, _model_dir(root))
    if not os.path.isdir(om_dir):
        return []
    return sorted(d for d in glob.glob(os.path.join(om_dir, "*")) if os.path.isdir(d))


def _git_last_commit(root, relpath):
    """(iso committer date, unix epoch) of the last commit touching relpath, or (None, None)."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%ct %cI", "--", relpath], cwd=root,
            stderr=subprocess.DEVNULL).decode("utf-8").strip()
    except Exception:
        return None, None
    parts = out.split(" ", 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return None, None
    return parts[1], int(parts[0])


def _lint(model_tree, plugin_scripts):
    lint_py = _model_lint_path(plugin_scripts)
    proc = subprocess.run([sys.executable, lint_py, model_tree], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    lines = [l for l in proc.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    findings = sorted(l for l in lines if l.split(" ", 1)[0] in ("ERROR", "WARN"))
    errors = sum(1 for l in findings if l.startswith("ERROR"))
    # without pyyaml, model-lint.py emits W-NOYAML and never runs the E-LINK check, so a clean
    # lint.errors=0 would silently lie; the flag lets a reader tell a real clean lint from a skipped one
    parse_skipped = any("W-NOYAML" in l for l in findings)
    return {"errors": errors, "findings": findings, "parse_skipped": parse_skipped}


def _compiled_staleness(root, model_tree):
    """The NEWEST compiled artifact (by last commit date; ties by path) under compiled/*.md against
    the model tree's last commit date. `stale` is None when either date is unknown."""
    rel_model = os.path.relpath(model_tree, root)
    model_date, model_epoch = _git_last_commit(root, rel_model)
    best = None   # (epoch, rel, iso)
    for path in sorted(glob.glob(os.path.join(root, "compiled", "*.md"))):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        iso, epoch = _git_last_commit(root, rel)
        cand = (epoch if epoch is not None else -1, rel, iso)
        if best is None or cand[0] > best[0]:
            best = cand
    stale = None
    compiled_rel = None
    compiled_date = None
    if best is not None:
        compiled_rel, compiled_date = best[1], best[2]
        if model_epoch is not None and best[0] >= 0:
            stale = best[0] < model_epoch
    return {"stale": stale, "compiled_path": compiled_rel, "compiled_commit_date": compiled_date,
            "model_commit_date": model_date}


def _run_compile_command(root):
    cmd = _consumer_config(root).get("compile_command")
    if not cmd or not isinstance(cmd, str):
        return {"command": None, "rc": None}
    try:
        proc = subprocess.run(cmd, shell=True, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"command": cmd, "rc": proc.returncode}
    except Exception:
        return {"command": cmd, "rc": -1}


def evaluate(root, plugin_scripts, do_compile_check=True):
    rows = []
    for tree in _model_trees(root):
        rel_tree = os.path.relpath(tree, root).replace(os.sep, "/")
        lint = _lint(tree, plugin_scripts)
        compiled = _compiled_staleness(root, tree) if do_compile_check else {"stale": None}
        cc = _run_compile_command(root) if do_compile_check else {"command": None, "rc": None}
        row = {
            "kind": "model-evaluated",
            "schema": SCHEMA,
            "model_tree": rel_tree,
            "landed_in": "root",
            "lint": lint,
            "compiled": compiled,
            "compile_command": cc,
            "date": compiled.get("model_commit_date"),
        }
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- drain
def _inbox_dirs(inbox_root):
    inbox = os.path.join(inbox_root, "inbox")
    for sub in ("inbox", "processing", "processed", "quarantine", "overflow"):
        os.makedirs(os.path.join(inbox_root, sub), exist_ok=True)
    return inbox


def _recover_processing(inbox_root):
    """Move every pointer a killed drain left in processing/ back into inbox/ so it is drained
    again (dedupe by canonical bytes makes the re-run harmless). Returns the count moved."""
    moved = 0
    processing = os.path.join(inbox_root, "processing")
    inbox = os.path.join(inbox_root, "inbox")
    for f in sorted(glob.glob(os.path.join(processing, "*.json"))):
        dest = os.path.join(inbox, os.path.basename(f))
        try:
            if os.path.exists(dest):
                os.remove(f)          # the inbox already holds a pointer of that name
            else:
                shutil.move(f, dest)
            moved += 1
        except OSError:
            continue
    return moved


def _rotate_if_needed(inbox_root, cap_bytes=1 << 20):
    inbox = os.path.join(inbox_root, "inbox")
    files = sorted(glob.glob(os.path.join(inbox, "*.json")), key=lambda p: os.path.getmtime(p))
    total = sum(os.path.getsize(f) for f in files)
    if total <= cap_bytes:
        return None
    epoch = str(int(time.time()))
    dest = os.path.join(inbox_root, "overflow", epoch)
    os.makedirs(dest, exist_ok=True)
    moved = 0
    for f in files:
        if total <= cap_bytes:
            break
        sz = os.path.getsize(f)
        shutil.move(f, os.path.join(dest, os.path.basename(f)))
        total -= sz
        moved += 1
    return moved


def drain(root, plugin_scripts, inbox_override=None, n_cap=20, t_cap=60.0):
    """Process at most n_cap pointer files within t_cap seconds (monotonic clock, checked between
    files): rename-before-parse into processing/, observe, append, then processed/ -- or
    quarantine/ with one `quarantine` row naming the file's basename and the exception class."""
    inbox_root = inbox_override or state_root(root)
    inbox = _inbox_dirs(inbox_root)
    written = {"rows": 0, "quarantined": 0, "rotated": False, "landed": 0, "recovered": 0}
    if free_bytes(ledger_path(root)) < free_floor_bytes():
        sys.stderr.write("om-worker drain: refusing, free space below floor\n")
        return written
    written["recovered"] = _recover_processing(inbox_root)
    rotated = _rotate_if_needed(inbox_root)
    if rotated:
        append_row(root, {"kind": "spool-overflow", "schema": SCHEMA, "landed_in": "root",
                          "moved": rotated, "date": None})
        written["rotated"] = True
    start = time.monotonic()
    files = sorted(glob.glob(os.path.join(inbox, "*.json")))
    n_done = 0
    for f in files:
        if n_done >= n_cap or (time.monotonic() - start) >= t_cap:
            break
        base = os.path.basename(f)
        processing_path = os.path.join(inbox_root, "processing", base)
        try:
            shutil.move(f, processing_path)
        except OSError:
            continue
        n_done += 1
        try:
            with open(processing_path, "r", encoding="utf-8") as fh:
                pointer = json.load(fh)
            transcript_path = pointer["transcript_path"]
            session_id = pointer.get("session_id") or os.path.splitext(base)[0]
            if not os.path.isfile(transcript_path):
                raise ValueError("transcript missing")
            row = observe(transcript_path, root, plugin_scripts)
            row["session"] = session_id
            result = append_row(root, row)
            if result == "written":
                written["rows"] += 1
            written["landed"] += 1
            shutil.move(processing_path, os.path.join(inbox_root, "processed", base))
        except Exception as exc:
            reason = type(exc).__name__
            append_row(root, {"kind": "quarantine", "schema": SCHEMA, "landed_in": "root",
                              "file": base, "reason": reason, "date": None})
            dest = os.path.join(inbox_root, "quarantine", base)
            try:
                shutil.move(processing_path, dest)
            except OSError:
                pass
            written["quarantined"] += 1
    return written


# --------------------------------------------------------------------------- readers
def read_rows(path):
    """Tolerant ledger reader: every line that parses as one JSON object, unknown fields and
    higher `schema` values passed through untouched; lines that do not parse are counted, never
    rewritten. Returns (rows, unparsed_count)."""
    rows, unparsed = [], 0
    if not os.path.isfile(path):
        return rows, unparsed
    with open(path, "rb") as fh:
        for line in fh.read().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line.decode("utf-8"))
            except ValueError:
                unparsed += 1
                continue
            if isinstance(rec, dict):
                rows.append(rec)
            else:
                unparsed += 1
    return rows, unparsed


def status(root):
    """One JSON line: total rows, rows per `schema` value (so a schema: 2 row is reported, once),
    unparsed line count, and the ledger's repository-relative path -- never an absolute path."""
    rows, unparsed = read_rows(ledger_path(root))
    schemas = {}
    for r in rows:
        k = str(r.get("schema"))
        schemas[k] = schemas.get(k, 0) + 1
    print(json.dumps({"rows": len(rows), "unparsed": unparsed, "schemas": schemas,
                      "ledger": ledger_rel(root)}, sort_keys=True))
    return 0


def latest_rows(root):
    """The latest-wins view: for each `session-observed` session, the row with the highest
    `through` cursor (the later row in file order on a tie), as canonical bytes, in session order.
    A superseding row from a transcript that grew replaces the earlier cursor here while the
    append-only ledger keeps both."""
    rows, _ = read_rows(ledger_path(root))
    best = {}
    for r in rows:
        if r.get("kind") != "session-observed":
            continue
        through = r.get("through")
        if not isinstance(through, int):
            continue
        sid = r.get("session")
        cur = best.get(sid)
        if cur is None or through >= cur.get("through"):
            best[sid] = r
    return [canonical_bytes(best[s]) for s in sorted(best, key=str)]


def latest(root):
    sys.stdout.write(b"".join(latest_rows(root)).decode("utf-8"))
    sys.stdout.flush()
    return 0


# --------------------------------------------------------------------------- CLI
USAGE = "usage: om-worker.py observe <transcript>|evaluate|compile-check|drain [--inbox DIR]|status|latest [--root R] [--plugin-scripts D]\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(USAGE)
        return 2
    verb = argv[0]
    rest = argv[1:]

    known_flags = ("--plugin-scripts", "--root", "--inbox")

    def _opt(flag, default=None):
        if flag in rest:
            i = rest.index(flag)
            return rest[i + 1] if i + 1 < len(rest) else default
        return default

    positional = []
    i = 0
    while i < len(rest):
        if rest[i] in known_flags:
            i += 2
        else:
            positional.append(rest[i])
            i += 1

    root = os.path.abspath(_opt("--root", "."))
    if verb == "status":
        return status(root)
    if verb == "latest":
        return latest(root)
    plugin_scripts = plugin_scripts_dir(_opt("--plugin-scripts"))

    if verb == "observe":
        if not positional:
            sys.stderr.write(USAGE)
            return 2
        transcript = positional[0]
        row = observe(transcript, root, plugin_scripts)
        result = append_row(root, row)
        # the append status rides the stdout marker line: stderr carries refusal lines only, so
        # "zero rows and one refusal line" is countable on stderr alone
        print("om-worker %s observe rc 0 %s" % (SCHEMA, result))
        return 0
    if verb == "evaluate":
        for row in evaluate(root, plugin_scripts):
            append_row(root, row)
        print("om-worker %s evaluate rc 0" % SCHEMA)
        return 0
    if verb == "compile-check":
        for row in evaluate(root, plugin_scripts, do_compile_check=True):
            append_row(root, row)
        print("om-worker %s compile-check rc 0" % SCHEMA)
        return 0
    if verb == "drain":
        inbox_override = _opt("--inbox")
        result = drain(root, plugin_scripts, inbox_override)
        print("om-worker %s drain rc 0 %s" % (SCHEMA, json.dumps(result, sort_keys=True)))
        return 0
    sys.stderr.write("om-worker: unknown verb %r\n" % verb)
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
