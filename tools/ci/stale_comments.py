"""stale_comments.py: on a pull request, marks the plan comment of every stack this commit does not plan, its config being
as on the base again (check.sh), so that no earlier plan or failure stays on the pull request as if it still held.
Needs GH_TOKEN, GITHUB_REPOSITORY, PR_NUMBER and STACKS (what this run plans, as JSON).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.comment import own_comments  # noqa: E402
from ci.gh import ConfigError, Pipeline, env, work  # noqa: E402

# Only the comments comment.py posted, as the run's token, and only a stack's name, which is lower-case letters, digits
# and hyphens: a marker anyone else writes is never read, or used in a file name.
MARKER = re.compile(r"^<!-- mlz-plan:([a-z0-9-]+) -->")


def mark(github: Pipeline, say=print) -> None:
    repo, number, planned = env("GITHUB_REPOSITORY"), int(env("PR_NUMBER")), json.loads(env("STACKS"))
    for comment in own_comments(github, repo, number):
        found = MARKER.match(comment["body"])
        if not found or found[1] in planned:
            continue
        stack = found[1]
        text = (f"<!-- mlz-plan:{stack} -->\n**{stack}**: not planned on this commit: its config is as on the base branch, "
                "so this pull request does not change it.\n")
        (work() / f"comment-{stack}.md").write_text(text)
        github._api(f"repos/{repo}/issues/comments/{comment['id']}", "PATCH", {"body": text})
        say(f"{stack}: its earlier plan comment is marked as not planned on this commit")


if __name__ == "__main__":
    try:
        mark(Pipeline())
    except ConfigError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
