# Vending

The `mlz-vending` role: one landing zone per YAML file, each with a subscription placed in its management
group, a spoke network peered to the hub with everything routed through the firewall, NSGs, a deploy
identity, and a budget. Built on Microsoft's `Azure/avm-ptn-alz-sub-vending/azure` and
`az-tf-mlz-route-table`. Each landing zone has a state of its own.

## What the customer writes

Four layers, each saying only what differs from the one above:

```
landing-zones/
  _defaults/all.yaml          every landing zone
  _defaults/<env>.yaml        every landing zone in that environment (optional)
  <workload>/_workload.yaml   one workload, all its environments
  <workload>/<env>.yaml       one landing zone
```

```yaml
# payments/_workload.yaml: the payments workload, all environments
name: Payments
owner: payments-team@demo.example     # becomes the Owner tag
cost_center: "4711"                   # becomes the CostCenter tag
archetype: corp                       # corp, online or sandbox
```

```yaml
# payments/prod.yaml: payments in production, one subscription
subscription: 4f6e5589-48dd-43f1-9955-43475966658d
budget:
  monthly: 2000
```

The landing zone's key is its folder and file name, `payments-prod`. Settings: `name`, `owner`,
`cost_center`, `archetype`, `location`, `subscription`, `budget` (`monthly`, `contacts`), `network`
(`size`, a prefix length, default 24; `subnets`, name to prefix length, default `default: 26`),
`github` (`repo`), and `tags` for extra tags. Any layer can be a folder of chunks.

## What is worked out

- **Address ranges**, by the IP allocator (`python3 -m mlz allocate`): the first free block of the landing
  zone's size in its region's `landing_zone_ranges`, never the hub's own range. It writes one file,
  `_generated/ip-plan.yaml`, the whole IP plan in one list; a range never moves once given. Nobody types
  an address range, and no bot edits a file people write.
- **Subnets**, carved from the range largest first.
- **Tags** on everything: `Owner`, `CostCenter`, `Environment` (the file name), `Workload` (the folder),
  `ManagedBy`.
- **Names**, `<type>-<key>-<region code>`, for example `vnet-payments-prod-nwe`.

`python3 -m mlz vending` checks every landing zone (every problem at once, with the file it came from)
and writes `<key>.json` per landing zone; the Terraform root reads one of them.
