output "contract" {
  description = <<-EOT
    What governance and vending need from the platform, published as JSON (`_generated/platform.json` in
    their repos). No secrets.
  EOT
  value = {
    connectivity = module.connectivity.connectivity
    # The hubs' own ranges, which the IP allocator never gives to a landing zone.
    hub_address_spaces = { for region, hub in local.config.hubs : region => hub.address_space }
    # For governance's Private Link DNS policy.
    private_dns = {
      subscription_id     = local.config.connectivity_subscription_id
      resource_group_name = module.naming.connectivity.dns_resource_group
      region              = local.config.primary_region
    }
  }
}
