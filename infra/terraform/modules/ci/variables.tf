variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "github_subject_prefix" {
  description = "The `sub` claim prefix GitHub issues for this repository, without the event suffix. Only its pull requests can assume the role."
  type        = string
}

variable "state_bucket" {
  description = "Bucket holding the Terraform state."
  type        = string
}
