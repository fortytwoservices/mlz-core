# Naming

Every name mlz-core gives comes from one template per customer, filled with short names the customer chooses,
and checked against Azure's rules for that kind of resource before anything is planned. An explicit name for
one resource always wins.

**Where it stands:** everything mlz-core makes is named from it: landing zones, the platform, governance and the
bootstrap. Subnets, peerings and other parts named inside a resource keep their own patterns.

## The template

mlz-core's default:

```
{type}-{org}-{app}-{purpose}-{env}-{region}-{nn}
rg-acme-pay-net-pd-nwe-01        vnet-acme-pay-pd-nwe-01        stacmepaypdnwe01
```

| Token | What | From |
|---|---|---|
| `{type}` | The resource's short name, CAF's abbreviation: `rg`, `vnet`, `st`, `kv` | mlz-core |
| `{org}` | The customer's code, 2 to 4 lower-case letters and digits | `naming.org`, or the root ID |
| `{app}` | The landing zone's short name, 2 to 5 | the workload |
| `{purpose}` | Optional: what a resource is for, where there are several of a type | mlz-core, or the engineer naming it |
| `{env}` | The environment's short name | the landing zone's file name |
| `{region}` | The region's short name, Microsoft's code | `modules/naming/region-codes.json` |
| `{nn}` | 01, 02 and so on, for more of the same role | |

A template is tokens in braces, dashes, and lower-case text such as `az`. It must contain `{type}`. An empty
`{purpose}` drops out with one dash beside it; no other token may be empty, so a missing short name, or an
environment without one, stops the check. Where a type takes no dashes, as a storage account doesn't, they all
drop out; where it must be lower case, the whole name is lowered.

## Azure's rules

Each name is checked against its type's rules (`modules/naming/name-rules.json`), which come from the catalog
of `Azure/avm-utl-naming` v0.2.0, built from Microsoft's [resource naming
rules](https://learn.microsoft.com/azure/azure-resource-manager/management/resource-name-rules):
length, case, dashes and allowed characters. mlz-core adds what the catalog's patterns leave out (no two
hyphens in a row in a key vault's name, no period at the end of a resource group's), and takes its short
names from CAF. `tools/dev/name_rules.py` writes the file from the catalog.

Two types have no rule anywhere: data collection rules (the catalog's entry has no lengths) and GitHub's network
settings (not in the catalog). Microsoft publishes none for either: they are not on the naming rules page, the REST
API specs give the name no pattern or length, and azurerm only checks that it is not empty. For those mlz-core sets
its own, narrow enough to be safe: 1 to 64 letters, digits, hyphens, underscores and periods, starting and ending
with a letter or digit. It may refuse a name Azure would take, never the other way round.

**A name that does not fit is refused, never cut**: two long names cut short can come out the same. The
message gives the name, its length and how far over it is. The way out is a shorter short name, a template of
its own for that type, or an explicit name. Storage accounts and key vaults allow only 24 characters, so the
short names are kept short: the customer's code 2 to 4, an app 2 to 5. A purpose takes up to 20, with hyphens inside (an NSG's is its subnet's name), but the type's length is the real limit.

## Landing zones

Vending names everything it makes in a landing zone from the template, with the workload's short name, the
landing zone's environment (its file name) and its region:

| What | Purpose | For example |
|---|---|---|
| The network's resource group | `net` | `rg-acme-pay-net-pd-nwe-01` |
| The identity's resource group | `id` | `rg-acme-pay-id-pd-nwe-01` |
| The virtual network, the route table, the deploy identity, the budget | none | `vnet-acme-pay-pd-nwe-01` |
| Each subnet's NSG | the subnet's name | `nsg-acme-pay-default-pd-nwe-01` |
| The workload repo's plan identity | `plan` | `id-acme-pay-plan-pd-nwe-01` |
| Its state's resource group and storage account | `state` | `rg-acme-pay-state-pd-nwe-01`, `stacmepaystatepdnwe01` |
| The workload repo itself, when vending makes it | `customer.yaml`'s `naming.repository` | `payments-prod` |

Subnets (`snet-<name>`) and peerings (`peer-<key>-to-hub`, `peer-hub-to-<key>`) are named inside their network,
so they keep that pattern.

- **`short_name`**, in `_workload.yaml`: the workload's short name, 2 to 5 lower-case letters and digits, one
  per workload. A workload whose folder name fits (`web`) needs none: that is its short name. Two workloads may
  not share one. `python3 -m mlz new landing-zone` writes it for a new workload (`--short-name`), or leaves it to
  fill in.
- **`names:`**, in the landing zone's own file only: an explicit name for any of these, which wins over the
  template and is checked against the same rules.

  ```yaml
  names:
    virtual_network: vnet-payments-legacy
    network_security_groups:
      default: nsg-payments-web
  ```

  A changed name replaces the resource: the plan shows it deleted and created again.
- Both resource groups, and every NSG, need names of their own. A template without `{purpose}` gives them one
  each, so the check refuses it until `{purpose}` is back or one of them is named under `names:`.
- `python3 -m mlz explain <key>` shows every name and where it came from.

### A name, once given, stays

The IP allocator gives a landing zone its names along with its addresses, and records them in
`_generated/names.yaml`; the pull request's run commits both (`docs/vending.md`). Vending's plan reads the names
from there, not from the template, so **changing the template, a short name or an environment's code renames
nothing already named**: only what is new takes the template as it is then, such as a new landing zone, or the NSG of
a new subnet.

```yaml
# _generated/names.yaml
payments-prod:
  network_resource_group: rg-acme-pay-net-pd-nwe-01
  ...
  network_security_groups:
    default: nsg-acme-pay-default-pd-nwe-01
```

A name counts as given once it is on `main`, where it can be deployed from. Until then, each run of the pull
request gives it again from the YAML as it is, so a short name changed in review still applies.

To rename:

- **one resource**: set its name under `names:` in the landing zone's file. The file's name wins and the record
  drops that resource; take the name out again and the template gives it a new one.
- **a whole landing zone**: `python3 -m mlz names --repo . --renew <key>` gives what it has now the template's names
  again and writes the record. Commit it in a pull request: its plan shows each renamed resource replaced, and nothing is
  changed until the reviewer approves the apply. `mlz explain <key>` shows, beside each recorded name, what the
  template would give now.

**A removed landing zone or subnet keeps its names while it holds its addresses** in the IP plan: asked for again
before it is released, it is the same resource under the same name. `python3 -m mlz release` frees both, and a
decommissioned landing zone's names go with its file and range, in the same pull request.

## The platform, governance and the bootstrap

Each part of the platform is an app of its own, in `prod` (the platform's names, below, are for a customer whose
code is `acme`, with its primary region Norway East):

| Part | Its names |
|---|---|
| `hub`, in each region | `rg-acme-hub-pd-nwe-01`, `vnet-acme-hub-pd-nwe-01`, `afw-acme-hub-pd-nwe-01`, `afwp-acme-hub-pd-nwe-01`, `pip-acme-hub-afw-pd-nwe-01` (and `-afwmgmt-` behind a Basic firewall), `rt-acme-hub-afw-pd-nwe-01`, `bas-acme-hub-pd-nwe-01` with `pip-acme-hub-bas-pd-nwe-01`, `dnspr-acme-hub-pd-nwe-01` with `in-` and `out-acme-hub-pd-nwe-01` |
| `dns` | `rg-acme-dns-pd-nwe-01`, the Private Link zones' group |
| `mgt`, management | governance's `rg-acme-mgt-pd-nwe-01`, `log-acme-mgt-pd-nwe-01`, `id-acme-mgt-ama-pd-nwe-01`, `dcr-acme-mgt-changetracking-pd-nwe-01` (`vminsights`, `defendersql`); the network's `rg-acme-mgt-net-pd-nwe-01`, `vnet-`, `rt-`, `ghns-acme-mgt-pd-nwe-01` and an NSG per subnet, `nsg-acme-mgt-private-endpoints-pd-nwe-01` |
| `idn`, identity | `rg-acme-idn-net-pd-nwe-01`, and in each region with identity's network `vnet-acme-idn-pd-sdc-01` and the rest |
| `mlz`, the bootstrap | `rg-acme-mlz-pd-nwe-01`, `stacmemlzstatepdnwe01`, `id-acme-mlz-govplan-pd-nwe-01` (`govapply`, `platplan`, `platapply`, `vendplan`, `vendapply`), `pep-` and `nic-acme-mlz-state-pd-nwe-01` |

`tools/mlz/names.py` works them all out, and each root's loader puts its own in its config (`render_library.py`
for governance, `mlz platform` for the platform's roots, `mlz install` for the bootstrap). A name two roots share,
such as the DNS zones' group or management's network, comes from one place, so they always agree.

Unlike a landing zone's, these are **worked out on every run** and not recorded: they change only with a reviewed
change to `customer.yaml`, whose plans show every renamed resource replaced. Two ways to keep a name:

- **A hub component**: under `names:` in its hub file (`docs/platform.md`).
- **The bootstrap's state account or resource group**: under `names:` in `bootstrap.yaml`. The state account
  holds every role's state, so it has `prevent_destroy`: a change that would rename it stops the plan instead
  (`docs/bootstrap.md`). A storage account's name is global, so this is also the way out when another tenant
  has taken it.

## customer.yaml

```yaml
naming:
  org: acme                    # 2–4; default: the root ID, if that fits
  template: "{type}-{org}-{app}-{purpose}-{env}-{region}-{nn}"   # the default; leave it out
  templates:                   # one type's own, for the 24-character ones
    key_vault: "{type}{org}{app}{purpose}{env}{nn}"              # kvacmepaylogspd01
  environments:                # added to or replacing mlz-core's
    hotfix: hf
  regions:                     # replacing Microsoft's code, before the first deployment only
    westeurope: weu
  platform:                    # the platform's parts' short names, replacing mlz-core's
    hub: con
  repository: "{app}-{env}"    # a landing zone's workload repo, with long names: payments-prod
```

mlz-core's environments: `dev: dv`, `test: te`, `uat: ut`, `sit: st`, `qa: qa`, `preprod: pp`,
`sandbox: sb`, `dr: dr`, `prod: pd`. Regions take Microsoft's codes (`nwe`, `sdc`, `we`, `ne`, …); two regions
may not share one.

The platform's parts are each named as an app of their own, in `prod`: `hub`, `dns`, `mgt` (management), `idn`
(identity) and `mlz` (the bootstrap), as in `vnet-acme-hub-pd-nwe-01`, `rg-acme-mgt-net-pd-nwe-01` or
`id-acme-mlz-govplan-pd-nwe-01`. `naming.platform` gives a part another short name, 2 to 5, each its own. A workload
may not take one of them as its short name: a landing zone named `hub` would name its network as the hub's is named.

The section is checked where it is written, in governance's run (`governance/scripts/render_library.py`), and
governance's contract carries it as written to the repos that name things with it. A root ID longer than four
characters, or with a hyphen, cannot be the customer's code, so it needs `naming.org`, with or without the section:
every root names things with it.

The design and the decisions behind it, 13 November 2026: recorded names, `mlz names --renew`, the platform's
app names and child resources, are in the managed landing zone's design notes.
