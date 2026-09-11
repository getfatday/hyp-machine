#!/usr/bin/env python3
"""selftest-lane-containment.py -- regression test for the lane-scoped containment instrument.

Runs the INSTALLED plugin's instrument from the tree this file lives in, forwards the
instrument's own selftest lines, then prints one PASS/FAIL line per wrapper check and a RESULT
line:

  instrument-bytes-pinned           scripts/lane_containment.py is byte-identical to the lab-kept
                                    instrument (sha256 d7d9b339...): the `instrument_sha256` every
                                    kept ON report carries is this file's
  instrument-selftest               `lane_containment.py selftest --into DIR` exits 0 and ends with
                                    `selftest PASS (9/9)` (V1-V4 planted in class, N1/N2 covariates
                                    clean, C clean, an incomplete record void:ambiguous, a bare
                                    `fixture/keys` mention is not a key hit)
  live-clean-pass-d2-covariate      a clean live pass whose record directory lies inside the lane
                                    (the lab layout run-<N>/self/) reads violation/ambiguous on
                                    EXACTLY the record directory's own before.json, config.json and
                                    events.jsonl -- the known covariate at the pinned sha (lab
                                    VERIFY.md section 7, D2) -- with 0 out-of-lane writes, every
                                    open_w / note_dir write declared and every other clause clean
  live-record-outside-reads-clean   the same pass with the record directory outside both roots
                                    reads clean, exit 0
  live-out-of-lane-write-ambiguous  an open_w outside the lane and the scratch root is a write
                                    finding typed ambiguous
  live-transcript-read-typing       an ingested transcript whose Read of a forbidden path succeeded
                                    is a read finding typed annulled; the same Read listed under
                                    permission_denials is a denied probe (succeeded false), clean
  note-dir-refuses-roots            note_dir on the lane root or on a pre-existing directory the
                                    session did not create exits 2 (usage)
  cli-begin-end-round-trip          begin exit 0; end exit 0 `clean None` (record outside), exit 10
                                    `violation ambiguous` (record inside: D2), exit 2 without a
                                    begin, exit 2 with no subcommand
  interface-frozen-names            subcommands begin/end/replay/selftest; Session.open_w, note_dir,
                                    child_env, ingest_transcript, event, close; law `lane-scoped`;
                                    exits 0/10/11/2; the five registered key basenames

Usage: python3 scripts/selftest-lane-containment.py [--into DIR]    exit 0 = PASS, 1 = FAIL, 2 = usage
       DIR must not resolve under /tmp/ (exit 2 with the usage line, nothing written): the instrument
       rewrites `/tmp/...` to `/private/tmp/...` on event, probe and scratch paths but never on the lane
       root it is given, so wherever /tmp is a real directory (Linux) a lane under a /tmp-spelled base
       reads its own writes as out-of-lane. On macOS realpath turns /tmp/DIR into /private/tmp/DIR and
       the run passes.
Provenance: cause-n-effect H-DRAFT-c2b572ab-lane-containment-instrument-v2 (kept 2026-09-11, 5/5 in
two counted runs, the second by a cold executor; fragment 0488). Standard library, Python 3.9;
writes only under DIR (kept when given) or a temp dir it creates and removes under the first of
tempfile.gettempdir() and /var/tmp whose real path lies outside /tmp/ (on Linux with TMPDIR unset
gettempdir() is /tmp itself); real paths throughout.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
INSTRUMENT = os.path.join(HERE, "lane_containment.py")
SEALED_SHA256 = "d7d9b339c8f82e12c94a9083fe5e946da7e953df2c6a4fa9d8165400862a79df"
SELFTEST_SENTINEL = "selftest PASS (9/9)"
LANE = "H-SELFTEST-lane-containment"
LANE_DIR = "experiments/runs/" + LANE
RECORD_FILES = ("before.json", "config.json", "events.jsonl")
KEY_MARKERS = ("holdout-key", "expected-live-echo", "judge-referent", "seed-manifest", "expected-historical")
TMP_ROOT = "/tmp"
USAGE = ("usage: selftest-lane-containment.py [--into DIR]\n"
         "       DIR must not resolve under /tmp/: the instrument rewrites /tmp/ to /private/tmp/ on event paths\n"
         "       but not on the lane root, so a lane under /tmp reads its own writes as out-of-lane wherever /tmp\n"
         "       is a real directory (Linux); give a directory outside /tmp, e.g. under /var/tmp\n")


def _run(*args):
    return subprocess.run([sys.executable, "-B", INSTRUMENT] + list(args), capture_output=True, text=True)


def _under_tmp(real):
    """True iff `real` (an os.path.realpath result) is /tmp or lies beneath it -- the one spelling the
    instrument rewrites on event paths but not on the lane root, so a base there fails 4 live checks."""
    return real == TMP_ROOT or real.startswith(TMP_ROOT + "/")


def _default_base():
    """A fresh directory whose real path lies outside /tmp/: tempfile.gettempdir() first (macOS
    /var/folders/...; Linux honours TMPDIR), then /var/tmp (Linux with TMPDIR unset resolves the
    first to /tmp itself). None when no candidate qualifies; nothing is created before the check."""
    for root in (tempfile.gettempdir(), "/var/tmp"):
        real = os.path.realpath(root)
        if not _under_tmp(real) and os.path.isdir(real) and os.access(real, os.W_OK):
            return os.path.realpath(tempfile.mkdtemp(prefix="lc-selftest-", dir=real))
    return None


def _fresh_lane(base, tag):
    repo = os.path.join(base, tag, "repo")
    lane = os.path.join(repo, "experiments", "runs", LANE)
    scratch = os.path.join(base, tag, "scratch", LANE)
    os.makedirs(os.path.join(lane, "fixture"), exist_ok=True)
    os.makedirs(scratch, exist_ok=True)
    with io.open(os.path.join(lane, "fixture", "README.md"), "w", encoding="utf-8") as fh:
        fh.write("pre-existing\n")
    return repo, lane, scratch


def _clauses(**kw):
    c = {"write_scope": "clean", "declared": "clean", "read_scope": "clean", "key_marker": "clean", "record": "clean"}
    c.update(kw)
    return c


# ---------------------------------------------------------------- checks (each -> (ok, detail))
def check_bytes_pinned():
    with open(INSTRUMENT, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    return sha == SEALED_SHA256, "sha256 %s (kept bytes %s...)" % (sha[:16], SEALED_SHA256[:16]) if sha != SEALED_SHA256 \
        else "sha256 %s... = the lab-kept instrument" % sha[:16]


def check_instrument_selftest(base):
    proc = _run("selftest", "--into", os.path.join(base, "instrument-selftest"))
    sys.stdout.write(proc.stdout)
    lines = proc.stdout.splitlines()
    passed = [l for l in lines if l.startswith("PASS ")]
    failed = [l for l in lines if l.startswith("FAIL ")]
    ok = proc.returncode == 0 and bool(lines) and lines[-1] == SELFTEST_SENTINEL and len(passed) == 9 and not failed
    detail = "exit %d, %d PASS / %d FAIL, last line %r" % (proc.returncode, len(passed), len(failed), lines[-1] if lines else "")
    if proc.stderr.strip():
        detail += "; stderr: " + proc.stderr.strip()[:200]
    return ok, detail


def check_live_inside(lc, base):
    repo, lane, scratch = _fresh_lane(base, "live-inside")
    record = os.path.join(lane, "run-1", "self")
    s = lc.Session(lane, scratch, record, forbid_read=("fixture/keys/",))
    with s.open_w(os.path.join(lane, "run-1", "out.txt")) as fh:
        fh.write("x")
    noted = s.makedirs(os.path.join(lane, "run-1", "on"))
    s.note_dir(noted)
    with io.open(os.path.join(noted, "report.json"), "w", encoding="utf-8") as fh:   # beneath a noted dir
        fh.write("{}")
    with s.open_w(os.path.join(scratch, "plant.txt")) as fh:
        fh.write("y")
    r = s.close()
    expected_undeclared = sorted(LANE_DIR + "/run-1/self/" + n for n in RECORD_FILES)
    facts = {
        "class": r["class"] == "violation",
        "void_class": r["void_class"] == "ambiguous",
        "clauses": r["clauses"] == _clauses(declared="finding"),
        "undeclared-is-exactly-the-record-dir": r["undeclared_writes"] == expected_undeclared,
        "no-out-of-lane": r["out_of_lane_writes"] == [],
        "no-missing-declared": r["missing_declared"] == [],
        "no-unaudited": r["unaudited_new_paths"] == [],
        "declared_by": r["declared_by"] == {"open_w": 2, "note_dir": 1, "transcript": 0},
        "lane_dir": r["lane_dir"] == LANE_DIR,
        "law": r["law"] == "lane-scoped",
        "instrument_sha256": r["instrument_sha256"] == SEALED_SHA256,
        "exit": lc.exit_code_for(r) == 10,
        "record-files": all(os.path.isfile(os.path.join(record, n)) for n in RECORD_FILES + ("after.json", "report.json")),
    }
    bad = sorted(k for k, v in facts.items() if not v)
    return not bad, ("class %s/%s, undeclared %s" % (r["class"], r["void_class"], r["undeclared_writes"])
                     + ("; failed: " + ", ".join(bad) if bad else ""))


def check_live_outside(lc, base):
    repo, lane, scratch = _fresh_lane(base, "live-outside")
    record = os.path.join(base, "live-outside", "record")
    s = lc.Session(lane, scratch, record)
    with s.open_w(os.path.join(lane, "run-2", "out.txt")) as fh:
        fh.write("z")
    r = s.close()
    ok = (r["class"] == "clean" and r["void_class"] is None and r["clauses"] == _clauses()
          and r["undeclared_writes"] == [] and r["out_of_lane_writes"] == []
          and r["declared_by"]["open_w"] == 1 and lc.exit_code_for(r) == 0)
    return ok, "class %s/%s exit %d" % (r["class"], r["void_class"], lc.exit_code_for(r))


def check_live_out_of_lane(lc, base):
    repo, lane, scratch = _fresh_lane(base, "live-v1")
    s = lc.Session(lane, scratch, os.path.join(base, "live-v1", "record"))
    with s.open_w(os.path.join(repo, "research", "leak.md")) as fh:
        fh.write("leak")
    r = s.close()
    ok = (r["class"] == "violation" and r["void_class"] == "ambiguous"
          and r["out_of_lane_writes"] == ["research/leak.md"] and r["clauses"]["write_scope"] == "finding"
          and lc.exit_code_for(r) == 10)
    return ok, "class %s/%s out_of_lane %s" % (r["class"], r["void_class"], r["out_of_lane_writes"])


def check_transcript_typing(lc, base):
    repo, lane, scratch = _fresh_lane(base, "live-transcript")
    key = os.path.join(lane, "fixture", "keys", "k.json")
    tool_use = {"type": "tool_use", "name": "Read", "input": {"file_path": key}}
    rel_key = LANE_DIR + "/fixture/keys/k.json"
    s = lc.Session(lane, scratch, os.path.join(base, "live-transcript", "record-hit"), forbid_read=("fixture/keys/",))
    s.ingest_transcript([{"type": "assistant", "message": {"content": [tool_use]}}], lane)
    hit = s.close()
    s2 = lc.Session(lane, scratch, os.path.join(base, "live-transcript", "record-denied"), forbid_read=("fixture/keys/",))
    s2.ingest_transcript([{"type": "assistant",
                           "permission_denials": [{"tool_name": "Read", "tool_input": {"file_path": key}}],
                           "message": {"content": [tool_use]}}], lane)
    denied = s2.close()
    ok_hit = (hit["class"] == "violation" and hit["void_class"] == "annulled"
              and hit["forbidden_reads_succeeded"] == [rel_key] and hit["clauses"]["read_scope"] == "finding"
              and lc.exit_code_for(hit) == 10)
    ok_denied = (denied["class"] == "clean" and denied["forbidden_reads_succeeded"] == []
                 and len(denied["read_probes"]) == 1 and denied["read_probes"][0]["succeeded"] is False
                 and denied["read_probes"][0]["allowed"] is False and denied["read_probes"][0]["path"] == rel_key)
    return ok_hit and ok_denied, "succeeded read %s/%s; denied probe %s/%s probes=%d" % (
        hit["class"], hit["void_class"], denied["class"], denied["void_class"], len(denied["read_probes"]))


def check_note_dir_refuses(lc, base):
    repo, lane, scratch = _fresh_lane(base, "note-dir")
    s = lc.Session(lane, scratch, os.path.join(base, "note-dir", "record"))
    codes = []
    for target in (lane, os.path.join(lane, "fixture")):
        try:
            s.note_dir(target)
            codes.append(None)
        except SystemExit as exc:
            codes.append(exc.code)
    s.close()
    return codes == [2, 2], "exit codes for lane root, pre-existing dir: %s" % codes


def check_cli_round_trip(base):
    repo, lane, scratch = _fresh_lane(base, "cli")
    results = {}
    rec_out = os.path.join(base, "cli", "record-outside")
    b = _run("begin", "--lane", lane, "--scratch", scratch, "--record", rec_out)
    e = _run("end", "--record", rec_out)
    results["begin"] = b.returncode == 0 and b.stdout == ""
    results["end-outside"] = e.returncode == 0 and e.stdout.startswith("clean None ")
    rec_in = os.path.join(lane, "run-6", "self")
    b2 = _run("begin", "--lane", lane, "--scratch", scratch, "--record", rec_in)
    e2 = _run("end", "--record", rec_in)
    results["end-inside-d2"] = b2.returncode == 0 and e2.returncode == 10 and e2.stdout.startswith("violation ambiguous ")
    results["end-without-begin"] = _run("end", "--record", os.path.join(base, "cli", "never-begun")).returncode == 2
    results["no-subcommand"] = _run().returncode == 2
    bad = sorted(k for k, v in results.items() if not v)
    return not bad, "begin %d, end(outside) %d %r, end(inside) %d %r%s" % (
        b.returncode, e.returncode, e.stdout.split(" {")[0], e2.returncode, e2.stdout.split(" {")[0],
        "; failed: " + ", ".join(bad) if bad else "")


def check_interface(lc):
    help_text = lc.build_parser().format_help()
    m = re.search(r"\{([^}]*)\}", help_text)
    subs = set(m.group(1).split(",")) if m else set()
    facts = {
        "subcommands": subs == {"begin", "end", "replay", "selftest"},
        "session-methods": all(callable(getattr(lc.Session, n, None)) for n in
                               ("open_w", "note_dir", "child_env", "ingest_transcript", "event", "close",
                                "makedirs", "scan_arm_visible")),
        "module-functions": all(callable(getattr(lc, n, None)) for n in
                                ("compute_report", "build_live_model", "build_replay_model", "end_from_record",
                                 "parse_transcript", "selftest", "exit_code_for")),
        "law": lc.INSTRUMENT_LAW == "lane-scoped",
        "exits": (lc.EXIT_CLEAN, lc.EXIT_VIOLATION, lc.EXIT_VOID, lc.EXIT_USAGE) == (0, 10, 11, 2),
        "key-markers": tuple(lc.DEFAULT_KEY_MARKERS) == KEY_MARKERS,
    }
    bad = sorted(k for k, v in facts.items() if not v)
    return not bad, "subcommands %s%s" % (sorted(subs), "; failed: " + ", ".join(bad) if bad else "")


# ---------------------------------------------------------------- main
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    into = None
    if argv[:1] == ["--into"] and len(argv) == 2:
        into = argv[1]
    elif argv:
        sys.stderr.write(USAGE)
        return 2
    if into is not None:
        base = os.path.realpath(into)
        if _under_tmp(base):                 # Linux: /tmp is a real directory and realpath keeps it
            sys.stderr.write(USAGE)
            return 2
    else:
        base = _default_base()
        if base is None:
            sys.stderr.write(USAGE)
            return 2
    os.makedirs(base, exist_ok=True)
    sys.path.insert(0, HERE)
    import lane_containment as lc  # the adopter's import, from the tree this file lives in

    checks = [
        ("instrument-bytes-pinned", lambda: check_bytes_pinned()),
        ("instrument-selftest", lambda: check_instrument_selftest(base)),
        ("live-clean-pass-d2-covariate", lambda: check_live_inside(lc, base)),
        ("live-record-outside-reads-clean", lambda: check_live_outside(lc, base)),
        ("live-out-of-lane-write-ambiguous", lambda: check_live_out_of_lane(lc, base)),
        ("live-transcript-read-typing", lambda: check_transcript_typing(lc, base)),
        ("note-dir-refuses-roots", lambda: check_note_dir_refuses(lc, base)),
        ("cli-begin-end-round-trip", lambda: check_cli_round_trip(base)),
        ("interface-frozen-names", lambda: check_interface(lc)),
    ]
    failures = 0
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:  # a crash is a failed check, never a silent skip
            ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
        print("%s %s -- %s" % ("PASS" if ok else "FAIL", name, detail))
        failures += 0 if ok else 1
    print("RESULT %s selftest-lane-containment: %d of %d check(s) failed"
          % ("PASS" if not failures else "FAIL", failures, len(checks)))
    if into is None:
        shutil.rmtree(base, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
