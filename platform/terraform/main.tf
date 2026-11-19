locals {
  # Checked and worked out by `python3 -m mlz platform`: subnets, the primary hub, the subscription.
  config = jsondecode(file(var.config_file))

  tags = { ManagedBy = "mlz-core" }

  # Each hub as the hub module takes it, every component named explicitly, from the customer's template or the hub
  # file's names: (tools/mlz/names.py). No naming template: a component left without a name fails the plan rather
  # than take another scheme's. A hub file's advanced: (tools/mlz/advanced.py: only inputs the input map calls open,
  # checked against the module's types) goes in first and mlz-core's values over it, so it only adds; a hub without
  # one is passed as it always was.
  hub_virtual_networks = {
    for region, hub in local.config.hubs : region => merge(try(hub.advanced, {}), {
      location            = hub.location
      address_space       = hub.address_space
      is_primary          = hub.is_primary
      name                = hub.names.virtual_network
      resource_group_name = hub.names.resource_group
      routing_supernet    = hub.routing_supernet # the platform's range and the landing zone ranges
      firewall = merge(try(hub.advanced.firewall, {}), {
        name                             = hub.names.firewall
        sku_tier                         = hub.firewall.sku_tier
        subnet_address_prefix            = hub.firewall.subnet_address_prefix
        management_subnet_address_prefix = hub.firewall.management_subnet_address_prefix
        public_ip_name                   = hub.names.firewall_public_ip
        management_public_ip_name        = hub.firewall.management_subnet_address_prefix == null ? null : hub.names.firewall_management_public_ip
        route_table_name                 = hub.names.firewall_route_table
        firewall_policy                  = merge(try(hub.advanced.firewall.firewall_policy, {}), { name = hub.names.firewall_policy })
      })
      # Names in, from mlz platform; null when the hub file turns the component off, which advanced: cannot turn on.
      # A filter, not a conditional: Terraform types both branches of a conditional, merge({}, null) is an empty
      # object, and the null it gives is then typed as one, which the module's object, with its required subnet,
      # refuses (abc's hub without Bastion, v0.11.3). one() of nothing is a null without a type.
      dns_resolver = one([for resolver in [hub.dns_resolver] : merge(try(hub.advanced.dns_resolver, {}), resolver) if resolver != null])
      bastion      = one([for bastion in [hub.bastion] : merge(try(hub.advanced.bastion, {}), bastion) if bastion != null])
    })
  }
}

# The hubs, their firewalls, DNS resolvers and Bastions, and the Private Link DNS zones.
module "connectivity" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-connectivity-hub.git?ref=v0.1.0"

  parent_id = "/subscriptions/${local.config.connectivity_subscription_id}"

  hub_virtual_networks = local.hub_virtual_networks

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
