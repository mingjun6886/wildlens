# 12. Observe with a separate alert topic, EMF metrics and X-Ray in the tagger

**Status:** accepted · Phase 9 · 2026-10-29

## Context

Until now the only signal was one alarm that changed colour in a console nobody was
watching, structured logs, and the correlation id. A failure was found by someone
looking. The design document asked for X-Ray, four custom metrics, a dashboard,
alarms to email and 14-day log retention, and six `checkov:skip` lines promised the
rest "in Phase 9".

## Decision

**Alarms go to their own SNS topic**, `wildlens-dev-alerts`, with an email
subscription created from the variable `alert_email`. The address lives in an
untracked `terraform.tfvars` because the repository is public. The topic is **not
encrypted**: a CloudWatch alarm cannot publish to a topic encrypted with the
AWS-managed key `alias/aws/sns`, the failure is silent, and a customer-managed key
costs $1 a month for an alert that carries no user data.

**Alarms:** the existing dead-letter alarm (now wired to the topic), more than 3
errors in 5 minutes for each of the five functions, and the age of the oldest message
in the ingest queue above 1200 seconds.

**Metrics through EMF**, namespace `WildLens/Process`, with no dimensions:
`ModelLoadDuration` and `ColdStartDuration` (cold starts only), `InferenceDuration`
and `TagsPerFile`. A line on stdout becomes a metric, so there is no API call and no
permission, and with no dimensions the cost is four metrics.

**X-Ray: active tracing on all five functions and the API stage; the SDK only in
`process`.** The SDK is not in the Python 3.11 runtime, the API artefact is one zip
with no third-party packages, and every subsegment the design asked for is in
`process`: `model-load`, `tagging`, `thumbnail`, plus the DynamoDB, S3 and SNS calls
through the botocore patch.

**API Gateway access logs** as one JSON line per request, with the request id and no
caller email or IP. The account-level role they need is created in this
environment, by its own small module. Execution logging is not enabled, so
`CKV2_AWS_4` stays skipped with that reason.

| Considered | Why not |
|---|---|
| Alarm on "records stuck PENDING" | CloudWatch has no per-status count for DynamoDB. A PENDING record expires through its own one-hour TTL, and abandoned uploads would make it noisy. A Lambda that scans for them was the alternative |
| Custom metrics through `PutMetricData` | A permission to grant and a call on the request path, for the same four metrics |
| Percentiles and the cold-start ratio from Logs Insights alone | No curve over time, nothing an alarm can act on |
| The X-Ray SDK in the API functions | Would put a third-party package in a zip that has none, for per-call subsegments in functions that already log a correlation id |
| Splitting `tagging` into MegaDetector and SpeciesNet spans | `ml/tagger.py` runs both in one call and exposes no boundary. Splitting means changing `ml/`, which the evaluation script depends on |
| Creating the API Gateway logging role by hand | Infrastructure that is not in code |

## Consequences

**The queue-age alarm has a blind spot.** If the S3 to SQS notification itself
breaks, the queue is empty, the age is zero and nothing fires. The notification is
managed by Terraform, so a `plan` shows the drift. The 1200 s threshold exceeds the
900 s function timeout on purpose, because a message being processed still counts
toward its age.

**Destroying this estate switches off API Gateway logging for the account.** With
the AWS provider 6.x, destroying `aws_api_gateway_account` resets the account's
CloudWatch role. Nothing else is in this account today. If that changes, move the
role out of this environment.

**"Cold" is the existing `cold_start` flag.** If the first call on a container fails
before the model loads, the load on the next call is not counted. The ratio
undercounts and never overcounts.

**Metrics add about $1.20 a month.** X-Ray stays inside its free tier at this
volume, and the dashboard is one of the three CloudWatch does not bill.

**Alarm mail depends on someone confirming the subscription.** With `alert_email`
unset the alarms exist and nobody is told. The runbook says so.
