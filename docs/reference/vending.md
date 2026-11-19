# vending/terraform: who owns each input

<!-- Written by python3 -m mlz reference from vending/terraform/inputs.yaml and tools/mlz/module-variables.json; do not edit. -->

The Terraform root of the stack `<workload>-<env>`: every input of every module call it makes, and who owns it. The map is `vending/terraform/inputs.yaml`; `tools/mlz/inputs.py` says how it is checked.

Each input is in one class:

| Class | Meaning | Can a customer set it? |
|---|---|---|
| setting | from a key in the customer's YAML | yes, with that key |
| derived | worked out by mlz-core: from the layout, the IP allocator, a contract or the naming | no; a name, under `names:` where the entry says so |
| fixed | Fortytwo's decision for every customer | no |
| open | mlz-core does not set it, so the module's default applies | under `advanced:` where the entry says so (docs/advanced.md); not yet elsewhere |
| closed | not supported yet | no |

An entry covers its input and everything in it: `bastion` closed means every attribute of `bastion`. A map or list of
objects passes through to the objects' attributes (`hub_virtual_networks.firewall.sku_tier` is each hub's).

## module.route_table: az-tf-mlz-route-table v0.1.1

The landing zone's route table, whose routes send everything to its hub's firewall.

Settings from a landing zone's file or a layer above it.

13 entries: 2 setting, 3 derived, 3 fixed, 4 open, 1 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `location` | string | setting | `location` in a landing zone's file or a layer above it |
| `name` | string | derived | the naming template, kept in _generated/names.yaml; set it with `names.route_table` in the landing zone's own file, `landing-zones/<workload>/<env>.yaml` |
| `parent_id` | string | derived | the landing zone's network resource group |
| `azure_managed_routes_enabled` | bool | open | the module's default: `false` |
| `bgp_route_propagation_enabled` | bool | fixed | `false`: a route learned from a gateway would go round the firewall |
| `routes` | map(object) | derived | the routes the hub module gives its region's spokes, from the platform contract |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retry` | object | open | the module's default: `null` |
| `timeouts` | object | open | the module's default: `null` |
| `lock` | object | closed | a decommission destroys what vending made, and a lock would stop it |
| `role_assignments` | map(object) | open | the module's default: `{}` |
| `tags` | map(string) | setting | `tags` in a landing zone's file or a layer above it: with Owner (owner), CostCenter (cost_center), Environment and Workload (the file's place) and ManagedBy |

## module.vending: Azure/avm-ptn-alz-sub-vending/azure 0.3.4

The landing zone's subscription in its management group, its network peered with the hub, an NSG per subnet, the workload repo's two identities and the budget.

Settings from a landing zone's file or a layer above it.

96 entries: 10 setting, 27 derived, 31 fixed, 15 open, 13 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `location` | string | setting | `location` in a landing zone's file or a layer above it |
| `enable_telemetry` | bool | fixed | `false`: no module mlz-core calls sends telemetry |
| `budget_enabled` | bool | derived | on when budget.monthly is set, until decommission is true |
| `budgets` | map(object) | | one, monthly, when budget.monthly is set |
| `budgets.name` | string | derived | the naming template, kept in _generated/names.yaml; set it with `names.budget` in the landing zone's own file, `landing-zones/<workload>/<env>.yaml` |
| `budgets.amount` | number | setting | `budget.monthly` in a landing zone's file or a layer above it |
| `budgets.time_grain` | string | fixed | `"Monthly"`: a monthly budget |
| `budgets.time_period_start` | string | derived | the first of the month the landing zone was vended |
| `budgets.time_period_end` | string | fixed | `"2099-12-31T00:00:00Z"`: a budget that does not run out |
| `budgets.relative_scope` | string | fixed | the module's default (`""`): the whole subscription |
| `budgets.resource_group_key` | string | fixed | the module's default (`null`): the whole subscription |
| `budgets.notifications` | map(object) | | two, at 80 % actual and 100 % forecast |
| `budgets.notifications.enabled` | bool | fixed | `true`: both alerts are on |
| `budgets.notifications.operator` | string | fixed | `"GreaterThan"`: an alert when spending passes the threshold |
| `budgets.notifications.threshold` | number | fixed | `{"actual_80": 80, "forecast_100": 100}`: a warning at 80 % spent, and when the month is forecast to go over |
| `budgets.notifications.threshold_type` | string | fixed | `{"actual_80": "Actual", "forecast_100": "Forecasted"}`: a warning at 80 % spent, and when the month is forecast to go over |
| `budgets.notifications.contact_emails` | list(string) | setting | `budget.contacts` in a landing zone's file or a layer above it: the owner when not set |
| `budgets.notifications.contact_roles` | list(string) | open | the module's default: `[]`; set it with `advanced.budgets.monthly.notifications.<actual_80 or forecast_100>.contact_roles` in a landing zone's file or a layer above it |
| `budgets.notifications.contact_groups` | list(string) | open | the module's default: `[]`; set it with `advanced.budgets.monthly.notifications.<actual_80 or forecast_100>.contact_groups` in a landing zone's file or a layer above it |
| `budgets.notifications.locale` | string | open | the module's default: `"en-us"`; set it with `advanced.budgets.monthly.notifications.<actual_80 or forecast_100>.locale` in a landing zone's file or a layer above it |
| `network_security_group_enabled` | bool | derived | on, until decommission is true |
| `network_security_groups` | map(object) | | one per subnet, by the subnet's name |
| `network_security_groups.name` | string | derived | the naming template, kept in _generated/names.yaml; set it with `names.network_security_groups` in the landing zone's own file, `landing-zones/<workload>/<env>.yaml` |
| `network_security_groups.location` | string | setting | `location` in a landing zone's file or a layer above it |
| `network_security_groups.resource_group_key` | string | fixed | the module's default (`null`): vending makes the landing zone's resource groups itself |
| `network_security_groups.resource_group_name_existing` | string | derived | the landing zone's network resource group |
| `network_security_groups.tags` | map(string) | setting | `tags` in a landing zone's file or a layer above it: with Owner, CostCenter, Environment, Workload and ManagedBy |
| `network_security_groups.resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `network_security_groups.security_rules` | map(object) | open | the module's default: `null`; set it with `advanced.network_security_groups.<subnet>.security_rules` in a landing zone's file or a layer above it |
| `resource_group_creation_enabled` | bool | fixed | the module's default (`false`): off: vending makes the landing zone's resource groups itself, named from the template |
| `resource_groups` | map(object) | fixed | the module's default (`{}`): vending makes the landing zone's resource groups itself, named from the template |
| `subscription_register_resource_providers_and_features` | map(set(string)) | open | the module's default: see the module; set it with `advanced.subscription_register_resource_providers_and_features` in a landing zone's file or a layer above it |
| `subscription_register_resource_providers_enabled` | bool | fixed | `true`: a new subscription needs its providers before anything is made in it |
| `role_assignment_enabled` | bool | derived | on when advanced: sets role_assignments |
| `role_assignments` | map(object) | open | the module's default: `{}`; set it with `advanced.role_assignments` in a landing zone's file or a layer above it |
| `route_table_enabled` | bool | fixed | the module's default (`false`): off: the route table is module.route_table's |
| `route_tables` | map(object) | fixed | the module's default (`{}`): the route table is module.route_table's |
| `subscription_alias_enabled` | bool | fixed | `false`: vending makes a subscription with one alias PUT of its own, which no plan reads back: reading an alias needs aliases/write, which the plan identity has not |
| `subscription_alias_name` | string | fixed | the module's default (`null`): unused: the module's alias is off |
| `subscription_billing_scope` | string | fixed | the module's default (`null`): unused: the module's alias is off; billing_scope goes to vending's own alias PUT |
| `subscription_display_name` | string | derived | the naming template, for a subscription vending makes; set it with `names.subscription` in the landing zone's own file, `landing-zones/<workload>/<env>.yaml` |
| `subscription_id` | string | setting | `subscription` in a landing zone's file or a layer above it: else the one vending makes where billing_scope says |
| `subscription_management_group_association_enabled` | bool | fixed | `true`: every landing zone's subscription goes into its archetype's group |
| `subscription_management_group_id` | string | derived | archetype's management group, from the governance contract; Decommissioned once decommission is true |
| `subscription_tags` | map(string) | setting | `tags` in a landing zone's file or a layer above it: with Owner (owner), CostCenter (cost_center), Environment and Workload (the file's place) and ManagedBy |
| `subscription_update_existing` | bool | derived | true for a subscription vending made, so the module names and tags it |
| `subscription_workload` | string | fixed | the module's default (`null`): unused: the module's alias is off |
| `wait_for_subscription_before_subscription_operations` | object | open | the module's default: `{}`; set it with `advanced.wait_for_subscription_before_subscription_operations` in a landing zone's file or a layer above it |
| `umi_enabled` | bool | derived | on, until decommission is true |
| `user_managed_identities` | map(object) | | plan (Reader) and deploy (Contributor), for the workload repo |
| `user_managed_identities.name` | string | derived | the naming template, kept in _generated/names.yaml; set it with `names.plan_identity` or `names.deploy_identity` in the landing zone's own file, `landing-zones/<workload>/<env>.yaml` |
| `user_managed_identities.resource_group_key` | string | fixed | the module's default (`null`): vending makes the landing zone's resource groups itself |
| `user_managed_identities.resource_group_name_existing` | string | derived | the landing zone's identity resource group |
| `user_managed_identities.location` | string | setting | `location` in a landing zone's file or a layer above it |
| `user_managed_identities.tags` | map(string) | derived | Workload and Environment (the file's place) and ManagedBy only: a tag change must not update the identities, whose role assignments are named from their principal IDs |
| `user_managed_identities.role_assignments` | map(object) | fixed | `{"deploy": "Contributor", "plan": "Reader"}`: on the landing zone's subscription: the platform team runs every pipeline (decision D2) |
| `user_managed_identities.federated_credentials_github` | map(object) | closed | the identities trust GitHub's immutable subjects, through federated_credentials_advanced |
| `user_managed_identities.federated_credentials_terraform_cloud` | map(object) | closed | the workload repo runs on GitHub |
| `user_managed_identities.federated_credentials_advanced` | map(object) | derived | the workload repo's GitHub subjects, by ID: its pull requests and main for plan, its apply environment for deploy |
| `virtual_network_enabled` | bool | derived | on, until decommission is true |
| `virtual_networks` | map(object) | | one, spoke |
| `virtual_networks.name` | string | derived | the naming template, kept in _generated/names.yaml; set it with `names.virtual_network` in the landing zone's own file, `landing-zones/<workload>/<env>.yaml` |
| `virtual_networks.address_space` | list(string) | derived | the landing zone's range in _generated/ip-plan.yaml, whose size network.size asks for |
| `virtual_networks.resource_group_key` | string | fixed | the module's default (`null`): vending makes the landing zone's resource groups itself |
| `virtual_networks.resource_group_name_existing` | string | derived | the landing zone's network resource group |
| `virtual_networks.ipam_pools` | list(object) | closed | addresses come from the IP allocator |
| `virtual_networks.location` | string | setting | `location` in a landing zone's file or a layer above it |
| `virtual_networks.dns_servers` | list(string) | derived | the region's DNS servers, from the platform contract |
| `virtual_networks.flow_timeout_in_minutes` | number | open | the module's default: `null`; set it with `advanced.virtual_networks.spoke.flow_timeout_in_minutes` in a landing zone's file or a layer above it |
| `virtual_networks.ddos_protection_enabled` | bool | open | the module's default: `false`; set it with `advanced.virtual_networks.spoke.ddos_protection_enabled` in a landing zone's file or a layer above it |
| `virtual_networks.ddos_protection_plan_id` | string | open | the module's default: `null`; set it with `advanced.virtual_networks.spoke.ddos_protection_plan_id` in a landing zone's file or a layer above it |
| `virtual_networks.subnets.name` | string | derived | snet-&lt;subnet&gt; |
| `virtual_networks.subnets.address_prefix` | string | derived | the subnet's block in _generated/ip-plan.yaml, whose size network.subnets asks for |
| `virtual_networks.subnets.address_prefixes` | list(string) | closed | one prefix per subnet, from the IP allocator |
| `virtual_networks.subnets.ipam_pools` | list(object) | closed | addresses come from the IP allocator |
| `virtual_networks.subnets.nat_gateway` | object | closed | everything leaves through the hub's firewall |
| `virtual_networks.subnets.network_security_group` | object | derived | the subnet's own NSG |
| `virtual_networks.subnets.private_endpoint_network_policies` | string | open | the module's default: `"Enabled"`; set it with `advanced.virtual_networks.spoke.subnets.<subnet>.private_endpoint_network_policies` in a landing zone's file or a layer above it |
| `virtual_networks.subnets.private_link_service_network_policies_enabled` | bool | open | the module's default: `true`; set it with `advanced.virtual_networks.spoke.subnets.<subnet>.private_link_service_network_policies_enabled` in a landing zone's file or a layer above it |
| `virtual_networks.subnets.route_table` | object | derived | the landing zone's route table |
| `virtual_networks.subnets.default_outbound_access_enabled` | bool | fixed | the module's default (`false`): off: everything leaves through the hub's firewall |
| `virtual_networks.subnets.service_endpoints` | set(string) | open | the module's default: `null`; set it with `advanced.virtual_networks.spoke.subnets.<subnet>.service_endpoints` in a landing zone's file or a layer above it |
| `virtual_networks.subnets.service_endpoint_policies` | map(object) | open | the module's default: `null`; set it with `advanced.virtual_networks.spoke.subnets.<subnet>.service_endpoint_policies` in a landing zone's file or a layer above it |
| `virtual_networks.subnets.delegations` | list(object) | open | the module's default: `null`; set it with `advanced.virtual_networks.spoke.subnets.<subnet>.delegations` in a landing zone's file or a layer above it |
| `virtual_networks.hub_network_resource_id` | string | derived | the region's hub, from the platform contract |
| `virtual_networks.hub_peering_enabled` | bool | fixed | `true`: every landing zone is a spoke of its region's hub |
| `virtual_networks.hub_peering_direction` | string | fixed | the module's default (`"both"`): both: vending makes both ends |
| `virtual_networks.hub_peering_name_tohub` | string | derived | peer-&lt;key&gt;-to-hub |
| `virtual_networks.hub_peering_options_tohub` | object | fixed | `{"use_remote_gateways": false}`: the hubs have no gateway yet; the rest is the module's default |
| `virtual_networks.hub_peering_name_fromhub` | string | derived | peer-hub-to-&lt;key&gt; |
| `virtual_networks.hub_peering_options_fromhub` | object | fixed | `{"allow_gateway_transit": false}`: the hubs have no gateway yet; the rest is the module's default |
| `virtual_networks.mesh_peering_enabled` | bool | fixed | the module's default (`false`): off: one landing zone reaches another through the firewall, by a rule |
| `virtual_networks.mesh_peering_allow_forwarded_traffic` | bool | fixed | the module's default (`false`): no mesh peering |
| `virtual_networks.vwan_associated_routetable_resource_id` | string | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.vwan_connection_enabled` | bool | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.vwan_connection_name` | string | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.vwan_hub_resource_id` | string | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.vwan_propagated_routetables_labels` | list(string) | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.vwan_propagated_routetables_resource_ids` | list(string) | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.vwan_security_configuration` | object | closed | MLZ's hubs are virtual networks, not Virtual WAN |
| `virtual_networks.tags` | map(string) | setting | `tags` in a landing zone's file or a layer above it: with Owner, CostCenter, Environment, Workload and ManagedBy |
