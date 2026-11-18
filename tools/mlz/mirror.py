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
# The first release whose role.yml reads a private copy (an MLZ App token for every checkout of its code): an older
# one cannot run from the copy, so a repo on the copy never moves back past it.
FIRST_RELEASE = "v0.9.0"
CORE = Path(__file__).resolve().parents[2]  # this mlz-core checkout, which the releases are pushed from
RULESETS = [
    {"name": "releases", "target": "tag", "enforcement": "active",
     "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
     "rules": [{"type": "update"}, {"type": "deletion"}]},
    {"name": "releases-branch", "target": "branch", "enforcement": "active",
     "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
     "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
    # A branch named like a release would answer @vX.Y.Z as well as the tag, outside the tags' protection, in what
    # the runner group admits: none may be made.
    {"name": "no-release-branches", "target": "branch", "enforcement": "active",
     "conditions": {"ref_name": {"include": ["refs/heads/v*"], "exclude": []}},
     "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}]},
]


def workflow(core: str, release: str) -> str:
    """role.yml at a release, as the repos' mlz.yml calls it and the runner group admits it (`@vX.Y.Z`, the
    short ref GitHub's docs give for a reusable workflow pinned to a tag)."""
    return f"{core}/.github/workflows/role.yml@{release}"


def ensure(github, core: str, release: str, out: Callable[[str], None] = print, run=subprocess.run) -> None:
    """The copy, made if missing, with its settings, and holding this release."""
    # In mlz-core's own organization the default name is mlz-core itself, whose settings and rulesets are not the
    # copy's to set, and which already holds every release.
    if core.lower() == UPSTREAM.lower():
        raise ConfigError(f"{core} is mlz-core itself, not a copy of it: name the organization's copy in bootstrap.yaml's "
                          "github.repositories.core (docs/bootstrap.md).")
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
    """The release's tag into the copy, and its branch at the release or a newer one: from this checkout, after
    fetching the tag from mlz-core itself, since the checkout may be on another commit. Each is checked on every
    run, so a run after a failed one finishes what it left."""
    has_tag = github.tag_exists(core, release)
    if has_tag and github.compare(core, release, BRANCH) in ("identical", "ahead"):
        out(f"{core}: has {release}")
        return
    git = ["git", "-C", str(CORE), "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    url = f"https://github.com/{core}.git"

    def git_run(step: str, *arguments: str, where: str) -> None:
        done = run([*git, *arguments], capture_output=True, text=True)
        if done.returncode:
            raise ConfigError(f"git {step} of {release} {where} failed: {done.stderr.strip()}")

    git_run("fetch", "fetch", "--quiet", f"https://github.com/{UPSTREAM}.git", f"refs/tags/{release}:refs/tags/{release}", where=f"from {UPSTREAM}")
    if not has_tag:
        git_run("push", "push", "--quiet", url, f"refs/tags/{release}:refs/tags/{release}", where=f"into {core}")
    # The branch moves forward only: one at a newer release already stays there.
    state = github.compare(core, release, BRANCH)
    if state == "diverged":
        raise ConfigError(f"{core}'s branch {BRANCH} has commits that are not mlz-core's: it should only ever hold mlz-core's releases. "
                          f"Point it at mlz-core's newest release by hand, then run this again.")
    if state not in ("identical", "ahead"):  # behind, or not there yet
        git_run("push", "push", "--quiet", url, f"refs/tags/{release}^{{commit}}:refs/heads/{BRANCH}", where=f"onto {core}'s branch {BRANCH}")
    out(f"{core}: {release} pushed")
