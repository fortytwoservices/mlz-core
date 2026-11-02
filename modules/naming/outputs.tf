output "region_code" {
  description = "The region's short code used in names, for example `nwe` for norwayeast."
  value       = local.region
}

output "management" {
  description = "Names in the management subscription."
  value = {
    resource_group = format("rg-${local.suffix}", "management")
    workspace      = format("law-${local.suffix}", "management")
    ama_identity   = format("id-${local.suffix}", "ama")
    data_collection_rules = {
      change_tracking = format("dcr-${local.suffix}", "changetracking")
      vm_insights     = format("dcr-${local.suffix}", "vminsights")
      defender_sql    = format("dcr-${local.suffix}", "defendersql")
    }
  }
}

output "connectivity" {
  description = "Names in the connectivity subscription that other roles refer to before it is applied."
  value = {
    dns_resource_group = format("rg-${local.suffix}", "dns")
  }
}
