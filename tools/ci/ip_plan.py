"""ip_plan.py: on a vending pull request, brings the IP plan and the names up to date and commits them to the pull
request's branch, so nobody runs the allocator by hand. It allocates on the checkout, which is the pull request merged
into its base: blocks the base has given out since the branch was made are taken into account, so two pull requests
never get the same block (the check on main still stops one that does). A name is kept once it is on the base, where
it can be deployed from; one this pull request gave on an earlier run is given again from its YAML as it is now, so a
short name changed in review still counts.

Exits 0 when both are already up to date, and 1 when it changed the branch or could not: this run then stops, and the
run of the new commit goes on. Needs GH_TOKEN for an app that may write to this repo and update its pull requests,
GITHUB_REPOSITORY, PR_NUMBER, HEAD_REF and HEAD_SHA (the pull request's branch and the commit this run checked), and
BASE_SHA (its base).
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import ConfigError, Pipeline, env, work  # noqa: E402
from ci.run_mlz import mlz  # noqa: E402

PATHS = ("_generated/ip-plan.yaml", "_generated/names.yaml")
MESSAGE = "chore: IP plan and names for this pull request"


def allocate(base_names: Path) -> None:
    """Writes the plan and the names, or nothing at all and fails when anything is wrong (a conflict, a landing zone that
    does not fit, a name Azure would refuse): that failure is the check's own, with every problem listed."""
    subprocess.run(mlz("allocate", "--repo", ".", "--base-names", str(base_names)), stdout=sys.stderr, check=True)


def changed() -> bool:
    """Whether the allocator changed either file; the difference goes to the log."""
    status = subprocess.run(["git", "status", "--porcelain", "--", *PATHS], capture_output=True, text=True, check=True)
    if not status.stdout.strip():
        return False
    subprocess.run(["git", "diff", "--", *PATHS], stdout=sys.stderr)
    return True


def commit(github: Pipeline, repo: str, branch: str, head: str) -> str:
    """One commit with both files, made only if the branch still points at head; its ID."""
    mutation = "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }"
    answer = github._api("graphql", "POST", {"query": mutation, "variables": {"input": {
        "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
        "expectedHeadOid": head,
        "message": {"headline": MESSAGE},
        "fileChanges": {"additions": [{"path": path, "contents": base64.b64encode(Path(path).read_bytes()).decode()} for path in PATHS]},
    }}})
    return answer["data"]["createCommitOnBranch"]["commit"]["oid"]


def update(github: Pipeline, allocate: Callable[[Path], None] = allocate, changed: Callable[[], bool] = changed,
           commit: Callable = commit, say=print) -> int:
    # The addresses come from the platform's contract, and the names from governance's: without both there is nothing
    # to give out yet, and the check says vending waits for them.
    for name in ("platform", "governance"):
        if not Path(f"_generated/{name}.yaml").is_file():
            say(f"No {name} contract yet (_generated/{name}.yaml): no addresses or names to give out.")
            return 0
    repo, number, head_ref, head, base = env("GITHUB_REPOSITORY"), int(env("PR_NUMBER")), env("HEAD_REF"), env("HEAD_SHA"), env("BASE_SHA")

    # The names on the base, which the allocator keeps. A base without the file has none yet; any other failure stops
    # here, since giving every name again could rename what is deployed.
    try:
        names = github.raw(repo, "_generated/names.yaml", base)
    except ConfigError as error:
        say(f"::error::Could not read the base's names (_generated/names.yaml at {base[:7]}): {error}")
        return 1
    base_names = work() / "base-names.yaml"
    base_names.write_text(names or "")

    allocate(base_names)
    if not changed():
        say("The IP plan and the names are up to date.")
        return 0

    # What this step committed must come out the same on the next run. If it does not, stop rather than commit again
    # and again.
    if github.commit_message(repo, head) == MESSAGE:
        say("::error::The IP plan or the names changed again after they were committed to this pull request. "
            "Run ./mlz allocate --repo . on the branch and commit the result.")
        return 1

    # The plan written here includes what the base gave out since the branch was made. If the base changed the plan or
    # the names in that time, committing them onto the branch as they are would conflict with the base, and a
    # conflicting pull request starts no run. So the branch is first brought up to date with its base (GitHub's update
    # branch, only if it is still at HEAD_SHA); the run of that merge then commits them on top.
    if any(path in PATHS for path in github.changed_files(repo, head, base)):
        try:
            github.update_branch(repo, number, head)
        except ConfigError:
            say("::error::The base changed the IP plan or the names since this branch was made, and the branch could not be "
                "brought up to date with it. Merge the base into the branch; the next run writes them.")
            return 1
        say("::notice::The base changed the IP plan or the names since this branch was made, so the branch is now up to "
            "date with it. The run of that merge commits this pull request's.")
        return 1

    # A push since this run started has a run of its own, which writes them for it.
    try:
        oid = commit(github, repo, head_ref, head)
    except ConfigError:
        now = github.pull_head(repo, number)
        if now != head:
            say(f"::notice::The branch moved on while this run allocated (now {now[:7]}). The run of that commit writes the IP plan and the names.")
        else:
            say(f"::error::Could not commit the IP plan and the names to {head_ref}.")
        return 1
    say(f"::notice::The IP plan or the names were out of date, so they are now committed to this pull request ({oid[:7]}). "
        "The run of that commit plans with them.")
    return 1


def main() -> int:
    try:
        return update(Pipeline())
    except (ConfigError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    sys.exit(main())
