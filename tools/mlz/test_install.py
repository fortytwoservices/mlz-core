"""Tests for mlz install, with GitHub, Terraform and Azure in memory. YAML is read with terraform's yamldecode."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from mlz import install
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
                 org_branch="main", existing_branch="main", half_made=()):
        self.repos = {repo: {"private": private, "default_branch": existing_branch} for repo in existing}
        self.org_branch = org_branch
        # An existing MLZ repo has its workflow, unless it is half-made (created, its starter commit failed).
        self.files = {(repo, WORKFLOW): ("on: push\n", "sha") for repo in existing if repo not in half_made}
        self.files.update(files or {})
        self.renamed = []
        self.variables, self.secrets = set(variables), set(secrets)
        self.prefixes = prefixes or {repo: f"repo:acme@1/{repo.split('/')[1]}@2" for repo in REPOS.values()}
        self._open_pull = open_pull
        self.created, self.commits, self.rulesets, self.pulls, self.branches = [], [], [], [], []

    def repo(self, repo):
        return self.repos.get(repo)

    def create_repo(self, owner, name):
        self.created.append(f"{owner}/{name}")
        self.repos[f"{owner}/{name}"] = {"private": True, "default_branch": self.org_branch}

    def default_branch(self, repo):
        return self.repos.get(repo, {}).get("default_branch", "main")

    def rename_branch(self, repo, branch, name):
        self.renamed.append((repo, branch, name))
        self.repos[repo]["default_branch"] = name

    def head(self, repo, branch):
        return f"head-of-{branch}"

    def commit_files(self, repo, branch, head, files, message):
        self.commits.append((repo, branch, head, files, message))

    def upsert_ruleset(self, repo, ruleset):
        self.rulesets.append((repo, ruleset["name"]))

    def has_variable(self, repo, name):
        return (repo, name) in self.variables

    def has_secret(self, repo, name):
        return (repo, name) in self.secrets

    def subject_prefix(self, repo):
        return self.prefixes[repo]

    def file(self, repo, path, ref):
        return self.files.get((repo, path), (None, None))

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

    def __init__(self, work: Path, plan_status=2, migrate_failures=0, subjects=None):
        self.work, self.root = work, work / "bootstrap" / "terraform"
        self.plan_status, self.migrate_failures = plan_status, migrate_failures
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
            "contract": {"top_management_group": "acme", "state": {"subscription": "00000000-0000-0000-0000-000000000001",
                         "resource_group": "rg-acme-bootstrap-nwe", "storage_account": "stacmestate1234"}, "identities": {}},
        }[name])


class FakeAzure:
    def __init__(self, parents):
        self._parents = parents
        self.moves = []

    def parents(self):
        return self._parents

    def move(self, group, subscription):
        self.moves.append((group, subscription))


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

    def test_a_half_made_repo_gets_its_starter(self):
        github = FakeGitHub(existing=REPOS.values(), half_made=["acme/mlz-vending"])
        install.step_repos(github, self.folder, self.out)
        self.assertEqual([repo for repo, *_ in github.commits], ["acme/mlz-vending"])
        self.assertIn(WORKFLOW, github.commits[0][3])
        self.assertIn("acme/mlz-vending: given the missing files of the vending starter", self.printed)

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
        self.assertEqual(len(self.printed), 3)

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

    def test_publish_needs_the_state_in_its_account(self):
        with self.assertRaisesRegex(ConfigError, "run mlz install bootstrap first"):
            install.step_publish(FakeGitHub(), FakeTerraform(self.work), self.folder, self.out)


if __name__ == "__main__":
    unittest.main()
