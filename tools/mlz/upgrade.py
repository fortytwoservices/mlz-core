"""Moves a customer repo to another mlz-core release, as a pull request a person reviews.

    python3 -m mlz upgrade <owner>/<repo> [<owner>/<repo> ...] [--to vX.Y.Z] [--dry-run]

An engineer runs it when Fortytwo decides a customer moves; nothing runs it on a schedule. Each repo gets one
pull request that replaces the files mlz-core owns in it (the workflow, .github/workflows/mlz.yml, and the
editor settings in .vscode/) with the target release's starter for the repo's role, so a release that
changes the caller's side (a new secret, a new input) ships with it, and the editor reads the schemas of
the release the repo runs. The body carries the
release notes in between, breaking changes first. Merging changes nothing in Azure: the plan shows what the
release would change, and the apply waits for the repo's reviewer.

Runs on the engineer's own GitHub login (the gh CLI), which needs the workflow scope, since the pull request
changes a workflow file: gh auth refresh -h github.com -s workflow.
"""

from __future__ import annotations

import difflib
import re
from typing import Callable

from .github import GitHub  # noqa: F401  (re-exported for python3 -m mlz upgrade)
from .merge import ConfigError

WORKFLOW = ".github/workflows/mlz.yml"
# The files in a customer repo that belong to mlz-core, which an upgrade replaces with the target release's
# starter: the workflow, and the editor settings pointing at that release's schemas. A release from before
# the editor settings has only the workflow, so going back to one removes them.
OWNED = (WORKFLOW, ".vscode/settings.json", ".vscode/extensions.json")
PIN = re.compile(r"uses:\s*(?P<core>[\w.-]+/[\w.-]+)/\.github/workflows/role\.yml@(?P<ref>[^\s#]+)")
ROLE = re.compile(r"^\s+role:\s*(?P<role>governance|platform|vending)\s*(#.*)?$", re.M)
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# release-please's marker for mlz-core's own release pull requests; it means nothing in a customer's repo.
# release-please's marker on a line it moves to each release (# in YAML, // in the editor settings), which a
# customer repo's copy goes without.
RELEASE_PLEASE_MARKER = re.compile(r"[ \t]*(#|//) x-release-please-version[ \t]*$", re.M)


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


def render_body(repo: str, role: str, current: str, target: str, releases: dict[str, str], tags: list[str],
                paths: dict[str, str | None] | None = None) -> str:
    back = version(current) is not None and version(target) < version(current)
    breaking = [entry for tag in tags for entry in breaking_changes(releases[tag])]
    paths = paths if paths is not None else {WORKFLOW: ""}
    replaced = ", ".join(f"`{p}`" for p, text in paths.items() if text is not None)
    removed = ", ".join(f"`{p}`" for p, text in paths.items() if text is None)
    lines = [
        f"Moves this repo's pipeline {'back ' if back else ''}from mlz-core `{current}` to `{target}`.",
        "",
        f"**What changes:** the files that belong to mlz-core are replaced by the `{target}` starter for the {role} "
        f"role: {replaced}." + (f" {removed}, which `{target}` does not have, {'is' if ',' not in removed else 'are'} removed." if removed else "")
        + " If this repo had changed them, the diff shows it: settings belong in the YAML files.",
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


def upgrade(github, repo: str, target: str | None = None, dry_run: bool = False, out: Callable[[str], None] = print) -> str | None:
    """Opens the upgrade pull request for one repo and returns its URL; None when nothing is opened."""
    base = github.default_branch(repo)
    current_text, _ = github.file(repo, WORKFLOW, base)
    if current_text is None:
        raise ConfigError(f"{repo} has no {WORKFLOW} on {base}: is it an MLZ repo?")
    core, current = pin_of(current_text)
    role = role_of(current_text)
    releases = {tag: notes for tag, notes in github.releases(core).items() if version(tag)}
    if not releases:
        raise ConfigError(f"{core} has no releases.")
    target = target or max(releases, key=version)
    if target not in releases:
        raise ConfigError(f"{target} is not a release of {core}; releases: {', '.join(sorted(releases, key=version))}.")
    if current == target:
        out(f"{repo}: already on {target}")
        return None
    # Each file mlz-core owns: the target's version, or None where the target has none and the repo does.
    changes, before = {}, {}
    for path in OWNED:
        starter, _ = github.file(core, f"starter/mlz-{role}/{path}", target)
        if starter is None and path == WORKFLOW:
            raise ConfigError(f"{core} {target} has no starter for the {role} role.")
        before[path] = current_text if path == WORKFLOW else github.file(repo, path, base)[0]
        after = None if starter is None else RELEASE_PLEASE_MARKER.sub("", starter)
        if after != before[path]:
            changes[path] = after
    title = f"chore: mlz-core {target}"
    body = render_body(repo, role, current, target, releases, releases_between(releases, current, target), changes)
    if dry_run:
        out(f"{repo} ({role}): {current} -> {target}; would open \"{title}\"")
        for path, after in changes.items():
            out("".join(difflib.unified_diff((before[path] or "").splitlines(True), (after or "").splitlines(True), f"a/{path}", f"b/{path}")))
        out(body)
        return None
    branch = f"mlz/upgrade-{target}"
    existing = github.open_pull(repo, branch)
    if existing:
        out(f"{repo}: the upgrade to {target} is already open: {existing}")
        return existing
    head = github.reset_branch(repo, branch, base)
    github.commit_files(repo, branch, head, changes, title)
    url = github.create_pull(repo, branch, base, title, body)
    out(f"{repo}: {current} -> {target}: {url}")
    return url
