# 7. One IAM role per function

**Status:** accepted · Phase 4–6 · 2026-10-01

## Context

The coursework this project rebuilds gave every Lambda the same administrator
role, because AWS Academy permits nothing else. There are now four functions, and
nothing forces that arrangement.

## Decision

A role per function, each scoped to exactly what the function uses. What is
**withheld** is recorded alongside what is granted.

| Function | Granted | Withheld, deliberately |
|---|---|---|
| `process` | S3 read raw + models · S3 write thumb · SQS receive/delete · DynamoDB get/put/update · logs write | `sqs:SendMessage` — a consumer that cannot feed its own queue cannot build a loop. `s3:DeleteObject` — no bug can delete a user's original. `dynamodb:DeleteItem` — tagging must not remove records. `logs:CreateLogGroup` |
| `upload` | DynamoDB get/update · S3 put raw · logs write | `s3:GetObject` — it never reads an upload, so a bug cannot be turned into a way to read the bucket |
| `status` | DynamoDB get · S3 get raw + thumb · logs write | every write |
| `search` | DynamoDB **scan only** · S3 get · `lambda:InvokeFunction` on one target · logs write | `GetItem`, and every write. A search cannot modify a record however badly it goes wrong |

## Consequences

**A bug is bounded by its function's grants.** The point is not that these
functions are expected to misbehave; it is that the damage a mistake can do is
now a property of configuration rather than of care.

**`logs:CreateLogGroup` is withheld from all four, which looks like an oversight
and is not.** Terraform declares each log group with a 14-day retention. A
function that can create log groups creates one with no retention policy on first
invocation, and logs then accumulate forever — a slow, quiet cost nobody notices.
Withholding the permission makes the explicit group the only possibility.

**`s3:GetObject` is needed to *sign* a URL, not only to read.** Presigning is a
local computation, but S3 honours the signature only if the signing identity could
itself perform the action. A presigned URL grants exactly what its signer held and
never more — which is also why `upload` can sign a PUT without being able to read.

**`search` is the only function that may invoke another, and only one target.**
`/search/byfile` needs the models, and the models live in one function. Loading
them in `search` as well would double the memory footprint and the cold-start cost
of the estate.

**Four roles and four policies to maintain.** Adding a capability means touching
IAM, which is the intended friction. The policies are generated from one map in
`modules/api_functions/main.tf` so the four sit side by side and the differences
are readable.

## Alternatives considered

**One shared role.** What the coursework did, and the honest reason it did so was
that AWS Academy allowed nothing else. A shared role means a bug in any function
can reach every resource in the account — and with this estate that includes
deleting every uploaded photograph.

**Managed policies such as `AmazonDynamoDBFullAccess`.** Quicker, and grants every
table in the account including ones that do not exist yet. The whole value of the
decision is in the resource ARNs.
