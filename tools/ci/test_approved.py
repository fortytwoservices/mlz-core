"""Tests for the gate on an apply on GitHub Team (approved.py), changed.py's use of it, and replan.txt (check.py)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import approved, changed, check

WORKFLOW_REF = "acme/mlz-platform/.github/workflows/mlz.yml@refs/heads/main"


def merged(number=7, sha="sha1", head="head1", ref="feature"):
    return {"number": number, "merged_at": "2026-11-20T10:00:00Z", "merge_commit_sha": sha, "base": {"ref": "main"},
            "head": {"sha": head, "ref": ref, "repo": {"full_name": "acme/mlz-platform"}}, "user": {"type": "User"}, "title": "feat: a hub"}


class FakeGitHub:
    """GitHub as approved.py asks it: the commit's pull requests, the workflow's runs on a commit (one list per look),
    and each run's artifacts, as {artifact: {file: text}}."""

    def __init__(self, pulls=(), runs=(), artifacts=None):
        self.pulls, self.looks, self.artifacts = list(pulls), [list(r) for r in runs], artifacts or {}
        self.asked: list[tuple] = []

    def commit_pulls(self, repo, sha, timeout=None):
        self.asked.append(("commit_pulls", repo, sha))
        return self.pulls

    def workflow_runs(self, repo, workflow, head_sha):
        self.asked.append(("workflow_runs", repo, workflow, head_sha))
        return self.looks.pop(0) if len(self.looks) > 1 else (self.looks[0] if self.looks else [])

    def download_artifacts(self, repo, run_id, pattern, folder):
        self.asked.append(("download", repo, run_id, pattern))
        found = self.artifacts.get(run_id, {})
        for artifact, files in found.items():
            for name, text in files.items():
                (folder / artifact).mkdir(parents=True, exist_ok=True)
                (folder / artifact / name).write_text(text)
        return bool(found)


def run(id_=41, number=12, status="completed", event="pull_request"):
    return {"id": id_, "run_number": number, "status": status, "event": event, "html_url": f"https://example/runs/{id_}"}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path(".mlz/reviewed").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "acme/mlz-platform", "GITHUB_SHA": "sha1", "WORKFLOW_REF": WORKFLOW_REF})
        self.env.start()
        self.said: list[str] = []
        self.slept: list[float] = []

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def reviewed(self, stack, text):
        Path(f".mlz/reviewed/changes-{stack}.txt").write_text(text)

    def gate(self, role, stacks, github, **options):
        return approved.approved(role, stacks, github, sleep=self.slept.append, say=self.said.append, **options)


class ApprovedTest(Case):
    def test_main_s_plan_as_the_pull_request_s_run_showed_it_applies(self):
        self.reviewed("connectivity", "add azapi_resource.hub abc\n")
        github = FakeGitHub([merged(6, sha="other"), merged()], [[run(40, 11), run(41, 12), run(50, 13, event="push")]],
                            {41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "add azapi_resource.hub abc\n"}}})
        self.assertTrue(self.gate("platform", ["connectivity"], github))
        # The merged pull request's head, the caller's workflow by its file, and that run's plans of the role.
        self.assertIn(("workflow_runs", "acme/mlz-platform", "mlz.yml", "head1"), github.asked)
        self.assertIn(("download", "acme/mlz-platform", 41, "mlz-platform-plan-*"), github.asked)
        self.assertEqual(self.said[-1], "The plan of connectivity is the one #7's run showed: https://example/runs/41")

    def test_a_plan_that_differs_or_was_not_made_on_the_pull_request_applies_nothing(self):
        self.reviewed("connectivity", "add azapi_resource.hub abc\n")
        self.reviewed("management", "change azapi_resource.law def\n")
        github = FakeGitHub([merged()], [[run()]], {41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "add azapi_resource.hub xyz\n"}}})
        self.assertFalse(self.gate("platform", ["connectivity", "management"], github))
        text = "\n".join(self.said)
        self.assertIn("::error::connectivity: its plan on main is not the one the pull request's run showed:", text)
        self.assertIn("-add azapi_resource.hub xyz\n+add azapi_resource.hub abc", text)
        self.assertIn("::error::management: its plan on main changes it, but the pull request's run has no plan of it", text)
        self.assertIn("Open a pull request that changes replan.txt", text)

    def test_it_waits_for_the_pull_request_s_run_to_finish(self):
        self.reviewed("connectivity", "x\n")
        github = FakeGitHub([merged()], [[run(status="in_progress")], [run(status="in_progress")], [run()]],
                            {41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "x\n"}}})
        self.assertTrue(self.gate("platform", ["connectivity"], github))
        self.assertEqual(self.slept, [approved.PAUSE, approved.PAUSE])
        # Still going after the wait: its plans are read as they are, and a missing one refuses.
        github = FakeGitHub([merged()], [[run(status="in_progress")]], {})
        self.assertFalse(self.gate("platform", ["connectivity"], github, wait=30))
        self.assertIn("has no plans to read", self.said[-1])

    def test_no_pull_request_no_apply_in_a_role_repo_and_the_app_s_values_apply_in_a_workload_repo(self):
        self.reviewed("connectivity", "x\n")
        self.assertFalse(self.gate("platform", ["connectivity"], FakeGitHub([merged(sha="other")])))
        self.assertIn("::error::No pull request made sha1, so no code owner approved a plan of it", self.said[-1])
        self.assertTrue(self.gate("workload", ["workload"], FakeGitHub([])))
        self.assertIn("it is the MLZ App's, the landing zone's values from vending", self.said[-1])

    def test_a_pull_request_without_a_run_or_plans_applies_nothing(self):
        self.reviewed("workload", "x\n")
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([merged()], [[]])))
        self.assertIn("#7 has no run on its last commit", self.said[-1])
        self.assertIn("Open a pull request (an empty commit will do)", self.said[-1])
        self.assertFalse(self.gate("workload", ["workload"], FakeGitHub([merged()], [[run()]]), artifacts="mlz-workload-plan"))
        self.assertIn("its artifacts have expired after 30 days", self.said[-1])

    def test_nothing_to_apply_asks_github_nothing(self):
        github = FakeGitHub()
        self.assertTrue(self.gate("platform", [], github))
        self.assertEqual(github.asked, [])

    def test_the_workflow_file_from_its_ref(self):
        self.assertEqual(approved.workflow_file(WORKFLOW_REF), "mlz.yml")
        with self.assertRaises(approved.ConfigError):
            approved.workflow_file("")


class ChangedByPullRequestTest(Case):
    def run_changed(self, github):
        out, summary = Path("out"), Path("summary")
        with mock.patch.dict(os.environ, {"ROLE": "platform", "STACKS": json.dumps(["connectivity", "identity"]), "BATCHES": "[]",
                                          "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_EVENT_NAME": "push",
                                          "MLZ_APPROVAL": "pull-request"}), \
                mock.patch.object(approved.time, "sleep", self.slept.append):
            code = changed.main(github, self.said.append)
        return code, out.read_text() if out.exists() else "", summary.read_text()

    def test_the_gate_passes_and_nothing_says_it_waits(self):
        self.reviewed("connectivity", "x\n")
        self.reviewed("identity", "")
        github = FakeGitHub([merged()], [[run()]], {41: {"mlz-platform-plan-connectivity": {"changes-connectivity.txt": "x\n"}}})
        code, out, summary = self.run_changed(github)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('stacks=["connectivity"]\n'))
        self.assertEqual(summary, "To apply: connectivity, as its pull request's run showed. No changes: identity.\n")

    def test_the_gate_refuses_and_the_job_fails_with_no_stacks(self):
        self.reviewed("connectivity", "x\n")
        self.reviewed("identity", "")
        code, out, summary = self.run_changed(FakeGitHub([merged()], [[run()]], {41: {"a": {"changes-connectivity.txt": "y\n"}}}))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Nothing is applied: the plan of connectivity on main is not the one its pull request's run showed", summary)


class ReplanTest(unittest.TestCase):
    def test_a_changed_replan_txt_plans_every_stack(self):
        def run(args, **kwargs):
            if args[0] == "git" and args[1] == "rev-parse":
                return mock.Mock(returncode=0, stdout="base\n")
            if args[0] == "git" and args[1] == "diff":
                return mock.Mock(returncode=1 if args[-1] == check.REPLAN else 0, stdout="")
            return mock.Mock(returncode=0, stdout='["connectivity", "management"]\n')  # mlz check
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(check.subprocess, "run", side_effect=run), \
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


if __name__ == "__main__":
    unittest.main()
