---
bump: minor
---
`compose` now refuses to wire a shared subscription token into a model-calling tier when a
repository has recorded more than one author in the last 90 days, and offers three alternatives
(an API key owned by the repository, workload-identity federation, or the platform's own OIDC
identity) instead of silently binding one person's token for everyone. Why: an OAuth token from
`claude setup-token` is tied to the subscription of the person who ran it, so sharing it across
authors makes every model call look like one person's -- the lab keep
H-DRAFT-744a5773-om-credential-policy (VERDICT.json, five counted looks, A1 pass in every one,
cold-verified) and GOVERNANCE.md's Recoverability/Isolation invariants. What to do after
upgrading: nothing, unless `compose`/`emit` prints `CREDENTIAL-REFUSED` for a model-calling tier
you requested (`--tier`/`--credential-class`, or `.claude/hyp.json` `om_credential_tier`/
`om_credential_class`) -- then pick one of the three printed `OFFER` lines. No shipped handle
requests one yet, so this is silent for every consumer today. How to undo: revert the merge.
