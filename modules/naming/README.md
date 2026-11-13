# Naming

Names from the customer's template (`docs/naming.md`), for the resources a team makes in its landing zone's workload
repo: the same template, codes and rules mlz-core names the landing zone with, so a team's Application Gateway is
named as vending names its network.

```hcl
module "gateway_name" {
  source = "git::https://github.com/fortytwoservices/mlz-core.git//modules/naming?ref=vX.Y.Z"

  mlz     = var.mlz # the landing zone's naming values, which vending writes to mlz.auto.tfvars.json
  type    = "application_gateway"
  purpose = "web" # optional: where the landing zone has more than one of its type
}
# module.gateway_name.name: agw-acme-pay-web-pd-nwe-01
```

- **`type`** is one of `name-rules.json`'s types.
- **`purpose`** is optional, up to 20 lower-case letters, digits and inner hyphens. An empty one drops out of the name
  with one dash.
- **`nn`** (default 1) tells apart more of the same role: `01`, `02`.
- A name Azure would not take for its type stops the plan with the reason, as mlz-core's own names do: it is
  refused, never cut.

`tools/mlz/test_naming_helper.py` renders cases through this module and through `tools/mlz/naming.py`, and requires
the same names.

Its data, which `tools/mlz/naming.py` reads too:

- `region-codes.json`: every Azure region and Microsoft's code for it, as in `vnet-acme-pay-pd-nwe-01`. It is
  `Azure/avm-utl-regions` v0.13.0's geo codes. A code is in resource names, so one never changes once used.
- `name-rules.json`: each type's short name and Azure's naming rules for it (length, case, dashes, characters).
  `tools/dev/name_rules.py` writes it from `Azure/avm-utl-naming`'s catalog, with mlz-core's own rule where
  Microsoft publishes none.
