#!/usr/bin/env python3
"""Routing ledger (hyp): one agent-route/v1 row per finished workflow agent (source lab
H-DRAFT-38f86fad-routing-ledger-row, VERDICT.json evidence-sufficient promote).

Stop row (synchronous -- async hooks lose their tail at exit) and SubagentStop row (never
measured against a real Claude Code session in the source lane's build; ships as a
disclosed no-op-safe call, see docs/model-routing.md). Reads the session's own run
directories under `<project dir>/<session id>/subagents/workflows/wf_*` (derived from the
payload's `transcript_path`) in the shape Claude Code writes them (routing_lib's agent-
route/v1 section docstring): the `journal.jsonl` records (`started` / `result` / `failed`),
every `agent-<id>.meta.json` (the agent id is the file name; `model` when the call declared
one), and the assistant lines' `message.model` / `message.usage` / `timestamp` of each
`agent-<id>.jsonl` under the 8 MB head+tail disk rule -- never a transcript body. Appends
exactly one row per agent with a `result` record not already on the worktree's
`ledger/routing-ledger.jsonl`, keyed `(wf, agent)`; `failed` and still-running agents are
counted in the summary line and never given a row (never imputed). Never raises: any
unexpected error is swallowed and the hook exits 0 (hyp_config's fail-open contract for a
hook that observes but never gates). No prompt or tool-input text is ever read or written
(redaction-safe by construction: only journal/meta/transcript METADATA fields listed above
are read, and only routing_lib.build_row's fixed key set is written).

Experiment-level join (the lane-driver opt-in): a lane driver that wants its workflow's rows
joined to its run writes `<root>/.claude/routing-outcomes/<wf>.json`
`{"lane", "run", "outcome_ref"}` in the checkout it runs in; the writer copies the pointer
onto every row of that workflow and never resolves it (the join itself is computed by a
consumer reading the ledger, never at hook time).

Bounded wall: the source lane measured interpreter start plus resolver git latency as its
stall (~15 s at host load 15-24, ~50% ambiguous voids) -- this port keeps imports light (no
heavy imports at module top; `routing_lib` and `hyp_config` are both stdlib-only) and states
its chosen Stop timeout (15 s) in docs/model-routing.md rather than assuming a quiet host.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import load_config, resolve_root  # noqa: E402
import routing_lib as R  # noqa: E402

LEDGER_RELPATH = os.path.join("ledger", "routing-ledger.jsonl")
OUTCOMES_RELDIR = os.path.join(".claude", "routing-outcomes")


def _repo_name(root):
    try:
        return os.path.basename(os.path.abspath(root.rstrip(os.sep))) or "unknown"
    except Exception:
        return "unknown"


def _watermark_path(session_id):
    """The watermark is an optimisation, never load-bearing for correctness (row-key dedup
    is): it must never enter the TRACKED consumer tree, or two worktrees of one session that
    each write it independently conflict on every merge over a variable this ledger does not
    declare `merge=union` on. Keyed by session id alone (never the checkout path), so two
    worktrees of one session converge on one watermark state; stored under the config dir so
    it survives a session without ever being a repository file."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "routing-ledger-watermarks", "%s.json" % (session_id or "unknown"))


def _run_directories(payload):
    """wf_* directories under this session's workflow run root, sorted for determinism."""
    transcript_path = payload.get("transcript_path") or ""
    session_id = payload.get("session_id") or os.path.splitext(os.path.basename(transcript_path))[0]
    if not transcript_path or not session_id:
        return session_id, []
    project_dir = os.path.dirname(transcript_path)
    run_root = os.path.join(project_dir, session_id, "subagents", "workflows")
    return session_id, sorted(glob.glob(os.path.join(run_root, "wf_*")))


def _agent_meta_files(run_dir):
    return sorted(glob.glob(os.path.join(run_dir, "agent-*.meta.json")))


def _table_context(root, plugin_root):
    """(prices_table, prices_sha, table_sha, default_sha, override_sha). Prices and the
    default routing table ship WITH THE PLUGIN (`rules/` lives in the installed plugin tree,
    never the consumer checkout); a repository-side override, when one exists, is read from
    `.claude/routing.json` -- the SAME file and SAME merge (`routing_lib.merge_table`) the
    model-routing guard already uses, so `table_sha`/`default_sha` on a ledger row and on a
    `routing.py table` printout always agree; this writer never forks a second notion of the
    effective table. None-safe, never resolves a role."""
    prices_path = os.path.join(plugin_root or "", "rules", "model-prices.json")
    default_path = os.path.join(plugin_root or "", "rules", "routing-default.json")
    override_path = os.path.join(root, ".claude", "routing.json")
    prices_table = R.read_json(prices_path)
    prices_sha = R.sha256_file(prices_path)
    default_obj = R.read_json(default_path) or {}
    override_obj = R.read_json(override_path)
    if not isinstance(override_obj, dict):
        override_obj = {}
    table = R.merge_table(default_obj, override_obj)
    override_sha = R.sha256_bytes(R.canonical_json_bytes(override_obj)) if os.path.isfile(override_path) else None
    return prices_table, prices_sha, table.get("table_sha"), table.get("default_sha"), override_sha


def _outcome_pointer(root, wf):
    """(lane, run, outcome_ref) from the consumer-side opt-in file
    `<root>/.claude/routing-outcomes/<wf>.json`, else all-None. The row carries only the
    pointer -- the join itself (routing_lib.join_outcome) is computed by whoever reads the
    ledger, never by the writer at hook time."""
    pointer = R.read_json(os.path.join(root, OUTCOMES_RELDIR, "%s.json" % wf))
    if not isinstance(pointer, dict):
        return None, None, None
    lane = pointer.get("lane")
    outcome_ref = pointer.get("outcome_ref")
    if not (lane and outcome_ref):
        return None, None, None
    return lane, pointer.get("run"), outcome_ref


def sweep(root, payload, host_load_1m):
    cfg = load_config(root)
    del cfg  # reserved: a future ledger path override key; unused today.
    session_id, run_dirs = _run_directories(payload)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    prices_table, prices_sha, table_sha, default_sha, override_sha = _table_context(root, plugin_root)
    existing, malformed = R.load_existing_rows(os.path.join(root, LEDGER_RELPATH))
    candidates, skipped, truncated_count = [], [], 0
    unfinished = {"failed": 0, "running": 0}
    agents_seen = 0
    repo = _repo_name(root)

    for run_dir in run_dirs:
        wf = os.path.basename(run_dir)
        journal_rows, _ = R.read_jsonl_capped(os.path.join(run_dir, "journal.jsonl"))
        if not journal_rows:
            skipped.append({"wf": wf, "reason": "no readable journal.jsonl"})
            continue
        index = R.journal_index(journal_rows)
        lane, run_no, outcome_ref = _outcome_pointer(root, wf)

        for meta_path in _agent_meta_files(run_dir):
            agent = R.agent_id_from_meta_path(meta_path)
            meta = R.read_json(meta_path)
            if not agent or not isinstance(meta, dict):
                skipped.append({"wf": wf, "reason": "unreadable meta: %s" % os.path.basename(meta_path)})
                continue
            agents_seen += 1
            records = index.get(agent) or {"started": None, "result": None, "failed": None}
            if records["result"] is None:
                # No result record -> no outcome exists; counted in the summary, never a
                # row (never imputed). A later sweep records the agent if a result lands;
                # the (wf, agent) key makes that safe.
                unfinished["failed" if records["failed"] is not None else "running"] += 1
                continue

            transcript = R.scan_transcript(os.path.join(run_dir, "agent-%s.jsonl" % agent))
            if not transcript["readable"]:
                skipped.append({"wf": wf, "agent": agent, "reason": "no readable transcript"})
                continue
            if transcript["truncated"]:
                truncated_count += 1

            started = records["started"] or {}
            label = started.get("label") or meta.get("description") or ""
            role, klass = R.role_and_class(label)
            declared = {"model": meta.get("model"), "effort": meta.get("effort"),
                        "agentType": meta.get("agentType")}
            observed_model = transcript["dominant_model"]
            observed = {"model": observed_model, "tier": R.tier_for_model(observed_model)}
            tokens = transcript["tokens"]

            row = R.build_row(
                ts=transcript["ts"],
                repo=repo,
                session=session_id,
                wf=wf,
                agent=agent,
                label=label,
                role=role,
                klass=klass,
                declared=declared,
                observed=observed,
                tokens=tokens,
                wall_s=transcript["wall_s"],
                cost=R.cost_usd(tokens, observed_model, prices_table),
                prices_sha=prices_sha,
                table_sha=table_sha,
                default_sha=default_sha,
                override_sha=override_sha,
                lane=lane,
                run=run_no,
                outcome_ref=outcome_ref,
                outcome=R.outcome_from_result(records["result"]),
                override=None,
                host_load_1m=host_load_1m,
                guard_ran=None,
                transcript_truncated=transcript["truncated"],
            )
            candidates.append(row)

    to_write, duplicates, conflicts = R.classify_new_rows(candidates, existing)
    if to_write:
        R.append_rows(os.path.join(root, LEDGER_RELPATH), to_write)
    for conflict in conflicts:
        sys.stderr.write(
            "routing-ledger: REFUSED conflicting row for (wf=%s, agent=%s) -- differs from the "
            "row already on disk; not written\n" % R.row_key(conflict))

    R.write_watermark(_watermark_path(session_id), {
        "session": session_id, "swept_wf": sorted({os.path.basename(d) for d in run_dirs})})

    return {
        "written": len(to_write), "duplicates": len(duplicates), "conflicts": len(conflicts),
        "skipped": skipped, "malformed_existing": malformed, "transcript_truncated": truncated_count,
        "agents_seen": agents_seen, "unfinished": unfinished,
    }


def _host_load_1m():
    """The live 1-minute load average, UNLESS a test harness pins one via
    HYP_ROUTING_LEDGER_LOAD_OVERRIDE -- a covariate on the row (never gates anything), but a
    live-resampled float differing call to call otherwise makes two independent, otherwise-
    identical observations of the SAME agent fail byte-identity (breaks both a same-process
    idempotence check and a cross-worktree merge union). Production is unaffected (the
    override is unset outside a harness)."""
    override = os.environ.get("HYP_ROUTING_LEDGER_LOAD_OVERRIDE")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        root = resolve_root(payload)
        summary = sweep(root, payload, _host_load_1m())
        sys.stdout.write(json.dumps({"routing_ledger": summary}) + "\n")
    except Exception as exc:  # fail open: an observational hook never blocks the turn.
        sys.stderr.write("routing-ledger: internal error (fail-open, no row written): %r\n" % (exc,))
    sys.exit(0)


if __name__ == "__main__":
    main()
