#!/usr/bin/env python3
"""om_check_jobs.py -- the ONE JOBS table for the operating-model tier-0 workflow, shared
byte-for-byte by the renderer (`render_yaml`, emitted into `.github/workflows/om-check.yml` by
`scripts/om-ci.py emit ci-tier0`) and the local self-test executor (`scripts/om-ci.py self-test
ci-tier0`, which walks `iter_steps()` under the CI-runner constraint). Self-test equals CI by
construction: both read this one list, never a second copy. This file is ALSO vendored
byte-for-byte into every consumer's `.github/om-scripts/om_check_jobs.py`; nothing the rendered
workflow currently shells out to imports it there (each glue script carries its own inline copy
of the one config rule it needs instead, mirroring how there is no `${CLAUDE_PLUGIN_ROOT}` on a
GitHub-hosted runner) -- it rides along so a future glue script needing the shared table/config
constants has something to import without a plugin install, not because anything on the runner
imports it today (advisory A4, ship fix round 1).

Ported from the lab keep (getfatday/cause-n-effect H-DRAFT-a28b91c9-om-ci-tier0, kept
2026-09-15: five counted looks, A1-A5 pass in every one, SPRT llr 2.9389 over the 2.8904 promote
bound; VERDICT.json, VERIFY.md beside the lane). Two drifts from the kept fixture's `impl/`
bytes, disclosed here rather than hidden in a diff:

1. **MODEL_DIR is a call-time parameter, not a module constant**, and **MODEL_CONTEXTS is gone**.
   The fixture graded exactly one context (`operating-model/ops`) and hardcoded a Python-side
   step list, one step per context (its own comment: "a real emit would glob
   operating-model/*/"). A real consumer may have zero, one, or many contexts under whatever
   `model_dir` its own `.claude/hyp.json` names (`hyp_config.DEFAULTS["model_dir"]`,
   default `operating-model`), so the shipped steps glob at RUN TIME instead: the lint step
   walks `{model_dir}/*/` in the shell and the catalogue step uses
   `compile-catalog.py --model-dir` (which already renders every context under a directory in
   one call). This keeps the rendered YAML byte-stable regardless of how many contexts a given
   consumer happens to have, which the per-context fixture shape could not do.
2. **A `git push` step is added** to the compile-check job, gated by the same actor predicate as
   the regenerate-and-commit step. The fixture's own glue (`om_check_regen_commit.py`) commits
   locally and explicitly never pushes ("out of scope for a local run" -- graded only against a
   local bare origin the harness controls). A hosted job's regenerate commit is inert without a
   push; `compile-check`'s `permissions: contents: write` only has a reason to exist if this job
   pushes with the checkout-persisted `GITHUB_TOKEN`. The push step's own `run:` skips only on a
   detached HEAD (ship fix round 2, B1 below) -- `actions/checkout@v4` leaves every
   `pull_request` checkout detached at the merge ref, so a naive `git push origin HEAD` there
   fails the job whether or not anything was stale -- and a failed push on an attached head
   fails the job (ship fix round 3, B1: the round-2 `&& ... ||` chain swallowed that failure).
   Whether the push itself re-triggers this workflow, and under which `github.actor`,
   remains the disclosed, not-yet-run hosted acceptance check (VERIFY.md section 10, finding 3;
   docs/ci-scaffold.md, "What is still owed") -- this addition does not close that; it is what
   the acceptance check will exercise.

Trigger block (kept as graded, AMENDMENTS.md #1): the hosting service's workflow syntax forbids
`paths` and `paths-ignore` under the same event ("You cannot use both the `paths` and
`paths-ignore` filters for the same event in a workflow. If you want to both include and exclude
path patterns for a single event, use the `paths` filter prefixed with the `!` character to
indicate which paths should be excluded."). The exclusion of `compiled/**` rides in
TRIGGER_PATHS as a `!` pattern and no `paths-ignore:` key is ever rendered; `trigger_fires`
implements the same ordered include/exclude semantics over that one list (the last matching
pattern decides per path; the push is in scope iff at least one changed path ends up included).

PyYAML dependency (kept as graded, undisclosed by the spec text, closed by the build): under the
CI-runner constraint (`HOME` empty, `PATH` = system python3 and git only, no pip, no network)
`model-lint.py`'s `import yaml` fails -- PyYAML's discovery on a real host is a user-site
package keyed to `$HOME`. Without it, model-lint.py degrades to `W-NOYAML` and never runs the
E-LINK check the lint job's whole point depends on. PYVENDOR_DIR carries a vendored copy of
PyYAML's pure-Python modules only (`scripts/vendor/pyyaml/`, MIT-licensed) and both jobs' `env:`
sets `PYTHONPATH` to it.

Stdlib only, Python 3.9. This module never imports another plugin module (it is vendored on its
own into consumers that have no plugin install at all).
"""
import os

BOT_IDENTITY = "om-check[bot]"
VENDOR_DIR = ".github/om-scripts"
PYVENDOR_DIR = ".github/om-scripts/pyyaml"
TIMEOUT_MINUTES = 5
DEFAULT_MODEL_DIR = "operating-model"
# A6 (ship fix round 1): SCHEMA_VERSION and REGEN_COMMIT_MESSAGE (the commit message string
# duplicated, unused, in om_check_regen_commit.py) were dead; dropped rather than left unread.


def trigger_paths(model_dir=DEFAULT_MODEL_DIR):
    """One `paths` list, in filter-pattern order: a positive pattern includes, a `!` pattern
    excludes; never a second `paths-ignore` list (forbidden beside `paths` on the same event --
    see module docstring). `compiled/**` is a plugin-wide constant (scripts/om-worker.py's own
    `_compiled_staleness` reads a literal `compiled/` directory, never the configured
    `model_dir`), so only the first entry is parameterized."""
    return ["%s/**" % model_dir.strip("/"), "!compiled/**"]


# One job = {name, permissions, timeout_minutes, env?, steps:[{name, run, if?, env?}]}. `run` is
# a shell command template; "{vendor}"/"{pyvendor}"/"{model_dir}" are substituted at
# render/exec time. `if`, when present, is a presence-only marker (its value is never read --
# render_yaml renders one fixed predicate text for every marked step, `github.actor != BOT`,
# and om-ci.py's self-test parses THAT rendered text back out rather than re-deriving from this
# table, per A6/B3 ship fix round 1): the loop guard on the regenerate-and-push steps. Job-level
# `env` (PYTHONPATH to the vendored PyYAML) applies to every step in the job: both jobs shell
# out to `om-worker.py` verbs that internally re-invoke model-lint.py, and the lint job's own
# direct `model-lint.py` step needs it too.
JOBS = [
    {
        "name": "lint",
        "permissions": {"contents": "read"},
        "timeout_minutes": TIMEOUT_MINUTES,
        "env": {"PYTHONPATH": "{pyvendor}"},
        "steps": [
            {
                "name": "regenerate catalogue",
                "run": "python3 {vendor}/compile-catalog.py --model-dir {model_dir} --write",
            },
            {
                "name": "lint",
                "run": ('for d in {model_dir}/*/; do [ -d "$d" ] || continue; '
                        'python3 {vendor}/model-lint.py "$d" || exit 1; done'),
            },
            {
                "name": "evaluate",
                "run": "python3 {vendor}/om-worker.py evaluate --root . --plugin-scripts {vendor}",
            },
        ],
    },
    {
        "name": "compile-check",
        "permissions": {"contents": "write"},
        "timeout_minutes": TIMEOUT_MINUTES,
        "env": {"PYTHONPATH": "{pyvendor}"},
        "steps": [
            {
                "name": "compile-check (check mode)",
                "run": "python3 {vendor}/om-worker.py compile-check --root . --plugin-scripts {vendor}",
            },
            {
                "name": "report staleness",
                "run": "python3 {vendor}/om_check_report_stale.py",
            },
            {
                "name": "regenerate and commit if stale",
                "run": "python3 {vendor}/om_check_regen_commit.py",
                "if": True,  # presence-only marker; see comment above JOBS
            },
            {
                "name": "push regenerated commit if any",
                # `HEAD` names the source revision explicitly rather than relying on an
                # upstream-tracking branch (a plain `git push` refuses with "no upstream branch"
                # on a checkout that never set one) and pushes to a destination of the same
                # name, matching how `actions/checkout@v4` leaves a push-event ref checked out
                # locally. B1 (ship fix round 2): `actions/checkout@v4` leaves EVERY
                # `pull_request` checkout detached at the merge ref, and `git push origin HEAD`
                # exits 1 on a detached HEAD ("unable to push to unqualified destination: HEAD")
                # -- so this job would go red on every pull_request the template triggers on,
                # whether or not anything was actually stale or broken. `git symbolic-ref -q
                # HEAD` reports whether HEAD is attached to a branch; push only when it is, and
                # print a one-line skip notice otherwise. B1 (ship fix round 3): the guard is an
                # `if ...; then push; else echo; fi` -- NOT the round-2 `A && push || echo`
                # chain, which routed a FAILED push on an ATTACHED head into the echo branch
                # (measured: attached HEAD, unreachable origin -> git's `fatal:` lines, then the
                # false "detached" notice, job rc 0), so a stale catalogue whose regenerate
                # commit never landed read green on the exact push-event shape this step exists
                # for. Now the skip happens only on a detached HEAD; a failed push on an attached
                # head fails the job with this step as the failing step. The disclosed,
                # not-yet-run hosted acceptance check (this module's docstring, drift 2) is:
                # "is `github.actor` ever a branch push where this guard should NOT have
                # skipped", and "does a push the hosting service rejects (branch protection, a
                # read-only token) now go red as intended".
                "run": 'if git symbolic-ref -q HEAD >/dev/null; then git push origin HEAD; '
                       'else echo "PUSH skipped -- detached HEAD (pull_request checkout)"; fi',
                "if": True,  # presence-only marker; see comment above JOBS
            },
        ],
    },
]


def render_step_command(step, model_dir=DEFAULT_MODEL_DIR):
    return step["run"].format(vendor=VENDOR_DIR, pyvendor=PYVENDOR_DIR, model_dir=model_dir.strip("/"))


def _resolve_env(raw, model_dir=DEFAULT_MODEL_DIR):
    return {k: v.format(vendor=VENDOR_DIR, pyvendor=PYVENDOR_DIR, model_dir=model_dir.strip("/"))
            for k, v in (raw or {}).items()}


def job_env(job, model_dir=DEFAULT_MODEL_DIR):
    return _resolve_env(job.get("env"), model_dir)


def step_env(job, step, model_dir=DEFAULT_MODEL_DIR):
    """Resolved env for one step: the job's own env merged with (overridden by) the step's own
    `env:`, placeholders substituted. Additive over the process env the job already carries --
    never a full replacement -- so a job/step with no `env:` key changes nothing."""
    merged = dict(job.get("env") or {})
    merged.update(step.get("env") or {})
    return _resolve_env(merged, model_dir)


def iter_steps():
    """Yields (job, step) in table order -- the single source both render_yaml and the local
    self-test executor walk."""
    for job in JOBS:
        for step in job["steps"]:
            yield job, step


def render_yaml(model_dir=DEFAULT_MODEL_DIR):
    """Deterministic, hand-rolled YAML text (no PyYAML dependency, matching
    compile-catalog.py's own stdlib-only convention) -- byte-identical on every call over the
    same table and the same model_dir."""
    model_dir = model_dir.strip("/") or DEFAULT_MODEL_DIR
    lines = []
    lines.append("# GENERATED by scripts/om-ci.py emit ci-tier0 -- do not hand-edit; re-run the emit step.")
    lines.append("name: om-check")
    lines.append("on:")
    for event in ("push", "pull_request"):
        lines.append("  %s:" % event)
        lines.append("    paths:")  # the ONE filter key per event; `!` entries carry the exclusion
        for p in trigger_paths(model_dir):
            lines.append("      - %r" % p)
    lines.append("concurrency:")
    lines.append("  group: om-check-${{ github.ref }}")
    lines.append("  cancel-in-progress: true")
    lines.append("jobs:")
    for job in JOBS:
        lines.append("  %s:" % job["name"])
        lines.append("    runs-on: ubuntu-latest")
        lines.append("    timeout-minutes: %d" % job["timeout_minutes"])
        lines.append("    permissions:")
        for k, v in job["permissions"].items():
            lines.append("      %s: %s" % (k, v))
        jenv = job_env(job, model_dir)
        if jenv:
            lines.append("    env:")
            for k, v in sorted(jenv.items()):
                lines.append("      %s: %s" % (k, v))
        lines.append("    steps:")
        lines.append("      - uses: actions/checkout@v4")
        lines.append("        with:")
        lines.append("          token: ${{ secrets.GITHUB_TOKEN }}")
        lines.append("      - uses: actions/setup-python@v5")
        lines.append("        with:")
        lines.append("          python-version: '3.9'")
        for step in job["steps"]:
            lines.append("      - name: %s" % step["name"])
            if "if" in step:
                lines.append("        if: ${{ github.actor != '%s' }}" % BOT_IDENTITY)
            senv = _resolve_env(step.get("env"), model_dir)
            if senv:
                lines.append("        env:")
                for k, v in sorted(senv.items()):
                    lines.append("          %s: %s" % (k, v))
            lines.append("        run: %s" % render_step_command(step, model_dir))
    lines.append("")  # trailing newline
    return "\n".join(lines)


def extract_run_blocks(yaml_text):
    """The inverse of render_yaml over ITS OWN output: pulls every `run:` line's command back
    out in job/step order. Used to compare what got rendered against what iter_steps() would
    execute -- never a general YAML parser, since this template never needs one."""
    blocks = []
    for line in yaml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run: "):
            blocks.append(stripped[len("run: "):])
    return blocks


def matches_glob_rooted(path, pattern):
    """`<model_dir>/**` / `compiled/**` -- the only glob shape this template ever declares: a
    literal directory prefix followed by `/**`. Deliberately not a general globber. A leading
    `!` (the exclusion marker) is the caller's to strip; this matches the bare glob."""
    if pattern.endswith("/**"):
        prefix = pattern[: -len("/**")]
        return path == prefix or path.startswith(prefix + "/")
    return path == pattern


def path_in_scope(path, patterns):
    """One changed path against a `paths` filter list in order (the hosting service's
    filter-pattern semantics for a single list carrying `!` exclusions): the LAST matching
    pattern decides -- a positive match includes, a `!` match excludes; a path matching nothing
    is out of scope."""
    included = False
    for pat in patterns:
        negate = pat.startswith("!")
        if matches_glob_rooted(path, pat[1:] if negate else pat):
            included = not negate
    return included


def trigger_fires(changed_paths, model_dir=DEFAULT_MODEL_DIR):
    """The push is in scope iff at least one changed path is included by the `paths` filter
    after its `!` exclusions. changed_paths=[] (e.g. an empty push) never fires."""
    if not changed_paths:
        return False
    patterns = trigger_paths(model_dir)
    return any(path_in_scope(p, patterns) for p in changed_paths)
