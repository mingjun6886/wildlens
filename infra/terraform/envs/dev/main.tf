provider "aws" {
  region = var.region

  # A misconfigured AWS_PROFILE is the one mistake that creates real resources
  # in the wrong place and is expensive to notice. This turns that class of
  # error into a refusal to start, naming both accounts in the message.
  allowed_account_ids = [var.account_id]

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
module "registry" {
  source = "../../modules/registry"

  name_prefix = local.name_prefix
}

module "compute" {
  source = "../../modules/compute"

  name_prefix          = local.name_prefix
  image_repository_url = module.registry.repository_url

  raw_bucket_arn     = module.storage.bucket_arns["raw"]
  models_bucket_arn  = module.storage.bucket_arns["models"]
  models_bucket_name = module.storage.bucket_names["models"]
  thumb_bucket_arn   = module.storage.bucket_arns["thumb"]
  thumb_bucket_name  = module.storage.bucket_names["thumb"]
}
