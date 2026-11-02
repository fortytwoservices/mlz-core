# Governance

The `mlz-governance` role: management groups, Azure Policy and central management. It is built
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
| Management group IDs | Kept from the old product: `<root>`, `-platform`, `-management`, `-connectivity`, `-identity`, `-landing-zones`, `-lz-corp`, `-lz-online`, `-sandboxes`, `-decommissioned`. The old `-unusedsub` is dropped: unused subscriptions go to Decommissioned |
| Optional groups | Off unless `customer.yaml` turns them on: `management_groups.security` (`-security` under Platform, for security tooling such as Sentinel) and `management_groups.local` (`-lz-local` under Landing Zones, for workloads on Azure Local clusters) |
| Sandboxes | The `-sandboxes` group is always there; sandbox subscriptions come from vending (`archetype: sandbox`) and get the region restriction. The tag rules apply there only with `tags.in_sandboxes: true`: whether sandbox users must tag what they create is the customer's cost-allocation choice (default off; vending still tags the sandbox subscription) |
| Data platform | Dropped for now: no `-lz-dataplatform` group, no Databricks policies |
| Allowed regions | norwayeast, norwaywest, westeurope, northeurope, swedencentral, for resources and resource groups in the landing zones |
| Tags | `Owner` and `CostCenter` required on landing zone resource groups (deny); `Owner`, `CostCenter`, `Environment`, `Workload` inherited from the resource group (modify). No `Location` tag. The deny uses Fortytwo's `Deny-RG-Without-Tag` (governance/lib), the built-in rule plus `tags.exempt_resource_groups`: groups that Azure services and policy deployments create without the customer's tags (NetworkWatcherRG, the Service Health alerts group, Defender export, AKS node groups, ...) |
| DDoS | Off: `Enable-DDoS-VNET` removed from connectivity and landing zones |
| Subnets without an NSG | Denied in landing zones (ALZ default kept) |
| Guest attestation | Not used: `Deploy-GuestAttest` removed from platform and landing zones |
| Log Analytics by policy | Not used (no longer in the ALZ library) |
| Corp | ALZ default, including public IPs denied on network cards |
| New ALZ assignments | Service health alerts and security benchmark monitoring kept (both at the root) |

Tag names are limited to 12 characters, because the assignment name (`Inherit-Tag-<tag>`) may be at
most 24.

## Central management and the platform subscriptions

- `az-tf-mlz-management` runs in this root: the Log Analytics workspace, the Azure Monitor Agent's
  identity and the three data collection rules, in `rg-<root>-management-<region>` in the management
  subscription. Names come from `modules/naming`, shared by every root.
- The ALZ policies get their IDs as defaults (`log_analytics_workspace_id`, the `ama_*` values,
  `resource_group_location`, `email_security_contact`). The IDs are built from the names, so they are known
  at plan time, and `policy_assignments_dependencies` assigns the policies after the resources exist.
- `security_contact` is Defender for Cloud's security contact (ALZ's `email_security_contact`). Service
  health alerts are separate: ALZ's `Deploy-SvcHealth-BuiltIn` sends them to each subscription's Owners
  (its default `actionGroupRoleIds`); a dedicated recipient is a later choice.
- `customer.yaml` names the platform subscriptions (`subscriptions.management`, `.connectivity`, and
  optionally `.identity` and `.security`); each one is placed in its management group.
- **Private Link DNS:** the policy's defaults come from the platform's published contract
  (`platform_contract_file`), not from `customer.yaml`. Until the platform has created the zones, the
  library's placeholders stay, and the ALZ module grants no rights on zones; the run after the platform
  publishes its contract grants them. Without this, the first apply fails on 53 role assignments on zones
  that do not exist yet (met in the canary).
- `contract` output: what platform and vending read, written as `_generated/governance.yaml` in their repos by `python3 -m mlz contract governance`.

## Tested

- 30 October 2026: 22 renderer unit tests; a read-only plan in the Fortytwo Labs sandbox under `ft42poc`;
  `Deny-RG-Without-Tag` created and deleted in the sandbox subscription, which is when Azure validates a
  policy rule.
- 2 November 2026, the canary (TCAF tenant, root `mlz`): applied end to end, with management, the
  platform subscriptions placed, and Private Link DNS left for the platform contract. A fresh plan then
  shows no changes. 24 renderer unit tests.

## Known gap

- **Security benchmark remediation.** The ALZ library cannot read one parameter inside
  `Deploy-MCSB2-Monitoring` (a user-assigned identity given as an ARM function), so the ALZ module cannot
  create the role assignments its deploy-if-not-exists parts need (alzlib warning, "External role
  assignment creation required"). Its audits work; its remediation of the Defender for SQL agent identity
  waits for those role assignments, to be added with the Defender plans.

## Later

- Default management group for new subscriptions, through the module's
  `management_group_hierarchy_settings` (ALZ suggests Sandboxes). Needs tenant-level rights.
- DDoS as an opt-in: a customer drops `Enable-DDoS-VNET` from `policy_assignments_to_remove` and supplies
  `ddos_protection_plan_id`.
- Migration from the old product: its tag assignment `Enforce-Mandatory-Tags` and policy set
  `Enforce-RSG-Tags` are replaced by the per-tag assignments, so a migrating customer's plan removes the
  old two. Its `-unusedsub` group is removed too: move any subscriptions in it to `-decommissioned` first,
  since Azure does not delete a management group that holds subscriptions.

## Operating notes

- **Renaming the monitoring agent's identity is blocked by design.** ALZ's `DenyAction-DeleteUAMIAMA`
  (on the Platform group) refuses deleting the identity it protects, and it is only pointed at the new
  name after the new identity exists, which Terraform creates after deleting the old one. To get past
  it, exempt the old identity alone from that assignment for a few hours, then apply again:
  `az policy exemption create --name tmp-rename-ama-identity --scope <old identity ID>
  --policy-assignment <the assignment's ID> --exemption-category Waiver --expires-on <in 3 hours>`.
  Met in the canary on 2 November 2026, when the region codes in names changed to Microsoft's.
