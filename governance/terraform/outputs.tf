output "contract" {
  description = <<-EOT
    What platform and vending need from governance. `python3 -m mlz contract governance` writes it to
    `_generated/governance.yaml` in their repos. No secrets.
  EOT
  value = {
    root            = local.config.root_id
    primary_region  = local.region
    allowed_regions = local.config.allowed_regions
    required_tags   = coalesce(try(local.config.tags.required, null), []) # null in the YAML means none
    archetypes = merge(
      {
        corp    = "${local.config.root_id}-lz-corp"
        online  = "${local.config.root_id}-lz-online"
        sandbox = "${local.config.root_id}-sandboxes"
      },
      try(local.config.management_groups.local, false) == true ? { local = "${local.config.root_id}-lz-local" } : {},
    )
    platform_subscriptions = local.subscriptions
    # governance.yaml's naming section as written, checked by mlz governance, which writes {} when there is
    # none; mlz-core's defaults fill the rest where names are made (tools/mlz/naming.py).
    naming = local.config.naming
    # bootstrap.yaml's bypass team, which vending gives each workload repo's rules; {} before it is set.
    github = try(local.config.github, {})
    monitoring = {
      workspace = module.management.resource_id
    }
  }
}
