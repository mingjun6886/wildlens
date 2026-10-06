output "bucket_names" {
  description = "Bucket names keyed by role: raw, thumb, models, web."
  value       = { for role, bucket in aws_s3_bucket.this : role => bucket.id }
}

output "bucket_arns" {
  description = "Bucket ARNs keyed by role. IAM policies consume these."
  value       = { for role, bucket in aws_s3_bucket.this : role => bucket.arn }
}
output "bucket_ids" {
  description = "Bucket ids, for attaching a policy."
  value       = { for key, bucket in aws_s3_bucket.this : key => bucket.id }
}

output "bucket_regional_domain_names" {
  description = <<-EOT
    The regional domain of each bucket, which is what a CloudFront origin needs.

    Not the global form: bucket.s3.amazonaws.com redirects for a bucket outside
    us-east-1, and CloudFront does not follow the redirect — it passes the 301 to
    the viewer, which looks like a broken site rather than a misconfigured origin.
  EOT
  value       = { for key, bucket in aws_s3_bucket.this : key => bucket.bucket_regional_domain_name }
}
