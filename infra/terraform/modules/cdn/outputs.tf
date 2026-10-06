output "domain_name" {
  description = "The hostname the site is served from."
  value       = aws_cloudfront_distribution.site.domain_name
}

output "url" {
  description = <<-EOT
    The site's origin, in the form Cognito's callback list and the raw bucket's
    CORS rule both want. Assembled here so neither has to know the scheme.
  EOT
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "distribution_id" {
  description = "Needed to invalidate index.html after a deploy."
  value       = aws_cloudfront_distribution.site.id
}
