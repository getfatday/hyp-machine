#!/usr/bin/env python3
"""watch-dispatch.py -- the file-watch dispatch trigger (H-240), consumer port.

PROVENANCE -- COUNTED port of the kept H-240 fixture watcher
(experiments/runs/H-240/fixture/watchlab-template/scripts/watch-dispatch.py in
the source lab; H-240-watch-triggered-dispatch KEPT 2026-09-02, 2x5/5: fires
exactly once per debounced append burst, zero firings on unrelated writes, and
the H-217 firing contract -- one capped adoption per firing, gate consulted,
K-strikes quarantine -- held unchanged under the trigger swap). Mechanism
unchanged: a kqueue vnode watch on the event stream (NOTE_WRITE|NOTE_EXTEND --
the same kernel facility launchd's WatchPaths uses) with a trailing-edge settle
debounce (DEBOUNCE_S frozen at the H-240 registration constant); startup is
SILENT -- pre-existing stream lines never fire.

Named divergences from the counted fixture copy (consumer resolution only):
  - repo root = cwd (run from your repo root; launchd sets it via
    WorkingDirectory), stream path from `.claude/hyp.json` `events_file`
    (default ledger/events.jsonl);
  - the firing handler is the shipped H-217 entrypoint `hyp-resume.sh` beside
    this script (one dispatch read + at most one capped adoption per firing;
    relaunch-class actions consult scripts/dispatch-gate.py, K=2 quarantine --
    the exact contract H-240's OFF arm re-measured), with any argv after `--`
    passed through as the capped invocation command;
  - runtime state lives in `.claude/watch-dispatch/` (PID, READY, the
    STOP-WATCH marker), never committed;
  - the fixture's calibration seam (H240_CAL) is not shipped -- it existed to
    seed failure classes in counted runs;
  - kqueue is macOS/BSD; elsewhere this exits 2 with a typed reason (on macOS
    prefer the launchd WatchPaths plist: scripts/install-watch-plist.sh,
    emitted never auto-loaded).

This is the foreground watcher entrypoint the H-240 On-keep ships beside the
interval plist; launchd WatchPaths is the persistent variant of the same wake.
"""
import json
import os
import sys
import time

DEBOUNCE_S = 2.0          # frozen at H-240 registration (the debounce constant)
POLL_S = 0.5              # kevent wait quantum (STOP-marker poll cadence)
SETTLE_POLL_S = 0.2

STATE_DIR = os.path.join(".claude", "watch-dispatch")
STOP = os.path.join(STATE_DIR, "STOP-WATCH")
READY = os.path.join(STATE_DIR, "WATCH-READY")
PIDF = os.path.join(STATE_DIR, "WATCH-PID")


def stream_relpath():
    rel = "ledger/events.jsonl"
    try:
        with open(os.path.join(".claude", "hyp.json"), encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("events_file") if isinstance(data, dict) else None
        if isinstance(v, str) and v.strip():
            rel = v.strip().strip("/")
    except Exception:
        pass
    return os.path.join(*rel.split("/"))


def log(obj):
    obj["ts_unix"] = int(time.time())
    print(json.dumps(obj, sort_keys=True), flush=True)


def line_count(path):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def fsize(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return -1


def fire(handler_args):
    """One firing = one hyp-resume.sh invocation: dispatch read + at most one
    capped adoption (H-217 contract, preserved verbatim by the H-240 keep)."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    argv = ["/bin/bash", os.path.join(here, "hyp-resume.sh")] + list(handler_args)
    r = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    log({"kind": "fired", "rc": r.returncode,
         "handler_stdout": (r.stdout or "").strip()[-800:],
         "handler_stderr": (r.stderr or "").strip()[-300:]})
    return r.returncode


def settle(stream):
    """Trailing-edge debounce: return once the stream has been quiet (no size
    change) for DEBOUNCE_S."""
    quiet_since = time.monotonic()
    size = fsize(stream)
    while True:
        time.sleep(SETTLE_POLL_S)
        now_size = fsize(stream)
        if now_size != size:
            size = now_size
            quiet_since = time.monotonic()
        if time.monotonic() - quiet_since >= DEBOUNCE_S:
            return


def main(argv):
    handler_args = []
    if "--" in argv:
        idx = argv.index("--")
        handler_args = argv[idx:]  # keep the "--" so hyp-resume.sh parses it
        argv = argv[:idx]
    if len(argv) > 1:
        log({"kind": "abort", "error": "unknown args %r "
             "(usage: watch-dispatch.py [-- <capped invocation cmd ...>], "
             "run from the repo root)" % argv[1:]})
        return 2
    try:
        import select
        select.kqueue
    except (ImportError, AttributeError):
        log({"kind": "abort", "error": "kqueue unavailable on this platform; "
             "use the launchd WatchPaths plist on macOS "
             "(scripts/install-watch-plist.sh, emitted never auto-loaded)"})
        return 2
    import select

    stream = stream_relpath()
    if not os.path.isfile(stream):
        log({"kind": "abort", "error": "no event stream at %s -- the watcher "
             "only wakes on appends to an existing stream (emit one event "
             "first: scripts/emit-event.py)" % stream})
        return 2
    os.makedirs(STATE_DIR, exist_ok=True)
    init_lines = line_count(stream)
    with open(PIDF, "w", encoding="utf-8") as f:
        f.write("%d\n" % os.getpid())

    kq = select.kqueue()
    fd = os.open(stream, os.O_RDONLY)
    vnode_flags = (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND
                   | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME)
    kevs = [select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                          flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                          fflags=vnode_flags)]
    kq.control(kevs, 0, 0)

    with open(READY, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "init_lines": init_lines,
                   "debounce_s": DEBOUNCE_S, "stream": stream}, f,
                  sort_keys=True)
        f.write("\n")
    log({"kind": "ready", "init_lines": init_lines, "stream": stream,
         "note": "silent start: pre-existing lines never fire; "
                 "touch %s to stop" % STOP})

    seen_lines = init_lines
    batches = 0
    while True:
        if os.path.isfile(STOP):
            log({"kind": "stopped", "reason": "STOP-WATCH marker"})
            return 0
        events = kq.control(None, 8, POLL_S)
        if not events:
            continue
        if not any(e.ident == fd for e in events):
            continue
        settle(stream)
        pending_now = line_count(stream)
        pending = pending_now - seen_lines
        if pending <= 0:
            # stale kevent: the writes behind it were coalesced into the
            # previous firing during its settle window -- dedup, never re-fire
            # (the debounce contract: one firing per append, not per kevent)
            log({"kind": "stale-wake", "pending_lines": pending})
            continue
        batches += 1
        log({"kind": "wake", "batch": batches, "pending_lines": pending})
        fire(handler_args)
        seen_lines = line_count(stream)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
