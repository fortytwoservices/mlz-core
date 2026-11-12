output "resource_id" {
  description = "Resource ID of the virtual network."
  value       = module.virtual_network.resource_id
}

output "subnet_resource_ids" {
  description = "Resource IDs of the subnets, by name."
  value       = { for name, subnet in module.virtual_network.subnets : name => subnet.resource_id }
}
