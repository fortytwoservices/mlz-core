"""Tests for the JSON Schemas (the committed files are what schema.py writes, and each schema has exactly the
settings its loader takes) and for the starters' example files, read by the loaders themselves."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from mlz import firewall, install, platform, schema, vending
from mlz.files import read_setting
from mlz.merge import ConfigError
from mlz.test_platform import GOVERNANCE as PLATFORM_GOVERNANCE
from mlz.test_vending import GOVERNANCE, PLATFORM

SCHEMAS = schema.CORE / "schemas"
STARTERS = schema.CORE / "starter"


def committed(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


class SchemaTest(unittest.TestCase):
    def test_the_committed_schemas_are_what_schema_py_writes(self):
        written = schema.render()
        self.assertEqual(sorted(p.name for p in SCHEMAS.glob("*.json")), sorted(written))
        for name, text in written.items():
            self.assertEqual((SCHEMAS / name).read_text(), text, f"schemas/{name} is out of date: run python3 -m mlz schema --out schemas")

    def test_each_schema_has_exactly_the_settings_its_loader_takes(self):
        zone = committed("landing-zone.schema.json")["properties"]
        self.assertEqual(set(zone), vending.KEYS)
        for section, keys in vending.SECTIONS.items():
            self.assertEqual(set(zone[section]["properties"]), keys, section)
        self.assertEqual(set(committed("hub.schema.json")["properties"]), platform.HUB_KEYS)
        rule = committed("firewall.schema.json")["properties"]["outbound"]["items"]
        self.assertEqual(set(rule["properties"]), set(firewall.RULE_KEYS))
        rules = schema.governance_rules()
        customer = committed("customer.schema.json")["properties"]
        self.assertEqual(set(customer), set(rules.SETTINGS))
        self.assertEqual(set(customer["subscriptions"]["properties"]), set(rules.PLATFORM_ROLES))
        self.assertEqual(set(customer["management_groups"]["properties"]), set(rules.OPTIONAL_GROUPS))
        self.assertEqual(set(customer["tags"]["properties"]), set(rules.TAG_KEYS))
        boot = committed("bootstrap.schema.json")["properties"]
        self.assertEqual(set(boot), set(install.BOOTSTRAP_SETTINGS))
        self.assertEqual(set(boot["github"]["properties"]), set(install.GITHUB_SETTINGS))

    def test_every_pattern_is_one_json_schema_reads(self):
        # ECMAScript has no inline flags such as (?i), and no possessive or lookbehind syntax Python has.
        def patterns(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "pattern":
                        yield value
                    yield from patterns(value)
            elif isinstance(node, list):
                for value in node:
                    yield from patterns(value)

        for name in schema.SCHEMAS:
            for found in patterns(committed(name)):
                self.assertNotIn("(?", found, f"{name}: {found}")
                re.compile(found)

    def test_each_starter_points_its_files_at_the_schemas_of_this_release(self):
        release = install.release()
        expected = {
            "governance": {"customer.schema.json", "bootstrap.schema.json"},
            "platform": {"hub.schema.json", "firewall.schema.json"},
            "vending": {"landing-zone.schema.json"},
        }
        for role, names in expected.items():
            text = (STARTERS / f"mlz-{role}" / ".vscode" / "settings.json").read_text()
            urls = re.findall(r'"(https://[^"]+)"', text)
            self.assertEqual({url.rsplit("/", 1)[1] for url in urls}, names, role)
            for url in urls:
                self.assertTrue(url.startswith(f"https://raw.githubusercontent.com/fortytwoservices/mlz-core/{release}/schemas/"), url)


class StarterTest(unittest.TestCase):
    """Nothing in a starter deploys as it comes: the examples are commented out or in a folder vending skips."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def copy(self, role: str) -> Path:
        shutil.copytree(STARTERS / f"mlz-{role}", self.repo, dirs_exist_ok=True)
        return self.repo

    def test_the_platform_starters_hub_stops_the_check_until_it_is_filled_in(self):
        with self.assertRaises(ConfigError) as caught:
            platform.build(self.copy("platform"), PLATFORM_GOVERNANCE)
        self.assertIn("hubs/norwayeast: platform_address_space is required", str(caught.exception))
        self.assertIn("hubs/norwayeast: landing_zone_ranges is required", str(caught.exception))

    def test_the_platform_starters_rules_file_holds_no_rules(self):
        problems: list[str] = []
        rules = firewall.read_rules("firewall/platform", read_setting(self.copy("platform") / "firewall" / "platform") or {}, problems)
        self.assertEqual((rules, problems), ([], []))

    def test_the_vending_starter_has_no_landing_zone_and_its_example_is_a_valid_one(self):
        repo = self.copy("vending")
        self.assertEqual(vending.landing_zones(repo), {})  # _examples/ is skipped
        shutil.copytree(repo / "landing-zones" / "_examples" / "payments", repo / "landing-zones" / "payments")
        plan, _, retired = vending.allocate(repo, PLATFORM)
        (repo / vending.IP_PLAN).parent.mkdir(parents=True, exist_ok=True)
        (repo / vending.IP_PLAN).write_text(vending.render_plan(plan, retired))
        (repo / vending.NAMES).write_text(vending.render_names(vending.give_names(repo, plan, GOVERNANCE)[0]))
        config = vending.build(repo, GOVERNANCE, PLATFORM)["payments-prod"]
        self.assertEqual(config["management_group_id"], "demo-lz-corp")
        self.assertEqual(config["names"]["virtual_network"], "vnet-demo-pay-pd-nwe-01")
        self.assertEqual(set(config["network"]["subnets"]), {"app", "data"})


if __name__ == "__main__":
    unittest.main()
