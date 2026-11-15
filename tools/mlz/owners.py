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
          wait: Callable[[float], None] = time.sleep, new_teams: str | None = None, base: str | None = None) -> None:
    """Every run's check: the repo's CODEOWNERS at ref, as GitHub reads it. A repo without one is only noted.
    new_teams says what becomes of a visible team of the organization that cannot write to the repo yet: "note" (a
    pull request, whose base commit is base: noted, if the pull request is what names it), "grant" (main: given
    write access) or None (an error, like any other). A pull request that deletes the file still waits for main."""
    owned = any((folder / location).is_file() for location in LOCATIONS)
    if not owned:
        out(f"::notice::No {PATH}: anyone with write access may approve a change to this repo. mlz install repos offers one (docs/pipelines.md).")
    errors = read_errors(github, repo, ref, wait) if owned else []
    blocked = []
    if new_teams:
        errors, blocked = without_new_teams(github, repo, ref, errors, new_teams, out, wait, base)
    found = problems(errors, repo) + blocked
    if found:
        raise ConfigError("GitHub cannot use every line of CODEOWNERS, and a path whose line it skips has no owner at all:\n  "
                          + "\n  ".join(found))
    if owned:
        out(f"{PATH}: GitHub reads every line")


def read_errors(github, repo: str, ref: str, wait: Callable[[float], None]) -> list[dict]:
    """GitHub's errors in the file at ref, once it has read it."""
    for attempt in range(TRIES):
        errors = github.codeowners_errors(repo, ref)
        if errors is not None:
            return errors
        if attempt < TRIES - 1:
            wait(WAIT)
    raise ConfigError(f"GitHub has not read {PATH} at {ref} yet; run the check again in a minute.")


def team_of(error: dict, org: str) -> str | None:
    """The team of the organization an "Unknown owner" error is about, if it is about one."""
    match = TEAM.fullmatch(owner(error)) if error.get("kind") == "Unknown owner" else None
    return match[2].lower() if match and match[1].lower() == org.lower() else None


def without_new_teams(github, repo: str, ref: str, errors: list[dict], new_teams: str, out: Callable[[str], None],
                      wait: Callable[[float], None], base: str | None) -> tuple[list[dict], list[str]]:
    """The errors left once the visible teams of the organization that cannot write to the repo yet are noted or given
    write access, and what else stops the run. GitHub reads the line again as soon as the team may write (tested
    2026-11-15)."""
    org, visible = repo.split("/")[0], {}

    def waiting_for_access(found: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
        """The errors about visible teams of the organization, by team, and the rest."""
        teams, rest = {}, []
        for error in found:
            slug = team_of(error, org)
            if slug and slug not in visible:
                team = github.team(org, slug)
                visible[slug] = bool(team) and team.get("privacy") != "secret"
            if slug and visible[slug]:
                teams.setdefault(slug, []).append(error)
            else:
                rest.append(error)
        return teams, rest

    waiting, left = waiting_for_access(errors)
    if new_teams == "grant":
        if waiting and github.head(repo, "main") != ref:
            # A run of an older commit, such as one run again: CODEOWNERS may name other teams now, or an admin may have
            # taken a team's access away. The run of main's latest commit decides.
            for slug in sorted(waiting):
                out(f"::notice::CODEOWNERS at {ref} names {org}/{slug}, which cannot write to {repo}; main has moved on, and its latest run decides.")
            return left, []
        for slug in sorted(waiting):
            # Looked at again: an admin may have given it more than write (maintain, admin) since, which a grant would lower.
            if github.team_can_write(org, slug, repo):
                out(f"{org}/{slug} can write to {repo} already")
            else:
                github.grant_team(org, slug, repo)
                out(f"{org}/{slug} given write access to {repo}: CODEOWNERS names it, and a code owner needs it")
        return left, []
    # A pull request. While main names a team the run on main has not given access yet (it may wait behind an apply's
    # approval, or have failed), that team's paths have no owner on main, and GitHub takes the code owners from main:
    # a pull request could change them, or the file itself, with nobody's approval. So none passes until the access is
    # given, whatever it changes, the line included. GitHub answers 404 both before it has read a file and at a commit
    # without one, so a base without one is told apart first: the pull request that brings the first CODEOWNERS.
    if not base:
        raise ConfigError("a pull request's check needs its base commit (--base) to tell the teams it names from those main names already")
    has_file = any(github.file(repo, location, base)[0] is not None for location in LOCATIONS)
    on_main, _ = waiting_for_access(read_errors(github, repo, base, wait) if has_file else [])
    for slug in sorted(set(waiting) - set(on_main)):
        out(f"::notice::CODEOWNERS names {org}/{slug}, which cannot write to {repo} yet; the run on main after the merge gives it write access.")
    left += [error for slug in sorted(set(waiting) & set(on_main)) for error in waiting[slug]]
    blocked = [f"main names {org}/{slug}, which cannot write to {repo} yet, so its paths have no owner there: no pull request merges until "
               "the run on main gives it write access (approve or reject the apply it waits behind, or run it again if it failed)."
               for slug in sorted(on_main)]
    return left, blocked
