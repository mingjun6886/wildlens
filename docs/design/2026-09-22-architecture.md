# WildLens — Design

**Date:** 2026-09-22
**Status:** Approved; implementation planning under way
**Author:** Minh Quan Nguyen

---

## 1. Goals

Rebuild a serverless wildlife-identification platform solo, as a portfolio piece.

**Provenance.** The problem domain originates from a four-person university group assignment (FIT5225, Monash University). This is an independent rebuild by one person, on a personal AWS account, with deliberate architectural changes. The README states this openly.

**Target roles.** Cloud / DevOps / Platform engineering, combined with ML engineering. The design therefore weights infrastructure as code, CI/CD, observability, failure handling, cost control, latency reduction, model versioning, and measured model accuracy.

**Budget.** $100 of AWS credit. Not to be exceeded.

### Success criteria

| # | Criterion | How it is measured |
|---|---|---|
| S1 | The whole estate stands up and tears down with one command | `terraform apply` / `terraform destroy` run clean from nothing |
| S2 | A real latency improvement, not a claimed one | p95 cold start measured before and after optimisation, recorded in the README |
| S3 | A real accuracy figure, not a claimed one | Evaluation harness scores the 26-image test set and emits a per-species report |
| S4 | Spend has a hard ceiling | No failure mode can exceed roughly $5 per day |
| S5 | Incidents are diagnosable | One correlation ID traces a file across every function |
| S6 | No long-lived secrets in the repository | CI authenticates through OIDC; no AWS access keys are stored |

### Out of scope

- Multiple environments — `dev` only
- Role-based authorisation; every authenticated user is equal
- A management UI for bulk tag editing and deletion (a minimal delete endpoint exists, for clearing test data)
- Production-scale query optimisation, such as replacing table scans with a secondary index

---

## 2. Architecture decisions

The six decisions below are where this build departs from the original coursework. Each gets an ADR.

| # | Decision | Rationale |
|---|---|---|
| AD-1 | **Terraform** for all infrastructure; no console clicking | One tool covers both AWS and GCP. `destroy` protects the credit. Most frequently named in job listings |
| AD-2 | Region **ap-southeast-2** (Sydney) | Closest to users; the original used us-east-1 only because the lab mandated it |
| AD-3 | **SQS + DLQ** between S3 and the processing Lambda | Hard spend ceiling, controlled retries, failed messages preserved rather than lost |
| AD-4 | Models **baked into the image, overridable from S3** | Fast cold start without losing the ability to swap models by configuration alone |
| AD-5 | **No VPC** | A VPC-attached Lambda needing egress requires a NAT Gateway: $32/month billed hourly, in exchange for nothing. Every service called is a public endpoint already gated by IAM |
| AD-6 | **Status record written early** (PENDING → DONE) | Lets the UI show progress instead of hanging for three minutes |

---

## 3. Architecture

### 3.1 Components

```
        ┌──────────────────────────────┐
        │  CloudFront + S3 static site │
        └──────────────┬───────────────┘
                       │  JWT in the Authorization header
                       ▼
        ┌──────────────────────┐     ┌──────────────┐
        │  API Gateway (REST)  │◄────┤   Cognito    │
        │  Cognito authoriser  │     │  user pool   │
        └──────────┬───────────┘     └──────────────┘
                   │
      ┌────────────┴─────────────┐
      │  Zip Lambdas (Python 3.11)│
      │  upload · search · status │
      │  delete · subscribe       │
      └────────────┬─────────────┘
                   │
   ┌───────────────┼──────────────────────────────────┐
   │               ▼                                  ▼
   │       ┌──────────────┐                  ┌────────────────┐
   │       │  S3 raw      │                  │   DynamoDB     │
   │       └──────┬───────┘                  │ wildlens-files │
   │              │ ObjectCreated            └────────▲───────┘
   │              ▼                                   │
   │       ┌──────────────┐  3 failures   ┌────────┐  │
   │       │     SQS      │──────────────►│  DLQ   │  │
   │       └──────┬───────┘               └────┬───┘  │
   │              │ concurrency cap = 2        │      │
   │              ▼                          alarm    │
   │    ┌─────────────────────┐                │      │
   │    │ Container Lambda    │────────────────┼──────┘
   │    │ 4 GB · 900 s        │                │
   │    │ MegaDetector →      │                ▼
   │    │ SpeciesNet          │          ┌──────────┐
   │    └──────┬───────┬──────┘          │   SNS    │
   │           │       │                 └──────────┘
   │           ▼       ▼
   │   ┌───────────┐ ┌──────────┐
   │   │S3 thumbs  │ │   ECR    │
   │   └───────────┘ └──────────┘
   │
   └── Observability: X-Ray · CloudWatch dashboard · alarms · JSON logs, 14-day retention

   GCP Cloud Run: a /resolve endpoint that verifies the Cognito-issued JWT itself
```

### 3.2 AWS resources

| Resource | Name | Notes |
|---|---|---|
| S3 | `wildlens-raw-<suffix>` | Originals. Public access blocked. Lifecycle expiry at 30 days |
| S3 | `wildlens-thumb-<suffix>` | Thumbnails |
| S3 | `wildlens-models-<suffix>` | Model override versions; not the default load path |
| S3 | `wildlens-web-<suffix>` | Static site, served through CloudFront |
| DynamoDB | `wildlens-files` | Partition key `fileId`. On-demand capacity. TTL enabled on the `ttl` attribute |
| SQS | `wildlens-ingest` | Visibility timeout 5400 s. `maxReceiveCount` 3 |
| SQS | `wildlens-ingest-dlq` | No consumer attached. 14-day retention |
| Lambda | `wildlens-process` | Container image. 4096 MB, 900 s, 4096 MB `/tmp`, x86_64, reserved concurrency 2 |
| Lambda | `wildlens-upload` / `-search` / `-status` / `-delete` / `-subscribe` | Zip, Python 3.11, 512 MB |
| ECR | `wildlens-process` | Lifecycle policy retaining the three most recent images |
| Cognito | `wildlens-users` | Email plus given and family name, email verification, Hosted UI |
| API Gateway | REST | Cognito authoriser on **every** route |
| SNS | `wildlens-tags` | Email subscriptions with per-species filter policies |
| CloudWatch | One log group per function | **14-day retention, declared explicitly** |

The suffix is a six-character random string generated by Terraform, because S3 bucket names are globally unique.

---

## 4. Data model

### 4.1 DynamoDB record

```json
{
  "fileId":       "a3f9c2… (SHA-256 hex, partition key)",
  "status":       "PENDING | PROCESSING | DONE | FAILED",
  "type":         "image | video",
  "s3Key":        "a3f9c2….jpg",
  "thumbKey":     "a3f9c2….jpg | null",
  "tags":         { "wild boar": 1 },
  "uploadedBy":   "someone@example.com",
  "createdAt":    1758499200,

  "modelVersion": "v1",
  "modelLoadMs":  410,
  "processingMs": 2180,
  "coldStart":    false,
  "frameCount":   null,
  "errorReason":  null,
  "ttl":          1758502800
}
```

**Field conventions**

- `fileId` is the SHA-256 of the file **content**, not its name. It serves as partition key, S3 object key, and deduplication token at once.
- `s3Key` and `thumbKey` store object keys, **not URLs**. URLs are signed at read time. The original stored full URLs and recovered the key by string-splitting, which is brittle.
- `ttl` is set only while `status` is `PENDING`. The transition to `DONE` **removes** the attribute (via `REMOVE` in the update expression), making the record permanent.
- An empty `tags` map is a valid result — an image containing no animals — and is distinct from `null`.
- `frameCount` is populated for video only.

### 4.2 State transitions

| From | To | Written by | Condition |
|---|---|---|---|
| — | `PENDING` | `wildlens-upload` | `attribute_not_exists(fileId)` |
| `PENDING` | `PROCESSING` | `wildlens-process` | `status = PENDING` |
| `PROCESSING` | `DONE` | `wildlens-process` | `status <> DONE` ← **the idempotency guard** |
| `PROCESSING` | `FAILED` | `wildlens-process` | Permanent error, or retries exhausted |

The condition on the third row is what makes processing idempotent. Standard SQS delivers *at least once*; on a redelivery the second write is rejected with `ConditionalCheckFailedException`. The worker catches that specific exception, logs at INFO, and exits successfully — it must **not** raise, because raising would return the message to the queue.

---

## 5. API contract

Every route sits behind the Cognito authoriser. All responses are JSON.

| Route | Request body | Response |
|---|---|---|
| `POST /upload` | `{sha256, ext, size}` | `{duplicate: bool, uploadUrl?, key?}` |
| `GET /files/{fileId}` | — | `{status, tags?, thumbUrl?, fullUrl?, errorReason?}` |
| `POST /search/tags` | `{"wombat": 2, "magpie": 1}` | `{results: [{fileId, thumbUrl, fullUrl, tags}]}` |
| `POST /search/species` | `["dingo", "koala"]` | as above |
| `POST /search/byfile` | `{file: "<base64>", ext}` | as above |
| `POST /subscribe` | `{email, tag, operation}` | `{ok, tags}` |
| `POST /files/delete` | `{fileIds: [...]}` | `{ok, deleted}` |
| `POST /resolve` *(GCP)* | `{thumbUrl}` | `{fullUrl}` |

**Search semantics**

- `/search/tags` — logical **AND** with minimum counts. A file matches when, for every `(species, n)` pair in the request, `tags[species] >= n`.
- `/search/species` — logical **OR**. A file matches when it contains **any** listed species with a count of at least one.
- `/search/byfile` — runs inference on the submitted file and returns files whose tag set is a **superset** of the query file's tags. **The query file is never persisted.**

`/search/byfile` is served by `wildlens-search`, which does **not** load models itself. It invokes `wildlens-process` synchronously with `{"query_mode": true, "file_bytes": "<base64>", "ext": "jpg"}`; the processing Lambda returns `{"tags": {...}}` and writes nothing to DynamoDB, S3, or SNS. Only one function in the system carries the models.

All queries scan the table and filter in memory. This is a deliberate choice at this scale (under a few thousand records) and is recorded as an ADR, together with the threshold at which a secondary index becomes warranted.

---

## 6. ML pipeline

### 6.1 Two stages

1. **MegaDetector v5a** takes the full frame and returns bounding boxes with a class: `1` animal, `2` person, `3` vehicle. Only class `1` is processed, at a confidence threshold of 0.05.
2. **SpeciesNet** takes each crop, upscaled to 600×600 then resized to 480×480, **permuted to channels-last `(B,H,W,C)`**, and returns logits over 46 classes. The highest-probability class wins.
3. **Label mapping** — `labels.txt` maps scientific names (`Sus_scrofa`) to common names (`wild boar`), using columns 4, 5 and 6 (genus, species, common name).

The `CLASSES` list holds 46 entries and **its order must match the model's output order**. It must never be sorted.

### 6.2 Model loading

```
Default:   /opt/models/v1/{mdv5a.pt, model.pt, labels.txt}   ← baked into the image
Override:  s3://wildlens-models-<suffix>/<MODEL_VERSION>/    ← when MODEL_VERSION != "v1"
```

`model_loader.get_models(version)` returns from an in-memory cache when warm; otherwise it loads from disk (default) or downloads from S3 (override). The cache is keyed by version string, so one warm execution environment can hold several versions at once.

This preserves swapping models without a code change — set an environment variable, no image rebuild — while removing a 470 MB download from every cold start.

### 6.3 Video

Sample one frame per second, **capped at 60 frames**. Tag each frame and sum the per-species counts. The thumbnail comes from the first frame. Longer video is truncated, and `frameCount` records how many frames were actually processed.

### 6.4 Accuracy evaluation

`ml/eval/run_eval.py` runs the pipeline over the 26 test images, compares against `ml/eval/answer_key.yaml`, and emits:

- Per-species precision, recall and F1
- A confusion matrix across species
- Count error, over- and under-counting
- Mean processing time per image

Results are written to `ml/eval/reports/<version>.md` and committed. When ML code changes, CI reruns the harness and posts the comparison table as a pull-request comment.

**Canonical check:** `Sus_scrofa_1.JPG` must yield exactly `{"wild boar": 1}`.

---

## 7. Failure handling

### 7.1 Error taxonomy

| Class | Example | Handling |
|---|---|---|
| **Transient** | S3 throttling, network blip, DynamoDB throttle | Raise, let SQS retry (up to 3) |
| **Permanent** | Corrupt file, unsupported format, zero-byte image | Write `FAILED` with `errorReason`, **delete the message, do not retry** |
| **Oversized** | Very long video | Truncate at 60 frames, treat as success, record `frameCount` |
| **Duplicate** | Message delivered twice | Catch `ConditionalCheckFailedException`, log at INFO, exit cleanly |

Separating transient from permanent is the core of this section: retrying a corrupt file three times pays three times for work that is certain to fail.

### 7.2 Queue configuration

| Setting | Value | Reason |
|---|---|---|
| Visibility timeout | 5400 s | 6 × the function's 900 s maximum runtime |
| `maxReceiveCount` | 3 | Three failures is enough to conclude |
| DLQ retention | 14 days | The SQS maximum; ample time to inspect |
| Batch size | 1 | One file per invocation; simplifies idempotency |
| Reserved concurrency | 2 | The hard spend ceiling |

### 7.3 Alarms

| Alarm | Threshold | Action |
|---|---|---|
| DLQ not empty | `ApproximateNumberOfMessagesVisible >= 1` | Email, then follow `docs/runbook.md` |
| Function errors | More than 3 in 5 minutes | Email |
| Records stuck PENDING | More than 10 older than 15 minutes | Email |
| Budget | $10 / $25 / $50 | Email from AWS Budgets |

---

## 8. Observability

**Structured logging.** Every log line is single-line JSON carrying `fileId` as the correlation ID. One CloudWatch Logs Insights query traces a file across every function.

**Distributed tracing.** X-Ray enabled on API Gateway and all functions, with subsegments around model load, MegaDetector, SpeciesNet, thumbnail generation and the DynamoDB write. This answers "where did those forty seconds go" without guessing.

**Custom metrics** — limited to four to control cost, at roughly $0.30 per metric per month:
`ColdStartDuration`, `ModelLoadDuration`, `InferenceDuration`, `TagsPerFile`

**Dashboard.** One board: files processed per hour, queue depth, DLQ depth, error rate, p50/p95/p99 processing time, cold-start ratio.

**Log retention.** 14 days on every log group, declared explicitly in Terraform. The CloudWatch default is to retain forever, which is a quiet and growing cost.

---

## 9. Security

| Concern | Control |
|---|---|
| API access | Cognito authoriser on every route, no exceptions |
| CI access | OIDC federation with short-lived IAM roles; **no AWS access keys stored** |
| Bucket access | Public access blocked on all four buckets. Every read goes through a time-limited signed URL |
| IAM | A dedicated role per function, scoped to exactly what it uses. No shared role |
| Secrets | None. Configuration lives in Lambda environment variables |
| Infrastructure scanning | `checkov` on every pull request |
| Root account | MFA enabled, never used for work. All operations through a dedicated IAM user |

The original coursework required every Lambda to share a single `LabRole`, an AWS Academy constraint. This build uses a role per function — which is what an interviewer wants to hear, and is itself an ADR.

---

## 10. Testing

| Layer | What it checks | When it runs | Tooling |
|---|---|---|---|
| Unit | Hashing, tag merging, error classification, thumbnail sizing | Every commit | `pytest` with models mocked |
| Infrastructure | Terraform syntax, formatting, security findings | Every pull request | `terraform validate`, `tflint`, `checkov` |
| Integration | Real upload, poll, assert the tags | After every deploy | `pytest` against the live API |
| ML accuracy | 26 test images against the answer key | On ML changes | `ml/eval/run_eval.py` |

Unit tests must **not** depend on the real `.pt` files. Models are mocked so that CI runs in seconds rather than minutes.

---

## 11. Repository layout

```
wildlens/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── adr/
│   ├── runbook.md
│   ├── design/
│   └── diagrams/
├── infra/terraform/
│   ├── envs/dev/
│   ├── modules/{storage,auth,api,pipeline,notify,observability}/
│   └── gcp/
├── services/
│   ├── process/                 (container Lambda)
│   ├── api/{upload,search,status,delete,subscribe}/
│   └── gcp-resolve/
├── ml/
│   ├── tagger.py
│   ├── labels.txt
│   ├── eval/{run_eval.py,answer_key.yaml,reports/}
│   └── tests/
├── web/
└── .github/workflows/{ci.yml,deploy.yml,ml-eval.yml}
```

`infra/` and `services/` are separate because they change on different cadences; CI runs only the parts a change affects.

**Never committed:** `*.pt`, `*.pth`, `*.tfstate`, `*.tfvars` holding real values, anything containing access keys.

---

## 12. Cost control

| Mechanism | What it prevents |
|---|---|
| Budget alarms at $10 / $25 / $50 | Finding out too late |
| Reserved concurrency of 2 | A retry loop multiplying workers |
| 14-day log retention | Logs accumulating indefinitely |
| ECR lifecycle keeping three images | Each build adding 4.5 GB |
| S3 lifecycle expiry at 30 days | Test uploads living forever |
| No VPC | A $32/month NAT Gateway |
| `terraform destroy` | Paying for an idle estate between sessions |

**Expected cost:** about $0.75/month idle, about $3.30/month while actively demonstrating. The $100 credit should last roughly two years.

**Bounded worst case:** two workers × 4 GB × 900 s running continuously for eight hours ≈ $4.

---

## 13. Build plan

Ordering principle: **whatever is hardest to debug is built earliest, in the environment where debugging is cheapest.**

### Milestone A — Inference running in the cloud

| Phase | Content |
|---|---|
| 0 | Toolchain, IAM user with MFA, repository, **budget alarms switched on first** |
| 1 | Terraform for buckets and the table. `apply` and `destroy` both run clean |
| 2 | ML proven locally: `tagger.py`, the 26-image test set, the evaluation harness, unit tests |
| 3 | Container, ECR, Lambda, **loading models from S3**. Baseline p95 cold start measured |

Phase 3 deliberately builds the *slow* version first. Without a "before" number, the Phase 5 improvement cannot be demonstrated.

### Milestone B — Working end to end (viable stopping point)

| Phase | Content |
|---|---|
| 4 | SQS, DLQ, S3 notification, idempotency, the four-state lifecycle |
| 5 | **Optimisation:** bake models into the image, re-measure, record the delta |
| 6 | Cognito, API Gateway, the upload/search/status functions |
| 7 | Web client with status polling |

### Milestone C — Operable, not merely working

| Phase | Content |
|---|---|
| 8 | SNS tag subscriptions |
| 9 | X-Ray, dashboard, alarms, `runbook.md` |
| 10 | GitHub Actions: CI, deploy, OIDC, checkov |

### Milestone D — Complete

| Phase | Content |
|---|---|
| 11 | GCP Cloud Run cross-cloud JWT verification, provisioned from the same Terraform |
| 12 | Video support |
| 13 | README, six ADRs, architecture diagram, demo recording |

---

## 14. Working principle

A phase is finished when the question *"why was it built this way?"* can be answered without consulting notes — not when the code first runs.

A portfolio project that cannot be defended in an interview is a liability rather than an asset. Every architectural decision therefore carries its reasoning, and that reasoning is written into an ADR rather than left in memory.

---

## 15. Known risks

| Risk | Mitigation |
|---|---|
| A 4.5 GB image makes CI slow and storage-hungry | Cached build layers; ECR lifecycle policy; build only when `services/process/**` changes |
| Apple Silicon host building for x86_64 | Always `--platform linux/amd64`, declared in the Dockerfile |
| Baked models produce a very large image layer | Accepted; the Lambda container limit is 10 GB |
| GCP requires a separate account | Phase 11 sits in Milestone D and can be dropped without weakening the project |
| Table scans do not scale | Deliberate at this scale; the ADR records the threshold for moving to an index |
| The build estimate may slip | Milestone B is a safe stopping point; C and D are additive |
