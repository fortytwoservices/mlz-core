"""Tests for the YAML writer and the contracts. Every file is read back with terraform's yamldecode."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz.contract import render
from mlz.files import preload, read_yaml
from mlz.merge import ConfigError
from mlz.yamlout import dump, scalar

GOVERNANCE = {
    "root": "mlz",
    "primary_region": "norwayeast",
    "allowed_regions": ["norwayeast", "norwaywest", "westeurope", "northeurope", "swedencentral"],
    "required_tags": ["Owner", "CostCenter"],
    "archetypes": {"corp": "mlz-lz-corp", "online": "mlz-lz-online", "sandbox": "mlz-sandboxes"},
    "platform_subscriptions": {"management": "6d6a6044-7f37-46cd-b5f2-a8a6f75aeffe"},
    "monitoring": {
        "workspace": "/subscriptions/6d6a6044-7f37-46cd-b5f2-a8a6f75aeffe/resourceGroups/rg-mlz-management-nwe"
        "/providers/Microsoft.OperationalInsights/workspaces/law-mlz-management-nwe"
    },
}
PLATFORM = {
    "topology": "hub_and_spoke",
    "regions": {
        "norwayeast": {
            "primary": True,
            "hub_range": ["10.100.0.0/22"],
            "firewall": "10.100.0.4",
            "spoke_routes": [{"name": "default", "to": "0.0.0.0/0", "via": "10.100.0.4"}],
        }
    },
    "private_dns": {"subscription": "a002de69-082a-4f44-b78f-c9e36e209af2", "resource_group": "rg-mlz-dns-nwe", "region": "norwayeast"},
}

BOOTSTRAP = {
    "top_management_group": "mlz",
    "state": {"subscription": "6d6a6044-7f37-46cd-b5f2-a8a6f75aeffe", "resource_group": "rg-mlz-bootstrap-nwe", "storage_account": "stmlzstate1a2b"},
    "identities": {
        "vending": {
            "plan": {"name": "id-mlz-vending-plan-nwe", "client_id": "11111111-1111-1111-1111-111111111111", "principal_id": "22222222-2222-2222-2222-222222222222"},
            "apply": {"name": "id-mlz-vending-apply-nwe", "client_id": "33333333-3333-3333-3333-333333333333", "principal_id": "44444444-4444-4444-4444-444444444444"},
        }
    },
}


class YamlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def round_trip(self, text: str) -> dict:
        path = self.dir / "file.yaml"
        path.write_text(text)
        return read_yaml(path)

    def test_contracts_read_back_exactly(self):
        for kind, data in (("governance", GOVERNANCE), ("platform", PLATFORM), ("bootstrap", BOOTSTRAP)):
            self.assertEqual(self.round_trip(render(kind, data)), data, kind)

    def test_values_yaml_would_misread_are_quoted(self):
        data = {"cost_center": "4711", "flag": "yes", "version": "1.0", "empty": "", "colon": "a: b", "hash": "a #b", "none": None, "on": True}
        self.assertEqual(self.round_trip(dump(data)), data)
        self.assertEqual(scalar("4711"), '"4711"')
        self.assertEqual(scalar("10.100.0.0/22"), "10.100.0.0/22")

    def test_dates_stay_text_as_keys_and_values(self):
        data = {"2026-11-02": {"range": "10.100.4.0/24"}, "root": "2026-1-2", "when": "2026-11-02T10:00:00Z"}
        self.assertEqual(self.round_trip(dump(data)), data)

    def test_binary_looking_text_reads_back_as_text_unquoted(self):
        data = {"0b10": "0b10", "subnet": "0B11"}  # yamldecode has no binary integers
        self.assertEqual(self.round_trip(dump(data)), data)
        self.assertEqual(scalar("0b10"), "0b10")

    def test_a_broken_file_among_many_is_named_with_terraform_s_reason(self):
        good, bad = self.dir / "good.yaml", self.dir / "bad.yaml"
        good.write_text("a: 1\n")
        bad.write_text("a: [1\n")
        preload([good, bad])
        self.assertEqual(read_yaml(good), {"a": 1})
        with self.assertRaises(ConfigError) as caught:
            read_yaml(bad)
        self.assertIn("bad.yaml: not valid YAML", str(caught.exception))
        self.assertIn("yamldecode", str(caught.exception))

    def test_a_path_with_quotes_and_template_marks_is_read(self):
        folder = self.dir / 'a "quoted" ${x} %{y}'
        folder.mkdir()
        (folder / "file.yaml").write_text("a: 1\n")
        self.assertEqual(read_yaml(folder / "file.yaml"), {"a": 1})

    def test_the_governance_contract_reads_like_the_approved_shape(self):
        text = render("governance", GOVERNANCE)
        self.assertTrue(text.startswith("# What governance decided"))
        self.assertIn(
            "# Landing zones may only use these regions.\n"
            "allowed_regions: [norwayeast, norwaywest, westeurope, northeurope, swedencentral]\n",
            text,
        )
        self.assertIn("archetypes:\n  corp: mlz-lz-corp\n", text)

    def test_a_list_of_mappings_is_a_block(self):
        text = render("platform", PLATFORM)
        self.assertIn("    spoke_routes:\n      - name: default\n        to: 0.0.0.0/0\n        via: 10.100.0.4\n", text)


if __name__ == "__main__":
    unittest.main()
