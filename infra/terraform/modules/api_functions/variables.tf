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

# Timeouts are set per function, in the map in main.tf, not here. Two of the three
# handlers do one DynamoDB call and some signing and finish in milliseconds; the
# third waits on an ML inference that takes up to twenty seconds. A single shared
# value is how the third one came to be killed before the timeout it was written
# to respect — see the note next to `search`.

variable "memory_mb" {
  description = <<-EOT
    Memory also buys CPU share, and these functions are latency-sensitive rather
    than memory-hungry: 512 MB costs little per invocation because the invocation
    is short, and it keeps JSON and signing work off the slowest CPU slice.
  EOT
  type        = number
  default     = 512
}

variable "process_function_arn" {
  description = "The tagging function, invoked synchronously by /search/byfile."
  type        = string
}

variable "process_function_name" {
  description = "Same function, by name: what the Invoke call takes."
  type        = string
}
