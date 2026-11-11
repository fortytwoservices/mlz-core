"""Tests for mlz new landing-zone. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mlz import new, vending
from mlz.__main__ import main
from mlz.merge import ConfigError
from mlz.test_vending import DEFAULTS, PROD, WORKLOAD

NOTHING = {k: None for k in ("owner", "cost_center", "archetype", "location", "subscription")}


class NewLandingZoneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.write("landing-zones/_defaults/all.yaml", DEFAULTS)
        self.write("landing-zones/payments/_workload.yaml", WORKLOAD)
        self.write("landing-zones/payments/prod.yaml", PROD)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def read(self, relative: str) -> str:
        return (self.repo / relative).read_text()

    def test_a_new_workload_gets_its_required_settings_commented_out(self):
        written = new.landing_zone(self.repo, "billing", "prod", NOTHING)
        self.assertEqual([p.name for p in written], ["_workload.yaml", "prod.yaml"])
        workload = self.read("landing-zones/billing/_workload.yaml")
        self.assertIn("# owner: team@example.com", workload)
        self.assertIn("# archetype: corp", workload)
        self.assertNotIn("location", workload)  # _defaults/all.yaml sets it
        env = self.read("landing-zones/billing/prod.yaml")
        self.assertIn("# subscription: 00000000-0000-0000-0000-000000000000", env)
        # Commented out, they read as not set: the check names each one until someone fills it in.
        lz = vending.landing_zones(self.repo)["billing-prod"]
        self.assertEqual(lz["settings"], vending.landing_zones(self.repo)["billing-prod"]["settings"])
        self.assertNotIn("owner", lz["settings"])

    def test_given_settings_are_written_and_one_a_layer_above_has_is_left_out(self):
        given = {**NOTHING, "subscription": "55555555-5555-5555-5555-555555555555", "cost_center": "4711", "archetype": "online"}
        new.landing_zone(self.repo, "payments", "dev", given)
        settings = vending.landing_zones(self.repo)["payments-dev"]["settings"]
        self.assertEqual(settings["subscription"], "55555555-5555-5555-5555-555555555555")
        self.assertEqual(settings["archetype"], "online")  # given, over corp from _workload.yaml
        self.assertEqual(settings["cost_center"], "4711")  # from _workload.yaml: the same value is not repeated
        self.assertNotIn("cost_center", self.read("landing-zones/payments/dev.yaml"))
        self.assertFalse((self.repo / "landing-zones/payments/_workload.yaml").read_text() != WORKLOAD)

    def test_a_cost_center_of_digits_stays_text(self):
        new.landing_zone(self.repo, "billing", "prod", {**NOTHING, "cost_center": "0042"})
        self.assertEqual(vending.landing_zones(self.repo)["billing-prod"]["settings"]["cost_center"], "0042")

    def test_an_existing_landing_zone_or_key_is_refused(self):
        with self.assertRaisesRegex(ConfigError, "is the landing zone payments-prod already"):
            new.landing_zone(self.repo, "payments", "prod", NOTHING)
        # payments-api/prod would be the key payments-api-prod, which payments/api-prod already is.
        self.write("landing-zones/payments/api-prod.yaml", "subscription: 66666666-6666-6666-6666-666666666666\n")
        with self.assertRaisesRegex(ConfigError, "is the landing zone payments-api-prod already"):
            new.landing_zone(self.repo, "payments-api", "prod", NOTHING)

    def test_a_name_azure_names_cannot_take_is_refused(self):
        with self.assertRaisesRegex(ConfigError, "workload 'Billing'"):
            new.landing_zone(self.repo, "Billing", "prod", NOTHING)

    def test_the_command_writes_the_files_and_runs_the_check(self):
        out, err = io.StringIO(), io.StringIO()
        argv = ["mlz", "new", "landing-zone", "--repo", str(self.repo), "billing", "prod", "--owner", "billing@demo.example"]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 0)
        self.assertIn("mlz new: wrote", out.getvalue())
        self.assertIn("cost_center is required", out.getvalue())
        self.assertNotIn("owner is required", out.getvalue())
        self.assertIn("owner: billing@demo.example", self.read("landing-zones/billing/_workload.yaml"))


if __name__ == "__main__":
    unittest.main()
