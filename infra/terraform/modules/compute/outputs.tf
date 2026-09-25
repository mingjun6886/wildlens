output "function_name" {
  description = "Lambda function name, used when invoking it directly."
  value       = aws_lambda_function.process.function_name
}

output "function_arn" {
  description = "Function ARN. The SQS event source mapping in Phase 4 consumes this."
  value       = aws_lambda_function.process.arn
}

output "role_arn" {
  description = "Execution role ARN."
  value       = aws_iam_role.process.arn
}

output "log_group" {
  description = "CloudWatch log group name."
  value       = aws_cloudwatch_log_group.process.name
}
