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
# The landing zone's GitHub organization, its owning team, and the repo vending makes, by the IDs GitHub would give.
mock_provider "github" {
  mock_data "github_organization" {
    defaults = { id = "1001" }
  }
  mock_data "github_team" {
    defaults = { id = "42" }
  }
  mock_data "github_rest_api" {
    defaults = { body = "{\"id\": 3003, \"name\": \"payments-infra\"}" }
  }
}

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
  assert {
    condition     = output.landing_zone.subscription_id == "00000000-0000-0000-0000-000000000010" && azapi_resource.resource_group["network"].parent_id == "/subscriptions/00000000-0000-0000-0000-000000000010" && length(terraform_data.made_subscription) == 0
    error_message = "A subscription handed over is the one the landing zone's resources go in, and not one vending made."
  }
}

run "made_subscription" {
  command = plan
  variables {
    config_file = "tests/made-subscription.json"
  }
  # The new subscription's ID is Azure's, known once the alias is made: given here, as Azure would.
  override_resource {
    target          = module.vending.module.subscription[0].azapi_resource.subscription[0]
    values          = { output = { properties = { subscriptionId = "00000000-0000-0000-0000-000000000020" } } }
    override_during = plan
  }
  assert {
    condition     = output.landing_zone.subscription_id == "00000000-0000-0000-0000-000000000020" && azapi_resource.resource_group["network"].parent_id == "/subscriptions/00000000-0000-0000-0000-000000000020" && azapi_resource.state_resource_group.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000020"
    error_message = "A landing zone without a subscription gets the one vending makes, and its resources go in it."
  }
  assert {
    condition     = length(terraform_data.made_subscription) == 1
    error_message = "A subscription vending makes is guarded: a plan that would cancel it is refused."
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

run "with_a_repo" {
  command = plan
  variables {
    config_file = "tests/with-repo.json"
  }
  # The repo's ID is GitHub's, known once it is made: given here, as GitHub would.
  override_resource {
    target = github_repository.workload["made"]
    values = { repo_id = 2002 }
  }
  assert {
    condition     = github_repository.workload["made"].name == "payments-prod" && github_repository.workload["made"].archive_on_destroy && github_repository.workload["made"].visibility == "private"
    error_message = "Vending makes the landing zone's repo, private, archived rather than deleted."
  }
  assert {
    condition     = github_team_repository.owner[0].permission == "maintain" && contains(github_repository_environment.apply[0].reviewers[0].teams, 42)
    error_message = "The owning team maintains the repo and approves its applies."
  }
  assert {
    condition     = github_repository_environment_deployment_policy.main[0].branch_pattern == "main" && github_branch_default.main[0].branch == "main" && github_branch_default.main[0].rename
    error_message = "The repo's default branch is main, renamed from whatever the organization's default made, and only main may apply."
  }
  assert {
    condition     = toset(keys(github_actions_variable.workload)) == toset(["AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "TFSTATE_RESOURCE_GROUP", "TFSTATE_STORAGE_ACCOUNT", "TFSTATE_CONTAINER"]) && github_actions_variable.workload["TFSTATE_STORAGE_ACCOUNT"].value == "stacmepaystatepdnwe01"
    error_message = "The repo's variables name its subscription, its plan identity and its state."
  }
}

run "with_an_existing_repo" {
  command = plan
  variables {
    config_file = "tests/existing-repo.json"
  }
  assert {
    condition     = length(github_repository.workload) == 0 && length(github_actions_variable.workload) == 0 && length(github_repository_environment.apply) == 0
    error_message = "An existing repo is the team's own: vending only trusts it."
  }
  assert {
    condition     = local.subject == "repo:acme-org@1001/payments-infra@3003"
    error_message = "The identities trust GitHub's immutable subject for the repo, by the organization's and the repo's IDs."
  }
}

run "decommissioned_with_a_repo" {
  command = plan
  variables {
    config_file = "tests/decommissioned-with-repo.json"
  }
  assert {
    condition     = length(github_repository.workload) == 0 && length(github_repository_environment.apply) == 0 && local.subject == null
    error_message = "A decommissioned landing zone's repo goes (archive_on_destroy archives it), with nothing trusting it."
  }
}
