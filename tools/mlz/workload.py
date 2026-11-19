"""The landing zones' workload repos' files, after vending's apply has made the repos (docs/vending.md).

A repo vending makes gets mlz-core's workload starter once (starter/mlz-workload: its pipeline, its Terraform's
settings, a README), and the landing zone's values in mlz.auto.tfvars.json, which this keeps up to date. Each change
is one commit, by the MLZ App, on main. Nothing a team wrote is ever replaced or deleted: the starter adds only files
that are not there, and only to a repo without mlz.auto.tfvars.json yet; after that only mlz.auto.tfvars.json is
written, when the landing zone's values change. Not Terraform's: a file in Terraform's state is deleted with it, and a
decommissioned landing zone's repo is archived with the team's files in it.

Once vending runs a newer release than a repo's pipeline calls, the repo is offered a pull request that moves it
there (offer_upgrade), which the owning team merges. It owns the pipeline, .github/workflows/mlz.yml; the starter's
other files move only while they are as the starter wrote them, and a file the team changed is named in the pull
request, never changed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from . import mirror
from .merge import ConfigError
from .upgrade import RELEASE_PLEASE_MARKER, breaking_changes, demoted, releases_between, version

CORE = Path(__file__).resolve().parents[2]
STARTER = CORE / "starter" / "mlz-workload"
VALUES = "mlz.auto.tfvars.json"
BRANCH = "main"
WORKFLOW = ".github/workflows/mlz.yml"
UPGRADE_BRANCH = "mlz/upgrade"  # one per repo: a newer release moves the same pull request on
PIN = re.compile(r"uses:\s*(?P<core>[\w.-]+/[\w.-]+)/\.github/workflows/workload\.yml@(?P<ref>[^\s#]+)")
NAMING = re.compile(r"mlz-core\.git//modules/naming\?ref=(?P<ref>[^\"'\s&]+)")
KEY = re.compile(r"^ {4}(\w+)\s*=", re.M)  # a key of variable "mlz"'s object type, as terraform fmt indents them


def starter() -> dict[str, str]:
    """The starter's files, as a repo gets them: the release markers that move its pins out."""
    return {str(p.relative_to(STARTER)): RELEASE_PLEASE_MARKER.sub("", p.read_text()) for p in sorted(STARTER.rglob("*")) if p.is_file()}


def first_readme(text: str, name: str, description: str) -> bool:
    """GitHub's own README in a repo vending made: its name as a heading and vending's description, exactly."""
    return text.rstrip("\n") == f"# {name}\n{description}"


def values_file(workload: dict) -> str:
    return json.dumps({"mlz": workload}, indent=2, sort_keys=True) + "\n"


def release() -> str:
    """The release vending runs on: the one its starter's pipeline calls (release-please moves that pin each release)."""
    return PIN.search(starter()[WORKFLOW])["ref"]


def sync(github, configs: Path, out: Callable[[str], None] = print) -> None:
    """For each landing zone whose repo vending makes (configs: the folder of <key>.json that python3 -m mlz vending
    writes), the starter once and the values whenever they change, then the pull request to vending's release when the
    repo's pipeline is on an older one."""
    releases: dict[str, str] = {}
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
        # Everything read at one commit, and the commit made only if main is still there: a team's push in between
        # makes it fail, never overwrites what they pushed.
        head = github.head(repo, BRANCH)
        values, _ = github.file(repo, VALUES, head)
        if values is None:
            # The first time: the starter, without any file the team has written already (GitHub's own first README
            # is not the team's), and the values.
            files = {}
            for name, text in starter().items():
                there, _ = github.file(repo, name, head)
                if there is None or (name == "README.md" and first_readme(there, github_config["repo"], github_config.get("description", ""))):
                    files[name] = text
            files[VALUES] = want
            message = "chore: mlz-core's workload starter, and the landing zone's values"
        elif values != want:
            files, message = {VALUES: want}, "chore: the landing zone's values, from vending"
        else:
            files = {}
        if files:
            github.commit_files(repo, BRANCH, head, files, message)
            out(f"{repo}: {', '.join(sorted(files))}")
        # Its values reach main whatever happens to the upgrade: a failure there is reported, and the next repo goes on.
        try:
            offer_upgrade(github, repo, releases, out)
        except ConfigError as error:
            out(f"::warning::{repo}: no upgrade pull request: {error}")


def offer_upgrade(github, repo: str, releases: dict[str, str], out: Callable[[str], None] = print) -> str | None:
    """The pull request that moves the repo's pipeline to vending's release, opened or moved on; its URL, or None when
    the repo is on that release or ahead of it, calls something other than mlz-core's workload.yml, or its team
    closed the pull request to this release. releases: mlz-core's release notes, read once per run (filled here)."""
    workflow, _ = github.file(repo, WORKFLOW, BRANCH)
    pinned = PIN.search(workflow or "")
    if not pinned:
        return None  # no mlz-core pipeline any more: the team's to say
    called, current, target = pinned["core"], pinned["ref"], release()
    # Only ever forward: a repo its team moved ahead, or pinned to a branch, stays as the team has it.
    if version(current) is None or version(current) >= version(target):
        return None
    title = f"chore: mlz-core {target}"
    latest = github.latest_pull(repo, UPGRADE_BRANCH)
    if latest and latest["title"] == title:
        if latest["state"] == "open":
            out(f"::notice::{repo} calls mlz-core {current}; the pull request to {target} waits for its team since "
                f"{latest['created_at'][:10]}: {latest['html_url']}")
            return latest["html_url"]
        if not latest.get("merged_at"):
            out(f"{repo}: its team closed the pull request to {target} without merging it; the next release is offered again")
            return None
    if not releases:
        releases.update({tag: notes for tag, notes in github.releases(mirror.UPSTREAM).items() if version(tag)})
    head = github.head(repo, BRANCH)
    changes, kept = upgrade_files(github, repo, head, called, current)
    body = upgrade_body(current, target, releases, changes, kept, naming_pins(github, repo, head, changes, target))
    github.commit_files(repo, UPGRADE_BRANCH, github.reset_branch(repo, UPGRADE_BRANCH, BRANCH), changes, title)
    if latest and latest["state"] == "open":
        # The pull request to an older release, still open: the same one moves on to this release.
        github.update_pull(repo, latest["number"], title, body)
        out(f"{repo}: {current} -> {target}, in {latest['html_url']}")
        return latest["html_url"]
    url = github.create_pull(repo, UPGRADE_BRANCH, BRANCH, title, body)
    out(f"{repo}: {current} -> {target}: {url}")
    return url


def upgrade_files(github, repo: str, head: str, called: str, current: str) -> tuple[dict[str, str], dict[str, str]]:
    """The files the upgrade writes, and the starter's files it leaves because the team changed them (name to the
    repo's text). The pipeline is mlz-core's: always the target's, still calling the mlz-core repo it calls. Another
    starter file moves only while it is exactly the starter of the release the repo is on; one new in the target is
    added; one the team removed stays removed."""
    changes, kept = {}, {}
    for name, text in starter().items():
        there, _ = github.file(repo, name, head)
        if name == WORKFLOW:
            text = text.replace(f"uses: {mirror.UPSTREAM}/.github/workflows/workload.yml@", f"uses: {called}/.github/workflows/workload.yml@")
            if text != there:
                changes[name] = text
            continue
        if there == text:
            continue
        old, _ = github.file(mirror.UPSTREAM, f"starter/mlz-workload/{name}", current)
        old = RELEASE_PLEASE_MARKER.sub("", old) if old is not None else None
        if there is None:
            if old is None:
                changes[name] = text
        elif there == old:
            changes[name] = text
        else:
            kept[name] = there
    return changes, kept


def naming_pins(github, repo: str, head: str, changes: dict[str, str], target: str) -> list[str]:
    """Where the repo's own Terraform, at its top, calls mlz-core's naming module at another release: file:line."""
    found = []
    for name in github.root_files(repo, head):
        if not name.endswith(".tf") or name in changes:
            continue
        text, _ = github.file(repo, name, head)
        for number, line in enumerate((text or "").splitlines(), 1):
            pin = NAMING.search(line)
            if pin and pin["ref"] != target:
                found.append(f"`{name}` line {number} (`{pin['ref']}`{', commented out' if line.lstrip().startswith('#') else ''})")
    return found


def upgrade_body(current: str, target: str, releases: dict[str, str], changes: dict[str, str], kept: dict[str, str],
                 naming: list[str]) -> str:
    tags = releases_between(releases, current, target)
    breaking = [entry for tag in tags for entry in breaking_changes(releases.get(tag, ""))]
    lines = [
        f"Moves this repo's pipeline from mlz-core `{current}` to `{target}`, the release vending runs on.",
        "",
        f"**What changes:** {', '.join(f'`{name}`' for name in sorted(changes))}: the pipeline, which belongs to mlz-core, "
        f"and the starter's files this repo has not changed, as `{target}`'s starter has them.",
    ]
    if kept or naming:
        lines += ["", "**Left as they are**, since they are this repo's own now:"]
    for name, text in sorted(kept.items()):
        gained = sorted(set(KEY.findall(starter()[name])) - set(KEY.findall(text))) if name == "variables.tf" else []
        if gained:
            lines += [f"- `{name}`: `{target}`'s `variable \"mlz\"` also declares {', '.join(f'`{key}`' for key in gained)}. Terraform "
                      f"drops the values a type does not declare, so nothing breaks; take the block from mlz-core's "
                      f"`starter/mlz-workload/variables.tf` at `{target}` when you want them."]
        else:
            lines += [f"- `{name}`: `{target}`'s starter has another version (mlz-core's `starter/mlz-workload/{name}`)."]
    if naming:
        lines += [f"- mlz-core's naming module at another release: {', '.join(naming)}. Moving it to `{target}` may "
                  "rename resources, which the plan of that change shows, so it is left to you."]
    lines += ["", "## Before you merge", ""]
    if breaking:
        lines += ["- **Breaking changes in these releases:**"]
        for entry in breaking:
            first, *rest = entry.split("\n")
            lines += [f"  {first}"] + [f"    {line.strip()}" if line.strip() else "" for line in rest]
    else:
        lines += ["- No breaking changes are listed in these releases."]
    lines += [
        "- The plan on this pull request shows what the release changes in Azure. Nothing is applied until the `apply` "
        "environment's reviewer approves the apply after the merge.",
        "",
        "## Release notes",
        "",
    ]
    for tag in tags:
        lines += [demoted(releases[tag]) or f"## {tag}", ""]
    lines += ["Opened by vending's run on main (`python3 -m mlz workload-repos`). A newer release moves this pull request on."]
    return "\n".join(lines).rstrip() + "\n"
