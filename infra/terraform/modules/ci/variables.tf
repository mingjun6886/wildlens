variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "github_repository" {
  description = "GitHub owner/repo, e.g. mingjun6886/wildlens. Only its pull requests can assume the role."
  type        = string
}

variable "state_bucket" {
  description = "Bucket holding the Terraform state."
  type        = string
}
