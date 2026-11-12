output "networks" {
  description = "The networks this stack built, by region: the virtual network's and its subnets' resource IDs."
  value       = { for region, network in module.network : region => { id = network.resource_id, subnets = network.subnet_resource_ids } }
}
