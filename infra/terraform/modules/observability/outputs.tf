output "alerts_topic_arn" {
  description = "Every alarm acts on this topic. The queue module's dead-letter alarm is wired to it too."
  value       = aws_sns_topic.alerts.arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard."
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}
