# Platform

The `mlz-platform` role: the hub network in each region, its firewall, DNS resolver and Bastion, the
Private Link DNS zones, and management's and identity's networks beside the hubs. Built on `az-tf-mlz-connectivity-hub` (hub-and-spoke; Virtual WAN is the
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
identity: false                          # identity's network here, for domain controllers
```

A hub can also be a folder of chunks, `hubs/<region>/*.yaml`, read as one file; a value set in two
chunks is an error that names both files.

Defaults: `firewall: Standard`, `dns_resolver: true`, `bastion: false`, `identity: false`, no `reserved` ranges. The primary
region (from governance) must have a hub.

The platform starter comes with `hubs/norwayeast.yaml`, `firewall/platform.yaml` and `firewall/management.yaml`, every setting in them
commented out. Until the hub file has its platform address space and landing zone ranges, the platform's check
stops, so nothing is built from the starter as it comes; rename the file if the primary region is another.
If governance's contract pull request has already failed its run for that reason, bring its branch up to
date once the hub file is merged (`gh pr update-branch <number> -R <the platform repo>`): a re-run would check
the same commit again, while the run of that merge checks the pull request with the hub, and merges it.

`reserved` lists ranges the IP allocator must never hand out: existing virtual networks, on-premises
sites, partner links, anything inside `landing_zone_ranges` that mlz-core does not manage. A region's
platform address space may not overlap any hub's reserved ranges, nor another region's landing zone ranges
(its own may hold it). Reserving a range a landing zone already holds
is reported as a conflict on the next run, never solved by moving the landing zone.

Before v0.3.0, a hub file gave the hub's own range as `address_space`. The check now refuses it and says
what to write instead: for a hub at 10.100.0.0/22, `platform_address_space: 10.100.0.0/21` keeps the hub
where it is. A landing zone already given an address in the new /21 is reported as a conflict on vending's
next run, never moved, and the platform's check, which reads `_generated/vending.yaml`, refuses to build
management's or identity's network over it, or over one removed but not yet released (the contract's
`held`), whose network may still exist. Upgrade vending first: it reads the platform's contract from either release, while
vending on v0.2 fails its check on the new one.

## What is worked out

- **The platform's range**, `platform_address_space`, a /21 laid out by fixed offsets: the hub's /22
  first, management's /24 at the fifth /24, identity's /24 at the sixth, and a spare /23 last. Every region
  looks the same, and a network turned on later never moves another. The IP allocator gives no landing
  zone an address in it, and the other regions route all of it to this region's firewall. Management's
  and identity's networks keep their /24s whether they are built or not.
- **The hub's subnets**, by one fixed layout inside its /22: firewall /26 first, firewall management /26
  second (Basic only), gateway /27, Bastion /26 at the start of the second /24, then the DNS resolver's
  inbound and outbound /28s. Every hub looks the same.
- **Names**, `<type>-<root>-hub-<region code>` (for example `afw-mlz-hub-nwe`), and the Private Link zones'
  group `rg-<root>-dns-<region code>`, the one governance's DNS policy points at.

`python3 -m mlz platform` reads `hubs/` and `_generated/governance.yaml`, checks both, and writes the
root's config (JSON, for one run, never committed); the Terraform roots only read that file.

## Management and identity networks

The platform builds two more networks, each a spoke of its region's hub as a landing zone is: two
peerings, made from both sides by the platform, which owns both ends; a route table sending everything
(`0.0.0.0/0`) to the hub's firewall; the hub's firewall as DNS server (its resolver behind a Basic
firewall); and a network security group on every subnet.

| Network | Where | Subnets |
|---|---|---|
| Management, `vnet-<root>-management-<region code>` | the management subscription, in the primary region | `snet-github-runners` /25, for GitHub-hosted runners ([below](#github-hosted-runners)); `snet-runners` /26, for self-hosted runners; `snet-private-endpoints` /27, the state account's first; a /27 free |
| Identity, `vnet-<root>-identity-<region code>` | the identity subscription, in each region whose hub file says `identity: true` | `snet-domain-controllers` /26: the domain controllers will have its .4 and .5 |

Each is a stack of its own in the platform pipeline, `management` and `identity`, with its own state, so
a change to one never plans the hub or the other. They apply after the hub, which they peer with. Both run
every time, so turning `identity` off takes identity's network out of Azure. Identity needs an identity
subscription in `customer.yaml`.

They are built in a resource group the bootstrap makes in each subscription,
`rg-<root>-<management|identity>-network-<primary region code>`, which holds the network in every region.
The platform's pipeline identities have rights on that group only, not on the rest of the subscription:
governance's monitoring and the state account are in management too. Their outbound traffic is allowed by
the firewall's rules like a landing zone's ([below](#firewall-rules)): `firewall/management.yaml` and
`firewall/identity.yaml`. The domain controllers come in a later release.

### GitHub-hosted runners

Where GitHub runs its hosted runners in the customer's own networks (of the regions mlz-core supports,
Norway East and Sweden Central; [GitHub's list](https://docs.github.com/en/organizations/managing-organization-settings/about-azure-private-networking-for-github-hosted-runners-in-your-organization#about-supported-regions)),
the management stack delegates `snet-github-runners` to GitHub and creates the `GitHub.Network/networkSettings`
resource that joins it to the customer's GitHub organization, `ghns-<root>-management-<region code>`. The
pipeline names the organization by its ID (`GITHUB_REPOSITORY_OWNER_ID`); a plan by hand takes
`-var github_business_id=<id>` (`gh api orgs/<organization> --jq .id`). The stack's `networks` output
holds the resource's ID at GitHub, which the organization's network configuration names. The runners take
no connections, so the subnet's network security group refuses every one opened to them; their own
connections out still get their replies. Elsewhere the subnet stays undelegated, for self-hosted runners.

The state account's private endpoint, through which the runners reach the state, is the bootstrap's
([bootstrap](bootstrap.md#running-it-again)). Azure refuses to delete the network settings resource while
the organization's network configuration still uses it: to take management's network down, delete that
configuration in GitHub first.

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

`firewall/platform.yaml` holds rules for every landing zone of a hub, from all its landing zone ranges; a
platform address space inside those ranges gets them too. `firewall/management.yaml` holds management's,
from its network's range, and `firewall/identity.yaml` identity's, from its range in every region that
builds it (a rule there with no such region stops the check). A rule names only where traffic goes; the
source is worked out from the vending contract (`_generated/vending.yaml`) and the platform's own layout.
A file in `firewall/landing-zones/` for a key that contract does not name stops the check, since it is
usually a typo. Before vending has published anything, on the platform's
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
| `platform` | 500 | `required-network` (100): what governance's own policies need, whatever the customer writes. Today, the Azure Monitor Agent, which reaches the `AzureMonitor` and `AzureResourceManager` service tags on 443, from the landing zones and the platform's own networks. `runners-network` and `runners-application` (110 and 115): what the three pipelines need from management's two runner subnets ([below](#what-the-runners-reach)). Then `firewall/platform.yaml` (200 and 205), `firewall/management.yaml` (300 and 305) and `firewall/identity.yaml` (400 and 405). |
| `lz-00` to `lz-29` | 1000 to 3900 | Each landing zone's rules, in the group its key hashes to, for ever. Adding a landing zone never moves another's rules, and the 31 groups in all stay under Azure's limit per policy, however many landing zones there are. |

### What the runners reach

From `snet-github-runners` and `snet-runners`, in the primary hub's `platform` group, on 443:

- **GitHub**: the names GitHub's runners need, as GitHub's meta endpoint lists them
  (`api.github.com/meta`, `domains.actions`): its own wildcards, a few fixed names and its result storage
  accounts, named one by one. A name one label below a wildcard in the same list is left out.
- **Entra ID and Azure Resource Manager**: the `AzureActiveDirectory` and `AzureResourceManager` service tags.
- **Terraform**: `registry.terraform.io` and `releases.hashicorp.com`, for Terraform, its providers and modules.

The platform's check reads GitHub's list on every run, and stops if it cannot: the rule is never planned
without it. GitHub lists a name at least a week before using it. When the list changes, the next plan
changes the `platform` group, and the daily drift check fails until it is applied: run the workflow on
`main` and approve the apply, as for any other change. Anything else the runners reach, such as systems in
landing zones or on-premises, goes in `firewall/management.yaml`.

## Contract

The `contract` output, written as `_generated/platform.yaml` in the governance and vending repos by
`python3 -m mlz contract platform`: the hub's
connectivity (peering target, firewall address, DNS servers, the spoke routes), the hubs' own ranges
and the reserved ones (which the IP allocator never hands out), and where the Private Link zones are.
