output "landing_zone" {
  description = "What the landing zone's repo and the platform need: the deploy identity, the network, and the subscription. No secrets."
  value = {
    key             = local.lz.key
    subscription_id = local.lz.subscription_id
    # None of these once decommissioned: nothing vending made is left.
    decommissioned = local.decommission
    deploy_identity = local.decommission ? null : {
      client_id    = module.vending.umi_client_ids["deploy"]
      principal_id = module.vending.umi_principal_ids["deploy"]
    }
    virtual_network_id = local.decommission ? null : module.vending.virtual_network_resource_ids["spoke"]
    address_space      = local.decommission ? null : local.lz.network.address_space
  }
}
