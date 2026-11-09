# Pipelines

Each customer repo runs its role from mlz-core with one short workflow, `.github/workflows/mlz.yml`
(the starters have it), pinned to a release:

```yaml
jobs:
  governance:
    uses: fortytwoservices/mlz-core/.github/workflows/role.yml@v0.1.5 # x-release-please-version
    with:
      role: governance
    secrets: inherit
```

Upgrading is a pull request that changes the release; each mlz-core release moves the starters' pin to
itself. The starters' workflow also decides which runs wait for which:

- **Runs on `main` go one at a time**, first in first out (a concurrency group with `queue: max` on the job
  that calls `role.yml`). A run starts only when the one before it has finished, review included, so it
  plans against what that run applied and the plan its reviewer approves is the plan that applies. Two
  runs therefore never apply at once, not even different stacks of one role.
- **A pull request's newer push cancels its older run**, so an older plan never replaces the newer one's
  comment.
- **The daily drift check never waits** and is never waited for: it only plans, against a copy of the state. The workflow checks out mlz-core's code at the exact
commit it came from (it reads which one from the run's own OIDC token), so code and workflow never drift
apart. The steps themselves are small scripts in `tools/ci/`.

## What runs when

| Job | When | What it does |
|---|---|---|
| `check` | every run | Checks the YAML and works out what the roots read: governance renders its library, platform writes its config, vending checks the IP plan is current (`mlz allocate --check`) and writes one config per landing zone. Fails with every problem listed. |
| `plan` | pull requests, `main`, and daily | One plan per stack (the role, or each landing zone for vending), as the read-only plan identity. Its summary goes on the run page and, for a pull request, in one comment per stack that later runs update; the full plan is attached to the run. The daily run fails when Azure has drifted from `main`. A plan that deletes or replaces a firewall policy is refused (below). |
| `changed` | `main` | Picks the stacks whose reviewed plan changes something: a resource, or an output such as the contract. Only those go on to `apply`; a stack with nothing to change asks for no approval, and the run page says which were skipped. |
| `apply` | `main` | Waits for a reviewer in the role's environment. Then plans each stack as the apply identity and applies only if that plan makes exactly the changes in the reviewed plan of the same run, to the same values; otherwise it stops and applies nothing. |
| `publish` | `main` | After every run in which nothing failed, applied or not: offers the role's contract to the repos that read it (below). Governance and platform read theirs from the state as the plan identity; vending's comes from its IP plan. |

The summary is short enough to read: the counts, the changes by resource type, and every change to or
removal of an existing resource by name. A reviewer approving an apply sees the plan of that very run on
its page, and that plan is what gets applied, or nothing is.

The plan identities can only read the state, and Terraform's Azure backend writes an empty state the
first time it meets a new key, so a plan runs against a downloaded copy of its state with a local
backend. Only the apply job uses the backend itself.

Applies of the same stack never run at the same time, and one is never cancelled halfway.

## A plan that deletes a firewall policy is refused

A hub's firewall policy holds the rules, and the rules are a stack of their own (`firewall`), planned before
the hub applies. If the hub's plan deleted or replaced the policy, every rule in it would go with it while
the rules plan said nothing changes, and the firewall would deny traffic until the rules were written again.
So `tools/ci/summary.py` refuses any plan that deletes or replaces a firewall policy: the summary on the run
page and the pull request starts with **Refused** and names the policy, and the plan job fails, so nothing
can be approved. The apply refuses it too.

In mlz-core the policy changes in place for every setting, tier included; only a new name, resource group or
region replaces it. A release that changes one of those ships its own migration step. Tearing a hub down
does not run through the pipeline.

## Contracts between the repos

After every run on `main` in which nothing failed, governance's contract goes to the platform and vending
repos, platform's to the governance and vending repos, and vending's to the platform repo, each as a pull
request that updates `_generated/<role>.yaml`. A person sees what changed before the other role uses it;
merging the pull request runs that role's pipeline with it. If the contract did not change, nothing is
opened, and a pull request still open from an earlier run is closed: it no longer matches what was
deployed. It runs even when nothing was applied, so a publication that failed is retried by the next run.
The pull requests come from a GitHub App that may write to the three repos.

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
