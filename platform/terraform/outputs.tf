locals {
  connectivity = module.connectivity.connectivity

  # A route as people read it: where to, and through what (an address, or Internet).
  spoke_routes = {
    for region, details in local.connectivity.regions : region => [
      for name, route in coalesce(details.spoke_default_routes, {}) : {
        name = name
        to   = route.address_prefix
        via  = route.next_hop_type == "VirtualAppliance" ? route.next_hop_in_ip_address : route.next_hop_type
      }
    ]
  }
}

output "contract" {
  description = <<-EOT
    What governance and vending need from the platform. `python3 -m mlz contract platform` writes it to
    `_generated/platform.yaml` in their repos. No secrets.
  EOT
  value = {
    topology                  = local.connectivity.topology
    connectivity_subscription = local.config.connectivity_subscription_id
    regions = {
      for region, details in local.connectivity.regions : region => {
        primary                = details.is_primary
        hub_network            = details.hub_virtual_network_id
        platform_address_space = local.config.hubs[region].platform_address_space
        landing_zone_ranges    = local.config.hubs[region].landing_zone_ranges
        reserved               = local.config.hubs[region].reserved
        firewall               = details.firewall_private_ip
        dns_servers            = details.dns_servers
        spoke_routes           = local.spoke_routes[region]
      }
    }
    # For governance's Private Link DNS policy.
    private_dns = {
      subscription   = local.config.connectivity_subscription_id
      resource_group = module.naming.connectivity.dns_resource_group
      region         = local.config.primary_region
    }
  }
}

# Management's and identity's stacks work out the firewall's address and the DNS servers before the hub
# exists (tools/mlz/platform.py), as the hub module does; this warns if the hub's own ever differ.
check "platform_networks_match_their_hubs" {
  assert {
    condition = alltrue(flatten([
      for network, regions in local.config.networks : [
        for region, spoke in regions : (
          spoke.firewall == local.connectivity.regions[region].firewall_private_ip &&
          jsonencode(spoke.dns_servers) == jsonencode(local.connectivity.regions[region].dns_servers)
        )
      ]
    ]))
    error_message = "The firewall address or DNS servers worked out for management's or identity's network differ from the hub's: fix tools/mlz/platform.py to match the hub module."
  }
}
