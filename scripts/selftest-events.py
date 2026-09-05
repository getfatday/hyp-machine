#!/usr/bin/env python3
"""selftest-events.py -- consumer-runnable proof of the event stream.

Builds its own scratch consumer repo under a temp dir (never touches yours) and
checks the installed plugin end to end:

  1  append two records through the gated CLI (both land; stream = 2 lines)
  2  idempotent re-append (identical records land ZERO; stream still 2 lines)
  3  the resolver's events-cursor join surfaces both exactly once and the
     cursor advances to the stream length (H-239)
  4  the same session's next boundary is silent (once-per-channel re-fire)
  5  profile gate: a capture-profile repo is refused with the typed reason
  6  the H-239 byte-compare contract: stripping the join block + call line
     from the shipped resolver yields a stock variant that (a) regenerates the
     shipped bytes exactly when re-inserted at its anchors (mechanical
     additivity, the H-230 strip-proof pattern) and (b) prints byte-identical
     output on a stream-less repo -- non-event rows are untouched by the join.

Run it after touching events_lib.py, emit-event.py, or the resolver:
  python3 "$CLAUDE_PLUGIN_ROOT/scripts/selftest-events.py"
Exit 0 on PASS (prints one line per check), 1 on any FAIL. Stdlib only; all
writes stay under the temp dir.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
RESOLVER = os.path.join(PLUGIN, "hooks", "scripts", "session_resolver.py")
BLOCK_BEGIN = "# <<<H-239-EVENTS-JOIN block begin>>>"
BLOCK_END = "# <<<H-239-EVENTS-JOIN block end>>>"
CALL_MARK = "# <<<H-239-EVENTS-JOIN call>>>"

FAILURES = []
RAN = []


def check(name, ok, detail=""):
    print("CHECK %-4s %s%s" % ("ok" if ok else "FAIL", name,
                               (" (%s)" % detail) if detail else ""))
    RAN.append(name)
    if not ok:
        FAILURES.append(name)


def build_repo(base, name, profile):
    root = os.path.join(base, name)
    os.makedirs(os.path.join(root, ".claude"))
    with open(os.path.join(root, ".claude", "hyp.json"), "w", encoding="utf-8") as f:
        json.dump({"profile": profile}, f)
    os.makedirs(os.path.join(root, "ledger"))
    with open(os.path.join(root, "ledger", "ledger.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"date": "2026-01-01", "slug": "selftest-never-matches",
                            "hit": "selftest fixture row"}) + "\n")
    os.makedirs(os.path.join(root, "hypotheses"))
    nodes = os.path.join(root, "operating-model", "scratch", "events")
    os.makedirs(nodes)
    tpl_dir = os.path.join(PLUGIN, "templates", "event-nodes")
    for fn in os.listdir(tpl_dir):
        with open(os.path.join(tpl_dir, fn), encoding="utf-8") as f:
            text = f.read().replace("{{CONTEXT}}", "scratch")
        with open(os.path.join(nodes, fn), "w", encoding="utf-8") as f:
            f.write(text)
    return root


def emit(root, *args):
    p = subprocess.run([sys.executable, os.path.join(HERE, "emit-event.py"),
                        "--root", root] + list(args),
                       capture_output=True, text=True, timeout=60)
    try:
        return p.returncode, json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:
        return p.returncode, {"status": "unparseable", "raw": p.stdout + p.stderr}


def resolve(resolver_path, root, session_id):
    payload = json.dumps({"session_id": session_id, "cwd": root})
    p = subprocess.run([sys.executable, resolver_path, root],
                       input=payload, capture_output=True, text=True, timeout=60)
    return p.stdout


def main():
    base = tempfile.mkdtemp(prefix="hyp-selftest-events-")
    try:
        repo = build_repo(base, "consumer", "experiments")
        stream = os.path.join(repo, "ledger", "events.jsonl")

        # -- 1: two appends land ------------------------------------------------
        rc1, r1 = emit(repo, "ledger-record-appended", "--kind", "intent",
                       "--slug", "selftest-intent", "--date", "2026-01-02",
                       "--caused-by", "selftest")
        rc2, r2 = emit(repo, "advisory-surfaced", "--policy", "policy/selftest",
                       "--subject", "selftest-lane", "--message", "hello stream",
                       "--date", "2026-01-02", "--caused-by", "selftest")
        lines = open(stream, encoding="utf-8").read().splitlines() \
            if os.path.isfile(stream) else []
        check("append-two", rc1 == 0 and rc2 == 0
              and r1.get("status") == "appended" and r2.get("status") == "appended"
              and len(lines) == 2,
              "statuses %s/%s, %d lines" % (r1.get("status"), r2.get("status"),
                                            len(lines)))

        # -- 2: idempotent re-append -------------------------------------------
        rc3, r3 = emit(repo, "ledger-record-appended", "--kind", "intent",
                       "--slug", "selftest-intent", "--date", "2026-01-02",
                       "--caused-by", "selftest")
        rc4, r4 = emit(repo, "advisory-surfaced", "--policy", "policy/selftest",
                       "--subject", "selftest-lane", "--message", "hello stream",
                       "--date", "2026-01-02", "--caused-by", "selftest")
        lines = open(stream, encoding="utf-8").read().splitlines()
        check("idempotent-reappend", rc3 == 0 and rc4 == 0
              and r3.get("status") == "skipped" and r4.get("status") == "skipped"
              and len(lines) == 2,
              "statuses %s/%s, %d lines" % (r3.get("status"), r4.get("status"),
                                            len(lines)))

        # -- 3: cursor surfaces exactly once and advances -----------------------
        out1 = resolve(RESOLVER, repo, "selftest-A")
        ev_lines = [l for l in out1.splitlines() if l.startswith("EVENT-STREAM\t")]
        new_lines = [l for l in out1.splitlines() if l.startswith("EVENTS-NEW\t")]
        cursor = os.path.join(repo, ".claude", "events-cursor", "selftest-A.cursor")
        cursor_val = open(cursor).read().strip() if os.path.isfile(cursor) else None
        check("cursor-surfaces-and-advances",
              len(ev_lines) == 2 and len(new_lines) == 1 and cursor_val == "2",
              "%d EVENT-STREAM, cursor=%r" % (len(ev_lines), cursor_val))

        # -- 4: silent re-fire at the next boundary -----------------------------
        out2 = resolve(RESOLVER, repo, "selftest-A")
        ev2 = [l for l in out2.splitlines() if l.startswith("EVENT-STREAM\t")]
        check("silent-refire", len(ev2) == 0 and "EVENTS-NEW" not in out2,
              "%d EVENT-STREAM on re-fire" % len(ev2))

        # -- 5: profile gate ----------------------------------------------------
        gated = build_repo(base, "capture-consumer", "capture")
        rc5, r5 = emit(gated, "ledger-record-appended", "--kind", "intent",
                       "--slug", "x", "--date", "2026-01-02", "--caused-by", "s")
        check("profile-gate", rc5 == 3 and r5.get("status") == "refused",
              "rc=%d status=%s" % (rc5, r5.get("status")))

        # -- 6: the H-239 byte-compare contract ---------------------------------
        shipped = open(RESOLVER, encoding="utf-8").read()
        ok_markers = (shipped.count(BLOCK_BEGIN) == 1
                      and shipped.count(BLOCK_END) == 1
                      and shipped.count(CALL_MARK) == 1)
        check("join-markers-present", ok_markers, "begin/end/call exactly once")
        if ok_markers:
            b = shipped.index(BLOCK_BEGIN)
            e = shipped.index(BLOCK_END) + len(BLOCK_END)
            block_segment = shipped[b:e]
            # the block was inserted as BLOCK + '\n\n\n' before the run() anchor
            stock = shipped.replace(block_segment + "\n\n\n", "", 1)
            call_line = next(l for l in shipped.splitlines() if CALL_MARK in l)
            stock = stock.replace("\n" + call_line, "", 1)
            run_anchor = "def run(ledger_path, hyp_dir, om_dir, repo_root):"
            print_anchor = ("        print('{}\\t{}\\t{}\\t{}'"
                            ".format(tag, date, slug, hit))")
            stock_clean = (BLOCK_BEGIN not in stock and CALL_MARK not in stock
                           and stock.count(run_anchor) == 1
                           and stock.count(print_anchor) == 1)
            check("strip-yields-stock", stock_clean, "anchors exactly once, join gone")
            regen = stock.replace(run_anchor,
                                  block_segment + "\n\n\n" + run_anchor, 1)
            regen = regen.replace(print_anchor, print_anchor + "\n" + call_line, 1)
            check("additivity-regenerates-shipped", regen == shipped,
                  "%d vs %d bytes" % (len(regen), len(shipped)))
            # non-event output byte-identical on a stream-less repo
            stock_dir = os.path.join(base, "stock-resolver")
            os.makedirs(stock_dir)
            stock_path = os.path.join(stock_dir, "session_resolver.py")
            with open(stock_path, "w", encoding="utf-8") as f:
                f.write(stock)
            shutil.copy(os.path.join(PLUGIN, "hooks", "scripts", "hyp_config.py"),
                        os.path.join(stock_dir, "hyp_config.py"))
            bare = build_repo(base, "bare-consumer", "experiments")
            on_out = resolve(RESOLVER, bare, "selftest-B")
            off_out = resolve(stock_path, bare, "selftest-B")
            check("byte-compare-no-events", on_out == off_out and on_out != "",
                  "%d bytes each" % len(on_out))
            # and with events present, the stock output is a byte-prefix of the
            # shipped output (the join prints LAST; every non-event row untouched)
            on_ev = resolve(RESOLVER, repo, "selftest-C")
            off_ev = resolve(stock_path, repo, "selftest-C")
            check("non-event-rows-byte-prefix",
                  on_ev.startswith(off_ev) and "EVENT-STREAM\t" in on_ev,
                  "prefix %d of %d bytes" % (len(off_ev), len(on_ev)))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if FAILURES:
        print("selftest-events: FAIL (%s)" % ", ".join(FAILURES))
        return 1
    print("selftest-events: PASS (%d checks)" % len(RAN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
