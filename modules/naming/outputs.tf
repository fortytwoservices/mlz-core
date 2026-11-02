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

output "landing_zone" {
  description = "Names in a landing zone's subscription; null without `landing_zone`."
  value = var.landing_zone == null ? null : {
    network_resource_group  = "rg-${var.landing_zone}-network-${local.region}"
    identity_resource_group = "rg-${var.landing_zone}-identity-${local.region}"
    virtual_network         = "vnet-${var.landing_zone}-${local.region}"
    route_table             = "rt-${var.landing_zone}-${local.region}"
    deploy_identity         = "id-${var.landing_zone}-deploy-${local.region}"
    budget                  = "budget-${var.landing_zone}"
    peering_to_hub          = "peer-${var.landing_zone}-to-hub"
    peering_from_hub        = "peer-hub-to-${var.landing_zone}"
    # Per subnet: format(subnet, "<subnet name>"), format(network_security_group, "<subnet name>").
    subnet                 = "snet-%s"
    network_security_group = "nsg-${var.landing_zone}-%s-${local.region}"
  }
}

output "connectivity" {
  description = "Names in the connectivity subscription that other roles refer to before it is applied."
  value = {
    dns_resource_group = format("rg-${local.suffix}", "dns")
  }
}
