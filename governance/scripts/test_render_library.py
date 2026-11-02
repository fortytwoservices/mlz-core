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

    BASE = (
        "root_id: demo\nroot_display_name: Demo\nprimary_region: norwayeast\n"
        "security_contact: platform-team@demo.example\n"
        "subscriptions:\n"
        "  management: 00000000-0000-0000-0000-000000000001\n"
        "  connectivity: 00000000-0000-0000-0000-000000000002\n"
    )

    def test_management_groups_keep_the_old_fortytwo_ids(self):
        _, out = self.render(self.BASE)
        groups = self.read(out, "architecture_definitions/demo.alz_architecture_definition.json")["management_groups"]
        ids = {g["id"]: (g["parent_id"], g["archetypes"]) for g in groups}
        self.assertEqual(ids["demo"], (None, ["fortytwo_root"]))
        self.assertEqual(ids["demo-platform"], ("demo", ["fortytwo_platform"]))
        self.assertEqual(ids["demo-connectivity"], ("demo-platform", ["fortytwo_connectivity"]))
        self.assertEqual(ids["demo-landing-zones"], ("demo", ["fortytwo_landing_zones"]))
        self.assertEqual(ids["demo-lz-corp"], ("demo-landing-zones", ["corp"]))
        self.assertEqual(ids["demo-lz-online"], ("demo-landing-zones", ["online"]))
        self.assertEqual(ids["demo-sandboxes"], ("demo", ["fortytwo_sandbox"]))
        self.assertEqual(ids["demo-decommissioned"], ("demo", ["decommissioned"]))
        self.assertEqual(len(ids), 10)
        for absent in ("demo-security", "demo-lz-local", "demo-lz-dataplatform", "demo-unusedsub"):
            self.assertNotIn(absent, ids)

    def test_a_customer_can_turn_on_one_optional_group_alone(self):
        config, out = self.render(self.BASE + "management_groups:\n  local: true\n")
        groups = self.read(out, "architecture_definitions/demo.alz_architecture_definition.json")["management_groups"]
        ids = {g["id"]: g["parent_id"] for g in groups}
        self.assertEqual(ids["demo-lz-local"], "demo-landing-zones")
        self.assertNotIn("demo-security", ids)  # still off from the defaults

    def test_the_security_group_sits_under_platform_when_turned_on(self):
        _, out = self.render(self.BASE + "management_groups:\n  security: true\n")
        groups = self.read(out, "architecture_definitions/demo.alz_architecture_definition.json")["management_groups"]
        ids = {g["id"]: (g["parent_id"], g["archetypes"]) for g in groups}
        self.assertEqual(ids["demo-security"], ("demo-platform", ["security"]))

    def test_sandboxes_get_the_same_region_and_tag_assignments_as_landing_zones(self):
        _, out = self.render(self.BASE)
        lz = self.read(out, "archetype_overrides/fortytwo_landing_zones.alz_archetype_override.json")
        sandbox = self.read(out, "archetype_overrides/fortytwo_sandbox.alz_archetype_override.json")
        self.assertEqual(sandbox["base_archetype"], "sandbox")
        self.assertEqual(sandbox["policy_assignments_to_add"], lz["policy_assignments_to_add"])
        self.assertEqual(sandbox["policy_assignments_to_remove"], [])

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

    def test_the_root_override_brings_the_tag_definition(self):
        _, out = self.render(self.BASE)
        root = self.read(out, "archetype_overrides/fortytwo_root.alz_archetype_override.json")
        self.assertEqual(root["base_archetype"], "root")
        self.assertEqual(root["policy_definitions_to_add"], ["Deny-RG-Without-Tag"])
        self.assertEqual(root["policy_assignments_to_remove"], [])
        self.assertTrue((rl.GOVERNANCE / "lib/policy_definitions/Deny-RG-Without-Tag.alz_policy_definition.json").exists())

    def test_quoted_or_unknown_management_group_flags_are_refused(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(self.BASE + 'management_groups:\n  security: "false"\n  local: "false"\n  secuirty: false\n')
        message = str(caught.exception)
        self.assertIn("management_groups.security must be true or false", message)
        self.assertIn("management_groups.local must be true or false", message)
        self.assertIn("management_groups.secuirty: unknown", message)

    def test_archetype_keys_must_be_alz_archetypes(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(
                self.BASE + "policy_assignments_to_remove:\n"
                "  '../../../../existing': [Deploy-GuestAttest]\n"
                "  landing_zone: [Enable-DDoS-VNET]\n"
            )
        message = str(caught.exception)
        self.assertIn("policy_assignments_to_remove.../../../../existing: not an ALZ archetype", message)
        self.assertIn("policy_assignments_to_remove.landing_zone: not an ALZ archetype", message)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["customer.yaml"])

    def test_writes_never_leave_the_output_folder(self):
        out = self.dir / "out"
        out.mkdir()
        with self.assertRaises(rl.RenderError) as caught:
            rl.write_json(out, "../escaped.json", {})
        self.assertIn("outside --out", str(caught.exception))
        self.assertFalse((self.dir / "escaped.json").exists())

    def test_exempt_patterns_follow_the_like_condition(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(self.BASE + "tags:\n  exempt_resource_groups: ['rg-*-temp-*']\n")
        self.assertIn("at most one '*'", str(caught.exception))

    def test_tag_assignments(self):
        _, out = self.render(self.BASE)
        require = self.read(out, "policy_assignments/Require-Tag-Owner.alz_policy_assignment.json")
        self.assertEqual(require["properties"]["policyDefinitionId"], rl.REQUIRE_TAG_DEFINITION)
        parameters = require["properties"]["parameters"]
        self.assertEqual(parameters["tagName"], {"value": "Owner"})
        self.assertIn("NetworkWatcherRG", parameters["exemptResourceGroupNames"]["value"])
        self.assertIn("rg-serviceHealthAlert", parameters["exemptResourceGroupNames"]["value"])
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

    def test_a_bare_string_where_a_list_belongs_is_refused_not_split(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(self.BASE + "allowed_regions: norwayeast\ntags:\n  required: Owner\n")
        message = str(caught.exception)
        self.assertIn("allowed_regions must be a list of strings", message)
        self.assertIn("tags.required must be a list of strings", message)
        self.assertFalse((self.dir / "out").exists())

    def test_mappings_must_be_mappings(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(self.BASE + "tags: [Owner]\nmanagement_groups: yes\npolicy_assignments_to_remove: {platform: Deploy-GuestAttest}\n")
        message = str(caught.exception)
        self.assertIn("tags must be a mapping", message)
        self.assertIn("management_groups must be a mapping", message)
        self.assertIn("policy_assignments_to_remove.platform must be a list of strings", message)

    def test_null_tag_lists_mean_no_tag_policies(self):
        _, out = self.render(self.BASE + "tags:\n  required: null\n  inherited: null\n")
        self.assertFalse((out / "policy_assignments").exists())

    def test_output_holding_the_inputs_is_refused(self):
        with self.assertRaises(rl.RenderError) as caught:
            rl.render(self.customer(self.BASE), DEFAULTS, self.dir)
        self.assertIn("holds source files", str(caught.exception))
        self.assertTrue((self.dir / "customer.yaml").exists())

    def test_output_inside_the_governance_sources_is_refused(self):
        for folder in (rl.GOVERNANCE, rl.GOVERNANCE.parent):
            with self.assertRaises(rl.RenderError):
                rl.render(self.customer(self.BASE), DEFAULTS, folder)
        self.assertTrue((rl.GOVERNANCE / "defaults.yaml").exists())

    def test_a_folder_the_script_did_not_create_is_never_deleted(self):
        out = self.dir / "out"
        out.mkdir()
        (out / "keep.txt").write_text("not ours")
        with self.assertRaises(rl.RenderError) as caught:
            rl.render(self.customer(self.BASE), DEFAULTS, out)
        self.assertIn("not created by this script", str(caught.exception))
        self.assertTrue((out / "keep.txt").exists())

    def test_an_empty_existing_folder_is_used(self):
        out = self.dir / "out"
        out.mkdir()
        rl.render(self.customer(self.BASE), DEFAULTS, out)
        self.assertTrue((out / rl.MARKER).exists())

    def test_missing_required_settings(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render("name: nothing else\n")
        for key in ("root_id", "root_display_name", "primary_region", "security_contact"):
            self.assertIn(f"{key} is required", str(caught.exception))
        for role in ("management", "connectivity"):
            self.assertIn(f"subscriptions.{role} is required", str(caught.exception))

    def test_platform_subscriptions_are_checked(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(
                "root_id: demo\nroot_display_name: Demo\nprimary_region: norwayeast\n"
                "security_contact: not-an-address\n"
                "subscriptions:\n"
                "  management: 00000000-0000-0000-0000-000000000001\n"
                "  connectivity: 00000000-0000-0000-0000-000000000001\n"
                "  identity: sub-3\n"
                "  networking: 00000000-0000-0000-0000-000000000004\n"
            )
        message = str(caught.exception)
        self.assertIn("security_contact 'not-an-address' is not an email address", message)
        self.assertIn("subscriptions.identity must be a subscription ID", message)
        self.assertIn("subscriptions.networking: unknown", message)
        self.assertIn("each platform subscription must be a different one", message)

    def test_config_carries_lower_case_ids_and_plain_flags(self):
        _, out = self.render(
            "root_id: demo\nroot_display_name: Demo\nprimary_region: norwayeast\n"
            "security_contact: platform-team@demo.example\n"
            "management_groups:\n  security: null\n"
            "subscriptions:\n"
            "  management: ABCDEF01-1234-1234-1234-1234567890AB\n"
            "  connectivity: 00000000-0000-0000-0000-000000000002\n"
            "  security: 00000000-0000-0000-0000-000000000004\n"
        )
        config = self.read(out, "config.json")
        self.assertEqual(config["subscriptions"]["management"], "abcdef01-1234-1234-1234-1234567890ab")
        self.assertEqual(config["management_groups"], {"security": False, "local": False})

    def test_a_trailing_newline_is_not_part_of_a_valid_value(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(
                "root_id: demo\nroot_display_name: Demo\nprimary_region: norwayeast\n"
                "security_contact: |\n  platform-team@demo.example\n"
                "subscriptions:\n"
                "  management: |\n    00000000-0000-0000-0000-000000000001\n"
                "  connectivity: 00000000-0000-0000-0000-000000000002\n"
            )
        message = str(caught.exception)
        self.assertIn("subscriptions.management must be a subscription ID", message)
        self.assertIn("is not an email address", message)

    def test_long_root_ids_are_refused(self):
        with self.assertRaises(rl.RenderError) as caught:
            self.render(self.BASE.replace("root_id: demo", "root_id: a-very-long-root-id"))
        self.assertIn("2 to 16 lower-case letters", str(caught.exception))

    def test_platform_subscriptions_reach_the_config(self):
        config, out = self.render(self.BASE + "  identity: 00000000-0000-0000-0000-000000000003\n")
        self.assertEqual(
            self.read(out, "config.json")["subscriptions"],
            {
                "management": "00000000-0000-0000-0000-000000000001",
                "connectivity": "00000000-0000-0000-0000-000000000002",
                "identity": "00000000-0000-0000-0000-000000000003",
            },
        )

    def test_the_starter_customer_renders(self):
        starter = rl.GOVERNANCE.parent / "starter" / "mlz-governance" / "customer.yaml"
        config = rl.render(starter, DEFAULTS, self.dir / "starter")
        self.assertEqual(config["root_id"], "demo")


if __name__ == "__main__":
    unittest.main()
