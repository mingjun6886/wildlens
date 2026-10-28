output "invoke_url" {
  description = "Base URL for every route. Append /files/{fileId}, /upload and so on."
  value       = aws_api_gateway_stage.stage.invoke_url
}

output "rest_api_id" {
  description = "Needed to redeploy a stage by hand."
  value       = aws_api_gateway_rest_api.api.id
}

output "execution_arn" {
  description = "Scope for lambda:InvokeFunction permissions granted to this API."
  value       = aws_api_gateway_rest_api.api.execution_arn
}

output "rest_api_name" {
  description = "The ApiName dimension of the AWS/ApiGateway metrics, used by the dashboard."
  value       = aws_api_gateway_rest_api.api.name
}
