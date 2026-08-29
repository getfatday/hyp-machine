#!/usr/bin/env python3
"""Audited gh invocation helper for the hyp issueops scripts (counted under
H-136-issueops-live-tier1 in the source lab, kept 2026-08-27, two consecutive
counted 5/5 on live GitHub issues; shipped as counted — only provenance
framing and the pinned-account resolution differ from the counted fixture
copy `ghops.py`; usage guide: docs/issueops.md in this plugin).

Every gh/API invocation an issueops script makes -- read or write -- goes
through gh() below and lands as one JSONL row in the audit log, so
outward-write confinement is gradable from the log alone. The op
classification here is FROZEN (the counted allowlist):

    WRITE ops (the outward-write allowlist):
        issue-create           gh issue create ...        (seed time only)
        label-add              gh issue edit --add-label
        label-remove           gh issue edit --remove-label
        close-with-pointer     gh issue close --comment   (teardown only)
    READ ops (everything else these scripts run):
        auth-token, api-read, issue-view, ...

Account pinning (the counted doctrine; machine reality): gh's active-account
state is machine-global and concurrently mutated by other sessions, so the
account is pinned PER-INVOCATION by resolving `gh auth token --user <pinned
account>` once per process and injecting GH_TOKEN into each child's env. The
token value lives only in process memory: it is never written to the audit
log, any artifact, or stdout.

Consumer configuration: set HYP_GH_ACCOUNT to the gh account these scripts
must pin (the counted fixture hardcoded the source lab's account; a shipped
copy has no account to assume). Unset = every invocation refuses -- pinning
is the doctrine, not an optimization.
"""
import json
import os
import subprocess
import time

# Pinned per-invocation; required. The old CRUX_GH_ACCOUNT name is honored so
# consumers migrating from the crux plugin lose nothing.
GH_ACCOUNT = os.environ.get("HYP_GH_ACCOUNT") or os.environ.get("CRUX_GH_ACCOUNT", "")
GH_TIMEOUT_S = 120

_token_cache = {"token": None}


class GhError(RuntimeError):
    pass


def resolve_token(audit_log=None):
    """Resolve the pinned account's token once per process (uncached calls would
    spam keyring prompts). Raises GhError if the pinned account is not logged in.
    The resolution is itself a gh invocation, so it is audited (op auth-token) --
    argv only; the token value travels in stdout and is never written anywhere."""
    if not GH_ACCOUNT:
        raise GhError(
            "no pinned gh account: set HYP_GH_ACCOUNT to the account these "
            "scripts must pin per-invocation (see docs/issueops.md)"
        )
    if _token_cache["token"]:
        return _token_cache["token"]
    t0 = time.time()
    proc = subprocess.run(
        ["gh", "auth", "token", "--user", GH_ACCOUNT],
        capture_output=True, text=True, timeout=GH_TIMEOUT_S,
    )
    _audit(audit_log, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": ["gh", "auth", "token", "--user", GH_ACCOUNT],
        "op": "auth-token",
        "issue": None,
        "exit": proc.returncode,
        "wall_s": round(time.time() - t0, 2),
        "account": GH_ACCOUNT,
    })
    tok = (proc.stdout or "").strip()
    if proc.returncode != 0 or not tok:
        raise GhError(
            "cannot resolve gh token for pinned account %r (rc=%d): %s"
            % (GH_ACCOUNT, proc.returncode, (proc.stderr or "").strip())
        )
    _token_cache["token"] = tok
    return tok


def _audit(audit_log, row):
    if not audit_log:
        return
    with open(audit_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def gh(args, op, audit_log, issue=None, check=True, input_text=None):
    """Run `gh <args>` with the pinned account's token in env; append one audit row.

    op: frozen op-class string (see module docstring). The audit row records argv
    verbatim (argv never contains the token -- it travels via env only), exit code,
    op class, wall time, and the touched issue number when known."""
    env = dict(os.environ)
    env["GH_TOKEN"] = resolve_token(audit_log)
    env.pop("GITHUB_TOKEN", None)  # a stray ambient token must never outrank the pin
    t0 = time.time()
    proc = subprocess.run(
        ["gh"] + list(args), capture_output=True, text=True,
        timeout=GH_TIMEOUT_S, env=env, input=input_text,
    )
    _audit(audit_log, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": ["gh"] + list(args),
        "op": op,
        "issue": issue,
        "exit": proc.returncode,
        "wall_s": round(time.time() - t0, 2),
        "account": GH_ACCOUNT,
    })
    if check and proc.returncode != 0:
        raise GhError(
            "gh %s failed (rc=%d, op=%s): %s"
            % (" ".join(args[:3]), proc.returncode, op, (proc.stderr or "").strip()[:500])
        )
    return proc


WRITE_OPS = frozenset({"issue-create", "label-add", "label-remove", "close-with-pointer"})
