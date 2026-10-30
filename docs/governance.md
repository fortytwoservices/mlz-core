# Governance

The `mlz-governance` role: management groups, Azure Policy and (next) central management. It is built
on Microsoft's `Azure/avm-ptn-alz/azurerm` module and the Azure Landing Zones library, plus Fortytwo's
library and a per-customer rendering step.

## How it fits together

1. The customer's `customer.yaml` holds only what differs from Fortytwo's defaults
   (`governance/defaults.yaml`).
2. `governance/scripts/render_library.py` merges the two, checks them, and writes the customer's part of
   the ALZ library: the management group tree, Fortytwo's archetype overrides and the tag policy
   assignments, plus `config.json` for Terraform. Management group IDs cannot be templated at plan time,
   which is why this step exists.
3. `governance/terraform` loads three libraries in order: Microsoft's (`platform/alz` 2026.10.0, a
   dependency of `governance/lib`), Fortytwo's (`governance/lib`), and the rendered one. It calls the ALZ
   module with telemetry off.

## What the defaults give (decided by the user, 30 October 2026)

| Topic | Default |
|---|---|
| Management group IDs | Kept from the old product: `<root>`, `-platform`, `-management`, `-connectivity`, `-identity`, `-landing-zones`, `-lz-corp`, `-lz-online`, `-sandboxes`, `-decommissioned`, `-unusedsub` |
| Data platform | Dropped for now: no `-lz-dataplatform` group, no Databricks policies |
| Allowed regions | norwayeast, norwaywest, westeurope, northeurope, swedencentral, for resources and resource groups in the landing zones |
| Tags | `Owner` and `CostCenter` required on landing zone resource groups (deny); `Owner`, `CostCenter`, `Environment`, `Workload` inherited from the resource group (modify). No `Location` tag. The deny uses Fortytwo's `Deny-RG-Without-Tag` (governance/lib), the built-in rule plus `tags.exempt_resource_groups`: groups that Azure services and policy deployments create without the customer's tags (NetworkWatcherRG, the Service Health alerts group, Defender export, AKS node groups, ...) |
| DDoS | Off: `Enable-DDoS-VNET` removed from connectivity and landing zones |
| Subnets without an NSG | Denied in landing zones (ALZ default kept) |
| Guest attestation | Not used: `Deploy-GuestAttest` removed from platform and landing zones |
| Log Analytics by policy | Not used (no longer in the ALZ library) |
| Corp | ALZ default, including public IPs denied on network cards |
| New ALZ assignments | Service health alerts and security benchmark monitoring kept (both at the root) |

**Proposed, not yet confirmed:** the Security management group is included (ALZ's current hierarchy, for
security tooling such as Sentinel), Local is left out unless a customer sets `management_groups.local`,
and `-unusedsub` is kept. Tag names are limited to 12 characters, because the assignment name
(`Inherit-Tag-<tag>`) may be at most 24.

## Tested (30 October 2026)

- Not yet run in Azure: the rule syntax of `Deny-RG-Without-Tag` (a `count` over the exempt patterns), which Azure validates when the definition is created. The plan accepts it.

- `render_library.py`: 9 unit tests (`python3 -m unittest discover -s governance/scripts`).
- `terraform validate`, and a read-only `terraform plan` in the Fortytwo Labs sandbox under the PoC's
  `ft42poc` management group, as root `ft42lib`: 530 to add, nothing applied. The plan has 12 management
  groups and 126 policy assignments; landing zones 59 (53 from ALZ, minus 2, plus 2 region and 6 tag
  assignments), platform 39, connectivity 0, Corp 5.

## Before the first apply

1. **Management in this root.** `az-tf-mlz-management` creates the Log Analytics workspace, the monitoring
   agent's identity and the data collection rules. Their IDs must feed the ALZ policy defaults
   (`log_analytics_workspace_id`, `ama_user_assigned_managed_identity_id`,
   `ama_user_assigned_managed_identity_name`, `ama_vm_insights_data_collection_rule_id`,
   `ama_change_tracking_data_collection_rule_id`, `ama_mdfc_sql_data_collection_rule_id`), with
   `resource_group_location`, `resource_group_name_service_health_alerts`, `resource_group_name_mdfc` and
   `email_security_contact`. Until then those assignments point at the library's placeholders, so the
   root must not be applied.
2. **Role assignments for the security benchmark monitoring.** The plan warns that
   `Deploy-MCSB2-Monitoring` needs role assignments the module cannot generate (its user-assigned identity
   parameter). They belong with step 1.
3. **Private DNS zone defaults** for `Deploy-Private-DNS-Zones` in Corp
   (`private_dns_zone_subscription_id`, `_resource_group_name`, `_region`), from the platform contract.
4. **Subscription placement** of the platform subscriptions (management, connectivity, identity, security)
   from `customer.yaml`.

## Later

- Default management group for new subscriptions (`-unusedsub`), through the module's
  `management_group_hierarchy_settings`. Needs tenant-level rights.
- DDoS as an opt-in: a customer drops `Enable-DDoS-VNET` from `policy_assignments_to_remove` and supplies
  `ddos_protection_plan_id`.
- Migration from the old product: its tag assignment `Enforce-Mandatory-Tags` and policy set
  `Enforce-RSG-Tags` are replaced by the per-tag assignments, so a migrating customer's plan removes the
  old two.
