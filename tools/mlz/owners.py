"""Code owners: who must approve a change to a customer's repo (docs/pipelines.md, Code owners).

Each of the three repos has .github/CODEOWNERS. mlz install repos writes it with the platform team,
bootstrap.yaml's github.platform_team, owning everything; a narrower line hands one path to another team of the
organization, and _generated/ has no owner, so the contracts other repos offer still merge themselves. The main
ruleset requires a code owner's review.

GitHub skips a line it cannot use (a team that does not exist, is secret or has no write access to the repo), and
the paths that line matches are then left with no owner at all, not the platform team's: tested in a public
fortytwo-test repo on 2026-11-14. So every run's check fails on any error GitHub reports in the file, but one: a
visible team of the organization that cannot write to the repo yet. On a pull request it is only noted; the run on
main after the merge, which the current code owner approved, gives it write access, and GitHub reads the line at
once (tested in fortytwo-test on 2026-11-15). A person, a secret team or one that does not exist still stops the run.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from .merge import ConfigError

PATH = ".github/CODEOWNERS"
# Where GitHub looks for the file, in this order.
LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
# GitHub reads a newly pushed CODEOWNERS some seconds after the push, and answers 404 until then.
TRIES, WAIT = 6, 10.0
TEAM = re.compile(r"@([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")


def render(org: str, team: str) -> str:
    """The file mlz install repos writes: the platform team owns everything, _generated/ nothing."""
    return (
        "# Who must approve a change to this repo (docs/pipelines.md in mlz-core, Code owners). The platform team owns\n"
        "# everything. A line below hands one path to another team of this organization, which the run on main after\n"
        "# the merge gives write access to the repo; the last line that matches a file wins.\n"
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
            text += (f". The run on main gives write access to a visible team of {repo.split('/')[0]} that CODEOWNERS names there; "
                     f"it cannot fix a team that does not exist or is secret, or a person without write access to {repo}")
        found.append(text)
    return found


def owner(error: dict) -> str:
    """The owner an error is about: GitHub's column (from 1) points at it in its line, one error per owner (tested
    2026-11-15)."""
    source, column = str(error.get("source") or ""), error.get("column")
    rest = source[column - 1:].split() if isinstance(column, int) and column > 0 else []
    return rest[0] if rest else ""


def check(github, repo: str, ref: str, folder: Path, out: Callable[[str], None] = print,
          wait: Callable[[float], None] = time.sleep, new_teams: str | None = None) -> None:
    """Every run's check: the repo's CODEOWNERS at ref, as GitHub reads it. A repo without one is only noted.
    new_teams says what becomes of a visible team of the organization that cannot write to the repo yet: "note" (a
    pull request: it is noted, and gets access once merged), "grant" (main: it gets write access now) or None (an
    error, like any other)."""
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
    if new_teams:
        errors = without_new_teams(github, repo, errors, new_teams, out)
    found = problems(errors, repo)
    if found:
        raise ConfigError("GitHub cannot use every line of CODEOWNERS, and a path whose line it skips has no owner at all:\n  "
                          + "\n  ".join(found))
    out(f"{PATH}: GitHub reads every line")


def without_new_teams(github, repo: str, errors: list[dict], new_teams: str, out: Callable[[str], None]) -> list[dict]:
    """The errors left once the visible teams of the organization that cannot write to the repo yet are noted or given
    write access. GitHub reads the line again as soon as the team may write (tested 2026-11-15)."""
    org, left, teams = repo.split("/")[0], [], {}
    for error in errors:
        match = TEAM.fullmatch(owner(error)) if error.get("kind") == "Unknown owner" else None
        if match and match[1].lower() == org.lower():
            slug = match[2].lower()
            if slug not in teams:
                found = github.team(org, slug)
                teams[slug] = bool(found) and found.get("privacy") != "secret"
            if teams[slug]:
                continue
        left.append(error)
    for slug in sorted(slug for slug, visible in teams.items() if visible):
        if new_teams == "grant":
            github.grant_team(org, slug, repo)
            out(f"{org}/{slug} given write access to {repo}: CODEOWNERS names it, and a code owner needs it")
        else:
            out(f"::notice::CODEOWNERS names {org}/{slug}, which cannot write to {repo} yet; the run on main after the merge gives it write access.")
    return left
