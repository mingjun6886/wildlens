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

# The address is read from SSM Parameter Store, set once by hand, rather than from a
# variable. It used to be a variable fed by an untracked terraform.tfvars, and a
# plan from anywhere without that file (CI, a fresh clone) proposed destroying the
# subscription, silently, because an empty address meant "no subscription". Now a
# missing parameter fails the plan loudly instead. The repository is public, so the
# address never appears in it; the provider marks the value sensitive, so it never
# appears in plan output either. The read-only CI role can read it (ReadOnlyAccess,
# and a String parameter needs no KMS key).
data "aws_ssm_parameter" "alert_email" {
  name = var.alert_email_parameter
}

# SNS mails a confirmation link when this is created and delivers nothing until it is
# clicked.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = data.aws_ssm_parameter.alert_email.value
}

# The subscription had count = 0 or 1 while the address was optional. Without this the
# confirmed subscription would be destroyed and recreated, and SNS would ask for
# confirmation again.
moved {
  from = aws_sns_topic_subscription.email[0]
  to   = aws_sns_topic_subscription.email
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

# The errors alarms above see only what Lambda counts as an error: a raise, a timeout,
# running out of memory, a crash at init. Most real failures are none of those. The
# API functions catch every exception in common.handle_errors and return a 500, and
# process catches a transient failure and reports it back to SQS for a retry. Both
# are deliberate, and both are invisible to AWS/Lambda Errors. The two alarms below
# watch where those failures do show up.

# Every 500 the API returns, from any function, since handle_errors turns an
# unhandled exception into one. 502 and 504 from API Gateway itself count too.
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name        = "${var.name_prefix}-api-5xx"
  alarm_description = "More than 3 server errors from the API in 5 minutes. See docs/runbook.md, An alarm fired."

  namespace   = "AWS/ApiGateway"
  metric_name = "5XXError"
  dimensions  = { ApiName = var.api_name, Stage = var.api_stage }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# process logs "transient failure on <key>, will retry" for every file it hands back
# to SQS. One file failing three times ends in the dead-letter queue and its own
# alarm; several in five minutes means something shared is down (S3, DynamoDB, the
# model bucket). This is a fifth custom metric, about $0.30 a month.
resource "aws_cloudwatch_log_metric_filter" "transient_failures" {
  name           = "${var.name_prefix}-process-transient-failures"
  log_group_name = var.process_log_group
  pattern        = "\"transient failure\""

  metric_transformation {
    name      = "TransientFailures"
    namespace = "WildLens/Process"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "transient_failures" {
  alarm_name        = "${var.name_prefix}-process-transient-failures"
  alarm_description = "More than 3 files handed back to SQS for a retry in 5 minutes. See docs/runbook.md, An alarm fired."

  namespace   = "WildLens/Process"
  metric_name = "TransientFailures"

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]

  depends_on = [aws_cloudwatch_log_metric_filter.transient_failures]
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
