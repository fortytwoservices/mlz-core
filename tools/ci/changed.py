"""changed.py: the changed job's step, after every plan job: the stacks whose reviewed plan makes changes, which the
apply jobs take, one each (the platform's together, in order), and which plan job planned each, for its apply to
download only that one. Reads STACKS and BATCHES (JSON, from check), ROLE, and .mlz/reviewed/changes-<stack>.txt;
writes stacks= and batch_of= to GITHUB_OUTPUT, and a line to GITHUB_STEP_SUMMARY. Was role.yml's inline Python.

A run with something to apply then waits for a reviewer in the role's environment, and every later run on main queues
behind it (finding 11). GitHub tells the reviewers itself; this says why, where people look (waiting):
- on the run's page, always: what started the run, what it applies, where it waits;
- on the pull request whose merge started it, when a person opened it: one comment, on the run's first attempt only.
  A contract pull request, or any other one an app opened, gets none: nobody reads it. Neither does a push without a
  pull request, or a manual run.
Reads, for that, GITHUB_EVENT_NAME, GITHUB_SHA, GITHUB_REPOSITORY, GITHUB_RUN_ATTEMPT, RUN_URL, HEAD_TITLE (the head
commit's first line), GITHUB_ACTOR, and GH_TOKEN (pull-requests: write). Neither part can stop the apply: a failure
to find or comment on the pull request is a notice, and the summary is written regardless.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import Pipeline  # noqa: E402

# A contract pull request (publish.py): its branch, and its commit's first line, which a squash merge keeps.
CONTRACT_BRANCH = re.compile(r"^mlz/(governance|platform|vending)-contract$")
CONTRACT_TITLE = re.compile(r"^chore: (governance|platform|vending) contract\b")


def changes(stack: str) -> bool:
    """A missing list counts as changes: the apply then stops on it."""
    path = pathlib.Path(f".mlz/reviewed/changes-{stack}.txt")
    return not path.exists() or path.read_text().strip() != ""


def merged_pull(github: Pipeline, repo: str, sha: str) -> dict | None:
    """The pull request whose merge made this commit on main, or None for a push without one."""
    return next((pull for pull in github.commit_pulls(repo, sha) if pull.get("merged_at") and pull.get("merge_commit_sha") == sha), None)


def started_by(event: str, title: str, actor: str, pull: dict | None) -> str:
    """What started the run, in words."""
    if event != "push":
        return f"a manual run by {actor}"
    contract = CONTRACT_BRANCH.match((pull or {}).get("head", {}).get("ref", "")) or CONTRACT_TITLE.match(title)
    if contract:
        return f"{contract.group(1)}'s contract, merged by itself" + (f" (#{pull['number']})" if pull else "")
    if pull:
        return f"#{pull['number']}, merged: {title}"
    return f"a push to main: {title}" if title else "a push to main"


def waiting(role: str, stacks: list[str], github: Pipeline | None = None, say=print) -> None:
    """The run's note, and the pull request's comment, for a run that waits for approval."""
    event, title = os.environ.get("GITHUB_EVENT_NAME", ""), next(iter((os.environ.get("HEAD_TITLE") or "").splitlines()), "")
    pull = None
    if event == "push":
        try:
            pull = merged_pull(github or Pipeline(), os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"])
        except Exception as error:  # noqa: BLE001: the note goes out without it; the apply is never held up by it
            say(f"::notice::The pull request this push merged could not be found ({error}); the run's page says why it waits.")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        out.write(f"\n### Waiting for approval\n\nThis run applies {', '.join(stacks)} once it is approved in the `{role}` environment, by one "
                  f"of its required reviewers. It was started by {started_by(event, title, os.environ.get('GITHUB_ACTOR', ''), pull)}. "
                  "Every later run on main queues behind this one until it is approved or rejected.\n")
    person = pull is not None and pull.get("user", {}).get("type") == "User" and not CONTRACT_BRANCH.match(pull.get("head", {}).get("ref", ""))
    if not person or os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        return
    try:
        (github or Pipeline()).comment(os.environ["GITHUB_REPOSITORY"], pull["number"], (
            f"This merge's run waits for approval in the `{role}` environment before it applies {', '.join(stacks)}: "
            f"{os.environ.get('RUN_URL', '')}\n\nEvery later run on main queues behind it until it is approved or rejected."))
    except Exception as error:  # noqa: BLE001
        say(f"::notice::Could not comment on #{pull['number']} ({error}); the run's page says why it waits.")


def main(github: Pipeline | None = None, say=print) -> None:
    stacks = json.loads(os.environ["STACKS"])
    batch_of = {stack: batch["id"] for batch in json.loads(os.environ["BATCHES"]) for stack in batch["stacks"].split()}
    changed = [stack for stack in stacks if changes(stack)]
    unchanged = [stack for stack in stacks if stack not in changed]
    jobs = [" ".join(changed)] if os.environ["ROLE"] == "platform" and changed else changed
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write(f"stacks={json.dumps(jobs)}\n")
        out.write(f"batch_of={json.dumps(batch_of)}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        out.write(f"To apply: {', '.join(changed) or 'nothing'}. No changes, so no approval: {', '.join(unchanged) or 'none'}.\n")
    if changed:
        waiting(os.environ["ROLE"], changed, github, say)


if __name__ == "__main__":
    main()
