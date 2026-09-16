#!/usr/bin/env python3
"""selftest-om-credential-policy.py -- regression test for scripts/om-credential-policy.py and
its one call site in scripts/om-integrate.py's `compose`.

Ports A1 from the source lab lane (H-DRAFT-744a5773-om-credential-policy, kept 2026-09-16: five
counted looks, A1 pass in every one, cold-verified) onto the live plugin tip. Builds every row
set in this file's own scratch (never copies a real record): synthetic `substrate-discovered`
rows with `authors_90d` 1, 2 and 5, and (for the CLI case) real throwaway git consumers with 1 or
2 distinct commit authors.

Cases:
  B1  the standalone check (`om-credential-policy.py check`) over a replayed rows file: class
      `shared-subscription-token` refuses at authors_90d 2 and 5 (exactly 1 `CREDENTIAL-REFUSED`
      line carrying the count, exactly 3 `OFFER` lines naming api-key/federation/platform-identity
      in that order, exit 3) and proceeds at 1 (0 refusals, exit 0); class `api-key` is silent
      (0 refusals, exit 0) over all three sets (the must-silent control); the credential
      environment-variable name never appears on stdout
  B2  `compose()` (direct import): with no `tier`/`credential_class` given, `decision` carries no
      `credential` key at all (the no-op default -- no consumer has requested a model-calling
      tier's credential); given both, it matches B1's readings exactly and the mechanism picks
      (`on_device`, `remote`) are unaffected by the credential verdict either way
  B3  `emit` through the live CLI over a real throwaway git consumer: a two-author consumer
      (authors_90d 2) refuses (exit 3, the lock carries no `credential` entry, the environment
      variable name appears 0 times in the lock file or stdout); a one-author consumer
      (authors_90d 1) proceeds (exit 0, the lock's `credential` entry reads `allowed: true`)

Usage: python3 scripts/selftest-om-credential-policy.py    exit 0 = PASS, 1 = FAIL
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OM_INTEGRATE = os.path.join(HERE, "om-integrate.py")
OM_CREDENTIAL_POLICY = os.path.join(HERE, "om-credential-policy.py")
ENV_VAR_NAME = "CLAUDE_CODE_OAUTH_TOKEN"

GIT_ENV_BASE = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
for _k in ("GH_TOKEN", "GITHUB_TOKEN"):
    GIT_ENV_BASE.pop(_k, None)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (": " + str(detail).strip() if (detail and not cond) else ""))


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synthetic_row(authors_90d):
    return {"kind": "substrate-discovered", "schema": 1, "handle": "launchd-queue",
            "advertised": True, "usable": False, "probe_cmd": "true", "exit": 0, "void": None,
            "evidence": "selftest synthetic row", "authors_90d": authors_90d,
            "disk": {"free_bytes": 0, "plugin_cache_bytes": 0}, "host_key": "selftest"}


def write_rows(path, authors_90d, n=3):
    with open(path, "w", encoding="utf-8") as fh:
        for _ in range(n):
            fh.write(json.dumps(synthetic_row(authors_90d), sort_keys=True) + "\n")


def run_policy(rows_path, tier, credential_class):
    return subprocess.run([sys.executable, OM_CREDENTIAL_POLICY, "check", "--rows", rows_path,
                           "--tier", tier, "--class", credential_class],
                          capture_output=True, text=True, timeout=20)


def b1_standalone_check(scratch):
    for authors_90d, want_refused, want_exit in ((1, 0, 0), (2, 1, 3), (5, 1, 3)):
        rows_path = os.path.join(scratch, "rows-%d.jsonl" % authors_90d)
        write_rows(rows_path, authors_90d)
        p = run_policy(rows_path, "b1-tier", "shared-subscription-token")
        lines = p.stdout.splitlines()
        refused = [l for l in lines if l.startswith("CREDENTIAL-REFUSED")]
        offers = [l for l in lines if l.startswith("OFFER")]
        check("b1-authors%d-refused-lines" % authors_90d, len(refused) == want_refused, lines)
        check("b1-authors%d-offer-lines" % authors_90d, len(offers) == (3 if want_refused else 0), lines)
        check("b1-authors%d-exit" % authors_90d, p.returncode == want_exit, p.returncode)
        if want_refused:
            check("b1-authors%d-count-carried" % authors_90d,
                  refused[0].endswith("authors_90d=%d" % authors_90d), refused)
            check("b1-authors%d-offer-order" % authors_90d,
                  offers == ["OFFER api-key repository-owned", "OFFER federation workload-identity",
                             "OFFER platform-identity oidc"], offers)
        check("b1-authors%d-no-env-var-name" % authors_90d, ENV_VAR_NAME not in p.stdout, p.stdout)

    for authors_90d in (1, 2, 5):
        rows_path = os.path.join(scratch, "rows-%d.jsonl" % authors_90d)
        p = run_policy(rows_path, "b1-tier", "api-key")
        lines = p.stdout.splitlines()
        check("b1-api-key-silent-authors%d" % authors_90d,
              p.returncode == 0 and not any(l.startswith("CREDENTIAL-REFUSED") for l in lines),
              (p.returncode, lines))


def b2_compose_import():
    om_integrate = _load_module(OM_INTEGRATE, "om_integrate_selftest_cred")
    rows2 = [synthetic_row(2)]
    rows1 = [synthetic_row(1)]

    d0 = om_integrate.compose(rows2)
    check("b2-no-request-no-credential-key", "credential" not in d0, d0)

    d_refuse = om_integrate.compose(rows2, credential_class="shared-subscription-token", tier="b2-tier")
    cred = d_refuse.get("credential", {})
    check("b2-refuse-allowed-false", cred.get("allowed") is False, cred)
    check("b2-refuse-exit-3", cred.get("exit") == 3, cred)
    check("b2-refuse-lines-shape",
          sum(1 for l in cred.get("lines", []) if l.startswith("CREDENTIAL-REFUSED")) == 1
          and sum(1 for l in cred.get("lines", []) if l.startswith("OFFER")) == 3, cred)
    check("b2-picks-unaffected",
          d_refuse["on_device"] == d0["on_device"] and d_refuse["remote"] == d0["remote"])

    d_proceed = om_integrate.compose(rows1, credential_class="shared-subscription-token", tier="b2-tier")
    cred1 = d_proceed.get("credential", {})
    check("b2-proceed-allowed-true", cred1.get("allowed") is True, cred1)
    check("b2-proceed-exit-0", cred1.get("exit") == 0, cred1)
    check("b2-proceed-zero-refusals",
          not any(l.startswith("CREDENTIAL-REFUSED") for l in cred1.get("lines", [])), cred1)

    for authors_90d, rows in ((1, rows1), (2, rows2)):
        d_apikey = om_integrate.compose(rows, credential_class="api-key", tier="b2-tier")
        cred_ak = d_apikey.get("credential", {})
        check("b2-api-key-silent-authors%d" % authors_90d,
              cred_ak.get("allowed") is True
              and not any(l.startswith("CREDENTIAL-REFUSED") for l in cred_ak.get("lines", [])),
              cred_ak)


def _git(cwd, *args, env=None):
    e = dict(GIT_ENV_BASE)
    if env:
        e.update(env)
    return subprocess.run(["git"] + list(args), cwd=cwd, env=e, capture_output=True, text=True,
                          timeout=20)


def build_consumer_with_authors(root, n):
    os.makedirs(root, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    for i in range(n):
        env = {"GIT_AUTHOR_NAME": "selftest-author-%d" % i,
               "GIT_AUTHOR_EMAIL": "selftest-author-%d@example.invalid" % i,
               "GIT_COMMITTER_NAME": "selftest-author-%d" % i,
               "GIT_COMMITTER_EMAIL": "selftest-author-%d@example.invalid" % i}
        with open(os.path.join(root, "f%d.txt" % i), "w", encoding="utf-8") as fh:
            fh.write("selftest\n")
        _git(root, "add", "-A", env=env)
        _git(root, "commit", "-q", "-m", "c%d" % i, env=env)
    return root


def run_integrate(args, cwd=None, timeout=90):
    return subprocess.run([sys.executable, OM_INTEGRATE] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=timeout)


def b3_emit_cli(scratch):
    root2 = build_consumer_with_authors(os.path.join(scratch, "b3-two"), 2)
    agents_dir2 = os.path.join(scratch, "b3-two-agents")
    p2 = run_integrate(["emit", "--root", root2, "--agents-dir", agents_dir2,
                       "--plugin-scripts", HERE, "--tier", "b3-tier",
                       "--credential-class", "shared-subscription-token"])
    lock_path2 = os.path.join(root2, ".claude", "om-offload.lock.json")
    lock2 = json.load(open(lock_path2)) if os.path.isfile(lock_path2) else {}
    check("b3-two-authors-exit-3", p2.returncode == 3, (p2.returncode, p2.stdout[-400:], p2.stderr[-300:]))
    check("b3-two-authors-refused-line",
          sum(1 for l in p2.stdout.splitlines() if l.startswith("CREDENTIAL-REFUSED")) == 1, p2.stdout)
    check("b3-two-authors-no-credential-in-lock", "credential" not in lock2, lock2)
    check("b3-two-authors-no-env-var-anywhere",
          ENV_VAR_NAME not in json.dumps(lock2) and ENV_VAR_NAME not in p2.stdout)

    root1 = build_consumer_with_authors(os.path.join(scratch, "b3-one"), 1)
    agents_dir1 = os.path.join(scratch, "b3-one-agents")
    p1 = run_integrate(["emit", "--root", root1, "--agents-dir", agents_dir1,
                       "--plugin-scripts", HERE, "--tier", "b3-tier",
                       "--credential-class", "shared-subscription-token"])
    lock_path1 = os.path.join(root1, ".claude", "om-offload.lock.json")
    lock1 = json.load(open(lock_path1)) if os.path.isfile(lock_path1) else {}
    check("b3-one-author-exit-0", p1.returncode == 0, (p1.returncode, p1.stdout[-400:], p1.stderr[-300:]))
    check("b3-one-author-credential-allowed-true",
          lock1.get("credential", {}).get("allowed") is True, lock1)


def b4_compose_cli(scratch):
    root2 = build_consumer_with_authors(os.path.join(scratch, "b4-two"), 2)
    p2 = run_integrate(["compose", "--root", root2,
                       "--tier", "b4-tier", "--credential-class", "shared-subscription-token"],
                      cwd=root2)
    check("b4-two-authors-compose-exit-3", p2.returncode == 3,
          (p2.returncode, p2.stdout[-400:], p2.stderr[-300:]))
    check("b4-two-authors-compose-refused-line",
          sum(1 for l in p2.stdout.splitlines() if l.startswith("CREDENTIAL-REFUSED")) == 1,
          p2.stdout)

    root1 = build_consumer_with_authors(os.path.join(scratch, "b4-one"), 1)
    p1 = run_integrate(["compose", "--root", root1,
                       "--tier", "b4-tier", "--credential-class", "shared-subscription-token"],
                      cwd=root1)
    check("b4-one-author-compose-exit-0", p1.returncode == 0,
          (p1.returncode, p1.stdout[-400:], p1.stderr[-300:]))


def b5_half_request_cli(scratch):
    root = build_consumer_with_authors(os.path.join(scratch, "b5-root"), 1)
    p_tier_only = run_integrate(["compose", "--root", root, "--tier", "b5-tier"], cwd=root)
    check("b5-tier-only-exit-2", p_tier_only.returncode == 2, p_tier_only.stdout)
    p_class_only = run_integrate(["compose", "--root", root,
                                 "--credential-class", "shared-subscription-token"], cwd=root)
    check("b5-class-only-exit-2", p_class_only.returncode == 2, p_class_only.stdout)


def b6_half_request_hyp_json(scratch):
    root = build_consumer_with_authors(os.path.join(scratch, "b6-root"), 1)
    claude_dir = os.path.join(root, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(claude_dir, "hyp.json"), "w", encoding="utf-8") as fh:
        json.dump({"om_credential_tier": "b6-tier"}, fh)
    agents_dir = os.path.join(scratch, "b6-agents")
    p = run_integrate(["emit", "--root", root, "--agents-dir", agents_dir,
                      "--plugin-scripts", HERE], cwd=root)
    check("b6-half-request-diagnostic-printed",
          "CREDENTIAL-UNDECIDABLE half request" in p.stdout, p.stdout)
    check("b6-half-request-exit-0", p.returncode == 0, (p.returncode, p.stdout[-400:]))
    lock_path = os.path.join(root, ".claude", "om-offload.lock.json")
    lock = json.load(open(lock_path)) if os.path.isfile(lock_path) else {}
    check("b6-half-request-no-credential-in-lock", "credential" not in lock, lock)


def b7_policy_missing_void(scratch):
    vendor_dir = os.path.join(scratch, "b7-vendor")
    os.makedirs(vendor_dir, exist_ok=True)
    shutil.copy(OM_INTEGRATE, os.path.join(vendor_dir, "om-integrate.py"))
    # deliberately do NOT copy om-credential-policy.py beside it
    om_integrate_missing_policy = _load_module(os.path.join(vendor_dir, "om-integrate.py"),
                                                "om_integrate_selftest_missing_policy")
    decision = om_integrate_missing_policy.compose([synthetic_row(2)],
                                                    credential_class="shared-subscription-token",
                                                    tier="b7-tier")
    cred = decision.get("credential", {})
    check("b7-policy-missing-exit-2", cred.get("exit") == 2, cred)
    check("b7-policy-missing-allowed-false", cred.get("allowed") is False, cred)
    check("b7-policy-missing-typed-void",
          any(l.startswith("void: policy-missing") for l in cred.get("lines", [])), cred)


def main():
    scratch = tempfile.mkdtemp(prefix="selftest-om-credential-policy-")
    try:
        b1_standalone_check(scratch)
        b2_compose_import()
        b3_emit_cli(scratch)
        b4_compose_cli(scratch)
        b5_half_request_cli(scratch)
        b6_half_request_hyp_json(scratch)
        b7_policy_missing_void(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    n_pass = sum(1 for r in RESULTS if r)
    print("%d/%d checks passed" % (n_pass, len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
