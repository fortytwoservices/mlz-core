"""Sets a new customer up, step by step; every step can be run again and changes only what is missing.

    python3 -m mlz install repos     --settings <dir>                 the three repos: private, main ruleset, starters
    python3 -m mlz install check     --settings <dir>                 the MLZ App's client ID and key in each repo
    python3 -m mlz install bootstrap --settings <dir> --tenant <id>   bootstrap/terraform, its state moved, subjects checked
    python3 -m mlz install handover  --settings <dir> --tenant <id>   the platform subscriptions into the top group
    python3 -m mlz install publish   --settings <dir>                 bootstrap.yaml offered to the governance repo

<dir> holds the customer's customer.yaml and bootstrap.yaml: a checkout of the governance repo, or for a new
customer a folder of the two files before the repos exist (repos then commits them to the governance repo).
Run from an mlz-core checkout (PYTHONPATH=tools), signed in to gh (admin on the customer's repos, workflow
scope) and, for bootstrap and handover, to the Azure CLI as someone who may create the top group.

The MLZ App itself is made by hand (GitHub needs a browser for that); check says whether it is set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from . import contract
from .files import read_yaml
from .merge import ConfigError
from .upgrade import RELEASE_PLEASE_MARKER, WORKFLOW

ROLES = ("governance", "platform", "vending")
CORE = Path(__file__).resolve().parents[2]
APP_VARIABLE, APP_SECRET = "MLZ_RELEASE_APP_CLIENT_ID", "MLZ_RELEASE_APP_PRIVATE_KEY"
# Changes reach main only through pull requests; main is never rewritten or deleted (decided 2026-11-11).
MAIN_RULESET = {
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
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": False,
        }},
    ],
}
CONTRACT_PATH, CONTRACT_BRANCH = "_generated/bootstrap.yaml", "mlz/bootstrap-contract"


def settings(folder: Path) -> tuple[dict, dict]:
    files = [folder / "customer.yaml", folder / "bootstrap.yaml"]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)}: --settings is the folder with the customer's customer.yaml and bootstrap.yaml.")
    return read_yaml(files[0]), read_yaml(files[1])


def repositories(bootstrap: dict) -> dict[str, str]:
    github = bootstrap.get("github") or {}
    names = github.get("repositories") or {}
    if not github.get("organization") or set(names) != set(ROLES):
        raise ConfigError("bootstrap.yaml needs github.organization and github.repositories for governance, platform and vending.")
    return {role: f"{github['organization']}/{names[role]}" for role in ROLES}


def release() -> str:
    return "v" + json.loads((CORE / ".release-please-manifest.json").read_text())["."]


def starter_files(role: str, folder: Path) -> dict[str, str]:
    """The role's starter as a new repo gets it, with the customer's own settings for governance."""
    base = CORE / "starter" / f"mlz-{role}"
    files = {str(p.relative_to(base)): RELEASE_PLEASE_MARKER.sub("", p.read_text()) for p in sorted(base.rglob("*")) if p.is_file()}
    if role == "governance":
        files.update({name: (folder / name).read_text() for name in ("customer.yaml", "bootstrap.yaml")})
    return files


def answer(question: str) -> str:
    """The person's answer; none (no terminal, as in CI or a script) counts as no."""
    try:
        return input(question)
    except EOFError:
        return ""


# repos and check

def step_repos(github, folder: Path, out: Callable[[str], None] = print) -> None:
    _, bootstrap = settings(folder)
    for role, repo in repositories(bootstrap).items():
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
            if not found.get("private"):
                out(f"{repo}: it is not private; change that in its settings if it should be")
            if found.get("default_branch", "main") != "main":
                out(f"{repo}: its default branch is {found['default_branch']}, but the pipelines run on main; rename it")
        # A repo made here whose starter commit failed (or any repo without the workflow) gets the starter's
        # missing files; a file that is there already is never replaced.
        branch = github.default_branch(repo)
        if created or github.file(repo, WORKFLOW, branch)[0] is None:
            missing = {path: text for path, text in starter_files(role, folder).items() if created or github.file(repo, path, branch)[0] is None}
            github.commit_files(repo, branch, github.head(repo, branch), missing, f"chore: mlz-{role} from mlz-core {release()}")
            out(f"{repo}: {'created, private, with' if created else 'given the missing files of'} the {role} starter")
        else:
            out(f"{repo}: exists, files left as they are")
        github.upsert_ruleset(repo, MAIN_RULESET)
        out(f"{repo}: ruleset main (pull requests only, no force push or deletion)")


def step_check(github, folder: Path, out: Callable[[str], None] = print) -> None:
    _, bootstrap = settings(folder)
    problems = []
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
    if problems:
        raise ConfigError("\n  ".join(["the MLZ App is not set up everywhere:"] + problems))


# bootstrap

class Terraform:
    """bootstrap/terraform, copied with what it uses into a folder of the customer's own, so its local state,
    backend config and plan never mix with another customer's."""

    def __init__(self, work: Path, tenant: str):
        # Absolute, since terraform runs with -chdir into the root below it.
        self.work = work.expanduser().resolve()
        self.root = self.work / "bootstrap" / "terraform"
        self.env = {**os.environ, "ARM_TENANT_ID": tenant}
        self.env.setdefault("GITHUB_TOKEN", subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip())

    def prepare(self) -> None:
        for source, target in ((CORE / "bootstrap" / "terraform", self.root), (CORE / "modules" / "naming", self.work / "modules" / "naming")):
            target.mkdir(parents=True, exist_ok=True)
            for path in [*source.glob("*.tf"), *source.glob(".terraform.lock.hcl")]:
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
    _, bootstrap = settings(folder)
    terraform.prepare()
    backend, pending = terraform.work / "backend.hcl", terraform.work / "backend.hcl.pending"
    override = terraform.root / "backend_override.tf"
    local = not backend.exists()
    if local:
        override.write_text(f'terraform {{\n  backend "local" {{\n    path = "{terraform.work / "bootstrap.tfstate"}"\n  }}\n}}\n')
    else:
        override.unlink(missing_ok=True)
    init = ["init", "-input=false", "-reconfigure"] + ([] if local else [f"-backend-config={backend}"])
    if terraform.run(*init).returncode:
        raise ConfigError("terraform init failed; see above.")
    plan = terraform.work / "bootstrap.tfplan"
    variables = ["-var", f"customer_file={(folder / 'customer.yaml').resolve()}", "-var", f"bootstrap_file={(folder / 'bootstrap.yaml').resolve()}"]
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

    def request(self, method: str, url: str) -> dict:
        # Without no-cache, getEntities answered from a cache for minutes after a move: three subscriptions just
        # moved into the tree still showed in unmanaged_subscriptions (TCAF, 2026-11-11), so a second handover
        # would have asked to move them again.
        req = urllib.request.Request(url if url.startswith("https://") else f"https://management.azure.com{url}", method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Length": "0", "Cache-Control": "no-cache"})
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


# publish

def step_publish(github, terraform, folder: Path, out: Callable[[str], None] = print) -> str | None:
    """The bootstrap's contract, offered to the governance repo as a pull request like every other contract."""
    _, bootstrap = settings(folder)
    repo = repositories(bootstrap)["governance"]
    terraform.prepare()
    backend = terraform.work / "backend.hcl"
    if not backend.exists():
        raise ConfigError("the bootstrap's state is not in its account yet; run mlz install bootstrap first.")
    if terraform.run("init", "-input=false", "-reconfigure", f"-backend-config={backend}").returncode:
        raise ConfigError("terraform init failed; see above.")
    text = contract.render("bootstrap", json.loads(terraform.output("contract")))
    base = github.default_branch(repo)
    if github.file(repo, CONTRACT_PATH, base)[0] == text:
        out(f"publish: {repo} already has this {CONTRACT_PATH}")
        return None
    head = github.reset_branch(repo, CONTRACT_BRANCH, base)
    github.commit_files(repo, CONTRACT_BRANCH, head, {CONTRACT_PATH: text}, "chore: bootstrap contract")
    url = github.open_pull(repo, CONTRACT_BRANCH) or github.create_pull(
        repo, CONTRACT_BRANCH, base, "chore: bootstrap contract",
        "The pipeline identities and the state account from the bootstrap. Merging it runs governance's first "
        "deployment with them.\n\nThen, in order: merge the governance contract into platform and vending, platform's "
        "into governance and vending (governance's second run points the Private Link DNS policy at the zones), "
        "and hand each landing zone's subscription over before its YAML is merged (docs/pipelines.md, The first run).\n")
    out(f"publish: {url}")
    return url
