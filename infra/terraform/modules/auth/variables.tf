variable "name_prefix" {
  description = "Prefix shared by every resource in this environment, e.g. wildlens-dev."
  type        = string
}

variable "suffix" {
  description = <<-EOT
    Random suffix shared with the S3 buckets. Needed here because a Cognito
    Hosted UI domain prefix is unique across all of AWS, not just this account.
  EOT
  type        = string
}

variable "callback_urls" {
  description = <<-EOT
    Where the Hosted UI sends the browser after a successful sign-in. Phase 7
    appends the CloudFront URL rather than replacing this, so that local
    development keeps working.

    The match is exact, including the trailing slash. A mismatch fails with
    redirect_mismatch before the login form is even shown.
  EOT
  type        = list(string)
  default     = ["http://localhost:3000"]
}

variable "logout_urls" {
  description = "Where the Hosted UI sends the browser after sign-out."
  type        = list(string)
  default     = ["http://localhost:3000"]
}

variable "token_validity_hours" {
  description = <<-EOT
    Lifetime of the ID and access tokens.

    One hour is the default and is left alone deliberately. Shorter means the
    browser refreshes more often for no security gain, since a stolen token is
    replayable for its whole life either way. Longer widens that window.
  EOT
  type        = number
  default     = 1

  validation {
    condition     = var.token_validity_hours >= 1 && var.token_validity_hours <= 24
    error_message = "Cognito accepts 5 minutes to 24 hours; this module works in whole hours."
  }
}

variable "refresh_validity_days" {
  description = "Lifetime of the refresh token, which decides how often a user must sign in again."
  type        = number
  default     = 30
}
