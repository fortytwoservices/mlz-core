"""The GitHub calls mlz upgrade and mlz install make, through the gh CLI and the engineer's own login.

Commits go through createCommitOnBranch, which GitHub signs, so they show as verified; the REST contents API
leaves a commit made with a person's login unsigned.
"""

from __future__ import annotations

import base64
import json
import subprocess

from .merge import ConfigError


class GitHub:
    def _api(self, path: str, method: str = "GET", body: dict | None = None, missing_ok: bool = False):
        command = ["gh", "api", "-X", method, path] + (["--input", "-"] if body is not None else [])
        run = subprocess.run(command, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
        if run.returncode:
            if missing_ok and "HTTP 404" in run.stderr:
                return None
            hint = " Add the scope with: gh auth refresh -h github.com -s workflow" if "workflow" in run.stderr and "scope" in run.stderr else ""
            raise ConfigError(f"gh api {method} {path}: {run.stderr.strip() or run.stdout.strip()}{hint}")
        return json.loads(run.stdout) if run.stdout.strip() else None

    def _pages(self, path: str) -> list:
        """Every item of a listing, not only its first page."""
        found, page = [], 1
        while True:
            batch = self._api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}") or []
            found += batch
            if len(batch) < 100:
                return found
            page += 1

    # Repos and files

    def repo(self, repo: str) -> dict | None:
        return self._api(f"repos/{repo}", missing_ok=True)

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

    def commit_files(self, repo: str, branch: str, head: str, files: dict[str, str], message: str) -> None:
        """One commit with the files, made only if the branch is still at head."""
        mutation = "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }"
        additions = [{"path": path, "contents": base64.b64encode(text.encode()).decode()} for path, text in sorted(files.items())]
        self._api("graphql", "POST", {"query": mutation, "variables": {"input": {
            "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
            "expectedHeadOid": head,
            "message": {"headline": message},
            "fileChanges": {"additions": additions},
        }}})

    def commit_file(self, repo: str, branch: str, head: str, path: str, text: str, message: str) -> None:
        self.commit_files(repo, branch, head, {path: text}, message)

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
