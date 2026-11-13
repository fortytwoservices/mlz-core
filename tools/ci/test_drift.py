"""Tests for drift.sh: the daily check's one issue, against a gh that records what it is asked to do."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DRIFT = Path(__file__).resolve().parent / "drift.sh"
TITLE = "Daily check: Azure differs from main"

# Answers `gh issue list` with $FAKE_OPEN (an issue number, or nothing) and records every call, with the body
# of a --body-file, in $FAKE_LOG.
FAKE_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_LOG"
if [[ $1 == issue && $2 == list ]]; then
  [[ -n ${FAKE_OPEN:-} ]] && echo "$FAKE_OPEN"
  exit 0
fi
for ((i = 1; i <= $#; i++)); do
  if [[ ${!i} == --body-file ]]; then next=$((i + 1)); cat "${!next}" >> "$FAKE_LOG"; fi
done
"""


class DriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "bin").mkdir()
        (self.dir / "bin" / "gh").write_text(FAKE_GH)
        (self.dir / "bin" / "gh").chmod(0o755)
        (self.dir / ".mlz").mkdir()
        self.log = self.dir / "gh.log"

    def tearDown(self):
        self.tmp.cleanup()

    def run_drift(self, statuses: dict[str, int | None], open_issue: str = "") -> str:
        for stack, status in statuses.items():
            if status is not None:
                (self.dir / ".mlz" / f"status-{stack}.txt").write_text(f"{status}\n")
        env = {**os.environ, "PATH": f"{self.dir / 'bin'}:{os.environ['PATH']}", "FAKE_LOG": str(self.log), "FAKE_OPEN": open_issue,
               "STACKS": json.dumps(list(statuses)), "GH_REPO": "acme/mlz-platform", "RUN_URL": "https://example/run/1",
               "RUNNER_TEMP": str(self.dir), "ROLE": "platform"}
        subprocess.run(["bash", str(DRIFT)], cwd=self.dir, env=env, check=True, capture_output=True, text=True)
        return self.log.read_text() if self.log.exists() else ""

    def test_drift_opens_one_issue_naming_the_stacks(self):
        log = self.run_drift({"platform": 2, "management": 0, "firewall": 2, "identity": None})
        self.assertIn(f"issue create --title {TITLE} --body-file", log)
        self.assertIn("- **Azure differs from main:** `platform`, `firewall`", log)
        self.assertIn("- **No plan:** `identity`, the plan did not finish", log)
        self.assertNotIn("`management`", log)
        self.assertIn("https://example/run/1", log)
        self.assertIn("A change in GitHub's own list of domains", log)  # the platform's firewall stack

    def test_a_refused_plan_is_reported_as_not_planned(self):
        log = self.run_drift({"platform": 3})
        self.assertIn("- **No plan:** `platform`, refused: it deletes or replaces a firewall policy", log)
        self.assertNotIn("Azure differs from main:**", log)

    def test_drift_again_comments_on_the_open_issue(self):
        log = self.run_drift({"platform": 2}, open_issue="12")
        self.assertIn("issue comment 12 --body-file", log)
        self.assertNotIn("issue create", log)

    def test_a_clean_check_closes_the_open_issue(self):
        log = self.run_drift({"platform": 0, "firewall": 0}, open_issue="12")
        self.assertIn("issue close 12 --comment The daily check of", log)
        self.assertIn("found every stack as main says: https://example/run/1", log)

    def test_a_clean_check_without_an_issue_does_nothing_more(self):
        log = self.run_drift({"platform": 0})
        self.assertEqual([line.split()[:2] for line in log.splitlines()], [["issue", "list"]])

    def test_only_its_own_issue_is_looked_for(self):
        log = self.run_drift({"platform": 0})
        self.assertIn("--author app/github-actions", log)
        self.assertIn(f'in:title "{TITLE}"', log)


if __name__ == "__main__":
    unittest.main()
