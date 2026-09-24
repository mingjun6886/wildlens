# Four buckets, described once as data rather than four times as code.
# expire_days = 0 means "keep indefinitely".
locals {
  buckets = {
    raw    = { expire_days = var.raw_retention_days }
    thumb  = { expire_days = var.raw_retention_days }
    models = { expire_days = 0 }
    web    = { expire_days = 0 }
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket = "${var.name_prefix}-${each.key}-${var.suffix}"

  # Dev only. Lets `terraform destroy` remove a bucket that still holds test
  # objects, which is what makes the destroy/apply cycle reproducible. A
  # production bucket would never carry this.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  # A multipart upload that never completes leaves its parts in the bucket.
  # They are invisible in the console and billed like any other storage. This
  # rule applies to every bucket, including the ones that keep objects forever.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Only the buckets with a retention period get an expiry rule.
  dynamic "rule" {
    for_each = each.value.expire_days > 0 ? [each.value.expire_days] : []

    content {
      id     = "expire-objects"
      status = "Enabled"

      filter {}

      expiration {
        days = rule.value
      }
    }
  }
}