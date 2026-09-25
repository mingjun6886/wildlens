variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "image_repository_url" {
  description = "ECR repository URL holding the container image."
  type        = string
}

variable "image_tag" {
  description = "Image tag to deploy. Terraform sees no change when this stays the same, so pushing a new image under an existing tag needs an explicit function update."
  type        = string
  default     = "latest"
}

variable "raw_bucket_arn" {
  description = "ARN of the bucket holding uploaded originals. Read only."
  type        = string
}

variable "models_bucket_arn" {
  description = "ARN of the bucket holding model artefacts. Read only."
  type        = string
}

variable "models_bucket_name" {
  description = "Name of the models bucket, passed to the function as MODEL_BUCKET."
  type        = string
}

variable "thumb_bucket_arn" {
  description = "ARN of the thumbnail bucket. Write only."
  type        = string
}

variable "thumb_bucket_name" {
  description = "Name of the thumbnail bucket, passed to the function as THUMB_BUCKET."
  type        = string
}

variable "model_version" {
  description = "S3 prefix the model artefacts are read from. Changing this swaps models without touching code or rebuilding the image."
  type        = string
  default     = "v1"
}

variable "memory_mb" {
  description = "Memory, which on Lambda also determines CPU share."
  type        = number
  default     = 3008

  # The design calls for 4096 MB, but this account is new and carries the
  # reduced quotas AWS applies until an account has billing history: memory is
  # capped at 3008 MB and total concurrent executions at 10, against defaults of
  # 10240 and 1000. Both are adjustable through a Service Quotas request.
  #
  # 3008 MB is roughly 1.8 vCPU against 2.4 at 4096, so inference is slower but
  # works. Raise this once the quota increase lands, and re-measure: the whole
  # point of Phase 3 is having numbers to compare against.
  validation {
    condition     = var.memory_mb >= 1024 && var.memory_mb <= 10240
    error_message = "Lambda memory must be between 1024 and 10240 MB, and no higher than this account's quota."
  }
}

variable "timeout_seconds" {
  description = "Maximum run time. The cold path downloads 470 MB before doing any work."
  type        = number
  default     = 900
}

variable "ephemeral_storage_mb" {
  description = "Size of /tmp, which holds the model files and the image being processed."
  type        = number
  default     = 4096
}

variable "log_retention_days" {
  description = "CloudWatch retention. The AWS default is to keep logs forever."
  type        = number
  default     = 14
}
