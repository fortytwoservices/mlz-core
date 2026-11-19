# Platform

The `mlz-platform` role: the hub network in each region, its firewall, DNS resolver and Bastion, the
Private Link DNS zones, and management's and identity's networks beside the hubs. Built on `az-tf-mlz-connectivity-hub` (hub-and-spoke; Virtual WAN is the
alternative, through `az-tf-mlz-connectivity-vwan`).

## What the customer writes

A folder per stack, each named after it, and in each a file per region (or a folder of chunks,
`<folder>/<region>/*.yaml`, read as one file; a value set in two chunks is an error that names both files).
`./mlz map` prints which stack, Terraform root and modules each file feeds.

| Folder | Stack | What a file says |
|---|---|---|
| `connectivity/<region>.yaml` | `connectivity` | the hub, its firewall, resolver and Bastion, and the region's platform range |
| `management/<region>.yaml` | `management` | nothing yet; optional, and only for the primary region, where management's network is built anyway |
| `identity/<region>.yaml` | `identity` | nothing yet: the file being there builds identity's network in that region, so an empty or comment-only file will do |
| `firewall-rules/` | `firewall-rules` | outbound rules ([below](#firewall-rules)) |

Since a folder is one stack, handing an area to a team on the customer's side is one line in
`.github/CODEOWNERS`, below `*`: `/identity/ @acme/identity-team` ([code owners](pipelines.md#code-owners)). That
team then approves the folder's pull requests; the apply still waits for the platform environment's reviewer.

A connectivity file:

```yaml
# connectivity/norwayeast.yaml: the hub and the platform's range in Norway East. Landing zones peer to its
# hub, and their traffic goes through its firewall.

platform_address_space: 10.100.0.0/21    # the hub, management and identity; laid out by mlz-core
landing_zone_ranges: 10.100.0.0/16       # where landing zone networks get their address ranges
reserved: [10.100.200.0/22]              # networks mlz-core does not manage: never given to a landing zone

firewall: Standard                       # Standard, Premium or Basic
dns_resolver: true                       # resolves Private Link names for landing zones and on-premises
bastion: false                           # Bastion for VMs in any landing zone
```

Defaults: `firewall: Standard`, `dns_resolver: true`, `bastion: false`, no `reserved` ranges. The primary
region (from governance) must have a hub, and a management or identity file needs a hub in its region. When a
network's components get names of their own (`names:`), they will go in that network's file, as a hub's go in
its connectivity file.

The platform starter comes with `connectivity/norwayeast.yaml`, `firewall-rules/platform.yaml` and
`firewall-rules/management.yaml`, every setting in them commented out; rename the connectivity file if the
primary region is another. While no connectivity file has a setting, the platform waits: its check passes with
a notice, and nothing is planned, built or offered to vending. So on a first deployment governance's contract
pull request merges itself, and the pull request that writes the hub is checked against that contract. Once a
connectivity file has a setting, `platform_address_space` and `landing_zone_ranges` are required. A hub pull
request opened before governance's contract has arrived fails its check, saying so; bring its branch up to date
once the contract is merged (`gh pr update-branch <number> -R <the platform repo>`): a re-run would check the
same commit again.

`reserved` lists ranges the IP allocator must never hand out: existing virtual networks, on-premises
sites, partner links, anything inside `landing_zone_ranges` that mlz-core does not manage. A region's
platform address space may not overlap any hub's reserved ranges, nor another region's landing zone ranges
(its own may hold it). Reserving a range a landing zone already holds
is reported as a conflict on the next run, never solved by moving the landing zone.

The platform's check reads `_generated/vending.yaml` too, and refuses to build management's or identity's
network over a landing zone's range, or over one removed but not yet released (the contract's `held`), whose
network may still exist. Vending gives no landing zone an address in a platform range, so only an IP plan
edited by hand could ask for that.

## What is worked out

- **The platform's range**, `platform_address_space`, a /21 laid out by fixed offsets: the hub's /22
  first, management's /24 at the fifth /24, identity's /24 at the sixth, and a spare /23 last. Every region
  looks the same, and a network turned on later never moves another. The IP allocator gives no landing
  zone an address in it, and the other regions route all of it to this region's firewall. Management's
  and identity's networks keep their /24s whether they are built or not.
- **The hub's subnets**, by one fixed layout inside its /22: firewall /26 first, firewall management /26
  second (Basic only), gateway /27, Bastion /26 at the start of the second /24, then the DNS resolver's
  inbound and outbound /28s. Every hub looks the same.
- **Names**, from the customer's naming template (`docs/naming.md`), each part of the platform an app of its
  own in `prod`: the hub's components `vnet-acme-hub-pd-nwe-01`, `afw-acme-hub-pd-nwe-01`,
  `pip-acme-hub-afw-pd-nwe-01` and so on, and the Private Link zones' group `rg-acme-dns-pd-nwe-01`, the one
  governance's DNS policy points at. They are worked out on every run, so a change to `customer.yaml`'s `naming:`
  renames them, and the plan shows each one replaced for the reviewer to decide. A component can keep a name of
  its own under `names:` in the connectivity file (`firewall: afw-legacy`; the components are in `tools/mlz/names.py`'s
  `HUB`). Region codes are Microsoft's, for every Azure region (`modules/naming/region-codes.json`, from
  avm-utl-regions); a hub may be in any of them.

`python3 -m mlz platform` reads `connectivity/`, `management/`, `identity/` and `_generated/governance.yaml`, checks them, and writes the
root's config (JSON, for one run, never committed); the Terraform roots only read that file.

## Hub module inputs mlz-core leaves alone

A hub file's `advanced:` sets inputs of the hub module that mlz-core does not set, such as Bastion's SKU or the
firewall's zones, for that hub only: `docs/advanced.md`. Anything mlz-core owns is refused with a message that names
its owner; the inputs it takes are on `docs/reference/platform.md`.

## Where a value comes from

`./mlz explain --repo . <region>` shows a hub's every value and where it comes from: what its
connectivity file sets, what mlz-core fills in, and every input of the hub module that is worked out, with its value
as the root is given it. `management`, `identity/<region>` and `firewall-rules` do the same for those stacks.

```
connectivity/norwayeast (connectivity/norwayeast.yaml)

Set in its file
  firewall                                            Premium                 <- connectivity/norwayeast.yaml
  ...
Filled in by mlz-core
  dns_resolver                                        true                    <- mlz-core's default
Worked out
  hub_virtual_networks.firewall.subnet_address_prefix 10.100.0.0/26           <- the first /26 of the hub's /22
  hub_virtual_networks.bastion.name                   bas-demo-hub-pd-nwe-01  <- the naming template; names.bastion in connectivity/<region>.yaml sets it
  diagnostic_settings.workspace_resource_id           /subscriptions/m/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law
                                                      <- management's workspace, from the governance contract
Check
  no problems in the platform's settings (python3 -m mlz platform checks every hub together)
```

`--inputs` lists every input of the stack's module calls instead, each with its class (a setting, worked out,
fixed by Fortytwo, open or closed), its value and where it comes from. The same without the values, for every
root, is in `docs/reference/`.

## Management and identity networks

The platform builds two more networks, each a spoke of its region's hub as a landing zone is: two
peerings, made from both sides by the platform, which owns both ends; a route table sending everything
(`0.0.0.0/0`) to the hub's firewall; the hub's firewall as DNS server (its resolver behind a Basic
firewall); and a network security group on every subnet.

| Network | Where | Subnets |
|---|---|---|
| Management, `vnet-acme-mgt-pd-nwe-01` | the management subscription, in the primary region | `snet-github-runners` /25, for GitHub-hosted runners ([below](#github-hosted-runners)); `snet-runners` /26, for self-hosted runners; `snet-private-endpoints` /27, the state account's first; a /27 free |
| Identity, `vnet-acme-idn-pd-sdc-01` | the identity subscription, in each region with a file in `identity/` | `snet-domain-controllers` /26: the domain controllers will have its .4 and .5 |

Each is a stack of its own in the platform pipeline, `management` and `identity`, with its own state, so
a change to one never plans the hub or the other. They apply after the hub, which they peer with. Both run
every time, so deleting a region's identity file takes identity's network there out of Azure. Identity needs an identity
subscription in `customer.yaml`.

They are built in a resource group the bootstrap makes in each subscription, `rg-acme-mgt-net-pd-nwe-01` or
`rg-acme-idn-net-pd-nwe-01`, named for the primary region, which holds the network in every region.
The platform's pipeline identities have rights on that group only, not on the rest of the subscription:
governance's monitoring and the state account are in management too. Their outbound traffic is allowed by
the firewall's rules like a landing zone's ([below](#firewall-rules)): `firewall-rules/management.yaml` and
`firewall-rules/identity.yaml`. The domain controllers come in a later release.

### GitHub-hosted runners

Where GitHub runs its hosted runners in the customer's own networks (`GITHUB_RUNNER_REGIONS` in
`tools/mlz/platform.py`, from [GitHub's list](https://docs.github.com/en/organizations/managing-organization-settings/about-azure-private-networking-for-github-hosted-runners-in-your-organization#about-supported-regions):
Norway East, Sweden Central, France Central, Switzerland North and UK South and West in Europe; not West or
North Europe),
the management stack delegates `snet-github-runners` to GitHub and creates the `GitHub.Network/networkSettings`
resource that joins it to the customer's GitHub organization, `ghns-acme-mgt-pd-nwe-01`. The
pipeline names the organization by its ID (`GITHUB_REPOSITORY_OWNER_ID`); a plan by hand takes
`-var github_business_id=<id>` (`gh api orgs/<organization> --jq .id`). The stack's `networks` output
holds the resource's ID at GitHub, which the organization's network configuration names. The runners take
no connections, so the subnet's network security group refuses every one opened to them; their own
connections out still get their replies. Elsewhere the subnet stays undelegated. They are used only with
`bootstrap.yaml`'s `runners.kind: github-hosted`: self-hosted runners, the default, are the bootstrap's, in
`snet-runners` ([bootstrap](bootstrap.md#private-runners)).

The state account's private endpoint, through which the runners reach the state, is the bootstrap's
([bootstrap](bootstrap.md#running-it-again)), and `mlz install runners` sets up GitHub's side and moves the
pipelines onto the runner ([bootstrap](bootstrap.md#private-runners)). Azure refuses to delete the network settings resource while
the organization's network configuration still uses it: to take management's network down, delete that
configuration in GitHub first.

## Firewall rules

Every landing zone's outbound traffic goes through its hub's firewall, which denies anything no rule
allows. The rules live in the platform repo, next to the firewalls, so a network person reads and
reviews them in one place:

```yaml
# firewall-rules/landing-zones/payments-prod.yaml: what payments-prod may reach, from its own range.
outbound:
  - name: card-processor
    description: Card payments.
    to: [api.stripe.com]
    ports: [https]
```

`firewall-rules/platform.yaml` holds rules for every landing zone of a hub, from all its landing zone ranges; a
platform address space inside those ranges gets them too. `firewall-rules/management.yaml` holds management's,
from its network's range, and `firewall-rules/identity.yaml` identity's, from its range in every region that
builds it (a rule there with no such region stops the check). A rule names only where traffic goes; the
source is worked out from the vending contract (`_generated/vending.yaml`) and the platform's own layout.

Every rule has a `name`, a `description`, `to` and `ports`. The description says what the rule is for: a
person approves every rule, and reads it there. `to` takes:

- names in lower case (`api.example.com`) and wildcards (`*.example.com`)
- addresses and ranges, up to a /8, and other landing zones by key (`website-prod`)
- FQDN tags such as `WindowsUpdate`
- service tags such as `AzureMonitor` or `Storage.NorwayEast`

Nothing reaches the whole internet: `*`, the `Internet` service tag and a range wider than a /8 stop the
check.

`ports` takes `https`, `http`, TCP ports (`8443`) and `udp:<port>`. A name reaches `https` and `http`
through an application rule, which checks the name the client asks for, and the ports written as numbers
(`443` included) through a network rule. Addresses, landing zones and service tags take every port through
a network rule. A rule makes network rules named after it, `<name>-names` and `<name>-udp` too, so another
rule may not take one of those names. Network rules match names by DNS resolution through the firewall's
DNS proxy, so a name on a port written as a number cannot be a wildcard (`*.example.com` takes only `https`
and `http`), and a Basic firewall, which has no DNS proxy, reaches names on `https` and `http` only.
Regional service tags are written as Azure names them: `Storage.NorwayEast`, but `AzureCloud.norwaye`.

`python3 -m mlz firewall` turns the files into a fixed set of rule collection groups per hub. They are
applied by their own root, `platform/rules/terraform`, the `firewall-rules` stack, so a rule change never plans
the hub. The pipeline refuses a hub plan that deletes or replaces the firewall policy, since the rules live
inside it ([pipelines](pipelines.md)). The stack runs even without a `firewall-rules/` folder: the `platform` group always holds what mlz-core
requires, and deleting a rules file takes its rules out of Azure.

| Group | Priority | Holds |
|---|---|---|
| `platform` | 500 | `required-network` (100): what governance's own policies need, whatever the customer writes. Today, the Azure Monitor Agent, which reaches the `AzureMonitor` and `AzureResourceManager` service tags on 443, from the landing zones and the platform's own networks. `runners-network` and `runners-application` (110 and 115): what the three pipelines need from management's two runner subnets ([below](#what-the-runners-reach)). Then `firewall-rules/platform.yaml` (200 and 205), `firewall-rules/management.yaml` (300 and 305) and `firewall-rules/identity.yaml` (400 and 405). |
| `lz-00` to `lz-29` | 1000 to 3900 | Each landing zone's rules, in the group its key hashes to, for ever. Adding a landing zone never moves another's rules, and the 31 groups in all stay under Azure's limit per policy, however many landing zones there are. |

### Asking for a rule

A landing zone's team asks for its rules with a pull request in the platform repo that changes
`firewall-rules/landing-zones/<key>.yaml`. Any number of landing zones may change in one pull request, as long as
it says why. The platform repo's check runs on it, and the repo's owners approve and merge it; the platform's
next run applies it once its reviewer approves. `schemas/firewall-rules.schema.json` in mlz-core describes the
file, so an editor, or a coding agent writing the request, checks it before the pull request is opened.

- **A landing zone that does not exist yet.** Its file can come before it: a key the vending contract does not
  name yet is checked like any other, can be merged, and waits, with a notice on every run; the platform's
  first run after vending publishes the landing zone applies its rules. A mistyped key waits for ever, named
  in that notice. Before vending has published anything at all, on the platform's first deployment, every
  landing zone's file waits the same way, so the hub can be built first.
- **A destination must exist.** A rule to another landing zone that the contract does not name stops the
  check.
- **A landing zone being retired.** A key the contract lists as held, a landing zone decommissioned or
  removed whose range is not released yet, only warns: its rules leave Azure with that run, before the range
  can go to another landing zone, and the file can be deleted (`docs/vending.md`, decommissioning). A rule
  elsewhere that still names a held landing zone stops the check, saying so.

### What the runners reach

From `snet-github-runners` and `snet-runners`, in the primary hub's `platform` group, on 443:

- **GitHub**: the names GitHub's runners need, as GitHub's meta endpoint lists them
  (`api.github.com/meta`, `domains.actions`): its own wildcards, a few fixed names and its result storage
  accounts, named one by one. A name one label below a wildcard in the same list is left out.
- **Entra ID and Azure Resource Manager**: the `AzureActiveDirectory` and `AzureResourceManager` service tags.
- **Terraform**: `registry.terraform.io` and `releases.hashicorp.com`, for Terraform, its providers and modules.
- **Runner registration**: `raw.githubusercontent.com`, where self-hosted runners fetch Fortytwo's registration
  script when they boot, pinned to a commit and checked against its hash. GitHub does not list it for Actions.

The platform's check reads GitHub's list on every run, and stops if it cannot: the rule is never planned
without it. The pipeline reads it in a step before the check, with the job's token, whose rate limit is the
repository's own (1,000 requests an hour, 15,000 on GitHub Enterprise Cloud). `./mlz check` on a laptop reads
it anonymously, which GitHub allows 60 times an hour per address; once that is used up, the check says until
when, and tries no more. GitHub lists a name at least a week before using it. When the list changes, the next
plan changes the `platform` group, and the daily drift check fails until it is applied: run the workflow on
`main` and approve the apply, as for any other change. Anything else the runners reach, such as systems in
landing zones or on-premises, goes in `firewall-rules/management.yaml`.

## Contract

The `contract` output, written as `_generated/platform.yaml` in the governance and vending repos by
`python3 -m mlz contract platform`: the hub's
connectivity (peering target, firewall address, DNS servers, the spoke routes), the hubs' own ranges
and the reserved ones (which the IP allocator never hands out), and where the Private Link zones are.
