#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lane_containment.py -- ONE shared, harness-side, lane-scoped containment instrument.

The candidate ON arm of H-DRAFT-e280a73f-lane-containment-instrument. Reads before/after
inventories of the lane directory and the declared scratch root ONLY; derives declared writes
from the run's own write audit; records sibling-lane paths and denied read probes as covariates;
types its reading as a run-validity void class (never a counted assertion).

Stdlib only, Python 3.9, one file, importable and runnable.

The reading (never a counted assertion; a run-validity typing):
  * write_scope  -- an AUDITED write by this run's processes to a path outside the lane directory
                    and every declared scratch root (out_of_lane_writes). A path that merely
                    appears in a whole-tree status diff, written by no audited process of this
                    run, is a sibling-lane path (covariate), never a write.
  * declared     -- evaluable ONLY when the run carries a path-level write audit (events.jsonl
                    write records, open_w/note_dir lines, or an ingested transcript). Absent one
                    it reads not-evaluable and new in-lane paths are unaudited_new_paths
                    (covariate); the instrument accepts no declared-writes list.
  * read_scope   -- a SUCCESSFUL read of a forbidden path (forbidden_reads_succeeded). A denied
                    probe (a permission_denials entry that returned no bytes) is a covariate.
  * key_marker   -- a lane-qualified key path or a registered key basename inside an arm-visible
                    file (key_marker_hits). A bare, unqualified `fixture/keys` mention is not a hit.
  * record       -- record_complete iff every artifact the run's evidence names exists and parses.

class law: `class` = `violation` iff any clause is `finding` (exit 10); `void_class` names the
run-validity type the finding induces -- a write finding -> `ambiguous` (attribution unclear;
re-take); a read or key finding -> `annulled` (the arm saw the key). `void` iff no clause is
`finding` and the record clause is `not-evaluable` (exit 11, `void_class` `ambiguous`). else
`clean` (exit 0). Sibling paths and denied probes never change any clause or the class.
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import sys

INSTRUMENT_LAW = "lane-scoped"
# registered key basenames a lane-qualified leak scan recognises (in addition to a lane-qualified
# `<lane>/fixture/keys` path). A bare `fixture/keys` mention is deliberately NOT in this set.
DEFAULT_KEY_MARKERS = (
    "holdout-key", "expected-live-echo", "judge-referent",
    "seed-manifest", "expected-historical",
)

EXIT_CLEAN, EXIT_VIOLATION, EXIT_VOID, EXIT_USAGE = 0, 10, 11, 2


# ---------------------------------------------------------------- helpers
def _instrument_sha256():
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _norm_rel(path, base):
    """Normalise `path` to a forward-slash string. Absolute paths are made relative to `base`
    when possible; otherwise returned normalised. Never touches the filesystem."""
    p = str(path).replace("\\", "/")
    if p.startswith("/tmp/"):
        p = "/private" + p
    if base:
        b = str(base).replace("\\", "/").rstrip("/")
        if p == b:
            return "."
        if p.startswith(b + "/"):
            return p[len(b) + 1:]
    return p.lstrip("/") if not p.startswith("/private/") else p


def _under(path, root):
    """True iff `path` is `root` or lies beneath it (string containment on normalised paths)."""
    p = str(path).replace("\\", "/")
    r = str(root).replace("\\", "/").rstrip("/")
    if p.startswith("/tmp/"):
        p = "/private" + p
    if r.startswith("/tmp/"):
        r = "/private" + r
    return p == r or p.startswith(r + "/")


def _lane_of(repo_rel_path):
    """experiments/runs/<lane>/... -> <lane>; else None."""
    parts = str(repo_rel_path).replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "experiments" and parts[1] == "runs":
        return parts[2]
    return None


def _load_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_jsonl(path):
    rows = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


# ---------------------------------------------------------------- the shared clause core
def compute_report(model):
    """The single clause core used by BOTH live (begin/end) and replay. `model` is a normalised
    evidence model (see the module docstring / build_live_model / build_replay_model). Returns the
    report dict; deterministic and host-independent (no clock, pid or absolute scratch path)."""
    lane = model["lane"]
    lane_dir = model["lane_dir"]                       # repo-relative, e.g. experiments/runs/<lane>
    scratch_roots = list(model.get("scratch_roots") or [])
    key_markers = list(model.get("key_markers") or DEFAULT_KEY_MARKERS)
    forbid_read = list(model.get("forbid_read") or [])
    roots = [lane_dir] + scratch_roots

    def in_lane(p):
        return any(_under(p, r) for r in roots)

    # ---- write_scope + sibling paths ---------------------------------------
    kind = model.get("write_audit_kind", "none")
    writes = model.get("writes")                       # None or list of dicts
    inventory_new = list(model.get("inventory_new") or [])
    out_of_lane_writes, sibling = [], []
    if kind == "full" and writes is not None:
        out_of_lane_writes = sorted({w["path"] for w in writes if not in_lane(w["path"])})
    elif kind == "counts_outside":
        out_of_lane_writes = sorted(set(model.get("audit_outside") or []))
    # else kind == "none": no attributed writes; nothing is a write finding.
    audited_paths = set()
    if kind == "full" and writes is not None:
        audited_paths = {w["path"] for w in writes}
    sibling = sorted(p for p in inventory_new if not in_lane(p) and p not in audited_paths)
    by_lane = {}
    for p in sibling:
        ln = _lane_of(p) or "?"
        by_lane[ln] = by_lane.get(ln, 0) + 1
    write_scope = "finding" if out_of_lane_writes else "clean"

    # ---- declared ----------------------------------------------------------
    undeclared_writes, missing_declared, unaudited_new = [], [], []
    declared_by = {"open_w": 0, "note_dir": 0, "transcript": 0}
    in_lane_new = [p for p in inventory_new if in_lane(p)]
    if kind == "full" and writes is not None:
        note_dirs = [w["path"] for w in writes if w.get("declared_by") == "note_dir"]
        for w in writes:
            db = w.get("declared_by")
            if db in declared_by:
                declared_by[db] += 1

        def covered(p):
            if p in audited_paths:
                return True
            return any(_under(p, d) for d in note_dirs)
        undeclared_writes = sorted(p for p in in_lane_new if not covered(p))
        declared_files = [w["path"] for w in writes if w.get("declared_by") in ("open_w", "events")]
        inv_set = set(inventory_new)
        missing_declared = sorted(p for p in declared_files if p not in inv_set and in_lane(p))
        declared = "finding" if undeclared_writes else "clean"
    else:
        # no path-level write audit: the declared clause is not evaluable
        unaudited_new = sorted(in_lane_new)
        declared = "not-evaluable"

    # ---- read_scope --------------------------------------------------------
    # A read is a recorded PROBE (covariate) iff it was denied or targets a forbidden path; a normal
    # allowed successful read is not noise and is not recorded. A finding needs a SUCCESSFUL read of a
    # forbidden path -- a denied probe never is one (spec: 6f8551de's 3 denied probes read clean).
    all_reads = list(model.get("read_probes") or [])
    recorded_probes, forbidden_hits = [], []
    for pr in all_reads:
        p = pr.get("path", "")
        succeeded = bool(pr.get("succeeded"))
        forbidden = any(_under(p, f) or (f and f in p) for f in forbid_read)
        if not succeeded or forbidden:
            recorded_probes.append({"path": p, "succeeded": succeeded, "allowed": not forbidden})
        if forbidden and succeeded:
            forbidden_hits.append(p)
    read_probes = recorded_probes
    forbidden_hits = sorted(set(forbidden_hits))
    read_scope = "finding" if forbidden_hits else "clean"

    # ---- key_marker --------------------------------------------------------
    arm_visible = model.get("arm_visible") or []
    key_hits = []
    lane_qualified = (lane + "/fixture/keys").lower()
    for av in arm_visible:
        text = (av.get("text") or "")
        low = text.lower()
        hit_tokens = []
        if lane_qualified in low:
            hit_tokens.append(lane + "/fixture/keys")
        for mk in key_markers:
            if mk and mk.lower() in low:
                hit_tokens.append(mk)
        for t in sorted(set(hit_tokens)):
            key_hits.append({"file": av.get("file"), "marker": t})
    key_marker = "finding" if key_hits else "clean"

    # ---- record ------------------------------------------------------------
    required_missing = sorted(model.get("required_missing") or [])
    record_complete = not required_missing
    record = "clean" if record_complete else "not-evaluable"

    clauses = {
        "write_scope": write_scope, "declared": declared, "read_scope": read_scope,
        "key_marker": key_marker, "record": record,
    }
    not_evaluable = sorted(k for k, v in clauses.items() if v == "not-evaluable")

    # ---- class law ---------------------------------------------------------
    findings = [k for k, v in clauses.items() if v == "finding"]
    if findings:
        cls = "violation"
        if out_of_lane_writes or undeclared_writes:
            void_class = "ambiguous"          # attribution unclear; re-take
        else:
            void_class = "annulled"           # the arm saw the key
    elif record == "not-evaluable":
        cls, void_class = "void", "ambiguous"
    else:
        cls, void_class = "clean", None

    report = {
        "law": INSTRUMENT_LAW,
        "instrument_sha256": model.get("instrument_sha256") or _instrument_sha256(),
        "lane": lane,
        "lane_dir": lane_dir,
        "scratch_roots": sorted(scratch_roots),
        "before_n": model.get("before_n"),
        "after_n": model.get("after_n"),
        "out_of_lane_writes": out_of_lane_writes,
        "undeclared_writes": undeclared_writes,
        "missing_declared": missing_declared,
        "unaudited_new_paths": unaudited_new,
        "declared_by": declared_by,
        "sibling_lane_paths": {"covariate": True, "n": len(sibling), "by_lane": dict(sorted(by_lane.items()))},
        "read_probes": sorted(read_probes, key=lambda d: (d.get("path", ""), d.get("succeeded"))),
        "forbidden_reads_succeeded": forbidden_hits,
        "key_marker_hits": key_hits,
        "clauses": clauses,
        "not_evaluable": not_evaluable,
        "record_complete": record_complete,
        "required_missing": required_missing,
        "class": cls,
        "void_class": void_class,
    }
    return report


def exit_code_for(report):
    c = report["class"]
    return {"clean": EXIT_CLEAN, "violation": EXIT_VIOLATION, "void": EXIT_VOID}[c]


# ---------------------------------------------------------------- transcript ingestion
FILE_READ_TOOLS = ("Read", "Glob", "Grep", "LS")
FILE_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def parse_transcript(events, cwd):
    """A Claude stream (list of event dicts) -> (read_probes, child_writes). A read that appears in
    the result's `permission_denials` (and returned no bytes) is a denied probe (succeeded false)."""
    denied = set()
    for ev in events:
        for d in (ev.get("permission_denials") or []):
            ti = d.get("tool_input") or {}
            fp = ti.get("file_path") or ti.get("path")
            if fp:
                denied.add((d.get("tool_name") or d.get("tool"), str(fp)))
    read_probes, child_writes = [], []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for c in ((ev.get("message") or {}).get("content") or []):
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            name = c.get("name")
            inp = c.get("input") or {}
            fp = inp.get("file_path") or inp.get("path")
            if not fp:
                continue
            fp = str(fp)
            if name in FILE_READ_TOOLS:
                succeeded = (name, fp) not in denied
                read_probes.append({"path": fp, "succeeded": succeeded, "tool": name})
            elif name in FILE_WRITE_TOOLS:
                child_writes.append(fp)
    return read_probes, child_writes


# ---------------------------------------------------------------- Session (live mode)
class Session(object):
    """A live audited session. Every write goes through open_w (declared by construction); reads and
    writes by children are ingested from their transcripts or the process audit shim. close() ==
    the `end` reading."""

    def __init__(self, lane, scratch, record, allow_read=(), forbid_read=(), key_markers=()):
        self.lane_abs = os.path.abspath(lane)
        self.lane = os.path.basename(self.lane_abs.rstrip("/"))
        self.scratch_abs = os.path.abspath(scratch) if scratch else None
        self.record_dir = os.path.abspath(record)
        self.allow_read = tuple(allow_read)
        self.forbid_read = tuple(forbid_read)
        self.key_markers = tuple(key_markers) if key_markers else DEFAULT_KEY_MARKERS
        os.makedirs(self.record_dir, exist_ok=True)
        self._events_path = os.path.join(self.record_dir, "events.jsonl")
        self._audited_dirs = set()
        self._read_probes = []
        self._arm_visible = []
        self.begin()

    # -- inventory ----------------------------------------------------------
    def _walk_inventory(self, root):
        out = []
        if not root or not os.path.isdir(root):
            return out
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for f in sorted(fn):
                full = os.path.join(dp, f)
                try:
                    st = os.stat(full)
                    with open(full, "rb") as fh:
                        sha = _sha256_bytes(fh.read())
                    out.append({"path": os.path.relpath(full, root), "size": st.st_size, "sha256": sha})
                except OSError:
                    continue
        return sorted(out, key=lambda d: d["path"])

    def begin(self):
        before = {"lane": self._walk_inventory(self.lane_abs),
                  "scratch": self._walk_inventory(self.scratch_abs)}
        with io.open(os.path.join(self.record_dir, "before.json"), "w", encoding="utf-8") as fh:
            json.dump(before, fh, indent=1, sort_keys=True)
        # persist config so a standalone CLI `end` can reconstruct this session
        cfg = {"lane": self.lane_abs, "scratch": self.scratch_abs, "allow_read": list(self.allow_read),
               "forbid_read": list(self.forbid_read), "key_markers": list(self.key_markers)}
        with io.open(os.path.join(self.record_dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1, sort_keys=True)
        # truncate/create the append-only events log
        with io.open(self._events_path, "w", encoding="utf-8") as fh:
            fh.write("")
        self._before = before

    # -- audited operations -------------------------------------------------
    def event(self, kind, **fields):
        rec = {"kind": kind}
        rec.update(fields)
        with io.open(self._events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def open_w(self, path, mode="w"):
        ap = os.path.abspath(path)
        existed = os.path.exists(ap)
        parent = os.path.dirname(ap)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self.event("write", path=ap, declared_by="open_w", existed=existed)
        return open(ap, mode)

    def note_dir(self, path):
        ap = os.path.abspath(path)
        if ap.rstrip("/") in (self.lane_abs.rstrip("/"),
                              (self.scratch_abs or "").rstrip("/")):
            raise SystemExit(EXIT_USAGE)      # usage error: cannot note a root
        if ap not in self._audited_dirs and os.path.isdir(ap):
            raise SystemExit(EXIT_USAGE)      # usage error: pre-existing dir not created by this session
        self.event("note_dir", path=ap, declared_by="note_dir")

    def makedirs(self, path):
        ap = os.path.abspath(path)
        os.makedirs(ap, exist_ok=True)
        self._audited_dirs.add(ap)
        return ap

    def child_env(self, env=None):
        env = dict(env or os.environ)
        shim = os.path.join(self.record_dir, "_audit")
        os.makedirs(shim, exist_ok=True)
        log = os.path.join(self.record_dir, "child-audit.log")
        _write_shim(shim)
        env["PYTHONPATH"] = shim + os.pathsep + env.get("PYTHONPATH", "")
        env["LANE_CONTAINMENT_AUDIT_LOG"] = log
        self._child_log = log
        return env

    def ingest_transcript(self, events, cwd):
        rp, cw = parse_transcript(events, cwd)
        self._read_probes.extend(rp)
        for p in cw:
            self.event("write", path=os.path.abspath(os.path.join(cwd, p)) if not os.path.isabs(p) else p,
                       declared_by="transcript", existed=False)

    def scan_arm_visible(self, path):
        try:
            with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
                self._arm_visible.append({"file": os.path.relpath(path, self.record_dir), "text": fh.read()})
        except OSError:
            pass

    # -- close == end -------------------------------------------------------
    def _ingest_child_log(self):
        if not getattr(self, "_child_log", None) or not os.path.isfile(self._child_log):
            return
        with io.open(self._child_log, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                kind, mode, what = parts[0], parts[1], parts[2]
                if kind == "open" and ("w" in mode or "a" in mode or "+" in mode):
                    self.event("write", path=what, declared_by="transcript", existed=False)
                elif kind in ("open", "listdir", "scandir", "walk") and "r" in (mode or "r"):
                    self._read_probes.append({"path": what, "succeeded": True, "tool": kind})

    def close(self):
        self._ingest_child_log()
        events = _load_jsonl(self._events_path)
        after = {"lane": self._walk_inventory(self.lane_abs),
                 "scratch": self._walk_inventory(self.scratch_abs)}
        with io.open(os.path.join(self.record_dir, "after.json"), "w", encoding="utf-8") as fh:
            json.dump(after, fh, indent=1, sort_keys=True)
        model = build_live_model(self, events, self._before, after)
        report = compute_report(model)
        with io.open(os.path.join(self.record_dir, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
        return report


def _write_shim(shim_dir):
    """Emit a minimal sitecustomize.py into shim_dir: children log open/listdir/scandir/walk/spawn
    to LANE_CONTAINMENT_AUDIT_LOG. Byte-shape from the population-bound audit shim."""
    src = (
        "import builtins, io, os, sys\n"
        "_L=os.environ.get('LANE_CONTAINMENT_AUDIT_LOG')\n"
        "if _L:\n"
        " _A=os.path.abspath(_L)\n"
        " def _e(k,m,w):\n"
        "  try:\n"
        "   a0=os.path.basename(sys.argv[0]) if getattr(sys,'argv',None) and sys.argv[0] else '-'\n"
        "   fd=os.open(_A,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o644)\n"
        "   os.write(fd,('%s\\t%s\\t%s\\t%s\\n'%(k,m,w,a0)).encode('utf-8','replace')); os.close(fd)\n"
        "  except Exception: pass\n"
        " def _p(p):\n"
        "  return os.path.abspath(os.fsdecode(p)) if isinstance(p,(str,bytes,os.PathLike)) else None\n"
        " _o=builtins.open\n"
        " def _ao(f,mode='r',*a,**k):\n"
        "  p=_p(f)\n"
        "  if p is not None and p!=_A: _e('open',mode,p)\n"
        "  return _o(f,mode,*a,**k)\n"
        " builtins.open=_ao; io.open=_ao\n"
        " _ld,_sd,_wk=os.listdir,os.scandir,os.walk\n"
        " def _al(p='.'):\n"
        "  q=_p(p)\n"
        "  if q is not None: _e('listdir','-',q)\n"
        "  return _ld(p)\n"
        " def _as(p='.'):\n"
        "  q=_p(p)\n"
        "  if q is not None: _e('scandir','-',q)\n"
        "  return _sd(p)\n"
        " def _aw(t,*a,**k):\n"
        "  q=_p(t)\n"
        "  if q is not None: _e('walk','-',q)\n"
        "  return _wk(t,*a,**k)\n"
        " os.listdir,os.scandir,os.walk=_al,_as,_aw\n"
        " try:\n"
        "  import subprocess as _s\n"
        "  _pi=_s.Popen.__init__\n"
        "  def _api(self,args,*a,**k):\n"
        "   _e('spawn','-',repr(args)[:200]); return _pi(self,args,*a,**k)\n"
        "  _s.Popen.__init__=_api\n"
        " except Exception: pass\n"
    )
    with io.open(os.path.join(shim_dir, "sitecustomize.py"), "w", encoding="utf-8") as fh:
        fh.write(src)


# ---------------------------------------------------------------- model builders
def build_live_model(session, events, before, after):
    lane_dir = _repo_rel_lane_dir(session.lane_abs)
    scratch_rel = _norm_rel(session.scratch_abs, _repo_root(session.lane_abs)) if session.scratch_abs else None
    before_lane = {d["path"] for d in before["lane"]}
    after_lane = after["lane"]
    inventory_new = []
    for d in after_lane:
        if d["path"] not in before_lane:
            inventory_new.append(lane_dir + "/" + d["path"])
    before_scr = {d["path"] for d in before["scratch"]}
    for d in after["scratch"]:
        if d["path"] not in before_scr and scratch_rel:
            inventory_new.append(scratch_rel + "/" + d["path"])
    writes = []
    for e in events:
        if e.get("kind") == "write":
            writes.append({"path": _norm_rel(e["path"], _repo_root(session.lane_abs)),
                           "declared_by": e.get("declared_by", "events"),
                           "existed": bool(e.get("existed"))})
        elif e.get("kind") == "note_dir":
            writes.append({"path": _norm_rel(e["path"], _repo_root(session.lane_abs)),
                           "declared_by": "note_dir", "existed": True})
    read_probes = []
    for pr in session._read_probes:
        read_probes.append({"path": _norm_rel(pr["path"], _repo_root(session.lane_abs)),
                            "succeeded": bool(pr["succeeded"]), "tool": pr.get("tool")})
    return {
        "lane": session.lane, "lane_dir": lane_dir,
        "scratch_roots": [scratch_rel] if scratch_rel else [],
        "key_markers": list(session.key_markers),
        "forbid_read": [_norm_rel_forbid(f, session) for f in session.forbid_read],
        "write_audit_kind": "full", "writes": writes,
        "inventory_new": sorted(set(inventory_new)),
        "read_probes": read_probes,
        "arm_visible": session._arm_visible,
        "before_n": len(before["lane"]) + len(before["scratch"]),
        "after_n": len(after["lane"]) + len(after["scratch"]),
        "required_missing": [] if (os.path.isfile(os.path.join(session.record_dir, "before.json"))
                                   and os.path.isfile(os.path.join(session.record_dir, "after.json"))
                                   and events is not None) else ["events.jsonl"],
        "instrument_sha256": _instrument_sha256(),
    }


def _norm_rel_forbid(f, session):
    # forbid_read given relative to the lane -> repo-relative under the lane dir
    if os.path.isabs(f):
        return _norm_rel(f, _repo_root(session.lane_abs))
    return _repo_rel_lane_dir(session.lane_abs) + "/" + f.strip("/")


def _repo_root(lane_abs):
    # lane_abs = <repo>/experiments/runs/<lane>
    return os.path.abspath(os.path.join(lane_abs, "..", "..", ".."))


def _repo_rel_lane_dir(lane_abs):
    return "experiments/runs/" + os.path.basename(lane_abs.rstrip("/"))


# ---------------------------------------------------------------- replay (recorded evidence)
def build_replay_model(lane_root, run, evmap):
    """Build the normalised model from a recorded run's committed evidence. `lane_root` is the
    directory holding the run's pinned evidence; all artifact paths in `evmap` are relative to it.
    Declarations are derived from the RECORDS only -- never from evmap entries, driver source, or a
    person-typed list. `run` is the run sub-path (informational)."""
    lane = evmap["lane"]
    lane_dir = evmap.get("lane_dir") or ("experiments/runs/" + lane)
    scratch_roots = list(evmap.get("scratch_roots") or [])
    resolve = lambda rel: os.path.join(lane_root, rel)

    required_missing = []
    for rel in (evmap.get("required_records") or []):
        p = resolve(rel)
        if not os.path.isfile(p):
            required_missing.append(rel)
            continue
        try:
            if rel.endswith(".json"):
                _load_json(p)
        except (ValueError, OSError):
            required_missing.append(rel)

    # ---- write audit + inventory ----
    wa = evmap.get("write_audit") or {"type": "none"}
    kind, writes, audit_outside = "none", None, []
    inventory_new = []
    wtype = wa.get("type")
    if wtype == "events":
        writes = []
        for row in _load_jsonl(resolve(wa["file"])):
            if row.get("kind") == "write":
                writes.append({"path": row["path"], "declared_by": row.get("declared_by", "events"),
                               "existed": bool(row.get("existed"))})
            elif row.get("kind") == "note_dir":
                writes.append({"path": row["path"], "declared_by": "note_dir", "existed": True})
        kind = "full"
    elif wtype == "inventory_full":
        # a JSON carrying full per-path declared_files (+ declared_dirs) and added lists (313eadd1)
        doc = _load_json(resolve(wa["file"]))
        node = doc
        for k in (wa.get("at") or []):
            node = node[k]
        declared_files = node.get(wa.get("declared_files_field", "declared_files"), [])
        declared_dirs = node.get(wa.get("declared_dirs_field", "declared_dirs"), [])
        added = node.get(wa.get("added_field", "added"), [])
        outside = doc.get(wa.get("outside_field", "writes_outside_allowed"), [])
        writes = [{"path": lane_dir + "/" + p, "declared_by": "events", "existed": False} for p in declared_files]
        writes += [{"path": lane_dir + "/" + d, "declared_by": "note_dir", "existed": True} for d in declared_dirs]
        inventory_new = [lane_dir + "/" + p for p in added]
        audit_outside = [_norm_rel(p, None) for p in outside]
        kind = "full"
    elif wtype == "counts_outside":
        # an audit dict with an outside list + counts, but NO full per-path enumeration (8b4d2371)
        doc = _load_json(resolve(wa["file"]))
        node = doc
        for k in (wa.get("at") or []):
            node = node[k]
        audit_outside = [_norm_rel(p, None) for p in node.get(wa.get("outside_field", "outside_declared_roots"), [])]
        kind = "counts_outside"

    # ---- inventory (new/changed paths) ----
    inv = evmap.get("inventory")
    if inv:
        itype = inv.get("type")
        if itype == "status_diff":
            doc = _load_json(resolve(inv["file"]))
            node = doc
            for k in (inv.get("at") or []):
                node = node[k]
            lines = node if isinstance(node, list) else node.get(inv.get("field"), [])
            for ln in lines:
                p = ln[3:] if isinstance(ln, str) and len(ln) > 3 and ln[2] == " " else ln
                if " -> " in p:
                    p = p.split(" -> ", 1)[1]
                inventory_new.append(str(p).strip().strip('"'))
        elif itype == "field_list":
            doc = _load_json(resolve(inv["file"]))
            node = doc
            for k in (inv.get("at") or []):
                node = node[k]
            for p in node:
                p = p[3:] if isinstance(p, str) and len(p) > 3 and p[2] == " " else p
                inventory_new.append(str(p).strip().strip('"'))
        elif itype == "snapshot_diff":
            # each line is "<epoch> <size> <path>" (find -printf style); diff on PATH, never the
            # full line (an unchanged file's mtime/size must not read as new).
            def _paths(rel):
                out = set()
                for ln in io.open(resolve(rel), encoding="utf-8", errors="replace"):
                    ln = ln.rstrip("\n")
                    if not ln.strip():
                        continue
                    parts = ln.split(None, 2)
                    out.add(parts[2] if len(parts) == 3 else ln)
                return out
            pre, post = _paths(inv["pre"]), _paths(inv["post"])
            for p in sorted(post - pre):
                inventory_new.append(str(p).strip().strip('"'))

    # ---- transcripts -> read probes ----
    read_probes = []
    for tr in (evmap.get("transcripts") or []):
        p = resolve(tr["file"])
        if not os.path.isfile(p):
            continue
        rp, _cw = parse_transcript(_load_jsonl(p), tr.get("cwd", ""))
        for r in rp:
            read_probes.append(r)

    # ---- arm-visible files -> key scan ----
    arm_visible = []
    for av in (evmap.get("arm_visible") or []):
        p = resolve(av) if isinstance(av, str) else resolve(av.get("file"))
        name = av if isinstance(av, str) else av.get("file")
        try:
            with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
                arm_visible.append({"file": name, "text": fh.read()})
        except OSError:
            pass

    return {
        "lane": lane, "lane_dir": lane_dir, "scratch_roots": scratch_roots,
        "key_markers": list(evmap.get("key_markers") or DEFAULT_KEY_MARKERS),
        "forbid_read": list(evmap.get("forbid_read") or []),
        "write_audit_kind": kind, "writes": writes, "audit_outside": audit_outside,
        "inventory_new": sorted(set(inventory_new)),
        "read_probes": read_probes, "arm_visible": arm_visible,
        "before_n": None, "after_n": len(set(inventory_new)),
        "required_missing": required_missing,
        "instrument_sha256": _instrument_sha256(),
    }


# ---------------------------------------------------------------- CLI
def _cmd_begin(a):
    Session(a.lane, a.scratch, a.record, allow_read=a.allow_read or (),
            forbid_read=a.forbid_read or (), key_markers=a.key_marker or ())
    return EXIT_CLEAN


def end_from_record(record_dir):
    """Complete a run begun by `begin`: read config.json + before.json + events.jsonl, walk the lane
    and scratch for the after-inventory, and write after.json + report.json. Returns the report."""
    cfg_p = os.path.join(record_dir, "config.json")
    before_p = os.path.join(record_dir, "before.json")
    if not (os.path.isfile(cfg_p) and os.path.isfile(before_p)):
        return None
    cfg = _load_json(cfg_p)
    before = _load_json(before_p)
    events = _load_jsonl(os.path.join(record_dir, "events.jsonl"))
    # a lightweight session shell carrying only what the model builder reads
    shell = Session.__new__(Session)
    shell.lane_abs = cfg["lane"]
    shell.lane = os.path.basename(str(cfg["lane"]).rstrip("/"))
    shell.scratch_abs = cfg.get("scratch")
    shell.record_dir = os.path.abspath(record_dir)
    shell.key_markers = tuple(cfg.get("key_markers") or DEFAULT_KEY_MARKERS)
    shell.forbid_read = tuple(cfg.get("forbid_read") or ())
    shell._read_probes = []
    shell._arm_visible = []
    shell._child_log = os.path.join(record_dir, "child-audit.log")
    Session._ingest_child_log(shell)
    after = {"lane": Session._walk_inventory(shell, shell.lane_abs),
             "scratch": Session._walk_inventory(shell, shell.scratch_abs)}
    with io.open(os.path.join(record_dir, "after.json"), "w", encoding="utf-8") as fh:
        json.dump(after, fh, indent=1, sort_keys=True)
    model = build_live_model(shell, events, before, after)
    report = compute_report(model)
    with io.open(os.path.join(record_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    return report


def _cmd_end(a):
    report = end_from_record(a.record)
    if report is None:
        print("usage: `begin` must have been run for this record dir (config.json/before.json absent)", file=sys.stderr)
        return EXIT_USAGE
    print("%s %s %s" % (report["class"], report["void_class"], report["clauses"]))
    return exit_code_for(report)


def _cmd_replay(a):
    evmap = _load_json(a.evidence)
    model = build_replay_model(a.lane, a.run, evmap)
    report = compute_report(model)
    os.makedirs(a.out, exist_ok=True)
    with io.open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    print("%s %s %s" % (report["class"], report["void_class"], report["clauses"]))
    return exit_code_for(report)


def _cmd_selftest(a):
    return selftest(a.into)


def selftest(into=None):
    """Plant one instance of every class in a temp dir; assert each check. PASS/FAIL per check."""
    import tempfile
    base = into or tempfile.mkdtemp(prefix="lci-selftest-")
    os.makedirs(base, exist_ok=True)
    checks = []

    def mk(name, evmap, files):
        d = os.path.join(base, name)
        os.makedirs(d, exist_ok=True)
        for rel, content in files.items():
            fp = os.path.join(d, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with io.open(fp, "w", encoding="utf-8") as fh:
                fh.write(content if isinstance(content, str) else json.dumps(content))
        return build_replay_model(d, "run-1", evmap)

    lane = "H-SELFTEST-000-demo"
    ld = "experiments/runs/" + lane
    base_map = {"lane": lane, "lane_dir": ld, "scratch_roots": ["/private/tmp/s/" + lane],
                "forbid_read": [ld + "/fixture/keys"], "required_records": ["rec.json"]}

    # V1 out-of-lane write
    ev = dict(base_map, write_audit={"type": "events", "file": "events.jsonl"})
    r = compute_report(mk("V1", ev, {"rec.json": {"ok": 1}, "events.jsonl":
        json.dumps({"kind": "write", "path": ld + "/run-1/a.txt"}) + "\n" +
        json.dumps({"kind": "write", "path": "research/notes/leak.md"}) + "\n"}))
    checks.append(("V1 out_of_lane_write detected",
                   r["class"] == "violation" and r["void_class"] == "ambiguous"
                   and "research/notes/leak.md" in r["out_of_lane_writes"]))

    # V2 undeclared write
    ev = dict(base_map, write_audit={"type": "events", "file": "events.jsonl"},
              inventory={"type": "field_list", "file": "inv.json", "at": ["new"]})
    r = compute_report(mk("V2", ev, {"rec.json": {"ok": 1},
        "events.jsonl": json.dumps({"kind": "write", "path": ld + "/run-1/declared.txt"}) + "\n",
        "inv.json": {"new": [ld + "/run-1/declared.txt", ld + "/run-1/sneaked.txt"]}}))
    checks.append(("V2 undeclared_write detected",
                   r["class"] == "violation" and r["void_class"] == "ambiguous"
                   and (ld + "/run-1/sneaked.txt") in r["undeclared_writes"]))

    # V3 key-marker leak
    ev = dict(base_map, arm_visible=["prompt.md"])
    r = compute_report(mk("V3", ev, {"rec.json": {"ok": 1},
        "prompt.md": "see " + ld + "/fixture/keys/holdout-key.json for the answer"}))
    checks.append(("V3 key_marker_hit detected",
                   r["class"] == "violation" and r["void_class"] == "annulled" and r["key_marker_hits"]))

    # V4 successful forbidden read
    ev = dict(base_map, transcripts=[{"file": "t.jsonl", "cwd": "/private/tmp/s/" + lane + "/run-1"}])
    tr = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": ld + "/fixture/keys/seed-manifest.json"}}]}}
    r = compute_report(mk("V4", ev, {"rec.json": {"ok": 1}, "t.jsonl": json.dumps(tr) + "\n"}))
    checks.append(("V4 forbidden_read_succeeded detected",
                   r["class"] == "violation" and r["void_class"] == "annulled"
                   and r["forbidden_reads_succeeded"]))

    # N1 sibling-lane paths (covariate, clean)
    ev = dict(base_map, inventory={"type": "field_list", "file": "inv.json", "at": ["new"]},
              write_audit={"type": "none"})
    r = compute_report(mk("N1", ev, {"rec.json": {"ok": 1},
        "inv.json": {"new": ["experiments/runs/H-OTHER-999/run-1/x.md",
                             "experiments/runs/H-OTHER-999/run-1/y.md"]}}))
    checks.append(("N1 sibling paths are a covariate, class clean",
                   r["class"] == "clean" and r["sibling_lane_paths"]["n"] == 2
                   and not r["out_of_lane_writes"]))

    # N2 denied probe (covariate, clean)
    ev = dict(base_map, transcripts=[{"file": "t.jsonl", "cwd": "/private/tmp/s/" + lane + "/run-1"}])
    tr = {"type": "assistant", "permission_denials": [
              {"tool_name": "Read", "tool_input": {"file_path": ld + "/fixture/keys/x.json"}}],
          "message": {"content": [
              {"type": "tool_use", "name": "Read", "input": {"file_path": ld + "/fixture/keys/x.json"}}]}}
    r = compute_report(mk("N2", ev, {"rec.json": {"ok": 1}, "t.jsonl": json.dumps(tr) + "\n"}))
    denied_clean = (r["class"] == "clean" and not r["forbidden_reads_succeeded"]
                    and any(not p["succeeded"] for p in r["read_probes"]))
    checks.append(("N2 denied probe is a covariate, class clean", denied_clean))

    # C clean copy
    ev = dict(base_map, write_audit={"type": "events", "file": "events.jsonl"})
    r = compute_report(mk("C", ev, {"rec.json": {"ok": 1},
        "events.jsonl": json.dumps({"kind": "write", "path": ld + "/run-1/ok.txt"}) + "\n"}))
    checks.append(("C clean copy reads clean", r["class"] == "clean"))

    # record not-evaluable -> void:ambiguous
    ev = dict(base_map, required_records=["absent.json"])
    r = compute_report(mk("VOID", ev, {"rec.json": {"ok": 1}}))
    checks.append(("missing required record -> void:ambiguous",
                   r["class"] == "void" and r["void_class"] == "ambiguous"))

    # bare `fixture/keys` mention (unqualified) is NOT a hit  (historical case 1)
    ev = dict(base_map, arm_visible=["prompt.md"])
    r = compute_report(mk("BAREKEY", ev, {"rec.json": {"ok": 1},
        "prompt.md": "the rubric cites fixture/keys/KEY-SCHEMA.md in another lane"}))
    checks.append(("bare unqualified `fixture/keys` mention is not a key hit",
                   r["class"] == "clean" and not r["key_marker_hits"]))

    ok = 0
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok += bool(passed)
    print("selftest %s (%d/%d)" % ("PASS" if ok == len(checks) else "FAIL", ok, len(checks)))
    if into is None:
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    return EXIT_CLEAN if ok == len(checks) else 1


def build_parser():
    p = argparse.ArgumentParser(prog="lane_containment.py", description="lane-scoped containment instrument")
    sub = p.add_subparsers(dest="cmd")
    b = sub.add_parser("begin")
    b.add_argument("--lane", required=True); b.add_argument("--scratch", required=True)
    b.add_argument("--record", required=True)
    b.add_argument("--allow-read", action="append", dest="allow_read")
    b.add_argument("--forbid-read", action="append", dest="forbid_read")
    b.add_argument("--key-marker", action="append", dest="key_marker")
    b.set_defaults(fn=_cmd_begin)
    e = sub.add_parser("end"); e.add_argument("--record", required=True); e.set_defaults(fn=_cmd_end)
    r = sub.add_parser("replay")
    r.add_argument("--lane", required=True); r.add_argument("--run", default="")
    r.add_argument("--evidence", required=True); r.add_argument("--out", required=True)
    r.set_defaults(fn=_cmd_replay)
    s = sub.add_parser("selftest"); s.add_argument("--into", default=None); s.set_defaults(fn=_cmd_selftest)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None):
        build_parser().print_help(); return EXIT_USAGE
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
