---
bump: minor
---
One command now emits a zero-credential CI check for the operating model: run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/om-ci.py" emit ci-tier0` and commit the result to get a
GitHub Actions workflow (`.github/workflows/om-check.yml`) that lints `operating-model/**` and
checks whether its compiled catalogue is caught up on every push and pull request touching it —
red on a broken model, green otherwise, and it regenerates the catalogue and commits the
result at most once per push (never in a loop, never on the bot's own commit). No secret beyond
the checkout's own `GITHUB_TOKEN`, no model session, no network beyond the checkout. Why: the
lab keep `H-DRAFT-a28b91c9-om-ci-tier0` (getfatday/cause-n-effect, VERDICT.json — five counted
looks, A1-A5 pass in every one, SPRT llr 2.9389 over the 2.8904 promote bound) proved the
underlying variable; Amendment #1 in that lane is why the trigger renders one `paths:` list
with a `!compiled/**` exclusion rather than the `paths`+`paths-ignore` pair a first draft would
reach for (the hosting service forbids both filters on one event). What to do after upgrading:
run the `emit` verb once per repository and commit the workflow plus the vendored
`.github/om-scripts/` it calls (`docs/upgrading.md` has the exact commands); verify locally with
`om-ci.py self-test ci-tier0` before pushing. What is still owed: the hosted acceptance check —
whether the hosting service's own evaluation of the trigger, and `github.actor` for a
`GITHUB_TOKEN` push, behave as the local self-test simulates them (`docs/ci-scaffold.md`,
"Shipped ahead of the scaffold"). How to undo: delete `.github/workflows/om-check.yml` and
`.github/om-scripts/` — nothing else in the repository depends on either.
