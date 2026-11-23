"""Tests for the plan side's steps that bash or role.yml held without tests: comment.py, stale_comments.py, changed.py,
and apply_guard.py, which holds the apply's refusals (one test each)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import apply_guard, changed, comment, run_mlz, stale_comments

RUN = "https://github.com/acme/mlz-vending/actions/runs/7"


class FakeGitHub:
    """A pull request's comments, and what was posted or changed."""

    def __init__(self, comments=()):
        self.comments = list(comments)
        self.calls: list[tuple] = []

    def _pages(self, path):
        return self.comments

    def _api(self, path, method="GET", body=None, missing_ok=False):
        self.calls.append((method, path, body["body"] if body else None))


def mine(id_, body, login="github-actions[bot]"):
    return {"id": id_, "body": body, "user": {"login": login}}


class Case(unittest.TestCase):
    environment = {"GITHUB_REPOSITORY": "acme/mlz-vending", "PR_NUMBER": "5", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "7"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path(".mlz").mkdir()
        self.env = mock.patch.dict(os.environ, self.environment)
        self.env.start()
        self.said: list[str] = []

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()


class CommentTest(Case):
    def setUp(self):
        super().setUp()
        Path(".mlz/summary-pay-prod.md").write_text("**pay-prod**: 1 to change\n")

    def test_the_plan_comes_after_the_settings_difference_and_a_link_to_the_run(self):
        github = FakeGitHub()
        comment.post("pay-prod", "2", "vending", github, difference=lambda role, stack: "**pay-prod**: 1 setting changed")
        text = f"<!-- mlz-plan:pay-prod -->\n**pay-prod**: 1 setting changed\n\n**pay-prod**: 1 to change\n\nThe full plan is attached to [the run]({RUN}).\n"
        self.assertEqual(github.calls, [("POST", "repos/acme/mlz-vending/issues/5/comments", text)])
        self.assertEqual(Path(".mlz/comment-pay-prod.md").read_text(), text)

    def test_a_failed_plan_says_so_and_a_workload_repo_s_has_no_difference(self):
        github = FakeGitHub()
        comment.post("workload", "1", "", github, difference=lambda role, stack: self.fail("no role, no difference"))
        self.assertEqual(github.calls[0][2], f"<!-- mlz-plan:workload -->\n**workload**: the plan failed; see [the run]({RUN}).\n")

    def test_its_own_earlier_comment_is_updated_and_no_one_else_s(self):
        github = FakeGitHub([mine(1, "<!-- mlz-plan:pay-prod -->\nold", login="someone"), mine(2, "<!-- mlz-plan:pay-dev -->\nold"),
                             mine(3, "<!-- mlz-plan:pay-prod -->\nold")])
        comment.post("pay-prod", "0", "vending", github, difference=lambda role, stack: "")
        self.assertEqual([c[:2] for c in github.calls], [("PATCH", "repos/acme/mlz-vending/issues/comments/3")])

    def test_a_difference_that_cannot_be_worked_out_says_so(self):
        with mock.patch.object(comment.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="")):
            self.assertEqual(comment.settings("vending", "pay-prod"), "(The settings' difference could not be worked out; the run's log says why.)")
        with mock.patch.object(comment.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="| a |\n\n")) as run:
            self.assertEqual(comment.settings("vending", "pay-prod"), "| a |")  # as bash's $(...) kept it
        self.assertEqual(run.call_args.args[0], run_mlz.mlz("diff", "--role", "vending", "--stack", "pay-prod"))  # mlz-core's own mlz
        self.assertNotIn("capture_output", run.call_args.kwargs)  # its message about a failure stays in the log
        self.assertNotIn("stderr", run.call_args.kwargs)


class StaleCommentsTest(Case):
    def test_a_stack_this_commit_does_not_plan_has_its_comment_marked(self):
        github = FakeGitHub([mine(1, "<!-- mlz-plan:pay-prod -->\nplan"), mine(2, "<!-- mlz-plan:pay-dev -->\nplan"),
                             mine(3, "<!-- mlz-plan:pay-dev -->\nplan", login="someone"), mine(4, "<!-- mlz-plan:Bad_Name -->\nplan"),
                             mine(5, "no marker")])
        with mock.patch.dict(os.environ, {"STACKS": json.dumps(["pay-prod"])}):
            stale_comments.mark(github, say=self.said.append)
        text = ("<!-- mlz-plan:pay-dev -->\n**pay-dev**: not planned on this commit: its config is as on the base branch, "
                "so this pull request does not change it.\n")
        self.assertEqual(github.calls, [("PATCH", "repos/acme/mlz-vending/issues/comments/2", text)])
        self.assertEqual(self.said, ["pay-dev: its earlier plan comment is marked as not planned on this commit"])
        self.assertEqual(Path(".mlz/comment-pay-dev.md").read_text(), text)


class Pulls:
    """GitHub as changed.py asks it: the pull requests of a commit (pulls, or an error to raise), and the comments."""

    def __init__(self, pulls=(), fails: str = ""):
        self.pulls, self.fails, self.calls, self.comments, self.timeouts = list(pulls), fails, [], [], []

    def commit_pulls(self, repo, sha, timeout=None):
        self.calls.append(("commit_pulls", repo, sha))
        self.timeouts.append(timeout)
        if self.fails == "pulls":
            raise RuntimeError("HTTP 502")
        if self.fails == "stalls":
            raise subprocess.TimeoutExpired(["gh", "api"], timeout)
        return self.pulls

    def comment(self, repo, number, body, timeout=None):
        self.timeouts.append(timeout)
        if self.fails == "comment":
            raise RuntimeError("HTTP 403")
        self.comments.append((repo, number, body))


def pull(number, ref="feature", kind="User", sha="sha1", title="feat: a hub", repo="acme/mlz-platform", base="main"):
    return {"number": number, "merged_at": "2026-11-19T10:00:00Z", "merge_commit_sha": sha, "user": {"type": kind},
            "head": {"ref": ref, "repo": {"full_name": repo}}, "base": {"ref": base}, "title": title}


class ChangedTest(Case):
    def run_changed(self, role, stacks, batches, event="workflow_dispatch", github=None, message="feat: a hub (#7)\n\nbody", **environment):
        out, summary, payload = Path("out"), Path("summary"), Path("event.json")
        payload.write_text(json.dumps({"head_commit": {"message": message}} if event == "push" else {"inputs": {}}))  # GitHub's event
        self.github, self.said = github or Pulls(), []
        with mock.patch.dict(os.environ, {"ROLE": role, "STACKS": json.dumps(stacks), "BATCHES": json.dumps(batches),
                                          "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_EVENT_NAME": event,
                                          "GITHUB_SHA": "sha1", "GITHUB_REPOSITORY": "acme/mlz-platform", "GITHUB_RUN_ATTEMPT": "1",
                                          "GITHUB_ACTOR": "alex", "RUN_URL": "https://example/run/9", "GITHUB_EVENT_PATH": str(payload),
                                          **environment}):
            changed.main(self.github, self.said.append)
        return out.read_text(), summary.read_text()

    def test_each_changed_landing_zone_is_an_apply_job_and_no_list_counts_as_changes(self):
        Path(".mlz/reviewed").mkdir()
        Path(".mlz/reviewed/changes-a-prod.txt").write_text("x\n")
        Path(".mlz/reviewed/changes-b-prod.txt").write_text("\n")
        out, summary = self.run_changed("vending", ["a-prod", "b-prod", "c-prod"], [{"id": "a-prod", "stacks": "a-prod"}, {"id": "group-001", "stacks": "b-prod c-prod"}])
        self.assertEqual(out, 'stacks=["a-prod", "c-prod"]\nbatch_of={"a-prod": "a-prod", "b-prod": "group-001", "c-prod": "group-001"}\n')
        self.assertTrue(summary.startswith("To apply: a-prod, c-prod. No changes, so no approval: b-prod.\n"))

    def test_the_platform_s_changed_stacks_are_one_job_in_order(self):
        Path(".mlz/reviewed").mkdir()
        for stack in ("connectivity", "management"):
            Path(f".mlz/reviewed/changes-{stack}.txt").write_text("x\n")
        Path(".mlz/reviewed/changes-identity.txt").write_text("")
        out, _ = self.run_changed("platform", ["connectivity", "management", "identity"], [])
        self.assertTrue(out.startswith('stacks=["connectivity management"]\n'))

    def test_nothing_to_apply(self):
        Path(".mlz/reviewed").mkdir()
        Path(".mlz/reviewed/changes-a-prod.txt").write_text("")
        out, summary = self.run_changed("platform", ["a-prod"], [])
        self.assertTrue(out.startswith("stacks=[]\n"))
        self.assertEqual(summary, "To apply: nothing. No changes, so no approval: a-prod.\n")  # no note: nothing waits
        self.assertEqual((self.github.calls, self.github.comments), ([], []))

    def changes(self):
        Path(".mlz/reviewed").mkdir()
        Path(".mlz/reviewed/changes-connectivity.txt").write_text("x\n")

    def test_a_person_s_merge_waits_and_its_pull_request_is_told(self):
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(6, sha="other"), pull(7)]))
        self.assertIn("### Waiting for approval\n\nThis run applies connectivity once it is approved in the `platform` environment, by one of its "
                      "required reviewers. It was started by #7, merged: feat: a hub. Every later push or manual run on main queues behind this one until "
                      "it finishes: applied, or rejected.", summary)
        self.assertEqual(self.github.calls, [("commit_pulls", "acme/mlz-platform", "sha1")])
        self.assertEqual(self.github.comments, [("acme/mlz-platform", 7, "This merge's run waits for approval in the `platform` environment before it "
                                                 "applies connectivity: https://example/run/9\n\nEvery later push or manual run on main queues behind this one "
                                                 "until it finishes: applied, or rejected.")])
        self.assertEqual(self.github.timeouts, [changed.DEADLINE, changed.DEADLINE])  # never the job's whole five minutes

    def test_the_pull_request_s_own_title_not_a_merge_commit_s(self):
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(7, title="feat: a second hub")]),
                                      message="Merge pull request #7 from acme/second-hub\n\nfeat: a second hub")
        self.assertIn("It was started by #7, merged: feat: a second hub.", summary)

    def test_a_contract_merge_says_whose_contract_and_comments_nowhere(self):
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(14, ref="mlz/vending-contract", kind="Bot")]),
                                      message="chore: vending contract from acme/mlz-vending (#14)")
        self.assertIn("It was started by vending's contract, merged (#14).", summary)  # by the merge job, or by a person
        self.assertEqual(self.github.comments, [])
        # Any other pull request an app opened gets no comment either.
        self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(15, kind="Bot")]))
        self.assertEqual(self.github.comments, [])
        # The bootstrap's contract, which mlz install publish opens with an engineer's own login: a contract all the same.
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push",
                                      github=Pulls([pull(3, ref="mlz/bootstrap-contract", title="chore: bootstrap contract")]))
        self.assertIn("It was started by bootstrap's contract, merged (#3).", summary)
        self.assertEqual(self.github.comments, [])
        # A fork's branch of a contract's name is a person's pull request: a contract comes from the repo itself.
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push",
                                      github=Pulls([pull(9, ref="mlz/vending-contract", repo="someone/mlz-platform", title="fix: a rule")]))
        self.assertIn("It was started by #9, merged: fix: a rule.", summary)
        self.assertEqual([number for _, number, _ in self.github.comments], [9])
        # A person's pull request is told by its branch, whatever its title says.
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(8, title="chore: vending contract update")]))
        self.assertIn("It was started by #8, merged: chore: vending contract update.", summary)
        self.assertEqual([number for _, number, _ in self.github.comments], [8])

    def test_a_pull_request_merged_into_another_branch_started_nothing_here(self):
        # Merged into release, its merge commit then pushed onto main: a push, not that pull request's merge into main.
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(11, base="release")]),
                                      message="fix: on release")
        self.assertIn("It was started by a push to main: fix: on release.", summary)
        self.assertEqual(self.github.comments, [])

    def test_a_push_without_a_pull_request(self):
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([]), message="fix: by hand\n\n" + "x" * 200_000)
        self.assertIn("It was started by a push to main: fix: by hand.", summary)  # a long message is read from the event, never passed along
        # A subject long enough to take the summary past GitHub's 1 MiB is cut, and says so.
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([]), message="y" * 2_000_000)
        self.assertIn("It was started by a push to main: " + "y" * changed.TITLE + "….", summary)
        self.assertLess(len(summary), 10_000)
        self.assertEqual(self.github.comments, [])
        # Without a pull request it is a push, whatever its title says.
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([]),
                                      message="chore: governance contract from acme/mlz-governance")
        self.assertIn("It was started by a push to main: chore: governance contract from acme/mlz-governance.", summary)

    def test_a_manual_run_asks_github_nothing(self):
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="workflow_dispatch")
        self.assertIn("It was started by a manual run by alex.", summary)
        self.assertEqual((self.github.calls, self.github.comments), ([], []))

    def test_a_re_run_comments_no_more(self):
        self.changes()
        _, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(7)]), GITHUB_RUN_ATTEMPT="2")
        self.assertIn("### Waiting for approval", summary)
        self.assertEqual(self.github.comments, [])

    def test_a_pull_request_without_its_author_gets_no_comment(self):
        self.changes()
        nobody = {**pull(7), "user": None, "head": None}
        out, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([nobody]))
        self.assertTrue(out.startswith('stacks=["connectivity"]\n'))
        self.assertIn("It was started by #7, merged: feat: a hub.", summary)
        self.assertEqual(self.github.comments, [])

    def test_github_failing_never_stops_the_apply(self):
        self.changes()
        out, summary = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(7)], fails="pulls"))
        self.assertTrue(out.startswith('stacks=["connectivity"]\n'))
        self.assertIn("It was started by a push to main: feat: a hub (#7).", summary)
        self.assertEqual(self.said, ["::notice::The pull request this push merged could not be found (HTTP 502); the run's page says why it waits."])
        # Stalled past its deadline: the same, well inside the job's own timeout.
        out, _ = self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(7)], fails="stalls"))
        self.assertTrue(out.startswith('stacks=["connectivity"]\n'))
        self.assertTrue(self.said[0].startswith("::notice::The pull request this push merged could not be found (Command '['gh', 'api']' timed out after 30 seconds)"))
        self.run_changed("platform", ["connectivity"], [], event="push", github=Pulls([pull(7)], fails="comment"))
        self.assertEqual(self.said, ["::notice::Could not comment on #7 (HTTP 403); the run's page says why it waits."])


class ApplyGuardTest(Case):
    """apply.sh's refusals, one test each, as #100 made them."""

    def setUp(self):
        super().setUp()
        Path(".mlz/reviewed").mkdir()
        Path(".mlz/reviewed/changes-pay-prod.txt").write_text("create a\nupdate b\n")
        Path(".mlz/reviewed/version-pay-prod.txt").write_text("4:lineage\n")

    def test_no_reviewed_plan_no_apply(self):
        self.assertEqual(apply_guard.before("vending", "pay-dev", say=self.said.append), apply_guard.REFUSE)
        self.assertEqual(self.said, ["::error::No reviewed plan for pay-dev in this run; nothing is applied."])

    def test_a_change_main_no_longer_says_stands_down(self):
        main = Path("main")
        main.mkdir()
        Path(".mlz/vending").mkdir()
        Path(".mlz/vending/pay-prod.json").write_text('{"a": 1}')

        def run(command, **kwargs):
            if command[0] == "git":
                return mock.Mock(stdout="newer-sha\n", returncode=0)
            out = Path(command[command.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "pay-prod.json").write_text(self.latest)
            return mock.Mock(returncode=0)
        with mock.patch.dict(os.environ, {"MLZ_MAIN": str(main), "GITHUB_SHA": "run-sha"}), mock.patch.object(apply_guard.subprocess, "run", run):
            self.latest = '{"a": 1}'
            self.assertEqual(apply_guard.before("vending", "pay-prod", say=self.said.append), apply_guard.GO)  # main renders it the same
            self.latest = '{"a": 2}'
            self.assertEqual(apply_guard.before("vending", "pay-prod", say=self.said.append), apply_guard.STAND_DOWN)
        self.assertEqual(self.said, ["::notice::main has moved on, and its latest commit changes pay-prod (or no longer checks with this release): "
                                     "that commit's run plans it and applies what is left to change. Nothing is applied here."])

    def test_main_that_no_longer_checks_stands_down_and_the_platform_never_does(self):
        main = Path("main")
        main.mkdir()
        run = mock.Mock(side_effect=[mock.Mock(stdout="newer-sha\n"), mock.Mock(returncode=1)])
        with mock.patch.dict(os.environ, {"MLZ_MAIN": str(main), "GITHUB_SHA": "run-sha"}), mock.patch.object(apply_guard.subprocess, "run", run):
            self.assertEqual(apply_guard.before("vending", "pay-prod", say=self.said.append), apply_guard.STAND_DOWN)
            Path(".mlz/reviewed/changes-connectivity.txt").write_text("")
            self.assertEqual(apply_guard.before("platform", "connectivity", say=self.said.append), apply_guard.GO)
        with mock.patch.dict(os.environ, {"MLZ_MAIN": str(main), "GITHUB_SHA": "same"}), \
                mock.patch.object(apply_guard.subprocess, "run", return_value=mock.Mock(stdout="same\n")):
            self.assertEqual(apply_guard.before("vending", "pay-prod"), apply_guard.GO)  # this run is main's latest

    def test_a_state_another_run_changed_refuses(self):
        self.assertEqual(apply_guard.state("pay-prod", "4:lineage", say=self.said.append), apply_guard.GO)
        self.assertEqual(apply_guard.state("pay-prod", "5:lineage", say=self.said.append), apply_guard.REFUSE)
        self.assertEqual(self.said, ["The state is the one the reviewed plan started from: 4:lineage.",  # a pass is logged too (finding 19)
                                     "::error::pay-prod's state changed after this plan was reviewed (it was at 4:lineage, it is at 5:lineage): another "
                                     "run applied it. Nothing is applied. Start a new run on main for a fresh plan (gh workflow run mlz.yml --ref main)."])
        self.assertEqual(apply_guard.state("pay-dev", "none", say=self.said.append), apply_guard.REFUSE)  # not recorded is no match
        self.assertIn("(it was at not recorded, it is at none)", self.said[-1])

    def test_a_plan_that_differs_from_the_reviewed_one_refuses(self):
        Path(".mlz/apply-changes-pay-prod.txt").write_text("create a\nupdate b\n")
        self.assertEqual(apply_guard.matches("pay-prod", say=self.said.append), apply_guard.GO)
        self.assertEqual(self.said, ["The plan matches the reviewed one: 2 change(s)."])
        Path(".mlz/apply-changes-pay-prod.txt").write_text("create a\nupdate c\n")
        self.assertEqual(apply_guard.matches("pay-prod", say=self.said.append), apply_guard.REFUSE)
        self.assertTrue(self.said[1].startswith("::error::The plan differs from the one the reviewer saw; nothing is applied."))
        self.assertIn("-update b\n+update c", self.said[2])
        self.assertEqual(Path(".mlz/difference-pay-prod.txt").read_text().splitlines()[-2:], ["-update b", "+update c"])


if __name__ == "__main__":
    unittest.main()
