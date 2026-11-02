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

state_operators:                   # people who may run Terraform by hand, by Entra object ID
  platform-lead: 00000000-0000-0000-0000-000000000010
```

## What it creates

| Where | What |
|---|---|
| Tenant | The top management group, `<root>`, below the tenant root or `parent_management_group_id`. Governance builds everything below it, so its pipeline never needs rights at the tenant root. |
| Management subscription | `rg-<root>-bootstrap-<region>`, holding the state account and the six pipeline identities. |
| State account | `st<root>state<4 hex>`: Entra ID only (no account keys, no key-signed SAS), versioning, 30 days of soft delete for blobs and containers. One container per role, plus `bootstrap` for its own state. The public endpoint stays on until the private runners exist. |
| Connectivity subscription | The custom role `<root> hub peering`: read the hubs and create, change or delete peerings, nothing else. |
| GitHub, per repo | The environment the role applies in, which waits for a reviewer and accepts only `main`; the repository variables the workflows read. |

## The pipeline identities

One read-only identity per role for pull request plans, and one for approved applies, named
`id-<root>-<role>-<plan|apply>-<region>`. A plan identity trusts the repo's pull requests, and `main` for
the scheduled drift check; an apply identity trusts only its role's environment. An unmerged branch can
never change Azure.

| Identity | Rights from the bootstrap | Rights from governance |
|---|---|---|
| `governance-plan` | Reader on `<root>` and on each platform subscription; reads the `governance` container | |
| `governance-apply` | Owner on `<root>` and on each platform subscription, until governance has placed them; writes the `governance` container | |
| `platform-plan` | Reader on the connectivity subscription; reads the `platform` container | |
| `platform-apply` | Contributor on the connectivity subscription; writes the `platform` container | |
| `vending-plan` | Reader on the connectivity subscription; reads the `vending` container | Reader on Landing Zones and Sandboxes |
| `vending-apply` | Hub peering on the connectivity subscription; writes the `vending` container | Owner on Landing Zones and Sandboxes |

Governance holds Owner on the whole tree, the ALZ accelerator's standard: ALZ's policies need it to give
their own managed identities roles. Vending's rights on Landing Zones and Sandboxes come from governance,
because those groups do not exist until governance creates them; it reads the identities from
`_generated/bootstrap.yaml`.

Each landing zone also gets an identity of its own from vending, `id-<workload>-<env>-deploy-<region>`:
Contributor on its subscription, for its workload repo.

The repository variables are IDs and names, never secrets: `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
(management), `AZURE_CLIENT_ID`, `TFSTATE_STORAGE_ACCOUNT` and `TFSTATE_CONTAINER`. `AZURE_CLIENT_ID` is
the plan identity for the repo and the apply identity inside its environment, so a workflow always signs
in as the identity its job is trusted for. Plans run with `-lock=false`, because the plan identities can
only read the state.

## Handing a new subscription to vending

Azure lets an identity move a subscription into a management group only if it is Owner on that
subscription. So whoever creates a landing zone subscription (for a CSP customer, in Partner Center)
hands it to vending once, before its YAML is merged:

```sh
terraform -chdir=bootstrap/terraform output -raw handover
```

prints the two commands, with the identities filled in: Owner for `vending-apply`, which places and
builds it, and Reader for `vending-plan`, which plans it. Once the subscription is in Corp, Online or
Sandboxes, vending's rights there cover it.

Vending does not create subscriptions yet; it adopts existing ones. For EA and MCA customers it could
create them itself, and then the handover step disappears for them.

## Running it

Needs the Azure CLI signed in to the customer's tenant as someone who may create the top group (for
example a Global Administrator with access elevated to the tenant root) and who is Owner on the platform
subscriptions, and a GitHub token with admin rights on the three repos.

```sh
cd bootstrap/terraform
export GITHUB_TOKEN=$(gh auth token)
GOV=<path to the customer's mlz-governance checkout>

# 1. The first run keeps its state locally: the account it goes into does not exist yet.
printf 'terraform {\n  backend "local" {}\n}\n' > backend_override.tf   # gitignored
terraform init
terraform apply -var customer_file=$GOV/customer.yaml -var bootstrap_file=$GOV/bootstrap.yaml

# 2. Move the state into the account it created.
terraform output -raw backend_config > backend.hcl
rm backend_override.tf
terraform init -migrate-state -backend-config=backend.hcl

# 3. Check the subjects match what GitHub issues, then publish the contract to the governance repo.
terraform output oidc_subjects
gh api repos/<organization>/<repository>/actions/oidc/customization/sub   # sub_claim_prefix, per repo
terraform output -json contract | PYTHONPATH=../../tools python3 -m mlz contract bootstrap --out $GOV/_generated/bootstrap.yaml
```

The subjects use GitHub's immutable form, `repo:<organization>@<id>/<repository>@<id>`, which keeps a
renamed or recreated repo from inheriting the identities. If GitHub prints a different prefix, the
organization or repo does not use immutable subjects yet; turn them on before the first pipeline run.
