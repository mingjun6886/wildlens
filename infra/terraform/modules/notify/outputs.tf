output "topic_arn" {
  description = "Where process publishes and where subscribe manages subscriptions."
  value       = aws_sns_topic.tags.arn
}
