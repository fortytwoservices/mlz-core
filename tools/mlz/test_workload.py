"""Tests for the workload repos' files (tools/mlz/workload.py). Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mlz import install, workload
from mlz.merge import ConfigError

WORKLOAD = {"landing_zone": "payments-prod", "location": "norwayeast",
            "org": "acme", "app": "pay", "env": "pd", "region": "nwe", "template": "{type}-{org}-{app}-{purpose}-{env}-{region}-{nn}", "templates": {}}


DESCRIPTION = "Workload repo of landing zone payments-prod, made by mlz-core's vending."


class FakeGitHub:
    """Repos and their main branch's files, in memory. A commit made on a head main has left is refused, as GitHub's
    createCommitOnBranch refuses one whose expectedHeadOid is not the branch's. push_on: a file whose reading makes the
    team push in between, with that file in it."""

    def __init__(self, repos: dict[str, dict[str, str]], archived=(), push_on=None, core=None, pulls=None, releases=None,
                 teams=None, rulesets=None, writers=None, secret=()):
        self.repos, self.archived, self.commits, self.push_on = repos, set(archived), [], push_on
        self.heads = {repo: 0 for repo in repos}
        # mlz-core's files at its releases, {(path, ref): text}; each repo's pull requests, newest last; release notes.
        self.core, self.pulls, self._releases = core or {}, pulls or {}, releases
        self.branches: list[tuple] = []  # commits on a branch other than main: (repo, branch, files, message)
        self.refs: dict[tuple[str, str], str] = {}  # each other branch's commit
        self.resets: list[tuple] = []  # (repo, branch, commit) each time a branch is made or moved
        self.updated: list[tuple] = []
        self.at: dict[tuple[str, str], dict[str, str]] = {}  # a repo's files at a commit other than main's head
        # The organization's teams (slug to ID), each repo's own rulesets, and who may write to each repo.
        self.teams = {"acme-admins": 7, "payments-devs": 8} if teams is None else teams
        self.made_rulesets = rulesets if rulesets is not None else {}
        self.writers = writers if writers is not None else {}
        self.secret = set(secret)  # teams the organization keeps secret, which GitHub reads in no CODEOWNERS

    def app_id(self, slug):
        return {"mlz-release": 99}[slug]

    def team(self, org, slug):
        return {"slug": slug, "id": self.teams[slug], "privacy": "secret" if slug in self.secret else "closed"} if slug in self.teams else None

    def team_can_write(self, org, slug, repo):
        return slug in self.writers.get(repo, set())

    def grant_team(self, org, slug, repo):
        self.writers.setdefault(repo, set()).add(slug)

    def rulesets(self, repo):
        return [{**ruleset, "source_type": "Repository", "source": repo} for ruleset in self.made_rulesets.get(repo, [])]

    def add_ruleset(self, repo, ruleset):
        self.made_rulesets.setdefault(repo, []).append(ruleset)
        return True

    def releases(self, core):
        if isinstance(self._releases, Exception):
            raise self._releases
        return dict(self._releases or {})

    def latest_pull(self, repo, branch):
        mine = [pull for pull in self.pulls.get(repo, []) if pull["branch"] == branch]
        return mine[-1] if mine else None

    def reset_branch(self, repo, branch, base, at=None):
        sha = at or self.head(repo, base)
        self.refs[(repo, branch)] = sha
        self.resets.append((repo, branch, sha))
        return sha

    def delete_branch(self, repo, branch):
        self.refs.pop((repo, branch), None)

    def create_pull(self, repo, branch, base, title, body):
        number = len(self.pulls.get(repo, [])) + 1
        url = f"https://github.com/{repo}/pull/{number}"
        self.pulls.setdefault(repo, []).append({"branch": branch, "number": number, "title": title, "body": body, "state": "open",
                                                "merged_at": None, "created_at": "2026-11-20T06:00:00Z", "html_url": url,
                                                "head": {"sha": self.refs[(repo, branch)]}})
        return url

    def update_pull(self, repo, number, title, body):
        self.updated.append((repo, number, title))
        pull = next(pull for pull in self.pulls[repo] if pull["number"] == number)
        pull.update(title=title, body=body)

    def root_files(self, repo, ref):
        return sorted(path for path in self.repos[repo] if "/" not in path)

    def repo(self, repo):
        return {"archived": repo in self.archived} if repo in self.repos else None

    def file(self, repo, path, ref):
        if repo == workload.mirror.UPSTREAM:
            text = self.core.get((path, ref))
            return (text, "sha") if text is not None else (None, None)
        if (repo, ref) in self.at:
            text = self.at[(repo, ref)].get(path)
            return (text, "sha") if text is not None else (None, None)
        text = self.repos[repo].get(path)
        if path == self.push_on:
            self.repos[repo][path], self.heads[repo], self.push_on = "# the team's, pushed meanwhile\n", self.heads[repo] + 1, None
        return (text, "sha") if text is not None else (None, None)

    def head(self, repo, branch):
        return f"head-{self.heads[repo]}" if branch == "main" else self.refs[(repo, branch)]

    def commit_files(self, repo, branch, head, files, message):
        if branch != "main":
            if self.refs.get((repo, branch)) != head:
                raise RuntimeError("expectedHeadOid is not the branch's head")
            self.branches.append((repo, branch, dict(files), message))
            self.refs[(repo, branch)] = f"commit-{len(self.branches)}"
            self.at[(repo, self.refs[(repo, branch)])] = {**self.repos[repo], **files}
            return
        if head != f"head-{self.heads[repo]}":
            raise RuntimeError("expectedHeadOid is not the branch's head")
        self.commits.append((repo, branch, head, dict(files), message))
        self.repos[repo].update(files)
        self.heads[repo] += 1


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.configs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, key: str, **extra) -> None:
        config = {"key": key, "github": {"organization": "acme-org", "repo": key, "create": True, "team": "payments-devs", "description": DESCRIPTION,
                                         "bypass_team": "acme-admins"},
                  "workload": {**WORKLOAD, "landing_zone": key}, "decommission": False, **extra}
        (self.configs / f"{key}.json").write_text(json.dumps(config))

    def sync(self, github, app_slug="mlz-release") -> list[str]:
        out = []
        workload.sync(github, self.configs, out.append, app_slug)
        return out

    def test_a_new_repo_gets_the_starter_and_its_values_in_one_commit(self):
        self.config("payments-prod")
        github = FakeGitHub({"acme-org/payments-prod": {"README.md": f"# payments-prod\n{DESCRIPTION}\n"}})
        self.sync(github)
        [(repo, branch, _, files, message)] = github.commits
        self.assertEqual((repo, branch), ("acme-org/payments-prod", "main"))
        self.assertEqual(set(files), {*workload.starter(), "mlz.auto.tfvars.json", ".github/CODEOWNERS"})
        # GitHub's own first README is replaced; the pins are the release's, without release-please's marker.
        self.assertIn("This landing zone's own Terraform", files["README.md"])
        self.assertIn("uses: fortytwoservices/mlz-core/.github/workflows/workload.yml@v", files[".github/workflows/mlz.yml"])
        self.assertNotIn("x-release-please-version", files[".github/workflows/mlz.yml"])
        self.assertEqual(json.loads(files["mlz.auto.tfvars.json"])["mlz"]["landing_zone"], "payments-prod")
        # Again: nothing to do.
        self.sync(github)
        self.assertEqual(len(github.commits), 1)

    def test_nothing_a_team_wrote_is_replaced(self):
        self.config("payments-prod")
        # Even a README as short as GitHub's own: only GitHub's exact one is replaced.
        own = {"README.md": "# payments-prod\nContact Payments before deploying.\n", "main.tf": "# ours\n"}
        github = FakeGitHub({"acme-org/payments-prod": dict(own)})
        self.sync(github)
        files = github.commits[0][3]
        self.assertNotIn("README.md", files)
        self.assertNotIn("main.tf", files)
        self.assertIn(".github/workflows/mlz.yml", files)

    def test_a_team_s_push_while_it_reads_makes_the_commit_fail_not_overwrite(self):
        self.config("payments-prod")
        # main.tf is not there when read, and the team pushes it right after: the commit was to be made on the head
        # read first, which main has left, so GitHub refuses it.
        github = FakeGitHub({"acme-org/payments-prod": {}}, push_on="main.tf")
        with self.assertRaisesRegex(RuntimeError, "expectedHeadOid"):
            self.sync(github)
        self.assertEqual(github.repos["acme-org/payments-prod"]["main.tf"], "# the team's, pushed meanwhile\n")

    def test_after_the_first_time_only_the_values_change(self):
        self.config("payments-prod")
        github = FakeGitHub({"acme-org/payments-prod": {}})
        self.sync(github)
        github.repos["acme-org/payments-prod"]["main.tf"] = "# the team's now\n"
        github.repos["acme-org/payments-prod"].pop(".github/workflows/mlz.yml")  # even one the team removed stays removed
        self.config("payments-prod", workload={**WORKLOAD, "location": "swedencentral"})
        self.sync(github)
        [_, (_, _, head, files, message)] = github.commits
        self.assertEqual(set(files), {"mlz.auto.tfvars.json"})
        self.assertEqual(json.loads(files["mlz.auto.tfvars.json"])["mlz"]["location"], "swedencentral")
        self.assertEqual((head, message), ("head-1", "chore: the landing zone's values, from vending"))

    def test_only_repos_vending_makes_once_they_exist(self):
        self.config("payments-prod")
        self.config("legacy-prod", github={"organization": "acme-org", "repo": "legacy-infra", "create": False, "team": None, "description": DESCRIPTION})
        self.config("old-prod", decommission=True)
        self.config("archived-prod")
        self.config("none-prod", github=None, workload=None)
        github = FakeGitHub({"acme-org/legacy-infra": {}, "acme-org/old-prod": {}, "acme-org/archived-prod": {}}, archived={"acme-org/archived-prod"})
        out = self.sync(github)
        self.assertEqual(github.commits, [])
        self.assertEqual(out, ["acme-org/payments-prod: not made yet; vending's apply makes it, and the next run gives it its files"])


OLD = "v0.10.1"
TARGET = workload.release()
NOTES = {OLD: "## [0.10.1](x) (2026-11-13)\n\n### Bug Fixes\n\n* old\n",
         "v0.11.0": "## [0.11.0](x) (2026-11-18)\n\n### ⚠ BREAKING CHANGES\n\n* **vending:** something moved\n",
         TARGET: f"## [{TARGET[1:]}](x) (2026-11-19)\n\n### Features\n\n* **vending:** something new in {TARGET}\n"}


def at(text: str, tag: str) -> str:
    """A starter file as an older release had it: mlz-core's own, its pins on that release (release-please's marker
    kept, as mlz-core's repo has it)."""
    return re.sub(r"v\d+\.\d+\.\d+(?=\S* # x-release-please-version)", tag, text)


def old_starter(tag: str = OLD, without: tuple[str, ...] = ()) -> dict[str, str]:
    """The workload starter at an older release, as mlz-core's repo holds it, and as a repo vending made then has it."""
    real = {str(p.relative_to(workload.STARTER)): p.read_text() for p in sorted(workload.STARTER.rglob("*")) if p.is_file()}
    # That release's variable "mlz" had no network yet.
    real["variables.tf"] = re.sub(r"\n    # The landing zone's network.*?\n    \}\)\n", "\n", real["variables.tf"], flags=re.S)
    return {name: at(text, tag) for name, text in real.items() if name not in without}


class UpgradeTest(SyncTest):
    """A repo whose pipeline is on an older release than vending's gets one pull request to vending's release."""

    REPO = "acme-org/payments-prod"

    def pull(self, github, number: int, offers: str, state: str = "open", merged: bool = False, title: str | None = None) -> dict:
        """A pull request on mlz/upgrade from an earlier run, at a commit whose pipeline calls the release it offers."""
        sha = f"pull-{number}"
        workflow = workload.starter()[".github/workflows/mlz.yml"].replace(f"workload.yml@{TARGET}", f"workload.yml@{offers}")
        github.at[(self.REPO, sha)] = {".github/workflows/mlz.yml": workflow}
        pull = {"branch": "mlz/upgrade", "number": number, "title": title or f"chore: mlz-core {offers}", "body": "", "state": state,
                "merged_at": "2026-11-18T07:00:00Z" if merged else None, "created_at": "2026-11-18T06:00:00Z",
                "html_url": f"https://github.com/{self.REPO}/pull/{number}", "head": {"sha": sha}}
        github.pulls.setdefault(self.REPO, []).append(pull)
        return pull

    def made_at(self, tag: str = OLD, **changed) -> dict[str, str]:
        """The repo as vending made it at tag, with its values up to date, and what the team changed since."""
        files = {name: workload.RELEASE_PLEASE_MARKER.sub("", text) for name, text in old_starter(tag).items()}
        files[workload.VALUES] = workload.values_file({**WORKLOAD, "landing_zone": "payments-prod"})
        files[workload.CODEOWNERS] = workload.codeowners("acme-org", "payments-devs")
        return {**files, **changed}

    def github(self, files: dict[str, str], **kwargs) -> FakeGitHub:
        self.config("payments-prod")
        core = {(f"starter/mlz-workload/{name}", OLD): text for name, text in old_starter().items()}
        ruled = {self.REPO: [install.main_ruleset("workload", 7, 99, approvals=1), install.checks_ruleset("workload", 99, workload.CHECK)]}
        return FakeGitHub({self.REPO: files}, core=core, releases=kwargs.pop("releases", NOTES), rulesets=ruled,
                          writers={self.REPO: {"acme-admins"}}, **kwargs)

    def test_an_older_repo_gets_one_pull_request_with_the_files_it_has_not_changed(self):
        github = self.github(self.made_at())
        out = self.sync(github)
        self.assertEqual(github.commits, [])  # main is not touched: its values were up to date
        [(repo, branch, files, message)] = github.branches
        self.assertEqual((repo, branch, message), (self.REPO, "mlz/upgrade-next", f"chore: mlz-core {TARGET}"))
        # Made on main's commit as read, then the pull request's branch put onto it; the build branch goes again.
        self.assertEqual(github.resets, [(self.REPO, "mlz/upgrade-next", "head-0"), (self.REPO, "mlz/upgrade", "commit-1")])
        self.assertEqual(github.refs, {(self.REPO, "mlz/upgrade"): "commit-1"})
        self.assertEqual(set(files), {".github/workflows/mlz.yml", "main.tf", "variables.tf"})
        self.assertIn(f"workload.yml@{TARGET}\n", files[".github/workflows/mlz.yml"])
        self.assertNotIn("x-release-please", "".join(files.values()))
        self.assertEqual(files["variables.tf"], workload.starter()["variables.tf"])
        [pull] = github.pulls[self.REPO]
        self.assertEqual(pull["title"], f"chore: mlz-core {TARGET}")
        body = pull["body"]
        self.assertIn(f"from mlz-core `{OLD}` to `{TARGET}`, the release vending runs on", body)
        self.assertIn("**What changes:** `.github/workflows/mlz.yml`, `main.tf`, `variables.tf`", body)
        self.assertNotIn("Left as they are", body)
        self.assertIn("* **vending:** something moved", body)  # the breaking change, before the notes
        self.assertLess(body.index("something moved"), body.index("## Release notes"))
        self.assertIn(f"something new in {TARGET}", body)
        self.assertNotIn("* old", body)  # the release the repo is on already
        self.assertEqual(out, [f"{self.REPO}: {OLD} -> {TARGET}: https://github.com/{self.REPO}/pull/1"])

    def test_the_team_s_changed_files_stay_and_the_pull_request_names_them(self):
        own_main = 'module "group_name" {\n  source = "git::https://github.com/fortytwoservices/mlz-core.git//modules/naming?ref=v0.10.1"\n}\n'
        own_variables = old_starter()["variables.tf"].replace("# payments-prod", "# our landing zone") + (
            '\nvariable "extra" {\n  type = object({\n    network = string\n  })\n}\n')
        github = self.github(self.made_at(**{"main.tf": own_main, "variables.tf": own_variables, "app.tf": '#   source = "git::https://github.com/fortytwoservices/mlz-core.git//modules/naming?ref=v0.9.0"\n'}))
        self.sync(github)
        [(_, _, files, _)] = github.branches
        self.assertEqual(set(files), {".github/workflows/mlz.yml"})
        body = github.pulls[self.REPO][0]["body"]
        self.assertIn(f"- `variables.tf`: `{TARGET}`'s `variable \"mlz\"` also declares `network`. Terraform drops", body)
        self.assertIn(f"- `main.tf`: `{TARGET}`'s starter has another version", body)
        self.assertIn("naming module at another release: `app.tf` line 1 (`v0.9.0`, commented out), `main.tf` line 2 (`v0.10.1`)", body)

    def test_a_file_new_in_the_release_is_added_and_one_the_team_removed_stays_removed(self):
        made = self.made_at()
        del made["terraform.tf"]  # the team's own backend now
        github = self.github(made)
        github.core = {(f"starter/mlz-workload/{name}", OLD): text for name, text in old_starter(without=("README.md",)).items()}
        github.repos[self.REPO].pop("README.md")
        self.sync(github)
        files = github.branches[0][2]
        self.assertIn("README.md", files)  # not in the release the repo was made with
        self.assertNotIn("terraform.tf", files)

    def test_a_push_while_it_reads_is_never_folded_in(self):
        # main.tf is read as the starter wrote it, and the team pushes its own right after: the upgrade is made on the
        # commit read, so the pull request conflicts with the team's main.tf rather than overwriting it.
        github = self.github(self.made_at(), push_on="main.tf")
        self.sync(github)
        self.assertEqual(github.resets[0], (self.REPO, "mlz/upgrade-next", "head-0"))
        self.assertEqual(github.heads[self.REPO], 1)  # main moved on meanwhile

    def test_a_commented_out_call_does_not_decide_the_release(self):
        workflow = workload.starter()[".github/workflows/mlz.yml"].replace(
            f"    uses: fortytwoservices/mlz-core/.github/workflows/workload.yml@{TARGET}",
            f"    # uses: fortytwoservices/mlz-core/.github/workflows/workload.yml@{OLD}\n    uses: fortytwoservices/mlz-core/.github/workflows/workload.yml@v99.0.0")
        github = self.github(self.made_at(**{".github/workflows/mlz.yml": workflow}))
        self.assertEqual(self.sync(github), [])
        self.assertEqual(github.branches, [])

    def test_while_it_waits_nothing_is_opened_again(self):
        github = self.github(self.made_at())
        self.sync(github)
        out = self.sync(github)
        self.assertEqual((len(github.branches), len(github.pulls[self.REPO])), (1, 1))
        self.assertEqual(out, [f"::notice::{self.REPO} calls mlz-core {OLD}; the pull request to {TARGET} waits for its team since "
                               f"2026-11-20: https://github.com/{self.REPO}/pull/1"])

    def test_a_newer_release_moves_the_open_pull_request_on(self):
        github = self.github(self.made_at())
        self.pull(github, 3, "v0.11.0")
        self.sync(github)
        self.assertEqual(github.updated, [(self.REPO, 3, f"chore: mlz-core {TARGET}")])
        self.assertEqual(len(github.pulls[self.REPO]), 1)
        # Its branch goes straight from the old commit to the new one, never onto main, where GitHub would close it.
        self.assertNotIn((self.REPO, "mlz/upgrade", "head-0"), github.resets)
        self.assertEqual(github.refs, {(self.REPO, "mlz/upgrade"): "commit-1"})

    def test_a_pull_request_the_team_closed_is_not_opened_again_for_that_release(self):
        # Renamed before it was closed: the release it offered is read from its own pipeline, not its title.
        github = self.github(self.made_at())
        self.pull(github, 2, TARGET, state="closed", title="not now")
        out = self.sync(github)
        self.assertEqual(github.branches, [])
        self.assertIn("closed the pull request", out[0])
        # One merged earlier, to an older release, is no reason to wait.
        github = self.github(self.made_at())
        self.pull(github, 2, "v0.11.0", state="closed", merged=True)
        self.sync(github)
        self.assertEqual(len(github.pulls[self.REPO]), 2)

    def test_a_failed_update_of_the_open_pull_request_leaves_its_branch_to_try_again(self):
        github = self.github(self.made_at())
        self.pull(github, 3, "v0.11.0")
        failing = github.update_pull
        github.update_pull = lambda *args: (_ for _ in ()).throw(ConfigError("gh api PATCH pulls/3: HTTP 502"))
        out = self.sync(github)
        self.assertIn("no upgrade pull request: gh api PATCH pulls/3: HTTP 502", out[-1])
        self.assertNotIn((self.REPO, "mlz/upgrade"), github.refs)  # not moved: its title and notes still say v0.11.0
        github.update_pull = failing
        self.sync(github)
        self.assertEqual(github.updated, [(self.REPO, 3, f"chore: mlz-core {TARGET}")])
        self.assertEqual(github.refs[(self.REPO, "mlz/upgrade")], "commit-2")

    def test_an_open_pull_request_renamed_by_its_team_is_left_as_it_is(self):
        github = self.github(self.made_at())
        self.pull(github, 4, TARGET, title="chore: mlz-core upgrade, reviewed by Kari")
        out = self.sync(github)
        self.assertEqual((github.branches, github.updated), ([], []))
        self.assertIn("waits for its team", out[0])

    def test_on_vending_s_release_ahead_of_it_or_on_a_branch_nothing_is_offered(self):
        for pin in (TARGET, "v99.0.0", "main"):
            with self.subTest(pin=pin):
                workflow = workload.starter()[".github/workflows/mlz.yml"].replace(f"workload.yml@{TARGET}", f"workload.yml@{pin}")
                github = self.github(self.made_at(**{".github/workflows/mlz.yml": workflow}))
                self.assertEqual(self.sync(github), [])
                self.assertEqual(github.branches, [])

    def test_the_repo_keeps_the_mlz_core_it_calls(self):
        workflow = self.made_at()[".github/workflows/mlz.yml"].replace("fortytwoservices/mlz-core/", "acme-org/mlz-core/")
        github = self.github(self.made_at(**{".github/workflows/mlz.yml": workflow}))
        self.sync(github)
        self.assertIn(f"uses: acme-org/mlz-core/.github/workflows/workload.yml@{TARGET}", github.branches[0][2][".github/workflows/mlz.yml"])

    def test_an_upgrade_that_fails_leaves_the_values_and_the_other_repos_to_go_on(self):
        stale = {**self.made_at(), workload.VALUES: "{}\n"}
        github = self.github(stale, releases=ConfigError("gh api GET repos/fortytwoservices/mlz-core/releases: HTTP 502"))
        out = self.sync(github)
        self.assertEqual(len(github.commits), 1)  # the values reached main
        self.assertEqual(out[-1], f"::warning::{self.REPO}: no upgrade pull request: gh api GET repos/fortytwoservices/mlz-core/releases: HTTP 502")


class RuleTest(SyncTest):
    """A workload repo's main takes the role repos' rule (decision D14), with the MLZ App let through for its values."""

    REPO = "acme-org/payments-prod"

    def made(self, **files) -> FakeGitHub:
        self.config("payments-prod")
        return FakeGitHub({self.REPO: {"README.md": f"# payments-prod\n{DESCRIPTION}\n", **files}})

    def test_a_repo_gets_the_rule_its_code_owners_and_the_bypass_team_s_access(self):
        github = self.made()
        out = self.sync(github)
        main, checks = github.made_rulesets[self.REPO]
        self.assertEqual(main, install.main_ruleset("workload", 7, 99, approvals=1))
        self.assertEqual(main["bypass_actors"], [{"actor_id": 7, "actor_type": "Team", "bypass_mode": "pull_request"},
                                                 {"actor_id": 99, "actor_type": "Integration", "bypass_mode": "always"}])
        self.assertTrue(main["rules"][2]["parameters"]["require_code_owner_review"])
        self.assertEqual(main["rules"][2]["parameters"]["required_approving_review_count"], 1)  # one besides the author's
        self.assertEqual(checks["bypass_actors"], [{"actor_id": 99, "actor_type": "Integration", "bypass_mode": "always"}])
        self.assertEqual(checks["rules"][0]["parameters"]["required_status_checks"], [{"context": "workload / plan", "integration_id": 15368}])
        self.assertTrue(checks["rules"][0]["parameters"]["strict_required_status_checks_policy"])
        self.assertEqual(github.writers[self.REPO], {"acme-admins"})
        self.assertTrue(github.repos[self.REPO][".github/CODEOWNERS"].endswith("\n* @acme-org/payments-devs\n"))
        self.assertEqual(out[1:], [f"{self.REPO}: acme-org/acme-admins may write, for its bypass of main's approval",
                                   f"{self.REPO}: ruleset main: pull requests only, a code owner's approval (and one besides the author's), "
                                   "acme-org/acme-admins may merge without it",
                                   f"{self.REPO}: ruleset main-checks: workload / plan must pass, on a branch up to date with main, before any "
                                   "merge into main; nobody but the MLZ App may bypass it, for its commits of the landing zone's values"])
        # Again: nothing to do, nothing said.
        self.assertEqual(self.sync(github), [])
        self.assertEqual(len(github.made_rulesets[self.REPO]), 2)

    def test_a_secret_owning_team_is_said_and_main_still_asks_another_s_approval(self):
        # GitHub reads no secret team in CODEOWNERS: without the second approval, the author could merge alone.
        github = self.made()
        github.secret.add("payments-devs")
        out = self.sync(github)
        self.assertIn(f"::warning::{self.REPO}: the landing zone's team acme-org/payments-devs is secret, so GitHub does not read it in "
                      "CODEOWNERS: main asks any other writer's approval until it is a visible team.", out)
        self.assertEqual(github.made_rulesets[self.REPO][0]["rules"][2]["parameters"]["required_approving_review_count"], 1)

    def test_a_failed_rule_leaves_the_upgrade_to_go_on(self):
        github = self.made()
        github.rulesets = lambda repo: (_ for _ in ()).throw(ConfigError("gh api GET rulesets: HTTP 502"))
        offered = []
        original = workload.offer_upgrade
        workload.offer_upgrade = lambda *args: offered.append(args[1])
        try:
            out = self.sync(github)
        finally:
            workload.offer_upgrade = original
        self.assertIn(f"::warning::{self.REPO}: its rule on main is not complete: gh api GET rulesets: HTTP 502", out)
        self.assertEqual(offered, [self.REPO])

    def test_the_code_owners_file_is_vending_s(self):
        github = self.made()
        self.sync(github)
        github.repos[self.REPO][".github/CODEOWNERS"] = "* @acme-org/someone-else\n"
        self.sync(github)
        files, message = github.commits[-1][3], github.commits[-1][4]
        self.assertEqual((set(files), message), ({".github/CODEOWNERS"}, "chore: the landing zone's code owners, from vending"))

    def test_a_ruleset_the_repo_has_already_is_left_as_it_is_and_said(self):
        own = {"name": "main", "target": "branch", "enforcement": "active", "rules": [{"type": "deletion"}]}
        github = self.made()
        github.made_rulesets[self.REPO] = [own]
        out = self.sync(github)
        self.assertEqual([r["name"] for r in github.made_rulesets[self.REPO]], ["main", "main-checks"])
        self.assertIn(f"{self.REPO}: has a ruleset named main that is not the one vending makes; left as it is (docs/vending.md, Its rule on main)", out)

    def test_without_the_app_s_id_no_rule_is_made_and_the_values_still_go(self):
        github = self.made()
        out = self.sync(github, app_slug=None)
        self.assertEqual(github.made_rulesets, {})
        self.assertEqual(len(github.commits), 1)
        self.assertIn("no rule on main: the MLZ App's ID is not known (MLZ_APP_SLUG)", out[1])

    def test_without_a_bypass_team_main_has_none_and_says_so(self):
        self.config("payments-prod", github={"organization": "acme-org", "repo": "payments-prod", "create": True, "team": "payments-devs",
                                             "description": DESCRIPTION, "bypass_team": None})
        github = FakeGitHub({self.REPO: {}})
        out = self.sync(github)
        main = github.made_rulesets[self.REPO][0]
        self.assertEqual(main["bypass_actors"], [{"actor_id": 99, "actor_type": "Integration", "bypass_mode": "always"}])
        self.assertTrue(any("governance's contract names no bypass team yet" in line for line in out))
        # One named that the organization does not have: said, and main gets no team bypass either.
        self.config("other-prod", github={"organization": "acme-org", "repo": "other-prod", "create": True, "team": "payments-devs",
                                          "description": DESCRIPTION, "bypass_team": "gone"})
        github = FakeGitHub({"acme-org/other-prod": {}})
        out = self.sync(github)
        self.assertTrue(any("the bypass team acme-org/gone is not there" in line for line in out))


class CommandTest(unittest.TestCase):
    def test_only_the_run_of_vending_s_current_main_writes(self):
        import os
        import sys
        from unittest import mock
        from mlz import __main__ as cli

        class Head:
            def __init__(self, head):
                self._head = head

            def head(self, repo, branch):
                return self._head

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "argv", ["mlz", "workload-repos", "--configs", tmp]), \
                mock.patch.dict(os.environ, {"GITHUB_SHA": "older", "GITHUB_REPOSITORY": "acme-org/mlz-vending"}), \
                mock.patch.object(workload, "sync") as sync:
            out = io.StringIO()
            with mock.patch.object(cli, "GitHub", lambda: Head("newer")), redirect_stdout(out):
                self.assertEqual(cli.main(), 0)
            sync.assert_not_called()
            self.assertIn("main has moved on from this run's older", out.getvalue())
            with mock.patch.object(cli, "GitHub", lambda: Head("older")):
                self.assertEqual(cli.main(), 0)
            sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
