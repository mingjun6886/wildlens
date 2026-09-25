resource "aws_ecr_repository" "process" {
  name = "${var.name_prefix}-process"

  # Scan each pushed image for known CVEs in its OS packages. Free, and the
  # findings are the only routine visibility into what a 4.5 GB base image
  # actually contains.
  image_scanning_configuration {
    scan_on_push = true
  }

  # Mutable so that "latest" can be repointed during development. A production
  # registry would be IMMUTABLE, forcing every deploy to name a digest.
  image_tag_mutability = "MUTABLE"

  # Dev only: lets `terraform destroy` remove the repository together with the
  # images inside it, which is what keeps the destroy/apply cycle reproducible.
  force_delete = true
}

# Each build pushes roughly 4.5 GB. Ten iterations without this policy is 45 GB
# of storage for images nobody will ever pull again — about $4.50 a month to
# keep rubbish, on a project whose steady-state cost is under a dollar.
resource "aws_ecr_lifecycle_policy" "keep_recent" {
  repository = aws_ecr_repository.process.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the three most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 3
        }
        action = { type = "expire" }
      },
    ]
  })
}
