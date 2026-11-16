"""Tests for the organization's copy of mlz-core. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import subprocess
import unittest

from mlz import mirror
from mlz.merge import ConfigError


class FakeGitHub:
    def __init__(self, repo=None, tags=(), access=None, rulesets=()):
        self.repos = {"acme/mlz-core": repo} if repo else {}
        self.tags, self.access, self.rulesets = set(tags), access, set(rulesets)
        self.created, self.made = [], []

    def repo(self, repo):
        return self.repos.get(repo)

    def create_repo(self, owner, name, first_commit=True):
        self.created.append((f"{owner}/{name}", first_commit))
        self.repos[f"{owner}/{name}"] = {"private": True}

    def tag_exists(self, repo, tag):
        return (repo, tag) in self.tags

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
    """git, run by mirror.py: each command recorded; a failure by the words in its arguments."""

    def __init__(self, failing=()):
        self.commands, self.failing = [], failing

    def __call__(self, command, capture_output=False, text=False):
        self.commands.append(command)
        failed = any(word in " ".join(command) for word in self.failing)
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "refused" if failed else "")


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.printed = []

    def test_a_new_copy_is_made_empty_given_the_release_and_its_settings(self):
        github, git = FakeGitHub(), FakeGit()
        mirror.ensure(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=git)
        self.assertEqual(github.created, [("acme/mlz-core", False)])  # no first commit of GitHub's to push onto
        pushes = [command[command.index("push") + 3:] for command in git.commands if "push" in command]
        self.assertEqual(pushes, [["refs/tags/v0.9.0:refs/tags/v0.9.0"], ["refs/tags/v0.9.0^{commit}:refs/heads/releases"]])
        fetch = next(command for command in git.commands if "fetch" in command)
        self.assertIn("https://github.com/fortytwoservices/mlz-core.git", fetch)  # the release from mlz-core itself
        self.assertIn("credential.helper=!gh auth git-credential", git.commands[0])
        self.assertEqual(github.access, "organization")
        tag, branch = github.made
        self.assertEqual((tag["target"], [rule["type"] for rule in tag["rules"]]), ("tag", ["update", "deletion"]))
        self.assertEqual((branch["target"], [rule["type"] for rule in branch["rules"]]), ("branch", ["deletion", "non_fast_forward"]))

    def test_a_copy_that_has_the_release_is_left_as_it_is(self):
        github = FakeGitHub(repo={"private": True}, tags=[("acme/mlz-core", "v0.9.0")], access="organization", rulesets=["releases", "releases-branch"])
        git = FakeGit()
        mirror.ensure(github, "acme/mlz-core", "v0.9.0", self.printed.append, run=git)
        self.assertEqual((github.created, git.commands, github.made), ([], [], []))
        self.assertEqual(self.printed, ["acme/mlz-core: has v0.9.0"])

    def test_an_older_release_leaves_the_branch_at_the_newer_one(self):
        git = FakeGit(failing=["refs/heads/releases"])
        mirror.push_release(FakeGitHub(repo={"private": True}), "acme/mlz-core", "v0.8.0", self.printed.append, run=git)
        self.assertEqual(self.printed, ["acme/mlz-core: v0.8.0 pushed, its branch releases left at a newer release"])

    def test_a_failed_fetch_or_tag_push_stops(self):
        with self.assertRaisesRegex(ConfigError, "git fetch of v0.9.0 from fortytwoservices/mlz-core failed: refused"):
            mirror.push_release(FakeGitHub(), "acme/mlz-core", "v0.9.0", self.printed.append, run=FakeGit(failing=["fetch"]))
        with self.assertRaisesRegex(ConfigError, "git push of v0.9.0 into acme/mlz-core failed: refused"):
            mirror.push_release(FakeGitHub(), "acme/mlz-core", "v0.9.0", self.printed.append, run=FakeGit(failing=["--quiet https://github.com/acme"]))

    def test_the_workflow_the_repos_call_and_the_group_admits(self):
        self.assertEqual(mirror.workflow("acme/mlz-core", "v0.9.0"), "acme/mlz-core/.github/workflows/role.yml@v0.9.0")


if __name__ == "__main__":
    unittest.main()
