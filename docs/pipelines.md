# Pipelines

Each customer repo runs its role from mlz-core with one short workflow, `.github/workflows/mlz.yml`
(the starters have it), pinned to a release:

```yaml
jobs:
  governance:
    uses: fortytwoservices/mlz-core/.github/workflows/role.yml@v0.1.4
    with:
      role: governance
    secrets: inherit
```

Upgrading is a pull request that changes the release. The workflow checks out mlz-core's code at the exact
commit it came from (it reads which one from the run's own OIDC token), so code and workflow never drift
apart. The steps themselves are small scripts in `tools/ci/`.

## What runs when

| Job | When | What it does |
|---|---|---|
| `check` | every run | Checks the YAML and works out what the roots read: governance renders its library, platform writes its config, vending checks the IP plan is current (`mlz allocate --check`) and writes one config per landing zone. Fails with every problem listed. |
| `plan` | pull requests, `main`, and daily | One plan per stack (the role, or each landing zone for vending), as the read-only plan identity. Its summary goes on the run page and, for a pull request, in one comment per stack that later runs update; the full plan is attached to the run. The daily run fails when Azure has drifted from `main`. |
| `apply` | `main` | Waits for a reviewer in the role's environment. Then plans each stack as the apply identity and applies only if that plan makes exactly the changes in the reviewed plan of the same run; otherwise it stops and applies nothing. Governance and platform then offer their new contract to the repos that read it (below). |

The summary is short enough to read: the counts, the changes by resource type, and every change to or
removal of an existing resource by name. A reviewer approving an apply sees the plan of that very run on
its page, and that plan is what gets applied, or nothing is.

The plan identities can only read the state, and Terraform's Azure backend writes an empty state the
first time it meets a new key, so a plan runs against a downloaded copy of its state with a local
backend. Only the apply job uses the backend itself.

Applies of the same stack never run at the same time, and one is never cancelled halfway.

## Contracts between the repos

After an apply, governance's contract goes to the platform and vending repos, and platform's to the
governance and vending repos, each as a pull request that updates `_generated/<role>.yaml`. A person sees
what changed before the other role uses it; merging the pull request runs that role's pipeline with it.
If the contract did not change, nothing is opened. The pull requests come from a GitHub App that may write
to the three repos.

## What a repo needs

| Setting | From |
|---|---|
| Variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`, `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER`, `MLZ_REPOSITORIES`; the environment named after the role, with its reviewer and its own `AZURE_CLIENT_ID` | the bootstrap |
| Variable `AZ_TF_MODULE_READER_CLIENT_ID`, secret `AZ_TF_MODULE_READER_PRIVATE_KEY` | the app that reads mlz-core and the `az-tf-mlz-*` modules, while they are internal |
| Variable `MLZ_RELEASE_APP_CLIENT_ID`, secret `MLZ_RELEASE_APP_PRIVATE_KEY` | the app that opens the contract pull requests; installed on the three repos |

For customers, creating and installing these apps is part of the install: nothing deploys until they are
in place. mlz-core's Actions access must allow the customer's repos to call its workflows (for internal
use, "accessible from repositories in the organization").

## The first run

The roles depend on each other's contracts, so a new customer goes in this order:

1. **Governance** (needs the bootstrap's contract): the tree, the policies, central management. It offers
   its contract to platform and vending.
2. **Platform** (needs governance's): the hubs. It offers its contract to governance and vending.
3. **Governance again**, with platform's contract: the Private Link DNS policy now points at the real
   zones.
4. **Vending** (needs both): the landing zones. A new landing zone's subscription is handed to vending
   first (`docs/bootstrap.md`).

From then on, each repo's pull requests and merges run on their own.
