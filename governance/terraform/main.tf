locals {
  # Defaults and customer.yaml, merged and checked by scripts/render_library.py.
  config = jsondecode(file("${var.rendered_library_path}/config.json"))

  subscriptions = local.config.subscriptions
  region        = local.config.primary_region
  tags          = { ManagedBy = "mlz-core" }

  # Resource IDs built from names, so the policies know them at plan time, before management is applied.
  management_resource_group_id = "/subscriptions/${local.subscriptions.management}/resourceGroups/${module.naming.management.resource_group}"
  management_ids = {
    workspace = "${local.management_resource_group_id}/providers/Microsoft.OperationalInsights/workspaces/${module.naming.management.workspace}"
    identity  = "${local.management_resource_group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${module.naming.management.ama_identity}"
    rules = {
      for kind, name in module.naming.management.data_collection_rules :
      kind => "${local.management_resource_group_id}/providers/Microsoft.Insights/dataCollectionRules/${name}"
    }
  }

  # The Private Link DNS zones, from the platform's contract once they exist.
  platform = var.platform_contract_file == null ? null : yamldecode(file(var.platform_contract_file))
  private_dns_defaults = local.platform == null ? {} : {
    private_dns_zone_subscription_id     = jsonencode({ value = local.platform.private_dns.subscription })
    private_dns_zone_resource_group_name = jsonencode({ value = local.platform.private_dns.resource_group })
    private_dns_zone_region              = jsonencode({ value = local.platform.private_dns.region })
  }

  # Vending's rights on the groups landing zone subscriptions go into, for its pipeline identities from
  # the bootstrap: the apply identity owns every vended subscription, the plan identity reads them.
  bootstrap = var.bootstrap_contract_file == null ? null : yamldecode(file(var.bootstrap_contract_file))
  vending_rights = local.bootstrap == null ? {} : merge([
    for group in ["landing-zones", "sandboxes"] : {
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

module "naming" {
  source = "../../modules/naming"

  root_id = local.config.root_id
  region  = local.region
}

# Central management: the Log Analytics workspace, the Azure Monitor Agent's identity and the data
# collection rules the ALZ policies point machines at.
resource "azapi_resource" "management_resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = module.naming.management.resource_group
  parent_id = "/subscriptions/${local.subscriptions.management}"
  location  = local.region
  tags      = local.tags

  response_export_values = []
}

module "management" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-management.git?ref=v0.2.0"

  name      = module.naming.management.workspace
  parent_id = azapi_resource.management_resource_group.id
  location  = local.region
  data_collection_rules = {
    change_tracking = { name = module.naming.management.data_collection_rules.change_tracking }
    vm_insights     = { name = module.naming.management.data_collection_rules.vm_insights }
    defender_sql    = { name = module.naming.management.data_collection_rules.defender_sql }
  }
  user_assigned_identity = { name = module.naming.management.ama_identity }
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
      ama_user_assigned_managed_identity_name     = jsonencode({ value = module.naming.management.ama_identity })
      ama_change_tracking_data_collection_rule_id = jsonencode({ value = local.management_ids.rules.change_tracking })
      ama_vm_insights_data_collection_rule_id     = jsonencode({ value = local.management_ids.rules.vm_insights })
      ama_mdfc_sql_data_collection_rule_id        = jsonencode({ value = local.management_ids.rules.defender_sql })
      resource_group_location                     = jsonencode({ value = local.region })
      email_security_contact                      = jsonencode({ value = local.config.security_contact })
    },
    local.private_dns_defaults,
    var.policy_default_values,
  )

  # The policies point at management's resources, so they are assigned after those exist.
  policy_assignments_dependencies = [
    module.management.resource_id,
    module.management.user_assigned_identity,
    module.management.data_collection_rule_resource_ids,
  ]

  subscription_placement = local.placement
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
