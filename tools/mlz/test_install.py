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

from mlz import install
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
  reviewers: [acme-lead]
"""
REPOS = {"governance": "acme/mlz-governance", "platform": "acme/mlz-platform", "vending": "acme/mlz-vending"}


WORKFLOW = ".github/workflows/mlz.yml"


class FakeGitHub:
    def __init__(self, existing=(), private=True, variables=(), secrets=(), prefixes=None, files=None, open_pull=None,
                 org_branch="main", existing_branch="main", half_made=(), rulesets=(), empty=(), org_plan="enterprise"):
        self.org_plan = org_plan
        self.repos = {repo: {"private": private, "default_branch": existing_branch} for repo in existing}
        self.org_branch = org_branch
        # An existing MLZ repo has its workflow, unless it is half-made (created, its starter commit failed).
        self.files = {(repo, WORKFLOW): ("on: push\n", "sha") for repo in existing if repo not in half_made}
        self.files.update(files or {})
        self.renamed, self.empty = [], set(empty)
        self.existing_rulesets = set(rulesets)
        self.variables, self.secrets = set(variables), set(secrets)
        self.prefixes = prefixes or {repo: f"repo:acme@1/{repo.split('/')[1]}@2" for repo in REPOS.values()}
        self._open_pull = open_pull
        self.created, self.commits, self.rulesets, self.pulls, self.branches = [], [], [], [], []

    def repo(self, repo):
        return self.repos.get(repo)

    def plan(self, org):
        return self.org_plan

    def create_repo(self, owner, name):
        self.created.append(f"{owner}/{name}")
        self.repos[f"{owner}/{name}"] = {"private": True, "default_branch": self.org_branch}

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

    def add_ruleset(self, repo, ruleset):
        if (repo, ruleset["name"]) in self.existing_rulesets:
            return False
        self.rulesets.append((repo, ruleset["name"]))
        return True

    def has_variable(self, repo, name):
        return (repo, name) in self.variables

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


class FakeTerraform:
    """Answers like terraform would. migrate_failures: how many state moves fail before one succeeds."""

    def __init__(self, work: Path, plan_status=2, migrate_failures=0, subjects=None, state_endpoint=False, contract_identities=None):
        self.work, self.root = work, work / "bootstrap" / "terraform"
        self.contract_identities = contract_identities or {}
        self.plan_status, self.migrate_failures = plan_status, migrate_failures
        self.state_endpoint = state_endpoint
        self.subjects = subjects or {f"{role}-plan": f"repo:acme@1/mlz-{role}@2:pull_request" for role in install.ROLES}
        self.calls: list[tuple] = []

    def prepare(self):
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, *arguments, capture=False):
        self.calls.append(arguments)
        status = 0
        if arguments[0] == "plan":
            status = self.plan_status
        elif "-migrate-state" in arguments:
            status = 1 if self.migrate_failures > 0 else 0
            self.migrate_failures -= 1
        return subprocess.CompletedProcess(arguments, status, "", "")

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

    def __init__(self, assignments=(), readable=True, grantable=True):
        self.assignments, self.readable, self.grantable = list(assignments), readable, grantable
        self.calls = []

    def request(self, method, url, body=None):
        self.calls.append((method, url, body))
        if method == "GET" and not self.readable or method != "GET" and not self.grantable:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return {"value": self.assignments} if method == "GET" else {}


class RunnersGitHub(FakeGitHub):
    """The organization's runner settings, as an owner (or not) sees them."""

    def __init__(self, owner=True, configurations=(), groups=(), runners=(), fail_configuration=False, **kwargs):
        super().__init__(existing=REPOS.values(), **kwargs)
        for number, repo in enumerate(REPOS.values()):
            self.repos[repo]["id"] = 101 + number
        self.owner, self.fail_configuration = owner, fail_configuration
        self.configurations, self.groups, self.runners = list(configurations), list(groups), list(runners)
        self.group_repositories, self.values, self.calls = {}, {}, []

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
        self.assertNotIn("x-release-please", workflow)
        self.assertEqual(github.rulesets, [(repo, "main") for repo in REPOS.values()])
        self.assertIn("acme/mlz-platform: exists, files left as they are", self.printed)

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
        self.assertNotIn(("acme/mlz-platform", "main"), github.rulesets)  # the customer's own may be stricter
        self.assertIn("acme/mlz-platform: has a ruleset named main already, left as it is; check that it allows changes only through pull requests", self.printed)

    def test_an_adopted_repo_without_the_workflow_keeps_its_own_files(self):
        own = ("acme/mlz-governance", "customer.yaml")
        github = FakeGitHub(existing=REPOS.values(), half_made=["acme/mlz-governance"], files={own: ("name: Ours\n", "sha")})
        install.step_repos(github, self.folder, self.out)
        added = github.commits[0][3]
        self.assertIn(WORKFLOW, added)
        self.assertNotIn("customer.yaml", added)  # already there: never replaced
        self.assertIn("bootstrap.yaml", added)

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
        rules = {rule["type"] for rule in install.MAIN_RULESET["rules"]}
        self.assertEqual(rules, {"deletion", "non_fast_forward", "pull_request"})
        self.assertNotIn("bypass_actors", install.MAIN_RULESET)

    def test_check_passes_when_the_app_is_set_everywhere(self):
        github = FakeGitHub(existing=REPOS.values(),
                            variables=[(r, install.APP_VARIABLE) for r in REPOS.values()], secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        install.step_check(github, self.folder, self.out)
        self.assertEqual(self.printed[0], "names: the state account will be stacmemlzstatepdnwe01")
        self.assertEqual(self.printed[1], "acme: on GitHub Enterprise")
        self.assertEqual(len(self.printed), 5)

    def test_check_refuses_an_organization_not_on_enterprise(self):
        ready = dict(existing=REPOS.values(), variables=[(r, install.APP_VARIABLE) for r in REPOS.values()],
                     secrets=[(r, install.APP_SECRET) for r in REPOS.values()])
        with self.assertRaisesRegex(ConfigError, "acme: on GitHub's team plan. MLZ needs GitHub Enterprise: every apply waits for a reviewer"):
            install.step_check(FakeGitHub(**ready, org_plan="team"), self.folder, self.out)
        # Only owners see the plan: said, not refused.
        install.step_check(FakeGitHub(**ready, org_plan=None), self.folder, self.out)
        self.assertIn("acme: its GitHub plan is shown to its owners only, so it is not checked here; MLZ needs GitHub Enterprise.", self.printed)

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
        self.assertEqual([c[0] for c in terraform.calls], ["init", "plan"])

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
        return install.step_subscriptions(azure, terraform, self.folder, scope, TENANT, yes, self.out, lambda question: answer)

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
        method, path, body = azure.calls[-1]
        self.assertEqual(method, "PUT")
        # Named after the scope and the identity: given again, it is the same assignment.
        name = path.split("/billingRoleAssignments/")[1].split("?")[0]
        self.assertEqual(path, f"{EA}/billingRoleAssignments/{name}?api-version=2019-10-01-preview")
        self.assertEqual(install.grant_request("EA", EA, PRINCIPAL, TENANT)[1], path)
        self.assertEqual(body, {"properties": {"principalId": PRINCIPAL, "principalTenantId": TENANT,
                                               "roleDefinitionId": f"{EA}/billingRoleDefinitions/a0bcee42-bf30-4d1b-926a-48d21664ef71"}})

    def test_mca_the_role_is_given_on_the_invoice_section(self):
        azure = BillingAzure()
        self.assertTrue(self.subscriptions(azure, scope=MCA, yes=True))
        self.assertEqual(azure.calls[-1], ("POST", f"{MCA}/createBillingRoleAssignment?api-version=2024-04-01", {
            "principalId": PRINCIPAL, "principalTenantId": TENANT,
            "roleDefinitionId": f"{MCA}/billingRoleDefinitions/30000000-aaaa-bbbb-cccc-100000000006"}))

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

    # runners

    def test_the_runners_step_makes_the_three_and_moves_the_repos_onto_them(self):
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
        github = RunnersGitHub()
        install.step_runners(github, runners_azure(endpoint=False), self.folder, self.out)
        self.assertEqual([call[0] for call in github.calls], ["configuration", "group", "runner"])
        self.assertEqual(github.values, {})
        self.assertTrue(any("run mlz install bootstrap again, then this step" in line for line in self.printed))

    def test_a_second_run_changes_nothing_in_place_and_narrows_a_widened_group(self):
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

    def test_a_value_from_yaml_stays_one_argument_in_the_commands(self):
        hostile = "2-core'; printf INJECTED; #"
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("  reviewers: [acme-lead]\n", f'  reviewers: [acme-lead]\n  runner_size: "{hostile}"\n'))
        install.step_runners(RunnersGitHub(owner=False), runners_azure(), self.folder, self.out)
        line = next(line for block in self.printed for line in block.split("\n") if "hosted-runners" in line)
        words = shlex.split(line)
        self.assertIn(f"size={hostile}", words)
        self.assertNotIn("printf", words)

    def test_the_runner_size_comes_from_bootstrap_yaml(self):
        (self.folder / "bootstrap.yaml").write_text(BOOTSTRAP.replace("  reviewers: [acme-lead]\n", "  reviewers: [acme-lead]\n  runner_size: 4-core\n"))
        github = RunnersGitHub()
        install.step_runners(github, runners_azure(), self.folder, self.out)
        self.assertEqual(dict(call[:2] for call in github.calls)["runner"]["size"], "4-core")

    def test_no_runners_where_github_does_not_join_them_to_a_network(self):
        (self.folder / "customer.yaml").write_text(CUSTOMER.replace("primary_region: norwayeast", "primary_region: westeurope"))
        with self.assertRaisesRegex(ConfigError, "only in some regions, not westeurope"):
            install.step_runners(RunnersGitHub(), runners_azure(), self.folder, self.out)

    def test_the_network_settings_come_first(self):
        with self.assertRaisesRegex(ConfigError, "no GitHub network settings yet \\(ghns-acme-mgt-pd-nwe-01\\)"):
            install.step_runners(RunnersGitHub(), FakeAzure(), self.folder, self.out)

    def test_another_network_configuration_named_mlz_is_not_taken_over(self):
        for found in ({"network_settings_ids": ["ELSEWHERE"]}, {"network_settings_ids": ["ELSEWHERE", "GHID123"]},
                      {"network_settings_ids": ["GHID123"], "failover_network_enabled": True, "failover_network_settings_ids": ["ELSEWHERE"]}):
            github = RunnersGitHub(configurations=[{"name": "mlz", "id": "OTHER", **found}])
            with self.assertRaisesRegex(ConfigError, "for other or further network settings; it must name ghns-acme-mgt-pd-nwe-01 alone"):
                install.step_runners(github, runners_azure(), self.folder, self.out)
            self.assertEqual(github.calls, [])

    def test_a_refused_network_configuration_names_the_enterprise_policy(self):
        with self.assertRaisesRegex(ConfigError, "Policies, Hosted compute networking"):
            install.step_runners(RunnersGitHub(fail_configuration=True), runners_azure(), self.folder, self.out)


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
        self.assertFalse(github.add_ruleset("acme/mlz-platform", install.MAIN_RULESET))
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
