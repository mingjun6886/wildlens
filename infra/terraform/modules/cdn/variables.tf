variable "name_prefix" {
  description = "Prefix shared by every resource in this environment."
  type        = string
}

variable "web_bucket_id" {
  description = "Bucket holding the built site. Stays private; CloudFront reads it through OAC."
  type        = string
}

variable "web_bucket_arn" { type = string }

variable "web_bucket_regional_domain_name" {
  description = <<-EOT
    The regional domain, not the global one. The global form
    (bucket.s3.amazonaws.com) redirects for a bucket outside us-east-1, and
    CloudFront does not follow it — the result is a 301 served to the viewer.
  EOT
  type        = string
}

variable "api_domain_name" {
  description = "API Gateway's execute-api host, without a scheme or path."
  type        = string
}

variable "api_path_prefix" {
  description = <<-EOT
    Path prefix routed to the API instead of to the site. It is the API Gateway
    stage name, which is what lets CloudFront forward the path unchanged.

    CloudFront cannot rewrite a path without a CloudFront Function. Naming the
    prefix after the stage removes the need for one: a request for /v1/files/x
    reaches API Gateway as /v1/files/x, which is stage v1 and resource /files/x.
  EOT
  type        = string
}

variable "price_class" {
  description = <<-EOT
    PriceClass_200 includes Asia Pacific. PriceClass_100 is cheaper and covers
    only North America and Europe, which for users in Australia would mean every
    request crossing the Pacific — the opposite of the reason this exists.
  EOT
  type        = string
  default     = "PriceClass_200"
}
