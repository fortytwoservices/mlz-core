"""The GitHub calls mlz upgrade and mlz install make, through the gh CLI and the engineer's own login.

Commits go through createCommitOnBranch, which GitHub signs, so they show as verified; the REST contents API
leaves a commit made with a person's login unsigned.
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


class GitHub:
    def _api(self, path: str, method: str = "GET", body: dict | None = None, missing_ok: bool = False):
        command = ["gh", "api", "-X", method, path] + (["--input", "-"] if body is not None else [])
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

    def create_repo(self, owner: str, name: str) -> dict:
        """A private repo in an organisation, with GitHub's first commit, so that a branch exists to commit to."""
        return self._api(f"orgs/{owner}/repos", "POST", {"name": name, "private": True, "auto_init": True})

    def rename_branch(self, repo: str, branch: str, name: str) -> None:
        self._api(f"repos/{repo}/branches/{branch}/rename", "POST", {"new_name": name})

    def file(self, repo: str, path: str, ref: str) -> tuple[str, str] | tuple[None, None]:
        found = self._api(f"repos/{repo}/contents/{path}?ref={ref}", missing_ok=True)
        if not found:
            return None, None
        return base64.b64decode(found["content"]).decode(), found["sha"]

    def head(self, repo: str, branch: str) -> str:
        return self._api(f"repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]

    def reset_branch(self, repo: str, branch: str, base: str) -> str:
        """The branch at the base branch's head, created or moved back there (it is the tool's own); returns
        that commit."""
        sha = self.head(repo, base)
        if self._api(f"repos/{repo}/git/ref/heads/{branch}", missing_ok=True):
            self._api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": sha, "force": True})
        else:
            self._api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": sha})
        return sha

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

    def hosted_runner(self, org: str, name: str) -> dict | None:
        return next((r for r in self._pages(f"orgs/{org}/actions/hosted-runners", "runners") if r["name"] == name), None)

    def create_hosted_runner(self, org: str, body: dict) -> dict:
        return self._api(f"orgs/{org}/actions/hosted-runners", "POST", body)

    def variable(self, repo: str, name: str) -> str | None:
        found = self._api(f"repos/{repo}/actions/variables/{name}", missing_ok=True)
        return None if found is None else found["value"]

    def set_variable(self, repo: str, name: str, value: str) -> None:
        if self.has_variable(repo, name):
            self._api(f"repos/{repo}/actions/variables/{name}", "PATCH", {"name": name, "value": value})
        else:
            self._api(f"repos/{repo}/actions/variables", "POST", {"name": name, "value": value})
