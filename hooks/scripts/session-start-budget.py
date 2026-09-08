#!/usr/bin/env python3
"""session-start-budget.py -- a foreground budget for SessionStart hook commands (v2, cheap start).

    python3 -S -E session-start-budget.py run <name> <command>        # startup: budgeted, cached, detached
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
crc32 and adler32 (16 hex) of the real path of the checkout the payload cwd sits in (its
nearest ancestor with a .git entry; else CLAUDE_PROJECT_DIR; else the process cwd) -- the tree
the wrapped commands read. Python 3.9, stdlib only.
"""
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
    'printf "%s\\n%s\\n" "$$" "$SSB_T0" > "$SSB_LOCK/pid" 2>/dev/null; '
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
# Four external processes after the command itself (sh, mv, rm, mv; printf is a builtin): v1
# forked seven, and at load1 12 each fork on this host costs 0.1-5 s, so `echo x; exit 3` missed
# a 5 s budget. The finish time is the .meta mtime; no `date` fork.


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


def state_dir(cwd):
    root = None
    if cwd and os.path.isdir(cwd):
        root = toplevel(cwd)
    if root is None:
        env_root = os.environ.get("CLAUDE_PROJECT_DIR")
        root = env_root if env_root and os.path.isdir(env_root) else (cwd if cwd and os.path.isdir(cwd) else os.getcwd())
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
    """(pid or None, age_s or None) of the lock directory."""
    try:
        st = os.stat(lock)
    except OSError:
        return None, None
    age = int(time.time() - st.st_mtime)
    pid = None
    t = read_text(os.path.join(lock, "pid"))
    if t:
        try:
            pid = int(t.split("\n", 1)[0].strip() or "0") or None
        except ValueError:
            pid = None
    return pid, age


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
    """H-309 rule: a lock whose pid is dead, whose pid file is absent after the grace
    period, or which is older than LOCK_MAX_S is removed only by the invocation that wins
    the reclaim token. Returns True when the lock is gone afterwards."""
    pid, age = lock_holder(lock)
    if age is None:
        return True
    stale = False
    if pid is not None:
        if not pid_alive(pid):
            stale = True
    elif age > LOCK_GRACE_S:
        stale = True
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


def spawn_runner(env):
    """Start /bin/sh -c RUNNER in a NEW session with every fd on /dev/null; returns its pid.
    (os.fork + os.setsid + os.execve: what subprocess.Popen(start_new_session=True,
    close_fds=True) does, without importing subprocess.)"""
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
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


def cmd_run(name, command, t_start):
    t_epoch = time.time()
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    source = jstr(raw, "source") or ""
    cwd = jstr(raw, "cwd")
    sd = state_dir(cwd)
    budget = budget_s()
    lock = os.path.join(sd, name + ".lock")
    out_p = os.path.join(sd, name + ".out")
    prev = read_bytes(out_p)
    if os.path.isdir(lock) and not reclaim_if_stale(lock):
        pid, lage = lock_holder(lock)
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
        pid, lage = lock_holder(lock)
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


def cmd_cached(names):
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    source = jstr(raw, "source") or ""
    sd = state_dir(jstr(raw, "cwd"))
    have, none = [], []
    for name in names:
        data = read_bytes(os.path.join(sd, name + ".out"))
        if data is None:
            none.append(name)
            continue
        out(data)
        have.append("%s %ds" % (name, age_s(os.path.join(sd, name + ".out")) or 0))
    out("SESSION-START-CACHE: source=%s; readings: %s; none: %s; heavy work runs on startup only\n"
        % (source, ", ".join(have) if have else "-", ", ".join(none) if none else "-"))
    return 0


def main(argv):
    t_start = time.monotonic()
    if len(argv) >= 4 and argv[1] == "run":
        return cmd_run(argv[2], argv[3], t_start)
    if len(argv) >= 3 and argv[1] == "cached":
        return cmd_cached(argv[2:])
    out("SESSION-START-BUDGET: usage: run <name> <command> | cached <name>...\n")
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
