"""Tests for the loader's combining rules. Run from the repository root: python3 -m unittest discover -s tools"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz.files import read_setting, setting_names
from mlz.merge import ConfigError, combine_chunks, layer


class LayerTest(unittest.TestCase):
    def test_mappings_merge_and_the_later_layer_wins(self):
        base = {"budget": {"monthly": 500, "contacts": ["a@x.example"]}, "location": "norwayeast"}
        later = {"budget": {"monthly": 2000}, "location": "swedencentral"}
        self.assertEqual(
            layer(base, later),
            {"budget": {"monthly": 2000, "contacts": ["a@x.example"]}, "location": "swedencentral"},
        )

    def test_lists_are_replaced_not_appended(self):
        self.assertEqual(layer({"tags": ["a", "b"]}, {"tags": ["c"]}), {"tags": ["c"]})

    def test_the_earlier_layer_is_not_changed(self):
        base = {"network": {"size": 24}}
        layer(base, {"network": {"size": 25}})
        self.assertEqual(base, {"network": {"size": 24}})


class ChunkTest(unittest.TestCase):
    def test_chunks_combine_into_one_setting(self):
        merged = combine_chunks([
            (Path("network.yaml"), {"address_space": "10.0.0.0/22", "firewall": {"sku": "Standard"}}),
            (Path("firewall.yaml"), {"firewall": {"dns_proxy": True}}),
        ])
        self.assertEqual(merged, {"address_space": "10.0.0.0/22", "firewall": {"sku": "Standard", "dns_proxy": True}})

    def test_a_value_set_in_two_chunks_names_both_files(self):
        with self.assertRaises(ConfigError) as caught:
            combine_chunks([
                (Path("a.yaml"), {"firewall": {"sku": "Standard"}}),
                (Path("b.yaml"), {"firewall": {"sku": "Premium"}}),
            ])
        self.assertIn("firewall.sku is set in both a.yaml and b.yaml", str(caught.exception))

    def test_a_mapping_and_a_value_for_the_same_key_conflict(self):
        with self.assertRaises(ConfigError) as caught:
            combine_chunks([(Path("a.yaml"), {"bastion": {"sku": "Basic"}}), (Path("b.yaml"), {"bastion": False})])
        self.assertIn("bastion is set in both a.yaml and b.yaml", str(caught.exception))


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_file_or_a_folder_of_chunks_reads_the_same(self):
        (self.dir / "one.yaml").write_text("address_space: 10.0.0.0/22\nfirewall: Standard\n")
        (self.dir / "two").mkdir()
        (self.dir / "two/network.yaml").write_text("address_space: 10.0.0.0/22\n")
        (self.dir / "two/firewall.yaml").write_text("firewall: Standard\n")
        self.assertEqual(read_setting(self.dir / "one"), read_setting(self.dir / "two"))

    def test_a_file_and_a_folder_with_the_same_name_is_an_error(self):
        (self.dir / "hub.yaml").write_text("a: 1\n")
        (self.dir / "hub").mkdir()
        (self.dir / "hub/b.yaml").write_text("b: 2\n")
        with self.assertRaises(ConfigError) as caught:
            read_setting(self.dir / "hub")
        self.assertIn("keep one", str(caught.exception))

    def test_names_skip_underscore_and_hidden_entries(self):
        for name in ("norwayeast.yaml", "_defaults.yaml", ".hidden.yaml"):
            (self.dir / name).write_text("{}\n")
        (self.dir / "swedencentral").mkdir()
        self.assertEqual(setting_names(self.dir), ["norwayeast", "swedencentral"])

    def test_an_empty_file_reads_as_no_settings(self):
        (self.dir / "empty.yaml").write_text("# only a comment\n")
        self.assertEqual(read_setting(self.dir / "empty"), {})


if __name__ == "__main__":
    unittest.main()
