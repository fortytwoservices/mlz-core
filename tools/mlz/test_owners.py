"""Tests for the code owners check. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz import owners
from mlz.merge import ConfigError

UNKNOWN = {"line": 4, "column": 12, "kind": "Unknown owner", "path": ".github/CODEOWNERS",
           "source": "/firewall/ @acme/network\n",
           "message": "Unknown owner on line 4: make sure the team @acme/network exists, is publicly visible, and has write access to the repository\n\n  /firewall/ @acme/network\n             ^"}


class FakeGitHub:
    """GitHub's answers about CODEOWNERS, one per call: None until it has read the file, then its errors."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def codeowners_errors(self, repo, ref):
        self.calls.append((repo, ref))
        return self.answers.pop(0) if self.answers else None


class OwnersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.printed, self.waits = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, github):
        owners.check(github, "acme/mlz-platform", "abc123", self.repo, self.printed.append, self.waits.append)

    def owned(self, text: str = "* @acme/platform\n") -> None:
        (self.repo / ".github").mkdir(exist_ok=True)
        (self.repo / ".github" / "CODEOWNERS").write_text(text)

    def test_the_file_install_writes(self):
        text = owners.render("acme", "platform")
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        # The platform team owns everything; _generated/ has no owner, so contracts still merge themselves.
        self.assertEqual(lines, ["*             @acme/platform", "/_generated/"])

    def test_a_file_github_reads_whole_passes(self):
        self.owned()
        self.check(FakeGitHub([]))
        self.assertEqual(self.printed, [".github/CODEOWNERS: GitHub reads every line"])

    def test_a_line_github_skips_stops_the_run_and_says_how_to_fix_it(self):
        self.owned()
        with self.assertRaises(ConfigError) as caught:
            self.check(FakeGitHub([UNKNOWN]))
        message = str(caught.exception)
        self.assertIn("a path whose line it skips has no owner at all", message)
        self.assertIn(".github/CODEOWNERS line 4: Unknown owner on line 4: make sure the team @acme/network exists, is publicly visible, "
                      "and has write access to the repository. A team needs write access to acme/mlz-platform before CODEOWNERS can name it: "
                      "an admin gives it with gh api -X PUT orgs/acme/teams/<team>/repos/acme/mlz-platform -f permission=push", message)

    def test_it_waits_while_github_has_not_read_a_new_file(self):
        self.owned()
        github = FakeGitHub(None, None, [])
        self.check(github)
        self.assertEqual(github.calls, [("acme/mlz-platform", "abc123")] * 3)  # at the run's own commit
        self.assertEqual(self.waits, [owners.WAIT] * 2)

    def test_a_file_github_never_reads_stops_the_run(self):
        self.owned()
        with self.assertRaisesRegex(ConfigError, "GitHub has not read .github/CODEOWNERS at abc123 yet"):
            self.check(FakeGitHub())
        self.assertEqual(len(self.waits), owners.TRIES - 1)

    def test_a_repo_without_the_file_is_only_noted(self):
        github = FakeGitHub()
        self.check(github)
        self.assertEqual(github.calls, [])
        self.assertIn("::notice::No .github/CODEOWNERS: anyone with write access may approve a change to this repo.", self.printed[0])
        # GitHub reads it from the repo's root or docs/ too.
        (self.repo / "CODEOWNERS").write_text("* @acme/platform\n")
        self.check(FakeGitHub([]))
        self.assertEqual(self.printed[-1], ".github/CODEOWNERS: GitHub reads every line")


if __name__ == "__main__":
    unittest.main()
