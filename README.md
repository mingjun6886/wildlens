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

`docs/architecture.md` and the architecture decision records under `docs/adr/`
are written up as each milestone lands.

> Built solo as a portfolio project. The problem domain originates from a
> four-person university group assignment (FIT5225, Monash University); this is
> an independent rebuild with a different architecture, infrastructure-as-code,
> CI/CD, and model-accuracy evaluation.

**Status:** in development.
