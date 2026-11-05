"""Tests for the firewall rules: from firewall/ YAML to rule collection groups."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz.firewall import PARTITIONS, build, partition
from mlz.merge import ConfigError

PLATFORM = {
    "root_id": "mlz",
    "connectivity_subscription_id": "a002de69-082a-4f44-b78f-c9e36e209af2",
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
        ))
        group = build(self.repo, PLATFORM, VENDING)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]
        storage = group["network"]["payments-prod-network"]["rules"]["storage"]
        self.assertEqual(storage["destination_addresses"], ["AzureKeyVault", "Storage.NorwayEast"])
        self.assertEqual((storage["protocols"], storage["destination_ports"]), (["TCP"], ["443"]))
        partner = group["application"]["payments-prod-application"]["rules"]["partner"]
        self.assertEqual(partner["destination_fqdns"], ["partner.example.com"])

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
