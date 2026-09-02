#!/bin/sh
# Passive hardening check — wired to SessionStart and PreCompact hooks.
# Prints warnings (which land in session context); exit 0 always (advisory, not blocking).
# The advisory body lives in main(); its stdout is captured once and then BOTH printed and
# written to .claude/harden-last.txt (the dashboard's advisory cache, session-local untracked
# infra like .claude/stop-snooze). Capture-then-write rather than a live tee pipe: advisory 19
# below runs compile-dashboard.py --check, whose source digest covers the cache file — a tee
# would have already truncated it mid-run and every session would read spuriously stale.
cd "$(dirname "$0")/.." || exit 0
main() {
W=0
if ! python3 scripts/check-governance-drift.py >/dev/null 2>&1; then
  echo "HARDEN-WARNING: GOVERNANCE drift — CLAUDE.md mirror differs from kernel canonical (run scripts/check-governance-drift.py)"; W=1
fi
latest_h=$(ls hypotheses/H-*.md 2>/dev/null | sed 's/.*H-\([0-9]*\).*/\1/' | sort -n | tail -1)
# row-based: the id must appear in a status-table row's ID cell — a mention elsewhere
# (e.g. another row's "refined-into H-NNN") must not mask a deleted row (H-077's finding)
if [ -n "$latest_h" ] && ! { grep -E '^\|' program.md | cut -d'|' -f2 | grep -Eq "H-0*${latest_h}([^0-9]|$)"; }; then
  echo "HARDEN-WARNING: program.md status table is stale — latest hypothesis H-$latest_h is not reflected"; W=1
fi
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [ "$dirty" -gt 0 ]; then
  echo "HARDEN-WARNING: $dirty uncommitted change(s) in the working tree — durable work should be committed (Durability invariant)"; W=1
fi
ahead=$(git status -sb 2>/dev/null | head -1 | grep -o 'ahead [0-9]*' | grep -o '[0-9]*')
if [ -n "$ahead" ] && [ "$ahead" -gt 0 ]; then
  echo "HARDEN-WARNING: $ahead unpushed commit(s) — durable only when shared"; W=1
fi
# eighth advisory (H-100/H-101 kept): open + unjoinable commitments, one line
if [ -f scripts/commitment-lint.py ]; then
  cm=$(python3 scripts/commitment-lint.py . 2>/dev/null | awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}')
  if [ -n "$cm" ]; then
    echo "HARDEN-WARNING: commitment findings: $cm(commitment-lint for detail; close via evidence or rewrite with closes-when)"; W=1
  fi
fi
# journal freeze (M5 ruled 2026-08-15): volume 1 is byte-frozen; entries live in fragments
if [ -f scripts/journal-freeze.sha ] && [ -f experiments/journal.md ]; then
  cur=$(shasum -a 256 experiments/journal.md | awk '{print $1}')
  if [ "$cur" != "$(cat scripts/journal-freeze.sha)" ]; then
    echo "HARDEN-WARNING: experiments/journal.md changed after the M5 freeze — volume 1 is byte-frozen; new entries belong in journal-fragments/"; W=1
  fi
fi
# sixth advisory class (H-092/H-093 kept): stale/unwired derived claims, one line
if [ -f scripts/claim-lint.py ]; then
  cc=$(python3 scripts/claim-lint.py . --check 2>/dev/null | awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}')
  if [ -n "$cc" ]; then
    echo "HARDEN-WARNING: derived-claim findings: $cc(claim_tool --check for detail; refresh via --fix in an attributed commit)"; W=1
  fi
fi
# fifth advisory class (H-080 kept): corpus staleness summary, one line, never blocking
if [ -f scripts/corpus-lint.py ]; then
  cl=$(timeout 45 python3 scripts/corpus-lint.py . 2>/dev/null | awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}')
  if [ -n "$cl" ]; then
    echo "HARDEN-WARNING: corpus-lint findings: $cl(run scripts/corpus-lint.py . for detail; triage via doc-factoring tier)"; W=1
  fi
fi
# fourteenth advisory class (H-104 kept): open move manifests — declared, never verified
if [ -f scripts/fidelity-manifest.py ] && [ -d manifests ]; then
  fm=$(python3 scripts/fidelity-manifest.py . --open 2>/dev/null | grep -c "^UNVERIFIED-MANIFEST" | tr -d ' ')
  if [ -n "$fm" ] && [ "$fm" != "0" ]; then
    echo "HARDEN-WARNING: $fm open move manifest(s) awaiting verification (scripts/fidelity-manifest.py . --open — verify after executing, or --abandon with a reason)"; W=1
  fi
fi
# thirteenth advisory class (H-070/H-082 kept): frontmatter confinement, epoch-anchored at the
# grant commit 26e9640 (pre-grant history is not actionable and stays excluded)
if [ -f experiments/runs/H-082/fixture/validator.py ] && git rev-parse -q --verify 26e9640 >/dev/null 2>&1; then
  fc=$(python3 experiments/runs/H-082/fixture/validator.py . 2>/dev/null | awk -F'\t' -v revs="$(git rev-list 26e9640..HEAD 2>/dev/null | tr '\n' ' ')" 'BEGIN{split(revs,a," "); for(i in a) e[a[i]]=1} NF>=2 {split($2,p,":"); if (p[1] in e) n++} END{if(n>0) printf "FRONTMATTER-CONFINEMENT=%d ", n}')
  if [ -n "$fc" ]; then
    echo "HARDEN-WARNING: frontmatter findings since the grant epoch (26e9640): $fc(run experiments/runs/H-082/fixture/validator.py . for detail)"; W=1
  fi
fi
# twelfth advisory class (H-103 kept): model-instance coincidence, one line, level-triggered
if [ -f scripts/coincidence-check.py ]; then
  co=$(python3 scripts/coincidence-check.py . 2>/dev/null | head -1)
  case "$co" in
    HARDEN-WARNING*) echo "$co"; W=1 ;;
  esac
fi
# eleventh advisory class (H-094/H-095 kept; wiring closes the matrix's stage-2 debt): un-modeled amendments
if [ -f scripts/amendment-detector.py ]; then
  model_base=$(git log -1 --format=%H -- operating-model/ 2>/dev/null)
  if [ -n "$model_base" ]; then
    am=$(python3 scripts/amendment-detector.py . "$model_base" 2>/dev/null | awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}')
    if [ -n "$am" ]; then
      echo "HARDEN-WARNING: amendments since the model last moved ($(git log -1 --format=%as -- operating-model/)): $am(scripts/amendment-detector.py . $model_base for detail; model the segment or record why not)"; W=1
    fi
  fi
fi
# ninth advisory class (client-zero dedup 2026-08-15): plugin hooks.json vs project settings wiring parity
if [ -f hooks/hooks.json ] && [ -f .claude/settings.json ]; then
  hp=""
  for tok in "intent-detector.py" "ledger-append.py" "ledger/work-ledger.jsonl" "session_resolver.py" "compile-dashboard.py"; do
    grep -q "$tok" hooks/hooks.json || hp="$hp $tok(plugin)"
    grep -q "$tok" .claude/settings.json || hp="$hp $tok(settings)"
  done
  if [ -n "$hp" ]; then
    echo "HARDEN-WARNING: hook wiring parity: hooks/hooks.json and .claude/settings.json disagree on:$hp — plugin consumers would get different behavior than client zero (experiments/reviews/unification/client-zero-proof.md)"; W=1
  fi
fi
# tenth advisory class (client-zero dedup): the skill single-home invariant — .claude/skills entries are symlinks into skills/
if [ -d .claude/skills ] && [ -d skills ]; then
  strays=$(find .claude/skills -mindepth 1 -maxdepth 1 ! -type l 2>/dev/null | wc -l | tr -d ' ')
  if [ "$strays" != "0" ]; then
    echo "HARDEN-WARNING: skill twin re-forming: $strays non-symlink entr(ies) in .claude/skills — single home is skills/ (experiments/reviews/unification/client-zero-proof.md)"; W=1
  fi
fi
if [ -f scripts/em-slice-lint.py ] && [ -f experiments/reviews/self-board/slice-board.json ]; then
  emfind=$(python3 scripts/em-slice-lint.py experiments/reviews/self-board/slice-board.json 2>/dev/null | grep -cv '^WARN-' || true)
  if [ "${emfind:-0}" != "0" ]; then
    echo "HARDEN-WARNING: EM slice-board reconciliation queue: $emfind finding(s) (scripts/em-slice-lint.py for detail; baseline at H-114 keep was 35 — GWT-empty slices, projection-less views, unmarked ellipses)"; W=1
  fi
fi
if [ -f scripts/repo-coverage-lint.py ] && [ -f experiments/runs/DESIGN-event-modeling/fixture/repo-coverage-map.json ]; then
  covtmp=$(mktemp)
  git ls-files > "$covtmp" 2>/dev/null
  covfind=$(python3 scripts/repo-coverage-lint.py "$covtmp" experiments/runs/DESIGN-event-modeling/fixture/repo-coverage-map.json 2>/dev/null | grep -cv '^WARN-' || true)
  /bin/rm -f "$covtmp"
  if [ "${covfind:-0}" != "0" ]; then
    echo "HARDEN-WARNING: repo-coverage drift: $covfind unmapped/dead finding(s) — every tracked artifact joins the map or is called out (scripts/repo-coverage-lint.py for detail; classify via the maintenance loop, fragment 0053)"; W=1
  fi
fi
if [ -f scripts/lexicon-lint.py ] && [ -d operating-model/cause-n-effect ]; then
  lexfind=$(python3 scripts/lexicon-lint.py operating-model/cause-n-effect 2>/dev/null | grep -cv '	WARN: ' || true)
  if [ "${lexfind:-0}" != "0" ]; then
    echo "HARDEN-WARNING: lexicon/definition debt: $lexfind finding(s) on the live model (scripts/lexicon-lint.py operating-model/cause-n-effect for detail; baseline at H-111 keep was 68 — mostly D4 definition-block-missing, the census's 0/51 gap made mechanical)"; W=1
  fi
fi
# Advisory 18 (name-neutrality ruling, fragment 0091): the banned third-party name is stored
# rot13-encoded so this guard never reintroduces the literal token into the tree.
nametok=$(printf 'yhpl' | tr 'A-Za-z' 'N-ZA-Mn-za-m')
namefind=$(grep -rlwi "$nametok" --exclude-dir=.git --exclude-dir=publish --exclude="*grounding-wordlist*" . 2>/dev/null | wc -l | tr -d ' ')
if [ "${namefind:-0}" != "0" ]; then
  echo "HARDEN-WARNING: name-neutrality violation: banned third-party name present in $namefind file(s) — substitute [P2]/p2 per fragment 0091 (grep -rlwi \"\$(printf 'yhpl' | tr A-Za-z N-ZA-Mn-za-m)\" for detail)"; W=1
fi
# Advisory 19 (living-dashboard contract §3): DASHBOARD.md drift guard — the terraform-docs
# regenerate-or-fail discipline in advisory form. Never blocking, like all 18 before it.
if [ -f scripts/compile-dashboard.py ] && \
   ! python3 scripts/compile-dashboard.py --check >/dev/null 2>&1; then
  echo "HARDEN-WARNING: DASHBOARD.md is stale against its sources — regenerate via scripts/compile-dashboard.py (the Stop hook normally does this; staleness here means a hook gap)"; W=1
fi
# Advisory 20 (decision-triage tracking §4, two-way-doors grant 2026-08-18): triage sidecar
# coverage drift — an open maintainer-ruling row the triage never saw, or a filed ruling
# whose file vanished from worktree and HEAD. Silent when the sidecar is absent (the
# dashboard already renders that state as untriaged: N). Never blocking, like all 19 before it.
if [ -f scripts/compile-dashboard.py ] && [ -f experiments/runs/DESIGN-decision-triage/triage.json ] && \
   ! python3 scripts/compile-dashboard.py --triage-check >/dev/null 2>&1; then
  echo "HARDEN-WARNING: decision-triage drift — open maintainer-ruling row(s) lack a triage entry, or a filed ruling's file is gone (scripts/compile-dashboard.py --triage-check for detail; re-triage per research/raw/2026-08-18-decisions-are-two-way-doors-grant.md)"; W=1
fi
# Advisory 21 (dashboard SPA loop guarantee, spa-design-contract.md §7): a maintainer
# submission from the decision board still unprocessed after the session that first
# surfaced it. hooks/surface-submissions.py --stale prints the overdue ids (it reads the
# session state the SessionStart surfacing hook maintains; it never mutates). Silent when
# the submissions file is absent. Never blocking, like all 20 before it.
if [ -f hooks/surface-submissions.py ] && [ -f experiments/reviews/dashboard/submissions.jsonl ]; then
  ss=$(python3 hooks/surface-submissions.py . --stale 2>/dev/null | tr '\n' ' ')
  if [ -n "$ss" ]; then
    echo "HARDEN-WARNING: maintainer submission(s) unprocessed for more than one session: ${ss}— the decision-board loop guarantee is breached; act on each item, then append its {\"id\", \"status\": \"processed\", \"actions\"} record to experiments/reviews/dashboard/submissions.jsonl"; W=1
  fi
fi
# Advisory 22 (multi-user contract §4, portability guard): lab-vs-plugin dashboard feature
# drift — merge-back debt (portable-pending), manifest status disagreement, or a shipped
# feature whose synced_sha no longer matches any generator-embedded plugin file (the lab
# moved and the plugin did not, or vice versa). scripts/plugin-parity-check.py prints one
# KIND<TAB>id<TAB>detail line per finding; silent + exit 0 when the manifests agree.
# Never blocking, like all 21 before it.
if [ -f scripts/plugin-parity-check.py ] && [ -f scripts/dashboard-features.json ]; then
  pp=$(python3 scripts/plugin-parity-check.py . 2>/dev/null | awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}')
  if [ -n "$pp" ]; then
    echo "HARDEN-WARNING: dashboard feature parity drift: $pp(scripts/plugin-parity-check.py . for detail; the lab-vs-plugin law is measured drift, never silence — sync the manifests or record the lab-only reason)"; W=1
  fi
fi
# Advisory 23 — submission-connectivity guard (H-153 kept, fragment 0146): the surfacing
# hook must stay registered on SessionStart + UserPromptSubmit + PreCompact; a missing
# registration reopens the measured 40-minute mid-session black hole (sub-0006).
if ! python3 scripts/check-submission-connectivity.py >/dev/null 2>&1; then
  echo "ADVISORY-23 submission-connectivity: surface-submissions.py registration incomplete — run scripts/check-submission-connectivity.py for the missing events (H-153, fragment 0146)"; W=1
fi

# ADVISORY-24 release-train state (maintainer directive 2026-08-26): the wave plan's
# progress is recomputed mechanically from committed specs at every session boundary,
# so compression, new sessions, and cold machines all resume the train from git alone.
python3 scripts/wave-status.py 2>/dev/null | head -8 || true

# ADVISORY-25 decisions-waiting (consolidated decision-making directive 2026-08-28,
# decisions-schema.md §6): open kind:"decision" ledger rows surface at every session
# boundary — count + oldest age, escalated when any is >7d old or blocks a lane. Never
# blocking, like every advisory before it. NUMBERING: 25 was the next free number at land
# time (23 + 24 were the highest on file); H-192's flow-metrics On-keep also reserved 25 —
# it lands second and takes the next free number per the whichever-lands-second-renumbers
# protocol. Pin DECISIONS_TODAY=YYYY-MM-DD for deterministic tests.
if [ -f "${DECISIONS_LEDGER:-ledger/work-ledger.jsonl}" ]; then
  dline=$(python3 - "${DECISIONS_LEDGER:-ledger/work-ledger.jsonl}" "${DECISIONS_TODAY:-}" <<'PYEOF'
import datetime, json, sys

path, pin = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
today = datetime.date.fromisoformat(pin) if pin else datetime.date.today()
decisions, closed = {}, set()
try:
    for raw in open(path, encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("kind")
        if kind == "decision" and rec.get("id"):
            decisions.setdefault(rec["id"], rec)
        elif kind == "decision-resolution" and rec.get("disposition") in ("accepted", "denied"):
            closed.add(rec.get("id"))
except OSError:
    sys.exit(0)

open_rows = [r for i, r in decisions.items() if i not in closed]
if not open_rows:
    sys.exit(0)

def age(row):
    try:
        return max(0, (today - datetime.date.fromisoformat(
            str(row.get("requested_at") or row.get("date"))[:10])).days)
    except ValueError:
        return 0

oldest = max(open_rows, key=age)
blocking = sorted({str(b) for r in open_rows for b in (r.get("blocks") or [])})
n, days = len(open_rows), age(oldest)
agestr = "new today" if days == 0 else "%dd" % days
if days > 7 or blocking:
    extra = []
    if days > 7:
        extra.append("%s has waited %s" % (oldest["id"], agestr))
    if blocking:
        extra.append("blocking: %s" % ", ".join(blocking[:4]))
    print("OPEN DECISIONS: %d (oldest %s %s) — STALLED WORK BEHIND YOU (%s) — open DASHBOARD"
          % (n, oldest["id"], agestr, "; ".join(extra)))
else:
    print("OPEN DECISIONS: %d (oldest %s %s) — open DASHBOARD" % (n, oldest["id"], agestr))
PYEOF
)
  if [ -n "$dline" ]; then
    echo "ADVISORY-25 decisions-waiting: $dline (§1 of DASHBOARD.md / decisions.html; answer: python3 scripts/decisions.py resolve <id> --accept \"<label>\" | --deny | --comment \"...\")"; W=1
  fi
fi

# ADVISORY-27 direction currency (H-243 keep, closes: direction-currency-lint): direction
# prose (program.md, wave plans, vision text) cites moving targets; the lint catches
# stale references and rename drift deterministically — report-only, bounded.
if [ -f scripts/direction-lint.py ]; then
  dl=$(timeout 45 python3 scripts/direction-lint.py . 2>/dev/null | grep -c "^DIRECTION-LINT" || true)
  if [ -n "$dl" ] && [ "$dl" != "0" ]; then
    echo "ADVISORY-27 direction-currency: $dl stale/unresolvable reference(s) in direction prose (python3 scripts/direction-lint.py . for detail)"; W=1
  fi
fi

# ADVISORY-26 vocabulary integrity (H-224 keep, closes: term-lint): a malformed
# vocabulary entry poisons every render surface, so the vocab lint runs whenever
# either copy exists — report-only, never blocks.
if [ -f scripts/clarity-lint.py ] && [ -f scripts/house-vocabulary.json ]; then
  vline=$(python3 scripts/clarity-lint.py vocab scripts/house-vocabulary.json 2>/dev/null | grep -c "FINDING" || true)
  if [ -n "$vline" ] && [ "$vline" != "0" ]; then
    echo "ADVISORY-26 vocabulary-integrity: $vline finding(s) in scripts/house-vocabulary.json (python3 scripts/clarity-lint.py vocab scripts/house-vocabulary.json for detail)"; W=1
  fi
fi

[ "$W" -eq 0 ] && echo "harden-check: clean (governance in sync, program.md current, tree clean, pushed)"
:
}
# Cache-first execution (2026-09-01, the hook-timeout incident, fragment 0235):
# the full advisory suite measures ~2 minutes — inline it killed every headless
# child at the SessionStart hook timeout. A hook invocation now prints the last
# cached advisory instantly and refreshes the cache in a DETACHED background run;
# `--fresh` forces the old inline behavior (CI, manual audits). Staleness is
# disclosed in the output, never silent.
CACHE=.claude/harden-last.txt
if [ "${1:-}" != "--fresh" ] && [ "${HARDEN_INLINE:-}" != "1" ] && [ -f "$CACHE" ]; then
  age=$(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) ))
  printf '%s\n' "$(cat "$CACHE")"
  echo "HARDEN-CACHE: advisory snapshot ${age}s old — refreshing in background (bash scripts/harden-check.sh --fresh for live)"
  ( HARDEN_INLINE=1 nohup bash "$0" --fresh >/dev/null 2>&1 & ) 2>/dev/null
  exit 0
fi
out=$(main)
printf '%s\n' "$out"
printf '%s\n' "$out" >| .claude/harden-last.txt 2>/dev/null || true
exit 0
