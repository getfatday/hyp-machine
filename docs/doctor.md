# The environment-health doctor

Two scripts that turn a degraded-credential mystery into a typed state with exactly one
next step: `scripts/doctor-classify.py` (detection) and `scripts/doctor-remediate.py`
(remediation). Both are stdlib-only and deterministic; the classifier writes no files,
the remediator is a pure function of its arguments.

## The flap signature

A headless `claude -p` child that dies at startup with `Not logged in · Please run /login`
(`is_error: true`, `total_cost_usd: 0`) usually does NOT mean the credentials are gone —
it means the per-session OAuth refresh call flapped. The commissioned root-cause research
(captured in the source lab as `research/raw/2026-08-25-oauth-refresh-deep-research.md`)
established the load-bearing facts the doctor's frozen rules encode:

- `expiresAt: 0` in the credentials file is a **documented refresh sentinel**, never a
  defect signal on its own.
- The flap IS the per-session refresh failing (single-use refresh token, racing sessions);
  `/login` heals it only until the freshly minted access token expires.
- The measured degradation this instrument was built against: 41 of 80 gate probes failing
  (51%) across a sustained evening window, load-independent — while the credential surface
  looked superficially healthy.

Re-running `/login` on every failure is therefore the wrong reflex; classify first, then
take the ladder's step for the typed state.

## Classify

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor-classify.py" stream.jsonl [more.jsonl ...]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor-classify.py" --live [--probe-json FILE]
```

Replay mode reads recorded credential-surface streams (JSONL: probe records with a boolean
`ok`, snapshot records with `expiresAt`/`refreshTokenExpiresAt`, event markers) and emits
one typed verdict line per record plus a final line. Live mode takes a read-only snapshot
of the credentials file (and, on macOS, the keychain lock state) and classifies it with the
same frozen rules. The exit status is the typed verdict:

| Verdict | Exit | Meaning (frozen rule) |
|---|---|---|
| `CLEAN` | 0 | 3 consecutive passing probes, or a passing probe under the `expiresAt=0` sentinel |
| `FLAP-DEGRADED` | 10 | >= 3 fresh-probe auth failures within 30 minutes across >= 2 distinct invocations |
| `HARD-EXPIRED` | 11 | access AND refresh expiries both past with a failing probe (precedence over flap) |
| `INDETERMINATE` | 12 | insufficient probe evidence — the honest single-shot verdict |

(64 = usage error, 65 = unreadable input.)

## Remediate

Feed the typed state to the ladder; it emits exactly one next step with a stable exit code:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor-remediate.py" step --state FLAP-DEGRADED \
    [--version-fit passed|failed|unknown]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor-remediate.py" verify --state <STATE> \
    --probe-record <path>
```

| State | Next step | Exit |
|---|---|---|
| `CLEAN` | no action | 0 |
| `FLAP-DEGRADED`, version-fit **passed** | token path: `CLAUDE_CODE_OAUTH_TOKEN` (minted once via `claude setup-token`) exported per-invocation, then re-probe | 10 |
| `FLAP-DEGRADED`, version-fit failed/unknown | partial mitigation: `/login` + serialized session startups, then re-probe | 11 |
| `HARD-EXPIRED` | needs-maintainer: re-mint via `claude setup-token` (browser approval; not automatable) | 20 |
| `INDETERMINATE` | run one fail-closed probe, append its record, re-classify — never act on a guess | 30 |

The token path is trusted only when PROVEN on the current CLI (`--version-fit passed`);
the default `unknown` takes the conservative branch. `verify` is fail-closed: it answers
`VERIFIED-CLEAN` (exit 0) only on a probe record showing a passing fail-closed probe through
the token path; absent or unreadable evidence refuses (exit 64), a non-passing record is
`NOT-VERIFIED` (exit 12). Two rungs are frozen OUT of the ladder and never emitted:
`ANTHROPIC_API_KEY` (bills as API usage, not the subscription) and OS-level credential-store
manipulation (wrong layer).

## Evidence

- **H-182-doctor-flap-detection** (source lab, kept 2026-08-26, two consecutive counted
  5/5): the classifier types the frozen flap corpus correctly — FLAP-DEGRADED on the
  measured 51% band, CLEAN on the healed morning and the `expiresAt=0` sentinel,
  HARD-EXPIRED only on true double-expiry — and flags a seeded flap within two probe
  records.
- **H-183-doctor-guided-remediation** (source lab, kept 2026-08-26, two consecutive counted
  5/5): the version-fit probe proved the token path in every lane-relevant invocation mode,
  and the frozen decision table emitted exactly the right step per seeded state.
- **The OAuth research capture** (source lab,
  `research/raw/2026-08-25-oauth-refresh-deep-research.md`): the root-cause findings the
  frozen rules encode — the refresh sentinel, the failing per-session refresh, the
  setup-token bypass, and the upstream issue reports behind the version-fit requirement.

Both shipped scripts are byte-preserving copies of the counted fixture artifacts; only
provenance framing differs (verified by diff at port time).
