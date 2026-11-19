"""The landing zones' workload repos' files, after vending's apply has made the repos (docs/vending.md).

A repo vending makes gets mlz-core's workload starter once (starter/mlz-workload: its pipeline, its Terraform's
settings, a README), and the landing zone's values in mlz.auto.tfvars.json, which this keeps up to date. Each change
is one commit, by the MLZ App, on main. Nothing a team wrote is ever replaced or deleted: the starter adds only files
that are not there, and only to a repo without mlz.auto.tfvars.json yet; after that only mlz.auto.tfvars.json is
written, when the landing zone's values change. Not Terraform's: a file in Terraform's state is deleted with it, and a
decommissioned landing zone's repo is archived with the team's files in it.

Its main takes the role repos' rule (protect, decision D14): a pull request, a code owner's approval, the check
passed. The code owner is the landing zone's github.team, in .github/CODEOWNERS, which belongs to mlz-core; the bypass
team (bootstrap.yaml's, through governance's contract) may merge without the approval; and nobody but the MLZ App may
bypass the check, so that its commits of the values above still reach main without a person.

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

from . import install, mirror
from .merge import ConfigError
from .upgrade import RELEASE_PLEASE_MARKER, breaking_changes, demoted, releases_between, version

CORE = Path(__file__).resolve().parents[2]
STARTER = CORE / "starter" / "mlz-workload"
VALUES = "mlz.auto.tfvars.json"
BRANCH = "main"
WORKFLOW = ".github/workflows/mlz.yml"
UPGRADE_BRANCH = "mlz/upgrade"  # one per repo: a newer release moves the same pull request on
BUILD_BRANCH = "mlz/upgrade-next"  # where the next upgrade commit is made, so the pull request's branch never stands on main
# The call itself: a line that starts with uses:, so a commented-out one never decides the release.
PIN = re.compile(r"^[ \t]*uses:[ \t]*(?P<core>[\w.-]+/[\w.-]+)/\.github/workflows/workload\.yml@(?P<ref>[^\s#]+)", re.M)
NAMING = re.compile(r"mlz-core\.git//modules/naming\?ref=(?P<ref>[^\"'\s&]+)")
CODEOWNERS = ".github/CODEOWNERS"
CHECK = "workload / plan"  # the caller's job (starter/mlz-workload), then workload.yml's: what main-checks requires
KEY = re.compile(r"^ {4}(\w+)\s*=", re.M)  # a key of variable "mlz"'s object type, as terraform fmt indents them
MLZ_VARIABLE = re.compile(r'^variable\s+"mlz"\s*\{(?P<body>.*?)^\}', re.M | re.S)  # that block alone, to its closing brace


def mlz_keys(variables: str) -> set[str]:
    """The keys variable "mlz" declares in a variables.tf, not another variable's."""
    block = MLZ_VARIABLE.search(variables)
    return set(KEY.findall(block["body"])) if block else set()


def starter() -> dict[str, str]:
    """The starter's files, as a repo gets them: the release markers that move its pins out."""
    return {str(p.relative_to(STARTER)): RELEASE_PLEASE_MARKER.sub("", p.read_text()) for p in sorted(STARTER.rglob("*")) if p.is_file()}


def first_readme(text: str, name: str, description: str) -> bool:
    """GitHub's own README in a repo vending made: its name as a heading and vending's description, exactly."""
    return text.rstrip("\n") == f"# {name}\n{description}"


def values_file(workload: dict) -> str:
    return json.dumps({"mlz": workload}, indent=2, sort_keys=True) + "\n"


def codeowners(organization: str, team: str) -> str:
    """The repo's CODEOWNERS: the landing zone's team owns every file."""
    return (f"# Who must approve a change: the landing zone's team, github.team in vending. mlz-core's vending writes\n"
            f"# this file and keeps it as it is.\n* @{organization}/{team}\n")


def release() -> str:
    """The release vending runs on: the one its starter's pipeline calls (release-please moves that pin each release)."""
    return PIN.search(starter()[WORKFLOW])["ref"]


def sync(github, configs: Path, out: Callable[[str], None] = print, app_slug: str | None = None) -> None:
    """For each landing zone whose repo vending makes (configs: the folder of <key>.json that python3 -m mlz vending
    writes), the starter once and the values and code owners whenever they change, the rule on main, then the pull
    request to vending's release when the repo's pipeline is on an older one. app_slug: the MLZ App's, whose commits
    the rule lets through; without it no rule is made, since it would refuse them."""
    releases: dict[str, str] = {}
    app_id = None
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
            files, message = {}, "chore: the landing zone's code owners, from vending"
        owners = codeowners(github_config["organization"], github_config["team"]) if github_config.get("team") else None
        if owners and github.file(repo, CODEOWNERS, head)[0] != owners:
            files[CODEOWNERS] = owners
        if files:
            github.commit_files(repo, BRANCH, head, files, message)
            out(f"{repo}: {', '.join(sorted(files))}")
        # Its values reach main whatever happens to the rule or the upgrade: a failure there is reported, and the next
        # repo goes on.
        try:
            if app_slug and app_id is None:
                app_id = github.app_id(app_slug)
            protect(github, repo, github_config, app_id, out)
            offer_upgrade(github, repo, releases, out)
        except ConfigError as error:
            out(f"::warning::{repo}: no upgrade pull request: {error}")


def protect(github, repo: str, config: dict, app_id: int | None, out: Callable[[str], None] = print) -> None:
    """The rule on main, as the role repos have it (tools/mlz/install.py): the ruleset main, a pull request with a code
    owner's approval, the bypass team merging without it; main-checks, the pipeline's plan passed on a branch up to
    date with main, which nobody but the MLZ App may bypass. The bypass team gets write access, without which its
    bypass is no use. A ruleset of either name the repo has already is left as it is, and said when it is not
    vending's."""
    if app_id is None:
        out(f"::warning::{repo}: no rule on main: the MLZ App's ID is not known (MLZ_APP_SLUG), and the rule would refuse its commits of the values.")
        return
    organization, bypass = config["organization"], config.get("bypass_team")
    team = github.team(organization, bypass) if bypass else None
    if not bypass:
        out(f"::warning::{repo}: governance's contract names no bypass team yet (bootstrap.yaml's github.bypass_team), so nobody may "
            "merge into main without a code owner's approval.")
    elif team is None:
        out(f"::warning::{repo}: the bypass team {organization}/{bypass} is not there: no bypass on main's approval.")
    elif not github.team_can_write(organization, bypass, repo):
        github.grant_team(organization, bypass, repo)
        out(f"{repo}: {organization}/{bypass} may write, for its bypass of main's approval")
    wanted = [install.main_ruleset("workload", team["id"] if team else None, app_id), install.checks_ruleset("workload", app_id, CHECK)]
    found = {ruleset["name"]: ruleset for ruleset in github.rulesets(repo) if ruleset.get("source_type") == "Repository"}
    for ruleset in wanted:
        if ruleset["name"] not in found:
            github.add_ruleset(repo, ruleset)
            out(f"{repo}: " + ("ruleset main: pull requests only, a code owner's approval" + (f", {organization}/{bypass} may merge without it" if team else "")
                               if ruleset["name"] == "main" else
                               f"ruleset {install.CHECKS_RULESET}: {CHECK} must pass, on a branch up to date with main, before any merge into main; "
                               "nobody but the MLZ App may bypass it, for its commits of the landing zone's values"))
        elif not same_ruleset(found[ruleset["name"]], ruleset):
            out(f"{repo}: has a ruleset named {ruleset['name']} that is not the one vending makes; left as it is (docs/vending.md, Its rule on main)")


def same_ruleset(found: dict, wanted: dict) -> bool:
    """Whether a ruleset GitHub holds is the one wanted: its target, enforcement, bypass actors and conditions, and
    each rule with at least the parameters wanted (GitHub adds others of its own)."""
    def actors(ruleset):
        return sorted((a.get("actor_id"), a.get("actor_type"), a.get("bypass_mode")) for a in ruleset.get("bypass_actors") or [])
    rules = {rule["type"]: rule.get("parameters") or {} for rule in found.get("rules") or []}
    return (all(found.get(key) == wanted.get(key) for key in ("target", "enforcement", "conditions")) and actors(found) == actors(wanted)
            and set(rules) == {rule["type"] for rule in wanted["rules"]}
            and all(all(rules[rule["type"]].get(k) == v for k, v in (rule.get("parameters") or {}).items()) for rule in wanted["rules"]))


def offer_upgrade(github, repo: str, releases: dict[str, str], out: Callable[[str], None] = print) -> str | None:
    """The pull request that moves the repo's pipeline to vending's release, opened or moved on; its URL, or None when
    the repo is on that release or ahead of it, calls something other than mlz-core's workload.yml, or its team
    closed the pull request to this release. releases: mlz-core's release notes, read once per run (filled here)."""
    # Everything from one commit of main: a push after it is never folded in, and the upgrade is made on top of it.
    head = github.head(repo, BRANCH)
    workflow, _ = github.file(repo, WORKFLOW, head)
    pinned = PIN.search(workflow or "")
    if not pinned:
        return None  # no mlz-core pipeline any more: the team's to say
    called, current, target = pinned["core"], pinned["ref"], release()
    # Only ever forward: a repo its team moved ahead, or pinned to a branch, stays as the team has it.
    if version(current) is None or version(current) >= version(target):
        return None
    title = f"chore: mlz-core {target}"
    latest = github.latest_pull(repo, UPGRADE_BRANCH)
    # The release a pull request offers, from the pipeline at its own commit (which GitHub keeps after its branch goes),
    # not from its title, which anyone may edit.
    offered = PIN.search(github.file(repo, WORKFLOW, latest["head"]["sha"])[0] or "") if latest else None
    if offered and offered["ref"] == target:
        if latest["state"] == "open":
            out(f"::notice::{repo} calls mlz-core {current}; the pull request to {target} waits for its team since "
                f"{latest['created_at'][:10]}: {latest['html_url']}")
            return latest["html_url"]
        if not latest.get("merged_at"):
            out(f"{repo}: its team closed the pull request to {target} without merging it; the next release is offered again")
            return None
    if not releases:
        releases.update({tag: notes for tag, notes in github.releases(mirror.UPSTREAM).items() if version(tag)})
    changes, kept = upgrade_files(github, repo, head, called, current)
    body = upgrade_body(current, target, releases, changes, kept, naming_pins(github, repo, head, changes, target))
    # The commit is made on a branch of its own at that commit, then the pull request's branch moves straight onto it:
    # a pull request whose branch stood on main for a moment would be closed by GitHub.
    github.commit_files(repo, BUILD_BRANCH, github.reset_branch(repo, BUILD_BRANCH, BRANCH, at=head), changes, title)
    github.reset_branch(repo, UPGRADE_BRANCH, BRANCH, at=github.head(repo, BUILD_BRANCH))
    github.delete_branch(repo, BUILD_BRANCH)
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
        gained = sorted(mlz_keys(starter()[name]) - mlz_keys(text)) if name == "variables.tf" else []
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
