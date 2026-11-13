locals {
  # Checked and worked out by `python3 -m mlz vending`: layers merged, the address range and subnets,
  # the hub to peer with, and every name, from the customer's template or the landing zone's own (docs/naming.md).
  lz = jsondecode(file(var.config_file))

  subscription = "/subscriptions/${local.lz.subscription_id}"
  names        = local.lz.names
  github       = local.lz.github # {organization, repo, create, team} when the landing zone has a repo, else null

  # decommission: true in the landing zone's file. Nothing vending made is kept: the network, both peerings,
  # the NSGs, the route table, the deploy identity and its role, the budget and the two resource groups (with
  # anything else in them) are destroyed, and the subscription goes to Decommissioned (its management group
  # comes from the config), where ALZ's policies deny new resources.
  decommission = try(local.lz.decommission, false)
  live         = local.decommission ? {} : { live = true } # for_each over what only a live landing zone has

  # The workload repo (docs/vending.md): made here, or an existing one the landing zone names (github.repo), which
  # vending only trusts. A made one is archived, never deleted, when the landing zone is decommissioned.
  repo_made = local.github != null && try(local.github.create, false) && !local.decommission
  repo_id   = local.github == null ? null : (try(local.github.create, false) ? try(github_repository.workload["made"].repo_id, null) : one(data.github_repository.existing[*].repo_id))
  # GitHub's immutable subjects, which the customer's organization issues (docs/bootstrap.md): a renamed or
  # recreated repo never inherits the identities. The deploy identity applies from the repo's apply environment,
  # the plan identity plans its pull requests.
  subject = local.github == null || local.decommission ? null : "repo:${local.github.organization}@${one(data.github_organization.customer[*].id)}/${local.github.repo}@${local.repo_id}"
}

data "github_organization" "customer" {
  count = local.github == null || local.decommission ? 0 : 1
  name  = local.github.organization
}

data "github_repository" "existing" {
  count     = local.github != null && !try(local.github.create, false) && !local.decommission ? 1 : 0
  full_name = "${local.github.organization}/${local.github.repo}"
}

resource "github_repository" "workload" {
  for_each = local.repo_made ? { made = true } : {}

  name                   = local.github.repo
  description            = "Workload repo of landing zone ${local.lz.key}, made by mlz-core's vending."
  visibility             = "private"
  auto_init              = true # a main branch, which the apply environment's policy names
  allow_merge_commit     = false
  allow_rebase_merge     = false
  allow_squash_merge     = true
  delete_branch_on_merge = true
  archive_on_destroy     = true # decommissioned: archived, the team's code and history kept
}

# auto_init makes the organization's default branch, which need not be main: the apply environment allows main alone.
resource "github_branch_default" "main" {
  count = local.repo_made ? 1 : 0

  repository      = github_repository.workload["made"].name
  branch          = "main"
  rename          = true # whatever the organization's default made, renamed (Contents: write)
  wait_for_rename = true
}

data "github_team" "owner" {
  count = local.repo_made ? 1 : 0
  slug  = local.github.team
}

# The owning team maintains the repo, and approves its applies.
resource "github_team_repository" "owner" {
  count = local.repo_made ? 1 : 0

  team_id    = data.github_team.owner[0].id
  repository = github_repository.workload["made"].name
  permission = "maintain"
}

resource "github_repository_environment" "apply" {
  count = local.repo_made ? 1 : 0

  repository  = github_repository.workload["made"].name
  environment = "apply"
  # GitHub takes a reviewer that has access already: the team's, above.
  depends_on = [github_team_repository.owner]

  reviewers {
    teams = [tonumber(data.github_team.owner[0].id)]
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "main" {
  count      = local.repo_made ? 1 : 0
  depends_on = [github_branch_default.main]

  repository     = github_repository.workload["made"].name
  environment    = github_repository_environment.apply[0].environment
  branch_pattern = "main"
}

data "azapi_client_config" "current" {}

# IDs and names only, no secrets. AZURE_CLIENT_ID is the plan identity for the repo, and the deploy identity inside
# its apply environment, so a workflow always signs in as the identity its job is trusted for.
resource "github_actions_variable" "workload" {
  for_each = local.repo_made ? {
    AZURE_TENANT_ID         = data.azapi_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID   = local.lz.subscription_id
    AZURE_CLIENT_ID         = module.vending.umi_client_ids["plan"]
    TFSTATE_RESOURCE_GROUP  = azapi_resource.state_resource_group.name
    TFSTATE_STORAGE_ACCOUNT = azapi_resource.state.name
    TFSTATE_CONTAINER       = azapi_resource.state_container.name
  } : {}

  repository    = github_repository.workload["made"].name
  variable_name = each.key
  value         = each.value
}

resource "github_actions_environment_variable" "deploy_identity" {
  count = local.repo_made ? 1 : 0

  repository    = github_repository.workload["made"].name
  environment   = github_repository_environment.apply[0].environment
  variable_name = "AZURE_CLIENT_ID"
  value         = module.vending.umi_client_ids["deploy"]
}

resource "azapi_resource" "resource_group" {
  for_each = local.decommission ? {} : {
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

# The workload repo's Terraform state, in the landing zone's own subscription: its plan identity reads it, its deploy
# identity writes it. Kept when the landing zone is decommissioned, in a group of its own (the other two go), since the
# team's resources may outlive the landing zone, and a state account renamed would lose the state: prevent_destroy.
resource "azapi_resource" "state_resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = local.names.state_resource_group
  parent_id = local.subscription
  location  = local.lz.location
  tags      = local.lz.tags

  response_export_values = []
}

resource "azapi_resource" "state" {
  type      = "Microsoft.Storage/storageAccounts@2024-01-01"
  name      = local.names.state_account
  parent_id = azapi_resource.state_resource_group.id
  location  = local.lz.location
  tags      = local.lz.tags
  body = {
    kind = "StorageV2"
    sku  = { name = "Standard_LRS" }
    properties = {
      allowSharedKeyAccess         = false # Entra ID only, as the bootstrap's
      defaultToOAuthAuthentication = true
      allowBlobPublicAccess        = false
      allowCrossTenantReplication  = false
      isLocalUserEnabled           = false
      minimumTlsVersion            = "TLS1_2"
      supportsHttpsTrafficOnly     = true
    }
  }
  depends_on = [module.vending] # which registers Microsoft.Storage in a new subscription

  lifecycle {
    prevent_destroy = true
  }
}

# Every storage account comes with its blob service, so this sets it rather than creating it.
resource "azapi_update_resource" "state_blob_service" {
  type      = "Microsoft.Storage/storageAccounts/blobServices@2024-01-01"
  name      = "default"
  parent_id = azapi_resource.state.id
  body = {
    properties = {
      isVersioningEnabled            = true
      deleteRetentionPolicy          = { enabled = true, days = 30 }
      containerDeleteRetentionPolicy = { enabled = true, days = 30 }
    }
  }
}

resource "azapi_resource" "state_container" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01"
  name      = "tfstate"
  parent_id = azapi_update_resource.state_blob_service.id
  body      = { properties = { publicAccess = "None" } }
}

# The plan identity reads the state, the deploy identity writes it: on the container only.
resource "azapi_resource" "state_role" {
  for_each = {
    for identity, role in {
      plan   = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1" # Storage Blob Data Reader
      deploy = "ba92f5b4-2d11-453d-a403-e96b0029c9fe" # Storage Blob Data Contributor
    } : identity => role if !local.decommission
  }

  type = "Microsoft.Authorization/roleAssignments@2022-04-01"
  # Unique in the tenant, as Azure needs: the container and the principal, so a replaced identity gets a new assignment
  # (an assignment's principal never changes).
  name      = uuidv5("url", "mlz-core/${azapi_resource.state_container.id}/${module.vending.umi_principal_ids[each.key]}")
  parent_id = azapi_resource.state_container.id
  body = {
    properties = {
      # A role's ID in the subscription's own form, as Azure stores it there: plans stay clean.
      roleDefinitionId = "${local.subscription}/providers/Microsoft.Authorization/roleDefinitions/${each.value}"
      principalId      = module.vending.umi_principal_ids[each.key]
      principalType    = "ServicePrincipal"
    }
  }
}

# Everything leaves the spoke through the hub's firewall.
module "route_table" {
  for_each = local.live
  source   = "git::https://github.com/fortytwoservices/az-tf-mlz-route-table.git?ref=v0.1.1"

  name                          = local.names.route_table
  parent_id                     = azapi_resource.resource_group["network"].id
  location                      = local.lz.location
  bgp_route_propagation_enabled = false
  routes                        = local.lz.network.routes
  tags                          = local.lz.tags
}

# Before decommissioning existed, the route table was the module itself.
moved {
  from = module.route_table
  to   = module.route_table["live"]
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

  network_security_group_enabled = !local.decommission
  network_security_groups = local.decommission ? {} : {
    for name, _ in local.lz.network.subnets : name => {
      name                         = local.names.network_security_groups[name]
      resource_group_name_existing = azapi_resource.resource_group["network"].name
      location                     = local.lz.location
      tags                         = local.lz.tags
    }
  }

  virtual_network_enabled = !local.decommission
  virtual_networks = local.decommission ? {} : {
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
          route_table            = { id = module.route_table["live"].resource_id }
        }
      }

      hub_network_resource_id  = local.lz.network.hub_virtual_network_id
      hub_peering_enabled      = true
      hub_peering_name_tohub   = local.names.peering_to_hub
      hub_peering_name_fromhub = local.names.peering_from_hub
      # The hubs have no VPN or ExpressRoute gateway yet, so neither side uses one.
      hub_peering_options_tohub   = { use_remote_gateways = false }
      hub_peering_options_fromhub = { allow_gateway_transit = false }
    }
  }

  # The landing zone's own identities for its repo: deploy, Contributor on its subscription, signed in from its repo's
  # apply environment; plan, Reader, for its pull requests' plans.
  umi_enabled = !local.decommission
  # Two entries of different shapes: each its own conditional, merged, so neither has to match the other's type.
  user_managed_identities = merge(local.decommission ? {} : {
    plan = {
      name                         = local.names.plan_identity
      resource_group_name_existing = azapi_resource.resource_group["identity"].name
      location                     = local.lz.location
      tags                         = local.lz.tags
      role_assignments = {
        reader = { definition = "Reader" }
      }
      federated_credentials_advanced = local.subject == null ? {} : {
        pull_request = {
          name               = "github-pull-request"
          subject_identifier = "${local.subject}:pull_request"
          issuer_url         = "https://token.actions.githubusercontent.com"
        }
      }
    }
    }, local.decommission ? {} : {
    deploy = {
      name                         = local.names.deploy_identity
      resource_group_name_existing = azapi_resource.resource_group["identity"].name
      location                     = local.lz.location
      tags                         = local.lz.tags
      role_assignments = {
        contributor = { definition = "Contributor" }
      }
      federated_credentials_advanced = local.subject == null ? {} : {
        apply = {
          name               = "github-environment-apply"
          subject_identifier = "${local.subject}:environment:apply"
          issuer_url         = "https://token.actions.githubusercontent.com"
        }
      }
    }
  })

  budget_enabled = !local.decommission && try(local.lz.budget.monthly, null) != null
  budgets = local.decommission || try(local.lz.budget.monthly, null) == null ? {} : {
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
