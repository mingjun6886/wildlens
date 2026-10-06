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

  # The browser PUTs directly to S3, so S3 needs its own CORS rule — separate
  # from the API's, and required even in development where the API is proxied.
  #
  # This is the one place CORS is still unavoidable. Everything else is same-origin
  # through CloudFront; the presigned PUT goes to S3 by definition.
  web_origins = concat(var.web_origins, [module.cdn.url])
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

  # Local development and the deployed site, both. Appending rather than replacing
  # is what keeps `npm run dev` working after the site goes up — and the local port
  # must match Vite's strictPort exactly, or sign-in fails with redirect_mismatch.
  callback_urls = concat(var.web_callback_urls, [module.cdn.url])
  logout_urls   = concat(var.web_callback_urls, [module.cdn.url])
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

  stage_name            = var.api_stage
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

module "cdn" {
  source = "../../modules/cdn"

  name_prefix = local.name_prefix

  web_bucket_id                   = module.storage.bucket_ids["web"]
  web_bucket_arn                  = module.storage.bucket_arns["web"]
  web_bucket_regional_domain_name = module.storage.bucket_regional_domain_names["web"]

  api_domain_name = replace(replace(module.api.invoke_url, "https://", ""), "/${var.api_stage}", "")

  # The prefix CloudFront routes to the API is the API Gateway stage name, which is
  # what lets the path be forwarded unchanged. See the module's variables.
  api_path_prefix = "/${var.api_stage}"
}
