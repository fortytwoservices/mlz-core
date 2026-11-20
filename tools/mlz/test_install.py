"""Tests for mlz install, with GitHub, Terraform and Azure in memory. YAML is read with terraform's yamldecode."""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

from mlz import install, mirror
from mlz.github import GitHub
from mlz.merge import ConfigError

CUSTOMER = """name: Acme
root_id: acme
root_display_name: Acme
primary_region: norwayeast
subscriptions:
  management: 00000000-0000-0000-0000-000000000001
  connectivity: 00000000-0000-0000-0000-000000000002
  identity: 00000000-0000-0000-0000-000000000003
"""
BOOTSTRAP = """github:
  organization: acme
  repositories:
    governance: mlz-governance
    platform: mlz-platform
    vending: mlz-vending
  platform_team: platform
  bypass_team: mlz-admins
  reviewers: [acme-lead]
"""
REPOS = {"governance": "acme/mlz-governance", "platform": "acme/mlz-platform", "vending": "acme/mlz-vending"}
CORE_COPY = "acme/mlz-core"  # the organization's copy of mlz-core


WORKFLOW = ".github/workflows/mlz.yml"
TEAM_IDS = {"platform": 11, "mlz-admins": 12}


class FakeGitHub:
    def __init__(self, existing=(), private=True, variables=(), secrets=(), prefixes=None, files=None, open_pull=None,
                 org_branch="main", existing_branch="main", half_made=(), rulesets=(), empty=(), org_plan="enterprise",
                 teams=("platform", "mlz-admins"), secret=(), writers=None, owner_errors=None, ruleset_review=True, dismiss_stale=True, owned=True,
                 required_check=True, strict=True, core=True, not_executable=(), push_guard=True, can_bypass="always", main_bypass=True):
        self.org_plan = org_plan
        # Code owners: the organization's teams, the (team, repo) pairs with write access, GitHub's errors in each
        # repo's CODEOWNERS (None: not read yet), and whether the main ruleset requires a code owner's review.
        self.teams, self.secret, self.granted = set(teams), set(secret), []
        self.writers = {("platform", repo) for repo in REPOS.values()} if writers is None else set(writers)
        self.owner_errors, self.ruleset_review = owner_errors or {}, ruleset_review
        self.dismiss_stale = dismiss_stale  # whether the repo's main dismisses an approval on a later push
        # And whether it requires the role's check to pass.
        self.required_check, self.strict = required_check, strict
        self.repos = {repo: {"private": private, "default_branch": existing_branch} for repo in existing}
        self.org_branch = org_branch
        # An existing MLZ repo has its workflow, unless it is half-made (created, its starter commit failed).
        self.files = {(repo, WORKFLOW): ("on: push\n", "sha") for repo in existing if repo not in half_made}
        # And its CODEOWNERS, unless it was set up before code owners (owned=False).
        self.files.update({(repo, ".github/CODEOWNERS"): ("* @acme/platform\n", "sha") for repo in existing if owned and repo not in half_made})
        self.files.update(files or {})
        self.renamed, self.empty = [], set(empty)
        self.existing_rulesets = set(rulesets)
        # The organization's copy of mlz-core (tools/mlz/mirror.py): made already, at this release, unless core=False.
        self.tags, self.access = set(), {}
        if core:
            self.repos[CORE_COPY] = {"private": True, "default_branch": "releases"}
            self.tags.add((CORE_COPY, install.release()))
            self.access[CORE_COPY] = "organization"
            self.existing_rulesets |= {(CORE_COPY, "releases"), (CORE_COPY, "releases-branch"), (CORE_COPY, "no-release-branches")}
        self.variables, self.secrets = set(variables), set(secrets)
        self.prefixes = prefixes or {repo: f"repo:acme@1/{repo.split('/')[1]}@2" for repo in REPOS.values()}
        self._open_pull = open_pull
        self.created, self.commits, self.made_rulesets, self.pulls, self.branches, self.executable = [], [], [], [], [], []
        self.unexecutable = set(not_executable)  # (repo, path): on main without its mode, as a stopped run left it
        self.ruleset_bodies = {}
        # The push ruleset that guards .github/ (finding 17), as GitHub would answer for the person signed in.
        self.push_guard, self.can_bypass = push_guard, can_bypass
        self.main_bypass = main_bypass  # whether the ruleset main (ID 7) lets the bypass team merge without the approval

    def repo(self, repo):
        return self.repos.get(repo)

    def plan(self, org):
        return self.org_plan

    def create_repo(self, owner, name, first_commit=True):
        self.created.append(f"{owner}/{name}" + ("" if first_commit else " (empty)"))
        self.repos[f"{owner}/{name}"] = {"private": True, "default_branch": self.org_branch}

    def tag_exists(self, repo, tag):
        return (repo, tag) in self.tags

    def compare(self, repo, base, head):
        # The copy's branch holds each release it has a tag for, here.
        return "identical" if (repo, base) in self.tags else None

    def actions_access(self, repo):
        return self.access.get(repo)

    def set_actions_access(self, repo, level):
        self.access[repo] = level

    def is_empty(self, repo):
        return repo in self.empty

    def default_branch(self, repo):
        return self.repos.get(repo, {}).get("default_branch", "main")

    def rename_branch(self, repo, branch, name):
        self.renamed.append((repo, branch, name))
        self.repos[repo]["default_branch"] = name

    def head(self, repo, branch):
        return f"head-of-{branch}"

    def commit_files(self, repo, branch, head, files, message):
        self.commits.append((repo, branch, head, files, message))

    def not_executable(self, repo, ref, paths):
        return sorted(path for path in paths if (repo, path) in self.unexecutable)

    def make_executable(self, repo, branch, paths, message):
        self.executable.append((repo, branch, tuple(paths)))
        return True

    def add_ruleset(self, repo, ruleset):
        if (repo, ruleset["name"]) in self.existing_rulesets:
            return False
        self.made_rulesets.append((repo, ruleset["name"]))
        self.ruleset_bodies[(repo, ruleset["name"])] = ruleset
        return True

    def has_variable(self, repo, name):
        return (repo, name) in self.variables

    def team(self, org, slug):
        return {"slug": slug, "id": TEAM_IDS.get(slug, 1), "privacy": "secret" if slug in self.secret else "closed"} if slug in self.teams else None

    def rulesets(self, repo):
        main = {**install.main_ruleset("platform", TEAM_IDS["mlz-admins"] if self.main_bypass else None), "id": 7}
        if not self.push_guard:
            return [main]
        return [main, {**install.push_ruleset(TEAM_IDS["mlz-admins"]), "id": 9, "current_user_can_bypass": self.can_bypass}]

    def team_can_write(self, org, slug, repo):
        return (slug, repo) in self.writers

    def grant_team(self, org, slug, repo):
        self.granted.append((slug, repo))
        self.writers.add((slug, repo))

    def codeowners_errors(self, repo, ref):
        return self.owner_errors.get(repo, [])

    def branch_rules(self, repo, branch):
        role = next(role for role, name in REPOS.items() if name == repo)
        checks = [{"context": f"{role} / check", "integration_id": 15368}] if self.required_check is True else self.required_check(role)
        return [{"type": "deletion", "ruleset_id": 7}, {"type": "pull_request", "ruleset_id": 7, "parameters": {"require_code_owner_review": self.ruleset_review,
                                                                                          "dismiss_stale_reviews_on_push": self.dismiss_stale}},
                {"type": "required_status_checks", "ruleset_id": 8, "parameters": {"strict_required_status_checks_policy": self.strict, "required_status_checks": checks}}]

    def has_secret(self, repo, name):
        return (repo, name) in self.secrets

    def subject_prefix(self, repo):
        return self.prefixes[repo]

    def file(self, repo, path, ref):
        # (repo, path, ref) for a file on one branch only; (repo, path) for one on every branch.
        return self.files.get((repo, path, ref)) or self.files.get((repo, path), (None, None))

    def reset_branch(self, repo, branch, base):
        self.branches.append((repo, branch, base))
        return "head-of-main"

    def open_pull(self, repo, branch):
        return self._open_pull

    def close_pulls(self, repo, branch, comment):
        if not self._open_pull:
            return []
        self.closed = [(repo, branch, comment)]
        return [self._open_pull]

    def create_pull(self, repo, branch, base, title, body):
        self.pulls.append((repo, branch, base, title, body))
        return f"https://github.com/{repo}/pull/1"


# The JSON of a plan as terraform show -json gives it: one resource to add, or nothing (tools/ci/summary.py reads it).
ONE_CHANGE = {"applyable": True, "complete": True, "errored": False, "output_changes": {}, "resource_changes": [
    {"address": "azapi_resource.x", "type": "azapi_resource", "change": {"actions": ["create"], "before": None, "after": {"name": "x"}, "after_unknown": {}}}]}
NO_CHANGE = {"applyable": False, "complete": True, "errored": False, "output_changes": {}, "resource_changes": []}


class FakeTerraform:
    """Answers like terraform would. plan_status: 1 for a plan that fails, 2 for one with a change, 0 for one without;
    plan_json, a plan of its own; show_fails, a saved plan that show cannot read. migrate_failures: how many state moves
    fail before one succeeds."""

    def __init__(self, work: Path, plan_status=2, migrate_failures=0, subjects=None, state_endpoint=False, contract_identities=None, plan_json=None,
                 show_fails=False):
        self.work, self.root = work, work / "bootstrap" / "terraform"
        self.contract_identities = contract_identities or {}
        self.plan_json = plan_json if plan_json is not None else ONE_CHANGE if plan_status == 2 else NO_CHANGE
        self.plan_status, self.migrate_failures, self.show_fails = plan_status, migrate_failures, show_fails
        self.state_endpoint = state_endpoint
        self.subjects = subjects or {f"{role}-plan": f"repo:acme@1/mlz-{role}@2:pull_request" for role in install.ROLES}
        self.calls: list[tuple] = []

    def prepare(self):
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, *arguments, capture=False):
        self.calls.append(arguments)
        status, shown, error = 0, "", ""
        if arguments[0] == "plan":  # exits 0 or 1 only: no -detailed-exitcode
            status = 1 if self.plan_status == 1 else 0
            if not status:
                Path(next(a for a in arguments if a.startswith("-out="))[len("-out="):]).write_text("plan")
        elif arguments[:2] == ("show", "-json"):
            if self.show_fails:
                status, error = 1, "Error: Failed to read the given file as a state or plan file\n"
            else:
                shown = json.dumps(self.plan_json)
        elif "-migrate-state" in arguments:
            status = 1 if self.migrate_failures > 0 else 0
            self.migrate_failures -= 1
        return subprocess.CompletedProcess(arguments, status, shown, error)

    def output(self, name):
        return json.dumps({
            "backend_config": 'storage_account_name = "stacmestate1234"\ncontainer_name = "bootstrap"\nkey = "bootstrap.tfstate"\n',
            "oidc_subjects": self.subjects,
            "state_private_endpoint": self.state_endpoint,
            "contract": {"top_management_group": "acme", "state": {"subscription": "00000000-0000-0000-0000-000000000001",
                         "resource_group": "rg-acme-bootstrap-nwe", "storage_account": "stacmestate1234"}, "identities": self.contract_identities},
        }[name])


class FakeAzure:
    def __init__(self, parents=None, resources=None):
        self._parents = parents or {}
        self.resources = resources or {}
        self.moves = []

    def parents(self):
        return self._parents

    def move(self, group, subscription):
        self.moves.append((group, subscription))

    def resource(self, resource_id, api_version):
        return self.resources.get(resource_id)


TENANT = "00000000-0000-0000-0000-0000000000aa"
PRINCIPAL = "11111111-1111-1111-1111-111111111111"
EA = "/providers/Microsoft.Billing/billingAccounts/1234567/enrollmentAccounts/7654321"
MCA = "/providers/Microsoft.Billing/billingAccounts/a1b2:c3d4_2019-05-31/billingProfiles/AB12-CD34/invoiceSections/EF56-GH78"
VENDING_IDENTITY = {"vending": {"apply": {"name": "id-acme-vending-apply-nwe", "client_id": "c", "principal_id": PRINCIPAL}}}


class BillingAzure:
    """The billing API, as a sign-in that may or may not read and give billing roles sees it."""

    def __init__(self, assignments=(), readable=True, grantable=True, shows_after=0):
        self.assignments, self.readable, self.grantable = list(assignments), readable, grantable
        # A grant shows in the role assignments after this many reads, as an MCA grant answers 202 and finishes later;
        # None: never, as one that failed later.
        self.shows_after, self.pending = shows_after, None
        self.calls = []

    def request(self, method, url, body=None):
        self.calls.append((method, url, body))
        if method == "GET" and not self.readable or method != "GET" and not self.grantable:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        if method != "GET":
            assignment = body.get("properties", body)
            self.pending = (self.shows_after, {"properties": {"principalId": assignment["principalId"], "roleDefinitionId": assignment["roleDefinitionId"]}})
            return {}
        if self.pending is not None and self.pending[0] is not None:
            reads, assignment = self.pending
            if reads <= 0:
                self.assignments.append(assignment)
                self.pending = None
            else:
                self.pending = (reads - 1, assignment)
        return {"value": self.assignments}


class RunnersGitHub(FakeGitHub):
    """The organization's runner settings, as an owner (or not) sees them."""

    def __init__(self, owner=True, configurations=(), groups=(), runners=(), fail_configuration=False, pins=None, **kwargs):
        super().__init__(existing=REPOS.values(), **kwargs)
        # Each repo's workflow calls mlz-core's role.yml at the release it pins (this one, unless given).
        for repo in REPOS.values():
            pin = (pins or {}).get(repo, install.release())
            core = CORE_COPY if "/" not in pin else pin.split("@")[0]
            ref = pin if "/" not in pin else pin.split("@")[1]
            self.files[(repo, WORKFLOW)] = (f"jobs:\n  check:\n    uses: {core}/.github/workflows/role.yml@{ref} # x\n", "sha")
        for number, repo in enumerate(REPOS.values()):
            self.repos[repo]["id"] = 101 + number
        self.owner, self.fail_configuration = owner, fail_configuration
        self.configurations, self.groups, self.runners = list(configurations), list(groups), list(runners)
        self.group_repositories, self.values, self.calls = {}, {}, []
        self.online = []  # the self-hosted runners registered in the group

    def is_owner(self, org):
        return self.owner

    def network_configuration(self, org, name):
        return next((c for c in self.configurations if c["name"] == name), None)

    def create_network_configuration(self, org, body):
        if self.fail_configuration:
            raise ConfigError("gh api POST orgs/acme/settings/network-configurations: HTTP 403")
        self.calls.append(("configuration", body))
        self.configurations.append({**body, "id": "CONFIG"})
        return self.configurations[-1]

    def runner_group(self, org, name):
        return next((g for g in self.groups if g["name"] == name), None)

    def create_runner_group(self, org, body):
        self.calls.append(("group", body))
        self.groups.append({**body, "id": 7})
        self.group_repositories[7] = set(body["selected_repository_ids"])
        return self.groups[-1]

    def update_runner_group(self, org, group, body):
        self.calls.append(("update group", body))

    def runner_group_repositories(self, org, group):
        return self.group_repositories.get(group, set())

    def set_runner_group_repositories(self, org, group, repositories):
        self.calls.append(("group repositories", repositories))

    def hosted_runner(self, org, name):
        return next((r for r in self.runners if r["name"] == name), None)

    def group_runners(self, org, group):
        return self.online

    def create_hosted_runner(self, org, body):
        self.calls.append(("runner", body))

    def variable(self, repo, name):
        return self.values.get((repo, name))

    def set_variable(self, repo, name, value):
        self.calls.append(("variable", repo, name, value))
        self.values[(repo, name)] = value


MANAGEMENT = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups"
# By the names tools/mlz/names.py gives them, from the customer's template.
NETWORK_SETTINGS = f"{MANAGEMENT}/rg-acme-mgt-net-pd-nwe-01/providers/GitHub.Network/networkSettings/ghns-acme-mgt-pd-nwe-01"
STATE_ENDPOINT = f"{MANAGEMENT}/rg-acme-mlz-pd-nwe-01/providers/Microsoft.Network/privateEndpoints/pep-acme-mlz-state-pd-nwe-01"


VAULT = f"{MANAGEMENT}/rg-acme-mlz-pd-nwe-01/providers/Microsoft.KeyVault/vaults/kv-acme-mlz-pd-nwe-01"
RUNNERS_SUBNET = f"{MANAGEMENT}/rg-acme-mgt-net-pd-nwe-01/providers/Microsoft.Network/virtualNetworks/vnet-acme-mgt-pd-nwe-01/subnets/snet-runners"
APP_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEnotarealkey\n-----END RSA PRIVATE KEY-----\n"
UPLOAD = "https://kv-acme-mlz-pd-nwe-01.vault.azure.net/secrets/github-runner-app-key?api-version=7.4"
# GitHub's labels on one of the bootstrap's runners: its own three, and the one MLZ_RUNS_ON names.
LABELS = [{"name": "self-hosted"}, {"name": "linux"}, {"name": "x64"}, {"name": "mlz"}]


def refused(code, message, status=403):
    """Key Vault's answer to a refused write, as its data plane sends it."""
    body = json.dumps({"error": {"code": "Forbidden", "message": message, "innererror": {"code": code}}}).encode()
    return urllib.error.HTTPError(UPLOAD, status, "Forbidden", {}, io.BytesIO(body))


class SelfHostedAzure(FakeAzure):
    """Management's network, the bootstrap's vault, Key Vault's data plane and the subscription's prerequisites."""

    def __init__(self, secret=False, endpoint=True, refusals=(), feature="Registered", accepted=False, vault=True, subnet=True):
        resources = {}
        if subnet:
            resources[RUNNERS_SUBNET] = {"name": "snet-runners"}
        if vault:
            resources[VAULT] = {"properties": {"vaultUri": "https://kv-acme-mlz-pd-nwe-01.vault.azure.net/",
                                               "networkAcls": {"defaultAction": "Deny", "bypass": "None", "ipRules": []}}}
        if secret:
            resources[f"{VAULT}/secrets/github-runner-app-key"] = {"name": "github-runner-app-key"}
        if endpoint:
            resources[STATE_ENDPOINT] = {"properties": {"provisioningState": "Succeeded"}}
        super().__init__(resources=resources)
        self.refusals, self.feature, self.accepted = list(refusals), feature, accepted
        self.calls, self.uploads = [], []

    def object_id(self):
        return "33333333-3333-3333-3333-333333333333"

    def request(self, method, url, body=None):
        self.calls.append((method, url.split("?")[0], body))
        if method == "GET" and url.split("?")[0].endswith("/agreements/current"):
            return {"name": "ubuntu-latest-gen2", "properties": {"accepted": self.accepted, "signature": "SIGNED"}}
        if method == "GET" and "/features/EncryptionAtHost" in url:
            return {"properties": {"state": self.feature}}
        return {}

    def vault(self, method, url, body=None):
        if self.refusals:
            raise self.refusals.pop(0)
        self.uploads.append((method, url, body))
        return {}


def runners_azure(endpoint=True):
    resources = {NETWORK_SETTINGS: {"tags": {"GitHubId": "GHID123"}}}
    if endpoint:
        resources[STATE_ENDPOINT] = {"properties": {"provisioningState": "Succeeded"}}
    return FakeAzure(resources=resources)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "settings"
        self.folder.mkdir()
        (self.folder / "customer.yaml").write_text(CUSTOMER)
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP)
        self.work = Path(self.tmp.name) / "work"
        self.printed: list[str] = []

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, line):
        self.printed.append(line)

    # repos and check

    def test_creates_missing_repos_from_the_starters(self):
        github = FakeGitHub(existing=["acme/mlz-platform"])
        install.step_repos(github, self.folder, self.out)
        self.assertEqual(github.created, ["acme/mlz-governance", "acme/mlz-vending"])
        commits = {repo: files for repo, _, _, files, _ in github.commits}
        self.assertEqual(set(commits), {"acme/mlz-governance", "acme/mlz-vending"})
        governance = commits["acme/mlz-governance"]
        self.assertEqual(governance["customer.yaml"], CUSTOMER)  # the customer's own, not the starter's example
        self.assertEqual(governance["bootstrap.yaml"], BOOTSTRAP)
        workflow = commits["acme/mlz-vending"][".github/workflows/mlz.yml"]
        self.assertIn("role: vending", workflow)
        # The layout of folders named after their stacks; vending's examples outside landing-zones/.
        self.assertTrue({"defaults/landing-zone-defaults.yaml", "landing-zones/README.md", "examples/payments/prod.yaml",
                         "examples/payments/workload-defaults.yaml"} <= set(commits["acme/mlz-vending"]))
        self.assertTrue({"connectivity/norwayeast.yaml", "firewall-rules/platform.yaml"} <= set(install.starter_files("platform", self.folder)))
        self.assertNotIn("x-release-please", workflow)
        # ./mlz, which the commit cannot make executable: a commit of its own, on the new repo's main.
        self.assertTrue(all(commits[repo]["mlz"].startswith("#!/bin/sh\n") for repo in commits))
        self.assertEqual(github.executable, [("acme/mlz-governance", "main", ("mlz",)), ("acme/mlz-vending", "main", ("mlz",))])
        # main lets the bypass team merge without the approval, pull requests only (decision D8).
        self.assertEqual(github.ruleset_bodies[("acme/mlz-vending", "main")]["bypass_actors"], [{"actor_id": TEAM_IDS["mlz-admins"], "actor_type": "Team", "bypass_mode": "pull_request"}])
        self.assertNotIn("bypass_actors", github.ruleset_bodies[("acme/mlz-vending", "main-checks")])  # the check binds the team too
        # The push ruleset first, after the starter's first commit; then main.
        self.assertEqual(github.made_rulesets, [(repo, name) for repo in REPOS.values() for name in ("pipeline-files", "main", "main-checks")])
        self.assertIn("acme/mlz-platform: exists, files left as they are", self.printed)
        # Only the repo that existed: its branches may predate the push ruleset.
        warned = [line for line in self.printed if "pushed before the ruleset" in line]
        self.assertEqual(warned, ["acme/mlz-platform: its branches pushed before the ruleset keep any change under .github/ they have; delete those you do not know"])

    def test_an_existing_public_repo_is_named_not_changed(self):
        github = FakeGitHub(existing=REPOS.values(), private=False)
        install.step_repos(github, self.folder, self.out)
        self.assertEqual((github.created, github.commits), ([], []))
        self.assertTrue(any("is not private" in line for line in self.printed))

    def test_a_new_repo_on_another_default_branch_moves_to_main_first(self):
        github = FakeGitHub(org_branch="master")
        install.step_repos(github, self.folder, self.out)
        self.assertEqual(github.renamed, [(repo, "master", "main") for repo in REPOS.values()])
        self.assertEqual({branch for _, branch, _, _, _ in github.commits}, {"main"})

    def test_the_bootstrap_s_names_come_from_the_template_or_bootstrap_yaml(self):
        customer, bootstrap = install.settings(self.folder)
        named = install.bootstrap_names(customer, bootstrap)
        self.assertEqual((named["resource_group"], named["storage_account"], named["identities"]["governance-plan"], named["state_endpoint"]),
                         ("rg-acme-mlz-pd-nwe-01", "stacmemlzstatepdnwe01", "id-acme-mlz-govplan-pd-nwe-01", "pep-acme-mlz-state-pd-nwe-01"))
        self.assertEqual(sorted(named["identities"]), sorted(f"{role}-{kind}" for role in install.ROLES for kind in ("plan", "apply")))
        # The platform's it points at, as the platform names them.
        self.assertEqual(named["network_resource_groups"], {"management": "rg-acme-mgt-net-pd-nwe-01", "identity": "rg-acme-idn-net-pd-nwe-01"})
        self.assertEqual((named["management_network"], named["state_endpoint_subnet"], named["dns_resource_group"]),
                         ("vnet-acme-mgt-pd-nwe-01", "snet-private-endpoints", "rg-acme-dns-pd-nwe-01"))
        # A name taken elsewhere in Azure, or one to keep: bootstrap.yaml's names:.
        named = install.bootstrap_names(customer, {**bootstrap, "names": {"storage_account": "stacmestate1234"}})
        self.assertEqual(named["storage_account"], "stacmestate1234")
        for wrong, message in (({"storage_account": "st-acme"}, "names.storage_account: storage_account name st-acme"),
                               ({"identity": "id-x"}, "names.identity is not one the bootstrap takes; use resource_group, storage_account"),
                               (["st"], "names maps resource_group or storage_account")):
            with self.assertRaisesRegex(ConfigError, message):
                install.bootstrap_names(customer, {**bootstrap, "names": wrong})
        with self.assertRaisesRegex(ConfigError, "set naming.org"):
            install.bootstrap_names({**customer, "root_id": "acme-corp"}, bootstrap)

    def test_an_unknown_bootstrap_setting_is_refused(self):
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP + "state_operator: {}\n")
        with self.assertRaisesRegex(ConfigError, "bootstrap.yaml: state_operator is not a setting"):
            install.step_repos(FakeGitHub(), self.folder, self.out)
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("  reviewers:", "  reviewer:"))
        with self.assertRaisesRegex(ConfigError, "bootstrap.yaml: github.reviewer is not a setting"):
            install.step_repos(FakeGitHub(), self.folder, self.out)

    def test_a_half_made_repo_gets_its_starter_as_a_pull_request(self):
        github = FakeGitHub(existing=REPOS.values(), half_made=["acme/mlz-vending"])
        install.step_repos(github, self.folder, self.out)
        repo, branch, _, files, _ = github.commits[0]
        self.assertEqual((repo, branch), ("acme/mlz-vending", "mlz/starter"))  # its main may take only pull requests
        self.assertIn(WORKFLOW, files)
        self.assertEqual([(r, b, base) for r, b, base, *_ in github.pulls], [("acme/mlz-vending", "mlz/starter", "main")])
        self.assertIn("acme/mlz-vending: the vending starter's missing files are offered in https://github.com/acme/mlz-vending/pull/1", self.printed)

    def test_an_empty_existing_repo_asks_for_a_first_commit(self):
        github = FakeGitHub(existing=REPOS.values(), half_made=["acme/mlz-governance"], empty=["acme/mlz-governance"])
        with self.assertRaisesRegex(ConfigError, "acme/mlz-governance exists but is empty; give it a first commit on main"):
            install.step_repos(github, self.folder, self.out)
        self.assertEqual(github.commits, [])

    def test_an_existing_ruleset_named_main_is_left_alone(self):
        github = FakeGitHub(existing=REPOS.values(), rulesets=[("acme/mlz-platform", "main")])
        install.step_repos(github, self.folder, self.out)
        self.assertNotIn(("acme/mlz-platform", "main"), github.made_rulesets)  # the customer's own may be stricter
        self.assertIn("acme/mlz-platform: has a ruleset named main already, left as it is; check that it allows changes only through pull requests, "
                      "and requires a code owner's review (mlz install check says)", self.printed)

    def test_an_adopted_repo_without_the_workflow_keeps_its_own_files(self):
        own = ("acme/mlz-governance", "customer.yaml")
        github = FakeGitHub(existing=REPOS.values(), half_made=["acme/mlz-governance"], files={own: ("name: Ours\n", "sha")})
        install.step_repos(github, self.folder, self.out)
        added = github.commits[0][3]
        self.assertIn(WORKFLOW, added)
        self.assertNotIn("customer.yaml", added)  # already there: never replaced
        self.assertIn("bootstrap.yaml", added)

    def test_an_adopted_repo_s_own_gitignore_keeps_its_lines_and_gets_the_starter_s(self):
        own = ("acme/mlz-governance", ".gitignore")
        github = FakeGitHub(existing=REPOS.values(), half_made=["acme/mlz-governance"], files={own: (".terraform/\n*.tfstate\n", "sha")})
        install.step_repos(github, self.folder, self.out)
        added = github.commits[0][3]
        self.assertIn(".idea/jsonSchemas.xml", added)
        self.assertTrue(added[".gitignore"].startswith(".terraform/\n*.tfstate\n\n# JetBrains"), added[".gitignore"])
        self.assertTrue(added[".gitignore"].endswith("/.idea/*\n!/.idea/jsonSchemas.xml\n"), added[".gitignore"])

    def test_an_adopted_repo_on_another_default_branch_is_named(self):
        install.step_repos(FakeGitHub(existing=REPOS.values(), existing_branch="master"), self.folder, self.out)
        self.assertIn("acme/mlz-platform: its default branch is master, but the pipelines run on main; rename it", self.printed)

    def test_a_relative_work_folder_becomes_absolute(self):
        previous = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "not-used"
        try:
            # A supplied token means gh is never run: a broken gh on the path would fail here otherwise.
            with unittest.mock.patch("subprocess.run", side_effect=AssertionError("gh was run")):
                terraform = install.Terraform(Path("relative/work"), "tenant")
        finally:
            if previous is None:
                os.environ.pop("GITHUB_TOKEN")
            else:
                os.environ["GITHUB_TOKEN"] = previous
        self.assertTrue(terraform.work.is_absolute())
        self.assertEqual(terraform.root, terraform.work / "bootstrap" / "terraform")

    def test_the_main_ruleset_allows_changes_through_pull_requests_only(self):
        ruleset = install.main_ruleset("platform")
        rules = {rule["type"] for rule in ruleset["rules"]}
        self.assertEqual(rules, {"deletion", "non_fast_forward", "pull_request"})
        self.assertNotIn("bypass_actors", ruleset)
        # The check in a ruleset of its own, which the bypass team's pull request bypass does not reach.
        checks = install.checks_ruleset("platform")
        self.assertEqual(([rule["type"] for rule in checks["rules"]], checks["conditions"]), (["required_status_checks"], ruleset["conditions"]))
        self.assertNotIn("bypass_actors", checks)

    def test_a_merge_waits_for_the_role_s_check(self):
        github = FakeGitHub()
        install.step_repos(github, self.folder, self.out)
        for role, repo in REPOS.items():
            required = next(rule["parameters"] for rule in github.ruleset_bodies[(repo, "main-checks")]["rules"] if rule["type"] == "required_status_checks")
            # By GitHub Actions, as the canary's runs named it; strict, so a pull request checked before a code owner
            # handover merged is checked again against the new owners.
            self.assertEqual(required["required_status_checks"], [{"context": f"{role} / check", "integration_id": 15368}])
            self.assertTrue(required["strict_required_status_checks_policy"])
        self.assertIn("acme/mlz-vending: ruleset main (pull requests only, a code owner's approval, which a later push takes back; no "
                      "force push or deletion; acme/mlz-admins may merge a pull request without the approval)", self.printed)
        review = next(rule["parameters"] for rule in github.ruleset_bodies[("acme/mlz-vending", "main")]["rules"] if rule["type"] == "pull_request")
        self.assertTrue(review["dismiss_stale_reviews_on_push"])  # as in the workload repos (decision of 19 November)
        self.assertFalse(review["require_last_push_approval"])
        self.assertIn("acme/mlz-vending: ruleset main-checks (vending / check passed on a branch up to date with main, before any merge; "
                      "nobody bypasses it)", self.printed)
        # The starter workflow's job gives the check its name.
        for role in REPOS:
            workflow = (install.CORE / "starter" / f"mlz-{role}" / WORKFLOW).read_text()
            self.assertRegex(workflow, rf"\njobs:\n(?:  #.*\n)*  {role}:\n")

    def test_check_passes_when_the_app_is_set_everywhere(self):
        github = FakeGitHub(existing=REPOS.values(),
                            variables=[(r, install.APP_VARIABLE) for r in REPOS.values()], secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        install.step_check(github, self.folder, self.out)
        self.assertEqual(self.printed[0], "names: the state account will be stacmemlzstatepdnwe01")
        self.assertEqual(self.printed[1], "acme: on GitHub Enterprise")
        # Per repo: the MLZ App, its code owners, its push ruleset, and its check; then what is not checked.
        self.assertEqual(len(self.printed), 15)
        self.assertIn("acme/mlz-vending: changes under .github/ are refused on every branch, but from acme/mlz-admins (ruleset pipeline-files)", self.printed)
        self.assertTrue(self.printed[-1].startswith("acme: not checked here: that the MLZ App is installed on all its repos, acme/mlz-core included."))
        self.assertIn("acme/mlz-vending: vending / check must pass, on a branch up to date with main, before any merge into main; nobody may bypass it", self.printed)
        self.assertIn("acme/mlz-vending: acme/platform may write, GitHub reads its CODEOWNERS whole, "
                      "and a code owner's review is required on main, which a later push takes back", self.printed)

    def test_check_fails_while_an_approval_survives_a_later_push(self):
        # A repo made before 19 November: its main keeps an approval after new commits, until the box is ticked.
        github = FakeGitHub(existing=REPOS.values(), dismiss_stale=False,
                            variables=[(r, install.APP_VARIABLE) for r in REPOS.values()], secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        with self.assertRaises(ConfigError) as caught:
            install.step_check(github, self.folder, self.out)
        self.assertIn("acme/mlz-vending: an approval on main stays after a later push, so an approved change can become another before it "
                      "merges; turn on 'Dismiss stale pull request approvals when new commits are pushed' in its ruleset main", str(caught.exception))

    # code owners

    def test_new_repos_get_the_platform_team_as_code_owner(self):
        github = FakeGitHub(writers=())
        install.step_repos(github, self.folder, self.out)
        for repo in REPOS.values():
            files = next(files for r, _, _, files, _ in github.commits if r == repo)
            self.assertIn("*             @acme/platform\n", files[".github/CODEOWNERS"])
            self.assertIn("\n/_generated/\n", files[".github/CODEOWNERS"])
        self.assertEqual(sorted(github.granted), sorted(("platform", repo) for repo in REPOS.values()))
        self.assertTrue(install.main_ruleset("platform")["rules"][2]["parameters"]["require_code_owner_review"])

    def test_a_team_that_can_write_already_keeps_its_access(self):
        github = FakeGitHub(existing=REPOS.values())
        install.step_repos(github, self.folder, self.out)
        self.assertEqual(github.granted, [])  # never lowered: one with maintain or admin keeps it
        self.assertIn("acme/mlz-platform: acme/platform may write, as its code owner", self.printed)

    def test_the_organization_s_copy_of_mlz_core_comes_first_and_the_starters_call_it(self):
        github = FakeGitHub(core=False)
        pushed = []
        with unittest.mock.patch.object(mirror, "push_release", lambda gh, core, tag, out, run=None: pushed.append((core, tag))):
            install.step_repos(github, self.folder, self.out)
        self.assertEqual(github.created[0], "acme/mlz-core (empty)")  # before the repos whose first runs call it
        self.assertEqual(pushed, [("acme/mlz-core", install.release())])
        self.assertEqual(github.access["acme/mlz-core"], "organization")
        self.assertEqual([name for repo, name in github.made_rulesets if repo == "acme/mlz-core"], ["releases", "releases-branch", "no-release-branches"])
        workflow = next(files for repo, _, _, files, _ in github.commits if repo == "acme/mlz-platform")[".github/workflows/mlz.yml"]
        self.assertIn(f"    uses: acme/mlz-core/.github/workflows/role.yml@{install.release()}\n", workflow)
        self.assertNotIn("fortytwoservices/mlz-core", workflow)

    def test_the_copy_may_have_a_name_of_its_own(self):
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("    vending: mlz-vending\n", "    vending: mlz-vending\n    core: platform-mlz-core\n"))
        _, bootstrap = install.settings(self.folder)
        self.assertEqual(install.core_repository(bootstrap), "acme/platform-mlz-core")
        self.assertEqual(install.repositories(bootstrap), REPOS)

    def test_the_platform_team_must_exist_and_be_named(self):
        with self.assertRaisesRegex(ConfigError, "acme has no team platform"):
            install.step_repos(FakeGitHub(teams=()), self.folder, self.out)
        # GitHub skips a secret team in CODEOWNERS; it is refused before any repo changes.
        github = FakeGitHub(secret=("platform",), core=False)
        with self.assertRaisesRegex(ConfigError, "acme/platform is a secret team"):
            install.step_repos(github, self.folder, self.out)
        self.assertEqual((github.repos, github.commits), ({}, []))  # not even the copy of mlz-core
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("  platform_team: platform\n", ""))
        with self.assertRaisesRegex(ConfigError, "bootstrap.yaml needs github.platform_team"):
            install.step_repos(FakeGitHub(), self.folder, self.out)

    def test_the_push_ruleset_refuses_github_changes_but_from_the_bypass_team(self):
        ruleset = install.push_ruleset(12)
        self.assertEqual((ruleset["target"], ruleset["enforcement"]), ("push", "active"))
        self.assertEqual(ruleset["bypass_actors"], [{"actor_id": 12, "actor_type": "Team", "bypass_mode": "always"}])
        self.assertEqual(ruleset["rules"], [{"type": "file_path_restriction", "parameters": {"restricted_file_paths": [".github/**/*"]}}])
        for path in (".github/workflows/mlz.yml", ".github/CODEOWNERS", ".github/actions/x/action.yml"):
            self.assertTrue(install.matches(".github/**/*", path), path)
        for path in ("customer.yaml", "mlz", "_generated/governance.yaml", "docs/.github/x", ".githubx/y"):
            self.assertFalse(install.matches(".github/**/*", path), path)
        self.assertTrue(install.matches(".github/**", ".github/CODEOWNERS"))
        self.assertFalse(install.matches(".github/**", ".github/workflows/mlz.yml"))  # ** without its slash is *

    def test_the_bypass_team_is_required_visible_and_not_the_platform_team(self):
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("  bypass_team: mlz-admins\n", ""))
        with self.assertRaisesRegex(ConfigError, "bootstrap.yaml needs github.bypass_team"):
            install.step_repos(FakeGitHub(), self.folder, self.out)
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("bypass_team: mlz-admins", "bypass_team: platform"))
        with self.assertRaisesRegex(ConfigError, "github.bypass_team is platform, the platform team"):
            install.step_repos(FakeGitHub(), self.folder, self.out)
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP)
        with self.assertRaisesRegex(ConfigError, "acme has no team mlz-admins"):
            install.step_repos(FakeGitHub(teams=("platform",)), self.folder, self.out)
        with self.assertRaisesRegex(ConfigError, "acme/mlz-admins is a secret team"):
            install.step_repos(FakeGitHub(secret=("mlz-admins",)), self.folder, self.out)

    def test_check_finds_a_missing_push_ruleset_and_a_bypass_without_the_team(self):
        everywhere = dict(existing=REPOS.values(), variables=[(r, install.APP_VARIABLE) for r in REPOS.values()],
                          secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        with self.assertRaises(ConfigError) as caught:
            install.step_check(FakeGitHub(push_guard=False, **everywhere), self.folder, self.out)
        self.assertIn("acme/mlz-platform: no active push ruleset refuses changes under .github/", str(caught.exception))

        def check(*rulesets):
            class Guarded(FakeGitHub):
                def rulesets(self, repo):
                    return [{"id": n, "current_user_can_bypass": "never", **r} for n, r in enumerate(rulesets)]
            with self.assertRaises(ConfigError) as caught:
                install.step_check(Guarded(**everywhere), self.folder, self.out)
            return str(caught.exception)

        guard = install.push_ruleset(TEAM_IDS["mlz-admins"])
        # Another team instead of the bypass team: nobody can run mlz upgrade, and someone else reaches the key.
        found = check(install.push_ruleset(99))
        self.assertIn("acme/mlz-admins (bootstrap.yaml's github.bypass_team) may not bypass it, so nobody can run mlz upgrade", found)
        self.assertIn("its ruleset pipeline-files also lets the team with ID 99 change .github/", found)
        # The bypass team and the platform team, or the repository's admins: finding 17 stays open for them.
        both = {**guard, "bypass_actors": guard["bypass_actors"] + [{"actor_id": TEAM_IDS["platform"], "actor_type": "Team", "bypass_mode": "always"},
                                                                    {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]}
        self.assertIn("also lets the platform team, the repository's admins change .github/", check(both))
        # Two files only, or the folder with an exception: not the whole folder.
        files = {**guard, "rules": [{"type": "file_path_restriction", "parameters": {"restricted_file_paths": [".github/workflows/mlz.yml", ".github/CODEOWNERS"]}}]}
        self.assertIn("no active push ruleset refuses every change (not .github/workflows/zz-mlz-probe.yml, .github/zz-mlz-probe, "
                      ".github/actions/zz-mlz-probe/action.yml) under .github/", check(files))
        # .github/** reaches the folder's top only: a ** without its slash is one *, in Ruby's fnmatch.
        top_only = {**guard, "rules": [{"type": "file_path_restriction", "parameters": {"restricted_file_paths": [".github/**"]}}]}
        self.assertIn("(not .github/workflows/mlz.yml, .github/workflows/zz-mlz-probe.yml, .github/actions/zz-mlz-probe/action.yml)", check(top_only))
        excepted = {**guard, "rules": [{"type": "file_path_restriction", "parameters": {"restricted_file_paths": [".github/**/*"], "ignored_file_paths": [".github/workflows/*"]}}]}
        self.assertIn("(not .github/workflows/mlz.yml, .github/workflows/zz-mlz-probe.yml)", check(excepted))
        # GitHub applies every ruleset: one more, from the organization, that the team may not bypass refuses its upgrade.
        org_guard = {**install.push_ruleset(99), "name": "org-pipeline", "bypass_actors": []}
        self.assertIn("its ruleset org-pipeline refuses changes under .github/, and acme/mlz-admins", check(guard, org_guard))

    def test_an_offer_under_github_stops_before_its_branch_when_the_person_may_not_bypass(self):
        github = FakeGitHub(existing=["acme/mlz-governance"], half_made=["acme/mlz-governance"], can_bypass="never")
        with self.assertRaisesRegex(ConfigError, "its ruleset pipeline-files refuses changes to .github/CODEOWNERS, .github/workflows/mlz.yml"):
            install.step_repos(github, self.folder, self.out)
        self.assertEqual((github.commits, github.branches), ([], []))  # nothing pushed, no branch made

    def test_check_notes_a_review_ruleset_on_main_the_bypass_team_cannot_pass(self):
        everywhere = dict(existing=REPOS.values(), variables=[(r, install.APP_VARIABLE) for r in REPOS.values()],
                          secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        install.step_check(FakeGitHub(main_bypass=False, **everywhere), self.folder, self.out)  # only noted: it passes
        self.assertIn("acme/mlz-vending: acme/mlz-admins may not merge a pull request without a code owner's approval past ruleset main. "
                      "mlz install repos gives a new repo's ruleset main that bypass. For an existing one: first run mlz install repos, so "
                      "that main-checks, without bypass actors, requires the check on an up-to-date branch, and take the check out of main; "
                      "then add the team to main as a bypass actor, pull requests only", "\n".join(self.printed))
        self.printed.clear()
        install.step_check(FakeGitHub(**everywhere), self.folder, self.out)
        self.assertFalse([line for line in self.printed if "may not merge a pull request" in line])

    def test_a_wrapper_left_without_its_mode_is_offered_the_mode(self):
        # An earlier run made the repo and its starter commit, and stopped before the commit that makes ./mlz executable.
        github = FakeGitHub(existing=REPOS.values(), not_executable=[("acme/mlz-vending", "mlz")])
        install.step_repos(github, self.folder, self.out)
        self.assertEqual(github.executable, [("acme/mlz-vending", "mlz/executable", ("mlz",))])
        self.assertEqual([(repo, branch) for repo, branch, *_ in github.pulls], [("acme/mlz-vending", "mlz/executable")])
        self.assertIn("acme/mlz-vending: mlz is not executable; the fix is offered in https://github.com/acme/mlz-vending/pull/1", self.printed)

    def test_a_repo_set_up_before_code_owners_is_offered_the_file(self):
        github = FakeGitHub(existing=REPOS.values(), owned=False)
        install.step_repos(github, self.folder, self.out)
        repo, branch, _, files, message = github.commits[0]
        self.assertEqual((branch, list(files), message), ("mlz/code-owners", [".github/CODEOWNERS"], "chore: the platform team as code owner"))
        self.assertIn("acme/mlz-governance: .github/CODEOWNERS is offered in https://github.com/acme/mlz-governance/pull/1", self.printed)

    def test_an_adopted_repo_keeps_its_own_code_owners(self):
        # Its CODEOWNERS at the root: a .github/ one beside it would be read first, and replace it.
        github = FakeGitHub(existing=["acme/mlz-governance"], half_made=["acme/mlz-governance"],
                            files={("acme/mlz-governance", "CODEOWNERS"): ("* @acme/own\n", "sha")})
        install.step_repos(github, self.folder, self.out)
        offered = next(files for repo, branch, _, files, _ in github.commits if repo == "acme/mlz-governance" and branch == "mlz/starter")
        self.assertNotIn(".github/CODEOWNERS", offered)
        self.assertIn(".github/workflows/mlz.yml", offered)
        self.assertIn(("acme/mlz-governance", "mlz/starter", ("mlz",)), github.executable)  # on the offer's branch

    def test_check_lists_what_stops_the_code_owners(self):
        github = FakeGitHub(existing=REPOS.values(), writers=(), ruleset_review=False, required_check=lambda role: [],
                            variables=[(r, install.APP_VARIABLE) for r in REPOS.values()], secrets=[(r, install.APP_SECRET) for r in REPOS.values()],
                            owner_errors={"acme/mlz-vending": [{"line": 3, "kind": "Unknown owner", "path": ".github/CODEOWNERS",
                                                                 "message": "Unknown owner on line 3: make sure the team @acme/network exists\n\n  /x @acme/network"}]})
        with self.assertRaises(ConfigError) as caught:
            install.step_check(github, self.folder, self.out)
        message = str(caught.exception)
        self.assertIn("acme/mlz-governance: acme/platform cannot write to it, so it cannot be its code owner", message)
        self.assertIn("acme/mlz-platform: no active ruleset requires a code owner's review on main", message)
        self.assertIn("acme/mlz-governance: no active ruleset requires governance / check to pass on main, so a change the check "
                      "refuses can still be merged; mlz install repos makes the ruleset main-checks, without bypass actors, which requires "
                      "it from GitHub Actions on a branch up to date with main.", message)
        self.assertIn("acme/mlz-vending: .github/CODEOWNERS line 3: Unknown owner on line 3: make sure the team @acme/network exists. "
                      "The platform team must be a visible team of acme that can write to acme/mlz-vending", message)

    def test_check_counts_only_a_required_check_from_github_actions(self):
        # Another app's check never comes; one from any source can be passed by any token that may write statuses.
        for source in ({"integration_id": 12345}, {}):
            github = FakeGitHub(existing=REPOS.values(), required_check=lambda role: [{"context": f"{role} / check", **source}],
                                variables=[(r, install.APP_VARIABLE) for r in REPOS.values()], secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
            with self.assertRaisesRegex(ConfigError, "acme/mlz-vending: no active ruleset requires vending / check to pass on main"):
                install.step_check(github, self.folder, self.out)

    def test_check_wants_the_required_check_on_an_up_to_date_branch(self):
        ready = dict(existing=REPOS.values(), variables=[(r, install.APP_VARIABLE) for r in REPOS.values()],
                     secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        with self.assertRaisesRegex(ConfigError, "acme/mlz-platform: platform / check must pass before a merge into main, but not on "
                                                 "a branch up to date with it"):
            install.step_check(FakeGitHub(**ready, strict=False), self.folder, self.out)
        install.step_check(FakeGitHub(**ready), self.folder, self.out)
        self.assertIn("acme/mlz-platform: platform / check must pass, on a branch up to date with main, before any merge into main; nobody may bypass it", self.printed)

        # Another ruleset, such as the organization's, strict about a check of its own keeps the branch up to date too.
        class Layered(FakeGitHub):
            def branch_rules(self, repo, branch):
                return super().branch_rules(repo, branch) + [{"type": "required_status_checks", "parameters": {
                    "strict_required_status_checks_policy": True, "required_status_checks": [{"context": "security / scan"}]}}]
        self.printed.clear()
        install.step_check(Layered(**ready, strict=False), self.folder, self.out)
        self.assertIn("acme/mlz-platform: platform / check must pass, on a branch up to date with main, before any merge into main; nobody may bypass it", self.printed)

        # Required only by a ruleset its bypass actors may skip, as an existing repo's own main-checks, or the old main, may be.
        class Bypassable(FakeGitHub):
            def rulesets(self, repo):
                return super().rulesets(repo) + [{**install.checks_ruleset("platform"), "id": 8,
                                                  "bypass_actors": [{"actor_id": TEAM_IDS["mlz-admins"], "actor_type": "Team", "bypass_mode": "pull_request"}]}]
        with self.assertRaisesRegex(ConfigError, "acme/mlz-platform: platform / check is required only by ruleset main-checks, whose bypass "
                                                 "actors may merge without it; require it in a ruleset without bypass actors"):
            install.step_check(Bypassable(**ready), self.folder, self.out)

        # The check from a bypass-free ruleset, but up to date only by a ruleset someone may bypass: not up to date for them.
        class StrictOnlyBypassable(Layered):
            def rulesets(self, repo):
                return super().rulesets(repo) + [{"id": 99, "name": "org-strict", "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]}]

            def branch_rules(self, repo, branch):
                return [{**rule, "ruleset_id": 99} if (rule.get("parameters") or {}).get("required_status_checks") == [{"context": "security / scan"}] else rule
                        for rule in super().branch_rules(repo, branch)]
        with self.assertRaisesRegex(ConfigError, "acme/mlz-platform: platform / check must pass before a merge into main, but not on a branch up to date"):
            install.step_check(StrictOnlyBypassable(**ready, strict=False), self.folder, self.out)

    def test_check_matches_the_organization_s_plan_with_bootstrap_yaml_s(self):
        ready = dict(existing=REPOS.values(), variables=[(r, install.APP_VARIABLE) for r in REPOS.values()],
                     secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        # On Team, the environments' reviewers are refused by GitHub: bootstrap.yaml must say team.
        with self.assertRaisesRegex(ConfigError, "acme: on GitHub's team plan, but bootstrap.yaml's github.plan says enterprise: .* Set github.plan: team"):
            install.step_check(FakeGitHub(**ready, org_plan="team"), self.folder, self.out)
        bootstrap = self.folder / "bootstrap.yaml"
        bootstrap.write_text(BOOTSTRAP.replace("github:\n", "github:\n  plan: team\n", 1))
        install.step_check(FakeGitHub(**ready, org_plan="team"), self.folder, self.out)
        self.assertIn("acme: on GitHub Team, so each apply is approved on its pull request", self.printed)
        # Enterprise with team said: allowed, the weaker gate named.
        install.step_check(FakeGitHub(**ready, org_plan="enterprise"), self.folder, self.out)
        self.assertIn("acme: on GitHub Enterprise, but bootstrap.yaml's github.plan says team: each apply is approved on its pull request, "
                      "not by a reviewer in its environment. Set github.plan: enterprise for the reviewer.", self.printed)
        # Free has no environments in a private repo, whatever bootstrap.yaml says.
        with self.assertRaisesRegex(ConfigError, "acme: on GitHub's free plan. MLZ needs GitHub Team or Enterprise"):
            install.step_check(FakeGitHub(**ready, org_plan="free"), self.folder, self.out)
        # Only owners see the plan: said, not refused.
        install.step_check(FakeGitHub(**ready, org_plan=None), self.folder, self.out)
        self.assertIn("acme: its GitHub plan is shown to its owners only, so it is not checked here; bootstrap.yaml says team.", self.printed)
        bootstrap.write_text(BOOTSTRAP.replace("github:\n", "github:\n  plan: business\n", 1))
        with self.assertRaisesRegex(ConfigError, "github.plan is enterprise or team, the customer's GitHub plan, not 'business'"):
            install.step_check(FakeGitHub(**ready, org_plan="enterprise"), self.folder, self.out)

    def test_check_lists_every_missing_piece(self):
        github = FakeGitHub(existing=["acme/mlz-governance", "acme/mlz-platform"],
                            variables=[("acme/mlz-governance", install.APP_VARIABLE)], secrets=[("acme/mlz-platform", install.APP_SECRET)])
        with self.assertRaises(ConfigError) as caught:
            install.step_check(github, self.folder, self.out)
        message = str(caught.exception)
        self.assertIn("acme/mlz-governance: no secret MLZ_RELEASE_APP_PRIVATE_KEY", message)
        self.assertIn("acme/mlz-platform: no variable MLZ_RELEASE_APP_CLIENT_ID", message)
        self.assertIn("acme/mlz-vending: no such repo", message)

    def test_settings_must_name_the_three_repos(self):
        (self.folder / "bootstrap.yaml").write_text("github:\n  organization: acme\n  repositories:\n    governance: g\n")
        with self.assertRaisesRegex(ConfigError, "github.repositories for governance, platform and vending"):
            install.step_repos(FakeGitHub(), self.folder, self.out)
        (self.folder / "customer.yaml").unlink()
        with self.assertRaisesRegex(ConfigError, "missing .*customer.yaml"):
            install.step_repos(FakeGitHub(), self.folder, self.out)

    # bootstrap

    def test_the_first_run_applies_locally_then_moves_the_state(self):
        terraform, waits = FakeTerraform(self.work, migrate_failures=2), []
        install.step_bootstrap(FakeGitHub(), terraform, self.folder, yes=True, out=self.out, wait=waits.append)
        first_init = terraform.calls[0]
        self.assertEqual(first_init[0], "init")
        self.assertFalse(any(a.startswith("-backend-config") for a in first_init))  # the local backend
        self.assertIn("apply", [call[0] for call in terraform.calls])
        self.assertEqual(waits, [30, 30])  # two failed moves while the new rights spread
        self.assertTrue((self.work / "backend.hcl").exists())
        self.assertFalse((self.work / "backend.hcl.pending").exists())
        self.assertFalse((terraform.root / "backend_override.tf").exists())
        # The names, worked out on this run, go to the root in a file of their own.
        plan = next(call for call in terraform.calls if call[0] == "plan")
        self.assertIn(f"names_file={self.work / 'names.json'}", plan)
        self.assertEqual(json.loads((self.work / "names.json").read_text())["storage_account"], "stacmemlzstatepdnwe01")
        # So do the runner settings, checked and with their defaults: the root never reads them raw.
        self.assertIn(f"runners_file={self.work / 'runners.json'}", plan)
        self.assertEqual(json.loads((self.work / "runners.json").read_text()),
                         {"kind": "self-hosted", "size": "Standard_D2ds_v6", "instances": 1, "app_client_id": None, "image": {
                             "publisher": "amestofortytwoas1653635920536", "offer": "self_hosted_runner_github", "sku": "ubuntu-latest-gen2",
                             "plan": {"name": "ubuntu-latest-gen2", "product": "self_hosted_runner_github", "publisher": "amestofortytwoas1653635920536"}}})
        self.assertIn("bootstrap: all 3 subjects match what GitHub issues", self.printed)

    def test_a_state_that_will_not_move_stays_local_and_the_next_run_moves_it(self):
        terraform = FakeTerraform(self.work, migrate_failures=99)
        with self.assertRaisesRegex(ConfigError, "still local"):
            install.step_bootstrap(FakeGitHub(), terraform, self.folder, yes=True, out=self.out, wait=lambda s: None)
        override = terraform.root / "backend_override.tf"
        self.assertIn('backend "local"', override.read_text())
        self.assertFalse((self.work / "backend.hcl").exists())
        again = FakeTerraform(self.work, plan_status=0, migrate_failures=0)
        install.step_bootstrap(FakeGitHub(), again, self.folder, out=self.out, wait=lambda s: None)
        self.assertTrue((self.work / "backend.hcl").exists())
        self.assertNotIn("apply", [call[0] for call in again.calls])

    def test_a_later_run_uses_the_account_and_applies_nothing_unchanged(self):
        (self.work).mkdir(parents=True)
        (self.work / "backend.hcl").write_text('storage_account_name = "x"\n')
        terraform = FakeTerraform(self.work, plan_status=0)
        install.step_bootstrap(FakeGitHub(), terraform, self.folder, out=self.out)
        self.assertIn(f"-backend-config={self.work / 'backend.hcl'}", terraform.calls[0])
        self.assertEqual([c[0] for c in terraform.calls], ["init", "plan", "show"])
        self.assertNotIn("-detailed-exitcode", terraform.calls[1])  # never relied on (D12)
        self.assertIn("**bootstrap**: no changes.", self.printed)

    def test_an_import_or_a_move_alone_is_applied(self):
        # Terraform's exit code once decided; an import alone changes nothing in Azure, but the state must record it.
        (self.work).mkdir(parents=True)
        (self.work / "backend.hcl").write_text('storage_account_name = "x"\n')
        imported = {**NO_CHANGE, "applyable": True, "resource_changes": [
            {"address": "azapi_resource.vault", "type": "azapi_resource", "change": {"actions": ["no-op"], "before": {}, "after": {}, "after_unknown": {},
                                                                                     "importing": {"id": "/subscriptions/s/resourceGroups/g"}}},
            {"address": "azapi_resource.group", "previous_address": "azapi_resource.rg", "type": "azapi_resource",
             "change": {"actions": ["no-op"], "before": {}, "after": {}, "after_unknown": {}}}]}
        terraform = FakeTerraform(self.work, plan_json=imported)
        install.step_bootstrap(FakeGitHub(), terraform, self.folder, yes=True, out=self.out)
        self.assertIn("apply", [call[0] for call in terraform.calls])
        summary = "\n".join(self.printed)
        self.assertIn("**bootstrap**: no resource changes.", summary)
        self.assertIn("- imports `azapi_resource.vault`", summary)
        self.assertIn("- moves `azapi_resource.rg` to `azapi_resource.group`", summary)

    def test_a_plan_that_failed_that_terraform_cannot_show_or_that_errored_stops(self):
        (self.work).mkdir(parents=True)
        (self.work / "backend.hcl").write_text('storage_account_name = "x"\n')
        failed = FakeTerraform(self.work, plan_status=1)
        with self.assertRaisesRegex(ConfigError, "terraform plan failed"):
            install.step_bootstrap(FakeGitHub(), failed, self.folder, yes=True, out=self.out)
        self.assertEqual([call[0] for call in failed.calls], ["init", "plan"])
        unreadable = FakeTerraform(self.work, show_fails=True)
        with self.assertRaisesRegex(ConfigError, "terraform show could not read the plan: Error: Failed to read"):
            install.step_bootstrap(FakeGitHub(), unreadable, self.folder, yes=True, out=self.out)
        self.assertEqual([call[0] for call in unreadable.calls], ["init", "plan", "show"])
        errored = FakeTerraform(self.work, plan_json={**ONE_CHANGE, "errored": True})
        with self.assertRaisesRegex(ConfigError, "the bootstrap's plan is not one to apply"):
            install.step_bootstrap(FakeGitHub(), errored, self.folder, yes=True, out=self.out)
        self.assertNotIn("apply", [call[0] for call in errored.calls])

    def test_it_says_whether_the_state_has_its_private_endpoint(self):
        (self.work).mkdir(parents=True)
        (self.work / "backend.hcl").write_text('storage_account_name = "x"\n')
        install.step_bootstrap(FakeGitHub(), FakeTerraform(self.work, plan_status=0), self.folder, out=self.out)
        self.assertIn("bootstrap: the state account's private endpoint waits for management's network and the hub's blob zone: run this step again after the platform's first apply.", self.printed)
        install.step_bootstrap(FakeGitHub(), FakeTerraform(self.work, plan_status=0, state_endpoint=True), self.folder, out=self.out)
        self.assertIn("bootstrap: the state account has its private endpoint in management's network.", self.printed)

    def test_the_plan_waits_for_a_yes(self):
        terraform = FakeTerraform(self.work)
        with self.assertRaisesRegex(ConfigError, "not applied"):
            install.step_bootstrap(FakeGitHub(), terraform, self.folder, out=self.out, ask=lambda q: "n")
        self.assertNotIn("apply", [call[0] for call in terraform.calls])

    def test_a_piped_yes_is_not_an_answer(self):
        stdin, stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO("y\n"), io.StringIO()
        try:
            self.assertEqual(install.answer("Apply this plan? [y/N] "), "")
            self.assertIn("run again with --yes", sys.stdout.getvalue())
        finally:
            sys.stdin, sys.stdout = stdin, stdout

    def test_subjects_github_does_not_issue_are_refused(self):
        subjects = {"vending-apply": "repo:acme/mlz-vending:environment:vending"}
        with self.assertRaisesRegex(ConfigError, "vending-apply: trusts repo:acme/mlz-vending:environment:vending, but GitHub issues repo:acme@1/mlz-vending@2"):
            install.check_subjects(FakeGitHub(), subjects, REPOS, self.out)

    # handover

    def test_moves_only_the_subscriptions_outside_the_tree(self):
        azure = FakeAzure({
            "00000000-0000-0000-0000-000000000001": ["tenant", "unmanaged_subscriptions"],
            "00000000-0000-0000-0000-000000000002": ["tenant", "acme", "acme-platform", "acme-connectivity"],
            "00000000-0000-0000-0000-000000000003": ["tenant"],
        })
        asked = []
        install.step_handover(azure, self.folder, out=self.out, ask=lambda q: asked.append(q) or "y")
        self.assertEqual(azure.moves, [("acme", "00000000-0000-0000-0000-000000000003"), ("acme", "00000000-0000-0000-0000-000000000001")])
        self.assertEqual(len(asked), 1)
        self.assertIn("handover: connectivity subscription 00000000-0000-0000-0000-000000000002 is already in the acme tree", self.printed)
        self.assertIn("handover: management subscription 00000000-0000-0000-0000-000000000001 moves from unmanaged_subscriptions into acme", self.printed)

    def test_nothing_moves_without_a_yes(self):
        azure = FakeAzure({f"00000000-0000-0000-0000-00000000000{n}": ["tenant", "unmanaged_subscriptions"] for n in (1, 2, 3)})
        with self.assertRaisesRegex(ConfigError, "not moved"):
            install.step_handover(azure, self.folder, out=self.out, ask=lambda q: "")
        self.assertEqual(azure.moves, [])

    def test_a_subscription_the_sign_in_cannot_see_stops_the_handover(self):
        with self.assertRaisesRegex(ConfigError, "not visible to this sign-in"):
            install.step_handover(FakeAzure({}), self.folder, yes=True, out=self.out)

    # publish

    def test_publish_offers_the_contract_to_the_governance_repo(self):
        (self.work).mkdir(parents=True)
        (self.work / "backend.hcl").write_text("x")
        github = FakeGitHub()
        url = install.step_publish(github, FakeTerraform(self.work), self.folder, self.out)
        self.assertEqual(url, "https://github.com/acme/mlz-governance/pull/1")
        repo, branch, head, files, message = github.commits[0]
        self.assertEqual((repo, branch, message), ("acme/mlz-governance", "mlz/bootstrap-contract", "chore: bootstrap contract"))
        self.assertIn("stacmestate1234", files["_generated/bootstrap.yaml"])
        self.assertIn("Written by Fortytwo after running the bootstrap", files["_generated/bootstrap.yaml"])

    def test_publish_does_nothing_when_main_already_has_it(self):
        (self.work).mkdir(parents=True)
        (self.work / "backend.hcl").write_text("x")
        terraform = FakeTerraform(self.work)
        text = install.contract.render("bootstrap", json.loads(terraform.output("contract")))
        github = FakeGitHub(files={("acme/mlz-governance", "_generated/bootstrap.yaml"): (text, "sha")})
        self.assertIsNone(install.step_publish(github, terraform, self.folder, self.out))
        self.assertEqual(github.commits, [])

    def test_publish_closes_an_older_offer_once_main_matches(self):
        (self.work).mkdir(parents=True, exist_ok=True)
        (self.work / "backend.hcl").write_text("x")
        terraform = FakeTerraform(self.work)
        text = install.contract.render("bootstrap", json.loads(terraform.output("contract")))
        github = FakeGitHub(files={("acme/mlz-governance", "_generated/bootstrap.yaml"): (text, "sha")}, open_pull="https://github.com/acme/mlz-governance/pull/3")
        install.step_publish(github, terraform, self.folder, self.out)
        self.assertEqual(github.closed[0][:2], ("acme/mlz-governance", "mlz/bootstrap-contract"))
        self.assertIn("publish: closed https://github.com/acme/mlz-governance/pull/3, which no longer matches the bootstrap", self.printed)

    def test_publish_again_updates_its_open_offer_without_moving_the_branch_back(self):
        (self.work).mkdir(parents=True, exist_ok=True)
        (self.work / "backend.hcl").write_text("x")
        offer = "https://github.com/acme/mlz-governance/pull/3"
        github = FakeGitHub(open_pull=offer, files={("acme/mlz-governance", "_generated/bootstrap.yaml", "mlz/bootstrap-contract"): ("older\n", "sha")})
        self.assertEqual(install.step_publish(github, FakeTerraform(self.work), self.folder, self.out), offer)
        self.assertEqual(github.branches, [])  # moved back to main, the branch would close its pull request
        self.assertEqual(github.commits[0][1:3], ("mlz/bootstrap-contract", "head-of-mlz/bootstrap-contract"))
        self.assertEqual(github.pulls, [])

    def test_publish_again_leaves_an_open_offer_of_the_same_contract_alone(self):
        (self.work).mkdir(parents=True, exist_ok=True)
        (self.work / "backend.hcl").write_text("x")
        terraform = FakeTerraform(self.work)
        text = install.contract.render("bootstrap", json.loads(terraform.output("contract")))
        offer = "https://github.com/acme/mlz-governance/pull/3"
        github = FakeGitHub(open_pull=offer, files={("acme/mlz-governance", "_generated/bootstrap.yaml", "mlz/bootstrap-contract"): (text, "sha")})
        install.step_publish(github, terraform, self.folder, self.out)
        self.assertEqual(github.commits, [])
        self.assertIn(f"publish: {offer} already offers this _generated/bootstrap.yaml", self.printed)

    def test_publish_needs_the_state_in_its_account(self):
        with self.assertRaisesRegex(ConfigError, "run mlz install bootstrap first"):
            install.step_publish(FakeGitHub(), FakeTerraform(self.work), self.folder, self.out)

    # subscriptions

    def subscriptions(self, azure, scope=EA, yes=False, answer="", contract=VENDING_IDENTITY):
        (self.work).mkdir(parents=True, exist_ok=True)
        (self.work / "backend.hcl").write_text("x")
        terraform = FakeTerraform(self.work, contract_identities=contract)
        self.waits = []
        return install.step_subscriptions(azure, terraform, self.folder, scope, TENANT, yes, self.out, lambda question: answer, self.waits.append)

    def test_an_identity_with_the_role_needs_nothing(self):
        role = f"{EA}/billingRoleDefinitions/a0bcee42-bf30-4d1b-926a-48d21664ef71"
        azure = BillingAzure(assignments=[{"properties": {"principalId": "someone-else", "roleDefinitionId": role}},
                                          {"properties": {"principalId": PRINCIPAL, "roleDefinitionId": role}}])
        self.assertTrue(self.subscriptions(azure))
        self.assertEqual([c[0] for c in azure.calls], ["GET"])
        self.assertIn("subscriptions: it has it; vending can make them.", self.printed)

    def test_ea_the_role_is_given_when_the_sign_in_may(self):
        azure = BillingAzure()
        self.assertTrue(self.subscriptions(azure, answer="y"))
        self.assertEqual([c[0] for c in azure.calls], ["GET", "PUT", "GET"])  # done once the role assignments show it
        method, path, body = azure.calls[1]
        # Named after the scope and the identity: given again, it is the same assignment.
        name = path.split("/billingRoleAssignments/")[1].split("?")[0]
        self.assertEqual(path, f"{EA}/billingRoleAssignments/{name}?api-version=2019-10-01-preview")
        self.assertEqual(install.grant_request("EA", EA, PRINCIPAL, TENANT)[1], path)
        self.assertEqual(body, {"properties": {"principalId": PRINCIPAL, "principalTenantId": TENANT,
                                               "roleDefinitionId": f"{EA}/billingRoleDefinitions/a0bcee42-bf30-4d1b-926a-48d21664ef71"}})

    def test_mca_the_role_is_given_on_the_invoice_section(self):
        # Answered 202, and shown in the role assignments two reads later: done only then.
        azure = BillingAzure(shows_after=2)
        self.assertTrue(self.subscriptions(azure, scope=MCA, yes=True))
        grant = next(call for call in azure.calls if call[0] == "POST")
        self.assertEqual(grant, ("POST", f"{MCA}/createBillingRoleAssignment?api-version=2024-04-01", {
            "principalId": PRINCIPAL, "principalTenantId": TENANT,
            "roleDefinitionId": f"{MCA}/billingRoleDefinitions/30000000-aaaa-bbbb-cccc-100000000006"}))
        self.assertEqual([c[0] for c in azure.calls], ["GET", "POST", "GET", "GET", "GET"])
        self.assertEqual(self.waits, [install.GRANT_WAIT] * 2)

    def test_a_grant_azure_took_but_never_shows_is_not_reported_done(self):
        azure = BillingAzure(shows_after=None)
        self.assertFalse(self.subscriptions(azure, scope=MCA, yes=True))
        self.assertEqual(len(self.waits), install.GRANT_CHECKS)
        text = "\n".join(self.printed)
        self.assertIn("Azure took the request, but its role assignments do not show the role yet", text)
        self.assertNotIn("subscriptions: done", text)
        self.assertIn("subscriptions: an owner of the invoice section, of its billing profile or of the billing account gives it", text)
        self.assertTrue(any(line.startswith("  az rest --method get") for line in self.printed))

    def test_without_the_billing_role_the_person_who_has_it_gets_the_command(self):
        # The engineer may neither read nor give billing roles there, as usually only the customer's billing owner may.
        azure = BillingAzure(readable=False, grantable=False)
        self.assertFalse(self.subscriptions(azure, answer="y"))
        text = "\n".join(self.printed)
        self.assertIn("the EA role SubscriptionCreator on the enrollment account", text)
        self.assertIn("Azure refused it (HTTP 403)", text)
        self.assertIn("subscriptions: the enrollment account's owner, its Account Owner", text)
        self.assertIn(f"  az login --tenant {TENANT}", text)
        command = next(line for line in self.printed if line.startswith("  az rest --method put"))
        # As a shell would read it: the same call this step makes.
        words = shlex.split(command)
        method, path, body = install.grant_request("EA", EA, PRINCIPAL, TENANT)
        self.assertEqual(words[words.index("--url") + 1], f"https://management.azure.com{path}")
        self.assertEqual(json.loads(words[words.index("--body") + 1]), body)
        check = next(line for line in self.printed if line.startswith("  az rest --method get"))
        self.assertIn(f'value[?properties.principalId==`"{PRINCIPAL}"`].properties.roleDefinitionId', shlex.split(check))
        self.assertNotIn("'\"'\"'", check)  # one quoting, readable as sent

    def test_nothing_is_given_without_a_yes(self):
        azure = BillingAzure()
        self.assertFalse(self.subscriptions(azure, scope=MCA))
        self.assertEqual([c[0] for c in azure.calls], ["GET"])
        text = "\n".join(self.printed)
        self.assertIn("subscriptions: an owner of the invoice section, of its billing profile or of the billing account gives it", text)
        self.assertIn("or in the Azure portal: Cost Management + Billing, the invoice section, Access control (IAM): Add, role Azure "
                      "subscription creator, for the identity id-acme-vending-apply-nwe.", text)

    def test_a_billing_scope_vending_cannot_use_is_refused(self):
        for scope in ("/providers/Microsoft.Billing/billingAccounts/a:b_2019-05-31/customers/c", "/subscriptions/x"):
            with self.assertRaisesRegex(ConfigError, "is an EA enrollment account"):
                self.subscriptions(BillingAzure(), scope=scope)
        with self.assertRaisesRegex(ConfigError, "no vending apply identity"):
            self.subscriptions(BillingAzure(), contract={})

    # runners: GitHub-hosted, joined to management's network

    def hosted(self, runners: str = ""):
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP + "  runners:\n    kind: github-hosted\n" + runners)

    def test_the_runners_step_makes_the_three_and_moves_the_repos_onto_them(self):
        self.hosted()
        github = RunnersGitHub()
        install.step_runners(github, runners_azure(), self.folder, self.out)
        calls = dict((call[0], call[1]) for call in github.calls if call[0] != "variable")
        self.assertEqual(calls["configuration"], {"name": "mlz", "compute_service": "actions", "network_settings_ids": ["GHID123"]})
        self.assertEqual(calls["group"], {"name": "mlz", "visibility": "selected", "selected_repository_ids": [101, 102, 103],
                                          "allows_public_repositories": False, "network_configuration_id": "CONFIG"})
        self.assertEqual(calls["runner"], {"name": "mlz", "image": {"id": "ubuntu-latest", "source": "github"}, "size": "2-core",
                                           "maximum_runners": 10, "runner_group_id": 7})
        self.assertEqual(github.values, {(repo, "MLZ_RUNS_ON"): "mlz" for repo in REPOS.values()})
        self.assertIn("runners: the three repos run their plans, applies and publications on mlz from their next run. "
                      "To go back to GitHub's public runners: gh variable delete MLZ_RUNS_ON --repo <repo>.", self.printed)

    def test_the_repos_stay_on_public_runners_until_the_state_has_its_private_endpoint(self):
        self.hosted()
        github = RunnersGitHub()
        install.step_runners(github, runners_azure(endpoint=False), self.folder, self.out)
        self.assertEqual([call[0] for call in github.calls], ["configuration", "group", "runner"])
        self.assertEqual(github.values, {})
        self.assertTrue(any("run mlz install bootstrap again, then this step" in line for line in self.printed))

    def test_a_second_run_changes_nothing_in_place_and_narrows_a_widened_group(self):
        self.hosted()
        github = RunnersGitHub(
            configurations=[{"name": "mlz", "id": "CONFIG", "network_settings_ids": ["GHID123"]}],
            groups=[{"name": "mlz", "id": 7, "visibility": "all", "network_configuration_id": "CONFIG"}],
            runners=[{"name": "mlz", "runner_group_id": 7}],
        )
        github.group_repositories[7] = {101, 102, 103, 999}
        for repo in REPOS.values():
            github.values[(repo, "MLZ_RUNS_ON")] = "mlz"
        install.step_runners(github, runners_azure(), self.folder, self.out)
        self.assertEqual(github.calls, [
            ("update group", {"visibility": "selected", "allows_public_repositories": False, "network_configuration_id": "CONFIG"}),
            ("group repositories", [101, 102, 103]),
        ])

    def test_someone_who_is_not_an_owner_gets_the_commands_and_changes_nothing(self):
        self.hosted()
        github = RunnersGitHub(owner=False)
        install.step_runners(github, runners_azure(endpoint=False), self.folder, self.out)
        self.assertEqual(github.calls, [])
        text = "\n".join(self.printed)
        self.assertIn("you are not an owner of acme, so nothing was changed", text)
        self.assertIn("gh auth refresh -h github.com -s admin:org,manage_runners:org,read:network_configurations,write:network_configurations", text)
        self.assertIn("gh api -X POST orgs/acme/settings/network-configurations -f name=mlz -f compute_service=actions "
                      "-f 'network_settings_ids[]=GHID123' --jq .id", text)
        self.assertIn("-F 'selected_repository_ids[]=101' -F 'selected_repository_ids[]=102' -F 'selected_repository_ids[]=103' "
                      "-F allows_public_repositories=false -f network_configuration_id=\"$configuration\"", text)
        self.assertIn("-f 'image[id]=ubuntu-latest' -f 'image[source]=github' -f size=2-core -F maximum_runners=10 -F runner_group_id=\"$group\"", text)
        self.assertIn("as an admin on them. Not yet: the state account has no private endpoint yet, and the runner reaches the state "
                      "only through it: run mlz install bootstrap again, then run these:", text)
        self.assertIn("gh variable set MLZ_RUNS_ON --repo acme/mlz-vending --body mlz", text)

    def test_a_size_that_is_no_size_is_refused_before_it_reaches_a_command(self):
        self.hosted("    size: \"2-core'; printf INJECTED; #\"\n")
        with self.assertRaisesRegex(ConfigError, "github.runners.size is one of GitHub's runner sizes for github-hosted runners, such as 2-core"):
            install.step_runners(RunnersGitHub(owner=False), runners_azure(), self.folder, self.out)

    def test_the_runner_size_comes_from_bootstrap_yaml(self):
        self.hosted("    size: 4-core\n")
        github = RunnersGitHub()
        install.step_runners(github, runners_azure(), self.folder, self.out)
        self.assertEqual(dict(call[:2] for call in github.calls)["runner"]["size"], "4-core")

    def test_no_runners_where_github_does_not_join_them_to_a_network(self):
        self.hosted()
        (self.folder / "customer.yaml").write_text(CUSTOMER.replace("primary_region: norwayeast", "primary_region: westeurope"))
        with self.assertRaisesRegex(ConfigError, "only in some regions, not westeurope"):
            install.step_runners(RunnersGitHub(), runners_azure(), self.folder, self.out)

    def test_the_network_settings_come_first(self):
        self.hosted()
        with self.assertRaisesRegex(ConfigError, "no GitHub network settings yet \\(ghns-acme-mgt-pd-nwe-01\\)"):
            install.step_runners(RunnersGitHub(), FakeAzure(), self.folder, self.out)

    def test_another_network_configuration_named_mlz_is_not_taken_over(self):
        self.hosted()
        for found in ({"network_settings_ids": ["ELSEWHERE"]}, {"network_settings_ids": ["ELSEWHERE", "GHID123"]},
                      {"network_settings_ids": ["GHID123"], "failover_network_enabled": True, "failover_network_settings_ids": ["ELSEWHERE"]}):
            github = RunnersGitHub(configurations=[{"name": "mlz", "id": "OTHER", **found}])
            with self.assertRaisesRegex(ConfigError, "for other or further network settings; it must name ghns-acme-mgt-pd-nwe-01 alone"):
                install.step_runners(github, runners_azure(), self.folder, self.out)
            self.assertEqual(github.calls, [])

    def test_a_refused_network_configuration_names_the_enterprise_policy(self):
        self.hosted()
        with self.assertRaisesRegex(ConfigError, "Policies, Hosted compute networking"):
            install.step_runners(RunnersGitHub(fail_configuration=True), runners_azure(), self.folder, self.out)


    # runners: self-hosted, the default

    def self_hosted(self, runners: str = "    app_client_id: Iv23liRunners\n"):
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP + "  runners:\n" + runners)
        key = Path(self.tmp.name) / "runners.private-key.pem"
        key.write_text(APP_KEY)
        return key

    def run_self_hosted(self, github, azure, app_key=None):
        waits = []
        with unittest.mock.patch.object(install, "apply_bootstrap") as apply:
            install.step_runners(github, azure, self.folder, self.out, terraform="terraform", app_key=app_key, wait=waits.append)
        return apply, waits

    def test_the_runner_settings_default_to_self_hosted_on_fortytwo_s_image_size(self):
        self.assertEqual(install.runner_settings({"github": {}}),
                         {"kind": "self-hosted", "size": "Standard_D2ds_v6", "instances": 1, "app_client_id": None, "image": "fortytwo"})
        self.assertEqual(install.runner_settings({"github": {"runners": {"kind": "github-hosted"}}}),
                         {"kind": "github-hosted", "size": "2-core", "instances": 1, "app_client_id": None, "image": "fortytwo"})

    def test_runner_settings_that_would_reach_a_command_or_a_script_are_checked(self):
        for given, problem in (({"kind": "ephemeral"}, "kind is self-hosted or github-hosted, not 'ephemeral'"),
                               ({"kind": []}, "kind is self-hosted or github-hosted, not \\[\\]"),
                               ({"size": "Standard_D2'; rm -rf /"}, "size is an Azure virtual machine size for self-hosted runners"),
                               ({"instances": 60}, "instances is a whole number of runners from 0 to 59, as many as snet-runners holds"),
                               ({"instances": True}, "instances is a whole number"),
                               ({"app_client_id": "Iv23\"; x"}, "app_client_id is the runner App's client ID"),
                               ({"kind": "github-hosted", "instances": 2}, "instances is for self-hosted runners"),
                               ({"image": "ubuntu"}, "image is fortytwo or dev \\(Fortytwo's own tests\\), not 'ubuntu'"),
                               ({"kind": "github-hosted", "image": "dev"}, "image is for self-hosted runners"),
                               ({"labels": ["x"]}, "github.runners.labels is not a setting")):
            with self.assertRaisesRegex(ConfigError, problem):
                install.runner_settings({"github": {"runners": given}})

    def test_self_hosted_runners_need_the_runner_app(self):
        self.self_hosted("    instances: 2\n")
        with self.assertRaisesRegex(ConfigError, "needs github.runners.app_client_id: the client ID of the customer's runner App"):
            self.run_self_hosted(RunnersGitHub(), SelfHostedAzure())

    def test_the_group_the_key_the_runners_and_then_the_repos(self):
        key = self.self_hosted()
        github = RunnersGitHub()
        github.online = [{"name": "vmss-acme-mlz-pd-nwe-01000000", "status": "online", "labels": LABELS}]
        # The vault's firewall refuses this address first, then Key Vault has not seen the new role yet.
        azure = SelfHostedAzure(refusals=[
            refused("ForbiddenByFirewall", "Client address is not authorized and caller is not a trusted service.\r\nClient address: 203.0.113.7\r\nCaller: x"),
            refused("ForbiddenByRbac", "Caller is not authorized to perform action on resource."),
        ])
        apply, waits = self.run_self_hosted(github, azure, key)
        workflow = f"acme/mlz-core/.github/workflows/role.yml@{install.release()}"
        self.assertEqual(dict(github.calls[:1])["group"], {
            "name": "mlz", "visibility": "selected", "selected_repository_ids": [101, 102, 103], "allows_public_repositories": False,
            "restricted_to_workflows": True, "selected_workflows": [workflow]})
        self.assertEqual(azure.uploads, [("PUT", UPLOAD, {"value": APP_KEY, "contentType": "application/x-pem-file"})])
        steps = [(method, url.rsplit("/", 1)[-1] if "roleAssignments" not in url else "roleAssignment") for method, url, _ in azure.calls]
        self.assertEqual(steps, [("PUT", "roleAssignment"), ("PATCH", "kv-acme-mlz-pd-nwe-01"), ("PATCH", "kv-acme-mlz-pd-nwe-01"),
                                 ("DELETE", "roleAssignment"), ("GET", "current"), ("PUT", "current"), ("GET", "EncryptionAtHost"),
                                 ("POST", "register")])
        role, opened, closed = azure.calls[0][2], azure.calls[1][2], azure.calls[2][2]
        self.assertEqual(role["properties"]["principalId"], "33333333-3333-3333-3333-333333333333")
        self.assertTrue(role["properties"]["roleDefinitionId"].endswith("/roleDefinitions/b86a8fe4-44ce-4948-aee5-eccb2c155cd7"))
        self.assertEqual(opened["properties"]["networkAcls"]["ipRules"], [{"value": "203.0.113.7"}])
        self.assertEqual(closed["properties"]["networkAcls"]["ipRules"], [])
        self.assertTrue(azure.calls[5][2]["properties"]["accepted"])
        self.assertEqual(azure.calls[5][2]["properties"]["signature"], "SIGNED")  # the terms as Azure gave them
        self.assertEqual(waits, [10, 10])
        apply.assert_called_once()
        self.assertEqual(github.values, {(repo, "MLZ_RUNS_ON"): "mlz" for repo in REPOS.values()})
        self.assertFalse(any("MIIE" in line for line in self.printed))  # the key is never printed
        self.assertTrue(any("Delete the .pem file now, and never send it on" in line for line in self.printed))

    def test_an_upload_that_fails_still_takes_the_role_and_the_address_away(self):
        key = self.self_hosted()
        azure = SelfHostedAzure(refusals=[
            refused("ForbiddenByFirewall", "Client address: 203.0.113.7"),
            refused("BadParameter", "The request is not valid.", status=400),
        ])
        with self.assertRaisesRegex(ConfigError, "Key Vault refused the key \\(HTTP 400\\): The request is not valid."):
            self.run_self_hosted(RunnersGitHub(), azure, key)
        self.assertEqual([method for method, _, _ in azure.calls], ["PUT", "PATCH", "PATCH", "DELETE"])
        self.assertEqual(azure.calls[2][2]["properties"]["networkAcls"]["ipRules"], [])

    def test_the_role_goes_even_when_the_firewall_cannot_be_put_back(self):
        key = self.self_hosted()

        class Failing(SelfHostedAzure):
            def request(self, method, url, body=None):
                if method == "PATCH" and body["properties"]["networkAcls"]["ipRules"] == []:
                    self.calls.append((method, url.split("?")[0], body))
                    raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b"{}"))
                return super().request(method, url, body)

        azure = Failing(refusals=[refused("ForbiddenByFirewall", "Client address: 203.0.113.7")])
        self.run_self_hosted(RunnersGitHub(), azure, key)
        self.assertEqual([method for method, _, _ in azure.calls][:4], ["PUT", "PATCH", "PATCH", "DELETE"])
        text = "\n".join(self.printed)
        self.assertIn("these could not be undone after the upload; run them now:\naz keyvault network-rule remove --name kv-acme-mlz-pd-nwe-01 "
                      "--ip-address 203.0.113.7", text)
        self.assertNotIn("your role and address are gone", text)

    def test_a_key_key_vault_never_took_is_not_reported_as_stored(self):
        key = self.self_hosted()
        # Every write refused while the new role spreads, until the attempts run out.
        azure = SelfHostedAzure(refusals=[refused("ForbiddenByRbac", "not yet")] * 29 + [refused("ForbiddenByFirewall", "Client address: 203.0.113.7")])
        with self.assertRaisesRegex(ConfigError, "Key Vault did not take the key within five minutes"):
            self.run_self_hosted(RunnersGitHub(), azure, key)
        self.assertEqual(azure.uploads, [])
        self.assertFalse(any("Delete the .pem file now" in line for line in self.printed))
        self.assertEqual([method for method, _, _ in azure.calls][-2:], ["PATCH", "DELETE"])  # cleaned up all the same

    def test_the_group_admits_the_releases_the_repos_pin(self):
        self.self_hosted()
        github = RunnersGitHub(pins={"acme/mlz-governance": "v0.8.0", "acme/mlz-platform": "v0.8.0"})
        github.tags.add((CORE_COPY, "v0.8.0"))
        self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))
        self.assertEqual(dict(github.calls[:1])["group"]["selected_workflows"],
                         sorted([mirror.workflow(CORE_COPY, "v0.8.0"), mirror.workflow(CORE_COPY, install.release())]))

    def test_a_repo_pinned_to_anything_but_a_release_of_mlz_core_stops_the_step(self):
        self.self_hosted()
        github = RunnersGitHub(pins={"acme/mlz-vending": "main"})
        with self.assertRaisesRegex(ConfigError, "acme/mlz-vending: its .github/workflows/mlz.yml calls acme/mlz-core's role.yml at main, "
                                                 "which the runner group cannot admit"):
            self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))
        self.assertEqual(github.calls, [])

    def test_a_release_the_copy_has_no_tag_for_stops_the_step(self):
        self.self_hosted()
        github = RunnersGitHub(pins={"acme/mlz-vending": "v0.8.0"})
        with self.assertRaisesRegex(ConfigError, "acme/mlz-vending: its .github/workflows/mlz.yml calls acme/mlz-core's role.yml at v0.8.0, which "
                                                 "acme/mlz-core has no tag for: python3 -m mlz upgrade acme/mlz-vending --to v0.8.0 --core acme/mlz-core"):
            self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))

    def test_a_repo_still_calling_mlz_core_s_own_workflow_stops_the_step(self):
        # A runner group admits no workflow of another organization's: only the organization's copy will do.
        self.self_hosted()
        github = RunnersGitHub(pins={"acme/mlz-platform": f"fortytwoservices/mlz-core@{install.release()}"})
        with self.assertRaisesRegex(ConfigError, "acme/mlz-platform: its .github/workflows/mlz.yml calls fortytwoservices/mlz-core's role.yml "
                                                 f"at {install.release()}, which the runner group cannot admit: only acme/mlz-core's.*"
                                                 "python3 -m mlz upgrade acme/mlz-platform --core acme/mlz-core moves it onto the copy"):
            self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))

    def test_fortytwo_s_dev_image_for_its_own_tests(self):
        self.self_hosted("    app_client_id: Iv23liRunners\n    image: dev\n")
        azure = SelfHostedAzure(secret=True)
        self.run_self_hosted(RunnersGitHub(), azure)
        terms = [url for method, url, _ in azure.calls if url.endswith("/agreements/current")]
        self.assertEqual(terms[0], f"/subscriptions/00000000-0000-0000-0000-000000000001/providers/Microsoft.MarketplaceOrdering/offerTypes/"
                                   "virtualmachine/publishers/amestofortytwoas1653635920536/offers/self_hosted_runner_ado/plans/"
                                   "ubuntu-latest-dev-gen2/agreements/current")
        self.assertEqual(install.runner_image("dev")["plan"], {"name": "ubuntu-latest-dev-gen2", "product": "self_hosted_runner_ado",
                                                                "publisher": "amestofortytwoas1653635920536"})

    def test_a_subscription_the_dev_image_is_not_for_is_told_so(self):
        self.self_hosted("    app_client_id: Iv23liRunners\n    image: dev\n")

        # Azure may refuse reading the plan's terms, or accepting them once read.
        for refused_method, code in (("GET", 404), ("PUT", 403)):
            class Refusing(SelfHostedAzure):
                def request(self, method, url, body=None):
                    if method == refused_method and url.split("?")[0].endswith("/agreements/current"):
                        raise urllib.error.HTTPError(url, code, "Refused", {}, io.BytesIO(b"{}"))
                    return super().request(method, url, body)

            with self.assertRaisesRegex(ConfigError, "the management subscription may not use Fortytwo's dev image \\(self_hosted_runner_ado, "
                                                     f"ubuntu-latest-dev-gen2; HTTP {code}\\): it is for Fortytwo's own tenants"):
                self.run_self_hosted(RunnersGitHub(), Refusing(secret=True))

    def test_a_file_that_is_no_private_key_is_refused_before_anything_changes(self):
        key = self.self_hosted()
        key.write_text("ssh-ed25519 AAAA not a GitHub App key\n")
        azure = SelfHostedAzure()
        with self.assertRaisesRegex(ConfigError, "is not a private key file: use the .pem file the runner App's settings page downloads"):
            self.run_self_hosted(RunnersGitHub(), azure, key)
        self.assertEqual(azure.calls, [])

    def test_without_a_key_in_the_vault_the_step_asks_for_one(self):
        self.self_hosted()
        with self.assertRaisesRegex(ConfigError, "the runner App's key is not in kv-acme-mlz-pd-nwe-01 yet: run this step with --app-key"):
            self.run_self_hosted(RunnersGitHub(), SelfHostedAzure())

    def test_a_key_already_in_the_vault_is_kept(self):
        self.self_hosted()
        azure = SelfHostedAzure(secret=True, accepted=True)
        apply, _ = self.run_self_hosted(RunnersGitHub(), azure)
        self.assertEqual(azure.uploads, [])
        self.assertEqual([method for method, _, _ in azure.calls], ["GET", "GET", "POST"])  # the terms, the feature, the provider
        apply.assert_called_once()

    def test_someone_who_is_not_an_owner_gets_the_group_command_and_nothing_else_happens(self):
        key = self.self_hosted()
        azure = SelfHostedAzure()
        apply, _ = self.run_self_hosted(RunnersGitHub(owner=False), azure, key)
        apply.assert_not_called()
        self.assertEqual(azure.calls, [])
        text = "\n".join(self.printed)
        self.assertIn("gh api -X POST orgs/acme/actions/runner-groups -f name=mlz -f visibility=selected -F 'selected_repository_ids[]=101' "
                      "-F 'selected_repository_ids[]=102' -F 'selected_repository_ids[]=103' -F allows_public_repositories=false "
                      f"-F restricted_to_workflows=true -f 'selected_workflows[]=acme/mlz-core/.github/workflows/role.yml@{install.release()}'", text)
        self.assertIn("Then run this step again.", text)

    def test_a_group_made_for_github_hosted_runners_is_not_taken_over(self):
        key = self.self_hosted()
        github = RunnersGitHub(groups=[{"name": "mlz", "id": 7, "network_configuration_id": "CONFIG"}])
        with self.assertRaisesRegex(ConfigError, "runner group mlz is for GitHub-hosted runners"):
            self.run_self_hosted(github, SelfHostedAzure(), key)

    def test_a_new_release_is_admitted_beside_those_the_group_admits_already(self):
        self.self_hosted()
        older = "acme/mlz-core/.github/workflows/role.yml@v0.1.0"
        # Anything but mlz-core's releases goes: a customer repo's workflow, or role.yml at a branch.
        others = ["acme/mlz-platform/.github/workflows/mlz.yml@refs/heads/main", "acme/mlz-core/.github/workflows/role.yml@main"]
        github = RunnersGitHub(groups=[{"name": "mlz", "id": 7, "visibility": "selected", "restricted_to_workflows": True, "selected_workflows": [older, *others]}])
        github.group_repositories[7] = {101, 102, 103}
        self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))
        self.assertEqual(github.calls, [("update group", {"visibility": "selected", "allows_public_repositories": False, "restricted_to_workflows": True,
                                                          "selected_workflows": sorted([older, mirror.workflow(CORE_COPY, install.release())])})])

    def test_encryption_at_host_still_registering_stops_before_the_apply(self):
        self.self_hosted()
        for state, registers in (("NotRegistered", True), ("Registering", False)):
            azure = SelfHostedAzure(secret=True, accepted=True, feature=state)
            apply, _ = self.run_self_hosted(RunnersGitHub(), azure)
            apply.assert_not_called()
            self.assertEqual(("POST", "register") in [(m, u.rsplit("/", 1)[-1]) for m, u, _ in azure.calls], registers)
        self.assertIn("runners: encryption at host is being registered on the management subscription, which takes a few minutes. "
                      "Run this step again then.", self.printed)

    def test_the_repos_wait_until_a_runner_is_online_and_the_state_has_its_endpoint(self):
        self.self_hosted()
        github = RunnersGitHub()
        github.online = [{"name": "vmss-acme-mlz-pd-nwe-01000000", "status": "offline", "labels": LABELS}]
        self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))
        self.assertEqual(github.values, {})
        self.assertTrue(any("none is online in the runner group mlz yet" in line for line in self.printed))
        # Online, but without the label the repos' jobs ask for: it could take none of them.
        github.online = [{"name": "someone-else", "status": "online", "labels": LABELS[:3]}]
        self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True))
        self.assertEqual(github.values, {})
        github.online = [{"name": "vmss-acme-mlz-pd-nwe-01000000", "status": "online", "labels": LABELS}]
        self.run_self_hosted(github, SelfHostedAzure(secret=True, accepted=True, endpoint=False))
        self.assertEqual(github.values, {})
        self.assertTrue(any("the state account has no private endpoint yet" in line for line in self.printed))

    def test_the_runners_wait_for_their_subnet_and_their_vault(self):
        self.self_hosted()
        with self.assertRaisesRegex(ConfigError, "management's network has no snet-runners yet"):
            self.run_self_hosted(RunnersGitHub(), SelfHostedAzure(subnet=False))
        with self.assertRaisesRegex(ConfigError, "the runners' vault kv-acme-mlz-pd-nwe-01 is not there yet"):
            self.run_self_hosted(RunnersGitHub(), SelfHostedAzure(vault=False))


class PrepareTest(unittest.TestCase):
    """The real Terraform.prepare, against a checkout of mlz-core made in a temporary folder."""

    def test_the_copy_is_the_checkouts_root_without_its_override_files_or_older_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            core, work = Path(tmp) / "core", Path(tmp) / "work"
            (core / "bootstrap/terraform").mkdir(parents=True)
            (core / "bootstrap/terraform/main.tf").write_text("# main\n")
            (core / "bootstrap/terraform/backend_override.tf").write_text('terraform {\n  backend "local" {}\n}\n')
            (core / "bootstrap/terraform/.terraform.lock.hcl").write_text("# lock\n")
            (work / "bootstrap/terraform").mkdir(parents=True)
            (work / "bootstrap/terraform/removed.tf").write_text("# from an older mlz-core\n")
            with unittest.mock.patch.object(install, "CORE", core), unittest.mock.patch.dict(os.environ, {"GITHUB_TOKEN": "token"}):
                install.Terraform(work, "tenant").prepare()
            root = work / "bootstrap/terraform"
            self.assertEqual(sorted(p.name for p in root.iterdir()), [".terraform.lock.hcl", "main.tf"])
            self.assertFalse((work / "modules").exists())  # the names come in names.json, worked out by mlz install

    def test_the_checkout_itself_is_refused_as_the_work_folder_and_keeps_its_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp) / "core"
            (core / "bootstrap/terraform").mkdir(parents=True)
            (core / "bootstrap/terraform/main.tf").write_text("# main\n")
            with unittest.mock.patch.object(install, "CORE", core), unittest.mock.patch.dict(os.environ, {"GITHUB_TOKEN": "token"}):
                with self.assertRaisesRegex(ConfigError, "own checkout"):
                    install.Terraform(core / "bootstrap" / "..", "tenant").prepare()
            self.assertTrue((core / "bootstrap/terraform/main.tf").exists())


class PagedGitHub(GitHub):
    """GitHub with its API in memory: a listing answers per_page items a page."""

    def __init__(self, listings: dict[str, list], wrapped: dict[str, str] | None = None):
        # wrapped: the listings GitHub answers inside an object, by path, with the key that holds the items.
        self.listings, self.wrapped, self.posted = listings, wrapped or {}, []

    def _api(self, path, method="GET", body=None, missing_ok=False):
        if method == "POST":
            self.posted.append((path, body))
            return {}
        base, _, query = path.partition("?")
        params = dict(part.split("=") for part in query.split("&"))
        size, page = int(params["per_page"]), int(params["page"])
        items = self.listings[base][(page - 1) * size:page * size]
        return {self.wrapped[base]: items} if base in self.wrapped else items


class GitHubTest(unittest.TestCase):
    def test_a_ruleset_past_the_first_page_is_found_and_not_made_again(self):
        rulesets = [{"name": f"other-{n}"} for n in range(120)] + [{"name": "main"}]
        github = PagedGitHub({"repos/acme/mlz-platform/rulesets": rulesets})
        self.assertFalse(github.add_ruleset("acme/mlz-platform", install.main_ruleset("platform")))
        self.assertEqual(github.posted, [])

    def test_every_page_of_releases_is_read(self):
        releases = [{"tag_name": f"v0.{n}.0", "body": "", "draft": False, "prerelease": False} for n in range(150)]
        self.assertEqual(len(PagedGitHub({"repos/f/mlz-core/releases": releases}).releases("f/mlz-core")), 150)

    def test_the_runner_settings_past_the_first_page_are_found(self):
        others = [{"name": f"other-{n}", "id": n} for n in range(150)]
        paths = {"orgs/acme/settings/network-configurations": "network_configurations", "orgs/acme/actions/runner-groups": "runner_groups",
                 "orgs/acme/actions/hosted-runners": "runners", "orgs/acme/actions/runner-groups/7/repositories": "repositories"}
        github = PagedGitHub({path: others + [{"name": "mlz", "id": 999}] for path in paths}, wrapped=paths)
        self.assertEqual(github.network_configuration("acme", "mlz")["id"], 999)
        self.assertEqual(github.runner_group("acme", "mlz")["id"], 999)
        self.assertEqual(github.hosted_runner("acme", "mlz")["id"], 999)
        self.assertEqual(len(github.runner_group_repositories("acme", 7)), 151)
        self.assertIsNone(github.runner_group("acme", "missing"))

    def test_only_an_active_admin_is_an_owner(self):
        for membership, owner in (({"state": "active", "role": "admin"}, True), ({"state": "active", "role": "member"}, False),
                                  ({"state": "pending", "role": "admin"}, False), (None, False)):
            github = AnsweringGitHub({"user": {"login": "someone"}, "orgs/acme/memberships/someone": membership})
            self.assertEqual(github.is_owner("acme"), owner, membership)

    def test_a_variable_is_made_or_changed(self):
        github = AnsweringGitHub({"repos/acme/mlz-platform/actions/variables/MLZ_RUNS_ON": None})
        github.set_variable("acme/mlz-platform", "MLZ_RUNS_ON", "mlz")
        github.answers["repos/acme/mlz-platform/actions/variables/MLZ_RUNS_ON"] = {"name": "MLZ_RUNS_ON", "value": "mlz"}
        github.set_variable("acme/mlz-platform", "MLZ_RUNS_ON", "mlz")
        self.assertEqual([call[:2] for call in github.sent], [
            ("POST", "repos/acme/mlz-platform/actions/variables"),
            ("PATCH", "repos/acme/mlz-platform/actions/variables/MLZ_RUNS_ON"),
        ])


class ModeGitHub(GitHub):
    """A branch whose top has mlz at the given mode, a README and a folder; writes recorded, each answered with a SHA."""

    def __init__(self, mode, kind="blob"):
        self.sent = []
        top = [{"path": "mlz", "mode": mode, "type": kind, "sha": "blob-mlz"},
               {"path": "README.md", "mode": "100644", "type": "blob", "sha": "blob-readme"},
               {"path": "tools", "mode": "040000", "type": "tree", "sha": "tree-tools"}]
        self.answers = {"repos/acme/mlz-vending/git/ref/heads/mlz/upgrade": {"object": {"sha": "head"}},
                        "repos/acme/mlz-vending/git/commits/head": {"tree": {"sha": "tree"}},
                        "repos/acme/mlz-vending/git/trees/tree": {"tree": top},
                        "repos/acme/mlz-vending/git/trees/mlz/upgrade": {"tree": top}}

    def _api(self, path, method="GET", body=None, missing_ok=False):
        if method != "GET":
            self.sent.append((method, path, body))
            return {"sha": f"made-{len(self.sent)}"}
        return self.answers[path]


class ModeTest(unittest.TestCase):
    """The real calls that make ./mlz executable, which createCommitOnBranch cannot (GitHub.make_executable)."""

    def test_a_file_without_its_mode_is_found_and_only_it(self):
        # Only the paths asked about, and only those there: README.md is not asked about, and nothing is named missing.
        self.assertEqual(ModeGitHub("100644").not_executable("acme/mlz-vending", "mlz/upgrade", ["mlz", "missing"]), ["mlz"])
        self.assertEqual(ModeGitHub("100755").not_executable("acme/mlz-vending", "mlz/upgrade", ["mlz"]), [])

    def test_one_commit_on_the_branch_s_head_gives_the_same_blob_its_mode(self):
        github = ModeGitHub("100644")
        self.assertTrue(github.make_executable("acme/mlz-vending", "mlz/upgrade", ["mlz"], "chore: mlz executable"))
        self.assertEqual(github.sent, [
            ("POST", "repos/acme/mlz-vending/git/trees",
             {"base_tree": "tree", "tree": [{"path": "mlz", "mode": "100755", "type": "blob", "sha": "blob-mlz"}]}),
            ("POST", "repos/acme/mlz-vending/git/commits", {"message": "chore: mlz executable", "tree": "made-1", "parents": ["head"]}),
            ("PATCH", "repos/acme/mlz-vending/git/refs/heads/mlz/upgrade", {"sha": "made-2", "force": False}),  # never over a newer push
        ])

    def test_an_executable_file_a_folder_or_a_link_makes_no_commit(self):
        for mode, kind in (("100755", "blob"), ("040000", "tree"), ("120000", "blob")):  # a symbolic link is a blob at 120000
            github = ModeGitHub(mode, kind)
            self.assertEqual(github.not_executable("acme/mlz-vending", "mlz/upgrade", ["mlz"]), [], mode)
            self.assertFalse(github.make_executable("acme/mlz-vending", "mlz/upgrade", ["mlz"], "chore: mlz executable"), mode)
            self.assertEqual(github.sent, [], mode)


class AnsweringGitHub(GitHub):
    """GitHub with canned answers by path; None is a 404. Writes are recorded, never sent."""

    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def _api(self, path, method="GET", body=None, missing_ok=False):
        if method != "GET":
            self.sent.append((method, path, body))
            return {}
        found = self.answers.get(path)
        if found is None and not missing_ok:
            raise ConfigError(f"gh api GET {path}: HTTP 404")
        return found


if __name__ == "__main__":
    unittest.main()
