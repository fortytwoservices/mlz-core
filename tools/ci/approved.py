"""approved.py: the gate on GitHub Team (approval: pull-request). There no environment can wait for a reviewer in a private
repo, so the approval of the pull request is the only human gate, and the pipeline makes it mean as much as it can: what
applies on main is a plan a code owner could see and did approve (docs/pipelines.md, "Approval on GitHub Team"). Two
checks, one before the merge and one after:

covers <role>: a pull request's run, in its planned job, once every plan finished (role.yml, workload.yml). It passes
    - when these plans change nothing;
    - when no code owner has approved yet: any approval from now on comes after these plans;
    - when the last code owner's approval came after these plans finished;
    - when these plans are the ones that approval saw: those of the pull request's last run on the commit it approved,
      finished before it.
    Otherwise it fails, and the pull request cannot merge, since main-checks requires <caller job> / planned: the plan
    changed since the approval, so a code owner approves again and the job is run again. A code owner is, in a role repo,
    a member of a team (or a user) that .github/CODEOWNERS names on the base branch. In a workload repo, whose job's token
    cannot read a team's members, it is anyone with write access but the author: weaker, as the docs say. Approvals
    GitHub dismissed do not count.

gate <role> [<stack> ...]: a run on main, before anything applies (role.yml's changed job through changed.py, and
    workload.yml's gate job). Each stack's plan must be the plan the merged pull request's last run showed, change for
    change and value for value. Nothing applies when:
    - a later attempt of a run finds its commit no longer main's head: its fresh plan, "back to that commit", fingerprints
      as the plan once approved, and would undo what later runs applied. A first attempt is always in order: main's runs
      queue whole in a role repo, and a workload repo's pending run is replaced, never run late;
    - no pull request made the commit (role repos). In a workload repo, such a commit applies only as the MLZ App's
      values from vending, with no person's approval (decided 23 November 2026): only while a rule on main asks for a
      pull request, which vending's ruleset main lets nothing but the MLZ App bypass (a workflow's token cannot read who
      may bypass), and only when the commit has one parent and changes nothing but what vending commits: the values and
      the code owners, and the starter's files only in the commit that first adds the values;
    - the pull request's last run did not finish with success, or was run again after the merge, or while its plans were
      read;
    - its plans do not carry its number (STAMP, which each plan job of a Team pull request's run writes): the last run
      on its head commit may be another pull request's, into another branch, whose plans are others;
    - a stack's plan on main changes something the pull request's run did not plan, or planned otherwise.
    In a role repo it also reads whether the merge used a ruleset's bypass, for changed.py to say so.

Needs GH_TOKEN (Actions, Pull requests, Contents and Metadata read; in a role repo also Members and Administration read),
GITHUB_REPOSITORY, WORKFLOW_REF (github.workflow_ref: the caller's workflow), and: for covers PR_NUMBER, PR_AUTHOR,
BASE_REF, GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT, and this run's plans in .mlz/planned/; for gate GITHUB_SHA and
GITHUB_RUN_ATTEMPT, and main's plans in .mlz/reviewed/.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import ConfigError, Pipeline, env  # noqa: E402
from mlz import owners as codeowners  # noqa: E402

STAMP = "pull-request.txt"  # the pull request's number, which each plan job of a Team pull request's run puts with its plans
# GitHub lists a merge's pull request one to five seconds after the merge (tested 23 November 2026): ask for 30 seconds.
LOOKS, PAUSE = 7, 5
WRITE = ("admin", "maintain", "write")
CODEOWNERS = codeowners.PATH
# What vending's MLZ App commits to a workload repo's main (tools/mlz/workload.py): the values, the code owners, and once the
# starter's files.
VALUES = "mlz.auto.tfvars.json"
STARTER = Path(__file__).resolve().parents[2] / "starter" / "mlz-workload"
PLAN_JOB = re.compile(r"(^|/ )plan( \(.*\))?$")  # a plan job's name as the jobs API gives it: "<caller job> / plan (<batch>)"
REPLAN = ("To apply what main says, open a pull request that changes replan.txt (one line saying why): its run plans every stack, and "
          "once a code owner approves it and it merges, main applies exactly that plan.")
REPLAN_WORKLOAD = ("To apply what main says, open a pull request (any change will do: this repo plans its one stack whole), and merge it "
                   "once it is approved.")


@dataclass
class Verdict:
    ok: bool
    pull: dict | None = None
    run: dict | None = None
    bypass: str | None = None  # who merged with a ruleset's bypass, in a role repo
    notes: list[str] = field(default_factory=list)


def replan(role: str) -> str:
    return REPLAN_WORKLOAD if role == "workload" else REPLAN


def pattern(role: str) -> str:
    """The plan artifacts' names: one per plan job in a role repo, one in a workload repo."""
    return "mlz-workload-plan" if role == "workload" else f"mlz-{role}-plan-*"


def when(stamp: str | None) -> datetime | None:
    """A GitHub timestamp (UTC, ending in Z) as a time; None for none."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def link(run: dict) -> str:
    return run.get("html_url") or f"run {run.get('id')}"


def workflow_file(workflow_ref: str) -> str:
    """The caller's workflow file, from github.workflow_ref (acme/mlz-platform/.github/workflows/mlz.yml@refs/heads/main):
    inside a called workflow, github.workflow_ref is the caller's (tested 23 November 2026)."""
    path = workflow_ref.split("@", 1)[0]
    if "/.github/workflows/" not in path:
        raise ConfigError(f"WORKFLOW_REF is {workflow_ref!r}, not a workflow's ref.")
    return path.rsplit("/", 1)[-1]


def plans(folder: Path) -> dict[str, list[str]]:
    """Each stack's list of changes (changes-<stack>.txt) in a folder of downloaded plans, at any depth."""
    return {path.name[len("changes-"):-len(".txt")]: path.read_text().splitlines(True) for path in sorted(folder.rglob("changes-*.txt"))}


def stamped(folder: Path, number: int) -> bool | None:
    """Whether the downloaded plans say they are this pull request's: True when every artifact that holds plans carries its
    number, False when one carries another or none, None when there are no plans at all."""
    holders = {path.parent for path in folder.rglob("changes-*.txt")}
    if not holders:
        return None
    return all((holder / STAMP).is_file() and (holder / STAMP).read_text().strip() == str(number) for holder in holders)


def differences(stacks: list[str], ours: dict[str, list[str]], theirs: dict[str, list[str]], here: str, there: str) -> dict[str, str]:
    """Each stack whose list of changes differs between two sets of plans, with the difference. A stack that one set did not
    plan counts as no changes there."""
    found = {}
    for stack in stacks:
        mine, other = ours.get(stack, []), theirs.get(stack, [])
        if mine != other:
            found[stack] = "".join(difflib.unified_diff(other, mine, there, here)).rstrip("\n") or "(the two lists differ only in order)"
    return found


def changes_anything(found: dict[str, list[str]]) -> bool:
    return any(line.strip() for lines in found.values() for line in lines)


def merged_pull(github, repo: str, sha: str, timeout: float | None = None) -> dict | None:
    """The pull request whose merge made this commit on main, or None for a commit without one. Merged into main: one
    merged into another branch, whose merge commit was then pushed onto main, did not make it."""
    return next((pull for pull in github.commit_pulls(repo, sha, timeout=timeout)
                 if pull.get("merged_at") and pull.get("merge_commit_sha") == sha and (pull.get("base") or {}).get("ref") == "main"), None)


def find_pull(github, repo: str, sha: str, sleep: Callable[[float], None]) -> dict | None:
    """merged_pull, asked again for a while: GitHub lists a merge's pull request a few seconds after it."""
    for look in range(LOOKS):
        pull = merged_pull(github, repo, sha)
        if pull is not None or look == LOOKS - 1:
            return pull
        sleep(PAUSE)
    return None


def last_run(github, repo: str, workflow: str, head: str, number: int, artifacts: str, folder: Path) -> tuple[dict, Path] | None:
    """The pull request's last run on a commit, with its plans downloaded under folder: the newest run whose plans carry the
    pull request's number (a merged pull request's runs list it no more, so the stamp says whose a run is). A run without
    plans (it planned nothing, or stopped before) is passed over. None when there is none."""
    for run in github.pull_runs(repo, workflow, head):
        where = folder / str(run["id"])
        if not github.download_artifacts(repo, run["id"], artifacts, where):
            continue
        if stamped(where, number):
            return run, where
    return None


def plans_finished(github, repo: str, run_id: int, attempt: int, now: Callable[[], datetime]) -> datetime:
    """When the plan jobs of a run's attempt finished: the last of their completion times (a job a re-run did not run again
    keeps its own). Now, when it had none."""
    times = [when(job["completed_at"]) for job in github.run_jobs(repo, run_id, attempt)
             if PLAN_JOB.search(job.get("name", "")) and job.get("completed_at")]
    return max(times) if times else now()


def code_owner(role: str, github, repo: str, base: str) -> Callable[[str], bool]:
    """Whether a login counts as a code owner here. In a role repo: a user or a member of a team that the CODEOWNERS file
    GitHub reads on the base branch names (the first of .github/, the top and docs/, as GitHub looks; its comments, a
    line's own included, are no owners), read as tools/mlz/owners.py reads it. mlz-core's names one team, the platform team.
    In a workload repo, whose job cannot read a team's members: anyone with write access."""
    if role == "workload":
        return lambda login: github.permission(repo, login) in WRITE
    found = next(((location, text) for location in codeowners.LOCATIONS if (text := github.text(repo, location, base)) is not None), None)
    location, text = found or (CODEOWNERS, "")
    names = {owner[1:] for rule in codeowners.rules(text) for owner in rule[1:] if owner.startswith("@")}
    if not names:
        raise ConfigError(f"{location} on {base} names no owner, so no approval can be checked; mlz install repos writes it.")
    users, teams = {name for name in names if "/" not in name}, [name.split("/", 1) for name in names if "/" in name]
    return lambda login: login in users or any(github.team_member(org, team, login) for org, team in teams)


def seen_by(github, repo: str, workflow: str, review: dict, number: int, artifacts: str, folder: Path) -> tuple[dict, Path] | None:
    """The plans an approval saw: those of the pull request's last run on the commit it approved, if that run's latest
    attempt started, and its plans finished, before the approval. None when they cannot be known: that run was run again
    after the approval (a re-run replaces its plans), or was still planning when it came."""
    at = when(review["submitted_at"])
    found = last_run(github, repo, workflow, review["commit_id"], number, artifacts, folder)
    if found is None:
        return None
    run, where = found
    if when(run.get("run_started_at")) > at:
        return None
    finished = plans_finished(github, repo, run["id"], run.get("run_attempt") or 1, now=lambda: at)
    return (run, where) if finished <= at else None


def covers(role: str, github=None, say=print, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> bool:
    """Whether the last code owner's approval covers this run's plans: see the module's docstring."""
    github = github or Pipeline()
    repo, number, author = env("GITHUB_REPOSITORY"), int(env("PR_NUMBER")), env("PR_AUTHOR")
    ours = plans(Path.cwd() / ".mlz" / "planned")
    if not changes_anything(ours):
        say("These plans change nothing, so there is nothing for an approval to cover.")
        return True
    owner = code_owner(role, github, repo, env("BASE_REF"))
    approvals = sorted((review for review in github.reviews(repo, number)
                        if review.get("state") == "APPROVED" and review.get("submitted_at") and (review.get("user") or {}).get("login") != author
                        and owner((review.get("user") or {}).get("login", ""))), key=lambda review: when(review["submitted_at"]))
    if not approvals:
        say("No code owner has approved this pull request yet. An approval from now on comes after these plans, and covers them.")
        return True
    last = approvals[-1]
    who = "@" + last["user"]["login"]
    finished = plans_finished(github, repo, int(env("GITHUB_RUN_ID")), int(env("GITHUB_RUN_ATTEMPT")), now)
    if when(last["submitted_at"]) >= finished:
        say(f"{who} approved after these plans were on the pull request ({last['submitted_at']}), so the approval covers them.")
        return True
    seen = seen_by(github, repo, workflow_file(env("WORKFLOW_REF")), last, number, pattern(role), Path.cwd() / ".mlz" / "seen")
    again = ("Ask a code owner to approve the pull request again, now that this plan is on it, then re-run this job (Re-run failed jobs): "
             "until then it cannot merge.")
    if seen is None:
        say(f"::error::{who} approved before these plans were on the pull request ({last['submitted_at']}), and the plans that approval saw "
            f"cannot be read: that run was still planning, or was run again since. {again}")
        return False
    run, where = seen
    changed = differences(sorted(set(ours) | set(plans(where))), ours, plans(where), "this run", f"approved ({link(run)})")
    if not changed:
        say(f"These plans are the ones {who} approved ({link(run)}), so the approval covers them; no new approval is needed.")
        return True
    for stack, difference in changed.items():
        say(f"::error::{stack}: its plan changed since {who} approved it:\n{difference}")
    say(f"::error::The plan of {', '.join(changed)} changed since {who} approved the pull request ({last['submitted_at']}), so nobody has approved "
        f"what it would apply. {again}")
    return False


def app_values(github, repo: str, sha: str, say) -> Verdict:
    """A workload repo's commit that no pull request made: the MLZ App's values from vending, or nothing applies."""
    short = sha[:7]
    if not any(rule.get("type") == "pull_request" for rule in github.branch_rules(repo, "main")):
        say(f"::error::No pull request made {short}, and no rule on main asks for one, so anyone who may push could have made it; nothing is "
            "applied. Vending's ruleset main (docs/vending.md, Its rule on main) lets nothing but the MLZ App push there.")
        return Verdict(False)
    commit = github.commit(repo, sha)
    files = commit.get("files") or []
    names = sorted({name for item in files for name in (item.get("filename"), item.get("previous_filename")) if name})
    # The starter's files only in the commit that first adds the values, as vending writes them (tools/mlz/workload.py).
    first = any(item.get("filename") == VALUES and item.get("status") == "added" for item in files)
    allowed = {VALUES, CODEOWNERS} | ({str(path.relative_to(STARTER)) for path in STARTER.rglob("*") if path.is_file()} if first else set())
    others = [name for name in names if name not in allowed]
    if len(commit.get("parents") or []) != 1 or others or not names:
        say(f"::error::No pull request made {short}, and it is not vending's commit of the landing zone's values: "
            + (f"it changes {', '.join(others)}" if others else "it merges branches" if names else "it changes no file")
            + f". Nothing is applied. {REPLAN_WORKLOAD}")
        return Verdict(False)
    say(f"::notice::No pull request made {short}: it is the MLZ App's commit of what vending writes ({', '.join(names)}), which the rule on "
        "main lets through alone. It applies as vending wrote it, with no person's approval (docs/pipelines.md, Approval on GitHub Team).")
    return Verdict(True)


def bypass_of(github, repo: str, sha: str, say) -> str | None:
    """Who merged this commit with a ruleset's bypass, from GitHub's record (Insights, Rules), or None. A failed read is a
    notice: it never holds up an apply the gate let through."""
    try:
        return next((suite.get("actor_name") or "someone" for suite in github.bypasses(repo, "main") if suite.get("after_sha") == sha), None)
    except Exception as error:  # noqa: BLE001
        say(f"::notice::Could not read whether this merge used a ruleset's bypass ({error}); GitHub records each bypass under Insights, Rules.")
        return None


def gate(role: str, stacks: list[str], github=None, sleep: Callable[[float], None] = time.sleep, say=print) -> Verdict:
    """Whether the stacks may apply: see the module's docstring. Says why on the run's log when they may not."""
    if not stacks:
        return Verdict(True)
    github = github or Pipeline()
    repo, sha, short = env("GITHUB_REPOSITORY"), env("GITHUB_SHA"), env("GITHUB_SHA")[:7]
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT") or "1")
    if attempt > 1:
        head = github.head(repo, "main")
        if head != sha:
            say(f"::error::This is attempt {attempt} of the run for {short}, and main has moved on to {head[:7]}: its plan, made again now, would "
                f"undo what later runs applied. Nothing is applied. {replan(role)}")
            return Verdict(False)
    pull = find_pull(github, repo, sha, sleep)
    if pull is None:
        if role == "workload":
            return app_values(github, repo, sha, say)
        say(f"::error::No pull request made {short}, so no code owner approved a plan of it; nothing is applied. {replan(role)}")
        return Verdict(False)
    number = pull["number"]
    found = last_run(github, repo, workflow_file(env("WORKFLOW_REF")), pull["head"]["sha"], number, pattern(role), Path.cwd() / ".mlz" / "approved")
    if found is None:
        say(f"::error::#{number} has no run on its last commit with plans marked as its own, so no plan of it was shown on it (or its plans have "
            f"expired after 30 days); nothing is applied. {replan(role)}")
        return Verdict(False)
    run, where = found
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        say(f"::error::#{number}'s last run ({link(run)}) did not finish with success ({run.get('status')}, {run.get('conclusion')}), so its plan "
            f"is not the one approved; nothing is applied. {replan(role)}")
        return Verdict(False)
    if when(run.get("run_started_at")) > when(pull["merged_at"]):
        say(f"::error::#{number}'s last run ({link(run)}) was run again after the merge ({run.get('run_started_at')}): its plans are of a later "
            f"Azure, which nobody approved; nothing is applied. {replan(role)}")
        return Verdict(False)
    # A re-run started while they were read may have replaced them: each plan job's artifact is overwritten.
    now = github.workflow_run(repo, run["id"])
    if now.get("run_attempt") != run.get("run_attempt") or now.get("status") != "completed":
        say(f"::error::#{number}'s last run ({link(run)}) was run again while its plans were read, so they may not be the ones approved; nothing is "
            f"applied. {replan(role)}")
        return Verdict(False)
    reviewed = plans(Path.cwd() / ".mlz" / "reviewed")
    refused = differences(stacks, {stack: reviewed.get(stack, []) for stack in stacks}, plans(where), "main", f"#{number}")
    if refused:
        for stack, difference in refused.items():
            say(f"::error::{stack}: its plan on main is not the one #{number}'s run showed:\n{difference}")
        say(f"::error::Nothing is applied: main's plan must be the one #{number}'s run showed ({link(run)}). Azure or the state changed since, or "
            f"an apply failed half way. {replan(role)}")
        return Verdict(False)
    say(f"The plan of {', '.join(stacks)} is the one #{number}'s run showed: {link(run)}")
    return Verdict(True, pull=pull, run=run, bypass=None if role == "workload" else bypass_of(github, repo, sha, say))


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["covers"] and len(argv) == 2:
            return 0 if covers(argv[1]) else 1
        if argv[:1] == ["gate"] and len(argv) >= 3:
            return 0 if gate(argv[1], argv[2:]).ok else 1
    except ConfigError as error:
        print(f"::error::{error}; nothing is applied, and an approval cannot be checked.", file=sys.stderr)
        return 1
    print("usage: approved.py covers <role> | gate <role> <stack> ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    sys.exit(main(sys.argv[1:]))
