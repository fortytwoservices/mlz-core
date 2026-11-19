"""Tests for plans.py: a group of stacks planned one after the other in one job, each as one job per stack did. plans.sh's
tests, unchanged but for the script they run and the comment step's fake, which is Python as comment.py is."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLANS = Path(__file__).resolve().parent / "plans.py"

# plan.sh as plans.py calls it: exits with the code .fake/<stack> holds, after writing the stack's summary unless the
# plan failed. comment.py records its call, and fails for a stack named in .fake/comment-fails.
FAKE_PLAN = """#!/usr/bin/env bash
code=$(cat ".fake/$2")
echo "planned $2" >> .fake/calls
[[ $code == early-* ]] && exit "${code#early-}"  # stopped before planning: no summary
[[ $code != 1 ]] && echo "**$2**: summary" > ".mlz/summary-$2.md"
exit "$code"
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
        for name, text in (("plan.sh", FAKE_PLAN), ("comment.py", FAKE_COMMENT)):
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
