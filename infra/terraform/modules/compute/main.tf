data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  function_name = "${var.name_prefix}-process"
  log_group     = "/aws/lambda/${var.name_prefix}-process"
}

# --- Execution role --------------------------------------------------------
#
# A role dedicated to this one function. The coursework this project rebuilds
# had every Lambda share a single administrator role, because AWS Academy
# permits nothing else. Outside that constraint, a shared role means a bug in
# any function can reach every resource in the account.

resource "aws_iam_role" "process" {
  name = "${local.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "process" {
  name = "${local.function_name}-policy"
  role = aws_iam_role.process.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Read the uploaded original and the model artefacts. Read only: this
        # function has no reason to modify either bucket, and saying so means a
        # bug cannot delete the source images.
        Sid      = "ReadSourceObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${var.raw_bucket_arn}/*", "${var.models_bucket_arn}/*"]
      },
      {
        # Write thumbnails, and nothing else anywhere else.
        Sid      = "WriteThumbnails"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${var.thumb_bucket_arn}/*"]
      },
      {
        # Claim and release queue messages. GetQueueAttributes is required by
        # the event source mapping itself, not by the handler.
        #
        # Absent on purpose: sqs:SendMessage. This function consumes the queue
        # and must never feed it, so a bug cannot build a loop that republishes
        # its own work.
        Sid    = "ConsumeIngestQueue"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = [var.queue_arn]
      },
      {
        # GetItem and UpdateItem only. DeleteItem is withheld: deletion belongs
        # to a separate function with its own role, and a tagging bug should not
        # be able to remove records.
        Sid    = "RecordResults"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
        ]
        Resource = [var.table_arn]
      },
      {
        # Write to its own log group only. CreateLogGroup is deliberately
        # absent: Terraform creates the group below, and a function that cannot
        # create log groups cannot create one with no retention policy.
        Sid    = "WriteOwnLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group}:*"
        ]
      },
    ]
  })
}

# --- Log group -------------------------------------------------------------
#
# Declared explicitly rather than left to Lambda's implicit creation, which
# produces a group that retains logs forever. That default is a slow, quiet
# cost that nobody notices until the bill arrives.
resource "aws_cloudwatch_log_group" "process" {
  #checkov:skip=CKV_AWS_158:Logs are encrypted at rest with the default AWS-managed key; a customer-managed key costs $1 a month and adds nothing for application logs that hold no secrets
  #checkov:skip=CKV_AWS_338:Retention is 14 days by design, to bound log storage cost (design document, observability)
  name              = local.log_group
  retention_in_days = var.log_retention_days
}

# --- Function --------------------------------------------------------------

resource "aws_lambda_function" "process" {
  #checkov:skip=CKV_AWS_115:No reservation on the function: AWS requires 100 unreserved executions account-wide and a new account has 10 in total. The ceiling for process is set on the event source mapping instead (modules/compute)
  #checkov:skip=CKV_AWS_116:A Lambda dead-letter config only applies to asynchronous invocations. The API functions are invoked synchronously and process is driven by SQS, whose own dead-letter queue handles failures (ADR 1)
  #checkov:skip=CKV_AWS_117:Deliberately outside a VPC: egress would need a NAT gateway at about $32 a month for no benefit, since every service called is a public endpoint already gated by IAM (design document AD-5)
  #checkov:skip=CKV_AWS_173:The environment holds resource names and one origin, no secrets, so the default AWS-managed key is sufficient
  #checkov:skip=CKV_AWS_272:Code signing needs a signing profile and a managed release pipeline; this function is built and deployed by its single owner
  #checkov:skip=CKV_AWS_50:Lambda X-Ray tracing is enabled in Phase 9; correlation ids in the JSON logs cover diagnosis until then
  function_name = local.function_name
  role          = aws_iam_role.process.arn

  package_type  = "Image"
  image_uri     = "${var.image_repository_url}:${var.image_tag}"
  architectures = ["x86_64"]

  # Memory also buys CPU on Lambda: more memory means a larger share of a core,
  # which is what keeps inference at seconds rather than minutes. Paying for
  # more memory to finish sooner is often cheaper than paying for less memory
  # for longer, because billing is per GB-second.
  memory_size = var.memory_mb

  # The cold path downloads 470 MB of weights before it can do any work.
  timeout = var.timeout_seconds

  # /tmp holds the model files plus the image being processed.
  ephemeral_storage {
    size = var.ephemeral_storage_mb
  }

  environment {
    variables = {
      MODEL_BUCKET  = var.models_bucket_name
      MODEL_VERSION = var.model_version
      THUMB_BUCKET  = var.thumb_bucket_name
      RAW_BUCKET    = var.raw_bucket_name
      TABLE_NAME    = var.table_name
    }
  }

  # Without this the function can start before its log group exists and create
  # one implicitly, with no retention policy — the exact outcome the explicit
  # group above is meant to prevent.
  depends_on = [
    aws_cloudwatch_log_group.process,
    aws_iam_role_policy.process,
  ]
}

# --- Queue consumer --------------------------------------------------------

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn = var.queue_arn
  function_name    = aws_lambda_function.process.arn

  # One message per invocation. Batching would amortise the poller's overhead,
  # but each message here is a multi-second ML job, and a batch that fails part
  # way through redelivers every message in it — including the ones that already
  # succeeded. Idempotency would catch that, but not paying for it is simpler.
  batch_size = 1

  # The cost ceiling, set here rather than as reserved_concurrent_executions on
  # the function.
  #
  # Two reasons. First, AWS refuses a reservation unless at least 100 unreserved
  # executions remain account-wide, and a new account is capped at 10 in total,
  # so the function-level setting is simply rejected. Second, and true whatever
  # the quota: this bounds only the queue consumer, leaving headroom for direct
  # invocations such as the search API's query mode in Phase 6.
  scaling_config {
    maximum_concurrency = var.max_queue_concurrency
  }

  # Report per-message failures instead of failing the whole batch. With a batch
  # size of one this changes little today, but it is the setting that lets batch
  # size grow later without redelivering successful work.
  function_response_types = ["ReportBatchItemFailures"]
}
