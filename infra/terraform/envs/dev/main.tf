provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# S3 bucket names are unique across all of AWS, not just this account, so every
# name carries a random suffix. With no keepers, the suffix is generated once
# and then lives in state — it only changes if the state itself is destroyed.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

locals {
  name_prefix = "${var.project}-${var.environment}"
  suffix      = random_string.suffix.result
}

module "storage" {
  source = "../../modules/storage"

  name_prefix        = local.name_prefix
  suffix             = local.suffix
  raw_retention_days = var.raw_retention_days
}

module "database" {
  source = "../../modules/database"

  name_prefix = local.name_prefix
}