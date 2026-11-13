output "landing_zone" {
  description = "What the landing zone's repo and the platform need: its identities, its state, the network, and the subscription. No secrets."
  value = {
    key             = local.lz.key
    subscription_id = local.lz.subscription_id
    # None of these once decommissioned: nothing vending made is left.
    decommissioned = local.decommission
    deploy_identity = local.decommission ? null : {
      client_id    = module.vending.umi_client_ids["deploy"]
      principal_id = module.vending.umi_principal_ids["deploy"]
    }
    plan_identity = local.decommission ? null : {
      client_id    = module.vending.umi_client_ids["plan"]
      principal_id = module.vending.umi_principal_ids["plan"]
    }
    # The workload repo's Terraform backend: kept, decommissioned or not.
    state = {
      resource_group  = azapi_resource.state_resource_group.name
      storage_account = azapi_resource.state.name
      container       = azapi_resource.state_container.name
    }
    virtual_network_id = local.decommission ? null : module.vending.virtual_network_resource_ids["spoke"]
    address_space      = local.decommission ? null : local.lz.network.address_space
  }
}
