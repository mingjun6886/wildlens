output "queue_arn" {
  description = "Main queue ARN. The Lambda event source mapping consumes this."
  value       = aws_sqs_queue.ingest.arn
}

output "queue_url" {
  description = "Main queue URL, used when sending or purging messages by hand."
  value       = aws_sqs_queue.ingest.id
}

output "queue_name" {
  description = "Main queue name."
  value       = aws_sqs_queue.ingest.name
}

output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_url" {
  description = "Dead-letter queue URL, for inspecting failed messages."
  value       = aws_sqs_queue.dlq.id
}
