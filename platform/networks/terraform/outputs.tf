output "networks" {
  description = "The networks this stack built, by region: the virtual network's and its subnets' resource IDs, and GitHub's network settings ID."
  value = { for region, network in module.network : region => {
    id      = network.resource_id
    subnets = network.subnet_resource_ids
    # What the organization's network configuration names (mlz install runners); null where none.
    github_network_settings = network.github_network_settings_id
  } }
}
