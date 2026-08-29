# hyp evals

One suite per skill, cases in the `claude plugin eval` format
(`evals/<skill>/<case>/case.yaml`), all with neutral fixtures: every prompt, path, spec,
model node, transcript, and scaffolded file is invented for these tests and carries no
content from any particular repository.

## Run

```
claude plugin eval . --scaffold
```

`--scaffold` is required — each case's `scaffold_script` git-inits its fixture repo.
Scaffolds are deterministic: fixed strings only, no timestamps. Run one suite with
`--case '<skill>-*'` name globs or per-directory `--eval-dir`.

## Suites

| Suite | Should-act | Should-NOT-act / guard |
|---|---|---|
| `intake` | external finding filed + indexed + journaled; self-reference note; testable idea routed by profile | write-once hook denies a raw-file edit |
| `hypothesis` | spec-from-template before any run; mechanical verdict incl. journal + Runs row | run-shaped command gated without a spec; two-variable idea split |
| `adopt` | a recorded mini-session mined into a SCHEMA-shaped scaffold | no model invented for a plain coding question |
| `observe` | node-tagged trace with msg_ citations over a seeded transcript | refuses non-session input |
| `evaluate` | catches a seeded dangling reference + an unreified event | no audit fabricated where no operating-model/ exists |
| `compile` | a stale compiled artifact is fixed at the NODE, never hand-patched | refuses to price a flow containing uncosted high-freedom steps |
| `verify` | A/B spec with fixture isolation + blind referent grading, no run | no A/B harness for a plain factual question |
| `init` | `/hyp:init` scaffolds the capture profile (config, dirs, rules block) | (idempotence is exercised directly by the extraction harness) |
| `run` | executes a compiled flow to its mechanical verdict | refuses with an adopt-first pointer when no model exists |

## Grading notes

- Deterministic graders (regex, file_exists) carry the outcome checks; llm rubrics are
  written as concrete checkable claims. Use a sonnet-tier judge (`--judge-model sonnet`) —
  small judges miss the refusal-quality nuance in `raw-write-once`.
- `raw-write-once` grants only Edit/Write (no Bash) so the PreToolUse hook — not tool
  absence — is the mechanism under test. `run-gated-without-spec` grants Bash so the
  preflight gate is a live mechanism under test.
- The model-skill suites use deterministic graders only.
