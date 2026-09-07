#!/bin/sh
# Passive hardening check — wired to SessionStart and PreCompact hooks.
# Prints warnings (which land in session context); exit 0 always (advisory, not blocking).
# The advisory body lives in main(); its stdout is captured once and then BOTH printed and
# written to .claude/harden-last.txt (the dashboard's advisory cache, session-local untracked
# infra like .claude/stop-snooze). Capture-then-write rather than a live tee pipe: advisory 19
# below runs compile-dashboard.py --check, whose source digest covers the cache file — a tee
# would have already truncated it mid-run and every session would read spuriously stale.
# Plugin-rooted (consumer parity G1): every scripts/<name> below resolves under the plugin
# root -- $CLAUDE_PLUGIN_ROOT in a consumer install, this file's parent directory in the lab
# -- while the advisories inspect the repo the session works in: the cwd's git toplevel
# (worktree-aware, the same checkout hyp_config.resolve_root picks), else CLAUDE_PROJECT_DIR,
# else the parent directory (the pre-parity form). Lab-only linters are skipped where their
# script is absent under the plugin root; the advisory cache is written only where .claude/
# exists. Advisory logic, thresholds, and wording are unchanged.
# Scale guard (consumer parity G1, successor lane): the two whole-tree scans below — advisory
# 18's name grep and ADVISORY-27's direction lint — measure ~41 s and ~11 s on a 35k-file
# consumer, more than a SessionStart hook may spend inline. They run when the tree is small
# (tracked files <= HARDEN_TREE_MAX, default 2000) or when an advisory cache already exists
# (the cache-first background refresh, a manual --fresh after the first session); the cold
# first run in a large tree defers them and says so on one HARDEN-SKIP line.
HERE=$(cd "$(dirname "$0")/.." && pwd)
S="${CLAUDE_PLUGIN_ROOT:-$HERE}/scripts"
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "${CLAUDE_PROJECT_DIR:-$HERE}")" || exit 0
# Block budget (consumer gap G11, lab H-DRAFT-38db3701): one refresh on a real consumer ran
# 12-35 min, every sampled second in the unbounded name grep. ht <block> skips a block once
# HARDEN_TOTAL_MAX s (default 600) have elapsed since main() started; hb <block> <cmd...> also
# runs the block's command under timeout HARDEN_BLOCK_MAX (default 120) with its stdout in
# $hb_out and its rc in $hb_rc -- a command killed at the ceiling (rc 124) has its output
# DISCARDED (never read as zero findings) and the block is named on one HARDEN-PARTIAL line.
# Blocks that already carry their own timeout keep it and pass only ht. Both return 1 when the
# block is skipped, so a block reads `guard && hb <block> <cmd>` and prints nothing when skipped.
hp() { case " $partial " in *" $1("*) return 0 ;; esac; partial="$partial $1($2)"; pn=$((pn + 1)); }
ht() { if [ $(( $(date +%s) - HARDEN_T0 )) -ge "${HARDEN_TOTAL_MAX:-600}" ]; then hp "$1" over-total; return 1; fi; }
hb() { hb_name=$1; shift; ht "$hb_name" || return 1; timeout "${HARDEN_BLOCK_MAX:-120}" "$@" > "$hb_out" 2>/dev/null; hb_rc=$?; if [ "$hb_rc" -eq 124 ]; then hp "$hb_name" over-budget; return 1; fi; return 0; }
main() {
W=0
HARDEN_T0=$(date +%s); partial=""; pn=0; hb_rc=0; hb_out=$(mktemp)
ntracked=$(git ls-files 2>/dev/null | wc -l | tr -d ' ')
if [ -f .claude/harden-last.txt ] || [ "${ntracked:-0}" -le "${HARDEN_TREE_MAX:-2000}" ]; then SCAN=1; else SCAN=0; fi
skipped=""
if [ -f "$S/check-governance-drift.py" ] && hb governance-drift python3 "$S/check-governance-drift.py" && [ "$hb_rc" -ne 0 ]; then
  echo "HARDEN-WARNING: GOVERNANCE drift — CLAUDE.md mirror differs from kernel canonical (run scripts/check-governance-drift.py)"; W=1
fi
latest_h=$(ls hypotheses/H-*.md 2>/dev/null | sed 's/.*H-\([0-9]*\).*/\1/' | sort -n | tail -1)
# row-based: the id must appear in a status-table row's ID cell — a mention elsewhere
# (e.g. another row's "refined-into H-NNN") must not mask a deleted row (H-077's finding)
if [ -n "$latest_h" ] && [ -f program.md ] && ! { grep -E '^\|' program.md | cut -d'|' -f2 | grep -Eq "H-0*${latest_h}([^0-9]|$)"; }; then
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
if [ -f "$S/commitment-lint.py" ] && hb commitments python3 "$S/commitment-lint.py" .; then
  cm=$(awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}' < "$hb_out")
  if [ -n "$cm" ]; then
    echo "HARDEN-WARNING: commitment findings: $cm(commitment-lint for detail; close via evidence or rewrite with closes-when)"; W=1
  fi
fi
# journal freeze (M5 ruled 2026-08-15): volume 1 is byte-frozen; entries live in fragments
if [ -f "$S/journal-freeze.sha" ] && [ -f experiments/journal.md ]; then
  cur=$(shasum -a 256 experiments/journal.md | awk '{print $1}')
  if [ "$cur" != "$(cat "$S/journal-freeze.sha")" ]; then
    echo "HARDEN-WARNING: experiments/journal.md changed after the M5 freeze — volume 1 is byte-frozen; new entries belong in journal-fragments/"; W=1
  fi
fi
# sixth advisory class (H-092/H-093 kept): stale/unwired derived claims, one line
if [ -f "$S/claim-lint.py" ] && hb derived-claims python3 "$S/claim-lint.py" . --check; then
  cc=$(awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}' < "$hb_out")
  if [ -n "$cc" ]; then
    echo "HARDEN-WARNING: derived-claim findings: $cc(claim_tool --check for detail; refresh via --fix in an attributed commit)"; W=1
  fi
fi
# fifth advisory class (H-080 kept): corpus staleness summary, one line, never blocking
if [ -f "$S/corpus-lint.py" ] && ht corpus-lint; then
  cl=$(timeout 45 python3 "$S/corpus-lint.py" . 2>/dev/null | awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}')
  if [ -n "$cl" ]; then
    echo "HARDEN-WARNING: corpus-lint findings: $cl(run scripts/corpus-lint.py . for detail; triage via doc-factoring tier)"; W=1
  fi
fi
# fourteenth advisory class (H-104 kept): open move manifests — declared, never verified
if [ -f "$S/fidelity-manifest.py" ] && [ -d manifests ] && hb manifests python3 "$S/fidelity-manifest.py" . --open; then
  fm=$(grep -c "^UNVERIFIED-MANIFEST" < "$hb_out" | tr -d ' ')
  if [ -n "$fm" ] && [ "$fm" != "0" ]; then
    echo "HARDEN-WARNING: $fm open move manifest(s) awaiting verification (scripts/fidelity-manifest.py . --open — verify after executing, or --abandon with a reason)"; W=1
  fi
fi
# thirteenth advisory class (H-070/H-082 kept): frontmatter confinement, epoch-anchored at the
# grant commit 26e9640 (pre-grant history is not actionable and stays excluded)
if [ -f experiments/runs/H-082/fixture/validator.py ] && git rev-parse -q --verify 26e9640 >/dev/null 2>&1 && hb frontmatter python3 experiments/runs/H-082/fixture/validator.py .; then
  fc=$(awk -F'\t' -v revs="$(git rev-list 26e9640..HEAD 2>/dev/null | tr '\n' ' ')" 'BEGIN{split(revs,a," "); for(i in a) e[a[i]]=1} NF>=2 {split($2,p,":"); if (p[1] in e) n++} END{if(n>0) printf "FRONTMATTER-CONFINEMENT=%d ", n}' < "$hb_out")
  if [ -n "$fc" ]; then
    echo "HARDEN-WARNING: frontmatter findings since the grant epoch (26e9640): $fc(run experiments/runs/H-082/fixture/validator.py . for detail)"; W=1
  fi
fi
# twelfth advisory class (H-103 kept): model-instance coincidence, one line, level-triggered
if [ -f "$S/coincidence-check.py" ] && hb coincidence python3 "$S/coincidence-check.py" .; then
  co=$(head -1 < "$hb_out")
  case "$co" in
    HARDEN-WARNING*) echo "$co"; W=1 ;;
  esac
fi
# eleventh advisory class (H-094/H-095 kept; wiring closes the matrix's stage-2 debt): un-modeled amendments
if [ -f "$S/amendment-detector.py" ]; then
  model_base=$(git log -1 --format=%H -- operating-model/ 2>/dev/null)
  if [ -n "$model_base" ] && hb amendments python3 "$S/amendment-detector.py" . "$model_base"; then
    am=$(awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}' < "$hb_out")
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
if [ -f "$S/em-slice-lint.py" ] && [ -f experiments/reviews/self-board/slice-board.json ] && hb em-slice python3 "$S/em-slice-lint.py" experiments/reviews/self-board/slice-board.json; then
  emfind=$(grep -cv '^WARN-' < "$hb_out" || true)
  if [ "${emfind:-0}" != "0" ]; then
    echo "HARDEN-WARNING: EM slice-board reconciliation queue: $emfind finding(s) (scripts/em-slice-lint.py for detail; baseline at H-114 keep was 35 — GWT-empty slices, projection-less views, unmarked ellipses)"; W=1
  fi
fi
if [ -f "$S/repo-coverage-lint.py" ] && [ -f experiments/runs/DESIGN-event-modeling/fixture/repo-coverage-map.json ]; then
  covtmp=$(mktemp)
  git ls-files > "$covtmp" 2>/dev/null
  covfind=0
  if hb repo-coverage python3 "$S/repo-coverage-lint.py" "$covtmp" experiments/runs/DESIGN-event-modeling/fixture/repo-coverage-map.json; then covfind=$(grep -cv '^WARN-' < "$hb_out" || true); fi
  /bin/rm -f "$covtmp"
  if [ "${covfind:-0}" != "0" ]; then
    echo "HARDEN-WARNING: repo-coverage drift: $covfind unmapped/dead finding(s) — every tracked artifact joins the map or is called out (scripts/repo-coverage-lint.py for detail; classify via the maintenance loop, fragment 0053)"; W=1
  fi
fi
if [ -f "$S/lexicon-lint.py" ] && [ -d operating-model/cause-n-effect ] && hb lexicon python3 "$S/lexicon-lint.py" operating-model/cause-n-effect; then
  lexfind=$(grep -cv '	WARN: ' < "$hb_out" || true)
  if [ "${lexfind:-0}" != "0" ]; then
    echo "HARDEN-WARNING: lexicon/definition debt: $lexfind finding(s) on the live model (scripts/lexicon-lint.py operating-model/cause-n-effect for detail; baseline at H-111 keep was 68 — mostly D4 definition-block-missing, the census's 0/51 gap made mechanical)"; W=1
  fi
fi
# Advisory 18 (name-neutrality ruling, fragment 0091): the banned third-party name is stored
# rot13-encoded so this guard never reintroduces the literal token into the tree.
if [ "$SCAN" = 1 ]; then
nametok=$(printf 'yhpl' | tr 'A-Za-z' 'N-ZA-Mn-za-m')
if hb name-neutrality grep -rlwi "$nametok" --exclude-dir=.git --exclude-dir=publish --exclude="*grounding-wordlist*" .; then
namefind=$(wc -l < "$hb_out" | tr -d ' ')
if [ "${namefind:-0}" != "0" ]; then
  echo "HARDEN-WARNING: name-neutrality violation: banned third-party name present in $namefind file(s) — substitute [P2]/p2 per fragment 0091 (grep -rlwi \"\$(printf 'yhpl' | tr A-Za-z N-ZA-Mn-za-m)\" for detail)"; W=1
fi
fi
else skipped="$skipped name-neutrality"; fi
# Advisory 19 (living-dashboard contract §3): DASHBOARD.md drift guard — the terraform-docs
# regenerate-or-fail discipline in advisory form. Never blocking, like all 18 before it.
if [ -f "$S/compile-dashboard.py" ] && \
   hb dashboard-stale python3 "$S/compile-dashboard.py" --check && [ "$hb_rc" -ne 0 ]; then
  echo "HARDEN-WARNING: DASHBOARD.md is stale against its sources — regenerate via scripts/compile-dashboard.py (the Stop hook normally does this; staleness here means a hook gap)"; W=1
fi
# Advisory 20 (decision-triage tracking §4, two-way-doors grant 2026-08-18): triage sidecar
# coverage drift — an open maintainer-ruling row the triage never saw, or a filed ruling
# whose file vanished from worktree and HEAD. Silent when the sidecar is absent (the
# dashboard already renders that state as untriaged: N). Never blocking, like all 19 before it.
# Capability guard (consumer gap G13, lab H-DRAFT-45585281): the plugin's portable
# compile-dashboard.py carries no --triage-check branch and renders DASHBOARD.md and
# decisions.html on any flag it does not know -- a write from a read-only advisory. The
# block runs only where the script under $S names the flag in its own source (a fixed-string
# grep; no interpreter is spawned to find out); elsewhere it is skipped silently, like every
# lab-only helper above.
if [ -f "$S/compile-dashboard.py" ] && [ -f experiments/runs/DESIGN-decision-triage/triage.json ] && \
   grep -qF -e "--triage-check" "$S/compile-dashboard.py" && \
   hb triage-drift python3 "$S/compile-dashboard.py" --triage-check && [ "$hb_rc" -ne 0 ]; then
  echo "HARDEN-WARNING: decision-triage drift — open maintainer-ruling row(s) lack a triage entry, or a filed ruling's file is gone (scripts/compile-dashboard.py --triage-check for detail; re-triage per research/raw/2026-08-18-decisions-are-two-way-doors-grant.md)"; W=1
fi
# Advisory 21 (dashboard SPA loop guarantee, spa-design-contract.md §7): a maintainer
# submission from the decision board still unprocessed after the session that first
# surfaced it. hooks/surface-submissions.py --stale prints the overdue ids (it reads the
# session state the SessionStart surfacing hook maintains; it never mutates). Silent when
# the submissions file is absent. Never blocking, like all 20 before it.
if [ -f hooks/surface-submissions.py ] && [ -f experiments/reviews/dashboard/submissions.jsonl ] && hb submissions python3 hooks/surface-submissions.py . --stale; then
  ss=$(tr '\n' ' ' < "$hb_out")
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
if [ -f "$S/plugin-parity-check.py" ] && [ -f "$S/dashboard-features.json" ] && hb feature-parity python3 "$S/plugin-parity-check.py" .; then
  pp=$(awk -F'\t' '{n[$1]++} END {for (k in n) printf "%s=%d ", k, n[k]}' < "$hb_out")
  if [ -n "$pp" ]; then
    echo "HARDEN-WARNING: dashboard feature parity drift: $pp(scripts/plugin-parity-check.py . for detail; the lab-vs-plugin law is measured drift, never silence — sync the manifests or record the lab-only reason)"; W=1
  fi
fi
# Advisory 23 — submission-connectivity guard (H-153 kept, fragment 0146): the surfacing
# hook must stay registered on SessionStart + UserPromptSubmit + PreCompact; a missing
# registration reopens the measured 40-minute mid-session black hole (sub-0006).
if [ -f "$S/check-submission-connectivity.py" ] && hb advisory-23 python3 "$S/check-submission-connectivity.py" && [ "$hb_rc" -ne 0 ]; then
  echo "ADVISORY-23 submission-connectivity: surface-submissions.py registration incomplete — run scripts/check-submission-connectivity.py for the missing events (H-153, fragment 0146)"; W=1
fi

# ADVISORY-24 release-train state (maintainer directive 2026-08-26): the wave plan's
# progress is recomputed mechanically from committed specs at every session boundary,
# so compression, new sessions, and cold machines all resume the train from git alone.
if [ -f "$S/wave-status.py" ] && hb wave-status python3 "$S/wave-status.py"; then head -8 < "$hb_out"; fi

# ADVISORY-25 decisions-waiting (consolidated decision-making directive 2026-08-28,
# decisions-schema.md §6): open kind:"decision" ledger rows surface at every session
# boundary — count + oldest age, escalated when any is >7d old or blocks a lane. Never
# blocking, like every advisory before it. NUMBERING: 25 was the next free number at land
# time (23 + 24 were the highest on file); H-192's flow-metrics On-keep also reserved 25 —
# it lands second and takes the next free number per the whichever-lands-second-renumbers
# protocol. Pin DECISIONS_TODAY=YYYY-MM-DD for deterministic tests.
if [ -f "${DECISIONS_LEDGER:-ledger/work-ledger.jsonl}" ]; then
  dline=""
  if hb decisions-waiting python3 - "${DECISIONS_LEDGER:-ledger/work-ledger.jsonl}" "${DECISIONS_TODAY:-}" <<'PYEOF'
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
  then dline=$(cat "$hb_out"); fi
  if [ -n "$dline" ]; then
    echo "ADVISORY-25 decisions-waiting: $dline (§1 of DASHBOARD.md / decisions.html; answer: python3 scripts/decisions.py resolve <id> --accept \"<label>\" | --deny | --comment \"...\")"; W=1
  fi
fi

# ADVISORY-30 flow leak (H-246 keep, closes: flow-leak-meter-ships): the counted
# answer to "minutes-work taking days" — alarmed 4-21h before the maintainer's catch
# on all three held-out episodes. Read-only, bounded, count-only line.
if [ -x "$S/leak-status.sh" ] && ht advisory-30; then
  fl=$(timeout 50 bash "$S/leak-status.sh" 2>/dev/null | grep "^FLOW" | tail -1)
  case "$fl" in
    *ALARM*|*BURN*) echo "ADVISORY-30 flow-leak: $fl (bash scripts/leak-status.sh for the full reading; the reflex chain escalates unconsumed alarms)"; W=1 ;;
  esac
fi

# ADVISORY-31 meter-consumption (throughput-floor directive 2026-09-02,
# research/raw/2026-09-02-throughput-floor-directive.md): the meter MEASURED stalls
# but nothing CONSUMED the alarm — 5 BURN-SLOW fires on 2026-09-02, all "acted": null,
# one action total. A BURN/ALARM fire >30m old with no consumption record surfaces
# here; the script also lands ONE H-253 fixture-side incident row per fire
# (ledger/incident-records.jsonl, DEC-013 pending — never the work ledger).
# Count-only, bounded, exit-0.
if [ -f "$S/reflex-consume.py" ] && ht advisory-31; then
  mc=$(timeout 45 python3 "$S/reflex-consume.py" . 2>/dev/null | grep -c "^CONSUMPTION-DUE" || true)
  if [ -n "$mc" ] && [ "$mc" != "0" ]; then
    echo "ADVISORY-31 meter-consumption: $mc unconsumed meter fire(s) >30m old (python3 scripts/reflex-consume.py . for detail; consume: scripts/reflex-consume.py . --record <fire-ts> --action \"<commit/lane>\")"; W=1
  fi
fi

# ADVISORY-27 direction currency (H-243 keep, closes: direction-currency-lint): direction
# prose (program.md, wave plans, vision text) cites moving targets; the lint catches
# stale references and rename drift deterministically — report-only, bounded.
if [ -f "$S/direction-lint.py" ] && [ "$SCAN" = 1 ] && ht advisory-27; then
  dl=$(timeout 45 python3 "$S/direction-lint.py" . 2>/dev/null | grep -c "^DIRECTION-LINT" || true)
  if [ -n "$dl" ] && [ "$dl" != "0" ]; then
    echo "ADVISORY-27 direction-currency: $dl stale/unresolvable reference(s) in direction prose (python3 scripts/direction-lint.py . for detail)"; W=1
  fi
elif [ -f "$S/direction-lint.py" ] && [ "$SCAN" != 1 ]; then skipped="$skipped direction-currency"
fi

# ADVISORY-26 vocabulary integrity (H-224 keep, closes: term-lint): a malformed
# vocabulary entry poisons every render surface, so the vocab lint runs whenever
# either copy exists — report-only, never blocks.
if [ -f "$S/clarity-lint.py" ] && [ -f "$S/house-vocabulary.json" ] && hb advisory-26 python3 "$S/clarity-lint.py" vocab "$S/house-vocabulary.json"; then
  vline=$(grep -c "FINDING" < "$hb_out" || true)
  if [ -n "$vline" ] && [ "$vline" != "0" ]; then
    echo "ADVISORY-26 vocabulary-integrity: $vline finding(s) in scripts/house-vocabulary.json (python3 scripts/clarity-lint.py vocab scripts/house-vocabulary.json for detail)"; W=1
  fi
fi

# ADVISORY-28 rule-currency (H-248 keep, closes: rule-lint-ships): the four-class
# currency lint over ledger/rules-registry.jsonl — RULE-EXPIRED / RULE-UNLICENSED /
# RULE-ORPHAN / SCOPE-EXCESS. The lint reads a corpus root (frozen contract), so this
# block assembles one under .claude/ from live surfaces: pinned as-of date, the live
# registry, intent/re-earn stores when present, pinned-tree -> the repo. Report-only,
# never blocks. RULE-EXPIRED findings are the mechanical trigger for the rule-retest
# flow (H-249): file the retest via decisions.py add --class rule-retest.
if [ -f "$S/rule-lint.py" ] && [ -f ledger/rules-registry.jsonl ] && ht advisory-28; then
  rlc=.claude/rule-lint-corpus
  mkdir -p "$rlc" 2>/dev/null || true
  printf '%s\n' "${RULE_LINT_TODAY:-$(date +%F)}" > "$rlc/as-of-date.txt" 2>/dev/null || true
  ln -sfn ../../ledger/rules-registry.jsonl "$rlc/rules-registry.jsonl" 2>/dev/null || true
  [ -f ledger/retest-intents.jsonl ] && ln -sfn ../../ledger/retest-intents.jsonl "$rlc/retest-intents.jsonl" 2>/dev/null
  [ -f ledger/re-earn-evidence.jsonl ] && ln -sfn ../../ledger/re-earn-evidence.jsonl "$rlc/re-earn-evidence.jsonl" 2>/dev/null
  ln -sfn ../.. "$rlc/pinned-tree" 2>/dev/null || true
  rl=$(timeout 45 python3 "$S/rule-lint.py" "$rlc" 2>/dev/null | grep -cE '^(RULE-[A-Z]+|SCOPE-EXCESS)' || true)
  if [ -n "$rl" ] && [ "$rl" != "0" ]; then
    echo "ADVISORY-28 rule-currency: $rl finding(s) over ledger/rules-registry.jsonl (python3 scripts/rule-lint.py $rlc for detail; RULE-EXPIRED feeds the H-249 retest flow: decisions.py add --class rule-retest)"; W=1
  fi
fi

# ADVISORY-29 laws-drift (H-247 keep, closes: laws-drift-advisory): compiled-carrier
# drift on the same channel as the parity advisories — registry field/license health
# always (lint-registry at the meta pin), plus LAWS-DRIFT checks on every templated
# carrier whose source file exists in this tree. Report-only, never blocks.
if [ -f "$S/compile-laws.py" ] && [ -f ledger/rules-registry.jsonl ] && ht advisory-29; then
  rdef=$(timeout 45 python3 "$S/compile-laws.py" lint-registry --registry ledger/rules-registry.jsonl --repo . 2>/dev/null | grep -c "	DEFECT	" || true)
  drift=0
  pairs=$(python3 -c "
import json,sys
meta=None
for ln in open('ledger/rules-registry.jsonl',encoding='utf-8'):
    ln=ln.strip()
    if not ln: continue
    d=json.loads(ln)
    if d.get('kind')=='meta': meta=d; break
for cid,t in sorted((meta or {}).get('carrier_templates',{}).items()):
    src=t.get('source')
    if src: print('%s\t%s'%(cid,src))
" 2>/dev/null || true)
  if [ -n "$pairs" ]; then
    while IFS="$(printf '\t')" read -r cid src; do
      [ -f "$src" ] || continue
      d=$(timeout 45 python3 "$S/compile-laws.py" check --registry ledger/rules-registry.jsonl --carrier "$src" --carrier-id "$cid" 2>/dev/null | grep -c "^LAWS-DRIFT" || true)
      drift=$((drift + ${d:-0}))
    done <<RLEOF
$pairs
RLEOF
  fi
  if [ "${rdef:-0}" != "0" ] || [ "$drift" != "0" ]; then
    echo "ADVISORY-29 laws-drift: ${rdef:-0} registry defect(s), $drift carrier drift finding(s) (scripts/compile-laws.py lint-registry / check for detail; carriers compile from the registry — never hand-retype a LAWS block)"; W=1
  fi
fi

# ADVISORY-32 hook-wiring parity (H-DRAFT-4c0dadb8-hook-wiring-parity): script bytes and
# dashboard features are parity-checked (advisory 22) but hook WIRING was not, and it
# drifted silently — guards that run on only one side of the lab-plugin boundary. The
# lint normalizes .claude/settings.json and hooks/hooks.json into (event, matcher, guard)
# rows and prints one HOOK-PARITY line per one-side-only row; this block prints the
# count. Report-only, never blocks. Pin HOOK_PARITY_SETTINGS / HOOK_PARITY_HOOKS to
# compare a different pair (tests, consumer installs).
if [ -f "$S/hook-parity-check.py" ] && [ -f "${HOOK_PARITY_SETTINGS:-.claude/settings.json}" ] && [ -f "${HOOK_PARITY_HOOKS:-hooks/hooks.json}" ] && hb advisory-32 python3 "$S/hook-parity-check.py" "${HOOK_PARITY_SETTINGS:-.claude/settings.json}" "${HOOK_PARITY_HOOKS:-hooks/hooks.json}"; then
  hw=$(grep -c "^HOOK-PARITY" < "$hb_out" || true)
  if [ -n "$hw" ] && [ "$hw" != "0" ]; then
    echo "ADVISORY-32 hook-wiring-parity: $hw hook guard row(s) run on only one side of the lab-plugin boundary (python3 scripts/hook-parity-check.py ${HOOK_PARITY_SETTINGS:-.claude/settings.json} ${HOOK_PARITY_HOOKS:-hooks/hooks.json} for detail; port each guard or record it as one-side-only)"; W=1
  fi
fi

# ADVISORY-33 keeps-unshipped (H-DRAFT-3e26af94-lab-plugin-keep-ships-gate): a keep whose
# ON arm changed plugin-shipped bytes can close its ledger row with a lab-only commit and
# sit unshipped for weeks (H-148: 13 days). The gate joins each kept lane's committed
# VERDICT.json files_changed_in_on to the deploy tree and to a committed
# experiments/runs/<id>/SHIP.md carrying a pr: line, and prints one KEEP-UNSHIPPED line
# per unshipped keep; this block prints the count. Plugin-rooted (CLAUDE_PLUGIN_ROOT when
# set, else this tree) so a consumer install runs the shipped copy. Report-only, never blocks.
g="${CLAUDE_PLUGIN_ROOT:-.}/scripts/keep-ship-gate.py"
if [ -f "$g" ] && hb advisory-33 python3 "$g" .; then
  ku=$(grep -c "^KEEP-UNSHIPPED" < "$hb_out" || true)
  if [ -n "$ku" ] && [ "$ku" != "0" ]; then
    echo "ADVISORY-33 keeps-unshipped: $ku kept lane(s) changed plugin-shipped bytes with no committed SHIP.md carrying a pr: line (python3 $g . for detail; ship through the changeset flow, then commit experiments/runs/<id>/SHIP.md with 'pr: <n>')"; W=1
  fi
fi

# ADVISORY-34 branch-without-pr (lab DESIGN-durability-gaps, why-no-pr.md; H-DRAFT-ee81f74d-branch-without-pr-advisory):
# a pushed worktree branch is "pushed" to the advisory above yet invisible to review — 52 commits sat on
# one lab branch with no PR (2026-09-04..06) while sibling worktrees had drafts. Fires when HEAD is a
# non-default branch >= HARDEN_PR_MIN (default 1) commits ahead of origin/<default> and gh reports no
# open PR whose head is this branch. Silent on the default branch, detached HEAD, no gh on PATH, or
# HARDEN_PR_CHECK=0. The gh call is pinned to the remote owner's account first (gh auth token --user
# <owner>; the active account may not see that remote) and falls back to the active account; if both
# fail it says so rather than hide. Repo-rooted (H-DRAFT-40ec0bc2): every git/gh call reads the
# repository the session works in.
if [ "${HARDEN_PR_CHECK:-1}" != "0" ] && command -v gh >/dev/null 2>&1 && ht advisory-34; then
  br=$(git symbolic-ref -q --short HEAD 2>/dev/null)
  def=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); def=${def:-main}
  if [ -n "$br" ] && [ "$br" != "$def" ] && git rev-parse -q --verify "origin/$def" >/dev/null 2>&1; then
    ahead=$(git rev-list --count "origin/$def..HEAD" 2>/dev/null || echo 0)
    if [ "${ahead:-0}" -ge "${HARDEN_PR_MIN:-1}" ]; then
      owner=$(git remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#')
      n=""
      if [ -n "$owner" ]; then
        tok=$(gh auth token --user "$owner" 2>/dev/null)
        [ -n "$tok" ] && n=$(GH_TOKEN="$tok" timeout 8 gh pr list --head "$br" --state open --json number --jq 'length' 2>/dev/null)
      fi
      [ -z "$n" ] && n=$(timeout 8 gh pr list --head "$br" --state open --json number --jq 'length' 2>/dev/null)
      case "$n" in
        '') echo "ADVISORY-34 branch-without-pr: $br is $ahead commit(s) ahead of origin/$def and gh could not read pull requests for $owner (gh auth switch --user $owner; or HARDEN_PR_CHECK=0)"; W=1 ;;
        0)  echo "ADVISORY-34 branch-without-pr: $br is $ahead commit(s) ahead of origin/$def with no open pull request — branch work is durable only once a PR carries it toward $def (gh pr create --draft --base $def --head $br; or HARDEN_PR_CHECK=0)"; W=1 ;;
      esac
    fi
  fi
fi
if [ -n "$skipped" ]; then
  echo "HARDEN-SKIP: whole-tree scan(s) deferred to the cached refresh:$skipped ($ntracked tracked files > ${HARDEN_TREE_MAX:-2000}, no advisory cache yet; bash scripts/harden-check.sh --fresh after this session for the full reading)"; W=1
fi
if [ -n "$partial" ]; then
  echo "HARDEN-PARTIAL: $pn block(s) skipped:$partial (bash scripts/harden-check.sh --fresh with HARDEN_BLOCK_MAX/HARDEN_TOTAL_MAX raised for the full reading)"; W=1
fi
/bin/rm -f "$hb_out"
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
# Single-flight refresh (consumer gap G9): N concurrent session starts on one repo spawn at
# most ONE detached refresh. The lock is a directory (mkdir is atomic on every POSIX fs;
# macOS ships no flock) holding the refresh's pid and start epoch. Stale-lock rule: a lock
# whose holder pid is dead, or whose pid file is still absent after HARDEN_LOCK_GRACE s, or
# which is older than HARDEN_LOCK_MAX s, is reclaimed. The reclaim itself is single-flight:
# only the invocation that wins a second mkdir-atomic token ($LOCK.reclaim) removes the stale
# lock, so a straggler that judged the OLD lock stale can never remove its successor; the
# token is released by the refresh's exit (owner-checked, like the lock) or by age. Foreground
# output is unchanged.
LOCK=.claude/harden-refresh.lock
if [ "${1:-}" != "--fresh" ] && [ "${HARDEN_INLINE:-}" != "1" ] && [ -f "$CACHE" ]; then
  age=$(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) ))
  printf '%s\n' "$(cat "$CACHE")"
  echo "HARDEN-CACHE: advisory snapshot ${age}s old — refreshing in background (bash scripts/harden-check.sh --fresh for live)"
  lnow=$(date +%s); lgrace=${HARDEN_LOCK_GRACE:-60}
  if [ -d "$LOCK.reclaim" ] && [ $(( lnow - $(stat -f %m "$LOCK.reclaim" 2>/dev/null || echo "$lnow") )) -gt "$lgrace" ]; then
    rmdir "$LOCK.reclaim" 2>/dev/null
  fi
  if [ -d "$LOCK" ]; then
    lage=$(( lnow - $(stat -f %m "$LOCK" 2>/dev/null || echo "$lnow") ))
    lpid=$(sed -n 1p "$LOCK/pid" 2>/dev/null)
    lstale=0
    if [ -n "$lpid" ]; then kill -0 "$lpid" 2>/dev/null || lstale=1
    elif [ "$lage" -gt "$lgrace" ]; then lstale=1; fi
    [ "$lage" -gt "${HARDEN_LOCK_MAX:-1800}" ] && lstale=1
    if [ "$lstale" -eq 1 ] && mkdir "$LOCK.reclaim" 2>/dev/null; then rm -rf "$LOCK"; fi
  fi
  if mkdir "$LOCK" 2>/dev/null; then
    ( HARDEN_INLINE=1 HARDEN_LOCK="$LOCK" nohup bash "$S/harden-check.sh" --fresh >/dev/null 2>&1 & ) 2>/dev/null
  fi
  exit 0
fi
if [ -n "${HARDEN_LOCK:-}" ] && [ -d "$HARDEN_LOCK" ]; then
  printf '%s\n%s\n' "$$" "$(date +%s)" > "$HARDEN_LOCK/pid" 2>/dev/null
  trap '[ "$(sed -n 1p "$HARDEN_LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$HARDEN_LOCK" "$HARDEN_LOCK.reclaim"' EXIT
fi
out=$(main)
printf '%s\n' "$out"
[ -d .claude ] && printf '%s\n' "$out" >| .claude/harden-last.txt 2>/dev/null || true
exit 0
