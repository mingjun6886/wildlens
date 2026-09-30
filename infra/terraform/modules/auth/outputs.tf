output "user_pool_id" {
  description = "Referenced by the API Gateway Cognito authoriser."
  value       = aws_cognito_user_pool.users.id
}

output "user_pool_arn" {
  description = "The authoriser identifies the pool by ARN, not by id."
  value       = aws_cognito_user_pool.users.arn
}

output "client_id" {
  description = "The browser needs this to start the Hosted UI flow. Not a secret."
  value       = aws_cognito_user_pool_client.web.id
}

output "hosted_ui_domain" {
  description = "Domain prefix of the Cognito-hosted sign-in page."
  value       = aws_cognito_user_pool_domain.hosted_ui.domain
}

output "hosted_ui_url" {
  description = <<-EOT
    The sign-in URL, assembled here so that nothing downstream has to know how
    Cognito builds it. Phase 7's client config reads this output rather than
    hard-coding a pattern that AWS may change.
  EOT
  value       = "https://${aws_cognito_user_pool_domain.hosted_ui.domain}.auth.${data.aws_region.current.region}.amazoncognito.com"
}

output "jwks_url" {
  description = <<-EOT
    Where the public signing keys live. API Gateway fetches these itself, but
    Phase 11's GCP function verifies the same token independently and needs the
    URL explicitly — that independence is the point of the cross-cloud check.
  EOT
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.users.id}/.well-known/jwks.json"
}
