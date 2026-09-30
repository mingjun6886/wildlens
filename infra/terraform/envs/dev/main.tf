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

module "queue" {
  source = "../../modules/queue"

  name_prefix     = local.name_prefix
  raw_bucket_name = module.storage.bucket_names["raw"]
  raw_bucket_arn  = module.storage.bucket_arns["raw"]
}

module "compute" {
  source = "../../modules/compute"

  name_prefix          = local.name_prefix
  image_repository_url = module.registry.repository_url

  raw_bucket_arn     = module.storage.bucket_arns["raw"]
  raw_bucket_name    = module.storage.bucket_names["raw"]
  models_bucket_arn  = module.storage.bucket_arns["models"]
  models_bucket_name = module.storage.bucket_names["models"]
  thumb_bucket_arn   = module.storage.bucket_arns["thumb"]
  thumb_bucket_name  = module.storage.bucket_names["thumb"]

  queue_arn  = module.queue.queue_arn
  table_arn  = module.database.table_arn
  table_name = module.database.table_name
}

module "auth" {
  source = "../../modules/auth"

  name_prefix = local.name_prefix
  suffix      = local.suffix

  # Phase 7 appends the CloudFront URL here. The port must match Vite's
  # strictPort setting exactly, or every sign-in fails with redirect_mismatch.
  callback_urls = var.web_callback_urls
  logout_urls   = var.web_callback_urls
}

module "api_functions" {
  source = "../../modules/api_functions"

  name_prefix = local.name_prefix
  source_dir  = "${path.root}/../../../../services/api"

  table_arn  = module.database.table_arn
  table_name = module.database.table_name

  raw_bucket_arn    = module.storage.bucket_arns["raw"]
  raw_bucket_name   = module.storage.bucket_names["raw"]
  thumb_bucket_arn  = module.storage.bucket_arns["thumb"]
  thumb_bucket_name = module.storage.bucket_names["thumb"]

  allowed_origin = var.allowed_origin

  # /search/byfile asks the tagging function to identify a sample without storing
  # it. Only one function in the estate carries the models.
  process_function_arn  = module.compute.function_arn
  process_function_name = module.compute.function_name
}

module "api" {
  source = "../../modules/api"

  name_prefix           = local.name_prefix
  cognito_user_pool_arn = module.auth.user_pool_arn
  allowed_origin        = var.allowed_origin

  # Paths first, routes second: two methods on one path must share a resource,
  # and CORS is keyed off the paths so that sharing cannot produce a duplicate
  # OPTIONS method.
  resources = {
    files  = ["{fileId}"]
    upload = []
    search = ["tags", "species", "byfile"]
  }

  routes = {
    status = {
      http_method   = "GET"
      resource_key  = "files/{fileId}"
      invoke_arn    = module.api_functions.invoke_arns["status"]
      function_name = module.api_functions.function_names["status"]
    }
    upload = {
      http_method   = "POST"
      resource_key  = "upload"
      invoke_arn    = module.api_functions.invoke_arns["upload"]
      function_name = module.api_functions.function_names["upload"]
    }

    # One function, three routes. The dispatch is on event["resource"], so an
    # undeclared path is refused by API Gateway and never reaches the handler.
    search_tags = {
      http_method   = "POST"
      resource_key  = "search/tags"
      invoke_arn    = module.api_functions.invoke_arns["search"]
      function_name = module.api_functions.function_names["search"]
    }
    search_species = {
      http_method   = "POST"
      resource_key  = "search/species"
      invoke_arn    = module.api_functions.invoke_arns["search"]
      function_name = module.api_functions.function_names["search"]
    }
    search_byfile = {
      http_method   = "POST"
      resource_key  = "search/byfile"
      invoke_arn    = module.api_functions.invoke_arns["search"]
      function_name = module.api_functions.function_names["search"]
    }
  }
}
