"""Tests for the platform config. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz.merge import ConfigError
from mlz.platform import build

GOVERNANCE = {
    "root": "demo",
    "primary_region": "norwayeast",
    "platform_subscriptions": {"management": "m", "connectivity": "c"},
    "monitoring": {"workspace": "/subscriptions/m/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law"},
}
HUB = "address_space: 10.100.0.0/22\nlanding_zone_ranges: 10.100.0.0/16\n"


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
        self.hub("norwayeast", "address_space: 10.100.0.0/24\nfirewall: Gold\nbastion: 'yes'\nfirewal: Standard\n")
        self.hub("swedencentral", "address_space: 10.100.0.0/22\nlanding_zone_ranges: [10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("'firewal' is not a hub setting", message)
        self.assertIn("10.100.0.0/24 is too small", message)

    def test_overlapping_hubs_and_missing_ranges_are_refused(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "address_space: 10.100.0.0/22\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("hubs/swedencentral: landing_zone_ranges is required", message)
        self.assertIn("address spaces 10.100.0.0/22 and 10.100.0.0/22 overlap", message)

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


if __name__ == "__main__":
    unittest.main()
