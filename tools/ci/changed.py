"""changed.py: the changed job's step, after every plan job: the stacks whose reviewed plan makes changes, which the
apply jobs take, one each (the platform's together, in order), and which plan job planned each, for its apply to
download only that one. Reads STACKS and BATCHES (JSON, from check), ROLE, and .mlz/reviewed/changes-<stack>.txt;
writes stacks= and batch_of= to GITHUB_OUTPUT, and a line to GITHUB_STEP_SUMMARY. Was role.yml's inline Python.

A run with something to apply then waits for a reviewer in the role's environment, and every later run on main queues
behind it (finding 11). Where no environment can wait for one (MLZ_APPROVAL=pull-request, on GitHub Team), the gate is
approved.py's instead: main's plan must be the one the merged pull request's run showed, or nothing applies and the job
fails. GitHub tells the reviewers itself; this says why, where people look (waiting):
- on the run's page, always: what started the run, what it applies, where it waits;
- on the pull request whose merge started it, when a person opened it: one comment, on the run's first attempt only.
  A contract pull request, or any other one an app opened, gets none: nobody reads it. Neither does a push without a
  pull request, or a manual run.
Reads, for that, GITHUB_EVENT_NAME, GITHUB_SHA, GITHUB_REPOSITORY, GITHUB_RUN_ATTEMPT, RUN_URL, GITHUB_ACTOR,
GITHUB_EVENT_PATH (the push's head commit, for its first line) and GH_TOKEN (pull-requests: write). None of it can stop
the apply: the outputs are written first, and any failure after them is a notice.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci import approved  # noqa: E402
from ci.approved import merged_pull  # noqa: E402,F401: changed.merged_pull, as before
from ci.gh import ConfigError, Pipeline  # noqa: E402

# A contract pull request, by its branch (publish.py, and mlz install publish for the bootstrap's, opened with an
# engineer's own login). Its merge may be the merge job's or a person's, where a ruleset asks for a review.
CONTRACT_BRANCH = re.compile(r"^mlz/(bootstrap|governance|platform|vending)-contract$")
# Each call to GitHub, well inside the job's five minutes: a stalled one is a notice, never a skipped apply.
DEADLINE = 30
# The caller's queue holds main's pushes and manual runs for the whole run; the daily check has a group of its own.
QUEUE = "Every later push or manual run on main queues behind this one until it finishes: applied, or rejected."
TITLE = 200  # characters of a title shown at most: a step summary over GitHub's 1 MiB is dropped whole


def changes(stack: str) -> bool:
    """A missing list counts as changes: the apply then stops on it."""
    path = pathlib.Path(f".mlz/reviewed/changes-{stack}.txt")
    return not path.exists() or path.read_text().strip() != ""


def head_title() -> str:
    """The push's head commit's first line, from the event (not from an environment variable, which a long message could
    make too long to start the step)."""
    try:
        with open(os.environ["GITHUB_EVENT_PATH"]) as event:
            message = (json.load(event).get("head_commit") or {}).get("message") or ""
    except (KeyError, OSError, ValueError):
        return ""
    return shown(next(iter(message.splitlines()), ""))


def shown(title: str) -> str:
    """A title as the note shows it: TITLE characters at most, marked where it was cut."""
    return title if len(title) <= TITLE else title[:TITLE] + "…"


def contract_of(pull: dict | None) -> str | None:
    """The role whose contract a pull request carries: by its branch, in this repo itself, as the merge job asks; a fork's
    branch of the same name is a person's. None for any other."""
    head = (pull or {}).get("head") or {}
    found = CONTRACT_BRANCH.match(head.get("ref") or "")
    own = (head.get("repo") or {}).get("full_name") == os.environ.get("GITHUB_REPOSITORY")
    return found.group(1) if found and own else None


def started_by(event: str, title: str, actor: str, pull: dict | None) -> str:
    """What started the run, in words: the merged pull request, by its own title (a merge commit's subject is "Merge pull
    request #...", a rebase's its last commit's), or the push, by its head commit's."""
    if event != "push":
        return f"a manual run by {actor}"
    if pull and contract_of(pull):
        return f"{contract_of(pull)}'s contract, merged (#{pull['number']})"
    if pull:
        return f"#{pull['number']}, merged: {shown(pull.get('title') or title)}"
    return f"a push to main: {title}" if title else "a push to main"


def waiting(role: str, stacks: list[str], github: Pipeline | None = None, say=print) -> None:
    """The run's note, and the pull request's comment, for a run that waits for approval."""
    event, title = os.environ.get("GITHUB_EVENT_NAME", ""), head_title()
    pull = None
    if event == "push":
        try:
            pull = merged_pull(github or Pipeline(), os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"], timeout=DEADLINE)
        except Exception as error:  # noqa: BLE001: the note goes out without it; the apply is never held up by it
            say(f"::notice::The pull request this push merged could not be found ({error}); the run's page says why it waits.")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        out.write(f"\n### Waiting for approval\n\nThis run applies {', '.join(stacks)} once it is approved in the `{role}` environment, by one "
                  f"of its required reviewers. It was started by {started_by(event, title, os.environ.get('GITHUB_ACTOR', ''), pull)}. "
                  f"{QUEUE}\n")
    person = pull is not None and (pull.get("user") or {}).get("type") == "User" and not contract_of(pull)
    if not person or os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        return
    try:
        (github or Pipeline()).comment(os.environ["GITHUB_REPOSITORY"], pull["number"], (
            f"This merge's run waits for approval in the `{role}` environment before it applies {', '.join(stacks)}: "
            f"{os.environ.get('RUN_URL', '')}\n\n{QUEUE}"), timeout=DEADLINE)
    except Exception as error:  # noqa: BLE001
        say(f"::notice::Could not comment on #{pull['number']} ({error}); the run's page says why it waits.")


def main(github: Pipeline | None = None, say=print) -> int:
    stacks = json.loads(os.environ["STACKS"])
    batch_of = {stack: batch["id"] for batch in json.loads(os.environ["BATCHES"]) for stack in batch["stacks"].split()}
    changed = [stack for stack in stacks if changes(stack)]
    unchanged = [stack for stack in stacks if stack not in changed]
    by_pull_request = os.environ.get("MLZ_APPROVAL") == "pull-request"
    if by_pull_request and not approved.approved(os.environ["ROLE"], changed, github, say=say):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
            out.write(f"Nothing is applied: the plan of {', '.join(changed)} on main is not the one its pull request's run showed; "
                      "the log says why and what to do.\n")
        return 1
    jobs = [" ".join(changed)] if os.environ["ROLE"] == "platform" and changed else changed
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write(f"stacks={json.dumps(jobs)}\n")
        out.write(f"batch_of={json.dumps(batch_of)}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        if by_pull_request:
            out.write(f"To apply: {', '.join(changed) or 'nothing'}, as its pull request's run showed. No changes: {', '.join(unchanged) or 'none'}.\n")
        else:
            out.write(f"To apply: {', '.join(changed) or 'nothing'}. No changes, so no approval: {', '.join(unchanged) or 'none'}.\n")
    if changed and not by_pull_request:
        try:
            waiting(os.environ["ROLE"], changed, github, say)
        except Exception as error:  # noqa: BLE001: the outputs are written; nothing here may skip the apply
            say(f"::notice::Could not say why the run waits ({error}); it waits for approval in the {os.environ['ROLE']} environment all the same.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    try:
        sys.exit(main())
    except ConfigError as error:  # the gate could not ask GitHub (approved.py): nothing applies
        print(f"::error::{error}; nothing is applied.", file=sys.stderr)
        sys.exit(1)
