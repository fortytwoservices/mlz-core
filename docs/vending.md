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

The landing zone's key is its folder and file name, `payments-prod`; two paths that make the same key
(`payments-api/prod.yaml` and `payments/api-prod.yaml`) are refused. Settings:

| Setting | What it is |
|---|---|
| `name`, `owner`, `cost_center`, `archetype`, `location`, `subscription` | One value each. `owner` is the owning team's email address. |
| `budget` | `monthly`, a whole amount; `contacts`, email addresses (default: the owner). No `monthly`, no budget. |
| `network` | `size`, the range's prefix length, 16 to 28 (default 24); `subnets`, each name to its prefix length, up to 29 (default one subnet, `default`, a /26 or the whole of a smaller range). |
| `github` | `organization` and `repo`: the landing zone's repo, whose `main` branch signs in as its deploy identity. Set the organization once in `_defaults/all.yaml`. |
| `tags` | Extra tags. `Owner`, `CostCenter`, `Environment`, `Workload` and `ManagedBy` are set for you. |

Any layer can be a folder of chunks.

## What is worked out

- **Address ranges and subnets**, by the IP allocator (`python3 -m mlz allocate`). A new landing zone gets
  the first free block of its size in its region's `landing_zone_ranges`, never the hub's own range; a new
  subnet gets the first free block in its landing zone's range, largest first. It writes one file, the
  network plan, and nobody types an address:

  ```yaml
  # _generated/ip-plan.yaml
  payments-prod:
    range: 10.100.4.0/24
    subnets:
      default: 10.100.4.0/26
  ```

  **Nothing in it ever moves.** Adding a subnet leaves the others where they are; a range or subnet never
  changes size (give a bigger subnet a new name, then remove the old one). A removed subnet is freed, but
  not given out again in the same run. A removed landing zone keeps its range, marked in the file, until it
  is destroyed and someone runs `python3 -m mlz release --repo . <key>`. Every run checks what was given
  against the platform as it is now (inside the region's ranges, clear of every hub and every other
  landing zone) and reports a conflict rather than moving anything. It writes nothing when anything is
  wrong.
- **Tags** on everything: `Owner`, `CostCenter`, `Environment` (the file name), `Workload` (the folder),
  `ManagedBy`.
- **Names**, `<type>-<key>-<region code>`, for example `vnet-payments-prod-nwe`.

`python3 -m mlz vending` checks every landing zone (every problem at once, with the file it came from)
and writes `<key>.json` per landing zone; the Terraform root reads one of them.

## Operating notes

- **A new landing zone settles on its second run.** Sub-vending records whether the subscription is already
  in its management group as part of the plan. On the first apply it is not (placing it is that apply's
  job), so the next plan re-places it in the same group once, and from then on plans show no changes.
  Harmless; met in the canary on 2 November 2026.
- **Subscriptions must be writable.** A subscription that is disabled or read-only (for CSP, the "Warned"
  state) fails at its first resource group with `ReadOnlyDisabledSubscription`, after it has already been
  placed in its management group. Check the subscription's state before vending it.
