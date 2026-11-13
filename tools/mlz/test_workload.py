"""Tests for the workload repos' files (tools/mlz/workload.py). Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mlz import workload

WORKLOAD = {"landing_zone": "payments-prod", "location": "norwayeast",
            "org": "acme", "app": "pay", "env": "pd", "region": "nwe", "template": "{type}-{org}-{app}-{purpose}-{env}-{region}-{nn}", "templates": {}}


DESCRIPTION = "Workload repo of landing zone payments-prod, made by mlz-core's vending."


class FakeGitHub:
    """Repos and their main branch's files, in memory. A commit made on a head main has left is refused, as GitHub's
    createCommitOnBranch refuses one whose expectedHeadOid is not the branch's. push_on: a file whose reading makes the
    team push in between, with that file in it."""

    def __init__(self, repos: dict[str, dict[str, str]], archived=(), push_on=None):
        self.repos, self.archived, self.commits, self.push_on = repos, set(archived), [], push_on
        self.heads = {repo: 0 for repo in repos}

    def repo(self, repo):
        return {"archived": repo in self.archived} if repo in self.repos else None

    def file(self, repo, path, ref):
        text = self.repos[repo].get(path)
        if path == self.push_on:
            self.repos[repo][path], self.heads[repo], self.push_on = "# the team's, pushed meanwhile\n", self.heads[repo] + 1, None
        return (text, "sha") if text is not None else (None, None)

    def head(self, repo, branch):
        return f"head-{self.heads[repo]}"

    def commit_files(self, repo, branch, head, files, message):
        if head != f"head-{self.heads[repo]}":
            raise RuntimeError("expectedHeadOid is not the branch's head")
        self.commits.append((repo, branch, head, dict(files), message))
        self.repos[repo].update(files)
        self.heads[repo] += 1


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.configs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, key: str, **extra) -> None:
        config = {"key": key, "github": {"organization": "acme-org", "repo": key, "create": True, "team": "payments-devs", "description": DESCRIPTION},
                  "workload": {**WORKLOAD, "landing_zone": key}, "decommission": False, **extra}
        (self.configs / f"{key}.json").write_text(json.dumps(config))

    def sync(self, github) -> list[str]:
        out = []
        workload.sync(github, self.configs, out.append)
        return out

    def test_a_new_repo_gets_the_starter_and_its_values_in_one_commit(self):
        self.config("payments-prod")
        github = FakeGitHub({"acme-org/payments-prod": {"README.md": f"# payments-prod\n{DESCRIPTION}\n"}})
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
        # Even a README as short as GitHub's own: only GitHub's exact one is replaced.
        own = {"README.md": "# payments-prod\nContact Payments before deploying.\n", "main.tf": "# ours\n"}
        github = FakeGitHub({"acme-org/payments-prod": dict(own)})
        self.sync(github)
        files = github.commits[0][3]
        self.assertNotIn("README.md", files)
        self.assertNotIn("main.tf", files)
        self.assertIn(".github/workflows/mlz.yml", files)

    def test_a_team_s_push_while_it_reads_makes_the_commit_fail_not_overwrite(self):
        self.config("payments-prod")
        # main.tf is not there when read, and the team pushes it right after: the commit was to be made on the head
        # read first, which main has left, so GitHub refuses it.
        github = FakeGitHub({"acme-org/payments-prod": {}}, push_on="main.tf")
        with self.assertRaisesRegex(RuntimeError, "expectedHeadOid"):
            self.sync(github)
        self.assertEqual(github.repos["acme-org/payments-prod"]["main.tf"], "# the team's, pushed meanwhile\n")

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
        self.config("legacy-prod", github={"organization": "acme-org", "repo": "legacy-infra", "create": False, "team": None, "description": DESCRIPTION})
        self.config("old-prod", decommission=True)
        self.config("archived-prod")
        self.config("none-prod", github=None, workload=None)
        github = FakeGitHub({"acme-org/legacy-infra": {}, "acme-org/old-prod": {}, "acme-org/archived-prod": {}}, archived={"acme-org/archived-prod"})
        out = self.sync(github)
        self.assertEqual(github.commits, [])
        self.assertEqual(out, ["acme-org/payments-prod: not made yet; vending's apply makes it, and the next run gives it its files"])


class CommandTest(unittest.TestCase):
    def test_only_the_run_of_vending_s_current_main_writes(self):
        import os
        import sys
        from unittest import mock
        from mlz import __main__ as cli

        class Head:
            def __init__(self, head):
                self._head = head

            def head(self, repo, branch):
                return self._head

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "argv", ["mlz", "workload-repos", "--configs", tmp]), \
                mock.patch.dict(os.environ, {"GITHUB_SHA": "older", "GITHUB_REPOSITORY": "acme-org/mlz-vending"}), \
                mock.patch.object(workload, "sync") as sync:
            out = io.StringIO()
            with mock.patch.object(cli, "GitHub", lambda: Head("newer")), redirect_stdout(out):
                self.assertEqual(cli.main(), 0)
            sync.assert_not_called()
            self.assertIn("main has moved on from this run's older", out.getvalue())
            with mock.patch.object(cli, "GitHub", lambda: Head("older")):
                self.assertEqual(cli.main(), 0)
            sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
