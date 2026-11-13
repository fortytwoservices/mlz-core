locals {
  # Checked and worked out by `python3 -m mlz platform`: subnets, the primary hub, the subscription.
  config = jsondecode(file(var.config_file))

  tags = { ManagedBy = "mlz-core" }
}

# The hubs, their firewalls, DNS resolvers and Bastions, and the Private Link DNS zones.
module "connectivity" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-connectivity-hub.git?ref=v0.1.0"

  parent_id = "/subscriptions/${local.config.connectivity_subscription_id}"

  # Every component named explicitly, from the customer's template or the hub file's names: (tools/mlz/names.py).
  # No naming template here: a component left without a name fails the plan rather than take another scheme's.
  hub_virtual_networks = {
    for region, hub in local.config.hubs : region => {
      location            = hub.location
      address_space       = hub.address_space
      is_primary          = hub.is_primary
      name                = hub.names.virtual_network
      resource_group_name = hub.names.resource_group
      routing_supernet    = hub.routing_supernet # the platform's range and the landing zone ranges
      firewall = {
        name                             = hub.names.firewall
        sku_tier                         = hub.firewall.sku_tier
        subnet_address_prefix            = hub.firewall.subnet_address_prefix
        management_subnet_address_prefix = hub.firewall.management_subnet_address_prefix
        public_ip_name                   = hub.names.firewall_public_ip
        management_public_ip_name        = hub.firewall.management_subnet_address_prefix == null ? null : hub.names.firewall_management_public_ip
        route_table_name                 = hub.names.firewall_route_table
        firewall_policy                  = { name = hub.names.firewall_policy }
      }
      dns_resolver = hub.dns_resolver # names in, from mlz platform
      bastion      = hub.bastion
    }
  }

  # The firewalls, Bastions and gateways send all their logs to the central workspace, in Azure's
  # resource-specific tables: the rule hits a network person reads, threat intelligence, DNS proxy.
  # Governance gives the platform's identity the right to name the workspace as a destination.
  diagnostic_settings = {
    workspace = {
      name                  = local.config.names.diagnostic_setting
      workspace_resource_id = local.config.workspace_resource_id
      logs                  = [{ category_group = "allLogs" }]
    }
  }

  # Every Private Link zone, in the resource group governance's DNS policy points at.
  private_dns_zones = {
    resource_group_name = local.config.names.dns_resource_group
  }

  tags = local.tags
}
