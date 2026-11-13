# What vending plans for a live landing zone and for one marked decommission: true, with the providers mocked:
# no Azure. Run: terraform -chdir=vending/terraform test
mock_provider "azapi" {
  # The sub-vending module looks the subscription up, and where it is placed now: in Corp, as vended.
  mock_data "azapi_resource_list" {
    defaults = {
      output = {
        value = [{
          subscriptionId = "00000000-0000-0000-0000-000000000010"
          id             = "/providers/Microsoft.Management/managementGroups/mlz-lz-corp/subscriptions/00000000-0000-0000-0000-000000000010"
        }]
      }
    }
  }
}
mock_provider "time" {}

run "live" {
  command = plan
  variables {
    config_file = "tests/live.json"
  }
  assert {
    condition     = length(azapi_resource.resource_group) == 2 && length(module.route_table) == 1
    error_message = "A live landing zone has its two resource groups and its route table."
  }
  assert {
    condition     = output.landing_zone.decommissioned == false && output.landing_zone.address_space == "10.100.8.0/24"
    error_message = "A live landing zone's output names its network."
  }
  assert {
    condition     = azapi_resource.resource_group["network"].name == "rg-acme-pay-net-pd-nwe-01" && azapi_resource.resource_group["identity"].name == "rg-acme-pay-id-pd-nwe-01"
    error_message = "Names come from the config, as python3 -m mlz vending worked them out."
  }
  assert {
    condition     = azapi_resource.state.name == "stacmepaystatepdnwe01" && azapi_resource.state_resource_group.name == "rg-acme-pay-state-pd-nwe-01" && azapi_resource.state_container.name == "tfstate"
    error_message = "The workload repo's state account is in a group of its own, named from the config."
  }
  assert {
    condition     = toset(keys(azapi_resource.state_role)) == toset(["plan", "deploy"])
    error_message = "The plan identity reads the state and the deploy identity writes it."
  }
}

run "decommissioned" {
  command = plan
  variables {
    config_file = "tests/decommissioned.json"
  }
  assert {
    condition     = length(azapi_resource.resource_group) == 0 && length(module.route_table) == 0
    error_message = "A decommissioned landing zone keeps neither resource group nor its route table."
  }
  assert {
    condition     = output.landing_zone.decommissioned && output.landing_zone.deploy_identity == null && output.landing_zone.plan_identity == null && output.landing_zone.virtual_network_id == null
    error_message = "A decommissioned landing zone's output has no identity or network."
  }
  assert {
    condition     = azapi_resource.state.name == "stacmepaystatepdnwe01" && length(azapi_resource.state_role) == 0
    error_message = "A decommissioned landing zone keeps its state account, without the roles of the identities that went."
  }
}
