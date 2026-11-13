# Naming

Every name mlz-core gives comes from one template per customer, filled with short names the customer chooses,
and checked against Azure's rules for that kind of resource before anything is planned. An explicit name for
one resource always wins.

**Where it stands:** landing zones are named from it. The platform, governance and the bootstrap keep
mlz-core's fixed names until they move onto it too.

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
- `python3 -m mlz explain <key>` shows every name and where it came from.

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
```

mlz-core's environments: `dev: dv`, `test: te`, `uat: ut`, `sit: st`, `qa: qa`, `preprod: pp`,
`sandbox: sb`, `dr: dr`, `prod: pd`. Regions take Microsoft's codes (`nwe`, `sdc`, `we`, `ne`, …); two regions
may not share one.

The section is checked where it is written, in governance's run (`governance/scripts/render_library.py`), and
governance's contract carries it as written to the repos that name things with it. A root ID longer than four
characters, or with a hyphen, cannot be the customer's code, so it needs `naming.org`: governance's check asks for it
once the section is there, and vending's, which names every landing zone, asks for it either way.

The design and the decisions behind it, 13 November 2026: recorded names, `mlz names --renew`, the platform's
app names and child resources, in the managed landing zone's design notes.
