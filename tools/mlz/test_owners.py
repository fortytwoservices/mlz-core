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


def unknown(line: int, source: str, name: str) -> dict:
    """GitHub's error for an owner it cannot use: one per owner, its column (from 1) at the owner (tested 2026-11-15)."""
    return {"line": line, "column": source.index(name) + 1, "kind": "Unknown owner", "path": ".github/CODEOWNERS",
            "source": source + "\n", "message": f"Unknown owner on line {line}: make sure the team {name} exists, is publicly visible, "
            "and has write access to the repository"}


class FakeGitHub:
    """GitHub's answers about CODEOWNERS, one per call: None until it has read the file, then its errors. And the
    organization's teams: slug to privacy."""

    def __init__(self, *answers, teams=None):
        self.answers, self.calls = list(answers), []
        self.teams, self.granted, self.looked_up = teams or {}, [], []

    def codeowners_errors(self, repo, ref):
        self.calls.append((repo, ref))
        return self.answers.pop(0) if self.answers else None

    def team(self, org, slug):
        self.looked_up.append((org, slug))
        return {"slug": slug, "privacy": self.teams[slug]} if slug in self.teams else None

    def grant_team(self, org, slug, repo):
        self.granted.append((org, slug, repo))


class OwnersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.printed, self.waits = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, github, new_teams=None):
        owners.check(github, "acme/mlz-platform", "abc123", self.repo, self.printed.append, self.waits.append, new_teams)

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
                      "and has write access to the repository. The run on main gives write access to a visible team of acme that "
                      "CODEOWNERS names there; it cannot fix a team that does not exist or is secret, or a person without write access "
                      "to acme/mlz-platform", message)

    # A team the file names that cannot write yet

    def errors(self):
        # The line GitHub's own errors API answered for in fortytwo-test: two owners, one without access.
        return [unknown(2, "/firewall/ @acme/platform @acme/network", "@acme/network"), unknown(3, "/net/ @acme/network", "@acme/network")]

    def test_on_a_pull_request_a_visible_team_without_access_is_only_noted(self):
        self.owned()
        github = FakeGitHub(self.errors(), teams={"network": "closed"})
        self.check(github, "note")
        self.assertEqual(github.granted, [])  # nothing is given before the code owner's approval and the merge
        self.assertEqual(github.looked_up, [("acme", "network")])  # once, for its two lines
        self.assertEqual(self.printed, ["::notice::CODEOWNERS names acme/network, which cannot write to acme/mlz-platform yet; "
                                        "the run on main after the merge gives it write access.",
                                        ".github/CODEOWNERS: GitHub reads every line"])

    def test_on_main_it_is_given_write_access(self):
        self.owned()
        github = FakeGitHub(self.errors(), teams={"network": "closed"})
        self.check(github, "grant")
        self.assertEqual(github.granted, [("acme", "network", "acme/mlz-platform")])
        self.assertIn("acme/network given write access to acme/mlz-platform: CODEOWNERS names it, and a code owner needs it", self.printed)

    def test_a_team_it_cannot_give_access_to_still_stops_the_run(self):
        self.owned()
        cases = {
            "secret": ({"network": "secret"}, "@acme/network"),
            "missing": ({}, "@acme/network"),
            "another organization's": ({"network": "closed"}, "@other/network"),
            "a person": ({}, "@someone"),
        }
        for case, (teams, name) in cases.items():
            with self.subTest(case):
                github = FakeGitHub([unknown(2, f"/firewall/ {name}", name)], teams=teams)
                with self.assertRaisesRegex(ConfigError, "line 2: Unknown owner"):
                    self.check(github, "grant")
                self.assertEqual(github.granted, [])

    def test_without_new_teams_a_team_without_access_is_an_error(self):
        # The daily check, or a run of another branch: it neither notes nor gives access.
        self.owned()
        github = FakeGitHub(self.errors(), teams={"network": "closed"})
        with self.assertRaises(ConfigError):
            self.check(github)
        self.assertEqual((github.looked_up, github.granted), ([], []))

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
