# GitHub's token issuer. AWS validates the certificate chain itself, so no
# thumbprint is configured. There can be only one provider per URL per account.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "plan" {
  name = "${var.name_prefix}-ci-plan"

  # Exactly one subject: a pull_request run of this repository. A run on main,
  # on a tag, or in any other repository presents a different `sub` and is
  # refused. The workflow file can be edited by any branch, so this condition
  # and the policies below are the real controls, not the workflow.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:pull_request"
        }
      }
    }]
  })
}

# Enough for `plan` to refresh every resource type in use.
resource "aws_iam_role_policy_attachment" "read_only" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "state_and_data" {
  name = "state-read-and-data-deny"
  role = aws_iam_role.plan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Read, never write: the role cannot take the state lock, which is why
        # the workflow plans with -lock=false.
        Sid      = "ReadState"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.state_bucket}/*"
      },
      {
        Sid      = "ListStateBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${var.state_bucket}"
      },
      {
        # ReadOnlyAccess would otherwise let this role read every uploaded
        # image and every row in the table. `plan` needs resource metadata, not
        # user data. An explicit Deny overrides any Allow.
        Sid    = "DenyUserDataReads"
        Effect = "Deny"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:BatchGetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = "*"
      },
      {
        Sid         = "DenyObjectReadsOutsideState"
        Effect      = "Deny"
        Action      = ["s3:GetObject"]
        NotResource = "arn:aws:s3:::${var.state_bucket}/*"
      },
    ]
  })
}
