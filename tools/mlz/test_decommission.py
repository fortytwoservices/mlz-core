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
HUB = "/subscriptions/c/resourceGroups/rg-demo-hub-nwe/providers/Microsoft.Network/virtualNetworks/vnet-demo-hub-nwe"
POLICY = "/subscriptions/c/resourceGroups/rg-demo-hub-nwe/providers/Microsoft.Network/firewallPolicies/afwp-demo-hub-nwe"


class FakeAzure:
    """The hub's firewall policy's rule collection groups, as Azure lists them."""

    def __init__(self, groups=None):
        self.groups, self.read = groups or [], []

    def resource(self, resource_id, api_version):
        self.read.append((resource_id, api_version))
        return {"value": self.groups} if resource_id == f"{POLICY}/ruleCollectionGroups" else None


def group(name, *collections, state="Succeeded"):
    return {"name": name, "properties": {"provisioningState": state, "ruleCollections": list(collections)}}


def collection(name, *rules):
    return {"name": name, "rules": list(rules)}


class FakeGitHub:
    """The platform repo's main as a set of paths, and every pull request opened or found open."""

    def __init__(self, platform_files=(), open_pulls=None):
        self.platform_files = set(platform_files)
        self.open_pulls = dict(open_pulls or {})  # (repo, branch) -> url
        self.commits, self.pulls, self.bases = [], [], []

    def file(self, repo, path, ref):
        return ("rules", "sha") if repo == REPOS["platform"] and path in self.platform_files else (None, None)

    def folder(self, repo, path, ref):
        return sorted(p for p in self.platform_files if repo == REPOS["platform"] and p.startswith(path + "/"))

    def open_pull(self, repo, branch):
        return self.open_pulls.get((repo, branch))

    def reset_branch(self, repo, branch, base, at=None):
        self.bases.append((repo, at or f"head-of-{base}"))
        return at or f"head-of-{base}"

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
        self.write("_generated/platform.yaml", f"regions:\n  norwayeast:\n    hub_network: {HUB}\n")
        self.azure = FakeAzure()
        self.printed = []

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def cleanup(self, github):
        decommission.cleanup(github, self.azure, self.repo, REPOS, "https://example/run/1", self.printed.append, sha="run-sha")

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
        # From the commit its files were worked out from, not whatever main is now.
        self.assertEqual(github.bases, [(REPOS["vending"], "run-sha")])

    def wait_reason(self, groups):
        """What a decommissioned payments-dev's release waits for, with these groups in the hub's policy."""
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        self.azure.groups, self.printed = groups, []
        github = FakeGitHub()
        self.cleanup(github)
        return None if github.pulls else self.printed[0]

    def test_the_range_waits_while_its_rules_may_still_be_in_azure(self):
        dev = read_yaml(self.repo / IP_PLAN)["payments-dev"]["range"]
        other = collection("payments-prod-network", {"name": "api", "sourceAddresses": ["10.100.9.0/24"], "destinationAddresses": ["203.0.113.10"]})
        # Its own collections, still there.
        self.assertIn("lz-07/payments-dev-network", self.wait_reason([group("lz-07", collection("payments-dev-network"))]))
        # A rule elsewhere that still names it by key: its range as a destination.
        named = collection("platform-network", {"name": "to-dev", "sourceAddresses": ["10.100.0.0/16"], "destinationAddresses": [dev]})
        self.assertIn(f"platform/platform-network/to-dev, from or to {dev}", self.wait_reason([group("platform", named)]))
        # A group whose change has not finished, or failed.
        self.assertIn("rule collection group lz-07 is Updating", self.wait_reason([group("lz-07", other, state="Updating")]))
        self.assertIn("is Failed", self.wait_reason([group("lz-07", other, state="Failed")]))
        # Gone: other landing zones' rules may stay, and platform rules from the whole landing zone range.
        broad = collection("platform-network", {"name": "updates", "sourceAddresses": ["10.100.0.0/16"], "destinationAddresses": ["AzureCloud"]})
        self.assertIsNone(self.wait_reason([group("lz-07", other), group("platform", broad)]))
        self.assertIsNone(self.wait_reason([]))  # no group left at all
        self.assertEqual(self.azure.read[-1], (f"{POLICY}/ruleCollectionGroups", "2025-07-01"))

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
