# IssueOps: the audited GitHub-issues round trip

Three scripts plus one shared helper that let a hyp-conventioned repository treat GitHub
issues as a safe, audited feedback intake: `scripts/issueops-fetch.py` (the CRLF-normalizing
transport adapter), `scripts/issueops-reply.py` (the deterministic reply templater),
`scripts/issueops-teardown.py` (the manifest-scoped restoration script), and
`scripts/issueops_gh.py` (the audited, account-pinned gh invocation helper the other three
import). All are stdlib-only Python.

An issue body is attacker-reachable text. Everything in this document exists to keep that
text DATA, never instructions: a deterministic parser reduces it to typed fields, only
allowlisted writes ever leave the machine, and every gh invocation lands in an audit log.

## The tiered safety ladder

Each incoming issue is handled at the lowest sufficient tier; each verdict is one of
reject / escalate / run:

| Tier | Action | Gate |
|---|---|---|
| 0 — auto: parse and label | Schema validation via the adapter + downstream converter: a malformed payload is **REJECT**ed with a field pointer (no draft, no run); well-formed payloads get a reversible taxonomy label | None — pure script, no tools, offline parsing |
| 1 — auto: sandboxed run | A well-formed payload converts to a draft, passes the shipped preflight, and **RUN**s unattended in an isolated clone to a verdict; the templated reply is STAGED as a run artifact, never posted | Isolated clone only; no write path to any live repo |
| 2 — propose: draft spec / fix PR | Drafts whose Method trips preflight's one-way-door screen are **ESCALATE**d into exactly one staged maintainer-queue row quoting the signal, and never run | `scripts/preflight.py`'s existing screen; never auto-merged |
| 3 — propose: templated reply send | Posting a staged reply onto the real issue | Human-approved send; the reply text itself is template-assembled from typed fields, never free generation |
| M — maintainer queue | First live activation, out-of-allowlist authors, one-way-door drafts, ambiguous provenance | Maintainer go/no-go, logged |

The counted evidence runs at tier 1: reject/escalate/run verdicts on real issues, all
outward writes confined (below), replies staged but never posted.

## The frozen outward-write allowlist

The only write operations these scripts ever perform, enforced structurally (each is a
named op-class in `issueops_gh.py`; `WRITE_OPS` is frozen) and gradable from the audit log:

1. `issue-create` — seeding, harness-side only;
2. `label-add` — one reversible taxonomy label per issue;
3. `label-remove` — teardown removing exactly that label;
4. `close-with-pointer` — teardown's `gh issue close --comment <run-record pointer>`, the
   single comment this machinery ever writes, carried by the allowlisted close itself.

Zero unattended comments, merges, pushes, or non-teardown closes. Staged replies are run
artifacts on disk; sending one is tier 3, human-gated.

## Account pinning

gh's active-account state is machine-global and concurrently mutated by other sessions, so
the account is pinned PER-INVOCATION: `issueops_gh.py` resolves
`gh auth token --user $HYP_GH_ACCOUNT` once per process and injects `GH_TOKEN` into each
child's env (a stray ambient `GITHUB_TOKEN` is dropped). Set `HYP_GH_ACCOUNT` before any
live invocation; unset, every call refuses. The token value lives only in process memory —
never in the audit log, an artifact, or stdout.

## The transport adapter

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issueops-fetch.py" --schema issue-form.yml --out payload.json \
    --issue N --repo owner/repo --audit-log gh-audit.jsonl [--raw-out body.md]   # live
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issueops-fetch.py" --schema issue-form.yml --out payload.json \
    --body-file body.md                                                          # offline / calibration
```

Fetches an issue by pinned number, applies the frozen normalization (strictly `\r\n` to
`\n`, nothing else — web-form-submitted issue bodies are CRLF), and inverts GitHub's
issue-form rendering back to a typed JSON payload. Unknown headings pass through as
slugified keys and missing sections yield no key, so the downstream converter stays the
single validation point (field-pointer rejection is transported, not simulated). The
adapter also carries the render direction (`render_body`) so the whole contract calibrates
offline with zero model calls.

## The reply templater

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issueops-reply.py" resolved  <issue_id> <draft> <results.json> <summary> <out>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issueops-reply.py" rejected  <issue_id> <field_pointer> <reason> <out>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issueops-reply.py" escalated <issue_id> <check> <detail> <queue_ref> <out>
```

Every reply field is argv (sourced from a prior deterministic script's own output) or read
verbatim from a pipeline artifact; the templater never invents prose, and refuses to
template a "resolved" reply for a draft whose Status is not `kept`. Byte-deterministic
output.

## Teardown

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/issueops-teardown.py" --manifest RUN-MANIFEST.json \
    --pointer "<run-record pointer>" --audit-log gh-audit.jsonl --out post-teardown-issues.json
```

Manifest-scoped restoration: only manifest-listed issues are touched; per issue, the
harness label is removed and one close-with-pointer names the durable run record. Never
delete/reopen/lock/edit/comment-outside-the-close. Best-effort completion (one failure
never strands the rest), every invocation audited, and a post-teardown listing is fetched
as the restoration record.

## Evidence

- **H-136-issueops-live-tier1** (source lab, kept 2026-08-27, two consecutive counted 5/5
  on LIVE GitHub issues): the well-formed payload converted, preflighted, and auto-ran in an
  isolated clone to a verdict with a staged templated reply; the malformed payload was
  field-pointer-REJECTed; the one-way-door fix draft was ESCALATEd into a maintainer-queue
  row and never run; the audited write census equaled exactly the frozen allowlist; the
  post-teardown listing showed full restoration. Transport parity held: converter output on
  adapter-fetched bodies was byte-identical to output on the pinned payload content.
- **H-106-issueops-roundtrip** (source lab, kept 2026-08-15, two consecutive 5/5): the same
  composed gates with the transport file-based — the reply templater ships byte-unchanged
  through both keeps.

All three ported scripts ship as counted; only provenance framing, script names, the
audited-helper import path, and the pinned-account resolution (`HYP_GH_ACCOUNT` instead of
the source lab's hardcoded account) differ from the counted fixture copies.
