
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

output "api_invoke_url" {
  description = "Base URL of the API. Append /files/{fileId} and so on."
  value       = module.api.invoke_url
}

# --- Named for the runbook ---------------------------------------------------
#
# docs/runbook.md reads every name from here rather than hard-coding one, because
# the bucket suffix is regenerated whenever the estate is destroyed and recreated.
# A procedure that embeds a name is wrong the first time somebody runs destroy.

output "rest_api_id" {
  description = "Needed to force a stage redeployment by hand. See the runbook."
  value       = module.api.rest_api_id
}

output "raw_bucket_name" {
  description = "Uploaded originals."
  value       = module.storage.bucket_names["raw"]
}

output "thumb_bucket_name" {
  description = "Generated thumbnails."
  value       = module.storage.bucket_names["thumb"]
}

output "models_bucket_name" {
  description = "Model artefacts, one prefix per version."
  value       = module.storage.bucket_names["models"]
}

output "table_name" {
  description = "The records table."
  value       = module.database.table_name
}

output "site_url" {
  description = "The deployed site. Serves both the client and, under the stage prefix, the API."
  value       = module.cdn.url
}

output "cdn_distribution_id" {
  description = "Needed to invalidate index.html after a deploy. Used by scripts/deploy-web.sh."
  value       = module.cdn.distribution_id
}

output "web_bucket_name" {
  description = "Where the built site is synced. Private; CloudFront reads it through OAC."
  value       = module.storage.bucket_names["web"]
}
