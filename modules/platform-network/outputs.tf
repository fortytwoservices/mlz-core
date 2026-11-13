output "resource_id" {
  description = "Resource ID of the virtual network."
  value       = module.virtual_network.resource_id
}

output "subnet_resource_ids" {
  description = "Resource IDs of the subnets, by name."
  value       = { for name, subnet in module.virtual_network.subnets : name => subnet.resource_id }
}

output "github_network_settings_id" {
  description = "The network settings resource's ID at GitHub (its GitHubId tag), which the organization's network configuration names; null without GitHub's runners."
  value       = try(azapi_resource.github_network_settings[0].output.tags.GitHubId, null)
}
