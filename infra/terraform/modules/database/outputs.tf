output "table_name" {
  description = "DynamoDB table name. Lambda functions read this from an environment variable."
  value       = aws_dynamodb_table.files.name
}

output "table_arn" {
  description = "DynamoDB table ARN. IAM policies consume this."
  value       = aws_dynamodb_table.files.arn
}