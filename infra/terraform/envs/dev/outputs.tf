
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
