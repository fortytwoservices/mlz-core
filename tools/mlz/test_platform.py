"""Tests for the platform config. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mlz import platform
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
        (self.repo / "connectivity").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self, region: str, text: str) -> None:
        (self.repo / "connectivity" / f"{region}.yaml").write_text(text)

    def network(self, name: str, region: str, text: str = "") -> None:
        (self.repo / name).mkdir(exist_ok=True)
        (self.repo / name / f"{region}.yaml").write_text(text)

    def problems(self, governance: dict = GOVERNANCE) -> str:
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, governance)
        return str(caught.exception)

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
            "name": "dnspr-demo-hub-pd-nwe-01", "inbound_endpoint_name": "in-demo-hub-pd-nwe-01", "outbound_endpoint_name": "out-demo-hub-pd-nwe-01",
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

    def test_management_is_built_in_the_primary_region_and_identity_where_asked(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\n")
        self.network("identity", "swedencentral", "# domain controllers here\n")  # comments only, as an empty file
        self.network("management", "norwayeast")  # optional: the same network as without it
        config = build(self.repo, {**GOVERNANCE, "platform_subscriptions": {**GOVERNANCE["platform_subscriptions"], "identity": "i"}})
        for regions in config["networks"].values():  # the names are tested on their own
            for net in regions.values():
                net.pop("names")
        self.assertEqual(config["networks"]["management"], {"norwayeast": {
            "address_space": "10.100.4.0/24",
            "subnets": {"github-runners": "10.100.4.0/25", "runners": "10.100.4.128/26", "private-endpoints": "10.100.4.192/27"},
            "dns_servers": ["10.100.0.4"],  # the firewall's DNS proxy
            "firewall": "10.100.0.4",
            "github_runners": True,  # GitHub's hosted runners can join a subnet in Norway East
        }})
        self.assertEqual(config["networks"]["identity"], {"swedencentral": {
            "address_space": "10.200.5.0/24",
            "subnets": {"domain-controllers": "10.200.5.0/26"},
            "dns_servers": ["10.200.0.4"],
            "firewall": "10.200.0.4",
        }})
        self.assertEqual((config["management_subscription_id"], config["identity_subscription_id"]), ("m", "i"))

    def test_every_name_comes_from_the_template_or_the_hub_file(self):
        self.hub("norwayeast", HUB + "names:\n  firewall: afw-legacy\n")
        self.hub("swedencentral", "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\n")
        self.network("identity", "swedencentral")
        governance = {**GOVERNANCE, "naming": {"org": "acme"}, "platform_subscriptions": {**GOVERNANCE["platform_subscriptions"], "identity": "i"}}
        config = build(self.repo, governance)
        hub = config["hubs"]["norwayeast"]["names"]
        self.assertEqual((hub["resource_group"], hub["virtual_network"], hub["firewall"], hub["firewall_policy"], hub["firewall_public_ip"]),
                         ("rg-acme-hub-pd-nwe-01", "vnet-acme-hub-pd-nwe-01", "afw-legacy", "afwp-acme-hub-pd-nwe-01", "pip-acme-hub-afw-pd-nwe-01"))
        self.assertEqual(config["hubs"]["swedencentral"]["names"]["dns_resolver_inbound_endpoint"], "in-acme-hub-pd-sdc-01")
        # The resolver's object as the hub module takes it, with its names in.
        self.assertEqual(config["hubs"]["swedencentral"]["dns_resolver"]["outbound_endpoint_name"], "out-acme-hub-pd-sdc-01")
        self.assertIsNone(config["hubs"]["norwayeast"]["bastion"])
        management = config["networks"]["management"]["norwayeast"]["names"]
        self.assertEqual((management["virtual_network"], management["github_network_settings"], management["network_security_groups"]["private-endpoints"]),
                         ("vnet-acme-mgt-pd-nwe-01", "ghns-acme-mgt-pd-nwe-01", "nsg-acme-mgt-private-endpoints-pd-nwe-01"))
        self.assertEqual(config["networks"]["identity"]["swedencentral"]["names"]["virtual_network"], "vnet-acme-idn-pd-sdc-01")
        self.assertEqual(config["names"], {
            "dns_resource_group": "rg-acme-dns-pd-nwe-01",
            # One group for each network, in the primary region, which the bootstrap makes.
            "network_resource_groups": {"management": "rg-acme-mgt-net-pd-nwe-01", "identity": "rg-acme-idn-net-pd-nwe-01"},
            "diagnostic_setting": "to-acme-workspace",
        })

    def test_a_hub_file_s_names_are_checked(self):
        self.hub("norwayeast", HUB + "names:\n  firewall: 'afw legacy'\n  gateway: vgw-1\n")
        message = self.problems()
        self.assertIn("connectivity/norwayeast: names.firewall: azure_firewall name afw legacy has characters Azure does not allow", message)
        self.assertIn("connectivity/norwayeast: names.gateway is not a hub component; use resource_group, virtual_network, firewall,", message)
        self.hub("norwayeast", HUB + "names: [afw-x]\n")
        self.assertIn("connectivity/norwayeast: names maps a hub component", self.problems())

    def test_a_name_azure_would_not_take_is_refused(self):
        self.hub("norwayeast", HUB)
        long = {**GOVERNANCE, "naming": {"templates": {"azure_firewall": "{type}-{org}-{app}-" + "x" * 70}}}
        self.assertIn("names: azure_firewall name afw-demo-hub-xxx", self.problems(long))
        self.assertIn("more than Azure allows", self.problems(long))

    def test_region_codes_are_unique_and_never_change(self):
        # A code is in resource names: the ones customers have used stay as they are.
        for region, code in {"norwayeast": "nwe", "norwaywest": "nww", "westeurope": "we", "northeurope": "ne", "swedencentral": "sdc"}.items():
            self.assertEqual(platform.REGION_CODES[region], code)
        self.assertEqual(len(set(platform.REGION_CODES.values())), len(platform.REGION_CODES))
        self.assertLessEqual(set(platform.GITHUB_RUNNER_REGIONS), set(platform.REGIONS))

    def test_a_hub_in_any_azure_region(self):
        self.hub("germanywestcentral", HUB)
        config = build(self.repo, {**GOVERNANCE, "primary_region": "germanywestcentral"})
        self.assertFalse(config["networks"]["management"]["germanywestcentral"]["github_runners"])
        self.hub("francecentral", HUB.replace("10.100.", "10.150."))
        self.assertIn("francecentral", build(self.repo, {**GOVERNANCE, "primary_region": "germanywestcentral"})["hubs"])

    def test_github_s_runners_join_management_s_network_only_where_github_supports_the_region(self):
        self.hub("westeurope", HUB)
        config = build(self.repo, {**GOVERNANCE, "primary_region": "westeurope"})
        self.assertFalse(config["networks"]["management"]["westeurope"]["github_runners"])

    def test_a_network_is_never_built_over_a_landing_zone_given_before_v0_3(self):
        self.hub("norwayeast", HUB)
        # Under v0.2 the allocator stepped over the hub's /22 only, so the first landing zone got management's /24.
        vending = {"landing_zones": {"payments-prod": {"region": "norwayeast", "range": "10.100.4.0/24"},
                                     "website-prod": {"region": "norwayeast", "range": "10.100.5.0/24"}}}
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE, vending)
        message = str(caught.exception)
        self.assertIn("connectivity/norwayeast: management's network 10.100.4.0/24 would overlap payments-prod's range 10.100.4.0/24 (_generated/vending.yaml)", message)
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

    def run_command(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["mlz", "platform", "--repo", str(self.repo), "--out", str(self.repo / "platform.json")]), redirect_stdout(out), redirect_stderr(err):
            code = main()
        return code, out.getvalue(), err.getvalue()

    def test_the_starter_as_it_comes_waits_with_or_without_governance_s_contract(self):
        # On a first deployment governance's contract pull request is checked against the starter's hub file, every
        # setting commented out: it must pass, or it never merges itself, and no hub pull request can pass without it.
        starter = Path(__file__).resolve().parents[2] / "starter" / "mlz-platform" / "connectivity" / "norwayeast.yaml"
        self.hub("norwayeast", starter.read_text())
        (self.repo / "platform.json").write_text("{}")  # an older config goes, so nothing plans with it
        for contract in (False, True):
            if contract:
                (self.repo / "_generated").mkdir()
                (self.repo / "_generated/governance.yaml").write_text(render("governance", GOVERNANCE))
            code, out, _ = self.run_command()
            self.assertEqual(code, 0)
            self.assertIn("has a setting yet; nothing written until one does", out)
            self.assertFalse((self.repo / "platform.json").exists())
        self.assertTrue(platform.unset(self.repo))

    def test_once_a_hub_has_a_setting_it_is_checked_and_every_hub_must_be_complete(self):
        self.hub("norwayeast", "firewall: Basic\n")
        self.assertFalse(platform.unset(self.repo))
        with self.assertRaisesRegex(ConfigError, "platform_address_space is required"):
            build(self.repo, GOVERNANCE)
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "# not yet\n")
        self.assertFalse(platform.unset(self.repo))
        with self.assertRaisesRegex(ConfigError, "connectivity/swedencentral: landing_zone_ranges is required"):
            build(self.repo, GOVERNANCE)

    def test_a_hub_without_governance_s_contract_says_it_has_not_arrived(self):
        self.hub("norwayeast", HUB)
        code, _, err = self.run_command()
        self.assertEqual(code, 1)
        self.assertIn("_generated/governance.yaml is not there: governance's contract has not arrived yet. Governance offers it", err)
        self.assertNotIn("Errno", err)

    def test_behind_a_basic_firewall_the_networks_resolve_through_the_resolver(self):
        self.hub("norwayeast", HUB + "firewall: Basic\n")
        self.assertEqual(build(self.repo, GOVERNANCE)["networks"]["management"]["norwayeast"]["dns_servers"], ["10.100.1.68"])
        self.hub("norwayeast", HUB + "firewall: Basic\ndns_resolver: false\n")
        self.assertEqual(build(self.repo, GOVERNANCE)["networks"]["management"]["norwayeast"]["dns_servers"], [])  # Azure's

    def test_identity_needs_an_identity_subscription(self):
        self.hub("norwayeast", HUB)
        self.assertEqual(build(self.repo, GOVERNANCE)["networks"]["identity"], {})
        self.network("identity", "norwayeast")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("identity/norwayeast: builds identity's network in the identity subscription, and customer.yaml names none", str(caught.exception))

    def test_a_network_file_takes_no_settings_and_needs_its_hub(self):
        self.hub("norwayeast", HUB)
        governance = {**GOVERNANCE, "platform_subscriptions": {**GOVERNANCE["platform_subscriptions"], "identity": "i"}}
        self.network("identity", "norwayeast", "domain_controllers: 2\n")
        self.network("identity", "swedencentral")
        self.network("management", "francecentral")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, governance)
        message = str(caught.exception)
        self.assertIn("identity/norwayeast: takes no settings yet; its being there builds identity's network in norwayeast", message)
        self.assertIn("identity/swedencentral: no hub in swedencentral (connectivity/swedencentral.yaml)", message)
        self.assertIn("management/francecentral: no hub in francecentral", message)
        self.hub("francecentral", "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, governance)
        self.assertIn("management/francecentral: management's network is built in the primary region, norwayeast, only", str(caught.exception))
        self.assertNotIn("identity", "".join(platform.HUB_KEYS))  # identity is a file of its own now

    def test_basic_gets_the_management_subnet_and_bastion_its_own(self):
        self.hub("norwayeast", HUB + "firewall: Basic\nbastion: true\ndns_resolver: false\n")
        hub = build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]
        self.assertEqual(hub["firewall"]["management_subnet_address_prefix"], "10.100.0.64/26")
        self.assertEqual(hub["bastion"], {"subnet_address_prefix": "10.100.1.0/26", "name": "bas-demo-hub-pd-nwe-01", "public_ip_name": "pip-demo-hub-bas-pd-nwe-01"})
        self.assertIsNone(hub["dns_resolver"])

    def test_a_hub_split_into_chunks_is_the_same_hub(self):
        self.hub("norwayeast", HUB + "bastion: true\n")
        whole = build(self.repo, GOVERNANCE)
        (self.repo / "connectivity/norwayeast.yaml").unlink()
        (self.repo / "connectivity/norwayeast").mkdir()
        (self.repo / "connectivity/norwayeast/network.yaml").write_text(HUB)
        (self.repo / "connectivity/norwayeast/bastion.yaml").write_text("bastion: true\n")
        self.assertEqual(build(self.repo, GOVERNANCE), whole)

    def test_every_problem_is_reported_at_once(self):
        self.hub("norwayeast", "platform_address_space: 10.100.0.0/24\nfirewall: Gold\nbastion: 'yes'\nfirewal: Standard\n")
        self.hub("swedencentral", "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: [10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("'firewal' is not a connectivity setting", message)
        self.assertIn("10.100.0.0/24 must be a /21", message)

    def test_overlapping_hubs_and_missing_ranges_are_refused(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "platform_address_space: 10.100.0.0/21\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("connectivity/swedencentral: landing_zone_ranges is required", message)
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
        self.assertIn("connectivity/norwayeast: platform address space 10.100.0.0/21 overlaps 10.100.4.0/24, which connectivity/norwayeast reserves", str(caught.exception))

    def test_landing_zone_ranges_belong_to_one_region(self):
        self.hub("norwayeast", HUB)
        self.hub("swedencentral", "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: [10.100.0.0/24, 10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        self.assertIn("connectivity/norwayeast and connectivity/swedencentral: landing zone ranges 10.100.0.0/16 and 10.100.0.0/24 overlap", str(caught.exception))

    def test_a_platform_range_lies_only_in_its_own_regions_landing_zone_ranges(self):
        self.hub("norwayeast", HUB)  # its /21 inside its own 10.100.0.0/16: fine
        self.hub("swedencentral", "platform_address_space: 10.100.8.0/21\nlanding_zone_ranges: [10.200.0.0/16]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE)
        message = str(caught.exception)
        self.assertIn("connectivity/swedencentral: platform address space 10.100.8.0/21 overlaps 10.100.0.0/16, a landing zone range of connectivity/norwayeast", message)
        self.assertNotIn("connectivity/norwayeast: platform address space", message)

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
