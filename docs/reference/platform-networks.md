# platform/networks/terraform: who owns each input

<!-- Written by python3 -m mlz reference from platform/networks/terraform/inputs.yaml and tools/mlz/module-variables.json; do not edit. -->

The Terraform root of the stacks `management`, `identity`: every input of every module call it makes, and who owns it. The map is `platform/networks/terraform/inputs.yaml`; `tools/mlz/inputs.py` says how it is checked.

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

## module.network: modules/platform-network (mlz-core)

Management's network in the primary region, or identity's in each region with a file in identity/, as a spoke of the region's hub.

10 entries: 9 derived, 1 fixed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `names` | object | derived | a network's names override will go in its own file, management/&lt;region&gt;.yaml or identity/&lt;region&gt;.yaml; not built yet. the naming template (customer.yaml's naming, through the governance contract) |
| `location` | string | derived | the region: the primary region for management, the file's name for identity |
| `parent_id` | string | derived | the network's resource group, which the bootstrap made |
| `address_space` | string | derived | the fifth /24 of the region's platform_address_space for management, the sixth for identity |
| `subnets` | map(string) | derived | a fixed layout in that /24 (tools/mlz/platform.py, NETWORKS) |
| `dns_servers` | list(string) | derived | the hub firewall's address, or the resolver's inbound address behind a Basic firewall |
| `firewall` | string | derived | the fifth address of the hub's firewall subnet |
| `hub_virtual_network_id` | string | derived | the region's hub, from its names |
| `tags` | map(string) | fixed | `{"ManagedBy": "mlz-core"}`: what mlz-core built, at a glance |
| `github_runners` | object | derived | management's network where GitHub's hosted runners can join it (tools/mlz/platform.py, GITHUB_RUNNER_REGIONS), with the organization's ID from the pipeline |

## module.network.module.peering: Azure/avm-res-network-virtualnetwork/azurerm//modules/peering 0.22.3

The network's peering with its hub, both ends, as a landing zone's.

32 entries: 4 derived, 26 fixed, 2 open.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `name` | string | derived | peer-&lt;network&gt;-to-hub |
| `parent_id` | string | derived | the network |
| `remote_virtual_network_id` | string | derived | the region's hub, from its names |
| `allow_forwarded_traffic` | bool | fixed | `true`: traffic the hub's firewall forwards comes in |
| `allow_gateway_transit` | bool | fixed | `false`: the hubs have no gateway yet |
| `allow_virtual_network_access` | bool | fixed | `true`: the network and its hub reach each other |
| `create_reverse_peering` | bool | fixed | `true`: the platform owns both networks, so it makes both ends |
| `do_not_verify_remote_gateways` | bool | fixed | the module's default (`false`): the hubs have no gateway yet |
| `enable_only_ipv6_peering` | bool | fixed | the module's default (`false`): MLZ's networks are IPv4 |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `local_peered_address_spaces` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `local_peered_subnets` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `peer_complete_vnets` | bool | fixed | the module's default (`true`): whole networks peer |
| `remote_peered_address_spaces` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `remote_peered_subnets` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `retry` | object | open | the module's default: `{}` |
| `reverse_allow_forwarded_traffic` | bool | fixed | `true`: traffic the hub's firewall forwards comes in |
| `reverse_allow_gateway_transit` | bool | fixed | `false`: the hubs have no gateway yet |
| `reverse_allow_virtual_network_access` | bool | fixed | `true`: the network and its hub reach each other |
| `reverse_do_not_verify_remote_gateways` | bool | fixed | the module's default (`false`): the hubs have no gateway yet |
| `reverse_enable_only_ipv6_peering` | bool | fixed | the module's default (`false`): MLZ's networks are IPv4 |
| `reverse_local_peered_address_spaces` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `reverse_local_peered_subnets` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `reverse_name` | string | derived | peer-hub-to-&lt;network&gt; |
| `reverse_peer_complete_vnets` | bool | fixed | the module's default (`true`): whole networks peer |
| `reverse_remote_peered_address_spaces` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `reverse_remote_peered_subnets` | list(object) | fixed | the module's default (`[]`): whole networks peer |
| `reverse_use_remote_gateways` | bool | fixed | `false`: the hubs have no gateway yet |
| `sync_remote_address_space_enabled` | bool | fixed | the module's default (`false`): a platform network's range never changes once built |
| `sync_remote_address_space_triggers` | any | fixed | the module's default (`null`): a platform network's range never changes once built |
| `timeouts` | object | open | the module's default: `{}` |
| `use_remote_gateways` | bool | fixed | `false`: the hubs have no gateway yet |

## module.network.module.route_table: az-tf-mlz-route-table v0.1.1

The network's route table, whose one route sends everything to the hub's firewall.

13 entries: 4 derived, 4 fixed, 5 open.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `location` | string | derived | the network's region |
| `name` | string | derived | the naming template |
| `parent_id` | string | derived | the network's resource group |
| `azure_managed_routes_enabled` | bool | open | the module's default: `false` |
| `bgp_route_propagation_enabled` | bool | fixed | `false`: a route learned from a gateway would go round the firewall |
| `routes` | map(object) | derived | one route, default: 0.0.0.0/0 to the hub firewall's address |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retry` | object | open | the module's default: `null` |
| `timeouts` | object | open | the module's default: `null` |
| `lock` | object | open | the module's default: `null` |
| `role_assignments` | map(object) | open | the module's default: `{}` |
| `tags` | map(string) | fixed | `{"ManagedBy": "mlz-core"}`: what mlz-core built, at a glance |

## module.network.module.virtual_network: Azure/avm-res-network-virtualnetwork/azurerm 0.22.3

The network and its subnets, each with its own NSG and the network's route table.

41 entries: 10 derived, 5 fixed, 20 open, 6 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `location` | string | derived | the network's region |
| `parent_id` | string | derived | the network's resource group |
| `address_space` | set(string) | derived | the network's /24 |
| `bgp_community` | string | open | the module's default: `null` |
| `ddos_protection_plan` | object | open | the module's default: `null` |
| `diagnostic_settings` | map(object) | open | the module's default: `{}` |
| `dns_servers` | object | derived | the hub firewall's address, or the resolver's inbound address behind a Basic firewall |
| `enable_telemetry` | bool | fixed | `false`: no module mlz-core calls sends telemetry |
| `enable_vm_protection` | bool | open | the module's default: `false` |
| `encryption` | object | open | the module's default: `null` |
| `extended_location` | object | closed | edge zones are not supported |
| `flow_timeout_in_minutes` | number | open | the module's default: `null` |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `ipam_pools` | list(object) | closed | addresses come from the layout |
| `lock` | object | open | the module's default: `null` |
| `name` | string | derived | the naming template |
| `peerings` | map(object) | closed | the network's peering with its hub is module.peering's |
| `retry` | object | open | the module's default: `{}` |
| `role_assignments` | map(object) | open | the module's default: `{}` |
| `subnets.address_prefix` | string | derived | the subnet's place in the layout |
| `subnets.address_prefixes` | list(string) | closed | one prefix per subnet, from the layout |
| `subnets.name` | string | derived | snet-&lt;subnet&gt; |
| `subnets.ipam_pools` | list(object) | closed | addresses come from the layout |
| `subnets.ignore_body_changes` | list(string) | fixed | the module's default (`[]`): every difference Azure shows is planned, and the daily check reports drift |
| `subnets.nat_gateway` | object | closed | everything leaves through the hub's firewall |
| `subnets.network_security_group` | object | derived | the subnet's own NSG: Azure's default rules, and on the GitHub runners' subnet one more that refuses every connection in |
| `subnets.private_endpoint_network_policies` | string | open | the module's default: `"Enabled"` |
| `subnets.private_endpoint_network_policies_enabled` | bool | open | the module's default: `true` |
| `subnets.private_link_service_network_policies_enabled` | bool | open | the module's default: `true` |
| `subnets.route_table` | object | derived | the network's route table |
| `subnets.service_endpoint_policies` | map(object) | open | the module's default: `null` |
| `subnets.service_endpoints` | set(string) | open | the module's default: `null` |
| `subnets.default_outbound_access_enabled` | bool | fixed | the module's default (`false`): off: everything leaves through the hub's firewall |
| `subnets.sharing_scope` | string | open | the module's default: `null` |
| `subnets.service_endpoints_with_location` | list(object) | open | the module's default: `null` |
| `subnets.delegations` | list(object) | derived | GitHub.Network/networkSettings on github-runners where GitHub's hosted runners can join, else none |
| `subnets.timeouts` | object | open | the module's default: `{}` |
| `subnets.retry` | object | open | the module's default: `{}` |
| `subnets.role_assignments` | map(object) | open | the module's default: `null` |
| `tags` | map(string) | fixed | `{"ManagedBy": "mlz-core"}`: what mlz-core built, at a glance |
| `timeouts` | object | open | the module's default: `{}` |
