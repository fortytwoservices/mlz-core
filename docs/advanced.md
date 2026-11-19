# advanced: module inputs mlz-core leaves alone

Every input of every module call mlz-core makes has an owner, listed in `docs/reference/`: a setting in the
customer's YAML, a value mlz-core works out, a value Fortytwo fixes for every customer, or nobody. An input nobody
owns is `open`: mlz-core does not set it, and the module's default applies. `advanced:` sets such an input in one
line of YAML, without an mlz-core release (decision D1, 12 November 2026).

```yaml
# connectivity/norwayeast.yaml
platform_address_space: 10.100.0.0/21
landing_zone_ranges: 10.100.0.0/16
bastion: true

advanced:                       # module inputs mlz-core leaves alone; names as in the module
  bastion:
    sku: Premium
    tunneling_enabled: true
```

## What it reaches

A file's `advanced:` reaches only the module call of the stack that file feeds, and in it only what that file
builds:

| File | Reaches | Keys, as the module names them |
|---|---|---|
| `connectivity/<region>.yaml` | that region's hub in `az-tf-mlz-connectivity-hub`'s `hub_virtual_networks` | `bastion.sku`, `firewall.zones`, `virtual_network_dns_servers`, ... |
| a landing zone's file, or a layer above it | `Azure/avm-ptn-alz-sub-vending`'s call: its own inputs, and its objects by the keys vending gives them | `role_assignments`, `virtual_networks.spoke.subnets.<subnet>.service_endpoints`, `network_security_groups.<subnet>.security_rules`, ... |

`customer.yaml` comes next (brief L3). An input of the whole hub module call, such as its `retry`, is not reached
from a hub file, since each region has its own file.

A landing zone's `advanced:` goes in any of its layers and is merged as its other settings are, so
`defaults/landing-zone-defaults.yaml` can give every landing zone a role assignment, and one landing zone's file a
subnet's service endpoints. Sub-vending's objects are maps vending keys itself, and `advanced:` names an entry by
its key: `spoke` for the one network, a subnet's name for a subnet or its NSG, `monthly` for the budget (only with
`budget.monthly`), `actual_80` and `forecast_100` for its alerts. A key vending does not give is refused, so a
mistyped subnet name is not dropped:

```yaml
# landing-zones/payments/prod.yaml
advanced:
  role_assignments:               # on the subscription; role_assignment_enabled follows from it
    readers: {principal_id: 00000000-0000-0000-0000-000000000000, definition: Reader}
  virtual_networks:
    spoke:
      subnets:
        app:
          service_endpoints: [Microsoft.Storage]
```

Which keys a file takes is on its root's page in `docs/reference/` ("set it with `advanced.bastion.sku`"), in the
editor through the file's schema, and in `python3 -m mlz explain --repo . <region> --inputs`, which shows what
`advanced:` set beside everything else the module is given.

## What it refuses

Anything that is not `open` is refused, with a message that names its owner:

```
connectivity/norwayeast: advanced.firewall.subnet_address_prefix is worked out by mlz-core (the first /26 of the hub's /22); it cannot be set.
connectivity/norwayeast: advanced.firewall.name is worked out by mlz-core (the naming template); it cannot be set. To name it yourself, set names.firewall in this file.
connectivity/norwayeast: advanced.firewall.sku_tier is the setting firewall in connectivity/<region>.yaml; set it there.
connectivity/norwayeast: advanced.firewall.firewall_policy.dns_proxy_enabled is fixed by Fortytwo (...). Ask for a change in mlz-core.
connectivity/norwayeast: advanced.vpn_gateway is not supported yet (...).
```

A name is never set here: `names:` is its key. Neither can `advanced:` add a component the file turns off
(`advanced.bastion` with `bastion: false`). A key the module does not have, and a value of the wrong type, are refused
too (below).

**The rule of promotion:** an `advanced:` path two customers use becomes a setting of its own, or a value Fortytwo
fixes for everyone.

## How it works

1. The loader checks the block against the root's input map (`<root>/inputs.yaml`, `tools/mlz/advanced.py`): each
   path must be an input of the module at its pin, covered by an `open` entry, with a value of the module's type.
2. It writes the block, as given, into the stack's config (`hubs.<region>.advanced` in the platform's, `advanced`
   in a landing zone's), only when there is one. A stack without `advanced:` renders exactly as before.
3. The root puts the block in first and mlz-core's own values over it, so it can only add: the platform's
   `local.hub_virtual_networks`, vending's `local.network_security_groups`, `local.virtual_networks` and
   `local.budgets`. A module input of its own is passed from the block, or as `null` or the module's default when the
   block has none (sub-vending's `role_assignments` and `subscription_register_resource_providers_and_features` are
   `nullable = false`, so `null` is their default). A stack without one is given exactly what it was given before:
   `platform/terraform/tests/advanced.tftest.hcl` and vending's `tests/decommission.tftest.hcl` (runs `advanced` and
   `without_advanced`) check both, with the providers mocked.

### Why the loader checks types itself

Terraform does not guard a module's object inputs. Checked with Terraform 1.17.0, a throwaway root passing objects to
a module whose variable is `map(object({ name = string, bastion = optional(object({ sku = optional(string,
"Standard"), tunneling = optional(bool, false) })) }))`, from `jsondecode` of a file and as a literal alike:

- an attribute the type does not declare (`colour`, or `tunnelling` for `tunneling`) is dropped, with no error and
  no warning, and `terraform validate` passes;
- a value Terraform can convert is converted: `5` becomes `"5"` for a string, `"true"` becomes `true` for a bool;
- a value it cannot convert (`"yes"` for a bool, a list for a string) fails the plan with "Invalid value for input
  variable";
- an explicit `null` for an optional attribute gives the attribute's default, as leaving it out does.

So a mistyped key would be ignored without a word, and a quoted number would pass. The loader refuses both before
anything is planned.
