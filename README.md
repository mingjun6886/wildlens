# WildLens

Serverless wildlife observation platform on AWS. Camera-trap images and video
are uploaded, tagged with species and counts by a two-stage ML pipeline
(MegaDetector → SpeciesNet), and made queryable through a Cognito-protected
REST API.

**[▶ Interactive architecture walkthrough](https://mingjun6886.github.io/wildlens/diagrams/pipeline.html)**
— step through all thirteen stages of the ingest path, the queue semantics, and
the cost guardrails.

## What makes this more than a demo

- **Infrastructure as code** — every resource in Terraform, including the GCP
  component. `terraform destroy` tears the whole estate down between sessions.
- **Cost-bounded ingest** — S3 events land in SQS rather than invoking Lambda
  directly, with a reserved concurrency of 2 and a dead-letter queue. The worst
  realistic runaway costs about $4 instead of about $80.
- **Measured, not asserted** — cold-start latency is benchmarked before and
  after optimisation, and model accuracy is scored against a 26-image answer
  key on every change to the ML code.
- **Operable** — structured JSON logs with a correlation ID, X-Ray tracing,
  a CloudWatch dashboard, alarms on DLQ depth, and a runbook.
- **No long-lived secrets** — CI authenticates to AWS through OIDC federation.

## Measured results

Latency, tagging `Sus_scrofa_1.JPG` on a 3008 MB container Lambda in
`ap-southeast-2`. The optimisation is a single change: the module cache holds the
loaded MegaDetector rather than a path to its weights.

| | Before | After | Change |
|---|---|---|---|
| First invocation after deploy | 55.2 s | **42.3 s** | −23% |
| Cold start, p50 | 23.6 s | **21.5 s** | −9% |
| Warm | 8.35 s | **6.40 s** | −23% |
| Peak memory | 3000 MB | **2861 MB** | −139 MB |

Cold improved far less than predicted, for a reason worth stating: a cache cannot
help the invocation that populates it. The change moved a per-request cost to a
per-environment one, which is why warm gained and cold barely did. Re-measuring
also exposed a mis-read log line in the baseline report that had over-estimated
the expected cold-start saving tenfold — recorded in
[`docs/benchmarks/`](docs/benchmarks/) rather than quietly fixed.

Accuracy, scored per individual against a 26-image hand-checked answer key:

| Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|
| 0.821 | 1.000 | 0.901 | 32 | 7 | 0 |

Recall is 1.000: nothing in the key was missed. The seven false positives are
over-counts, and two close-range cattle frames account for five of them — frames
where the answer key itself carries real uncertainty, documented in
`ml/eval/answer_key.yaml`.

## Architecture

| Layer | Services |
|---|---|
| Client | S3 static site behind CloudFront |
| Auth | Cognito user pool, Hosted UI, authoriser on every route |
| API | API Gateway REST + Python 3.11 Lambdas |
| Ingest | S3 → SQS (+ DLQ) → container Lambda, 4 GB / 900 s |
| Data | DynamoDB on-demand, TTL-based cleanup of abandoned uploads |
| Notify | SNS with per-subscriber filter policies |
| Second cloud | GCP Cloud Run verifying the Cognito JWT via JWKS |

Full design spec: [`docs/design/`](docs/design/) —
goals, architecture decisions, data model, API contract, ML pipeline, error
handling, observability, cost controls and build plan.

Why it was built this way: [`docs/adr/`](docs/adr/) — eight decision records so
far, written when each decision was taken rather than at the end. Three of them
record a decision that was reversed by a measurement, which is kept rather than
tidied away.

Latency measurements, before and after each optimisation:
[`docs/benchmarks/`](docs/benchmarks/).

> Built solo as a portfolio project. The problem domain originates from a
> four-person university group assignment (FIT5225, Monash University); this is
> an independent rebuild with a different architecture, infrastructure-as-code,
> CI/CD, and model-accuracy evaluation.

**Status:** in development.
