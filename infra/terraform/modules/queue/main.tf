data "aws_caller_identity" "current" {}

# --- Dead-letter queue ------------------------------------------------------
#
# Declared first because the main queue's redrive policy references its ARN.
#
# Nothing consumes this queue. That is the point: a message here is a message a
# human needs to look at, and an automatic consumer would quietly hide exactly
# the failures worth seeing. docs/runbook.md covers what to do when one arrives.
resource "aws_sqs_queue" "dlq" {
  name = "${var.name_prefix}-ingest-dlq"

  # The SQS maximum. A poison message discovered on a Monday should still be
  # there after a fortnight's holiday.
  message_retention_seconds = 1209600
}

# --- Main queue -------------------------------------------------------------

resource "aws_sqs_queue" "ingest" {
  name = "${var.name_prefix}-ingest"

  # The single most dangerous setting on this queue.
  #
  # While a consumer holds a message, SQS hides it for this long. If the worker
  # is still running when the window closes, SQS assumes it died and hands the
  # message to another worker — which starts a second copy of a job that was
  # never failing. Under load that compounds into exactly the runaway the queue
  # exists to prevent.
  #
  # The rule is at least six times the function's timeout. The function is
  # capped at 900s, so 5400s. Note this is sized against the function's MAXIMUM,
  # not its measured average of 8-24s: a video, or an image with many animals to
  # classify, can approach the ceiling.
  visibility_timeout_seconds = var.visibility_timeout_seconds

  # Three attempts is enough to distinguish a transient failure from a file that
  # will never process. A fourth attempt pays a fourth time for the same answer.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  # Long polling. With a zero wait time the Lambda poller returns immediately
  # and asks again, turning an idle queue into a steady stream of billable
  # requests. Twenty seconds is the maximum and cuts idle polling to a trickle.
  receive_wait_time_seconds = 20
}

# --- Letting S3 publish into the queue --------------------------------------
#
# S3 is a service principal, not an IAM user, so it needs a resource policy on
# the queue itself. The two conditions matter: aws:SourceArn pins the sender to
# one specific bucket, and aws:SourceAccount stops another account whose bucket
# happens to share the name from publishing here. Without them, any S3 bucket
# anywhere could feed this queue.
resource "aws_sqs_queue_policy" "allow_s3" {
  queue_url = aws_sqs_queue.ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowRawBucketToPublish"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = var.raw_bucket_arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# --- S3 event notification --------------------------------------------------

resource "aws_s3_bucket_notification" "raw_uploads" {
  bucket = var.raw_bucket_name

  queue {
    queue_arn = aws_sqs_queue.ingest.arn
    events    = ["s3:ObjectCreated:*"]
  }

  # S3 validates that it can publish when the notification is created, so the
  # queue policy has to exist first or the apply fails with a permissions error
  # that reads as though the queue were misconfigured.
  depends_on = [aws_sqs_queue_policy.allow_s3]
}

# --- Alarm on the dead-letter queue -----------------------------------------
#
# One message in here means a file could not be processed after three attempts.
# Nothing else in the system will say so.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name        = "${var.name_prefix}-dlq-not-empty"
  alarm_description = "A message failed three times and was moved to the dead-letter queue. See docs/runbook.md."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions  = { QueueName = aws_sqs_queue.dlq.name }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  # A queue that has never received a message reports no data at all. Treating
  # that as OK avoids an alarm that sits permanently in INSUFFICIENT_DATA and
  # trains everyone to ignore it.
  treat_missing_data = "notBreaching"

  alarm_actions = var.alarm_topic_arns
}
