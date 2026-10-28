# API Gateway writes its logs with a role that is set once for the whole account and
# region, not once per API. That makes this a resource that does not belong to any
# one API's module, and it has a consequence worth knowing: with the AWS provider
# 6.x, destroying it resets the account's setting to "no role", so a destroy of this
# estate would switch API Gateway logging off for any other API in the account.
# Nothing else is in this account (the provider pins allowed_account_ids), which is
# why it lives here rather than outside Terraform. See ADR 12.

resource "aws_iam_role" "apigw_cloudwatch" {
  name = "${var.name_prefix}-apigw-cloudwatch"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The AWS-managed policy for exactly this purpose: it allows writing to log groups.
resource "aws_iam_role_policy_attachment" "apigw_cloudwatch" {
  role       = aws_iam_role.apigw_cloudwatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "this" {
  cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch.arn

  depends_on = [aws_iam_role_policy_attachment.apigw_cloudwatch]
}
