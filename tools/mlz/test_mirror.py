"""Tests for the organization's copy of mlz-core. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import subprocess
import unittest

from mlz import mirror
from mlz.merge import ConfigError


class FakeGitHub:
    """The copy as GitHub sees it: its tags, and how its branch stands to each release (None: no branch yet)."""

    def __init__(self, repo=None, tags=(), branch=None, access=None, rulesets=()):
        self.repos = {"acme/mlz-core": repo} if repo else {}
        self.tags, self.branch, self.access, self.rulesets = set(tags), branch, access, set(rulesets)
        self.created, self.made = [], []

    def repo(self, repo):
        return self.repos.get(repo)

    def create_repo(self, owner, name, first_commit=True):
        self.created.append((f"{owner}/{name}", first_commit))
        self.repos[f"{owner}/{name}"] = {"private": True}

    def tag_exists(self, repo, tag):
        return tag in self.tags

    def compare(self, repo, base, head):
        return self.branch

    def actions_access(self, repo):
        return self.access

    def set_actions_access(self, repo, level):
        self.access = level

    def add_ruleset(self, repo, ruleset):
        if ruleset["name"] in self.rulesets:
            return False
        self.rulesets.add(ruleset["name"])
        self.made.append(ruleset)
        return True


class FakeGit:
    """git, run by mirror.py: each command recorded; a failure by the words in its arguments. A push of a tag
    makes it, and a push to the branch puts the branch at the release, in the FakeGitHub given."""

    def __init__(self, github=None, failing=()):
        self.commands, self.failing, self.github = [], failing, github

    def __call__(self, command, capture_output=False, text=False):
        self.commands.append(command)
        failed = any(word in " ".join(command) for word in self.failing)
        if not failed and self.github is not None and "push" in command:
            spec = command[-1]
            if spec.startswith("refs/tags/") and spec.endswith(spec.split(":")[0].split("/")[-1]) and "^{commit}" not in spec:
                self.github.tags.add(spec.split(":")[0].split("/")[-1])
            elif spec.endswith(f"refs/heads/{mirror.BRANCH}"):
                self.github.branch = "identical"
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "refused" if failed else "")


def pushed(git):
    return [command[-1] for command in git.commands if "push" in command]


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.printed = []

    def test_a_new_copy_is_made_empty_given_the_release_and_its_settings(self):
        github = FakeGitHub()
        git = FakeGit(github)
        mirror.ensure(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=git)
        self.assertEqual(github.created, [("acme/mlz-core", False)])  # no first commit of GitHub's to push onto
        self.assertEqual(pushed(git), ["refs/tags/v0.9.0:refs/tags/v0.9.0", "refs/tags/v0.9.0^{commit}:refs/heads/releases"])
        fetch = next(command for command in git.commands if "fetch" in command)
        self.assertIn("https://github.com/fortytwoservices/mlz-core.git", fetch)  # the release from mlz-core itself
        self.assertIn("credential.helper=!gh auth git-credential", git.commands[0])
        self.assertEqual(github.access, "organization")
        tag, branch, release_branches = github.made
        self.assertEqual((tag["target"], [rule["type"] for rule in tag["rules"]]), ("tag", ["update", "deletion"]))
        self.assertEqual((branch["target"], [rule["type"] for rule in branch["rules"]]), ("branch", ["deletion", "non_fast_forward"]))
        # No branch may be named like a release: @vX.Y.Z would match it as well as the tag.
        self.assertEqual(release_branches["conditions"]["ref_name"]["include"], ["refs/heads/v*"])
        self.assertEqual([rule["type"] for rule in release_branches["rules"]], ["creation", "update", "deletion"])

    def test_a_copy_that_has_the_release_is_left_as_it_is(self):
        github = FakeGitHub(repo={"private": True}, tags=["v0.9.0"], branch="identical", access="organization",
                            rulesets=["releases", "releases-branch", "no-release-branches"])
        git = FakeGit(github)
        mirror.ensure(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=git)
        self.assertEqual((github.created, git.commands, github.made), ([], [], []))
        self.assertEqual(self.printed, ["acme/mlz-core: has v0.9.0"])

    def test_an_older_release_leaves_the_branch_at_the_newer_one(self):
        github = FakeGitHub(repo={"private": True}, branch="ahead")
        git = FakeGit(github)
        mirror.push_release(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=git)
        self.assertEqual(pushed(git), ["refs/tags/v0.9.0:refs/tags/v0.9.0"])

    def test_a_run_after_a_failed_branch_push_finishes_it(self):
        # The tag went in, the branch did not: the next run pushes only the branch.
        github = FakeGitHub(repo={"private": True}, tags=["v0.9.0"], branch="behind")
        git = FakeGit(github)
        mirror.push_release(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=git)
        self.assertEqual(pushed(git), ["refs/tags/v0.9.0^{commit}:refs/heads/releases"])
        self.assertEqual(self.printed, ["acme/mlz-core: v0.9.0 pushed"])

    def test_a_failed_push_stops_and_says_so(self):
        with self.assertRaisesRegex(ConfigError, "git fetch of v0.9.0 from fortytwoservices/mlz-core failed: refused"):
            mirror.push_release(FakeGitHub(), "acme/mlz-core", "v0.9.0", self.printed.append, run=FakeGit(failing=["fetch"]))
        with self.assertRaisesRegex(ConfigError, "git push of v0.9.0 into acme/mlz-core failed: refused"):
            mirror.push_release(FakeGitHub(), "acme/mlz-core", "v0.9.0", self.printed.append, run=FakeGit(failing=["--quiet https://github.com/acme"]))
        github = FakeGitHub()
        with self.assertRaisesRegex(ConfigError, "git push of v0.9.0 onto acme/mlz-core's branch releases failed: refused"):
            mirror.push_release(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=FakeGit(github, failing=["refs/heads/releases"]))
        self.assertNotIn("acme/mlz-core: v0.9.0 pushed", self.printed)

    def test_a_branch_that_holds_other_commits_stops(self):
        github = FakeGitHub(repo={"private": True}, tags=["v0.9.0"], branch="diverged")
        with self.assertRaisesRegex(ConfigError, "acme/mlz-core's branch releases has commits that are not mlz-core's"):
            mirror.push_release(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=FakeGit(github))

    def test_the_workflow_the_repos_call_and_the_group_admits(self):
        self.assertEqual(mirror.workflow("acme/mlz-core", "v0.9.0"), "acme/mlz-core/.github/workflows/role.yml@v0.9.0")


if __name__ == "__main__":
    unittest.main()
