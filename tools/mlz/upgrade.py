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
changes a workflow file: gh auth refresh -h github.com -s workflow. With self-hosted runners, the organization's
runner group mlz admits only mlz-core's role.yml at the releases it lists, so the upgrade adds the target first,
which needs an owner's admin:org scope; anyone else gets the command for one.
"""

from __future__ import annotations

import difflib
import re
from typing import Callable

from . import mirror
from .github import GitHub  # noqa: F401  (re-exported for python3 -m mlz upgrade)
from .merge import ConfigError

WORKFLOW = ".github/workflows/mlz.yml"
WRAPPER = "mlz"  # ./mlz, which runs mlz-core's command line at the release the workflow calls
# Where a pull request could change what its own run does (finding 17): every role repo's push ruleset refuses changes
# here but from bootstrap.yaml's github.bypass_team (tools/mlz/install.py, push_ruleset).
PIPELINE_FILES = ".github/**/*"
# The files in a customer repo that belong to mlz-core, which an upgrade replaces with the target release's
# starter: the workflow, the editor settings pointing at that release's schemas, and ./mlz. A release from before
# the editor settings, or before ./mlz, does not have them, so going back to one removes them.
OWNED = (WORKFLOW, ".vscode/settings.json", ".vscode/extensions.json", ".idea/jsonSchemas.xml", WRAPPER)
# The repo's .gitignore is the customer's, but for the lines that let JetBrains' schema mappings through: those are
# added when missing, and nothing else in the file is ever changed or taken away (gitignore_with).
GITIGNORE = ".gitignore"
# Of those, the ones a person runs: executable (GitHub.make_executable), which the commit that writes them cannot do.
EXECUTABLE = (WRAPPER,)
PIN = re.compile(r"uses:\s*(?P<core>[\w.-]+/[\w.-]+)/\.github/workflows/role\.yml@(?P<ref>[^\s#]+)")
ROLE = re.compile(r"^\s+role:\s*(?P<role>governance|platform|vending)\s*(#.*)?$", re.M)
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# release-please's marker for mlz-core's own release pull requests; it means nothing in a customer's repo.
# release-please's marker on a line it moves to each release (# in YAML, // in the editor settings), which a
# customer repo's copy goes without.
RELEASE_PLEASE_MARKER = re.compile(r"[ \t]*(#|//|<!--) x-release-please-version( -->)?[ \t]*$", re.M)


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


# Who approves an apply, as a caller passes it to role.yml and workload.yml: environment (the default, when the caller
# passes nothing: a reviewer in the environment, GitHub Enterprise) or pull-request (GitHub Team: the pull request's
# approval, which then covers the plan applied). The caller is under .github/, which only the bypass team may change.
CALL = re.compile(r"^(?P<indent>[ \t]+)uses:\s*[\w.-]+/[\w.-]+/\.github/workflows/(role|workload)\.yml@")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _with(lines: list[str]) -> tuple[int, int, str] | None:
    """The with: of the job that calls mlz-core's workflow: (its line, the line after its block, its inline value, or
    "" for a block), or None when that job has no with:. Only that mapping is the input, so an approval: anywhere else
    in the file, a step's env or a comment, is never taken for it."""
    call = next((n for n, line in enumerate(lines) if CALL.match(line)), None)
    if call is None:
        return None
    indent = _indent(lines[call])

    def key(n: int) -> bool:  # a line of YAML, not blank and not a comment
        return bool(lines[n].strip()) and not lines[n].lstrip().startswith("#")
    first = call
    while first > 0 and (not key(first - 1) or _indent(lines[first - 1]) >= indent):
        first -= 1
    last = call + 1
    while last < len(lines) and (not key(last) or _indent(lines[last]) >= indent):
        last += 1
    for n in range(first, last):
        match = re.match(rf"^[ \t]{{{indent}}}with:[ \t]*(?P<inline>[^#\n]*?)[ \t]*(#.*)?$", lines[n])
        if match:
            end = n + 1
            while end < last and (not key(end) or _indent(lines[end]) > indent):
                end += 1
            return n, end, match["inline"]
    return None


def approval_of(workflow: str) -> str:
    """The approval a caller's workflow passes to mlz-core's workflow, in the with: of the job that calls it, as a block
    or inline: environment when it passes none."""
    lines = workflow.splitlines()
    found = _with(lines)
    if found is None:
        return "environment"
    start, end, inline = found
    # Its keys without their comments: a comment that reads like an approval is not one. A flow mapping may go on over
    # the lines below its opening brace.
    block = [re.sub(r"(^|\s)#.*$", "", line) for line in lines[start + 1:end]]
    keys = [" ".join([inline, *block]) if inline.startswith("{") and "}" not in inline else inline] if inline else block
    for key in keys:
        match = re.search(r"(^\s*|[{,]\s*)(['\"]?)approval\2[ \t]*:[ \t]*['\"]?(?P<approval>[\w-]+)", key)
        if match:
            return match["approval"]
    return "environment"


# The line a role repo's caller gets on GitHub Team, under its with:, after its role.
TEAM_APPROVAL = "approval: pull-request # GitHub Team: the pull request's approval covers the plan applied (docs/bootstrap.md)"


def plan_of(workflow: str) -> str:
    """The GitHub plan a caller is written for: team when it passes approval: pull-request, else enterprise."""
    return "team" if approval_of(workflow) == "pull-request" else "enterprise"


def caller(workflow: str, plan: str) -> str:
    """A role repo's caller (.github/workflows/mlz.yml) written for the plan: on Team it passes approval:
    pull-request, on Enterprise none, so that an Enterprise caller is the starter's byte for byte. The same text from
    either plan, so the installer, mlz upgrade and a switch of plans all write it alike. Only the with: of the job that
    calls role.yml changes; a with: written inline is refused, since that one line would be rewritten whole."""
    lines = workflow.splitlines(keepends=True)
    found = _with([line.rstrip("\n") for line in lines])
    if (found is None or found[2]) and plan == "enterprise" and approval_of(workflow) == "environment":
        return workflow  # nothing to take out
    if found is None or found[2]:
        raise ConfigError(f"{WORKFLOW} has no with: block in the job that calls mlz-core's role.yml, so its approval cannot be set; "
                          "write it as the starter has it (with: on its own line, role: under it).")
    start, end, _ = found
    block = [line for line in lines[start + 1:end] if not re.match(r"^[ \t]+approval:", line)]
    if plan == "team":
        at = next((n + 1 for n, line in enumerate(block) if re.match(r"^[ \t]+role:", line)), len(block))
        indent = re.match(r"^[ \t]*", block[at - 1] if at else lines[start])[0] if at else re.match(r"^[ \t]*", lines[start])[0] + "  "
        block[at:at] = [f"{indent}{TEAM_APPROVAL}\n"]
    return "".join(lines[:start + 1] + block + lines[end:])


def gitignore_with(current: str | None, starter: str) -> str | None:
    """The repo's .gitignore with the starter's lines it lacks appended, comments included once; None when it has them
    all. Never changes or removes a line the repo has."""
    rules = [line for line in starter.splitlines() if line.strip() and not line.startswith("#")]
    if current is None or not current.strip():
        return starter
    have = {line.strip() for line in current.splitlines()}
    if all(rule in have for rule in rules):
        return None
    comments = [line for line in starter.splitlines() if line.startswith("#")]
    added = [line for line in comments if line.strip() not in have] + [rule for rule in rules if rule not in have]
    return current + ("" if current.endswith("\n") or not current else "\n") + "\n" + "\n".join(added) + "\n"


def matches(pattern: str, path: str) -> bool:
    """Whether a push rule's path pattern takes the whole path, in GitHub's fnmatch (Ruby's File.fnmatch with
    File::FNM_PATHNAME): **/ spans any number of folders, none included; a ** without its slash is only a *, as Ruby's
    docs say, so .github/** takes .github/CODEOWNERS but not .github/workflows/mlz.yml. * and ? stay within one folder,
    [set] is one character of the set, ranges included. GitHub takes no backslash quoting, no complemented set and no braces (its docs, "Unsupported fnmatch
    syntax"), so those stay literal here too."""
    regex = ""
    i = 0
    while i < len(pattern):
        close = pattern.find("]", i + 2) if pattern[i] == "[" else -1
        if pattern.startswith("**/", i):
            regex, i = regex + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            regex, i = regex + "[^/]*", i + 2
        elif close != -1:
            regex, i = regex + "[" + re.sub(r"([\\\]\[^])", r"\\\1", pattern[i + 1:close]) + "]", close + 1
        else:
            regex += {"*": "[^/]*", "?": "[^/]"}.get(pattern[i], re.escape(pattern[i]))
            i += 1
    return re.fullmatch(regex, path) is not None


def restricts(ruleset: dict, path: str) -> bool:
    """Whether the ruleset's file path rules refuse a change to the path: a restricted pattern takes it, and none of
    that rule's allowed exceptions (ignored_file_paths) does."""
    for rule in ruleset.get("rules") or []:
        parameters = rule.get("parameters") or {}
        if rule.get("type") == "file_path_restriction" and any(matches(p, path) for p in parameters.get("restricted_file_paths") or []) \
                and not any(matches(p, path) for p in parameters.get("ignored_file_paths") or []):
            return True
    return False


def push_refused(github, repo: str, paths: list[str]) -> str | None:
    """Why a commit of these paths would be refused for whoever is signed in, before it is tried; None when nothing
    refuses it. An active push ruleset restricting one of them refuses it, unless they may bypass it."""
    for ruleset in github.rulesets(repo):
        if ruleset.get("target") != "push" or ruleset.get("enforcement") != "active" or ruleset.get("current_user_can_bypass") in ("always", "exempt"):
            continue
        refused = [path for path in sorted(paths) if restricts(ruleset, path)]
        if refused:
            return (f"{repo}: its ruleset {ruleset['name']} refuses changes to {', '.join(refused)} from you. Only its bypass team "
                    f"(bootstrap.yaml's github.bypass_team) may change them: ask one of its members to run this, or join it (docs/pipelines.md).")
    return None


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
    replaced = ", ".join(f"`{p}`" for p, text in paths.items() if text is not None and p != GITIGNORE)
    removed = ", ".join(f"`{p}`" for p, text in paths.items() if text is None)
    lines = [
        f"Moves this repo's pipeline {'back ' if back else ''}from mlz-core `{current}` to `{target}`.",
        "",
        f"**What changes:** the files that belong to mlz-core are replaced by the `{target}` starter for the {role} "
        f"role: {replaced}." + (f" {removed}, which `{target}` does not have, {'is' if ',' not in removed else 'are'} removed." if removed else "")
        + " If this repo had changed them, the diff shows it: settings belong in the YAML files."
        + (f" `{GITIGNORE}` gets the starter's lines it lacks, which keep JetBrains' own files in `.idea/` out of the repo; "
           "every line it has stays." if paths.get(GITIGNORE) else ""),
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


RUNNER_GROUP = "mlz"  # install.RUNNER: the self-hosted runners' group, when the customer has them


def admit_release(github, org: str, core: str, target: str, out: Callable[[str], None] = print,
                  told: set[tuple[str, str]] | None = None) -> None:
    """The runner group mlz, when it admits only some workflows (self-hosted runners), admits the target's role.yml
    too, before the pull request whose jobs run it: they would wait for a runner forever. mlz-core's releases it
    admits already stay, for the repos still on them; any other workflow goes, as mlz install runners does. told,
    shared by the repos of one run, holds the (organization, workflow) pairs whose owner's step was printed already:
    the step is printed once, while the group is read and kept up to date for every repo."""
    prefix = mirror.workflow(core, "")
    workflow = prefix + target
    try:
        group = github.runner_group(org, RUNNER_GROUP)
        if group is None or not group.get("restricted_to_workflows"):
            return
        admitted = group.get("selected_workflows") or []
        wanted = sorted({w for w in admitted if w.startswith(prefix) and version(w[len(prefix):])} | {workflow})
        if wanted == sorted(admitted):
            return
        github.update_runner_group(org, group["id"], {"restricted_to_workflows": True, "selected_workflows": wanted})
        out(f"{org}: the runner group {RUNNER_GROUP} admits {workflow}")
    except ConfigError:
        told = told if told is not None else set()
        if (org, workflow) not in told:
            told.add((org, workflow))
            out(f"{org}: if it has self-hosted runners, an owner must let the runner group {RUNNER_GROUP} run {target}'s role.yml, or the "
                f"upgrade's jobs wait for a runner: Settings, Actions, Runner groups, {RUNNER_GROUP}, add {workflow}.")


def upgrade(github, repo: str, target: str | None = None, dry_run: bool = False, out: Callable[[str], None] = print,
            core: str | None = None, told: set[tuple[str, str]] | None = None) -> str | None:
    """Opens the upgrade pull request for one repo and returns its URL; None when nothing is opened. core moves the
    repo onto another copy of mlz-core, such as the organization's (tools/mlz/mirror.py); by default it stays on
    the one it calls. told, shared by the repos of one run, keeps the owner's step for the runner group from being
    printed once per repo (admit_release)."""
    base = github.default_branch(repo)
    current_text, _ = github.file(repo, WORKFLOW, base)
    if current_text is None:
        raise ConfigError(f"{repo} has no {WORKFLOW} on {base}: is it an MLZ repo?")
    called, current = pin_of(current_text)
    core = core or called
    role = role_of(current_text)
    # A repo that calls the organization's copy of mlz-core (tools/mlz/mirror.py) moves to one of mlz-core's own
    # releases, which the copy gets before the pull request; the copy has no releases of its own.
    source = mirror.UPSTREAM if core != mirror.UPSTREAM else core
    releases = {tag: notes for tag, notes in github.releases(source).items() if version(tag)}
    if not releases:
        raise ConfigError(f"{source} has no releases.")
    target = target or max(releases, key=version)
    if target not in releases:
        raise ConfigError(f"{target} is not a release of {source}; releases: {', '.join(sorted(releases, key=version))}.")
    if core != mirror.UPSTREAM and version(target) < version(mirror.FIRST_RELEASE):
        raise ConfigError(f"{target} cannot run from {core}: releases before {mirror.FIRST_RELEASE} read mlz-core's code without a token, "
                          f"so not from a private copy. Choose {mirror.FIRST_RELEASE} or later, or move the repo back onto mlz-core "
                          f"itself with --core {mirror.UPSTREAM}.")
    if current == target and called == core:
        # A repo on its release already: the copy is still made to hold it, which a tag that never arrived would
        # keep the runner group from admitting.
        if core != mirror.UPSTREAM and not dry_run:
            mirror.push_release(github, core, target, out)
        out(f"{repo}: already on {target}")
        return None
    # Each file mlz-core owns: the target's version. Where the target has none, the repo's copy is removed
    # only if the release it leaves put it there; a file no release of mlz-core gave is the customer's own.
    changes, before, executable = {}, {}, []
    for path in OWNED:
        starter, _ = github.file(source, f"starter/mlz-{role}/{path}", target)
        if starter is None and path == WORKFLOW:
            raise ConfigError(f"{source} {target} has no starter for the {role} role.")
        if starter is not None and path in EXECUTABLE:
            executable.append(path)
        before[path] = current_text if path == WORKFLOW else github.file(repo, path, base)[0]
        if starter is None and github.file(source, f"starter/mlz-{role}/{path}", current)[0] is None:
            continue
        after = None if starter is None else RELEASE_PLEASE_MARKER.sub("", starter).replace(
            f"uses: {mirror.UPSTREAM}/.github/workflows/role.yml@", f"uses: {core}/.github/workflows/role.yml@")
        if after is not None and path == WORKFLOW:
            # The repo's GitHub plan stays as its caller has it: an upgrade never switches it (mlz install repos does).
            after = caller(after, plan_of(current_text))
        if after != before[path]:
            changes[path] = after
    # The target's .gitignore lines, appended where missing; going back to a release without them leaves them.
    starter_ignore, _ = github.file(source, f"starter/mlz-{role}/{GITIGNORE}", target)
    if starter_ignore is not None:
        before[GITIGNORE] = github.file(repo, GITIGNORE, base)[0]
        ignore = gitignore_with(before[GITIGNORE], starter_ignore)
        if ignore is not None:
            changes[GITIGNORE] = ignore
    title = f"chore: mlz-core {target}"
    body = render_body(repo, role, current, target, releases, releases_between(releases, current, target), changes)
    if dry_run:
        out(f"{repo} ({role}): {current} -> {target}; would open \"{title}\"")
        for path, after in changes.items():
            out("".join(difflib.unified_diff((before[path] or "").splitlines(True), (after or "").splitlines(True), f"a/{path}", f"b/{path}")))
        out(body)
        return None
    # Before anything is pushed: a push ruleset that refuses the files would refuse the commit after the copy and the
    # branch were made, with GitHub's bare 422.
    refused = push_refused(github, repo, list(changes) + executable)
    if refused:
        raise ConfigError(refused)
    if core != mirror.UPSTREAM:
        mirror.push_release(github, core, target, out)
    admit_release(github, repo.split("/")[0], core, target, out, told)
    branch = f"mlz/upgrade-{target}"
    existing = github.open_pull(repo, branch)
    if existing:
        # The open one, if it already calls this copy of mlz-core at the target; otherwise, as when --core moves a
        # repo whose upgrade is open already, its branch is made again with this one's files.
        pinned = github.file(repo, WORKFLOW, branch)[0]
        if pinned is not None and pin_of(pinned) == (core, target):
            # Its files are in; their mode too, which a run that stopped after the files did not give.
            if executable:
                github.make_executable(repo, branch, executable, f"{title}: {', '.join(executable)} executable")
            out(f"{repo}: the upgrade to {target} is already open: {existing}")
            return existing
        github.commit_files(repo, branch, github.reset_branch(repo, branch, base), changes, title)
        if executable:
            github.make_executable(repo, branch, executable, f"{title}: {', '.join(executable)} executable")
        out(f"{repo}: the upgrade to {target} in {existing} now calls {core}")
        return existing
    head = github.reset_branch(repo, branch, base)
    github.commit_files(repo, branch, head, changes, title)
    # Also when ./mlz is unchanged: a copy that came without its mode (an earlier run that stopped) gets it here.
    if executable:
        github.make_executable(repo, branch, executable, f"{title}: {', '.join(executable)} executable")
    url = github.create_pull(repo, branch, base, title, body)
    out(f"{repo}: {current} -> {target}: {url}")
    return url
