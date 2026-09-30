output "invoke_arns" {
  description = <<-EOT
    Keyed by short name: status, upload, search. These are invoke_arn values, which
    already carry the arn:aws:apigateway:...:lambda:path/.../invocations wrapper an
    API Gateway integration uri wants. Named invoke_arns rather than function_arns
    so that nothing downstream mistakes them for plain function ARNs and wraps them
    a second time.
  EOT
  value       = { for key, function in aws_lambda_function.function : key => function.invoke_arn }
}

output "function_names" {
  description = "Keyed by short name. Used for the lambda:InvokeFunction permission and for logs."
  value       = { for key, function in aws_lambda_function.function : key => function.function_name }
}

output "role_names" {
  description = "One role per function, so a later phase can attach to a specific one."
  value       = { for key, role in aws_iam_role.function : key => role.name }
}
