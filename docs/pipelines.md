# Pipelines

Each customer repo runs its role from mlz-core with one short workflow, `.github/workflows/mlz.yml`
(the starters have it), pinned to a release:

```yaml
jobs:
  governance:
    uses: fortytwoservices/mlz-core/.github/workflows/role.yml@v0.3.3 # x-release-please-version
    with:
      role: governance
    secrets:
      MLZ_RELEASE_APP_PRIVATE_KEY: ${{ secrets.MLZ_RELEASE_APP_PRIVATE_KEY }}
```

mlz-core and the `az-tf-mlz-*` modules it uses are public, so a customer's repo needs no Fortytwo credential to
run them. The secret is passed by name because `secrets: inherit` only reaches a workflow in the caller's own
organisation or enterprise.

Each mlz-core release moves the starters' pin to itself. The starters' workflow also decides which runs
wait for which:

- **Runs on `main` go one at a time**, first in first out (a concurrency group with `queue: max` on the job
  that calls `role.yml`). A run starts only when the one before it has finished, review included, so it
  plans against what that run applied and the plan its reviewer approves is the plan that applies. Two
  runs therefore never apply at once, not even different stacks of one role.
- **A pull request's newer push cancels its older run**, so an older plan never replaces the newer one's
  comment.
- **The daily drift check never waits** and is never waited for: it only plans, against a copy of the state. The workflow checks out mlz-core's code at the exact
commit it came from (it reads which one from the run's own OIDC token), so code and workflow never drift
apart. The steps themselves are small scripts in `tools/ci/`.

## Upgrading a customer

A customer stays on its release until Fortytwo decides to move it. Then an engineer runs, from an mlz-core
checkout:

```sh
PYTHONPATH=tools python3 -m mlz upgrade acme/mlz-governance acme/mlz-platform acme/mlz-vending --to v0.1.8
```

Each repo gets one pull request, `chore: mlz-core v0.1.8`, on branch `mlz/upgrade-v0.1.8`:

- It replaces the files that belong to mlz-core with the release's starter for the repo's role:
  `.github/workflows/mlz.yml`, so changes on the caller's side ship with the release, and the editor
  settings in `.vscode/`, so the editor reads the schemas of the release the repo runs. Settings go in the
  YAML. Going back to a release from before the editor settings removes them.
- Its description carries the release notes between the two releases, breaking changes first.

`--to` defaults to the latest release, an older release is a rollback (the description says so), and
`--dry-run` shows the change and the text without opening anything. Running it again while the pull
request is open changes nothing.

Merging changes nothing in Azure. The pipeline plans with the new release, and the apply waits for the
repo's reviewer, so the three repos can move on different days, platform last. The command runs on the
engineer's own GitHub login, which needs write access to the repos and the `workflow` scope
(`gh auth refresh -h github.com -s workflow`), since the pull request changes a workflow file.

## What runs when

| Job | When | What it does |
|---|---|---|
| `check` | every run | Checks the YAML and works out what the roots read: governance renders its library, platform writes its config, vending writes one config per landing zone. Fails with every problem listed. On a vending pull request it first brings the IP plan up to date: when a new landing zone or subnet needs addresses, it commits the new plan to the pull request and stops, and the run of that commit plans with them. On `main` a plan that is not up to date fails the check (`mlz allocate --check`). |
| `plan` | pull requests, `main`, and daily | One plan per stack (the role, or each landing zone for vending), as the read-only plan identity. Its summary goes on the run page and, for a pull request, in one comment per stack that later runs update; the full plan is attached to the run. The daily run fails when Azure has drifted from `main`. A plan that deletes or replaces a firewall policy is refused (below). |
| `changed` | `main` | Picks the stacks whose reviewed plan changes something: a resource, or an output such as the contract. Only those go on to `apply`; a stack with nothing to change asks for no approval, and the run page says which were skipped. |
| `apply` | `main` | Waits for a reviewer in the role's environment. Then plans each stack as the apply identity and applies only if that plan makes exactly the changes in the reviewed plan of the same run, to the same values; otherwise it stops and applies nothing. A job per landing zone; the platform's changed stacks in one job, one after the other (hub, management, identity, rules), stopping at the first that fails. |
| `publish` | `main` | After every run in which nothing failed, applied or not: offers the role's contract to the repos that read it (below). Governance and platform read theirs from the state as the plan identity; vending's comes from its IP plan. |
| `merge` | contract pull requests | Merges the pull request once `check` and `plan` have passed on it (below). |
| `drift` | daily | Keeps one issue in the repo, "Daily check: Azure differs from main": opened, or commented on, when the check fails, or a stack's plan differs from `main` or did not finish (refused, or stopped before planning); closed by the first daily check that finds every stack as `main` says. An issue of that title opened by a person is left alone. |

The summary is short enough to read: the counts, the changes by resource type, and every change to or
removal of an existing resource by name. A reviewer approving an apply sees the plan of that very run on
its page, and that plan is what gets applied, or nothing is.

The plan identities can only read the state, and Terraform's Azure backend writes an empty state the
first time it meets a new key, so a plan runs against a downloaded copy of its state with a local
backend. Only the apply job uses the backend itself.

Applies of the same stack never run at the same time, and one is never cancelled halfway.

`plan`, `apply` and `publish` reach Azure and the state. They run on the repo variable `MLZ_RUNS_ON` when it is
set: the private runner in management's network, which `mlz install runners` sets up and switches the
repos to ([bootstrap](bootstrap.md#private-runners)). Until then, and once it is deleted again, they run on
GitHub's public runners. `check`, `changed` and `merge` never reach Azure and always run on GitHub's public
runners.

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
repos, platform's to the vending repo, and vending's to the platform repo, each as a pull
request that updates `_generated/<role>.yaml`. If the contract did not change, nothing is opened, and a pull
request still open from an earlier run is closed: it no longer matches what was deployed. A pull request
still open with an older contract gets the new one on its branch. Publishing runs even when nothing was
applied, so a publication that failed is retried by the next run.

The reading repo's run on that pull request merges it once `check` and `plan` have passed on it. A person's
merge would add no control: the contract says what the other role has already deployed, and the run on
`main` that the merge starts still waits for this role's reviewer, whose plan shows what the contract
changes. A contract that fails this repo's check or plan stays on its pull request, not on `main`. On
vending, a contract that gives landing zones their addresses gets the IP plan committed to the pull request
first, and the run of that commit merges it. Until vending has both governance's and platform's contracts,
it waits: its check plans nothing, and it publishes nothing.

A contract pull request stays open for a person when its run fails, when it changes more than
`_generated/`, or when the repo's own rules refuse the merge, for example a ruleset that asks for a review:
that is how a customer keeps contracts for a person to merge. The run page says which. Once what stopped a
run is fixed, re-running its failed jobs runs the merge after them. The pull requests come from a GitHub
App that may write to the three repos, and the App merges them.

## What a repo needs

| Setting | From |
|---|---|
| Variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`, `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER`, `MLZ_REPOSITORIES`; the environment named after the role, with its reviewer and its own `AZURE_CLIENT_ID` | the bootstrap |
| Variable `MLZ_RELEASE_APP_CLIENT_ID`, secret `MLZ_RELEASE_APP_PRIVATE_KEY` | the app that opens and merges the contract pull requests and commits the IP plan to vending pull requests; installed on the three repos |
| Variable `MLZ_RUNS_ON` (optional) | `mlz install runners`: the private runner the Azure jobs run on |

For customers, creating and installing this app is part of the install: nothing deploys until it is in
place. If the customer's organisation allows only selected actions and reusable workflows, its Actions
policy must allow `fortytwoservices/mlz-core/.github/workflows/role.yml` and the actions it uses (each is
pinned by commit in `role.yml`).

## The first run

The roles depend on each other's contracts, so a new customer goes in this order. The contracts move on by
themselves; what a person does is approve each apply, and hand over landing zone subscriptions.

1. **Governance** (needs the bootstrap's contract): the tree, the policies, central management. It offers
   its contract to platform and vending.
2. **Platform** (needs governance's): the hubs, then management's and identity's networks. It offers its
   contract to vending.
3. **Vending** (needs both): the landing zones. A new landing zone's subscription is handed to vending
   first (`docs/bootstrap.md`).

From then on, each repo's pull requests and merges run on their own.
