data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  api_name = "${var.name_prefix}-api"

  # Flatten { files = ["{fileId}"] } into [{ parent = "files", child = "{fileId}" }]
  # so that child resources can be declared with for_each.
  children = flatten([
    for parent, kids in var.resources : [
      for child in kids : { parent = parent, child = child }
    ]
  ])
}

# --- The API -----------------------------------------------------------------
#
# REST API rather than HTTP API. HTTP API costs about a third as much per request
# and needs far less configuration than the CORS and deployment machinery below,
# but it does not support X-Ray tracing, and tracing a request from the edge is a
# stated goal of this project. At a few thousand requests the price difference is
# a few cents, so cost is not an argument here in either direction.
#
# The trade is recorded as an ADR.
resource "aws_api_gateway_rest_api" "api" {
  name        = local.api_name
  description = "WildLens API. Every route sits behind the Cognito authoriser."

  endpoint_configuration {
    # Regional, not edge-optimized. Edge would put a CloudFront distribution in
    # front of the API, which helps callers far from the region; the clients here
    # are in the same region as the API, so it would add a hop and a cache to no
    # purpose. Phase 7 puts CloudFront in front of the static site instead.
    types = ["REGIONAL"]
  }
}

# --- Path resources ----------------------------------------------------------

resource "aws_api_gateway_resource" "parent" {
  for_each = var.resources

  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = each.key
}

resource "aws_api_gateway_resource" "child" {
  for_each = { for entry in local.children : "${entry.parent}/${entry.child}" => entry }

  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.parent[each.value.parent].id
  path_part   = each.value.child
}

locals {
  # resource_key -> resource id, for every declared path. The keys are known at
  # plan time, which is what allows for_each over this map; only the values wait
  # for apply.
  resource_ids = merge(
    { for key, resource in aws_api_gateway_resource.parent : key => resource.id },
    { for key, resource in aws_api_gateway_resource.child : key => resource.id },
  )

  # Only paths that carry a method need a preflight. A segment such as "files"
  # exists solely to hang "{fileId}" from and is never called on its own, so an
  # OPTIONS method there would answer a question nobody asks.
  #
  # Keyed by resource rather than by route, so two methods on one path still share
  # a single OPTIONS method instead of colliding over it.
  cors_resource_ids = {
    for key in toset([for route in var.routes : route.resource_key]) :
    key => local.resource_ids[key]
  }
}

# --- Authoriser --------------------------------------------------------------
#
# Validates the Cognito ID token before the request reaches a function, so no
# handler contains authentication code and none can forget to check.
#
# It validates the ID token. Sending the access token instead returns 401 with no
# indication of why, which is documented in docs/runbook.md because it costs an
# afternoon otherwise.
resource "aws_api_gateway_authorizer" "cognito" {
  name          = "${var.name_prefix}-cognito"
  rest_api_id   = aws_api_gateway_rest_api.api.id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [var.cognito_user_pool_arn]

  # Where to find the token. The default is "Authorization"; naming it explicitly
  # means the client contract is visible here rather than implied.
  identity_source = "method.request.header.Authorization"
}

# --- Methods and integrations ------------------------------------------------

resource "aws_api_gateway_method" "route" {
  for_each = var.routes

  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = local.resource_ids[each.value.resource_key]
  http_method   = each.value.http_method
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "route" {
  for_each = var.routes

  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = local.resource_ids[each.value.resource_key]
  http_method = aws_api_gateway_method.route[each.key].http_method

  type = "AWS_PROXY"

  # Always POST, whatever the route's own method is. This is the method API
  # Gateway uses to call Lambda, not the method the client used — setting it to
  # match the route gives a 500 that names nothing.
  integration_http_method = "POST"
  uri                     = each.value.invoke_arn
}

# --- Letting API Gateway invoke the functions --------------------------------
#
# Keyed by function name, not by route, because one function can serve several
# routes and a second permission with the same statement id fails the apply.
resource "aws_lambda_permission" "invoke" {
  for_each = toset([for route in var.routes : route.function_name])

  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = each.value
  principal     = "apigateway.amazonaws.com"

  # Scoped to this API, so another API in the account cannot invoke these
  # functions. The wildcards cover every stage and method: narrowing them further
  # would mean a permission per route with no security gained, since the routes
  # all belong to this one API.
  source_arn = "${aws_api_gateway_rest_api.api.execution_arn}/*/*"
}

# --- CORS --------------------------------------------------------------------
#
# A browser sends OPTIONS before any cross-origin request that carries an
# Authorization header. REST APIs do not answer that automatically, so every path
# needs an OPTIONS method backed by a mock integration.
#
# This is only half of CORS. The other half is the Access-Control-Allow-Origin
# header on the *real* response, which the functions add themselves via
# services/api/common.py. Configuring one and not the other is the hardest failure
# in this area to diagnose: the preflight succeeds, so the configuration looks
# complete, and the browser blocks the real response with a message that does not
# say which half is missing.
#
# Rule of thumb: curl works and the browser does not means CORS, never the
# backend.

resource "aws_api_gateway_method" "options" {
  for_each = local.cors_resource_ids

  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = each.value
  http_method = "OPTIONS"

  # NONE, and this does not weaken the rule that every route requires a token.
  #
  # A preflight request carries no Authorization header — that is the point of it,
  # the browser is asking whether it may send one. Requiring authorisation here
  # makes the preflight fail with 401 and the real request never happens. The
  # mock integration below returns headers and no data, so there is nothing here
  # to protect.
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "options" {
  for_each = local.cors_resource_ids

  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = each.value
  http_method = aws_api_gateway_method.options[each.key].http_method

  # MOCK: API Gateway answers from its own configuration and invokes nothing, so
  # a preflight costs no Lambda invocation.
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "options" {
  for_each = local.cors_resource_ids

  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = each.value
  http_method = aws_api_gateway_method.options[each.key].http_method
  status_code = "200"

  # Declares which headers may be returned. The values come from the integration
  # response below; omitting a header here makes it silently disappear from the
  # reply even though the integration sets it.
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "options" {
  for_each = local.cors_resource_ids

  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = each.value
  http_method = aws_api_gateway_method.options[each.key].http_method
  status_code = aws_api_gateway_method_response.options[each.key].status_code

  # The single quotes are required: these are VTL expressions, and an unquoted
  # value is read as a variable reference that resolves to empty.
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Authorization,Content-Type'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'${var.allowed_origin}'"
  }

  depends_on = [aws_api_gateway_integration.options]
}

# --- Deployment --------------------------------------------------------------
#
# A REST API has two layers: the configuration, and a deployment that freezes it.
# Changing the configuration does nothing until a new deployment is created, and
# nothing reports that — the stage keeps serving the previous snapshot.
#
# Terraform cannot see that relationship either. Without the hash below, editing a
# route and running apply reports "no changes" and the API quietly keeps the old
# behaviour. That makes this the most dangerous resource in the module, and the
# reason the hash covers *every* method and integration: an omitted one is a route
# that never deploys while everything else works.
resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_resource.parent,
      aws_api_gateway_resource.child,
      aws_api_gateway_authorizer.cognito,
      aws_api_gateway_method.route,
      aws_api_gateway_integration.route,
      aws_api_gateway_method.options,
      aws_api_gateway_integration.options,
      aws_api_gateway_method_response.options,
      aws_api_gateway_integration_response.options,
    ]))
  }

  # A deployment cannot be destroyed while a stage points at it, so the
  # replacement has to exist first.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "stage" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = var.stage_name

  # Access logging and X-Ray tracing are configured in Phase 9, together with the
  # dashboard and the alarms. Both need an account-level API Gateway CloudWatch
  # role, which is a single regional setting shared by every API in the account —
  # a resource that does not belong to one API's module, and one whose destruction
  # would silently disable logging for anything else using it.
}
