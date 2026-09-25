output "repository_url" {
  description = "Registry URL that docker push targets and Lambda pulls from."
  value       = aws_ecr_repository.process.repository_url
}

output "repository_name" {
  description = "Repository name, used by the build script when logging in."
  value       = aws_ecr_repository.process.name
}
