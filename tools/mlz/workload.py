"""The landing zones' workload repos' files, after vending's apply has made the repos (docs/vending.md).

A repo vending makes gets mlz-core's workload starter once (starter/mlz-workload: its pipeline, its Terraform's
settings, a README), and the landing zone's values in mlz.auto.tfvars.json, which this keeps up to date. Each change
is one commit, by the MLZ App, on main. Nothing a team wrote is ever replaced or deleted: the starter adds only files
that are not there, and only to a repo without mlz.auto.tfvars.json yet; after that only mlz.auto.tfvars.json is
written, when the landing zone's values change. Not Terraform's: a file in Terraform's state is deleted with it, and a
decommissioned landing zone's repo is archived with the team's files in it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .upgrade import RELEASE_PLEASE_MARKER

CORE = Path(__file__).resolve().parents[2]
STARTER = CORE / "starter" / "mlz-workload"
VALUES = "mlz.auto.tfvars.json"
BRANCH = "main"


def starter() -> dict[str, str]:
    """The starter's files, as a repo gets them: the release markers that move its pins out."""
    return {str(p.relative_to(STARTER)): RELEASE_PLEASE_MARKER.sub("", p.read_text()) for p in sorted(STARTER.rglob("*")) if p.is_file()}


def first_readme(text: str, name: str) -> bool:
    """GitHub's own README in a new repo: its name as a heading, and its description, if any. Nothing a team wrote."""
    lines = text.strip("\n").split("\n")
    return lines[0] == f"# {name}" and len(lines) <= 2


def values_file(workload: dict) -> str:
    return json.dumps({"mlz": workload}, indent=2, sort_keys=True) + "\n"


def sync(github, configs: Path, out: Callable[[str], None] = print) -> None:
    """For each landing zone whose repo vending makes (configs: the folder of <key>.json that python3 -m mlz vending
    writes), the starter once and the values whenever they change."""
    for path in sorted(configs.glob("*.json")):
        config = json.loads(path.read_text())
        github_config, workload = config.get("github") or {}, config.get("workload")
        if not github_config.get("create") or not workload or config.get("decommission"):
            continue
        repo = f"{github_config['organization']}/{github_config['repo']}"
        found = github.repo(repo)
        if found is None:
            out(f"{repo}: not made yet; vending's apply makes it, and the next run gives it its files")
            continue
        if found.get("archived"):
            continue
        want = values_file(workload)
        values, _ = github.file(repo, VALUES, BRANCH)
        if values is None:
            # The first time: the starter, without any file the team has written already (GitHub's own first README
            # is not the team's), and the values.
            files = {}
            for name, text in starter().items():
                there, _ = github.file(repo, name, BRANCH)
                if there is None or (name == "README.md" and first_readme(there, github_config["repo"])):
                    files[name] = text
            files[VALUES] = want
            message = "chore: mlz-core's workload starter, and the landing zone's values"
        elif values != want:
            files, message = {VALUES: want}, "chore: the landing zone's values, from vending"
        else:
            continue
        github.commit_files(repo, BRANCH, github.head(repo, BRANCH), files, message)
        out(f"{repo}: {', '.join(sorted(files))}")
