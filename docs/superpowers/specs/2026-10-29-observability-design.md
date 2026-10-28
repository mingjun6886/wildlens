# Phase 9 - Observability: tracing, metrics, alarms, dashboard

**Date:** 2026-10-29
**Status:** Awaiting review
**Scope:** `services/process/` (two new flat modules, the Dockerfile), `infra/terraform/modules/observability` (new), `infra/terraform/modules/apigw_logging` (new), `infra/terraform/modules/api`, `infra/terraform/modules/api_functions`, `infra/terraform/modules/compute`, `infra/terraform/modules/queue` (wiring only), `docs/runbook.md`, documentation

---

## 1. Goal

When something is wrong, the owner is told by email within minutes, and can then see where the time or the failure went without guessing. Success means:

- an alarm that fires sends one email to a confirmed address;
- one X-Ray trace of an uploaded image shows the time spent loading the model, tagging, making the thumbnail and each AWS call;
- one dashboard answers "is it healthy and how fast is it" at a glance;
- the `checkov:skip` lines that promise "Phase 9" are gone (five of the six; section 6 says why `CKV2_AWS_4` may stay).

The design document (`docs/design/2026-09-22-architecture.md`, sections 7.3 and 8) fixes the shape: X-Ray on API Gateway and every function, four custom metrics, one dashboard, alarms to email, 14-day log retention. This phase follows it and records each place it differs (section 8).

### Decisions already taken

| # | Decision | Reason |
|---|---|---|
| D1 | **Alarms go to a new SNS topic `wildlens-dev-alerts`**, separate from the user-facing tags topic | A user-facing topic must never carry operator alerts, and the reverse |
| D2 | The email subscription is created by Terraform from the variable `alert_email` (default empty: no subscription). The real value lives in an untracked `terraform.tfvars` | The repository is public. `*.tfvars` is already ignored (`.gitignore`). A subscription in code survives `destroy` and `apply` cycles |
| D3 | The "records stuck PENDING" alarm is **replaced** by an alarm on the age of the oldest message in the main queue | CloudWatch has no per-status count for DynamoDB, a PENDING record expires through its own one-hour TTL, and abandoned uploads would make the alarm noisy. Queue age is a native metric and detects the real failure (a file waiting that nothing processes). Known blind spot: section 8 |
| D4 | **Custom metrics through EMF** (embedded metric format log lines), namespace `WildLens/Process`, **no dimensions** | No SDK call and no `cloudwatch:PutMetricData` permission. Each distinct dimension set is a separately billed metric, so no dimensions keeps the bill at four metrics (about $1.20 a month) |
| D5 | **The X-Ray SDK is installed in `process` only.** The four API functions get active tracing with no SDK | The SDK is not in the Python 3.11 Lambda runtime. The API artefact is one zip with no third-party packages, and every subsegment the design asks for is in `process`. The image takes the SDK with one `pip install` |
| D6 | **The account-level API Gateway CloudWatch role lives in `dev`**, in its own small module `apigw_logging` | The account holds only WildLens (the provider pins `allowed_account_ids`) and everything stays in one state. Consequence in section 6 |
| D7 | Nothing is applied, built or pushed without the owner's go-ahead | Standing rule |

### Out of scope

An alarm on the cold-start ratio; an alarm on the `notification not sent` log line (the runbook already has the Logs Insights query); subsegments for DynamoDB and S3 inside the API functions; a Lambda that scans for stuck PENDING records; anomaly detection; a WAF; a metric per route (API Gateway detailed metrics bill per method); splitting the tagging span into MegaDetector and SpeciesNet (section 4).

---

## 2. Components

| Unit | Purpose | Depends on |
|---|---|---|
| `modules/observability` | Alerts topic, optional email subscription, the alarms, the dashboard | function names, queue names, API name and stage, log group name |
| `modules/apigw_logging` | IAM role API Gateway uses to write logs, and `aws_api_gateway_account` | nothing |
| `modules/api` | Access log group and settings on the stage, X-Ray on the stage | `apigw_logging` |
| `modules/compute`, `modules/api_functions` | `tracing_config { mode = "Active" }` and the two X-Ray write actions on each function's own role | nothing |
| `modules/queue` | Receives `alarm_topic_arns` (the variable already exists) | the alerts topic |
| `services/process/metrics.py` | `emit(name, value, unit)`: one EMF line to stdout | nothing |
| `services/process/tracing.py` | `span(name)`: a context manager that opens an X-Ray subsegment, or does nothing | `aws-xray-sdk`, if installed |
| `services/process/app.py` | Calls both, and logs one line per query-mode call | the two modules above |

`metrics` and `tracing` are added to `known-first-party` in `pyproject.toml` and each gets a `COPY` line in `services/process/Dockerfile` (`test_packaging.py` fails without it).

---

## 3. Alarms and the alerts topic

### 3.1 Topic and subscription

`aws_sns_topic.alerts` named `${name_prefix}-alerts`, **not encrypted at rest**, unlike the tags topic. A CloudWatch alarm cannot publish to a topic encrypted with the AWS-managed key `alias/aws/sns`, because that key's policy cannot be changed to allow CloudWatch, and the failure is silent: the alarm fires and no mail arrives. Encrypting it needs a customer-managed key at about $1 a month. An alert holds a resource name, a metric and a threshold, nothing a user submitted, so the topic stays unencrypted and the checkov finding (`CKV_AWS_26`) is skipped on the resource with this reason. The default topic policy already lets alarms in the same account publish, so no explicit policy is written. Step 5 of the manual checks below is therefore **not optional**: `set-alarm-state` is the only proof an alarm can reach the owner.

`aws_sns_topic_subscription.email` is created with `count = var.alert_email == "" ? 0 : 1`. A new file `terraform.tfvars.example` shows the variable with a placeholder. SNS sends a confirmation mail on first apply; nothing arrives until it is confirmed.

### 3.2 Alarms

All alarms use `treat_missing_data = "notBreaching"`, as `dlq_not_empty` does, so an idle estate is not permanently in `INSUFFICIENT_DATA`. All act on the alerts topic through `alarm_actions`. None sends an OK notification: the runbook says to look at the dashboard, and a second mail per incident teaches the owner to ignore both.

| Alarm | Metric | Threshold |
|---|---|---|
| `dlq-not-empty` (exists) | `AWS/SQS ApproximateNumberOfMessagesVisible` on the DLQ | >= 1; only `alarm_actions` is wired |
| `<function>-errors`, one per function (`process`, `upload`, `search`, `status`, `subscribe`) | `AWS/Lambda Errors`, Sum | > 3 over one 5-minute period |
| `queue-oldest-message` | `AWS/SQS ApproximateAgeOfOldestMessage` on the main queue, Maximum | > 1200 s over one 5-minute period |

The age threshold is 1200 s, not the 900 s of the design's PENDING alarm: a message being processed still counts toward the age, and `process` is allowed to run for 900 s, so 900 would fire on a file that merely used its full timeout. 1200 s means a file has waited, or been retried, past any legitimate run.

The budget alarm in the design (10 / 25 / 50 dollars) already exists from Phase 0 and is not touched.

---

## 4. Tracing

### 4.1 Infrastructure

- Every function: `tracing_config { mode = "Active" }`.
- Each function's own role gains an inline statement for `xray:PutTraceSegments` and `xray:PutTelemetryRecords` on `*` (those two actions do not support resource-level permissions). Roles stay one per function (ADR 7).
- The API stage: `xray_tracing_enabled = true`.
- Removed: `CKV_AWS_50` (two places), `CKV_AWS_73`.

### 4.2 `process`

`tracing.span(name)` is a context manager. It opens `aws_xray_sdk.core.xray_recorder.in_subsegment(name)` when the SDK imports and a segment is active, and otherwise yields without doing anything, so unit tests, `ml/eval` and local runs need no SDK. `patch(["boto3"])` is applied once at import, which gives the DynamoDB, S3 and SNS calls their own subsegments with no code of ours.

Spans in `app.py`: `model-load` (around `get_models`), `tagging` (around `tag_image`), `thumbnail` (around `make_thumbnail` and the upload).

**One `tagging` span, not two.** `ml/tagger.py` runs the detector and the species model inside a single call and exposes no boundary between them. Splitting the span would mean changing `ml/`, which the evaluation script and the Lambda both depend on, for a distinction the `InferenceDuration` metric and the detector log already show. The design's wording ("MegaDetector, SpeciesNet") is not met literally; section 8.

### 4.3 Image

One `RUN pip install --no-cache-dir --only-binary=:all: "aws-xray-sdk==2.15.0"` placed after the OpenCV step and before the verification step. The verification step is extended to import the SDK and to print the protobuf version again, because the Dockerfile's step 4 shows protobuf is pinned by hand against a conflict and any later install can move it. `aws-xray-sdk` depends on `botocore` and `wrapt` only, so no movement is expected; the build proves it.

---

## 5. Metrics (EMF)

`metrics.emit(name, value, unit)` prints one line of single-line JSON:

```json
{"_aws": {"Timestamp": 1793000000000, "CloudWatchMetrics": [
  {"Namespace": "WildLens/Process", "Dimensions": [[]],
   "Metrics": [{"Name": "InferenceDuration", "Unit": "Milliseconds"}]}]},
 "InferenceDuration": 7150}
```

An empty dimension set is what keeps one metric per name. The call never raises: a failure to print is swallowed, as `publish_tags` swallows a failed publish, because a metric must not fail a correctly tagged file.

### 5.1 Meanings and emit points

| Metric | Unit | Emitted | Value |
|---|---|---|---|
| `ModelLoadDuration` | Milliseconds | when `cold_start` is true | `model_load_ms` from `load_and_tag` |
| `ColdStartDuration` | Milliseconds | when `cold_start` is true | the whole invocation of that file (`processingMs`, already computed) or, in query mode, the whole handler call |
| `InferenceDuration` | Milliseconds | every call to `load_and_tag` | `inference_ms` |
| `TagsPerFile` | Count | every stored file, after `record_success` | `sum(tags.values())` |

The metrics describe the model, so `ModelLoadDuration`, `ColdStartDuration` and `InferenceDuration` are emitted in both stored and query mode. `TagsPerFile` is for stored files only: a query is never stored.

The cold-start ratio on the dashboard is the sample count of `ModelLoadDuration` divided by `Invocations`.

**Known limit.** "Cold" is the existing `cold_start` flag, the same one written to DynamoDB as `coldStart`. If the first call on a new container fails permanently before the model loads, the model loads on the next call while `cold_start` is already false, and that load is not counted. This undercounts and never overcounts.

### 5.2 Counting `/search/byfile`

The query-mode branch of `handler` logs no line today. It gains one structured line, `query tagged`, carrying `coldStart`, `modelLoadMs` and `inferenceMs`, so the dashboard can count calls to the route with a Logs Insights query. ADR 5 asks for exactly this number.

---

## 6. API Gateway logging

`modules/apigw_logging`:

- `aws_iam_role` `${name_prefix}-apigw-cloudwatch`, trust `apigateway.amazonaws.com`, with the AWS-managed policy `AmazonAPIGatewayPushToCloudWatchLogs`;
- `aws_api_gateway_account` with `cloudwatch_role_arn` set to that role.

`modules/api`:

- `aws_cloudwatch_log_group` `/aws/apigateway/${name_prefix}` with `retention_in_days = var.log_retention_days`, and the two checkov skips the existing log groups carry (`CKV_AWS_158`, `CKV_AWS_338`), each with its reason;
- `access_log_settings` on the stage, format JSON with `requestId`, `extendedRequestId`, `httpMethod`, `resourcePath`, `status`, `responseLatency`, `integrationLatency`, `integrationStatus`, `errorMessage`. **No caller email or IP.** The repository is public and the logs hold user data; `requestId` is enough, because `common.correlation_id` returns that same id, so an access-log line ties directly to the function's own log lines;
- the stage `depends_on` the account resource: a stage cannot enable logging until the account has a role, and the failure message does not say so.
- Removed: `CKV_AWS_76`. `CKV2_AWS_4` ("stage has a logging level") is expected to want method settings with `logging_level`, which means execution logging: a second, verbose log group this phase did not decide on. If checkov still reports it, the skip stays with its reason rewritten to say access logging is on and execution logging is not; if it passes, the skip goes.

**Consequence of D6.** The AWS provider 6.67.0 pinned here resets the account's CloudWatch role to unset on `destroy` (documented in the provider's 6.x upgrade guide and not run here). A `terraform destroy` of this estate therefore switches API Gateway logging off for any other API in the account, and nothing else is in this account today. ADR 12 records it. Destroy order is safe: the stage depends on the account resource, so the stage goes first.

---

## 7. Dashboard

One `aws_cloudwatch_dashboard` named `${name_prefix}`, which falls inside the three dashboards CloudWatch does not bill.

| Widget | Source |
|---|---|
| Files processed per hour | `WildLens/Process TagsPerFile`, SampleCount, 1 h |
| Queue depth and DLQ depth | `AWS/SQS ApproximateNumberOfMessagesVisible`, both queues |
| Oldest message age | `AWS/SQS ApproximateAgeOfOldestMessage`, with the alarm threshold as an annotation |
| Error rate | `process` `Errors` / `Invocations` (metric math) |
| Processing time p50, p95, p99 | `AWS/Lambda Duration` for `process`, percentile statistics |
| Cold-start ratio | `ModelLoadDuration` SampleCount / `process` `Invocations` (metric math) |
| Model load and inference time | `ModelLoadDuration`, `InferenceDuration`, average and maximum |
| API requests, 4xx, 5xx, latency | `AWS/ApiGateway` `Count`, `4XXError`, `5XXError`, `Latency` (stage level, free) |
| `/search/byfile` calls per hour, and how many were cold | Logs Insights on the `process` log group, `filter msg = "query tagged"`, `stats count(), sum(coldStart) by bin(1h)` |

---

## 8. Where this differs from the design document

| Design says | This phase does | Why |
|---|---|---|
| "Records stuck PENDING: more than 10 older than 15 minutes" | Oldest queue message older than 1200 s | D3. **Blind spot:** if the S3 to SQS notification itself breaks, the queue is empty, its age is zero and nothing fires. The notification is Terraform-managed and a `plan` shows drift. A Lambda that scans for PENDING would catch it and is out of scope |
| Subsegments around MegaDetector and SpeciesNet separately | One `tagging` span | Section 4.2 |
| X-Ray "on all functions", subsegments throughout | SDK only in `process` | D5. API functions get a segment per call, with duration and errors, and no per-call subsegments |
| Logs carry `fileId` as the correlation id | Unchanged. The correlation id is the SQS message id for queued files, as the runbook already says | Not in scope to change |

---

## 9. Testing

**Unit** (no AWS):

- `metrics`: one line, valid JSON, the `_aws` block names the right namespace, an empty dimension set, the right unit; `emit` never raises when stdout write fails.
- `tracing`: with no SDK, `span` yields and does nothing; with a fake recorder injected, it opens a subsegment of the given name and closes it when the body raises.
- `app`: a stored file emits `InferenceDuration` and `TagsPerFile`; the first call also emits `ModelLoadDuration` and `ColdStartDuration` and the second does not; a query-mode call emits the model metrics but not `TagsPerFile`, and logs `query tagged`; a file that fails permanently emits no `TagsPerFile`.
- `test_packaging`: every flat module `app.py` imports, including the two new ones, has a `COPY` line.

**Infrastructure:** `terraform fmt`, `validate` and checkov in CI, and the read-only `plan` on the pull request. Expected plan: new topic, subscription (when `alert_email` is set), about 7 alarms, a dashboard, a role, the account resource, a log group, and in-place changes to the functions and the stage. The stage change creates a new API Gateway deployment only if the deployment's triggers change; the plan will show whether it does.

**Manual, on AWS, each step after the owner's go-ahead:**

1. `plan`, then `apply`.
2. Build and push the `process` image, update the function, confirm `LastUpdateStatus` is `Successful`.
3. The owner confirms the alerts subscription mail.
4. Upload a new image. Check: the X-Ray trace shows `model-load`, `tagging`, `thumbnail` and the DynamoDB and S3 calls; the four metrics appear in `WildLens/Process`; the API access log has a line with that request id; the dashboard renders every widget.
5. With the owner's go-ahead, because it sends mail: `aws cloudwatch set-alarm-state` on one alarm to `ALARM`, confirm the alert arrives, then set it back to `OK`. Required, for the reason in section 3.1.

---

## 10. Documentation

- **ADR 12** records D1 to D6, the replaced PENDING alarm and its blind spot, EMF without dimensions, the single tagging span, and the account-role consequence on `destroy`. Row added to `docs/adr/README.md`.
- **Runbook:** the two `(Phase 9)` sections become real procedures: reading a trace (`aws xray get-trace-summaries`, then `batch-get-traces`, or the console), reading the dashboard, and one short entry per alarm saying what it means and what to do first. The note at the top about placeholders is removed.
- **`CLAUDE.md`:** the observability path in the Architecture section, and the two new flat modules.
- ADR 5 is not edited (ADRs are not rewritten). The runbook says where the `/search/byfile` number is.
