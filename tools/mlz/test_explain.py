"""Tests for mlz explain. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mlz import explain, vending
from mlz.__main__ import main
from mlz.contract import render
from mlz.test_vending import DEFAULTS, GOVERNANCE, PLATFORM, PROD, WORKLOAD


class ExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("landing-zones/_defaults/all.yaml", DEFAULTS)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD)
        self.write("landing-zones/payments/prod.yaml", PROD)
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))

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

    def test_each_value_names_the_file_that_set_it_and_what_it_replaced(self):
        rows = self.rows()
        self.assertEqual(rows["owner"], ("payments@demo.example", "landing-zones/payments/_workload.yaml"))
        self.assertEqual(rows["location"], ("norwayeast", "landing-zones/_defaults/all.yaml"))
        # _defaults/all.yaml says 500, payments/prod.yaml 2000: the later layer wins, and says what it replaced.
        self.assertEqual(rows["budget.monthly"], ("2000", "landing-zones/payments/prod.yaml, replacing landing-zones/_defaults/all.yaml: 500"))
        self.assertEqual(rows["budget.contacts"], ("[platform@demo.example]", "landing-zones/_defaults/all.yaml"))

    def test_what_nothing_sets_is_mlz_cores_default(self):
        rows = self.rows()
        self.assertEqual(rows["network.size"], ("24", "mlz-core's default"))
        self.assertEqual(rows["network.subnets.default"][0], "26")
        self.assertEqual(rows["github"], ("not set", "no repo of its own"))

    def test_names_and_where_they_come_from(self):
        rows = self.rows()
        self.assertEqual(rows["name virtual_network"], ("vnet-demo-pay-pd-nwe-01", "the naming template (docs/naming.md)"))
        self.assertEqual(rows["name NSG of default"], ("nsg-demo-pay-default-pd-nwe-01", "the naming template (docs/naming.md)"))
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
        self.assertIn("replacing landing-zones/_defaults/all.yaml: budget.contacts [platform@demo.example]", source)
        self.assertIn("landing-zones/_defaults/all.yaml: budget.monthly 500", source)

    def test_another_landing_zones_problems_stay_out(self):
        # internal-payments-prod ends with payments-prod; its own overlap is not payments-prod's problem.
        self.write("landing-zones/internal/_workload.yaml", WORKLOAD)
        self.write("landing-zones/internal/payments-prod.yaml", "subscription: 44444444-4444-4444-4444-444444444444\n")
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        plan["internal-payments-prod"]["range"] = vending.IPv4Network("10.100.0.0/24")  # inside the hub's range
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))
        self.assertEqual(self.problems(), [])

    def test_an_overlap_with_another_landing_zone_is_reported(self):
        self.write("landing-zones/internal/_workload.yaml", WORKLOAD)
        self.write("landing-zones/internal/web.yaml", "subscription: 44444444-4444-4444-4444-444444444444\n")
        plan, _, retired = vending.allocate(self.repo, PLATFORM)
        plan["internal-web"]["range"] = plan["payments-prod"]["range"]
        self.write(vending.IP_PLAN, vending.render_plan(plan, retired))
        self.assertIn("payments-prod's range 10.100.8.0/24 overlaps internal-web's 10.100.8.0/24", "\n".join(self.problems()))

    def test_without_the_contracts_a_missing_required_setting_is_still_reported(self):
        self.write("landing-zones/payments/_workload.yaml", "cost_center: '4711'\narchetype: corp\n")
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


if __name__ == "__main__":
    unittest.main()
