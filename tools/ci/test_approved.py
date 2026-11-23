"""Tests for the gate on GitHub Team (approved.py): each refusal and each acceptance of covers (a pull request's planned
job) and gate (a run on main), against a recording fake of GitHub; changed.py's use of the gate; the environment's
cross-check (apply_guard.py); replan.txt (check.py); and the token a Pipeline uses (gh.py)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import apply_guard, approved, changed, check, gh

WORKFLOW_REF = "acme/mlz-platform/.github/workflows/mlz.yml@refs/heads/main"
APP = {"type": "pull_request"}


def merged(number=7, sha="sha1", head="head1", at="2026-11-23T10:00:00Z", base="main"):
    return {"number": number, "merged_at": at, "merge_commit_sha": sha, "base": {"ref": base}, "head": {"sha": head},
            "html_url": f"https://example/pull/{number}", "title": "feat: a hub", "user": {"type": "User"}}


def run(id_=41, number=12, status="completed", conclusion="success", started="2026-11-23T09:50:00Z", attempt=1):
    return {"id": id_, "run_number": number, "status": status, "conclusion": conclusion, "html_url": f"https://example/runs/{id_}",
            "run_started_at": started, "run_attempt": attempt}


def plan_jobs(completed, name="platform / plan (a)"):
    return [{"name": "platform / check", "completed_at": "2026-11-23T08:00:00Z"}, {"name": name, "completed_at": completed},
            {"name": "platform / planned", "completed_at": None}]


def review(login="bea", at="2026-11-23T10:05:00Z", commit="head1", state="APPROVED"):
    return {"user": {"login": login}, "state": state, "submitted_at": at, "commit_id": commit}


class FakeGitHub:
    """GitHub as approved.py asks it. runs: {head sha: [runs, newest first]}; artifacts: {run id: {artifact: {file: text}}},
    each stamped with #7 unless it says otherwise (None leaves the stamp out); jobs: {(run id, attempt): [jobs]}."""

    def __init__(self, pulls=(), runs=None, artifacts=None, now=None, head="sha1", rules=(APP,), commit=None, bypasses=(), reviews=(),
                 jobs=None, codeowners="*  @acme/platform\n/_generated/  @acme/platform\n", members=("bea",), permissions=None):
        self.pulls, self.runs, self.artifacts, self.now = pulls, runs or {}, artifacts or {}, now or {}
        self.main, self.rules, self._commit, self._bypasses = head, list(rules), commit or {}, list(bypasses)
        self._reviews, self.jobs, self.codeowners, self.members = list(reviews), jobs or {}, codeowners, set(members)
        self.permissions = permissions or {}
        self.asked: list[tuple] = []

    def commit_pulls(self, repo, sha, timeout=None):
        self.asked.append(("commit_pulls", sha))
        return self.pulls.pop(0) if self.pulls and isinstance(self.pulls[0], list) else list(self.pulls)

    def pull_runs(self, repo, workflow, head_sha):
        self.asked.append(("pull_runs", workflow, head_sha))
        return self.runs.get(head_sha, [])

    def download_artifacts(self, repo, run_id, pattern, folder):
        self.asked.append(("download", run_id, pattern))
        found = self.artifacts.get(run_id, {})
        for artifact, files in found.items():
            (folder / artifact).mkdir(parents=True, exist_ok=True)
            for name, text in ({approved.STAMP: "7\n"} | files).items():
                if text is not None:
                    (folder / artifact / name).write_text(text)
        return bool(found)

    def workflow_run(self, repo, run_id):
        self.asked.append(("workflow_run", run_id))
        return self.now.get(run_id) or next(item for runs in self.runs.values() for item in runs if item["id"] == run_id)

    def run_jobs(self, repo, run_id, attempt):
        self.asked.append(("run_jobs", run_id, attempt))
        return self.jobs.get((run_id, attempt), [])

    def head(self, repo, branch):
        self.asked.append(("head", branch))
        return self.main

    def branch_rules(self, repo, branch):
        self.asked.append(("branch_rules", branch))
        return self.rules

    def commit(self, repo, sha):
        return self._commit

    def bypasses(self, repo, branch):
        self.asked.append(("bypasses", branch))
        return self._bypasses

    def reviews(self, repo, number):
        self.asked.append(("reviews", number))
        return self._reviews

    def text(self, repo, path, ref=None):
        self.asked.append(("text", path, ref))
        if isinstance(self.codeowners, dict):  # by location; a location it lacks is no file there
            return self.codeowners.get(path)
        return self.codeowners if path == ".github/CODEOWNERS" else None

    def team_member(self, org, team, login):
        return (org, team) == ("acme", "platform") and login in self.members

    def permission(self, repo, login):
        return self.permissions.get(login, "none")


class Case(unittest.TestCase):
    environment = {"GITHUB_REPOSITORY": "acme/mlz-platform", "GITHUB_SHA": "sha1", "GITHUB_RUN_ATTEMPT": "1", "WORKFLOW_REF": WORKFLOW_REF}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path(".mlz/reviewed").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, self.environment)
        self.env.start()
        self.said: list[str] = []
        self.slept: list[float] = []

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def reviewed(self, stack, text):
        Path(f".mlz/reviewed/changes-{stack}.txt").write_text(text)

    def gate(self, role, stacks, github):
        return approved.gate(role, stacks, github, sleep=self.slept.append, say=self.said.append)

    def text(self):
        return "\n".join(self.said)


class GateTest(Case):
    def platform(self, plans="add hub abc\n", **options):
        self.reviewed("connectivity", "add hub abc\n")
        options.setdefault("runs", {"head1": [run()]})
        options.setdefault("artifacts", {41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": plans}}})
        return FakeGitHub([merged(6, sha="other"), merged()], **options)

    def test_main_s_plan_as_the_pull_request_s_last_run_showed_it_applies(self):
        github = self.platform()
        verdict = self.gate("platform", ["connectivity"], github)
        self.assertTrue(verdict.ok)
        self.assertEqual((verdict.pull["number"], verdict.run["id"], verdict.bypass), (7, 41, None))
        self.assertIn(("pull_runs", "mlz.yml", "head1"), github.asked)  # the caller's workflow, by its file
        self.assertIn(("download", 41, "mlz-platform-plan-*"), github.asked)
        self.assertIn(("bypasses", "main"), github.asked)
        self.assertNotIn(("head", "main"), github.asked)  # a first attempt is always in order
        self.assertEqual(self.said[-1], "The plan of connectivity is the one #7's run showed: https://example/runs/41")

    def test_a_merge_with_the_bypass_is_named(self):
        github = self.platform(bypasses=[{"after_sha": "other", "actor_name": "carl"}, {"after_sha": "sha1", "actor_name": "dana"}])
        self.assertEqual(self.gate("platform", ["connectivity"], github).bypass, "dana")
        github = self.platform()
        github.bypasses = mock.Mock(side_effect=gh.ConfigError("HTTP 403"))
        verdict = self.gate("platform", ["connectivity"], github)
        self.assertTrue(verdict.ok)  # a failed read of the record never holds up the apply
        self.assertIn("::notice::Could not read whether this merge used a ruleset's bypass", self.text())

    def test_a_plan_that_differs_or_was_not_made_on_the_pull_request_applies_nothing(self):
        self.reviewed("management", "change law def\n")
        verdict = self.gate("platform", ["connectivity", "management"], self.platform(plans="add hub xyz\n"))
        self.assertFalse(verdict.ok)
        self.assertIn("::error::connectivity: its plan on main is not the one #7's run showed:", self.text())
        self.assertIn("-add hub xyz\n+add hub abc", self.text())
        self.assertIn("::error::management: its plan on main is not the one #7's run showed:", self.text())
        self.assertIn("+change law def", self.text())
        self.assertIn("open a pull request that changes replan.txt", self.said[-1])

    def test_a_later_attempt_applies_only_while_its_commit_is_main_s_head(self):
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
            github = self.platform(head="sha9")
            self.assertFalse(self.gate("platform", ["connectivity"], github).ok)
            self.assertEqual(self.said[-1], "::error::This is attempt 2 of the run for sha1, and main has moved on to sha9: its plan, made again now, "
                                            "would undo what later runs applied. Nothing is applied. " + approved.REPLAN)
            self.assertNotIn(("commit_pulls", "sha1"), github.asked)
            self.assertTrue(self.gate("platform", ["connectivity"], self.platform(head="sha1")).ok)

    def test_no_pull_request_no_apply_in_a_role_repo_after_asking_for_a_while(self):
        self.reviewed("connectivity", "x\n")
        self.assertFalse(self.gate("platform", ["connectivity"], FakeGitHub([merged(sha="other")])).ok)
        self.assertEqual(self.said[-1], "::error::No pull request made sha1, so no code owner approved a plan of it; nothing is applied. " + approved.REPLAN)
        self.assertEqual(self.slept, [approved.PAUSE] * (approved.LOOKS - 1))

    def test_a_pull_request_listed_late_is_still_found(self):
        github = self.platform()
        github.pulls = [[], [], [merged()]]
        self.assertTrue(self.gate("platform", ["connectivity"], github).ok)
        self.assertEqual(self.slept, [approved.PAUSE, approved.PAUSE])

    def test_a_last_run_that_did_not_finish_with_success_applies_nothing(self):
        for status, conclusion in (("in_progress", None), ("completed", "failure"), ("completed", "cancelled")):
            with self.subTest(status=status, conclusion=conclusion):
                self.assertFalse(self.gate("platform", ["connectivity"], self.platform(runs={"head1": [run(status=status, conclusion=conclusion)]})).ok)
                self.assertIn(f"#7's last run (https://example/runs/41) did not finish with success ({status}, {conclusion})", self.said[-1])

    def test_a_last_run_run_again_after_the_merge_or_while_read_applies_nothing(self):
        self.assertFalse(self.gate("platform", ["connectivity"], self.platform(runs={"head1": [run(started="2026-11-23T10:30:00Z", attempt=2)]})).ok)
        self.assertIn("was run again after the merge (2026-11-23T10:30:00Z): its plans are of a later Azure", self.said[-1])
        github = self.platform(now={41: run(status="in_progress", attempt=2, started="2026-11-23T10:30:00Z")})
        self.assertFalse(self.gate("platform", ["connectivity"], github).ok)
        self.assertIn("was run again while its plans were read", self.said[-1])

    def test_plans_marked_as_another_pull_request_s_are_passed_over(self):
        # The newest run on the head commit is #9's, into another branch; the one before it is #7's own.
        artifacts = {50: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "other\n", approved.STAMP: "9\n"}},
                     41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "add hub abc\n"}}}
        verdict = self.gate("platform", ["connectivity"], self.platform(runs={"head1": [run(50, 13), run()]}, artifacts=artifacts))
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.run["id"], 41)
        for stamp in ("9\n", None):  # another's, or a run that marked none
            with self.subTest(stamp=stamp):
                artifacts = {41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "add hub abc\n", approved.STAMP: stamp}}}
                self.assertFalse(self.gate("platform", ["connectivity"], self.platform(artifacts=artifacts)).ok)
                self.assertIn("#7 has no run on its last commit with plans marked as its own", self.said[-1])

    def test_a_run_without_plans_is_passed_over(self):
        github = self.platform(runs={"head1": [run(50, 13), run()]})
        self.assertTrue(self.gate("platform", ["connectivity"], github).ok)
        self.assertIn(("download", 50, "mlz-platform-plan-*"), github.asked)

    def test_nothing_to_apply_asks_github_nothing(self):
        github = FakeGitHub()
        self.assertTrue(self.gate("platform", [], github).ok)
        self.assertEqual(github.asked, [])

    def test_the_workflow_file_from_its_ref(self):
        self.assertEqual(approved.workflow_file(WORKFLOW_REF), "mlz.yml")
        with self.assertRaises(gh.ConfigError):
            approved.workflow_file("")


class WorkloadValuesTest(Case):
    environment = {**Case.environment, "GITHUB_REPOSITORY": "acme/pay-prod"}

    def commit(self, *names, parents=1, added=()):
        return {"parents": [{"sha": "p"}] * parents,
                "files": [{"filename": name, "status": "added" if name in added else "modified"} for name in names]}

    def test_the_mlz_app_s_values_apply_with_no_pull_request(self):
        self.reviewed("workload", "x\n")
        starter = ("mlz.auto.tfvars.json", ".github/workflows/mlz.yml", "README.md", "main.tf")
        for names, added in ((("mlz.auto.tfvars.json",), ()), (("mlz.auto.tfvars.json", ".github/CODEOWNERS"), ()), (("CODEOWNERS",), ()),
                             (starter, starter)):  # the first commit, which adds the values, brings the starter
            names = tuple(".github/CODEOWNERS" if name == "CODEOWNERS" else name for name in names)
            with self.subTest(names=names):
                github = FakeGitHub([], commit=self.commit(*names, added=added))
                verdict = self.gate("workload", ["workload"], github)
                self.assertTrue(verdict.ok)
                self.assertIsNone(verdict.bypass)
                self.assertNotIn(("bypasses", "main"), github.asked)  # a workflow's token cannot read the record
                self.assertIn("::notice::No pull request made sha1: it is the MLZ App's commit of what vending writes", self.said[-1])

    def test_anything_else_without_a_pull_request_applies_nothing(self):
        self.reviewed("workload", "x\n")
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([], rules=[{"type": "deletion"}], commit=self.commit("mlz.auto.tfvars.json"))).ok)
        self.assertIn("no rule on main asks for one, so anyone who may push could have made it", self.said[-1])
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([], commit=self.commit("mlz.auto.tfvars.json", "main.tf"))).ok)
        self.assertIn("it is not vending's commit of the landing zone's values: it changes main.tf. Nothing is applied.", self.said[-1])
        # The starter's files pass only with the commit that first adds the values.
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([], commit=self.commit("main.tf", ".github/workflows/mlz.yml",
                                                                                              added=("main.tf",)))).ok)
        self.assertIn("it changes .github/workflows/mlz.yml, main.tf", self.said[-1])
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([], commit=self.commit("mlz.auto.tfvars.json", parents=2))).ok)
        self.assertIn("it merges branches", self.said[-1])
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([], commit=self.commit())).ok)
        self.assertIn("it changes no file", self.said[-1])
        self.assertIn(approved.REPLAN_WORKLOAD, self.said[-1])

    def test_a_re_run_of_an_older_values_commit_never_rolls_back(self):
        self.reviewed("workload", "x\n")
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "3"}):
            self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([], head="sha9", commit=self.commit("mlz.auto.tfvars.json"))).ok)
            self.assertIn("This is attempt 3 of the run for sha1, and main has moved on to sha9", self.said[-1])

    def test_a_workload_pull_request_s_plan_as_its_run_showed_it(self):
        self.reviewed("workload", "x\n")
        github = FakeGitHub([merged()], runs={"head1": [run()]}, artifacts={41: {"mlz-workload-plan": {"changes-workload.txt": "x\n"}}})
        self.assertTrue(self.gate("workload", ["workload"], github).ok)
        self.assertIn(("download", 41, "mlz-workload-plan"), github.asked)
        self.assertIn(approved.REPLAN_WORKLOAD, approved.replan("workload"))


class CoversTest(Case):
    environment = {**Case.environment, "PR_NUMBER": "7", "PR_AUTHOR": "alex", "BASE_REF": "main", "GITHUB_RUN_ID": "50", "GITHUB_RUN_ATTEMPT": "1"}
    NOW = approved.when("2026-11-23T11:00:00Z")

    def setUp(self):
        super().setUp()
        self.planned("add hub abc\n")

    def planned(self, text, stack="connectivity"):
        Path(".mlz/planned/mlz-platform-plan-a").mkdir(parents=True, exist_ok=True)
        Path(f".mlz/planned/mlz-platform-plan-a/changes-{stack}.txt").write_text(text)

    def covers(self, github, role="platform"):
        return approved.covers(role, github, say=self.said.append, now=lambda: self.NOW)

    def github(self, reviews=(), seen_plans="add hub abc\n", seen_run=None, seen_jobs="2026-11-23T09:45:00Z", **options):
        """This run (50) planned by 10:00; an earlier run (40) on head0, planned by seen_jobs."""
        jobs = {(50, 1): plan_jobs("2026-11-23T10:00:00Z"), (40, 1): plan_jobs(seen_jobs)}
        runs = {"head0": [seen_run or run(40, 10, started="2026-11-23T09:40:00Z")]}
        artifacts = {40: {"mlz-platform-plan-a": {"changes-connectivity.txt": seen_plans}}}
        return FakeGitHub(reviews=reviews, jobs=jobs, runs=runs, artifacts=artifacts, **options)

    def test_a_plan_that_changes_nothing_needs_no_approval(self):
        self.planned("")
        github = self.github()
        self.assertTrue(self.covers(github))
        self.assertEqual(github.asked, [])
        self.assertEqual(self.said, ["These plans change nothing, so there is nothing for an approval to cover."])

    def test_no_code_owner_s_approval_yet_passes(self):
        # The author's own, a reviewer who is no code owner, and a dismissed one do not count.
        reviews = [review("alex"), review("eve"), review("bea", state="DISMISSED"), review("bea", state="COMMENTED")]
        self.assertTrue(self.covers(self.github(reviews)))
        self.assertEqual(self.said[-1], "No code owner has approved this pull request yet. An approval from now on comes after these plans, and covers them.")

    def test_an_approval_after_these_plans_covers_them(self):
        self.assertTrue(self.covers(self.github([review(at="2026-11-23T10:05:00Z")])))
        self.assertEqual(self.said[-1], "@bea approved after these plans were on the pull request (2026-11-23T10:05:00Z), so the approval covers them.")

    def test_an_older_approval_covers_the_same_plans_and_no_other(self):
        older = [review(at="2026-11-23T09:50:00Z", commit="head0")]
        self.assertTrue(self.covers(self.github(older)))
        self.assertEqual(self.said[-1], "These plans are the ones @bea approved (https://example/runs/40), so the approval covers them; no new approval "
                                        "is needed.")
        self.assertFalse(self.covers(self.github(older, seen_plans="add hub xyz\n")))
        self.assertIn("::error::connectivity: its plan changed since @bea approved it:", self.text())
        self.assertIn("-add hub xyz\n+add hub abc", self.text())
        self.assertIn("The plan of connectivity changed since @bea approved the pull request (2026-11-23T09:50:00Z), so nobody has approved what "
                      "it would apply. Ask a code owner to approve the pull request again", self.said[-1])

    def test_a_stack_planned_now_and_not_then_needs_a_new_approval(self):
        self.planned("add vnet\n", stack="management")
        self.assertFalse(self.covers(self.github([review(at="2026-11-23T09:50:00Z", commit="head0")])))
        self.assertIn("::error::management: its plan changed since @bea approved it:", self.text())

    def test_plans_an_approval_saw_that_cannot_be_read_need_a_new_approval(self):
        approval = [review(at="2026-11-23T09:50:00Z", commit="head0")]
        for options in ({"seen_jobs": "2026-11-23T09:55:00Z"},  # still planning when the approval came
                        {"seen_run": run(40, 10, started="2026-11-23T09:58:00Z", attempt=2)},  # run again since
                        {"runs": {}}):  # no run of it at all
            with self.subTest(options=options):
                github = self.github(approval, **{key: value for key, value in options.items() if key != "runs"})
                if "runs" in options:
                    github.runs = {}
                self.assertFalse(self.covers(github))
                self.assertIn("::error::@bea approved before these plans were on the pull request (2026-11-23T09:50:00Z), and the plans that "
                              "approval saw cannot be read", self.said[-1])

    def test_a_code_owner_is_a_member_of_a_team_or_a_user_codeowners_names(self):
        self.assertTrue(self.covers(self.github([review("dan", at="2026-11-23T10:05:00Z")], codeowners="* @dan\n")))
        self.assertIn("@dan approved after these plans", self.said[-1])
        github = self.github([review("dan", at="2026-11-23T10:05:00Z")])
        self.assertTrue(self.covers(github))
        self.assertIn("No code owner has approved", self.said[-1])  # dan is not in acme/platform
        self.assertIn(("text", ".github/CODEOWNERS", "main"), github.asked)  # the base branch's
        with self.assertRaises(gh.ConfigError):
            self.covers(self.github([review()], codeowners="# nobody\n"))
        with self.assertRaisesRegex(gh.ConfigError, r"^\.github/CODEOWNERS on main names no owner"):
            self.covers(self.github([review()], codeowners={}))

    def test_a_comment_names_no_owner(self):
        # GitHub ignores a line's own comment: a mention in it makes nobody a code owner.
        github = self.github([review("bea", at="2026-11-23T09:50:00Z", commit="head0", ), review("alice", at="2026-11-23T10:05:00Z")],
                             seen_plans="add hub xyz\n", codeowners="*  @acme/platform # ask @alice\n# or @alice\n")
        self.assertFalse(self.covers(github))
        self.assertIn("changed since @bea approved the pull request", self.said[-1])

    def test_the_codeowners_file_github_reads_first(self):
        # An adopted repo's own file at the top or in docs/, where the installer leaves it, as GitHub looks.
        for files in ({"CODEOWNERS": "* @dan\n"}, {"docs/CODEOWNERS": "* @dan\n"},
                      {".github/CODEOWNERS": "* @dan\n", "CODEOWNERS": "* @eve\n", "docs/CODEOWNERS": "* @eve\n"}):
            with self.subTest(files=sorted(files)):
                self.assertTrue(self.covers(self.github([review("eve", at="2026-11-23T10:05:00Z"), review("dan", at="2026-11-23T10:06:00Z")],
                                                        codeowners=files)))
                self.assertIn("@dan approved after these plans", self.said[-1])

    def test_a_workload_repo_counts_anyone_with_write_access(self):
        Path(".mlz/planned/mlz-platform-plan-a/changes-connectivity.txt").unlink()
        Path(".mlz/planned/mlz-workload-plan").mkdir()
        Path(".mlz/planned/mlz-workload-plan/changes-workload.txt").write_text("add vm\n")
        github = self.github([review("eve", at="2026-11-23T10:05:00Z"), review("ray", at="2026-11-23T10:06:00Z")], permissions={"eve": "write", "ray": "read"})
        self.assertTrue(self.covers(github, role="workload"))
        self.assertIn("@eve approved after these plans", self.said[-1])
        self.assertNotIn(("text", ".github/CODEOWNERS", "main"), github.asked)

    def test_the_plans_finish_at_the_last_plan_job_and_now_without_one(self):
        github = FakeGitHub(jobs={(50, 2): plan_jobs("2026-11-23T10:00:00Z") + [{"name": "platform / plan (b)", "completed_at": "2026-11-23T10:20:00Z"}]})
        self.assertEqual(approved.plans_finished(github, "r", 50, 2, now=lambda: self.NOW), approved.when("2026-11-23T10:20:00Z"))
        self.assertEqual(approved.plans_finished(FakeGitHub(), "r", 50, 1, now=lambda: self.NOW), self.NOW)
        workload = FakeGitHub(jobs={(50, 1): [{"name": "workload / plan", "completed_at": "2026-11-23T09:00:00Z"},
                                              {"name": "workload / planned", "completed_at": "2026-11-23T09:30:00Z"}]})
        self.assertEqual(approved.plans_finished(workload, "r", 50, 1, now=lambda: self.NOW), approved.when("2026-11-23T09:00:00Z"))


class MainTest(Case):
    def test_github_failing_refuses(self):
        with mock.patch.object(approved, "Pipeline", lambda: mock.Mock(commit_pulls=mock.Mock(side_effect=gh.ConfigError("gh api: HTTP 502")))), \
                contextlib.redirect_stderr(io.StringIO()) as printed, contextlib.redirect_stdout(io.StringIO()):
            self.reviewed("connectivity", "x\n")
            self.assertEqual(approved.main(["gate", "platform", "connectivity"]), 1)
        self.assertIn("::error::gh api: HTTP 502; nothing is applied, and an approval cannot be checked.", printed.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(approved.main(["gate", "platform"]), 2)


class ChangedOnTeamTest(Case):
    def run_changed(self, verdict, comment_fails=False, attempt="1"):
        out, summary = Path("out"), Path("summary")
        self.reviewed("connectivity", "x\n")
        self.reviewed("identity", "")
        commenter = mock.Mock()
        if comment_fails:
            commenter.comment.side_effect = RuntimeError("HTTP 403")
        with mock.patch.dict(os.environ, {"ROLE": "platform", "STACKS": json.dumps(["connectivity", "identity"]), "BATCHES": "[]",
                                          "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_EVENT_NAME": "push",
                                          "APPROVAL": "pull-request", "GATE_TOKEN": "app-token", "RUN_URL": "https://example/runs/90",
                                          "GITHUB_RUN_ATTEMPT": attempt}), \
                mock.patch.object(approved, "gate", return_value=verdict) as gate:
            code = changed.main(commenter, self.said.append)
        self.gate_call, self.commenter = gate.call_args, commenter
        return code, out.read_text() if out.exists() else "", summary.read_text()

    def test_the_gate_passes_and_nothing_says_it_waits(self):
        code, out, summary = self.run_changed(approved.Verdict(True, pull=merged(), run=run()))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('stacks=["connectivity"]\n'))
        self.assertEqual(summary, "To apply: connectivity, as #7's run showed (https://example/runs/41). On GitHub Team the pull request's approval "
                                  "is the gate, so nothing waits. No changes: identity.\n")
        role, stacks, reader = self.gate_call.args
        self.assertEqual((role, stacks, reader._env["GH_TOKEN"]), ("platform", ["connectivity"], "app-token"))  # read as the MLZ App
        self.commenter.comment.assert_not_called()

    def test_the_gate_refuses_and_the_job_fails_with_no_stacks(self):
        code, out, summary = self.run_changed(approved.Verdict(False))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Nothing is applied: the plan of connectivity on main could not be shown to be the one its pull request's run showed", summary)

    def test_a_bypass_is_said_on_the_page_and_once_on_the_pull_request(self):
        verdict = approved.Verdict(True, pull=merged(), run=run(), bypass="dana")
        code, _, summary = self.run_changed(verdict)
        self.assertEqual(code, 0)
        note = ("#7 was merged by dana with a ruleset's bypass, without a code owner's approval. Its run's plan applies (https://example/runs/41); "
                "GitHub records the bypass under Insights, Rules.")
        self.assertIn(f"**Bypass:** {note}", summary)
        self.commenter.comment.assert_called_once_with("acme/mlz-platform", 7, f"{note}\n\nhttps://example/runs/90", timeout=changed.DEADLINE)
        self.run_changed(verdict, attempt="2")
        self.commenter.comment.assert_not_called()
        code, _, _ = self.run_changed(verdict, comment_fails=True)
        self.assertEqual(code, 0)
        self.assertIn("::notice::Could not comment on #7 (HTTP 403); the run's page says the merge used the bypass.", self.said)

    def test_github_failing_in_the_gate_applies_nothing(self):
        out, summary = Path("out"), Path("summary")
        self.reviewed("connectivity", "x\n")
        with mock.patch.dict(os.environ, {"ROLE": "platform", "STACKS": '["connectivity"]', "BATCHES": "[]", "GITHUB_OUTPUT": str(out),
                                          "GITHUB_STEP_SUMMARY": str(summary), "APPROVAL": "pull-request"}), \
                mock.patch.object(approved, "gate", side_effect=gh.ConfigError("gh api: HTTP 502")):
            self.assertEqual(changed.main(mock.Mock(), self.said.append), 1)
        self.assertFalse(out.exists())
        self.assertIn("::error::gh api: HTTP 502; nothing is applied.", self.said)


class CrossCheckTest(Case):
    def test_an_environment_mode_apply_in_a_team_environment_is_refused(self):
        self.reviewed("pay-prod", "x\n")
        for approval, variable, verdict in (("environment", "pull-request", apply_guard.REFUSE), ("", "pull-request", apply_guard.REFUSE),
                                            ("pull-request", "pull-request", apply_guard.GO), ("environment", "", apply_guard.GO),
                                            ("pull-request", "", apply_guard.GO)):
            with self.subTest(approval=approval, variable=variable), mock.patch.dict(os.environ, {"MLZ_APPROVAL": variable, "APPROVAL": approval}):
                if not approval:
                    del os.environ["APPROVAL"]
                said = []
                self.assertEqual(apply_guard.before("platform", "pay-prod", say=said.append), verdict)
                self.assertEqual(said, [apply_guard.UNGATED] if verdict == apply_guard.REFUSE else [])
        self.assertIn("On Enterprise, delete the repository variable MLZ_APPROVAL: it belongs on the environment, and only on Team.", apply_guard.UNGATED)


class ReplanTest(unittest.TestCase):
    def test_a_changed_replan_txt_plans_every_stack(self):
        def run_(args, **kwargs):
            if args[0] == "git" and args[1] == "rev-parse":
                return mock.Mock(returncode=0, stdout="base\n")
            if args[0] == "git" and args[1] == "diff":
                return mock.Mock(returncode=1 if args[-1] == check.REPLAN else 0, stdout="")
            return mock.Mock(returncode=0, stdout='["connectivity", "management"]\n')  # mlz check
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(check.subprocess, "run", side_effect=run_), \
                mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}), contextlib.redirect_stdout(io.StringIO()) as printed, \
                contextlib.redirect_stderr(io.StringIO()) as noted:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                self.assertEqual(check.check("platform"), 0)
            finally:
                os.chdir(cwd)
        self.assertEqual(printed.getvalue(), '["connectivity", "management"]\n')
        self.assertIn("Planning every stack: replan.txt changed.", noted.getvalue())


class PipelineTokenTest(unittest.TestCase):
    def test_a_pipeline_with_a_token_uses_it_and_one_without_uses_the_job_s(self):
        done = mock.Mock(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(gh.subprocess, "run", return_value=done) as called, mock.patch.dict(os.environ, {"GH_TOKEN": "job-token"}):
            gh.Pipeline(token="app-token").workflow_run("acme/r", 1)
            self.assertEqual(called.call_args.kwargs["env"]["GH_TOKEN"], "app-token")
            gh.Pipeline().workflow_run("acme/r", 1)
            self.assertIsNone(called.call_args.kwargs["env"])  # the step's own environment, GH_TOKEN as it is


if __name__ == "__main__":
    unittest.main()
