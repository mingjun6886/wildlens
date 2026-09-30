
output "ecr_repository_url" {
  description = "Push the container image here; Lambda pulls it from the same URL."
  value       = module.registry.repository_url
}

output "process_function_name" {
  description = "Invoke this to tag an object."
  value       = module.compute.function_name
}

output "process_log_group" {
  description = "Where the function writes its structured logs."
  value       = module.compute.log_group
}

output "ingest_queue_url" {
  description = "Main queue. S3 publishes here, the process function consumes."
  value       = module.queue.queue_url
}

output "dlq_url" {
  description = "Dead-letter queue. Inspect this when the DLQ alarm fires."
  value       = module.queue.dlq_url
}

output "cognito_user_pool_id" {
  description = "Used by the API Gateway authoriser and by the GCP verifier in Phase 11."
  value       = module.auth.user_pool_id
}

output "cognito_client_id" {
  description = "The browser starts the Hosted UI flow with this. Not a secret."
  value       = module.auth.client_id
}

output "cognito_hosted_ui_url" {
  description = "Sign-in page. Open this to obtain a token for testing the API."
  value       = module.auth.hosted_ui_url
}

output "cognito_jwks_url" {
  description = "Public signing keys. Phase 11's GCP function verifies tokens against these."
  value       = module.auth.jwks_url
}
