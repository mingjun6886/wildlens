data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
}

# --- Where alarms go ---------------------------------------------------------
#
# Separate from the tags topic: a topic that users subscribe to must never carry
# operator alerts, and the reverse.
resource "aws_sns_topic" "alerts" {
  #checkov:skip=CKV_AWS_26:Deliberately unencrypted. A CloudWatch alarm cannot publish to a topic encrypted with the AWS-managed key alias/aws/sns, because that key's policy cannot be changed to allow CloudWatch, and the failure is silent: the alarm fires and no mail arrives. A customer-managed key would cost $1 a month. An alert holds a resource name, a metric and a threshold, nothing a user submitted
  name = "${var.name_prefix}-alerts"
}

# Created only when an address is given. SNS mails a confirmation link on the first
# apply and delivers nothing until it is clicked.
resource "aws_sns_topic_subscription" "email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Alarms ------------------------------------------------------------------
#
# All use treat_missing_data = notBreaching, as the dead-letter queue alarm does: an
# idle estate reports no data, and an alarm stuck in INSUFFICIENT_DATA trains
# everyone to ignore it. None sends an OK notification: the runbook says to look at
# the dashboard, and a second mail per incident teaches the owner to ignore both.

resource "aws_cloudwatch_metric_alarm" "function_errors" {
  for_each = var.function_names

  alarm_name        = "${var.name_prefix}-${each.key}-errors"
  alarm_description = "More than 3 errors from ${each.value} in 5 minutes. See docs/runbook.md, An alarm fired."

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  dimensions  = { FunctionName = each.value }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Replaces the design document's "more than 10 records stuck PENDING". CloudWatch has
# no per-status count for DynamoDB, a PENDING record expires through its own
# one-hour TTL, and abandoned uploads would make that alarm noisy. A file waiting in
# the queue is the failure that matters, and its age is a native metric.
#
# Blind spot: if the S3 to SQS notification itself breaks, the queue is empty and
# its age is zero, so this stays quiet. The notification is Terraform-managed, so a
# plan shows the drift.
resource "aws_cloudwatch_metric_alarm" "queue_oldest_message" {
  alarm_name        = "${var.name_prefix}-queue-oldest-message"
  alarm_description = "A file has waited in the ingest queue longer than ${var.queue_age_threshold_seconds} seconds. See docs/runbook.md, An alarm fired."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateAgeOfOldestMessage"
  dimensions  = { QueueName = var.queue_name }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.queue_age_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}
