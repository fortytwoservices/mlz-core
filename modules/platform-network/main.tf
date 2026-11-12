# Management's or identity's network in one region: a spoke of the hub, as a landing zone is. Everything
# leaves through the hub's firewall, names resolve through the hub, and the platform, which owns both
# networks, makes both ends of the peering.

module "route_table" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-route-table.git?ref=v0.1.1"

  name                          = var.names.route_table
  parent_id                     = var.parent_id
  location                      = var.location
  bgp_route_propagation_enabled = false
  routes = {
    default = { name = "default", address_prefix = "0.0.0.0/0", next_hop_type = "VirtualAppliance", next_hop_in_ip_address = var.firewall }
  }
  tags = var.tags
}

# One per subnet, with Azure's default rules: the firewall decides what leaves.
resource "azapi_resource" "network_security_group" {
  for_each = var.subnets

  type      = "Microsoft.Network/networkSecurityGroups@2024-07-01"
  name      = format(var.names.network_security_group, each.key)
  parent_id = var.parent_id
  location  = var.location
  tags      = var.tags
  body      = { properties = {} }

  response_export_values = []
}

module "virtual_network" {
  source  = "Azure/avm-res-network-virtualnetwork/azurerm" # AzAPI only, despite the suffix
  version = "0.22.3"

  enable_telemetry = false
  name             = var.names.virtual_network
  parent_id        = var.parent_id
  location         = var.location
  address_space    = [var.address_space]
  # Always sent, so removing the servers reaches Azure (an empty list is Azure-provided DNS).
  dns_servers = { dns_servers = var.dns_servers }
  subnets = {
    for name, prefix in var.subnets : name => {
      name                   = format(var.names.subnet, name)
      address_prefix         = prefix
      network_security_group = { id = azapi_resource.network_security_group[name].id }
      route_table            = { id = module.route_table.resource_id }
    }
  }
  tags = var.tags
}

# The hubs have no VPN or ExpressRoute gateway yet, so neither side uses one.
module "peering" {
  source  = "Azure/avm-res-network-virtualnetwork/azurerm//modules/peering"
  version = "0.22.3"

  name                         = var.names.peering_to_hub
  parent_id                    = module.virtual_network.resource_id
  remote_virtual_network_id    = var.hub_virtual_network_id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false

  create_reverse_peering               = true
  reverse_name                         = var.names.peering_from_hub
  reverse_allow_virtual_network_access = true
  reverse_allow_forwarded_traffic      = true
  reverse_allow_gateway_transit        = false
  reverse_use_remote_gateways          = false
}
