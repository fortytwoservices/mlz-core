# Pipelines

Each customer repo runs its role from mlz-core with one short workflow, `.github/workflows/mlz.yml`
(the starters have it), pinned to a release, in the organization's own copy of mlz-core (`acme/mlz-core`,
[bootstrap](bootstrap.md#running-it)):

```yaml
jobs:
  governance:
    uses: acme/mlz-core/.github/workflows/role.yml@v0.13.0 # x-release-please-version
    with:
      role: governance
    secrets:
      MLZ_RELEASE_APP_PRIVATE_KEY: ${{ secrets.MLZ_RELEASE_APP_PRIVATE_KEY }}
```

The copy is private: every job reads its code as the MLZ App. The `az-tf-mlz-*` modules mlz-core uses are
public, so a customer's repo needs no Fortytwo credential to run them. A repo set up before the copy calls
`fortytwoservices/mlz-core` itself, which is public, until `mlz upgrade <repo> --core <org>/mlz-core` moves it onto
the copy (at `v0.9.0` or later: older releases read the code without a token). The secret is passed by name,
the one the workflow takes.

Each mlz-core release moves the starters' pin to itself. The starters' workflow also decides which runs
wait for which:

- **Runs on `main` go one at a time**, first in first out (a concurrency group with `queue: max` on the job
  that calls `role.yml`). A run starts only when the one before it has finished, review included, so it
  plans against what that run applied and the plan its reviewer approves is the plan that applies. Two
  runs therefore never apply at once, not even different stacks of one role. Two guards hold besides:
  - **An apply refuses a state that changed after its plan**: the plan records the state's version (its lineage and
    serial, `tools/ci/state_version.py`), and the apply compares it after planning again, while Terraform refuses a
    saved plan whose state changed after it. A hand-made apply in between, for one, stops it; start a manual run.
  - **A vending apply stands down when a newer commit changed its landing zone**: it renders the landing zone's config
    at `main`'s latest commit, and when that differs from what its run planned (a newer change, or the change taken
    back, merged while it waited for its reviewer), it applies nothing and says so; that commit's run, queued behind
    it, plans the landing zone and applies what is left to change.
  Vending keeps its queue for now (decision D3, put off by the user on 19 November): without it, a workload repo's
  values could be written before the change they describe is applied.
- **A pull request's newer push cancels its older run**, so an older plan never replaces the newer one's
  comment.
- **The daily drift check never waits** and is never waited for: it only plans, against a copy of the state. The workflow checks out mlz-core's code at the exact
commit it came from (it reads which one from GitHub's `job.workflow_sha`), so code and workflow never drift
apart. The steps themselves are small scripts in `tools/ci/`.

## Upgrading a customer

A customer stays on its release until Fortytwo decides to move it. Then an engineer in the customer's bypass team
([changes to `.github/`](#changes-to-github)) runs, from an mlz-core checkout:

```sh
PYTHONPATH=tools python3 -m mlz upgrade acme/mlz-governance acme/mlz-platform acme/mlz-vending --to vX.Y.Z
```

A repo that calls the organization's copy of mlz-core (`acme/mlz-core`, made by `mlz install repos`), or that
`--core acme/mlz-core` moves onto it, moves to mlz-core's own release: the command first pushes that release into the copy, from mlz-core itself, and the
pull request's workflow calls the copy at it. The release notes and the starters are mlz-core's. With
self-hosted runners it then lets the runner group run that release's `role.yml` too.

Each repo gets one pull request, `chore: mlz-core vX.Y.Z`, on branch `mlz/upgrade-vX.Y.Z`:

- It replaces the files that belong to mlz-core with the release's starter for the repo's role:
  `.github/workflows/mlz.yml`, so changes on the caller's side ship with the release, and the editor
  settings in `.vscode/` and `.idea/jsonSchemas.xml`, so the editor reads the schemas of the release the repo
  runs. Settings go in the YAML. Going back to a release from before the editor settings removes them.
- It adds to `.gitignore` the starter's lines it lacks, which keep JetBrains' other files in `.idea/` out of the
  repo. The repo's own lines are never changed or removed.
- Its description carries the release notes between the two releases, breaking changes first.

`--to` defaults to the latest release, an older release is a rollback (the description says so), and
`--dry-run` shows the change and the text without opening anything. Running it again while the pull
request is open changes nothing.

`mlz upgrade` moves the three role repos, not the workload repos: once vending's own upgrade is merged, vending's next run on `main` offers each
workload repo it made a pull request to its release, which the owning team merges (`docs/vending.md`, "Its upgrades").

Merging changes nothing in Azure. The pipeline plans with the new release, and the apply waits for the
repo's reviewer, so the three repos can move on different days, platform last. The command runs on the
engineer's own GitHub login, which needs write access to the repos and the `workflow` scope
(`gh auth refresh -h github.com -s workflow`), since the pull request changes a workflow file.

The copy of mlz-core gets no team: only the organization's owners, and whoever made it, may push to it, so the
engineer who runs an upgrade needs write access to it too (an owner gives it, the copy's Settings,
Collaborators and teams). Its tags cannot be moved or deleted by anyone. With self-hosted runners, the runner
group admits only the copy's `role.yml`: `--core fortytwoservices/mlz-core` takes a repo off the runners, so
delete its `MLZ_RUNS_ON` first, or its Azure jobs wait for a runner that never takes them.

## What runs when

| Job | When | What it does |
|---|---|---|
| `check` | every run | Checks the YAML and works out what the roots read: governance renders its library, platform writes its config, vending writes one config per landing zone. Fails with every problem listed. On a vending pull request it first brings the IP plan and the names up to date: when a new landing zone or subnet needs addresses or names, it commits them to the pull request and stops, and the run of that commit plans with them. On `main` a plan or names not up to date fail the check (`mlz allocate --check`). `./mlz check` runs the same checks on a person's own checkout before they push. A pull request merges only once it has passed on a branch up to date with `main`: the ruleset `main-checks` requires `<role> / check`, the starter workflow's job (named after the role) then this one. |
| `plan` | pull requests, `main`, and daily | One plan per stack the run plans ([below](#what-a-run-plans)) (the role's stacks, or each landing zone for vending), as the read-only plan identity. Its summary goes on the run page and, for a pull request, in one comment per stack that later runs update; the full plan is attached to the run. The daily run fails when Azure has drifted from `main`. A plan that deletes or replaces a firewall policy is refused (below). |
| `changed` | `main` | Picks the stacks whose reviewed plan changes something: a resource, or an output such as the contract. Only those go on to `apply`; a stack with nothing to change asks for no approval, and the run page says which were skipped. With something to apply, the run then waits for a reviewer in the role's environment, and every later push or manual run on `main` queues behind it until it finishes (the daily check has a queue of its own): GitHub notifies the reviewers itself, and the run's page says why it waits (what started it, such as vending's contract merging, and what it applies). When a person's pull request started it, a comment there says so too, once; a contract pull request, a push without a pull request and a manual run get none. On GitHub Team nothing waits: the job applies only the plan the merged pull request's run showed, or fails ([below](#approval-on-github-team)). |
| `apply` | `main` | Waits for a reviewer in the role's environment (on Enterprise; on Team, `changed` was the gate). Then plans each stack as the apply identity and applies only if that plan makes exactly the changes in the reviewed plan of the same run, to the same values; otherwise it stops and applies nothing. After an apply that failed half way, re-running the job always stops so, since it compares with the same reviewed plan: start a new run (`gh workflow run mlz.yml --ref main`) for a fresh plan to review. A job per landing zone; the platform's changed stacks in one job, one after the other (`connectivity`, `management`, `identity`, `firewall-rules`), stopping at the first that fails. |
| `publish` | `main` | After every run in which nothing failed, applied or not: offers the role's contract to the repos that read it (below). Governance and platform read theirs from the state as the plan identity; vending's comes from its IP plan. |
| `merge` | contract pull requests | Merges the pull request once `check` and `plan` have passed on it (below). |
| `workload-repos` | vending's `main`, and daily | After every run in which nothing failed: each workload repo vending made gets mlz-core's workload starter once, and the landing zone's values (`mlz.auto.tfvars.json`) whenever they change, by the MLZ App; a repo whose pipeline is on an older release than vending's is offered a pull request to vending's (`docs/vending.md`, "Its upgrades"); each repo's `main` gets the role repos' rule, which nobody but the MLZ App may bypass ("Its rule on main"). |
| `decommission` | vending's `main`, and daily | After a landing zone's decommission is applied, offers the pull requests that delete its files: the platform's rules file for it first, then, once its rules are out of Azure, its own file with its range released (`docs/vending.md`). Signs in as vending's plan identity to look at the hubs' firewall policies. A person merges them. |
| `drift` | daily | Keeps one issue in the repo, "Daily check: Azure differs from main": opened, or commented on, when the check fails, or a stack's plan differs from `main` or did not finish (refused, or stopped before planning); closed by the first daily check that finds every stack as `main` says. An issue of that title opened by a person is left alone. |

The summary is short enough to read: the counts, the changes by resource type, and every change to or
removal of an existing resource by name.

Whether a stack has changes is only ever its list of changes, read from the plan's JSON (`terraform show -json`,
`tools/ci/summary.py`): never Terraform's exit code, and never its text. Terraform's exit code says only whether a
plan was made. The list holds every resource that is added, changed, replaced or destroyed, and every output that
changes. It also holds what changes the state alone: an `import` block, a `moved` block and a `removed` block
(forget), which are applied like the rest, so that the state records them, though nothing in Azure changes. Terraform's
own `applyable` must agree with the list being empty or not, or the plan fails rather than guess. An update whose
every difference is a value unknown until apply is planned and applied, but on the daily check it is no drift on its
own: it may well end unchanged, and the run page says how many there were. A reviewer approving an apply sees the plan of that very run on
its page, and that plan is what gets applied, or nothing is.

The plan identities can only read the state, and Terraform's Azure backend writes an empty state the
first time it meets a new key, so a plan runs against a downloaded copy of its state with a local
backend. Only the apply job uses the backend itself.

Applies of the same stack never run at the same time, and one is never cancelled halfway.

**Plans in groups.** A run planning 20 stacks or fewer plans each in a job of its own, for quick feedback. The daily
check, and any run planning more (an upgrade plans every stack), plans them in groups of 20, one after the other in
one job (`tools/ci/plans.py`, `tools/mlz/stacks.py`'s `batches`), so a hundred landing zones hold a runner for five
jobs, not a hundred, and stay well under GitHub's limit of 256 jobs a matrix. Each stack still gets its summary on
the run page, its comment on a pull request and its status for the drift issue.

`plan`, `apply` and `publish` reach Azure and the state. They run on the repo variable `MLZ_RUNS_ON` when it is
set: the runners in management's network, self-hosted unless `bootstrap.yaml` asks for GitHub-hosted ones,
which `mlz install runners` sets up and switches the repos to ([bootstrap](bootstrap.md#private-runners)).
Self-hosted runners take only jobs from mlz-core's `role.yml` at the release a repo pins, so a job a pull
request adds to `mlz.yml` gets none. Until then, and once it is deleted again, they run on
GitHub's public runners. `decommission` reaches Azure too (as vending's plan identity, to read the hubs' firewall
policies), always from GitHub's public runners. `check`, `changed`, `merge`, `drift` and `workload-repos` never reach
Azure and always run on GitHub's public runners.

Every step runs in the repo's checkout, which a pull request controls, and some hold a token or an Azure sign-in.
So no file of the checkout ever runs as code: the pipeline runs mlz isolated (`python3 -I`,
`tools/ci/run_mlz.py`), from mlz-core's own code and the standard library only, and a folder `mlz/` or a file such
as `json.py` in the repo is never imported in their place, whatever the runner's Python version.

## What a run plans

A pull request or a push to `main` plans only the stacks its change touches: those whose rendered config (what
`check` writes for the roots) differs from the base commit's, rendered in the same run with the same mlz-core. The
base is the commit before: a pull request's base branch, or where `main` was before the push. A change to one landing
zone's file plans that landing zone; a change to `defaults/`, the IP plan, `_generated/names.yaml` or a contract plans
every stack whose config it changes, and no other; a README plans nothing. The run page says which and why. On a
pull request, the plan comment of a stack an earlier commit planned and this one does not (a change taken back) is
marked as not planned on this commit, so no plan stays there that no longer holds.

On a pull request, each stack's plan comment starts with what changed in its settings: every value of the config its
root reads (`.mlz/platform.json`, `.mlz/vending/<key>.json`, governance's `config.json`) that differs from the base
branch's, as a table of the setting, its value there and its value now (`tools/mlz/stacks.py`'s `difference`). A
reviewer sees what a YAML change turned into before reading the Terraform plan: setting `firewall: Premium` in a hub
file shows one row, `hubs.norwayeast.firewall.sku_tier`. When every stack is planned without comparing, there is no
base to compare with, and the comment has the plan only.

Every stack is planned on the daily check, on a manual run (Actions, mlz, Run workflow), when the pipeline file changed
(an upgrade to another mlz-core release), when `replan.txt` changed, when a push brings more than one commit, and when
the base does not check with this release. A stack whose apply failed is not planned again by the next push that does
not touch it: start a manual run, which plans every stack, or wait for the daily check, which reports it. On GitHub
Team, a pull request that changes `replan.txt` instead ([below](#approval-on-github-team)).

## Approval on GitHub Team

On GitHub Team (`bootstrap.yaml`'s `github.plan: team`, [bootstrap](bootstrap.md#github-plan)) no environment can wait
for a reviewer in a private repo, so the pull request's approval is the apply's: its code owner approved the plan its
run showed, on the pull request. The run on `main` then applies a stack only if its own plan of it is that plan, change
for change and value for value (each stack's `changes-<stack>.txt`, as the apply job compares its plan with the
reviewed one). The `changed` job is the gate (`tools/ci/approved.py`), on every run on `main`, a manual one included:

- It finds the pull request whose merge made the commit `main` is at, and that pull request's last run on its head
  commit, and waits up to 25 minutes for that run to finish: a pull request merged as soon as its check passed
  may still be planning. A run of it that started after the merge (run again by someone), before or while the gate
  reads its plans, is refused: its plan is of a later Azure, which nobody approved.
- Each plan job of a pull request's run puts the pull request's number with its plans (`pull-request.txt`), and the
  gate refuses plans that name another: two pull requests on the same commit, one into another branch, have different
  plans. A pull request planned by a release from before this mark has none, and its merge must plan again.
- Each stack whose plan on `main` changes something must have the same list of changes in that run's plans. A stack
  the pull request did not plan (its change did not touch it), or planned otherwise (Azure or the state changed since,
  or an apply failed half way), stops the run: nothing applies, the job fails, and its log shows the difference.
- A commit no pull request made applies nothing: in a role repo, the ruleset `main` lets none through. In a workload
  repo it is the MLZ App's values, and applies as vending wrote them, but only while a rule on `main` asks for a pull
  request (a commit's author is only the email it names, which anyone may set to a bot's, so it is vending's rule,
  which lets nobody but the App push to `main`, that proves it the App's), only when an app's bot wrote the commit,
  and on a re-run only while it is still `main`'s head (an older one would roll the landing zone back). The plans
  are kept 30 days; a pull request merged after that has none to compare with.
- A contract pull request is a pull request like any other: its run's plan, which nobody approves, is the one its
  merge's run applies, without a person (decided 20 November 2026). On Enterprise the role's reviewer approves it.

To apply what the gate stopped, open a pull request that changes `replan.txt` at the repo's top (one line saying why
will do): a change to it plans every stack, on the pull request and again on `main`, so a code owner approves the
plan of everything that would change, and its merge applies exactly that. In a workload repo, which always plans its
one stack, any pull request does, an empty commit included.

The environments stay, with no reviewer: only `main` may use them, and only a job in one may sign in as the apply
identity. The bootstrap sets `MLZ_APPROVAL: pull-request` on each repo, and vending on each workload repo it makes;
without it the environment's reviewer is the gate. The callers' `mlz.yml` grant `actions: read`, for the pull
request's run and its plans (and `pull-requests`, which they grant already, for the pull request).

## A plan that deletes a firewall policy is refused

A hub's firewall policy holds the rules, and the rules are a stack of their own (`firewall-rules`), planned before
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
vending, a contract that gives landing zones their addresses or names gets the IP plan and the names committed
to the pull request first, and the run of that commit merges it. A contract that another merge left behind
`main` is first brought up to date with it, since the ruleset `main-checks` merges only such a branch, and the run of
that merge merges it. Until vending has both governance's and platform's contracts,
it waits: its check plans nothing, and it publishes nothing.

A contract pull request stays open for a person when its run fails, when it changes more than
`_generated/`, or when the repo's own rules refuse the merge, for example a ruleset that asks for a review:
that is how a customer keeps contracts for a person to merge. The run page says which. Once what stopped a
run is fixed, re-running its failed jobs runs the merge after them. The pull requests come from a GitHub
App that may write to the three repos, and the App merges them.

## Code owners

Each repo's `.github/CODEOWNERS` says who must approve a change: the platform team owns every file, and
`_generated/` has no owner, so the contract pull requests still merge themselves. The ruleset `main` requires a
code owner's approval, so a change merges only once the platform team has approved it. `mlz install repos` writes
the file, from `bootstrap.yaml`'s `platform_team`:

```
*             @acme/platform
/_generated/
```

- **Nothing else goes in the file** (decision D8, 19 November).
  - Every run's check reads it offline (`mlz check`, which `./mlz check` runs on a person's checkout too).
  - The check fails on any other rule and names it. Handing a folder to another team is not supported.
  - In the governance repo, whose `bootstrap.yaml` names the platform team, the owner must be that team.
- **The owner is a GitHub team** of the customer's organization, visible, with write access to the repo, which
  `mlz install repos` gives it. GitHub skips a line naming a team that does not exist, is secret or cannot write,
  and the paths that line matches then have no owner at all (tested on 14 November 2026). `mlz install check`
  shows any error GitHub reports in the file.
  - A team can take its members from an Entra ID group: the organization's owner connects the two in the team's
    settings (team synchronization, on GitHub Enterprise Cloud), and GitHub refreshes it at least hourly.
- **The bypass team may merge without the approval,** pull requests only: `bootstrap.yaml`'s `bypass_team`, which
  alone may also change `.github/` ([below](#changes-to-github)). The check still binds it: it lives in the
  ruleset `main-checks`, which has no bypass, since a bypass covers its whole ruleset.
  - GitHub records each bypass under the repo's Insights, Rules.
  - `mlz install repos` gives a new repo's ruleset `main` that bypass.
  - For a repo made before, first run `mlz install repos`, so that `main-checks` requires the check, and take the
    check out of the old `main`. Only then add the team to `main` as a bypass actor, pull requests only: a bypass
    covers its whole ruleset. `mlz install check` notes the missing bypass, and fails while the check is required
    only by a ruleset someone may bypass.
- A code owner approves merges to `main`; on Enterprise the apply still waits for the role's environment's reviewer,
  and on Team that approval is the apply's ([below](#approval-on-github-team)).
- **A push after an approval needs a new one** (stale approvals are dismissed, the user's decision of 19 November, as
  in the workload repos): an approved change cannot become another before it merges.
  - Bringing a branch up to date with `main` keeps the approval while the pull request's own diff is unchanged:
    tested on GitHub on 19 November, "Update branch" and a pushed merge of `main` both kept it, while a merge whose
    resolution changed the pull request's files, and a new commit, dismissed it. So `main-checks`' up-to-date rule
    does not make every open pull request wait for a new approval after each merge.
  - Left open on purpose: someone who pushes onto a pull request they did not open may approve it themselves.
    GitHub's "Require approval of the most recent reviewable push" would close that, but it would leave a team of two
    unable to approve a reviewer's small fix; the bypass team, whose every bypass GitHub records, is the way for such
    exceptions.
  - `mlz install repos` gives a new repo's `main` this. For a repo made before, tick "Dismiss stale pull request
    approvals when new commits are pushed" in its ruleset `main` (Settings, Rules, Rulesets); `mlz install check`
    fails until it is on.

## Changes to `.github/`

A run uses the workflow of the branch it runs on, with the repo's secrets and its plan identity's trust. A branch
that changed `.github/workflows/mlz.yml` could therefore take the MLZ App's private key, or sign in as the plan
identity, from its own pull request (proved on a test customer on 19 November). So each role repo's push ruleset,
`pipeline-files`, refuses a change under `.github/` on every branch, from everyone but `bootstrap.yaml`'s
`bypass_team`:

- **Who must be in the bypass team:**
  - whoever runs `mlz upgrade`, which rewrites `.github/workflows/mlz.yml`;
  - whoever changes `.github/CODEOWNERS`.
  - Both `mlz upgrade` and `mlz install repos` check before they push anything, and stop with who may if you may not.
- **Its members can reach the App's key,** as the repo's admins can. Keep it small; it may not be the platform team.
- **What the rule leaves alone:** the contracts, the IP plan and the names, which are under `_generated/`. The MLZ
  App's own pull requests and commits never need the bypass.
- **It checks pushes, not merges** (tested 19 November). A pull request whose branch a bypass member pushed merges
  like any other, after its review. A branch pushed before the ruleset existed keeps whatever it changed under
  `.github/`, and a pull request from it would run that. So when the ruleset comes to an existing repo, delete the
  branches you do not know.
- **Plans and records:** GitHub records every bypass (the repo's Insights, Rules). Push rulesets need GitHub Team or
  Enterprise, and private repos.

## What a repo needs

| Setting | From |
|---|---|
| Variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`, `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER`, `MLZ_REPOSITORIES`, and on GitHub Team `MLZ_APPROVAL` (`pull-request`); the environment named after the role, with its reviewer (Enterprise) and its own `AZURE_CLIENT_ID` | the bootstrap |
| Variable `MLZ_RELEASE_APP_CLIENT_ID`, secret `MLZ_RELEASE_APP_PRIVATE_KEY` | the app every job reads the organization's copy of mlz-core with, and which opens and merges the contract pull requests, commits the IP plan and the names to vending pull requests, and makes the landing zones' workload repos. Installed on all the organization's repos, with Contents, Pull requests, Administration, Environments, Variables and Workflows read and write, and Actions and Members read (Workflows: writing a workload repo's pipeline; Actions: reading a workload repo's apply environment back, which GitHub lists under Actions, not Environments) |
| Variable `MLZ_RUNS_ON` (optional) | `mlz install runners`: the runners the Azure jobs run on (`mlz`) |
| `.github/CODEOWNERS`, write access for the platform team, the rulesets `main`, `main-checks` and `pipeline-files` | `mlz install repos` ([code owners](#code-owners), [changes to `.github/`](#changes-to-github)) |

For customers, creating and installing this app is part of the install: nothing deploys until it is in
place. If the customer's organisation allows only selected actions and reusable workflows, its Actions
policy must allow its copy's `role.yml` (`acme/mlz-core/.github/workflows/role.yml`) and the actions it uses
(each is pinned by commit in `role.yml`).

## The first run

The roles depend on each other's contracts, so a new customer goes in this order. The contracts move on by
themselves; what a person does is approve each apply, and hand over the landing zone subscriptions vending does
not make.

1. **Governance** (needs the bootstrap's contract): the tree, the policies, central management. It offers
   its contract to platform and vending.
2. **Platform** (needs governance's): the hubs, then management's and identity's networks. It offers its
   contract to vending. Its starter's connectivity file has every setting commented out, so governance's contract
   merges itself and the platform waits, planning nothing, until the platform team writes the hub in a pull
   request (`docs/platform.md`).
3. **Vending** (needs both): the landing zones. A new landing zone's subscription is handed to vending
   first (`docs/bootstrap.md`), or made by vending where `billing_scope` says (`docs/vending.md`).

From then on, each repo's pull requests and merges run on their own.
