# Pipelines

Each customer repo runs its role from mlz-core with one short workflow, `.github/workflows/mlz.yml`
(the starters have it), pinned to a release, in the organization's own copy of mlz-core (`acme/mlz-core`,
[bootstrap](bootstrap.md#running-it)):

```yaml
jobs:
  governance:
    uses: acme/mlz-core/.github/workflows/role.yml@v0.14.0 # x-release-please-version
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
| `changed` | `main` | Picks the stacks whose reviewed plan changes something: a resource, or an output such as the contract. Only those go on to `apply`; a stack with nothing to change asks for no approval, and the run page says which were skipped. With something to apply, the run then waits for a reviewer in the role's environment, and every later push or manual run on `main` queues behind it until it finishes (the daily check has a queue of its own): GitHub notifies the reviewers itself, and the run's page says why it waits (what started it, such as vending's contract merging, and what it applies). When a person's pull request started it, a comment there says so too, once; a contract pull request, a push without a pull request and a manual run get none. On GitHub Team nothing waits: the stacks go on to `apply` only if their plan is the one the merged pull request's run showed ([Approval on GitHub Team](#approval-on-github-team)). |
| `apply` | `main` | Waits for a reviewer in the role's environment (on GitHub Team, the environment has none, and `changed` was the gate). Then plans each stack as the apply identity and applies only if that plan makes exactly the changes in the reviewed plan of the same run, to the same values; otherwise it stops and applies nothing. After an apply that failed half way, re-running the job always stops so, since it compares with the same reviewed plan: on GitHub Enterprise, start a new run (`gh workflow run mlz.yml --ref main`) for a fresh plan to review. On GitHub Team a new run's plan of the rest differs from the approved one, and the gate refuses it: open a pull request that changes `replan.txt` ([below](#when-the-gate-refuses-replantxt)). A job per landing zone; the platform's changed stacks in one job, one after the other (`connectivity`, `management`, `identity`, `firewall-rules`), stopping at the first that fails. |
| `publish` | `main` | After every run in which nothing failed, applied or not: offers the role's contract to the repos that read it (below). Governance and platform read theirs from the state as the plan identity; vending's comes from its IP plan. |
| `merge` | contract pull requests | Merges the pull request once `check` and `plan` have passed on it (below). Not on GitHub Team, where a code owner approves a contract like any other change. |
| `planned` | pull requests, GitHub Team only | One check that every plan of the pull request finished and that the last code owner's approval covers the plan; the ruleset `main-checks` requires it there ([Approval on GitHub Team](#approval-on-github-team)). |
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
Team a manual run applies only when its plan is the one the pull request that made `main`'s commit showed (after an
apply that stopped before changing anything, say), so it cannot apply drift or the rest of a failed apply: change
`replan.txt` in a pull request instead ([below](#when-the-gate-refuses-replantxt)).

## Approval on GitHub Team

GitHub lets an environment wait for a reviewer in a private repo only on GitHub Enterprise. So on GitHub Team
(`bootstrap.yaml`'s `github.plan: team`, [bootstrap](bootstrap.md)) no apply waits for a person, and the approval of the
pull request is the only human gate. The pipeline makes it mean as much as it can: **what applies on `main` is a plan a
code owner saw and approved, and nothing else.** Nobody looks at the plan of the run on `main` before it applies; it
applies when it is the approved one, and nothing applies otherwise.

Each repo's `.github/workflows/mlz.yml` says which plan it is on (`approval: pull-request`, which the installer and vending
write for Team; without it, the Enterprise path runs). It is not a repo variable, which anyone with write access
may change; a change under `.github/` needs the bypass team ([below](#changes-to-github)).

### Before the merge: `planned`

On Team, the ruleset `main-checks` requires `<role> / planned` beside `<role> / check` (`workload / planned` beside
`workload / plan` in a workload repo), so no pull request merges before its plan is on it, and none merges with a plan
nobody approved. The job fails unless every plan of the run finished, and then unless one of these holds:

- the plan changes nothing;
- no code owner has approved yet: any approval from now on comes after this plan;
- the last code owner's approval came after this plan was on the pull request;
- the plan is the one that approval saw: that of the pull request's run on the commit it approved, which had finished
  before it. So "Update branch" asks for a new approval only when the plan itself changed.

It also asks for a new approval when the plans that approval saw can no longer be read: the run it approved was run again
since (a re-run replaces its plans), or was still planning when the approval came.

When it fails, it says why, and how the plan changed where it did: a code owner approves again, and then re-running
the failed job lets the pull request merge. A code owner is a member of the team `.github/CODEOWNERS` names (in a role
repo, the platform team).

The job runs whatever happened before it, since GitHub counts a skipped required check as passed. A run cancelled by a
newer push, or with a failed plan, leaves it failed on that commit.

### After the merge: the gate

Before anything applies, the run on `main` checks that each stack's plan is the plan the merged pull request's last run
showed, change for change and value for value (`tools/ci/approved.py`). Nothing applies, and the run says why, when:

- the run is a later attempt of an older run, and its commit is no longer `main`'s head. Planned again, an older commit's
  plan reads "back to that commit", the same changes as the plan once approved, and would undo what later runs applied.
  A first attempt is always in order, since runs on `main` queue;
- no pull request made the commit (a push to `main`, or a manual run of a commit no pull request made);
- the pull request's last run with plans marked as its own did not finish with success, or was run again after the merge,
  or there is none. Each plan job of a Team pull request's run marks its plans with the pull request's number. On the
  same commit, a run without plans (it planned nothing, or stopped before) and a run with another pull request's (into
  another branch) are passed over, and the one before them counts. A newer run of its own that failed before planning
  leaves the required `planned` failed, so the pull request could not have merged on it. One that planned nothing
  passes `planned`, and the gate still holds `main`'s plan to the earlier run's, which must match;
- a stack's plan on `main` differs from the one the pull request's run showed: Azure or the state changed since, or an
  apply failed half way.

Applies that pass the gate still check, as on Enterprise, that their own plan is the reviewed one and the state has not
moved since.

### When the gate refuses: `replan.txt`

To plan every stack in a pull request, change `replan.txt` at the repo's top, or add it if it is not there, with one
line saying why. Its run plans every stack, and once it is approved and merged, `main` applies exactly that plan.

That is the way to apply drift the daily check found, or the rest of an apply that failed half way. A workload repo needs
none: it plans its one stack whole on every pull request, so any pull request will do.

### Contracts and the bypass team

- **Contracts:** a code owner approves each contract pull request, like any other change. `_generated/` has the
  platform team as its code owner on Team, and `merge` does not run there.
- **The bypass team** may still merge a pull request without a code owner's approval, which on Team skips the only
  person. Its merge applies what the pull request's run showed. When GitHub's record of the past day shows the bypass,
  the run's page names who used it, and so does one comment on the pull request, on the run's first attempt. Both are
  best effort: a failed read or comment never holds up the apply, so a run without the note proves no approval. GitHub's
  own record, which is complete, is under the repo's Insights, Rules.

### Workload repos

The same gate, with three differences:

- **Who approves:** a workflow's token cannot read a team's members, so `planned` counts an approval by anyone with write
  access but the author, not only the landing zone team's. Vending gives write access to that team and the bypass team.
- **The values:** the MLZ App's commits of the landing zone's values still reach `main` without a pull request, and apply
  with no person's approval. The gate accepts such a commit only while a rule on `main` asks for a pull request (vending's
  ruleset lets nothing but the MLZ App past it), with one parent, changing only the values, the code owners, and the
  starter's files in the commit that first adds the values.
- **The bypass:** the job cannot read GitHub's record of a bypass, so a bypass merge is not named on the run's page.

### What it does not catch

- **The mode is held twice:**
  - the pipeline file's input, which only the bypass team may change in a role repo, and on Team in a workload repo too;
  - the environment's variable `MLZ_APPROVAL: pull-request`, which the bootstrap and vending set on Team.

  An apply in Enterprise mode whose environment says `pull-request` is refused, since nothing would gate it. That
  check is against a switch done out of order and against mistakes. It is not proof against a person with write or
  maintain access, who may change an environment's variables (tested on 23 November).
- **Removing an environment's reviewer on Enterprise** by hand is not caught by the pipeline. In a workload repo,
  vending's daily plan shows it as drift.
- **Switching between the plans:** for the time between two of GitHub's API calls inside the switch's apply, an
  environment has neither its reviewer nor the variable. Cancel any run waiting for a reviewer, and merge nothing, until
  the switch has applied ([bootstrap](bootstrap.md)).
- **Old runs, once an environment has no reviewer:** GitHub lets anyone with write access re-run a run for 30 days, on
  its commit and so with the pipeline it had. Two kinds of run would then apply with no person:
  - in a role repo, a run whose apply was rejected or cancelled after the switch to Team merged. "Re-run failed jobs"
    runs only the apply again, not the gate in `changed`, and the pull request's approval covered that plan;
  - a run made on a release before v0.14.0, which has no `MLZ_APPROVAL` check.

  The switch to Team deletes them first ([bootstrap](bootstrap.md#switching-plans), step 5). Derived from GitHub's
  documentation ("Re-running workflows and jobs") and the pipeline's code, not tested.

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

On GitHub Team a code owner approves each contract pull request instead, like any other change
([Approval on GitHub Team](#contracts-and-the-bypass-team)); what follows is GitHub Enterprise's.

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
`_generated/` has no owner, so the contract pull requests still merge themselves; only the MLZ App and the bypass team
may change it ([below](#changes-to-_generated)). The ruleset `main` requires a
code owner's approval, so a change merges only once the platform team has approved it. `mlz install repos` writes
the file, from `bootstrap.yaml`'s `platform_team`:

```
*             @acme/platform
/_generated/
```

That is GitHub Enterprise's file. On GitHub Team, `_generated/` has the platform team as its code owner too, so a person
approves each contract pull request, which no longer merges itself ([Approval on GitHub Team](#contracts-and-the-bypass-team);
[what Team gives up](bootstrap.md#what-team-gives-up), [switching plans](bootstrap.md#switching-plans)). There the
installer writes:

```
*             @acme/platform
/_generated/  @acme/platform
```

- **Nothing else goes in the file** (decision D8, 19 November).
  - Every run's check reads it offline (`mlz check`, which `./mlz check` runs on a person's checkout too).
  - The check fails on any other rule and names it. Handing a folder to another team is not supported.
  - `/_generated/` may take either form; in the governance repo, whose `bootstrap.yaml` names the plan, it must be the
    form that plan writes: no owner on Enterprise, the platform team on Team.
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
- A code owner approves merges to `main`; the apply still waits for the role's environment's reviewer.
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
- **What the rule leaves alone:** `_generated/`, which has a rule of its own ([below](#changes-to-_generated)). The
  MLZ App is never let past this one.
- **It checks pushes, not merges** (tested 19 November). A pull request whose branch a bypass member pushed merges
  like any other, after its review. A branch pushed before the ruleset existed keeps whatever it changed under
  `.github/`, and a pull request from it would run that. So when the ruleset comes to an existing repo, delete the
  branches you do not know.
- **Plans and records:** GitHub records every bypass (the repo's Insights, Rules). Push rulesets need GitHub Team or
  Enterprise, and private repos.

## Changes to `_generated/`

The contracts, the IP plan and the names are what the roles plan from, and `_generated/` has no code owner so that
the contract pull requests merge themselves. Whoever could push a change there could merge it alone: a forged
`bypass_team` in vending's `_generated/governance.yaml` would give that team write access to every workload repo,
with no apply to approve. So each role repo's push ruleset `generated-files` refuses a change under `_generated/` on
every branch, from everyone but the MLZ App (`bootstrap.yaml`'s `app`) and the bypass team:

- **The MLZ App makes every change there** in a pipeline: the contract pull requests, the IP plan and the names it
  commits to a vending pull request, and the decommission pull requests.
- **The bypass team** runs `mlz install publish`, and pushes `./mlz allocate`'s result when a run says the pipeline
  cannot. `mlz install publish` checks before it pushes, and stops with who may if you may not.
- **Bringing a branch up to date is not refused** (tested on GitHub on 23 November): "Update branch" and a pushed
  merge of `main` go through, though they bring `main`'s files there in. A merge commit that changes such a file itself
  is refused, so resolve a conflict there by keeping `main`'s file; the next run writes the pull request's.
- **A branch pushed before the ruleset** keeps what it changed there, as with `.github/`: when the ruleset comes to an
  existing repo, delete the branches you do not know.

## What a repo needs

| Setting | From |
|---|---|
| Variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`, `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER`, `MLZ_REPOSITORIES`; the environment named after the role, with its reviewer and its own `AZURE_CLIENT_ID` | the bootstrap |
| Variable `MLZ_RELEASE_APP_CLIENT_ID`, secret `MLZ_RELEASE_APP_PRIVATE_KEY` | the app every job reads the organization's copy of mlz-core with, and which opens and merges the contract pull requests, commits the IP plan and the names to vending pull requests, and makes the landing zones' workload repos. Installed on all the organization's repos, with Contents, Pull requests, Administration, Environments, Variables and Workflows read and write, and Actions and Members read (Workflows: writing a workload repo's pipeline; Actions: reading a workload repo's apply environment back, which GitHub lists under Actions, not Environments) |
| Variable `MLZ_RUNS_ON` (optional) | `mlz install runners`: the runners the Azure jobs run on (`mlz`) |
| `.github/CODEOWNERS`, write access for the platform team, the rulesets `main`, `main-checks`, `pipeline-files` and `generated-files` | `mlz install repos` ([code owners](#code-owners), [changes to `.github/`](#changes-to-github), [to `_generated/`](#changes-to-_generated)) |

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
