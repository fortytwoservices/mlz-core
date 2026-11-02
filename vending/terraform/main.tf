locals {
  # Checked and worked out by `python3 -m mlz vending`: layers merged, the address range and subnets,
  # the hub to peer with.
  lz = jsondecode(file(var.config_file))

  subscription = "/subscriptions/${local.lz.subscription_id}"
  names        = module.naming.landing_zone
  github       = try(local.lz.github.repo, null) == null ? null : local.lz.github
}

module "naming" {
  source = "../../modules/naming"

  root_id      = local.lz.root_id
  region       = local.lz.location
  landing_zone = local.lz.key
}

resource "azapi_resource" "resource_group" {
  for_each = {
    network  = local.names.network_resource_group
    identity = local.names.identity_resource_group
  }

  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = each.value
  parent_id = local.subscription
  location  = local.lz.location
  tags      = local.lz.tags

  response_export_values = []
}

# Everything leaves the spoke through the hub's firewall.
module "route_table" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-route-table.git?ref=v0.1.1"

  name                          = local.names.route_table
  parent_id                     = azapi_resource.resource_group["network"].id
  location                      = local.lz.location
  bgp_route_propagation_enabled = false
  routes                        = local.lz.network.routes
  tags                          = local.lz.tags
}

# Budgets start on the first of the month the landing zone was vended, and stay there.
resource "time_static" "budget_start" {}

module "vending" {
  source  = "Azure/avm-ptn-alz-sub-vending/azure"
  version = "0.3.4"

  location         = local.lz.location
  enable_telemetry = false

  # The subscription exists: it is placed and tagged, not created.
  subscription_alias_enabled                        = false
  subscription_id                                   = local.lz.subscription_id
  subscription_management_group_association_enabled = true
  subscription_management_group_id                  = local.lz.management_group_id
  subscription_tags                                 = local.lz.tags
  subscription_register_resource_providers_enabled  = true

  network_security_group_enabled = true
  network_security_groups = {
    for name, _ in local.lz.network.subnets : name => {
      name                         = format(local.names.network_security_group, name)
      resource_group_name_existing = azapi_resource.resource_group["network"].name
      location                     = local.lz.location
      tags                         = local.lz.tags
    }
  }

  virtual_network_enabled = true
  virtual_networks = {
    spoke = {
      name                         = local.names.virtual_network
      address_space                = [local.lz.network.address_space]
      resource_group_name_existing = azapi_resource.resource_group["network"].name
      location                     = local.lz.location
      dns_servers                  = local.lz.network.dns_servers
      tags                         = local.lz.tags

      subnets = {
        for name, prefix in local.lz.network.subnets : name => {
          name                   = format(local.names.subnet, name)
          address_prefix         = prefix
          network_security_group = { key_reference = name }
          route_table            = { id = module.route_table.resource_id }
        }
      }

      hub_network_resource_id = local.lz.network.hub_virtual_network_id
      hub_peering_enabled     = true
      # The hubs have no VPN or ExpressRoute gateway yet, so neither side uses one.
      hub_peering_options_tohub   = { use_remote_gateways = false }
      hub_peering_options_fromhub = { allow_gateway_transit = false }
    }
  }

  # The landing zone's own deploy identity: Contributor on its subscription, signed in from its repo.
  umi_enabled = true
  user_managed_identities = {
    deploy = {
      name                         = local.names.deploy_identity
      resource_group_name_existing = azapi_resource.resource_group["identity"].name
      location                     = local.lz.location
      tags                         = local.lz.tags
      role_assignments = {
        contributor = { definition = "Contributor" }
      }
      federated_credentials_github = local.github == null || var.github_organization == null ? {} : {
        main = {
          organization = var.github_organization
          repository   = local.github.repo
          entity       = "branch"
          value        = "main"
        }
      }
    }
  }

  budget_enabled = try(local.lz.budget.monthly, null) != null
  budgets = try(local.lz.budget.monthly, null) == null ? {} : {
    monthly = {
      name              = local.names.budget
      amount            = local.lz.budget.monthly
      time_grain        = "Monthly"
      time_period_start = formatdate("YYYY-MM-01'T'00:00:00Z", time_static.budget_start.rfc3339)
      time_period_end   = "2099-12-31T00:00:00Z"
      notifications = {
        actual_80 = {
          enabled        = true
          operator       = "GreaterThan"
          threshold      = 80
          contact_emails = local.lz.budget.contacts
        }
        forecast_100 = {
          enabled        = true
          operator       = "GreaterThan"
          threshold      = 100
          threshold_type = "Forecasted"
          contact_emails = local.lz.budget.contacts
        }
      }
    }
  }
}
