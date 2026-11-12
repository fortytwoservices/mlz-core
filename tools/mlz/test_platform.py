"""Tests for the platform config. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mlz.__main__ import main
from mlz.contract import render
from mlz.merge import ConfigError
from mlz.platform import build

GOVERNANCE = {
    "root": "demo",
    "primary_region": "norwayeast",
    "platform_subscriptions": {"management": "m", "connectivity": "c"},
    "monitoring": {"workspace": "/subscriptions/m/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law"},
}
HUB = "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\n"


class PlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "hubs").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self, region: str, text: str) -> None:
        (self.repo / "hubs" / f"{region}.yaml").write_text(text)

    def test_a_minimal_hub_gets_the_defaults_and_the_fixed_subnet_layout(self):
        self.hub("norwayeast", HUB)
        hub = build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]
        self.assertTrue(hub["is_primary"])
        self.assertEqual(hub["firewall"], {
            "sku_tier": "Standard",
            "subnet_address_prefix": "10.100.0.0/26",
            "management_subnet_address_prefix": None,
        })
        self.assertEqual(hub["gateway_subnet_address_prefix"], "10.100.0.128/27")
        self.assertEqual(hub["dns_resolver"], {
            "inbound_subnet_address_prefix": "10.100.1.64/28",
            "outbound_subnet_address_prefix": "10.100.1.80/28",
        })
        self.assertIsNone(hub["bastion"])
        self.assertEqual(hub["landing_zone_ranges"], ["10.100.0.0/16"])
        # The hub is the platform range's first /22; the landing zone range already holds the platform's.
        self.assertEqual((hub["platform_address_space"], hub["address_space"]), ("10.100.0.0/21", ["10.100.0.0/22"]))
        self.assertEqual(hub["routing_supernet"], ["10.100.0.0/16"])

    def test_other_regions_route_a_platform_range_outside_the_landing_zone_ranges(self):
        self.hub("norwayeast", "platform_address_space: 10.99.0.0/21\nlanding_zone_ranges: [10.100.0.0/16, 10.101.0.0/16]\n")
        self.assertEqual(build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]["routing_supernet"], ["10.99.0.0/21", "10.100.0.0/16", "10.101.0.0/16"])

    def test_the_platform_range_is_a_21(self):
        for size in ("22", "20"):
            self.hub("norwayeast", f"platform_address_space: 10.100.0.0/{size}\nlanding_zone_ranges: 10.100.0.0/16\n")
            with self.assertRaises(ConfigError) as caught:
                build(self.repo, GOVERNANCE)
            self.assertIn(f"platform_address_space 10.100.0.0/{size} must be a /21: the hub's /22, management's /24, identity's /24 and a spare /23", str(caught.exception))

    def test_the_old_address_space_says_what_to_write(self):
        for value, expected in (
            ("10.100.0.0/22", "address_space is now platform_address_space, a /21 that holds the hub (its first /22), management and identity; platform_address_space: 10.100.0.0/21 keeps the hub at 10.100.0.0/22."),
            ("10.100.4.0/22", "the hub at 10.100.4.0/22 is not the first /22 of a /21, so choose a free /21 for the platform."),
        ):
            self.hub("norwayeast", f"address_space: {value}\nlanding_zone_ranges: 10.100.0.0/16\n")
            with self.assertRaises(ConfigError) as caught:
                build(self.repo, GOVERNANCE)
            message = str(caught.exception)
            self.assertIn(expected, message)
            self.assertNotIn("platform_address_space is required", message)  # one problem, saying what to write
            self.assertNotIn("is not a hub setting", message)

    def test_management_is_built_in_the_primary_region_and_identity_where_asked(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\nidentity: true\n")
        config = build(self.repo, {**GOVERNANCE, "platform_subscriptions": {**GOVERNANCE["platform_subscriptions"], "identity": "i"}})
        self.assertEqual(config["networks"]["management"], {"norwayeast": {
            "address_space": "10.100.4.0/24",
            "subnets": {"github-runners": "10.100.4.0/25", "runners": "10.100.4.128/26", "private-endpoints": "10.100.4.192/27"},
            "dns_servers": ["10.100.0.4"],  # the firewall's DNS proxy
            "firewall": "10.100.0.4",
        }})
        self.assertEqual(config["networks"]["identity"], {"swedencentral": {
            "address_space": "10.200.5.0/24",
            "subnets": {"domain-controllers": "10.200.5.0/26"},
            "dns_servers": ["10.200.0.4"],
            "firewall": "10.200.0.4",
        }})
        self.assertEqual((config["management_subscription_id"], config["identity_subscription_id"]), ("m", "i"))

    def test_a_network_is_never_built_over_a_landing_zone_given_before_v0_3(self):
        self.hub("norwayeast", HUB)
        # Under v0.2 the allocator stepped over the hub's /22 only, so the first landing zone got management's /24.
        vending = {"landing_zones": {"payments-prod": {"region": "norwayeast", "range": "10.100.4.0/24"},
                                     "website-prod": {"region": "norwayeast", "range": "10.100.5.0/24"}}}
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE, vending)
        message = str(caught.exception)
        self.assertIn("hubs/norwayeast: management's network 10.100.4.0/24 would overlap payments-prod's range 10.100.4.0/24 (_generated/vending.yaml)", message)
        self.assertNotIn("website-prod", message)  # identity's /24, not built here: vending reports it
        self.assertEqual(build(self.repo, GOVERNANCE, {"landing_zones": {"website-prod": vending["landing_zones"]["website-prod"]}})["networks"]["identity"], {})
        # A landing zone removed but not yet released still holds its range, and its network may still exist.
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE, {"landing_zones": {}, "held": {"payments-prod": "10.100.4.0/24"}})
        self.assertIn("management's network 10.100.4.0/24 would overlap payments-prod's range 10.100.4.0/24, removed but not yet released", str(caught.exception))

    def test_the_command_reads_the_vending_contract_in_the_repo(self):
        self.hub("norwayeast", HUB)
        (self.repo / "_generated").mkdir()
        (self.repo / "_generated/governance.yaml").write_text(render("governance", GOVERNANCE))
        (self.repo / "_generated/vending.yaml").write_text(render("vending", {"landing_zones": {"payments-prod": {"region": "norwayeast", "range": "10.100.4.0/24"}}}))
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["mlz", "platform", "--repo", str(self.repo), "--out", str(self.repo / "platform.json")]), redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(main(), 1)
        self.assertIn("management's network 10.100.4.0/24 would overlap payments-prod's range", err.getvalue())

    def test_behind_a_basic_firewall_the_networks_resolve_through_the_resolver(self):
        self.hub("norwayeast", HUB + "firewall: Basic\n")
        self.assertEqual(build(self.repo, GOVERNANCE)["networks"]["management"]["norwayeast"]["dns_servers"], ["10.100.1.68"])
        self.hub("norwayeast", HUB + "firewall: Basic\ndns_resolver: false\n")
        self.assertEqual(build(self.repo, GOVERNANCE)["networks"]["management"]["norwayeast"]["dns_servers"], [])  # Azure's

    def test_identity_needs_an_identity_subscription_and_a_yes_or_no(self):
        self.hub("norwayeast", HUB + "identity: true\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("hubs/norwayeast: identity: true builds identity's network in the identity subscription, and customer.yaml names none", str(caught.exception))
        self.hub("norwayeast", HUB + "identity: 'yes'\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("identity must be true or false", str(caught.exception))
        self.hub("norwayeast", HUB)
        self.assertEqual(build(self.repo, GOVERNANCE)["networks"]["identity"], {})

    def test_basic_gets_the_management_subnet_and_bastion_its_own(self):
        self.hub("norwayeast", HUB + "firewall: Basic\nbastion: true\ndns_resolver: false\n")
        hub = build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]
        self.assertEqual(hub["firewall"]["management_subnet_address_prefix"], "10.100.0.64/26")
        self.assertEqual(hub["bastion"], {"subnet_address_prefix": "10.100.1.0/26"})
        self.assertIsNone(hub["dns_resolver"])

    def test_a_hub_split_into_chunks_is_the_same_hub(self):
        self.hub("norwayeast", HUB + "bastion: true\n")
        whole = build(self.repo, GOVERNANCE)
        (self.repo / "hubs/norwayeast.yaml").unlink()
        (self.repo / "hubs/norwayeast").mkdir()
        (self.repo / "hubs/norwayeast/network.yaml").write_text(HUB)
        (self.repo / "hubs/norwayeast/bastion.yaml").write_text("bastion: true\n")
        self.assertEqual(build(self.repo, GOVERNANCE), whole)

    def test_every_problem_is_reported_at_once(self):
        self.hub("norwayeast", "platform_address_space: 10.100.0.0/24\nfirewall: Gold\nbastion: 'yes'\nfirewal: Standard\n")
        self.hub("swedencentral", "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: [10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("'firewal' is not a hub setting", message)
        self.assertIn("10.100.0.0/24 must be a /21", message)

    def test_overlapping_hubs_and_missing_ranges_are_refused(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "platform_address_space: 10.100.0.0/21\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("hubs/swedencentral: landing_zone_ranges is required", message)
        self.assertIn("platform address spaces 10.100.0.0/21 and 10.100.0.0/21 overlap", message)

    def test_the_primary_region_needs_a_hub(self):
        self.hub("swedencentral", HUB)
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("no hub in the primary region norwayeast", str(caught.exception))

    def test_bad_values_are_named(self):
        self.hub("norwayeast", HUB + "firewall: Gold\nbastion: 'yes'\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("firewall 'Gold' must be one of Standard, Premium, Basic", message)
        self.assertIn("bastion must be true or false", message)

    def test_reserved_ranges_are_published_and_kept_clear_of_hubs(self):
        self.hub("norwayeast", HUB + "reserved: [10.100.200.0/22, 192.168.0.0/16]\n")
        self.assertEqual(build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]["reserved"], ["10.100.200.0/22", "192.168.0.0/16"])
        self.hub("norwayeast", HUB + "reserved: [10.100.4.0/24]\n")  # management's /24
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("hubs/norwayeast: platform address space 10.100.0.0/21 overlaps 10.100.4.0/24, which hubs/norwayeast reserves", str(caught.exception))

    def test_landing_zone_ranges_belong_to_one_region(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: [10.100.0.0/24, 10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("hubs/norwayeast and hubs/swedencentral: landing zone ranges 10.100.0.0/16 and 10.100.0.0/24 overlap", str(caught.exception))

    def test_a_platform_range_lies_only_in_its_own_regions_landing_zone_ranges(self):
        self.hub("norwayeast", HUB)  # its /21 inside its own 10.100.0.0/16: fine
        self.hub("swedencentral", "platform_address_space: 10.100.8.0/21\nlanding_zone_ranges: [10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("hubs/swedencentral: platform address space 10.100.8.0/21 overlaps 10.100.0.0/16, a landing zone range of hubs/norwayeast", message)
        self.assertNotIn("hubs/norwayeast: platform address space", message)

    def test_every_hub_problem_is_reported_even_without_an_address_space(self):
        self.hub("norwayeast", "platform_address_space: []\nfirewall: Gold\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("platform_address_space is required", message)
        self.assertIn("firewall 'Gold' must be one of", message)
        self.assertIn("landing_zone_ranges is required", message)

    def test_ranges_azure_reserves_are_refused(self):
        for text, expected in (
            ("platform_address_space: 127.0.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\n", "127.0.0.0/21 overlaps 127.0.0.0/8, which Azure does not allow"),
            ("platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: [10.100.0.0/16, 169.254.0.0/16]\n", "169.254.0.0/16 overlaps 169.254.0.0/16"),
        ):
            self.hub("norwayeast", text)
            with self.assertRaises(ConfigError) as caught:
                build(self.repo, GOVERNANCE)
            self.assertIn(expected, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
