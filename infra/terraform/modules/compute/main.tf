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
  name              = local.log_group
  retention_in_days = var.log_retention_days
}

# --- Function --------------------------------------------------------------

resource "aws_lambda_function" "process" {
  function_name = local.function_name
  role          = aws_iam_role.process.arn

  package_type  = "Image"
  image_uri     = "${var.image_repository_url}:${var.image_tag}"
  architectures = ["x86_64"]

  # Memory also buys CPU on Lambda: 4096 MB gives roughly two full cores, which
  # is what keeps inference at a few seconds rather than a few minutes. Paying
  # for more memory to finish sooner is often cheaper than paying for less
  # memory for longer.
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
