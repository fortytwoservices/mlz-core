# platform/rules/terraform: who owns each input

<!-- Written by python3 -m mlz reference from platform/rules/terraform/inputs.yaml and tools/mlz/module-variables.json; do not edit. -->

The Terraform root of the stack `firewall-rules`: every input of every module call it makes, and who owns it. The map is `platform/rules/terraform/inputs.yaml`; `tools/mlz/inputs.py` says how it is checked.

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

## module.rule_collection_group: az-tf-mlz-azure-firewall//modules/rule_collection_group v0.1.4

The rule collection groups in each hub's firewall policy, platform for the shared rules and lz-&lt;nn&gt; for the landing zones' (tools/mlz/firewall.py).

Settings from a rules file in `firewall-rules/`.

31 entries: 9 setting, 7 derived, 4 fixed, 2 open, 9 closed.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `name` | string | derived | platform, or lz-&lt;nn&gt; with nn from the landing zone's key, so a landing zone's rules never move |
| `parent_id` | string | derived | the region's hub firewall policy, by its name |
| `priority` | number | derived | 500 for platform, 1000 + 100 × nn for lz-&lt;nn&gt; |
| `application_rule_collections` | map(object) | | &lt;owner&gt;-application, the rules to names and FQDN tags on https and http. The settings below are a rules file's; mlz-core also adds collections of its own to each hub's platform group, which no rules file sets: required-application (the Azure Monitor Agent that governance's policies install) and, in the primary hub, runners-application (what the pipelines reach from management's runner subnets, GitHub Actions' names from GitHub's meta endpoint), from tools/mlz/firewall.py's REQUIRED and RUNNERS |
| `application_rule_collections.priority` | number | derived | its network collection's, plus 5 |
| `application_rule_collections.action` | string | fixed | `"Allow"`: a rule allows; whatever no rule allows is denied |
| `application_rule_collections.rules` | map(object) | | one per rule, by its name |
| `application_rule_collections.rules.description` | string | setting | `outbound.description` in a rules file in `firewall-rules/` |
| `application_rule_collections.rules.source_addresses` | list(string) | derived | the landing zone's range (vending contract), every landing zone range of the hub for platform.yaml, or the network's own range |
| `application_rule_collections.rules.source_ip_groups` | list(string) | closed | a rule's source is worked out, never written |
| `application_rule_collections.rules.protocols` | list(object) | setting | `outbound.ports` in a rules file in `firewall-rules/`: https and http |
| `application_rule_collections.rules.destination_fqdns` | list(string) | setting | `outbound.to` in a rules file in `firewall-rules/`: the names among them |
| `application_rule_collections.rules.destination_fqdn_tags` | list(string) | setting | `outbound.to` in a rules file in `firewall-rules/`: the FQDN tags among them |
| `application_rule_collections.rules.destination_urls` | list(string) | closed | URL filtering needs TLS inspection, which mlz-core does not set up |
| `application_rule_collections.rules.destination_addresses` | list(string) | closed | addresses go into network rules |
| `application_rule_collections.rules.web_categories` | list(string) | closed | a rule names what it reaches; web categories are not offered yet |
| `application_rule_collections.rules.terminate_tls` | bool | closed | TLS inspection is not set up |
| `application_rule_collections.rules.http_headers` | map(string) | closed | TLS inspection is not set up |
| `network_rule_collections` | map(object) | | &lt;owner&gt;-network, the rules to addresses, landing zones and service tags, and to names on other ports. As with application rules, required-network and runners-network are mlz-core's own, which no rules file sets |
| `network_rule_collections.priority` | number | derived | by owner: required 100, runners 110, platform.yaml 200, management.yaml 300, identity.yaml 400; a landing zone's 100 + 10 × its place among its group's landing zones |
| `network_rule_collections.action` | string | fixed | `"Allow"`: a rule allows; whatever no rule allows is denied |
| `network_rule_collections.rules` | map(object) | | one to three per rule, by its name (&lt;name&gt;, &lt;name&gt;-names, &lt;name&gt;-udp) |
| `network_rule_collections.rules.description` | string | setting | `outbound.description` in a rules file in `firewall-rules/` |
| `network_rule_collections.rules.protocols` | list(string) | setting | `outbound.ports` in a rules file in `firewall-rules/`: TCP, or UDP for udp:&lt;port&gt; |
| `network_rule_collections.rules.source_addresses` | list(string) | derived | the landing zone's range (vending contract), every landing zone range of the hub for platform.yaml, or the network's own range |
| `network_rule_collections.rules.source_ip_groups` | list(string) | closed | a rule's source is worked out, never written |
| `network_rule_collections.rules.destination_addresses` | list(string) | setting | `outbound.to` in a rules file in `firewall-rules/`: the addresses, ranges and service tags among them, and the ranges of the landing zones they name |
| `network_rule_collections.rules.destination_ip_groups` | list(string) | closed | IP groups are not offered yet |
| `network_rule_collections.rules.destination_fqdns` | list(string) | setting | `outbound.to` in a rules file in `firewall-rules/`: the names among them, on ports written as numbers |
| `network_rule_collections.rules.destination_ports` | list(string) | setting | `outbound.ports` in a rules file in `firewall-rules/` |
| `nat_rule_collections` | map(object) | closed | outbound rules only: nothing comes in through the firewall yet |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retry` | object | open | the module's default: `{}` |
| `timeouts` | object | open | the module's default: `null` |
