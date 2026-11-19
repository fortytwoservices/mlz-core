"""Code owners: who must approve a change to a customer's repo (docs/pipelines.md, Code owners).

Each of the three repos has .github/CODEOWNERS, written by mlz install repos: the platform team, bootstrap.yaml's
github.platform_team, owns everything, and _generated/ has no owner, so the contracts other repos offer still merge
themselves. The main ruleset requires a code owner's review. Nothing else may be in the file (decision D8, the user's
call on 19 November): handing a folder to another team is gone, and with it the check every run made against GitHub,
the App tokens it needed, and the write access the run on main gave a team the file named. Every run's check now
reads the file offline (offline_problems): exactly the installer's two rules.

GitHub skips a line it cannot use (a team that does not exist, is secret or has no write access to the repo), and
the paths that line matches are then left with no owner at all: tested in a public fortytwo-test repo on 2026-11-14.
mlz install check reads the file as GitHub reads it, once (problems).
"""

from __future__ import annotations

import re
from pathlib import Path

PATH = ".github/CODEOWNERS"
# Where GitHub looks for the file, in this order.
LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
TEAM = re.compile(r"@([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")


def render(org: str, team: str) -> str:
    """The file mlz install repos writes: the platform team owns everything, _generated/ nothing."""
    return (
        "# Who must approve a change to this repo (docs/pipelines.md in mlz-core, Code owners): the platform team,\n"
        "# for everything. Written by mlz install repos; every run's check fails on any other rule.\n"
        f"*             @{org}/{team}\n"
        "\n"
        "# No owner: the contracts other repos offer merge themselves once checked.\n"
        "/_generated/\n"
    )


def rules(text: str) -> list[list[str]]:
    """A CODEOWNERS file's rules, each split into its pattern and owners; comments and blank lines left out."""
    return [line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def offline_problems(folder: Path, org: str | None = None, team: str | None = None) -> list[str]:
    """What makes the repo's CODEOWNERS other than the installer's: one owner, a team, for everything, and no owner
    for _generated/. With org and team (in the governance repo, from bootstrap.yaml), that team. A repo without the
    file has none: mlz install repos offers it, and mlz install check says so."""
    path = folder / PATH
    if not path.is_file():
        return []
    found = rules(path.read_text())
    owner = found[0][1] if found and len(found[0]) == 2 and found[0][0] == "*" else None
    if len(found) != 2 or owner is None or not TEAM.fullmatch(owner) or found[1] != ["/_generated/"]:
        extra = [" ".join(rule) for rule in found if rule[0] not in ("*", "/_generated/")]
        return [f"{PATH} is not what mlz install repos writes: exactly `* @<org>/<platform team>` and `/_generated/`. The platform team "
                "owns everything; handing a path to another team is gone (decision D8)."
                + (f" Remove {', '.join(f'`{rule}`' for rule in extra)}." if extra else "")
                + " Its rules: " + "; ".join(f"`{' '.join(rule)}`" for rule in found)]
    if org and team and owner.lower() != f"@{org}/{team}".lower():
        return [f"{PATH} names {owner} as the owner, but bootstrap.yaml's platform team is @{org}/{team}."]
    return []


def problems(errors: list[dict], repo: str) -> list[str]:
    """GitHub's errors in a CODEOWNERS file, each as something a person can act on."""
    found = []
    for error in errors:
        message = str(error.get("message") or error.get("kind") or "").splitlines()[0]
        text = f"{error.get('path', PATH)} line {error.get('line')}: {message}"
        if error.get("kind") == "Unknown owner":
            text += (f". The platform team must be a visible team of {repo.split('/')[0]} that can write to {repo}; "
                     "mlz install repos gives it write access")
        found.append(text)
    return found
