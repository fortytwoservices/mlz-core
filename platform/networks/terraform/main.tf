# Management's network (in the primary region) or identity's (in each region whose hub file says
# identity: true), as `var.network` says: one root, two stacks, each with a state of its own, so a change
# to one never plans the hub or the other. Everything here is known before the hub is applied (the
# config works out the firewall's address and the DNS servers, the names give the hub's ID), so the
# first run plans the hub and these networks together.
locals {
  config   = jsondecode(file(var.config_file))
  networks = local.config.networks[var.network] # by region
  # Unused when there are no networks, as without an identity subscription.
  subscription = var.network == "management" ? local.config.management_subscription_id : local.config.identity_subscription_id
  tags         = { ManagedBy = "mlz-core" }
}

module "naming" {
  for_each = local.networks
  source   = "../../../modules/naming"

  root_id = local.config.root_id
  region  = each.key
}

# The resource group the bootstrap made for this network, named for the primary region.
module "primary" {
  source = "../../../modules/naming"

  root_id = local.config.root_id
  region  = local.config.primary_region
}

module "network" {
  for_each = local.networks
  source   = "../../../modules/platform-network"

  names                  = module.naming[each.key].platform_networks[var.network]
  location               = each.key
  parent_id              = "/subscriptions/${local.subscription}/resourceGroups/${module.primary.platform_networks[var.network].resource_group}"
  address_space          = each.value.address_space
  subnets                = each.value.subnets
  dns_servers            = each.value.dns_servers
  firewall               = each.value.firewall
  hub_virtual_network_id = "/subscriptions/${local.config.connectivity_subscription_id}/resourceGroups/${module.naming[each.key].connectivity.hub_resource_group}/providers/Microsoft.Network/virtualNetworks/${module.naming[each.key].connectivity.hub_virtual_network}"
  tags                   = local.tags
}
