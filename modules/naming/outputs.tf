output "name" {
  description = "The resource's name. The plan stops when Azure would not take it."
  value       = local.name

  precondition {
    condition     = length(local.problems) == 0
    error_message = join(" ", local.problems)
  }
}
