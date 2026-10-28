# One board. The grid is 24 columns wide; every widget here is half a row or a full
# row. The custom metrics have no dimensions (services/process/metrics.py), so their
# metric rows carry only a namespace and a name.

locals {
  process = var.function_names["process"]

  widgets = [
    {
      type = "metric", x = 0, y = 0, width = 12, height = 6
      properties = {
        title   = "Files tagged per hour"
        region  = local.region
        view    = "timeSeries"
        period  = 3600
        stat    = "SampleCount"
        metrics = [["WildLens/Process", "TagsPerFile"]]
      }
    },
    {
      type = "metric", x = 12, y = 0, width = 12, height = 6
      properties = {
        title  = "Queue depth and dead-letter queue depth"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Maximum"
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name, { label = "ingest" }],
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name, { label = "dead-letter" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 6, width = 12, height = 6
      properties = {
        title  = "Age of the oldest queued message (s)"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Maximum"
        metrics = [
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.queue_name],
        ]
        annotations = {
          horizontal = [{ label = "alarm", value = var.queue_age_threshold_seconds }]
        }
      }
    },
    {
      type = "metric", x = 12, y = 6, width = 12, height = 6
      properties = {
        # Errors alone misses most failures: process catches a transient one and hands
        # it back to SQS, which Lambda does not count. Both are shown.
        title  = "Tagging function failures per 5 minutes"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Sum"
        metrics = [
          ["WildLens/Process", "TransientFailures", { label = "handed back to SQS for a retry" }],
          ["AWS/Lambda", "Errors", "FunctionName", local.process, { label = "crashed, timed out or ran out of memory" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 12, width = 12, height = 6
      properties = {
        title  = "Tagging function duration (ms)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["AWS/Lambda", "Duration", "FunctionName", local.process, { stat = "p50", label = "p50" }],
          ["AWS/Lambda", "Duration", "FunctionName", local.process, { stat = "p95", label = "p95" }],
          ["AWS/Lambda", "Duration", "FunctionName", local.process, { stat = "p99", label = "p99" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 12, width = 12, height = 6
      properties = {
        title  = "Cold-start ratio (% of invocations that loaded the model)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          [{ expression = "100 * FILL(loads, 0) / invocations", label = "cold", id = "ratio" }],
          ["WildLens/Process", "ModelLoadDuration", { id = "loads", stat = "SampleCount", visible = false }],
          ["AWS/Lambda", "Invocations", "FunctionName", local.process, { id = "invocations", stat = "Sum", visible = false }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 18, width = 12, height = 6
      properties = {
        title  = "Model load and inference time (ms)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["WildLens/Process", "ModelLoadDuration", { stat = "Average", label = "model load, average" }],
          ["WildLens/Process", "ModelLoadDuration", { stat = "Maximum", label = "model load, max" }],
          ["WildLens/Process", "InferenceDuration", { stat = "Average", label = "inference, average" }],
          ["WildLens/Process", "InferenceDuration", { stat = "Maximum", label = "inference, max" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 18, width = 12, height = 6
      properties = {
        title  = "API requests, errors and latency"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["AWS/ApiGateway", "Count", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "Sum", label = "requests" }],
          ["AWS/ApiGateway", "4XXError", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "Sum", label = "4xx" }],
          ["AWS/ApiGateway", "5XXError", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "Sum", label = "5xx" }],
          ["AWS/ApiGateway", "Latency", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "p95", label = "latency p95 (ms)", yAxis = "right" }],
        ]
      }
    },
    {
      type = "log", x = 0, y = 24, width = 24, height = 6
      properties = {
        title  = "/search/byfile calls per hour, and how many were cold (ADR 5)"
        region = local.region
        view   = "table"
        query  = "SOURCE '${var.process_log_group}' | filter msg = 'query tagged' | stats count(*) as calls, sum(coldStart) as cold by bin(1h) | sort bin(1h) desc"
      }
    },
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({ widgets = local.widgets })
}
