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

# Names from the customer's template (tools/mlz/names.py): each region's network, its hub's, and the one resource
# group the bootstrap made for this network, named for the primary region.
module "network" {
  for_each = local.networks
  source   = "../../../modules/platform-network"

  names                  = each.value.names
  location               = each.key
  parent_id              = "/subscriptions/${local.subscription}/resourceGroups/${local.config.names.network_resource_groups[var.network]}"
  address_space          = each.value.address_space
  subnets                = each.value.subnets
  dns_servers            = each.value.dns_servers
  firewall               = each.value.firewall
  hub_virtual_network_id = "/subscriptions/${local.config.connectivity_subscription_id}/resourceGroups/${local.config.hubs[each.key].names.resource_group}/providers/Microsoft.Network/virtualNetworks/${local.config.hubs[each.key].names.virtual_network}"
  # Management's, where GitHub supports the region (tools/mlz/platform.py).
  github_runners = try(each.value.github_runners, false) ? {
    subnet      = "github-runners"
    name        = each.value.names.github_network_settings
    business_id = var.github_business_id
  } : null
  tags = local.tags
}
