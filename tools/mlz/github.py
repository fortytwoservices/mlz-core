"""The GitHub calls mlz upgrade and mlz install make, through the gh CLI and the engineer's own login.

Commits go through createCommitOnBranch, which GitHub signs, so they show as verified; the REST contents API
leaves a commit made with a person's login unsigned. The one exception is make_executable: createCommitOnBranch
cannot set a file's mode.
"""

from __future__ import annotations

import base64
import json
import subprocess

from .merge import ConfigError

# What an owner's gh login needs for the organization's network configuration, runner group and runner: runner
# groups take admin:org, runners manage_runners:org, network configurations read: to list and write: to make
# (GitHub's REST docs, each endpoint's classic token scope).
RUNNER_SCOPES = "admin:org,manage_runners:org,read:network_configurations,write:network_configurations"


def plain(item: dict) -> bool:
    """A tree entry that is a plain file, not executable: the one kind make_executable changes. A folder (040000), a
    symbolic link (120000), a submodule or a file already executable stay as they are."""
    return item.get("type") == "blob" and item.get("mode") == "100644"


class GitHub:
    def _api(self, path: str, method: str = "GET", body: dict | None = None, missing_ok: bool = False, accept: str | None = None):
        command = ["gh", "api", "-X", method, path] + (["--input", "-"] if body is not None else []) + (["-H", f"Accept: {accept}"] if accept else [])
        run = subprocess.run(command, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
        if run.returncode:
            if missing_ok and "HTTP 404" in run.stderr:
                return None
            runner_scope = any(word in run.stderr for word in ("admin:org", "manage_runners", "network_configurations"))
            scopes = "workflow" if "workflow" in run.stderr else RUNNER_SCOPES if runner_scope else None
            hint = f" Add the scope with: gh auth refresh -h github.com -s {scopes}" if scopes and "scope" in run.stderr else ""
            raise ConfigError(f"gh api {method} {path}: {run.stderr.strip() or run.stdout.strip()}{hint}")
        return json.loads(run.stdout) if run.stdout.strip() else None

    def _pages(self, path: str, key: str | None = None) -> list:
        """Every item of a listing, not only its first page: the answer itself, or its `key` for a listing that
        comes wrapped in an object (network configurations, runner groups, runners)."""
        found, page = [], 1
        while True:
            answer = self._api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}") or ({} if key else [])
            batch = (answer.get(key) or []) if key else answer
            found += batch
            if len(batch) < 100:
                return found
            page += 1

    # Repos and files

    def repo(self, repo: str) -> dict | None:
        return self._api(f"repos/{repo}", missing_ok=True)

    def is_empty(self, repo: str) -> bool:
        """No commit yet, so no branch either: a repo made without GitHub's first commit."""
        return not self._api(f"repos/{repo}/branches?per_page=1")

    def default_branch(self, repo: str) -> str:
        return self._api(f"repos/{repo}")["default_branch"]

    def create_repo(self, owner: str, name: str, first_commit: bool = True) -> dict:
        """A private repo in an organisation, with GitHub's first commit, so that a branch exists to commit to; or
        empty, for history pushed into it (the customer's copy of mlz-core)."""
        return self._api(f"orgs/{owner}/repos", "POST", {"name": name, "private": True, "auto_init": first_commit})

    def tag_exists(self, repo: str, tag: str) -> bool:
        try:
            return self._api(f"repos/{repo}/git/ref/tags/{tag}", missing_ok=True) is not None
        except ConfigError as error:
            if "HTTP 409" in str(error):  # an empty repo: GitHub's Git database answers "Git Repository is empty"
                return False
            raise

    def compare(self, repo: str, base: str, head: str) -> str | None:
        """How head stands to base (ahead, behind, identical or diverged), or None when either is missing."""
        try:
            found = self._api(f"repos/{repo}/compare/{base}...{head}", missing_ok=True)
        except ConfigError as error:
            if "HTTP 409" in str(error):
                return None
            raise
        return None if found is None else found.get("status")

    def actions_access(self, repo: str) -> str | None:
        """Which other repos may call this one's reusable workflows: none, organization or enterprise."""
        found = self._api(f"repos/{repo}/actions/permissions/access", missing_ok=True)
        return None if found is None else found.get("access_level")

    def set_actions_access(self, repo: str, level: str) -> None:
        self._api(f"repos/{repo}/actions/permissions/access", "PUT", {"access_level": level})

    def rename_branch(self, repo: str, branch: str, name: str) -> None:
        self._api(f"repos/{repo}/branches/{branch}/rename", "POST", {"new_name": name})

    def file(self, repo: str, path: str, ref: str) -> tuple[str, str] | tuple[None, None]:
        found = self._api(f"repos/{repo}/contents/{path}?ref={ref}", missing_ok=True)
        if not found:
            return None, None
        return base64.b64decode(found["content"]).decode(), found["sha"]

    def folder(self, repo: str, path: str, ref: str) -> list[str]:
        """The paths of the files in a folder and below it; [] when there is no such folder."""
        found = self._api(f"repos/{repo}/contents/{path}?ref={ref}", missing_ok=True)
        if not isinstance(found, list):
            return []
        return sorted(p for item in found for p in ([item["path"]] if item["type"] == "file" else self.folder(repo, item["path"], ref)))

    def head(self, repo: str, branch: str) -> str:
        return self._api(f"repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]

    def reset_branch(self, repo: str, branch: str, base: str, at: str | None = None) -> str:
        """The branch at the base branch's head, or at the commit `at`, created or moved back there (it is the
        tool's own); returns that commit."""
        sha = at or self.head(repo, base)
        if self._api(f"repos/{repo}/git/ref/heads/{branch}", missing_ok=True):
            self._api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": sha, "force": True})
        else:
            self._api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": sha})
        return sha

    def app_id(self, slug: str) -> int:
        """A GitHub App's ID, which a ruleset's bypass names it by, from its slug."""
        return self._api(f"apps/{slug}")["id"]

    def app(self, slug: str) -> dict | None:
        """A GitHub App (its id, client_id and owner), or None when there is none the person can see: a private App
        is shown only to its organization's members (tested 23 November 2026)."""
        return self._api(f"apps/{slug}", missing_ok=True)

    def delete_branch(self, repo: str, branch: str) -> None:
        self._api(f"repos/{repo}/git/refs/heads/{branch}", "DELETE", missing_ok=True)

    def commit_files(self, repo: str, branch: str, head: str, files: dict[str, str | None], message: str) -> None:
        """One commit with the files (None deletes one), made only if the branch is still at head."""
        mutation = "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }"
        additions = [{"path": path, "contents": base64.b64encode(text.encode()).decode()} for path, text in sorted(files.items()) if text is not None]
        deletions = [{"path": path} for path, text in sorted(files.items()) if text is None]
        self._api("graphql", "POST", {"query": mutation, "variables": {"input": {
            "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
            "expectedHeadOid": head,
            "message": {"headline": message},
            "fileChanges": {"additions": additions, **({"deletions": deletions} if deletions else {})},
        }}})

    def not_executable(self, repo: str, ref: str, paths: list[str]) -> list[str]:
        """Of the paths, those on ref that are a plain file without the executable mode (100644); a folder or a symbolic
        link of that name is left alone. Only paths at the repo's top: the root tree is read (a branch name gives its
        commit's)."""
        return sorted(item["path"] for item in self._api(f"repos/{repo}/git/trees/{ref}")["tree"] if item["path"] in paths and plain(item))

    def make_executable(self, repo: str, branch: str, paths: list[str], message: str) -> bool:
        """One commit that makes the paths executable (mode 100755), only if one is not yet; returns whether it made
        one. createCommitOnBranch cannot set a mode, and keeps the one a file has (checked 19 November 2026), so a file
        it adds is not executable until this commit. The REST API makes it unsigned: on a pull request's branch the
        squash merge makes it part of a verified commit; on a new repo's main it stays the one unverified commit. Only
        paths at the repo's top: the root tree is read."""
        head = self.head(repo, branch)
        tree = self._api(f"repos/{repo}/git/commits/{head}")["tree"]["sha"]
        entries = [{"path": item["path"], "mode": "100755", "type": "blob", "sha": item["sha"]}
                   for item in self._api(f"repos/{repo}/git/trees/{tree}")["tree"] if item["path"] in paths and plain(item)]
        if not entries:
            return False
        made = self._api(f"repos/{repo}/git/trees", "POST", {"base_tree": tree, "tree": entries})["sha"]
        commit = self._api(f"repos/{repo}/git/commits", "POST", {"message": message, "tree": made, "parents": [head]})["sha"]
        self._api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": commit, "force": False})
        return True

    # Pull requests

    def open_pull(self, repo: str, branch: str) -> str | None:
        owner = repo.split("/")[0]
        pulls = self._api(f"repos/{repo}/pulls?state=open&head={owner}:{branch}")
        return pulls[0]["html_url"] if pulls else None

    def close_pulls(self, repo: str, branch: str, comment: str) -> list[str]:
        """Closes the open pull requests from the branch, each with the comment; returns their URLs."""
        owner = repo.split("/")[0]
        closed = []
        for pull in self._api(f"repos/{repo}/pulls?state=open&head={owner}:{branch}") or []:
            self._api(f"repos/{repo}/issues/{pull['number']}/comments", "POST", {"body": comment})
            self._api(f"repos/{repo}/pulls/{pull['number']}", "PATCH", {"state": "closed"})
            closed.append(pull["html_url"])
        return closed

    def create_pull(self, repo: str, branch: str, base: str, title: str, body: str) -> str:
        return self._api(f"repos/{repo}/pulls", "POST", {"title": title, "head": branch, "base": base, "body": body})["html_url"]

    def latest_pull(self, repo: str, branch: str) -> dict | None:
        """The newest pull request from the branch, open or closed: html_url, number, title, state, merged_at,
        created_at; None when the branch never had one."""
        owner = repo.split("/")[0]
        pulls = self._api(f"repos/{repo}/pulls?state=all&head={owner}:{branch}&sort=created&direction=desc&per_page=1")
        return pulls[0] if pulls else None

    def update_pull(self, repo: str, number: int, title: str, body: str) -> None:
        self._api(f"repos/{repo}/pulls/{number}", "PATCH", {"title": title, "body": body})

    def root_files(self, repo: str, ref: str) -> list[str]:
        """The names of the files at the top of the repo, not in a folder."""
        found = self._api(f"repos/{repo}/contents/?ref={ref}", missing_ok=True)
        return sorted(item["path"] for item in found or [] if item["type"] == "file")

    # Releases

    def releases(self, core: str) -> dict[str, str]:
        """Every published release: tag to release notes."""
        return {r["tag_name"]: r.get("body") or "" for r in self._pages(f"repos/{core}/releases") if not r["draft"] and not r["prerelease"]}

    # Settings

    def add_ruleset(self, repo: str, ruleset: dict) -> bool:
        """The repo's own ruleset, created unless one of that name exists (which is left as it is). True if made."""
        if any(r["name"] == ruleset["name"] for r in self._pages(f"repos/{repo}/rulesets?includes_parents=false")):
            return False
        self._api(f"repos/{repo}/rulesets", "POST", ruleset)
        return True

    def rulesets(self, repo: str) -> list[dict]:
        """Every ruleset that applies to the repo, the organization's too, each in full (rules, bypass actors), with
        current_user_can_bypass as GitHub answers it for whoever is signed in: always, never or exempt."""
        return [self._api(f"repos/{repo}/rulesets/{listed['id']}?includes_parents=true")
                for listed in self._pages(f"repos/{repo}/rulesets?includes_parents=true")]

    def branch_rules(self, repo: str, branch: str) -> list[dict]:
        """Every rule GitHub enforces on the branch, from the active rulesets that apply to it, the organization's too."""
        return self._pages(f"repos/{repo}/rules/branches/{branch}")

    # Code owners (tools/mlz/owners.py)

    def team(self, org: str, slug: str) -> dict | None:
        return self._api(f"orgs/{org}/teams/{slug}", missing_ok=True)

    def team_can_write(self, org: str, slug: str, repo: str) -> bool:
        """Whether the team has write access to the repo or more (maintain, admin)."""
        found = self._api(f"orgs/{org}/teams/{slug}/repos/{repo}", missing_ok=True, accept="application/vnd.github.v3.repository+json")
        return bool(found and (found.get("permissions") or {}).get("push"))

    def grant_team(self, org: str, slug: str, repo: str) -> None:
        """Write access for the team: what a code owner needs. Only called when it has less, so it never lowers one."""
        self._api(f"orgs/{org}/teams/{slug}/repos/{repo}", "PUT", {"permission": "push"})

    def codeowners_errors(self, repo: str, ref: str) -> list[dict] | None:
        """GitHub's errors in the repo's CODEOWNERS at ref, or None while GitHub has not read the file: it answers 404
        for some seconds after a push, and for a repo without one."""
        found = self._api(f"repos/{repo}/codeowners/errors?ref={ref}", missing_ok=True)
        return None if found is None else found.get("errors") or []

    def has_variable(self, repo: str, name: str) -> bool:
        return self._api(f"repos/{repo}/actions/variables/{name}", missing_ok=True) is not None

    def has_secret(self, repo: str, name: str) -> bool:
        return self._api(f"repos/{repo}/actions/secrets/{name}", missing_ok=True) is not None

    def subject_prefix(self, repo: str) -> str:
        """The start of the subject GitHub issues to this repo's workflows."""
        found = self._api(f"repos/{repo}/actions/oidc/customization/sub")
        return found.get("sub_claim_prefix") or f"repo:{repo}"

    # Runners: the organization's network configuration, runner group and GitHub-hosted runner, which only its
    # owners may make (mlz install runners).

    def plan(self, org: str) -> str | None:
        """The organization's GitHub plan (enterprise, team, free), or None: GitHub shows it to its owners only."""
        return ((self._api(f"orgs/{org}", missing_ok=True) or {}).get("plan") or {}).get("name")

    def is_owner(self, org: str) -> bool:
        login = self._api("user")["login"]
        found = self._api(f"orgs/{org}/memberships/{login}", missing_ok=True) or {}
        return found.get("state") == "active" and found.get("role") == "admin"

    def network_configuration(self, org: str, name: str) -> dict | None:
        return next((c for c in self._pages(f"orgs/{org}/settings/network-configurations", "network_configurations") if c["name"] == name), None)

    def create_network_configuration(self, org: str, body: dict) -> dict:
        return self._api(f"orgs/{org}/settings/network-configurations", "POST", body)

    def runner_group(self, org: str, name: str) -> dict | None:
        return next((g for g in self._pages(f"orgs/{org}/actions/runner-groups", "runner_groups") if g["name"] == name), None)

    def create_runner_group(self, org: str, body: dict) -> dict:
        return self._api(f"orgs/{org}/actions/runner-groups", "POST", body)

    def update_runner_group(self, org: str, group: int, body: dict) -> None:
        self._api(f"orgs/{org}/actions/runner-groups/{group}", "PATCH", body)

    def runner_group_repositories(self, org: str, group: int) -> set[int]:
        return {r["id"] for r in self._pages(f"orgs/{org}/actions/runner-groups/{group}/repositories", "repositories")}

    def set_runner_group_repositories(self, org: str, group: int, repositories: list[int]) -> None:
        self._api(f"orgs/{org}/actions/runner-groups/{group}/repositories", "PUT", {"selected_repository_ids": repositories})

    def group_runners(self, org: str, group: int) -> list[dict]:
        """The self-hosted runners registered in a runner group, with their status (online or offline)."""
        return list(self._pages(f"orgs/{org}/actions/runner-groups/{group}/runners", "runners"))

    def hosted_runner(self, org: str, name: str) -> dict | None:
        return next((r for r in self._pages(f"orgs/{org}/actions/hosted-runners", "runners") if r["name"] == name), None)

    def create_hosted_runner(self, org: str, body: dict) -> dict:
        return self._api(f"orgs/{org}/actions/hosted-runners", "POST", body)

    def variable(self, repo: str, name: str) -> str | None:
        found = self._api(f"repos/{repo}/actions/variables/{name}", missing_ok=True)
        return None if found is None else found["value"]

    # Environments (the bootstrap makes one per role repo, named after the role)

    def environment(self, repo: str, name: str) -> dict | None:
        """The environment with its protection_rules and deployment_branch_policy, or None when there is none."""
        return self._api(f"repos/{repo}/environments/{name}", missing_ok=True)

    def environment_variable(self, repo: str, environment: str, name: str) -> str | None:
        found = self._api(f"repos/{repo}/environments/{environment}/variables/{name}", missing_ok=True)
        return None if found is None else found["value"]

    def branch_policies(self, repo: str, environment: str) -> list[str]:
        """The branch names or patterns an environment with custom branch policies accepts."""
        return [p["name"] for p in self._pages(f"repos/{repo}/environments/{environment}/deployment-branch-policies", "branch_policies")]

    def set_variable(self, repo: str, name: str, value: str) -> None:
        if self.has_variable(repo, name):
            self._api(f"repos/{repo}/actions/variables/{name}", "PATCH", {"name": name, "value": value})
        else:
            self._api(f"repos/{repo}/actions/variables", "POST", {"name": name, "value": value})
