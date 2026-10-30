locals {
  # Defaults and customer.yaml, merged and checked by scripts/render_library.py.
  config = jsondecode(file("${var.rendered_library_path}/config.json"))
}

data "azapi_client_config" "current" {}

module "alz" {
  source  = "Azure/avm-ptn-alz/azurerm"
  version = "0.22.0"

  # The rendered architecture is named after the root ID.
  architecture_name  = local.config.root_id
  parent_resource_id = coalesce(try(local.config.parent_management_group_id, null), data.azapi_client_config.current.tenant_id)
  location           = local.config.primary_region
  enable_telemetry   = false

  policy_default_values = merge(
    { allowed_locations = jsonencode({ value = local.config.allowed_regions }) },
    var.policy_default_values,
  )
}
