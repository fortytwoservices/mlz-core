"""Tests for plans.py: a group of stacks planned one after the other in one job, each as one job per stack did. plans.sh's
tests, unchanged but for the script they run and the fakes of the plan and comment steps, which are Python as plan.py
and comment.py are."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLANS = Path(__file__).resolve().parent / "plans.py"

# plan.py as plans.py calls it: exits with the code .fake/<stack> holds, after writing the stack's summary unless the
# plan failed. comment.py records its call, and fails for a stack named in .fake/comment-fails.
FAKE_PLAN = """import sys
from pathlib import Path
stack = sys.argv[2]
code = Path(".fake", stack).read_text().strip()
with open(".fake/calls", "a") as calls:
    calls.write(f"planned {stack}\\n")
if code.startswith("early-"):  # stopped before planning: no summary
    sys.exit(int(code[len("early-"):]))
if code != "1":
    Path(".mlz", f"summary-{stack}.md").write_text(f"**{stack}**: summary\\n")
listed = Path(".fake", f"{stack}.changes")  # the change list summary.py would write
if listed.exists():
    Path(".mlz", f"changes-{stack}.txt").write_text(listed.read_text())
sys.exit(int(code))
"""
FAKE_COMMENT = """import sys
from pathlib import Path
with open(".fake/calls", "a") as calls:
    calls.write("comment " + " ".join(sys.argv[1:]) + "\\n")
fails = Path(".fake/comment-fails")
sys.exit(1 if fails.exists() and sys.argv[1] in fails.read_text().split() else 0)
"""


class PlansTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        ci = self.dir / ".mlz-core" / "tools" / "ci"
        ci.mkdir(parents=True)
        for name, text in (("plan.py", FAKE_PLAN), ("comment.py", FAKE_COMMENT)):
            (ci / name).write_text(text)
            (ci / name).chmod(0o755)
        (self.dir / ".fake").mkdir()
        (self.dir / ".mlz").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_plans(self, codes: dict[str, int | str], event: str) -> tuple[int, str, str]:
        for stack, code in codes.items():
            (self.dir / ".fake" / stack).write_text(f"{code}\n")
        summary = self.dir / "step-summary.md"
        env = {**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_STEP_SUMMARY": str(summary)}
        done = subprocess.run([sys.executable, str(PLANS), "vending", *codes], cwd=self.dir, env=env, capture_output=True, text=True)
        calls = (self.dir / ".fake" / "calls").read_text() if (self.dir / ".fake" / "calls").exists() else ""
        return done.returncode, done.stdout, calls + (summary.read_text() if summary.exists() else "")

    def status(self, stack: str) -> str | None:
        path = self.dir / ".mlz" / f"status-{stack}.txt"
        return path.read_text().strip() if path.exists() else None

    def test_the_daily_check_plans_every_stack_records_each_status_and_fails_on_drift(self):
        code, out, record = self.run_plans({"a-prod": 0, "b-prod": 2, "c-prod": 1, "d-prod": 3, "e-prod": 0}, "schedule")
        self.assertEqual(code, 1)
        self.assertEqual([line for line in record.splitlines() if line.startswith("planned")],
                         ["planned a-prod", "planned b-prod", "planned c-prod", "planned d-prod", "planned e-prod"])  # a failure stops none
        self.assertEqual([self.status(s) for s in ("a-prod", "b-prod", "c-prod", "d-prod", "e-prod")], ["0", "2", "1", "3", "0"])
        self.assertIn("::error::b-prod: drift: Azure differs from main.", out)
        self.assertIn("::error::c-prod: the plan failed.", out)
        self.assertIn("::error::d-prod: the plan is refused", out)
        self.assertIn("not as they should be: b-prod c-prod d-prod.", out)
        self.assertIn("**a-prod**: summary", record)  # on the run page
        self.assertNotIn("**c-prod**", record)  # a failed plan has no summary
        self.assertNotIn("comment", record)  # comments only on a pull request

    def test_a_plan_that_stopped_early_is_a_failure_whatever_its_code_and_the_next_stack_is_planned(self):
        code, out, record = self.run_plans({"a-prod": "early-2", "b-prod": "early-127", "c-prod": 0}, "schedule")
        self.assertEqual(code, 1)
        self.assertEqual([self.status(s) for s in ("a-prod", "b-prod", "c-prod")], ["1", "1", "0"])  # not drift
        self.assertIn("planned c-prod", record)
        self.assertIn("::error::a-prod: the plan failed.", out)
        self.assertNotIn("drift", out)

    def test_the_daily_check_counts_no_drift_for_updates_only_to_unknown_values(self):
        # They may well end unchanged (abc's role assignments, 19 November): the run page says how many, the drift issue
        # sees none. Beside any other change, the stack has drifted as before.
        (self.dir / ".fake" / "a-prod.changes").write_text("unknown azapi_resource.role 11\nunknown azapi_resource.role2 12\n")
        (self.dir / ".fake" / "b-prod.changes").write_text("unknown azapi_resource.role 11\nchange azapi_resource.tags 13\n")
        code, out, record = self.run_plans({"a-prod": 2, "b-prod": 2}, "schedule")
        self.assertEqual([self.status(s) for s in ("a-prod", "b-prod")], ["0", "2"])
        self.assertIn("::notice::a-prod: 2 update(s) only to values unknown until apply, which may end unchanged: no drift on their own.", out)
        self.assertIn("a-prod: 2 update(s) only to values unknown until apply, which may end unchanged; not counted as drift.", record)
        self.assertNotIn("a-prod: drift", out)
        self.assertIn("::error::b-prod: drift: Azure differs from main.", out)
        self.assertEqual(code, 1)

    def test_a_pull_request_comments_each_plan_and_changes_are_no_failure(self):
        (self.dir / ".fake" / "comment-fails").write_text("a-prod\n")  # one comment fails: the next stack is still planned
        code, out, record = self.run_plans({"a-prod": 2, "b-prod": 0}, "pull_request")
        self.assertEqual(code, 0)
        self.assertIn("comment a-prod 2 vending", record)  # the role, for the settings' difference (mlz diff)
        self.assertIn("comment b-prod 0 vending", record)
        self.assertIn("::warning::a-prod: its plan could not be put on the pull request", out)
        self.assertIsNone(self.status("a-prod"))  # statuses are the daily check's only
        self.assertIn("Planned 2 stack(s).", out)


if __name__ == "__main__":
    unittest.main()
