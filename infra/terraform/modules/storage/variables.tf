variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "suffix" {
  description = "Random suffix that makes bucket names globally unique."
  type        = string
}

variable "raw_retention_days" {
  description = <<-EOT
    Days before uploaded originals expire. 0 keeps them indefinitely.

    Must not be set below the lifetime of a DONE record, which has no ttl and
    therefore never expires. A shorter value leaves records reporting DONE with
    signed URLs to objects that have been deleted — see the note in main.tf.
  EOT
  type        = number
  default     = 0
}

variable "thumb_retention_days" {
  description = <<-EOT
    Days before thumbnails expire. 0 keeps them indefinitely, which is the default
    for the same reason as the originals: they are what a results grid renders, and
    a thumbnail is roughly 11 KB against a 5 MB original.
  EOT
  type        = number
  default     = 0
}
