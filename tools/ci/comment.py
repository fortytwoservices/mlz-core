"""comment.py <stack> <plan exit code> [<role>]: puts the stack's plan on the pull request, in one comment per stack that
later runs update. With a role (plans.py's; a workload repo's runs give none), what changed in the stack's settings
against the base branch comes first (mlz diff). Needs GH_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, GITHUB_SERVER_URL and
GITHUB_RUN_ID; reads .mlz/summary-<stack>.md and writes .mlz/comment-<stack>.md.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import ConfigError, Pipeline, env, work  # noqa: E402

TOOLS = str(Path(__file__).resolve().parents[1])
BOT = "github-actions[bot]"  # the run's token posts as this: never a comment someone else wrote with the marker


def marker(stack: str) -> str:
    return f"<!-- mlz-plan:{stack} -->"


def settings(role: str, stack: str) -> str:
    """The stack's settings difference against the base branch, or a line saying why there is none; its own failure
    leaves the plan to be posted without it."""
    # Only stdout is read: mlz diff's own message about a failure stays in the log, which the line below points to.
    found = subprocess.run([sys.executable, "-m", "mlz", "diff", "--role", role, "--stack", stack], stdout=subprocess.PIPE, text=True,
                           env={**os.environ, "PYTHONPATH": TOOLS})
    return found.stdout.rstrip("\n") if found.returncode == 0 else "(The settings' difference could not be worked out; the run's log says why.)"


def body(stack: str, status: str, role: str = "", difference=settings) -> str:
    run = f"{env('GITHUB_SERVER_URL')}/{env('GITHUB_REPOSITORY')}/actions/runs/{env('GITHUB_RUN_ID')}"
    text = marker(stack) + "\n"
    found = difference(role, stack) if role else ""
    if found:
        text += found + "\n\n"
    if status == "1":
        return text + f"**{stack}**: the plan failed; see [the run]({run}).\n"
    return text + (work() / f"summary-{stack}.md").read_text() + "\n" + f"The full plan is attached to [the run]({run}).\n"


def own_comments(github: Pipeline, repo: str, number: int) -> list[dict]:
    """The pull request's comments the run's token posted."""
    return [c for c in github._pages(f"repos/{repo}/issues/{number}/comments") if c["user"]["login"] == BOT]


def post(stack: str, status: str, role: str, github: Pipeline, difference=settings) -> None:
    repo, number = env("GITHUB_REPOSITORY"), int(env("PR_NUMBER"))
    text = body(stack, status, role, difference)
    (work() / f"comment-{stack}.md").write_text(text)
    existing = next((c["id"] for c in own_comments(github, repo, number) if c["body"].startswith(marker(stack))), None)
    if existing is not None:
        github._api(f"repos/{repo}/issues/comments/{existing}", "PATCH", {"body": text})
    else:
        github._api(f"repos/{repo}/issues/{number}/comments", "POST", {"body": text})


if __name__ == "__main__":
    try:
        post(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "", Pipeline())
    except (ConfigError, OSError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
