#!/usr/bin/env python3
"""selftest-decision-queue.py -- regression test for the decision queue (/hyp:decisions; docs/decisions.md section 8).

Runs the INSTALLED plugin's queue selftests from the tree this file lives in, forwards their per-check lines, then
drives the shipped wiring end to end in throwaway repositories, printing one PASS/FAIL line per stage and a RESULT line:

  queue-selftest       decision_queue.py --selftest: the projection (the caller's cards only; --all lists another role's
                       card view-only), the seam (NOT-ADDRESSEE refused before anything is appended; one single-line
                       commit on a dirty ledger carrying via=decisions-queue; the route table incl. the go-deeper prose
                       fallback), the ladder and the findings (UNAUTHORIZED, CONTESTED + settle, SUPERSEDED-BY-ADDRESSEE,
                       ADDRESSEE-UNMAPPED, OVERRIDE, MULTI-ROW-COMMIT), the union merge in both orders, the identity
                       cases, readiness, the announce per source, zero said, off, the deadline (a real git shim), the
                       behind clause, the lock and the failing signer -- the named checks below must each PASS
  decisions-selftest   decisions.py --selftest: the kit's loop under the ON bytes; resolve-dirty-ledger-single-line
                       (RESOLVE-BLOCKED retired)
  render-selftest      decision_brief_render.py --selftest: control-options-slot-rule (the one source of the two control texts)
  announce-contract    the frozen grammar constants; the deadline derivation 7.8 s = floor(10 s / 1.28 x 10) / 10 with the
                       2.2 s margin and the 60 s H-310 ceiling; hooks/hooks.json carries exactly one SessionStart row under
                       the matcher startup|resume|clear|compact|fork running decision_queue.py announce --hook at timeout 10,
                       outside the budget wrapper, with no 2>/dev/null, no head and no || true; the resolver row's wrapper
                       carries the RESOLVER-FAILED line; skills/decisions/SKILL.md and the four evals/decisions/ cases ship
  cli-delegates        a throwaway consumer driven through decisions.py's delegates as real processes: queue --json shows the
                       caller only (mine true, batched) and --all the other role's card with mine false and never batched;
                       queue --text prints the DECISIONS-YOURS line; queue-answer records ONE single-line commit carrying
                       via=decisions-queue while a dirty intent row stays uncommitted; the non-addressee is refused with the
                       ledger byte-unchanged; announce --hook prints the zero-said JSON through the delegate; the compiler
                       renders section 1c and routes the header through the ladder; check exits 0; the resolver
                       row's wrapper prints the DECISIONS-OPEN suffix line and RESOLVER-FAILED on an empty reading
  scaffold-union       init-scaffold.py writes `<ledger> merge=union` to .gitattributes once (created, then unchanged on a
                       re-run; the configured ledger_file when the consumer set one) and git check-attr reads union;
                       harden-check.sh prints ADVISORY-36 ledger-merge-attribute while the line is missing and not after

Usage: python3 scripts/selftest-decision-queue.py    exit 0 = PASS, 1 = FAIL
Provenance: cause-n-effect H-DRAFT-015cb9c8-decision-queue-projection (kept 2026-09-13 by the lineage rule: five counted
looks each 5/5; journal fragment 0515). Standard library, Python 3.9; writes only under the temp dirs it creates and
removes itself and the temp dirs the component selftests create and remove themselves.
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

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
ANNOUNCE_MATCHER = "startup|resume|clear|compact|fork"
ANNOUNCE_ROW_TIMEOUT_S = 10
H310_HOOK_LIMIT_S = 60
H310_HEADROOM_RATIO = 1.28
ANNOUNCE_DEADLINE_S = 7.8
ANNOUNCE_MARGIN_S = 2.2
EVAL_CASES = ("headless-surface-only", "not-ready-never-a-question", "dirty-ledger-single-line-commit", "no-invented-options")
A, B = "a@dq-plugin-selftest.invalid", "b@dq-plugin-selftest.invalid"
STAMP = "2026-03-01"

STAGES = (
    ("queue-selftest", "decision_queue.py", "queue-selftest: 0 failure(s)", "QUEUE-SELFTEST-",
     ("filter-mine-only", "identity-roles", "all-lists-others-view-only", "envelope-keys", "ask-contract", "slot-rule",
      "batches-4-1-equal-question-split", "filters-never-change-counts", "not-addressee-refused",
      "dirty-ledger-single-line-commit", "via-on-row", "route-go-deeper", "go-deeper-fallback-comment",
      "later-when-waiting", "unauthorized-status-unchanged", "contested-earlier-decides", "settles-row-clears-contested",
      "human-denied-over-record", "multi-row-commit", "union-merge-ab", "union-merge-ba", "identity-unresolved",
      "mailmap-fold", "unmapped-visible-mine-null", "codeowners-path-role", "override-resolvable",
      "addressee-supersedes-override-without-reopen", "legacy-awaiting-brief", "not-ready-lines",
      "announce-json-per-source", "announce-zero-said", "announce-off-silent", "announce-deadline-loud",
      "announce-behind-clause", "announce-shim-deadline", "announce-shim-last-act-new-unknown", "resolve-busy",
      "commit-failed-changes-nothing", "no-id-literals-in-source", "no-question-grammar-literals-in-source")),
    ("decisions-selftest", "decisions.py", "selftest: 0 failure(s)", "SELFTEST-", ("resolve-dirty-ledger-single-line",)),
    ("render-selftest", "decision_brief_render.py", "render-selftest: 0 failure(s)", "RENDER-SELFTEST-",
     ("control-options-slot-rule",)),
)


def clean_env(extra=None):
    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "DECISIONS_TODAY": STAMP,
                "PYTHONDONTWRITEBYTECODE": "1"})
    for k in ("HYP_DECISIONS", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_NAME",
              "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update(extra or {})
    return env


LOAD_SENSITIVE = ("announce-shim-deadline", "announce-shim-last-act-new-unknown")   # real-process shims at the 7.8 s margin


def run_stage(name, script, sentinel, prefix, wanted):
    proc = subprocess.run([sys.executable, "-B", os.path.join(HERE, script), "--selftest"], capture_output=True, text=True,
                          env=clean_env(), timeout=900)
    lines = proc.stdout.splitlines()
    failed_names = [l.split()[1] for l in lines if l.startswith(prefix + "FAIL") and len(l.split()) > 1]
    if failed_names and set(failed_names) <= set(LOAD_SENSITIVE):
        # the two shim scenarios time a real announce against its 7.8 s deadline; a loaded host can push the seven
        # pre-last-act calls past it (the spec's void class: load is a covariate, never a gate) -- re-run once, loudly
        try:
            load = "%.1f %.1f %.1f" % os.getloadavg()
        except (AttributeError, OSError):
            load = "?"
        print("LOAD-RETRY %s -- %s missed at load %s; re-running the stage once" % (name, ",".join(failed_names), load))
        proc = subprocess.run([sys.executable, "-B", os.path.join(HERE, script), "--selftest"], capture_output=True, text=True,
                              env=clean_env(), timeout=900)
        lines = proc.stdout.splitlines()
    for line in lines:
        if line.startswith(prefix):
            print("  " + line[:220])
    passed = [l for l in lines if l.startswith(prefix + "PASS")]
    failed = [l for l in lines if l.startswith(prefix + "FAIL")]
    missing = [w for w in wanted if not any(re.match(r"^%sPASS %s(\s|$)" % (re.escape(prefix), re.escape(w)), l) for l in lines)]
    ok = proc.returncode == 0 and sentinel in proc.stdout and not failed and not missing
    print("%s %s -- exit %d, %d check(s), %d failed, named checks %d/%d%s"
          % ("PASS" if ok else "FAIL", name, proc.returncode, len(passed), len(failed), len(wanted) - len(missing), len(wanted),
             (" MISSING " + ",".join(missing)) if missing else ""))
    if not ok and proc.stderr.strip():
        print("  stderr: " + proc.stderr.strip().splitlines()[-1][:200])
    return ok


def load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def announce_contract_stage():
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("CONTRACT-PASS" if cond else "CONTRACT-FAIL", name, (" -- " + str(detail)[:200]) if detail else ""))
        if not cond:
            fails.append(name)

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    dq = load_module("decision_queue", os.path.join(HERE, "decision_queue.py"))
    derived = int(ANNOUNCE_ROW_TIMEOUT_S / H310_HEADROOM_RATIO * 10) / 10.0
    ok("deadline-derivation", dq.ANNOUNCE_DEADLINE_S == ANNOUNCE_DEADLINE_S == derived
       and round(ANNOUNCE_DEADLINE_S + ANNOUNCE_MARGIN_S, 6) == ANNOUNCE_ROW_TIMEOUT_S
       and ANNOUNCE_ROW_TIMEOUT_S < H310_HOOK_LIMIT_S, (dq.ANNOUNCE_DEADLINE_S, derived))
    ok("announce-grammar", dq.SYSTEM_MESSAGE_FMT.startswith("Decisions: {n} are yours ({k} new since you last answered; ")
       and dq.SYSTEM_MESSAGE_FMT.endswith("acting as {role} ({basis}). Answer them: /hyp:decisions")
       and dq.SYSTEM_MESSAGE_NONE_FMT == "Decisions: none are yours — acting as {role} ({basis})"
       and dq.SYSTEM_MESSAGE_FAILED_FMT == "Decisions: count unavailable — ANNOUNCE-FAILED {reason}; run /hyp:decisions"
       and dq.MACHINE_LINE_FMT.startswith("DECISIONS-YOURS\t{n}\tnew {k}\treturned {d}\twaiting {w}\tto-glance {r}\trole {role} ({basis})\tbehind {b}\t")
       and dq.ANNOUNCE_SOURCES == tuple(ANNOUNCE_MATCHER.split("|")), dq.SYSTEM_MESSAGE_FMT)
    ok("decisions-open-suffix", dq.DECISIONS_OPEN_SUFFIX == " — answer them: /hyp:decisions")
    with io.open(os.path.join(PLUGIN_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)
    groups = hooks.get("hooks", {}).get("SessionStart", [])
    announce_groups = [g for g in groups if g.get("matcher") == ANNOUNCE_MATCHER]
    row = announce_groups[0]["hooks"][0] if announce_groups and announce_groups[0].get("hooks") else {}
    cmd = row.get("command", "")
    ok("hook-row-present", len(announce_groups) == 1 and len(announce_groups[0]["hooks"]) == 1
       and row.get("type") == "command" and cmd.endswith('/scripts/decision_queue.py" announce --hook'), cmd)
    ok("hook-row-timeout", row.get("timeout") == ANNOUNCE_ROW_TIMEOUT_S, row.get("timeout"))
    ok("hook-row-live", "session-start-budget" not in cmd and "2>/dev/null" not in cmd and "head" not in cmd and "|| true" not in cmd, cmd)
    every = [h for g in hooks["hooks"].values() for grp in g for h in grp.get("hooks", [])]
    resolver_rows = [h for h in every if "session_resolver.py" in h.get("command", "")]
    ok("resolver-wrapper-loud", len(resolver_rows) == 1 and "RESOLVER-FAILED" in resolver_rows[0]["command"]
       and "2>/dev/null" not in resolver_rows[0]["command"], len(resolver_rows))
    skill = os.path.join(PLUGIN_ROOT, "skills", "decisions", "SKILL.md")
    text = io.open(skill, encoding="utf-8").read() if os.path.isfile(skill) else ""
    ok("skill-shipped", text.startswith("---\nname: decisions\ndescription: ") and "/hyp:decisions" in text
       and '"${CLAUDE_PLUGIN_ROOT}/scripts/decisions.py"' in text and "queue --text" in text and "queue --json" in text
       and "queue-answer" in text and not any(lit in text for lit in ("ask:", "[ ]", "answer:")), skill)
    ok("evals-shipped", all(os.path.isfile(os.path.join(PLUGIN_ROOT, "evals", "decisions", c, "case.yaml")) for c in EVAL_CASES))
    print("%s announce-contract -- %d check(s), %d failed" % ("PASS" if not fails else "FAIL", 9, len(fails)))
    return not fails


# ---------- the throwaway consumer ----------

def sh(root, args, env=None, check=True, stdin=None):
    p = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True, env=env or clean_env(), input=stdin)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (args[:2], p.stderr.strip()[:200]))
    return p


def make_card(lint, n, role=None, k=2):
    opts = [{"label": "opt%d" % i, "description": "option %d happens" % i, "undo": "ledger-row"} for i in range(1, k + 1)]
    rec = {"kind": "decision", "id": "DEC-%03d" % n, "date": "2026-02-01", "requested_at": "2026-02-01",
           "requested_by": "lane selftest-%d" % n, "title": "Card %d" % n, "urgency": "normal", "class": "plan",
           "why_only_you": "only you", "context_pointers": ["experiments/runs/lane-%d/x" % n], "blocks": [],
           "ask": {"question": "Question %d?" % n, "header": "Card%d" % n, "multiSelect": False, "options": opts},
           "staged_artifact": "none", "evidence": "none-exists", "externality": "none", "recommended": "none",
           "default_on_silence": "nothing-changes", "door": {"fields_sha": "0" * 64, "outcome": "CARD"}}
    if role:
        rec["addressee"] = {"role": role}
    brief = {"decide": "Decide card %d now." % n, "situation": "It is ready.", "yours_because": "You hold the key.",
             "choices": [{"label": o["label"], "in_practice": "The %s option happens." % o["label"],
                          "undo": "A later row supersedes it."} for o in opts],
             "if_nothing": "Nothing changes: the card stays open.", "evidence_line": "The selftest built it.", "terms": {},
             "sources": ["pipeline-fact:card-stays-open"], "provenance": {"protocol": "inline"}}
    brief["card_sha"] = lint.card_sha(rec)
    brief["lint"] = {"tool": "decision_card_lint", "sha7": "0000000", "exit": 0, "brief_sha": lint.brief_sha(brief)}
    rec["brief"] = brief
    return rec


def consumer(tmp, name, email=A, roles=None, ledger="ledger/ledger.jsonl", attribute=True, cards=()):
    root = os.path.join(tmp, name)
    os.makedirs(os.path.join(root, ".claude"))
    os.makedirs(os.path.join(root, os.path.dirname(ledger)))
    subprocess.run(["git", "init", "-q", root], check=True, env=clean_env())
    sh(root, ["symbolic-ref", "HEAD", "refs/heads/main"])
    sh(root, ["config", "user.name", "identity-%s" % email[0].upper()])
    sh(root, ["config", "user.email", email])
    sh(root, ["config", "user.useConfigOnly", "true"])
    sh(root, ["config", "commit.gpgsign", "false"])
    sh(root, ["config", "core.hooksPath", "/dev/null"])
    cfg = {"profile": "experiments", "ledger_file": ledger, "decision_brief_legacy_max_id": 0, "decision_door_legacy_max_id": 0}
    if roles is not None:
        cfg["decision_roles"] = roles
    with io.open(os.path.join(root, ".claude", "hyp.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    with io.open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("selftest consumer\n")
    if attribute:
        with io.open(os.path.join(root, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write("%s merge=union\n" % ledger)
    with io.open(os.path.join(root, ledger), "w", encoding="utf-8") as fh:
        for rec in cards:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sh(root, ["add", "-A"])
    sh(root, ["commit", "-q", "-m", "consumer scaffold"],
       env=clean_env({"GIT_AUTHOR_DATE": "2026-02-02T00:00:00Z", "GIT_COMMITTER_DATE": "2026-02-02T00:00:00Z"}))
    return root


def cli(root, *args, stdin=None, root_first=False):
    """decisions.py as a process; --root trails the command for the three delegates (the order the skill and the
    fixture use) and precedes it for the kit's own commands (its argparse form)."""
    argv = (["--root", root] + list(args)) if root_first else (list(args) + ["--root", root])
    p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "decisions.py")] + argv,
                       capture_output=True, text=True, env=clean_env(), input=stdin, timeout=120)
    return p.returncode, p.stdout, p.stderr


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def cli_delegates_stage(tmp):
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("CLI-PASS" if cond else "CLI-FAIL", name, (" -- " + str(detail)[:240]) if detail else ""))
        if not cond:
            fails.append(name)

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    lint = load_module("decision_card_lint", os.path.join(HERE, "decision_card_lint.py"))
    root = consumer(tmp, "consumer", roles={"maintainer": [A], "lane-owner": [B]},
                    cards=(make_card(lint, 1, k=2), make_card(lint, 2, role="lane-owner", k=3)))
    ledger = os.path.join(root, "ledger", "ledger.jsonl")
    rc, out, err = cli(root, "queue", "--json")
    try:
        env = json.loads(out)
    except ValueError:
        env = {}
    items = env.get("items", [])
    ok("queue-json-caller-only", rc == 0 and [it["id"] for it in items] == ["DEC-001"] and env.get("can_record") is True
       and items and items[0]["accountable"] == {"role": "maintainer", "basis": "decision_roles", "mine": True}
       and env.get("batches") == [["DEC-001"]] and env["counts"]["mine"] == 1 and env["counts"]["others"] == 1
       and env["counts"]["open"] == 2, (rc, err[-200:], env.get("counts"), env.get("block_reason")))
    ok("queue-json-ask-contract", bool(items) and tuple(items[0]["ask"].keys()) == ("header", "question", "multiSelect", "options")
       and [o["label"] for o in items[0]["ask"]["options"]] == ["opt1", "opt2", "go deeper", "later"]
       and items[0]["controls"] == {"go_deeper": "option", "later": "option"}, items[0]["ask"] if items else None)
    rc, out, err = cli(root, "queue", "--json", "--all")
    env_all = json.loads(out) if rc == 0 and out.strip() else {}
    other = next((it for it in env_all.get("items", []) if it["id"] == "DEC-002"), None)
    ok("queue-all-others-view-only", other is not None and other["accountable"] == {"role": "lane-owner", "basis": "decision_roles", "mine": False}
       and env_all.get("batches") == [["DEC-001"]] and other["controls"] == {"go_deeper": "option", "later": "other"},
       (rc, other and other["accountable"], env_all.get("batches")))
    rc, out, err = cli(root, "queue", "--text")
    ok("queue-text-machine-line", rc == 0 and out.startswith("DECISIONS-YOURS\t1\tnew 1\treturned 0\twaiting 0\tto-glance 0\trole maintainer (decision_roles)\tbehind 0\tanswer: /hyp:decisions\n")
       and "DECISION-QUEUE\tDEC-001\t" in out and "DECISION-QUEUE\tbatches\t" in out, out[:160])
    # the seam on a dirty ledger: one single-line commit, the intent row left uncommitted
    with io.open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "intent", "date": STAMP, "slug": "selftest-intent", "hit": "an uncommitted intent row"}) + "\n")
    head0 = sh(root, ["rev-parse", "HEAD"]).stdout.strip()
    rc, out, err = cli(root, "queue-answer", "DEC-001", "--label", "opt1")
    head1 = sh(root, ["rev-parse", "HEAD"]).stdout.strip()
    stat = sh(root, ["show", "--stat", "--format=", "HEAD"]).stdout.strip().splitlines()
    subject = sh(root, ["log", "-1", "--format=%s%x1f%ae"]).stdout.strip().split("\x1f")
    text = read(ledger)
    last = json.loads(text.strip().splitlines()[-1])
    ok("queue-answer-single-line-commit", rc == 0 and head1 != head0 and "committed JUST that line" in out and len(stat) == 2
       and "1 insertion(+)" in stat[-1] and subject == ["decision: DEC-001 accepted — decision-resolved=DEC-001 via=decisions-queue", A]
       and last.get("via") == "decisions-queue" and last.get("chosen_options") == ["opt1"] and last.get("disposition") == "accepted"
       and "an uncommitted intent row" in text and sh(root, ["status", "--porcelain", "--", "ledger/ledger.jsonl"]).stdout.startswith(" M"),
       (rc, out[-200:], stat, subject))
    committed = sh(root, ["show", "HEAD:ledger/ledger.jsonl"]).stdout
    ok("dirty-row-stays-uncommitted", "an uncommitted intent row" not in committed and '"via": "decisions-queue"' in committed)
    before = read(ledger)
    rc, out, err = cli(root, "queue-answer", "DEC-002", "--label", "opt1")
    ok("not-addressee-refused-via-cli", rc == 2 and out.splitlines()[0] == "RESOLVE-REFUSED\tNOT-ADDRESSEE\tDEC-002\trole=lane-owner\tyou=decision_roles"
       and read(ledger) == before and sh(root, ["rev-parse", "HEAD"]).stdout.strip() == head1, (rc, out[:160]))
    rc, out, err = cli(root, "queue-answer", "DEC-001", "--label", "opt2")
    ok("already-decided-refused-via-cli", rc == 1 and out.startswith("RESOLVE-INVALID\tDEC-001 is already accepted"), out[:120])
    rc, out, err = cli(root, "announce", "--hook", stdin=json.dumps({"source": "startup", "cwd": root}))
    try:
        ann = json.loads(out)
    except ValueError:
        ann = {}
    ok("announce-hook-via-delegate", rc == 0 and ann.get("systemMessage") == "Decisions: none are yours — acting as maintainer (decision_roles)"
       and ann.get("hookSpecificOutput", {}).get("hookEventName") == "SessionStart"
       and ann.get("hookSpecificOutput", {}).get("additionalContext", "").startswith("DECISIONS-YOURS\t0\tnew 0\t"), (rc, out[:200], err[-120:]))
    rc, out, err = cli(root, "check", root_first=True)
    ok("check-clean", rc == 0 and "DECISIONS-CHECK\tFAIL" not in out and out.startswith("decisions-check: 2 decision(s)"), (rc, out[:200], err[-160:]))
    rc, out, err = cli(root, "queue", "--json", root_first=True)
    ok("queue-root-first-form", rc == 0 and json.loads(out)["counts"]["mine"] == 0, (rc, out[:120]))
    # the resolver row's wrapper (hooks.json), run as the budget wrapper runs it (sh -c on the inner command): the
    # DECISIONS-OPEN suffix and the role-aware open set on the consumer; RESOLVER-FAILED on an empty reading
    with io.open(os.path.join(PLUGIN_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        every = [h for g in json.load(fh)["hooks"].values() for grp in g for h in grp.get("hooks", [])]
    wrapper = next(h["command"] for h in every if "session_resolver.py" in h.get("command", ""))
    inner = wrapper[wrapper.index("'") + 1:wrapper.rindex("'")]
    renv = clean_env({"CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT, "CLAUDE_PROJECT_DIR": root})
    r1 = subprocess.run(["sh", "-c", inner], input=json.dumps({"source": "startup", "cwd": root}), capture_output=True, text=True,
                        env=renv, timeout=120)
    ok("resolver-wrapper-open-line", r1.returncode == 0 and "DECISIONS-OPEN\t1\toldest DEC-002 28d \u2014 answer them: /hyp:decisions" in r1.stdout
       and "DECISION-LEDGER\tDEC-002\t" in r1.stdout and "RESOLVER-FAILED" not in r1.stdout, (r1.returncode, r1.stdout[:240]))
    empty = os.path.join(tmp, "empty-consumer")
    os.makedirs(empty)
    r2 = subprocess.run(["sh", "-c", inner], input=json.dumps({"source": "startup", "cwd": empty}), capture_output=True, text=True,
                        env=clean_env({"CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT, "CLAUDE_PROJECT_DIR": empty}), timeout=120)
    ok("resolver-wrapper-loud-on-empty", r2.returncode == 0 and r2.stdout.startswith("RESOLVER-FAILED rc=0 empty-reading"), (r2.returncode, r2.stdout[:160]))
    p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "compile-dashboard.py"), root, "--quiet"], capture_output=True,
                       text=True, env=clean_env(), timeout=120)
    board = read(os.path.join(root, "DASHBOARD.md")) if os.path.isfile(os.path.join(root, "DASHBOARD.md")) else ""
    ok("compiler-routes-and-1c", p.returncode == 0 and "## 1c. DECIDED CARDS WITH FINDINGS (0)" in board
       and "(none — no decided card carries a multi-user finding)" in board
       and re.search(r"^## 1\. DECISIONS WAITING \(1 open — yours 0 \| others 1\)", board, re.M) is not None,
       (p.returncode, p.stderr[-160:], [l for l in board.splitlines() if l.startswith("## 1")][:3]))
    print("%s cli-delegates -- %d check(s), %d failed" % ("PASS" if not fails else "FAIL", 14, len(fails)))
    return not fails


def scaffold_union_stage(tmp):
    fails = []

    def ok(name, cond, detail=""):
        print("%s %s%s" % ("SCAFFOLD-PASS" if cond else "SCAFFOLD-FAIL", name, (" -- " + str(detail)[:240]) if detail else ""))
        if not cond:
            fails.append(name)

    scaffold = os.path.join(HERE, "init-scaffold.py")
    root = os.path.join(tmp, "init")
    os.makedirs(root)
    subprocess.run(["git", "init", "-q", root], check=True, env=clean_env())
    p1 = subprocess.run([sys.executable, "-B", scaffold, root, "--profile", "capture"], capture_output=True, text=True,
                        env=clean_env(), timeout=300)
    attrs = os.path.join(root, ".gitattributes")
    text1 = read(attrs) if os.path.isfile(attrs) else ""
    ok("scaffold-writes-union-line", p1.returncode == 0 and text1 == "ledger/ledger.jsonl merge=union\n"
       and any(l.startswith("created   .gitattributes") for l in p1.stdout.splitlines()), (p1.returncode, text1, p1.stderr[-160:]))
    p2 = subprocess.run([sys.executable, "-B", scaffold, root, "--profile", "capture"], capture_output=True, text=True,
                        env=clean_env(), timeout=300)
    ok("scaffold-idempotent", p2.returncode == 0 and read(attrs) == text1
       and any(l.startswith("unchanged .gitattributes") for l in p2.stdout.splitlines()), p2.stdout[-200:])
    attr = sh(root, ["check-attr", "merge", "--", "ledger/ledger.jsonl"]).stdout.strip()
    ok("check-attr-reads-union", attr == "ledger/ledger.jsonl: merge: union", attr)
    # a consumer that configured its ledger path: the line names that ledger
    root2 = os.path.join(tmp, "configured")
    os.makedirs(os.path.join(root2, ".claude"))
    subprocess.run(["git", "init", "-q", root2], check=True, env=clean_env())
    with io.open(os.path.join(root2, ".claude", "hyp.json"), "w", encoding="utf-8") as fh:
        json.dump({"profile": "capture", "ledger_file": "ledger/work-ledger.jsonl"}, fh)
    with io.open(os.path.join(root2, ".gitattributes"), "w", encoding="utf-8") as fh:
        fh.write("*.md text\n")
    p3 = subprocess.run([sys.executable, "-B", scaffold, root2, "--profile", "capture"], capture_output=True, text=True,
                        env=clean_env(), timeout=300)
    text3 = read(os.path.join(root2, ".gitattributes"))
    ok("scaffold-configured-ledger-appended", p3.returncode == 0 and text3 == "*.md text\nledger/work-ledger.jsonl merge=union\n"
       and any(l.startswith("updated   .gitattributes") for l in p3.stdout.splitlines()), (p3.returncode, text3))
    # the harden advisory fires while the line is missing and not after it lands
    root3 = consumer(tmp, "harden", roles={"maintainer": [A]}, attribute=False)
    henv = clean_env({"CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT, "HARDEN_BLOCK_MAX": "60", "HARDEN_TOTAL_MAX": "240"})
    h1 = subprocess.run(["sh", os.path.join(HERE, "harden-check.sh"), "--fresh"], cwd=root3, capture_output=True, text=True,
                        env=henv, timeout=400)
    ok("harden-advisory-36-fires", h1.returncode == 0 and "ADVISORY-36 ledger-merge-attribute: ledger/ledger.jsonl carries no merge=union attribute" in h1.stdout,
       (h1.returncode, [l for l in h1.stdout.splitlines() if "ADVISORY-36" in l][:1] or h1.stdout[-200:]))
    with io.open(os.path.join(root3, ".gitattributes"), "w", encoding="utf-8") as fh:
        fh.write("ledger/ledger.jsonl merge=union\n")
    h2 = subprocess.run(["sh", os.path.join(HERE, "harden-check.sh"), "--fresh"], cwd=root3, capture_output=True, text=True,
                        env=henv, timeout=400)
    ok("harden-advisory-36-quiet", h2.returncode == 0 and "ADVISORY-36" not in h2.stdout, [l for l in h2.stdout.splitlines() if "ADVISORY-36" in l][:1])
    print("%s scaffold-union -- %d check(s), %d failed" % ("PASS" if not fails else "FAIL", 6, len(fails)))
    return not fails


def main(argv):
    results = []
    for name, script, sentinel, prefix, wanted in STAGES:
        results.append(run_stage(name, script, sentinel, prefix, wanted))
    results.append(announce_contract_stage())
    tmp = tempfile.mkdtemp(prefix="selftest-decision-queue-")
    try:
        results.append(cli_delegates_stage(tmp))
        results.append(scaffold_union_stage(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failures = sum(1 for r in results if not r)
    print("RESULT %s selftest-decision-queue: %d of %d stage(s) failed" % ("PASS" if failures == 0 else "FAIL", failures, len(results)))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
