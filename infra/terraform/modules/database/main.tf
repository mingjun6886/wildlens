resource "aws_dynamodb_table" "files" {
  name = "${var.name_prefix}-files"

  # On-demand. An idle table costs nothing, which matters when the project
  # spends most of its life idle between demos. Provisioned capacity would
  # bill around the clock for throughput nobody is using.
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "fileId"

  # Only key attributes are declared. DynamoDB has no fixed schema, so the
  # application writes status, tags, modelVersion and the rest at runtime
  # without Terraform ever knowing they exist.
  attribute {
    name = "fileId"
    type = "S"
  }

  # Records are written as PENDING carrying an expiry one hour out. The
  # transition to DONE removes the attribute, and the row becomes permanent.
  # Abandoned uploads are reaped by DynamoDB at no charge.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  # Off deliberately. PITR bills per GB of backup, and everything here is
  # reproducible: delete the table and the next upload rebuilds its own row.
  point_in_time_recovery {
    enabled = false
  }

  # Dev only, and the default anyway — stated explicitly so the destroy/apply
  # cycle cannot be broken by someone flipping it on without noticing.
  deletion_protection_enabled = false
}