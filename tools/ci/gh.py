"""The pipeline's GitHub calls, through the gh CLI and the run's token (GH_TOKEN). A step's tests give it a fake of this
class instead. Apart from mlz install's and mlz upgrade's client (tools/mlz/github.py) on purpose: the pipeline's calls
stay as they are when that one changes."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/, for mlz

from mlz.merge import ConfigError  # noqa: E402

__all__ = ["ConfigError", "Pipeline", "env", "work"]

# A branch move is sent at most three times, five seconds apart. GitHub ends a REST request it has worked on for 10
# seconds with a "Server Error" (docs.github.com, Troubleshooting the REST API, Timeouts); gh itself waits forever.
MOVE_TRIES, MOVE_WAIT, MOVE_TIMEOUT = 3, 5, 30


def refused(error: Exception) -> bool:
    """Whether GitHub refused the call: gh prints a 4xx's status ("gh: Reference already exists (HTTP 422)", or
    "gh: HTTP 422" without a message). An answer gh cannot read prints no status: empty or cut off ("unexpected end of
    JSON input", "unexpected EOF"), as does a dropped connection ('Patch "<url>": EOF'). gh exits 1 for each (gh 2.105.0,
    tested against a local server)."""
    return isinstance(error, ConfigError) and re.search(r"\bHTTP 4\d\d\b", str(error)) is not None


def env(name: str) -> str:
    """A variable the step needs: a missing one fails it, as bash's set -u did."""
    value = os.environ.get(name)
    if value is None:
        raise ConfigError(f"{name} is not set.")
    return value


def work() -> Path:
    """.mlz/ in the customer repo, where everything a run works out goes (WORK, as every step names it)."""
    path = Path.cwd() / ".mlz"
    path.mkdir(exist_ok=True)
    return path


class Pipeline:
    def __init__(self, token: str | None = None):
        """With a token, gh uses it instead of GH_TOKEN: the Team gate reads as the MLZ App and comments as the job."""
        self._env = {**os.environ, "GH_TOKEN": token} if token else None

    def _api(self, path: str, method: str = "GET", body: dict | None = None, missing_ok: bool = False, timeout: float | None = None):
        """gh api, with a JSON body; None for HTTP 404 where missing_ok; a ConfigError with gh's answer otherwise. With a
        timeout, subprocess.TimeoutExpired once it passes."""
        command = ["gh", "api", "-X", method, path] + (["--input", "-"] if body is not None else [])
        run = subprocess.run(command, input=json.dumps(body) if body is not None else None, capture_output=True, text=True, timeout=timeout,
                             env=self._env)
        if run.returncode:
            if missing_ok and "HTTP 404" in run.stderr:
                return None
            raise ConfigError(f"gh api {method} {path}: {run.stderr.strip() or run.stdout.strip()}")
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

    def head(self, repo: str, branch: str) -> str:
        return self._api(f"repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]

    def reset_branch(self, repo: str, branch: str, base: str) -> None:
        """The branch at the base branch's head, created or moved back there: it is the pipeline's own. A move that
        fails is checked on the branch, which may have moved though its answer was lost (finding 20), and sent again
        unless GitHub refused it."""
        sha = self.head(repo, base)
        for attempt in range(1, MOVE_TRIES + 1):
            try:
                self._move(repo, branch, sha)
                return
            except (ConfigError, subprocess.TimeoutExpired) as error:
                try:
                    if self._branch(repo, branch) == sha:
                        return
                except (ConfigError, subprocess.TimeoutExpired):
                    pass  # not known: the error decides
                if refused(error):
                    raise
                if attempt == MOVE_TRIES:
                    raise ConfigError(f"{error} (tried {MOVE_TRIES} times)") from error
                time.sleep(MOVE_WAIT)

    def _branch(self, repo: str, branch: str) -> str | None:
        """The commit the branch is at, None when GitHub says there is no such branch (HTTP 404): an empty answer is
        a hiccup, not a missing branch."""
        path = f"repos/{repo}/git/ref/heads/{branch}"
        try:
            found = self._api(path, timeout=MOVE_TIMEOUT)
        except ConfigError as error:
            if re.search(r"\bHTTP 404\b", str(error)):
                return None
            raise
        if not found:
            raise ConfigError(f"gh api GET {path}: an empty answer")
        return found["object"]["sha"]

    def _move(self, repo: str, branch: str, sha: str) -> None:
        """Read first, so a refusal to create the branch never turns into a forced update."""
        found = self._branch(repo, branch)
        if found is None:
            self._api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": sha}, timeout=MOVE_TIMEOUT)
        elif found != sha:
            self._api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": sha, "force": True}, timeout=MOVE_TIMEOUT)

    def create_pull(self, repo: str, branch: str, base: str, title: str, body: str) -> str:
        return self._api(f"repos/{repo}/pulls", "POST", {"title": title, "head": branch, "base": base, "body": body})["html_url"]

    def sha(self, repo: str, ref: str) -> str:
        """The commit a branch or a ref is at."""
        return self._api(f"repos/{repo}/commits/{ref}")["sha"]

    def commit_message(self, repo: str, sha: str) -> str:
        return self._api(f"repos/{repo}/commits/{sha}")["commit"]["message"]

    def text(self, repo: str, path: str, ref: str | None = None) -> str | None:
        """A file's text, or None when there is no such file (on that ref)."""
        found = self._api(f"repos/{repo}/contents/{path}" + (f"?ref={ref}" if ref else ""), missing_ok=True)
        return None if not found else base64.b64decode(found["content"]).decode()

    def raw(self, repo: str, path: str, ref: str) -> str | None:
        """A file's bytes as text, read raw (no size limit), or None for HTTP 404; any other failure raises."""
        run = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={ref}"],
                             capture_output=True, text=True, env=self._env)
        if run.returncode:
            if "HTTP 404" in run.stderr:
                return None
            raise ConfigError(run.stderr.strip() or run.stdout.strip())
        return run.stdout

    def put_file(self, repo: str, path: str, branch: str, text: str, message: str) -> None:
        """The file as one commit on the branch, over whatever the branch holds at that path."""
        found = self._api(f"repos/{repo}/contents/{path}?ref={branch}", missing_ok=True)
        body = {"message": message, "content": base64.b64encode(text.encode()).decode(), "branch": branch}
        if found:
            body["sha"] = found["sha"]
        self._api(f"repos/{repo}/contents/{path}", "PUT", body)

    def open_pulls(self, repo: str, branch: str) -> list[int]:
        """The open pull requests from the branch into main, by number."""
        owner = repo.split("/")[0]
        return [pull["number"] for pull in self._api(f"repos/{repo}/pulls?state=open&base=main&head={owner}:{branch}") or []]

    def comment(self, repo: str, number: int, body: str, timeout: float | None = None) -> None:
        """A comment on an issue or a pull request."""
        self._api(f"repos/{repo}/issues/{number}/comments", "POST", {"body": body}, timeout=timeout)

    def commit_pulls(self, repo: str, sha: str, timeout: float | None = None) -> list[dict]:
        """The pull requests a commit belongs to: for a merge's commit on main, the one it merged."""
        return self._api(f"repos/{repo}/commits/{sha}/pulls", timeout=timeout) or []

    # The Team gate's reads (tools/ci/approved.py).

    def pull_runs(self, repo: str, workflow: str, head_sha: str) -> list[dict]:
        """The caller workflow's pull request runs on a commit, newest first. A run's pull_requests is empty once its pull
        request is merged (seen 23 November 2026), so which pull request a run was for comes from its plans' stamp."""
        found = self._api(f"repos/{repo}/actions/workflows/{workflow}/runs?event=pull_request&head_sha={head_sha}&per_page=100") or {}
        return sorted(found.get("workflow_runs") or [], key=lambda run: run.get("run_number", 0), reverse=True)

    def workflow_run(self, repo: str, run_id: int) -> dict:
        """A run as it is now: its latest attempt (run_attempt), when that started (run_started_at), status and conclusion."""
        return self._api(f"repos/{repo}/actions/runs/{run_id}")

    def run_jobs(self, repo: str, run_id: int, attempt: int) -> list[dict]:
        """An attempt's jobs ({name, status, conclusion, completed_at}); a job a re-run did not run again keeps its time."""
        found, page = [], 1
        while True:
            batch = (self._api(f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}") or {}).get("jobs") or []
            found += batch
            if len(batch) < 100:
                return found
            page += 1

    def download_artifacts(self, repo: str, run_id: int, pattern: str, folder: Path) -> bool:
        """A run's artifacts whose names match the pattern, each into a folder of its own under folder; False when it has
        none, and a ConfigError for any other failure."""
        run = subprocess.run(["gh", "run", "download", str(run_id), "--repo", repo, "--pattern", pattern, "--dir", str(folder)],
                             capture_output=True, text=True, env=self._env)
        if run.returncode:
            if "no valid artifacts" in run.stderr or "no artifact matches" in run.stderr:
                return False
            raise ConfigError(f"gh run download {run_id}: {run.stderr.strip() or run.stdout.strip()}")
        return any(folder.rglob("*"))

    def reviews(self, repo: str, number: int) -> list[dict]:
        """A pull request's reviews, oldest first ({user, state, submitted_at, commit_id})."""
        return self._pages(f"repos/{repo}/pulls/{number}/reviews")

    def team_member(self, org: str, team: str, login: str) -> bool:
        """Whether the user is an active member of the organization's team (Members: read)."""
        found = self._api(f"orgs/{org}/teams/{team}/memberships/{login}", missing_ok=True)
        return bool(found) and found.get("state") == "active"

    def permission(self, repo: str, login: str) -> str:
        """The user's permission on the repo: admin, maintain, write, triage, read or none (Metadata: read)."""
        found = self._api(f"repos/{repo}/collaborators/{login}/permission", missing_ok=True) or {}
        return found.get("role_name") or found.get("permission") or "none"

    def branch_rules(self, repo: str, branch: str) -> list[dict]:
        """The rules of the repo's active rulesets that apply to the branch ({type, parameters, ...}), with the metadata
        every token reads."""
        return self._pages(f"repos/{repo}/rules/branches/{branch}")

    def commit(self, repo: str, sha: str) -> dict:
        """A commit: its parents and the files it changes."""
        return self._api(f"repos/{repo}/commits/{sha}")

    def bypasses(self, repo: str, branch: str) -> list[dict]:
        """The past day's pushes to the branch that bypassed a ruleset ({after_sha, actor_name, ...}; Administration: read)."""
        return self._api(f"repos/{repo}/rulesets/rule-suites?ref=refs/heads/{branch}&time_period=day&rule_suite_result=bypass&per_page=100") or []

    def close_pull(self, repo: str, number: int, comment: str) -> None:
        """Closes a pull request with a comment, and deletes its branch, as gh pr close --delete-branch does."""
        pull = self._api(f"repos/{repo}/pulls/{number}")
        self.comment(repo, number, comment)
        self._api(f"repos/{repo}/pulls/{number}", "PATCH", {"state": "closed"})
        self._api(f"repos/{repo}/git/refs/heads/{pull['head']['ref']}", "DELETE", missing_ok=True)

    def pull_files(self, repo: str, number: int) -> list[dict]:
        return self._pages(f"repos/{repo}/pulls/{number}/files")

    def pull_head(self, repo: str, number: int) -> str:
        return self._api(f"repos/{repo}/pulls/{number}")["head"]["sha"]

    def squash_allowed(self, repo: str) -> bool:
        return bool(self._api(f"repos/{repo}")["allow_squash_merge"])

    def behind_by(self, repo: str, base: str, head: str) -> int:
        return int(self._api(f"repos/{repo}/compare/{base}...{head}")["behind_by"])

    def changed_files(self, repo: str, base: str, head: str) -> list[str]:
        return [item["filename"] for item in self._api(f"repos/{repo}/compare/{base}...{head}").get("files") or []]

    def update_branch(self, repo: str, number: int, expected_head: str) -> None:
        """GitHub's update branch: main merged into the pull request's branch, only if it is still at expected_head."""
        self._api(f"repos/{repo}/pulls/{number}/update-branch", "PUT", {"expected_head_sha": expected_head})

    def merge_pull(self, repo: str, number: int, method: str, sha: str) -> None:
        """Merges the pull request if its head is still sha; GitHub answers HTTP 409 when it is not."""
        self._api(f"repos/{repo}/pulls/{number}/merge", "PUT", {"merge_method": method, "sha": sha})
