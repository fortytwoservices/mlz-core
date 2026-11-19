# platform/terraform: who owns each input

<!-- Written by python3 -m mlz reference from platform/terraform/inputs.yaml and tools/mlz/module-variables.json; do not edit. -->

The Terraform root of the stack `connectivity`: every input of every module call it makes, and who owns it. The map is `platform/terraform/inputs.yaml`; `tools/mlz/inputs.py` says how it is checked.

Each input is in one class:

| Class | Meaning | Can a customer set it? |
|---|---|---|
| setting | from a key in the customer's YAML | yes, with that key |
| derived | worked out by mlz-core: from the layout, the IP allocator, a contract or the naming | no; a name, under `names:` where the entry says so |
| fixed | Fortytwo's decision for every customer | no |
| open | mlz-core does not set it, so the module's default applies | not yet |
| closed | not supported yet | no |

An entry covers its input and everything in it: `bastion` closed means every attribute of `bastion`. A map or list of
objects passes through to the objects' attributes (`hub_virtual_networks.firewall.sku_tier` is each hub's).

## module.connectivity: az-tf-mlz-connectivity-hub v0.1.0

Each region's hub network with its firewall and policy, DNS resolver and Bastion, and the Private Link DNS zones.

Settings from `connectivity/<region>.yaml`.

70 entries: 1 setting, 25 derived, 10 fixed, 25 open, 9 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `parent_id` | string | derived | the connectivity subscription: customer.yaml's subscriptions.connectivity, through the governance contract |
| `hub_virtual_networks` | map(object) | | one hub per connectivity/&lt;region&gt;.yaml, keyed by the region |
| `hub_virtual_networks.location` | string | derived | the region: the file's name |
| `hub_virtual_networks.address_space` | list(string) | derived | the first /22 of platform_address_space |
| `hub_virtual_networks.is_primary` | bool | derived | customer.yaml's primary_region, through the governance contract |
| `hub_virtual_networks.name` | string | derived | the naming template; set it with `names.virtual_network` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.resource_group_name` | string | derived | the naming template; set it with `names.resource_group` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.routing_supernet` | list(string) | derived | what the other hubs send to this hub's firewall: platform_address_space (unless a landing zone range holds it) and landing_zone_ranges |
| `hub_virtual_networks.spoke_address_prefixes` | map(string) | closed | only for a gateway's route table, and the hubs have no gateway yet |
| `hub_virtual_networks.virtual_network_dns_servers` | list(string) | open | the module's default: `null` |
| `hub_virtual_networks.gateway_subnet_address_prefix` | string | closed | the hubs have no VPN or ExpressRoute gateway yet; the layout keeps the hub's third /27 for GatewaySubnet |
| `hub_virtual_networks.gateway_route_table_name` | string | closed | the hubs have no gateway yet |
| `hub_virtual_networks.firewall` | object | | every hub has one |
| `hub_virtual_networks.firewall.name` | string | derived | the naming template; set it with `names.firewall` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.firewall.sku_tier` | string | setting | `firewall` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.firewall.zones` | list(string) | open | the module's default: `null` |
| `hub_virtual_networks.firewall.subnet_address_prefix` | string | derived | the first /26 of the hub's /22 |
| `hub_virtual_networks.firewall.management_subnet_address_prefix` | string | derived | the second /26 of the hub's /22, for a Basic firewall only |
| `hub_virtual_networks.firewall.public_ip_name` | string | derived | the naming template; set it with `names.firewall_public_ip` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.firewall.management_public_ip_name` | string | derived | the naming template, for a Basic firewall only; set it with `names.firewall_management_public_ip` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.firewall.route_table_name` | string | derived | the naming template; set it with `names.firewall_route_table` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.firewall.default_route` | object | closed | anything but Internet is forced tunnelling, which needs the management subnet, and mlz-core gives one to a Basic firewall only |
| `hub_virtual_networks.firewall.firewall_policy_id` | string | closed | the firewall-rules stack writes its rules into the policy the hub makes, found by its name |
| `hub_virtual_networks.firewall.firewall_policy.name` | string | derived | the naming template; set it with `names.firewall_policy` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.firewall.firewall_policy.base_policy_id` | string | open | the module's default: `null` |
| `hub_virtual_networks.firewall.firewall_policy.threat_intelligence_mode` | string | open | the module's default: `"Alert"` |
| `hub_virtual_networks.firewall.firewall_policy.dns_proxy_enabled` | bool | fixed | the module's default (`null`): on except on Basic: management's, identity's and the landing zones' networks resolve through the firewall (tools/mlz/platform.py, spoke()) |
| `hub_virtual_networks.firewall.firewall_policy.dns_servers` | list(string) | open | the module's default: `null` |
| `hub_virtual_networks.nat_gateway` | object | closed | mlz-core gives it no name yet (tools/mlz/names.py, HUB), and this root passes no naming template |
| `hub_virtual_networks.bastion` | object | | built when the hub file says bastion true |
| `hub_virtual_networks.bastion.name` | string | derived | the naming template; set it with `names.bastion` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.bastion.subnet_address_prefix` | string | derived | the first /26 of the hub's second /24 |
| `hub_virtual_networks.bastion.sku` | string | open | the module's default: `"Standard"` |
| `hub_virtual_networks.bastion.zones` | list(string) | open | the module's default: `null` |
| `hub_virtual_networks.bastion.public_ip_name` | string | derived | the naming template; set it with `names.bastion_public_ip` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.bastion.scale_units` | number | open | the module's default: `2` |
| `hub_virtual_networks.bastion.copy_paste_enabled` | bool | open | the module's default: `true` |
| `hub_virtual_networks.bastion.file_copy_enabled` | bool | open | the module's default: `false` |
| `hub_virtual_networks.bastion.ip_connect_enabled` | bool | open | the module's default: `false` |
| `hub_virtual_networks.bastion.kerberos_enabled` | bool | open | the module's default: `false` |
| `hub_virtual_networks.bastion.shareable_link_enabled` | bool | open | the module's default: `false` |
| `hub_virtual_networks.bastion.tunneling_enabled` | bool | open | the module's default: `false` |
| `hub_virtual_networks.dns_resolver` | object | | built unless the hub file says dns_resolver false |
| `hub_virtual_networks.dns_resolver.name` | string | derived | the naming template; set it with `names.dns_resolver` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.dns_resolver.inbound_subnet_address_prefix` | string | derived | the /28 after Bastion's subnet |
| `hub_virtual_networks.dns_resolver.inbound_subnet_name` | string | fixed | the module's default (`"snet-dns-resolver-inbound"`): a subnet takes its name inside its network, not from the naming template (docs/naming.md) |
| `hub_virtual_networks.dns_resolver.inbound_endpoint_name` | string | derived | the naming template; set it with `names.dns_resolver_inbound_endpoint` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.dns_resolver.outbound_subnet_address_prefix` | string | derived | the /28 after the inbound one |
| `hub_virtual_networks.dns_resolver.outbound_subnet_name` | string | fixed | the module's default (`"snet-dns-resolver-outbound"`): a subnet takes its name inside its network, not from the naming template (docs/naming.md) |
| `hub_virtual_networks.dns_resolver.outbound_endpoint_name` | string | derived | the naming template; set it with `names.dns_resolver_outbound_endpoint` in `connectivity/<region>.yaml` |
| `hub_virtual_networks.vpn_gateway` | object | closed | no VPN gateway yet: every peering is made without remote gateways, and mlz-core has no name for one |
| `hub_virtual_networks.express_route_gateway` | object | closed | no ExpressRoute gateway yet: every peering is made without remote gateways, and mlz-core has no name for one |
| `mesh_peering_enabled` | bool | fixed | the module's default (`true`): the hubs peer with each other, so each sends the others' ranges to their firewalls |
| `connection_secrets` | map(string) | closed | only for gateway connections, and the hubs have no gateway yet |
| `ddos_protection_plan_id` | string | open | the module's default: `null` |
| `private_dns_zones.resource_group_name` | string | derived | the naming template: the DNS zones' group, which governance's DNS policy points at |
| `private_dns_zones.zone_name_filter` | string | open | the module's default: `null` |
| `private_dns_zones.excluded_zones` | list(string) | open | the module's default: `[]` |
| `private_dns_zones.additional_zones` | map(string) | open | the module's default: `{}` |
| `naming` | object | fixed | the module's default (`null`): mlz-core names every component itself (tools/mlz/names.py): one left without a name fails the plan rather than take another scheme's |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retry` | object | open | the module's default: `{}` |
| `timeouts` | object | open | the module's default: `{}` |
| `diagnostic_settings.name` | string | derived | to-&lt;org&gt;-workspace, from customer.yaml's naming |
| `diagnostic_settings.logs` | set(object) | fixed | `[{"category_group": "allLogs"}]`: every log goes to the central workspace |
| `diagnostic_settings.metrics` | set(object) | open | the module's default: `[]` |
| `diagnostic_settings.log_analytics_destination_type` | string | fixed | the module's default (`"Dedicated"`): Azure's resource-specific tables: a table per kind of log (rule hits, threat intelligence, DNS proxy) |
| `diagnostic_settings.workspace_resource_id` | string | derived | management's workspace, from the governance contract |
| `diagnostic_settings.storage_account_resource_id` | string | open | the module's default: `null` |
| `diagnostic_settings.event_hub_authorization_rule_resource_id` | string | open | the module's default: `null` |
| `diagnostic_settings.event_hub_name` | string | open | the module's default: `null` |
| `diagnostic_settings.marketplace_partner_resource_id` | string | open | the module's default: `null` |
| `tags` | map(string) | fixed | `{"ManagedBy": "mlz-core"}`: what mlz-core built, at a glance |
