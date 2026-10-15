output "plan_role_arn" {
  description = "Role assumed by the plan workflow."
  value       = aws_iam_role.plan.arn
}
