# Platform

The `mlz-platform` role: the hub network in each region, its firewall, DNS resolver and Bastion, and the
Private Link DNS zones, with the address space of management's and identity's networks held beside each hub. Built on `az-tf-mlz-connectivity-hub` (hub-and-spoke; Virtual WAN is the
alternative, through `az-tf-mlz-connectivity-vwan`).

## What the customer writes

One file per region, `hubs/<region>.yaml`, saying what the region's platform should have:

```yaml
# The platform's networks in Norway East. Landing zones peer to its hub, and their traffic goes through
# its firewall.

platform_address_space: 10.100.0.0/21    # the hub, management and identity; laid out by mlz-core
landing_zone_ranges: 10.100.0.0/16       # where landing zone networks get their address ranges
reserved: [10.100.200.0/22]              # networks mlz-core does not manage: never given to a landing zone

firewall: Standard                       # Standard, Premium or Basic
dns_resolver: true                       # resolves Private Link names for landing zones and on-premises
bastion: false                           # Bastion for VMs in any landing zone
```

A hub can also be a folder of chunks, `hubs/<region>/*.yaml`, read as one file; a value set in two
chunks is an error that names both files.

Defaults: `firewall: Standard`, `dns_resolver: true`, `bastion: false`, no `reserved` ranges. The primary
region (from governance) must have a hub.

The platform starter comes with `hubs/norwayeast.yaml` and `firewall/platform.yaml`, every setting in them
commented out. Until the hub file has its platform address space and landing zone ranges, the platform's check
stops, so nothing is built from the starter as it comes; rename the file if the primary region is another.
If governance's contract pull request has already failed its run for that reason, bring its branch up to
date once the hub file is merged (`gh pr update-branch <number> -R <the platform repo>`): a re-run would check
the same commit again, while the run of that merge checks the pull request with the hub, and merges it.

`reserved` lists ranges the IP allocator must never hand out: existing virtual networks, on-premises
sites, partner links, anything inside `landing_zone_ranges` that mlz-core does not manage. A region's
platform address space may not overlap any hub's reserved ranges. Reserving a range a landing zone already holds
is reported as a conflict on the next run, never solved by moving the landing zone.

Before v0.3.0, a hub file gave the hub's own range as `address_space`. The check now refuses it and says
what to write instead: for a hub at 10.100.0.0/22, `platform_address_space: 10.100.0.0/21` keeps the hub
where it is. A landing zone already given an address in the new /21 is reported as a conflict on vending's
next run, never moved.

## What is worked out

- **The platform's range**, `platform_address_space`, a /21 laid out by fixed offsets: the hub's /22
  first, management's /24 at the fifth /24, identity's /24 at the sixth, and a spare /23 last. Every region
  looks the same, and a network turned on later never moves another. The IP allocator gives no landing
  zone an address in it, and the other regions route all of it to this region's firewall. Management's
  and identity's networks are held from the start; mlz-core builds them in a later release.
- **The hub's subnets**, by one fixed layout inside its /22: firewall /26 first, firewall management /26
  second (Basic only), gateway /27, Bastion /26 at the start of the second /24, then the DNS resolver's
  inbound and outbound /28s. Every hub looks the same.
- **Names**, `<type>-<root>-hub-<region code>` (for example `afw-mlz-hub-nwe`), and the Private Link zones'
  group `rg-<root>-dns-<region code>`, the one governance's DNS policy points at.

`python3 -m mlz platform` reads `hubs/` and `_generated/governance.yaml`, checks both, and writes the
root's config (JSON, for one run, never committed); the Terraform root only reads that file.

## Firewall rules

Every landing zone's outbound traffic goes through its hub's firewall, which denies anything no rule
allows. The rules live in the platform repo, next to the firewalls, so a network person reads and
reviews them in one place:

```yaml
# firewall/landing-zones/payments-prod.yaml: what payments-prod may reach, from its own range.
outbound:
  - name: card-processor
    description: Card payments.
    to: [api.stripe.com]
    ports: [https]
```

`firewall/platform.yaml` holds rules for every landing zone of a hub, from all its landing zone ranges. A
rule names only where traffic goes; the source is worked out from the vending contract
(`_generated/vending.yaml`). A file in `firewall/landing-zones/` for a key that contract does not name
stops the check, since it is usually a typo. Before vending has published anything, on the platform's
first deployment, those files wait instead, with a notice, so the hub can be built first. `to` takes:

- names in lower case (`api.example.com`) and wildcards (`*.example.com`)
- addresses and ranges, and other landing zones by key (`website-prod`)
- FQDN tags such as `WindowsUpdate`
- service tags such as `AzureMonitor` or `Storage.NorwayEast`

`ports` takes `https`, `http`, TCP ports (`8443`) and `udp:<port>`. A name reaches `https` and `http`
through an application rule, which checks the name the client asks for, and the ports written as numbers
(`443` included) through a network rule. Addresses, landing zones and service tags take every port through
a network rule. A rule makes network rules named after it, `<name>-names` and `<name>-udp` too, so another
rule may not take one of those names. Network rules match names by DNS resolution through the firewall's
DNS proxy, so a name on a port written as a number cannot be a wildcard (`*.example.com` takes only `https`
and `http`), and a Basic firewall, which has no DNS proxy, reaches names on `https` and `http` only.
Regional service tags are written as Azure names them: `Storage.NorwayEast`, but `AzureCloud.norwaye`.

`python3 -m mlz firewall` turns the files into a fixed set of rule collection groups per hub. They are
applied by their own root, `platform/rules/terraform`, the `firewall` stack, so a rule change never plans
the hub. The pipeline refuses a hub plan that deletes or replaces the firewall policy, since the rules live
inside it ([pipelines](pipelines.md)). The stack runs even without a `firewall/` folder: the `platform` group always holds what mlz-core
requires, and deleting a rules file takes its rules out of Azure.

| Group | Priority | Holds |
|---|---|---|
| `platform` | 500 | `required-network` (100): what governance's own policies need, whatever the customer writes. Today, the Azure Monitor Agent, which reaches the `AzureMonitor` and `AzureResourceManager` service tags on 443. Then `firewall/platform.yaml` (200 and 205). |
| `lz-00` to `lz-29` | 1000 to 3900 | Each landing zone's rules, in the group its key hashes to, for ever. Adding a landing zone never moves another's rules, and the 31 groups in all stay under Azure's limit per policy, however many landing zones there are. |

## Contract

The `contract` output, written as `_generated/platform.yaml` in the governance and vending repos by
`python3 -m mlz contract platform`: the hub's
connectivity (peering target, firewall address, DNS servers, the spoke routes), the hubs' own ranges
and the reserved ones (which the IP allocator never hands out), and where the Private Link zones are.
