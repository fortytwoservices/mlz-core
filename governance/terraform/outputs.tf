output "contract" {
  description = <<-EOT
    What platform and vending need from governance, published as JSON (`_generated/governance.json` in
    their repos). No secrets.
  EOT
  value = {
    root_id           = local.config.root_id
    primary_region    = local.region
    allowed_regions   = local.config.allowed_regions
    subscriptions     = local.subscriptions
    management_groups = module.alz.management_group_resource_ids
    management = {
      workspace_resource_id = module.management.resource_id
      workspace_id          = module.management.workspace_id
      ama_identity          = module.management.user_assigned_identity
    }
  }
}
