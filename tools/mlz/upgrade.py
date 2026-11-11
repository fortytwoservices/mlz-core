"""Moves a customer repo to another mlz-core release, as a pull request a person reviews.

    python3 -m mlz upgrade <owner>/<repo> [<owner>/<repo> ...] [--to vX.Y.Z] [--dry-run]

An engineer runs it when Fortytwo decides a customer moves; nothing runs it on a schedule. Each repo gets one
pull request that replaces .github/workflows/mlz.yml with the target release's starter for the repo's role,
so a release that changes the caller's side (a new secret, a new input) ships with it. The body carries the
release notes in between, breaking changes first. Merging changes nothing in Azure: the plan shows what the
release would change, and the apply waits for the repo's reviewer.

Runs on the engineer's own GitHub login (the gh CLI), which needs the workflow scope, since the pull request
changes a workflow file: gh auth refresh -h github.com -s workflow.
"""

from __future__ import annotations

import base64
import difflib
import json
import re
import subprocess
from typing import Callable

from .merge import ConfigError

WORKFLOW = ".github/workflows/mlz.yml"
PIN = re.compile(r"uses:\s*(?P<core>[\w.-]+/[\w.-]+)/\.github/workflows/role\.yml@(?P<ref>[^\s#]+)")
ROLE = re.compile(r"^\s+role:\s*(?P<role>governance|platform|vending)\s*(#.*)?$", re.M)
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# release-please's marker for mlz-core's own release pull requests; it means nothing in a customer's repo.
RELEASE_PLEASE_MARKER = re.compile(r"[ \t]*# x-release-please-version[ \t]*$", re.M)


def version(ref: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) for a release tag such as v0.1.7; None for a branch or a commit."""
    match = RELEASE.match(ref)
    return tuple(int(n) for n in match.groups()) if match else None


def pin_of(workflow: str) -> tuple[str, str]:
    """The mlz-core repo and the ref a caller's workflow pins."""
    match = PIN.search(workflow)
    if not match:
        raise ConfigError(f"{WORKFLOW} does not call mlz-core's role.yml.")
    return match["core"], match["ref"]


def role_of(workflow: str) -> str:
    match = ROLE.search(workflow)
    if not match:
        raise ConfigError(f"{WORKFLOW} names no role (governance, platform or vending).")
    return match["role"]


def releases_between(releases: dict[str, str], current: str, target: str) -> list[str]:
    """The releases an upgrade takes in, oldest first, or a rollback takes out, newest first. From a branch or
    a commit, which has no place among the releases, only the target's own notes apply."""
    to = version(target)
    start = version(current)
    if start is None:
        return [target]
    if to > start:
        return sorted((tag for tag in releases if start < version(tag) <= to), key=version)
    return sorted((tag for tag in releases if to < version(tag) <= start), key=version, reverse=True)


def breaking_changes(notes: str) -> list[str]:
    """The entries under release-please's "BREAKING CHANGES" heading, each whole: a bullet and every line up to
    the next bullet or heading, so a migration step written under the bullet stays with it."""
    entries: list[list[str]] = []
    inside = False
    for line in notes.splitlines():
        if line.startswith("#"):
            inside = "BREAKING CHANGES" in line
        elif inside and re.match(r"[*-] ", line):
            entries.append([line.strip()])
        elif inside and entries:
            entries[-1].append(line.rstrip())
    return ["\n".join(entry).rstrip() for entry in entries]


def demoted(notes: str) -> str:
    """Release notes one heading level down, to sit under the pull request's own headings."""
    return re.sub(r"^#", "##", notes.strip(), flags=re.M)


def render_body(repo: str, role: str, current: str, target: str, releases: dict[str, str], tags: list[str]) -> str:
    back = version(current) is not None and version(target) < version(current)
    breaking = [entry for tag in tags for entry in breaking_changes(releases[tag])]
    lines = [
        f"Moves this repo's pipeline {'back ' if back else ''}from mlz-core `{current}` to `{target}`.",
        "",
        f"**What changes:** `{WORKFLOW}` is replaced by the `{target}` starter for the {role} role. If this repo had "
        "changed that file, the diff shows it: settings belong in the YAML files, not in the workflow.",
        "",
        "## Before you merge",
        "",
    ]
    if back:
        lines += [
            "- **This is a rollback.** The releases listed below are taken out. If one of them changed Terraform "
            "state (a provider upgrade, for example), going back can fail: check their notes first.",
        ]
    if breaking:
        lines += ["- **Breaking changes in these releases:**"]
        for entry in breaking:
            first, *rest = entry.split("\n")
            # Continuation lines nest under their bullet, which sits under "Breaking changes".
            lines += [f"  {first}"] + [f"    {line.strip()}" if line.strip() else "" for line in rest]
    else:
        lines += ["- No breaking changes are listed in these releases."]
    lines += [
        "- If these releases change a contract between the roles, the governance, platform and vending repos move together.",
        "- The plan on this pull request shows what the release changes in Azure. Nothing is applied until a "
        "reviewer approves the apply after the merge.",
        "",
        "## Release notes" + (" (taken out)" if back else ""),
        "",
    ]
    if version(current) is None:
        lines += [f"This repo pinned `{current}`, which is not a release, so only `{target}`'s notes are shown.", ""]
    for tag in tags:
        lines += [demoted(releases[tag]) or f"## {tag}", ""]
    lines += [f"Opened with `python3 -m mlz upgrade {repo} --to {target}`."]
    return "\n".join(lines).rstrip() + "\n"


class GitHub:
    """The calls the upgrade makes, through the gh CLI and the engineer's own login."""

    def _api(self, path: str, method: str = "GET", body: dict | None = None, missing_ok: bool = False):
        command = ["gh", "api", "-X", method, path] + (["--input", "-"] if body is not None else [])
        run = subprocess.run(command, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
        if run.returncode:
            if missing_ok and "HTTP 404" in run.stderr:
                return None
            hint = " Add the scope with: gh auth refresh -h github.com -s workflow" if "workflow" in run.stderr and "scope" in run.stderr else ""
            raise ConfigError(f"gh api {method} {path}: {run.stderr.strip() or run.stdout.strip()}{hint}")
        return json.loads(run.stdout) if run.stdout.strip() else None

    def default_branch(self, repo: str) -> str:
        return self._api(f"repos/{repo}")["default_branch"]

    def file(self, repo: str, path: str, ref: str) -> tuple[str, str] | tuple[None, None]:
        found = self._api(f"repos/{repo}/contents/{path}?ref={ref}", missing_ok=True)
        if not found:
            return None, None
        return base64.b64decode(found["content"]).decode(), found["sha"]

    def releases(self, core: str) -> dict[str, str]:
        """Every published release of mlz-core: tag to release notes."""
        found, page = {}, 1
        while True:
            batch = self._api(f"repos/{core}/releases?per_page=100&page={page}")
            found.update({r["tag_name"]: r.get("body") or "" for r in batch if not r["draft"] and not r["prerelease"]})
            if len(batch) < 100:
                return {tag: notes for tag, notes in found.items() if version(tag)}
            page += 1

    def open_pull(self, repo: str, branch: str) -> str | None:
        owner = repo.split("/")[0]
        pulls = self._api(f"repos/{repo}/pulls?state=open&head={owner}:{branch}")
        return pulls[0]["html_url"] if pulls else None

    def reset_branch(self, repo: str, branch: str, base: str) -> str:
        """The branch at the base branch's head, created or moved back there (it is the upgrade's own); returns
        that commit."""
        sha = self._api(f"repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
        if self._api(f"repos/{repo}/git/ref/heads/{branch}", missing_ok=True):
            self._api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": sha, "force": True})
        else:
            self._api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": sha})
        return sha

    def commit_file(self, repo: str, branch: str, head: str, path: str, text: str, message: str) -> None:
        """One commit with the file, made only if the branch is still at head. GitHub signs commits made this
        way (createCommitOnBranch), so they show as verified; the REST contents API leaves them unsigned."""
        mutation = "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }"
        change = {"path": path, "contents": base64.b64encode(text.encode()).decode()}
        self._api("graphql", "POST", {"query": mutation, "variables": {"input": {
            "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
            "expectedHeadOid": head,
            "message": {"headline": message},
            "fileChanges": {"additions": [change]},
        }}})

    def create_pull(self, repo: str, branch: str, base: str, title: str, body: str) -> str:
        return self._api(f"repos/{repo}/pulls", "POST", {"title": title, "head": branch, "base": base, "body": body})["html_url"]


def upgrade(github, repo: str, target: str | None = None, dry_run: bool = False, out: Callable[[str], None] = print) -> str | None:
    """Opens the upgrade pull request for one repo and returns its URL; None when nothing is opened."""
    base = github.default_branch(repo)
    current_text, _ = github.file(repo, WORKFLOW, base)
    if current_text is None:
        raise ConfigError(f"{repo} has no {WORKFLOW} on {base}: is it an MLZ repo?")
    core, current = pin_of(current_text)
    role = role_of(current_text)
    releases = github.releases(core)
    if not releases:
        raise ConfigError(f"{core} has no releases.")
    target = target or max(releases, key=version)
    if target not in releases:
        raise ConfigError(f"{target} is not a release of {core}; releases: {', '.join(sorted(releases, key=version))}.")
    if current == target:
        out(f"{repo}: already on {target}")
        return None
    starter, _ = github.file(core, f"starter/mlz-{role}/{WORKFLOW}", target)
    if starter is None:
        raise ConfigError(f"{core} {target} has no starter for the {role} role.")
    new_text = RELEASE_PLEASE_MARKER.sub("", starter)
    title = f"chore: mlz-core {target}"
    body = render_body(repo, role, current, target, releases, releases_between(releases, current, target))
    if dry_run:
        out(f"{repo} ({role}): {current} -> {target}; would open \"{title}\"")
        out("".join(difflib.unified_diff(current_text.splitlines(True), new_text.splitlines(True), f"a/{WORKFLOW}", f"b/{WORKFLOW}")))
        out(body)
        return None
    branch = f"mlz/upgrade-{target}"
    existing = github.open_pull(repo, branch)
    if existing:
        out(f"{repo}: the upgrade to {target} is already open: {existing}")
        return existing
    head = github.reset_branch(repo, branch, base)
    github.commit_file(repo, branch, head, WORKFLOW, new_text, title)
    url = github.create_pull(repo, branch, base, title, body)
    out(f"{repo}: {current} -> {target}: {url}")
    return url
