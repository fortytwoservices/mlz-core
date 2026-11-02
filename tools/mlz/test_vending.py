"""Tests for the vending config and the IP allocator. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz.merge import ConfigError
from mlz.vending import allocate, build

GOVERNANCE = {
    "root_id": "demo",
    "allowed_regions": ["norwayeast", "swedencentral"],
    "subscriptions": {"management": "11111111-1111-1111-1111-111111111111", "connectivity": "22222222-2222-2222-2222-222222222222"},
}
PLATFORM = {
    "hub_address_spaces": {"norwayeast": ["10.100.0.0/22"]},
    "connectivity": {
        "regions": {
            "norwayeast": {
                "routing_supernet": ["10.100.0.0/16"],
                "hub_virtual_network_id": "/subscriptions/c/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-hub",
                "dns_servers": ["10.100.0.4"],
                "spoke_default_routes": {"default": {"name": "default", "address_prefix": "0.0.0.0/0"}},
            }
        }
    },
}

DEFAULTS = "location: norwayeast\nbudget:\n  monthly: 500\n  contacts: [platform@demo.example]\n"
WORKLOAD = "name: Payments\nowner: payments@demo.example\ncost_center: '4711'\narchetype: corp\n"
PROD = "subscription: 33333333-3333-3333-3333-333333333333\nbudget:\n  monthly: 2000\n"
DEV = "subscription: 44444444-4444-4444-4444-444444444444\n"


class VendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("landing-zones/_defaults/all.yaml", DEFAULTS)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD)
        self.write("landing-zones/payments/prod.yaml", PROD)
        self.write("landing-zones/payments/dev.yaml", DEV)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def plan(self, entries: dict) -> None:
        self.write("_generated/ip-plan.yaml", "".join(f"{k}: {v}\n" for k, v in entries.items()))

    def test_the_four_layers_merge_and_the_key_names_the_landing_zone(self):
        self.plan({"payments-prod": "10.100.4.0/24", "payments-dev": "10.100.5.0/24"})
        configs = build(self.repo, GOVERNANCE, PLATFORM)
        prod = configs["payments-prod"]
        self.assertEqual(sorted(configs), ["payments-dev", "payments-prod"])
        self.assertEqual(prod["display_name"], "Payments (prod)")
        self.assertEqual(prod["budget"], {"monthly": 2000, "contacts": ["platform@demo.example"]})
        self.assertEqual(configs["payments-dev"]["budget"]["monthly"], 500)
        self.assertEqual(prod["management_group_id"], "demo-lz-corp")
        self.assertEqual(prod["tags"], {
            "Owner": "payments@demo.example", "CostCenter": "4711", "Environment": "prod",
            "Workload": "payments", "ManagedBy": "mlz-core",
        })
        self.assertEqual(prod["network"]["subnets"], {"default": "10.100.4.0/26"})
        self.assertEqual(prod["network"]["dns_servers"], ["10.100.0.4"])

    def test_subnets_are_carved_largest_first(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    app: 25\n    data: 26\n    web: 26\n")
        self.plan({"payments-prod": "10.100.4.0/24", "payments-dev": "10.100.5.0/24"})
        subnets = build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["network"]["subnets"]
        self.assertEqual(subnets, {"app": "10.100.4.0/25", "data": "10.100.4.128/26", "web": "10.100.4.192/26"})

    def test_the_allocator_skips_the_hub_and_never_moves_a_given_range(self):
        self.plan({"payments-prod": "10.100.4.0/24"})
        self.assertEqual(allocate(self.repo, PLATFORM), {"payments-dev": "10.100.5.0/24"})
        self.plan({})
        self.assertEqual(allocate(self.repo, PLATFORM), {"payments-dev": "10.100.4.0/24", "payments-prod": "10.100.5.0/24"})

    def test_a_landing_zone_without_a_range_is_refused_until_allocated(self):
        self.plan({"payments-prod": "10.100.4.0/24"})
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE, PLATFORM)
        self.assertIn("payments-dev: no address range yet", str(caught.exception))

    def test_every_problem_is_listed(self):
        self.write("landing-zones/payments/prod.yaml", "subscription: 11111111-1111-1111-1111-111111111111\nlocation: westeurope\narchetyp: corp\nnetwork:\n  address_space: 10.0.0.0/24\n")
        self.write("landing-zones/payments/dev.yaml", "subscription: not-a-sub\n")
        self.plan({"payments-prod": "10.100.4.0/24", "payments-dev": "10.100.5.0/24"})
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE, PLATFORM)
        message = str(caught.exception)
        self.assertIn("'archetyp' is not a landing zone setting", message)
        self.assertIn("subscription 11111111-1111-1111-1111-111111111111 is a platform subscription", message)
        self.assertIn("location westeurope is not one of the allowed regions", message)
        self.assertIn("network.address_space is written by the IP allocator", message)
        self.assertIn("payments/dev: subscription must be a subscription ID", message)

    def test_a_subscription_used_twice_is_refused(self):
        self.write("landing-zones/payments/dev.yaml", PROD)
        self.plan({"payments-prod": "10.100.4.0/24", "payments-dev": "10.100.5.0/24"})
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, GOVERNANCE, PLATFORM)
        self.assertIn("is used by more than one landing zone", str(caught.exception))

    def test_a_landing_zone_split_into_chunks_reads_the_same(self):
        self.plan({"payments-prod": "10.100.4.0/24", "payments-dev": "10.100.5.0/24"})
        whole = build(self.repo, GOVERNANCE, PLATFORM)
        (self.repo / "landing-zones/payments/prod.yaml").unlink()
        self.write("landing-zones/payments/prod/subscription.yaml", "subscription: 33333333-3333-3333-3333-333333333333\n")
        self.write("landing-zones/payments/prod/budget.yaml", "budget:\n  monthly: 2000\n")
        self.assertEqual(build(self.repo, GOVERNANCE, PLATFORM), whole)


if __name__ == "__main__":
    unittest.main()
