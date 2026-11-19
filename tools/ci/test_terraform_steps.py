"""Tests for plan.py and apply.py, the Terraform around a plan and an apply, against a fake az and terraform that record
what they are asked: one test for each way plan.sh and apply.sh could end. The refusals themselves are apply_guard.py's
and summary.py's, with tests of their own; here they are stood in for."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import apply, apply_guard, plan

STATE = json.dumps({"lineage": "abc", "serial": 4, "resources": [{"type": "x"}]})


class Terraform:
    """az and terraform as plan.py and apply.py call them: each call recorded; exists answers `blob`, plan exits with
    `plan`, apply with `apply`, and any command named in `fails` fails."""

    def __init__(self, blob="false", plan=0, apply=0, fails=()):
        self.calls: list[list[str]] = []
        self.blob, self.plan, self.apply, self.fails = blob, plan, apply, fails

    def __call__(self, command, stdout=None, text=False, check=False, **kwargs):
        self.calls.append(command)
        verb = self.verb(command)
        if verb in self.fails:
            if check:
                raise subprocess.CalledProcessError(4, command)
            return subprocess.CompletedProcess(command, 4, "")
        out = ""
        if verb == "exists":
            out = self.blob + "\n"
        elif verb == "download":
            Path(command[command.index("--file") + 1]).write_text(STATE)
        elif verb == "plan":
            Path(next(c for c in command if c.startswith("-out=")).split("=", 1)[1]).write_text("plan")
            return subprocess.CompletedProcess(command, self.plan, "")
        elif verb == "show" and stdout is not None and hasattr(stdout, "write"):
            stdout.write("{}" if "-json" in command else "the plan\n")
        elif verb == "pull":
            out = STATE
        elif verb == "apply":
            return subprocess.CompletedProcess(command, self.apply, "")
        return subprocess.CompletedProcess(command, 0, out)

    @staticmethod
    def verb(command: list[str]) -> str:
        """terraform's subcommand (state pull's second word), or az storage blob's."""
        if command[0] == "az":
            return command[3]
        return command[3] if command[2] == "state" else command[2]

    def verbs(self) -> list[str]:
        return [self.verb(c) if c[0] == "terraform" else f"az {self.verb(c)}" for c in self.calls]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()  # as Path.cwd() gives it (macOS: /private/var)
        self.root = self.dir / "root"
        self.root.mkdir()
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, self.cwd)
        for patch in (mock.patch.dict(os.environ, {"TFSTATE_STORAGE_ACCOUNT": "stacme", "TFSTATE_CONTAINER": "tfstate"}),
                      mock.patch("mlz.stacks.root", return_value=self.root)):
            patch.start()
            self.addCleanup(patch.stop)
        self.work = self.dir / ".mlz"

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()


class PlanTest(Case):
    def plan(self, fake: Terraform, summary: int = 0) -> int:
        with mock.patch.object(plan.summary, "main", return_value=summary) as checked:
            self.checked = checked
            return plan.plan("vending", "payments-prod", run=fake)

    def test_a_new_stack_plans_from_no_state(self):
        fake = Terraform(blob="false", plan=2)
        self.assertEqual(self.plan(fake), 2)
        self.assertEqual(fake.verbs(), ["az exists", "init", "plan", "show", "show"])  # nothing to download
        self.assertEqual((self.work / "version-payments-prod.txt").read_text(), "none\n")
        self.assertIn(f'path = "{self.work}/state/payments-prod.tfstate"', (self.root / "backend_override.tf").read_text())
        planned = fake.calls[2]
        self.assertIn("-detailed-exitcode", planned)
        self.assertIn("-lock=false", planned)
        self.assertEqual(planned[-3:], ["-var", f"config_file={self.work}/vending/payments-prod.json", f"-out={self.work}/plans/payments-prod.tfplan"])
        self.assertEqual((self.work / "plan-payments-prod.txt").read_text(), "the plan\n")
        self.assertEqual(self.checked.call_args.args[1:], ("payments-prod", f"{self.work}/summary-payments-prod.md", f"{self.work}/changes-payments-prod.txt"))
        self.assertEqual(list((self.work / "plans").iterdir()), [])  # the binary plan and its JSON go once summarised

    def test_a_stack_with_state_plans_from_its_copy_and_records_its_version(self):
        fake = Terraform(blob="true", plan=0)
        self.assertEqual(self.plan(fake), 0)
        self.assertEqual(fake.verbs()[:2], ["az exists", "az download"])
        self.assertIn("--auth-mode", fake.calls[0])
        self.assertEqual(fake.calls[0][fake.calls[0].index("--account-name") + 1], "stacme")
        self.assertEqual((self.work / "version-payments-prod.txt").read_text(), "abc 4\n")

    def test_a_failed_plan_exits_1_with_no_summary(self):
        fake = Terraform(plan=1)
        self.assertEqual(self.plan(fake), 1)
        self.assertNotIn("show", fake.verbs())
        self.checked.assert_not_called()

    def test_a_refused_plan_exits_3_and_a_failed_summary_1(self):
        self.assertEqual(self.plan(Terraform(plan=2), summary=3), 3)
        self.assertEqual(self.plan(Terraform(plan=0), summary=1), 1)

    def test_a_failed_lookup_stops_before_planning(self):
        # Never plan every resource as new because the state could not be looked for.
        fake = Terraform(fails=("exists",))
        with self.assertRaises(subprocess.CalledProcessError):
            self.plan(fake)
        self.assertEqual(fake.verbs(), ["az exists"])
        self.assertFalse((self.work / "version-payments-prod.txt").exists())


class ApplyTest(Case):
    def setUp(self):
        super().setUp()
        self.said: list[str] = []

    def apply(self, fake: Terraform, before=apply_guard.GO, state=apply_guard.GO, matches=apply_guard.GO, summary=0) -> int:
        with mock.patch.object(apply.apply_guard, "before", return_value=before), \
                mock.patch.object(apply.apply_guard, "state", return_value=state) as self.state, \
                mock.patch.object(apply.apply_guard, "matches", return_value=matches), \
                mock.patch.object(apply.summary, "main", return_value=summary):
            return apply.apply("vending", "payments-prod", run=fake, say=self.said.append)

    def test_a_refusal_before_planning_applies_nothing_and_standing_down_is_no_failure(self):
        for verdict, code in ((apply_guard.REFUSE, 1), (apply_guard.STAND_DOWN, 0)):
            fake = Terraform()
            self.assertEqual(self.apply(fake, before=verdict), code)
            self.assertEqual(fake.calls, [])

    def test_a_reviewed_plan_that_still_holds_is_applied(self):
        fake = Terraform(apply=0)
        self.assertEqual(self.apply(fake), 0)
        self.assertEqual(fake.verbs(), ["init", "plan", "pull", "show", "apply"])
        self.assertEqual(fake.calls[0][-3:], ["-backend-config=storage_account_name=stacme", "-backend-config=container_name=tfstate",
                                             "-backend-config=key=payments-prod.tfstate"])
        self.assertIn(f"-out={self.work}/plans/payments-prod.tfplan", fake.calls[1])
        self.assertEqual(self.state.call_args.args[:2], ("payments-prod", "abc 4"))  # the state as this plan found it
        self.assertEqual(fake.calls[-1][-1], f"{self.work}/plans/payments-prod.tfplan")

    def test_a_failed_apply_fails(self):
        self.assertEqual(self.apply(Terraform(apply=1)), 1)

    def test_a_state_another_run_changed_applies_nothing(self):
        fake = Terraform()
        self.assertEqual(self.apply(fake, state=apply_guard.REFUSE), 1)
        self.assertNotIn("apply", fake.verbs())

    def test_a_refused_plan_says_why_and_applies_nothing(self):
        (self.dir / ".mlz").mkdir()
        (self.work / "apply-summary-payments-prod.md").write_text("**payments-prod**: deletes a firewall policy\n")
        fake = Terraform()
        self.assertEqual(self.apply(fake, summary=3), 1)
        self.assertEqual(self.said, ["::error::The plan is refused; nothing is applied.", "**payments-prod**: deletes a firewall policy"])
        self.assertNotIn("apply", fake.verbs())

    def test_a_plan_that_differs_from_the_reviewed_one_applies_nothing(self):
        fake = Terraform()
        self.assertEqual(self.apply(fake, matches=apply_guard.REFUSE), 1)
        self.assertNotIn("apply", fake.verbs())

    def test_a_failed_step_stops_the_apply(self):
        for verb in ("init", "plan", "pull"):
            fake = Terraform(fails=(verb,))
            with self.assertRaises(subprocess.CalledProcessError):
                self.apply(fake)
            self.assertNotIn("apply", fake.verbs())


if __name__ == "__main__":
    unittest.main()
