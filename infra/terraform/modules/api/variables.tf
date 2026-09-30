variable "name_prefix" {
  description = "Prefix shared by every resource in this environment."
  type        = string
}

variable "cognito_user_pool_arn" {
  description = "The authoriser identifies the pool by ARN."
  type        = string
}

variable "stage_name" {
  description = "Stage the deployment is served from. Part of the invoke URL."
  type        = string
  default     = "v1"
}

variable "allowed_origin" {
  description = <<-EOT
    Browser origin allowed by CORS. Must match what the API functions return in
    their own Access-Control-Allow-Origin header: the preflight reply and the real
    reply are checked separately, and disagreeing values fail in a way that looks
    like CORS was never configured.

    Not "*", deliberately. A wildcard would let any page on the internet call this
    API with a token it had obtained.
  EOT
  type        = string
  default     = "http://localhost:3000"
}

variable "resources" {
  description = <<-EOT
    API paths, as a top-level segment mapped to its child segments. An empty list
    means the path is a leaf at the top level.

      { upload = [], files = ["{fileId}"], search = ["tags", "species"] }

    Declared separately from the routes below because REST APIs model a path as a
    chain of resources, and two methods on the same path must share one resource.
    Keying CORS off this map rather than off the routes is what stops a second
    method on a path from trying to create a duplicate OPTIONS method.
  EOT
  type        = map(list(string))
}

variable "routes" {
  description = <<-EOT
    One entry per method. `resource_key` is the path as written in `resources`:
    either a top-level segment ("upload") or "parent/child" ("files/{fileId}").
  EOT
  type = map(object({
    http_method  = string
    resource_key = string

    # aws_lambda_function.invoke_arn, NOT .arn. It is already the full
    # arn:aws:apigateway:...:lambda:path/.../invocations form that an integration
    # uri expects; wrapping it in that format again gives "Invalid lambda function
    # ARN", quoting a string that looks correct because the inner half is.
    invoke_arn    = string
    function_name = string
  }))
}
