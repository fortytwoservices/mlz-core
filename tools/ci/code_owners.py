"""code_owners.py: the check job's code owner step (mlz owners, tools/mlz/owners.py), after refusing a fork's pull
request: a fork's run gets none of the repo's secrets, so it cannot read teams as the MLZ App, nor see that main waits
for a team's access: it would pass a pull request that must wait. Reads GITHUB_EVENT_NAME, GITHUB_REPOSITORY,
HEAD_REPO (a pull request's head repo), REF, NEW_TEAMS (grant, note or nothing) and BASE (a pull request's base, for
note). Exits as mlz owners does. Was role.yml's inline script.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.run_mlz import mlz  # noqa: E402


def command() -> list[str] | None:
    """mlz owners' command line; None, with the error printed, for a fork's pull request."""
    repo, head_repo = os.environ["GITHUB_REPOSITORY"], os.environ.get("HEAD_REPO", "")
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and head_repo != repo:
        print(f"::error::{head_repo} is a fork, whose run cannot check the code owners as the MLZ App. "
              f"Push the branch to {repo} and open the pull request from there.")
        return None
    args = ["--repo", repo, "--ref", os.environ["REF"]]
    new_teams = os.environ.get("NEW_TEAMS", "")
    if new_teams:
        args += ["--new-teams", new_teams]
    if new_teams == "note":
        args += ["--base", os.environ.get("BASE", "")]
    return mlz("owners", *args)


if __name__ == "__main__":
    found = command()
    if found is None:
        sys.exit(1)
    sys.exit(subprocess.run(found).returncode)
