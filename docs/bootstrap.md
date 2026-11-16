# Bootstrap

The one-time setup a customer's pipelines run on. A person at Fortytwo runs it once per customer, before
the first pipeline run; running it again changes only what differs. It is `bootstrap/terraform`, and it
reads two files from the customer's governance repo: `customer.yaml` (root ID, display name, primary
region, platform subscriptions) and `bootstrap.yaml` (GitHub repos, platform team, reviewers, state operators).

```yaml
# bootstrap.yaml
github:
  organization: demo-org
  repositories:                    # the three repos made from mlz-core's templates
    governance: mlz-governance
    platform: mlz-platform
    vending: mlz-vending
  platform_team: platform          # the GitHub team that owns the three repos (their CODEOWNERS)
  reviewers: [demo-platform-lead]  # GitHub users who approve every apply
  runners:                         # where the pipelines' Azure jobs run (mlz install runners)
    kind: self-hosted              # self-hosted (the default) or github-hosted
    size: Standard_D2ds_v6         # an Azure VM size (the default); for github-hosted, GitHub's: 2-core, 4-core
    instances: 1                   # self-hosted: how many runners (at most 59), each billed while it exists
    app_client_id: Iv23liExample   # self-hosted: the client ID of the customer's runner App

state_operators:                   # people who may run Terraform by hand, by Entra object ID
  platform-lead: 00000000-0000-0000-0000-000000000010
```

## What it creates

| Where | What |
|---|---|
| Tenant | The top management group, `<root>`, below the tenant root or `parent_management_group_id`. Governance builds everything below it, so its pipeline never needs rights at the tenant root. |
| Management subscription | `rg-acme-mlz-pd-nwe-01`, holding the state account, the six pipeline identities and the self-hosted runners. `rg-acme-mgt-net-pd-nwe-01`, empty, for management's network, which the platform builds. `GitHub.Network` registered, for GitHub-hosted runners in that network. |
| Identity subscription | `rg-acme-idn-net-pd-nwe-01`, empty, for identity's network, when `customer.yaml` names an identity subscription. |
| State account | `stacmemlzstatepdnwe01`: Entra ID only (no account keys, no key-signed SAS), versioning, 30 days of soft delete for blobs and containers. One container per role, plus `bootstrap` for its own state. The public endpoint stays on until the private runners use the private endpoint. Never replaced: see [names](#names). |
| State account's private endpoint | `pep-acme-mlz-state-pd-nwe-01`, beside the account, in management's `snet-private-endpoints`, with its name in the hub's `privatelink.blob.core.windows.net` zone, and that zone linked to management's network, so the name resolves there whether management asks the hub or, behind a Basic firewall without a resolver, Azure's own DNS. Made on the first run after the platform has built that network and zone ([below](#running-it-again)); until then there is none. |
| Runners' vault | `kv-acme-mlz-pd-nwe-01`, for self-hosted runners: the runner App's private key and nothing else. Roles only, given to nobody but the runners' identity; purge protection; a firewall that admits no address, and a private endpoint in management's `snet-private-endpoints` (`pep-acme-mlz-vault-pd-nwe-01`) with its name in the hub's `privatelink.vaultcore.azure.net` zone. Made on the same run as the state account's endpoint; set `names.key_vault` for a name taken elsewhere. |
| Self-hosted runners | `vmss-acme-mlz-pd-nwe-01` in management's `snet-runners`, with `id-acme-mlz-runners-pd-nwe-01` and `nsg-acme-mlz-runners-pd-nwe-01` ([az-tf-mlz-github-runners](https://github.com/fortytwoservices/az-tf-mlz-github-runners)): made once the runner App's key is in the vault ([private runners](#private-runners)). |
| Connectivity subscription | The custom role `<root> hub peering`: read the hubs and create, change or delete peerings, nothing else. |
| GitHub, per repo | The environment the role applies in, which waits for a reviewer and accepts only `main`; the repository variables the workflows read. |

## Names

Every name here comes from the customer's naming template, worked out by `mlz install` on every run from
`customer.yaml`'s `naming:` (`docs/naming.md`): the bootstrap is the platform's `mlz` part, in `prod`, in the primary
region. The examples above are for a customer whose code is `acme`. `mlz install bootstrap` writes them to
`names.json` in its work folder, and the root reads them from there; `mlz install check` shows the state account's.

- **The state account is never replaced.** Every role's state is in it, so it has `prevent_destroy`: a changed
  template, or a changed customer code, that would rename it stops the plan with an error instead. To keep the
  account it has, set its name in `bootstrap.yaml`:

  ```yaml
  names:
    storage_account: stacmemlzstatepdnwe01   # also for a name another tenant already took: they are global
  ```

- **`names.resource_group`** does the same for the bootstrap's resource group, and **`names.key_vault`** for the
  runners' vault, whose name is global too, and at most 24 characters.
- The identities and the networks' groups follow the template: renamed, they are replaced on the next run, which
  shows them in its plan for the person running it to approve.

## The pipeline identities

One read-only identity per role for pull request plans, and one for approved applies, named
`id-acme-mlz-govplan-pd-nwe-01`, `id-acme-mlz-govapply-pd-nwe-01` and so on (`plat`, `vend`). A plan identity trusts the repo's pull requests, and `main` for
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

## GitHub Enterprise

MLZ needs the customer's GitHub organization on **GitHub Enterprise**. Every apply waits for a reviewer: the role
repos' environments, and the landing zones' workload repos' `apply` environments. GitHub allows required reviewers
on an environment in a private repo only on Enterprise; on Free, Pro and Team only in public repos (GitHub Docs,
"Managing environments for deployment", availability). Tested on 13 November 2026 in a test organization on Free:
GitHub refused the reviewer ("Please ensure the billing plan supports the required reviewers protection rule").
`mlz install check` reads the organization's plan and refuses another one; GitHub shows the plan only to its
owners, so for anyone else it says so and goes on.

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
   - Each gets the ruleset `main`: changes only through pull requests, a code owner's approval, the pipeline's
     check passed (`platform / check` in the platform repo) on a branch up to date with `main`, no force push, no
     deletion.
   - Each gets `.github/CODEOWNERS`, with `platform_team` owning every file but `_generated/`, and that team gets
     write access, which a code owner needs; a team with more keeps it. The team must exist first, and be visible:
     `CODEOWNERS` skips a secret team. It can take its members from an Entra ID group
     ([pipelines](pipelines.md#code-owners)).
   - A repo that exists keeps its files. If it has no workflow yet, the starter's missing files come as a pull
     request; if it has no `CODEOWNERS`, that file does. A ruleset of its own named `main` is left as it is.
2. **`check`** confirms the organization is on GitHub Enterprise ([above](#github-enterprise)), that the
   customer's MLZ App is set in each repo (`MLZ_RELEASE_APP_CLIENT_ID` and `MLZ_RELEASE_APP_PRIVATE_KEY`), and
   that each repo's code owners decide: the platform team may write, GitHub reads every line of `CODEOWNERS`,
   and an active ruleset requires a code owner's review on the default branch, and the pipeline's check from
   GitHub Actions on a branch up to date with it. Making the App needs a browser, so that step stays manual.
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

Each new landing zone's subscription is handed over by hand, before its YAML is merged (the second command in
`terraform output -raw handover`), unless vending makes it where `billing_scope` says (`docs/vending.md`).

### Letting vending make subscriptions

For a customer whose landing zones get subscriptions made by vending, vending's apply identity needs a billing role
on the billing scope ([vending](vending.md#the-subscription)). After the bootstrap:

```sh
python3 -m mlz install subscriptions --settings $SET --tenant $TENANT --billing-scope <billing_scope>
```

- **It looks first:** if the identity has the role already, it says so and does nothing.
- **Then it offers to give it**, signed in as you (`--yes` without asking): the EA role SubscriptionCreator on the
  enrollment account, or the MCA role Azure subscription creator on the invoice section.
- **Usually only the customer's billing owner may**, so when your sign-in may not, it prints what to send them: the
  role they must hold (the enrollment account's owner; for MCA, an owner of the invoice section, its billing
  profile or the billing account), the `az login` and `az rest` commands filled in with the identity, the tenant
  and the scope, the portal route where there is one (MCA), and a command to check afterwards. Running the step
  again checks too.

The EA assignment is named after the scope and the identity, so sending the command twice gives the role once.

### Private runners

The pipelines' Azure jobs (`plan`, `apply`, `publish`) move onto runners in management's network once the
platform's first apply has built it and the bootstrap has run again, for the state account's private endpoint and
the runners' vault ([below](#running-it-again)): `mlz install runners --settings <dir> --tenant <id>`. Then the
state is reached through its private endpoint. `bootstrap.yaml`'s `github.runners` says which kind of runner.

#### Self-hosted runners, the default

Fortytwo's runner image from Azure Marketplace on a scale set, `vmss-acme-mlz-pd-nwe-01` in `snet-runners`, which
the bootstrap deploys with [az-tf-mlz-github-runners](https://github.com/fortytwoservices/az-tf-mlz-github-runners),
never a pipeline that runs on it. Each instance registers with GitHub when it boots, as the customer's **runner
App**, and reads the App's private key from the runners' vault with an identity that may read that one secret.

1. **An owner of the customer's organization makes the runner App** in the browser (the organization's
   Settings, Developer settings, GitHub Apps): no webhook, one permission, **Organization > Self-hosted runners:
   write**, installed on the organization. It is a separate App from the MLZ App on purpose: a job on the
   runners can reach its key, so the key may do nothing but manage runners. Its client ID goes into
   `bootstrap.yaml` as `github.runners.app_client_id`.
2. **The same person downloads its private key** (Generate a private key, on the App's page) and runs, in one
   sitting, `mlz install runners --settings <dir> --tenant <id> --app-key <the .pem file>`, then deletes the
   file. **The key is never sent to anyone,** by mail, chat or otherwise: whoever runs the step holds the file
   for minutes, and the vault is its only copy. The step needs an owner of the organization, and Owner on the
   management subscription; the organization owner may be a Fortytwo engineer during the install, or the
   customer's owner runs this one step.
3. **What the step does,** in order, stopping where something is missing and saying what:
   - In GitHub, the **runner group `mlz`**: only the three repos may use it, and only jobs defined in
     mlz-core's `role.yml` at the releases the repos pin (`fortytwoservices/mlz-core/.github/workflows/role.yml@refs/tags/<release>`);
     a repo pinned to anything but a release of mlz-core stops the step.
     A pull request that adds a job of its own to `mlz.yml`, or points it at another `role.yml`, gets no runner
     (decided 2026-11-16; the first customer install is the first test of it). Run by anyone but an owner, the
     step changes nothing and prints the command for one. A group `mlz` made for GitHub-hosted runners is not
     taken over.
   - **The key, into the vault** as `github-runner-app-key`, straight to Key Vault: never through Terraform, so
     never in a state or a plan, and never printed. For the write alone, the person running it holds Key Vault
     Secrets Officer on the vault and their address passes its firewall; both are taken away again whatever
     happens, and the bootstrap's next apply takes out any address left behind. Without `--app-key`, a key
     already in the vault is kept.
   - **The management subscription,** once: the terms of the image's plan accepted, and encryption at host
     registered. That registration takes minutes; the step says so and stops, and a run again goes on.
   - **The bootstrap's apply,** which now makes the runners: `runners.instances` of `runners.size` (at most 59, as many as
     `snet-runners` holds), its plan
     shown and applied as in `mlz install bootstrap` (`--yes` without asking).
   - Once a runner is online and the state account has its private endpoint, **`MLZ_RUNS_ON`** is set to `mlz`
     in the three repos, and their next runs plan, apply and publish on the runners
     ([pipelines](pipelines.md#what-runs-when)). Until then, the step says what it waits for: run it again.

Running it again changes only what is missing. **A new key** (rotation): generate one on the App's page, run the
step with `--app-key`, delete the old key there. **A change to `size` or `instances`:** `mlz install bootstrap`.
Instances already running keep their model until reimaged (`az vmss reimage`, while no job runs on them); a
changed instance count takes effect at once.

**Upgrades:** `mlz upgrade` lets the runner group run the target release's `role.yml` before it opens the
upgrade's pull request, whose jobs run that release; the releases the group admits already stay, for the repos
not yet moved. Changing a runner group needs an owner's `admin:org` scope: anyone else is told what to add.

**Cost:** each instance is billed for its compute and for Fortytwo's hourly image fee from the moment it exists,
whether or not a job runs; `instances: 0` removes them all and stops both.

#### GitHub-hosted runners: `kind: github-hosted`

GitHub's own runners, joined to management's network (`snet-github-runners`). Nothing of the customer's runs
them, and nothing is billed while no job runs, but GitHub offers them in some regions only, and MLZ has not run
them yet. `mlz install runners --settings <dir> --tenant <id>`:

- In the customer's organization it makes a network configuration `mlz`, naming the network settings
  resource the platform made (`ghns-acme-mgt-pd-nwe-01`), a runner group `mlz` that only the three
  repos may use, and a runner `mlz` in it: Ubuntu, `runners.size` from `bootstrap.yaml` (2-core unless set),
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

GitHub runs these runners in customers' networks only in some regions ([platform](platform.md#github-hosted-runners)):
in Europe, Norway East, Sweden Central, France Central, Switzerland North and UK South and West. Elsewhere
the step stops, and the pipelines stay on GitHub's public runners.

If the organization belongs to a GitHub Enterprise account, organizations cannot make network
configurations until an enterprise owner allows it, once: the enterprise's Settings, Policies, Hosted
compute networking, then Enable. An organization on GitHub Team needs nothing from anyone. The runners
are GitHub's larger runners, which need GitHub Team or Enterprise Cloud.

To go back to GitHub's public runners, whichever the kind, delete the variable: `gh variable delete MLZ_RUNS_ON --repo <repo>`.

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
