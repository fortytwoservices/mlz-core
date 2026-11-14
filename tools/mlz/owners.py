"""Code owners: who must approve a change to a customer's repo (docs/pipelines.md, Code owners).

Each of the three repos has .github/CODEOWNERS. mlz install repos writes it with the platform team,
bootstrap.yaml's github.platform_team, owning everything; a narrower line hands one path to another team of the
organization, and _generated/ has no owner, so the contracts other repos offer still merge themselves. The main
ruleset requires a code owner's review.

GitHub skips a line it cannot use (a team that does not exist, is secret or has no write access to the repo), and
the paths that line matches are then left with no owner at all, not the platform team's: tested in a public
fortytwo-test repo on 2026-11-14. So every run's check fails on any error GitHub reports in the file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .merge import ConfigError

PATH = ".github/CODEOWNERS"
# Where GitHub looks for the file, in this order.
LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
# GitHub reads a newly pushed CODEOWNERS some seconds after the push, and answers 404 until then.
TRIES, WAIT = 6, 10.0


def render(org: str, team: str) -> str:
    """The file mlz install repos writes: the platform team owns everything, _generated/ nothing."""
    return (
        "# Who must approve a change to this repo (docs/pipelines.md in mlz-core, Code owners). The platform team owns\n"
        "# everything. A line below hands one path to another team of this organization, which needs write access to\n"
        "# the repo first; the last line that matches a file wins.\n"
        f"*             @{org}/{team}\n"
        "\n"
        "# No owner: the contracts other repos offer merge themselves once checked.\n"
        "/_generated/\n"
    )


def problems(errors: list[dict], repo: str) -> list[str]:
    """GitHub's errors in a CODEOWNERS file, each as something a person can act on."""
    found = []
    for error in errors:
        message = str(error.get("message") or error.get("kind") or "").splitlines()[0]
        text = f"{error.get('path', PATH)} line {error.get('line')}: {message}"
        if error.get("kind") == "Unknown owner":
            text += (f". A team needs write access to {repo} before CODEOWNERS can name it: an admin gives it with "
                     f"gh api -X PUT orgs/{repo.split('/')[0]}/teams/<team>/repos/{repo} -f permission=push")
        found.append(text)
    return found


def check(github, repo: str, ref: str, folder: Path, out: Callable[[str], None] = print,
          wait: Callable[[float], None] = time.sleep) -> None:
    """Every run's check: the repo's CODEOWNERS at ref, as GitHub reads it. A repo without one is only noted."""
    if not any((folder / location).is_file() for location in LOCATIONS):
        out(f"::notice::No {PATH}: anyone with write access may approve a change to this repo. mlz install repos offers one (docs/pipelines.md).")
        return
    for attempt in range(TRIES):
        errors = github.codeowners_errors(repo, ref)
        if errors is not None:
            break
        if attempt < TRIES - 1:
            wait(WAIT)
    else:
        raise ConfigError(f"GitHub has not read {PATH} at {ref} yet; run the check again in a minute.")
    found = problems(errors, repo)
    if found:
        raise ConfigError("GitHub cannot use every line of CODEOWNERS, and a path whose line it skips has no owner at all:\n  "
                          + "\n  ".join(found))
    out(f"{PATH}: GitHub reads every line")
