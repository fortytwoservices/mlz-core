"""Tests for render_library.py. Run with: python3 -m unittest discover governance/scripts"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import render_library as rl

DEFAULTS = rl.GOVERNANCE / "defaults.yaml"


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def customer(self, text: str) -> Path:
        path = self.dir / "customer.yaml"
        path.write_text(text)
        return path

    def render(self, text: str) -> tuple[dict, Path]:
        out = self.dir / "out"
        config = rl.render(self.customer(text), DEFAULTS, out)
        return config, out

    def read(self, out: Path, relative: str):
        return json.loads((out / relative).read_text())

    BASE = "root_id: demo\nroot_display_name: Demo\nprimary_region: norwayeast\n"

    def test_management_groups_keep_the_old_fortytwo_ids(self):
        _, out = self.render(self.BASE)
        groups = self.read(out, "architecture_definitions/demo.alz_architecture_definition.json")["management_groups"]
        ids = {g["id"]: (g["parent_id"], g["archetypes"]) for g in groups}
        self.assertEqual(ids["demo"], (None, ["root"]))
        self.assertEqual(ids["demo-platform"], ("demo", ["fortytwo_platform"]))
        self.assertEqual(ids["demo-connectivity"], ("demo-platform", ["fortytwo_connectivity"]))
        self.assertEqual(ids["demo-landing-zones"], ("demo", ["fortytwo_landing_zones"]))
        self.assertEqual(ids["demo-lz-corp"], ("demo-landing-zones", ["corp"]))
        self.assertEqual(ids["demo-lz-online"], ("demo-landing-zones", ["online"]))
        self.assertEqual(ids["demo-sandboxes"], ("demo", ["sandbox"]))
        self.assertEqual(ids["demo-unusedsub"], ("demo", ["fortytwo_empty"]))
        self.assertIn("demo-security", ids)
        self.assertNotIn("demo-lz-local", ids)
        self.assertNotIn("demo-lz-dataplatform", ids)

    def test_a_customer_can_turn_on_one_optional_group_alone(self):
        config, out = self.render(self.BASE + "management_groups:\n  local: true\n")
        groups = self.read(out, "architecture_definitions/demo.alz_architecture_definition.json")["management_groups"]
        ids = [g["id"] for g in groups]
        self.assertIn("demo-lz-local", ids)
        self.assertIn("demo-security", ids)  # still on from the defaults
        self.assertTrue(ids.index("demo-lz-local") > ids.index("demo-landing-zones"))

    def test_overrides_remove_attestation_and_ddos_and_add_regions_and_tags(self):
        _, out = self.render(self.BASE)
        lz = self.read(out, "archetype_overrides/fortytwo_landing_zones.alz_archetype_override.json")
        self.assertEqual(lz["base_archetype"], "landing_zones")
        self.assertEqual(sorted(lz["policy_assignments_to_remove"]), ["Deploy-GuestAttest", "Enable-DDoS-VNET"])
        self.assertEqual(
            lz["policy_assignments_to_add"],
            [
                "Deny-Resource-Locations",
                "Deny-RSG-Locations",
                "Require-Tag-Owner",
                "Require-Tag-CostCenter",
                "Inherit-Tag-Owner",
                "Inherit-Tag-CostCenter",
                "Inherit-Tag-Environment",
                "Inherit-Tag-Workload",
            ],
        )
        platform = self.read(out, "archetype_overrides/fortytwo_platform.alz_archetype_override.json")
        self.assertEqual(platform["policy_assignments_to_remove"], ["Deploy-GuestAttest"])
        connectivity = self.read(out, "archetype_overrides/fortytwo_connectivity.alz_archetype_override.json")
        self.assertEqual(connectivity["policy_assignments_to_remove"], ["Enable-DDoS-VNET"])

    def test_tag_assignments(self):
        _, out = self.render(self.BASE)
        require = self.read(out, "policy_assignments/Require-Tag-Owner.alz_policy_assignment.json")
        self.assertEqual(require["properties"]["parameters"], {"tagName": {"value": "Owner"}})
        self.assertNotIn("identity", require)
        inherit = self.read(out, "policy_assignments/Inherit-Tag-Workload.alz_policy_assignment.json")
        self.assertEqual(inherit["identity"], {"type": "SystemAssigned"})
        self.assertEqual(inherit["properties"]["policyDefinitionId"], rl.INHERIT_TAG_DEFINITION)
        for path in (out / "policy_assignments").iterdir():
            self.assertLessEqual(len(json.loads(path.read_text())["name"]), 24, path.name)

    def test_customer_values_replace_lists_and_merge_mappings(self):
        config, _ = self.render(self.BASE + "allowed_regions: [norwayeast]\ntags:\n  required: [Owner]\n")
        self.assertEqual(config["allowed_regions"], ["norwayeast"])
        self.assertEqual(config["tags"]["required"], ["Owner"])
        self.assertEqual(config["tags"]["inherited"], ["Owner", "CostCenter", "Environment", "Workload"])

    def test_rendering_twice_leaves_no_stale_files(self):
        self.render(self.BASE)
        _, out = self.render(self.BASE + "tags:\n  required: [Owner]\n")
        self.assertFalse((out / "policy_assignments/Require-Tag-CostCenter.alz_policy_assignment.json").exists())

    def test_invalid_settings_fail_with_every_problem(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(
                "root_id: Demo_1\nroot_display_name: Demo\nprimary_region: eastus\n"
                "tags:\n  required: [DataClassification]\n"
            )
        message = str(caught.exception)
        self.assertIn("root_id 'Demo_1'", message)
        self.assertIn("primary_region 'eastus' is not in allowed_regions", message)
        self.assertIn("'DataClassification' must be 1 to 12", message)

    def test_missing_required_settings(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render("name: nothing else\n")
        for key in ("root_id", "root_display_name", "primary_region"):
            self.assertIn(f"{key} is required", str(caught.exception))

    def test_the_starter_customer_renders(self):
        starter = rl.GOVERNANCE.parent / "starter" / "mlz-governance" / "customer.yaml"
        config = rl.render(starter, DEFAULTS, self.dir / "starter")
        self.assertEqual(config["root_id"], "demo")


if __name__ == "__main__":
    unittest.main()
