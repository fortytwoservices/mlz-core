"""Tests for the code owners: the file mlz install repos writes, and every run's offline check that it is still that
(decision D8). Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz import owners

UNKNOWN = {"line": 4, "column": 12, "kind": "Unknown owner", "path": ".github/CODEOWNERS",
           "source": "*             @acme/platform\n",
           "message": "Unknown owner on line 4: make sure the team @acme/platform exists, is publicly visible, and has write access to the repository\n\n  *  @acme/platform\n             ^"}


class OwnersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def owned(self, text: str) -> None:
        (self.repo / ".github").mkdir(exist_ok=True)
        (self.repo / ".github" / "CODEOWNERS").write_text(text)

    def test_the_file_install_writes(self):
        # The platform team owns everything; _generated/ has no owner, so contracts still merge themselves.
        self.assertEqual(owners.rules(owners.render("acme", "platform")), [["*", "@acme/platform"], ["/_generated/"]])

    def test_the_installer_s_file_passes_with_its_comments_or_without(self):
        self.owned(owners.render("acme", "platform"))
        self.assertEqual(owners.offline_problems(self.repo), [])
        self.assertEqual(owners.offline_problems(self.repo, "acme", "platform"), [])
        # As every repo set up before 19 November has it: the old comments, the same two rules.
        self.owned("# Who must approve a change. A line below hands one path to another team.\n*             @acme/platform\n\n/_generated/\n")
        self.assertEqual(owners.offline_problems(self.repo), [])

    def test_a_rule_s_own_comment_is_no_owner(self):
        # GitHub reads a # after a space as a comment, on a rule's line too.
        self.owned("* @acme/platform # the platform team\n/_generated/   # the contracts\n")
        self.assertEqual(owners.offline_problems(self.repo, "acme", "platform"), [])
        self.assertEqual(owners.rules("/a#b @acme/x\n"), [["/a#b", "@acme/x"]])  # a # inside a pattern stays

    def test_a_handover_line_fails_and_says_what_to_remove(self):
        self.owned("*                   @acme/platform\n/firewall-rules/    @acme/network\n/_generated/\n")
        found = owners.offline_problems(self.repo)
        self.assertEqual(len(found), 1)
        self.assertIn("is not what mlz install repos writes: exactly `* @<org>/<platform team>` and `/_generated/`", found[0])
        self.assertIn("handing a path to another team is gone (decision D8). Remove `/firewall-rules/ @acme/network`.", found[0])

    def test_a_person_two_owners_or_another_owner_for_the_contracts_fails(self):
        for text in ("* @someone\n/_generated/\n", "* @acme/platform @acme/other\n/_generated/\n",
                     "* @acme/platform\n/_generated/ @acme/other\n", "* @acme/platform\n", "/_generated/\n* @acme/platform\n"):
            self.owned(text)
            self.assertEqual(len(owners.offline_problems(self.repo)), 1, text)

    def test_the_contracts_have_no_owner_on_enterprise_and_the_platform_team_on_team(self):
        enterprise, team = owners.render("acme", "platform"), owners.render("acme", "platform", "team")
        self.assertIn("\n/_generated/\n", enterprise)
        self.assertIn("\n/_generated/  @acme/platform\n", team)
        self.assertEqual((owners.generated_owned(enterprise), owners.generated_owned(team), owners.generated_owned("* @acme/platform\n")), (False, True, None))
        # Either form passes offline, so a switch can change bootstrap.yaml and this file in pull requests of their own;
        # mlz install check enforces the plan's.
        for text in (enterprise, team):
            self.owned(text)
            self.assertEqual(owners.offline_problems(self.repo, "acme", "platform"), [], text)

    def test_in_the_governance_repo_it_must_be_bootstrap_yaml_s_platform_team(self):
        self.owned(owners.render("acme", "platform"))
        self.assertEqual(owners.offline_problems(self.repo, "acme", "Platform"), [])  # GitHub's names ignore case
        self.assertEqual(owners.offline_problems(self.repo, "acme", "network"),
                         [".github/CODEOWNERS names @acme/platform as the owner, but bootstrap.yaml's platform team is @acme/network."])

    def test_the_file_github_reads_is_checked_wherever_it_is(self):
        # An adopted repo's own file at the top, or in docs/, which GitHub reads when .github/ has none.
        for location in ("CODEOWNERS", "docs/CODEOWNERS"):
            (self.repo / location).parent.mkdir(exist_ok=True)
            (self.repo / location).write_text("* @acme/platform\n/connectivity/ @acme/network\n/_generated/\n")
            found = owners.offline_problems(self.repo)
            self.assertEqual(len(found), 1, location)
            self.assertTrue(found[0].startswith(f"{location} is not what mlz install repos writes"), found[0])
            (self.repo / location).unlink()
        # .github/'s comes first, as on GitHub: a stray one elsewhere is not read.
        self.owned(owners.render("acme", "platform"))
        (self.repo / "CODEOWNERS").write_text("* @acme/other\n")
        self.assertEqual(owners.offline_problems(self.repo), [])

    def test_a_repo_without_the_file_has_no_problem_here(self):
        # mlz install repos offers it, and mlz install check says it is missing.
        self.assertEqual(owners.offline_problems(self.repo), [])

    def test_github_s_errors_say_what_to_do(self):
        self.assertEqual(owners.problems([UNKNOWN], "acme/mlz-platform"),
                         [".github/CODEOWNERS line 4: Unknown owner on line 4: make sure the team @acme/platform exists, is publicly "
                          "visible, and has write access to the repository. The platform team must be a visible team of acme that "
                          "can write to acme/mlz-platform; mlz install repos gives it write access"])


if __name__ == "__main__":
    unittest.main()
