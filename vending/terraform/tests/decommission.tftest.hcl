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
    condition     = toset(keys(azapi_resource.identity_role)) == toset(["plan/subscription", "deploy/subscription", "plan/state", "deploy/state"])
    error_message = "Each identity has its role on the subscription and on the state's container."
  }
  assert {
    condition     = length(azapi_resource.state_exemption) == 0
    error_message = "No exemption where the management group has no Deny-Public-Endpoints assignment."
  }
  assert {
    condition     = output.landing_zone.subscription_id == "00000000-0000-0000-0000-000000000010" && azapi_resource.resource_group["network"].parent_id == "/subscriptions/00000000-0000-0000-0000-000000000010" && length(azapi_resource_action.subscription) == 0
    error_message = "A subscription handed over is the one the landing zone's resources go in, and vending makes none."
  }
}

# A landing zone already applied, whose identities a tag change updates: azapi then knows their principal IDs only
# after the apply, so the role assignments' names come from what stays known, the scope and the identity's resource
# ID. Named from a principal ID, as sub-vending names its own, they would be replaced on every tag change, the
# identities without their roles meanwhile. Sub-vending's outputs are given here, as such a landing zone has them.
run "identity_roles_named_from_ids" {
  command = plan
  variables {
    config_file = "tests/live.json"
  }
  override_module {
    target = module.vending
    outputs = {
      umi_resource_ids = {
        plan   = "/subscriptions/00000000-0000-0000-0000-000000000010/resourceGroups/rg-acme-pay-id-pd-nwe-01/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-acme-pay-plan-pd-nwe-01"
        deploy = "/subscriptions/00000000-0000-0000-0000-000000000010/resourceGroups/rg-acme-pay-id-pd-nwe-01/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-acme-pay-pd-nwe-01"
      }
      umi_principal_ids            = { plan = "00000000-0000-0000-0000-0000000000a1", deploy = "00000000-0000-0000-0000-0000000000a2" }
      umi_client_ids               = { plan = "00000000-0000-0000-0000-0000000000b1", deploy = "00000000-0000-0000-0000-0000000000b2" }
      virtual_network_resource_ids = { spoke = "/subscriptions/00000000-0000-0000-0000-000000000010/resourceGroups/rg-acme-pay-net-pd-nwe-01/providers/Microsoft.Network/virtualNetworks/vnet-acme-pay-pd-nwe-01" }
    }
  }
  override_resource {
    target          = azapi_resource.state_container
    values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000010/resourceGroups/rg-acme-pay-state-pd-nwe-01/providers/Microsoft.Storage/storageAccounts/stacmepaystatepdnwe01/blobServices/default/containers/tfstate" }
    override_during = plan
  }
  assert {
    condition = alltrue([for key, role in azapi_resource.identity_role :
      role.name == uuidv5("url", "mlz-core/${role.parent_id}/${module.vending.umi_resource_ids[split("/", key)[0]]}")
    ])
    error_message = "Each role assignment is named from its scope and its identity's resource ID, never from the principal ID."
  }
  assert {
    condition = alltrue([for key, role in azapi_resource.identity_role :
      role.body.properties.principalId == module.vending.umi_principal_ids[split("/", key)[0]] && role.body.properties.principalType == "ServicePrincipal"
    ])
    error_message = "Each role assignment is given to its own identity's principal."
  }
  assert {
    condition     = azapi_resource.identity_role["plan/subscription"].parent_id == "/subscriptions/00000000-0000-0000-0000-000000000010" && endswith(azapi_resource.identity_role["plan/subscription"].body.properties.roleDefinitionId, "/acdd72a7-3385-48ef-bd42-f606fba81ae7") && endswith(azapi_resource.identity_role["deploy/subscription"].body.properties.roleDefinitionId, "/b24988ac-6180-42a0-ab88-20f7382dd24c")
    error_message = "On the subscription, the plan identity is Reader and the deploy identity Contributor."
  }
  assert {
    condition     = endswith(azapi_resource.identity_role["plan/state"].parent_id, "/containers/tfstate") && endswith(azapi_resource.identity_role["plan/state"].body.properties.roleDefinitionId, "/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1") && endswith(azapi_resource.identity_role["deploy/state"].body.properties.roleDefinitionId, "/ba92f5b4-2d11-453d-a403-e96b0029c9fe")
    error_message = "On the state's container, the plan identity reads (Storage Blob Data Reader) and the deploy identity writes (Storage Blob Data Contributor)."
  }
}

# A live landing zone without budget.monthly: no budget, and the plan goes through sub-vending's types with its
# optional block left out (a null typed as an empty object would fail there, as the hub's Bastion did in v0.11.3).
run "live_without_a_budget" {
  command = plan
  variables {
    config_file = "tests/no-budget.json"
  }
  assert {
    condition     = length(local.budgets) == 0 && length(azapi_resource.resource_group) == 2 && length(azapi_resource.identity_role) == 4
    error_message = "A live landing zone without a budget has everything but the budget."
  }
}

# A corp landing zone: ALZ's Deny-Public-Endpoints on its management group would refuse the state account.
run "corp_denies_public_endpoints" {
  command = plan
  variables {
    config_file = "tests/live.json"
  }
  override_data {
    target = data.azapi_resource.public_endpoints_deny
    values = {
      exists = true
      id     = "/providers/Microsoft.Management/managementGroups/mlz-lz-corp/providers/Microsoft.Authorization/policyAssignments/Deny-Public-Endpoints"
    }
  }
  assert {
    condition     = data.azapi_resource.public_endpoints_deny.parent_id == "/providers/Microsoft.Management/managementGroups/mlz-lz-corp"
    error_message = "The assignment is looked for on the landing zone's own management group."
  }
  assert {
    condition     = length(azapi_resource.state_exemption) == 1 && azapi_resource.state_exemption[0].name == "mlz-state-public-endpoint"
    error_message = "The state's group is exempt where the management group denies public endpoints."
  }
  assert {
    condition     = azapi_resource.state_exemption[0].body.properties.policyAssignmentId == "/providers/Microsoft.Management/managementGroups/mlz-lz-corp/providers/Microsoft.Authorization/policyAssignments/Deny-Public-Endpoints" && azapi_resource.state_exemption[0].body.properties.policyDefinitionReferenceIds == ["StorageDenyPaasPublicIP"] && azapi_resource.state_exemption[0].body.properties.exemptionCategory == "Waiver"
    error_message = "The exemption covers only the storage rule of that assignment, as a waiver."
  }
}

run "made_subscription" {
  command = plan
  variables {
    config_file = "tests/made-subscription.json"
  }
  # The new subscription's ID is Azure's, in the alias's answer: given here, as Azure would.
  override_resource {
    target          = azapi_resource_action.subscription["made"]
    values          = { output = { properties = { subscriptionId = "00000000-0000-0000-0000-000000000020" } } }
    override_during = plan
  }
  assert {
    condition     = output.landing_zone.subscription_id == "00000000-0000-0000-0000-000000000020" && azapi_resource.resource_group["network"].parent_id == "/subscriptions/00000000-0000-0000-0000-000000000020" && azapi_resource.state_resource_group.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000020"
    error_message = "A landing zone without a subscription gets the one vending makes, and its resources go in it."
  }
  assert {
    condition     = azapi_resource_action.subscription["made"].resource_id == "/providers/Microsoft.Subscription/aliases/mlz-payments-prod" && azapi_resource_action.subscription["made"].method == "PUT" && azapi_resource_action.subscription["made"].when == "apply"
    error_message = "The subscription is made once, by an alias from the root and the key, and nothing is done to it on destroy."
  }
  assert {
    condition     = azapi_resource_action.subscription["made"].body.properties.billingScope == "/providers/Microsoft.Billing/billingAccounts/1234567/enrollmentAccounts/7654321" && azapi_resource_action.subscription["made"].body.properties.displayName == "sub-acme-pay-pd-nwe-01" && azapi_resource_action.subscription["made"].body.properties.additionalProperties.managementGroupId == "/providers/Microsoft.Management/managementGroups/mlz-lz-corp"
    error_message = "It is billed where the billing scope says, named from the config, and made in its management group."
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
    condition     = azapi_resource.state.name == "stacmepaystatepdnwe01" && length(azapi_resource.identity_role) == 0
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
    condition     = github_team_repository.owner[0].permission == "maintain" && contains(github_repository_environment.apply[0].reviewers[0].teams, 42) && length(github_actions_environment_variable.approval) == 0
    error_message = "The owning team maintains the repo and approves its applies (GitHub Enterprise: no MLZ_APPROVAL)."
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

run "with_a_repo_on_github_team" {
  command = plan
  variables {
    config_file = "tests/with-repo-team.json"
  }
  override_resource {
    target = github_repository.workload["made"]
    values = { repo_id = 2002 }
  }
  assert {
    condition     = length(github_repository_environment.apply[0].reviewers) == 0 && github_repository_environment_deployment_policy.main[0].branch_pattern == "main"
    error_message = "On GitHub Team the apply environment has no reviewer (the pull request's approval is the gate), and only main may apply."
  }
  assert {
    condition     = github_actions_environment_variable.approval[0].variable_name == "MLZ_APPROVAL" && github_actions_environment_variable.approval[0].value == "pull-request" && github_actions_environment_variable.approval[0].environment == "apply"
    error_message = "On GitHub Team the apply environment says it has no reviewer, so an apply whose pipeline expects one refuses."
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

# A landing zone's advanced: (tools/mlz/advanced.py) reaches sub-vending's objects beside mlz-core's values, which it
# cannot change; live.json, the same landing zone without it, is given what it always was (the run above).
run "advanced" {
  command = plan
  variables {
    config_file = "tests/advanced.json"
  }
  assert {
    condition     = local.virtual_networks["spoke"].ddos_protection_enabled == false && tolist(local.virtual_networks["spoke"].subnets["default"].service_endpoints) == tolist(["Microsoft.Storage"])
    error_message = "advanced: did not reach the spoke and its subnet."
  }
  assert {
    condition     = local.virtual_networks["spoke"].name == "vnet-acme-pay-pd-nwe-01" && local.virtual_networks["spoke"].subnets["default"].address_prefix == "10.100.8.0/26"
    error_message = "mlz-core's own values must stay as they are beside advanced:."
  }
  assert {
    condition     = local.network_security_groups["default"].security_rules["deny-rdp"].priority == 200 && local.network_security_groups["default"].name == "nsg-acme-pay-default-pd-nwe-01"
    error_message = "advanced: did not reach the subnet's NSG beside its name."
  }
  assert {
    condition     = tolist(local.budgets["monthly"].notifications["actual_80"].contact_roles) == tolist(["Owner"]) && local.budgets["monthly"].notifications["actual_80"].threshold == 80
    error_message = "advanced: did not reach the budget's alert beside its threshold."
  }
}

run "without_advanced" {
  command = plan
  variables {
    config_file = "tests/live.json"
  }
  # Exactly the attributes mlz-core gave each object before advanced: existed.
  assert {
    condition = (
      toset(keys(local.virtual_networks["spoke"])) == toset(["name", "address_space", "resource_group_name_existing", "location", "dns_servers", "tags", "subnets", "hub_network_resource_id", "hub_peering_enabled", "hub_peering_name_tohub", "hub_peering_name_fromhub", "hub_peering_options_tohub", "hub_peering_options_fromhub"]) &&
      toset(keys(local.virtual_networks["spoke"].subnets["default"])) == toset(["name", "address_prefix", "network_security_group", "route_table"]) &&
      toset(keys(local.network_security_groups["default"])) == toset(["name", "resource_group_name_existing", "location", "tags"]) &&
      toset(keys(local.budgets["monthly"].notifications["actual_80"])) == toset(["enabled", "operator", "threshold", "contact_emails"])
    )
    error_message = "A landing zone without advanced: is not given what it was before."
  }
}
