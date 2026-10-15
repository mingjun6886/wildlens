# 10. CI plans with a read-only OIDC role and never applies

**Status:** accepted · Phase 10 · 2026-10-08

## Context

Lint, tests, the web build and Terraform validation were run by hand. The design
document also required that CI hold no long-lived AWS keys. The estate is applied
and destroyed manually between sessions to protect the credit.

## Decision

Two workflows. `ci.yml` runs every check that needs no AWS and requests nothing
beyond reading the repository. `plan.yml` runs `terraform plan` on pull requests
that touch `infra/**`, assuming a role through GitHub's OIDC issuer.

The role can read the account and the state and write nothing. Its trust policy
accepts one subject, `repo:mingjun6886/wildlens:pull_request`, so a run on `main`,
on a tag or in another repository is refused. Two explicit denies remove the
ability to read uploaded objects (including by version id), table rows (including
through PartiQL), application logs and the list of users, all of which
`ReadOnlyAccess` would otherwise grant.

| Considered | Why not |
|---|---|
| A deploy job after merge | The estate is often destroyed. A deploy would fail against nothing or recreate it and spend credit |
| Accuracy evaluation in CI | Needs 470 MB of weights and the answer-key images and takes minutes on CPU. It stays a manual step before changing `ml/` |
| `plan` on every pull request | Gives credentials to changes that cannot affect infrastructure |
| Plan output as a pull-request comment | Needs `pull-requests: write`. The job summary is enough |

## Consequences

**The workflow file is not a security boundary.** Any branch can edit it, so the
role's trust condition and policies carry the safety. That is why the data-read
denies exist even though `plan` never needs them.

**The deny list is a list.** Anything `ReadOnlyAccess` grants that is not named
is still readable, so the list needs another look whenever the estate gains a new
store of user data. If a refresh under the role is ever denied one of these
actions, `plan` fails visibly rather than quietly.

**`plan` cannot take the state lock.** The role has no write access, so the
workflow plans with `-lock=false`. For one maintainer that is acceptable.

**After a `terraform destroy`, the plan lists the whole estate as new.** It is
accurate and noisy; the runbook says so.

**The first apply is manual.** The role does not exist until the owner applies it.

**The repository is public, so plan output is too.** It contains resource names
and ARNs, not secret values, since Terraform redacts sensitive attributes.

**Every checkov finding was skipped, none fixed.** The first run reported 45
findings across 14 resources. Fixing any of them would change real infrastructure,
and this phase changes none. Each skip sits on its resource with its reason. One
reason names a follow-up that is free and in place: explicit SQS encryption.
Several others point at Phase 9, whose tracing and logging will let those skips be
removed.
