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
    condition     = output.landing_zone.decommissioned && output.landing_zone.deploy_identity == null && output.landing_zone.virtual_network_id == null
    error_message = "A decommissioned landing zone's output has no identity or network."
  }
}
