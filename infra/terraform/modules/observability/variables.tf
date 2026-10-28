variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "alert_email" {
  description = <<-EOT
    Address that receives alarm mail. Empty creates the topic and the alarms but no
    subscription, which means an alarm fires and nobody is told. The real value
    belongs in an untracked terraform.tfvars: this repository is public.
  EOT
  type        = string
  default     = ""
}

variable "function_names" {
  description = "Function names keyed by short name (process, status, upload, search, subscribe). One errors alarm each."
  type        = map(string)
}

variable "queue_name" {
  description = "The ingest queue's name (QueueName dimension)."
  type        = string
}

variable "dlq_name" {
  description = "The dead-letter queue's name."
  type        = string
}

variable "process_log_group" {
  description = "Log group of the tagging function, queried by the dashboard's Logs Insights widget."
  type        = string
}

variable "api_name" {
  description = "REST API name (ApiName dimension of AWS/ApiGateway)."
  type        = string
}

variable "api_stage" {
  description = "REST API stage name (Stage dimension of AWS/ApiGateway)."
  type        = string
}

variable "queue_age_threshold_seconds" {
  description = <<-EOT
    Age of the oldest message in the ingest queue that raises the alarm. 1200, not
    900: a message being processed still counts toward its age and process may run
    for its full 900 s timeout, so 900 would fire on a file that merely used all of
    it. 1200 means a file has waited, or been retried, past any legitimate run.
  EOT
  type        = number
  default     = 1200
}
