# Four buckets, described once as data rather than four times as code.
# expire_days = 0 means "keep indefinitely".
#
# raw and thumb keep their objects, and that is a correction rather than a
# default. Both originally expired after 30 days as a guard against test data
# accumulating, which silently broke an invariant the API depends on: a DONE
# record has no ttl and lives forever, so after 30 days /files/{fileId} would
# still report DONE with tags and still hand back a thumbUrl and fullUrl — both
# pointing at objects that no longer existed. The record outlived the data it
# described, and nothing would have reported it until someone opened an old link.
#
# Two ways to restore the invariant: let objects live as long as records, or give
# records a ttl matching the bucket. The first, because this is a portfolio
# project that has to still work when somebody opens it in three months.
#
# The cost that guard was protecting against turns out not to exist. Uploads are
# capped at 25 MB, identical files are deduplicated by digest before they are ever
# sent, and a demo corpus of fifty photographs is about 150 MB — under half a cent
# a month. Test churn is handled by `terraform destroy` between sessions, which
# force_destroy below exists to make possible.
locals {
  buckets = {
    raw    = { expire_days = var.raw_retention_days }
    thumb  = { expire_days = var.thumb_retention_days }
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
# --- CORS on the raw bucket --------------------------------------------------
#
# The browser PUTs straight to S3 through a presigned URL, so that upload is a
# cross-origin request and S3 answers the preflight itself. Without this rule the
# bucket returns NoSuchCORSConfiguration, the preflight fails, and the PUT never
# happens — while the same presigned URL works perfectly from curl, because curl
# sends no Origin header and triggers no preflight.
#
# That asymmetry is the trap: the failure appears only in a browser, and the
# console message is "No 'Access-Control-Allow-Origin' header", identical to the
# message a missing API Gateway CORS rule produces. Two unrelated causes, one
# symptom.
#
# The thumbnail bucket deliberately has no rule. Thumbnails are loaded with
# <img src="...">, and an image embed is not a fetch — CORS does not apply. A rule
# there would be configuration nobody needs.
resource "aws_s3_bucket_cors_configuration" "raw" {
  bucket = aws_s3_bucket.this["raw"].id

  cors_rule {
    # PUT only. The browser never reads from this bucket: it reaches originals
    # through a presigned GET in an <img> or a link, neither of which is a fetch.
    allowed_methods = ["PUT"]
    allowed_origins = var.web_origins

    # Content-Type must be listed. The presigned URL signs that header, so the
    # browser is required to send it — and a header the client must send but the
    # CORS rule does not allow makes the preflight fail before the PUT.
    #
    # Content-Length is not listed, and must not be: it is a forbidden header
    # name that the browser sets itself and never asks permission for.
    allowed_headers = ["Content-Type"]

    # ETag is the only response header worth exposing; without it the client
    # cannot read the ETag of its own upload.
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}
