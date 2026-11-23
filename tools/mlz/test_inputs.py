"""Tests for the input maps (tools/mlz/inputs.py): every input of every pinned module has exactly one entry, every
setting names a key a loader takes, the snapshot matches the roots' pins, and the committed reference pages are what
the maps write."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from mlz import inputs

REFERENCE = inputs.CORE / "docs" / "reference"

MODULE = '''
# A comment with a brace {, and a string with one: "}"
variable "name" {
  type        = string
  description = <<-EOT
    A heredoc with } and ${var.x} and "quotes".
  EOT
}

variable "script" {
  type    = string
  default = <<-EOT
    echo one
  EOT
}

variable "hub" {
  type = map(object({
    location = string # a comment } inside the type
    firewall = optional(object({
      sku_tier = optional(string, "Standard")
      zones    = optional(list(string))
      policy = optional(object({
        dns_servers = optional(list(string), ["${var.prefix}.4", "}"])
      }), {})
    }))
    pair     = optional(tuple([string, number]))
    "quoted" : optional(number, 1)
  }))
  validation {
    condition     = alltrue([for h in var.hub : h.location != "{"])
    error_message = "A message with { and }."
  }
}

variable "tags" { type = map(string)
  default = null }
variable "anything" {}
variable "pair" {
  type    = tuple([string, number])
  default = ["a", 1]
}
'''


class ReadTest(unittest.TestCase):
    def read(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "variables.tf").write_text(text)
            return inputs.variables(Path(folder))

    def test_a_module_s_variables_become_paths_with_their_type_and_default(self):
        found = self.read(MODULE)
        self.assertEqual(list(found), [
            "name", "script", "hub", "hub.location", "hub.firewall", "hub.firewall.sku_tier", "hub.firewall.zones",
            "hub.firewall.policy", "hub.firewall.policy.dns_servers", "hub.pair", "hub.quoted", "tags", "anything", "pair"])
        self.assertEqual(found["name"], {"type": "string", "required": True})
        self.assertEqual(found["script"]["default"], "<<-EOT\n    echo one\n  EOT")  # whole, so a change to it shows
        self.assertEqual(found["hub"], {"type": "map(object)", "required": True})
        self.assertEqual(found["hub.firewall"], {"type": "object", "required": False, "default": None})
        self.assertEqual(found["hub.firewall.sku_tier"], {"type": "string", "required": False, "default": '"Standard"'})
        self.assertEqual(found["hub.firewall.zones"]["type"], "list(string)")
        self.assertEqual(found["hub.firewall.policy"]["default"], "{}")
        self.assertEqual(found["hub.firewall.policy.dns_servers"]["default"], '["${var.prefix}.4", "}"]')
        self.assertEqual(found["hub.quoted"], {"type": "number", "required": False, "default": "1"})
        self.assertEqual(found["tags"], {"type": "map(string)", "required": False, "default": "null"})
        self.assertEqual(found["anything"], {"type": "any", "required": True})
        self.assertEqual(found["pair"], {"type": "tuple", "required": False, "default": '["a", 1]'})

    def test_a_module_s_own_variables_file_comes_first(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "variables.azapi.tf").write_text('variable "retry" {\n  type = any\n}\n')
            (Path(folder) / "variables.tf").write_text('variable "name" {\n  type = string\n}\n')
            self.assertEqual(list(inputs.variables(Path(folder))), ["name", "retry"])

    def test_an_unclosed_string_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ValueError):
            self.read('variable "x" {\n  default = "open\n}\n')


class MapTest(unittest.TestCase):
    snapshot = inputs.load_snapshot()
    maps = inputs.load_maps()

    def test_the_snapshot_has_every_root_and_the_module_calls_each_root_pins(self):
        self.assertEqual(list(self.snapshot), list(inputs.ROOTS))
        for root in inputs.ROOTS:
            self.assertEqual(inputs.snapshot_pins(self.snapshot, root), inputs.pins(inputs.CORE / root),
                             f"{root}: a pin moved; run python3 -m mlz inputs --refresh")

    def test_the_snapshot_is_written_as_snapshot_text_writes_it(self):
        self.assertEqual(inputs.SNAPSHOT.read_text(), inputs.snapshot_text(self.snapshot))

    def test_every_input_of_every_module_call_has_exactly_one_entry_and_every_setting_a_key(self):
        self.assertEqual(inputs.problems(self.snapshot, self.maps), [])

    def test_the_committed_pages_are_what_the_maps_write(self):
        written = inputs.render(self.snapshot, self.maps)
        self.assertEqual(sorted(p.name for p in REFERENCE.glob("*.md")), sorted(written))
        for name, text in written.items():
            self.assertEqual((REFERENCE / name).read_text(), text, f"docs/reference/{name} is out of date: run python3 -m mlz reference --out docs/reference")

    def test_the_readme_points_at_the_pages(self):
        self.assertIn("docs/reference/", (inputs.CORE / "README.md").read_text())


class ProblemTest(unittest.TestCase):
    """Each kind of mistake in a map, on a copy of the connectivity root's."""

    def setUp(self):
        self.snapshot = inputs.load_snapshot()
        self.maps = copy.deepcopy(inputs.load_maps())
        self.hub = self.maps["platform/terraform"]["connectivity"]["inputs"]

    def found(self) -> list[str]:
        return [p.removeprefix("platform/terraform/inputs.yaml: connectivity: ") for p in inputs.problems(self.snapshot, self.maps)]

    def test_an_input_without_an_entry(self):
        del self.hub["hub_virtual_networks.bastion.sku"]
        self.assertEqual(self.found(), ["hub_virtual_networks.bastion.sku has no entry; it needs exactly one"])

    def test_an_entry_over_another(self):
        self.hub["hub_virtual_networks.bastion"] = "open"
        self.assertIn("hub_virtual_networks.bastion.sku has 2 entries; it needs exactly one", self.found())

    def test_an_entry_for_no_input(self):
        self.hub["hub_virtual_networks.bastion.colour"] = "open"
        self.assertEqual(self.found(), ["hub_virtual_networks.bastion.colour: no such input in the module"])

    def test_a_setting_no_loader_takes(self):
        self.hub["hub_virtual_networks.firewall.sku_tier"] = {"class": "setting", "key": "firewall_tier"}
        self.assertEqual(self.found(), ["hub_virtual_networks.firewall.sku_tier: `connectivity/<region>.yaml` takes no firewall_tier"])

    def test_a_names_override_no_loader_takes(self):
        self.hub["hub_virtual_networks.name"]["names"] = "network"
        self.assertEqual(self.found(), ["hub_virtual_networks.name: `connectivity/<region>.yaml` takes no names.network"])

    def test_a_class_without_what_it_needs_or_with_what_it_does_not_take(self):
        self.hub["naming"] = {"class": "fixed"}
        self.hub["retry"] = {"class": "open", "key": "retry"}
        self.hub["ddos_protection_plan_id"] = {"class": "optional"}
        self.assertEqual(sorted(self.found()), ["ddos_protection_plan_id: class 'optional' is not one of setting, derived, fixed, open, closed",
                                                "naming: fixed needs why", "retry: open takes no key"])

    def test_a_field_of_the_wrong_type(self):
        self.hub["hub_virtual_networks.firewall.sku_tier"] = {"class": "setting", "key": None}
        self.hub["hub_virtual_networks.name"] = {"class": "derived", "by": "the template", "names": 7}
        self.hub["naming"] = {"class": "fixed", "why": ""}
        self.assertEqual(sorted(self.found()), ["hub_virtual_networks.firewall.sku_tier: key must be text",
                                                "hub_virtual_networks.name: names must be a key under names:, or a list of them",
                                                "naming: why must be text"])

    def test_an_empty_section_or_one_that_is_not_a_mapping(self):
        self.maps["platform/terraform"]["connectivity"] = None
        self.maps["vending/terraform"]["route_table"]["notes"] = []
        self.maps["vending/terraform"]["vending"]["inputs"] = False
        found = inputs.problems(self.snapshot, self.maps)
        self.assertEqual(found[:2], [
            "platform/terraform/inputs.yaml: connectivity: a section maps builds, file, notes and inputs; this one is empty or not a mapping",
            "vending/terraform/inputs.yaml: route_table: notes maps an input's path to a note"])
        self.assertEqual(found[2:], ["vending/terraform/inputs.yaml: vending: inputs maps each input's path to its entry"])

    def test_a_module_call_without_a_section_and_a_section_without_a_call(self):
        self.maps["platform/terraform"]["hub"] = self.maps["platform/terraform"].pop("connectivity")
        self.assertEqual(inputs.problems(self.snapshot, self.maps), [
            "platform/terraform/inputs.yaml: no section for module.connectivity",
            "platform/terraform/inputs.yaml: hub is no module call of this root"])

    def test_a_setting_s_key_is_found_through_lists_and_maps(self):
        accepted = inputs.schemas()
        self.assertTrue(inputs.accepts(accepted["firewall-rules"], "outbound.to"))
        self.assertTrue(inputs.accepts(accepted["bootstrap"], "github.runners.size"))
        self.assertTrue(inputs.accepts(accepted["landing-zone"], "names.network_security_groups"))
        self.assertTrue(inputs.accepts(accepted["connectivity"], "names.firewall"))
        self.assertFalse(inputs.accepts(accepted["connectivity"], "names.nat_gateway"))
        self.assertFalse(inputs.accepts(accepted["customer"], "primary_region.code"))


if __name__ == "__main__":
    unittest.main()
