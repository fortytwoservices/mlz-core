"""Tests for mlz explain. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mlz import explain, platform, vending
from mlz.__main__ import main
from mlz.contract import render
from mlz.test_platform import GOVERNANCE as PLATFORM_GOVERNANCE_BASE
from mlz.test_vending import DEFAULTS, GOVERNANCE, PLATFORM, PROD, WORKLOAD


class ExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("defaults/landing-zone-defaults.yaml", DEFAULTS)
        self.write("landing-zones/payments/workload-defaults.yaml", WORKLOAD)
        self.write("landing-zones/payments/prod.yaml", PROD)
        self.allocate()

    def allocate(self, governance=GOVERNANCE) -> None:
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))
        self.write(vending.NAMES, vending.render_names(vending.give_names(self.repo, plan, governance)[0]))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def rows(self, governance=GOVERNANCE, platform=PLATFORM) -> dict[str, tuple[str, str]]:
        lz = vending.landing_zones(self.repo)["payments-prod"]
        parts = explain.explain(self.repo, "payments-prod", lz, vending.ip_plan(self.repo), governance, platform)
        return {setting: (value, source) for part in parts.values() for setting, value, source in part}

    def test_a_numbered_name_says_where_its_number_comes_from(self):
        # A template without {purpose}: mlz-core numbers its own (docs/naming.md, without {purpose}).
        flat = {**GOVERNANCE, "naming": {"template": "{type}-{org}-{app}-{env}-{region}-{nn}"}}
        (self.repo / vending.NAMES).unlink()
        self.allocate(flat)
        rows = self.rows(flat)
        self.assertEqual(rows["name identity_resource_group"], ("rg-demo-pay-pd-nwe-02", "recorded in _generated/names.yaml; number 02 of the landing "
                         "zone's resource groups (network_resource_group, identity_resource_group, state_resource_group)"))
        self.assertEqual(rows["name plan_identity"][1], "recorded in _generated/names.yaml; number 02 of the landing zone's identities "
                         "(deploy_identity, plan_identity)")
        self.assertTrue(rows["name NSG of default"][1].endswith("; NSGs are numbered: the lowest number no NSG of the landing zone held when it was first named"))
        self.assertEqual(rows["name virtual_network"][1], "recorded in _generated/names.yaml")  # alone of its type: nothing to say
        # With {purpose}, nothing is numbered and nothing is said.
        self.assertEqual(self.rows()["name identity_resource_group"][1], "recorded in _generated/names.yaml; the template now gives rg-demo-pay-id-pd-nwe-01 "
                         "(./mlz names --repo . --renew payments-prod)")

    def test_each_value_names_the_file_that_set_it_and_what_it_replaced(self):
        rows = self.rows()
        self.assertEqual(rows["owner"], ("payments@demo.example", "landing-zones/payments/workload-defaults.yaml"))
        self.assertEqual(rows["location"], ("norwayeast", "defaults/landing-zone-defaults.yaml"))
        # defaults/landing-zone-defaults.yaml says 500, payments/prod.yaml 2000: the later layer wins, and says what it replaced.
        self.assertEqual(rows["budget.monthly"], ("2000", "landing-zones/payments/prod.yaml, replacing defaults/landing-zone-defaults.yaml: 500"))
        self.assertEqual(rows["budget.contacts"], ("[platform@demo.example]", "defaults/landing-zone-defaults.yaml"))

    def test_what_nothing_sets_is_mlz_cores_default(self):
        rows = self.rows()
        self.assertEqual(rows["network.size"], ("24", "mlz-core's default"))
        self.assertEqual(rows["network.subnets.default"][0], "26")
        self.assertEqual(rows["github"], ("no repo", "no github.organization"))

    def test_names_and_where_they_come_from(self):
        rows = self.rows()
        self.assertEqual(rows["name virtual_network"], ("vnet-demo-pay-pd-nwe-01", "recorded in _generated/names.yaml"))
        self.assertEqual(rows["name NSG of default"], ("nsg-demo-pay-default-pd-nwe-01", "recorded in _generated/names.yaml"))
        # A changed short name renames nothing; explain shows what renewing would give.
        self.write("landing-zones/payments/workload-defaults.yaml", WORKLOAD.replace("short_name: pay", "short_name: pm"))
        self.assertEqual(self.rows()["name virtual_network"], (
            "vnet-demo-pay-pd-nwe-01", "recorded in _generated/names.yaml; the template now gives vnet-demo-pm-pd-nwe-01 (./mlz names --repo . --renew payments-prod)"))
        # Not recorded yet: the template's, which the pull request's run records.
        self.write("landing-zones/payments/workload-defaults.yaml", WORKLOAD)
        (self.repo / vending.NAMES).unlink()
        self.assertEqual(self.rows()["name route_table"], ("rt-demo-pay-pd-nwe-01", "the naming template (docs/naming.md), recorded by the IP allocator on the pull request"))
        self.write("landing-zones/payments/prod.yaml", PROD + "names:\n  virtual_network: vnet-legacy\n")
        self.assertEqual(self.rows()["name virtual_network"], ("vnet-legacy", "names: in landing-zones/payments/prod.yaml"))
        # The file as it is: a .yml, or a folder of chunks.
        (self.repo / "landing-zones/payments/prod.yaml").unlink()
        self.write("landing-zones/payments/prod.yml", PROD + "names:\n  virtual_network: vnet-legacy\n")
        self.assertEqual(self.rows()["name virtual_network"], ("vnet-legacy", "names: in landing-zones/payments/prod.yml"))

    def test_the_check_reports_what_stops_a_name(self):
        lz = lambda: vending.landing_zones(self.repo)["payments-prod"]
        self.write("landing-zones/payments/prod.yaml", PROD + "names:\n  route_table: 'rt bad'\n")
        found = explain.problems("payments-prod", lz(), vending.ip_plan(self.repo), GOVERNANCE, PLATFORM)
        self.assertIn("landing-zones/payments/prod: route_table name rt bad has characters Azure does not allow", "\n".join(found))
        self.write("landing-zones/payments/prod.yaml", PROD)
        found = explain.problems("payments-prod", lz(), vending.ip_plan(self.repo), {**GOVERNANCE, "root": "demo-corp"}, PLATFORM)
        self.assertIn("_generated/governance.yaml: naming settings are not valid", "\n".join(found))
        found = explain.problems("payments-prod", lz(), vending.ip_plan(self.repo), {**GOVERNANCE, "naming": {"environments": {"prod": "PROD"}}}, PLATFORM)
        self.assertTrue(found)
        self.assertEqual(explain.problems("payments-prod", lz(), vending.ip_plan(self.repo), GOVERNANCE, PLATFORM), [])

    def test_the_workload_repo_vending_makes(self):
        self.write("defaults/landing-zone-defaults.yaml", DEFAULTS + "github:\n  organization: demo-org\n")
        self.allocate()
        rows = self.rows()
        self.assertEqual(rows["github.repo"], ("made by vending", "github.organization is set; customer.yaml's naming.repository names it (below)"))
        self.assertEqual(rows["name repository"], ("payments-prod", "recorded in _generated/names.yaml"))
        self.assertEqual(rows["name state_account"], ("stdemopaypdnwe01", "recorded in _generated/names.yaml"))
        self.write("landing-zones/payments/prod.yaml", PROD + "github:\n  create: false\n")
        self.assertEqual(self.rows()["github"], ("no repo", "github.create is false"))

    def test_a_subscription_vending_makes(self):
        ea = "/providers/Microsoft.Billing/billingAccounts/1234567/enrollmentAccounts/7654321"
        self.write("defaults/landing-zone-defaults.yaml", DEFAULTS + f"billing_scope: {ea}\n")
        self.write("landing-zones/payments/prod.yaml", "budget: null\n")
        self.allocate()
        rows = self.rows()
        self.assertEqual(rows["subscription"], ("made by vending", "none is handed over: vending's apply makes it, by the alias demo-payments-prod, billed where billing_scope says (EA)"))
        self.assertEqual(rows["billing_scope"], (ea, "defaults/landing-zone-defaults.yaml"))
        self.assertEqual(rows["name subscription"], ("sub-demo-pay-pd-nwe-01", "recorded in _generated/names.yaml"))
        lz = vending.landing_zones(self.repo)["payments-prod"]
        self.assertEqual(explain.problems("payments-prod", lz, vending.ip_plan(self.repo), GOVERNANCE, PLATFORM, vending.recorded_names(self.repo)), [])
        # Without the contracts too: no subscription is asked for.
        self.assertEqual(explain.problems("payments-prod", lz, vending.ip_plan(self.repo), None, None, vending.recorded_names(self.repo)), [])
        self.write("defaults/landing-zone-defaults.yaml", DEFAULTS)
        lz = vending.landing_zones(self.repo)["payments-prod"]
        self.assertEqual(explain.problems("payments-prod", lz, vending.ip_plan(self.repo), None, None),
                         ["landing-zones/payments/prod: subscription is required, or billing_scope for vending to make one (set either here or in a layer above)."])

    def test_a_decommissioned_landing_zone_shows_where_it_goes(self):
        self.write("landing-zones/payments/prod.yaml", "decommission: true\n" + PROD)
        self.assertEqual(self.rows()["management group"], ("demo-decommissioned", "decommission: true; its plan destroys everything vending made"))

    def test_the_rest_comes_from_the_contracts_and_the_ip_plan(self):
        rows = self.rows()
        self.assertEqual(rows["management group"], ("demo-lz-corp", "governance's contract, for archetype corp"))
        self.assertEqual(rows["tag Environment"], ("prod", "from the file name"))
        self.assertEqual(rows["address range"], ("10.100.8.0/24", "the IP plan (_generated/ip-plan.yaml)"))
        self.assertEqual(rows["subnet default"], ("10.100.8.0/26", "the IP plan"))
        self.assertEqual(rows["route default"], ("0.0.0.0/0 via 10.100.0.4", "the platform's contract, for norwayeast"))

    def test_without_the_contracts_it_says_what_waits_for_them(self):
        rows = self.rows(governance=None, platform=None)
        self.assertEqual(rows["management group"][0], "not known yet")
        self.assertIn("waits for the platform's contract", rows["hub"][1])
        self.assertEqual(rows["address range"][0], "10.100.8.0/24")  # the IP plan is the repo's own

    def problems(self) -> list[str]:
        lz = vending.landing_zones(self.repo)["payments-prod"]
        return explain.problems("payments-prod", lz, vending.ip_plan(self.repo), GOVERNANCE, PLATFORM)

    def test_an_empty_contacts_list_shows_the_owner_who_gets_the_alerts(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "  contacts: []\n")
        self.assertEqual(self.rows()["budget.contacts"], ("[payments@demo.example]", "the owner, as no contacts are given"))

    def test_an_archetype_that_is_not_one_value_is_reported_not_a_crash(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "archetype: [corp]\n")
        self.assertNotIn("management group", self.rows())
        self.assertIn("archetype is one value", "\n".join(self.problems()))

    def test_a_size_given_cannot_change(self):
        self.write("landing-zones/payments/prod.yaml", PROD + "network:\n  size: 25\n  subnets:\n    default: 27\n")
        found = "\n".join(self.problems())
        self.assertIn("network.size is 25, but its range 10.100.8.0/24 was given as a /24", found)
        self.assertIn("subnet default is a /27 here but was given as 10.100.8.0/26", found)

    def test_a_group_a_later_layer_clears_names_what_it_had(self):
        self.write("landing-zones/payments/prod.yaml", "subscription: 33333333-3333-3333-3333-333333333333\nbudget: null\n")
        value, source = self.rows()["budget"]
        self.assertEqual(value, "null")
        # Both values the defaults had under budget are named, contacts and monthly.
        self.assertIn("replacing defaults/landing-zone-defaults.yaml: budget.contacts [platform@demo.example]", source)
        self.assertIn("defaults/landing-zone-defaults.yaml: budget.monthly 500", source)

    def test_another_landing_zones_problems_stay_out(self):
        # internal-payments-prod ends with payments-prod; its own overlap is not payments-prod's problem.
        self.write("landing-zones/internal/workload-defaults.yaml", WORKLOAD)
        self.write("landing-zones/internal/payments-prod.yaml", "subscription: 44444444-4444-4444-4444-444444444444\n")
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        plan["internal-payments-prod"]["range"] = vending.IPv4Network("10.100.0.0/24")  # inside the hub's range
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))
        self.assertEqual(self.problems(), [])

    def test_an_overlap_with_another_landing_zone_is_reported(self):
        self.write("landing-zones/internal/workload-defaults.yaml", WORKLOAD)
        self.write("landing-zones/internal/web.yaml", "subscription: 44444444-4444-4444-4444-444444444444\n")
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        plan["internal-web"]["range"] = plan["payments-prod"]["range"]
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))
        self.assertIn("payments-prod's range 10.100.8.0/24 overlaps internal-web's 10.100.8.0/24", "\n".join(self.problems()))

    def test_without_the_contracts_a_missing_required_setting_is_still_reported(self):
        self.write("landing-zones/payments/workload-defaults.yaml", "cost_center: '4711'\narchetype: corp\n")
        lz = vending.landing_zones(self.repo)["payments-prod"]
        found = explain.problems("payments-prod", lz, vending.ip_plan(self.repo), None, None)
        self.assertEqual(found, ["landing-zones/payments/prod: owner is required (set it here or in a layer above)."])
        self.assertIn("the checks that need the contracts wait for them", explain.clean(False))

    def run_main(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["mlz", *argv]), redirect_stdout(out), redirect_stderr(err):
            status = main()
        return status, out.getvalue(), err.getvalue()

    def test_the_command_reads_the_contracts_in_the_repo_and_checks_the_landing_zone(self):
        self.write("_generated/governance.yaml", render("governance", GOVERNANCE))
        self.write("_generated/platform.yaml", render("platform", PLATFORM))
        self.write("landing-zones/payments/prod.yaml", PROD + "archetype: online\nnetwork:\n  subnets:\n    app: 26\n")
        status, out, _ = self.run_main("explain", "--repo", str(self.repo), "payments-prod")
        self.assertEqual(status, 0)
        self.assertIn("payments-prod (landing-zones/payments/prod.yaml)", out)
        self.assertIn("demo-lz-online", out)
        self.assertIn("its subnets in _generated/ip-plan.yaml are not the ones asked for", out)

    def test_an_unknown_key_lists_the_landing_zones(self):
        status, _, err = self.run_main("explain", "--repo", str(self.repo), "payments-test")
        self.assertEqual(status, 1)
        self.assertIn("no landing zone payments-test; the landing zones are payments-prod.", err)


    def test_inputs_shows_every_input_of_both_module_calls_with_the_config_s_values(self):
        self.write("_generated/governance.yaml", render("governance", GOVERNANCE))
        self.write("_generated/platform.yaml", render("platform", PLATFORM))
        status, out, _ = self.run_main("explain", "--repo", str(self.repo), "payments-prod", "--inputs")
        self.assertEqual(status, 0)
        self.assertIn("module.route_table: az-tf-mlz-route-table v0.1.1", out)
        self.assertIn("module.vending: Azure/avm-ptn-alz-sub-vending/azure 0.3.4", out)
        self.assertRegex(out, r"budgets.amount +2000 +<- setting: budget.monthly in a landing zone's file")
        self.assertRegex(out, r"virtual_networks.dns_servers +\[10.100.0.4\] +<- derived")
        self.assertRegex(out, r"enable_telemetry +false +<- fixed by Fortytwo")
        self.assertNotIn("none: not built here", out)

    def test_inputs_gives_no_values_where_the_root_gives_none(self):
        self.write("_generated/governance.yaml", render("governance", GOVERNANCE))
        self.write("_generated/platform.yaml", render("platform", PLATFORM))
        self.write("landing-zones/payments/prod.yaml", PROD.replace("budget:\n  monthly: 2000\n", ""))
        self.write("defaults/landing-zone-defaults.yaml", DEFAULTS.replace("  monthly: 500\n", ""))
        out = self.run_main("explain", "--repo", str(self.repo), "payments-prod", "--inputs")[1]
        self.assertRegex(out, r"budgets.name +none: no budget \(budget.monthly\)")
        self.assertRegex(out, r"virtual_networks.name +vnet-")
        self.write("landing-zones/payments/prod.yaml", PROD + "decommission: true\n")
        out = self.run_main("explain", "--repo", str(self.repo), "payments-prod", "--inputs")[1]
        self.assertRegex(out, r"virtual_networks.name +none: decommissioned")
        self.assertRegex(out, r"enable_telemetry +false")  # what the map fixes is the root's either way

    def test_inputs_needs_both_contracts(self):
        status, _, err = self.run_main("explain", "--repo", str(self.repo), "payments-prod", "--inputs")
        self.assertEqual(status, 1)
        self.assertIn("needs governance's and the platform's contracts", err)

    def test_the_vending_starter_s_example_names_a_source_for_every_value(self):
        starter = Path(explain.inputs.CORE / "starter" / "mlz-vending")
        for path in (starter / "defaults").glob("*.yaml"):
            self.write(f"defaults/{path.name}", path.read_text())
        for path in (starter / "examples" / "payments").glob("*.yaml"):
            self.write(f"landing-zones/payments/{path.name}", path.read_text())
        self.allocate()
        parts = sources(self.rows())
        self.assertTrue(parts, "the example gave no rows")
        self.assertEqual([row for row, source in parts if not source], [])


def sources(rows: dict) -> list[tuple[str, str]]:
    return [(setting, source) for setting, (_, source) in rows.items()]


def rows_of(text: str) -> list[str]:
    """The rows of explain's output, under a part's title, the check excepted; a long value's source, on the line
    after it, joined to it."""
    found, part = [], None
    for line in text.splitlines():
        if line and not line.startswith(" "):
            part = line
        elif line.strip().startswith("<-") and found:
            found[-1] += " " + line.strip()
        elif line.startswith("  ") and part not in (None, "Check"):
            found.append(line)
    return found


HUB = "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\nbastion: true\nfirewall: Premium\n"
PLATFORM_GOVERNANCE = {**PLATFORM_GOVERNANCE_BASE, "platform_subscriptions": {"management": "m", "connectivity": "c", "identity": "i"}}


class PlatformExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("connectivity/norwayeast.yaml", HUB)
        self.write("identity/norwayeast.yaml", "")
        self.write("_generated/governance.yaml", render("governance", PLATFORM_GOVERNANCE))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["mlz", "explain", "--repo", str(self.repo), *argv]), redirect_stdout(out), redirect_stderr(err):
            status = main()
        return status, out.getvalue(), err.getvalue()

    def test_a_hub_s_settings_its_defaults_and_what_is_worked_out_each_with_its_source(self):
        status, out, _ = self.run_main("norwayeast")
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("connectivity/norwayeast (connectivity/norwayeast.yaml)\n"))
        self.assertRegex(out, r"firewall +Premium +<- connectivity/norwayeast.yaml")
        self.assertRegex(out, r"dns_resolver +true +<- mlz-core's default")
        self.assertRegex(out, r"hub_virtual_networks.firewall.subnet_address_prefix +10.100.0.0/26 +<- the first /26 of the hub's /22")
        self.assertRegex(out, r"hub_virtual_networks.bastion.name +bas-demo-hub-pd-nwe-01 +<- the naming template; names.bastion in connectivity/<region>.yaml sets it")
        self.assertIn("no problems in the platform's settings", out)
        self.assertEqual([row for row in rows_of(out) if "<-" not in row], [])
        self.assertEqual(self.run_main("connectivity/norwayeast")[1], out)

    def test_a_component_turned_off_is_said_not_to_be_built(self):
        self.write("connectivity/norwayeast.yaml", HUB.replace("bastion: true", "bastion: false") + "dns_resolver: false\n")
        self.assertRegex(self.run_main("norwayeast")[1], r"hub_virtual_networks.dns_resolver.name +none: not built")
        out = self.run_main("norwayeast", "--inputs")[1]  # its module defaults too: the root passes no Bastion at all
        self.assertRegex(out, r"hub_virtual_networks.bastion.sku +none: not built \(bastion is false\)")
        self.assertRegex(out, r"hub_virtual_networks.dns_resolver.inbound_subnet_name +none: not built \(dns_resolver is false\)")
        self.assertRegex(out, r"hub_virtual_networks.firewall.zones +null")

    def test_management_identity_and_the_rules(self):
        status, out, _ = self.run_main("management")
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("management/norwayeast\n"))
        self.assertRegex(out, r"network.address_space +10.100.4.0/24")
        self.assertRegex(out, r"network.route_table.name +rt-demo-mgt-pd-nwe-01")
        self.assertRegex(self.run_main("identity/norwayeast")[1], r"network.address_space +10.100.5.0/24")
        status, out, _ = self.run_main("firewall-rules")
        self.assertEqual(status, 0)
        self.assertIn("module.rule_collection_group:", out)
        self.assertRegex(out, r"nat_rule_collections +- +<- closed: outbound rules only")

    def test_only_a_network_the_platform_builds(self):
        status, _, err = self.run_main("identity/swedencentral")
        self.assertEqual(status, 1)
        self.assertIn("identity/swedencentral: no file there, so identity's network is not built in swedencentral (it is built in norwayeast)", err)
        status, _, err = self.run_main("management/swedencentral", "--inputs")
        self.assertEqual(status, 1)
        self.assertIn("management's network is built in the primary region, norwayeast, only", err)

    def test_a_network_file_with_settings_is_shown_and_refused_with_or_without_the_contract(self):
        self.write("identity/norwayeast.yaml", "dns_servers: [10.0.0.4]\n")
        for contract in (True, False):
            if not contract:
                (self.repo / "_generated" / "governance.yaml").unlink()
            out = self.run_main("identity/norwayeast")[1]
            self.assertRegex(out, r"dns_servers +\[10.0.0.4\] +<- identity/norwayeast.yaml")
            self.assertIn("identity/norwayeast: takes no settings yet", out)

    def test_without_governance_s_contract_what_is_worked_out_waits(self):
        (self.repo / "_generated" / "governance.yaml").unlink()
        out = self.run_main("norwayeast")[1]
        self.assertRegex(out, r"everything else +not known yet +<- waits for governance's contract")
        self.assertIn("no problems in this file; the checks that need governance's contract wait for it", out)
        self.write("connectivity/norwayeast.yaml", HUB.replace("Premium", "Gold"))  # the hub's own checks still run
        self.assertIn("connectivity/norwayeast: firewall 'Gold' must be one of Standard, Premium, Basic.", self.run_main("norwayeast")[1])
        self.write("connectivity/swedencentral.yaml", "")  # an empty hub beside one with settings: the loader refuses it too
        self.assertIn("connectivity/swedencentral: landing_zone_ranges is required", self.run_main("swedencentral")[1])

    def test_settings_that_do_not_check_are_reported(self):
        self.write("connectivity/norwayeast.yaml", HUB.replace("Premium", "Gold"))
        status, out, _ = self.run_main("norwayeast")
        self.assertEqual(status, 0)
        self.assertIn("connectivity/norwayeast: firewall 'Gold' must be one of Standard, Premium, Basic.", out)
        self.assertIn("the platform's settings must check first", out)
        status, _, err = self.run_main("norwayeast", "--inputs")  # with no values to show, it fails and says why
        self.assertEqual(status, 1)
        self.assertIn("firewall 'Gold' must be one of Standard, Premium, Basic.", err)

    def test_inputs_needs_governance_s_contract(self):
        (self.repo / "_generated" / "governance.yaml").unlink()
        status, _, err = self.run_main("norwayeast", "--inputs")
        self.assertEqual(status, 1)
        self.assertIn("needs governance's contract", err)

    def test_the_platform_starter_says_it_waits(self):
        self.write("connectivity/norwayeast.yaml", (explain.inputs.CORE / "starter/mlz-platform/connectivity/norwayeast.yaml").read_text())
        self.assertIn("no file in connectivity/ has a setting yet: the platform waits", self.run_main("norwayeast")[1])

    def test_an_unknown_target_lists_what_explain_takes(self):
        status, _, err = self.run_main("westeurope")
        self.assertEqual(status, 1)
        self.assertIn("explain takes a region with a file in connectivity/ (norwayeast), management, identity/<region> or firewall-rules", err)

    def test_inputs_shows_every_input_of_the_hub_with_the_config_s_values(self):
        status, out, _ = self.run_main("norwayeast", "--inputs")
        self.assertEqual(status, 0)
        self.assertNotIn("Check", out)
        self.assertRegex(out, r"hub_virtual_networks.firewall.sku_tier +Premium +<- setting: firewall in connectivity/<region>.yaml")
        self.assertRegex(out, r"hub_virtual_networks.bastion.sku +\"Standard\" +<- open: the module's default")
        self.assertRegex(out, r"hub_virtual_networks.vpn_gateway +- +<- closed:")
        mapped = explain.inputs.load_maps()["platform/terraform"]["connectivity"]["inputs"]
        self.assertEqual(len(rows_of(out)), len(mapped))


class GovernanceExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def explain(self, customer: str, show_inputs: bool = False) -> str:
        (self.repo / "customer.yaml").write_text(customer)
        return explain.governance_explain(self.repo, show_inputs)

    def test_the_starter_names_a_source_for_every_value(self):
        out = self.explain((explain.inputs.CORE / "starter/mlz-governance/customer.yaml").read_text())
        self.assertRegex(out, r"root_id +demo +<- customer.yaml")
        self.assertRegex(out, r"tags.required +\[Owner, CostCenter\] +<- mlz-core's governance/defaults.yaml")
        self.assertRegex(out, r"management.name +log-demo-mgt-pd-nwe-01 +<- the naming template")
        self.assertIn("no problems in customer.yaml", out)
        self.assertEqual([row for row in rows_of(out) if "<-" not in row], [])

    def test_a_value_that_replaces_a_default_names_it(self):
        customer = (explain.inputs.CORE / "starter/mlz-governance/customer.yaml").read_text()
        out = self.explain(customer + "allowed_regions: [norwayeast]\ntags:\n  required: [Owner]\n")
        self.assertRegex(out, r"allowed_regions +\[norwayeast\] +<- customer.yaml, replacing mlz-core's governance/defaults.yaml: \[norwayeast, norwaywest")
        self.assertRegex(out, r"tags.required +\[Owner\] +<- customer.yaml, replacing mlz-core's governance/defaults.yaml: \[Owner, CostCenter\]")
        self.assertRegex(out, r"tags.inherited +\[Owner, CostCenter, Environment, Workload\] +<- mlz-core's governance/defaults.yaml")

    def test_a_customer_file_that_does_not_check_is_reported(self):
        out = self.explain("root_id: Demo\n")
        self.assertIn("root_id 'Demo'", out)
        self.assertIn("customer.yaml must check first", out)
        with self.assertRaisesRegex(explain.ConfigError, "root_id 'Demo'"):  # --inputs: no values to show, so it fails
            self.explain("root_id: Demo\n", show_inputs=True)

    def test_inputs_shows_both_module_calls(self):
        out = self.explain((explain.inputs.CORE / "starter/mlz-governance/customer.yaml").read_text(), show_inputs=True)
        self.assertIn("module.alz: Azure/avm-ptn-alz/azurerm 0.22.0", out)
        self.assertIn("module.management: az-tf-mlz-management v0.2.0", out)
        self.assertRegex(out, r"architecture_name +demo +<- setting: root_id in customer.yaml")


class ConfigPathTest(unittest.TestCase):
    """Every config path in the maps is a place in its stack's config, on a config with every component built."""

    def test_every_config_path_resolves(self):
        maps = explain.inputs.load_maps()
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo / "connectivity").mkdir()
            (repo / "connectivity" / "norwayeast.yaml").write_text(HUB)
            (repo / "identity").mkdir()
            (repo / "identity" / "norwayeast.yaml").write_text("")
            configs = {"platform/terraform": (platform.build(repo, PLATFORM_GOVERNANCE), [{"region": "norwayeast"}]),
                       "platform/networks/terraform": (platform.build(repo, PLATFORM_GOVERNANCE),
                                                       [{"network": n, "region": "norwayeast"} for n in ("management", "identity")])}
        case = ExplainTest("run")
        case.setUp()
        try:
            configs["vending/terraform"] = (vending.build(case.repo, GOVERNANCE, PLATFORM)["payments-prod"], [{}])
        finally:
            case.tearDown()
        with tempfile.TemporaryDirectory() as folder:
            rules = explain.governance
            customer = rules.load_yaml(explain.inputs.CORE / "starter/mlz-governance/customer.yaml")
            merged = rules.merge(rules.load_yaml(explain.inputs.CORE / "governance/defaults.yaml"), customer)
            configs["governance/terraform"] = (rules.normalize(merged), [{}])
        checked = 0
        for root, (config, instances) in configs.items():
            for key, section in maps[root].items():
                for path, item in (section.get("inputs") or {}).items():
                    if isinstance(item, dict) and "config" in item:
                        for places in instances:
                            with self.subTest(root=root, input=f"{key}.{path}", places=places):
                                explain.inputs.value_at(config, item["config"], places)
                                checked += 1
        self.assertGreater(checked, 60)
        self.assertEqual({root for root, sections in maps.items() for s in sections.values()
                          for item in (s.get("inputs") or {}).values() if isinstance(item, dict) and "config" in item}, set(configs))


if __name__ == "__main__":
    unittest.main()
