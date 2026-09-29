variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "raw_bucket_name" {
  description = "Bucket whose ObjectCreated events feed the queue."
  type        = string
}

variable "raw_bucket_arn" {
  description = "ARN of that bucket, used to pin the queue policy to one sender."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a claimed message stays hidden. Must be at least six times the consuming function's timeout, measured against its maximum rather than its average."
  type        = number
  default     = 5400

  validation {
    condition     = var.visibility_timeout_seconds >= 5400
    error_message = "The consumer can run for 900s, so anything below 5400s risks SQS redelivering work that is still in progress."
  }
}

variable "max_receive_count" {
  description = "Attempts before a message moves to the dead-letter queue."
  type        = number
  default     = 3
}

variable "alarm_topic_arns" {
  description = "SNS topics notified when the dead-letter queue is not empty. Empty until Phase 8 creates the topic."
  type        = list(string)
  default     = []
}
