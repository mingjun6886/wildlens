data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  # One entry per function. The interesting column is `statements`: what each
  # function is allowed to touch, and nothing more. Reading them side by side is
  # the point of declaring them here rather than in three separate files.
  #
  # Phase 6 adds `upload` and `search` to this map. `status` is first because it
  # is the smallest, which makes it the right route to prove the API wiring with.
  functions = {
    status = {
      handler     = "status.handler"
      description = "GET /files/{fileId} - the poll target that makes async upload usable."
      statements = [
        {
          Sid      = "ReadOneRecord"
          Effect   = "Allow"
          Action   = ["dynamodb:GetItem"]
          Resource = [var.table_arn]
        },
        {
          # Needed even though presigning is a local computation: the signature is
          # only honoured if the signing identity itself may perform the action.
          # A presigned URL grants no more than its signer had.
          Sid      = "SignImageUrls"
          Effect   = "Allow"
          Action   = ["s3:GetObject"]
          Resource = ["${var.raw_bucket_arn}/*", "${var.thumb_bucket_arn}/*"]
        },
      ]
    }

    upload = {
      handler     = "upload.handler"
      description = "POST /upload - reserves a record and returns a presigned PUT."
      statements = [
        {
          # GetItem to answer "have we seen this digest?", UpdateItem to reserve it.
          #
          # Absent on purpose: DeleteItem. A caller must not be able to remove a
          # record by uploading, and the condition on the reservation already
          # refuses to overwrite a DONE row.
          Sid      = "ReserveARecord"
          Effect   = "Allow"
          Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
          Resource = [var.table_arn]
        },
        {
          # PutObject on the raw bucket only, and only so that the presigned URL it
          # signs is honoured: a presigned URL grants exactly what its signer held,
          # never more.
          #
          # No GetObject here. This function never reads an upload, so a bug in it
          # cannot be turned into a way to read the bucket.
          Sid      = "SignTheUpload"
          Effect   = "Allow"
          Action   = ["s3:PutObject"]
          Resource = ["${var.raw_bucket_arn}/*"]
        },
      ]
    }

    search = {
      handler     = "search.handler"
      description = "POST /search/{tags,species,byfile} - scan, filter in memory, sign URLs."
      statements = [
        {
          # Scan, because the thing being searched is a map and DynamoDB cannot index
          # into one. docs/adr/0004-scan-and-filter-in-memory.md records the threshold
          # at which that stops being the right answer (~2,000 records) and what
          # replaces it.
          #
          # Scan and nothing else: no GetItem, no write of any kind. A search cannot
          # modify a record however badly it goes wrong.
          Sid      = "ScanForMatches"
          Effect   = "Allow"
          Action   = ["dynamodb:Scan"]
          Resource = [var.table_arn]
        },
        {
          Sid      = "SignImageUrls"
          Effect   = "Allow"
          Action   = ["s3:GetObject"]
          Resource = ["${var.raw_bucket_arn}/*", "${var.thumb_bucket_arn}/*"]
        },
        {
          # The only function allowed to invoke another, and only this one target.
          #
          # /search/byfile identifies a sample without storing it, and the models
          # live in exactly one function. Loading them here instead would double the
          # memory footprint and the cold-start cost of the estate.
          Sid      = "AskTheTaggerToIdentifyASample"
          Effect   = "Allow"
          Action   = ["lambda:InvokeFunction"]
          Resource = [var.process_function_arn]
        },
      ]
    }
  }
}

# --- Package -----------------------------------------------------------------
#
# One zip, three handlers. Built by Terraform rather than by a script so that
# `terraform apply` alone is enough to deploy a code change — there is no step
# someone can forget.
data "archive_file" "api" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.module}/.build/api.zip"

  excludes = ["tests", "__pycache__", ".pytest_cache"]
}

# --- Per-function role -------------------------------------------------------

resource "aws_iam_role" "function" {
  for_each = local.functions

  name = "${var.name_prefix}-${each.key}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "function" {
  for_each = local.functions

  name = "${var.name_prefix}-${each.key}-policy"
  role = aws_iam_role.function[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(each.value.statements, [
      {
        # Write to its own log group only. logs:CreateLogGroup is withheld: the
        # group is declared below with a retention policy, and a function that
        # cannot create groups cannot create one that keeps logs forever.
        Sid    = "WriteOwnLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.name_prefix}-${each.key}:*"
        ]
      },
    ])
  })
}

# --- Log groups --------------------------------------------------------------

resource "aws_cloudwatch_log_group" "function" {
  for_each = local.functions

  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

# --- Functions ---------------------------------------------------------------

resource "aws_lambda_function" "function" {
  for_each = local.functions

  function_name = "${var.name_prefix}-${each.key}"
  description   = each.value.description
  role          = aws_iam_role.function[each.key].arn

  runtime = "python3.11"
  handler = each.value.handler

  filename = data.archive_file.api.output_path

  # Without this, Terraform compares only the filename and reports no change when
  # the code inside the zip has changed. The hash is what makes `apply` notice an
  # edit to a handler.
  source_code_hash = data.archive_file.api.output_base64sha256

  timeout     = var.timeout_seconds
  memory_size = var.memory_mb

  environment {
    variables = {
      TABLE_NAME     = var.table_name
      RAW_BUCKET     = var.raw_bucket_name
      THUMB_BUCKET   = var.thumb_bucket_name
      ALLOWED_ORIGIN = var.allowed_origin

      # Read by search.py for query mode. Set for all three functions rather than
      # per function, because a shared zip means a shared environment block and
      # splitting it would buy nothing: a name is not a credential.
      PROCESS_FUNCTION = var.process_function_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.function,
    aws_iam_role_policy.function,
  ]
}
