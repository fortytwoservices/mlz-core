locals {
  # Checked and worked out by `python3 -m mlz platform`: subnets, the primary hub, the subscription.
  config = jsondecode(file(var.config_file))

  root_id = local.config.root_id
  tags    = { ManagedBy = "mlz-core" }
}

module "naming" {
  source = "../../modules/naming"

  root_id = local.root_id
  region  = local.config.primary_region
}

# The hubs, their firewalls, DNS resolvers and Bastions, and the Private Link DNS zones.
module "connectivity" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-connectivity-hub.git?ref=v0.1.0"

  parent_id = "/subscriptions/${local.config.connectivity_subscription_id}"

  # Components the hub file does not name get <type>-<root>-hub-<region>, for example afw-mlz-hub-nwe.
  naming = {
    template = "$${slug}$${separator}${local.root_id}$${separator}hub$${separator}$${region}"
  }

  hub_virtual_networks = {
    for region, hub in local.config.hubs : region => {
      location         = hub.location
      address_space    = hub.address_space
      is_primary       = hub.is_primary
      routing_supernet = hub.landing_zone_ranges
      firewall = {
        sku_tier                         = hub.firewall.sku_tier
        subnet_address_prefix            = hub.firewall.subnet_address_prefix
        management_subnet_address_prefix = hub.firewall.management_subnet_address_prefix
      }
      dns_resolver = hub.dns_resolver
      bastion      = hub.bastion
    }
  }

  # Every Private Link zone, in the resource group governance's DNS policy points at.
  private_dns_zones = {
    resource_group_name = module.naming.connectivity.dns_resource_group
  }

  tags = local.tags
}
