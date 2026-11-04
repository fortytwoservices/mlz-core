output "rule_collection_groups" {
  description = "Each rule collection group, by region and name, and its resource ID."
  value       = { for key, group in module.rule_collection_group : key => group.resource_id }
}
