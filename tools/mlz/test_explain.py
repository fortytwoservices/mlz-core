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

    def test_the_rest_comes_from_the_contracts_and_the_ip_plan(self):
        rows = self.rows()
        self.assertEqual(rows["management group"], ("demo-lz-corp", "governance's contract, for archetype corp"))
        self.assertEqual(rows["tag Environment"], ("prod", "from the file name"))
        self.assertEqual(rows["address range"], ("10.100.4.0/24", "the IP plan (_generated/ip-plan.yaml)"))
        self.assertEqual(rows["subnet default"], ("10.100.4.0/26", "the IP plan"))
        self.assertEqual(rows["route default"], ("0.0.0.0/0 via 10.100.0.4", "the platform's contract, for norwayeast"))

    def test_without_the_contracts_it_says_what_waits_for_them(self):
        rows = self.rows(governance=None, platform=None)
        self.assertEqual(rows["management group"][0], "not known yet")
        self.assertIn("waits for the platform's contract", rows["hub"][1])
        self.assertEqual(rows["address range"][0], "10.100.4.0/24")  # the IP plan is the repo's own

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
