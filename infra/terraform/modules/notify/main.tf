# One topic for every tag notification. Subscribers are told apart by the filter
# policy on each subscription, not by separate topics, so adding a species costs
# nothing and the publisher never needs to know who is listening.
resource "aws_sns_topic" "tags" {
  name = "${var.name_prefix}-tags"

  # The AWS-managed key. Encryption at rest costs nothing and satisfies CKV_AWS_26;
  # a customer-managed key adds $1 a month and nothing for a message that holds a
  # file id and species names.
  kms_master_key_id = "alias/aws/sns"
}
