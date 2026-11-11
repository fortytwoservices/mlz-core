"""Tests for the firewall rules: from firewall/ YAML to rule collection groups."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from mlz.firewall import PARTITIONS, build, partition
from mlz.merge import ConfigError

PLATFORM = {
    "root_id": "mlz",
    "connectivity_subscription_id": "00000000-0000-0000-0000-000000000002",
    "hubs": {"norwayeast": {"landing_zone_ranges": ["10.100.0.0/16"]}},
}
VENDING = {"landing_zones": {
    "payments-prod": {"region": "norwayeast", "range": "10.100.4.0/24"},
    "website-prod": {"region": "norwayeast", "range": "10.100.5.0/24"},
}}


class FirewallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def problems(self, vending=VENDING) -> str:
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, PLATFORM, vending)
        return str(caught.exception)

    def test_a_landing_zone_s_rules_come_from_its_range_in_its_partition(self):
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: card-processor\n    to: [api.stripe.com, '*.stripe.com']\n    ports: [https]\n")
        groups = build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"]
        name = f"lz-{partition('payments-prod'):02d}"
        self.assertEqual(set(groups), {"platform", name})
        self.assertEqual(groups[name]["priority"], 1000 + 100 * partition("payments-prod"))
        rule = groups[name]["application"]["payments-prod-application"]["rules"]["card-processor"]
        self.assertEqual(rule["source_addresses"], ["10.100.4.0/24"])
        self.assertEqual(rule["destination_fqdns"], ["*.stripe.com", "api.stripe.com"])
        self.assertEqual(rule["protocols"], [{"type": "Https", "port": 443}])
        self.assertEqual(groups[name]["network"], {})

    def test_addresses_landing_zones_and_ports_become_network_rules(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: to-website\n    to: [website-prod]\n    ports: [443, 8443]\n"
            "  - name: dns-partner\n    to: [203.0.113.10]\n    ports: ['udp:53', 53]\n"
        ))
        group = build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]
        rules = group["network"]["payments-prod-network"]["rules"]
        self.assertEqual(rules["to-website"]["destination_addresses"], ["10.100.5.0/24"])
        self.assertEqual(rules["to-website"]["destination_ports"], ["443", "8443"])
        self.assertEqual((rules["dns-partner"]["protocols"], rules["dns-partner-udp"]["protocols"]), (["TCP"], ["UDP"]))
        self.assertEqual(rules["dns-partner-udp"]["destination_ports"], ["53"])

    def test_platform_rules_cover_every_landing_zone_range_of_the_hub(self):
        self.write("firewall/platform.yaml", "outbound:\n  - name: windows-update\n    to: [WindowsUpdate]\n    ports: [https, http]\n")
        groups = build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"]
        rule = groups["platform"]["application"]["platform-application"]["rules"]["windows-update"]
        self.assertEqual(groups["platform"]["priority"], 500)
        self.assertEqual(rule["source_addresses"], ["10.100.0.0/16"])
        self.assertEqual(rule["destination_fqdn_tags"], ["WindowsUpdate"])
        self.assertEqual(rule["protocols"], [{"type": "Http", "port": 80}, {"type": "Https", "port": 443}])

    def test_partitions_are_fixed_and_spread(self):
        keys = [f"app{i}-prod" for i in range(600)]
        used = {partition(k) for k in keys}
        self.assertEqual(len(used), PARTITIONS)  # 600 landing zones still fit in 30 groups
        self.assertEqual(partition("payments-prod"), partition("payments-prod"))

    def test_every_problem_names_the_file(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: Bad_Name\n    to: [not a host]\n    ports: [ssh]\n"
            "  - name: tag-on-tcp\n    to: [WindowsUpdate, 10.0.0.1]\n    ports: [https]\n"
        ))
        self.write("firewall/landing-zones/unknown-prod.yaml", "outbound: []\n")
        message = self.problems()
        self.assertIn("firewall/landing-zones/payments-prod: outbound[0]: name 'Bad_Name'", message)
        self.assertIn("'not a host' is not a name, an address or range", message)
        self.assertIn("port 'ssh' is https, http", message)
        self.assertIn("an FQDN tag such as WindowsUpdate takes only https and http", message)
        self.assertIn("firewall/landing-zones/unknown-prod: no landing zone unknown-prod in the vending contract", message)

    def test_service_tags_become_network_rules_and_capitalised_names_stay_names(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: storage\n    to: [Storage.NorwayEast, AzureKeyVault]\n    ports: [https]\n"
            "  - name: partner\n    to: [Partner.example.com]\n    ports: [https]\n"
            "  - name: short\n    to: [Partner.com]\n    ports: [https]\n"
        ))
        group = build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]
        storage = group["network"]["payments-prod-network"]["rules"]["storage"]
        self.assertEqual(storage["destination_addresses"], ["AzureKeyVault", "Storage.NorwayEast"])
        self.assertEqual((storage["protocols"], storage["destination_ports"]), (["TCP"], ["443"]))
        partner = group["application"]["payments-prod-application"]["rules"]["partner"]
        self.assertEqual(partner["destination_fqdns"], ["partner.example.com"])
        short = group["application"]["payments-prod-application"]["rules"]["short"]
        self.assertEqual(short["destination_fqdns"], ["partner.com"])

    def lz_group(self) -> dict:
        return build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]

    def test_a_name_never_gets_https_as_plain_tcp_from_an_address_beside_it(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: api\n    to: [api.example.com, 203.0.113.10]\n    ports: [https, 8443]\n")
        group = self.lz_group()
        network = group["network"]["payments-prod-network"]["rules"]
        self.assertEqual((network["api"]["destination_addresses"], network["api"]["destination_fqdns"]), (["203.0.113.10/32"], []))
        self.assertEqual(network["api"]["destination_ports"], ["443", "8443"])
        self.assertEqual((network["api-names"]["destination_fqdns"], network["api-names"]["destination_ports"]), (["api.example.com"], ["8443"]))
        application = group["application"]["payments-prod-application"]["rules"]["api"]
        self.assertEqual((application["destination_fqdns"], application["protocols"]), (["api.example.com"], [{"type": "Https", "port": 443}]))

    def test_a_name_keeps_a_port_written_as_a_number(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: api\n    to: [api.example.com]\n    ports: [http, 443]\n")
        group = self.lz_group()
        network = group["network"]["payments-prod-network"]["rules"]
        self.assertEqual((list(network), network["api"]["destination_fqdns"], network["api"]["destination_ports"]), (["api"], ["api.example.com"], ["443"]))
        self.assertEqual(group["application"]["payments-prod-application"]["rules"]["api"]["protocols"], [{"type": "Http", "port": 80}])

    def test_names_and_addresses_on_the_same_ports_share_a_rule(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: sftp\n    to: [sftp.example.com, 203.0.113.10]\n    ports: [22]\n")
        network = self.lz_group()["network"]["payments-prod-network"]["rules"]
        self.assertEqual(list(network), ["sftp"])
        self.assertEqual((network["sftp"]["destination_addresses"], network["sftp"]["destination_fqdns"]), (["203.0.113.10/32"], ["sftp.example.com"]))

    def test_two_rules_never_make_the_same_network_rule(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: dns\n    to: [203.0.113.10]\n    ports: ['udp:53']\n"
            "  - name: dns-udp\n    to: [203.0.113.11]\n    ports: [443]\n"
        ))
        self.assertIn("rules dns and dns-udp both make a network rule named dns-udp", self.problems())

    def test_regional_azurecloud_tags_are_service_tags(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: azure\n    to: [AzureCloud.norwaye]\n    ports: [https]\n")
        group = self.lz_group()
        self.assertEqual(group["network"]["payments-prod-network"]["rules"]["azure"]["destination_addresses"], ["AzureCloud.norwaye"])
        self.assertEqual(group["application"], {})

    def test_outbound_that_is_not_a_list_is_a_problem_not_an_empty_list(self):
        for value in ("false", "0", "{}", "''"):
            with self.subTest(value=value):
                self.write("firewall/landing-zones/payments-prod.yaml", f"outbound: {value}\n")
                self.assertIn("outbound is a list of rules", self.problems())
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n")
        self.assertEqual(list(build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"]), ["platform"])

    def test_bad_names_are_problems_not_crashes(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: [a]\n    to: [203.0.113.10]\n    ports: [443]\n"
            "  - name: 7\n    to: [203.0.113.10]\n    ports: [443]\n"
            "  - name: Bad_Name\n    to: [not a target]\n    ports: [443]\n"
        ))
        problems = self.problems()
        self.assertIn("name ['a'] is lower-case", problems)
        self.assertIn("name 7 is lower-case", problems)
        self.assertIn("name 'Bad_Name' is lower-case", problems)
        self.assertIn("'not a target' is not a name", problems)  # an invalid name's targets are still checked

    def test_a_wildcard_name_takes_only_web_ports(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: wild\n    to: ['*.example.com']\n    ports: [https, 8443]\n")
        self.assertIn("a wildcard name such as *.example.com takes only https and http", self.problems())
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: wild\n    to: ['*.example.com']\n    ports: [https]\n")
        self.assertIn("wild", self.lz_group()["application"]["payments-prod-application"]["rules"])

    def test_a_basic_hub_reaches_names_only_on_web_ports(self):
        basic = {**PLATFORM, "hubs": {"norwayeast": {**PLATFORM["hubs"]["norwayeast"], "firewall": {"sku_tier": "Basic"}}}}
        self.write("firewall/platform.yaml", "outbound:\n  - name: sftp\n    to: [sftp.example.com]\n    ports: [22]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, basic, VENDING)
        self.assertIn("firewall/platform: outbound[0]: a Basic firewall reaches names only on https and http", str(caught.exception))
        self.write("firewall/platform.yaml", "outbound:\n  - name: web\n    to: [api.example.com, 203.0.113.10]\n    ports: [https, 22]\n")
        with self.assertRaises(ConfigError):
            build(self.repo, basic, VENDING)
        self.write("firewall/platform.yaml", "outbound:\n  - name: web\n    to: [api.example.com, 203.0.113.10]\n    ports: [https]\n")
        self.assertIn("web", build(self.repo, basic, VENDING)["policies"]["norwayeast"]["platform"]["application"]["platform-application"]["rules"])

    def test_targets_that_are_not_strings_are_problems(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: odd\n    to: [true, null, 8080]\n    ports: [https]\n")
        problems = self.problems()
        for value in ("True", "None", "8080"):
            self.assertIn(f"{value} is not a name, an address or range", problems)

    def test_priorities_count_only_the_hub_s_own_landing_zones(self):
        number = partition("payments-prod")
        other = next(f"aa-{i}" for i in range(10_000) if partition(f"aa-{i}") == number and f"aa-{i}" < "payments-prod")
        platform = {**PLATFORM, "hubs": {**PLATFORM["hubs"], "swedencentral": {"landing_zone_ranges": ["10.200.0.0/16"]}}}
        vending = {"landing_zones": {**VENDING["landing_zones"], other: {"region": "swedencentral", "range": "10.200.4.0/24"}}}
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    to: [203.0.113.10]\n    ports: [443]\n")
        before = build(self.repo, platform, vending)["policies"]["norwayeast"][f"lz-{number:02d}"]
        self.write(f"firewall/landing-zones/{other}.yaml", "outbound:\n  - name: api\n    to: [203.0.113.11]\n    ports: [443]\n")
        after = build(self.repo, platform, vending)["policies"]["norwayeast"][f"lz-{number:02d}"]
        self.assertEqual(after, before)  # a zone in another hub, sorting first in the same partition, moves nothing here

    def test_landing_zone_rules_wait_for_the_vending_contract(self):
        # The platform's first deployment: rules files exist, vending has published nothing yet.
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    to: [203.0.113.10]\n    ports: [443]\n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            groups = build(self.repo, PLATFORM, None)["policies"]["norwayeast"]
        self.assertEqual(list(groups), ["platform"])
        self.assertIn("no vending contract yet (_generated/vending.yaml): the rules for payments-prod wait for it", stderr.getvalue())
        # Once the contract exists, a key it does not name stops the check again.
        with self.assertRaisesRegex(ConfigError, "no landing zone payments-prod in the vending contract"):
            build(self.repo, PLATFORM, {"landing_zones": {}})

    def test_without_rule_files_only_the_required_rules(self):
        groups = build(self.repo, PLATFORM, None)["policies"]["norwayeast"]
        self.assertEqual(list(groups), ["platform"])
        self.assertEqual(groups["platform"]["application"], {})
        collection = groups["platform"]["network"]["required-network"]
        rule = collection["rules"]["azure-monitor-agent"]
        self.assertEqual(collection["priority"], 100)  # before the customer's platform collections, at 200
        self.assertEqual(rule["source_addresses"], ["10.100.0.0/16"])
        self.assertEqual(rule["destination_addresses"], ["AzureMonitor", "AzureResourceManager"])
        self.assertEqual((rule["protocols"], rule["destination_ports"]), (["TCP"], ["443"]))


if __name__ == "__main__":
    unittest.main()
