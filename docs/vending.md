# Vending

The `mlz-vending` role: one landing zone per YAML file, each with a subscription, handed over or made by vending,
placed in its management group, a spoke network peered to the hub with everything routed through the firewall, NSGs, a deploy
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
| `owner`, `cost_center`, `archetype`, `location`, `subscription` | One value each. `owner` is the owning team's email address. `subscription` is one handed over; without it, vending makes one ([below](#the-subscription)). |
| `billing_scope` | Where the subscriptions vending makes are billed: an EA enrollment account or an MCA invoice section. Set once, in `_defaults/all.yaml` ([below](#the-subscription)). |
| `budget` | `monthly`, a whole amount; `contacts`, email addresses (default: the owner). No `monthly`, no budget. |
| `network` | `size`, the range's prefix length, 16 to 28 (default 24); `subnets`, each name (up to 20 characters) to its prefix length, up to 29 (default one subnet, `default`, a /26 or the whole of a smaller range). |
| `github` | The landing zone's workload repo ([below](#the-workload-repo)). `organization`, set once in `_defaults/all.yaml`: from then on every landing zone has one. `repo`: an existing repo to use instead, which vending only trusts. `create: false`: none, for example for a sandbox. `team`: the owning team's GitHub team, by its slug, set once per workload in `_workload.yaml`. |
| `tags` | Extra tags. `Owner`, `CostCenter`, `Environment`, `Workload` and `ManagedBy` are set for you. Azure ignores case in tag names, so the checks do too. At most 45, within Azure's limits on names and values. |

Any layer can be a folder of chunks.

## What is worked out

- **Address ranges and subnets**, by the IP allocator (`python3 -m mlz allocate`). A new landing zone gets
  the first free block of its size in its region's `landing_zone_ranges`, never in a region's
  `platform_address_space` (the hub, management and identity) or a range the platform reserves; a new subnet gets the first free block in its landing zone's range,
  largest first. It writes the network plan, and nobody types an address:

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
  into `main` and, when anything new needs addresses or names, commits the plan and the names to the pull request
  (`chore: IP plan and names for this pull request`, from the pipeline's GitHub App). That run stops there; the run
  of the new commit checks and plans with them, and the reviewer sees them in the pull request.
  Because it allocates on top of `main`, blocks another pull request has already merged are never given
  out twice. If `main` changed the plan since the branch was made, the pipeline first brings the branch up
  to date with `main` (a merge commit from the same App), so the plan it commits next never conflicts with
  `main`'s. The commit is made only if the branch has not moved in the meantime; a newer push has a run of
  its own. You can still run `python3 -m mlz allocate --repo .` yourself, for example to see the
  addresses before pushing.

  `python3 -m mlz allocate --check` writes nothing and fails when the plan or the names in the repo are not what
  the allocator would write. On `main` it stops two pull requests that each took the same free block, if both
  were merged before either was brought up to date. 500 landing zones take about a second.
- **Tags** on everything: `Owner`, `CostCenter`, `Environment` (the file name), `Workload` (the folder),
  `ManagedBy`.
- **Names** from the customer's template, with the workload's `short_name`: for example `vnet-acme-pay-pd-nwe-01`,
  or an explicit one under `names:` in the landing zone's file. The allocator gives them with the addresses
  and records them in `_generated/names.yaml`, where they stay when the template or a short name changes;
  `python3 -m mlz names --renew <key>` gives a landing zone the template's names again (`docs/naming.md`).

`python3 -m mlz vending` checks every landing zone: every problem at once, each naming the file that set
the value (a mistake in `_defaults/all.yaml` is reported once, against that file, not once per landing zone),
and writes `<key>.json` per landing zone; the Terraform root reads one of them. The output folder is
its own: it is emptied of old configs on every run, so a removed landing zone leaves none behind, and a
folder holding anything else is refused.

## Starting a landing zone

`python3 -m mlz new landing-zone --repo . <workload> <env>` writes `landing-zones/<workload>/<env>.yaml`,
and `_workload.yaml` for a new workload, with only what no layer above sets:

```sh
python3 -m mlz new landing-zone --repo . billing prod --subscription <id> --owner billing@demo.example
```

- **Given or not:** a required setting given on the command line (`--subscription`, `--owner`,
  `--cost-center`, `--archetype`, `--location`) is written as given. One not given is written commented
  out, and the check names it until someone fills it in. With `billing_scope` set above, no subscription is
  asked for: vending makes it, unless `--subscription` hands one over.
- **Not repeated:** a setting given at the value a layer above already has is left out.
- **Where it goes:** owner, cost center and archetype go in the new workload's `_workload.yaml`; the
  subscription goes in the landing zone's own file.

It writes nothing over a file that is there, and refuses a key another landing zone already has. Then it
runs the landing zone's check. The files go into a pull request: hand the subscription over to vending
first (`docs/bootstrap.md`), unless vending makes it, and the pull request's run gives the landing zone its addresses.

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

## Decommissioning a landing zone

A landing zone is retired by one line in its own file, never by deleting the file (a deleted file leaves
everything vending made in Azure, with no stack left to plan it):

```yaml
# landing-zones/payments/prod.yaml
decommission: true
owner: payments@demo.example
```

- **Its plan destroys everything vending made:** the network, both peerings, the NSGs, the route table, the
  deploy and plan identities and their roles, the budget, and vending's network and identity resource groups,
  with anything else in them. The workload repo's state account stays, in its own group: the team's own
  resources, which that state describes, may outlive the landing zone.
  The subscription moves to `<root>-decommissioned`, where ALZ's policies deny new resources; the team's own
  resource groups stay. The reviewer approves it as any other apply.
- **A subnet still in use stops it.** A team's own resource in a subnet, such as a private endpoint or a
  VM's network interface, stops the destroy until it is removed; then run the workflow again.
- **Only in that landing zone's own file**, and `true` or not there at all: in a layer above, it would retire
  every landing zone below.
- **Its range stays held:** the IP allocator gives it to no one, and vending's contract lists the landing zone
  as held. The platform drops its firewall rules from Azure on its next run, before the range can go to
  another landing zone, and its rules file only warns that it can be deleted (`docs/platform.md`).
- **Then the files go, in pull requests a person merges.** Once the destroy is applied, vending's pipeline
  (its `decommission` job, `python3 -m mlz decommission`) opens one in the platform repo that deletes
  `firewall/landing-zones/<key>.yaml`, when there is one. Once that is merged, and the platform's apply has
  taken the landing zone's rules out of Azure (the job reads every rule collection group of every hub's
  firewall policy, since shared rules are in each: none still changing or failed, none with the landing zone's collections, no rule with its
  range as source or destination; deleting the file alone does not take them out), it opens one here that
  deletes the landing zone's file, and `_workload.yaml` when it was the workload's last landing zone, and
  releases its range in the IP plan, and its names. In that order: released, the range may go to a new landing zone, which
  must not inherit the old one's rules, and the landing zone leaves vending's contract, so a rules file still
  there would stop the platform's check. The job runs after every run on `main` in which the landing zones are
  as `main` says, the daily check included, so each pull request follows within a day of what it waits for.
  An open pull request is left as it is. Both come from the MLZ App, on branches `mlz/decommission-<key>`.

## The subscription

A landing zone's subscription is handed over, or made by vending:

- **Handed over:** `subscription: <id>` in the landing zone's own file. Someone makes it and moves it into the tree
  before the file is merged (`docs/bootstrap.md`); vending places it in its management group and changes nothing
  else about it.
- **Made by vending:** no `subscription`, and a `billing_scope`, usually once for every landing zone in
  `_defaults/all.yaml`. Vending's apply makes the subscription there, straight into its management group,
  named from the template (`sub-acme-pay-pd-nwe-01`, its alias and display name, recorded in `_generated/names.yaml`,
  `docs/naming.md`), tagged as the landing zone, as a Production subscription. The plan shows it to be made, and
  everything in it waits for its ID. A landing zone may still set `subscription` to hand one over instead.

```yaml
# landing-zones/_defaults/all.yaml
billing_scope: /providers/Microsoft.Billing/billingAccounts/1234567/enrollmentAccounts/7654321
```

| Agreement | `billing_scope` | Who may let vending make subscriptions there |
|---|---|---|
| Enterprise Agreement (EA) | `/providers/Microsoft.Billing/billingAccounts/<account>/enrollmentAccounts/<enrollment account>` | The enrollment account's owner, its Account Owner (Microsoft's role table calls it "enrollment account owner (EA admin)"). They give vending's apply identity the EA role SubscriptionCreator on the enrollment account, through the billing API: there is no portal page for it. |
| Microsoft Customer Agreement (MCA) | `/providers/Microsoft.Billing/billingAccounts/<account>/billingProfiles/<profile>/invoiceSections/<section>` | An owner of the invoice section, its billing profile or the billing account. They give vending's apply identity the role Azure subscription creator on the invoice section, in the portal (Cost Management + Billing, the invoice section, Access control (IAM)) or through the billing API. |
| Microsoft Partner Agreement (CSP) | `.../customers/<customer>` | Refused: a CSP customer's subscription is made from Fortytwo's Partner Center, then handed over. |

These are billing roles, not Azure roles: Azure's role assignments and Entra ID's directory roles neither give nor
show them. Until vending's apply identity has the role, the apply that would make the subscription fails, and
nothing else in that landing zone is made.

**Azure makes vending's apply identity the new subscription's Owner**, besides the rights it has from the tree.

**Vending never cancels a subscription it made.** Decommissioned, it moves to `<root>-decommissioned` like a
handed-over one ([below](#decommissioning-a-landing-zone)); cancelling it is the customer's call, in Azure. Two
changes do nothing or are refused:

- **A changed `billing_scope`** moves no subscription already made: Azure bills it where it was made until its billing
  is transferred there. Only subscriptions made from then on are billed to the new scope.
- **`subscription: <its ID>` added later** would have the module that made the subscription cancel it, so vending's
  plan refuses it ("Instance cannot be destroyed", `terraform_data.made_subscription`). A subscription vending made
  stays vending's.

## The workload repo

Once `github.organization` is set, every landing zone has a workload repo of its own for the team's code, in the
customer's GitHub organization, unless its `github.repo` names an existing one or `github.create` is `false`.

- **Its name**, from `customer.yaml`'s `naming.repository` (default `{app}-{env}`, with long names:
  `payments-prod`), recorded with the landing zone's other names (`docs/naming.md`).
- **The repo**, made by vending's Terraform, as the customer's MLZ App: private, squash merges only, its default
  branch `main` whatever the organization's default is. Decommissioned, it is archived, never deleted: the team's
  code and history stay.
- **Its team**: `github.team`, required for a repo vending makes, set once per workload in `_workload.yaml`. The team
  maintains the repo, and is the reviewer of its `apply` environment, which only `main` may use. Every apply in MLZ
  waits for a reviewer, which GitHub allows in a private repo only on GitHub Enterprise (`docs/bootstrap.md`).
- **Its identities**: a plan identity, `id-acme-pay-plan-pd-nwe-01`, Reader on the subscription, trusted for the
  repo's pull requests; the deploy identity, Contributor, trusted for its `apply` environment. Both trust GitHub's
  immutable subjects, `repo:<organization>@<id>/<repo>@<id>`, as the role repos' identities do, so a renamed or
  recreated repo inherits neither.
- **Its Terraform state**, in a storage account in the landing zone's own subscription, `stacmepaystatepdnwe01`
  in `rg-acme-pay-state-pd-nwe-01`: Entra ID only, versioned, 30 days of soft delete, container `tfstate`. The plan
  identity reads it, the deploy identity writes it. It is never replaced (`prevent_destroy`) and outlives a
  decommission ([above](#decommissioning-a-landing-zone)).
- **Its variables**: `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID` (the plan identity; inside the
  `apply` environment, the deploy identity) and `TFSTATE_RESOURCE_GROUP`, `TFSTATE_STORAGE_ACCOUNT`,
  `TFSTATE_CONTAINER`. No secrets.

An existing repo (`github.repo`) is the team's own: vending makes nothing in it, and its identities trust it the same
way, so its workflows sign in from its `apply` environment and its pull requests.

**Its files.** After the apply that made it, vending's pipeline gives the repo mlz-core's workload starter
(`starter/mlz-workload`), in one commit by the MLZ App (`python3 -m mlz workload-repos`, `tools/mlz/workload.py`):

- `.github/workflows/mlz.yml`, which calls mlz-core's `workload.yml` at the release vending runs on. Every pull request
  and every push to `main` plans, as the plan identity, from a read-only copy of the state; on `main` the apply then
  waits for the team in `apply`, and applies, as the deploy identity, only the changes that plan showed. The scripts
  are the role repos' own (`tools/ci/plan.sh`, `apply.sh`).
- `terraform.tf`, with the state's backend; `variables.tf`, declaring `mlz`; `main.tf`, with an example named through
  mlz-core's naming helper (`modules/naming`, `docs/naming.md`); a README.
- **`mlz.auto.tfvars.json`**: the landing zone's key, region and naming values (the customer's code, the workload's
  short name, the environment's and region's codes, the templates). Its subscription is the one the pipeline signs in
  to, `AZURE_SUBSCRIPTION_ID`. Vending writes it again whenever they
  change, so a team never types them.

The starter goes in once, and adds only files that are not there: nothing the team wrote is replaced, and after that
only `mlz.auto.tfvars.json` is written. None of it is in Terraform's state, so none of it is deleted when the landing
zone is decommissioned and its repo archived.

The workload repos are in the organization the vending repo is in: the pipeline's App tokens are for that one. The
root's `landing_zone` output names the identities and the state.

## Operating notes

- **A new landing zone settles on its second run.** Sub-vending records whether the subscription is already
  in its management group as part of the plan. On the first apply it is not (placing it is that apply's
  job), so the next plan re-places it in the same group once, and from then on plans show no changes.
  Harmless; met in the canary on 2 November 2026.
- **Subscriptions must be writable.** A subscription that is disabled or read-only (for CSP, the "Warned"
  state) fails at its first resource group with `ReadOnlyDisabledSubscription`, after it has already been
  placed in its management group. Check the subscription's state before vending it.
