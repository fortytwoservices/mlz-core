locals {
  customer = yamldecode(file(var.customer_file))
  settings = yamldecode(file(var.bootstrap_file))
  github   = local.settings.github

  root          = local.customer.root_id
  region        = local.customer.primary_region
  subscriptions = { for role, id in local.customer.subscriptions : role => "/subscriptions/${lower(id)}" }
  management    = local.subscriptions.management
  connectivity  = local.subscriptions.connectivity
  tenant_id     = data.azapi_client_config.current.tenant_id
  top_group     = "/providers/Microsoft.Management/managementGroups/${local.root}"
  # The tree goes below the tenant root, or below an existing group named in customer.yaml.
  parent_group = "/providers/Microsoft.Management/managementGroups/${coalesce(try(local.customer.parent_management_group_id, null), local.tenant_id)}"
  tags         = { ManagedBy = "mlz-core" }

  # Management's and identity's networks, which the platform pipeline builds in resource groups made here,
  # with rights on those groups only: governance's monitoring and the state account are in management too.
  # Identity's only when customer.yaml names an identity subscription.
  network_subscriptions = { for network in ["management", "identity"] : network => local.subscriptions[network] if contains(keys(local.subscriptions), network) }

  # One read-only identity per role for pull request plans, and one for approved applies.
  roles      = toset(["governance", "platform", "vending"])
  identities = { for pair in setproduct(local.roles, ["plan", "apply"]) : "${pair[0]}-${pair[1]}" => { role = pair[0], kind = pair[1] } }

  names = {
    resource_group = "rg-${local.root}-bootstrap-${module.naming.region_code}"
    # Globally unique, at most 24 letters and digits: the first 4 hex characters of a hash keep it stable.
    storage_account = "st${substr(replace(local.root, "-", ""), 0, 11)}state${substr(sha256("${local.management}/${local.root}"), 0, 4)}"
    identity        = { for key, _ in local.identities : key => "id-${local.root}-${key}-${module.naming.region_code}" }
  }

  # Built-in roles by ID. Azure stores a role's ID in the form of the assignment's scope: the
  # subscription's own form inside a subscription, the bare form on a management group. role_id below
  # writes it that way, so plans stay clean.
  builtin = {
    owner                    = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
    contributor              = "b24988ac-6180-42a0-ab88-20f7382dd24c"
    reader                   = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
    storage_blob_contributor = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    storage_blob_reader      = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
  }

  # Every right the bootstrap grants. Subscriptions get their rights from the tree once they are handed
  # over (moved into it, see the handover output), so the pipelines never need rights on the tenant's
  # default group. Vending's rights on Landing Zones and Sandboxes come from governance, which creates them.
  role_assignments = merge(
    {
      "governance-apply/top-group"  = { identity = "governance-apply", role = local.builtin.owner, scope = local.top_group }
      "governance-plan/top-group"   = { identity = "governance-plan", role = local.builtin.reader, scope = local.top_group }
      "platform-apply/connectivity" = { identity = "platform-apply", role = local.builtin.contributor, scope = local.connectivity }
      "platform-plan/connectivity"  = { identity = "platform-plan", role = local.builtin.reader, scope = local.connectivity }
      "vending-apply/hub-peering"   = { identity = "vending-apply", role = azapi_resource.hub_peering.id, scope = local.connectivity }
      "vending-plan/connectivity"   = { identity = "vending-plan", role = local.builtin.reader, scope = local.connectivity }
    },
    merge([for network, _ in local.network_subscriptions : {
      "platform-apply/${network}-network" = { identity = "platform-apply", role = local.builtin.contributor, scope = azapi_resource.network_resource_group[network].id }
      "platform-plan/${network}-network"  = { identity = "platform-plan", role = local.builtin.reader, scope = azapi_resource.network_resource_group[network].id }
    }]...),
    # State: each role's plan identity reads its own container, and its apply identity writes it.
    merge([for role in local.roles : {
      "${role}-apply/state" = { identity = "${role}-apply", role = local.builtin.storage_blob_contributor, scope = azapi_resource.container[role].id }
      "${role}-plan/state"  = { identity = "${role}-plan", role = local.builtin.storage_blob_reader, scope = azapi_resource.container[role].id }
    }]...),
  )

  # GitHub issues immutable subjects: repo:<organization>@<id>/<repository>@<id>.
  subject_prefix = {
    for role in local.roles : role => "repo:${local.github.organization}@${data.github_organization.customer.id}/${local.github.repositories[role]}@${data.github_repository.role[role].repo_id}"
  }
}

data "azapi_client_config" "current" {}

module "naming" {
  source = "../../modules/naming"

  root_id = local.root
  region  = local.region
}

# The management subscription may be new: the state account and the identities need their providers.
resource "azapi_resource_action" "register" {
  for_each = toset(["Microsoft.Storage", "Microsoft.ManagedIdentity"])

  type        = "Microsoft.Resources/providers@2021-04-01"
  resource_id = "${local.management}/providers/${each.key}"
  action      = "register"
  method      = "POST"
}

# The customer's top management group. Governance builds everything below it, so its pipeline never
# needs rights at the tenant root.
resource "azapi_resource" "top_group" {
  type      = "Microsoft.Management/managementGroups@2023-04-01"
  name      = local.root
  parent_id = "/"
  body = {
    properties = {
      displayName = local.customer.root_display_name
      details     = { parent = { id = local.parent_group } }
    }
  }
}

resource "azapi_resource" "resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = local.names.resource_group
  parent_id = local.management
  location  = local.region
  tags      = local.tags
}

# The networks' subscriptions may be new too, and the platform's identities may not register providers.
resource "azapi_resource_action" "register_network" {
  for_each = local.network_subscriptions

  type        = "Microsoft.Resources/providers@2021-04-01"
  resource_id = "${each.value}/providers/Microsoft.Network"
  action      = "register"
  method      = "POST"
}

# One for each network, in the primary region, holding that network in every region it is built in.
resource "azapi_resource" "network_resource_group" {
  for_each = local.network_subscriptions

  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = module.naming.platform_networks[each.key].resource_group
  parent_id = each.value
  location  = local.region
  tags      = local.tags
}

# Terraform state for every role. Entra ID only: account keys and key-signed SAS are refused. Versions
# and soft delete keep earlier states. The public endpoint stays on until the private runners exist.
resource "azapi_resource" "state" {
  type      = "Microsoft.Storage/storageAccounts@2024-01-01"
  name      = local.names.storage_account
  parent_id = azapi_resource.resource_group.id
  location  = local.region
  tags      = local.tags
  body = {
    kind = "StorageV2"
    sku  = { name = "Standard_LRS" }
    properties = {
      allowSharedKeyAccess         = false
      defaultToOAuthAuthentication = true
      allowBlobPublicAccess        = false
      allowCrossTenantReplication  = false
      isLocalUserEnabled           = false
      minimumTlsVersion            = "TLS1_2"
      supportsHttpsTrafficOnly     = true
      publicNetworkAccess          = "Enabled"
    }
  }
  depends_on = [azapi_resource_action.register]
}

# Every storage account comes with its blob service, so this sets it rather than creating it.
resource "azapi_update_resource" "blob_service" {
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

# One container per role, plus the bootstrap's own state.
resource "azapi_resource" "container" {
  for_each = setunion(local.roles, ["bootstrap"])

  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01"
  name      = each.key
  parent_id = azapi_update_resource.blob_service.id
  body      = { properties = { publicAccess = "None" } }
}

resource "azapi_resource" "identity" {
  for_each = local.identities

  type                   = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name                   = local.names.identity[each.key]
  parent_id              = azapi_resource.resource_group.id
  location               = local.region
  tags                   = local.tags
  response_export_values = ["properties.principalId", "properties.clientId"]
  depends_on             = [azapi_resource_action.register]
}

# Which GitHub workflows may sign in as each identity. A plan identity trusts pull requests, and main for
# the scheduled drift check; an apply identity trusts only its role's environment, which waits for a
# reviewer and accepts only main. Azure takes one credential write per identity at a time, hence two
# resources for the plan identities' two credentials.
resource "azapi_resource" "credential" {
  for_each = local.identities

  type      = "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31"
  name      = each.value.kind == "plan" ? "github-pull-request" : "github-environment-${each.value.role}"
  parent_id = azapi_resource.identity[each.key].id
  body = {
    properties = {
      issuer    = "https://token.actions.githubusercontent.com"
      audiences = ["api://AzureADTokenExchange"]
      subject   = each.value.kind == "plan" ? "${local.subject_prefix[each.value.role]}:pull_request" : "${local.subject_prefix[each.value.role]}:environment:${each.value.role}"
    }
  }
}

resource "azapi_resource" "credential_main" {
  for_each = { for key, identity in local.identities : key => identity if identity.kind == "plan" }

  type      = "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31"
  name      = "github-main"
  parent_id = azapi_resource.identity[each.key].id
  body = {
    properties = {
      issuer    = "https://token.actions.githubusercontent.com"
      audiences = ["api://AzureADTokenExchange"]
      subject   = "${local.subject_prefix[each.value.role]}:ref:refs/heads/main"
    }
  }
  depends_on = [azapi_resource.credential]
}

# Vending peers each landing zone with its hub, from both sides, and may do nothing else in the
# connectivity subscription.
resource "azapi_resource" "hub_peering" {
  type      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name      = uuidv5("url", "mlz-core/${local.root}/hub-peering")
  parent_id = local.connectivity
  body = {
    properties = {
      roleName    = "${local.root} hub peering"
      description = "mlz-core vending: peer landing zone networks with the hubs."
      type        = "CustomRole"
      permissions = [{
        actions = [
          "Microsoft.Network/virtualNetworks/read",
          "Microsoft.Network/virtualNetworks/peer/action",
          "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/read",
          "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/write",
          "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/delete",
        ]
        notActions = []
      }]
      assignableScopes = [local.connectivity]
    }
  }
}

resource "azapi_resource" "role_assignment" {
  for_each = local.role_assignments

  type = "Microsoft.Authorization/roleAssignments@2022-04-01"
  # Azure cannot change an assignment's principal, so a new principal makes a new assignment.
  name      = uuidv5("url", "mlz-core/${local.root}/bootstrap/${each.key}/${azapi_resource.identity[each.value.identity].output.properties.principalId}")
  parent_id = each.value.scope
  body = {
    properties = {
      roleDefinitionId = startswith(each.value.role, "/") ? each.value.role : (
        startswith(each.value.scope, "/subscriptions/")
        ? "${join("/", slice(split("/", each.value.scope), 0, 3))}/providers/Microsoft.Authorization/roleDefinitions/${each.value.role}"
        : "/providers/Microsoft.Authorization/roleDefinitions/${each.value.role}"
      )
      principalId   = azapi_resource.identity[each.value.identity].output.properties.principalId
      principalType = "ServicePrincipal"
      description   = "mlz-core bootstrap: ${each.key}"
    }
  }
  # A new identity can take a minute to reach every Entra ID replica, and the person's own rights take a
  # while to reach a group created moments ago: the first assignment on the new top group was refused with
  # AuthorizationFailed in the TCAF canary on 2026-11-11, and made on the next run. A real lack of rights
  # still fails, after ten minutes.
  retry      = { error_message_regex = ["PrincipalNotFound", "AuthorizationFailed"] }
  depends_on = [azapi_resource.top_group]

  timeouts {
    create = "10m"
  }
}

# People who run Terraform by hand (this root, break-glass state work) need data plane rights, because
# the account accepts Entra ID only.
resource "azapi_resource" "state_operator" {
  for_each = coalesce(try(local.settings.state_operators, null), {})

  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "mlz-core/${local.root}/state-operator/${each.key}/${each.value}")
  parent_id = azapi_resource.state.id
  body = {
    properties = {
      roleDefinitionId = "${local.management}/providers/Microsoft.Authorization/roleDefinitions/${local.builtin.storage_blob_contributor}"
      principalId      = each.value
      principalType    = "User"
      description      = "mlz-core bootstrap: state operator ${each.key}"
    }
  }
}

# GitHub: the repos, the people who approve applies, and the settings the workflows read.

data "github_organization" "customer" {
  name         = local.github.organization
  summary_only = true # only its ID; a token with admin rights on the three repos is enough
}

data "github_repository" "role" {
  for_each  = local.roles
  full_name = "${local.github.organization}/${local.github.repositories[each.key]}"
}

data "github_user" "reviewer" {
  for_each = toset(local.github.reviewers)
  username = each.key
}

# The environment each apply runs in: a reviewer approves it, and only main may use it.
resource "github_repository_environment" "apply" {
  for_each = local.roles

  repository  = local.github.repositories[each.key]
  environment = each.key

  reviewers {
    users = [for user in data.github_user.reviewer : tonumber(user.id)]
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }

  lifecycle {
    precondition {
      condition     = length(local.github.reviewers) > 0
      error_message = "github.reviewers in bootstrap.yaml is empty: every apply needs someone to approve it."
    }
  }
}

resource "github_repository_environment_deployment_policy" "main" {
  for_each = local.roles

  repository     = local.github.repositories[each.key]
  environment    = github_repository_environment.apply[each.key].environment
  branch_pattern = "main"
}

# IDs and names only, no secrets. AZURE_CLIENT_ID is the plan identity for the repo, and the apply
# identity inside its environment, so a workflow always signs in as the identity its job is trusted for.
resource "github_actions_variable" "role" {
  for_each = merge([for role in local.roles : {
    for name, value in {
      AZURE_TENANT_ID         = local.tenant_id
      AZURE_SUBSCRIPTION_ID   = trimprefix(local.management, "/subscriptions/")
      AZURE_CLIENT_ID         = azapi_resource.identity["${role}-plan"].output.properties.clientId
      TFSTATE_STORAGE_ACCOUNT = local.names.storage_account
      TFSTATE_CONTAINER       = role
      # The three repos, for the pipeline that offers a contract to the repos that read it.
      MLZ_REPOSITORIES = jsonencode({ for r in local.roles : r => "${local.github.organization}/${local.github.repositories[r]}" })
    } : "${role}/${name}" => { role = role, name = name, value = value }
  }]...)

  repository    = local.github.repositories[each.value.role]
  variable_name = each.value.name
  value         = each.value.value
}

resource "github_actions_environment_variable" "apply_identity" {
  for_each = local.roles

  repository    = local.github.repositories[each.key]
  environment   = github_repository_environment.apply[each.key].environment
  variable_name = "AZURE_CLIENT_ID"
  value         = azapi_resource.identity["${each.key}-apply"].output.properties.clientId
}
