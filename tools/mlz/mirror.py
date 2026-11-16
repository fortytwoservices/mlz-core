"""The customer's own copy of mlz-core: a private repo in their organization, <org>/mlz-core, holding mlz-core's
releases, which the three repos' pipelines call instead of fortytwoservices/mlz-core.

Why a copy (decided 2026-11-16): self-hosted runners admit only mlz-core's role.yml at the releases the repos
pin, and an organization's runner group cannot admit a workflow from another organization (GitHub's docs,
"Changing which workflows can access a runner group"). Private, not a fork: an organization of Enterprise
Managed Users, or one whose policy forbids public repos, could not have a public fork.

Its default branch is `releases`, at the newest release pushed, with a `vX.Y.Z` tag on each. mlz-core's own CI
and release-please start only on pushes to main and on pull requests, so nothing runs there. Its rulesets keep
the tags from moving or going, and the branch from being rewritten; its Actions access lets the organization's
repos call its workflows. `mlz install repos` makes it and pushes this checkout's release; `mlz upgrade` pushes
each release it moves a repo to. Both push from this checkout, signed in as the gh CLI's login, which needs
the workflow scope: the releases carry workflow files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .merge import ConfigError

UPSTREAM = "fortytwoservices/mlz-core"
NAME = "mlz-core"  # bootstrap.yaml's github.repositories.core, unless set
BRANCH = "releases"
CORE = Path(__file__).resolve().parents[2]  # this mlz-core checkout, which the releases are pushed from
RULESETS = [
    {"name": "releases", "target": "tag", "enforcement": "active",
     "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
     "rules": [{"type": "update"}, {"type": "deletion"}]},
    {"name": "releases-branch", "target": "branch", "enforcement": "active",
     "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
     "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
]


def workflow(core: str, release: str) -> str:
    """role.yml at a release, as the repos' mlz.yml calls it and the runner group admits it (`@vX.Y.Z`, the
    short ref GitHub's docs give for a reusable workflow pinned to a tag)."""
    return f"{core}/.github/workflows/role.yml@{release}"


def ensure(github, core: str, release: str, out: Callable[[str], None] = print, run=subprocess.run) -> None:
    """The copy, made if missing, with its settings, and holding this release."""
    owner, name = core.split("/")
    if github.repo(core) is None:
        # Empty: the releases bring their own history, onto which GitHub's first commit would not fit.
        github.create_repo(owner, name, first_commit=False)
        out(f"{core}: created, private, for mlz-core's releases")
    push_release(github, core, release, out, run)
    if github.actions_access(core) != "organization":
        github.set_actions_access(core, "organization")
        out(f"{core}: the organization's repos may call its workflows")
    for ruleset in RULESETS:
        if github.add_ruleset(core, ruleset):
            out(f"{core}: ruleset {ruleset['name']} (no {'tag' if ruleset['target'] == 'tag' else 'rewrite of the branch'} moved or deleted)")


def push_release(github, core: str, release: str, out: Callable[[str], None] = print, run=subprocess.run) -> None:
    """The release's tag into the copy, and its branch forward to the release when that is newer: from this
    checkout, after fetching the tag from mlz-core itself, since the checkout may be on another commit."""
    if github.tag_exists(core, release):
        out(f"{core}: has {release}")
        return
    git = ["git", "-C", str(CORE), "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    url = f"https://github.com/{core}.git"
    for step, arguments in (("fetch", ["fetch", "--quiet", f"https://github.com/{UPSTREAM}.git", f"refs/tags/{release}:refs/tags/{release}"]),
                            ("push", ["push", "--quiet", url, f"refs/tags/{release}:refs/tags/{release}"])):
        done = run([*git, *arguments], capture_output=True, text=True)
        if done.returncode:
            raise ConfigError(f"git {step} of {release} {'from ' + UPSTREAM if step == 'fetch' else 'into ' + core} failed: {done.stderr.strip()}")
    # Forward only: a copy that holds a newer release already keeps its branch there.
    moved = run([*git, "push", "--quiet", url, f"refs/tags/{release}^{{commit}}:refs/heads/{BRANCH}"], capture_output=True, text=True)
    out(f"{core}: {release} pushed" + ("" if moved.returncode == 0 else f", its branch {BRANCH} left at a newer release"))
