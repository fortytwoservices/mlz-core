# Who owns each module input

<!-- Written by python3 -m mlz reference; do not edit. -->

One page per Terraform root in mlz-core: each module call the root makes, and for every input of the module, who owns it. Made from the input map beside each root (`inputs.yaml`), so it cannot fall behind a root or a pin:
`tools/mlz/inputs.py` says how.

| Root | Stacks | Module calls | Entries: setting / derived / fixed / open / closed |
|---|---|---|---|
| [bootstrap/terraform](bootstrap.md) | `bootstrap` | `runners`: az-tf-mlz-github-runners v0.1.0 | 6 / 6 / 5 / 7 / 0 |
| [governance/terraform](governance.md) | `governance` | `alz`: Azure/avm-ptn-alz/azurerm 0.22.0<br>`management`: az-tf-mlz-management v0.2.0 | 5 / 9 / 13 / 24 / 2 |
| [platform/terraform](platform.md) | `connectivity` | `connectivity`: az-tf-mlz-connectivity-hub v0.1.0 | 1 / 25 / 10 / 25 / 9 |
| [platform/networks/terraform](platform-networks.md) | `management`, `identity` | `network`: modules/platform-network (mlz-core)<br>`network.peering`: Azure/avm-res-network-virtualnetwork/azurerm//modules/peering 0.22.3<br>`network.route_table`: az-tf-mlz-route-table v0.1.1<br>`network.virtual_network`: Azure/avm-res-network-virtualnetwork/azurerm 0.22.3 | 0 / 27 / 36 / 27 / 6 |
| [platform/rules/terraform](platform-rules.md) | `firewall-rules` | `rule_collection_group`: az-tf-mlz-azure-firewall//modules/rule_collection_group v0.1.4 | 9 / 7 / 4 / 2 / 9 |
| [vending/terraform](vending.md) | `<workload>-<env>` | `route_table`: az-tf-mlz-route-table v0.1.1<br>`vending`: Azure/avm-ptn-alz-sub-vending/azure 0.3.4 | 13 / 28 / 34 / 20 / 14 |
