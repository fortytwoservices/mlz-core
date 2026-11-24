"""Tests for the contract steps, publish.py, merge.py and ip_plan.py: every case their bash had (each comment in the
scripts names one), against a fake GitHub that keeps files, branches and pull requests in memory and records what it is
asked to do."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import gh, ip_plan, merge, publish
from ci.gh import ConfigError

REPOS = {"governance": "acme/mlz-governance", "platform": "acme/mlz-platform", "vending": "acme/mlz-vending"}
RUN = "https://github.com/acme/mlz-governance/actions/runs/7"
CONTRACT = {"root": "acme"}


class FakeGitHub:
    def __init__(self, main_sha="run-sha"):
        self.main_sha = main_sha
        self.files: dict[tuple[str, str, str], str] = {}  # (repo, ref, path) -> text; ref "main" for the default branch
        self.pulls: dict[tuple[str, str], list[int]] = {}  # (repo, branch) -> open pull request numbers
        self.calls: list[tuple] = []
        self.fail: dict[str, object] = {}  # method -> ConfigError text, or a list of them, one per call
        self.after_put = None  # what put_file does to the open pull requests, for a merge in between

    def _maybe_fail(self, name):
        failure = self.fail.get(name)
        if isinstance(failure, list):
            failure = failure.pop(0) if failure else None
        if failure:
            raise ConfigError(f"gh api X path: {failure}")

    def sha(self, repo, ref):
        return self.main_sha

    def text(self, repo, path, ref=None):
        self._maybe_fail("text")
        return self.files.get((repo, ref or "main", path))

    def open_pulls(self, repo, branch):
        self._maybe_fail("open_pulls")
        return list(self.pulls.get((repo, branch), []))

    def close_pull(self, repo, number, comment):
        self.calls.append(("close", repo, number, comment))
        self.pulls = {k: [n for n in v if n != number] for k, v in self.pulls.items()}

    def put_file(self, repo, path, branch, text, message):
        self._maybe_fail("put_file")
        self.calls.append(("put", repo, branch, path, message))
        self.files[(repo, branch, path)] = text
        if self.after_put:
            self.after_put()

    def reset_branch(self, repo, branch, base, at=None):
        self.calls.append(("reset", repo, branch, base))
        return "main-sha"

    def create_pull(self, repo, branch, base, title, body):
        self.calls.append(("create", repo, branch, base, title, body))
        number = 10 + len([c for c in self.calls if c[0] == "create"])
        self.pulls.setdefault((repo, branch), []).append(number)
        return f"https://github.com/{repo}/pull/{number}"

    # merge.py
    def pull_files(self, repo, number):
        return self.files_of_pull

    def squash_allowed(self, repo):
        return self.squash

    def behind_by(self, repo, base, head):
        return self.behind

    def update_branch(self, repo, number, expected_head):
        self.calls.append(("update", repo, number, expected_head))
        self._maybe_fail("update_branch")

    def merge_pull(self, repo, number, method, sha):
        self.calls.append(("merge", repo, number, method, sha))
        self._maybe_fail("merge_pull")

    # ip_plan.py
    def raw(self, repo, path, ref):
        self._maybe_fail("raw")
        return self.files.get((repo, ref, path))

    def commit_message(self, repo, sha):
        return self.last_message

    def changed_files(self, repo, base, head):
        return self.base_changed

    def pull_head(self, repo, number):
        return self.head_now


class Case(unittest.TestCase):
    environment: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.env = mock.patch.dict(os.environ, self.environment)
        self.env.start()
        self.said: list[str] = []
        self.github = FakeGitHub()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def write(self, path: str, text: str = "x: 1\n") -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)


class PublishTest(Case):
    environment = {"GITHUB_REPOSITORY": REPOS["governance"], "GITHUB_SHA": "run-sha", "GITHUB_SERVER_URL": "https://github.com",
                   "GITHUB_RUN_ID": "7", "MLZ_REPOSITORIES": json.dumps(REPOS)}

    def offer(self, role="governance", state=CONTRACT):
        return publish.offer(role, self.github, state=lambda role: state, say=self.said.append)

    def contract(self) -> str:
        return Path(".mlz/governance.yaml").read_text()

    def test_a_role_with_no_readers_offers_nothing(self):
        self.assertEqual(publish.offer("workload", self.github, say=self.said.append), 0)
        self.assertEqual((self.said, self.github.calls), ([], []))

    def test_vending_offers_nothing_until_it_has_both_contracts(self):
        self.write("_generated/governance.yaml")
        self.assertEqual(self.offer("vending"), 0)
        self.assertEqual(self.said, ["No platform contract here yet: vending offers nothing until it has run with both."])

    def test_no_state_no_contract(self):
        self.assertEqual(self.offer(state=None), 0)
        self.assertEqual(self.said, ["No governance state yet: nothing has been applied, so there is no contract to offer."])

    def test_a_run_main_has_moved_past_offers_nothing(self):
        self.github.main_sha = "newer"
        self.assertEqual(self.offer(), 0)
        self.assertEqual(self.said, ["main is at newer now, not at this run's run-sha: the newer run offers the contract."])
        self.assertEqual(self.github.calls, [])

    def test_a_new_contract_is_offered_in_a_new_pull_request_from_main(self):
        self.assertEqual(self.offer(), 0)
        message = f"chore: governance contract from {REPOS['governance']}"
        for reader in ("platform", "vending"):
            repo = REPOS[reader]
            self.assertIn(("reset", repo, "mlz/governance-contract", "main"), self.github.calls)
            self.assertIn(("put", repo, "mlz/governance-contract", "_generated/governance.yaml", message), self.github.calls)
            self.assertIn(f"{repo}: offered the new _generated/governance.yaml", self.said)
        create = [c for c in self.github.calls if c[0] == "create"][0]
        self.assertEqual(create[4], "chore: governance contract")
        self.assertIn(f"The governance contract after the apply in {RUN}.", create[5])

    def test_up_to_date_closes_a_stale_pull_request_left_open(self):
        self.offer()
        for reader in ("platform", "vending"):
            self.github.files[(REPOS[reader], "main", "_generated/governance.yaml")] = self.contract() + "\n\n"  # newlines aside
        self.github.calls, self.said = [], []
        self.assertEqual(self.offer(), 0)
        self.assertIn(f"{REPOS['platform']}: _generated/governance.yaml is up to date", self.said)
        self.assertIn(f"{REPOS['platform']}: closed #11, which no longer matches what governance deployed", self.said)
        self.assertIn(("close", REPOS["platform"], 11, f"Closed by {RUN}: _generated/governance.yaml on main already matches what governance deployed."),
                      self.github.calls)

    def test_an_open_pull_request_that_offers_this_contract_is_left_alone(self):
        self.offer()
        self.github.calls, self.said = [], []
        self.assertEqual(self.offer(), 0)
        self.assertIn(f"{REPOS['platform']}: #11 already offers this _generated/governance.yaml", self.said)
        self.assertEqual(self.github.calls, [])

    def test_an_open_pull_request_gets_the_new_contract_on_its_own_branch(self):
        self.offer()
        self.github.calls, self.said = [], []
        self.assertEqual(self.offer(state={"root": "acme", "more": 1}), 0)
        self.assertIn(f"{REPOS['platform']}: offered the new _generated/governance.yaml in #11", self.said)
        self.assertFalse([c for c in self.github.calls if c[0] in ("reset", "create")])  # reset, the branch would close it

    def test_a_branch_that_cannot_be_written_is_left_and_the_step_fails_after_every_reader(self):
        self.offer()
        self.github.calls, self.said = [], []
        self.github.fail["put_file"] = "HTTP 403"
        self.assertEqual(self.offer(state={"root": "acme", "more": 1}), 1)
        self.assertEqual([s for s in self.said if s.startswith("::error::")], [
            f"::error::{REPOS[r]}: could not write the new _generated/governance.yaml to the branch of #{n}; the next run on main tries again."
            for r, n in (("platform", 11), ("vending", 12))])

    def test_not_knowing_whether_the_pull_request_is_still_open_fails_and_leaves_its_branch(self):
        self.offer()
        self.github.calls, self.said = [], []
        self.github.fail["open_pulls"] = [None, "HTTP 502", None, None]  # the check after writing fails, for platform
        self.assertEqual(self.offer(state={"root": "acme", "more": 1}), 1)
        self.assertIn(f"::error::{REPOS['platform']}: could not tell whether #11 is still open, so its branch is left as it is; "
                      "the next run on main tries again.", self.said)

    def test_a_pull_request_merged_meanwhile_starts_again_from_main(self):
        self.offer()
        self.github.calls, self.said = [], []
        self.github.after_put = lambda: self.github.pulls.clear()  # the reader's run merges it as the contract is written
        self.assertEqual(self.offer(state={"root": "acme", "more": 1}), 0)
        self.assertIn(("reset", REPOS["platform"], "mlz/governance-contract", "main"), self.github.calls)
        self.assertIn(f"{REPOS['platform']}: offered the new _generated/governance.yaml", self.said)

    def test_a_contract_that_cannot_be_read_counts_as_not_offered(self):
        self.github.fail["text"] = ["HTTP 502"] * 2
        self.assertEqual(self.offer(), 0)
        self.assertIn(f"{REPOS['platform']}: offered the new _generated/governance.yaml", self.said)

    def test_each_role_offers_to_its_readers(self):
        self.assertEqual(publish.READERS, {"governance": ("platform", "vending"), "platform": ("vending",), "vending": ("platform",)})

    def test_the_state_read_is_the_stack_that_outputs_the_contract(self):  # which stack: tools/mlz/test_stacks.py
        keys = []

        def run(command, **kwargs):
            keys.append(command[command.index("--name") + 1])
            self.assertNotIn("capture_output", kwargs)  # the Azure CLI's message about a failure stays in the log
            self.assertNotIn("stderr", kwargs)
            return mock.Mock(stdout="false\n")
        with mock.patch.dict(os.environ, {"TFSTATE_STORAGE_ACCOUNT": "st", "TFSTATE_CONTAINER": "platform"}), mock.patch.object(publish.subprocess, "run", run):
            self.assertIsNone(publish.state_contract("platform"))
            self.assertIsNone(publish.state_contract("governance"))
        self.assertEqual(keys, ["connectivity.tfstate", "governance.tfstate"])


class MergeTest(Case):
    environment = {"GITHUB_REPOSITORY": REPOS["vending"], "PR_NUMBER": "5", "HEAD_SHA": "head-sha", "BASE_REF": "main"}

    def setUp(self):
        super().setUp()
        self.github.files_of_pull = [{"filename": "_generated/platform.yaml"}]
        self.github.squash, self.github.behind = True, 0
        self.slept: list[int] = []

    def merge(self):
        return merge.merge(self.github, say=self.said.append, sleep=self.slept.append)

    def test_a_contract_merges_itself_squashed(self):
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.github.calls, [("merge", REPOS["vending"], 5, "squash", "head-sha")])
        self.assertEqual(self.said, ["::notice::Merged #5. The run on main now plans with it, and applies once this role's reviewer approves."])

    def test_a_merge_commit_where_squash_is_not_allowed(self):
        self.github.squash = False
        self.merge()
        self.assertEqual(self.github.calls[0][3], "merge")

    def test_anything_beside_generated_waits_for_a_person(self):
        self.github.files_of_pull = [{"filename": "_generated/ip-plan.yaml"}, {"filename": "_generated/names.yaml", "previous_filename": "names.yaml"},
                                     {"filename": "README.md"}]
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.said, ["::notice::This pull request changes more than _generated/, so a person merges it: names.yaml README.md "])
        self.assertEqual(self.github.calls, [])

    def test_a_branch_left_behind_is_brought_up_to_date_for_its_own_run(self):
        self.github.behind = 2
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.github.calls, [("update", REPOS["vending"], 5, "head-sha")])
        self.assertIn("moved on since this branch was made, so the branch is now up to date with it", self.said[0])

    def test_a_branch_behind_that_cannot_be_brought_up_to_date_is_left_for_a_person(self):
        self.github.behind, self.github.fail["update_branch"] = 1, "HTTP 422: expected head sha didn't match"
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.said, ["::warning::#5 is behind main and could not be brought up to date: a newer push has a run of its own; "
                                     "otherwise a person merges main into it. HTTP 422: expected head sha didn't match"])

    def test_a_branch_that_moved_on_is_merged_by_its_newer_run(self):
        self.github.fail["merge_pull"] = "HTTP 409: Head branch was modified"
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.said, ["::notice::The branch moved on since this run checked it. The run of its newer commit merges it."])

    def test_a_refusal_is_tried_three_times_then_left_for_a_person(self):
        self.github.fail["merge_pull"] = ["HTTP 405: Base branch was modified"] * 3
        self.assertEqual(self.merge(), 0)
        self.assertEqual(len([c for c in self.github.calls if c[0] == "merge"]), 3)
        self.assertEqual(self.slept, [10, 10])
        self.assertEqual(self.said, ["::warning::#5 stays open for a person to merge: HTTP 405: Base branch was modified"])

    def test_a_refusal_then_a_merge(self):
        self.github.fail["merge_pull"] = ["HTTP 405: Base branch was modified", None]
        self.merge()
        self.assertTrue(self.said[-1].startswith("::notice::Merged #5."))


class IpPlanTest(Case):
    environment = {"GITHUB_REPOSITORY": REPOS["vending"], "PR_NUMBER": "5", "HEAD_REF": "lz/payments", "HEAD_SHA": "head-sha", "BASE_SHA": "base-sha"}

    def setUp(self):
        super().setUp()
        self.write("_generated/platform.yaml")
        self.write("_generated/governance.yaml")
        self.github.files[(REPOS["vending"], "base-sha", "_generated/names.yaml")] = "payments-prod: {}\n"
        self.github.last_message, self.github.base_changed, self.github.head_now = "a person's change", [], "head-sha"
        self.allocated: list[str] = []
        self.moved, self.committed = True, None

    def update(self, commit=None):
        def allocate(path):
            self.allocated.append(path.read_text())

        def committer(github, repo, branch, head):
            if self.committed is None:
                raise ConfigError("gh api POST graphql: expectedHeadOid did not match")
            return self.committed
        return ip_plan.update(self.github, allocate=allocate, changed=lambda: self.moved, commit=commit or committer, say=self.said.append)

    def test_without_both_contracts_there_is_nothing_to_give_out(self):
        Path("_generated/governance.yaml").unlink()
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.said, ["No governance contract yet (_generated/governance.yaml): no addresses or names to give out."])

    def test_the_base_s_names_are_kept_and_none_on_a_base_without_them(self):
        self.moved = False
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.allocated, ["payments-prod: {}\n"])
        self.assertEqual(self.said, ["The IP plan and the names are up to date."])
        del self.github.files[(REPOS["vending"], "base-sha", "_generated/names.yaml")]
        self.update()
        self.assertEqual(self.allocated[-1], "")

    def test_a_base_whose_names_cannot_be_read_stops_before_renaming_anything(self):
        self.github.fail["raw"] = "HTTP 502: Bad Gateway"
        self.assertEqual(self.update(), 1)
        self.assertEqual(self.said, ["::error::Could not read the base's names (_generated/names.yaml at base-sh): gh api X path: HTTP 502: Bad Gateway"])
        self.assertEqual(self.allocated, [])

    def test_what_it_committed_must_come_out_the_same_next_time(self):
        self.github.last_message = ip_plan.MESSAGE
        self.assertEqual(self.update(), 1)
        self.assertTrue(self.said[0].startswith("::error::The IP plan or the names changed again after they were committed"))
        # Only the bypass team may push the allocator's result by hand (the ruleset generated-files).
        self.assertTrue(self.said[0].endswith("Only the MLZ App and the bypass team (bootstrap.yaml's github.bypass_team) may change files under "
                                              "_generated/. A member of that team runs ./mlz allocate --repo . on the branch and pushes the result; "
                                              "anyone else asks one."), self.said[0])

    def test_a_base_that_changed_them_brings_the_branch_up_to_date_first(self):
        self.github.base_changed = ["README.md", "_generated/ip-plan.yaml"]
        self.assertEqual(self.update(), 1)
        self.assertEqual(self.github.calls, [("update", REPOS["vending"], 5, "head-sha")])
        self.assertTrue(self.said[0].startswith("::notice::The base changed the IP plan or the names since this branch was made, so the branch is now up to date"))
        self.github.fail["update_branch"] = "HTTP 422"
        self.assertEqual(self.update(), 1)
        self.assertTrue(self.said[-1].startswith("::error::The base changed the IP plan or the names since this branch was made, and the branch could not"))
        # A person's merge keeps the base's files there: a merge commit that changes one itself is refused.
        self.assertTrue(self.said[-1].endswith("Merge the base into the branch, keeping the base's version of any file under _generated/ that "
                                               "conflicts; the next run writes this pull request's into them. Only the MLZ App and the bypass team "
                                               "(bootstrap.yaml's github.bypass_team) may change files under _generated/."), self.said[-1])

    def test_a_base_that_changed_other_files_does_not(self):
        self.github.base_changed = ["README.md"]
        self.committed = "abcdef0123"
        self.assertEqual(self.update(), 1)
        self.assertEqual(self.said, ["::notice::The IP plan or the names were out of date, so they are now committed to this pull request (abcdef0). "
                                     "The run of that commit plans with them."])

    def test_a_commit_refused_because_the_branch_moved_is_the_newer_run_s(self):
        self.github.head_now = "newer-sha"
        self.assertEqual(self.update(), 1)
        self.assertEqual(self.said, ["::notice::The branch moved on while this run allocated (now newer-s). The run of that commit writes the IP plan and the names."])
        self.github.head_now = "head-sha"
        self.update()
        self.assertEqual(self.said[-1], "::error::Could not commit the IP plan and the names to lz/payments.")

    def test_the_commit_holds_both_files_and_expects_the_checked_head(self):
        self.write("_generated/ip-plan.yaml", "plan\n")
        self.write("_generated/names.yaml", "names\n")
        sent = {}

        class Api:
            def _api(self, path, method, body):
                sent.update(body)
                return {"data": {"createCommitOnBranch": {"commit": {"oid": "1234567890"}}}}
        self.assertEqual(ip_plan.commit(Api(), REPOS["vending"], "lz/payments", "head-sha"), "1234567890")
        given = sent["variables"]["input"]
        self.assertEqual((given["expectedHeadOid"], given["branch"]["branchName"], given["message"]["headline"]), ("head-sha", "lz/payments", ip_plan.MESSAGE))
        self.assertEqual([a["path"] for a in given["fileChanges"]["additions"]], list(ip_plan.PATHS))



class PipelineTest(unittest.TestCase):
    """The client itself: gh api's answers as the steps read them."""

    def run_gh(self, answers):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            code, out, err = answers.pop(0)
            return mock.Mock(returncode=code, stdout=out, stderr=err)
        return calls, mock.patch.object(gh.subprocess, "run", run)

    def test_a_missing_file_is_none_and_any_other_failure_says_what_github_answered(self):
        calls, patched = self.run_gh([(1, "", "gh: Not Found (HTTP 404)"), (1, "", "gh: Bad Gateway (HTTP 502)")])
        with patched:
            self.assertIsNone(gh.Pipeline().text("acme/r", "_generated/x.yaml"))
            with self.assertRaisesRegex(ConfigError, r"^gh api GET repos/acme/r/contents/_generated/x.yaml\?ref=b: gh: Bad Gateway \(HTTP 502\)$"):
                gh.Pipeline().text("acme/r", "_generated/x.yaml", "b")

    def move(self, branch_at: str | None, writes: list, reads: list | None = None):
        """reset_branch against one repo's refs as GitHub keeps them: main at m, mlz/x-contract at branch_at (None: no
        such branch). Each POST or PATCH takes the next of writes: "ok" (done), "lost" (done, but the answer was empty,
        as in finding 20), an exception gh's run raises, or gh's text for a failure that leaves the branch as it was.
        The branch's first reads take reads' answers, (exit code, stdout, stderr), instead of the branch's.
        Returns the calls as (method, path, timeout), the waits, and the branch's commit after; or the error raised."""
        state, calls, reads = {"branch": branch_at}, [], list(reads or [])

        def run(command, input=None, timeout=None, **kwargs):
            method, path = command[3], command[4]
            calls.append((method, path, timeout))
            answer = lambda code, out="", err="": mock.Mock(returncode=code, stdout=out, stderr=err)
            if path.endswith("/git/ref/heads/main"):
                return answer(0, '{"object": {"sha": "m"}}')
            if method == "GET" and reads:
                return answer(*reads.pop(0))
            if method == "GET":
                return answer(0, json.dumps({"object": {"sha": state["branch"]}})) if state["branch"] else answer(1, "", "gh: Not Found (HTTP 404)")
            if method == "POST" and state["branch"]:
                return answer(1, "", "gh: Reference already exists (HTTP 422)")
            if method == "PATCH" and not state["branch"]:
                return answer(1, "", "gh: Reference does not exist (HTTP 422)")
            what = writes.pop(0)
            if isinstance(what, BaseException):
                raise what
            if what in ("ok", "lost"):
                state["branch"] = json.loads(input)["sha"]
                return answer(0, "{}") if what == "ok" else answer(1, "", "unexpected end of JSON input")
            return answer(1, "", what)

        with mock.patch.object(gh.subprocess, "run", run), mock.patch.object(gh.time, "sleep") as sleep:
            try:
                gh.Pipeline().reset_branch("acme/r", "mlz/x-contract", "main")
                error = None
            except ConfigError as raised:
                error = raised
        return calls, [c.args[0] for c in sleep.call_args_list], state["branch"], error

    def test_a_branch_is_made_from_main_or_moved_back_there(self):
        branch = "repos/acme/r/git/ref/heads/mlz/x-contract"
        for at, write in ((None, ("POST", "repos/acme/r/git/refs")), ("old", ("PATCH", "repos/acme/r/git/refs/heads/mlz/x-contract"))):
            with self.subTest(at=at):
                calls, waits, now, error = self.move(at, ["ok"])
                self.assertEqual((now, error, waits), ("m", None, []))
                self.assertEqual([c[:2] for c in calls], [("GET", "repos/acme/r/git/ref/heads/main"), ("GET", branch), write])
                self.assertEqual({c[2] for c in calls[1:]}, {gh.MOVE_TIMEOUT})
        calls, waits, now, error = self.move("m", [])  # already there: nothing to send
        self.assertEqual(([c[0] for c in calls], now, error), (["GET", "GET"], "m", None))

    def test_a_move_whose_answer_is_lost_carries_on_once_the_branch_is_there(self):
        for at in ("old", None):
            with self.subTest(at=at):
                calls, waits, now, error = self.move(at, ["lost"])
                self.assertEqual((now, error, waits), ("m", None, []))
                self.assertEqual([c[0] for c in calls], ["GET", "GET", "PATCH" if at else "POST", "GET"])

    def test_a_hiccup_is_sent_again_after_a_wait(self):
        timeout = subprocess.TimeoutExpired(["gh"], gh.MOVE_TIMEOUT)
        for hiccup in ("gh: Server Error (HTTP 502)", "gh: HTTP 503", "unexpected EOF", 'Patch "https://api.github.com/x": EOF', timeout):
            with self.subTest(hiccup=str(hiccup)):
                calls, waits, now, error = self.move("old", [hiccup, "ok"])
                self.assertEqual((now, error, waits), ("m", None, [gh.MOVE_WAIT]))
                self.assertEqual([c[0] for c in calls], ["GET", "GET", "PATCH", "GET", "GET", "PATCH"])

    def test_a_hiccup_on_creating_the_branch_is_not_taken_for_a_branch_that_exists(self):
        calls, waits, now, error = self.move(None, ['Post "https://api.github.com/repos/acme/r/git/refs": EOF', "ok"])
        self.assertEqual((now, error, waits), ("m", None, [gh.MOVE_WAIT]))
        self.assertEqual([c[0] for c in calls if c[0] != "GET"], ["POST", "POST"])

    def test_an_empty_answer_to_reading_the_branch_is_not_taken_for_no_branch(self):
        calls, waits, now, error = self.move("old", ["ok"], reads=[(0, "", "")])
        self.assertEqual((now, error, waits), ("m", None, [gh.MOVE_WAIT]))
        self.assertEqual([c[0] for c in calls if c[0] != "GET"], ["PATCH"])

    def test_a_refusal_is_not_sent_again_and_says_what_github_answered(self):
        for at, write in (("old", "PATCH"), (None, "POST")):  # a refused creation is not forced through as an update either
            with self.subTest(at=at):
                calls, waits, now, error = self.move(at, ["gh: Resource not accessible by integration (HTTP 403)"])
                self.assertEqual((now, waits), (at, []))
                self.assertRegex(str(error), rf"^gh api {write} repos/acme/r/git/refs\S*: gh: Resource not accessible by integration \(HTTP 403\)$")
                self.assertEqual([c[0] for c in calls if c[0] != "GET"], [write])

    def test_the_move_fails_with_the_last_error_after_its_last_try(self):
        calls, waits, now, error = self.move("old", ["gh: HTTP 502", "unexpected end of JSON input", "gh: Server Error (HTTP 500)"])
        self.assertEqual((now, waits), ("old", [gh.MOVE_WAIT] * (gh.MOVE_TRIES - 1)))
        self.assertRegex(str(error), r"^gh api PATCH repos/acme/r/git/refs/heads/mlz/x-contract: gh: Server Error \(HTTP 500\) \(tried 3 times\)$")
        self.assertEqual([c[0] for c in calls if c[0] != "GET"], ["PATCH"] * gh.MOVE_TRIES)
        calls, waits, now, error = self.move("old", [subprocess.TimeoutExpired(["gh"], gh.MOVE_TIMEOUT)] * gh.MOVE_TRIES)
        self.assertRegex(str(error), r"timed out after 30 seconds \(tried 3 times\)$")  # a ConfigError, as publish.main reports

    def test_a_listing_is_read_to_its_last_page(self):
        calls, patched = self.run_gh([(0, json.dumps([{"filename": f"f{i}"} for i in range(100)]), ""), (0, json.dumps([{"filename": "last"}]), "")])
        with patched:
            self.assertEqual(len(gh.Pipeline().pull_files("acme/r", 5)), 101)
        self.assertTrue(calls[1][4].endswith("per_page=100&page=2"))


if __name__ == "__main__":
    unittest.main()
