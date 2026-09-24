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