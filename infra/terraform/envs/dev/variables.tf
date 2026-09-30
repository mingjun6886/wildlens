variable "project" {
  description = "Prefix for every resource name."
  type        = string
  default     = "wildlens"
}

variable "environment" {
  description = "Deployment environment. Part of resource names and tags."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region. Chosen for proximity to users in Australia."
  type        = string
  default     = "ap-southeast-2"
}

variable "raw_retention_days" {
  description = "Days before uploaded originals expire. Keeps test data from accumulating."
  type        = number
  default     = 30
}
variable "account_id" {
  description = "AWS account this environment belongs to. Terraform refuses to run anywhere else."
  type        = string
  default     = "895770859102"
}

variable "web_callback_urls" {
  description = <<-EOT
    Origins allowed to complete a Cognito sign-in. Local development only until
    Phase 7 adds the CloudFront distribution; appended to rather than replaced,
    so that running the client locally keeps working afterwards.
  EOT
  type        = list(string)
  default     = ["http://localhost:3000"]
}
