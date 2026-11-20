"""approved.py <role> <stack>...: the gate on an apply where no environment waits for a reviewer, as on GitHub Team
(MLZ_APPROVAL=pull-request; bootstrap.yaml's github.plan, docs/bootstrap.md). GitHub allows required reviewers on an
environment in a private repo only on Enterprise, so there the approval is the pull request's: its code owner approved
its plan, and the run on main applies a stack only if main's plan of it is that plan, change for change and value for
value (.mlz/reviewed/changes-<stack>.txt against the same file of the pull request's last run).

The pull request is the one whose merge made the commit main is at (GITHUB_SHA): on a push, the one just merged; on a
manual run, the last one merged. Its last run is the caller's workflow's (WORKFLOW_REF) run on its head commit; this
waits for it to finish (WAIT seconds at most), then reads its plan artifacts (ARTIFACTS, a pattern). Refused, nothing
applies:
- a commit no pull request made: in a role repo, the rule on main lets none through;
- a pull request whose run was run again after its merge: that plan is of a later Azure, which nobody approved, and
  re-running an old merge's runs would otherwise apply it (a rollback, say);
- a stack whose plan on main changes something that the pull request's run did not plan, or planned otherwise: Azure
  or the state changed in between, or an apply failed half way. A new pull request plans it again (replan.txt in a
  role repo, docs/pipelines.md; any pull request in a workload repo).
A workload repo's commit that no pull request made is the MLZ App's, the landing zone's values from vending, which its
rule on main lets through alone (tools/mlz/workload.py): applied as vending wrote it, as with Enterprise's reviewer gone.
Only a commit an app's bot wrote, though (a person's merge whose pull request GitHub does not list yet is refused), and
on a re-run only while it is still main's head, so that re-running an old values commit's run never rolls back.

Needs GH_TOKEN (actions: read), GITHUB_REPOSITORY, GITHUB_SHA, WORKFLOW_REF (github.workflow_ref, the caller's
workflow) and, from the command line or changed.py, the stacks whose plan on main changes something.
"""

from __future__ import annotations

import difflib
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import ConfigError, Pipeline, env  # noqa: E402

# Seconds: how long a pull request's run may take to finish after the merge (a plan job may take 30 minutes, and main's
# own plan took a while already), and between looks.
WAIT, PAUSE = 1500, 15
LOOKS = 3  # times the pull request of a commit just merged is asked for, PAUSE apart, before there is said to be none
REPLAN = {"workload": "Open a pull request (an empty commit will do), whose run plans it again, and merge it once a code owner "
                      "approves that plan."}
REPLAN_ROLE = ("Open a pull request that changes replan.txt (any line: say why), whose run plans every stack again, and merge "
               "it once a code owner approves that plan (docs/pipelines.md, Approval on GitHub Team).")


def merged_pull(github, repo: str, sha: str, timeout: float | None = None) -> dict | None:
    """The pull request whose merge made this commit on main, or None for a commit without one. Merged into main: one
    merged into another branch, whose merge commit was then pushed onto main, did not make it."""
    return next((pull for pull in github.commit_pulls(repo, sha, timeout=timeout)
                 if pull.get("merged_at") and pull.get("merge_commit_sha") == sha and (pull.get("base") or {}).get("ref") == "main"), None)


def started_after(run: dict, pull: dict) -> bool:
    """Whether the run's latest attempt started after the pull request was merged (GitHub's UTC timestamps)."""
    started, merged = run.get("run_started_at"), pull.get("merged_at")
    return bool(started and merged) and datetime.fromisoformat(started.replace("Z", "+00:00")) > datetime.fromisoformat(merged.replace("Z", "+00:00"))


def app_commit(github, repo: str, sha: str, say=print) -> bool:
    """Whether a workload repo's commit without a pull request may apply as it is: an app's bot wrote it (the MLZ App,
    the only one its rule lets through), and on a re-run it is still main's head."""
    author = github.commit_author(repo, sha) or {}
    if author.get("type") != "Bot" or not str(author.get("login", "")).endswith("[bot]"):
        say(f"::error::No pull request made {sha[:7]}, and its author {author.get('login') or 'unknown'} is not the MLZ App; nothing is applied.")
        return False
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1" and github.head(repo, "main") != sha:
        say(f"::error::{sha[:7]} is no longer main's head: a re-run of its apply would roll the landing zone back; nothing is applied.")
        return False
    return True


def workflow_file(workflow_ref: str) -> str:
    """The caller's workflow file, from github.workflow_ref (acme/mlz-platform/.github/workflows/mlz.yml@refs/heads/main)."""
    path = workflow_ref.split("@", 1)[0]
    if "/.github/workflows/" not in path:
        raise ConfigError(f"WORKFLOW_REF is {workflow_ref!r}, not a workflow's ref.")
    return path.rsplit("/", 1)[-1]


def last_run(github, repo: str, workflow: str, head: str, wait: float = WAIT, pause: float = PAUSE,
             sleep: Callable[[float], None] = time.sleep, say=print) -> dict | None:
    """The pull request's last run on its head commit, once it has finished; None when it has none. A run still going
    after wait seconds is returned as it is: its plans are then missing, and the gate says so."""
    waited = 0.0
    while True:
        runs = [run for run in github.workflow_runs(repo, workflow, head) if run.get("event") == "pull_request"]
        found = max(runs, key=lambda run: run.get("run_number", 0), default=None)
        if found is None or found.get("status") == "completed" or waited >= wait:
            return found
        if not waited:
            say(f"Waiting for the pull request's run to finish: {found.get('html_url', '')}")
        sleep(pause)
        waited += pause


def differences(stacks: list[str], reviewed: Path, approved: Path) -> dict[str, str]:
    """Each stack whose plan on main (reviewed/) is not the one the pull request's run made (approved/, its artifacts in
    folders of their own), with why."""
    found = {path.name: path for path in approved.rglob("changes-*.txt")}
    refused = {}
    for stack in stacks:
        ours, theirs = reviewed / f"changes-{stack}.txt", found.get(f"changes-{stack}.txt")
        if theirs is None:
            refused[stack] = "its plan on main changes it, but the pull request's run has no plan of it (its change did not touch it, or the plan failed)."
            continue
        expected, planned = theirs.read_text().splitlines(True), ours.read_text().splitlines(True) if ours.is_file() else []
        if expected != planned:
            refused[stack] = ("its plan on main is not the one the pull request's run showed:\n"
                              + "".join(difflib.unified_diff(expected, planned, "pull request", "main")).rstrip("\n"))
    return refused


def approved(role: str, stacks: list[str], github=None, artifacts: str | None = None, wait: float = WAIT,
             sleep: Callable[[float], None] = time.sleep, say=print) -> bool:
    """Whether every stack may apply: see the module's docstring. Says why on the run's page when it may not."""
    if not stacks:
        return True
    github = github or Pipeline()
    repo, sha = env("GITHUB_REPOSITORY"), env("GITHUB_SHA")
    replan = REPLAN.get(role, REPLAN_ROLE)
    pull = merged_pull(github, repo, sha)
    for _ in range(LOOKS - 1):  # GitHub may list a merge's pull request a little after the push
        if pull is not None:
            break
        sleep(PAUSE)
        pull = merged_pull(github, repo, sha)
    if pull is None:
        if role == "workload":
            if not app_commit(github, repo, sha, say):
                return False
            say("::notice::No pull request made this commit: it is the MLZ App's, the landing zone's values from vending, which the rule on "
                "main lets through alone. Applied as vending wrote it.")
            return True
        say(f"::error::No pull request made {sha[:7]}, so no code owner approved a plan of it; nothing is applied. {replan}")
        return False
    number = pull["number"]
    run = last_run(github, repo, workflow_file(env("WORKFLOW_REF")), pull["head"]["sha"], wait=wait, sleep=sleep, say=say)
    if run is None:
        say(f"::error::#{number} has no run on its last commit, so no plan of it was shown; nothing is applied. {replan}")
        return False
    if started_after(run, pull):
        say(f"::error::#{number}'s run {run.get('html_url', run['id'])} was run again after its merge ({run.get('run_started_at')}): its "
            f"plans are not the ones approved on the pull request; nothing is applied. {replan}")
        return False
    folder = Path.cwd() / ".mlz" / "approved"
    if not github.download_artifacts(repo, run["id"], artifacts or f"mlz-{role}-plan-*", folder):
        say(f"::error::#{number}'s run {run.get('html_url', run['id'])} has no plans to read (it did not finish, or its artifacts have "
            f"expired after 30 days); nothing is applied. {replan}")
        return False
    refused = differences(stacks, Path.cwd() / ".mlz" / "reviewed", folder)
    for stack, why in refused.items():
        say(f"::error::{stack}: {why}")
    if refused:
        say(f"::error::Nothing is applied: main's plan must be the one #{number}'s run showed ({run.get('html_url', run['id'])}). {replan}")
        return False
    say(f"The plan of {', '.join(stacks)} is the one #{number}'s run showed: {run.get('html_url', run['id'])}")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    try:
        sys.exit(0 if approved(sys.argv[1], sys.argv[2:], artifacts=os.environ.get("ARTIFACTS") or None) else 1)
    except ConfigError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
