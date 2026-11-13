"""Tests for the pull requests that delete a decommissioned landing zone's files, with GitHub in memory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz import decommission
from mlz.files import read_yaml
from mlz.test_vending import DEFAULTS, DEV, PLATFORM, PROD, WORKLOAD
from mlz.vending import IP_PLAN, allocate, render_plan

REPOS = {"governance": "acme/mlz-governance", "platform": "acme/mlz-platform", "vending": "acme/mlz-vending"}


class FakeGitHub:
    """The platform repo's main as a set of paths, and every pull request opened or found open."""

    def __init__(self, platform_files=(), open_pulls=None):
        self.platform_files = set(platform_files)
        self.open_pulls = dict(open_pulls or {})  # (repo, branch) -> url
        self.commits, self.pulls = [], []

    def file(self, repo, path, ref):
        return ("rules", "sha") if repo == REPOS["platform"] and path in self.platform_files else (None, None)

    def folder(self, repo, path, ref):
        return sorted(p for p in self.platform_files if repo == REPOS["platform"] and p.startswith(path + "/"))

    def open_pull(self, repo, branch):
        return self.open_pulls.get((repo, branch))

    def reset_branch(self, repo, branch, base):
        return f"head-of-{base}"

    def commit_files(self, repo, branch, head, files, message):
        self.commits.append((repo, branch, files, message))

    def create_pull(self, repo, branch, base, title, body):
        url = f"https://github.com/{repo}/pull/{len(self.pulls) + 1}"
        self.pulls.append((repo, branch, title, body))
        self.open_pulls[(repo, branch)] = url
        return url


class DecommissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("landing-zones/_defaults/all.yaml", DEFAULTS)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD)
        self.write("landing-zones/payments/prod.yaml", PROD)
        self.write("landing-zones/payments/dev.yaml", DEV)
        plan, _, retired = allocate(self.repo, PLATFORM)
        self.write(IP_PLAN, render_plan(plan, retired))
        self.printed = []

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def cleanup(self, github):
        decommission.cleanup(github, self.repo, REPOS, "https://example/run/1", self.printed.append)

    def test_nothing_is_offered_while_no_landing_zone_is_decommissioned(self):
        github = FakeGitHub(platform_files={"firewall/landing-zones/payments-dev.yaml"})
        self.cleanup(github)
        self.assertEqual((github.commits, github.pulls), ([], []))

    def test_the_platform_s_rules_file_goes_first(self):
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        github = FakeGitHub(platform_files={"firewall/landing-zones/payments-dev.yaml", "firewall/landing-zones/payments-prod.yaml"})
        self.cleanup(github)
        self.assertEqual(github.commits, [(REPOS["platform"], "mlz/decommission-payments-dev",
                                           {"firewall/landing-zones/payments-dev.yaml": None},
                                           "chore: delete the firewall rules of decommissioned payments-dev")])
        self.assertEqual([pull[0] for pull in github.pulls], [REPOS["platform"]])  # vending's waits
        self.assertIn("its own pull request follows once that is merged", self.printed[0])

    def test_rules_kept_as_a_folder_of_chunks_go_whole(self):
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        github = FakeGitHub(platform_files={"firewall/landing-zones/payments-dev/web.yaml", "firewall/landing-zones/payments-dev/db.yaml"})
        self.cleanup(github)
        self.assertEqual(set(github.commits[0][2]), {"firewall/landing-zones/payments-dev/web.yaml", "firewall/landing-zones/payments-dev/db.yaml"})

    def test_then_the_landing_zone_s_file_goes_and_its_range_is_released(self):
        dev_range = read_yaml(self.repo / IP_PLAN)["payments-dev"]["range"]
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        github = FakeGitHub()
        self.cleanup(github)
        repo, branch, files, message = github.commits[0]
        self.assertEqual((repo, branch, message), (REPOS["vending"], "mlz/decommission-payments-dev", "chore: remove decommissioned payments-dev and release its range"))
        self.assertIsNone(files["landing-zones/payments/dev.yaml"])
        self.assertNotIn("landing-zones/payments/_workload.yaml", files)  # payments-prod still uses it
        self.write(IP_PLAN, files[IP_PLAN])
        self.assertEqual(sorted(read_yaml(self.repo / IP_PLAN)), ["payments-prod"])
        self.assertIn(f"releases its range, {dev_range},", github.pulls[0][3])

    def test_the_workload_s_last_landing_zone_takes_its_workload_file_too(self):
        (self.repo / "landing-zones/payments/prod.yaml").unlink()
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        github = FakeGitHub()
        self.cleanup(github)
        files = github.commits[0][2]
        self.assertIsNone(files["landing-zones/payments/_workload.yaml"])
        self.assertIsNone(files["landing-zones/payments/dev.yaml"])

    def test_an_open_pull_request_is_left_as_it_is(self):
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        github = FakeGitHub(open_pulls={(REPOS["vending"], "mlz/decommission-payments-dev"): "https://github.com/acme/mlz-vending/pull/7"})
        self.cleanup(github)
        self.assertEqual((github.commits, github.pulls), ([], []))
        self.assertEqual(self.printed, ["decommission: payments-dev's file and range go in https://github.com/acme/mlz-vending/pull/7"])


if __name__ == "__main__":
    unittest.main()
