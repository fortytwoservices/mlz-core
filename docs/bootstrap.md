# Bootstrap

The one-time setup a customer's pipelines run on. A person at Fortytwo runs it once per customer, before
the first pipeline run; running it again changes only what differs. It is `bootstrap/terraform`, and it
reads two files from the customer's governance repo: `customer.yaml` (root ID, display name, primary
region, platform subscriptions) and `bootstrap.yaml` (GitHub repos, reviewers, state operators).

```yaml
# bootstrap.yaml
github:
  organization: demo-org
  repositories:                    # the three repos made from mlz-core's templates
    governance: mlz-governance
    platform: mlz-platform
    vending: mlz-vending
  reviewers: [demo-platform-lead]  # GitHub users who approve every apply
  # runner_size: 4-core            # the private runner's size at GitHub (mlz install runners); 2-core if unset

state_operators:                   # people who may run Terraform by hand, by Entra object ID
  platform-lead: 00000000-0000-0000-0000-000000000010
```

## What it creates

| Where | What |
|---|---|
| Tenant | The top management group, `<root>`, below the tenant root or `parent_management_group_id`. Governance builds everything below it, so its pipeline never needs rights at the tenant root. |
| Management subscription | `rg-<root>-bootstrap-<region>`, holding the state account and the six pipeline identities. `rg-<root>-management-network-<region>`, empty, for management's network, which the platform builds. `GitHub.Network` registered, for GitHub's hosted runners in that network. |
| Identity subscription | `rg-<root>-identity-network-<region>`, empty, for identity's network, when `customer.yaml` names an identity subscription. |
| State account | `st<root>state<4 hex>`: Entra ID only (no account keys, no key-signed SAS), versioning, 30 days of soft delete for blobs and containers. One container per role, plus `bootstrap` for its own state. The public endpoint stays on until the private runners use the private endpoint. |
| State account's private endpoint | `pep-<root>-state-<region>`, beside the account, in management's `snet-private-endpoints`, with its name in the hub's `privatelink.blob.core.windows.net` zone, and that zone linked to management's network, so the name resolves there whether management asks the hub or, behind a Basic firewall without a resolver, Azure's own DNS. Made on the first run after the platform has built that network and zone ([below](#running-it-again)); until then there is none. |
| Connectivity subscription | The custom role `<root> hub peering`: read the hubs and create, change or delete peerings, nothing else. |
| GitHub, per repo | The environment the role applies in, which waits for a reviewer and accepts only `main`; the repository variables the workflows read. |

## The pipeline identities

One read-only identity per role for pull request plans, and one for approved applies, named
`id-<root>-<role>-<plan|apply>-<region>`. A plan identity trusts the repo's pull requests, and `main` for
the scheduled drift check; an apply identity trusts only its role's environment. An unmerged branch can
never change Azure.

| Identity | Rights from the bootstrap | Rights from governance |
|---|---|---|
| `governance-plan` | Reader on `<root>`; reads the `governance` container | |
| `governance-apply` | Owner on `<root>`; writes the `governance` container | |
| `platform-plan` | Reader on the connectivity subscription and the network resource groups in management and identity; reads the `platform` container | |
| `platform-apply` | Contributor on the connectivity subscription and the network resource groups in management and identity; writes the `platform` container | |
| `vending-plan` | Reader on the connectivity subscription; reads the `vending` container | Reader on Landing Zones and Sandboxes |
| `vending-apply` | Hub peering on the connectivity subscription; writes the `vending` container | Owner on Landing Zones and Sandboxes |

Governance holds Owner on the whole tree, the ALZ accelerator's standard: ALZ's policies need it to give
their own managed identities roles. Vending's rights on Landing Zones and Sandboxes come from governance,
because those groups do not exist until governance creates them; it reads the identities from
`_generated/bootstrap.yaml`.

Each landing zone also gets an identity of its own from vending, `id-<workload>-<env>-deploy-<region>`:
Contributor on its subscription, for its workload repo.

The repository variables are IDs and names, never secrets: `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
(management), `AZURE_CLIENT_ID`, `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER` and `MLZ_REPOSITORIES` (the
three repos, for offering contracts). `AZURE_CLIENT_ID` is
the plan identity for the repo and the apply identity inside its environment, so a workflow always signs
in as the identity its job is trusted for.

The plan identities can only read the state, and Terraform's Azure backend writes an empty state the first
time it meets a new key (a new landing zone, a role's first run), even with `-lock=false`. So a plan job
downloads a copy of its state, when there is one, and plans against that copy with a local backend; only
the apply job, in its environment, uses the backend itself.

## Handing subscriptions to the pipelines

To move a subscription into a management group, Azure asks for rights on the subscription, on the
group it goes to, and on the group it is in now. New subscriptions sit in the tenant's default group
(the tenant root, or a group such as `unmanaged_subscriptions`), where no pipeline has, or should have,
any rights. So a person hands each subscription over once, by moving it into the tree; from there the
pipelines' rights on the tree cover it:

```sh
terraform -chdir=bootstrap/terraform output -raw handover
```

prints the commands:

- **The platform subscriptions**, once, after the bootstrap: into `<root>`. Governance then moves each into
  Management, Connectivity or Identity.
- **Each new landing zone subscription** (for a CSP customer, created in Partner Center), before its YAML is
  merged: into `<root>-landing-zones`. Vending, Owner there, moves it on to Corp, Online or Sandboxes, as its
  YAML says.

Whoever runs them needs Owner on the subscription and rights on the group it is in now, as the person
who created it normally has.

Vending does not create subscriptions yet; it adopts existing ones. For EA and MCA customers it could
create them itself, and then the handover step disappears for them.

## Running it

`mlz install` does it step by step, from an mlz-core checkout. Every step can run again and changes only what
is missing, so a step that stops on an error is simply run again.

It needs:
- the Azure CLI signed in to the customer's tenant as someone who may create the top group (for example a
  Global Administrator with access elevated to the tenant root) and who is Owner on the platform
  subscriptions;
- `gh` signed in with admin rights on the three repos and the `workflow` scope.

`--settings` is the folder with the customer's `customer.yaml` and `bootstrap.yaml`: a checkout of the
governance repo, or, for a new customer, a folder with just the two files.

```sh
export PYTHONPATH=tools
SET=<folder with customer.yaml and bootstrap.yaml>
TENANT=<the customer's Entra tenant ID>

python3 -m mlz install repos     --settings $SET                  # 1
python3 -m mlz install check     --settings $SET                  # 2
python3 -m mlz install bootstrap --settings $SET --tenant $TENANT  # 3
python3 -m mlz install handover  --settings $SET --tenant $TENANT  # 4
python3 -m mlz install publish   --settings $SET --tenant $TENANT  # 5
```

1. **`repos`** creates the three repos named in `bootstrap.yaml`: private, on `main`, each with a signed first
   commit from its starter. The governance repo gets the customer's own `customer.yaml` and `bootstrap.yaml`.
   - Each gets the ruleset `main`: changes only through pull requests, no force push, no deletion.
   - A repo that exists keeps its files. If it has no workflow yet, the starter's missing files come as a pull
     request. A ruleset of its own named `main` is left as it is.
2. **`check`** confirms the customer's MLZ App is set in each repo (`MLZ_RELEASE_APP_CLIENT_ID` and
   `MLZ_RELEASE_APP_PRIVATE_KEY`). Making the App needs a browser, so that step stays manual.
3. **`bootstrap`** runs `bootstrap/terraform` in a folder of the customer's own (`~/.mlz/install/<tenant>/<root_id>`, or
   `--work`), so no customer's state or plan mixes with another's.
   - The first run keeps the state locally, since the account it goes into does not exist yet, then moves the
     state into that account. Your new rights on the account can take minutes to apply, so the move is tried
     again for up to five minutes.
   - Without `--yes` it shows the plan and asks before applying. Only a terminal can answer: piped input or
     a script counts as no, and `--yes` is the way to go ahead without a person.
   - It ends by checking that every identity trusts a subject GitHub actually issues to its repo.
4. **`handover`** moves the platform subscriptions into `<root>`, after asking. A subscription already anywhere
   in the tree stays where it is, so a run after governance never pulls one back out.
5. **`publish`** offers `_generated/bootstrap.yaml` to the governance repo as a pull request. Governance's run
   merges it once it has checked and planned it, and the run on `main` that starts is governance's first
   deployment, which waits for its reviewer; the first run's order is in `docs/pipelines.md`.

Each new landing zone's subscription is still handed over by hand, before its YAML is merged (the second
command in `terraform output -raw handover`).

### Private runners

Once the platform's first apply has built management's network, and the bootstrap has run again for the
state account's private endpoint ([below](#running-it-again)), `mlz install runners --settings <dir>
--tenant <id>` moves the pipelines' Azure jobs onto a GitHub-hosted runner in that network:

- In the customer's organization it makes a network configuration `mlz`, naming the network settings
  resource the platform made (`ghns-<root>-management-<region>`), a runner group `mlz` that only the three
  repos may use, and a runner `mlz` in it: Ubuntu, `runner_size` from `bootstrap.yaml` (2-core unless set),
  at most 10 at a time. A run again changes only what is missing, and narrows the group back to the three
  repos if it was widened.
- Then, once the state account has its private endpoint, it sets `MLZ_RUNS_ON` to `mlz` in the three repos,
  and their next runs plan, apply and publish on the runner ([pipelines](pipelines.md#what-runs-when)).
  Without the endpoint the runner could not reach the state, so the repos stay on GitHub's public runners.
- Only an owner of the organization may make the three, and their `gh` login needs the `admin:org`
  (runner groups), `manage_runners:org` (runners), and `read:network_configurations` and
  `write:network_configurations` scopes. Run by anyone else, the step changes
  nothing and prints the commands to send to an owner, filled in, and then the three commands that switch
  the repos, which an admin on them runs.

GitHub runs these runners in customers' networks only in some regions: of mlz-core's, Norway East and
Sweden Central. Elsewhere the step stops, and the pipelines stay on GitHub's public runners.

If the organization belongs to a GitHub Enterprise account, organizations cannot make network
configurations until an enterprise owner allows it, once: the enterprise's Settings, Policies, Hosted
compute networking, then Enable. An organization on GitHub Team needs nothing from anyone. The runners
are GitHub's larger runners, which need GitHub Team or Enterprise Cloud.

To go back to GitHub's public runners, delete the variable: `gh variable delete MLZ_RUNS_ON --repo <repo>`.

### Running it again

Run `bootstrap` again before the platform's next run when:
- the customer was bootstrapped before mlz-core built management's and identity's networks;
- or an identity subscription was added to `customer.yaml`.

It makes the networks' resource groups, registers `Microsoft.Network` in their subscriptions, and gives the
platform's identities their rights there. Without it, the platform's `management` or `identity` apply fails
for lack of rights.

Run it again after the platform's first apply, too, for the state account's private endpoint: it needs
management's network and the hub's blob zone, which that apply builds. The step says which it found: the
endpoint in place, or still waiting. The private runners reach the state only through it.

A governance deployment applied before the bootstrap existed created the top group itself. Hand the
group over before its next run, or governance plans to delete it (and Azure refuses, since it holds the
rest of the tree):

```sh
terraform -chdir=governance/terraform state rm 'module.alz.azapi_resource.management_groups_level_0["<root>"]'
terraform -chdir=bootstrap/terraform import -var customer_file=$GOV/customer.yaml -var bootstrap_file=$GOV/bootstrap.yaml \
  azapi_resource.top_group /providers/Microsoft.Management/managementGroups/<root>
```

The subjects use GitHub's immutable form, `repo:<organization>@<id>/<repository>@<id>`, which keeps a
renamed or recreated repo from inheriting the identities. If GitHub prints a different prefix, the
organization or repo does not use immutable subjects yet; turn them on before the first pipeline run.

## Tested

- 3 November 2026, in the TCAF canary tenant (root `mlz`, repos `fortytwoservices/mlz-canary-*`): 69
  resources, then a plan with no changes, and the state moved into the account. Checked in Azure: each
  identity holds exactly the rights in the table above, and trusts exactly its subjects. Checked in
  GitHub: each environment waits for the reviewer and accepts only `main`; the repository variables name
  the plan identities and each environment's `AZURE_CLIENT_ID` the apply identity.
- Met on the way: every storage account comes with its blob service, so the bootstrap sets it rather than
  creating it; and Azure stores a built-in role's ID in the subscription's own form when the assignment is
  inside a subscription, so the bootstrap writes it that way to keep plans clean.
