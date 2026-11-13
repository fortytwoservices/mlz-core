"""Tests for the vending config and the IP allocator. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from ipaddress import IPv4Network
from pathlib import Path

from mlz.files import read_yaml
from mlz.merge import ConfigError
from mlz.__main__ import MARKER, main, own_folder
from mlz import files
from mlz.contract import render
from mlz.firewall import vending_contract
from mlz.vending import IP_PLAN, allocate, build, ip_plan, landing_zones, place, release, render_plan

GOVERNANCE = {
    "root": "demo",
    "allowed_regions": ["norwayeast", "swedencentral"],
    "required_tags": ["Owner", "CostCenter"],
    "archetypes": {"corp": "demo-lz-corp", "online": "demo-lz-online", "sandbox": "demo-sandboxes"},
    "platform_subscriptions": {"management": "11111111-1111-1111-1111-111111111111", "connectivity": "22222222-2222-2222-2222-222222222222"},
}
PLATFORM = {
    "regions": {
        "norwayeast": {
            "hub_network": "/subscriptions/c/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-hub",
            "platform_address_space": "10.100.0.0/21",
            "landing_zone_ranges": ["10.100.0.0/16"],
            "dns_servers": ["10.100.0.4"],
            "spoke_routes": [{"name": "default", "to": "0.0.0.0/0", "via": "10.100.0.4"}],
        }
    },
}

DEFAULTS = "location: norwayeast\nbudget:\n  monthly: 500\n  contacts: [platform@demo.example]\n"
WORKLOAD = "short_name: pay\nowner: payments@demo.example\ncost_center: '4711'\narchetype: corp\n"
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

    def allocate(self, platform: dict = PLATFORM) -> list[str]:
        """Runs the allocator and writes its plan, as `python3 -m mlz allocate` does."""
        plan, changes, retired = allocate(self.repo, platform)
        self.write(IP_PLAN, render_plan(plan, retired))
        return changes

    def plan(self) -> dict:
        return read_yaml(self.repo / IP_PLAN)

    def problems(self, function, *args) -> str:
        with self.assertRaises(ConfigError) as caught:
            function(self.repo, *args)
        return str(caught.exception)

    def test_the_four_layers_merge_and_the_key_names_the_landing_zone(self):
        self.allocate()
        configs = build(self.repo, GOVERNANCE, PLATFORM)
        prod = configs["payments-prod"]
        self.assertEqual(sorted(configs), ["payments-dev", "payments-prod"])
        self.assertEqual(prod["budget"], {"monthly": 2000, "contacts": ["platform@demo.example"]})
        self.assertEqual(configs["payments-dev"]["budget"]["monthly"], 500)
        self.assertEqual(prod["management_group_id"], "demo-lz-corp")
        self.assertEqual(prod["tags"], {
            "Owner": "payments@demo.example", "CostCenter": "4711", "Environment": "prod",
            "Workload": "payments", "ManagedBy": "mlz-core",
        })
        self.assertEqual(prod["network"]["address_space"], "10.100.9.0/24")
        self.assertEqual(prod["network"]["subnets"], {"default": "10.100.9.0/26"})
        self.assertEqual(prod["network"]["dns_servers"], ["10.100.0.4"])
        self.assertEqual(prod["network"]["routes"], {"default": {
            "name": "default", "address_prefix": "0.0.0.0/0",
            "next_hop_type": "VirtualAppliance", "next_hop_in_ip_address": "10.100.0.4",
        }})
        self.assertIsNone(prod["github"])

    def test_the_ip_plan_reads_as_the_network_plan(self):
        self.allocate()
        text = (self.repo / IP_PLAN).read_text()
        self.assertTrue(text.startswith("# Every landing zone's network"))
        self.assertIn(
            "payments-dev:\n  range: 10.100.8.0/24\n  subnets:\n    default: 10.100.8.0/26\n\n"
            "payments-prod:\n  range: 10.100.9.0/24\n",
            text,
        )
        self.assertEqual(self.allocate(), [])  # a second run changes nothing
        self.assertEqual((self.repo / IP_PLAN).read_text(), text)

    def test_governance_decides_the_archetypes_and_the_required_tags(self):
        governance = {**GOVERNANCE, "required_tags": ["Owner", "CostCenter", "Environment", "BusinessUnit"]}
        self.allocate()
        message = self.problems(build, governance, PLATFORM)
        self.assertIn("governance requires the BusinessUnit tag", message)
        self.assertNotIn("Environment tag", message)  # mlz-core sets it
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD + "tags:\n  BusinessUnit: Finance\n")
        configs = build(self.repo, governance, PLATFORM)
        self.assertEqual(configs["payments-prod"]["tags"]["BusinessUnit"], "Finance")

    def test_tags_mlz_core_sets_cannot_be_overridden_in_any_case(self):
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD + "tags:\n  owner: someone@demo.example\n  Environment: production\n  Team: a\n  team: b\n")
        self.allocate()
        message = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("tags.owner comes from owner", message)
        self.assertIn("tags.Environment is set from the file name", message)
        self.assertIn("tags Team and team are one tag to Azure", message)

    def test_required_tags_match_in_any_case_and_none_means_none(self):
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD + "tags:\n  businessunit: Finance\n")
        self.allocate()
        build(self.repo, {**GOVERNANCE, "required_tags": ["owner", "BusinessUnit"]}, PLATFORM)
        build(self.repo, {**GOVERNANCE, "required_tags": None}, PLATFORM)

    def test_subnets_are_carved_largest_first(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    app: 25\n    data: 26\n    web: 26\n")
        self.allocate()
        subnets = build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["network"]["subnets"]
        self.assertEqual(subnets, {"app": "10.100.9.0/25", "data": "10.100.9.128/26", "web": "10.100.9.192/26"})

    def test_a_new_subnet_never_moves_the_ones_already_given(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n")
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n    app: 25\n")
        self.assertEqual(self.allocate(), ["payments-prod: subnet app 10.100.9.128/25"])
        subnets = build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["network"]["subnets"]
        self.assertEqual(subnets, {"web": "10.100.9.0/26", "app": "10.100.9.128/25"})

    def test_a_removed_subnet_is_held_until_released(self):
        prod = PROD + "network:\n  subnets:\n"
        self.write("landing-zones/payments/prod.yaml", prod + "    web: 26\n    app: 26\n")
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", prod + "    web: 26\n    data: 26\n")
        self.assertEqual(self.allocate(), ["payments-prod: subnet app 10.100.9.0/26 removed, held until released", "payments-prod: subnet data 10.100.9.128/26"])
        self.assertIn("  removed_subnets:\n    app: 10.100.9.0/26\n", (self.repo / IP_PLAN).read_text())
        self.assertEqual(build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["network"]["subnets"], {"web": "10.100.9.64/26", "data": "10.100.9.128/26"})
        self.write("landing-zones/payments/prod.yaml", prod + "    web: 26\n    data: 26\n    cache: 26\n")
        self.assertEqual(self.allocate(), ["payments-prod: subnet cache 10.100.9.192/26"])  # not app's block
        self.write("landing-zones/payments/prod.yaml", prod + "    web: 26\n    data: 26\n    cache: 26\n    app: 27\n")
        self.assertIn("subnet app was removed and still holds 10.100.9.0/26; ask for a /26", self.problems(allocate, PLATFORM))
        self.write("landing-zones/payments/prod.yaml", prod + "    web: 26\n    data: 26\n    cache: 26\n    app: 26\n")
        self.assertIn("payments-prod asks for app again; run the IP allocator", self.problems(release, "payments-prod"))
        self.assertEqual(self.allocate(), ["payments-prod: subnet app 10.100.9.0/26 given back"])

    def test_releasing_a_current_landing_zone_frees_only_its_removed_subnets(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n    app: 26\n")
        self.allocate()
        self.assertIn("holds no removed subnets", self.problems(release, "payments-prod"))
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n")
        self.allocate()
        plan, retired, freed = release(self.repo, "payments-prod")
        self.assertEqual((freed, plan["payments-prod"]["removed_subnets"], retired), ("payments-prod's removed subnets app 10.100.9.0/26", {}, []))
        self.assertEqual(str(plan["payments-prod"]["range"]), "10.100.9.0/24")

    def test_a_subnet_or_range_never_changes_size(self):
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  size: 23\n  subnets:\n    default: 27\n")
        message = self.problems(allocate, PLATFORM)
        self.assertIn("payments-prod: network.size is 23, but its range 10.100.9.0/24 was given as a /24", message)
        self.assertIn("subnet default is a /27 here but was given as 10.100.9.0/26", message)
        self.assertIn("a range never changes size", self.problems(build, GOVERNANCE, PLATFORM))

    def test_subnets_must_be_valid_and_fit(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  size: 26\n  subnets:\n    a: 30\n    b: '26'\n    c: 25\n    D: 27\n")
        self.write("landing-zones/payments/dev.yaml", DEV + "network:\n  size: 27\n  subnets:\n    a: 28\n    b: 28\n    c: 28\n")
        message = self.problems(allocate, PLATFORM)
        self.assertIn("network.subnets.a is a prefix length up to 29", message)
        self.assertIn("network.subnets.b is a prefix length up to 29", message)
        self.assertIn("network.subnets.c is a /25, larger than the landing zone's /26", message)
        self.assertIn("subnet name 'D' is up to 20 lower-case", message)
        self.assertIn("landing-zones/payments/dev: the subnets need more addresses than a /27 has", message)
        self.assertFalse((self.repo / IP_PLAN).exists())  # nothing is written when anything is wrong

    def test_the_default_subnet_fits_a_small_range(self):
        self.write("landing-zones/payments/dev.yaml", DEV + "network:\n  size: 28\n")
        self.allocate()
        self.assertEqual(build(self.repo, GOVERNANCE, PLATFORM)["payments-dev"]["network"]["subnets"], {"default": "10.100.9.0/28"})  # after the larger /24

    def test_larger_new_ranges_are_placed_first(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  size: 23\n")
        platform = {"regions": {"norwayeast": {**PLATFORM["regions"]["norwayeast"], "landing_zone_ranges": ["10.1.0.0/23", "10.1.2.0/24"]}}}
        self.allocate(platform)
        self.assertEqual((self.plan()["payments-prod"]["range"], self.plan()["payments-dev"]["range"]), ("10.1.0.0/23", "10.1.2.0/24"))

    def test_the_allocator_skips_the_platform_range_and_pools_too_small(self):
        platform = {"regions": {"norwayeast": {**PLATFORM["regions"]["norwayeast"], "landing_zone_ranges": ["10.100.0.0/25", "10.100.0.0/16"]}}}
        self.assertEqual(self.allocate(platform), [
            "payments-dev: range 10.100.8.0/24", "payments-dev: subnet default 10.100.8.0/26",
            "payments-prod: range 10.100.9.0/24", "payments-prod: subnet default 10.100.9.0/26",
        ])

    def test_a_platform_contract_from_before_v0_3_still_keeps_the_hub_clear(self):
        # Its hub_range is the hub's own /22: vending, upgraded first, reads it until the next contract.
        region = {k: v for k, v in PLATFORM["regions"]["norwayeast"].items() if k != "platform_address_space"}
        older = {"regions": {"norwayeast": {**region, "hub_range": ["10.100.0.0/22"]}}}
        self.allocate(older)
        self.assertEqual(self.plan()["payments-dev"]["range"], "10.100.4.0/24")

    def test_reserved_ranges_are_never_given_and_conflicts_are_reported(self):
        reserving = {"regions": {"norwayeast": {**PLATFORM["regions"]["norwayeast"], "reserved": ["10.100.8.0/23"]}}}
        self.allocate(reserving)
        self.assertEqual((self.plan()["payments-dev"]["range"], self.plan()["payments-prod"]["range"]), ("10.100.10.0/24", "10.100.11.0/24"))
        later = {"regions": {"norwayeast": {**PLATFORM["regions"]["norwayeast"], "reserved": ["10.100.10.0/24"]}}}
        self.assertIn("payments-dev's range 10.100.10.0/24 overlaps 10.100.10.0/24, which the platform reserves", self.problems(allocate, later))

    def test_check_mode_fails_when_the_plan_is_stale_and_writes_nothing(self):
        self.write("_generated/platform.yaml", render("platform", PLATFORM))
        run = lambda *extra: self.cli("allocate", "--repo", str(self.repo), *extra)
        self.assertEqual(run("--check"), 1)
        self.assertFalse((self.repo / IP_PLAN).exists())
        self.assertEqual(run(), 0)
        self.assertEqual(run("--check"), 0)
        self.write("landing-zones/payments/test.yaml", DEV.replace("44", "55"))
        self.assertEqual(run("--check"), 1)

    def test_a_command_starts_terraform_once(self):
        self.write("_generated/platform.yaml", render("platform", PLATFORM))
        self.write("_generated/governance.yaml", render("governance", GOVERNANCE))
        self.cli("allocate", "--repo", str(self.repo))
        files._parsed.clear()
        with mock.patch("mlz.files.subprocess.run", wraps=subprocess.run) as run:
            self.assertEqual(self.cli("vending", "--repo", str(self.repo), "--out", str(self.repo / "out")), 0)
        self.assertEqual(run.call_count, 1)

    def cli(self, *argv: str) -> int:
        with mock.patch.object(sys, "argv", ["mlz", *argv]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main()

    def test_names_are_checked_before_anything_is_allocated(self):
        self.write("landing-zones/pay_ments/prod.yaml", PROD.replace("33", "66"))
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    a-subnet-name-too-long: 26\n")
        message = self.problems(allocate, PLATFORM)
        self.assertIn("landing-zones/pay_ments/prod: folder and file names are up to 24 lower-case", message)
        self.assertIn("subnet name 'a-subnet-name-too-long' is up to 20", message)
        self.assertFalse((self.repo / IP_PLAN).exists())

    def test_kept_ranges_are_checked_against_the_platform_as_it_is_now(self):
        self.allocate()
        moved = {"regions": {"norwayeast": {**PLATFORM["regions"]["norwayeast"], "platform_address_space": "10.100.8.0/21", "landing_zone_ranges": ["10.100.9.0/24", "10.100.16.0/21"]}}}
        message = self.problems(allocate, moved)
        self.assertIn("payments-dev's range 10.100.8.0/24 overlaps the platform's range 10.100.8.0/21 (the hub, management and identity)", message)
        self.assertIn("payments-dev: its range 10.100.8.0/24 is outside norwayeast's landing zone ranges", message)
        self.assertNotIn("payments-prod: its range", message)
        self.assertIn("overlaps the platform's range", self.problems(build, GOVERNANCE, moved))

    def test_a_removed_landing_zone_keeps_its_range_until_released(self):
        self.allocate()
        (self.repo / "landing-zones/payments/dev.yaml").unlink()
        self.write("landing-zones/website/_workload.yaml", WORKLOAD.replace("short_name: pay", "short_name: web"))
        self.write("landing-zones/website/prod.yaml", DEV)
        self.allocate()
        self.assertEqual(self.plan()["website-prod"]["range"], "10.100.10.0/24")  # not dev's 10.100.8.0/24
        self.assertIn("# Removed from landing-zones/", (self.repo / IP_PLAN).read_text())
        self.assertEqual(sorted(build(self.repo, GOVERNANCE, PLATFORM)), ["payments-prod", "website-prod"])
        self.assertIn("payments-prod is still in landing-zones/payments/prod", self.problems(release, "payments-prod"))
        # The contract names it apart from the landing zones' firewall sources, so the platform builds nothing over it.
        contract = vending_contract(landing_zones(self.repo), ip_plan(self.repo))
        self.assertEqual((sorted(contract["landing_zones"]), contract["held"]), (["payments-prod", "website-prod"], {"payments-dev": "10.100.8.0/24"}))
        plan, retired, freed = release(self.repo, "payments-dev")
        self.assertEqual((sorted(plan), retired, freed), (["payments-prod", "website-prod"], [], "payments-dev's range 10.100.8.0/24"))
        self.write(IP_PLAN, render_plan(plan, retired))
        self.assertNotIn("held", vending_contract(landing_zones(self.repo), ip_plan(self.repo)))  # none left: as before

    def test_a_decommissioned_landing_zone_goes_to_decommissioned_and_keeps_its_range_held(self):
        self.allocate()
        dev_range = self.plan()["payments-dev"]["range"]
        self.write("landing-zones/payments/dev.yaml", "decommission: true\n" + DEV)
        self.assertEqual(self.allocate(), [])  # nothing moves, nothing is released
        config = build(self.repo, GOVERNANCE, PLATFORM)["payments-dev"]
        self.assertTrue(config["decommission"])
        self.assertEqual(config["management_group_id"], "demo-decommissioned")
        self.assertFalse(build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["decommission"])
        # The platform stops applying its rules, and builds nothing over its range, until it is released.
        contract = vending_contract(landing_zones(self.repo), ip_plan(self.repo))
        self.assertEqual((sorted(contract["landing_zones"]), contract["held"]), (["payments-prod"], {"payments-dev": dev_range}))
        self.assertIn("payments-dev is still in landing-zones/payments/dev", self.problems(release, "payments-dev"))

    def test_decommission_is_only_true_and_only_in_the_landing_zone_s_own_file(self):
        self.allocate()
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD + "decommission: true\n")
        problems = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/_workload: decommission retires one landing zone, so it goes in that landing zone's own file, landing-zones/payments/prod.", problems)
        # Also when the landing zone's own file sets it too, which hides the layer above.
        self.write("landing-zones/payments/prod.yaml", "decommission: true\n" + PROD)
        problems = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/_workload: decommission retires one landing zone, so it goes in that landing zone's own file, landing-zones/payments/prod.", problems)
        self.write("landing-zones/payments/prod.yaml", PROD)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD)
        self.write("landing-zones/payments/dev.yaml", "decommission: false\n" + DEV)
        self.assertIn("landing-zones/payments/dev: decommission is true or not there at all, not False.", self.problems(build, GOVERNANCE, PLATFORM))

    def test_names_come_from_the_template(self):
        self.allocate()
        names = build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["names"]
        self.assertEqual({k: v for k, v in names.items() if k != "network_security_groups"}, {
            "network_resource_group": "rg-demo-pay-net-pd-nwe-01", "identity_resource_group": "rg-demo-pay-id-pd-nwe-01",
            "virtual_network": "vnet-demo-pay-pd-nwe-01", "route_table": "rt-demo-pay-pd-nwe-01",
            "deploy_identity": "id-demo-pay-pd-nwe-01", "budget": "budget-demo-pay-pd-nwe-01",
            "subnet": "snet-%s", "peering_to_hub": "peer-payments-prod-to-hub", "peering_from_hub": "peer-hub-to-payments-prod",
        })
        self.assertEqual(names["network_security_groups"], {"default": "nsg-demo-pay-default-pd-nwe-01"})
        # customer.yaml's naming, through governance's contract.
        named = {**GOVERNANCE, "naming": {"org": "acme", "environments": {"prod": "pr"}}}
        self.assertEqual(build(self.repo, named, PLATFORM)["payments-prod"]["names"]["virtual_network"], "vnet-acme-pay-pr-nwe-01")

    def test_explicit_names_win_and_are_checked(self):
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", PROD + "names:\n  virtual_network: vnet-payments-legacy\n  network_security_groups:\n    default: nsg-legacy\n")
        names = build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]["names"]
        self.assertEqual((names["virtual_network"], names["network_security_groups"]["default"], names["route_table"]), ("vnet-payments-legacy", "nsg-legacy", "rt-demo-pay-pd-nwe-01"))
        self.write("landing-zones/payments/prod.yaml", PROD + "names:\n  vnet: x\n  route_table: 'rt bad'\n  network_security_groups:\n    web: nsg-web\n")
        problems = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/prod: names.vnet is not a name vending gives", problems)
        self.assertIn("landing-zones/payments/prod: route_table name rt bad has characters Azure does not allow", problems)
        self.assertIn("landing-zones/payments/prod: names.network_security_groups.web is not one of its subnets (default)", problems)
        self.write("landing-zones/payments/prod.yaml", PROD)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD + "names:\n  route_table: rt-x\n")
        self.assertIn("landing-zones/payments/_workload: names are one landing zone's own", self.problems(build, GOVERNANCE, PLATFORM))

    def test_two_of_one_type_side_by_side_are_named_apart(self):
        (self.repo / "landing-zones/payments/dev.yaml").unlink()
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n    app: 26\n")
        self.allocate()
        # A template without {purpose} gives both resource groups, and both NSGs, one name.
        flat = {**GOVERNANCE, "naming": {"template": "{type}-{org}-{app}-{env}-{region}-{nn}"}}
        problems = self.problems(build, flat, PLATFORM)
        self.assertIn("landing-zones/payments/prod: network_resource_group and identity_resource_group would both be rg-demo-pay-pd-nwe-01, "
                      "and Azure needs its resource groups named apart; put {purpose} in the template, or set one under names:.", problems)
        self.assertIn("network_security_groups.app and network_security_groups.web would both be nsg-demo-pay-pd-nwe-01", problems)
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n    app: 26\n"
                   "names:\n  identity_resource_group: RG-demo-pay-pd-nwe-01\n  network_security_groups:\n    web: nsg-pay-web\n")
        problems = self.problems(build, flat, PLATFORM)  # Azure ignores case in a resource group's name
        self.assertIn("would both be RG-demo-pay-pd-nwe-01", problems)
        self.assertNotIn("NSGs", problems)
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    web: 26\n    app: 26\n"
                   "names:\n  identity_resource_group: rg-pay-identity\n  network_security_groups:\n    web: nsg-pay-web\n")
        names = build(self.repo, flat, PLATFORM)["payments-prod"]["names"]
        self.assertEqual((names["network_resource_group"], names["identity_resource_group"]), ("rg-demo-pay-pd-nwe-01", "rg-pay-identity"))

    def test_a_short_name_that_is_not_text_is_reported_not_a_crash(self):
        self.allocate()
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD.replace("short_name: pay", "short_name: [pay]"))
        self.write("landing-zones/web/_workload.yaml", WORKLOAD.replace("short_name: pay", "short_name: 42"))
        self.write("landing-zones/web/prod.yaml", "subscription: 55555555-5555-5555-5555-555555555555\n")
        problems = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/_workload: short_name is ['pay']; it must be 2 to 5", problems)
        self.assertIn("landing-zones/web/_workload: short_name is 42; it must be 2 to 5", problems)

    def test_the_short_name_is_the_workload_s_own_when_it_fits(self):
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD.replace("short_name: pay\n", ""))
        self.write("landing-zones/web/_workload.yaml", WORKLOAD.replace("short_name: pay\n", ""))
        self.write("landing-zones/web/prod.yaml", "subscription: 55555555-5555-5555-5555-555555555555\n")
        self.allocate()
        problems = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/_workload.yaml: short_name is needed, since the workload's name 'payments' is not 2 to 5", problems)
        self.assertNotIn("landing-zones/web", problems)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD.replace("short_name: pay", "short_name: web"))
        self.assertIn("workloads payments, web share the short name web", self.problems(build, GOVERNANCE, PLATFORM))
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD)
        self.assertEqual(build(self.repo, GOVERNANCE, PLATFORM)["web-prod"]["names"]["virtual_network"], "vnet-demo-web-pd-nwe-01")

    def test_a_landing_zone_without_a_range_is_refused_until_allocated(self):
        self.allocate()
        self.write("landing-zones/payments/test.yaml", DEV.replace("44", "55"))
        self.assertIn("payments-test: no address range yet", self.problems(build, GOVERNANCE, PLATFORM))

    def test_subnets_not_yet_allocated_are_refused(self):
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  subnets:\n    default: 26\n    app: 26\n")
        self.assertIn("payments-prod: its subnets in _generated/ip-plan.yaml are not the ones asked for", self.problems(build, GOVERNANCE, PLATFORM))

    def test_two_paths_with_the_same_key_are_refused(self):
        self.write("landing-zones/payments-api/prod.yaml", PROD)
        self.write("landing-zones/payments/api-prod.yaml", DEV)
        message = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/api-prod and landing-zones/payments-api/prod are both the landing zone payments-api-prod", message)

    def test_every_problem_is_listed(self):
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", "subscription: 11111111-1111-1111-1111-111111111111\nlocation: westeurope\narchetyp: corp\nnetwork:\n  address_space: 10.0.0.0/24\n")
        self.write("landing-zones/payments/dev.yaml", "subscription: not-a-sub\nbudget:\n  montly: 500\n")
        message = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("'archetyp' is not a landing zone setting", message)
        self.assertIn("subscription 11111111-1111-1111-1111-111111111111 is a platform subscription", message)
        self.assertIn("location westeurope is not one of the allowed regions", message)
        self.assertIn("network.address_space is written by the IP allocator", message)
        self.assertIn("payments/dev: subscription must be a subscription ID", message)
        self.assertIn("payments/dev: budget.montly is not a setting; use contacts, monthly", message)

    def test_a_problem_names_the_file_that_set_the_value_once(self):
        self.write("landing-zones/_defaults/all.yaml", DEFAULTS + "network:\n  size: 30\n")
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD + "tags:\n  Team: [a]\n")
        message = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertEqual(message.count("landing-zones/_defaults/all: network.size is a prefix length"), 1)
        self.assertIn("landing-zones/payments/_workload: tags.Team is one value", message)
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  size: 31\n")
        self.assertIn("landing-zones/payments/prod: network.size", self.problems(build, GOVERNANCE, PLATFORM))

    def test_tags_stay_within_azure_limits(self):
        many = "".join(f"  T{i}: v\n" for i in range(46))
        self.write("landing-zones/payments/prod.yaml", PROD + "tags:\n" + many + "  Long: " + "x" * 257 + "\n  'a/b': v\n")
        self.allocate()
        message = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("48 extra tags; Azure allows 45", message)
        self.assertIn("tags.Long is longer than Azure's 256 characters", message)
        self.assertIn("tag name 'a/b' must be 1 to 512 characters", message)
        self.write("landing-zones/payments/prod.yaml", PROD + "tags:\n  '': Finance\n")
        self.assertIn("tag name '' must be 1 to 512 characters", self.problems(build, GOVERNANCE, PLATFORM))
        for empty in ("false", "0", "[]"):
            self.write("landing-zones/payments/prod.yaml", PROD + f"tags: {empty}\n")
            self.assertIn("tags maps each extra tag to its value", self.problems(build, GOVERNANCE, PLATFORM), empty)

    def test_placing_among_many_taken_ranges_is_one_pass(self):
        taken = [IPv4Network((0x0A000000 + i * 256, 24)) for i in range(4000)]
        self.assertEqual(place([IPv4Network("10.0.0.0/8")], 24, taken), IPv4Network("10.15.160.0/24"))
        self.assertEqual(place([IPv4Network("10.0.0.0/8")], 22, taken), IPv4Network("10.15.160.0/22"))
        self.assertIsNone(place([IPv4Network("10.0.0.0/20")], 24, taken))

    def test_settings_of_the_wrong_kind_are_reported_not_crashed_on(self):
        self.allocate()
        self.write("landing-zones/payments/prod.yaml", PROD.split("budget")[0] + "budget: 500\nnetwork: big\ntags: [a]\ngithub: payments\nlocation: [norwayeast]\n")
        message = self.problems(build, GOVERNANCE, PLATFORM)
        self.assertIn("budget is a group of settings (contacts, monthly), not 500", message)
        self.assertIn("network is a group of settings (size, subnets), not 'big'", message)
        self.assertIn("tags maps each extra tag to its value", message)
        self.assertIn("github is a group of settings (organization, repo), not 'payments'", message)
        self.assertIn("location is one value", message)
        self.assertIn("location ['norwayeast'] has no hub", self.problems(allocate, PLATFORM))

    def test_a_repo_needs_an_organization(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "github:\n  repo: payments-infra\n")
        self.allocate()
        self.assertIn("github.repo needs github.organization", self.problems(build, GOVERNANCE, PLATFORM))
        self.write("landing-zones/_defaults/all.yaml", DEFAULTS + "github:\n  organization: demo-org\n")
        configs = build(self.repo, GOVERNANCE, PLATFORM)
        self.assertEqual(configs["payments-prod"]["github"], {"organization": "demo-org", "repo": "payments-infra"})
        self.assertIsNone(configs["payments-dev"]["github"])

    def test_budget_alerts_go_to_the_owner_without_contacts(self):
        self.write("landing-zones/_defaults/all.yaml", "location: norwayeast\nbudget:\n  monthly: 500\n")
        self.allocate()
        self.assertEqual(build(self.repo, GOVERNANCE, PLATFORM)["payments-dev"]["budget"]["contacts"], ["payments@demo.example"])

    def test_a_subscription_used_twice_is_refused(self):
        self.write("landing-zones/payments/dev.yaml", PROD)
        self.allocate()
        self.assertIn("is used by more than one landing zone", self.problems(build, GOVERNANCE, PLATFORM))

    def test_a_landing_zone_split_into_chunks_reads_the_same(self):
        self.allocate()
        whole = build(self.repo, GOVERNANCE, PLATFORM)
        (self.repo / "landing-zones/payments/prod.yaml").unlink()
        self.write("landing-zones/payments/prod/subscription.yaml", "subscription: 33333333-3333-3333-3333-333333333333\n")
        self.write("landing-zones/payments/prod/budget.yaml", "budget:\n  monthly: 2000\n")
        self.assertEqual(build(self.repo, GOVERNANCE, PLATFORM), whole)

    def test_the_output_folder_is_emptied_of_old_configs_and_never_a_stranger_s(self):
        out = self.repo / "out"
        own_folder(out)
        (out / "website-prod.json").write_text("{}")
        own_folder(out)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [MARKER])
        (self.repo / "landing-zones" / "notes.txt").write_text("")
        with self.assertRaises(ConfigError):
            own_folder(self.repo / "landing-zones")

    def test_an_ip_plan_not_written_by_the_allocator_is_refused(self):
        self.write(IP_PLAN, "payments-prod: 10.100.4.0/24\n")
        self.assertIn("payments-prod is not as the IP allocator writes it", self.problems(build, GOVERNANCE, PLATFORM))


if __name__ == "__main__":
    unittest.main()
