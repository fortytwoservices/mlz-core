"""Tests for the workload repos' files (tools/mlz/workload.py). Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mlz import workload

WORKLOAD = {"landing_zone": "payments-prod", "subscription_id": "33333333-3333-3333-3333-333333333333", "location": "norwayeast",
            "org": "acme", "app": "pay", "env": "pd", "region": "nwe", "template": "{type}-{org}-{app}-{purpose}-{env}-{region}-{nn}", "templates": {}}


class FakeGitHub:
    """Repos and their main branch's files, in memory."""

    def __init__(self, repos: dict[str, dict[str, str]], archived=()):
        self.repos, self.archived, self.commits = repos, set(archived), []

    def repo(self, repo):
        return {"archived": repo in self.archived} if repo in self.repos else None

    def file(self, repo, path, ref):
        text = self.repos[repo].get(path)
        return (text, "sha") if text is not None else (None, None)

    def head(self, repo, branch):
        return f"head-{len(self.commits)}"

    def commit_files(self, repo, branch, head, files, message):
        self.commits.append((repo, branch, head, dict(files), message))
        self.repos[repo].update(files)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.configs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, key: str, **extra) -> None:
        config = {"key": key, "github": {"organization": "acme-org", "repo": key, "create": True, "team": "payments-devs"},
                  "workload": {**WORKLOAD, "landing_zone": key}, "decommission": False, **extra}
        (self.configs / f"{key}.json").write_text(json.dumps(config))

    def sync(self, github) -> list[str]:
        out = []
        workload.sync(github, self.configs, out.append)
        return out

    def test_a_new_repo_gets_the_starter_and_its_values_in_one_commit(self):
        self.config("payments-prod")
        github = FakeGitHub({"acme-org/payments-prod": {"README.md": "# payments-prod\nWorkload repo of landing zone payments-prod.\n"}})
        self.sync(github)
        [(repo, branch, _, files, message)] = github.commits
        self.assertEqual((repo, branch), ("acme-org/payments-prod", "main"))
        self.assertEqual(set(files), {*workload.starter(), "mlz.auto.tfvars.json"})
        # GitHub's own first README is replaced; the pins are the release's, without release-please's marker.
        self.assertIn("This landing zone's own Terraform", files["README.md"])
        self.assertIn("uses: fortytwoservices/mlz-core/.github/workflows/workload.yml@v", files[".github/workflows/mlz.yml"])
        self.assertNotIn("x-release-please-version", files[".github/workflows/mlz.yml"])
        self.assertEqual(json.loads(files["mlz.auto.tfvars.json"])["mlz"]["landing_zone"], "payments-prod")
        # Again: nothing to do.
        self.sync(github)
        self.assertEqual(len(github.commits), 1)

    def test_nothing_a_team_wrote_is_replaced(self):
        self.config("payments-prod")
        own = {"README.md": "# Payments\n\nOur service's infrastructure.\nRun it with care.\n", "main.tf": "# ours\n"}
        github = FakeGitHub({"acme-org/payments-prod": dict(own)})
        self.sync(github)
        files = github.commits[0][3]
        self.assertNotIn("README.md", files)
        self.assertNotIn("main.tf", files)
        self.assertIn(".github/workflows/mlz.yml", files)

    def test_after_the_first_time_only_the_values_change(self):
        self.config("payments-prod")
        github = FakeGitHub({"acme-org/payments-prod": {}})
        self.sync(github)
        github.repos["acme-org/payments-prod"]["main.tf"] = "# the team's now\n"
        github.repos["acme-org/payments-prod"].pop(".github/workflows/mlz.yml")  # even one the team removed stays removed
        self.config("payments-prod", workload={**WORKLOAD, "location": "swedencentral"})
        self.sync(github)
        [_, (_, _, head, files, message)] = github.commits
        self.assertEqual(set(files), {"mlz.auto.tfvars.json"})
        self.assertEqual(json.loads(files["mlz.auto.tfvars.json"])["mlz"]["location"], "swedencentral")
        self.assertEqual((head, message), ("head-1", "chore: the landing zone's values, from vending"))

    def test_only_repos_vending_makes_once_they_exist(self):
        self.config("payments-prod")
        self.config("legacy-prod", github={"organization": "acme-org", "repo": "legacy-infra", "create": False, "team": None})
        self.config("old-prod", decommission=True)
        self.config("archived-prod")
        self.config("none-prod", github=None, workload=None)
        github = FakeGitHub({"acme-org/legacy-infra": {}, "acme-org/old-prod": {}, "acme-org/archived-prod": {}}, archived={"acme-org/archived-prod"})
        out = self.sync(github)
        self.assertEqual(github.commits, [])
        self.assertEqual(out, ["acme-org/payments-prod: not made yet; vending's apply makes it, and the next run gives it its files"])


if __name__ == "__main__":
    unittest.main()
