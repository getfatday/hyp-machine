#!/usr/bin/env python3
"""session-start-budget.py -- a foreground budget for SessionStart hook commands (v2, cheap start).

    python3 -S -E session-start-budget.py run <name> <command>        # startup: budgeted, cached, detached
    python3 -S -E session-start-budget.py run <name> <command> --also <name2> <command2>
                                                                        # startup: same, plus the wake below
    python3 -S -E session-start-budget.py cached <name> [<name> ...]  # resume|clear|compact: readings only

`run` starts <command> under `sh -c` in a NEW process session with the hook payload (stdin)
as its stdin and its stdout to a file, waits up to HYP_SESSION_START_BUDGET seconds (float,
default 5.0) measured from this wrapper's own start, and then either prints the command's
bytes and exits with its rc (finished in budget) or prints the PREVIOUS cached reading plus
exactly one `SESSION-START-BUDGET:` line and exits 0 while the detached runner lands the new
reading for the next startup. One runner per name at a time (a `mkdir`-atomic lock directory
with the H-309 stale rule and reclaim token). `cached` prints the readings and exactly one
`SESSION-START-CACHE:` line and spawns nothing. Every error path prints at most one line and
exits 0: a hook that blocks or crashes a session start is worse than a missed reading.

Why v2 imports only os, sys, time and zlib and is started with `python3 -S -E`: the first
version's external wall missed its ceiling 22 times in 115 while its own foreground never did
(lab H-DRAFT-45585281, runs 1-2) -- the cost was the wrapper's own process start under load.
Measured at load1 10.8-12.8 (fixture startcost.json): `python3 -c pass` 0.44 s at best, the
old wrapper's imports (json, hashlib, shutil, subprocess) 135 ms of CPU against 15 ms for the
interpreter, `import json` alone 59 ms because it loads `re`. So the runner is started with
os.fork/os.setsid/os.execve (no subprocess), the payload and the runner's record are read by
a 30-line scanner (no json), the state key is crc32+adler32 of the checkout path (no hashlib),
and the lock is removed with os.unlink/os.rmdir (no shutil). The disclosure grammar, the
state files and the runner are the same as v1.

State lives OUTSIDE the repository: ${HYP_STATE_DIR:-$HOME/.claude/hyp}/session-start/<key>/
with, per name, <name>.out (last completed stdout), <name>.err, <name>.rc, <name>.meta (JSON,
written by the runner: pid, started_at, source, budget_s, rc; its mtime is the finish time),
<name>.fg (this wrapper's last foreground record, JSON: mode finished|over-budget|in-flight,
foreground_s, pid, rc, lock_seen_held, lock_seen_pid, started_epoch), a lock
directory <name>.lock holding a `pid` file, and a reclaim token <name>.lock.reclaim. <key> is
crc32 and adler32 (16 hex) of the real path of the ROOT the wrapped commands read:
hyp_config.resolve_root(payload) -- the payload cwd's checkout when it is CLAUDE_PROJECT_DIR
or another checkout of the same repository (a linked worktree), else the process cwd's, else
CLAUDE_PROJECT_DIR, else the cwd (the one contract every hook writer shares; lab
H-DRAFT-b9e771b2-hook-writes-worktree). The same root is handed to the wrapped command as
HYP_ROOT, so a wrapped shell script (harden-check.sh, leak-status.sh) works in the checkout
the session works in without re-deriving it. hyp_config imports only os (json is lazy), so
the import costs one small file read under -S -E. Python 3.9, stdlib only.

ON patch (H-DRAFT-10383178-om-startup-wake, the startup wake): `run <name> <command>` may be
followed by `--also <name2> <command2>`. Inside `cmd_run`, right after the root is resolved
(before any waiting on the primary runner, before even the primary's own lock is checked),
`do_also_wake` writes one pointer file for the CURRENT session (session_id, transcript_path,
the resolved root, common_dir, branch, an epoch timestamp, schema) into the passive worker's
inbox with one `os.write` on an `O_CREAT|O_EXCL` descriptor, then forks `<command2>` through the
SAME `spawn_runner` (an added optional `nice=` argument calls `os.nice(19)` in the forked
child before `execve`, still no waiting) under the per-root lock `<name2>.lock` in this same
state directory. `common_dir` and `branch` are read straight off `.git` (a file's `gitdir:`
line and its `commondir`, or the directory itself; the ref line of the resulting `HEAD`) --
never a subprocess, so the foreground path pays no interpreter start for them.

PORTED DRIFT (ship, this file vs the lane's fixture-pinned worker H-DRAFT-35397146 bytes): the
pointer's inbox directory is `om_inbox_root(root, common_dir)`
(`${HYP_STATE_DIR:-$HOME/.hyp-state}/om/<key>/inbox/<session_id>.json`), NOT the plain
`sha256(root)[:16]` single-path scheme the lane's own ON patch used. The shipped worker
(v0.30.3) ships a LATER, already-kept lane (H-DRAFT-a4a14ff4-om-outbox-carry-forward) whose
default (`--inbox`-less) `drain` reads a shared, `common_dir`-keyed inbox per repository so every
linked worktree drains the same spool; `<key>` is that same `common_dir` string run through this
wrapper's own crc32+adler32 recipe (`state_dir()`'s, never a hashlib import) when `common_dir`
resolves, else the old `sha256(root)[:16]` fallback -- mirroring the shipped worker's own
`_resolve_inbox_root` branch exactly, so the pointer lands where a default drain actually looks.

ON patch (H-DRAFT-3aef12a5-om-startup-reading-surface, the reading surface): inside `cmd_run`,
right after `do_also_wake` forks the side-runner and before the primary command's own budget
wait, `print_om_feedback(sd, also)` reads the side-runner's own cached `<name2>.out` (the SAME
`sd = state_dir(root)` this wrapper already keys every name under -- never the worker's own
sha256/common_dir inbox key, so a session in the wrong worktree never shows another root's
reading) and prints at most 8 of its lines, each prefixed `OM-FEEDBACK: `, the first carrying
`age=<seconds>` (the file's mtime age) ahead of its own text. A ninth `OM-FEEDBACK: ... <n>
more` line is added when the file holds more than 8 lines; nothing is printed when the file is
absent or empty. Every line is passed through `_feedback_forbidden`, the SAME two structural
checks `scripts/om-worker.py`'s own `_forbidden_hit` applies before it ever writes a row --
three forbidden JSON-shaped keys (`tool_input`, `prompt`, `last_assistant_message`) and the
`/users/`/`$home` path markers, case-folded -- never the worker's fixture-only canary literals
(a startup print must hold a class of leak, not memorize a test vocabulary); a line that
matches either check is replaced with exactly one literal `<held: 1 line>` line, every other
line printed unchanged. No new process, no subprocess and no interpreter start: the read and
the print happen inside this wrapper's own foreground path, the same one `do_also_wake`
already runs on. The `om-worker` name joins the `resume|clear|compact` row's argument list in
`hooks.json` so `cmd_cached`'s existing loop reports it in the `SESSION-START-CACHE:` line the
same way it reports every other name.

Fixture fix round 1 (REFUTE-FIXTURE-1 finding 1): `cached` was claimed to need no code change,
but a `cached` replay over a planted canary printed it verbatim (`cmd_cached` dumps every named
`.out` raw) -- the surface's content-free claim applies to every path that shows the
`om-worker` reading, not only `run`'s. `cmd_cached` now passes the `om-worker` name's bytes
through `_held_bytes`, the same per-line structural hold `print_om_feedback` applies, before
printing them; every other cached name is untouched (this lane owns no claim about them).

Stale-lock amendment (same lane): the RUNNER script now also captures `ps -o lstart= -p "$$"`
into a third pid-file line, and `reclaim_if_stale` reads it: a lock whose recorded pid is
alive AND whose current `ps -o lstart=` still matches the recorded one is never reclaimed by
age alone (LOCK_MAX_S no longer fires against it); reclaim now needs a dead pid, a changed
start time (the pid was reused by a different process), or an absent pid file past
LOCK_GRACE_S. This applies to every named lock this wrapper takes (primary and side), not
only the wake's own -- the amendment is to the shared rule, not a special case.
"""
import hashlib
import os
import sys
import time
import zlib

DEFAULT_BUDGET_S = 5.0
LOCK_GRACE_S = 60
LOCK_MAX_S = 1800
POLL_S = 0.05
WS = " \t\r\n"

RUNNER = (
    'LST=$(ps -o lstart= -p "$$" 2>/dev/null); '
    'printf "%s\\n%s\\n%s\\n" "$$" "$SSB_T0" "$LST" > "$SSB_LOCK/pid" 2>/dev/null; '
    'sh -c "$SSB_CMD" < "$SSB_PAYLOAD" > "$SSB_OUT_TMP" 2> "$SSB_ERR"; rc=$?; '
    'mv -f "$SSB_OUT_TMP" "$SSB_OUT"; '
    'printf "%s\\n" "$rc" > "$SSB_RC"; '
    'printf \'{"pid": %s, "started_at": %s, "source": "%s", "budget_s": %s, "rc": %s}\\n\' '
    '"$$" "$SSB_T0" "$SSB_SOURCE" "$SSB_BUDGET" "$rc" > "$SSB_META_TMP"; '
    'rm -rf "$SSB_PAYLOAD" "$SSB_LOCK" "$SSB_LOCK.reclaim"; '
    'mv -f "$SSB_META_TMP" "$SSB_META"'
)
# Order matters above: the reading (.out, .rc) lands, the lock is released, and only then
# does .meta land -- so a wrapper that sees its runner's .meta knows the lock is gone, and a
# concurrent wrapper that takes the freed lock reads the newly landed .out as "previous".
# The lstart capture (`ps`) runs inside the DETACHED runner, never on the foreground path.


def fmt(x):
    return "%g" % x


def out(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# ---------- a small JSON scanner (the wrapper imports no json: see the module docstring) ----------
def _jskip(raw, i):
    n = len(raw)
    while i < n and raw[i] in WS:
        i += 1
    return i


def _jvalue_at(raw, key):
    """Index of the first character of the value of top-level member `key`, or -1."""
    needle = '"%s"' % key
    i = 0
    while True:
        i = raw.find(needle, i)
        if i < 0:
            return -1
        j = _jskip(raw, i + len(needle))
        if j < len(raw) and raw[j] == ":":
            return _jskip(raw, j + 1)
        i = j if j > i else i + 1


def jstr(raw, key):
    """The string member `key` of a JSON object text, decoded; None when absent or not a string."""
    j = _jvalue_at(raw, key)
    if j < 0 or j >= len(raw) or raw[j] != '"':
        return None
    j += 1
    buf = []
    n = len(raw)
    while j < n:
        c = raw[j]
        if c == '"':
            return "".join(buf)
        if c == "\\" and j + 1 < n:
            e = raw[j + 1]
            if e == "u" and j + 5 < n:
                try:
                    cp = int(raw[j + 2:j + 6], 16)
                    if 0xD800 <= cp < 0xDC00 and raw[j + 6:j + 8] == "\\u":
                        lo = int(raw[j + 8:j + 12], 16)
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                        j += 6
                    buf.append(chr(cp))
                except ValueError:
                    buf.append("?")
                j += 6
                continue
            buf.append({"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}.get(e, e))
            j += 2
            continue
        buf.append(c)
        j += 1
    return None


def jint(raw, key):
    """The integer member `key` of a JSON object text, or None."""
    j = _jvalue_at(raw, key)
    if j < 0:
        return None
    k = j
    if k < len(raw) and raw[k] == "-":
        k += 1
    while k < len(raw) and raw[k].isdigit():
        k += 1
    try:
        return int(raw[j:k])
    except ValueError:
        return None


def jquote(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


# ---------- state ----------
def toplevel(start):
    """Nearest ancestor of `start` (inclusive) carrying a .git entry, on the path as given and
    then on its resolved form (the two walks hyp_config.resolve_root makes); None when neither."""
    for cur in (os.path.abspath(start), os.path.realpath(start)):
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


def resolve_root(cwd):
    """hyp_config.resolve_root({"cwd": cwd}) from the module beside this file; the pre-contract
    walk (payload cwd's toplevel, else CLAUDE_PROJECT_DIR, else the cwd) only if that import
    fails -- a wrapper must never crash a session start."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import hyp_config
        return hyp_config.resolve_root({"cwd": cwd} if cwd else None)
    except Exception:
        root = None
        if cwd and os.path.isdir(cwd):
            root = toplevel(cwd)
        if root is None:
            env_root = os.environ.get("CLAUDE_PROJECT_DIR")
            root = env_root if env_root and os.path.isdir(env_root) else (cwd if cwd and os.path.isdir(cwd) else os.getcwd())
        return root


def state_dir(root):
    b = os.path.realpath(root).encode("utf-8", "replace")
    key = "%08x%08x" % (zlib.crc32(b) & 0xFFFFFFFF, zlib.adler32(b) & 0xFFFFFFFF)
    base = os.environ.get("HYP_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "hyp")
    d = os.path.join(base, "session-start", key)
    os.makedirs(d, exist_ok=True)
    return d


def budget_s():
    raw = os.environ.get("HYP_SESSION_START_BUDGET", "")
    try:
        b = float(raw)
        if b < 0 or b != b:
            return DEFAULT_BUDGET_S
        return b
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_S


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def read_text(path):
    b = read_bytes(path)
    return None if b is None else b.decode("utf-8", "replace")


def age_s(path):
    try:
        return int(time.time() - os.stat(path).st_mtime)
    except OSError:
        return None


def lock_holder(lock):
    """(pid or None, age_s or None, lstart or None) of the lock directory. `lstart` is the
    third line of the pid file (the `ps -o lstart=` the RUNNER captured at lock creation);
    absent on a lock this amendment predates or a lock with no pid file at all."""
    try:
        st = os.stat(lock)
    except OSError:
        return None, None, None
    age = int(time.time() - st.st_mtime)
    pid = None
    lstart = None
    t = read_text(os.path.join(lock, "pid"))
    if t:
        lines = t.split("\n")
        try:
            pid = int((lines[0] if lines else "").strip() or "0") or None
        except ValueError:
            pid = None
        if len(lines) > 2 and lines[2].strip():
            lstart = lines[2].strip()
    return pid, age, lstart


def current_lstart(pid):
    """`ps -o lstart=` for a live pid right now, or None. Forked only when a lock LOOKS stale
    (dead-pid and absent-pid-file paths already returned before this is called), so it never
    runs on the common (no-lock-held) foreground path. os.popen, not subprocess: one exec, no
    module import cost on the hot path (see the module docstring)."""
    try:
        return os.popen('ps -o lstart= -p %d 2>/dev/null' % pid).read().strip() or None
    except Exception:
        return None


def remove_dir(path):
    try:
        for f in os.listdir(path):
            try:
                os.unlink(os.path.join(path, f))
            except OSError:
                pass
        os.rmdir(path)
    except OSError:
        pass


def reclaim_if_stale(lock):
    """H-309 rule, amended by this lane: a lock whose pid is dead, whose pid file is absent
    after the grace period, or whose live pid's current `ps -o lstart=` no longer matches the
    one recorded at lock creation (the pid was reused by a different process) is removed only
    by the invocation that wins the reclaim token. A lock whose recorded pid is alive with its
    recorded start time is NEVER reclaimed by age alone -- LOCK_MAX_S no longer applies to it.
    Returns True when the lock is gone afterwards."""
    pid, age, lstart = lock_holder(lock)
    if age is None:
        return True
    stale = False
    if pid is None:
        if age > LOCK_GRACE_S:
            stale = True
    elif not pid_alive(pid):
        stale = True
    elif lstart:
        cur = current_lstart(pid)
        if cur and cur != lstart:
            stale = True
    else:
        # a live pid with no recorded lstart (a lock predating this amendment): fall back to
        # the old age-based backstop rather than pin it forever.
        if age > LOCK_MAX_S:
            stale = True
    token = lock + ".reclaim"
    try:
        if time.time() - os.stat(token).st_mtime > LOCK_GRACE_S:
            os.rmdir(token)
    except OSError:
        pass
    if not stale:
        return False
    try:
        os.mkdir(token)
    except OSError:
        return False
    remove_dir(lock)
    try:
        os.rmdir(token)
    except OSError:
        pass
    return not os.path.isdir(lock)


def write_fg(sd, name, mode, finished, source, budget, fg, pid, rc, lock_seen_pid, t_epoch):
    """The wrapper's own foreground record: what it saw and what it did (JSON, one object)."""
    rec = ('{"name": %s, "mode": %s, "finished": %s, "source": %s, "budget_s": %s, "foreground_s": %.3f, '
           '"pid": %d, "rc": %s, "lock_seen_held": %s, "lock_seen_pid": %s, "started_epoch": %.3f}\n'
           % (jquote(name), jquote(mode), "true" if finished else "false", jquote(source), fmt(budget), fg,
              pid or 0, "null" if rc is None else str(int(rc)), "true" if lock_seen_pid is not None else "false",
              "null" if lock_seen_pid is None else str(int(lock_seen_pid)), t_epoch))
    try:
        tmp = os.path.join(sd, name + ".fg.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(rec)
        os.replace(tmp, os.path.join(sd, name + ".fg"))
    except OSError:
        pass


def cached_suffix(sd, name):
    a = age_s(os.path.join(sd, name + ".out"))
    return "no cached reading yet" if a is None else "cached reading %ds old" % a


def spawn_runner(env, nice=None):
    """Start /bin/sh -c RUNNER in a NEW session with every fd on /dev/null; returns its pid.
    (os.fork + os.setsid + os.execve: what subprocess.Popen(start_new_session=True,
    close_fds=True) does, without importing subprocess.) `nice`, when given, is applied to the
    forked child with os.nice() before execve -- the wake's side-runner asks for 19 (background
    priority); the primary runner never passes it."""
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            if nice:
                try:
                    os.nice(nice)
                except OSError:
                    pass
            fd = os.open(os.devnull, os.O_RDWR)
            os.dup2(fd, 0)
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            if fd > 2:
                os.close(fd)
            os.closerange(3, 1024)
            os.execve("/bin/sh", ["sh", "-c", RUNNER], env)
        except BaseException:  # noqa: BLE001 - never return into the parent's code path
            pass
        os._exit(127)
    return pid


# ---------------------------------------------------------------- the startup wake (ON, this lane)
def _git_paths(root):
    """(head_path, commondir_path_or_none) for `root`'s `.git`, worktree-aware, no subprocess."""
    gitpath = os.path.join(root, ".git")
    if os.path.isdir(gitpath):
        return os.path.join(gitpath, "HEAD"), gitpath
    if os.path.isfile(gitpath):
        txt = read_text(gitpath) or ""
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("gitdir:"):
                gitdir = line.split(":", 1)[1].strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.normpath(os.path.join(root, gitdir))
                return os.path.join(gitdir, "HEAD"), gitdir
    return None, None


def git_common_dir(root):
    """The repository's common .git dir: the worktree-private gitdir's `commondir` file resolved
    relative to it, or the gitdir itself for a main checkout / non-worktree repo. No subprocess."""
    _, gitdir = _git_paths(root)
    if not gitdir:
        return None
    commonfile = os.path.join(gitdir, "commondir")
    common = read_text(commonfile)
    if common:
        common = common.strip()
        if not os.path.isabs(common):
            common = os.path.normpath(os.path.join(gitdir, common))
        try:
            return os.path.realpath(common)
        except OSError:
            return common
    try:
        return os.path.realpath(gitdir)
    except OSError:
        return gitdir


def git_branch(root):
    """The branch name off the worktree-correct HEAD file, or None on a detached HEAD or an
    unreadable/missing one. No subprocess."""
    head_path, _ = _git_paths(root)
    if not head_path:
        return None
    txt = read_text(head_path)
    if not txt:
        return None
    txt = txt.strip()
    if txt.startswith("ref:"):
        ref = txt.split(":", 1)[1].strip()
        return ref.rsplit("/", 1)[-1] if "/" in ref else ref
    return None


def _om_state_base():
    override = os.environ.get("HYP_STATE_DIR")
    return override if override else os.path.join(os.path.expanduser("~"), ".hyp-state")


def om_state_root(root):
    """The single-path fallback key om-worker.py's own `state_root()` uses (sha256 of the real
    root path, first 16 hex) under the SAME base ($HYP_STATE_DIR or ~/.hyp-state). No longer the
    wake's primary target -- see `om_inbox_root` below -- but stays exactly what it always was:
    the shipped worker still falls back to it for a `root` that is not (or is no longer) a git
    checkout at all."""
    key = hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_om_state_base(), "om", key)


def om_inbox_root(root, common_dir):
    """Where `om-worker.py drain` (no `--inbox` override -- the hooks.json wiring here) actually
    reads by default. PORTED DRIFT vs the lane's fixture-pinned worker (H-DRAFT-35397146 bytes,
    which only ever had the single-path `state_root()` fallback below): the SHIPPED worker
    (v0.30.3) gained a shared, `common_dir`-keyed inbox per repository
    (H-DRAFT-a4a14ff4-om-outbox-carry-forward, kept 2026-09-14, ahead of this lane) so every
    linked worktree of one repository drains the same spool -- the equivalent of its
    `state_root_for_repo(common_dir)`, keyed by the SAME crc32+adler32 recipe this wrapper's own
    `state_dir()` already uses (never a realpath re-applied here: `common_dir` is realpath'd
    already by `git_common_dir`). Mirrors the shipped `_resolve_inbox_root`'s own branch exactly:
    `common_dir` truthy -> the shared repo-keyed root; falsy (this wrapper's pure-python git read
    found no checkout) -> the old single-path root, exactly as the shipped worker itself falls
    back. Never a subprocess."""
    if common_dir:
        b = common_dir.encode("utf-8", "replace")
        key = "%08x%08x" % (zlib.crc32(b) & 0xFFFFFFFF, zlib.adler32(b) & 0xFFFFFFFF)
        return os.path.join(_om_state_base(), "om", key)
    return om_state_root(root)


def write_pointer(root, session_id, transcript_path):
    """One pointer file, one `os.write` on an O_CREAT|O_EXCL descriptor. Returns True on a fresh
    write, False when a pointer for this session_id already exists (idempotent: never overwrites,
    never retries the write on a second startup of a resumed/cleared session). Lands under
    `om_inbox_root` -- the shared, common_dir-keyed root a default (`--inbox`-less) drain of the
    shipped v0.30.3 worker actually reads, ported drift from the lane's fixture-pinned worker."""
    common = git_common_dir(root)
    om_root = om_inbox_root(root, common)
    inbox = os.path.join(om_root, "inbox")
    os.makedirs(inbox, exist_ok=True)
    dest = os.path.join(inbox, session_id + ".json")
    branch = git_branch(root)
    body = ('{"session_id": %s, "transcript_path": %s, "root": %s, "common_dir": %s, '
            '"branch": %s, "timestamp": %.3f, "schema": 1}\n'
            % (jquote(session_id), jquote(transcript_path), jquote(os.path.realpath(root)),
               jquote(common) if common else "null",
               jquote(branch) if branch else "null", time.time()))
    data = body.encode("utf-8")
    try:
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError:
        return False
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return True


def read_pending(sd, also_name):
    """(session_id, transcript_path) of the session most recently started at this root (one
    boundary ago), or (None, None). Fixture fix round 1, REFUTE-FIXTURE-1 finding 3."""
    raw = read_text(os.path.join(sd, also_name + ".pending"))
    if not raw:
        return None, None
    sid = jstr(raw, "session_id")
    tp = jstr(raw, "transcript_path")
    if not sid or not tp:
        return None, None
    return sid, tp


def write_pending(sd, also_name, session_id, transcript_path):
    body = '{"session_id": %s, "transcript_path": %s}\n' % (jquote(session_id), jquote(transcript_path))
    tmp = os.path.join(sd, also_name + ".pending.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, os.path.join(sd, also_name + ".pending"))
    except OSError:
        pass


def do_also_wake(also, root, sd, source, raw, t_epoch):
    """The wake: one pointer write plus one detached, nice(19) fork of `<also_cmd>` under its
    own per-root lock `<also_name>.lock` in this same state directory -- never waited on, never
    allowed to raise past this function (a wrapper must never crash a session start over a
    feedback side-channel). Writes a `.fg`-shaped record for the wake itself (mode `spawned` |
    `in-flight` | `lock-race-lost` | `error`; `foreground_s` is always ~0 since nothing waits on
    it) so a replay or a census has something concrete to grade -- never counted as a foreground
    interpreter start (A2 counts the primary's own `.fg` rows, never this one)."""
    if not also:
        return
    also_name, also_cmd = also
    try:
        session_id = jstr(raw, "session_id") or ""
        transcript_path = jstr(raw, "transcript_path") or ""
        # ON amendment (fixture fix round 1, REFUTE-FIXTURE-1 finding 3): deliver the PREVIOUS
        # session's pointer, never this session's own. SessionStart fires before THIS session
        # has written any turn to its own transcript, so a pointer naming this session's own
        # transcript_path is guaranteed to find it missing (the pinned worker's drain() has no
        # catch-up path -- a quarantined pointer is never retried). The session most recently
        # started at this root, one boundary ago, has by construction already exited by the
        # time this session starts, so ITS transcript is complete. Tracked in a small per-root
        # `<also_name>.pending` file in this same state directory -- never the om inbox itself,
        # never read by the worker, and orthogonal to the two-worktree case (one `sd`, and so
        # one `.pending`, per root).
        prev_sid, prev_tp = read_pending(sd, also_name)
        if prev_sid and prev_tp:
            write_pointer(root, prev_sid, prev_tp)
        if session_id and transcript_path:
            write_pending(sd, also_name, session_id, transcript_path)
        lock = os.path.join(sd, also_name + ".lock")
        if os.path.isdir(lock) and not reclaim_if_stale(lock):
            pid, lage, _ = lock_holder(lock)
            write_fg(sd, also_name, "in-flight", False, source, 0.0, 0.0, pid, None, pid or 0, t_epoch)
            return  # single flight: a side-runner for this root is already in flight
        try:
            os.mkdir(lock)
        except OSError:
            write_fg(sd, also_name, "lock-race-lost", False, source, 0.0, 0.0, None, None, None, t_epoch)
            return  # lost the race to a concurrent wrapper for the same root
        payload_p = os.path.join(sd, also_name + ".payload")
        with open(payload_p, "w", encoding="utf-8") as f:
            f.write(raw)
        t0 = int(time.time())
        env = dict(os.environ)
        env.update({
            "HYP_ROOT": root,
            "SSB_CMD": also_cmd, "SSB_PAYLOAD": payload_p, "SSB_LOCK": lock, "SSB_T0": str(t0),
            "SSB_OUT_TMP": os.path.join(sd, also_name + ".out.tmp"),
            "SSB_OUT": os.path.join(sd, also_name + ".out"),
            "SSB_ERR": os.path.join(sd, also_name + ".err"),
            "SSB_RC": os.path.join(sd, also_name + ".rc"),
            "SSB_META_TMP": os.path.join(sd, also_name + ".meta.tmp"),
            "SSB_META": os.path.join(sd, also_name + ".meta"),
            "SSB_SOURCE": source.replace('"', "").replace("\\", ""), "SSB_BUDGET": "0",
        })
        side_pid = spawn_runner(env, nice=19)
        write_fg(sd, also_name, "spawned", False, source, 0.0, 0.0, side_pid, None, None, t_epoch)
    except Exception:
        try:
            write_fg(sd, also_name, "error", False, source, 0.0, 0.0, None, None, None, t_epoch)
        except Exception:
            pass
        return


# ------------------------------------------------------------- the reading surface (ON, this lane)
FEEDBACK_MAX_LINES = 8
FEEDBACK_PREFIX = "OM-FEEDBACK: "
# The SAME structural checks scripts/om-worker.py's own _forbidden_hit applies before it ever
# writes a row -- reused verbatim so this surface holds a CLASS of leak, never the fixture's
# own test-only canary literals (a production redactor must not memorize a test vocabulary).
FEEDBACK_FORBIDDEN_KEYS = ("tool_input", "prompt", "last_assistant_message")
FEEDBACK_FORBIDDEN_MARKERS = ("/users/", "$home")


def _feedback_forbidden(line):
    for k in FEEDBACK_FORBIDDEN_KEYS:
        if ('"%s"' % k) in line:
            return True
    low = line.lower()
    for m in FEEDBACK_FORBIDDEN_MARKERS:
        if m in low:
            return True
    return False


def print_om_feedback(sd, also):
    """Prints the side-runner's cached `<name2>.out` (this wrapper's OWN state key -- never the
    worker's own inbox key) at most 8 lines, each prefixed `OM-FEEDBACK: `, the first carrying
    `age=<seconds>` ahead of its own text; a ninth `... <n> more` line when the file holds more
    than 8; nothing when the file is absent or empty. Never raises past this function (a hook
    must never crash a session start over a feedback side-channel it did not ask to see)."""
    if not also:
        return
    also_name, _also_cmd = also
    try:
        path = os.path.join(sd, also_name + ".out")
        data = read_text(path)
        if not data:
            return
        lines = data.splitlines()
        if not lines:
            return
        a = age_s(path)
        shown = lines[:FEEDBACK_MAX_LINES]
        for i, line in enumerate(shown):
            text = "<held: 1 line>" if _feedback_forbidden(line) else line
            if i == 0:
                out("%sage=%d %s\n" % (FEEDBACK_PREFIX, a or 0, text))
            else:
                out("%s%s\n" % (FEEDBACK_PREFIX, text))
        if len(lines) > FEEDBACK_MAX_LINES:
            out("%s... %d more\n" % (FEEDBACK_PREFIX, len(lines) - FEEDBACK_MAX_LINES))
    except Exception:
        return


def cmd_run(name, command, t_start, also=None):
    t_epoch = time.time()
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    source = jstr(raw, "source") or ""
    root = resolve_root(jstr(raw, "cwd"))
    sd = state_dir(root)
    # the wake: after the root is resolved, before any waiting on the primary runner below
    do_also_wake(also, root, sd, source, raw, t_epoch)
    # the reading surface: after the fork, before the wrapper's own output (this lane)
    print_om_feedback(sd, also)
    budget = budget_s()
    lock = os.path.join(sd, name + ".lock")
    out_p = os.path.join(sd, name + ".out")
    prev = read_bytes(out_p)
    if os.path.isdir(lock) and not reclaim_if_stale(lock):
        pid, lage, _ = lock_holder(lock)
        if prev is not None:
            out(prev)
        out("SESSION-START-BUDGET: %s refresh in flight (pid %d, %ds); %s\n"
            % (name, pid or 0, lage or 0, cached_suffix(sd, name)))
        write_fg(sd, name, "in-flight", False, source, budget, time.monotonic() - t_start, pid, None, pid or 0, t_epoch)
        return 0
    try:
        os.mkdir(lock)
    except OSError:
        # lost the race to a concurrent wrapper for the same name: report in flight
        pid, lage, _ = lock_holder(lock)
        if prev is not None:
            out(prev)
        out("SESSION-START-BUDGET: %s refresh in flight (pid %d, %ds); %s\n"
            % (name, pid or 0, lage or 0, cached_suffix(sd, name)))
        write_fg(sd, name, "in-flight", False, source, budget, time.monotonic() - t_start, pid, None, pid or 0, t_epoch)
        return 0
    t0 = int(time.time())
    payload_p = os.path.join(sd, name + ".payload")
    with open(payload_p, "w", encoding="utf-8") as f:
        f.write(raw)
    env = dict(os.environ)
    env.update({
        "HYP_ROOT": root,
        "SSB_CMD": command, "SSB_PAYLOAD": payload_p, "SSB_LOCK": lock, "SSB_T0": str(t0),
        "SSB_OUT_TMP": out_p + ".tmp", "SSB_OUT": out_p,
        "SSB_ERR": os.path.join(sd, name + ".err"),
        "SSB_RC": os.path.join(sd, name + ".rc"),
        "SSB_META_TMP": os.path.join(sd, name + ".meta.tmp"), "SSB_META": os.path.join(sd, name + ".meta"),
        "SSB_SOURCE": source.replace('"', "").replace("\\", ""), "SSB_BUDGET": fmt(budget),
    })
    pid = spawn_runner(env)
    meta_p = os.path.join(sd, name + ".meta")
    deadline = t_start + budget
    finished_rc = None
    while True:
        m = read_text(meta_p)
        if m is not None and jint(m, "pid") == pid and jint(m, "started_at") == t0:
            finished_rc = jint(m, "rc")
            break
        try:
            os.waitpid(pid, os.WNOHANG)  # reap a finished runner; harmless when still running
        except OSError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_S, remaining))
    fg = time.monotonic() - t_start
    if finished_rc is not None:
        data = read_bytes(out_p)
        if data is not None:
            out(data)
        write_fg(sd, name, "finished", True, source, budget, fg, pid, finished_rc, None, t_epoch)
        return int(finished_rc)
    if prev is not None:
        out(prev)
    prev_age = None if prev is None else age_s(out_p)
    shown = "no cached reading yet" if prev is None else "cached reading %ds old shown" % (prev_age or 0)
    out("SESSION-START-BUDGET: %s over %ss foreground; %s; finishing detached (pid %d), next startup shows it\n"
        % (name, fmt(budget), shown, pid))
    write_fg(sd, name, "over-budget", False, source, budget, fg, pid, None, None, t_epoch)
    return 0


def _held_bytes(data):
    """Fixture fix round 1 (H-DRAFT-3aef12a5..., REFUTE-FIXTURE-1 finding 1): the SAME
    per-line hold `print_om_feedback` applies, over raw bytes rather than a prefixed line list
    -- `cmd_cached` dumps every named `.out` verbatim, and an ON `cached` replay over a planted
    canary printed it unfiltered (probe B). Line endings are preserved so the byte count a
    caller might depend on stays close to the original; only a forbidden line's TEXT changes."""
    try:
        text = data.decode("utf-8", "replace")
    except Exception:
        return data
    out_lines = []
    for line in text.splitlines(True):
        ending = ""
        body = line
        for e in ("\r\n", "\n", "\r"):
            if body.endswith(e):
                body, ending = body[: -len(e)], e
                break
        out_lines.append(("<held: 1 line>" if _feedback_forbidden(body) else body) + ending)
    return "".join(out_lines).encode("utf-8")


def cmd_cached(names):
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    source = jstr(raw, "source") or ""
    sd = state_dir(resolve_root(jstr(raw, "cwd")))
    have, none = [], []
    for name in names:
        data = read_bytes(os.path.join(sd, name + ".out"))
        if data is None:
            none.append(name)
            continue
        # Fixture fix round 1 (finding 1): `om-worker`'s cached reading is the SAME
        # content-free surface as the startup print, so it passes through the SAME hold --
        # never the fixture's canary literals verbatim, the same structural checks only.
        out(_held_bytes(data) if name == "om-worker" else data)
        have.append("%s %ds" % (name, age_s(os.path.join(sd, name + ".out")) or 0))
    out("SESSION-START-CACHE: source=%s; readings: %s; none: %s; heavy work runs on startup only\n"
        % (source, ", ".join(have) if have else "-", ", ".join(none) if none else "-"))
    return 0


def main(argv):
    t_start = time.monotonic()
    if len(argv) >= 4 and argv[1] == "run":
        also = None
        if len(argv) >= 7 and argv[4] == "--also":
            also = (argv[5], argv[6])
        return cmd_run(argv[2], argv[3], t_start, also=also)
    if len(argv) >= 3 and argv[1] == "cached":
        return cmd_cached(argv[2:])
    out("SESSION-START-BUDGET: usage: run <name> <command> [--also <name> <command>] | cached <name>...\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - a hook must never crash a session start
        try:
            out("SESSION-START-BUDGET: wrapper error %s\n" % type(exc).__name__)
        except Exception:
            pass
        sys.exit(0)
