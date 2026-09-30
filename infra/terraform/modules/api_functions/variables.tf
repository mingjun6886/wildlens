variable "name_prefix" {
  description = "Prefix shared by every resource in this environment."
  type        = string
}

variable "source_dir" {
  description = <<-EOT
    Directory holding the API handlers. All three functions are packaged from one
    zip because they share common.py; see that file for why.
  EOT
  type        = string
}

variable "table_arn" { type = string }
variable "table_name" { type = string }

variable "raw_bucket_arn" { type = string }
variable "raw_bucket_name" { type = string }
variable "thumb_bucket_arn" { type = string }
variable "thumb_bucket_name" { type = string }

variable "allowed_origin" {
  description = "Origin the handlers put in Access-Control-Allow-Origin. Must match the API module."
  type        = string
  default     = "http://localhost:3000"
}

variable "log_retention_days" {
  description = "Logs are a slow, quiet cost if left to accumulate forever."
  type        = number
  default     = 14
}

variable "timeout_seconds" {
  description = <<-EOT
    These handlers do one DynamoDB call and some signing, so they finish in
    milliseconds. The timeout exists to bound a hung dependency, not to allow for
    slow work — and it stays well under API Gateway's own hard limit of 29
    seconds, so a slow function produces its own error rather than a bare 504.
  EOT
  type        = number
  default     = 10
}

variable "memory_mb" {
  description = <<-EOT
    Memory also buys CPU share, and these functions are latency-sensitive rather
    than memory-hungry: 512 MB costs little per invocation because the invocation
    is short, and it keeps JSON and signing work off the slowest CPU slice.
  EOT
  type        = number
  default     = 512
}
