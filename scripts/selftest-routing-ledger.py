#!/usr/bin/env python3
"""selftest-routing-ledger.py -- regression test for the routing ledger writer.

  library    tier_for_model / declared_tier / role_and_class / journal_index /
             outcome_from_result / summarise_transcript_rows / build_row / classify_new_rows /
             join_outcome / cost_usd -- pure functions in hooks/scripts/routing_lib.py's
             agent-route/v1 section (ported from the lab keep H-DRAFT-38f86fad-routing-ledger-row)
  writer     drives the INSTALLED `hooks/scripts/routing-ledger.py` end to end as a subprocess
             (the pattern of scripts/selftest-worktree-root.py) against a throwaway consumer
             repository carrying a synthetic run directory in the real Claude Code shape
             (journal.jsonl started/result/failed records, agent-<id>.meta.json, agent-<id>.jsonl
             transcripts):
               - one row per finished agent (a `result` record); a `failed` or still-running
                 agent gets no row
               - role/class resolution from the label head, and declared-vs-observed tier
                 mismatch typing (`void: annulled`)
               - cost arithmetic: the row's `cost_usd` matches `routing_lib.cost_usd` computed
                 independently against the plugin's own shipped `rules/model-prices.json`
               - dedup: running the hook twice over the same run directory appends no second row,
                 and dedup identity ignores `host_load_1m` -- two sweeps of one agent taken under
                 DIFFERENT host loads still dedup as a duplicate, never refuse as a conflict (B1)
               - override_sha is never null: an absent `.claude/routing.json` still hashes as the
                 empty-override bytes, matching `table_sha`'s own convention (B2)
               - redaction: a planted prompt string in the transcript's user turn never appears
                 anywhere in the ledger file
               - fail-open: a malformed (truncated/non-JSON) journal.jsonl yields exit 0, no
                 traceback on stderr, and no row for that workflow
               - wall: one writer invocation completes well inside its hooks.json row budget
                 (15 s Stop / 10 s SubagentStop)

Usage: python3 scripts/selftest-routing-ledger.py        exit 0 = PASS, 1 = FAIL
Provenance: ported for the changeset that ships the lab keep H-DRAFT-38f86fad-routing-ledger-row
(VERDICT.json: evidence-sufficient promote, 5/5 looks, llr 2.9389 >= 2.8904).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "scripts"))
import routing_lib as R  # noqa: E402

RESULTS = []


def check(name, condition):
    RESULTS.append((name, bool(condition)))
    print(("PASS " if condition else "FAIL ") + name)


GIT_ENV = {"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("HOME", "/"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd] + list(args), check=True, capture_output=True, env=GIT_ENV)


def write(root, rel, obj_or_text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def write_jsonl(root, rel, rows):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _line(ts, model, usage):
    return {"type": "assistant", "timestamp": ts, "message": {"model": model, "usage": usage}}


SECRET = "sk-not-a-real-secret-plant-8f86fad"


def build_wf(root, wf, secret=None):
    """One synthetic workflow: agent `a1` finished (a `result` record), agent `a2` failed
    (a `failed` record, no result -- must yield no row). Returns (run_dir, a1_tokens)."""
    run_dir = os.path.join(root, "proj", "sess1", "subagents", "workflows", wf)
    write_jsonl(run_dir, "journal.jsonl", [
        {"type": "launched"},
        {"type": "started", "key": "k1", "agentId": "a1", "label": "build:port-selftest", "phase": "Build"},
        {"type": "result", "key": "k1", "agentId": "a1", "result": {"verdict": "keep", "refuted": False}},
        {"type": "started", "key": "k2", "agentId": "a2", "label": "refute:round-1"},
        {"type": "failed", "key": "k2", "agentId": "a2"},
    ])
    write(run_dir, "agent-a1.meta.json", {"agentType": "workflow-subagent", "description": "build:port-selftest"})
    write(run_dir, "agent-a2.meta.json", {"agentType": "workflow-subagent", "description": "refute:round-1"})
    tokens = {"input_tokens": 1000, "output_tokens": 500,
              "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 3000}
    rows = [_line("2026-01-01T00:00:00.000Z", "claude-sonnet-5", tokens)]
    if secret:
        rows.insert(0, {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
                        "message": {"role": "user", "content": secret}})
    write_jsonl(run_dir, "agent-a1.jsonl", rows)
    write_jsonl(run_dir, "agent-a2.jsonl", [_line("2026-01-01T00:00:00.000Z", "claude-sonnet-5", {})])
    return run_dir, tokens


def run_writer(project_root, hook_name="Stop", timeout=30, load_override=None):
    """Invoke the installed hooks/scripts/routing-ledger.py exactly the way hooks.json wires
    it, over `project_root`'s own session (proj/sess1). `load_override`, when given, pins
    `HYP_ROUTING_LEDGER_LOAD_OVERRIDE` for this invocation only (a live-resampled load average
    otherwise differs call to call -- see B1). Returns (proc, elapsed_s)."""
    payload = {"transcript_path": os.path.join(project_root, "proj", "sess1.jsonl"),
               "session_id": "sess1", "cwd": project_root, "hook_event_name": hook_name}
    env = dict(GIT_ENV)
    env.update({"CLAUDE_PLUGIN_ROOT": PLUGIN, "CLAUDE_PROJECT_DIR": project_root,
                "PYTHONDONTWRITEBYTECODE": "1"})
    if load_override is not None:
        env["HYP_ROUTING_LEDGER_LOAD_OVERRIDE"] = str(load_override)
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-B", os.path.join(PLUGIN, "hooks", "scripts", "routing-ledger.py")],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=project_root, timeout=timeout)
    return proc, time.time() - t0


def mk_consumer(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    write(path, ".claude/hyp.json", {"profile": "capture", "context": "selftest"})
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")


def ledger_lines(root):
    p = os.path.join(root, "ledger", "routing-ledger.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_library():
    check("tier-for-model-sonnet", R.tier_for_model("claude-sonnet-5") == "sonnet")
    check("tier-for-model-fable", R.tier_for_model("claude-fable-5-1") == "fable")
    check("tier-for-model-none", R.tier_for_model(None) is None)
    check("tier-for-model-unknown", R.tier_for_model("some-other-model") == "unknown")
    check("declared-tier-word", R.declared_tier("Sonnet") == "sonnet")
    check("declared-tier-full-id", R.declared_tier("claude-haiku-4-5-20251001") == "haiku")
    check("declared-tier-none", R.declared_tier(None) is None)

    check("role-class-from-label", R.role_and_class("refute:round-6") == ("refute", "adversarial"))
    check("role-class-unknown-head", R.role_and_class("zzz:thing") == ("zzz", "unknown"))
    check("role-class-empty", R.role_and_class("") == ("unknown", "unknown"))
    check("agent-id-from-meta-path", R.agent_id_from_meta_path("/x/agent-a18f56f6d434ed1c3.meta.json")
          == "a18f56f6d434ed1c3")
    check("agent-id-not-a-meta", R.agent_id_from_meta_path("/x/agent-a18f.jsonl") is None)

    journal = [{"type": "launched"},
               {"type": "started", "key": "k1", "agentId": "a1", "label": "build:x", "phase": "Build"},
               {"type": "result", "key": "k1", "agentId": "a1", "result": {"verdict": "keep", "refuted": False}},
               {"type": "started", "key": "k2", "agentId": "a2"},
               {"type": "failed", "key": "k2", "agentId": "a2"},
               {"type": "started", "key": "k3", "agentId": "a3"},
               {"type": "result", "key": "k3", "agentId": "a3", "result": "plain text output"}]
    idx = R.journal_index(journal)
    check("journal-index-result-agent", idx["a1"]["result"] is not None and idx["a1"]["started"]["label"] == "build:x")
    check("journal-index-failed-agent", idx["a2"]["result"] is None and idx["a2"]["failed"] is not None)
    check("journal-index-launched-ignored", set(idx) == {"a1", "a2", "a3"})

    o1 = R.outcome_from_result(idx["a1"]["result"])
    check("outcome-object-result", o1 == {"schema_valid": True, "verdict": "keep", "refuted": False})
    o3 = R.outcome_from_result(idx["a3"]["result"])
    check("outcome-string-result", o3 == {"schema_valid": True, "verdict": None, "refuted": None})
    o_bad = R.outcome_from_result({"type": "result", "agentId": "a9",
                                   "result": {"verdict": "discard", "refuted": True, "schema_retry_exhausted": True}})
    check("outcome-schema-retry-exhausted", o_bad == {"schema_valid": False, "verdict": "discard", "refuted": True})
    check("outcome-none-without-record", R.outcome_from_result(None) is None)
    long_v = "YES " + "x" * 200
    bounded = R.outcome_from_result({"type": "result", "agentId": "a8", "result": {"verdict": long_v}})["verdict"]
    check("outcome-verdict-bounded", len(bounded) == R.VERDICT_MAX_CHARS and bounded.endswith("...")
          and R.bound_verdict(bounded) == bounded and R.bound_verdict("keep") == "keep")

    rows = [_line("2026-01-01T00:00:00.000Z", "claude-fable-5",
                  {"input_tokens": 2, "output_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0}),
            {"type": "user", "timestamp": "2026-01-01T00:00:05.000Z", "message": {"role": "user", "content": "x"}},
            _line("2026-01-01T00:00:12.500Z", "claude-fable-5",
                  {"input_tokens": 3, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 400}),
            _line("2026-01-01T00:00:13.000Z", "<synthetic>", {"input_tokens": 0, "output_tokens": 0})]
    summary = R.summarise_transcript_rows(rows)
    check("transcript-dominant-model", summary["dominant_model"] == "claude-fable-5")
    check("transcript-tokens-summed-all-models",
          summary["tokens"] == {"in": 5, "out": 30, "cache_create": 100, "cache_read": 400, "messages": 3})
    check("transcript-ts-last-assistant", summary["ts"] == "2026-01-01T00:00:13.000Z")
    check("transcript-wall-s", summary["wall_s"] == 13.0)
    check("transcript-user-lines-ignored", "<synthetic>" in summary["models"] and len(summary["models"]) == 2)
    check("transcript-empty", R.summarise_transcript_rows([])["dominant_model"] is None
          and R.summarise_transcript_rows([])["wall_s"] is None)

    row = R.build_row(
        ts="2026-01-01T00:00:00Z", repo="r", session="s", wf="wf_x", agent="a1", label="",
        role="build", klass="execute", declared={"model": "sonnet", "effort": None, "agentType": "workflow-subagent"},
        observed={"model": "claude-sonnet-5", "tier": "sonnet"}, tokens={"in": 1, "out": 2},
        wall_s=1.0, cost=0.0, prices_sha=None, table_sha=None, default_sha=None, override_sha=None,
        lane=None, run=None, outcome_ref=None, outcome=None, override=None, host_load_1m=None)
    check("build-row-no-mismatch", row["mismatch"] is False and "void" not in row)

    row_mismatch = R.build_row(
        ts="2026-01-01T00:00:00Z", repo="r", session="s", wf="wf_x", agent="a2", label="",
        role="build", klass="execute", declared={"model": "sonnet", "effort": None, "agentType": "workflow-subagent"},
        observed={"model": "claude-haiku-4-5", "tier": "haiku"}, tokens={"in": 1, "out": 2},
        wall_s=1.0, cost=0.0, prices_sha=None, table_sha=None, default_sha=None, override_sha=None,
        lane=None, run=None, outcome_ref=None, outcome=None, override=None, host_load_1m=None)
    check("build-row-mismatch-typed-annulled", row_mismatch["mismatch"] is True and row_mismatch.get("void") == "annulled")

    to_write, dup, conf = R.classify_new_rows([row, row], {})
    check("classify-in-batch-duplicate-collapsed", len(to_write) == 1 and len(dup) == 1 and len(conf) == 0)
    row_diff = dict(row, wall_s=99.0)
    to_write2, dup2, conf2 = R.classify_new_rows([row, row_diff], {})
    check("classify-in-batch-conflict-refused", len(to_write2) == 1 and len(conf2) == 1)
    to_write3, dup3, conf3 = R.classify_new_rows([row], {R.row_key(row): row})
    check("classify-against-existing-duplicate", len(to_write3) == 0 and len(dup3) == 1)

    # B1: host_load_1m is a live-resampled covariate, never load-bearing identity -- two
    # otherwise-identical rows that differ only in host_load_1m must dedup as a duplicate,
    # never refuse as a conflict.
    row_load_a = dict(row, host_load_1m=1.0)
    row_load_b = dict(row, host_load_1m=2.0)
    to_write4, dup4, conf4 = R.classify_new_rows([row_load_b], {R.row_key(row_load_a): row_load_a})
    check("classify-differing-load-is-duplicate-not-conflict", len(to_write4) == 0 and len(dup4) == 1 and len(conf4) == 0)
    row_load_c = dict(row, host_load_1m=1.0, wall_s=99.0)
    to_write5, dup5, conf5 = R.classify_new_rows([row_load_c], {R.row_key(row_load_a): row_load_a})
    check("classify-real-difference-still-a-conflict", len(to_write5) == 0 and len(conf5) == 1)

    known = {"assertions": {"A1": {"pass": True}, "A2": {"pass": True}, "A3": {"pass": False}}}
    check("join-outcome-known-answer", R.join_outcome(known) == round(2 / 3, 4))
    check("join-outcome-none-on-bad-shape", R.join_outcome({"nope": 1}) is None)

    b = R.canonical_bytes({"b": 1, "a": 2})
    check("canonical-bytes-sorted-keys", b == b'{"a":2,"b":1}')
    check("sha256-bytes-deterministic", R.sha256_bytes(b) == R.sha256_bytes(b))
    check("sha256-file-fail-open", R.sha256_file("/no/such/path/at/all") is None)

    # cost_usd against the plugin's own SHIPPED rules/model-prices.json (prefix-matched
    # list schema, prices per million tokens directly -- not the lane's placeholder
    # tier+percentage schema).
    prices = R.read_json(os.path.join(PLUGIN, "rules", "model-prices.json"))
    check("model-prices-json-readable", isinstance(prices, dict) and prices.get("prices"))
    tokens = {"in": 1_000_000, "out": 1_000_000, "cache_read": 1_000_000, "cache_create": 1_000_000}
    expect_sonnet = round(2.00 + 10.00 + 0.20 + 2.50, 6)
    check("cost-usd-sonnet-prefix-match", R.cost_usd(tokens, "claude-sonnet-5", prices) == expect_sonnet)
    check("cost-usd-no-match", R.cost_usd(tokens, "some-unlisted-model", prices) == 0.0)
    check("cost-usd-no-model", R.cost_usd(tokens, None, prices) == 0.0)
    check("cost-usd-no-table", R.cost_usd(tokens, "claude-sonnet-5", None) == 0.0)

    # Reconciled price table (2026-09-17, lab DESIGN-rtk-token-cost): a model-specific row
    # wins over its tier row by longest-prefix match, and an unknown model within a known
    # tier family falls back to the tier row.
    expect_fable_5_1 = round(10.00 + 50.00 + 0.25 + 12.50, 6)
    check("cost-usd-fable-5-1-specific-row-wins-over-tier",
          R.cost_usd(tokens, "claude-fable-5-1", prices) == expect_fable_5_1)
    expect_fable_tier = round(10.00 + 50.00 + 1.00 + 12.50, 6)
    check("cost-usd-fable-5-no-specific-row-falls-back-to-tier",
          R.cost_usd(tokens, "claude-fable-5", prices) == expect_fable_tier
          and expect_fable_tier != expect_fable_5_1)
    expect_sonnet_5 = round(2.00 + 10.00 + 0.20 + 2.50, 6)
    check("cost-usd-sonnet-5-specific-row",
          R.cost_usd(tokens, "claude-sonnet-5", prices) == expect_sonnet_5)
    price_rows = prices.get("prices") or []
    check("model-prices-json-exactly-seven-prefixes", len(price_rows) == 7)
    check("model-prices-json-every-row-carries-required-keys",
          all(all(k in row for k in ("prefix", "in", "out", "cache_read", "cache_create", "seen", "source"))
              for row in price_rows))


def test_writer():
    tmp = tempfile.mkdtemp(prefix="hyp-selftest-routing-ledger-")
    try:
        consumer = os.path.join(tmp, "consumer")
        mk_consumer(consumer)
        run_dir, tokens = build_wf(consumer, "wf_test1", secret=SECRET)

        proc, elapsed = run_writer(consumer)
        check("writer-exit-0", proc.returncode == 0)
        check("writer-no-traceback", "Traceback" not in proc.stderr)
        check("writer-wall-under-budget", elapsed < 10.0)  # hooks.json row timeout is 15 s (Stop)

        lines = ledger_lines(consumer)
        check("writer-one-row-per-finished-agent", len(lines) == 1)
        row = lines[0] if lines else {}
        check("writer-no-row-for-failed-agent", all(r.get("agent") != "a2" for r in lines))
        check("writer-role-class-resolved", row.get("role") == "build" and row.get("class") == "execute")
        check("writer-observed-model", (row.get("observed") or {}).get("model") == "claude-sonnet-5"
              and (row.get("observed") or {}).get("tier") == "sonnet")
        check("writer-tokens-summed", row.get("tokens", {}).get("in") == 1000
              and row.get("tokens", {}).get("out") == 500)

        prices = R.read_json(os.path.join(PLUGIN, "rules", "model-prices.json"))
        expect_cost = R.cost_usd({"in": 1000, "out": 500, "cache_read": 3000, "cache_create": 2000},
                                  "claude-sonnet-5", prices)
        check("writer-cost-arithmetic", row.get("cost_usd") == expect_cost and expect_cost > 0)
        check("writer-table-sha-present", bool(row.get("table_sha")) and bool(row.get("default_sha")))
        # B2: override_sha is never null, even with no .claude/routing.json on disk (the
        # empty-override bytes still hash to something, matching table_sha's own convention).
        check("writer-override-sha-never-null", isinstance(row.get("override_sha"), str) and len(row["override_sha"]) == 64)

        with open(os.path.join(consumer, "ledger", "routing-ledger.jsonl"), "r", encoding="utf-8") as f:
            raw = f.read()
        check("writer-redaction-secret-absent", SECRET not in raw)

        # Dedup on re-run: same run directory, nothing new -> still exactly one row.
        proc2, _ = run_writer(consumer)
        check("writer-second-run-exit-0", proc2.returncode == 0)
        lines2 = ledger_lines(consumer)
        check("writer-dedup-on-rerun", len(lines2) == 1 and lines2[0] == row)

        # A second, brand-new agent in a second workflow appends without disturbing the first.
        build_wf(consumer, "wf_test2")
        proc3, _ = run_writer(consumer)
        check("writer-third-run-exit-0", proc3.returncode == 0)
        lines3 = ledger_lines(consumer)
        check("writer-appends-new-workflow-row", len(lines3) == 2)

        # B1: two sweeps of one already-written agent under two DIFFERENT host loads must
        # dedup as duplicates, never refuse as conflicts (live repro in the finding: same
        # rows written at load 5.17, refused 12 s later at load 4.92).
        build_wf(consumer, "wf_test3")
        proc4a, _ = run_writer(consumer, load_override=1.0)
        check("writer-load-a-run-exit-0", proc4a.returncode == 0)
        lines4a = ledger_lines(consumer)
        check("writer-load-a-wrote-one-row", len(lines4a) == 3)
        proc4b, _ = run_writer(consumer, load_override=2.0)
        check("writer-load-b-run-exit-0", proc4b.returncode == 0)
        check("writer-load-b-no-refused-conflict", "REFUSED" not in proc4b.stderr)
        lines4b = ledger_lines(consumer)
        check("writer-load-b-no-duplicate-row-written", len(lines4b) == 3)

        # Fail-open on a malformed journal: garbage bytes, not one valid JSON line.
        bad_run = os.path.join(consumer, "proj", "sess1", "subagents", "workflows", "wf_bad")
        os.makedirs(bad_run, exist_ok=True)
        with open(os.path.join(bad_run, "journal.jsonl"), "wb") as f:
            f.write(b"\x00\x01not json at all{{{\n")
        proc4, elapsed4 = run_writer(consumer)
        check("writer-malformed-journal-exit-0", proc4.returncode == 0)
        check("writer-malformed-journal-no-traceback", "Traceback" not in proc4.stderr)
        check("writer-malformed-journal-no-new-rows", len(ledger_lines(consumer)) == 3)
        check("writer-malformed-journal-wall-under-budget", elapsed4 < 10.0)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_library()
    test_writer()
    passed = sum(1 for _, ok in RESULTS if ok)
    print("RESULT: %s (%d/%d)" % ("PASS" if passed == len(RESULTS) else "FAIL", passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
