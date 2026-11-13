locals {
  # Defaults and customer.yaml, merged and checked by scripts/render_library.py.
  config = jsondecode(file("${var.rendered_library_path}/config.json"))

  subscriptions = local.config.subscriptions
  region        = local.config.primary_region
  tags          = { ManagedBy = "mlz-core" }
  # From the customer's naming template, worked out by render_library.py (tools/mlz/names.py).
  names = local.config.names

  # Resource IDs built from names, so the policies know them at plan time, before management is applied.
  management_resource_group_id = "/subscriptions/${local.subscriptions.management}/resourceGroups/${local.names.management.resource_group}"
  management_ids = {
    workspace = "${local.management_resource_group_id}/providers/Microsoft.OperationalInsights/workspaces/${local.names.management.workspace}"
    identity  = "${local.management_resource_group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${local.names.management.ama_identity}"
    rules = {
      for kind, name in local.names.management.data_collection_rules :
      kind => "${local.management_resource_group_id}/providers/Microsoft.Insights/dataCollectionRules/${name}"
    }
  }

  # The Private Link DNS zones, where the platform makes them: the connectivity subscription and the group
  # tools/mlz/names.py names there, in the primary region. Known from the first run, before the zones exist.
  private_dns_defaults = {
    private_dns_zone_subscription_id     = jsonencode({ value = local.subscriptions.connectivity })
    private_dns_zone_resource_group_name = jsonencode({ value = local.names.dns_resource_group })
    private_dns_zone_region              = jsonencode({ value = local.region })
  }
  # The policy that links landing zones' private endpoints to those zones needs rights on them. ALZ would give its
  # identity Network Contributor on each zone, which fails while the zones do not exist yet (53 role assignments,
  # met in the canary), and forced a second governance run after the platform's first. Instead it gets Private
  # DNS Zone Contributor on the connectivity subscription, below, and none on each zone: every zone parameter of
  # ALZ's Deploy-Private-DNS-Zones set, worked out from that set in the ALZ library (57; avm-ptn-alz's own list
  # has only some of them). Check it again when the library changes.
  private_dns_zone_parameters = jsondecode(file("${path.module}/private-dns-zone-parameters.json"))
  # Where the policy looks for the zones, after any override in policy_default_values: the role goes there.
  private_dns_subscription = jsondecode(merge(local.private_dns_defaults, var.policy_default_values).private_dns_zone_subscription_id).value

  # Vending's rights on the groups landing zone subscriptions go into, for its pipeline identities from
  # the bootstrap: the apply identity owns every vended subscription, the plan identity reads them. Decommissioned
  # too: a decommissioned landing zone's subscription moves there, and its stack still plans and applies.
  bootstrap = var.bootstrap_contract_file == null ? null : yamldecode(file(var.bootstrap_contract_file))
  vending_rights = local.bootstrap == null ? {} : merge([
    for group in ["landing-zones", "sandboxes", "decommissioned"] : {
      "vending-apply/${group}" = {
        scope     = "/providers/Microsoft.Management/managementGroups/${local.config.root_id}-${group}"
        role      = "/providers/Microsoft.Authorization/roleDefinitions/8e3af657-a8ff-443c-a75c-2fe8c4bcb635" # Owner
        principal = local.bootstrap.identities.vending.apply.principal_id
      }
      "vending-plan/${group}" = {
        scope     = "/providers/Microsoft.Management/managementGroups/${local.config.root_id}-${group}"
        role      = "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7" # Reader
        principal = local.bootstrap.identities.vending.plan.principal_id
      }
    }
  ]...)

  # Platform subscriptions into their management groups. Security only when its group exists.
  placement = {
    for role, id in local.subscriptions : role => {
      subscription_id       = id
      management_group_name = "${local.config.root_id}-${role}"
    }
    if role != "security" || try(local.config.management_groups.security, false) == true
  }
}

data "azapi_client_config" "current" {}

# Central management: the Log Analytics workspace, the Azure Monitor Agent's identity and the data
# collection rules the ALZ policies point machines at.
resource "azapi_resource" "management_resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = local.names.management.resource_group
  parent_id = "/subscriptions/${local.subscriptions.management}"
  location  = local.region
  tags      = local.tags

  response_export_values = []
}

module "management" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-management.git?ref=v0.2.0"

  name      = local.names.management.workspace
  parent_id = azapi_resource.management_resource_group.id
  location  = local.region
  data_collection_rules = {
    change_tracking = { name = local.names.management.data_collection_rules.change_tracking }
    vm_insights     = { name = local.names.management.data_collection_rules.vm_insights }
    defender_sql    = { name = local.names.management.data_collection_rules.defender_sql }
  }
  user_assigned_identity = { name = local.names.management.ama_identity }
  tags                   = local.tags
}

module "alz" {
  source  = "Azure/avm-ptn-alz/azurerm"
  version = "0.22.0"

  # The rendered architecture is named after the root ID.
  architecture_name  = local.config.root_id
  parent_resource_id = coalesce(try(local.config.parent_management_group_id, null), data.azapi_client_config.current.tenant_id)
  location           = local.region
  enable_telemetry   = false

  policy_default_values = merge(
    {
      allowed_locations                           = jsonencode({ value = local.config.allowed_regions })
      log_analytics_workspace_id                  = jsonencode({ value = local.management_ids.workspace })
      ama_user_assigned_managed_identity_id       = jsonencode({ value = local.management_ids.identity })
      ama_user_assigned_managed_identity_name     = jsonencode({ value = local.names.management.ama_identity })
      ama_change_tracking_data_collection_rule_id = jsonencode({ value = local.management_ids.rules.change_tracking })
      ama_vm_insights_data_collection_rule_id     = jsonencode({ value = local.management_ids.rules.vm_insights })
      ama_mdfc_sql_data_collection_rule_id        = jsonencode({ value = local.management_ids.rules.defender_sql })
      resource_group_location                     = jsonencode({ value = local.region })
      email_security_contact                      = jsonencode({ value = local.config.security_contact })
    },
    local.private_dns_defaults,
    var.policy_default_values,
  )

  # No role assignment on each Private Link zone: private_dns_zone_role below covers them all.
  override_policy_definition_parameter_assign_permissions_unset = local.private_dns_zone_parameters

  # The policies point at management's resources, so they are assigned after those exist.
  policy_assignments_dependencies = [
    module.management.resource_id,
    module.management.user_assigned_identity,
    module.management.data_collection_rule_resource_ids,
  ]

  subscription_placement = local.placement
}

# Deploy-Private-DNS-Zones' identity may manage every Private Link zone in the subscription it looks in, the
# connectivity subscription unless policy_default_values says otherwise, before and after the zones exist (see
# private_dns_zone_parameters).
resource "azapi_resource" "private_dns_zone_role" {
  for_each = { for key, id in module.alz.policy_assignment_resource_ids : key => id if endswith(key, "/Deploy-Private-DNS-Zones") }

  type = "Microsoft.Authorization/roleAssignments@2022-04-01"
  # Azure cannot change an assignment's principal, so a new identity (a policy assignment made again) makes a new one.
  name      = uuidv5("url", "mlz-core/${local.config.root_id}/private-dns-zone-role/${each.key}/${module.alz.policy_assignment_identity_ids[each.key]}")
  parent_id = "/subscriptions/${local.private_dns_subscription}"
  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${local.private_dns_subscription}/providers/Microsoft.Authorization/roleDefinitions/b12aa53e-6015-4669-85d0-8515ebb3ae7f" # Private DNS Zone Contributor
      principalId      = module.alz.policy_assignment_identity_ids[each.key]
      principalType    = "ServicePrincipal"
      description      = "mlz-core governance: ${each.key} links private endpoints to the Private Link zones."
    }
  }
  # A policy assignment's new identity can take a minute to reach every Entra ID replica.
  retry = { error_message_regex = ["PrincipalNotFound"] }

  response_export_values = []
}

# Diagnostic settings need the Microsoft.Insights provider registered in their subscription, and a new
# subscription may not have it: the platform's firewalls and gateways, and the policies that deploy
# diagnostic settings, would fail with SubscriptionNotRegistered. Landing zones get it from vending.
resource "azapi_resource_action" "insights_provider" {
  for_each = local.placement

  type        = "Microsoft.Resources/providers@2021-04-01"
  resource_id = "/subscriptions/${each.value.subscription_id}/providers/Microsoft.Insights"
  action      = "register"
  method      = "POST"
}

# The platform sends its firewalls' and gateways' logs to the workspace. Naming a workspace as a
# destination takes two rights on it, and this role holds only those: the platform cannot read the data
# or change the workspace.
resource "azapi_resource" "workspace_destination_role" {
  count = local.bootstrap == null ? 0 : 1

  type      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name      = uuidv5("url", "mlz-core/${local.config.root_id}/workspace-destination")
  parent_id = "/subscriptions/${local.subscriptions.management}" # Azure keeps custom roles at subscription level
  body = {
    properties = {
      roleName    = "${local.config.root_id} log destination"
      description = "mlz-core: name the central workspace as the destination of diagnostic settings, nothing more."
      type        = "CustomRole"
      permissions = [{
        actions    = ["Microsoft.OperationalInsights/workspaces/read", "Microsoft.OperationalInsights/workspaces/sharedKeys/action"]
        notActions = []
      }]
      assignableScopes = [azapi_resource.management_resource_group.id]
    }
  }
}

resource "azapi_resource" "workspace_destination" {
  count = local.bootstrap == null ? 0 : 1

  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "mlz-core/${local.config.root_id}/governance/platform-apply/workspace/${local.bootstrap.identities.platform.apply.principal_id}")
  parent_id = module.management.resource_id
  body = {
    properties = {
      roleDefinitionId = azapi_resource.workspace_destination_role[0].id
      principalId      = local.bootstrap.identities.platform.apply.principal_id
      principalType    = "ServicePrincipal"
      description      = "mlz-core governance: platform-apply/workspace-destination"
    }
  }
}

resource "azapi_resource" "vending_rights" {
  for_each = local.vending_rights

  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "mlz-core/${local.config.root_id}/governance/${each.key}/${each.value.principal}")
  parent_id = each.value.scope
  body = {
    properties = {
      roleDefinitionId = each.value.role
      principalId      = each.value.principal
      principalType    = "ServicePrincipal"
      description      = "mlz-core governance: ${each.key}"
    }
  }
  # The groups come from the ALZ module.
  depends_on = [module.alz]
}
