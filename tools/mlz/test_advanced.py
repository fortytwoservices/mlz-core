"""Tests for advanced: (tools/mlz/advanced.py): what a hub file's block may set, what it refuses and why, and what it
writes into the platform's config."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mlz import advanced, explain, governance, inputs, platform, schema
from mlz.merge import ConfigError
from mlz.test_platform import GOVERNANCE

HUB = "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\nbastion: true\n"
# What the Terraform test (platform/terraform/tests/advanced.tftest.hcl) gives the platform root.
FIXTURE = {
    "norwayeast": HUB,
    "swedencentral": "platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\nbastion: true\n"
                     "advanced:\n  virtual_network_dns_servers: [10.200.1.68]\n  bastion:\n    sku: Premium\n    tunneling_enabled: true\n"
                     "  firewall:\n    zones: ['1', '2', '3']\n    firewall_policy:\n      threat_intelligence_mode: Deny\n",
}


class AdvancedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "connectivity").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self, text: str, region: str = "norwayeast") -> None:
        (self.repo / "connectivity" / f"{region}.yaml").write_text(text)

    def problems(self, block: str) -> list[str]:
        self.hub(HUB + "advanced:\n" + block)
        with self.assertRaises(ConfigError) as caught:
            platform.build(self.repo, GOVERNANCE)
        return [line.strip() for line in str(caught.exception).splitlines()[1:]]

    def test_r2s_first_example_reaches_the_hub_and_only_it(self):
        self.hub(HUB + "advanced:\n  bastion:\n    sku: Premium\n    tunneling_enabled: true\n")
        hub = platform.build(self.repo, GOVERNANCE)["hubs"]["norwayeast"]
        self.assertEqual(hub["advanced"], {"bastion": {"sku": "Premium", "tunneling_enabled": True}})
        self.assertEqual(hub["bastion"], {"subnet_address_prefix": "10.100.1.0/26", "name": "bas-demo-hub-pd-nwe-01",
                                          "public_ip_name": "pip-demo-hub-bas-pd-nwe-01"})  # mlz-core's own, untouched

    def test_without_advanced_the_config_has_no_trace_of_it(self):
        self.hub(HUB)
        self.assertNotIn("advanced", platform.build(self.repo, GOVERNANCE)["hubs"]["norwayeast"])
        self.hub(HUB + "advanced:\n")  # empty, as a commented-out example leaves it
        self.assertNotIn("advanced", platform.build(self.repo, GOVERNANCE)["hubs"]["norwayeast"])

    def test_r2s_refusals_name_who_owns_the_input(self):
        self.assertEqual(self.problems("  firewall:\n    subnet_address_prefix: 10.100.0.0/26\n"), [
            "connectivity/norwayeast: advanced.firewall.subnet_address_prefix is worked out by mlz-core (the first /26 of the hub's /22); it cannot be set."])
        self.assertEqual(self.problems("  firewall:\n    name: afw-mine\n"), [
            "connectivity/norwayeast: advanced.firewall.name is worked out by mlz-core (the naming template); it cannot be set. "
            "To name it yourself, set names.firewall in this file."])
        self.assertEqual(self.problems("  firewall:\n    sku_tier: Premium\n"), [
            "connectivity/norwayeast: advanced.firewall.sku_tier is the setting firewall in connectivity/<region>.yaml; set it there."])
        self.assertIn("connectivity/norwayeast: advanced.firewall.firewall_policy.dns_proxy_enabled is fixed by Fortytwo",
                      self.problems("  firewall:\n    firewall_policy:\n      dns_proxy_enabled: false\n")[0])
        self.assertEqual(self.problems("  vpn_gateway:\n    sku: VpnGw2AZ\n"), [
            "connectivity/norwayeast: advanced.vpn_gateway is not supported yet (no VPN gateway yet: every peering is made without remote gateways, "
            "and mlz-core has no name for one)."])

    def test_a_mistyped_input_or_one_of_the_whole_call_is_refused_not_dropped(self):
        found = self.problems("  bastion:\n    tunnelling_enabled: true\n  retry:\n    interval_seconds: 5\n  bastion_sku: Premium\n")
        self.assertEqual(sorted(found), [
            "connectivity/norwayeast: advanced.bastion.tunnelling_enabled is not an input of az-tf-mlz-connectivity-hub v0.1.0 (docs/reference/platform.md lists them).",
            "connectivity/norwayeast: advanced.bastion_sku is not an input of az-tf-mlz-connectivity-hub v0.1.0 (docs/reference/platform.md lists them).",
            "connectivity/norwayeast: advanced.retry is an input of the whole module call, not of what this file builds; advanced: here does not reach it."])

    def test_values_are_checked_against_the_module_s_types(self):
        found = self.problems("  bastion:\n    scale_units: '4'\n    tunneling_enabled: 'yes'\n    zones: '1'\n  firewall:\n    zones: [1]\n")
        self.assertEqual(found, [
            "connectivity/norwayeast: advanced.bastion.scale_units must be a number, not '4'.",
            "connectivity/norwayeast: advanced.bastion.tunneling_enabled must be a bool, not 'yes'.",
            "connectivity/norwayeast: advanced.bastion.zones must be a list (list(string)), not '1'.",
            "connectivity/norwayeast: advanced.firewall.zones[0] must be a string, not 1."])
        self.assertEqual(self.problems("  bastion: Premium\n"), [
            "connectivity/norwayeast: advanced.bastion is a group of inputs: give its attributes (docs/reference/platform.md lists them)."])
        self.assertEqual(self.problems("  - bastion\n"), [
            "connectivity/norwayeast: advanced maps module inputs to their values (docs/reference/platform.md lists them)."])

    def test_a_dotted_key_is_refused_not_passed_as_one_attribute(self):
        self.assertEqual(self.problems("  bastion.sku: Premium\n"), [
            "connectivity/norwayeast: advanced.bastion.sku: write it nested, as the module's objects are (bastion: then the rest under it), not as one dotted key."])

    def test_a_component_the_hub_does_not_have_takes_no_inputs(self):
        self.hub(HUB.replace("bastion: true", "dns_resolver: false") + "advanced:\n  bastion:\n    sku: Premium\n  dns_resolver:\n    inbound_subnet_name: snet-in\n")
        with self.assertRaises(ConfigError) as caught:
            platform.build(self.repo, GOVERNANCE)
        self.assertIn("advanced.bastion sets inputs of a bastion this hub does not have; set bastion: true first.", str(caught.exception))
        self.assertIn("advanced.dns_resolver.inbound_subnet_name is fixed by Fortytwo", str(caught.exception))

    def test_the_terraform_test_s_config_is_what_the_loader_writes(self):
        for region, text in FIXTURE.items():
            self.hub(text, region)
        written = json.loads((inputs.CORE / "platform/terraform/tests/advanced.json").read_text())
        self.assertEqual(written, platform.build(self.repo, GOVERNANCE),
                         "platform/terraform/tests/advanced.json is out of date: write it again from test_advanced.FIXTURE")

    def test_the_schema_offers_the_open_inputs_and_nothing_else(self):
        block = schema.connectivity()["properties"]["advanced"]
        self.assertEqual(block["properties"]["bastion"]["properties"]["sku"], {"type": ["string", "null"]})  # null: the default
        self.assertEqual(block["properties"]["bastion"]["properties"]["zones"], {"type": ["array", "null"], "items": {"type": "string"}})
        self.assertEqual(block["type"], ["object", "null"])  # advanced: left empty is none
        self.assertEqual(block["properties"]["bastion"]["type"], "object")  # a group is not optional: give its attributes
        self.assertNotIn("name", block["properties"]["bastion"]["properties"])
        self.assertEqual(set(block["properties"]["firewall"]["properties"]), {"zones", "firewall_policy"})
        self.assertEqual(set(block["properties"]), {"virtual_network_dns_servers", "firewall", "bastion"})
        self.assertFalse(block["additionalProperties"])

    def test_explain_shows_what_advanced_sets(self):
        for region, text in FIXTURE.items():
            self.hub(text, region)
        config = platform.build(self.repo, GOVERNANCE)
        rows = {row[0]: row for part in explain.module_inputs("platform/terraform", config, {"region": "swedencentral"}).values() for row in part}
        self.assertEqual(rows["hub_virtual_networks.bastion.sku"][1:], ("Premium", "open: set under advanced.bastion.sku in connectivity/<region>.yaml"))
        self.assertEqual(rows["hub_virtual_networks.bastion.scale_units"][1], "2")  # not set: the module's default
        self.assertEqual(advanced.given("platform/terraform", "connectivity", config, {"region": "norwayeast"}), [])
        worked = explain.module_inputs("platform/terraform", config, {"region": "swedencentral"}, worked_out=True)["Worked out"]
        self.assertNotIn("hub_virtual_networks.bastion.sku", [row[0] for row in worked])  # the customer's, not worked out



class LandingZoneTest(unittest.TestCase):
    """A landing zone's advanced: reaches sub-vending's call: its own inputs, and its objects by vending's keys."""

    def setUp(self):
        from mlz.test_vending import DEFAULTS, PROD, WORKLOAD
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("defaults/landing-zone-defaults.yaml", DEFAULTS)
        self.write("landing-zones/payments/workload-defaults.yaml", WORKLOAD)
        self.prod = PROD + "network:\n  subnets:\n    app: 26\n"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def build(self, block: str = "") -> dict:
        from mlz import vending
        from mlz.test_vending import GOVERNANCE, PLATFORM
        self.write("landing-zones/payments/prod.yaml", self.prod + ("advanced:\n" + block if block else ""))
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))
        self.write(vending.NAMES, vending.render_names(vending.give_names(self.repo, plan, GOVERNANCE)[0]))
        return vending.build(self.repo, GOVERNANCE, PLATFORM)["payments-prod"]

    def problems(self, block: str) -> list[str]:
        with self.assertRaises(ConfigError) as caught:
            self.build(block)
        return [line.strip() for line in str(caught.exception).splitlines()[1:]]

    def test_its_own_inputs_and_its_objects_by_vending_s_keys(self):
        config = self.build("  role_assignments:\n    readers: {principal_id: 00000000-0000-0000-0000-0000000000aa, definition: Reader}\n"
                            "  virtual_networks:\n    spoke:\n      subnets:\n        app:\n          service_endpoints: [Microsoft.Storage]\n"
                            "  network_security_groups:\n    app:\n      security_rules:\n        deny-rdp: {access: Deny, direction: Inbound, name: deny-rdp, priority: 200, protocol: Tcp}\n")
        self.assertEqual(config["advanced"]["virtual_networks"]["spoke"]["subnets"]["app"], {"service_endpoints": ["Microsoft.Storage"]})
        self.assertEqual(config["advanced"]["role_assignments"]["readers"]["definition"], "Reader")

    def test_without_advanced_the_config_has_no_trace_of_it(self):
        self.assertNotIn("advanced", self.build())

    def test_keys_vending_does_not_give_and_inputs_it_owns_are_refused(self):
        where = "landing-zones/payments/prod"
        self.assertEqual(sorted(self.problems("  virtual_networks:\n    hub:\n      ddos_protection_enabled: true\n    spoke:\n      subnets:\n"
                                              "        web:\n          service_endpoints: [Microsoft.Storage]\n        app:\n          address_prefix: 10.0.0.0/26\n")), [
            f"{where}: advanced.virtual_networks.hub: there is no such spoke here (spoke).",
            f"{where}: advanced.virtual_networks.spoke.subnets.app.address_prefix is worked out by mlz-core (the subnet's block in _generated/ip-plan.yaml, "
            "whose size network.subnets asks for); it cannot be set.",
            f"{where}: advanced.virtual_networks.spoke.subnets.web: there is no such <subnet> here (app)."])
        self.assertEqual(self.problems("  budgets:\n    monthly:\n      notifications:\n        actual_80:\n          threshold: 90\n"), [
            f"{where}: advanced.budgets.monthly.notifications.actual_80.threshold is fixed by Fortytwo "
            "(a warning at 80 % spent, and when the month is forecast to go over). Ask for a change in mlz-core."])
        self.assertEqual(self.problems("  role_assignment_enabled: true\n"), [
            f"{where}: advanced.role_assignment_enabled is worked out by mlz-core (on when advanced: sets role_assignments); it cannot be set."])

    def test_what_the_module_requires_of_an_object_advanced_adds_is_required(self):
        where = "landing-zones/payments/prod"
        self.assertEqual(sorted(self.problems("  role_assignments:\n    readers: {definition: Reader}\n"
                                              "  virtual_networks:\n    spoke:\n      subnets:\n        app:\n          delegations: [{name: web}]\n")), [
            f"{where}: advanced.role_assignments.readers needs principal_id, which the module requires.",
            f"{where}: advanced.virtual_networks.spoke.subnets.app.delegations[0] needs service_delegation, which the module requires."])
        # The schema checks one layer, which may give part of an assignment another completes: no required there.
        assignments = schema.landing_zone()["properties"]["advanced"]["properties"]["role_assignments"]
        self.assertNotIn("required", assignments["additionalProperties"])
        self.write("defaults/landing-zone-defaults.yaml", self.read("defaults/landing-zone-defaults.yaml") +
                   "advanced:\n  role_assignments:\n    readers: {definition: Reader}\n")
        merged = self.build("  role_assignments:\n    readers: {principal_id: 00000000-0000-0000-0000-0000000000aa}\n")
        self.assertEqual(merged["advanced"]["role_assignments"]["readers"], {"definition": "Reader", "principal_id": "00000000-0000-0000-0000-0000000000aa"})

    def test_a_null_is_the_module_s_default_and_takes_an_upper_layer_s_value_away(self):
        self.write("defaults/landing-zone-defaults.yaml", self.read("defaults/landing-zone-defaults.yaml") +
                   "advanced:\n  role_assignments:\n    readers: {principal_id: 00000000-0000-0000-0000-0000000000aa, definition: Reader}\n")
        self.assertIn("role_assignments", self.build()["advanced"])
        self.assertNotIn("advanced", self.build("  role_assignments: null\n"))  # nothing left: no block at all

    def test_a_group_or_map_entry_left_empty_by_nulls_is_no_block(self):
        self.assertNotIn("advanced", self.build("  virtual_networks:\n    spoke:\n      subnets:\n        app:\n"
                                                "          service_endpoints: null\n"))

    def test_a_decommissioned_landing_zone_s_advanced_is_shown_as_not_given(self):
        self.prod += "decommission: true\n"
        config = self.build("  role_assignments:\n    readers: {principal_id: 00000000-0000-0000-0000-0000000000aa, definition: Reader}\n"
                            "  virtual_networks:\n    spoke:\n      subnets:\n        app:\n          service_endpoints: [Microsoft.Storage]\n")
        rows = {row[0]: row for part in explain.module_inputs("vending/terraform", config, {}).values() for row in part}
        self.assertTrue(rows["role_assignments"][1].startswith("none: decommissioned"))
        self.assertTrue(rows["virtual_networks.subnets.service_endpoints"][1].startswith("none: decommissioned"))

    def read(self, relative: str) -> str:
        return (self.repo / relative).read_text()

    def test_no_budget_no_budget_inputs(self):
        self.write("defaults/landing-zone-defaults.yaml", "location: norwayeast\n")
        self.prod = self.prod.replace("budget:\n  monthly: 2000\n", "")
        self.assertEqual(self.problems("  budgets:\n    monthly:\n      notifications:\n        actual_80:\n          locale: en-gb\n"), [
            "landing-zones/payments/prod: advanced.budgets.monthly: there is no such monthly here (none)."])

    def test_the_schema_takes_the_maps_by_any_key(self):
        block = schema.landing_zone()["properties"]["advanced"]
        subnets = block["properties"]["virtual_networks"]["additionalProperties"]["properties"]["subnets"]
        self.assertEqual(subnets["additionalProperties"]["properties"]["service_endpoints"], {"type": ["array", "null"], "items": {"type": "string"}})
        self.assertIn("role_assignments", block["properties"])
        self.assertNotIn("role_assignment_enabled", block["properties"])

    def test_explain_names_the_entry_advanced_set(self):
        config = self.build("  virtual_networks:\n    spoke:\n      subnets:\n        app:\n          service_endpoints: [Microsoft.Storage]\n")
        rows = {row[0]: row for part in explain.module_inputs("vending/terraform", config, {}).values() for row in part}
        self.assertEqual(rows["virtual_networks.subnets.service_endpoints"][1:], (
            "{virtual_networks.spoke.subnets.app.service_endpoints: [Microsoft.Storage]}",
            "open: set under advanced.virtual_networks.spoke.subnets.<subnet>.service_endpoints in a landing zone's file or a layer above it"))



class GovernanceTest(unittest.TestCase):
    """customer.yaml's advanced: reaches both of governance's calls, each under its name."""
    rules = governance
    starter = (inputs.CORE / "starter/mlz-governance/customer.yaml").read_text()

    def render(self, block: str) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "customer.yaml"
            path.write_text(self.starter + block)
            merged = self.rules.merge(self.rules.load_yaml(inputs.CORE / "governance/defaults.yaml"), self.rules.load_yaml(path))
        self.rules.validate(merged)
        return self.rules.normalize(merged)

    def problems(self, block: str) -> list[str]:
        with self.assertRaises(self.rules.RenderError) as caught:
            self.render(block)
        return [line.strip() for line in str(caught.exception).splitlines()[1:]]

    def test_each_call_under_its_name(self):
        config = self.render("advanced:\n  management:\n    retention_in_days: 90\n    data_collection_rules:\n      vm_insights:\n"
                             "        processes_and_dependencies_enabled: true\n  alz:\n    policy_assignments_to_modify: {}\n")
        self.assertEqual(config["advanced"], {"management": {"retention_in_days": 90, "data_collection_rules": {"vm_insights": {"processes_and_dependencies_enabled": True}}},
                                              "alz": {"policy_assignments_to_modify": {}}})

    def test_without_advanced_config_json_has_no_trace_of_it(self):
        self.assertNotIn("advanced", self.render(""))
        self.assertNotIn("advanced", self.render("advanced:\n  management:\n    retention_in_days: null\n"))
        self.assertNotIn("advanced", self.render("advanced:\n  management:\n    data_collection_rules:\n      vm_insights:\n"
                                                 "        processes_and_dependencies_enabled: null\n"))  # no group left empty
        self.assertEqual(self.render("advanced:\n  management:\n    sentinel_onboarding: {}\n")["advanced"],
                         {"management": {"sentinel_onboarding": {}}})  # an input's own {} is what it sets

    def test_what_governance_owns_and_calls_it_does_not_reach_are_refused(self):
        self.assertEqual(sorted(self.problems("advanced:\n  management:\n    name: log-mine\n    local_authentication_enabled: true\n"
                                              "  alz:\n    enable_telemetry: true\n  platform:\n    x: 1\n")), [
            "customer.yaml: advanced.alz.enable_telemetry is fixed by Fortytwo (no module mlz-core calls sends telemetry). Ask for a change in mlz-core.",
            "customer.yaml: advanced.management.local_authentication_enabled is fixed by Fortytwo (off: Entra ID only). Ask for a change in mlz-core.",
            "customer.yaml: advanced.management.name is worked out by mlz-core (the naming template (customer.yaml's naming)); it cannot be set.",
            "customer.yaml: advanced.platform is not a module call this file reaches; use alz or management."])

    def test_the_schema_and_the_reference_page_name_the_call(self):
        block = schema.customer()["properties"]["advanced"]
        self.assertEqual(set(block["properties"]), {"alz", "management"})
        self.assertEqual(block["properties"]["management"]["properties"]["retention_in_days"], {"type": ["number", "null"]})
        self.assertIn("set it with `advanced.management.retention_in_days` in `customer.yaml`", (inputs.CORE / "docs/reference/governance.md").read_text())

    def test_explain_shows_what_it_sets(self):
        config = self.render("advanced:\n  management:\n    retention_in_days: 90\n")
        rows = {row[0]: row for part in explain.module_inputs("governance/terraform", config, {}).values() for row in part}
        self.assertEqual(rows["retention_in_days"][1:], ("90", "open: set under advanced.management.retention_in_days in customer.yaml"))


if __name__ == "__main__":
    unittest.main()
