"""Tests for advanced: (tools/mlz/advanced.py): what a hub file's block may set, what it refuses and why, and what it
writes into the platform's config."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mlz import advanced, explain, inputs, platform, schema
from mlz.merge import ConfigError
from mlz.test_platform import GOVERNANCE

HUB = "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\nbastion: true\n"
# What the Terraform test (platform/terraform/tests/advanced.tftest.hcl) gives the platform root.
FIXTURE = {
    "norwayeast": HUB,
    "swedencentral": "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\nbastion: true\n"
                     "advanced:\n  virtual_network_dns_servers: [10.200.1.68]\n  bastion:\n    sku: Premium\n    tunneling_enabled: true\n"
                     "  firewall:\n    zones: ['1', '2', '3']\n    firewall_policy:\n      threat_intelligence_mode: Deny\n",
}


class AdvancedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "connectivity").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self, text: str, region: str = "norwayeast") -> None:
        (self.repo / "connectivity" / f"{region}.yaml").write_text(text)

    def problems(self, block: str) -> list[str]:
        self.hub(HUB + "advanced:\n" + block)
        with self.assertRaises(ConfigError) as caught:
            platform.build(self.repo, GOVERNANCE)
        return [line.strip() for line in str(caught.exception).splitlines()[1:]]

    def test_r2s_first_example_reaches_the_hub_and_only_it(self):
        self.hub(HUB + "advanced:\n  bastion:\n    sku: Premium\n    tunneling_enabled: true\n")
        hub = platform.build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]
        self.assertEqual(hub["advanced"], {"bastion": {"sku": "Premium", "tunneling_enabled": True}})
        self.assertEqual(hub["bastion"], {"subnet_address_prefix": "10.100.1.0/26", "name": "bas-demo-hub-pd-nwe-01",
                                          "public_ip_name": "pip-demo-hub-bas-pd-nwe-01"})  # mlz-core's own, untouched

    def test_without_advanced_the_config_has_no_trace_of_it(self):
        self.hub(HUB)
        self.assertNotIn("advanced", platform.build(self.repo, GOVERNANCE)["hubs"]["norwayeast"])
        self.hub(HUB + "advanced:\n")  # empty, as a commented-out example leaves it
        self.assertNotIn("advanced", platform.build(self.repo, GOVERNANCE)["hubs"]["norwayeast"])

    def test_r2s_refusals_name_who_owns_the_input(self):
        self.assertEqual(self.problems("  firewall:\n    subnet_address_prefix: 10.100.0.0/26\n"), [
            "connectivity/norwayeast: advanced.firewall.subnet_address_prefix is worked out by mlz-core (the first /26 of the hub's /22); it cannot be set."])
        self.assertEqual(self.problems("  firewall:\n    name: afw-mine\n"), [
            "connectivity/norwayeast: advanced.firewall.name is worked out by mlz-core (the naming template); it cannot be set. "
            "To name it yourself, set names.firewall in this file."])
        self.assertEqual(self.problems("  firewall:\n    sku_tier: Premium\n"), [
            "connectivity/norwayeast: advanced.firewall.sku_tier is the setting firewall in connectivity/<region>.yaml; set it there."])
        self.assertIn("connectivity/norwayeast: advanced.firewall.firewall_policy.dns_proxy_enabled is fixed by Fortytwo",
                      self.problems("  firewall:\n    firewall_policy:\n      dns_proxy_enabled: false\n")[0])
        self.assertEqual(self.problems("  vpn_gateway:\n    sku: VpnGw2AZ\n"), [
            "connectivity/norwayeast: advanced.vpn_gateway is not supported yet (no VPN gateway yet: every peering is made without remote gateways, "
            "and mlz-core has no name for one)."])

    def test_a_mistyped_input_or_one_of_the_whole_call_is_refused_not_dropped(self):
        found = self.problems("  bastion:\n    tunnelling_enabled: true\n  retry:\n    interval_seconds: 5\n  bastion_sku: Premium\n")
        self.assertEqual(sorted(found), [
            "connectivity/norwayeast: advanced.bastion.tunnelling_enabled is not an input of az-tf-mlz-connectivity-hub v0.1.0 (docs/reference/platform.md lists them).",
            "connectivity/norwayeast: advanced.bastion_sku is not an input of az-tf-mlz-connectivity-hub v0.1.0 (docs/reference/platform.md lists them).",
            "connectivity/norwayeast: advanced.retry is an input of the whole module call, not of what this file builds; advanced: here does not reach it."])

    def test_values_are_checked_against_the_module_s_types(self):
        found = self.problems("  bastion:\n    scale_units: '4'\n    tunneling_enabled: 'yes'\n    zones: '1'\n  firewall:\n    zones: [1]\n")
        self.assertEqual(found, [
            "connectivity/norwayeast: advanced.bastion.scale_units must be a number, not '4'.",
            "connectivity/norwayeast: advanced.bastion.tunneling_enabled must be a bool, not 'yes'.",
            "connectivity/norwayeast: advanced.bastion.zones must be a list (list(string)), not '1'.",
            "connectivity/norwayeast: advanced.firewall.zones[0] must be a string, not 1."])
        self.assertEqual(self.problems("  bastion: Premium\n"), [
            "connectivity/norwayeast: advanced.bastion is a group of inputs: give its attributes (docs/reference/platform.md lists them)."])
        self.assertEqual(self.problems("  - bastion\n"), [
            "connectivity/norwayeast: advanced maps module inputs to their values (docs/reference/platform.md lists them)."])

    def test_a_component_the_hub_does_not_have_takes_no_inputs(self):
        self.hub(HUB.replace("bastion: true", "dns_resolver: false") + "advanced:\n  bastion:\n    sku: Premium\n  dns_resolver:\n    inbound_subnet_name: snet-in\n")
        with self.assertRaises(ConfigError) as caught:
            platform.build(self.repo, GOVERNANCE)
        self.assertIn("advanced.bastion sets inputs of a bastion this hub does not have; set bastion: true first.", str(caught.exception))
        self.assertIn("advanced.dns_resolver.inbound_subnet_name is fixed by Fortytwo", str(caught.exception))

    def test_the_terraform_test_s_config_is_what_the_loader_writes(self):
        for region, text in FIXTURE.items():
            self.hub(text, region)
        written = json.loads((inputs.CORE / "platform/terraform/tests/advanced.json").read_text())
        self.assertEqual(written, platform.build(self.repo, GOVERNANCE),
                         "platform/terraform/tests/advanced.json is out of date: write it again from test_advanced.FIXTURE")

    def test_the_schema_offers_the_open_inputs_and_nothing_else(self):
        block = schema.connectivity()["properties"]["advanced"]
        self.assertEqual(block["properties"]["bastion"]["properties"]["sku"], {"type": "string"})
        self.assertEqual(block["properties"]["bastion"]["properties"]["zones"], {"type": "array", "items": {"type": "string"}})
        self.assertNotIn("name", block["properties"]["bastion"]["properties"])
        self.assertEqual(set(block["properties"]["firewall"]["properties"]), {"zones", "firewall_policy"})
        self.assertEqual(set(block["properties"]), {"virtual_network_dns_servers", "firewall", "bastion"})
        self.assertFalse(block["additionalProperties"])

    def test_explain_shows_what_advanced_sets(self):
        for region, text in FIXTURE.items():
            self.hub(text, region)
        config = platform.build(self.repo, GOVERNANCE)
        rows = {row[0]: row for part in explain.module_inputs("platform/terraform", config, {"region": "swedencentral"}).values() for row in part}
        self.assertEqual(rows["hub_virtual_networks.bastion.sku"][1:], ("Premium", "open: set under advanced.bastion.sku in connectivity/<region>.yaml"))
        self.assertEqual(rows["hub_virtual_networks.bastion.scale_units"][1], "2")  # not set: the module's default
        self.assertEqual(advanced.given("platform/terraform", "connectivity", config, {"region": "norwayeast"}), {})


if __name__ == "__main__":
    unittest.main()
