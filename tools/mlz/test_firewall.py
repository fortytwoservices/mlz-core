"""Tests for the firewall rules: from firewall/ YAML to rule collection groups."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mlz.firewall import PARTITIONS, build, github_actions_domains, partition
from mlz.merge import ConfigError

# A hub's names, as tools/mlz/names.py gives them: the policy's ID is built from two of them.
def names(code: str) -> dict:
    return {"resource_group": f"rg-mlz-hub-pd-{code}-01", "firewall_policy": f"afwp-mlz-hub-pd-{code}-01"}


PLATFORM = {
    "root_id": "mlz",
    "connectivity_subscription_id": "00000000-0000-0000-0000-000000000002",
    "hubs": {"norwayeast": {"landing_zone_ranges": ["10.100.0.0/16"], "names": names("nwe")}},
    # As tools/mlz/platform.py lays them out, here from a platform address space of 10.99.0.0/21.
    "networks": {
        "management": {"norwayeast": {"address_space": "10.99.4.0/24", "subnets": {
            "github-runners": "10.99.4.0/25", "runners": "10.99.4.128/26", "private-endpoints": "10.99.4.192/27"}}},
        "identity": {},
    },
}
IDENTITY = {"address_space": "10.99.5.0/24", "subnets": {"domain-controllers": "10.99.5.0/26"}}
# GitHub's Actions domains, as github_actions_domains returns them.
GITHUB = ["*.actions.githubusercontent.com", "*.github.com", "github.com", "productionresultssa0.blob.core.windows.net"]
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
            build(self.repo, PLATFORM, vending, GITHUB)
        return str(caught.exception)

    def meta(self, actions=GITHUB) -> Path:
        """A saved copy of api.github.com/meta, with only what github_actions_domains reads."""
        path = Path(self.tmp.name) / "meta.json"
        path.write_text(json.dumps({"domains": {"actions": actions}}))
        return path

    def test_each_hub_s_policy_is_found_by_the_names_the_platform_gives_it(self):
        platform = {**PLATFORM, "hubs": {**PLATFORM["hubs"], "swedencentral": {"names": names("sdc"), "landing_zone_ranges": ["10.200.0.0/16"]}}}
        self.assertEqual(build(self.repo, platform, VENDING, GITHUB)["policy_ids"], {
            region: f"/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/rg-mlz-hub-pd-{code}-01/providers/Microsoft.Network/firewallPolicies/afwp-mlz-hub-pd-{code}-01"
            for region, code in (("norwayeast", "nwe"), ("swedencentral", "sdc"))})

    def test_a_landing_zone_s_rules_come_from_its_range_in_its_partition(self):
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: card-processor\n    description: For the test.\n    to: [api.stripe.com, '*.stripe.com']\n    ports: [https]\n")
        groups = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]
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
            "  - name: to-website\n    description: For the test.\n    to: [website-prod]\n    ports: [443, 8443]\n"
            "  - name: dns-partner\n    description: For the test.\n    to: [203.0.113.10]\n    ports: ['udp:53', 53]\n"
        ))
        group = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]
        rules = group["network"]["payments-prod-network"]["rules"]
        self.assertEqual(rules["to-website"]["destination_addresses"], ["10.100.5.0/24"])
        self.assertEqual(rules["to-website"]["destination_ports"], ["443", "8443"])
        self.assertEqual((rules["dns-partner"]["protocols"], rules["dns-partner-udp"]["protocols"]), (["TCP"], ["UDP"]))
        self.assertEqual(rules["dns-partner-udp"]["destination_ports"], ["53"])

    def test_platform_rules_cover_every_landing_zone_range_of_the_hub(self):
        self.write("firewall/platform.yaml", "outbound:\n  - name: windows-update\n    description: For the test.\n    to: [WindowsUpdate]\n    ports: [https, http]\n")
        groups = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]
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
            "  - name: Bad_Name\n    description: For the test.\n    to: [not a host]\n    ports: [ssh]\n"
            "  - name: tag-on-tcp\n    description: For the test.\n    to: [WindowsUpdate, 10.0.0.1]\n    ports: [https]\n"
        ))
        self.write("firewall/landing-zones/unknown-prod.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [203.0.113.10]\n    ports: [bad]\n")
        message = self.problems()
        self.assertIn("firewall/landing-zones/payments-prod: outbound[0]: name 'Bad_Name'", message)
        self.assertIn("'not a host' is not a name, an address or range", message)
        self.assertIn("port 'ssh' is https, http", message)
        self.assertIn("an FQDN tag such as WindowsUpdate takes only https and http", message)
        # A landing zone vending has not published waits, and its rules are still checked.
        self.assertIn("firewall/landing-zones/unknown-prod: outbound[0]: port 'bad'", message)

    def test_service_tags_become_network_rules_and_capitalised_names_stay_names(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: storage\n    description: For the test.\n    to: [Storage.NorwayEast, AzureKeyVault]\n    ports: [https]\n"
            "  - name: partner\n    description: For the test.\n    to: [Partner.example.com]\n    ports: [https]\n"
            "  - name: short\n    description: For the test.\n    to: [Partner.com]\n    ports: [https]\n"
        ))
        group = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]
        storage = group["network"]["payments-prod-network"]["rules"]["storage"]
        self.assertEqual(storage["destination_addresses"], ["AzureKeyVault", "Storage.NorwayEast"])
        self.assertEqual((storage["protocols"], storage["destination_ports"]), (["TCP"], ["443"]))
        partner = group["application"]["payments-prod-application"]["rules"]["partner"]
        self.assertEqual(partner["destination_fqdns"], ["partner.example.com"])
        short = group["application"]["payments-prod-application"]["rules"]["short"]
        self.assertEqual(short["destination_fqdns"], ["partner.com"])

    def lz_group(self) -> dict:
        return build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"][f"lz-{partition('payments-prod'):02d}"]

    def test_a_name_never_gets_https_as_plain_tcp_from_an_address_beside_it(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: api\n    description: For the test.\n    to: [api.example.com, 203.0.113.10]\n    ports: [https, 8443]\n")
        group = self.lz_group()
        network = group["network"]["payments-prod-network"]["rules"]
        self.assertEqual((network["api"]["destination_addresses"], network["api"]["destination_fqdns"]), (["203.0.113.10/32"], []))
        self.assertEqual(network["api"]["destination_ports"], ["443", "8443"])
        self.assertEqual((network["api-names"]["destination_fqdns"], network["api-names"]["destination_ports"]), (["api.example.com"], ["8443"]))
        application = group["application"]["payments-prod-application"]["rules"]["api"]
        self.assertEqual((application["destination_fqdns"], application["protocols"]), (["api.example.com"], [{"type": "Https", "port": 443}]))

    def test_a_name_keeps_a_port_written_as_a_number(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: api\n    description: For the test.\n    to: [api.example.com]\n    ports: [http, 443]\n")
        group = self.lz_group()
        network = group["network"]["payments-prod-network"]["rules"]
        self.assertEqual((list(network), network["api"]["destination_fqdns"], network["api"]["destination_ports"]), (["api"], ["api.example.com"], ["443"]))
        self.assertEqual(group["application"]["payments-prod-application"]["rules"]["api"]["protocols"], [{"type": "Http", "port": 80}])

    def test_names_and_addresses_on_the_same_ports_share_a_rule(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: sftp\n    description: For the test.\n    to: [sftp.example.com, 203.0.113.10]\n    ports: [22]\n")
        network = self.lz_group()["network"]["payments-prod-network"]["rules"]
        self.assertEqual(list(network), ["sftp"])
        self.assertEqual((network["sftp"]["destination_addresses"], network["sftp"]["destination_fqdns"]), (["203.0.113.10/32"], ["sftp.example.com"]))

    def test_two_rules_never_make_the_same_network_rule(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: dns\n    description: For the test.\n    to: [203.0.113.10]\n    ports: ['udp:53']\n"
            "  - name: dns-udp\n    description: For the test.\n    to: [203.0.113.11]\n    ports: [443]\n"
        ))
        self.assertIn("rules dns and dns-udp both make a network rule named dns-udp", self.problems())

    def test_regional_azurecloud_tags_are_service_tags(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: azure\n    description: For the test.\n    to: [AzureCloud.norwaye]\n    ports: [https]\n")
        group = self.lz_group()
        self.assertEqual(group["network"]["payments-prod-network"]["rules"]["azure"]["destination_addresses"], ["AzureCloud.norwaye"])
        self.assertEqual(group["application"], {})

    def test_outbound_that_is_not_a_list_is_a_problem_not_an_empty_list(self):
        for value in ("false", "0", "{}", "''"):
            with self.subTest(value=value):
                self.write("firewall/landing-zones/payments-prod.yaml", f"outbound: {value}\n")
                self.assertIn("outbound is a list of rules", self.problems())
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n")
        self.assertEqual(list(build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]), ["platform"])

    def test_bad_names_are_problems_not_crashes(self):
        self.write("firewall/landing-zones/payments-prod.yaml", (
            "outbound:\n"
            "  - name: [a]\n    description: For the test.\n    to: [203.0.113.10]\n    ports: [443]\n"
            "  - name: 7\n    description: For the test.\n    to: [203.0.113.10]\n    ports: [443]\n"
            "  - name: Bad_Name\n    description: For the test.\n    to: [not a target]\n    ports: [443]\n"
        ))
        problems = self.problems()
        self.assertIn("name ['a'] is lower-case", problems)
        self.assertIn("name 7 is lower-case", problems)
        self.assertIn("name 'Bad_Name' is lower-case", problems)
        self.assertIn("'not a target' is not a name", problems)  # an invalid name's targets are still checked

    def test_a_wildcard_name_takes_only_web_ports(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: wild\n    description: For the test.\n    to: ['*.example.com']\n    ports: [https, 8443]\n")
        self.assertIn("a wildcard name such as *.example.com takes only https and http", self.problems())
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: wild\n    description: For the test.\n    to: ['*.example.com']\n    ports: [https]\n")
        self.assertIn("wild", self.lz_group()["application"]["payments-prod-application"]["rules"])

    def test_a_basic_hub_reaches_names_only_on_web_ports(self):
        basic = {**PLATFORM, "hubs": {"norwayeast": {**PLATFORM["hubs"]["norwayeast"], "firewall": {"sku_tier": "Basic"}}}}
        self.write("firewall/platform.yaml", "outbound:\n  - name: sftp\n    description: For the test.\n    to: [sftp.example.com]\n    ports: [22]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, basic, VENDING, GITHUB)
        self.assertIn("firewall/platform: outbound[0]: a Basic firewall reaches names only on https and http", str(caught.exception))
        self.write("firewall/platform.yaml", "outbound:\n  - name: web\n    description: For the test.\n    to: [api.example.com, 203.0.113.10]\n    ports: [https, 22]\n")
        with self.assertRaises(ConfigError):
            build(self.repo, basic, VENDING, GITHUB)
        self.write("firewall/platform.yaml", "outbound:\n  - name: web\n    description: For the test.\n    to: [api.example.com, 203.0.113.10]\n    ports: [https]\n")
        self.assertIn("web", build(self.repo, basic, VENDING, GITHUB)["policies"]["norwayeast"]["platform"]["application"]["platform-application"]["rules"])

    def test_targets_that_are_not_strings_are_problems(self):
        self.write("firewall/landing-zones/payments-prod.yaml",
                   "outbound:\n  - name: odd\n    description: For the test.\n    to: [true, null, 8080]\n    ports: [https]\n")
        problems = self.problems()
        for value in ("True", "None", "8080"):
            self.assertIn(f"{value} is not a name, an address or range", problems)

    def test_priorities_count_only_the_hub_s_own_landing_zones(self):
        number = partition("payments-prod")
        other = next(f"aa-{i}" for i in range(10_000) if partition(f"aa-{i}") == number and f"aa-{i}" < "payments-prod")
        platform = {**PLATFORM, "hubs": {**PLATFORM["hubs"], "swedencentral": {"names": names("sdc"), "landing_zone_ranges": ["10.200.0.0/16"]}}}
        vending = {"landing_zones": {**VENDING["landing_zones"], other: {"region": "swedencentral", "range": "10.200.4.0/24"}}}
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [203.0.113.10]\n    ports: [443]\n")
        before = build(self.repo, platform, vending, GITHUB)["policies"]["norwayeast"][f"lz-{number:02d}"]
        self.write(f"firewall/landing-zones/{other}.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [203.0.113.11]\n    ports: [443]\n")
        after = build(self.repo, platform, vending, GITHUB)["policies"]["norwayeast"][f"lz-{number:02d}"]
        self.assertEqual(after, before)  # a zone in another hub, sorting first in the same partition, moves nothing here

    def test_landing_zone_rules_wait_for_the_vending_contract(self):
        # The platform's first deployment: rules files exist, vending has published nothing yet.
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [203.0.113.10]\n    ports: [443]\n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(build(self.repo, PLATFORM, None, GITHUB))  # checked, and no config anyone could plan with
        self.assertIn("no vending contract yet (_generated/vending.yaml): the rules for payments-prod are checked, and wait for it", stderr.getvalue())
        # Once the contract exists, a key it does not name yet waits too: its rules are merged before the landing
        # zone exists, and the run after vending publishes it applies them.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            config = build(self.repo, PLATFORM, {"landing_zones": {}}, GITHUB)
        self.assertNotIn("lz-", "".join(config["policies"]["norwayeast"]))
        self.assertIn("firewall/landing-zones/payments-prod.yaml is for payments-prod, which the vending contract does not list yet: "
                      "its rules are checked, and wait until vending publishes it", stderr.getvalue())
        self.assertIn("lz-", "".join(build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]))

    def test_a_waiting_landing_zone_s_rules_go_only_to_what_exists(self):
        # Its own landing zone may come later; a destination may not.
        self.write("firewall/landing-zones/billing-prod.yaml",
                   "outbound:\n  - name: to-nowhere\n    description: For the test.\n    to: [ledger-prod]\n    ports: [443]\n")
        with self.assertRaisesRegex(ConfigError, "billing-prod: outbound\\[0\\]: ledger-prod is no landing zone in the vending contract"):
            build(self.repo, PLATFORM, VENDING, GITHUB)

    def test_every_rule_says_what_it_is_for(self):
        self.write("firewall/platform.yaml", (
            "outbound:\n"
            "  - name: no-reason\n    to: [api.example.com]\n    ports: [https]\n"
            "  - name: empty-reason\n    description: ' '\n    to: [api.example.com]\n    ports: [https]\n"
        ))
        message = self.problems()
        self.assertIn("firewall/platform: outbound[0]: a rule has name, description, to and ports.", message)
        self.assertIn("firewall/platform: outbound[1]: description says what the rule is for, for whoever approves it.", message)

    def test_no_rule_reaches_the_whole_internet(self):
        self.write("firewall/platform.yaml", (
            "outbound:\n"
            "  - name: everything\n    description: For the test.\n    to: ['*', Internet, INTERNET, InterNet]\n    ports: [https]\n"
            "  - name: everywhere\n    description: For the test.\n    to: [0.0.0.0/0, 128.0.0.0/1]\n    ports: [443]\n"
            "  - name: private\n    description: For the test.\n    to: [10.0.0.0/8]\n    ports: [443]\n"
        ))
        message = self.problems()
        self.assertIn("outbound[0]: * is the whole internet", message)
        for tag in ("Internet", "INTERNET", "InterNet"):  # in any case, as Azure reads it
            self.assertIn(f"outbound[0]: {tag} is the whole internet", message)
        self.assertIn("outbound[1]: 0.0.0.0/0 is wider than a /8", message)
        self.assertIn("outbound[1]: 128.0.0.0/1 is wider than a /8", message)
        self.assertNotIn("outbound[2]", message)  # a /8 is as wide as a rule goes

    def test_waiting_rules_are_still_checked(self):
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [not a host]\n    ports: [ssh]\n")
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, PLATFORM, None, GITHUB)
        message = str(caught.exception)
        self.assertIn("firewall/landing-zones/payments-prod: outbound[0]: 'not a host' is not a name", message)
        self.assertIn("port 'ssh' is https, http", message)

    def test_a_shared_rule_to_a_landing_zone_waits_for_the_contract_too(self):
        self.write("firewall/platform.yaml", "outbound:\n  - name: to-website\n    description: For the test.\n    to: [website-prod, 10.0.0.5]\n    ports: [443]\n")
        self.assertIsNone(build(self.repo, PLATFORM, None, GITHUB))
        with self.assertRaisesRegex(ConfigError, "website-prod is no landing zone in the vending contract"):
            build(self.repo, PLATFORM, {"landing_zones": {}}, GITHUB)
        resolved = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]["platform"]["network"]["platform-network"]["rules"]["to-website"]
        self.assertIn(VENDING["landing_zones"]["website-prod"]["range"], resolved["destination_addresses"])

    def test_a_waiting_landing_zone_target_still_counts_as_an_address(self):
        # An FQDN tag next to a landing zone is refused with or without the contract.
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: updates\n    description: For the test.\n    to: [WindowsUpdate, website-prod]\n    ports: [https]\n")
        with self.assertRaisesRegex(ConfigError, "an FQDN tag such as WindowsUpdate takes only https and http"):
            build(self.repo, PLATFORM, None, GITHUB)

    def test_waiting_rules_meet_basic_limits_when_every_hub_is_basic(self):
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: sftp\n    description: For the test.\n    to: [sftp.example.com]\n    ports: [22]\n")
        basic = {**PLATFORM, "hubs": {"norwayeast": {**PLATFORM["hubs"]["norwayeast"], "firewall": {"sku_tier": "Basic"}}}}
        with self.assertRaisesRegex(ConfigError, "a Basic firewall reaches names only on https and http"):
            build(self.repo, basic, None, GITHUB)
        mixed = {**PLATFORM, "hubs": {**basic["hubs"], "swedencentral": {"names": names("sdc"), "landing_zone_ranges": ["10.200.0.0/16"], "firewall": {"sku_tier": "Standard"}}}}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(build(self.repo, mixed, None, GITHUB))  # the zone's hub is not known yet: the tier check waits

    def test_without_a_contract_the_command_writes_nothing(self):
        out = self.repo / ".mlz" / "firewall.json"
        out.parent.mkdir()
        out.write_text("{}")  # an older config must not stay behind to be planned with
        platform_config = self.repo / ".mlz" / "platform.json"
        platform_config.write_text(json.dumps(PLATFORM))
        run = subprocess.run([sys.executable, "-m", "mlz", "firewall", "--repo", str(self.repo), "--platform-config", str(platform_config), "--out", str(out), "--github-meta", str(self.meta())],
                             capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(out.exists())
        self.assertIn("checked; nothing written until vending's contract", run.stdout)

    def test_without_a_contract_a_failed_check_leaves_no_config_behind(self):
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [not a host]\n    ports: [443]\n")
        out = self.repo / ".mlz" / "firewall.json"
        out.parent.mkdir()
        out.write_text("{}")
        platform_config = self.repo / ".mlz" / "platform.json"
        platform_config.write_text(json.dumps(PLATFORM))
        run = subprocess.run([sys.executable, "-m", "mlz", "firewall", "--repo", str(self.repo), "--platform-config", str(platform_config), "--out", str(out), "--github-meta", str(self.meta())],
                             capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        self.assertEqual(run.returncode, 1)
        self.assertFalse(out.exists())

    def test_a_held_landing_zone_s_rules_wait_to_be_deleted_and_apply_no_more(self):
        self.write("firewall/landing-zones/payments-prod.yaml", "outbound:\n  - name: api\n    description: For the test.\n    to: [203.0.113.10]\n    ports: [443]\n")
        held = {"landing_zones": {"website-prod": VENDING["landing_zones"]["website-prod"]}, "held": {"payments-prod": "10.100.4.0/24"}}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            groups = build(self.repo, PLATFORM, held, GITHUB)["policies"]["norwayeast"]
        self.assertEqual(list(groups), ["platform"])  # its partition group is gone, so are its rules
        self.assertIn("firewall/landing-zones/payments-prod.yaml is for payments-prod, decommissioned or removed: its rules are no longer applied; delete the file", stderr.getvalue())

    def test_a_rule_that_still_names_a_held_landing_zone_stops_the_check(self):
        self.write("firewall/platform.yaml", "outbound:\n  - name: to-payments\n    description: For the test.\n    to: [payments-prod]\n    ports: [443]\n")
        held = {"landing_zones": {}, "held": {"payments-prod": "10.100.4.0/24"}}
        with self.assertRaisesRegex(ConfigError, "firewall/platform: outbound\\[0\\]: payments-prod is decommissioned or removed, and its range is no longer its own; take it out of this rule."):
            build(self.repo, PLATFORM, held, GITHUB)

    def test_without_rule_files_only_the_required_rules(self):
        groups = build(self.repo, PLATFORM, {"landing_zones": {}}, GITHUB)["policies"]["norwayeast"]  # vending published, no zones yet
        self.assertEqual(list(groups), ["platform"])
        self.assertEqual(set(groups["platform"]["application"]), {"runners-application"})
        self.assertEqual(set(groups["platform"]["network"]), {"required-network", "runners-network"})
        collection = groups["platform"]["network"]["required-network"]
        rule = collection["rules"]["azure-monitor-agent"]
        self.assertEqual(collection["priority"], 100)  # before the customer's platform collections, at 200
        # Every landing zone, and the platform's own networks in this region: management's here.
        self.assertEqual(rule["source_addresses"], ["10.100.0.0/16", "10.99.4.0/24"])
        self.assertEqual(rule["destination_addresses"], ["AzureMonitor", "AzureResourceManager"])
        self.assertEqual((rule["protocols"], rule["destination_ports"]), (["TCP"], ["443"]))

    def test_the_runners_reach_github_azure_and_terraform_from_their_subnets(self):
        group = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]["platform"]
        application = group["application"]["runners-application"]
        self.assertEqual(application["priority"], 115)  # after the required collections, before the customer's
        github = application["rules"]["github-actions"]
        self.assertEqual(github["source_addresses"], ["10.99.4.0/25", "10.99.4.128/26"])  # GitHub-hosted and self-hosted
        self.assertEqual(github["destination_fqdns"], sorted(GITHUB))
        self.assertEqual(github["protocols"], [{"type": "Https", "port": 443}])
        self.assertEqual(application["rules"]["terraform"]["destination_fqdns"], ["registry.terraform.io", "releases.hashicorp.com"])
        azure = group["network"]["runners-network"]["rules"]["azure"]
        self.assertEqual(azure["destination_addresses"], ["AzureActiveDirectory", "AzureResourceManager"])
        self.assertEqual((azure["source_addresses"], azure["destination_ports"]), (["10.99.4.0/25", "10.99.4.128/26"], ["443"]))

    def test_the_runners_rules_are_only_where_management_is(self):
        platform = {**PLATFORM, "hubs": {**PLATFORM["hubs"], "swedencentral": {"names": names("sdc"), "landing_zone_ranges": ["10.200.0.0/16"]}}}
        policies = build(self.repo, platform, VENDING, GITHUB)["policies"]
        self.assertIn("runners-application", policies["norwayeast"]["platform"]["application"])
        self.assertEqual(policies["swedencentral"]["platform"]["application"], {})
        rule = policies["swedencentral"]["platform"]["network"]["required-network"]["rules"]["azure-monitor-agent"]
        self.assertEqual(rule["source_addresses"], ["10.200.0.0/16"])  # no platform network there

    def test_management_s_rules_come_from_its_range(self):
        self.write("firewall/management.yaml", (
            "outbound:\n"
            "  - name: payments-db\n    description: For the test.\n    to: [payments-prod]\n    ports: [1433]\n"
            "  - name: packages\n    description: For the test.\n    to: [pypi.org]\n    ports: [https]\n"
        ))
        group = build(self.repo, PLATFORM, VENDING, GITHUB)["policies"]["norwayeast"]["platform"]
        network = group["network"]["management-network"]
        self.assertEqual(network["priority"], 300)
        self.assertEqual(network["rules"]["payments-db"]["source_addresses"], ["10.99.4.0/24"])
        self.assertEqual(network["rules"]["payments-db"]["destination_addresses"], ["10.100.4.0/24"])
        self.assertEqual(group["application"]["management-application"]["rules"]["packages"]["source_addresses"], ["10.99.4.0/24"])

    def test_identity_s_rules_go_to_every_region_that_builds_it(self):
        platform = {
            **PLATFORM,
            "hubs": {**PLATFORM["hubs"], "swedencentral": {"names": names("sdc"), "landing_zone_ranges": ["10.200.0.0/16"]}},
            "networks": {**PLATFORM["networks"], "identity": {"norwayeast": IDENTITY, "swedencentral": {"address_space": "10.199.5.0/24"}}},
        }
        self.write("firewall/identity.yaml", "outbound:\n  - name: ntp\n    description: For the test.\n    to: [time.windows.com]\n    ports: ['udp:123']\n")
        policies = build(self.repo, platform, VENDING, GITHUB)["policies"]
        for region, space in (("norwayeast", "10.99.5.0/24"), ("swedencentral", "10.199.5.0/24")):
            group = policies[region]["platform"]
            self.assertEqual(group["network"]["identity-network"]["priority"], 400)
            self.assertEqual(group["network"]["identity-network"]["rules"]["ntp-udp"]["source_addresses"], [space])
            self.assertIn(space, group["network"]["required-network"]["rules"]["azure-monitor-agent"]["source_addresses"])

    def test_identity_s_rules_need_identity_s_network(self):
        self.write("firewall/identity.yaml", "outbound:\n  - name: ntp\n    description: For the test.\n    to: [time.windows.com]\n    ports: ['udp:123']\n")
        self.assertIn("firewall/identity: no hub builds identity's network (identity: true in hubs/<region>.yaml)", self.problems())
        self.write("firewall/identity.yaml", "# outbound: commented out until identity is built\n")
        build(self.repo, PLATFORM, VENDING, GITHUB)  # no rules, no source needed

    def test_management_s_rules_are_checked_like_any_other(self):
        self.write("firewall/management.yaml", "outbound:\n  - name: odd\n    description: For the test.\n    to: [not a host]\n    ports: [https]\n")
        self.assertIn("firewall/management: outbound[0]: 'not a host' is not a name", self.problems())

    def test_github_s_names_under_a_listed_wildcard_are_left_out(self):
        listed = ["*.actions.githubusercontent.com", "pipelinesghubeus1.actions.githubusercontent.com",
                  "results-receiver.actions.githubusercontent.com", "a.b.actions.githubusercontent.com",
                  "github.com", "*.github.com", "productionresultssa0.blob.core.windows.net", "Ghcr.io"]
        self.assertEqual(github_actions_domains(self.meta(listed)), [
            "*.actions.githubusercontent.com", "*.github.com", "a.b.actions.githubusercontent.com",
            "ghcr.io", "github.com", "productionresultssa0.blob.core.windows.net",
        ])

    def test_github_s_list_is_never_empty_or_odd(self):
        for actions in ([], ["github.com", "not a host"], "github.com", [None]):
            with self.assertRaisesRegex(ConfigError, "domains.actions is not a list of names"):
                github_actions_domains(self.meta(actions))
        broken = Path(self.tmp.name) / "broken.json"
        broken.write_text("{}")
        with self.assertRaisesRegex(ConfigError, "GitHub's Actions domains could not be read from .*broken.json: 'domains'"):
            github_actions_domains(broken)


if __name__ == "__main__":
    unittest.main()
