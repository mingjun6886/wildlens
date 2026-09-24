output "bucket_names" {
  description = "Bucket names keyed by role: raw, thumb, models, web."
  value       = { for role, bucket in aws_s3_bucket.this : role => bucket.id }
}

output "bucket_arns" {
  description = "Bucket ARNs keyed by role. IAM policies consume these."
  value       = { for role, bucket in aws_s3_bucket.this : role => bucket.arn }
}