"""Tests for plans.sh: a group of stacks planned one after the other in one job, each as one job per stack did."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PLANS = Path(__file__).resolve().parent / "plans.sh"

# plan.sh as plans.sh calls it: exits with the code .fake/<stack> holds, after writing the stack's summary unless the
# plan failed. comment.sh records its call, and fails for a stack named in .fake/comment-fails.
FAKE_PLAN = """#!/usr/bin/env bash
code=$(cat ".fake/$2")
echo "planned $2" >> .fake/calls
[[ $code != 1 ]] && echo "**$2**: summary" > ".mlz/summary-$2.md"
exit "$code"
"""
FAKE_COMMENT = """#!/usr/bin/env bash
echo "comment $1 $2" >> .fake/calls
grep -qx "$1" .fake/comment-fails 2>/dev/null && exit 1
exit 0
"""


class PlansTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        ci = self.dir / ".mlz-core" / "tools" / "ci"
        ci.mkdir(parents=True)
        for name, text in (("plan.sh", FAKE_PLAN), ("comment.sh", FAKE_COMMENT)):
            (ci / name).write_text(text)
            (ci / name).chmod(0o755)
        (self.dir / ".fake").mkdir()
        (self.dir / ".mlz").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_plans(self, codes: dict[str, int], event: str) -> tuple[int, str, str]:
        for stack, code in codes.items():
            (self.dir / ".fake" / stack).write_text(f"{code}\n")
        summary = self.dir / "step-summary.md"
        env = {**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_STEP_SUMMARY": str(summary)}
        done = subprocess.run(["bash", str(PLANS), "vending", *codes], cwd=self.dir, env=env, capture_output=True, text=True)
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

    def test_a_pull_request_comments_each_plan_and_changes_are_no_failure(self):
        (self.dir / ".fake" / "comment-fails").write_text("a-prod\n")  # one comment fails: the next stack is still planned
        code, out, record = self.run_plans({"a-prod": 2, "b-prod": 0}, "pull_request")
        self.assertEqual(code, 0)
        self.assertIn("comment a-prod 2", record)
        self.assertIn("comment b-prod 0", record)
        self.assertIn("::warning::a-prod: its plan could not be put on the pull request", out)
        self.assertIsNone(self.status("a-prod"))  # statuses are the daily check's only
        self.assertIn("Planned 2 stack(s).", out)


if __name__ == "__main__":
    unittest.main()
