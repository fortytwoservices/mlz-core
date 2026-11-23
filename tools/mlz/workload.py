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
from .upgrade import RELEASE_PLEASE_MARKER, _with, breaking_changes, demoted, plan_of, releases_between, takes_approval, version

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
# What main-checks requires besides CHECK on GitHub Team: workload.yml's planned job, which passes once the pull
# request's approval covers its plan (docs/pipelines.md).
TEAM_CHECKS = ["workload / planned"]
PLAN_BRANCH = "mlz/github-plan"  # the pull request that moves a repo's pipeline to its GitHub plan's way
# GitHub Team's lines in a workload repo's pipeline, which belongs to mlz-core: the approval it passes, and actions: read
# for workload.yml's gate job, which reads the pull request's run with the job's token (a workload repo holds no App
# key). Enterprise's pipeline has neither, so it is the starter's byte for byte.
TEAM_PERMISSION = "  actions: read # GitHub Team: workload.yml's gate reads the pull request's run (docs/vending.md)\n"
TEAM_APPROVAL = "      approval: pull-request # GitHub Team: the pull request's approval covers the plan applied (docs/vending.md)\n"
KEY = re.compile(r"^ {4}(\w+)\s*=\s*(?P<type>\S*)", re.M)  # a key of variable "mlz"'s object type, as terraform fmt indents them
OBJECT = re.compile(r"^ {2}type\s*=\s*object\(\{(?P<keys>.*?)^ {2}\}\)", re.M | re.S)  # its type, not its validation blocks
MLZ_VARIABLE = re.compile(r'^variable\s+"mlz"\s*\{(?P<body>.*?)^\}', re.M | re.S)  # that block alone, to its closing brace


def caller(workflow: str, plan: str) -> str:
    """A workload repo's pipeline written for the plan: on Team it grants actions: read and passes approval:
    pull-request; on Enterprise neither. The same text from either plan. The pipeline belongs to mlz-core, so any
    approval input in the calling job's with: (quoted or not, any value) and any actions permission are taken out
    first, and a with: left empty goes with them."""
    eol = "\r\n" if "\r\n" in workflow else "\n"
    lines = workflow.splitlines(keepends=True)
    permissions = next((n for n, line in enumerate(lines) if re.match(r"^permissions:[ \t]*(#.*)?\r?$", line.rstrip("\n"))), None)
    if permissions is not None:  # its keys: the indented lines below it, blank and comment lines between them included
        end = permissions + 1
        while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip() or lines[end].lstrip().startswith("#")):
            end += 1
        lines[permissions + 1:end] = [line for line in lines[permissions + 1:end] if not re.match(r"^[ \t]+(['\"]?)actions\1[ \t]*:", line)]
    found = _with([line.rstrip("\r\n") for line in lines])
    if found and found[2]:
        raise ConfigError(f"{WORKFLOW} has its with: inline in the job that calls workload.yml; write it as a block, as the starter does, "
                          "so its approval can be set.")
    if found:
        start, end, _ = found
        block = [line for line in lines[start + 1:end] if not re.match(r"^[ \t]+(['\"]?)approval\1[ \t]*:", line)]
        lines[start:end] = ([lines[start], *block] if any(line.strip() and not line.lstrip().startswith("#") for line in block) else block)
    text = "".join(lines)
    if plan == "enterprise":
        return text
    permissions = re.search(r"^permissions:[ \t]*(#[^\n]*)?\r?\n(?:[ \t]+[^\n]*\n)*", text, re.M)
    call = PIN.search(text)
    if not permissions or not call:
        raise ConfigError(f"{WORKFLOW} is not mlz-core's workload pipeline as its starter has it (a permissions: block, the job that calls "
                          "workload.yml), so GitHub Team's lines cannot be written into it.")
    # Each line at the indentation its mapping has in this file: the uses: line's for with:, the inputs' for approval,
    # the permissions' keys' for actions (the starter's two and four spaces otherwise).
    uses_indent = re.match(r"[ \t]*", text[text.rfind("\n", 0, call.start()) + 1:])[0]
    keys = re.findall(r"^([ \t]+)\S", permissions.group(0), re.M)
    step = keys[0] if keys else "  "  # the file's own step, as its permissions' keys are indented
    lines = text.splitlines(keepends=True)
    found = _with([line.rstrip("\r\n") for line in lines])
    if found:  # a with: of the repo's own: the approval goes into it, at its inputs' indentation
        start, end, _ = found
        inputs = [line for line in lines[start + 1:end] if line.strip() and not line.lstrip().startswith("#")]
        indent = re.match(r"[ \t]*", inputs[0])[0] if inputs else re.match(r"[ \t]*", lines[start])[0] + step
        at = sum(len(line) for line in lines[:start + 1])
        text = text[:at] + indent + TEAM_APPROVAL.strip() + eol + text[at:]
    else:
        if "\n" not in text[call.end():]:  # the call is the file's last line, without its line ending
            text += eol
        at = text.index("\n", call.end()) + 1
        text = text[:at] + uses_indent + "with:" + eol + uses_indent + step + TEAM_APPROVAL.strip() + eol + text[at:]
    # Found again in the text as it is now: permissions: may come after jobs:, below the lines just added.
    permissions = re.search(r"^permissions:[ \t]*(#[^\n]*)?\r?\n(?:[ \t]+[^\n]*\n)*", text, re.M)
    return text[:permissions.end()] + step + TEAM_PERMISSION.strip() + eol + text[permissions.end():]


def plan_wanted(config: dict) -> str:
    """The GitHub plan vending writes a repo for: team when governance's contract says so (vending.github_config)."""
    return "team" if config.get("approval") == "pull-request" else "enterprise"


def mlz_attributes(variables: str) -> dict[str, bool]:
    """The keys variable "mlz"'s object type declares in a variables.tf (not another variable's, nor a validation
    block's), each with whether Terraform requires it: one declared optional(...) it does not."""
    block = MLZ_VARIABLE.search(variables)
    found = OBJECT.search(block["body"]) if block else None
    return {key[1]: not key["type"].startswith("optional(") for key in KEY.finditer(found["keys"])} if found else {}


def mlz_keys(variables: str) -> set[str]:
    """The keys variable "mlz" declares in a variables.tf."""
    return set(mlz_attributes(variables))


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
    refused: list[str] = []  # repos whose commit to main GitHub refused: said once every repo has had its turn
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
                    files[name] = caller(text, plan_wanted(github_config)) if name == WORKFLOW else text
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
            try:
                github.commit_files(repo, BRANCH, head, files, message)
                out(f"{repo}: {', '.join(sorted(files))}")
            except ConfigError as error:
                # A ruleset of the repo's own, left as it is, may refuse the MLZ App: the repo's rule and upgrade, and the
                # other repos, still go on, and the run fails at the end.
                out(f"::error::{repo}: GitHub refused vending's commit of {', '.join(sorted(files))} to main: {error}")
                refused.append(repo)
        # Its values reach main whatever happens to the rule or the upgrade: a failure there is reported, and the next
        # repo goes on.
        try:
            if app_slug and app_id is None:
                app_id = github.app_id(app_slug)
            protect(github, repo, github_config, app_id, out)
        except ConfigError as error:
            out(f"::warning::{repo}: its rule on main is not complete: {error}")
        try:
            offer_upgrade(github, repo, releases, out)
        except ConfigError as error:
            out(f"::warning::{repo}: no upgrade pull request: {error}")
        try:
            offer_plan(github, repo, plan_wanted(github_config), out)
        except ConfigError as error:
            out(f"::warning::{repo}: its pipeline is not its GitHub plan's way: {error}")
    if refused:
        raise ConfigError(f"vending's commit to main was refused in {', '.join(refused)}: a ruleset of the repo's own must let the MLZ App "
                          "through, as vending's do (docs/vending.md, Its rule on main).")


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
    # GitHub reads no secret team in CODEOWNERS, and leaves the paths without an owner: main also asks one approval
    # besides the author's, so a change never merges unseen; the code owner's approval is that one when it works.
    owner = github.team(organization, config["team"]) if config.get("team") else None
    if owner is None or owner.get("privacy") == "secret":
        out(f"::warning::{repo}: the landing zone's team {organization}/{config.get('team')} is {'secret' if owner else 'not there'}, so "
            "GitHub does not read it in CODEOWNERS: main asks any other writer's approval until it is a visible team.")
    team = github.team(organization, bypass) if bypass else None
    if not bypass:
        out(f"::warning::{repo}: governance's contract names no bypass team yet (bootstrap.yaml's github.bypass_team), so nobody may "
            "merge into main without a code owner's approval.")
    elif team is None:
        out(f"::warning::{repo}: the bypass team {organization}/{bypass} is not there: no bypass on main's approval.")
    elif not github.team_can_write(organization, bypass, repo):
        github.grant_team(organization, bypass, repo)
        out(f"{repo}: {organization}/{bypass} may write, for its bypass of main's approval")
    # A push after an approval needs a new one: else an approved change could become another, and its author approve
    # its apply alone (the apply environment lets a reviewer approve their own run).
    team_plan = plan_wanted(config) == "team"
    wanted = [install.main_ruleset("workload", team["id"] if team else None, app_id, approvals=1, dismiss_stale=True),
              install.checks_ruleset("workload", app_id, CHECK, TEAM_CHECKS if team_plan else None)]
    if team_plan:
        # On Team the pipeline holds the gate's switch, and a skipped required check counts as passed: so .github/ takes
        # changes only from the MLZ App, which writes the pipeline, and the bypass team, as in the role repos. A team's
        # own workflows there need the bypass team too (docs/vending.md).
        wanted.append(install.push_ruleset(team["id"] if team else None, app_id))
    found = {ruleset["name"]: ruleset for ruleset in github.rulesets(repo) if ruleset.get("source_type") == "Repository"}
    leftover = found.get(install.PUSH_RULESET)
    if not team_plan and leftover is not None and leftover.get("id") is not None \
            and same_ruleset(leftover, install.push_ruleset(team["id"] if team else None, app_id)):
        # Vending's push rule from GitHub Team's way, back on Enterprise: gone, so the team may change .github/ again. One
        # of the repo's own stays.
        github.delete_ruleset(repo, leftover["id"])
        out(f"{repo}: ruleset {install.PUSH_RULESET} removed: on GitHub Enterprise the team may change .github/ again")
    for ruleset in wanted:
        if ruleset["name"] not in found:
            github.add_ruleset(repo, ruleset)
            out(f"{repo}: " + ("ruleset main: pull requests only, a code owner's approval (and one besides the author's)" + (f", {organization}/{bypass} may merge without it" if team else "")
                               if ruleset["name"] == "main" else
                               f"ruleset {install.PUSH_RULESET}: changes under .github/ refused on every branch, but from the MLZ App"
                               + (f" and {organization}/{bypass}" if team else "")
                               if ruleset["name"] == install.PUSH_RULESET else
                               f"ruleset {install.CHECKS_RULESET}: {' and '.join([CHECK, *(TEAM_CHECKS if team_plan else [])])} must pass, on a branch up "
                               "to date with main, before any merge into main; nobody but the MLZ App may bypass it, for its commits of the landing "
                               "zone's values"))
        elif not same_ruleset(found[ruleset["name"]], ruleset):
            # main-checks as vending made it for the other plan moves to this plan's (decision 2: workload / planned on Team);
            # any other ruleset of that name is the repo's own, and stays.
            other = install.checks_ruleset("workload", app_id, CHECK, None if team_plan else TEAM_CHECKS)
            if ruleset["name"] == install.CHECKS_RULESET and same_ruleset(found[ruleset["name"]], other) and found[ruleset["name"]].get("id") is not None:
                github.update_ruleset(repo, found[ruleset["name"]]["id"], ruleset)
                out(f"{repo}: ruleset {install.CHECKS_RULESET} now requires {' and '.join([CHECK, *(TEAM_CHECKS if team_plan else [])])}, "
                    f"GitHub {plan_wanted(config).capitalize()}'s checks")
            else:
                out(f"{repo}: has a ruleset named {ruleset['name']} that is not the one vending makes; left as it is (docs/vending.md, Its rule on main)")


def same_ruleset(found: dict, wanted: dict) -> bool:
    """Whether a ruleset GitHub holds is the one wanted: its target, enforcement, bypass actors and conditions, and
    each rule with at least the parameters wanted (GitHub adds others of its own)."""
    def actors(ruleset):  # a set: a deploy key's actor_id is null, which does not sort with the others' numbers
        return {(a.get("actor_id"), a.get("actor_type"), a.get("bypass_mode")) for a in ruleset.get("bypass_actors") or []}
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
    # a pull request whose branch stood on main for a moment would be closed by GitHub. An open one gets this release's
    # title and notes first: should that fail, its branch has not moved, and the next run tries both again.
    github.commit_files(repo, BUILD_BRANCH, github.reset_branch(repo, BUILD_BRANCH, BRANCH, at=head), changes, title)
    moving = latest and latest["state"] == "open"
    if moving:
        github.update_pull(repo, latest["number"], title, body)
    github.reset_branch(repo, UPGRADE_BRANCH, BRANCH, at=github.head(repo, BUILD_BRANCH))
    url = latest["html_url"] if moving else github.create_pull(repo, UPGRADE_BRANCH, BRANCH, title, body)
    github.delete_branch(repo, BUILD_BRANCH)
    out(f"{repo}: {current} -> {target}, in {url}" if moving else f"{repo}: {current} -> {target}: {url}")
    return url


def offer_plan(github, repo: str, plan: str, out: Callable[[str], None] = print) -> str | None:
    """A pull request that writes the repo's pipeline its GitHub plan's way, when governance's contract and the pipeline
    on main disagree; its URL, or None when they agree. The owning team merges it. The MLZ App pushes it, which on Team
    the push rule on .github/ lets through."""
    head = github.head(repo, BRANCH)
    workflow, _ = github.file(repo, WORKFLOW, head)
    latest = github.latest_pull(repo, PLAN_BRANCH)
    wanted = caller(workflow, plan) if workflow is not None and PIN.search(workflow) else None
    if latest and latest["state"] == "open":
        if wanted not in (None, workflow) and github.file(repo, WORKFLOW, latest["head"]["sha"])[0] == wanted:
            out(f"::notice::{repo}: its pipeline waits for its team to merge GitHub {plan.capitalize()}'s way: {latest['html_url']}")
            return latest["html_url"]
        # An earlier proposal for another pipeline (the plan changed since, or main did): closed, and offered again below if needed.
        github.close_pulls(repo, PLAN_BRANCH, f"Closed by mlz-core's vending: the organization's GitHub plan is {plan} now, and this pull "
                                              "request no longer holds that plan's pipeline.")
    if wanted in (None, workflow):
        return None
    pinned = PIN.search(workflow)
    if plan == "team" and not takes_approval(github.file(pinned["core"], ".github/workflows/workload.yml", pinned["ref"])[0]):
        # Its pipeline would not start with approval: the upgrade pull request comes first.
        out(f"::notice::{repo}: its pipeline calls workload.yml at {pinned['ref']}, which takes no approval input, so GitHub Team's way waits "
            "until its team merges the upgrade to a release that has it.")
        return None
    title = f"chore: GitHub {plan.capitalize()}'s way" + (": each apply approved on its pull request" if plan == "team" else ": each apply waits for a reviewer")
    github.commit_files(repo, PLAN_BRANCH, github.reset_branch(repo, PLAN_BRANCH, BRANCH, at=head), {WORKFLOW: wanted}, title)
    url = github.create_pull(repo, PLAN_BRANCH, BRANCH, title,
                             f"From mlz-core's vending: the organization's GitHub plan is `{plan}` (governance's contract), and this repo's pipeline "
                             f"is the other plan's. This writes `{WORKFLOW}` {plan}'s way" + (": it passes `approval: pull-request`, so the approval "
                             "of a pull request covers the plan applied, and grants `actions: read` for that check." if plan == "team" else
                             ": the `apply` environment's reviewer approves each apply again.") + " Merge it as docs/bootstrap.md in mlz-core says "
                             "(Switching plans).\n")
    out(f"{repo}: GitHub {plan.capitalize()}'s way is offered in {url}")
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
            text = caller(text, plan_of(there or ""))  # an upgrade keeps the repo's GitHub plan; offer_plan switches it
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
        ours, theirs = (mlz_keys(starter()[name]), mlz_attributes(text)) if name == "variables.tf" else (set(), {})
        gained, lost = sorted(ours - set(theirs)), sorted(key for key, required in theirs.items() if required and key not in ours)
        if lost:
            # Terraform requires each attribute a type declares (checked with Terraform 1.17.0): such a key stops the plan.
            lines += [f"- `{name}`: its `variable \"mlz\"` declares {', '.join(f'`{key}`' for key in lost)}, which the values vending writes "
                      f"at `{target}` do not carry, so this repo's plan fails on it until you take the block from mlz-core's "
                      f"`starter/mlz-workload/variables.tf` at `{target}` (and change what reads it: see the breaking changes below)."]
        elif gained:
            lines += [f"- `{name}`: `{target}`'s `variable \"mlz\"` also declares {', '.join(f'`{key}`' for key in gained)}. Terraform "
                      f"drops the values a type does not declare, so nothing breaks; take the block from mlz-core's "
                      f"`starter/mlz-workload/variables.tf` at `{target}` when you want them."]
        else:
            lines += [f"- `{name}`: `{target}`'s starter has another version (mlz-core's `starter/mlz-workload/{name}`)."]
    if naming:
        lines += [f"- mlz-core's naming module at another release: {', '.join(naming)}. One before `v0.15.0` reads the region's code "
                  "from `region`, which is the region's name in the values vending writes now, so it stops at its check until it moves. "
                  f"Moving it to `{target}` may also rename resources, which the plan of that change shows, so it is left to you."]
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
