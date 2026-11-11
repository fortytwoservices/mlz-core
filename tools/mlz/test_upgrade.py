"""Tests for mlz upgrade, against a GitHub that lives in memory."""

from __future__ import annotations

import unittest
from pathlib import Path

from mlz.merge import ConfigError
from mlz.upgrade import WORKFLOW, breaking_changes, pin_of, releases_between, role_of, upgrade, version

STARTERS = Path(__file__).resolve().parents[2] / "starter"
CORE = "fortytwoservices/mlz-core"


def caller(role: str, ref: str) -> str:
    """A customer's workflow: the starter of a role, pinned to ref."""
    text = (STARTERS / f"mlz-{role}" / WORKFLOW).read_text()
    core, pinned = pin_of(text)
    return text.replace(f"{core}/.github/workflows/role.yml@{pinned}", f"{CORE}/.github/workflows/role.yml@{ref}")


def notes(tag: str, *, breaking: str | None = None) -> str:
    """Release notes as release-please writes them."""
    text = f"## [{tag[1:]}](https://github.com/{CORE}/compare/x...{tag}) (2026-11-10)\n\n\n"
    if breaking:
        text += f"### ⚠ BREAKING CHANGES\n\n* **pipeline:** {breaking}\n\n"
    return text + f"### Features\n\n* **pipeline:** something new in {tag}\n"


class FakeGitHub:
    def __init__(self, workflow: str | None, releases: dict[str, str], open_pull: str | None = None):
        self.files = {("acme/mlz-governance", WORKFLOW, "main"): (workflow, "sha-main")} if workflow else {}
        for tag in releases:
            for role in ("governance", "platform", "vending"):
                starter = caller(role, tag)  # the real starter, release-please's marker included
                assert "# x-release-please-version" in starter
                self.files[(CORE, f"starter/mlz-{role}/{WORKFLOW}", tag)] = (starter, f"sha-{tag}")
        self._releases = releases
        self._open_pull = open_pull
        self.writes: list[tuple] = []

    def default_branch(self, repo):
        return "main"

    def file(self, repo, path, ref):
        return self.files.get((repo, path, ref), (None, None))

    def releases(self, core):
        return dict(self._releases)

    def open_pull(self, repo, branch):
        return self._open_pull

    def reset_branch(self, repo, branch, base):
        self.writes.append(("branch", repo, branch, base))
        return "head-of-main"

    def commit_file(self, repo, branch, head, path, text, message):
        self.writes.append(("file", repo, branch, head, path, text, message))

    def create_pull(self, repo, branch, base, title, body):
        self.writes.append(("pull", repo, branch, base, title, body))
        return f"https://github.com/{repo}/pull/7"


class UpgradeTest(unittest.TestCase):
    def run_upgrade(self, github, target=None, dry_run=False):
        printed: list[str] = []
        url = upgrade(github, "acme/mlz-governance", target, dry_run, printed.append)
        return url, "\n".join(printed)

    def test_versions_are_release_tags_only(self):
        self.assertEqual(version("v0.1.7"), (0, 1, 7))
        self.assertLess(version("v0.1.10"), version("v0.2.0"))
        self.assertIsNone(version("feat/firewall"))
        self.assertIsNone(version("v0.1"))

    def test_reads_the_pin_and_the_role_from_every_starter(self):
        for role in ("governance", "platform", "vending"):
            text = caller(role, "v0.1.6")
            self.assertEqual(pin_of(text), (CORE, "v0.1.6"))
            self.assertEqual(role_of(text), role)

    def test_a_workflow_without_the_pin_or_the_role_is_refused(self):
        with self.assertRaisesRegex(ConfigError, "does not call mlz-core"):
            pin_of("jobs:\n  other:\n    uses: actions/checkout@v4\n")
        with self.assertRaisesRegex(ConfigError, "names no role"):
            role_of("    with:\n      stage: governance\n")

    def test_releases_between_go_forward_oldest_first_and_back_newest_first(self):
        releases = {tag: "" for tag in ("v0.1.4", "v0.1.5", "v0.1.6", "v0.1.7")}
        self.assertEqual(releases_between(releases, "v0.1.5", "v0.1.7"), ["v0.1.6", "v0.1.7"])
        self.assertEqual(releases_between(releases, "v0.1.7", "v0.1.5"), ["v0.1.7", "v0.1.6"])
        self.assertEqual(releases_between(releases, "feat/firewall", "v0.1.7"), ["v0.1.7"])

    def test_breaking_changes_come_from_their_heading_only(self):
        self.assertEqual(breaking_changes(notes("v0.2.0", breaking="the secret is passed by name")),
                         ["* **pipeline:** the secret is passed by name"])
        self.assertEqual(breaking_changes(notes("v0.1.7")), [])

    def test_opens_one_pull_request_with_the_new_starter(self):
        github = FakeGitHub(caller("governance", "v0.1.6"), {"v0.1.6": notes("v0.1.6"), "v0.1.7": notes("v0.1.7")})
        url, printed = self.run_upgrade(github)
        self.assertEqual(url, "https://github.com/acme/mlz-governance/pull/7")
        branch, written, pull = github.writes
        self.assertEqual(branch, ("branch", "acme/mlz-governance", "mlz/upgrade-v0.1.7", "main"))
        _, _, on, head, path, text, message = written
        self.assertEqual((on, head, path, message), ("mlz/upgrade-v0.1.7", "head-of-main", WORKFLOW, "chore: mlz-core v0.1.7"))
        self.assertEqual(pin_of(text), (CORE, "v0.1.7"))
        self.assertNotIn("x-release-please", text)
        body = pull[5]
        self.assertEqual(pull[4], "chore: mlz-core v0.1.7")
        self.assertIn("from mlz-core `v0.1.6` to `v0.1.7`", body)
        self.assertIn("something new in v0.1.7", body)
        self.assertNotIn("something new in v0.1.6", body)
        self.assertIn("No breaking changes", body)
        self.assertIn("### [0.1.7]", body)  # release-please's heading, one level down
        self.assertIn("v0.1.6 -> v0.1.7", printed)

    def test_defaults_to_the_latest_release(self):
        github = FakeGitHub(caller("governance", "v0.1.5"), {t: notes(t) for t in ("v0.1.5", "v0.1.10", "v0.1.9")})
        self.run_upgrade(github)
        self.assertEqual(github.writes[0][2], "mlz/upgrade-v0.1.10")
        body = github.writes[2][5]
        self.assertLess(body.index("something new in v0.1.9"), body.index("something new in v0.1.10"))

    def test_lists_breaking_changes_before_the_notes(self):
        github = FakeGitHub(caller("governance", "v0.1.7"), {"v0.1.7": notes("v0.1.7"), "v0.2.0": notes("v0.2.0", breaking="pass the secret by name")})
        self.run_upgrade(github)
        body = github.writes[2][5]
        self.assertIn("**Breaking changes in these releases:**", body)
        self.assertLess(body.index("pass the secret by name"), body.index("## Release notes"))

    def test_a_rollback_says_so_and_lists_what_it_takes_out(self):
        github = FakeGitHub(caller("governance", "v0.1.7"), {t: notes(t) for t in ("v0.1.5", "v0.1.6", "v0.1.7")})
        self.run_upgrade(github, "v0.1.5")
        body = github.writes[2][5]
        self.assertIn("back from mlz-core `v0.1.7` to `v0.1.5`", body)
        self.assertIn("This is a rollback", body)
        self.assertIn("## Release notes (taken out)", body)
        self.assertIn("something new in v0.1.7", body)
        self.assertNotIn("something new in v0.1.5", body)

    def test_from_a_branch_shows_only_the_target(self):
        github = FakeGitHub(caller("governance", "feat/firewall"), {"v0.1.6": notes("v0.1.6"), "v0.1.7": notes("v0.1.7")})
        self.run_upgrade(github)
        body = github.writes[2][5]
        self.assertIn("pinned `feat/firewall`, which is not a release", body)
        self.assertNotIn("something new in v0.1.6", body)

    def test_nothing_happens_on_the_current_release(self):
        github = FakeGitHub(caller("governance", "v0.1.7"), {"v0.1.7": notes("v0.1.7")})
        url, printed = self.run_upgrade(github)
        self.assertIsNone(url)
        self.assertEqual(github.writes, [])
        self.assertIn("already on v0.1.7", printed)

    def test_an_open_upgrade_is_not_opened_twice(self):
        github = FakeGitHub(caller("governance", "v0.1.6"), {"v0.1.6": "", "v0.1.7": ""}, open_pull="https://github.com/acme/mlz-governance/pull/3")
        url, printed = self.run_upgrade(github)
        self.assertEqual(url, "https://github.com/acme/mlz-governance/pull/3")
        self.assertEqual(github.writes, [])
        self.assertIn("already open", printed)

    def test_a_dry_run_shows_the_change_and_writes_nothing(self):
        github = FakeGitHub(caller("governance", "v0.1.6"), {"v0.1.6": notes("v0.1.6"), "v0.1.7": notes("v0.1.7")})
        url, printed = self.run_upgrade(github, dry_run=True)
        self.assertIsNone(url)
        self.assertEqual(github.writes, [])
        self.assertIn("-    uses: fortytwoservices/mlz-core/.github/workflows/role.yml@v0.1.6", printed)
        self.assertIn("+    uses: fortytwoservices/mlz-core/.github/workflows/role.yml@v0.1.7", printed)
        self.assertIn("## Before you merge", printed)

    def test_refuses_a_target_that_is_not_a_release(self):
        github = FakeGitHub(caller("governance", "v0.1.6"), {"v0.1.6": "", "v0.1.7": ""})
        with self.assertRaisesRegex(ConfigError, "v0.9.9 is not a release of fortytwoservices/mlz-core; releases: v0.1.6, v0.1.7"):
            self.run_upgrade(github, "v0.9.9")
        self.assertEqual(github.writes, [])

    def test_refuses_a_repo_without_the_workflow(self):
        with self.assertRaisesRegex(ConfigError, "has no .github/workflows/mlz.yml on main"):
            self.run_upgrade(FakeGitHub(None, {"v0.1.7": ""}))


if __name__ == "__main__":
    unittest.main()
