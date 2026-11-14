"""Sets a new customer up, step by step; every step can be run again and changes only what is missing.

    python3 -m mlz install repos     --settings <dir>                 the three repos: private, main ruleset, starters,
                                                                      the platform team as code owner
    python3 -m mlz install check     --settings <dir>                 the MLZ App's client ID and key in each repo
    python3 -m mlz install bootstrap --settings <dir> --tenant <id>   bootstrap/terraform, its state moved, subjects checked;
                                                                      again after the platform's first apply, for the
                                                                      state account's private endpoint
    python3 -m mlz install handover  --settings <dir> --tenant <id>   the platform subscriptions into the top group
    python3 -m mlz install publish   --settings <dir>                 bootstrap.yaml offered to the governance repo
    python3 -m mlz install runners   --settings <dir> --tenant <id>   GitHub's side of the private runners, then
                                                                      the repos moved onto them (an owner of the
                                                                      organization; others get the commands to send)
    python3 -m mlz install subscriptions --settings <dir> --tenant <id> --billing-scope <scope>
                                                                      vending's apply identity may make subscriptions
                                                                      there (a billing owner; others get the command
                                                                      and the role to send)

<dir> holds the customer's customer.yaml and bootstrap.yaml: a checkout of the governance repo, or for a new
customer a folder of the two files before the repos exist (repos then commits them to the governance repo).
Run from an mlz-core checkout (PYTHONPATH=tools), signed in to gh (admin on the customer's repos, workflow
scope) and, for bootstrap and handover, to the Azure CLI as someone who may create the top group.

The MLZ App itself is made by hand (GitHub needs a browser for that); check says whether it is set.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import contract, names, naming, owners, platform, vending
from .files import read_yaml
from .github import RUNNER_SCOPES
from .merge import ConfigError
from .upgrade import RELEASE_PLEASE_MARKER, WORKFLOW

ROLES = ("governance", "platform", "vending")
CORE = Path(__file__).resolve().parents[2]
APP_VARIABLE, APP_SECRET = "MLZ_RELEASE_APP_CLIENT_ID", "MLZ_RELEASE_APP_PRIVATE_KEY"
# Changes reach main only through pull requests; main is never rewritten or deleted (decided 2026-11-11). A change to
# a file with a code owner needs that owner's approval (CODEOWNERS, tools/mlz/owners.py, decided 2026-11-14); a change
# to one without, such as _generated/, needs none, so the contract pull requests still merge themselves. And none
# merges until the pipeline's check has passed on it (decided 2026-11-14), so what the check refuses never reaches main:
# contract pull requests merge themselves only after it has passed anyway. Not the plan, which reaches Azure and can
# fail for reasons of its own. Not strict either: a contract still merges after main has moved on.
GITHUB_ACTIONS_APP = 15368  # the app that reports workflow jobs' checks, so no other app or token can pass one


def required_check(role: str) -> str:
    """The check GitHub knows the role's check job by: the starter workflow's job, named after the role, then
    role.yml's own (as the canary's runs showed, 2026-11-14)."""
    return f"{role} / check"


def main_ruleset(role: str) -> dict:
    return {
        "name": "main",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": True,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
            }},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": False,
                "required_status_checks": [{"context": required_check(role), "integration_id": GITHUB_ACTIONS_APP}],
            }},
        ],
    }
# bootstrap.yaml's settings, read by the bootstrap and by this script. Anything else is refused.
BOOTSTRAP_SETTINGS = ("github", "state_operators", "names")
GITHUB_SETTINGS = ("organization", "repositories", "platform_team", "reviewers", "runner_size")
CODE_OWNERS_BRANCH = "mlz/code-owners"
CONTRACT_PATH, CONTRACT_BRANCH = "_generated/bootstrap.yaml", "mlz/bootstrap-contract"
STARTER_BRANCH = "mlz/starter"


def settings(folder: Path) -> tuple[dict, dict]:
    files = [folder / "customer.yaml", folder / "bootstrap.yaml"]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)}: --settings is the folder with the customer's customer.yaml and bootstrap.yaml.")
    customer, bootstrap = read_yaml(files[0]), read_yaml(files[1])
    github = bootstrap.get("github")
    unknown = [str(k) for k in bootstrap if k not in BOOTSTRAP_SETTINGS]
    unknown += [f"github.{k}" for k in github if k not in GITHUB_SETTINGS] if isinstance(github, dict) else []
    if unknown:
        raise ConfigError(f"bootstrap.yaml: {', '.join(unknown)} is not a setting; it takes github ({', '.join(GITHUB_SETTINGS)}), state_operators and names.")
    return customer, bootstrap


def bootstrap_names(customer: dict, bootstrap: dict) -> dict:
    """The bootstrap's names, and the platform's it points at, from customer.yaml's naming (tools/mlz/names.py), with
    bootstrap.yaml's names: for its resource group or state account."""
    try:
        settings = naming.settings(customer, customer["root_id"])
        return names.bootstrap(settings, customer["primary_region"], names.bootstrap_explicit(bootstrap.get("names")))
    except KeyError as error:
        raise ConfigError(f"customer.yaml: {error} is required.") from None
    except ConfigError as error:
        raise ConfigError(f"names: {error}") from None


def repositories(bootstrap: dict) -> dict[str, str]:
    github = bootstrap.get("github") or {}
    names = github.get("repositories") or {}
    if not github.get("organization") or set(names) != set(ROLES):
        raise ConfigError("bootstrap.yaml needs github.organization and github.repositories for governance, platform and vending.")
    return {role: f"{github['organization']}/{names[role]}" for role in ROLES}


def platform_team(bootstrap: dict) -> tuple[str, str]:
    """(organization, team slug): the GitHub team that owns the three repos, bootstrap.yaml's github.platform_team."""
    github = bootstrap.get("github") or {}
    team = github.get("platform_team")
    if not isinstance(team, str) or not team:
        raise ConfigError("bootstrap.yaml needs github.platform_team: the slug of the GitHub team that owns the three repos "
                          "and approves their changes (docs/bootstrap.md).")
    return github["organization"], team


def release() -> str:
    return "v" + json.loads((CORE / ".release-please-manifest.json").read_text())["."]


def starter_files(role: str, folder: Path) -> dict[str, str]:
    """The role's starter as a new repo gets it, with its code owners and, for governance, the customer's settings."""
    _, bootstrap = settings(folder)
    base = CORE / "starter" / f"mlz-{role}"
    files = {str(p.relative_to(base)): RELEASE_PLEASE_MARKER.sub("", p.read_text()) for p in sorted(base.rglob("*")) if p.is_file()}
    files[owners.PATH] = owners.render(*platform_team(bootstrap))
    if role == "governance":
        files.update({name: (folder / name).read_text() for name in ("customer.yaml", "bootstrap.yaml")})
    return files


def answer(question: str) -> str:
    """The person's answer, read only from a terminal: a pipe or a script never answers (a piped "y" would
    otherwise approve an apply), so without one it counts as no. --yes is the way to say yes without a person."""
    if not sys.stdin.isatty():
        print(f"{question}no terminal to ask; run again with --yes to go ahead")
        return ""
    try:
        return input(question)
    except EOFError:
        return ""


# repos and check

def step_repos(github, folder: Path, out: Callable[[str], None] = print) -> None:
    _, bootstrap = settings(folder)
    repos = repositories(bootstrap)
    org, team = platform_team(bootstrap)
    found_team = github.team(org, team)
    if found_team is None:
        raise ConfigError(f"{org} has no team {team} (bootstrap.yaml's github.platform_team). Make it, and connect it to its "
                          "Entra ID group if the organization syncs teams, then run this step again.")
    if found_team.get("privacy") == "secret":
        # GitHub skips a secret team in CODEOWNERS, which would leave every path without an owner.
        raise ConfigError(f"{org}/{team} is a secret team, and CODEOWNERS can only name a visible one. An organization owner "
                          "makes it visible (the team's Settings, Team visibility), then run this step again.")
    for role, repo in repos.items():
        owner, name = repo.split("/")
        found = github.repo(repo)
        if found is None:
            github.create_repo(owner, name)
            # The workflows, the environments' branch policy and the plan identities' trust all name main; the
            # organisation's default name for a new branch may be another.
            if github.default_branch(repo) != "main":
                github.rename_branch(repo, github.default_branch(repo), "main")
            created = True
        else:
            created = False
            if github.is_empty(repo):
                # The commits here are made through the API GitHub signs, which adds to a branch: it cannot make
                # a repo's first commit.
                raise ConfigError(f"{repo} exists but is empty; give it a first commit on main (a README from its page will do), then run this step again.")
            if not found.get("private"):
                out(f"{repo}: it is not private; change that in its settings if it should be")
            if found.get("default_branch", "main") != "main":
                out(f"{repo}: its default branch is {found['default_branch']}, but the pipelines run on main; rename it")
        # A repo made here gets the starter as its first commit, before its ruleset exists. A repo that exists
        # without the workflow (made here once, its first commit failed; or adopted) gets the starter's missing
        # files as a pull request: its main may take nothing else, and a file already there is never replaced.
        branch = github.default_branch(repo)
        message = f"chore: mlz-{role} from mlz-core {release()}"
        if created:
            github.commit_files(repo, branch, github.head(repo, branch), starter_files(role, folder), message)
            out(f"{repo}: created, private, with the {role} starter")
        elif github.file(repo, WORKFLOW, branch)[0] is None:
            missing = {path: text for path, text in starter_files(role, folder).items() if github.file(repo, path, branch)[0] is None}
            # GitHub reads .github/CODEOWNERS before the root's and docs/: one added beside the repo's own would replace it.
            if any(github.file(repo, location, branch)[0] is not None for location in owners.LOCATIONS):
                missing.pop(owners.PATH, None)
            url = github.open_pull(repo, STARTER_BRANCH)
            if not url:
                github.commit_files(repo, STARTER_BRANCH, github.reset_branch(repo, STARTER_BRANCH, branch), missing, message)
                url = github.create_pull(repo, STARTER_BRANCH, branch, message, f"The {role} starter's files this repo does not have yet, from `mlz install repos`. Files already here are left as they are.\n")
            out(f"{repo}: the {role} starter's missing files are offered in {url}")
        else:
            out(f"{repo}: exists, files left as they are")
            if not any(github.file(repo, location, branch)[0] is not None for location in owners.LOCATIONS):
                # A repo set up before code owners: the file comes as a pull request, which a person merges.
                url = github.open_pull(repo, CODE_OWNERS_BRANCH)
                if not url:
                    github.commit_files(repo, CODE_OWNERS_BRANCH, github.reset_branch(repo, CODE_OWNERS_BRANCH, branch),
                                        {owners.PATH: owners.render(org, team)}, "chore: the platform team as code owner")
                    url = github.create_pull(repo, CODE_OWNERS_BRANCH, branch, "chore: the platform team as code owner",
                                             f"`{owners.PATH}` from `mlz install repos`: {org}/{team} owns every file, and `_generated/` none "
                                             "(docs/pipelines.md in mlz-core, Code owners).\n")
                out(f"{repo}: {owners.PATH} is offered in {url}")
        # A code owner needs write access; one with more (maintain, admin) keeps it.
        if github.team_can_write(org, team, repo):
            out(f"{repo}: {org}/{team} may write, as its code owner")
        else:
            github.grant_team(org, team, repo)
            out(f"{repo}: {org}/{team} given write access, as its code owner")
        # Never replaces a ruleset of that name: the customer's own may be stricter than this one.
        if github.add_ruleset(repo, main_ruleset(role)):
            out(f"{repo}: ruleset main (pull requests only, a code owner's approval, {required_check(role)} passed, no force push or deletion)")
        else:
            out(f"{repo}: has a ruleset named main already, left as it is; check that it allows changes only through pull requests, "
                f"and requires a code owner's review and {required_check(role)} (mlz install check says)")


def step_check(github, folder: Path, out: Callable[[str], None] = print) -> None:
    customer, bootstrap = settings(folder)
    out(f"names: the state account will be {bootstrap_names(customer, bootstrap)['storage_account']}")
    problems = []
    # Every apply waits for a reviewer, in the role repos and the workload repos alike, and GitHub allows required
    # reviewers on an environment in a private repo only on GitHub Enterprise (docs/bootstrap.md).
    org = (bootstrap.get("github") or {}).get("organization")
    plan = github.plan(org) if org else None
    if plan is None and org:
        out(f"{org}: its GitHub plan is shown to its owners only, so it is not checked here; MLZ needs GitHub Enterprise.")
    elif plan and plan != "enterprise":
        problems.append(f"{org}: on GitHub's {plan} plan. MLZ needs GitHub Enterprise: every apply waits for a reviewer, which GitHub "
                        "allows on an environment in a private repo only on Enterprise.")
    else:
        out(f"{org}: on GitHub Enterprise")
    for role, repo in repositories(bootstrap).items():
        if github.repo(repo) is None:
            problems.append(f"{repo}: no such repo; run mlz install repos first.")
            continue
        missing = []
        if not github.has_variable(repo, APP_VARIABLE):
            missing.append(f"variable {APP_VARIABLE}")
        if not github.has_secret(repo, APP_SECRET):
            missing.append(f"secret {APP_SECRET}")
        if missing:
            problems.append(f"{repo}: no {' and no '.join(missing)}. Install the customer's MLZ App on the repo, then: "
                            f"gh variable set {APP_VARIABLE} --repo {repo} --body <client ID>; gh secret set {APP_SECRET} --repo {repo} < <private key file>")
        else:
            out(f"{repo}: the MLZ App's client ID and key are set")
        problems += code_owner_problems(github, bootstrap, repo, out)
        problems += required_check_problems(github, role, repo, out)
    if problems:
        raise ConfigError("\n  ".join(["not ready for MLZ:"] + problems))


def code_owner_problems(github, bootstrap: dict, repo: str, out: Callable[[str], None]) -> list[str]:
    """What stops the repo's code owners from deciding: the platform team's access, the file as GitHub reads it, and
    the ruleset that makes their approval required."""
    try:
        org, team = platform_team(bootstrap)
    except ConfigError as error:
        return [str(error)]
    found = []
    if not github.team_can_write(org, team, repo):
        found.append(f"{repo}: {org}/{team} cannot write to it, so it cannot be its code owner; mlz install repos gives it access.")
    branch = github.default_branch(repo)
    if not any(github.file(repo, location, branch)[0] is not None for location in owners.LOCATIONS):
        found.append(f"{repo}: no {owners.PATH} on {branch}; mlz install repos offers one.")
    else:
        errors = github.codeowners_errors(repo, branch)
        if errors is None:
            found.append(f"{repo}: GitHub has not read {owners.PATH} yet; run this step again in a minute.")
        found += [f"{repo}: {problem}" for problem in owners.problems(errors or [], repo)]
    # The rules GitHub applies to the branch, from every active ruleset, the organization's too: a ruleset that is
    # disabled, only evaluated, or aimed at other branches has none here.
    if not any(rule.get("type") == "pull_request" and (rule.get("parameters") or {}).get("require_code_owner_review")
               for rule in github.branch_rules(repo, branch)):
        found.append(f"{repo}: no active ruleset requires a code owner's review on {branch}, so CODEOWNERS only suggests reviewers; "
                     "turn on 'Require review from Code Owners' in its ruleset main (Settings, Rules, Rulesets), and check that it is active.")
    if not found:
        out(f"{repo}: {org}/{team} may write, GitHub reads its CODEOWNERS whole, and a code owner's review is required on {branch}")
    return found


def required_check_problems(github, role: str, repo: str, out: Callable[[str], None]) -> list[str]:
    """Whether GitHub refuses a merge into the default branch until the pipeline's check has passed, by the rules it
    enforces there from every active ruleset."""
    branch, check = github.default_branch(repo), required_check(role)
    if any(rule.get("type") == "required_status_checks"
           and any(required.get("context") == check for required in (rule.get("parameters") or {}).get("required_status_checks", []))
           for rule in github.branch_rules(repo, branch)):
        out(f"{repo}: {check} must pass before a merge into {branch}")
        return []
    return [f"{repo}: no active ruleset requires {check} to pass on {branch}, so a change the check refuses can still be merged; "
            f"add it under 'Require status checks to pass' in its ruleset main (Settings, Rules, Rulesets)."]


# bootstrap

class Terraform:
    """bootstrap/terraform, copied with what it uses into a folder of the customer's own, so its local state,
    backend config and plan never mix with another customer's."""

    def __init__(self, work: Path, tenant: str):
        # Absolute, since terraform runs with -chdir into the root below it.
        self.work = work.expanduser().resolve()
        self.root = self.work / "bootstrap" / "terraform"
        self.env = {**os.environ, "ARM_TENANT_ID": tenant}
        if not self.env.get("GITHUB_TOKEN"):  # only then: a supplied token needs no gh on the machine
            self.env["GITHUB_TOKEN"] = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()

    def prepare(self) -> None:
        """The root as this mlz-core has it: files from an earlier copy go first, and the checkout's override files
        (a git-ignored backend_override.tf from running the root by hand) are never copied, since either would
        change what the root does. The bootstrap step writes its own override while the state is local."""
        pairs = ((CORE / "bootstrap" / "terraform", self.root),)
        if any(target.resolve() == source.resolve() for source, target in pairs):
            # The copy's old files go first: in the checkout itself, those are the sources.
            raise ConfigError(f"the work folder {self.work} is mlz-core's own checkout; choose another with --work.")
        for source, target in pairs:
            target.mkdir(parents=True, exist_ok=True)
            # .json too, which an earlier mlz-core's copy of modules/naming had.
            for old in [*target.glob("*.tf"), *target.glob("*.json")]:
                old.unlink()
            for path in [*source.glob("*.tf"), *source.glob("*.json"), *source.glob(".terraform.lock.hcl")]:
                if not path.name.endswith("override.tf"):
                    shutil.copy2(path, target / path.name)

    def run(self, *arguments: str, capture: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(["terraform", f"-chdir={self.root}", *arguments], env=self.env, capture_output=capture, text=True)

    def output(self, name: str) -> str:
        found = self.run("output", "-json", name, capture=True)
        if found.returncode:
            raise ConfigError(f"terraform output {name}: {found.stderr.strip()}")
        return found.stdout


def step_bootstrap(github, terraform, folder: Path, yes: bool = False, out: Callable[[str], None] = print,
                   ask: Callable[[str], str] = answer, wait: Callable[[float], None] = time.sleep) -> None:
    """Plans and applies the bootstrap, with a local state the first time; then moves the state into the account
    the bootstrap created, and checks that the identities trust the subjects GitHub issues."""
    customer, bootstrap = settings(folder)
    # Worked out here, on every run, from customer.yaml's naming and bootstrap.yaml's names: (docs/naming.md).
    names_file = terraform.work / "names.json"
    names_file.parent.mkdir(parents=True, exist_ok=True)
    names_file.write_text(json.dumps(bootstrap_names(customer, bootstrap), indent=2) + "\n")
    terraform.prepare()
    backend, pending = terraform.work / "backend.hcl", terraform.work / "backend.hcl.pending"
    override = terraform.root / "backend_override.tf"
    local = not backend.exists()
    if local:
        override.write_text(f'terraform {{\n  backend "local" {{\n    path = "{terraform.work / "bootstrap.tfstate"}"\n  }}\n}}\n')
    init = ["init", "-input=false", "-reconfigure"] + ([] if local else [f"-backend-config={backend}"])
    if terraform.run(*init).returncode:
        raise ConfigError("terraform init failed; see above.")
    plan = terraform.work / "bootstrap.tfplan"
    variables = ["-var", f"customer_file={(folder / 'customer.yaml').resolve()}", "-var", f"bootstrap_file={(folder / 'bootstrap.yaml').resolve()}",
                 "-var", f"names_file={names_file}"]
    status = terraform.run("plan", "-input=false", "-detailed-exitcode", f"-out={plan}", *variables).returncode
    if status == 1:
        raise ConfigError("terraform plan failed; see above.")
    if status == 2:
        if not yes and ask("Apply this plan? [y/N] ").strip().lower() != "y":
            raise ConfigError("not applied.")
        if terraform.run("apply", "-input=false", str(plan)).returncode:
            raise ConfigError("terraform apply failed; see above. Run the step again to continue.")
    if local:
        move_state(terraform, backend, pending, override, out, wait)
    check_subjects(github, json.loads(terraform.output("oidc_subjects")), repositories(bootstrap), out)
    if json.loads(terraform.output("state_private_endpoint")):
        out("bootstrap: the state account has its private endpoint in management's network.")
    else:
        out("bootstrap: the state account's private endpoint waits for management's network and the hub's blob zone: run this step again after the platform's first apply.")
    out("bootstrap: done. Next: mlz install handover, then mlz install publish.")


def move_state(terraform, backend: Path, pending: Path, override: Path, out, wait) -> None:
    """Into the account the bootstrap created. The person's own rights on that state are new, and Azure can
    take minutes to apply them, so the move is tried again for up to five minutes. Until it succeeds, the
    local backend stays in place and a run of the step picks up where this one stopped."""
    pending.write_text(json.loads(terraform.output("backend_config")))
    local_backend = override.read_text()
    override.unlink()
    for attempt in range(11):
        if terraform.run("init", "-input=false", "-migrate-state", "-force-copy", f"-backend-config={pending}").returncode == 0:
            pending.rename(backend)
            out(f"bootstrap: the state is now in the account ({backend.name} in {terraform.work})")
            return
        if attempt < 10:
            out("bootstrap: the state account does not take the state yet (new rights can take minutes); trying again in 30 s")
            wait(30)
    override.write_text(local_backend)
    raise ConfigError("the state could not be moved into the account; it is still local. Run the step again.")


def check_subjects(github, subjects: dict[str, str], repos: dict[str, str], out: Callable[[str], None] = print) -> None:
    """Each identity's subject must start with the prefix GitHub issues to its repo's workflows."""
    prefixes = {role: github.subject_prefix(repo) for role, repo in repos.items()}
    wrong = [f"{key}: trusts {subject}, but GitHub issues {prefixes[key.split('-')[0]]}:…"
             for key, subject in sorted(subjects.items()) if not subject.startswith(prefixes[key.split("-")[0]] + ":")]
    if wrong:
        raise ConfigError("\n  ".join(["the identities trust subjects GitHub does not issue, so no pipeline could sign in "
                                       "(docs/bootstrap.md: immutable subjects):"] + wrong))
    out(f"bootstrap: all {len(subjects)} subjects match what GitHub issues")


# handover

class Azure:
    """Azure Resource Manager in one tenant, with the Azure CLI's sign-in for that tenant."""

    def __init__(self, tenant: str):
        self.token = subprocess.run(["az", "account", "get-access-token", "--tenant", tenant, "--resource", "https://management.azure.com",
                                     "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True, check=True).stdout.strip()

    def request(self, method: str, url: str, body: dict | None = None) -> dict:
        # Without no-cache, getEntities answered from a cache for minutes after a move: three subscriptions just
        # moved into the tree still showed in unmanaged_subscriptions (TCAF, 2026-11-11), so a second handover
        # would have asked to move them again.
        headers = {"Authorization": f"Bearer {self.token}", "Cache-Control": "no-cache"}
        data = None if body is None else json.dumps(body).encode()
        headers.update({"Content-Length": "0"} if data is None else {"Content-Type": "application/json"})
        req = urllib.request.Request(url if url.startswith("https://") else f"https://management.azure.com{url}", data=data, method=method,
                                     headers=headers)
        with urllib.request.urlopen(req) as response:
            body = response.read()
        return json.loads(body) if body else {}

    def parents(self) -> dict[str, list[str]]:
        """Every subscription the sign-in can see, with the groups above it from the tenant root down (checked in
        the TCAF tenant, 2026-11-11: [<tenant ID>, unmanaged_subscriptions])."""
        found, url = {}, "/providers/Microsoft.Management/getEntities?api-version=2021-04-01"
        while url:
            page = self.request("POST", url)
            found.update({e["name"].lower(): e["properties"].get("parentNameChain") or [] for e in page.get("value", []) if e["type"] == "/subscriptions"})
            url = page.get("nextLink")
        return found

    def move(self, group: str, subscription: str) -> None:
        self.request("PUT", f"/providers/Microsoft.Management/managementGroups/{group}/subscriptions/{subscription}?api-version=2021-04-01")

    def resource(self, resource_id: str, api_version: str) -> dict | None:
        """The resource, or None if it, or its resource group, does not exist."""
        try:
            return self.request("GET", f"{resource_id}?api-version={api_version}")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise


def step_handover(azure, folder: Path, yes: bool = False, out: Callable[[str], None] = print, ask: Callable[[str], str] = answer) -> None:
    """The platform subscriptions into the top group, once. One already anywhere in the tree stays where it is:
    governance places it under Management, Connectivity or Identity, and must not find it moved back."""
    customer, _ = settings(folder)
    root = customer["root_id"]
    parents = azure.parents()
    moves = []
    for role, subscription in sorted(customer["subscriptions"].items()):
        chain = parents.get(subscription.lower())
        if chain is None:
            raise ConfigError(f"subscription {subscription} ({role}) is not visible to this sign-in.")
        if root in chain:
            out(f"handover: {role} subscription {subscription} is already in the {root} tree")
        else:
            moves.append((role, subscription, chain[-1] if chain else "the tenant root"))
    if not moves:
        return
    for role, subscription, now in moves:
        out(f"handover: {role} subscription {subscription} moves from {now} into {root}")
    if not yes and ask("Move them? [y/N] ").strip().lower() != "y":
        raise ConfigError("not moved.")
    for role, subscription, _ in moves:
        azure.move(root, subscription)
        out(f"handover: {role} subscription {subscription} is now in {root}")


# runners

RUNNER = "mlz"  # the organization's network configuration, runner group and runner, and the label jobs run on
RUNS_ON = "MLZ_RUNS_ON"  # the repo variable the workflow runs its Azure jobs on, when set (.github/workflows/role.yml)
RUNNER_SIZE, MAXIMUM_RUNNERS = "2-core", 10


def gh_fields(body: dict, prefix: str = "") -> list[str]:
    """A request body as gh api fields: -f for text, -F for numbers and booleans, key[] for lists, key[sub].
    Each one quoted for the shell: these are pasted into one by an owner, and some values come from YAML."""
    fields = []
    for key, value in body.items():
        name = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, dict):
            fields += gh_fields(value, name)
        else:
            for item in value if isinstance(value, list) else [value]:
                flag = "-f" if isinstance(item, str) else "-F"
                text = str(item).lower() if isinstance(item, bool) else str(item)
                fields.append(f"{flag} {shlex.quote(name + ('[]' if isinstance(value, list) else '') + '=' + text)}")
    return fields


def step_runners(github, azure, folder: Path, out: Callable[[str], None] = print) -> None:
    """GitHub's side of the private runners, in the customer's organization: a network configuration naming the
    network settings resource in management's network, a runner group for the three repos only, and a
    GitHub-hosted runner in it. Then, once the state account has its private endpoint, the repos run their Azure
    jobs on that runner. Only an owner of the organization may make the three: anyone else gets them as
    commands to send to one, filled in, and changes nothing."""
    customer, bootstrap = settings(folder)
    org, repos = bootstrap["github"]["organization"], repositories(bootstrap)
    root, region = customer["root_id"], customer["primary_region"]
    if region not in platform.GITHUB_RUNNER_REGIONS:
        raise ConfigError(f"GitHub joins its runners to a customer's network only in some regions, not {region} (docs/platform.md, "
                          "GitHub-hosted runners). The pipelines stay on GitHub's public runners.")
    named = bootstrap_names(customer, bootstrap)
    management = f"/subscriptions/{customer['subscriptions']['management']}"
    settings_name = names.network(naming.settings(customer, root), "management", region, [])["github_network_settings"]
    network = azure.resource(f"{management}/resourceGroups/{named['network_resource_groups']['management']}/providers/GitHub.Network/networkSettings/{settings_name}", "2024-04-02")
    github_id = ((network or {}).get("tags") or {}).get("GitHubId")
    if not github_id:
        raise ConfigError(f"management's network has no GitHub network settings yet ({settings_name}): the platform's management stack makes it. "
                          "Run the platform's pipeline on main, then this step again.")
    endpoint = azure.resource(f"{management}/resourceGroups/{named['resource_group']}/providers/Microsoft.Network/privateEndpoints/{named['state_endpoint']}", "2024-07-01")
    ready = ((endpoint or {}).get("properties") or {}).get("provisioningState") == "Succeeded"
    waiting = "the state account has no private endpoint yet, and the runner reaches the state only through it: run mlz install bootstrap again"
    repo_ids = sorted(github.repo(repo)["id"] for repo in repos.values())
    configuration = {"name": RUNNER, "compute_service": "actions", "network_settings_ids": [github_id]}
    group = {"name": RUNNER, "visibility": "selected", "selected_repository_ids": repo_ids, "allows_public_repositories": False}
    runner = {"name": RUNNER, "image": {"id": "ubuntu-latest", "source": "github"},
              "size": bootstrap["github"].get("runner_size") or RUNNER_SIZE, "maximum_runners": MAXIMUM_RUNNERS}
    switch = [f"gh variable set {RUNS_ON} --repo {shlex.quote(repo)} --body {RUNNER}" for repo in repos.values()]

    if not github.is_owner(org):
        out(f"runners: you are not an owner of {org}, so nothing was changed. Send this to one of its owners: it makes the network "
            f"configuration {RUNNER} for {settings_name}, the runner group {RUNNER} for the three repos only, and the runner {RUNNER} in it.\n")
        out("\n".join([
            f"gh auth refresh -h github.com -s {RUNNER_SCOPES}",
            f"configuration=$(gh api -X POST {shlex.quote(f'orgs/{org}/settings/network-configurations')} {' '.join(gh_fields(configuration))} --jq .id)",
            f"group=$(gh api -X POST {shlex.quote(f'orgs/{org}/actions/runner-groups')} {' '.join(gh_fields(group))} -f network_configuration_id=\"$configuration\" --jq .id)",
            f"gh api -X POST {shlex.quote(f'orgs/{org}/actions/hosted-runners')} {' '.join(gh_fields(runner))} -F runner_group_id=\"$group\"",
        ]) + "\n")
        out("runners: then move the three repos onto the runner, as an admin on them"
            + ("" if ready else f". Not yet: {waiting}, then run these") + ":\n")
        out("\n".join(switch))
        return

    found = github.network_configuration(org, RUNNER)
    if found is None:
        try:
            found = github.create_network_configuration(org, configuration)
        except ConfigError as error:
            raise ConfigError(f"{error}\nIf {org} belongs to an enterprise account, an enterprise owner must first let its organizations make network "
                              "configurations: the enterprise's Settings, Policies, Hosted compute networking (docs/bootstrap.md).") from error
        out(f"runners: network configuration {RUNNER} made, for {settings_name}")
    elif found.get("network_settings_ids") != [github_id] or found.get("failover_network_enabled") or found.get("failover_network_settings_ids"):
        # Only management's network: a second or failover network would run jobs where the state is out of reach.
        raise ConfigError(f"{org} has a network configuration named {RUNNER} for other or further network settings; it must name {settings_name} "
                          "alone, with no failover. Delete or rename it, then run this step again.")
    else:
        out(f"runners: network configuration {RUNNER} is in place")

    made = github.runner_group(org, RUNNER)
    if made is None:
        made = github.create_runner_group(org, {**group, "network_configuration_id": found["id"]})
        out(f"runners: runner group {RUNNER} made, for the three repos only")
    else:
        # Kept to the three repos and the network: a wider group would let other repos run jobs inside it.
        if made.get("visibility") != "selected" or made.get("allows_public_repositories") or made.get("network_configuration_id") != found["id"]:
            github.update_runner_group(org, made["id"], {"visibility": "selected", "allows_public_repositories": False, "network_configuration_id": found["id"]})
        if github.runner_group_repositories(org, made["id"]) != set(repo_ids):
            github.set_runner_group_repositories(org, made["id"], repo_ids)
        out(f"runners: runner group {RUNNER} is for the three repos only")

    existing = github.hosted_runner(org, RUNNER)
    if existing is None:
        github.create_hosted_runner(org, {**runner, "runner_group_id": made["id"]})
        out(f"runners: runner {RUNNER} made ({runner['size']}, at most {MAXIMUM_RUNNERS} at a time)")
    elif existing.get("runner_group_id") != made["id"]:
        raise ConfigError(f"{org} has a runner named {RUNNER} outside the runner group {RUNNER}; move or delete it, then run this step again.")
    else:
        out(f"runners: runner {RUNNER} is in place")

    if not ready:
        out(f"runners: the repos stay on GitHub's public runners: {waiting}, then this step.")
        return
    for repo in repos.values():
        if github.variable(repo, RUNS_ON) != RUNNER:
            github.set_variable(repo, RUNS_ON, RUNNER)
    out(f"runners: the three repos run their plans, applies and publications on {RUNNER} from their next run. "
        f"To go back to GitHub's public runners: gh variable delete {RUNS_ON} --repo <repo>.")


# publish

def bootstrap_contract(terraform) -> dict:
    """The bootstrap's contract, read from its state in its account."""
    terraform.prepare()
    backend = terraform.work / "backend.hcl"
    if not backend.exists():
        raise ConfigError("the bootstrap's state is not in its account yet; run mlz install bootstrap first.")
    if terraform.run("init", "-input=false", "-reconfigure", f"-backend-config={backend}").returncode:
        raise ConfigError("terraform init failed; see above.")
    return json.loads(terraform.output("contract"))


def step_publish(github, terraform, folder: Path, out: Callable[[str], None] = print) -> str | None:
    """The bootstrap's contract, offered to the governance repo as a pull request like every other contract."""
    _, bootstrap = settings(folder)
    repo = repositories(bootstrap)["governance"]
    text = contract.render("bootstrap", bootstrap_contract(terraform))
    base = github.default_branch(repo)
    if github.file(repo, CONTRACT_PATH, base)[0] == text:
        out(f"publish: {repo} already has this {CONTRACT_PATH}")
        # A pull request left open by an earlier publish offers a contract that is no longer the deployed one:
        # merged, it would bring back old identities. Closed, as tools/ci/publish.sh does for the other contracts.
        for url in github.close_pulls(repo, CONTRACT_BRANCH, f"Closed by mlz install publish: {CONTRACT_PATH} on {base} already matches the bootstrap's state."):
            out(f"publish: closed {url}, which no longer matches the bootstrap")
        return None
    url = github.open_pull(repo, CONTRACT_BRANCH)
    if url and github.file(repo, CONTRACT_PATH, CONTRACT_BRANCH)[0] == text:
        out(f"publish: {url} already offers this {CONTRACT_PATH}")
        return url
    # Onto an open pull request's branch: a branch moved back to main would close its pull request.
    head = github.head(repo, CONTRACT_BRANCH) if url else github.reset_branch(repo, CONTRACT_BRANCH, base)
    github.commit_files(repo, CONTRACT_BRANCH, head, {CONTRACT_PATH: text}, "chore: bootstrap contract")
    url = url or github.create_pull(
        repo, CONTRACT_BRANCH, base, "chore: bootstrap contract",
        "The pipeline identities and the state account from the bootstrap. Governance's run merges it once it has "
        "checked and planned it, and the run on main that starts is governance's first deployment, which waits for "
        "its reviewer.\n\nFrom there the contracts go between the repos by themselves, each run on main waiting for "
        "its reviewer: platform, then vending. "
        "Hand each landing zone's subscription over before its YAML is merged, unless vending makes it where billing_scope "
        "says (docs/pipelines.md, The first run).\n")
    out(f"publish: {url} merges itself once governance's run has checked and planned it; its apply then waits for the reviewer")
    return url


# subscriptions

# What lets vending's apply identity make subscriptions billed to a scope (docs/vending.md, The subscription): a
# billing role, which neither Azure's role assignments nor Entra ID's roles give or show, by agreement. Checked on
# Microsoft Learn on 13 November 2026: "Assign Enterprise Agreement roles to service principals" (EA) and
# "Programmatically create Azure subscriptions for a Microsoft Customer Agreement" (MCA).
BILLING_ROLES = {
    "EA": {
        "role": "SubscriptionCreator", "id": "a0bcee42-bf30-4d1b-926a-48d21664ef71", "api": "2019-10-01-preview",
        "on": "the enrollment account",
        "granter": "the enrollment account's owner, its Account Owner (\"enrollment account owner (EA admin)\" in Microsoft's role table)",
        "portal": None,
    },
    "MCA": {
        "role": "Azure subscription creator", "id": "30000000-aaaa-bbbb-cccc-100000000006", "api": "2024-04-01",
        "on": "the invoice section",
        "granter": "an owner of the invoice section, of its billing profile or of the billing account",
        "portal": "Cost Management + Billing, the invoice section, Access control (IAM): Add, role Azure subscription creator, for the identity",
    },
}


GRANT_CHECKS, GRANT_WAIT = 10, 6.0  # how often, and how many seconds apart, a grant is looked for after it is asked


def grant_request(kind: str, scope: str, principal: str, tenant: str) -> tuple[str, str, dict]:
    """The billing API call that gives principal the role on scope: (method, path, body). An EA assignment is named by
    whoever makes it: here after the scope and the identity, so making it again changes nothing."""
    role = BILLING_ROLES[kind]
    assignment = {"principalId": principal, "principalTenantId": tenant, "roleDefinitionId": f"{scope}/billingRoleDefinitions/{role['id']}"}
    if kind == "EA":
        name = uuid.uuid5(uuid.NAMESPACE_URL, f"mlz-core/{scope}/{principal}")
        return "PUT", f"{scope}/billingRoleAssignments/{name}?api-version={role['api']}", {"properties": assignment}
    return "POST", f"{scope}/createBillingRoleAssignment?api-version={role['api']}", assignment


def az_rest(method: str, path: str, body: dict | None = None, query: str | None = None) -> str:
    """The same call as an Azure CLI command, for someone else to run."""
    command = ["az", "rest", "--method", method.lower(), "--url", f"https://management.azure.com{path}"]
    command += ["--body", json.dumps(body)] if body is not None else []
    command += ["--query", query] if query is not None else []
    return shlex.join(command)


def holds_role(azure, kind: str, scope: str, principal: str) -> bool | None:
    """Whether principal has the role on scope; None when the sign-in may not read the scope's role assignments."""
    role = BILLING_ROLES[kind]
    url = f"{scope}/billingRoleAssignments?api-version={role['api']}"
    try:
        while url:
            page = azure.request("GET", url)
            if any((a.get("properties") or {}).get("principalId") == principal
                   and str((a.get("properties") or {}).get("roleDefinitionId", "")).lower().endswith(role["id"])
                   for a in page.get("value", [])):
                return True
            url = page.get("nextLink")
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404):
            return None
        raise
    return False


def step_subscriptions(azure, terraform, folder: Path, scope: str, tenant: str, yes: bool = False,
                       out: Callable[[str], None] = print, ask: Callable[[str], str] = answer,
                       wait: Callable[[float], None] = time.sleep) -> bool:
    """Lets vending's apply identity make the landing zones' subscriptions billed to scope: gives it the billing role
    when this sign-in may, after asking; otherwise prints the role the person who may give it holds, and the command
    they run. Returns whether the identity has the role."""
    settings(folder)
    kind = vending.agreement(scope)
    if kind not in BILLING_ROLES:
        raise ConfigError(f"--billing-scope {scope!r} is an EA enrollment account (/providers/Microsoft.Billing/billingAccounts/<id>/"
                          "enrollmentAccounts/<id>) or an MCA invoice section (.../billingAccounts/<id>/billingProfiles/<id>/"
                          "invoiceSections/<id>), as billing_scope is in the vending repo; a CSP customer's subscriptions are "
                          "made in Fortytwo's Partner Center.")
    identity = (((bootstrap_contract(terraform).get("identities") or {}).get("vending") or {}).get("apply") or {})
    if not identity.get("principal_id"):
        raise ConfigError("the bootstrap's contract has no vending apply identity; run mlz install bootstrap first.")
    principal, role = identity["principal_id"], BILLING_ROLES[kind]
    who = f"vending's apply identity {identity.get('name', '')} (object ID {principal})".replace("  ", " ")
    out(f"subscriptions: {who} makes the subscriptions billed to {scope} once it has the {kind} role {role['role']} on {role['on']}.")
    held = holds_role(azure, kind, scope, principal)
    if held:
        out("subscriptions: it has it; vending can make them.")
        return True
    out("subscriptions: it does not have it yet." if held is False else "subscriptions: your sign-in may not read who has billing roles there, or there is no such scope.")
    method, path, body = grant_request(kind, scope, principal, tenant)
    if yes or ask("Give it the role now, signed in as you? [y/N] ").strip().lower() in ("y", "yes"):
        try:
            azure.request(method, path, body)
        except urllib.error.HTTPError as error:
            if error.code not in (400, 401, 403):
                raise
            out(f"subscriptions: Azure refused it (HTTP {error.code}): your sign-in may not give billing roles there.")
        else:
            # Taken is not given: an MCA grant answers 202 and finishes later, or fails later. Done only once the role
            # assignments show it, read as at the start; for about a minute.
            for _ in range(GRANT_CHECKS):
                if holds_role(azure, kind, scope, principal):
                    out(f"subscriptions: done; {who} has the role {role['role']} on {role['on']}.")
                    return True
                wait(GRANT_WAIT)
            out("subscriptions: Azure took the request, but its role assignments do not show the role yet: it may still be "
                "on its way, or have failed. Check again later (below), or have the person who may give it run the command.")
    # The person who may: what they must hold, and what they run.
    check = az_rest("GET", f"{scope}/billingRoleAssignments?api-version={role['api']}",
                    query=f'value[?properties.principalId==`"{principal}"`].properties.roleDefinitionId')
    lines = [f"subscriptions: {role['granter']} gives it. Send them this, to run in a bash shell (Azure Cloud Shell will do):", "",
             f"  az login --tenant {tenant}", f"  {az_rest(method, path, body)}", ""]
    if role["portal"]:
        lines += [f"  or in the Azure portal: {role['portal']} {identity.get('name', principal)}.", ""]
    lines += ["subscriptions: then, to check, by them or anyone who may read the billing roles there (it ends in "
              f"{role['id']}):", "", f"  {check}"]
    for line in lines:
        out(line)
    return False
