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
owner: payments-team@demo.example     # becomes the Owner tag
cost_center: "4711"                   # becomes the CostCenter tag
archetype: corp                       # corp, online or sandbox
```

```yaml
# payments/prod.yaml: payments in production, one subscription
subscription: 00000000-0000-0000-0000-000000000005
budget:
  monthly: 2000
```

The vending starter comes with `_defaults/all.yaml`, its settings commented out, and a complete landing
zone in `landing-zones/_examples/payments/`, which vending skips: a folder whose name starts with `_` is
never a workload. Copy the folder to `landing-zones/payments/` to start from it.

The landing zone's key is its folder and file name, `payments-prod`; two paths that make the same key
(`payments-api/prod.yaml` and `payments/api-prod.yaml`) are refused. Settings:

| Setting | What it is |
|---|---|
| `owner`, `cost_center`, `archetype`, `location`, `subscription` | One value each. `owner` is the owning team's email address. |
| `budget` | `monthly`, a whole amount; `contacts`, email addresses (default: the owner). No `monthly`, no budget. |
| `network` | `size`, the range's prefix length, 16 to 28 (default 24); `subnets`, each name (up to 20 characters) to its prefix length, up to 29 (default one subnet, `default`, a /26 or the whole of a smaller range). |
| `github` | `organization` and `repo`: the landing zone's repo, whose `main` branch signs in as its deploy identity. Set the organization once in `_defaults/all.yaml`. |
| `tags` | Extra tags. `Owner`, `CostCenter`, `Environment`, `Workload` and `ManagedBy` are set for you. Azure ignores case in tag names, so the checks do too. At most 45, within Azure's limits on names and values. |

Any layer can be a folder of chunks.

## What is worked out

- **Address ranges and subnets**, by the IP allocator (`python3 -m mlz allocate`). A new landing zone gets
  the first free block of its size in its region's `landing_zone_ranges`, never a hub's own range or a
  range the platform reserves; a new subnet gets the first free block in its landing zone's range,
  largest first. It writes one file, the
  network plan, and nobody types an address:

  ```yaml
  # _generated/ip-plan.yaml
  payments-prod:
    range: 10.100.4.0/24
    subnets:
      default: 10.100.4.0/26
  ```

  **Nothing in it ever moves.** New ranges and subnets are placed largest first; adding a subnet leaves
  the others where they are; a range or subnet never changes size (give a bigger subnet a new name, then
  remove the old one). **Nothing removed is given out again until it is released**: a removed subnet
  moves to `removed_subnets` and a removed landing zone keeps its range, marked in the file, until it is
  destroyed and someone runs `python3 -m mlz release --repo . <key>`. A removed subnet asked for again at
  the same size gets its block back. Every run checks what was given against the platform as it is now
  (inside the region's ranges, clear of every hub and every other landing zone) and reports a conflict
  rather than moving anything. It writes nothing when anything is wrong.

  **Nobody runs the allocator by hand.** On a pull request, the pipeline runs it on the pull request merged
  into `main` and, when anything new needs addresses, commits the plan to the pull request
  (`chore: IP plan for this pull request`, from the pipeline's GitHub App). That run stops there; the run
  of the new commit checks and plans with the addresses, and the reviewer sees them in the pull request.
  Because it allocates on top of `main`, blocks another pull request has already merged are never given
  out twice. If `main` changed the plan since the branch was made, the pipeline first brings the branch up
  to date with `main` (a merge commit from the same App), so the plan it commits next never conflicts with
  `main`'s. The commit is made only if the branch has not moved in the meantime; a newer push has a run of
  its own. You can still run `python3 -m mlz allocate --repo .` yourself, for example to see the
  addresses before pushing.

  `python3 -m mlz allocate --check` writes nothing and fails when the plan in the repo is not what the
  allocator would write. On `main` it stops two pull requests that each took the same free block, if both
  were merged before either was brought up to date. 500 landing zones take about a second.
- **Tags** on everything: `Owner`, `CostCenter`, `Environment` (the file name), `Workload` (the folder),
  `ManagedBy`.
- **Names**, `<type>-<key>-<region code>`, for example `vnet-payments-prod-nwe`.

`python3 -m mlz vending` checks every landing zone: every problem at once, each naming the file that set
the value (a mistake in `_defaults/all.yaml` is reported once, against that file, not once per landing zone),
and writes `<key>.json` per landing zone; the Terraform root reads one of them. The output folder is
its own: it is emptied of old configs on every run, so a removed landing zone leaves none behind, and a
folder holding anything else is refused.

## Where a value comes from

With four layers, a value can come from any of them, from mlz-core, or from another repo's contract.
`python3 -m mlz explain --repo . <key>` shows a landing zone's every value and where it comes from:

```
payments-prod (landing-zones/payments/prod.yaml)

Set in its layers
  owner                payments-team@demo.example     <- landing-zones/payments/_workload.yaml
  budget.monthly       2000                           <- landing-zones/payments/prod.yaml, replacing landing-zones/_defaults/all.yaml: 500
  ...
Filled in by mlz-core
  network.size         24                             <- mlz-core's default
Worked out
  management group     demo-lz-corp                   <- governance's contract, for archetype corp
  address range        10.100.4.0/24                  <- the IP plan (_generated/ip-plan.yaml)
  route default        0.0.0.0/0 via 10.100.0.4       <- the platform's contract, for norwayeast
Check
  no problems in this landing zone (python3 -m mlz vending checks them all together)
```

It reads the contracts in `_generated/` when they are there, and says what waits for them when they are
not. It shows what mlz-core's loader decides; what the vending root does the same way for every landing
zone, such as its NSGs, is in `vending/terraform`.

## Operating notes

- **A new landing zone settles on its second run.** Sub-vending records whether the subscription is already
  in its management group as part of the plan. On the first apply it is not (placing it is that apply's
  job), so the next plan re-places it in the same group once, and from then on plans show no changes.
  Harmless; met in the canary on 2 November 2026.
- **Subscriptions must be writable.** A subscription that is disabled or read-only (for CSP, the "Warned"
  state) fails at its first resource group with `ReadOnlyDisabledSubscription`, after it has already been
  placed in its management group. Check the subscription's state before vending it.
