# scripts/vendor/

Third-party sources vendored into this plugin so the tier-0 CI template
(`scripts/om_check_jobs.py`, `docs/ci-scaffold.md`) can lint the operating model on a
GitHub-hosted runner with no `pip install` step and no network beyond the checkout.

## pyyaml/

`pyyaml/yaml/` is PyYAML 6.0.3's pure-Python package only (no `_yaml*.so` accelerator --
`yaml.safe_load`'s default `SafeLoader` never uses it, and a compiled extension is
platform-specific to begin with, so leaving it out is also the portable choice for a real
Linux-hosted runner). `scripts/om-ci.py emit ci-tier0` copies this tree into a consumer
repository's `.github/om-scripts/pyyaml/`, and the rendered workflow points `PYTHONPATH`
at it so `scripts/model-lint.py`'s `import yaml` resolves under the CI-runner constraint
(empty `HOME`, no user site, no network) instead of silently degrading to its `W-NOYAML`
skip path.

PyYAML is MIT-licensed: https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE -- reproduced in
`pyyaml/LICENSE` beside the vendored package.

Provenance: ported byte-for-byte from the lab keep's fixture
(`experiments/runs/H-DRAFT-a28b91c9-om-ci-tier0/fixture/impl/vendor/pyyaml/` in
getfatday/cause-n-effect, VERDICT.json), which vendored the same release for the same
reason (its own build session measured `import yaml` failing under an empty `HOME` on a
host where the real `HOME` has a user-site PyYAML install).
