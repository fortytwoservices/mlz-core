# governance/terraform: who owns each input

<!-- Written by python3 -m mlz reference from governance/terraform/inputs.yaml and tools/mlz/module-variables.json; do not edit. -->

The Terraform root of the stack `governance`: every input of every module call it makes, and who owns it. The map is `governance/terraform/inputs.yaml`; `tools/mlz/inputs.py` says how it is checked.

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

## module.alz: Azure/avm-ptn-alz/azurerm 0.22.0

The management group tree, ALZ's policies and their assignments, and the platform subscriptions' places in the tree.

Settings from `customer.yaml`.

27 entries: 4 setting, 3 derived, 9 fixed, 10 open, 1 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `architecture_name` | string | setting | `root_id` in `customer.yaml`: the rendered architecture is named after it |
| `location` | string | setting | `primary_region` in `customer.yaml` |
| `parent_resource_id` | string | setting | `parent_management_group_id` in `customer.yaml`: the tenant root group when it is not set |
| `management_group_hierarchy_settings` | object | open | the module's default: `null` |
| `management_groups_dependencies` | any | fixed | the module's default (`null`): the tree waits for nothing outside governance |
| `override_policy_definition_parameter_assign_permissions_set` | set(object) | fixed | the module's default (see the module): ALZ's own list |
| `override_policy_definition_parameter_assign_permissions_unset` | set(object) | fixed | the list in `governance/terraform/private-dns-zone-parameters.json`: no role on each Private Link zone, which fails before the zones exist; Deploy-Private-DNS-Zones' identity gets Private DNS Zone Contributor on the zones' subscription instead |
| `parent_id_overrides` | object | closed | definitions stay where ALZ's library puts them, where the assignments look for them |
| `policy_assignment_non_compliance_message_settings` | object | open | the module's default: `{}` |
| `policy_assignments_dependencies` | any | derived | management's workspace, identity and data collection rules, which the policies point at |
| `policy_assignments_to_modify` | map(object) | open | the module's default: `{}` |
| `policy_default_values` | map(string) | derived | management's resource IDs, built from names; where the Private Link DNS zones are; allowed_regions and security_contact from customer.yaml |
| `policy_role_assignments_dependencies` | any | fixed | the module's default (`null`): the policies' role assignments wait for nothing outside governance |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retries` | object | open | the module's default: `{}` |
| `schema_validation_enabled` | object | fixed | the module's default (`{}`): Azure's schema checks every body before it is sent |
| `subscription_placement` | map(object) | | one per platform subscription in customer.yaml; security only when management_groups.security is true |
| `subscription_placement.subscription_id` | string | setting | `subscriptions` in `customer.yaml`: security's only when management_groups.security is true |
| `subscription_placement.management_group_name` | string | derived | &lt;root_id&gt;-&lt;role&gt;, the group of the subscription's role |
| `subscription_placement_destroy_behavior` | string | open | the module's default: `"default"` |
| `subscription_placement_destroy_custom_target_management_group_id` | string | open | the module's default: `null` |
| `telemetry_additional_content` | map(string) | fixed | the module's default (`null`): no telemetry |
| `timeouts` | object | open | the module's default: `{}` |
| `delays` | object | open | the module's default: `{}` |
| `management_group_role_assignments` | map(object) | open | the module's default: `{}` |
| `role_assignment_definition_lookup_enabled` | bool | open | the module's default: `true` |
| `role_assignment_name_use_random_uuid` | bool | fixed | the module's default (`false`): off: an assignment keeps its name, so no plan replaces it |
| `enable_telemetry` | bool | fixed | `false`: no module mlz-core calls sends telemetry |

## module.management: az-tf-mlz-management v0.2.0

Management's Log Analytics workspace, the Azure Monitor Agent's identity, and the data collection rules ALZ's policies point machines at.

Settings from `customer.yaml`.

26 entries: 1 setting, 6 derived, 4 fixed, 14 open, 1 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `location` | string | setting | `primary_region` in `customer.yaml` |
| `name` | string | derived | the naming template (customer.yaml's naming) |
| `parent_id` | string | derived | management's resource group, made by this root, named by the template |
| `daily_quota_gb` | number | open | the module's default: `null` |
| `local_authentication_enabled` | bool | fixed | the module's default (`false`): off: Entra ID only |
| `permanently_delete_on_destroy` | bool | open | the module's default: `false` |
| `public_network_access_for_ingestion` | string | open | the module's default: `"Enabled"` |
| `public_network_access_for_query` | string | open | the module's default: `"Enabled"` |
| `resource_permissions_enabled` | bool | open | the module's default: `true` |
| `retention_in_days` | number | open | the module's default: `30` |
| `data_collection_rules.change_tracking.name` | string | derived | the naming template |
| `data_collection_rules.vm_insights.name` | string | derived | the naming template |
| `data_collection_rules.vm_insights.processes_and_dependencies_enabled` | bool | open | the module's default: `false` |
| `data_collection_rules.defender_sql.name` | string | derived | the naming template |
| `data_collection_rules.defender_sql.sql_queries_collection_enabled` | bool | open | the module's default: `false` |
| `user_assigned_identity.name` | string | derived | the naming template |
| `defender_plans` | map(object) | closed | Defender for Cloud plans per subscription are not offered yet |
| `sentinel_onboarding` | object | open | the module's default: `null` |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retry` | object | open | the module's default: `{}` |
| `timeouts` | object | open | the module's default: `null` |
| `diagnostic_settings` | map(object) | open | the module's default: `{}` |
| `lock` | object | open | the module's default: `null` |
| `role_assignments` | map(object) | open | the module's default: `{}` |
| `tags` | map(string) | fixed | `{"ManagedBy": "mlz-core"}`: what mlz-core built, at a glance |
