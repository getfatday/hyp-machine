#!/usr/bin/env python3
"""doctor-classify: typed OAuth credential-surface classifier (the
environment-health doctor's detection half; stdlib-only, writes NO files —
stdout only). Usage guide: docs/doctor.md in this plugin.

Reads recorded credential-surface streams and emits typed verdicts. This is
the detection instrument counted under H-182-doctor-flap-detection in the
source lab (kept 2026-08-26, two consecutive 5/5); shipped byte-preserving —
only this provenance framing differs from the counted fixture copy.

FROZEN RULE SET (H-182 spec, Method, registered 2026-08-26):
  Vocabulary   CLEAN | FLAP-DEGRADED | HARD-EXPIRED | INDETERMINATE
  Exit codes   CLEAN=0  FLAP-DEGRADED=10  HARD-EXPIRED=11  INDETERMINATE=12
               (usage error=64, unreadable/invalid input=65)
  FLAP-DEGRADED  >= 3 fresh-probe auth failures within 30 minutes (1800 s,
                 inclusive) across >= 2 distinct probe invocations (distinct
                 record timestamps).
  HARD-EXPIRED   expiresAt AND refreshTokenExpiresAt both past (expiresAt > 0,
                 both <= the failing probe's time) with a failing probe.
                 Precedence over FLAP-DEGRADED on the same record.
  CLEAN          expiresAt=0 with passing probes (the documented refresh
                 sentinel — never a defect signal; applies from INDETERMINATE
                 on a passing probe under a known expiresAt=0 surface), and
                 the clear rule: 3 consecutive passing probes -> CLEAN from
                 any state. Entering CLEAN resets the failure window (without
                 the reset, a single post-heal failure would re-alarm off
                 stale pre-clear evidence; calibration note in fixture.lock).
  INDETERMINATE  the initial state, and the honest single-shot verdict when
                 probe evidence is insufficient to type the surface.

RECORD KINDS (field-sniffed, one JSON object per line):
  probe     has boolean "ok" (window-log shape: ok, unix, ts, result_prefix,
            probe_n, ...). Time = "unix" if present, else numeric "ts", else
            ISO-8601 "ts" with offset.
  snapshot  has "expiresAt" and no "ok" (oauth-lifecycle shape: ts,
            expiresAt, refreshExpiresAt|refreshTokenExpiresAt ms epochs,
            keychain_info_rc, keychain_info). Updates the credential surface;
            never transitions state by itself (all three rules require probe
            evidence).
  event     has "event" (gate markers) — skipped, counted.

OUTPUT (deterministic: sort_keys, no wall clock in replay, no randomness):
  one line per classified record:
    {"i": <0-based index among classified records in (ts, read-order) sorted
     merged order>, "kind": "probe"|"snapshot", "ts": <unix s>,
     "verdict": <STATE>, "onset": <STATE-or-null when the verdict changed at
     this record>, "trigger_i": <index of the record that put the machine in
     its current state, or null>}
  final line:
    {"final": <STATE>, "exit_code": <code>, "records": N,
     "skipped_events": M, "onsets": [{"i","state","trigger_i"}...],
     "inputs": [...], "surface_last": {...}}
  Exit status = the typed exit code of the final verdict.

USAGE:
  replay:  doctor-classify.py stream.jsonl [more.jsonl ...]   (or stdin)
  live:    doctor-classify.py --live [--probe-json FILE]
           [--credentials PATH] [--keychain-db PATH] [--now UNIX]
           Read-only: reads the credentials file and (macOS)
           `security show-keychain-info`; classifies the single snapshot
           (+ optional just-run probe record) with the same frozen rules.
"""
import argparse
import json
import os
import subprocess
import sys

FLAP_WINDOW_S = 1800
FLAP_MIN_FAILS = 3
FLAP_MIN_INVOCATIONS = 2
CLEAR_CONSECUTIVE_PASSES = 3

CLEAN = "CLEAN"
FLAP = "FLAP-DEGRADED"
HARD = "HARD-EXPIRED"
INDET = "INDETERMINATE"
EXIT_CODE = {CLEAN: 0, FLAP: 10, HARD: 11, INDET: 12}
EXIT_USAGE = 64
EXIT_INPUT = 65


def _rec_time(rec):
    """Deterministic record time in unix seconds, or None."""
    u = rec.get("unix")
    if isinstance(u, (int, float)):
        return int(u)
    ts = rec.get("ts")
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        import datetime
        try:
            return int(datetime.datetime.strptime(
                ts, "%Y-%m-%dT%H:%M:%S%z").timestamp())
        except ValueError:
            return None
    return None


def _kind(rec):
    if "event" in rec:
        return "event"
    if isinstance(rec.get("ok"), bool):
        return "probe"
    if "expiresAt" in rec:
        return "snapshot"
    return "event"  # unknown rows are skipped, never guessed at


class Engine(object):
    def __init__(self):
        self.state = INDET
        self.trigger_i = None
        self.fail_ts = []          # failing-probe times inside the window
        self.consec_pass = 0
        self.expires_at = None     # ms epoch, 0 = sentinel
        self.refresh_at = None     # ms epoch
        self.keychain_rc = None
        self.keychain_info = None
        self.onsets = []

    def _enter(self, state, i):
        if state != self.state:
            self.state = state
            self.trigger_i = i
            self.onsets.append({"i": i, "state": state, "trigger_i": i})
            if state == CLEAN:
                self.fail_ts = []  # frozen reading: entering CLEAN resets
            return state
        return None

    def _double_expired(self, ts):
        if not isinstance(self.expires_at, (int, float)):
            return False
        if not isinstance(self.refresh_at, (int, float)):
            return False
        if self.expires_at <= 0:   # sentinel, never expiry evidence
            return False
        return (self.expires_at / 1000.0 <= ts
                and self.refresh_at / 1000.0 <= ts)

    def feed(self, i, kind, ts, rec):
        onset = None
        if kind == "snapshot":
            self.expires_at = rec.get("expiresAt")
            self.refresh_at = rec.get(
                "refreshExpiresAt", rec.get("refreshTokenExpiresAt"))
            if "keychain_info_rc" in rec:
                self.keychain_rc = rec.get("keychain_info_rc")
                self.keychain_info = rec.get("keychain_info")
        elif kind == "probe":
            if rec.get("ok") is True:
                self.consec_pass += 1
                if self.consec_pass >= CLEAR_CONSECUTIVE_PASSES:
                    onset = self._enter(CLEAN, i)
                elif (self.state == INDET and self.expires_at == 0):
                    onset = self._enter(CLEAN, i)  # sentinel + passing probe
            else:
                self.consec_pass = 0
                if ts is not None:
                    self.fail_ts.append(ts)
                    self.fail_ts = [t for t in self.fail_ts
                                    if ts - t <= FLAP_WINDOW_S]
                if self._double_expired(ts if ts is not None else 0):
                    onset = self._enter(HARD, i)
                elif (len(self.fail_ts) >= FLAP_MIN_FAILS
                      and len(set(self.fail_ts)) >= FLAP_MIN_INVOCATIONS
                      and self.state != HARD):
                    onset = self._enter(FLAP, i)
        return onset

    def surface(self):
        return {"expiresAt": self.expires_at,
                "refreshExpiresAt": self.refresh_at,
                "keychain_info_rc": self.keychain_rc,
                "keychain_info": self.keychain_info}


def _emit(obj):
    sys.stdout.write(json.dumps(obj, sort_keys=True) + "\n")


def run_stream(records, inputs):
    eng = Engine()
    idx = 0
    skipped = 0
    for kind, ts, rec in records:
        if kind == "event":
            skipped += 1
            continue
        onset = eng.feed(idx, kind, ts, rec)
        _emit({"i": idx, "kind": kind, "ts": ts, "verdict": eng.state,
               "onset": onset, "trigger_i": eng.trigger_i})
        idx += 1
    _emit({"final": eng.state, "exit_code": EXIT_CODE[eng.state],
           "records": idx, "skipped_events": skipped, "onsets": eng.onsets,
           "inputs": inputs, "surface_last": eng.surface()})
    return EXIT_CODE[eng.state]


def load_records(paths):
    """Parse all lines; merged order = stable sort by (ts, read order)."""
    rows = []
    seq = 0
    if not paths:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rows.append((_kind(rec), _rec_time(rec), seq, rec))
            seq += 1
        inputs = ["<stdin>"]
    else:
        inputs = []
        for p in paths:
            ap = os.path.abspath(p)
            inputs.append(ap)
            with open(ap, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    rows.append((_kind(rec), _rec_time(rec), seq, rec))
                    seq += 1
    rows.sort(key=lambda r: (r[1] if r[1] is not None else 0, r[2]))
    return [(k, t, rec) for k, t, _, rec in rows], inputs


def live_snapshot(cred_path, keychain_db, now):
    rec = {"ts": now}
    inputs = []
    try:
        with open(cred_path, encoding="utf-8") as f:
            d = json.load(f)
        o = d.get("claudeAiOauth", {})
        rec["expiresAt"] = o.get("expiresAt")
        rec["refreshExpiresAt"] = o.get("refreshTokenExpiresAt")
        inputs.append("live:" + cred_path)
    except (OSError, ValueError) as e:
        rec["cred_err"] = type(e).__name__
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "show-keychain-info", keychain_db],
                capture_output=True, text=True, timeout=15)
            rec["keychain_info_rc"] = r.returncode
            rec["keychain_info"] = (r.stderr or r.stdout).strip()[:120]
            inputs.append("live:security show-keychain-info")
        except (OSError, subprocess.TimeoutExpired) as e:
            rec["keychain_err"] = type(e).__name__
    return rec, inputs


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("streams", nargs="*",
                    help="JSONL record stream file(s); stdin if none")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--probe-json", default=None,
                    help="live mode: a just-run probe record (JSON file)")
    ap.add_argument("--credentials",
                    default=os.path.expanduser("~/.claude/.credentials.json"))
    ap.add_argument("--keychain-db", default=os.path.expanduser(
        "~/Library/Keychains/login.keychain-db"))
    ap.add_argument("--now", type=int, default=None,
                    help="live mode: override wall clock (testability)")
    o = ap.parse_args()

    if o.live:
        import time
        now = o.now if o.now is not None else int(time.time())
        snap, inputs = live_snapshot(o.credentials, o.keychain_db, now)
        records = [("snapshot", now, snap)]
        if o.probe_json:
            try:
                with open(o.probe_json, encoding="utf-8") as f:
                    probe = json.load(f)
            except (OSError, ValueError) as e:
                _emit({"error": "unreadable --probe-json",
                       "detail": type(e).__name__})
                return EXIT_INPUT
            pts = _rec_time(probe)
            records.append(("probe", pts if pts is not None else now, probe))
            inputs.append(os.path.abspath(o.probe_json))
        return run_stream(records, inputs)

    try:
        records, inputs = load_records(o.streams)
    except (OSError, ValueError) as e:
        _emit({"error": "unreadable input", "detail": type(e).__name__})
        return EXIT_INPUT
    return run_stream(records, inputs)


if __name__ == "__main__":
    sys.exit(main())
