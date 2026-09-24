variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "suffix" {
  description = "Random suffix that makes bucket names globally unique."
  type        = string
}

variable "raw_retention_days" {
  description = "Days before uploaded originals and their thumbnails expire."
  type        = number
}
