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
  description = <<-EOT
    Days before uploaded originals expire. 0 keeps them, which is the default.

    This was 30 as a guard against test data accumulating, and that quietly broke
    the API: a DONE record never expires, so after 30 days it still reported tags
    and still returned signed URLs to objects S3 had deleted. Test churn is handled
    by `terraform destroy` between sessions instead.
  EOT
  type        = number
  default     = 0
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

variable "allowed_origin" {
  description = <<-EOT
    Single browser origin permitted by CORS, used by both the API module's
    preflight replies and the handlers' own response headers. The two must agree:
    configuring one and not the other fails in a way that looks like CORS was
    never set up at all.
  EOT
  type        = string
  default     = "http://localhost:3000"
}

variable "web_origins" {
  description = <<-EOT
    Origins the web client is served from. Phase 7 appends the CloudFront domain
    rather than replacing the local one, so development keeps working.

    Used for the raw bucket's CORS rule. The API needs no CORS entry for these,
    because the client reaches it through a relative /api path: proxied by Vite in
    development, served by CloudFront from the API Gateway origin in production.
  EOT
  type        = list(string)
  default     = ["http://localhost:3000"]
}
